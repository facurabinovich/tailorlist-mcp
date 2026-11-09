"""What the Phase 1 tools do: list playlists, load them into the cache, summarize the cache.

Kept apart from server.py so it can be tested with a fake client and a temp database.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from tailorlist_mcp import store
from tailorlist_mcp.sources.spotify_api import SpotifyClient, SpotifyError

_ID_RE = re.compile(r"(?:playlist[/:])?([A-Za-z0-9]{22})")


def playlist_id_from(ref: str) -> str | None:
    """A playlist ID from an ID, a spotify:playlist: URI or an open.spotify.com URL."""
    ref = ref.strip()
    m = _ID_RE.search(ref.split("?")[0])
    return m.group(1) if m else None


def _track_total(p: dict) -> int | None:
    for key in ("items", "tracks"):  # Spotify renamed tracks -> items for Development Mode apps
        block = p.get(key)
        if isinstance(block, dict) and "total" in block:
            return block["total"]
    return None


def list_playlists(client: SpotifyClient, conn: sqlite3.Connection) -> dict:
    me = client.me()
    loaded = store.loaded_snapshots(conn)
    out = []
    for p in client.my_playlists():
        if not p or not p.get("id"):
            continue
        owner = p.get("owner") or {}
        cached = loaded.get(p["id"])
        out.append({
            "id": p["id"],
            "name": p.get("name"),
            "tracks": _track_total(p),
            "mine": owner.get("id") == me.get("id"),
            "owner": owner.get("display_name") or owner.get("id"),
            "cache": None if p["id"] not in loaded else ("current" if cached == p.get("snapshot_id") else "changed"),
            "url": (p.get("external_urls") or {}).get("spotify"),
        })
    return {"user": me.get("display_name") or me.get("id"), "playlists": out}


# Parallel Spotify reads. Small enough to stay clear of rate limits; 429s are retried anyway.
_WORKERS = 4


def _fetch_playlist(client: SpotifyClient, pid: str, known_snapshot: str | None, force: bool) -> dict:
    """Read one playlist from Spotify (no database access: runs in a worker thread)."""
    meta = client.playlist(pid)
    if not force and known_snapshot == meta.get("snapshot_id"):
        return {"meta": meta, "unchanged": True}
    rows, skipped = [], 0
    for item in client.playlist_items(pid):
        parsed = store.parse_item(item)
        if parsed is None:
            skipped += 1
        else:
            rows.append(parsed)
    return {"meta": meta, "unchanged": False, "rows": rows, "skipped": skipped}


def load_playlists(
    client: SpotifyClient,
    conn: sqlite3.Connection,
    refs: list[str],
    force: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Read playlists from Spotify into the cache. Unchanged playlists (same snapshot) are skipped.

    Spotify is read in parallel; the database is written from this thread only.
    """
    results: list[dict | None] = [None] * len(refs)
    known = store.loaded_snapshots(conn)
    done = 0
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = {}
        for i, ref in enumerate(refs):
            pid = playlist_id_from(ref)
            if not pid:
                results[i] = {"ref": ref, "status": "error", "error": "Not a playlist ID or link."}
                continue
            futures[pool.submit(_fetch_playlist, client, pid, known.get(pid), force)] = (i, pid)
        for future in as_completed(futures):
            i, pid = futures[future]
            try:
                got = future.result()
            except SpotifyError as exc:
                results[i] = {"id": pid, "status": "error", "error": str(exc)}
            else:
                name = got["meta"].get("name")
                if got["unchanged"]:
                    results[i] = {"id": pid, "name": name, "status": "unchanged"}
                else:
                    store.save_playlist(conn, got["meta"], got["rows"], got["skipped"])
                    results[i] = {"id": pid, "name": name, "status": "loaded",
                                  "tracks": len(got["rows"]), "skipped": got["skipped"]}
            done += 1
            if progress:
                progress(done, len(futures), (results[i] or {}).get("name") or pid)

        missing = store.artists_missing_details(conn)
        if missing:
            batches = [missing[j : j + 50] for j in range(0, len(missing), 50)]
            try:
                for artists in pool.map(client.artists, batches):
                    store.save_artist_details(conn, artists)
            except SpotifyError as exc:
                results.append({"status": "warning", "error": f"Artist details not fetched: {exc}"})
    return {"results": results, "collection": overview(conn)}


def overview(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT (SELECT COUNT(*) FROM playlists) AS playlists, "
        "(SELECT COUNT(*) FROM playlist_tracks) AS entries, "
        "(SELECT COUNT(*) FROM tracks) AS unique_tracks, "
        "(SELECT COUNT(*) FROM artists) AS artists"
    ).fetchone()
    return dict(row)


def summary(conn: sqlite3.Connection, top: int = 15) -> dict:
    """A compact picture of the cached collection, small enough for the conversation."""
    base = overview(conn)
    if not base["playlists"]:
        return {**base, "note": "Nothing loaded yet. Use list_my_playlists, then load_playlists."}

    playlists = [
        dict(r) for r in conn.execute(
            "SELECT p.id, p.name, COUNT(pt.track_id) AS tracks, p.skipped, "
            "ROUND(SUM(t.duration_ms) / 3600000.0, 1) AS hours, p.url "
            "FROM playlists p LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id "
            "LEFT JOIN tracks t ON t.id = pt.track_id GROUP BY p.id ORDER BY tracks DESC"
        )
    ]

    in_many = conn.execute(
        "SELECT COUNT(*) FROM (SELECT track_id FROM playlist_tracks GROUP BY track_id "
        "HAVING COUNT(DISTINCT playlist_id) > 1)"
    ).fetchone()[0]
    repeated_within = conn.execute(
        "SELECT COUNT(*) FROM (SELECT playlist_id, track_id FROM playlist_tracks "
        "GROUP BY playlist_id, track_id HAVING COUNT(*) > 1)"
    ).fetchone()[0]

    top_artists = [
        {"name": r["name"], "tracks": r["n"]}
        for r in conn.execute(
            "SELECT a.name, COUNT(DISTINCT ta.track_id) AS n FROM track_artists ta "
            "JOIN artists a ON a.id = ta.artist_id GROUP BY a.id ORDER BY n DESC, a.name LIMIT ?",
            (top,),
        )
    ]

    # Genres per unique track (through all its artists), Spotify's tags as they come.
    genre_counts: Counter[str] = Counter()
    track_genres: dict[str, set[str]] = {}
    for r in conn.execute(
        "SELECT ta.track_id, a.genres FROM track_artists ta JOIN artists a ON a.id = ta.artist_id"
    ):
        genres = json.loads(r["genres"]) if r["genres"] else []
        track_genres.setdefault(r["track_id"], set()).update(genres)
    for genres in track_genres.values():
        genre_counts.update(genres)
    artists_without_genres = conn.execute(
        "SELECT COUNT(*) FROM artists WHERE genres IS NULL OR genres = '[]'"
    ).fetchone()[0]
    tracks_without_genres = sum(1 for g in track_genres.values() if not g)

    decades: Counter[str] = Counter()
    for r in conn.execute("SELECT release_date FROM tracks"):
        year = (r["release_date"] or "")[:4]
        decades[f"{year[:3]}0s" if year.isdigit() else "unknown"] += 1

    return {
        **base,
        "tracks_in_several_playlists": in_many,
        "tracks_repeated_inside_a_playlist": repeated_within,
        "playlists": playlists,
        "top_artists": top_artists,
        "top_spotify_genres": [{"genre": g, "tracks": n} for g, n in genre_counts.most_common(top)],
        "genre_coverage": {
            "tracks_without_genres": tracks_without_genres,
            "artists_without_genres": artists_without_genres,
        },
        "decades": dict(sorted(decades.items())),
    }
