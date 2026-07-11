"""
Slash commands for music playback control: /play, /pause, /resume, /skip,
/stop, /queue, /volume, /nowplaying.

This cog contains no playback logic itself — it only receives interactions
from Discord, delegates the work to core.player.Player / core.search.search,
and displays the result via ready-made embeds from utils/embeds.py. All the
"smart" parts live in core/.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.player import LoopMode, Player, PlayerError
from core.queue import Track
from core.search import TrackNotFoundError, search
from core.spotify import SpotifyError, is_spotify_url, resolve as resolve_spotify
from utils import checks, embeds

logger = logging.getLogger(__name__)


def _parse_timestamp(value: str) -> int:
    """
    Parses a user-entered position into seconds.

    Accepts plain seconds ("90") or colon-separated timestamps ("1:30",
    "1:02:15"). Raises ValueError for anything else.
    """
    parts = value.strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"Invalid timestamp: {value!r}")

    seconds = 0
    for part in parts:
        if not part.isdigit():
            raise ValueError(f"Invalid timestamp: {value!r}")
        seconds = seconds * 60 + int(part)
    return seconds


def _format_timestamp(seconds: int) -> str:
    """Formats seconds as 'M:SS' / 'H:MM:SS' (mirrors Track.formatted_duration)."""
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class MusicCog(commands.Cog):
    """Music playback control commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # One Player per server (keyed by guild.id) — each Discord server
        # listens to its own music independently of the others.
        self.players: dict[int, Player] = {}

    def get_player(self, guild: discord.Guild) -> Player:
        """Returns the Player for a server, creating a new one if it doesn't exist yet."""
        if guild.id not in self.players:
            player = Player(guild)
            # Player handles the idle timeout itself but stays silent about
            # it (it never talks to text channels) — the notification is
            # this cog's job, wired in via callback.
            player.on_idle_disconnect = self._make_idle_notifier(player)
            self.players[guild.id] = player
        return self.players[guild.id]

    @staticmethod
    def _make_idle_notifier(player: Player):
        """Builds the callback that notifies the last used text channel about an idle disconnect."""

        async def notify() -> None:
            if player.text_channel is None:
                return
            await player.text_channel.send(
                embed=embeds.success_embed(
                    "👋 Вийшов з голосового каналу через неактивність."
                )
            )

        return notify

    # ------------------------------------------------------------------ #
    # Voice connection watchdog
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """
        Watches for the BOT itself leaving a voice channel (network drop,
        voice server outage, admin kick) and hands recovery over to the
        player. Intentional disconnects are filtered out inside
        Player.handle_external_disconnect().
        """
        if member.id != self.bot.user.id:
            return

        if before.channel is not None and after.channel is None:
            player = self.players.get(member.guild.id)
            if player is not None:
                await player.handle_external_disconnect(before.channel)

    # ------------------------------------------------------------------ #
    # /play
    # ------------------------------------------------------------------ #

    @app_commands.command(name="play", description="Додати трек у черзі за назвою або посиланням")
    @app_commands.describe(query="Назва треку для пошуку або пряме посилання (YouTube тощо)")
    @app_commands.guild_only()
    @checks.in_voice_channel()
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        # The yt-dlp search can take a few seconds — defer() immediately
        # tells Discord "the bot is thinking", otherwise the interaction
        # would time out after 3 seconds.
        await interaction.response.defer()

        player = self.get_player(interaction.guild)
        player.text_channel = interaction.channel

        voice_channel = interaction.user.voice.channel  # guaranteed non-None by checks.in_voice_channel()
        if not player.is_connected:
            await player.join(voice_channel)

        # Spotify links can't be streamed directly (DRM) — resolve them to
        # one or more plain text "Artist - Title" queries first, then run
        # the rest of the pipeline exactly like a normal text search.
        if is_spotify_url(query):
            try:
                queries = resolve_spotify(query)
            except SpotifyError as exc:
                await interaction.followup.send(embed=embeds.error_embed(str(exc)))
                return

            if not queries:
                await interaction.followup.send(
                    embed=embeds.error_embed("Не вдалося знайти треки за цим посиланням Spotify.")
                )
                return
        else:
            queries = [query]

        added: list[tuple[Track, int]] = []
        failed = 0
        last_error: str | None = None

        for single_query in queries:
            try:
                result = await search(single_query)
            except TrackNotFoundError as exc:
                # Keep the specific reason (private video, age restriction,
                # region block...) so a single failed query can show it.
                failed += 1
                last_error = str(exc)
                continue

            track = Track(
                title=result.title,
                webpage_url=result.webpage_url,
                stream_url=result.stream_url,
                duration=result.duration,
                requester=interaction.user,
                thumbnail=result.thumbnail,
                uploader=result.uploader,
            )

            try:
                position = await player.enqueue(track)
            except PlayerError as exc:
                await interaction.followup.send(embed=embeds.error_embed(str(exc)))
                return

            added.append((track, position))

        if not added:
            # For a single query show the specific reason; for a batch
            # (Spotify playlist) a generic summary is more useful.
            message = (
                last_error
                if len(queries) == 1 and last_error
                else "Не вдалося знайти жодного треку за цим запитом."
            )
            await interaction.followup.send(embed=embeds.error_embed(message))
            return

        if len(added) == 1:
            track, position = added[0]
            await interaction.followup.send(embed=embeds.track_added_embed(track, position))
        else:
            await interaction.followup.send(embed=embeds.tracks_added_embed(len(added), failed=failed))

    # ------------------------------------------------------------------ #
    # /pause /resume /skip /stop
    # ------------------------------------------------------------------ #

    @app_commands.command(name="pause", description="Поставити поточний трек на паузу")
    @app_commands.guild_only()
    @checks.same_voice_channel()
    async def pause(self, interaction: discord.Interaction) -> None:
        player = self.get_player(interaction.guild)

        if player.pause():
            await interaction.response.send_message(embed=embeds.success_embed("⏸️ Пауза."))
        else:
            await interaction.response.send_message(
                embed=embeds.error_embed("Зараз нічого не грає."), ephemeral=True
            )

    @app_commands.command(name="resume", description="Продовжити відтворення після паузи")
    @app_commands.guild_only()
    @checks.same_voice_channel()
    async def resume(self, interaction: discord.Interaction) -> None:
        player = self.get_player(interaction.guild)

        if player.resume():
            await interaction.response.send_message(embed=embeds.success_embed("▶️ Відтворення продовжено."))
        else:
            await interaction.response.send_message(
                embed=embeds.error_embed("Зараз нічого не на паузі."), ephemeral=True
            )

    @app_commands.command(name="skip", description="Пропустити поточний трек")
    @app_commands.guild_only()
    @checks.same_voice_channel()
    async def skip(self, interaction: discord.Interaction) -> None:
        player = self.get_player(interaction.guild)

        if await player.skip():
            await interaction.response.send_message(embed=embeds.success_embed("⏭️ Трек пропущено."))
        else:
            await interaction.response.send_message(
                embed=embeds.error_embed("Зараз нічого не грає."), ephemeral=True
            )

    @app_commands.command(name="stop", description="Зупинити відтворення й очистити чергу")
    @app_commands.guild_only()
    @checks.same_voice_channel()
    async def stop(self, interaction: discord.Interaction) -> None:
        player = self.get_player(interaction.guild)
        await player.stop()
        await interaction.response.send_message(
            embed=embeds.success_embed("⏹️ Відтворення зупинено, черга очищена.")
        )

    # ------------------------------------------------------------------ #
    # /seek /shuffle /remove /loop
    # ------------------------------------------------------------------ #

    @app_commands.command(name="seek", description="Перемотати поточний трек на вказану позицію")
    @app_commands.describe(position="Позиція: секунди (90) або час (1:30, 1:02:15)")
    @app_commands.guild_only()
    @checks.same_voice_channel()
    async def seek(self, interaction: discord.Interaction, position: str) -> None:
        player = self.get_player(interaction.guild)

        try:
            seconds = _parse_timestamp(position)
        except ValueError:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Невірний формат позиції. Приклади: `90`, `1:30`, `1:02:15`."
                ),
                ephemeral=True,
            )
            return

        try:
            await player.seek(seconds)
        except PlayerError as exc:
            await interaction.response.send_message(
                embed=embeds.error_embed(str(exc)), ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=embeds.success_embed(f"⏩ Перемотано на **{_format_timestamp(seconds)}**.")
        )

    @app_commands.command(name="shuffle", description="Перемішати чергу треків")
    @app_commands.guild_only()
    @checks.same_voice_channel()
    async def shuffle(self, interaction: discord.Interaction) -> None:
        player = self.get_player(interaction.guild)

        if len(player.queue) < 2:
            await interaction.response.send_message(
                embed=embeds.error_embed("У черзі замало треків, щоб їх перемішувати."),
                ephemeral=True,
            )
            return

        player.queue.shuffle()
        await interaction.response.send_message(
            embed=embeds.success_embed(f"🔀 Чергу перемішано ({len(player.queue)} трек(ів)).")
        )

    @app_commands.command(name="remove", description="Видалити трек із черги за номером")
    @app_commands.describe(position="Номер треку в черзі (див. /queue)")
    @app_commands.guild_only()
    @checks.same_voice_channel()
    async def remove(
        self,
        interaction: discord.Interaction,
        position: app_commands.Range[int, 1],
    ) -> None:
        player = self.get_player(interaction.guild)

        # /queue shows tracks numbered from 1, MusicQueue indexes from 0.
        track = player.queue.remove(position - 1)

        if track is None:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"У черзі немає треку з номером {position}."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=embeds.success_embed(f"🗑️ Видалено з черги: **{track.title}**.")
        )

    @app_commands.command(name="loop", description="Режим повторення: вимкнено / трек / черга")
    @app_commands.describe(mode="Що повторювати")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Вимкнено", value="off"),
            app_commands.Choice(name="Поточний трек", value="track"),
            app_commands.Choice(name="Уся черга", value="queue"),
        ]
    )
    @app_commands.guild_only()
    @checks.same_voice_channel()
    async def loop(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
    ) -> None:
        player = self.get_player(interaction.guild)
        player.loop_mode = LoopMode(mode.value)

        await interaction.response.send_message(
            embed=embeds.success_embed(f"🔁 Повторення: **{mode.name}**.")
        )

    # ------------------------------------------------------------------ #
    # /queue /volume /nowplaying
    # ------------------------------------------------------------------ #

    @app_commands.command(name="queue", description="Показати поточну черту треків")
    @app_commands.guild_only()
    async def queue_(self, interaction: discord.Interaction) -> None:
        player = self.get_player(interaction.guild)
        await interaction.response.send_message(
            embed=embeds.queue_embed(player.queue, current=player.current)
        )

    @app_commands.command(name="volume", description="Встановити гучність відтворення (0-200%)")
    @app_commands.describe(percent="Гучність у відсотках, наприклад 50")
    @app_commands.guild_only()
    @checks.same_voice_channel()
    async def volume(
        self,
        interaction: discord.Interaction,
        percent: app_commands.Range[int, 0, 200],
    ) -> None:
        player = self.get_player(interaction.guild)
        applied = player.set_volume(percent / 100)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"🔊 Гучність встановлена на {int(applied * 100)}%.")
        )

    @app_commands.command(name="nowplaying", description="Показати, який трек зараз грає")
    @app_commands.guild_only()
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        player = self.get_player(interaction.guild)

        if player.current is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Зараз нічого не грає."), ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=embeds.now_playing_embed(player.current, volume=player.volume)
        )

    # ------------------------------------------------------------------ #
    # Error handling (including CheckFailure from checks.py) for all of this cog's commands
    # ------------------------------------------------------------------ #

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            # checks.py raises CheckFailure with a ready, user-friendly
            # message — just show it to the user, no extra formatting needed.
            await interaction.response.send_message(embed=embeds.error_embed(str(error)), ephemeral=True)
            return

        logger.exception("Error in a music command", exc_info=error)
        message = embeds.error_embed("Сталася непередбачена помилка. Спробуй ще раз пізніше.")

        if interaction.response.is_done():
            await interaction.followup.send(embed=message, ephemeral=True)
        else:
            await interaction.response.send_message(embed=message, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    """Entry point called by bot.load_extension('cogs.music')."""
    await bot.add_cog(MusicCog(bot))
