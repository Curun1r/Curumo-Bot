"""
Access checks for the music slash commands.

Implemented as predicates for app_commands.check: if a check fails, it
raises its own CheckFailure subclass with a clear message. That message
can be neatly shown to the user in the on_app_command_error handler
(see cogs/music.py) — instead of a generic "An error occurred".
"""

from __future__ import annotations

from typing import Optional, Union

import discord
from discord import app_commands


class NotInVoiceChannel(app_commands.CheckFailure):
    """The user isn't connected to any voice channel."""


class BotNotInVoiceChannel(app_commands.CheckFailure):
    """The bot is currently not connected to a voice channel on this server."""


class NotInSameVoiceChannel(app_commands.CheckFailure):
    """The user is in a different voice channel than the bot."""


def _voice_state(user: Union[discord.Member, discord.User]) -> Optional[discord.VoiceState]:
    """
    Returns the user's voice state, if interaction.user is a guild member.

    In DMs, interaction.user is a plain User without a voice attribute.
    getattr saves us from an AttributeError in that case by returning None.
    """
    return getattr(user, "voice", None)


def in_voice_channel():
    """
    Allows the command to run only if the user is sitting in a voice channel.

    Needed mainly for /play — the bot needs to know which channel to join.
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        voice_state = _voice_state(interaction.user)

        if voice_state is None or voice_state.channel is None:
            raise NotInVoiceChannel("Спершу зайди в голосовий канал.")

        return True

    return app_commands.check(predicate)


def bot_in_voice_channel():
    """Allows the command to run only if the bot is currently connected to a voice channel."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild.voice_client is None:
            raise BotNotInVoiceChannel("Зараз я не підключений до жодного голосового каналу.")

        return True

    return app_commands.check(predicate)


def same_voice_channel():
    """
    Allows the command to run only if the user is in the same voice channel
    as the bot.

    Prevents someone in a different channel from pausing or stopping music
    for the people actually listening to it. Covers both checks above —
    no need to add them separately alongside this one.
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        voice_state = _voice_state(interaction.user)
        if voice_state is None or voice_state.channel is None:
            raise NotInVoiceChannel("Спершу зайди в голосовий канал.")

        guild = interaction.guild
        if guild is None or guild.voice_client is None:
            raise BotNotInVoiceChannel("Зараз я не підключений до жодного голосового каналу.")

        if voice_state.channel.id != guild.voice_client.channel.id:
            raise NotInSameVoiceChannel("Ти маєш бути в одному голосовому каналі зі мною.")

        return True

    return app_commands.check(predicate)
