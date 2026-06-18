"""
Wrapper around yt-dlp: turns a search query or link into track data with a
direct audio stream link, ready to be played through FFmpeg.

yt-dlp is a synchronous and relatively slow library (network requests,
parsing), so every call is run in a separate thread via
asyncio.run_in_executor — this keeps it from blocking the event loop and
"freezing" the bot while it searches.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Optional

import yt_dlp

logger = logging.getLogger(__name__)


# yt-dlp settings:
#   - format: take the best audio track (video isn't needed — FFmpeg will
#     drop it with the -vn option anyway, but it's better not to pick video
#     formats at all to avoid pulling extra megabytes for nothing);
#   - noplaylist: a playlist link -> process only the specific track;
#   - default_search: a "bare" text query automatically becomes a YouTube
#     search (the string is turned into "ytsearch:<query>");
#   - quiet/no_warnings: don't print yt-dlp's internal messages to the console.
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "default_search": "ytsearch",
    "quiet": True,
    "no_warnings": True,
    "ignoreerrors": False,
    "source_address": "0.0.0.0",  # works around known IPv6 issues on some hosts
}


class TrackNotFoundError(Exception):
    """Raised when no track could be found or processed for the given query."""


class SearchResult:
    """
    A compact search result — only what's needed to later build a
    core.queue.Track. Kept separate from Track because Track additionally
    carries requester (the member who made the request), which is the
    responsibility of the cog/player layer.
    """

    __slots__ = ("title", "webpage_url", "stream_url", "duration", "thumbnail", "uploader")

    def __init__(
        self,
        title: str,
        webpage_url: str,
        stream_url: str,
        duration: Optional[int],
        thumbnail: Optional[str],
        uploader: Optional[str],
    ) -> None:
        self.title = title
        self.webpage_url = webpage_url
        self.stream_url = stream_url
        self.duration = duration
        self.thumbnail = thumbnail
        self.uploader = uploader


def _extract(query: str) -> dict[str, Any]:
    """
    Synchronous "worker" function — runs in a separate executor thread.

    If query is plain text, default_search turns it into a search query and
    info will contain a list of results under the 'entries' key (we take the
    first non-empty one). If query is a direct link, info already contains
    the track data.
    """
    with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
        info = ydl.extract_info(query, download=False)

    if info and "entries" in info:
        entries = [entry for entry in info["entries"] if entry]
        if not entries:
            raise TrackNotFoundError(f"Нічого не знайдено за запитом: {query!r}")
        info = entries[0]

    if not info:
        raise TrackNotFoundError(f"Нічого не знайдено за запитом: {query!r}")

    return info


def _to_search_result(info: dict[str, Any]) -> SearchResult:
    """Converts a raw dict from yt-dlp into a SearchResult with a direct stream link."""
    stream_url = info.get("url")

    if not stream_url:
        # For some extractors the direct link isn't at the top level but
        # nested inside the formats list — try the last one (usually the best).
        formats = info.get("formats") or []
        if formats:
            stream_url = formats[-1].get("url")

    if not stream_url:
        raise TrackNotFoundError("Не вдалося отримати посилання на аудіопотік для цього треку.")

    return SearchResult(
        title=info.get("title") or "Без назви",
        webpage_url=info.get("webpage_url") or info.get("original_url") or "",
        stream_url=stream_url,
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail"),
        uploader=info.get("uploader"),
    )


async def search(query: str, *, loop: Optional[asyncio.AbstractEventLoop] = None) -> SearchResult:
    """
    Finds a track by a text query or link (YouTube, SoundCloud, etc. —
    anything yt-dlp supports) and returns a SearchResult with a direct
    audio stream.

    Called from the /play command like this:
        result = await search(user_query)
        track = Track(title=result.title, ..., requester=interaction.user)
    """
    loop = loop or asyncio.get_running_loop()

    # run_in_executor only passes positional arguments to the function, so
    # query is bound ahead of time via functools.partial.
    extractor = functools.partial(_extract, query)

    try:
        info = await loop.run_in_executor(None, extractor)
    except yt_dlp.utils.DownloadError as exc:
        logger.warning("yt-dlp failed to process query %r: %s", query, exc)
        raise TrackNotFoundError(f"Не вдалося обробити запит: {query!r}") from exc

    return _to_search_result(info)
