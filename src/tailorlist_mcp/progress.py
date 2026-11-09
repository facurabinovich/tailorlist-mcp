"""Organize progress: which tracks the user has already placed in a playlist they built.

Port of the original app's db/progress.py (table user_cluster_progress) for a single local user:

  status 'done'   - the track is in a group that was exported to Spotify (the original's "Mark these
                    tracks as done") or was skipped on purpose ("Skip N remaining tracks"). Done
                    tracks leave the pool of the next grouping.
  status 'manual' - an unmatched track the user assigned to a group by hand ("Save assignments"),
                    with the group's name. Written BEFORE export; it counts as organized in the
                    progress bar, but does not leave the pool (the original only excluded 'done').

Original operations and their ports:
  save_cluster_progress_bulk -> mark_done / save_manual     delete_done_progress -> reopen
  delete_progress_for_tracks -> clear                       reset_cluster_progress -> reset
  load_cluster_progress -> load                             the "x of y" bar -> summary
(The original's user_progress_meta.reset_at existed only to survive a failed DELETE on MySQL;
a local SQLite DELETE needs no such fallback.)
"""
from __future__ import annotations

import sqlite3
import time
from typing import Iterable


def _now() -> int:
    return int(time.time())


def load(conn: sqlite3.Connection) -> dict[str, dict]:
    """{track_id: {"status", "cluster_name"}} for every progress row."""
    return {r["track_id"]: {"status": r["status"], "cluster_name": r["cluster_name"]}
            for r in conn.execute("SELECT track_id, status, cluster_name FROM cluster_progress")}


def done_ids(conn: sqlite3.Connection) -> set[str]:
    return {r["track_id"] for r in conn.execute("SELECT track_id FROM cluster_progress WHERE status = 'done'")}


def mark_done(conn: sqlite3.Connection, track_ids: Iterable[str], cluster_name: str | None = None) -> int:
    """Upsert 'done' for the tracks (a 'manual' row becomes 'done'). Returns how many tracks."""
    rows = [(t, cluster_name, _now()) for t in dict.fromkeys(track_ids)]
    with conn:
        conn.executemany(
            "INSERT INTO cluster_progress (track_id, status, cluster_name, updated_at) VALUES (?, 'done', ?, ?) "
            "ON CONFLICT(track_id) DO UPDATE SET status = 'done', cluster_name = excluded.cluster_name, "
            "updated_at = excluded.updated_at", rows)
    return len(rows)


def save_manual(conn: sqlite3.Connection, assignments: dict[str, str]) -> None:
    """Upsert 'manual' rows {track_id: group name}. A track that is already 'done' stays done."""
    rows = [(t, name, _now()) for t, name in assignments.items()]
    with conn:
        conn.executemany(
            "INSERT INTO cluster_progress (track_id, status, cluster_name, updated_at) VALUES (?, 'manual', ?, ?) "
            "ON CONFLICT(track_id) DO UPDATE SET cluster_name = excluded.cluster_name, updated_at = excluded.updated_at "
            "WHERE cluster_progress.status = 'manual'", rows)


def reopen(conn: sqlite3.Connection, track_ids: Iterable[str]) -> int:
    """Drop the 'done' rows of these tracks so they re-enter the pool (the original's "Re-open")."""
    return _delete(conn, track_ids, "AND status = 'done'")


def clear_manual(conn: sqlite3.Connection, track_ids: Iterable[str]) -> int:
    """Drop the 'manual' rows of these tracks (their assignment was undone)."""
    return _delete(conn, track_ids, "AND status = 'manual'")


def _delete(conn: sqlite3.Connection, track_ids: Iterable[str], extra: str) -> int:
    ids = list(dict.fromkeys(track_ids))
    n = 0
    with conn:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            n += conn.execute(
                f"DELETE FROM cluster_progress WHERE track_id IN ({','.join('?' * len(chunk))}) {extra}", chunk
            ).rowcount
    return n


def reset(conn: sqlite3.Connection) -> int:
    """Delete every progress row (and nothing else). Returns how many rows were deleted."""
    with conn:
        return conn.execute("DELETE FROM cluster_progress").rowcount


def summary(conn: sqlite3.Connection) -> dict:
    """The original's progress bar: tracks organized (done + manual) of the unique tracks loaded.

    Only progress rows of tracks still in the cache count (as the original capped the count at
    the collection size), so removing a playlist can't push the percentage above 100.
    """
    total = conn.execute("SELECT COUNT(DISTINCT track_id) FROM playlist_tracks").fetchone()[0]
    counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM cluster_progress "
        "WHERE track_id IN (SELECT track_id FROM playlist_tracks) GROUP BY status")}
    done, manual = counts.get("done", 0), counts.get("manual", 0)
    organized = done + manual
    return {
        "total": total, "done": done, "manual": manual, "organized": organized,
        "remaining": max(0, total - organized),
        "percent": round(organized / total * 100, 1) if total else 0.0,
        "complete": bool(total) and organized >= total,
    }


def by_playlist(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT p.name AS playlist, COUNT(DISTINCT pt.track_id) AS tracks, "
        "COUNT(DISTINCT cp.track_id) AS organized FROM playlist_tracks pt "
        "JOIN playlists p ON p.id = pt.playlist_id LEFT JOIN cluster_progress cp ON cp.track_id = pt.track_id "
        "GROUP BY p.id ORDER BY p.name")]


def manual_groups(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT cluster_name AS \"group\", COUNT(*) AS tracks FROM cluster_progress "
        "WHERE status = 'manual' GROUP BY cluster_name ORDER BY tracks DESC, cluster_name")]


def describe(conn: sqlite3.Connection) -> str:
    s = summary(conn)
    if not s["total"]:
        return "Nothing loaded yet. Use list_my_playlists, ask which playlists to load, then load_playlists."
    lines = [f"{s['organized']:,} of {s['total']:,} unique tracks organized ({s['percent']}%), {s['remaining']:,} remaining."
             f" Done (placed in a playlist you built, or skipped): {s['done']:,}. Manually assigned to a group: {s['manual']:,}."]
    if s["complete"]:
        lines.append("100%: the whole collection is organized. reset_progress starts over (it clears this progress).")
    elif not s["organized"]:
        lines.append("Nothing organized yet. Tracks count once they are placed in a playlist built from a draft.")
    if s["organized"]:
        lines.append("Per playlist (organized / tracks): " + "; ".join(
            f"{r['playlist']} {r['organized']}/{r['tracks']}" for r in by_playlist(conn)))
    groups = manual_groups(conn)
    if groups:
        lines.append("Manual assignments by group: " + "; ".join(f"{g['group']} ({g['tracks']})" for g in groups[:15]))
    lines.append("Done tracks are left out of the next group_tracks unless include_done=true.")
    return "\n".join(lines)
