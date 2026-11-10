"""Track lists as plain text, and the cache lookups the write tools share.

Port of the original app's core/export.py (spotlistr_lines, spotify_track_urls): the "Artist - Song"
text Spotlistr reads and the track URLs. Nothing here touches Spotify.
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3

from tailorlist_mcp import dashboard, drafts

FORMATS = ("text", "urls", "csv")
_TRACK_RE = re.compile(r"^(?:https?://open\.spotify\.com/(?:intl-[a-z]+/)?track/|spotify:track:)?([A-Za-z0-9]{22})(?:[?/].*)?$")


def track_id_from(ref: str) -> str | None:
    """A track ID from an ID, a spotify:track: URI or an open.spotify.com/track URL."""
    m = _TRACK_RE.match(ref.strip())
    return m.group(1) if m else None


def track_meta(conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict]:
    """What the cache knows about each track ID: name, artists, primary artist, album, year, link and
    the relink columns. IDs the cache doesn't hold are left out."""
    out: dict[str, dict] = {}
    ids = list(dict.fromkeys(ids))
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
                f"SELECT id, name, album, release_date, url, current_id, current_album, unavailable "
                f"FROM tracks WHERE id IN ({marks})", chunk):
            out[r["id"]] = {"id": r["id"], "name": r["name"], "album": r["album"], "year": (r["release_date"] or "")[:4],
                            "url": r["url"], "current_id": r["current_id"], "current_album": r["current_album"],
                            "unavailable": bool(r["unavailable"]), "artist_list": []}
        for r in conn.execute(
                f"SELECT ta.track_id, a.name FROM track_artists ta JOIN artists a ON a.id = ta.artist_id "
                f"WHERE ta.track_id IN ({marks}) ORDER BY ta.track_id, ta.position", chunk):
            out[r["track_id"]]["artist_list"].append(r["name"])
    for m in out.values():
        m["artist"] = m["artist_list"][0] if m["artist_list"] else ""
        m["artists"] = ", ".join(m["artist_list"])
    return out


def spotlistr_lines(rows: list[dict]) -> str:
    """"Artist - Song", one per line, primary artist only, in order, duplicates kept."""
    return "\n".join(f"{r['artist']} - {r['name']}" for r in rows)


def spotify_track_urls(rows: list[dict]) -> str:
    return "\n".join(f"https://open.spotify.com/track/{r['id']}" for r in rows)


def _csv(blocks: list[tuple[str | None, list[dict]]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["group", "track", "artists", "album", "year", "id", "url"])
    for group, rows in blocks:
        for r in rows:
            w.writerow([group or "", r["name"], r["artists"], r["album"] or "", r["year"], r["id"],
                        f"https://open.spotify.com/track/{r['id']}"])
    return buf.getvalue().rstrip("\n")


def export_tracks(conn: sqlite3.Connection, playlist: str | None = None, draft: str | None = None,
                  group: str | None = None, fmt: str = "text") -> str:
    """A playlist's tracks (in order) or a draft's groups as "Artist - Song" text, track URLs or CSV."""
    fmt = (fmt or "text").strip().lower()
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of: {', '.join(FORMATS)}.")
    if bool(playlist) == bool(draft or group):
        raise ValueError("Give either a playlist, or a draft (optionally with one group).")
    if group and not draft:
        raise ValueError("A group needs its draft.")
    if playlist:
        pl = dashboard.find_playlist(conn, playlist)
        ids = [r["track_id"] for r in conn.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position", (pl["id"],))]
        blocks = [(pl["name"], ids)]
        title = f"Playlist {pl['name']}"
    else:
        d = drafts.load_draft(conn, draft)
        groups = [drafts._find_group(d["groups"], group)] if group else d["groups"]
        blocks = [(g["name"], g["tracks"]) for g in groups]
        title = f"Draft {d['id']}" + (f", group {groups[0]['name']}" if group else "")
    meta = track_meta(conn, [t for _, ids in blocks for t in ids])
    resolved = [(name, [meta[t] for t in ids if t in meta]) for name, ids in blocks]
    n = sum(len(r) for _, r in resolved)
    if fmt == "csv":
        return _csv(resolved)
    render = spotlistr_lines if fmt == "text" else spotify_track_urls
    parts = [f"{title}: {n} tracks ({'Artist - Song' if fmt == 'text' else 'URLs'}, one per line)."]
    for name, rows in resolved:
        parts.append(f"\n## {name} ({len(rows)})\n{render(rows)}" if len(resolved) > 1 else "\n" + render(rows))
    return "".join(parts)
