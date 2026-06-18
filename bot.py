"""
Bot entry point.

Creates the Bot instance, loads cogs (cogs/general.py, cogs/music.py),
syncs slash commands with Discord, and runs the bot using the token from
config.py.

Run:
    python bot.py
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

import config

logger = logging.getLogger(__name__)


# Intents are the bot's "subscription" to categories of events from the
# Discord Gateway. Intents.default() already includes guilds, voice_states,
# etc. — exactly what's needed for slash commands and voice channel
# handling. This bot doesn't need privileged intents (Message Content,
# Presences, Members) — those would have to be enabled separately in the
# Developer Portal.
intents = discord.Intents.default()


class MusicBot(commands.Bot):
    """
    Extension of the standard commands.Bot.

    All async setup (loading cogs, syncing commands) happens in
    setup_hook — this is the recommended approach in discord.py 2.x, since
    a working event loop already exists by the time it's called.
    """

    # Paths to cog modules in "package.module" format, as expected by
    # load_extension. Each such module must have an async setup(bot) function.
    INITIAL_COGS = (
        "cogs.general",
        "cogs.music",
    )

    def __init__(self) -> None:
        super().__init__(command_prefix=config.COMMAND_PREFIX, intents=intents)

    async def setup_hook(self) -> None:
        """Called once by the library before connecting to the Gateway."""
        for extension in self.INITIAL_COGS:
            try:
                await self.load_extension(extension)
                logger.info("Loaded cog: %s", extension)
            except Exception:
                logger.exception("Failed to load cog: %s", extension)

        # Sync slash commands with Discord: without this call, new or
        # changed commands (/play, /skip, etc.) won't show up in the user
        # interface.
        #
        # Dev tip: a global sync can take up to an hour to propagate. While
        # testing on a single server, it's faster to sync only there —
        # uncomment and fill in your server ID:
        #
        #   guild = discord.Object(id=YOUR_GUILD_ID)
        #   self.tree.copy_global_to(guild=guild)
        #   synced = await self.tree.sync(guild=guild)
        synced = await self.tree.sync()
        logger.info("Synced slash commands: %d", len(synced))

    async def on_ready(self) -> None:
        """Called every time the connection to Discord is established and the bot is ready."""
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id)
        logger.info("Bot is active on %d server(s)", len(self.guilds))

        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.listening, name="/play")
        )


def _setup_logging() -> None:
    """Basic logging configuration based on config.LOG_LEVEL."""
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    # discord.py is very "chatty" at DEBUG level — quiet it down to INFO so
    # the bot's own logs don't get lost in the library's internal messages.
    logging.getLogger("discord").setLevel(logging.INFO)


def main() -> None:
    _setup_logging()

    bot = MusicBot()

    try:
        # log_handler=None disables discord.py's built-in logging setup, so
        # our own (from _setup_logging) is used instead and logs aren't duplicated.
        bot.run(config.DISCORD_TOKEN, log_handler=None)
    except discord.LoginFailure:
        logger.error("Failed to log in to Discord: check DISCORD_TOKEN in the .env file")


if __name__ == "__main__":
    main()
