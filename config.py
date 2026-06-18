"""
Bot configuration.

All settings are loaded from environment variables (a .env file in the
project root). This keeps the token and other sensitive data out of the
code and out of git.

Create a .env file next to this one based on .env.example:
    DISCORD_TOKEN=your_bot_token
"""

import os

from dotenv import load_dotenv

# Load .env into the process environment variables (if the file doesn't
# exist, nothing breaks — os.getenv below will just return None / the default)
load_dotenv()


# --- Required settings ---

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not DISCORD_TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN not found in the environment. "
        "Create a .env file in the project root with the line:\n"
        "DISCORD_TOKEN=your_bot_token"
    )


# --- General bot settings ---

# Prefix for text commands. The bot's primary interface is slash commands
# (/play etc.), but discord.py still requires a command_prefix when creating Bot.
COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")


# --- Player settings ---

# Default volume for a new player (range 0.0 - 2.0, where 1.0 = 100%)
DEFAULT_VOLUME = float(os.getenv("DEFAULT_VOLUME", "0.5"))

# Maximum queue size per server. 0 = unlimited.
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "0"))

# How many seconds the bot waits in a voice channel with no listeners/tracks
# before disconnecting on its own (to free up resources).
INACTIVITY_TIMEOUT = int(os.getenv("INACTIVITY_TIMEOUT", "300"))


# --- FFmpeg settings for audio streaming ---

# Options BEFORE the input source: automatic reconnect on dropped connections.
# Important for long tracks and unstable links returned by yt-dlp.
FFMPEG_BEFORE_OPTIONS = (
    "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
)

# Options for the transcoding itself: "-vn" = discard the video track, audio only.
# Discord expects Opus — PCMVolumeTransformer/FFmpegPCMAudio handle the encoding.
FFMPEG_OPTIONS = "-vn"


# --- Spotify integration (optional) ---

# App-only credentials from https://developer.spotify.com/dashboard (Client
# Credentials flow — no user login needed, just enough to read public track/
# album/playlist metadata). Not required at startup: only used if someone
# pastes an open.spotify.com link into /play. If unset, Spotify links will
# fail with a clear error telling the user what to add to .env.
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

# Safety cap on how many tracks get resolved/enqueued from a single Spotify
# album or playlist link, so one big playlist can't flood the queue or take
# forever to process (each track still needs its own YouTube search).
SPOTIFY_MAX_TRACKS = int(os.getenv("SPOTIFY_MAX_TRACKS", "25"))


# --- Logging ---

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
