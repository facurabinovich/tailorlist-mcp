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
    loaded_at   INTEGER NOT NULL,
    followers   INTEGER
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
    popularity INTEGER,
    mb_genres     TEXT,       -- JSON list from MusicBrainz (only looked up when Spotify has none); NULL = not checked
    mb_checked_at INTEGER,    -- when MusicBrainz was last asked (also set when it found nothing)
    mb_source     TEXT        -- how the MusicBrainz artist was found: 'url' (Spotify link) or 'search' (exact name)
);
CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id TEXT NOT NULL,
    position    INTEGER NOT NULL,
    track_id    TEXT NOT NULL,
    added_at    TEXT,
    PRIMARY KEY (playlist_id, position)
);
CREATE INDEX IF NOT EXISTS playlist_tracks_track ON playlist_tracks(track_id);
CREATE TABLE IF NOT EXISTS jobs (          -- background jobs, one row each: keeps two server processes apart
    name      TEXT PRIMARY KEY,
    owner     TEXT NOT NULL,
    heartbeat INTEGER NOT NULL,
    error     TEXT
);
"""

# Columns added after the first release: (table, column, type). Applied with ALTER TABLE so existing caches keep their data.
_ADDED_COLUMNS = [
    ("playlists", "followers", "INTEGER"),
    ("artists", "mb_genres", "TEXT"),
    ("artists", "mb_checked_at", "INTEGER"),
    ("artists", "mb_source", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, ctype in _ADDED_COLUMNS:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")
            except sqlite3.OperationalError:  # another process added it first
                pass
    conn.commit()


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or settings.cache_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def loaded_snapshots(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["id"]: r["snapshot_id"] for r in conn.execute("SELECT id, snapshot_id FROM playlists")}


def save_playlist(conn: sqlite3.Connection, playlist: dict, rows: list[dict], skipped: int) -> None:
    """Replace one playlist's contents. rows: parsed tracks in playlist order (see parse_item)."""
    pid = playlist["id"]
    with conn:
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (pid,))
        conn.execute(
            "INSERT OR REPLACE INTO playlists (id, name, owner_id, owner_name, snapshot_id, url, skipped, loaded_at, "
            "followers) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pid,
                playlist.get("name") or "",
                (playlist.get("owner") or {}).get("id"),
                (playlist.get("owner") or {}).get("display_name"),
                playlist.get("snapshot_id"),
                (playlist.get("external_urls") or {}).get("spotify"),
                skipped,
                int(time.time()),
                followers_of(playlist),
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


def followers_of(playlist: dict) -> int | None:
    total = (playlist.get("followers") or {}).get("total")
    return int(total) if isinstance(total, (int, float)) else None


def update_followers(conn: sqlite3.Connection, playlist: dict) -> None:
    """Refresh the follower count of a cached playlist (it changes without a new snapshot)."""
    followers = followers_of(playlist)
    if followers is not None:
        with conn:
            conn.execute("UPDATE playlists SET followers = ? WHERE id = ?", (followers, playlist["id"]))


def artists_missing_details(conn: sqlite3.Connection) -> list[str]:
    return [r["id"] for r in conn.execute("SELECT id FROM artists WHERE genres IS NULL")]


def save_artist_details(conn: sqlite3.Connection, artists: list[dict]) -> None:
    with conn:
        for a in artists:
            conn.execute(
                "UPDATE artists SET genres = ?, popularity = ? WHERE id = ?",
                (json.dumps(a.get("genres") or []), a.get("popularity"), a["id"]),
            )


# ---------------------------------------------------------------- MusicBrainz genres

# Primary artists whose Spotify genres came back empty: the only ones MusicBrainz is asked about
# (genres are read from the primary artist only; see dashboard.records).
_NO_SPOTIFY_GENRES = (
    "a.genres = '[]' AND a.id IN (SELECT artist_id FROM track_artists WHERE position = 0)"
)


def mb_candidates(conn: sqlite3.Connection, now: int, retry_after: int) -> list[sqlite3.Row]:
    """Artists still to look up, the ones with the most tracks first.

    Anything asked recently is skipped, including "found nothing": that is asked again only
    after retry_after seconds. Artists that got genres are never asked again.
    """
    return conn.execute(
        f"SELECT a.id, a.name, COUNT(ta.track_id) AS tracks FROM artists a "
        f"JOIN track_artists ta ON ta.artist_id = a.id AND ta.position = 0 "
        f"WHERE {_NO_SPOTIFY_GENRES} AND (a.mb_checked_at IS NULL "
        f"OR ((a.mb_genres IS NULL OR a.mb_genres = '[]') AND a.mb_checked_at < ?)) "
        f"GROUP BY a.id ORDER BY tracks DESC, a.name",
        (now - retry_after,),
    ).fetchall()


def save_mb_result(conn: sqlite3.Connection, artist_id: str, genres: list[str], source: str | None, now: int) -> None:
    with conn:
        conn.execute(
            "UPDATE artists SET mb_genres = ?, mb_checked_at = ?, mb_source = ? WHERE id = ?",
            (json.dumps(genres), now, source if genres else None, artist_id),
        )


def mb_counts(conn: sqlite3.Connection, now: int, retry_after: int) -> dict:
    """Where the MusicBrainz fill stands. total = primary artists Spotify left without genres."""
    row = conn.execute(
        f"SELECT COUNT(*) AS total, "
        f"COALESCE(SUM(a.mb_checked_at IS NOT NULL), 0) AS checked, "
        f"COALESCE(SUM(a.mb_genres IS NOT NULL AND a.mb_genres != '[]'), 0) AS found, "
        f"COALESCE(SUM(a.mb_checked_at IS NULL OR ((a.mb_genres IS NULL OR a.mb_genres = '[]') "
        f"AND a.mb_checked_at < ?)), 0) AS pending "
        f"FROM artists a WHERE {_NO_SPOTIFY_GENRES}",
        (now - retry_after,),
    ).fetchone()
    return {k: int(row[k]) for k in ("total", "checked", "found", "pending")}


# ---------------------------------------------------------------- background job lock

def acquire_job(conn: sqlite3.Connection, name: str, owner: str, now: int, stale_after: int,
                owner_alive=None) -> bool:
    """Take the lock row of a background job unless another live owner holds it.

    A row whose heartbeat is older than stale_after is dead (its process went away) and is
    taken over, and so is one whose owner process is gone (owner_alive(owner) is False), e.g. a
    server that was killed. The owner may re-acquire its own row.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT owner, heartbeat FROM jobs WHERE name = ?", (name,)).fetchone()
        if row and row["owner"] != owner and row["heartbeat"] and now - row["heartbeat"] < stale_after \
                and (owner_alive is None or owner_alive(row["owner"])):
            conn.execute("ROLLBACK")
            return False
        conn.execute(
            "INSERT OR REPLACE INTO jobs (name, owner, heartbeat, error) VALUES (?, ?, ?, NULL)", (name, owner, now)
        )
        conn.execute("COMMIT")
        return True
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def job_heartbeat(conn: sqlite3.Connection, name: str, owner: str, now: int) -> None:
    with conn:
        conn.execute("UPDATE jobs SET heartbeat = ? WHERE name = ? AND owner = ?", (now, name, owner))


def release_job(conn: sqlite3.Connection, name: str, owner: str, error: str | None = None) -> None:
    """Free the lock. The row stays (heartbeat 0) so the last error can still be reported."""
    with conn:
        conn.execute("UPDATE jobs SET heartbeat = 0, error = ? WHERE name = ? AND owner = ?", (error, name, owner))


def job_state(conn: sqlite3.Connection, name: str, now: int, stale_after: int) -> dict:
    row = conn.execute("SELECT owner, heartbeat, error FROM jobs WHERE name = ?", (name,)).fetchone()
    if not row:
        return {"running": False, "owner": None, "error": None}
    return {"running": bool(row["heartbeat"]) and now - row["heartbeat"] < stale_after, "owner": row["owner"], "error": row["error"]}


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
