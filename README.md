# Curumo Bot

A self-hosted Discord music bot written in Python. Streams audio from YouTube (and anything else [yt-dlp](https://github.com/yt-dlp/yt-dlp) supports) straight into a voice channel, resolves Spotify links into playable tracks, and keeps a fully independent player per server.

Built with **discord.py 2.x**, **yt-dlp**, **FFmpeg** and plain **asyncio** — no Lavalink, no database, no external services to babysit.

> The bot's chat responses are in Ukrainian (that's its home audience); the codebase, comments and docs are in English.

## Features

- **Slash commands only** — modern Discord UX with autocomplete and typed parameters
- **Per-server players** — every guild gets its own queue, volume and voice connection; servers never interfere with each other
- **Spotify link support** — `open.spotify.com` track/album/playlist links are resolved through the Spotify Web API into "Artist – Title" queries and played back via YouTube (Spotify itself can't be streamed — DRM)
- **True streaming** — audio goes source → FFmpeg → Opus → Discord with no files ever touching the disk
- **Loop modes** — repeat the current track or cycle the whole queue
- **Seek** — jump to any position in the current track (`90`, `1:30`, `1:02:15`)
- **Idle auto-disconnect** — the bot leaves the voice channel after a configurable period of silence
- **Voice-drop recovery** — if the connection dies mid-song, the bot rejoins and restarts the interrupted track on its own
- **Friendly errors** — private, age-restricted, region-blocked and not-yet-premiered videos produce a specific human message instead of a generic failure

## Commands

| Command | Description |
|---|---|
| `/play <query or URL>` | Search YouTube by text, or play a direct link (YouTube, SoundCloud, Spotify, …) |
| `/pause` / `/resume` | Pause / resume playback |
| `/skip` | Skip the current track (works in loop mode too) |
| `/stop` | Stop playback and clear the queue |
| `/seek <position>` | Jump to a position: seconds or `M:SS` / `H:MM:SS` |
| `/queue` | Show the current track and what's up next |
| `/shuffle` | Shuffle the queue |
| `/remove <n>` | Remove track number *n* from the queue |
| `/loop <off\|track\|queue>` | Set the repeat mode |
| `/volume <0-200>` | Set playback volume |
| `/nowplaying` | Details about the current track |
| `/ping`, `/about` | Latency check and bot info |

## Architecture

```
Curumo_bot/
├── bot.py               # entry point: loads cogs, syncs slash commands
├── config.py            # all settings, read from .env
├── cogs/
│   ├── music.py         # slash commands — thin layer, no playback logic
│   └── general.py       # /ping, /about
├── core/
│   ├── player.py        # per-guild Player: queue, FFmpeg source, loop modes,
│   │                    # idle timer, reconnect logic
│   ├── queue.py         # Track dataclass + deque-based MusicQueue
│   ├── search.py        # yt-dlp wrapper, runs in a thread executor
│   └── spotify.py       # Spotify Web API → text queries for yt-dlp
└── utils/
    ├── checks.py        # slash-command permission checks (same voice channel, …)
    └── embeds.py        # all Discord message formatting in one place
```

Design decisions worth calling out:

- **Strict layering.** `core/` knows nothing about Discord interactions or embeds; `cogs/` contains no playback logic. Events that need a user-facing message (e.g. the idle disconnect) cross the boundary through callbacks wired up by the cog.
- **yt-dlp never blocks the event loop.** It's a synchronous library, so every call runs in `asyncio.run_in_executor`.
- **The `after=` callback minefield.** discord.py invokes the end-of-track callback from its own playback thread. All queue advancement is marshalled back onto the event loop via `run_coroutine_threadsafe`, and deliberate stream restarts (seek) use a one-shot flag so the queue doesn't advance by accident.
- **No persistence by design.** Queues live in memory; a restart gives every server a clean slate. For a music bot this is a feature, not a shortcut.

## Getting started

### Prerequisites

- Python 3.10+
- FFmpeg on `PATH` (`brew install ffmpeg` / `apt install ffmpeg`)
- A Discord application with a bot token ([Developer Portal](https://discord.com/developers/applications)) — enable the **Message Content** intent is *not* required; the bot is slash-command only, but it does need the `applications.commands` scope and voice permissions when you invite it

### Install & run

```bash
git clone git@github.com:Curun1r/Curumo-Bot.git
cd Curumo-Bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # then paste your DISCORD_TOKEN into .env
python bot.py
```

### Configuration

Everything is set through `.env` (see `.env.example` for the full annotated list):

| Variable | Default | Meaning |
|---|---|---|
| `DISCORD_TOKEN` | — | **Required.** Bot token |
| `DEFAULT_VOLUME` | `0.5` | Initial volume (0.0–2.0) |
| `MAX_QUEUE_SIZE` | `0` | Per-server queue cap, `0` = unlimited |
| `INACTIVITY_TIMEOUT` | `300` | Seconds of idle before auto-disconnect, `0` = never |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | — | Optional; enables Spotify links ([get them here](https://developer.spotify.com/dashboard), Client Credentials — no user login) |
| `SPOTIFY_MAX_TRACKS` | `25` | Cap on tracks pulled from one album/playlist link |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

## Deployment

The repo ships with two ready-made options:

- **`deploy/curumo-bot.service`** — a systemd unit for a VPS (Oracle Cloud Free Tier works well). Copy it to `/etc/systemd/system/`, adjust the paths, `systemctl enable --now curumo-bot`.
- **`Procfile`** — for Railway or any Railpack/buildpack-style platform (`worker: python bot.py`). Remember to add `ffmpeg` to the build's apt packages and set the env variables in the platform dashboard.

One caveat for any cloud host: YouTube is aggressive about datacenter IPs, so yt-dlp may occasionally hit bot-detection walls that never appear on a residential connection.

## License

MIT
