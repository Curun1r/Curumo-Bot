"""
Builds consistent Discord embeds for the music commands.

Keeping message layout here keeps cogs/ short and focused on command logic.
Want to change the bot's message style or colors? Just edit this file,
no need to touch the commands themselves.
"""

from __future__ import annotations

from typing import Optional

import discord

from core.queue import MusicQueue, Track

# A single color palette for all of the bot's embeds — keeps the UI consistent
COLOR_SUCCESS = discord.Color.green()
COLOR_ERROR = discord.Color.red()
COLOR_INFO = discord.Color.blurple()


def success_embed(description: str, *, title: Optional[str] = None) -> discord.Embed:
    """Generic embed for success messages (green)."""
    return discord.Embed(title=title, description=description, color=COLOR_SUCCESS)


def error_embed(description: str, *, title: str = "Помилка") -> discord.Embed:
    """Generic embed for error messages (red)."""
    return discord.Embed(title=title, description=description, color=COLOR_ERROR)


def now_playing_embed(track: Track, *, volume: float) -> discord.Embed:
    """Detailed embed for the currently playing track (used by /nowplaying)."""
    embed = discord.Embed(
        title="Зараз грає",
        description=f"[{track.title}]({track.webpage_url})",
        color=COLOR_INFO,
    )
    embed.add_field(name="Тривалість", value=track.formatted_duration, inline=True)
    embed.add_field(name="Гучність", value=f"{int(volume * 100)}%", inline=True)
    embed.add_field(name="Запитав(ла)", value=track.requester.mention, inline=True)

    if track.uploader:
        embed.set_footer(text=f"Канал: {track.uploader}")
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)

    return embed


def track_added_embed(track: Track, position: int) -> discord.Embed:
    """
    Confirmation embed for the /play command's result.

    position == 0 means the track started playing immediately (the queue
    was empty); any other number is the position the track was added at.
    """
    if position == 0:
        description = f"▶️ Відтворюю зараз: **[{track.title}]({track.webpage_url})**"
    else:
        description = (
            f"➕ Додано в чергу: **[{track.title}]({track.webpage_url})**\n"
            f"Позиція в черзі: **{position}**"
        )

    embed = discord.Embed(description=description, color=COLOR_SUCCESS)
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)

    return embed


def tracks_added_embed(count: int, *, failed: int = 0) -> discord.Embed:
    """
    Confirmation embed for when multiple tracks were enqueued at once
    (e.g. resolving a Spotify album/playlist link to several YouTube searches).
    """
    description = f"➕ Додано **{count}** трек(ів) у черту."
    if failed:
        description += f"\n⚠️ Не вдалося знайти **{failed}** трек(ів)."

    return discord.Embed(description=description, color=COLOR_SUCCESS)


def queue_embed(
    queue: MusicQueue,
    *,
    current: Optional[Track],
    page_size: int = 10,
) -> discord.Embed:
    """Embed listing the tracks in the queue: the current track and up to page_size upcoming ones."""
    embed = discord.Embed(title="Черга відтворення", color=COLOR_INFO)

    if current:
        embed.add_field(
            name="Зараз грає",
            value=f"[{current.title}]({current.webpage_url}) • {current.formatted_duration}",
            inline=False,
        )

    tracks = queue.to_list()

    if not tracks:
        embed.add_field(name="Далі в черзі", value="Черга порожня.", inline=False)
        return embed

    lines = [
        f"`{i}.` [{track.title}]({track.webpage_url}) • {track.formatted_duration} "
        f"— {track.requester.mention}"
        for i, track in enumerate(tracks[:page_size], start=1)
    ]

    if len(tracks) > page_size:
        lines.append(f"\n…і ще {len(tracks) - page_size} трек(ів).")

    embed.add_field(name="Далі в черзі", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"Усього треків у черзі: {len(tracks)}")

    return embed
