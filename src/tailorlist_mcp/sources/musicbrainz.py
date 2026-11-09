"""MusicBrainz genres for artists Spotify leaves without any.

Rules of the MusicBrainz API (https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting):
  - at most about 1 request per second, or it answers 503: every request here goes through one
    process-wide throttle;
  - a descriptive User-Agent with a way to reach the app (a project URL, never a personal email);
  - back off when it answers 503.

Lookup order for one artist:
  1. by the artist's Spotify URL (a URL relation some MusicBrainz artists carry);
  2. otherwise by name, accepting only a score-100 result whose name matches exactly
     (case-insensitive), so a namesake or a near match never gives wrong genres.
Then the artist's genres are read (its top tags when it has no genres yet).
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

from tailorlist_mcp import __version__

API = "https://musicbrainz.org/ws/2"
PROJECT_URL = "https://app.tailorlist.app"
USER_AGENT = f"tailorlist-mcp/{__version__} ( {PROJECT_URL} )"

MIN_INTERVAL = 1.1          # seconds between requests (MusicBrainz allows 1/s)
_MAX_TRIES = 5
_MAX_BACKOFF = 60.0
_MAX_GENRES = 10
_MAX_TAGS = 5               # the fallback when an artist has no genres, only free-form tags


class MusicBrainzError(RuntimeError):
    """MusicBrainz kept failing or refusing (after backoff). Nothing was learned about the artist."""


class Throttle:
    """Spaces requests at least min_interval apart, across threads."""

    def __init__(
        self,
        min_interval: float = MIN_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:  # sleeping under the lock queues the other threads behind this one
            now = self._clock()
            if now < self._next_at:
                self._sleep(self._next_at - now)
                now = self._clock()
            self._next_at = now + self.min_interval


#: Shared by every client in this process.
GLOBAL_THROTTLE = Throttle()


@dataclass(frozen=True)
class Found:
    mbid: str
    genres: list[str]
    source: str           # "url" or "search"


class MusicBrainzClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        throttle: Throttle | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._http = session or requests.Session()
        self._throttle = throttle or GLOBAL_THROTTLE
        self._sleep = sleep

    # ---------------------------------------------------------------- HTTP

    def _get(self, path: str, **params) -> dict | None:
        """GET one MusicBrainz JSON document; None when it doesn't exist (404)."""
        params["fmt"] = "json"
        for attempt in range(_MAX_TRIES):
            self._throttle.wait()
            try:
                resp = self._http.get(
                    f"{API}/{path}", params=params, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    timeout=20,
                )
            except requests.RequestException:
                self._sleep(min(2 ** (attempt + 1), _MAX_BACKOFF))
                continue
            if resp.status_code == 404:
                return None
            if resp.status_code in (429, 502, 503, 504):
                try:
                    wait = float(resp.headers.get("Retry-After", ""))
                except ValueError:
                    wait = 2 ** (attempt + 1)
                self._sleep(min(wait, _MAX_BACKOFF))
                continue
            if resp.status_code >= 400:
                raise MusicBrainzError(f"MusicBrainz {path} -> {resp.status_code}")
            try:
                return resp.json()
            except ValueError as exc:
                raise MusicBrainzError(f"MusicBrainz {path} returned invalid JSON") from exc
        raise MusicBrainzError(f"MusicBrainz {path} kept failing or answering 503 after {_MAX_TRIES} tries.")

    # ---------------------------------------------------------------- lookups

    def artist_by_spotify_id(self, spotify_id: str) -> str | None:
        """MusicBrainz artist ID that carries this Spotify artist URL, if any."""
        doc = self._get("url", resource=f"https://open.spotify.com/artist/{spotify_id}", inc="artist-rels")
        for rel in (doc or {}).get("relations") or []:
            artist = rel.get("artist")
            if artist and artist.get("id"):
                return artist["id"]
        return None

    def artist_by_name(self, name: str) -> str | None:
        """MusicBrainz artist ID of the one exact, score-100 name match.

        None when there is no such match or several (namesakes): no genres beats wrong genres.
        """
        doc = self._get("artist", query=f'artist:"{_lucene_escape(name)}"', limit=5)
        wanted = name.strip().casefold()
        matches = [
            artist["id"] for artist in (doc or {}).get("artists") or []
            if int(artist.get("score", 0)) == 100 and (artist.get("name") or "").strip().casefold() == wanted
        ]
        return matches[0] if len(matches) == 1 else None

    def genres_of(self, mbid: str) -> list[str]:
        """Genres of a MusicBrainz artist, most voted first; its top tags when it has no genres."""
        doc = self._get(f"artist/{mbid}", inc="genres+tags") or {}
        genres = _names(doc.get("genres"), _MAX_GENRES)
        return genres or _names(doc.get("tags"), _MAX_TAGS)

    def lookup(self, spotify_id: str, name: str) -> Found | None:
        """Genres for one artist, or None when MusicBrainz has no trustworthy match or no genres."""
        source = "url"
        mbid = self.artist_by_spotify_id(spotify_id)
        if not mbid:
            source = "search"
            mbid = self.artist_by_name(name) if name.strip() else None
        if not mbid:
            return None
        genres = self.genres_of(mbid)
        return Found(mbid, genres, source) if genres else None


def _names(items: list[dict] | None, limit: int) -> list[str]:
    ranked = sorted((i for i in items or [] if i.get("name")), key=lambda i: -int(i.get("count") or 0))
    seen: list[str] = []
    for item in ranked:
        tag = item["name"].strip().lower()
        if tag and tag not in seen:
            seen.append(tag)
    return seen[:limit]


def _lucene_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
