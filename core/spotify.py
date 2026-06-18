"""
Resolves Spotify links into search-friendly text queries.

Spotify tracks are DRM-protected — there's no public stream URL yt-dlp can
extract from an open.spotify.com link, unlike YouTube/SoundCloud. So instead
of trying to play the file itself, this module looks up the track's title
and artist(s) via the Spotify Web API and turns that into a plain text
query ("<artists> - <title>"). That query is then handed to core.search.search()
exactly like a normal /play <query>, which finds and plays the closest match
on YouTube.

Authentication uses the Client Credentials flow: app-only access, no user
login involved, just enough to read public track/album/playlist metadata.
Requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET (see config.py / .env.example).
"""

from __future__ import annotations

import re
import time
from typing import Optional

import requests

import config

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_API_BASE = "https://api.spotify.com/v1"

# Spotify sometimes prefixes links with a locale segment, e.g.
# open.spotify.com/intl-uk/track/<id> — the (?:intl-\w+/)? part absorbs that.
_TRACK_RE = re.compile(r"open\.spotify\.com/(?:intl-\w+/)?track/([a-zA-Z0-9]+)")
_ALBUM_RE = re.compile(r"open\.spotify\.com/(?:intl-\w+/)?album/([a-zA-Z0-9]+)")
_PLAYLIST_RE = re.compile(r"open\.spotify\.com/(?:intl-\w+/)?playlist/([a-zA-Z0-9]+)")

# Cached app-only access token, shared across calls until it expires.
_cached_token: Optional[str] = None
_token_expires_at: float = 0.0


class SpotifyError(Exception):
    """Raised when a Spotify link can't be resolved (missing credentials, API error, unsupported link, etc.)."""


def is_spotify_url(query: str) -> bool:
    """True if the query looks like an open.spotify.com link."""
    return "open.spotify.com" in query


def _get_access_token() -> str:
    """
    Returns a cached access token, requesting a fresh one from Spotify only
    if the cached one is missing or close to expiring.
    """
    global _cached_token, _token_expires_at

    if _cached_token and time.time() < _token_expires_at:
        return _cached_token

    if not config.SPOTIFY_CLIENT_ID or not config.SPOTIFY_CLIENT_SECRET:
        raise SpotifyError(
            "Підтримка Spotify не налаштована: додай SPOTIFY_CLIENT_ID і "
            "SPOTIFY_CLIENT_SECRET у файл .env (безкоштовно на "
            "developer.spotify.com/dashboard)."
        )

    response = requests.post(
        _TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(config.SPOTIFY_CLIENT_ID, config.SPOTIFY_CLIENT_SECRET),
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()

    _cached_token = payload["access_token"]
    # Refresh a bit early (60s buffer) rather than cutting it exactly at expiry.
    _token_expires_at = time.time() + payload.get("expires_in", 3600) - 60
    return _cached_token


def _api_get(path: str) -> dict:
    """GET helper against the Spotify Web API with the cached bearer token."""
    token = _get_access_token()
    response = requests.get(
        f"{_API_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def _track_query(track: dict) -> str:
    """Turns a Spotify track object into an 'Artist - Title' text query for YouTube search."""
    artists = ", ".join(artist.get("name", "") for artist in track.get("artists", []))
    title = track.get("name", "")
    return f"{artists} - {title}".strip(" -")


def resolve(url: str) -> list[str]:
    """
    Resolves a Spotify track/album/playlist URL into a list of YouTube
    search queries (one per track), ready to be passed into core.search.search().

    Album/playlist links are capped at config.SPOTIFY_MAX_TRACKS tracks to
    keep /play responsive — each query still needs its own separate YouTube
    search afterwards.

    Raises SpotifyError if credentials are missing, the link type isn't
    supported, or the Spotify API call fails.
    """
    track_match = _TRACK_RE.search(url)
    if track_match:
        track = _api_get(f"/tracks/{track_match.group(1)}")
        return [_track_query(track)]

    album_match = _ALBUM_RE.search(url)
    if album_match:
        data = _api_get(f"/albums/{album_match.group(1)}/tracks?limit={config.SPOTIFY_MAX_TRACKS}")
        return [_track_query(item) for item in data.get("items", [])]

    playlist_match = _PLAYLIST_RE.search(url)
    if playlist_match:
        data = _api_get(f"/playlists/{playlist_match.group(1)}/tracks?limit={config.SPOTIFY_MAX_TRACKS}")
        return [
            _track_query(item["track"])
            for item in data.get("items", [])
            if item.get("track")
        ]

    raise SpotifyError(
        "Це посилання Spotify не підтримується (очікую трек, альбом або плейлист)."
    )
