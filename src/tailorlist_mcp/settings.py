"""Where things live and how the user's Spotify app is configured.

Everything is local: one data directory (default ~/.tailorlist-mcp, override with
TAILORLIST_HOME) holds the OAuth token and the SQLite cache. The Spotify app settings come
from environment variables, which MCP clients pass in their server config, or from a .env
file in the data directory.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"

SCOPES = (
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-library-read",
)


def data_dir() -> Path:
    path = Path(os.environ.get("TAILORLIST_HOME") or Path.home() / ".tailorlist-mcp")
    path.mkdir(parents=True, exist_ok=True)
    return path


def token_path() -> Path:
    return data_dir() / "token.json"


def cache_path() -> Path:
    return data_dir() / "cache.db"


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class SpotifyApp:
    client_id: str
    redirect_uri: str


class NotConfigured(RuntimeError):
    """The user's Spotify app settings are missing."""


def spotify_app() -> SpotifyApp:
    """The user's Spotify app: environment variables first, then <data dir>/.env."""
    file_values = _read_env_file(data_dir() / ".env")

    def get(key: str) -> str:
        return (os.environ.get(key) or file_values.get(key) or "").strip()

    client_id = get("SPOTIFY_CLIENT_ID")
    if not client_id:
        raise NotConfigured(
            "SPOTIFY_CLIENT_ID is not set. Create an app at "
            "https://developer.spotify.com/dashboard, add the redirect URI "
            f"{get('SPOTIFY_REDIRECT_URI') or DEFAULT_REDIRECT_URI} to it, and set "
            "SPOTIFY_CLIENT_ID in the MCP server config (or in "
            f"{data_dir() / '.env'})."
        )
    return SpotifyApp(client_id=client_id, redirect_uri=get("SPOTIFY_REDIRECT_URI") or DEFAULT_REDIRECT_URI)
