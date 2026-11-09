"""Local SQLite cache of the playlists the user loaded.

A cache, not an archive (Spotify Developer Terms IV.3): playlists are re-read when their
snapshot_id changes, and forget() deletes everything.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from tailorlist_mcp import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS playlists (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    owner_id    TEXT,
    owner_name  TEXT,
    snapshot_id TEXT,
    url         TEXT,
    skipped     INTEGER NOT NULL DEFAULT 0,   -- episodes, local files, unavailable items
    loaded_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tracks (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    album        TEXT,
    album_id     TEXT,
    release_date TEXT,
    duration_ms  INTEGER,
    explicit     INTEGER,
    popularity   INTEGER,
    isrc         TEXT,
    url          TEXT
);
CREATE TABLE IF NOT EXISTS track_artists (
    track_id  TEXT NOT NULL,
    artist_id TEXT NOT NULL,
    position  INTEGER NOT NULL,
    PRIMARY KEY (track_id, position)
);
CREATE TABLE IF NOT EXISTS artists (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    genres     TEXT,          -- JSON list; NULL = not fetched yet
    popularity INTEGER
);
CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id TEXT NOT NULL,
    position    INTEGER NOT NULL,
    track_id    TEXT NOT NULL,
    added_at    TEXT,
    PRIMARY KEY (playlist_id, position)
);
CREATE INDEX IF NOT EXISTS playlist_tracks_track ON playlist_tracks(track_id);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or settings.cache_path())
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def loaded_snapshots(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["id"]: r["snapshot_id"] for r in conn.execute("SELECT id, snapshot_id FROM playlists")}


def save_playlist(conn: sqlite3.Connection, playlist: dict, rows: list[dict], skipped: int) -> None:
    """Replace one playlist's contents. rows: parsed tracks in playlist order (see parse_item)."""
    pid = playlist["id"]
    with conn:
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (pid,))
        conn.execute(
            "INSERT OR REPLACE INTO playlists (id, name, owner_id, owner_name, snapshot_id, url, skipped, loaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pid,
                playlist.get("name") or "",
                (playlist.get("owner") or {}).get("id"),
                (playlist.get("owner") or {}).get("display_name"),
                playlist.get("snapshot_id"),
                (playlist.get("external_urls") or {}).get("spotify"),
                skipped,
                int(time.time()),
            ),
        )
        for position, row in enumerate(rows):
            t = row["track"]
            conn.execute(
                "INSERT OR REPLACE INTO tracks (id, name, album, album_id, release_date, duration_ms, explicit, "
                "popularity, isrc, url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (t["id"], t["name"], t["album"], t["album_id"], t["release_date"], t["duration_ms"],
                 t["explicit"], t["popularity"], t["isrc"], t["url"]),
            )
            conn.execute("DELETE FROM track_artists WHERE track_id = ?", (t["id"],))
            for apos, (aid, aname) in enumerate(row["artists"]):
                conn.execute(
                    "INSERT OR REPLACE INTO track_artists (track_id, artist_id, position) VALUES (?, ?, ?)",
                    (t["id"], aid, apos),
                )
                conn.execute(
                    "INSERT INTO artists (id, name) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET name = excluded.name",
                    (aid, aname),
                )
            conn.execute(
                "INSERT INTO playlist_tracks (playlist_id, position, track_id, added_at) VALUES (?, ?, ?, ?)",
                (pid, position, t["id"], row["added_at"]),
            )
        _prune(conn)  # tracks a reload dropped


def artists_missing_details(conn: sqlite3.Connection) -> list[str]:
    return [r["id"] for r in conn.execute("SELECT id FROM artists WHERE genres IS NULL")]


def save_artist_details(conn: sqlite3.Connection, artists: list[dict]) -> None:
    with conn:
        for a in artists:
            conn.execute(
                "UPDATE artists SET genres = ?, popularity = ? WHERE id = ?",
                (json.dumps(a.get("genres") or []), a.get("popularity"), a["id"]),
            )


def remove_playlists(conn: sqlite3.Connection, playlist_ids: list[str]) -> None:
    """Drop playlists from the cache, then tracks and artists no playlist uses any more."""
    with conn:
        for pid in playlist_ids:
            conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (pid,))
            conn.execute("DELETE FROM playlists WHERE id = ?", (pid,))
        _prune(conn)


def _prune(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM tracks WHERE id NOT IN (SELECT track_id FROM playlist_tracks)")
    conn.execute("DELETE FROM track_artists WHERE track_id NOT IN (SELECT id FROM tracks)")
    conn.execute("DELETE FROM artists WHERE id NOT IN (SELECT artist_id FROM track_artists)")


def forget(path: Path | None = None) -> bool:
    """Delete the whole cache file. Returns whether there was one."""
    path = path or settings.cache_path()
    existed = path.exists()
    for p in (path, path.with_name(path.name + "-journal"), path.with_name(path.name + "-wal")):
        if p.exists():
            p.unlink()
    return existed


def parse_item(row: dict) -> dict | None:
    """One row of GET playlists/{id}/items -> {"track", "artists", "added_at"}, or None to skip.

    Skips episodes, local files and unavailable tracks (no Spotify ID).
    """
    item = row.get("item") or row.get("track")
    if not item or item.get("type", "track") != "track" or item.get("is_local") or not item.get("id"):
        return None
    album = item.get("album") or {}
    return {
        "track": {
            "id": item["id"],
            "name": item.get("name") or "",
            "album": album.get("name"),
            "album_id": album.get("id"),
            "release_date": album.get("release_date"),
            "duration_ms": item.get("duration_ms"),
            "explicit": int(bool(item.get("explicit"))),
            "popularity": item.get("popularity"),
            "isrc": (item.get("external_ids") or {}).get("isrc"),
            "url": (item.get("external_urls") or {}).get("spotify"),
        },
        "artists": [(a["id"], a.get("name") or "") for a in item.get("artists") or [] if a.get("id")],
        "added_at": row.get("added_at"),
    }
