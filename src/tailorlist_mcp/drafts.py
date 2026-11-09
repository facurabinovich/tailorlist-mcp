"""Phase 2b: group the tracks into drafts of new playlists, review and edit them.

Ports the original app's My Clusters page (pages/6_My_Clusters.py: filters, the 3-level counts, the
"By category" and "By specific genre" modes, which tracks leave the pool) and Cluster Results page
(pages/_Cluster_Results.py: one card per group, the unmatched tracks and their manual assignment,
"Re-open", "keep visible"), on top of the ported analysis/grouping.py and analysis/genres.py.

A draft is the original's `clustering_results` session entry made persistent: it lives in the
`drafts` table as track IDs only (names, artists and numbers are read from the cache when the draft
is shown). Progress rows (progress.py) are the original's user_cluster_progress.

Deliberate differences from the original, besides the ones noted on each function:
  - There is no KMeans mode (Phase 4, needs audio features).
  - Pre-filters apply only when given; the original always passed "all decades" and "all families",
    which silently dropped tracks without a decade before they could reach the unmatched list.
  - The original collected a track list per playlist and then dropped duplicates before filtering by
    playlist (keeping the first playlist); here the playlist filter comes first, so a track in two
    playlists counts once and is kept if either is selected.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Annotated, Literal, Union

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from tailorlist_mcp import dashboard, progress, queries
from tailorlist_mcp.analysis import grouping
from tailorlist_mcp.analysis.formatting import fmt_duration
from tailorlist_mcp.analysis.genres import GENRE_FAMILIES, assign_family, track_matches_families

FAMILIES = [*GENRE_FAMILIES, "Other"]
MAX_GROUPS = 100
KEEP_DRAFTS = 20            # drafts are a working copy: only the latest ones are kept
RESULT_BYTES = 25_000       # cap of the structured content that travels in the tool result
SAMPLES = 5
TOP_N = 5
UNMATCHED_IN_RESULT = 15

NO_GENRE = "no genre"
NO_DECADE = "no release decade"
NO_GENRE_NO_DECADE = "no genre and no release decade"
NO_MATCH = "no match among the selected genres"


class DraftError(ValueError):
    """The request can't be done as asked. The message says why."""


# ---------------------------------------------------------------- inputs

class GroupFilters(BaseModel):
    """Narrow the pool of tracks before grouping. Every field is optional; the ones given are combined with AND."""

    model_config = ConfigDict(extra="forbid")

    playlists: list[str] | None = Field(None, description="Only tracks from these loaded playlists (names, unique parts of a name, IDs or links). Default: all loaded playlists.")
    decades: list[int] | None = Field(None, description="Only these release decades, e.g. [1980, 1990]. Tracks with no release date are dropped when this is given.")
    genre_families: list[str] | None = Field(None, description="Only tracks matching ANY of these families: " + ", ".join(FAMILIES) + ". Tracks with no genre always pass (they end up unmatched).")


# ---------------------------------------------------------------- the pool (the original's filters and counts)

def _family_names(names: list[str]) -> list[str]:
    known = {k.casefold(): k for k in FAMILIES}
    out = []
    for n in names:
        fam = known.get(n.strip().casefold())
        if not fam:
            raise queries.QueryError(f"Unknown genre family '{n}'. Families: {', '.join(FAMILIES)}.")
        out.append(fam)
    return out


def _no_genre(g) -> bool:
    return not g or pd.isna(g) or str(g).strip() == ""


def build_pool(conn: sqlite3.Connection, f: GroupFilters | None, include_done: bool = False) -> tuple[pd.DataFrame, dict]:
    """The tracks to group (one row per unique track) and the original's counts after each filter.

    Order as in My Clusters: playlists, decades, genre families, then the tracks already done.
    """
    df = queries._frame(conn)           # raises QueryError when nothing is loaded
    f = f or GroupFilters()
    counts = {"in_cache": int(df["track_id"].nunique())}
    used: list[str] = []
    if f.playlists:
        names = [dashboard.find_playlist(conn, ref)["name"] for ref in f.playlists]
        df = df[df["playlist_name"].isin(names)]
        used.append("playlists=" + ", ".join(names))
    counts["in_playlists"] = int(df["track_id"].nunique())
    if f.decades:
        df = df[df["decade"].isin([int(d) // 10 * 10 for d in f.decades])]
        used.append("decades=" + ", ".join(f"{int(d) // 10 * 10}s" for d in f.decades))
    counts["after_decades"] = int(df["track_id"].nunique())
    if f.genre_families:
        fams = _family_names(f.genre_families)
        df = df[df["artist_genres"].map(lambda g: track_matches_families(g, fams))]   # original's family filter
        used.append("families=" + ", ".join(fams))
    counts["after_families"] = int(df["track_id"].nunique())

    playlists = df.groupby("track_id")["playlist_name"].agg(lambda s: sorted(set(s)))
    df = df.drop_duplicates("track_id").copy()
    df["playlists"] = df["track_id"].map(playlists)
    done = progress.done_ids(conn)
    counts["done_left_out"] = 0
    if done and not include_done:
        is_done = df["track_id"].isin(done)
        counts["done_left_out"] = int(is_done.sum())
        df = df[~is_done]
    counts["pool"] = len(df)
    counts["filters"] = used
    return df, counts


def genre_menu(pool: pd.DataFrame, per_family: int = 8) -> list[dict]:
    """The original's "Pick genres" list: the tags in the pool under their family, with track counts.
    A tag belongs to the first family it matches (assign_family order)."""
    tags: dict[str, int] = {}
    for g in pool["artist_genres"]:
        if _no_genre(g):
            continue
        for t in {x.strip() for x in g.split(",") if x.strip()}:
            tags[t] = tags.get(t, 0) + 1
    by_family: dict[str, list] = {}
    for tag, n in tags.items():
        by_family.setdefault(assign_family(tag)[0], []).append((tag, n))
    out = []
    for fam in FAMILIES:
        items = sorted(by_family.get(fam, []), key=lambda x: (-x[1], x[0]))
        if items:
            out.append({"family": fam, "genres": len(items), "tracks": int(sum(n for _, n in items)),
                        "top": [{"genre": t, "tracks": n} for t, n in items[:per_family]]})
    return out


def genres_of_families(pool: pd.DataFrame, families: list[str]) -> list[str]:
    """The original's default pre-selection: every tag in the pool whose family is one of these."""
    fams = set(_family_names(families))
    tags = {t.strip() for g in pool["artist_genres"] if not _no_genre(g) for t in g.split(",") if t.strip()}
    return sorted(t for t in tags if assign_family(t)[0] in fams)


# ---------------------------------------------------------------- grouping

def _label(by: str, genres_mode: bool, also_by_decade: bool) -> str:
    if genres_mode:
        return "by specific genres" + (" × decade" if also_by_decade else "")
    return {"decade": "by category: decade", "genre_family": "by category: genre family",
            "both": "by category: genre family × decade"}[by]


def group(pool: pd.DataFrame, mode: str, by: str = "both", genres: list[str] | None = None,
          also_by_decade: bool = False) -> tuple[dict[str, list[str]], list[dict]]:
    """Run the ported grouping. Returns ({group name: [track_id]}, unmatched entries).

    Unmatched entry: {"id", "reason", "kind"}; kind "missing" = the track lacks the data to be grouped
    (the original's Unmatched Tracks section), "left_out" = it has genres but none of the selected ones.

    By category (grouping.group_by_category): one group per genre family and/or decade. A track in
    several families goes to the FIRST family (the original used assign_family(...)[0]).
    By genres (grouping.group_by_specific_genres): one group per chosen tag; a track with several
    chosen tags appears in each. Tracks with no genre are unmatched; tracks whose tags match none
    of the chosen ones were "silently excluded" in the original: here they are listed as left out.
    """
    if mode == "category":
        gd, gg = by in ("decade", "both"), by in ("genre_family", "both")
        clustered, unmatched = grouping.group_by_category(pool, gd, gg)
        groups = {n: list(dict.fromkeys(g["track_id"])) for n, g in clustered.groupby("cluster_name")} if len(clustered) else {}
        out = []
        for _, r in unmatched.iterrows():
            no_g = gg and r["_genre_group"] == "Unknown Genre"
            no_d = gd and r["_decade_group"] == "Unknown Decade"
            out.append({"id": r["track_id"], "kind": "missing",
                        "reason": NO_GENRE_NO_DECADE if (no_g and no_d) else NO_GENRE if no_g else NO_DECADE})
    else:
        pool = pool.copy()
        pool["artist_genres"] = pool["artist_genres"].fillna("").str.lower()
        wanted = [g.strip().lower() for g in genres or []]
        clustered = grouping.group_by_specific_genres(pool, wanted, also_by_decade)
        groups = {n: list(dict.fromkeys(g["track_id"])) for n, g in clustered.groupby("cluster_name")} if len(clustered) else {}
        matched = set(clustered["track_id"]) if len(clustered) else set()
        out = []
        for _, r in pool[~pool["track_id"].isin(matched)].iterrows():
            if _no_genre(r["artist_genres"]):
                out.append({"id": r["track_id"], "kind": "missing", "reason": NO_GENRE})
            else:
                out.append({"id": r["track_id"], "kind": "left_out", "reason": NO_MATCH})
    ordered = dict(sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])))
    return ordered, out


def make_groups(conn: sqlite3.Connection, pool: pd.DataFrame, counts: dict, mode: str, by: str,
                genres: list[str] | None, also_by_decade: bool) -> tuple[dict[str, list[str]], list[dict]]:
    """group() plus the original's rule that unmatched tracks the user already assigned by hand
    ('manual' progress, "Save assignments") are not listed as unmatched again (_unmatched_done_ids)."""
    groups, unmatched = group(pool, mode, by, genres, also_by_decade)
    manual = {t for t, p in progress.load(conn).items() if p["status"] == "manual"}
    counts["manual_hidden"] = sum(1 for u in unmatched if u["id"] in manual)
    return groups, [u for u in unmatched if u["id"] not in manual]


def preview_text(counts: dict, groups: dict, unmatched: list[dict], label: str) -> str:
    c = counts
    steps = [f"{c['in_cache']:,} unique tracks in the cache"]
    if c["in_playlists"] != c["in_cache"]:
        steps.append(f"{c['in_playlists']:,} in the chosen playlists")
    if c["after_decades"] != c["in_playlists"]:
        steps.append(f"{c['after_decades']:,} in the chosen decades")
    if c["after_families"] != c["after_decades"]:
        steps.append(f"{c['after_families']:,} in the chosen genre families")
    if c["done_left_out"]:
        steps.append(f"{c['pool']:,} after leaving out {c['done_left_out']:,} already organized")
    grouped = len({t for ids in groups.values() for t in ids})
    reasons: dict[str, int] = {}
    for u in unmatched:
        reasons[u["reason"]] = reasons.get(u["reason"], 0) + 1
    lines = ["Pool: " + " -> ".join(steps) + ".",
             f"Grouping {label} would put {grouped:,} tracks in {len(groups)} groups"
             + (f" and leave {len(unmatched):,} unmatched ({', '.join(f'{n:,} {r}' for r, n in reasons.items())})." if unmatched else ".")]
    if c.get("manual_hidden"):
        lines.append(f"({c['manual_hidden']:,} more tracks you assigned to a group by hand earlier aren't listed as unmatched.)")
    return "\n".join(lines)


# ---------------------------------------------------------------- persistence

def new_draft(conn: sqlite3.Connection, mode: str, by: str, genres: list[str] | None, also_by_decade: bool,
              counts: dict, groups: dict[str, list[str]], unmatched: list[dict], include_done: bool) -> dict:
    now = int(time.time())
    seq = (conn.execute("SELECT COALESCE(MAX(seq), 0) FROM drafts").fetchone()[0]) + 1
    d = {
        "id": f"d{seq}", "created": now, "mode": mode, "by": by, "genres": genres or [], "also_by_decade": also_by_decade,
        "include_done": include_done, "pool": counts,
        "groups": [{"name": n, "tracks": ids, "keep_visible": False} for n, ids in groups.items()],
        "unmatched": unmatched, "skipped": [], "assigned": [],
    }
    with conn:
        conn.execute("INSERT INTO drafts (id, seq, created_at, updated_at, data) VALUES (?, ?, ?, ?, ?)",
                     (d["id"], seq, now, now, json.dumps(d)))
        conn.execute("DELETE FROM drafts WHERE seq <= ?", (seq - KEEP_DRAFTS,))
    return d


def save_draft(conn: sqlite3.Connection, d: dict) -> None:
    with conn:
        conn.execute("UPDATE drafts SET data = ?, updated_at = ? WHERE id = ?", (json.dumps(d), int(time.time()), d["id"]))


def prune_unloaded_tracks(conn: sqlite3.Connection) -> int:
    """Drop from every draft the tracks that are no longer in the cache (after unload_playlists).

    Returns how many track entries were removed across all drafts.
    """
    loaded = {r[0] for r in conn.execute("SELECT id FROM tracks")}
    removed = 0
    for r in conn.execute("SELECT data FROM drafts").fetchall():
        d = json.loads(r[0])
        before = (sum(len(g["tracks"]) for g in d["groups"]) + len(d["unmatched"])
                  + len(d["skipped"]) + len(d["assigned"]))
        for g in d["groups"]:
            g["tracks"] = [t for t in g["tracks"] if t in loaded]
        d["unmatched"] = [u for u in d["unmatched"] if u["id"] in loaded]
        d["skipped"] = [t for t in d["skipped"] if t in loaded]
        d["assigned"] = [t for t in d["assigned"] if t in loaded]
        after = (sum(len(g["tracks"]) for g in d["groups"]) + len(d["unmatched"])
                 + len(d["skipped"]) + len(d["assigned"]))
        if after != before:
            removed += before - after
            save_draft(conn, d)
    return removed


def list_drafts(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for r in conn.execute("SELECT data FROM drafts ORDER BY seq DESC"):
        d = json.loads(r["data"])
        out.append({"id": d["id"], "created": d["created"], "label": _label(d["by"], d["mode"] == "genres", d["also_by_decade"]),
                    "groups": len(d["groups"]), "tracks": len({t for g in d["groups"] for t in g["tracks"]}),
                    "unmatched": len(d["unmatched"])})
    return out


def load_draft(conn: sqlite3.Connection, ref: str | None) -> dict:
    """A draft by ID ("d3", "3") or the latest one (no ref, or "latest")."""
    ref = (ref or "").strip().lower()
    if ref in ("", "latest", "last"):
        row = conn.execute("SELECT data FROM drafts ORDER BY seq DESC LIMIT 1").fetchone()
        if not row:
            raise DraftError("There are no drafts yet. Use group_tracks to make one.")
    else:
        row = conn.execute("SELECT data FROM drafts WHERE id = ?", (ref if ref.startswith("d") else f"d{ref}",)).fetchone()
        if not row:
            ids = ", ".join(d["id"] for d in list_drafts(conn)) or "none"
            raise DraftError(f"There is no draft '{ref}'. Drafts: {ids}.")
    return json.loads(row["data"])


# ---------------------------------------------------------------- the draft as data (cards, unmatched, progress)

def _info(conn: sqlite3.Connection) -> pd.DataFrame:
    df = dashboard.tracks_frame(dashboard.records(conn))
    if df.empty:
        raise DraftError("Nothing loaded yet. Use list_my_playlists, ask which playlists to load, then load_playlists.")
    playlists = df.groupby("track_id")["playlist_name"].agg(lambda s: sorted(set(s)))
    info = df.drop_duplicates("track_id").set_index("track_id", drop=False)
    info["playlists"] = playlists
    info["_tags"] = info["artist_genres"].fillna("").map(lambda g: [t.strip().lower() for t in g.split(",") if t.strip()])
    return info


def _year(v):
    return None if pd.isna(v) else int(v)


def _track_row(r: pd.Series) -> dict:
    dur = None if pd.isna(r["duration_raw"]) else fmt_duration(r["duration_raw"] / 60000)
    return {"id": r["track_id"], "name": r["track_name"], "artist": r["artist_display"], "album": r["album_name"] or "",
            "year": _year(r["release_year"]), "duration": dur, "popularity": None if pd.isna(r["popularity_raw"]) else int(r["popularity_raw"]),
            "playlists": r["playlists"], "url": r["url"]}


def _group_card(info: pd.DataFrame, g: dict, done: set[str], assigned: set[str], full: bool) -> dict:
    ids = [t for t in g["tracks"] if t in info.index]
    rows = info.loc[ids]
    artists = rows["artist_name"].value_counts().head(TOP_N)
    tag_counts: dict[str, int] = {}
    for tags in rows["_tags"]:
        for t in set(tags):
            tag_counts[t] = tag_counts.get(t, 0) + 1
    top_tags = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_N]
    decades = rows["decade"].dropna().astype(int).value_counts().sort_index()
    hours = float(rows["duration_raw"].sum()) / 3_600_000
    by_pop = rows.sort_values("popularity_raw", ascending=False, na_position="last")
    card = {
        "name": g["name"], "size": len(ids), "missing": len(g["tracks"]) - len(ids), "hours": round(hours, 1),
        "top_artists": [{"name": n, "tracks": int(c)} for n, c in artists.items()],
        "top_genres": [{"genre": t, "tracks": c} for t, c in top_tags],
        "decades": [{"decade": int(d), "tracks": int(c)} for d, c in decades.items()],
        "samples": [{"name": r["track_name"], "artist": r["artist_display"], "year": _year(r["release_year"]), "url": r["url"]}
                    for _, r in by_pop.head(SAMPLES).iterrows()],
        "keep_visible": bool(g.get("keep_visible")), "done": bool(ids) and all(t in done for t in ids),
        "manual": sum(1 for t in ids if t in assigned),
    }
    if full:
        card["tracks"] = [_track_row(r) for _, r in by_pop.iterrows()]
    return card


def _unmatched_view(info: pd.DataFrame, entries: list[dict], full: bool) -> dict:
    present = [e for e in entries if e["id"] in info.index]
    reasons: dict[str, int] = {}
    for e in present:
        reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
    shown = present if full else present[:UNMATCHED_IN_RESULT]
    return {
        "total": len(present),
        "by_reason": [{"reason": r, "tracks": n} for r, n in sorted(reasons.items(), key=lambda kv: -kv[1])],
        "items": [{**_track_row(info.loc[e["id"]]), "reason": e["reason"]} if full else
                  {"id": e["id"], "name": info.loc[e["id"], "track_name"], "artist": info.loc[e["id"], "artist_display"], "reason": e["reason"]}
                  for e in shown],
    }


def draft_data(conn: sqlite3.Connection, d: dict, full: bool = False) -> dict:
    """Everything the draft dashboard shows. full=True adds every track (the web page); the tool
    result carries cards, sample tracks and the first unmatched tracks only."""
    info = _info(conn)
    prog = progress.load(conn)
    done = {t for t, p in prog.items() if p["status"] == "done"}
    assigned = set(d["assigned"])
    cards = sorted((_group_card(info, g, done, assigned, full) for g in d["groups"]), key=lambda c: (-c["size"], c["name"]))
    grouped = {t for g in d["groups"] for t in g["tracks"] if t in info.index}
    missing = [e for e in d["unmatched"] if e["kind"] == "missing"]
    left_out = [e for e in d["unmatched"] if e["kind"] == "left_out"]
    skipped = [t for t in d["skipped"] if t in info.index]
    drafts = list_drafts(conn)
    return {
        "id": d["id"], "created": d["created"], "label": _label(d["by"], d["mode"] == "genres", d["also_by_decade"]),
        "mode": d["mode"], "by": d["by"], "genres": d["genres"], "also_by_decade": d["also_by_decade"],
        "pool": d["pool"],
        "totals": {"groups": len(cards), "grouped": len(grouped), "unmatched": len([e for e in missing if e["id"] in info.index]),
                   "left_out": len([e for e in left_out if e["id"] in info.index]), "skipped": len(skipped)},
        "groups": cards,
        "unmatched": _unmatched_view(info, missing, full),
        "left_out": _unmatched_view(info, left_out, full),
        "progress": progress.summary(conn),
        "drafts": [x for x in drafts if x["id"] != d["id"]][:10],
    }


def result_data(data: dict) -> dict:
    """The dashboard data capped to RESULT_BYTES for the tool result: fewer samples, then fewer
    top lists, then fewer groups' details. The web page always gets the full data."""
    def size() -> int:
        return len(json.dumps(data, ensure_ascii=False))
    steps = [("samples", 3), ("samples", 1), ("lists", 3), ("samples", 0), ("lists", 1)]
    for what, n in steps:
        if size() <= RESULT_BYTES:
            break
        for g in data["groups"]:
            if what == "samples":
                g["samples"] = g["samples"][:n]
            else:
                g["top_artists"], g["top_genres"] = g["top_artists"][:n], g["top_genres"][:n]
        data["trimmed"] = True
    for key in ("unmatched", "left_out"):
        while size() > RESULT_BYTES and data[key]["items"]:
            data[key]["items"] = data[key]["items"][:len(data[key]["items"]) // 2]
            data["trimmed"] = True
    total = len(data["groups"])
    while size() > RESULT_BYTES and len(data["groups"]) > 1:      # last resort: only the biggest groups
        data["groups"] = data["groups"][:max(1, len(data["groups"]) * 3 // 4)]
        data["groups_omitted"] = total - len(data["groups"])
        data["trimmed"] = True
    return data


def _names(items: list[dict], key: str, n: int) -> str:
    return ", ".join(f"{x[key]} ({x['tracks']})" for x in items[:n])


def summary(data: dict, max_groups: int = 25) -> str:
    """Short text for Claude: group sizes, a few sample tracks, unmatched with reasons, progress."""
    t, p = data["totals"], data["progress"]
    lines = [f"Draft {data['id']} ({data['label']}): {t['grouped']:,} tracks in {t['groups']} groups; "
             f"{t['unmatched']:,} unmatched" + (f", {t['left_out']:,} left out" if t["left_out"] else "")
             + (f", {t['skipped']:,} skipped" if t["skipped"] else "")
             + f". Overall progress: {p['organized']:,} of {p['total']:,} tracks organized ({p['percent']}%)."]
    for g in data["groups"][:max_groups]:
        flags = (" [done]" if g["done"] else "") + (" [keep visible]" if g["keep_visible"] else "")
        samples = "; ".join(f"{s['name']} - {s['artist']}" for s in g["samples"][:2])
        lines.append(f"- {g['name']}{flags}: {g['size']} track{'s' if g['size'] != 1 else ''}, {g['hours']} h. Artists: {_names(g['top_artists'], 'name', 3)}. "
                     f"Genres: {_names(g['top_genres'], 'genre', 3)}. e.g. {samples}")
    if len(data["groups"]) > max_groups:
        small = data["groups"][max_groups:]
        lines.append(f"- ...and {len(small)} smaller groups ({sum(g['size'] for g in small)} tracks): "
                     + ", ".join(g["name"] for g in small[:15]) + (", ..." if len(small) > 15 else ""))
    if data.get("groups_omitted"):
        lines.append(f"({data['groups_omitted']} more small groups are on the dashboard page.)")
    for key, title in (("unmatched", "Unmatched"), ("left_out", "Left out")):
        u = data[key]
        if u["total"]:
            ex = "; ".join(f"{i['name']} - {i['artist']}" for i in u["items"][:3])
            lines.append(f"{title}: {u['total']:,} tracks (" + ", ".join(f"{r['tracks']:,} {r['reason']}" for r in u["by_reason"])
                         + f"). e.g. {ex}")
    others = data["drafts"]
    if others:
        lines.append("Other drafts: " + "; ".join(f"{x['id']} ({x['label']}, {x['groups']} groups, {x['tracks']} tracks)" for x in others[:5]) + ".")
    lines.append("Show the user this draft and get their OK before anything is written; change it with edit_draft "
                 "(rename, move, assign, drop_group, merge, keep_visible, skip_unmatched). Writing playlists to Spotify "
                 "isn't available yet. Don't paste this list back as text: the dashboard has it.")
    return "\n".join(lines)


# ---------------------------------------------------------------- edit_draft

class TrackSelector(queries.Filters):
    """Which tracks an operation applies to. Give at least one field (or all=true). Fields combine with AND,
    inside the tracks of the source group (or of the unmatched list)."""

    track_ids: list[str] | None = Field(None, description="Spotify track IDs.")
    reason: str | None = Field(None, description="Unmatched tracks only: text in the reason, e.g. 'no genre' or 'decade'.")
    all: bool = Field(False, description="Every track of the source.")


class Rename(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["rename"]
    group: str = Field(description="The group's name (or a unique part of it).")
    to: str = Field(description="The new name.")


class Move(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["move"]
    tracks: TrackSelector
    to: str = Field(description="Target group.")
    from_group: str | None = Field(None, description="Only move tracks out of this group. Default: out of whichever group has them.")
    create_if_missing: bool = Field(False, description="Create the target group when it doesn't exist (otherwise a typo is an error).")


class Assign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["assign"]
    tracks: TrackSelector
    to: str = Field(description="Target group.")
    create_if_missing: bool = False


class DropGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["drop_group"]
    group: str


class Merge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["merge"]
    groups: list[str] = Field(min_length=2, description="The groups to merge.")
    into: str | None = Field(None, description="Name of the merged group. Default: the first group's name.")


class KeepVisible(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["keep_visible"]
    group: str
    on: bool = True


class SkipUnmatched(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["skip_unmatched"]


class Reopen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["reopen"]
    group: str


Change = Annotated[Union[Rename, Move, Assign, DropGroup, Merge, KeepVisible, SkipUnmatched, Reopen], Field(discriminator="op")]


def _find_group(groups: list[dict], ref: str) -> dict:
    q = ref.strip().casefold()
    exact = [g for g in groups if g["name"].casefold() == q]
    if exact:
        return exact[0]
    part = [g for g in groups if q and q in g["name"].casefold()]
    if len(part) == 1:
        return part[0]
    if part:
        raise DraftError(f"'{ref}' matches several groups: {', '.join(g['name'] for g in part[:10])}.")
    names = [g["name"] for g in groups]
    raise DraftError(f"There is no group '{ref}'. Groups: {', '.join(names[:20])}{', ...' if len(names) > 20 else ''}.")


def _target(groups: list[dict], name: str, create: bool) -> dict:
    try:
        return _find_group(groups, name)
    except DraftError:
        if not create:
            raise
    new = {"name": name.strip(), "tracks": [], "keep_visible": False}
    groups.append(new)
    return new


def _select(conn: sqlite3.Connection, sel: TrackSelector, source: set[str], reasons: dict[str, str] | None = None) -> set[str]:
    fields = sel.model_dump(exclude={"track_ids", "reason", "all"})
    if not (sel.all or sel.track_ids or sel.reason or any(v is not None for v in fields.values())):
        raise DraftError("Say which tracks: give at least one field in `tracks` (artist, text, genre, decade, track_ids...) or all=true.")
    ids = set(source)
    if sel.track_ids:
        ids &= set(sel.track_ids)
    if any(v is not None for v in fields.values()):
        frame = queries._frame(conn)
        ids &= set(queries.apply_filters(conn, frame, queries.Filters(**fields))[0]["track_id"])
    if sel.reason:
        ids = {t for t in ids if reasons and sel.reason.casefold() in reasons.get(t, "").casefold()}
    return ids


def apply_changes(conn: sqlite3.Connection, d: dict, changes: list) -> list[str]:
    """Apply the operations to the draft in order, all or none, and return what each did.

    Progress side effects (the original wrote them when the user clicked the matching button):
      assign          -> 'manual' rows with the group's name ("Save assignments"); kept in step when the
                         group is renamed, merged or its tracks moved; removed if the group is dropped
      skip_unmatched  -> 'done' rows for the unmatched tracks ("Skip N remaining tracks")
      keep_visible    -> drops the 'done' rows of the group's tracks ("keep visible")
      reopen          -> drops the 'done' rows of the group's tracks ("Re-open")
    """
    groups = [dict(g, tracks=list(g["tracks"])) for g in d["groups"]]
    unmatched = [dict(u) for u in d["unmatched"]]
    skipped, assigned = list(d["skipped"]), list(d["assigned"])
    effects: dict[str, str] = {}            # track_id -> "done" | "reopen" (the last operation wins)
    notes: list[str] = []
    if not changes:
        raise DraftError("No changes given.")

    for ch in changes:
        if isinstance(ch, Rename):
            g = _find_group(groups, ch.group)
            new = ch.to.strip()
            if not new:
                raise DraftError("The new name is empty.")
            if any(o is not g and o["name"].casefold() == new.casefold() for o in groups):
                raise DraftError(f"There is already a group called '{new}'. Use merge to combine groups.")
            notes.append(f"renamed '{g['name']}' to '{new}'")
            g["name"] = new
        elif isinstance(ch, Move):
            to = _target(groups, ch.to, ch.create_if_missing)
            sources = [_find_group(groups, ch.from_group)] if ch.from_group else [g for g in groups if g is not to]
            have = {t for g in sources for t in g["tracks"]}
            ids = _select(conn, ch.tracks, have)
            if not ids:
                raise DraftError("No track in " + (f"'{sources[0]['name']}'" if ch.from_group else "the other groups") + " matches those filters.")
            for g in sources:
                if g is to:
                    continue
                g["tracks"] = [t for t in g["tracks"] if t not in ids]
            to["tracks"] += [t for t in ids if t not in set(to["tracks"])]
            groups = [g for g in groups if g["tracks"] or g is to]
            notes.append(f"moved {len(ids)} tracks to '{to['name']}'")
        elif isinstance(ch, Assign):
            reasons = {u["id"]: u["reason"] for u in unmatched}
            ids = _select(conn, ch.tracks, set(reasons), reasons)
            if not ids:
                raise DraftError("No unmatched track matches those filters.")
            to = _target(groups, ch.to, ch.create_if_missing)
            to["tracks"] += [t for t in sorted(ids) if t not in set(to["tracks"])]
            unmatched = [u for u in unmatched if u["id"] not in ids]
            assigned += [t for t in sorted(ids) if t not in assigned]
            notes.append(f"assigned {len(ids)} unmatched tracks to '{to['name']}'")
        elif isinstance(ch, DropGroup):
            g = _find_group(groups, ch.group)
            groups = [x for x in groups if x is not g]
            elsewhere = {t for x in groups for t in x["tracks"]}
            freed = [t for t in g["tracks"] if t not in elsewhere]
            unmatched += [{"id": t, "kind": "left_out", "reason": f"from the dropped group '{g['name']}'"} for t in freed]
            assigned = [t for t in assigned if t not in set(freed)]
            notes.append(f"dropped '{g['name']}' ({len(g['tracks'])} tracks; they stay in the pool, listed as left out)")
        elif isinstance(ch, Merge):
            found = []
            for ref in ch.groups:
                g = _find_group(groups, ref)
                if g in found:
                    raise DraftError(f"'{ref}' is listed twice.")
                found.append(g)
            name = (ch.into or found[0]["name"]).strip()
            if any(o not in found and o["name"].casefold() == name.casefold() for o in groups):
                raise DraftError(f"There is already a group called '{name}' that isn't part of the merge.")
            merged = {"name": name, "tracks": list(dict.fromkeys(t for g in found for t in g["tracks"])),
                      "keep_visible": any(g.get("keep_visible") for g in found)}
            groups = [g for g in groups if g not in found] + [merged]
            notes.append(f"merged {', '.join(repr(g['name']) for g in found)} into '{name}' ({len(merged['tracks'])} tracks)")
        elif isinstance(ch, KeepVisible):
            g = _find_group(groups, ch.group)
            g["keep_visible"] = ch.on
            if ch.on:
                effects.update({t: "reopen" for t in g["tracks"]})
            notes.append(f"'{g['name']}' " + ("kept visible: its tracks stay in the pool and aren't marked done when written" if ch.on else "no longer kept visible"))
        elif isinstance(ch, SkipUnmatched):
            ids = [u["id"] for u in unmatched if u["kind"] == "missing"]
            if not ids:
                raise DraftError("There are no unmatched tracks to skip.")
            unmatched = [u for u in unmatched if u["kind"] != "missing"]
            skipped += [t for t in ids if t not in skipped]
            effects.update({t: "done" for t in ids})
            notes.append(f"skipped {len(ids)} unmatched tracks (marked done: they leave the pool; reset_progress brings them back)")
        elif isinstance(ch, Reopen):
            g = _find_group(groups, ch.group)
            effects.update({t: "reopen" for t in g["tracks"]})
            notes.append(f"re-opened '{g['name']}' (its tracks count as not done)")

    # Everything validated: store the draft and sync the progress rows.
    groups = [g for g in groups if g["tracks"]]
    names_by_track: dict[str, str] = {}
    for g in groups:
        for t in g["tracks"]:
            names_by_track.setdefault(t, g["name"])
    assigned = [t for t in assigned if t in names_by_track]
    gone = set(d["assigned"]) - set(assigned)
    d["groups"], d["unmatched"], d["skipped"], d["assigned"] = groups, unmatched, skipped, assigned
    if gone:
        progress.clear_manual(conn, gone)
    if assigned:
        progress.save_manual(conn, {t: names_by_track[t] for t in assigned})
    done = [t for t, e in effects.items() if e == "done"]
    reopen = [t for t, e in effects.items() if e == "reopen"]
    if done:
        progress.mark_done(conn, done)
    if reopen:
        progress.reopen(conn, reopen)
    save_draft(conn, d)
    return notes


# ---------------------------------------------------------------- hook for Phase 3 (writing playlists)

def mark_group_written(conn: sqlite3.Connection, draft_id: str, group_name: str) -> int:
    """Phase 3 calls this after a group was written to Spotify as a playlist.

    The original's "Mark these tracks as done": every track of the group becomes 'done' (a 'manual'
    row is upgraded; the original left those as 'manual', but once the track is in the playlist it
    is placed), so they leave the pool of the next grouping and count toward the progress. A group
    flagged keep_visible is not marked (the original dropped those rows right after marking).
    Returns how many tracks were marked.
    """
    d = load_draft(conn, draft_id)
    g = _find_group(d["groups"], group_name)
    if g.get("keep_visible"):
        return 0
    return progress.mark_done(conn, g["tracks"])
