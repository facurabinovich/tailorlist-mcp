"""Thin Spotify Web API client: GETs with paging, 429 backoff and token refresh.

Plain requests instead of spotipy: Development Mode apps read playlist contents from
GET playlists/{id}/items (rows carry "item"), which spotipy doesn't use.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterator

import requests

from tailorlist_mcp.sources import spotify_auth

API = "https://api.spotify.com/v1"

_MAX_RETRIES = 5


class SpotifyError(RuntimeError):
    """A Spotify API call failed (after retries)."""


class SpotifyClient:
    def __init__(
        self,
        token_provider: Callable[[], str] = spotify_auth.access_token,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._token = token_provider
        self._http = session or requests.Session()
        self._sleep = sleep

    def request(self, method: str, path_or_url: str, **kwargs) -> dict:
        url = path_or_url if path_or_url.startswith("http") else f"{API}/{path_or_url.lstrip('/')}"
        for attempt in range(_MAX_RETRIES):
            resp = self._http.request(
                method, url, headers={"Authorization": f"Bearer {self._token()}"}, timeout=30, **kwargs
            )
            if resp.status_code == 429:
                self._sleep(min(float(resp.headers.get("Retry-After", 2 ** attempt)), 60))
                continue
            if resp.status_code >= 500:
                self._sleep(2 ** attempt)
                continue
            if resp.status_code >= 400:
                raise SpotifyError(f"Spotify {method} {url} -> {resp.status_code}: {_error_text(resp)}")
            return resp.json() if resp.content else {}
        raise SpotifyError(f"Spotify {method} {url} kept failing after {_MAX_RETRIES} tries.")

    def get(self, path_or_url: str, **params) -> dict:
        return self.request("GET", path_or_url, params=params or None)

    def paged(self, path: str, **params) -> Iterator[dict]:
        """Every item of a paged endpoint, following "next"."""
        page = self.get(path, **params)
        while True:
            yield from page.get("items") or []
            if not page.get("next"):
                return
            page = self.get(page["next"])

    # ---------------------------------------------------------------- endpoints

    def me(self) -> dict:
        return self.get("me")

    def my_playlists(self) -> list[dict]:
        return list(self.paged("me/playlists", limit=50))

    def playlist(self, playlist_id: str) -> dict:
        return self.get(f"playlists/{playlist_id}", fields="id,name,owner(id,display_name),snapshot_id,external_urls,followers(total)")

    def playlist_items(self, playlist_id: str) -> Iterator[dict]:
        return self.paged(f"playlists/{playlist_id}/items", limit=100)

    def tracks(self, track_ids: list[str], market: str | None = None) -> list[dict | None]:
        """Track objects in the order asked (None where Spotify has nothing). With
        market="from_token" Spotify relinks: a replaced track comes back as the version it
        serves now, with the asked ID in linked_from."""
        out: list[dict | None] = []
        for i in range(0, len(track_ids), 50):
            params = {"ids": ",".join(track_ids[i : i + 50])}
            if market:
                params["market"] = market
            out.extend(self.get("tracks", **params).get("tracks") or [])
        return out

    def search_tracks(self, query: str, limit: int = 5) -> list[dict]:
        """Track search, every market (market=from_token needs the user-read-private scope; check
        playability with tracks(ids, market="from_token") instead)."""
        return [t for t in (self.get("search", q=query, type="track", limit=limit).get("tracks") or {}).get("items") or [] if t]

    def artists(self, artist_ids: list[str]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(artist_ids), 50):
            batch = artist_ids[i : i + 50]
            out.extend(a for a in self.get("artists", ids=",".join(batch)).get("artists") or [] if a)
        return out


def _error_text(resp: requests.Response) -> str:
    try:
        err = resp.json().get("error")
        if isinstance(err, dict):
            return err.get("message") or str(err)
        return str(err or resp.text[:200])
    except ValueError:
        return resp.text[:200]
