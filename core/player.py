"""
Player — playback state and logic for music on a single server (Guild).

Each server gets its own Player instance (stored in a dict inside
cogs.music.MusicCog). This lets multiple servers listen to different music
at the same time without interfering with each other.

Player encapsulates: the voice connection, the track queue, the current
track, the volume, and the logic for automatically advancing to the next
track. It knows NOTHING about Discord commands or embeds — that's the job
of the cogs/ layer. This separation makes it possible to test and change
playback logic without touching the command interface.
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Awaitable, Callable, Optional

import discord

import config
from core.queue import MusicQueue, QueueFullError, Track

logger = logging.getLogger(__name__)


class PlayerError(Exception):
    """A player-level error (e.g. trying to play without a voice connection)."""


class LoopMode(Enum):
    """Repeat behaviour applied when a track finishes (see Player._advance)."""

    OFF = "off"        # normal queue consumption
    TRACK = "track"    # replay the current track forever (until /skip or /loop off)
    QUEUE = "queue"    # finished tracks go to the back of the queue


class Player:
    """
    Manages music playback for a single server.

    Typical flow:
      1. join(channel)   — connect to the user's voice channel
      2. enqueue(track)  — add a track to the queue; starts immediately if idle
      3. _start_playing  — starts the FFmpeg stream (Opus) for voice_client.play
      4. _after_playback — callback from discord.py when a track ends/errors
      5. _advance        — takes the next track from the queue, repeats from step 3
      6. disconnect()    — disconnects and fully resets state
    """

    def __init__(self, guild: discord.Guild) -> None:
        self.guild = guild
        # Store the event loop at creation time: _after_playback is called
        # from a discord.py thread (not from the event loop), and this is
        # the loop we use to safely schedule the next playback step.
        self.loop = asyncio.get_running_loop()

        self.queue = MusicQueue(max_size=config.MAX_QUEUE_SIZE)
        self.voice_client: Optional[discord.VoiceClient] = None
        self.current: Optional[Track] = None

        # Channel to send notifications like "now playing" to.
        # Set by the cog (e.g. in /play -> player.text_channel = interaction.channel).
        # Player deliberately writes NOTHING here itself — sending messages
        # and building embeds remain the responsibility of the cogs/utils layer.
        self.text_channel: Optional[discord.abc.Messageable] = None

        self.volume: float = config.DEFAULT_VOLUME
        self._is_paused = False

        self.loop_mode: LoopMode = LoopMode.OFF
        # /skip must always move to the next track, even in TRACK loop mode —
        # this flag lets _advance() tell a natural track end from a skip.
        self._skip_requested = False
        # Set before an intentional voice_client.stop() that should NOT
        # advance the queue (e.g. /seek restarts FFmpeg on the same track).
        self._suppress_advance = False

        # Guards enqueue against a race condition: two /play commands
        # arriving almost simultaneously shouldn't both decide the queue is
        # empty and try to start playback in parallel.
        self._enqueue_lock = asyncio.Lock()

        # Idle auto-disconnect. The timer starts whenever the player is
        # connected but has nothing to play (right after join(), when the
        # queue runs out, or after /stop) and is cancelled as soon as a
        # track starts. When it fires, the player disconnects and calls
        # on_idle_disconnect (set by the cog) so the notification message
        # stays in the cogs/ layer — Player itself never talks to Discord
        # text channels.
        self._idle_task: Optional[asyncio.Task] = None
        self.on_idle_disconnect: Optional[Callable[[], Awaitable[None]]] = None

        # True while WE are disconnecting on purpose (disconnect(), idle
        # timeout). Lets handle_external_disconnect() tell an intentional
        # leave apart from a dropped connection or a kick.
        self._expected_disconnect = False

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #

    @property
    def is_connected(self) -> bool:
        return self.voice_client is not None and self.voice_client.is_connected()

    @property
    def is_playing(self) -> bool:
        return self.is_connected and self.voice_client.is_playing()

    @property
    def is_paused(self) -> bool:
        return self.is_connected and self.voice_client.is_paused()

    # ------------------------------------------------------------------ #
    # Voice channel connection
    # ------------------------------------------------------------------ #

    async def join(self, channel: discord.VoiceChannel) -> None:
        """Connects to a voice channel, or moves into it if already connected elsewhere."""
        if self.voice_client and self.voice_client.is_connected():
            await self.voice_client.move_to(channel)
        else:
            self.voice_client = await channel.connect()

        # If nothing gets enqueued after joining (e.g. the search failed),
        # don't sit in the channel forever — the timer is cancelled by
        # _start_playing() as soon as a track actually starts.
        self._start_idle_timer()

    async def disconnect(self) -> None:
        """Disconnects from voice and fully resets the player state (queue, current track, etc.)."""
        self._cancel_idle_timer()

        if self.voice_client:
            if self.voice_client.is_connected():
                # Mark the upcoming voice_state_update as ours so the
                # external-disconnect handler doesn't try to reconnect.
                self._expected_disconnect = True
            await self.voice_client.disconnect(force=True)

        self.voice_client = None
        self.current = None
        self._is_paused = False
        self.queue.clear()

    async def handle_external_disconnect(self, channel: discord.VoiceChannel) -> None:
        """
        Called by the cog (via on_voice_state_update) when the bot left a
        voice channel WITHOUT us asking — a network drop, a voice server
        outage, or an admin kick.

        Strategy: give discord.py's built-in reconnect a moment to recover;
        if it didn't and there was something to play, try to rejoin the
        channel ourselves and restart the interrupted track. If nothing was
        playing (or reconnecting fails), just reset the state so the player
        isn't left half-broken.
        """
        if self._expected_disconnect:
            self._expected_disconnect = False
            return

        # discord.py's VoiceClient has its own reconnect logic for network
        # blips — don't fight it, check back after a short grace period.
        await asyncio.sleep(5)
        if self.is_connected:
            return

        interrupted = self.current

        if interrupted is None and not self.queue:
            # Nothing was playing anyway — quietly reset and move on.
            logger.info("[%s] Externally disconnected while idle — resetting state.", self.guild.name)
            await self.disconnect()
            return

        logger.warning("[%s] Voice connection lost — attempting to reconnect.", self.guild.name)
        self.voice_client = None

        for attempt in (1, 2):
            try:
                self.voice_client = await channel.connect()
                break
            except discord.DiscordException as exc:
                logger.warning(
                    "[%s] Reconnect attempt %d failed: %s", self.guild.name, attempt, exc
                )
                await asyncio.sleep(2 * attempt)

        if not self.is_connected:
            logger.error("[%s] Could not reconnect — giving up and resetting state.", self.guild.name)
            await self.disconnect()
            return

        logger.info("[%s] Reconnected to voice.", self.guild.name)
        if interrupted is not None:
            # Position within the track is lost (the FFmpeg stream died with
            # the connection), so the track restarts from the beginning.
            self.current = interrupted
            await self._start_playing(interrupted)

    # ------------------------------------------------------------------ #
    # Playback
    # ------------------------------------------------------------------ #

    async def enqueue(self, track: Track) -> int:
        """
        Adds a track to the queue. If nothing is currently playing, starts
        playback immediately.

        Returns the track's position: 0 means "started playing now",
        otherwise the spot number in the queue (handy for a message like
        "Added, position: 3").
        """
        async with self._enqueue_lock:
            if self.current is None and not self.is_playing:
                self.current = track
                await self._start_playing(track)
                return 0

            self.queue.add(track)
            return len(self.queue)

    async def _start_playing(self, track: Track, *, seek: int = 0) -> None:
        """Builds an audio source from the stream link and starts it via FFmpeg in Opus format.

        seek — start position in seconds. Passed to FFmpeg as `-ss` BEFORE
        the input, which makes FFmpeg ask the HTTP server for the right
        byte range instead of downloading and discarding audio (fast input
        seeking). 0 means "play from the beginning".
        """
        if not self.is_connected:
            raise PlayerError("Бот не підключений до голосового каналу.")

        before_options = config.FFMPEG_BEFORE_OPTIONS
        if seek > 0:
            before_options = f"-ss {seek} {before_options}"

        source: discord.AudioSource = discord.FFmpegPCMAudio(
            track.stream_url,
            before_options=before_options,
            options=config.FFMPEG_OPTIONS,
        )
        # PCMVolumeTransformer wraps the source and allows changing the
        # volume on the fly (via /volume), without restarting FFmpeg or
        # interrupting the stream.
        source = discord.PCMVolumeTransformer(source, volume=self.volume)

        self.voice_client.play(source, after=self._after_playback)
        self._is_paused = False
        self._cancel_idle_timer()

        logger.info("[%s] Now playing: %s", self.guild.name, track.title)

    def _after_playback(self, error: Optional[Exception]) -> None:
        """
        discord.py callback: called when a track finishes on its own or due
        to an error.

        IMPORTANT: discord.py calls this function from its INTERNAL playback
        thread, not from the main event loop. So we can't just await a
        coroutine here — it has to be safely handed off to the loop via
        run_coroutine_threadsafe.
        """
        if error:
            logger.error("[%s] Playback error: %s", self.guild.name, error)

        # A deliberate restart of the same track (e.g. /seek) — the queue
        # must not advance. The flag is one-shot: consume it and bail out.
        if self._suppress_advance:
            self._suppress_advance = False
            return

        asyncio.run_coroutine_threadsafe(self._advance(), self.loop)

    async def _advance(self) -> None:
        """Switches to the next track in the queue, or stops if the queue is exhausted.

        Also implements loop modes: TRACK replays the current track (unless
        the user explicitly skipped it), QUEUE re-appends the finished track
        to the back of the queue before taking the next one.
        """
        if not self.is_connected:
            # The stream died because the voice connection itself dropped.
            # Leave current/queue untouched — handle_external_disconnect()
            # decides whether to reconnect and resume or to reset the state.
            return

        skip_requested = self._skip_requested
        self._skip_requested = False

        if self.current is not None:
            if self.loop_mode is LoopMode.TRACK and not skip_requested:
                await self._start_playing(self.current)
                return

            if self.loop_mode is LoopMode.QUEUE:
                try:
                    self.queue.add(self.current)
                except QueueFullError:
                    # The queue hit its limit while the track was playing —
                    # drop the finished track instead of crashing the loop.
                    logger.warning(
                        "[%s] Queue full — dropping %r from QUEUE loop.",
                        self.guild.name,
                        self.current.title,
                    )

        next_track = self.queue.pop_next()

        if next_track is None:
            self.current = None
            logger.info("[%s] Queue is empty — playback finished.", self.guild.name)
            self._start_idle_timer()
            return

        self.current = next_track
        await self._start_playing(next_track)

    # ------------------------------------------------------------------ #
    # Playback control (called from slash commands via cogs)
    # ------------------------------------------------------------------ #

    def pause(self) -> bool:
        """Pauses playback. True if actually paused, False if there was nothing to pause."""
        if self.is_playing:
            self.voice_client.pause()
            self._is_paused = True
            return True
        return False

    def resume(self) -> bool:
        """Resumes from pause. True if actually resumed, False if nothing was paused."""
        if self.is_paused:
            self.voice_client.resume()
            self._is_paused = False
            return True
        return False

    async def skip(self) -> bool:
        """
        Skips the current track.

        voice_client.stop() interrupts the stream — this automatically
        triggers _after_playback, which in turn hands off to _advance().
        There's NO need to call the next track separately here (and it
        shouldn't be, to avoid advancing the queue twice).
        """
        if self.is_playing or self.is_paused:
            # Mark this as an explicit skip so TRACK loop mode doesn't
            # immediately replay the track the user is trying to get rid of.
            self._skip_requested = True
            self.voice_client.stop()
            return True
        return False

    async def seek(self, position: int) -> None:
        """
        Jumps to `position` seconds in the current track by restarting the
        FFmpeg stream with an `-ss` offset (Discord voice can't seek an
        already-running stream).
        """
        if self.current is None or not (self.is_playing or self.is_paused):
            raise PlayerError("Зараз нічого не грає.")

        if self.current.duration is None:
            raise PlayerError("Перемотування недоступне для живих трансляцій.")

        if not 0 <= position < self.current.duration:
            raise PlayerError(
                f"Позиція поза межами треку (тривалість: {self.current.formatted_duration})."
            )

        # stop() below fires _after_playback from discord.py's thread —
        # the flag tells it to NOT advance the queue this one time.
        self._suppress_advance = True
        self.voice_client.stop()
        await self._start_playing(self.current, seek=position)

    async def stop(self) -> None:
        """Fully stops playback and clears the queue, but does NOT leave the voice channel."""
        self.queue.clear()
        self.current = None
        self._is_paused = False

        if self.is_connected:
            self.voice_client.stop()
            # Nothing left to play — start counting down to auto-disconnect.
            # (voice_client.stop() will still fire _after_playback -> _advance,
            # which restarts the timer, but starting it here as well covers
            # the case where nothing was playing when /stop was used.)
            self._start_idle_timer()

    def set_volume(self, volume: float) -> float:
        """
        Sets the volume within the 0.0–2.0 range (1.0 = 100%) for the current
        and subsequent tracks.

        Returns the actually applied value (after clamping to the range).
        """
        self.volume = max(0.0, min(volume, 2.0))

        # voice_client.source is our PCMVolumeTransformer; change the volume
        # directly so the change takes effect instantly, without restarting
        # the current track.
        if self.voice_client and isinstance(self.voice_client.source, discord.PCMVolumeTransformer):
            self.voice_client.source.volume = self.volume

        return self.volume

    # ------------------------------------------------------------------ #
    # Idle auto-disconnect
    # ------------------------------------------------------------------ #

    def _start_idle_timer(self) -> None:
        """(Re)starts the inactivity countdown. A timeout of 0 disables the feature."""
        if config.INACTIVITY_TIMEOUT <= 0:
            return

        self._cancel_idle_timer()
        self._idle_task = self.loop.create_task(self._idle_disconnect())

    def _cancel_idle_timer(self) -> None:
        """Cancels a pending inactivity countdown, if any."""
        # Never cancel ourselves: _idle_disconnect() calls disconnect(),
        # which calls this method — cancelling the current task here would
        # kill the disconnect midway.
        if self._idle_task and self._idle_task is not asyncio.current_task():
            self._idle_task.cancel()
        self._idle_task = None

    async def _idle_disconnect(self) -> None:
        """Waits INACTIVITY_TIMEOUT seconds and disconnects if still idle."""
        try:
            await asyncio.sleep(config.INACTIVITY_TIMEOUT)
        except asyncio.CancelledError:
            return  # a track started (or manual disconnect) — abort silently

        # Double-check the state: pause counts as activity (a user
        # explicitly paused and probably intends to come back).
        if not self.is_connected or self.is_playing or self.is_paused:
            return

        logger.info(
            "[%s] Idle for %d seconds — auto-disconnecting.",
            self.guild.name,
            config.INACTIVITY_TIMEOUT,
        )
        await self.disconnect()

        if self.on_idle_disconnect is not None:
            try:
                await self.on_idle_disconnect()
            except Exception:  # noqa: BLE001 — a failed notification must not crash the loop
                logger.exception("[%s] on_idle_disconnect callback failed", self.guild.name)
