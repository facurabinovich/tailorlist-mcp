"""library_health: one offline report of what is wrong with the library.

Each check is a small function returning (count, example_lines) over the same records the other
data tools use, so every number matches find_duplicates, find_replaced_tracks and the overview.
Cache only: no Spotify or MusicBrainz call.
"""
from __future__ import annotations

import re
import sqlite3
import time

import pandas as pd

from tailorlist_mcp import dashboard, genre_fill, store, years
from tailorlist_mcp.analysis import comparison, duplicates
from tailorlist_mcp.queries import QueryError, shared_pairs

EXAMPLES = 5
#: Album titles that look like compilations (matched case-insensitively): their year is often the reissue's.
COMPILATION_PATTERN = re.compile(
    r"best of|greatest hits|the hits|collection|anthology|essential|compilation|very best|ultimate",
    re.IGNORECASE,
)
NEAR_IDENTICAL_SHARE = 0.8
NEAR_IDENTICAL_MIN_TRACKS = 10


def _first(items) -> list[str]:
    items = list(items)
    return items[:EXAMPLES] + ([f"... {len(items) - EXAMPLES} more"] if len(items) > EXAMPLES else [])


def _exact(recs: list[dict]) -> list[tuple[str, str, list[str]]]:
    """(track, artist, playlist of each occurrence) for every ID saved more than once."""
    raw = duplicates.drop_missing_ids(duplicates.raw_records_frame(recs))
    found = duplicates.exact_duplicates(raw, include_playlists=True)
    return [(r.track_name, r.artist, r.playlists) for r in found.itertuples()]


def exact_inside_playlist(recs: list[dict]) -> tuple[int, list[str]]:
    """Spotify IDs saved more than once in the same playlist."""
    rows = [(n, a, max(p.count(x) for x in set(p))) for n, a, p in _exact(recs)]
    rows = sorted((r for r in rows if r[2] > 1), key=lambda r: (-r[2], r[0]))
    return len(rows), _first(f"{n} - {a} (x{c})" for n, a, c in rows)


def exact_across_playlists(recs: list[dict]) -> tuple[int, list[str]]:
    """Spotify IDs present in two or more playlists."""
    rows = [(n, a, len(set(p))) for n, a, p in _exact(recs)]
    rows = sorted((r for r in rows if r[2] > 1), key=lambda r: (-r[2], r[0]))
    return len(rows), _first(f"{n} - {a} ({c} playlists)" for n, a, c in rows)


def different_versions(recs: list[dict]) -> tuple[int, list[str]]:
    found = comparison.different_versions_across_playlists(recs)
    return len(found), _first(f"{r.track_name} - {r.artist_name} ({r.n_unique_ids} versions)" for r in found.itertuples())


def _flagged_tracks(conn: sqlite3.Connection, recs: list[dict], condition: str) -> list[sqlite3.Row]:
    in_scope = {r["id"] for r in recs}
    rows = conn.execute(
        "SELECT t.id, t.name, a.name AS artist FROM tracks t LEFT JOIN track_artists ta ON ta.track_id = t.id AND ta.position = 0 "
        f"LEFT JOIN artists a ON a.id = ta.artist_id WHERE {condition} ORDER BY a.name, t.name")
    return [r for r in rows if r["id"] in in_scope]


def replaced_versions(conn: sqlite3.Connection, recs: list[dict]) -> tuple[int, list[str]]:
    rows = _flagged_tracks(conn, recs, "t.current_id IS NOT NULL")
    return len(rows), _first(f"{r['name']} - {r['artist']}" for r in rows)


def unavailable_tracks(conn: sqlite3.Connection, recs: list[dict]) -> tuple[int, list[str]]:
    rows = _flagged_tracks(conn, recs, "t.unavailable = 1")
    return len(rows), _first(f"{r['name']} - {r['artist']}" for r in rows)


def skipped_items(conn: sqlite3.Connection, names: set[str]) -> tuple[int, list[str]]:
    rows = [r for r in conn.execute("SELECT name, skipped FROM playlists WHERE skipped > 0 ORDER BY skipped DESC, name")
            if r["name"] in names]
    return sum(r["skipped"] for r in rows), _first(f"{r['name']} ({r['skipped']})" for r in rows)


def genres_not_looked_up(conn: sqlite3.Connection) -> tuple[int, list[str]]:
    """Library-wide, the pending count fill_genres works from (store.mb_counts)."""
    return store.mb_counts(conn, int(time.time()), genre_fill.RETRY_AFTER)["pending"], []


def genres_not_found(conn: sqlite3.Connection) -> tuple[int, list[str]]:
    c = store.mb_counts(conn, int(time.time()), genre_fill.RETRY_AFTER)
    return c["total"] - c["found"] - c["pending"], []


def years_not_dated(conn: sqlite3.Connection) -> tuple[int, list[str]]:
    """Library-wide, the pending count fill_years works from."""
    return years.FILL._counts(conn, int(time.time()))["pending"], []


def doubtful_years(recs: list[dict]) -> tuple[int, list[str]]:
    """Compilation-looking albums whose tracks still carry the album's own year."""
    seen: dict[str, dict] = {}
    for r in recs:
        if r["album"] and COMPILATION_PATTERN.search(r["album"]) and r["release_date"] == r["album_release_date"]:
            seen.setdefault(r["id"], r)
    rows = sorted(seen.values(), key=lambda r: (r["artists"], r["name"]))
    return len(rows), _first(f"{r['name']} - {r['artists']} ({r['album']}, {str(r['release_date'] or '')[:4]})" for r in rows)


def near_identical_playlists(recs: list[dict]) -> tuple[int, list[str]]:
    members: dict[str, set] = {}
    for r in recs:
        members.setdefault(r["playlist_name"], set()).add(r["id"])
    found = []
    for shared, a, b in shared_pairs(members, sorted(members)):
        smaller = min(len(members[a]), len(members[b]))
        pct = shared / smaller
        if smaller >= NEAR_IDENTICAL_MIN_TRACKS and pct >= NEAR_IDENTICAL_SHARE:
            found.append((pct, f"{a} ({len(members[a])}) / {b} ({len(members[b])}): {shared} shared ({pct:.0%} of the smaller)"))
    found.sort(key=lambda f: -f[0])
    return len(found), _first(line for _, line in found)


def _block(title: str, count: int, examples: list[str], follow_up: str) -> list[str]:
    out = [f"{title}: {count:,}"]
    if examples:
        out.append("  e.g. " + "; ".join(examples))
    if follow_up:
        out.append(f"  Full list: {follow_up}")
    return out


def library_health(conn: sqlite3.Connection, playlists: list[str] | None = None) -> str:
    """The checks in order: a block for each one with something to report, the rest in one line."""
    recs = dashboard.records(conn)
    if not recs:
        raise QueryError("Nothing loaded yet. Use list_my_playlists, ask which playlists to load, then load_playlists.")
    wide = ""
    if playlists:
        wanted = {dashboard.find_playlist(conn, ref)["name"] for ref in playlists}
        recs = [r for r in recs if r["playlist_name"] in wanted]
        wide = " (library-wide, not only these playlists)"
    names = {r["playlist_name"] for r in recs}
    # Liked Songs only counts as a duplicate when the user names it (same rule as find_duplicates).
    dup_recs = recs if playlists else dashboard.without_liked(recs)
    exact = 'find_duplicates(kind="exact")'
    # (short label for the "No issues" line, block title, (count, examples), follow-up)
    checks = [
        ("exact duplicates inside a playlist", "Exact duplicates inside a playlist (same track saved twice)",
         exact_inside_playlist(dup_recs), exact),
        ("exact duplicates across playlists", "Exact duplicates across playlists (informative: the same track in several playlists "
         "is not necessarily a problem)", exact_across_playlists(dup_recs), exact),
        ("different versions of a song", "Different versions of the same song (same song and primary artist, different IDs)",
         different_versions(recs), 'find_duplicates(kind="versions")'),
        ("replaced album versions", "Replaced album versions (Spotify now plays another version)",
         replaced_versions(conn, recs), "find_replaced_tracks, then replace_versions to swap them"),
        ("tracks unplayable in your country", "Tracks not playable in your country",
         unavailable_tracks(conn, recs), "find_replaced_tracks"),
        ("skipped items", "Skipped items (episodes, local files)", skipped_items(conn, names), ""),
        ("artists to look up for genres", f"Artists without genres, not looked up in MusicBrainz yet{wide}",
         genres_not_looked_up(conn), "fill_genres"),
        ("artists with no genres found", f"Artists without genres, looked up and nothing found{wide}",
         genres_not_found(conn), ""),
        ("songs undated by original release", f"Songs not dated by original release yet{wide}", years_not_dated(conn), "fill_years"),
        ("doubtful compilation years", "Doubtful years (heuristic: the album title looks like a compilation and the song "
         "still has the album's year)", doubtful_years(recs), 'find_tracks(filters={"album": ...})'),
    ]
    if len(names) >= 2:
        checks.append(("near-identical playlists", f"Near-identical playlists (shared tracks are at least "
                       f"{NEAR_IDENTICAL_SHARE:.0%} of the smaller, which has at least {NEAR_IDENTICAL_MIN_TRACKS} tracks)",
                       near_identical_playlists(recs), "compare_playlists"))
    out = [f"Library health: {len(names)} playlists, {len({r['id'] for r in recs}):,} unique tracks."]
    clean = []
    for label, title, (count, examples), follow_up in checks:
        if count:
            out += _block(title, count, examples, follow_up)
        else:
            clean.append(label)
    if clean:
        out.append("No issues: " + "; ".join(clean) + ".")
    return "\n".join(out)
