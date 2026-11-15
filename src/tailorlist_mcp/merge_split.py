"""Merge several playlists into one draft group, or split one playlist into a draft by decade / genre family.

Both only build a draft (drafts.py): nothing is written to Spotify and the source playlists are
never touched. write_draft turns the draft into new playlists later.
"""
from __future__ import annotations

import sqlite3

from tailorlist_mcp import dashboard, drafts
from tailorlist_mcp.analysis import comparison

SPLIT_BY = ("decade", "genre_family", "both")


def normalise_by(by: str) -> str:
    """The same normalisation group_tracks applies to `by`."""
    b = by.strip().lower().replace(" ", "_").replace("-", "_")
    b = {"genre": "genre_family", "family": "genre_family", "genres": "genre_family"}.get(b, b)
    if b not in SPLIT_BY:
        raise drafts.DraftError("by must be 'decade', 'genre_family' or 'both'.")
    return b


def merge_playlists(conn: sqlite3.Connection, refs: list[str], name: str | None = None) -> tuple[dict, str]:
    """Save a draft with one group: the union of the playlists' tracks. Returns (draft, intro text).

    Order: the playlists as given, then by position; a track ID already seen is kept at its first appearance.
    """
    chosen: list[sqlite3.Row] = []
    for ref in refs:
        pl = dashboard.find_playlist(conn, ref)
        if all(pl["id"] != c["id"] for c in chosen):
            chosen.append(pl)
    if len(chosen) < 2:
        raise drafts.DraftError("Give at least 2 different loaded playlists to merge.")
    names = [c["name"] for c in chosen]
    entries: list[str] = []
    for pl in chosen:
        entries += [r[0] for r in conn.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position", (pl["id"],))]
    unique = list(dict.fromkeys(entries))
    if not unique:
        raise drafts.DraftError("Those playlists have no tracks.")
    recs = [r for r in dashboard.records(conn) if r["playlist_name"] in names]
    versions = len(comparison.different_versions_across_playlists(recs))
    group_name = (name or "").strip() or " + ".join(names)
    counts = {"in_cache": len(unique), "in_playlists": len(unique), "after_decades": len(unique), "after_families": len(unique),
              "done_left_out": 0, "manual_hidden": 0, "pool": len(unique), "filters": ["playlists=" + ", ".join(names)]}
    d = drafts.new_draft(conn, "merge", "", [], False, counts, {group_name: unique}, [], True,
                         source={"kind": "merge", "playlists": names})
    dropped = len(entries) - len(unique)
    text = (f"Merged {len(names)} playlists ({', '.join(names)}): {len(entries):,} entries in total, "
            f"{len(unique):,} unique tracks in the merged group, {dropped:,} exact duplicate{'s' if dropped != 1 else ''} dropped.")
    if versions:
        text += (f"\n{versions:,} song{'s are' if versions != 1 else ' is'} present in several different versions (same song, different IDs). "
                 "Review them in the draft (edit_draft) or run dedupe_playlist after writing.")
    text += "\nNothing was written to Spotify. write_draft creates the playlist."
    return d, text


def split_playlist(conn: sqlite3.Connection, ref: str, by: str = "decade", preview_only: bool = False) -> tuple[dict | None, str, dict]:
    """The category grouping restricted to one playlist, done tracks included.

    Returns (the saved draft, or None for a preview; the text; the preview counts).
    """
    b = normalise_by(by)
    pl = dashboard.find_playlist(conn, ref)
    pool, counts = drafts.build_pool(conn, drafts.GroupFilters(playlists=[pl["name"]]), include_done=True)
    if not len(pool):
        raise drafts.DraftError(f"{pl['name']} has no tracks.")
    groups, unmatched = drafts.make_groups(conn, pool, counts, "category", b, None, False)
    source = {"kind": "split", "playlist": pl["name"]}
    label = drafts.draft_label({"mode": "category", "by": b, "source": source})
    if len(groups) > drafts.MAX_GROUPS:
        raise drafts.DraftError(f"That would make {len(groups)} groups (max {drafts.MAX_GROUPS}): split by decade or genre family alone.")
    text = drafts.preview_text(counts, groups, unmatched, label)
    preview = {"pool": counts, "groups": len(groups), "unmatched": len(unmatched)}
    if preview_only:
        return None, text + "\nNothing was saved. Run again without preview_only to make the draft.", preview
    if not groups:
        raise drafts.DraftError(text + "\nNo group could be made.")
    d = drafts.new_draft(conn, "category", b, [], False, counts, groups, unmatched, True, source=source)
    return d, text + f"\n{pl['name']} stays untouched. write_draft creates one new playlist per group.", preview
