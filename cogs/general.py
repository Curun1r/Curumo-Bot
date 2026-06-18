"""
General utility commands not related to music: /ping, /about.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils import embeds


class GeneralCog(commands.Cog):
    """Basic utility commands for the bot."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="Перевірити, чи бот живий, і яка затримка до Discord")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"🏓 Pong! Затримка: {latency_ms} мс")
        )

    @app_commands.command(name="about", description="Інформація про бота")
    async def about(self, interaction: discord.Interaction) -> None:
        embed = embeds.success_embed(
            "Музичний бот на discord.py + yt-dlp + FFmpeg.\n"
            "Команди керування музикою: /play, /queue, /pause, /resume, "
            "/skip, /stop, /volume, /nowplaying."
        )
        embed.title = str(self.bot.user)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Entry point called by bot.load_extension('cogs.general')."""
    await bot.add_cog(GeneralCog(bot))
