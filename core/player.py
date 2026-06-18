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
from typing import Optional

import discord

import config
from core.queue import MusicQueue, Track

logger = logging.getLogger(__name__)


class PlayerError(Exception):
    """A player-level error (e.g. trying to play without a voice connection)."""


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

        # Guards enqueue against a race condition: two /play commands
        # arriving almost simultaneously shouldn't both decide the queue is
        # empty and try to start playback in parallel.
        self._enqueue_lock = asyncio.Lock()

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

    async def disconnect(self) -> None:
        """Disconnects from voice and fully resets the player state (queue, current track, etc.)."""
        if self.voice_client:
            await self.voice_client.disconnect(force=True)

        self.voice_client = None
        self.current = None
        self._is_paused = False
        self.queue.clear()

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

    async def _start_playing(self, track: Track) -> None:
        """Builds an audio source from the stream link and starts it via FFmpeg in Opus format."""
        if not self.is_connected:
            raise PlayerError("Бот не підключений до голосового каналу.")

        source: discord.AudioSource = discord.FFmpegPCMAudio(
            track.stream_url,
            before_options=config.FFMPEG_BEFORE_OPTIONS,
            options=config.FFMPEG_OPTIONS,
        )
        # PCMVolumeTransformer wraps the source and allows changing the
        # volume on the fly (via /volume), without restarting FFmpeg or
        # interrupting the stream.
        source = discord.PCMVolumeTransformer(source, volume=self.volume)

        self.voice_client.play(source, after=self._after_playback)
        self._is_paused = False

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

        asyncio.run_coroutine_threadsafe(self._advance(), self.loop)

    async def _advance(self) -> None:
        """Switches to the next track in the queue, or stops if the queue is exhausted."""
        next_track = self.queue.pop_next()

        if next_track is None:
            self.current = None
            logger.info("[%s] Queue is empty — playback finished.", self.guild.name)
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
            self.voice_client.stop()
            return True
        return False

    async def stop(self) -> None:
        """Fully stops playback and clears the queue, but does NOT leave the voice channel."""
        self.queue.clear()
        self.current = None
        self._is_paused = False

        if self.is_connected:
            self.voice_client.stop()

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
