"""Semantic grouping: Claude places tracks in groups by free criteria ("for running", "Sunday morning").

The rules in drafts.py can't make groups like that, so the LLM does it with its own knowledge of the
music, a batch at a time. The result is a normal draft: show_draft, edit_draft and write_draft handle
it as any other. Tracks not placed yet are the draft's unmatched entries (reason "not placed yet"),
so assign_tracks also works as a batch version of edit_draft's assign on any draft with unmatched tracks.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from tailorlist_mcp import drafts

# Batches of 50 through a whole library are too many calls: ask the user to narrow the pool instead.
MAX_POOL = 1500
BATCH_DEFAULT = 50
BATCH_MAX = 100
PENDING_REASON = "not placed yet"
SHOWN_SKIPPED = 10
SHOWN_GROUPS = 8


def start(conn: sqlite3.Connection, criteria: str | None, filters: drafts.GroupFilters | None,
          include_done: bool) -> dict:
    """Save a semantic draft: no groups, every pool track pending."""
    crit = " ".join((criteria or "").split())
    if not crit:
        raise drafts.DraftError("Say what the groups are about with `criteria` (e.g. \"moods for a road trip\"), "
                                "and agree the group names with the user first.")
    pool, counts = drafts.build_pool(conn, filters, include_done)
    if not len(pool):
        raise drafts.DraftError("No tracks match those filters"
                                + (" (tracks already organized are left out; include_done=true brings them back)."
                                   if counts["done_left_out"] else "."))
    if len(pool) > MAX_POOL:
        raise drafts.DraftError(f"{len(pool):,} tracks is too many to place in batches (max {MAX_POOL:,}): "
                                "narrow the filters (playlists, decades, genre_families).")
    pending = [{"id": t, "kind": "missing", "reason": PENDING_REASON} for t in dict.fromkeys(pool["track_id"])]
    return drafts.new_draft(conn, "semantic", "", [], False, counts, {}, pending, include_done,
                            source={"kind": "semantic", "criteria": crit})


def _pending_ids(info: pd.DataFrame, d: dict) -> list[str]:
    return [u["id"] for u in d["unmatched"] if u["id"] in info.index]


def assign(conn: sqlite3.Connection, d: dict, assignments: dict[str, list[str]] | None) -> dict:
    """Place the pending tracks in the groups (created when missing) and save the draft.

    Returns {"placed": n, "skipped": [ids not pending here], "duplicates": [ids under several groups]}.
    A track goes to the first group it is listed under. Nothing is changed if one thing fails.
    """
    info = drafts._info(conn)
    pending = set(_pending_ids(info, d))
    taken: set[str] = set()
    skipped: list[str] = []
    duplicates: list[str] = []
    wanted: dict[str, list[str]] = {}
    for raw_name, ids in (assignments or {}).items():
        name = " ".join(str(raw_name).split())
        if not name:
            raise drafts.DraftError("A group name is empty.")
        existing = next((g["name"] for g in d["groups"] if g["name"].casefold() == name.casefold()), None)
        key = existing or next((k for k in wanted if k.casefold() == name.casefold()), name)
        for t in dict.fromkeys(i.strip() for i in ids or [] if i and i.strip()):
            if t in taken:
                duplicates.append(t)
            elif t not in pending:
                skipped.append(t)
            else:
                taken.add(t)
                wanted.setdefault(key, []).append(t)
    new_names = [n for n in wanted if not any(g["name"] == n for g in d["groups"])]
    if len(d["groups"]) + len(new_names) > drafts.MAX_GROUPS:
        raise drafts.DraftError(f"That would make {len(d['groups']) + len(new_names)} groups (max {drafts.MAX_GROUPS}): "
                                "use fewer, broader groups.")
    if wanted:
        # Empty groups for the new names, so apply_changes finds them by exact name instead of a partial match.
        d["groups"] = d["groups"] + [{"name": n, "tracks": [], "keep_visible": False} for n in new_names]
        changes = [drafts.Assign(op="assign", tracks=drafts.TrackSelector(track_ids=ids), to=n)
                   for n, ids in wanted.items()]
        drafts.apply_changes(conn, d, changes)
    return {"placed": len(taken), "skipped": skipped, "duplicates": duplicates}


def _short(ids: list[str]) -> str:
    return ", ".join(ids[:SHOWN_SKIPPED]) + (f" and {len(ids) - SHOWN_SKIPPED} more" if len(ids) > SHOWN_SKIPPED else "")


def _cell(v) -> str:
    return str(v).replace("|", "/").replace("\n", " ").strip()


def batch_table(info: pd.DataFrame, ids: list[str]) -> str:
    """The tracks as a compact table, in the order given."""
    lines = ["Track ID | Track | Artist | Year | Genres | Pop"]
    for t in ids:
        r = info.loc[t]
        year = "" if pd.isna(r["release_year"]) else int(r["release_year"])
        pop = "" if pd.isna(r["popularity_raw"]) else int(r["popularity_raw"])
        lines.append(" | ".join([t, _cell(r["track_name"]), _cell(r["artist_display"]), str(year),
                                 _cell(", ".join(r["_tags"][:3])), str(pop)]))
    return "\n".join(lines)


def result_text(conn: sqlite3.Connection, d: dict, batch: int = BATCH_DEFAULT, report: dict | None = None) -> str:
    """Progress line, notes about the last call, then the next batch (or the finished message)."""
    info = drafts._info(conn)
    n = max(1, min(int(batch), BATCH_MAX))
    pending = _pending_ids(info, d)
    sizes = [(g["name"], len([t for t in g["tracks"] if t in info.index])) for g in d["groups"]]
    sizes.sort(key=lambda x: (-x[1], x[0]))
    placed = sum(s for _, s in sizes)
    shown = ", ".join(f"{name} {s}" for name, s in sizes[:SHOWN_GROUPS]) + (", ..." if len(sizes) > SHOWN_GROUPS else "")
    lines = [f"Draft {d['id']} ({drafts.draft_label(d)}): {placed:,} of {placed + len(pending):,} placed, "
             f"{len(pending):,} left" + (f"; groups: {shown}" if sizes else "; no groups yet") + "."]
    if report:
        if report["placed"]:
            lines.append(f"Placed {report['placed']:,} tracks this call.")
        if report["duplicates"]:
            lines.append(f"{len(report['duplicates'])} track IDs were under more than one group: kept in the first ({_short(report['duplicates'])}).")
        if report["skipped"]:
            lines.append(f"Skipped {len(report['skipped'])} track IDs that are not waiting in this draft "
                         f"(unknown or already placed): {_short(report['skipped'])}.")
    if not pending:
        lines.append("Nothing is left to place. Call show_draft to review the draft with the user, then write_draft "
                     "(nothing has been written to Spotify).")
        return "\n".join(lines)
    ids = sorted(pending, key=lambda t: (str(info.loc[t, "artist_display"] or "").casefold(),
                                         str(info.loc[t, "track_name"] or "").casefold(), t))[:n]
    lines.append(f"Next {len(ids)} tracks to place (call assign_tracks again with draft=\"{d['id']}\" and "
                 "assignments {group: [track IDs]}; leave out the ones you don't know):")
    lines.append(batch_table(info, ids))
    return "\n".join(lines)
