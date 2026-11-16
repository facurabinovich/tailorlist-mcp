"""What the Phase 1 tools do: list playlists and load them into the cache.

Kept apart from server.py so it can be tested with a fake client and a temp database.
"""
from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from tailorlist_mcp import demo, store
from tailorlist_mcp.sources.spotify_api import SpotifyClient, SpotifyError

_ID_RE = re.compile(r"(?:playlist[/:])?([A-Za-z0-9]{22})")


def is_liked_ref(ref: str) -> bool:
    """True for "liked", "Liked Songs" and the Liked Songs link (spaces and case ignored)."""
    ref = ref.strip().casefold().split("?")[0].rstrip("/")
    return ref in ("liked", "liked songs", store.LIKED_URL)


def playlist_id_from(ref: str) -> str | None:
    """A playlist ID from an ID, a spotify:playlist: URI or an open.spotify.com URL."""
    ref = ref.strip()
    if is_liked_ref(ref):
        return None
    m = _ID_RE.search(ref.split("?")[0])
    return m.group(1) if m else None


def _track_total(p: dict) -> int | None:
    for key in ("items", "tracks"):  # Spotify renamed tracks -> items for Development Mode apps
        block = p.get(key)
        if isinstance(block, dict) and "total" in block:
            return block["total"]
    return None


def _liked_snapshot(total: int, newest: str | None) -> str:
    return f"{total}:{newest}"


def _liked_meta(me: dict, snapshot: str) -> dict:
    return {"id": store.LIKED_ID, "name": store.LIKED_NAME, "owner": {"id": me.get("id"), "display_name": me.get("display_name")},
            "snapshot_id": snapshot, "external_urls": {"spotify": store.LIKED_URL}}


def list_playlists(client: SpotifyClient, conn: sqlite3.Connection) -> dict:
    me = client.me()
    loaded = store.loaded_snapshots(conn)
    out, note = [], None
    try:
        total, newest = client.saved_summary()
    except SpotifyError:
        note = ("Liked Songs could not be read (the login may lack the permission): logging in again with "
                "connect_spotify may fix it.")
    else:
        snap = _liked_snapshot(total, newest)
        out.append({"id": store.LIKED_ID, "name": store.LIKED_NAME, "tracks": total, "mine": True,
                    "owner": me.get("display_name") or me.get("id"),
                    "cache": None if store.LIKED_ID not in loaded else ("current" if loaded[store.LIKED_ID] == snap else "changed"),
                    "url": store.LIKED_URL})
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
    result = {"user": me.get("display_name") or me.get("id"), "playlists": out}
    if note:
        result["note"] = note
    return result


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


def _fetch_liked(client: SpotifyClient, known_snapshot: str | None, force: bool) -> dict:
    """Read Liked Songs (newest first) like _fetch_playlist; its snapshot is "<total>:<newest added_at>"."""
    meta = _liked_meta(client.me(), _liked_snapshot(*client.saved_summary()))
    if not force and known_snapshot == meta["snapshot_id"]:
        return {"meta": meta, "unchanged": True}
    rows, skipped = [], 0
    for item in client.saved_tracks():
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
    cleared_demo = demo.clear(conn)       # demo and real playlists never mix
    known = store.loaded_snapshots(conn)
    done = 0
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        futures = {}
        for i, ref in enumerate(refs):
            pid = store.LIKED_ID if is_liked_ref(ref) else playlist_id_from(ref)
            if not pid:
                results[i] = {"ref": ref, "status": "error", "error": "Not a playlist ID or link."}
                continue
            fetch = (_fetch_liked, client) if pid == store.LIKED_ID else (_fetch_playlist, client, pid)
            futures[pool.submit(*fetch, known.get(pid), force)] = (i, pid)
        for future in as_completed(futures):
            i, pid = futures[future]
            try:
                got = future.result()
            except SpotifyError as exc:
                results[i] = {"id": pid, "status": "error", "error": str(exc)}
            else:
                name = got["meta"].get("name")
                if got["unchanged"]:
                    store.update_followers(conn, got["meta"])
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
    try:
        relink_popularity_zero(client, conn)
    except SpotifyError as exc:
        results.append({"status": "warning", "error": f"Replaced versions not checked: {exc}"})
    return {"results": results, "collection": overview(conn), "cleared_demo": cleared_demo}


def relink_popularity_zero(client: SpotifyClient, conn: sqlite3.Connection, now: int | None = None) -> int:
    """Ask Spotify which version it serves now for every popularity-0 track not asked yet.

    Spotify gives popularity 0 to album versions it replaced with a newer release; with the
    user's market it "relinks" them to the replacement (linked_from holds the saved ID). The
    replacement's own popularity needs a second, market-less read. Returns how many were asked.
    """
    ids = store.tracks_to_relink(conn)
    if not ids:
        return 0
    found: dict[str, dict | None] = {tid: None for tid in ids}
    for asked, t in zip(ids, client.tracks(ids, market="from_token")):
        if not t:
            continue
        original = (t.get("linked_from") or {}).get("id") or t.get("id")
        if original != asked:
            continue
        if t.get("id") and t["id"] != asked:
            found[asked] = {"current_id": t["id"]}
        else:
            found[asked] = {"unavailable": t.get("is_playable") is False}
    replacements = sorted({r["current_id"] for r in found.values() if r and r.get("current_id")})
    details = {t["id"]: t for t in client.tracks(replacements) if t} if replacements else {}
    for r in found.values():
        if r and r.get("current_id"):
            t = details.get(r["current_id"]) or {}
            r.update(current_popularity=t.get("popularity"), current_album=(t.get("album") or {}).get("name"),
                     current_url=(t.get("external_urls") or {}).get("spotify"))
    store.save_relinks(conn, found, now or int(time.time()))
    return len(ids)


def overview(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT (SELECT COUNT(*) FROM playlists) AS playlists, "
        "(SELECT COUNT(*) FROM playlist_tracks) AS entries, "
        "(SELECT COUNT(*) FROM tracks) AS unique_tracks, "
        "(SELECT COUNT(*) FROM artists) AS artists"
    ).fetchone()
    return dict(row)
