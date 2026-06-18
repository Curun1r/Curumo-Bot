"""
Track queue for a single server (Guild).

Implemented on top of collections.deque — it gives O(1) appends at the end
and removals from the front, which maps perfectly onto the playback model
(append to the back of the queue, take the next track from the front).
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass
from typing import Optional

import discord


@dataclass
class Track:
    """
    Information about a single track in the queue.

    stream_url — a direct link to the audio stream that core/search.py gets
    from yt-dlp and that is passed to FFmpeg for playback. This link is
    temporary (valid for a limited time), so the track shouldn't be cached
    for long.
    """

    title: str
    webpage_url: str            # link that can be shown to the user (e.g. YouTube)
    stream_url: str             # direct audio stream link for FFmpeg
    duration: Optional[int]     # duration in seconds; None for live streams
    requester: discord.Member   # who added the track to the queue
    thumbnail: Optional[str] = None
    uploader: Optional[str] = None

    def __str__(self) -> str:
        return f"{self.title} ({self.formatted_duration})"

    @property
    def formatted_duration(self) -> str:
        """Duration formatted as 'M:SS' / 'H:MM:SS', or 'LIVE' for streams."""
        if self.duration is None:
            return "LIVE"

        hours, remainder = divmod(self.duration, 3600)
        minutes, seconds = divmod(remainder, 60)

        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"


class QueueFullError(Exception):
    """Raised when trying to add a track to a queue that has reached max_size."""


class MusicQueue:
    """
    Playback queue for a single voice connection (one server).

    A thin wrapper around deque: it hides the underlying data structure
    behind a clear API (add / pop_next / peek / remove / clear / shuffle),
    so that Player works with the queue through a stable interface without
    worrying about implementation details.
    """

    def __init__(self, max_size: int = 0) -> None:
        # max_size <= 0 means "unlimited"
        self._max_size = max_size
        self._tracks: deque[Track] = deque()

    def __len__(self) -> int:
        return len(self._tracks)

    def __bool__(self) -> bool:
        return bool(self._tracks)

    @property
    def is_full(self) -> bool:
        return self._max_size > 0 and len(self._tracks) >= self._max_size

    def add(self, track: Track) -> None:
        """Appends a track to the end of the queue. Raises QueueFullError if the queue is full."""
        if self.is_full:
            raise QueueFullError(f"Черга заповнена (максимум {self._max_size} треків).")
        self._tracks.append(track)

    def pop_next(self) -> Optional[Track]:
        """Removes and returns the next track (front of the queue). None if the queue is empty."""
        if not self._tracks:
            return None
        return self._tracks.popleft()

    def peek(self) -> Optional[Track]:
        """Returns the next track without removing it from the queue."""
        if not self._tracks:
            return None
        return self._tracks[0]

    def remove(self, index: int) -> Optional[Track]:
        """
        Removes a track by its position in the queue (0 — the next track).
        Returns the removed track, or None if the index is out of range.
        """
        if 0 <= index < len(self._tracks):
            track = self._tracks[index]
            del self._tracks[index]
            return track
        return None

    def shuffle(self) -> None:
        """Randomly shuffles the order of tracks in the queue."""
        tracks = list(self._tracks)
        random.shuffle(tracks)
        self._tracks = deque(tracks)

    def clear(self) -> None:
        """Completely clears the queue (e.g. on /stop)."""
        self._tracks.clear()

    def to_list(self) -> list[Track]:
        """Returns a copy of the queue as a list — handy for displaying in /queue."""
        return list(self._tracks)
