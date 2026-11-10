"""The data tools of Phase 2a: collection_stats, find_tracks, find_duplicates, compare_playlists.

They answer in small text tables (aggregates, or listings capped at MAX_LIMIT rows) so Claude
never receives the whole collection and can chart the numbers itself.

Built on the same records / track frame as the dashboards (dashboard.records, tracks_frame:
artist_name is the primary artist, genres are the primary artist's) and on the ported analysis
modules: comparison (duplicates, per-playlist duration, popularity, genre diversity, era, age,
size and follower rankings), overview (decade / popularity-bin / month conventions), genres
(families). "Unique tracks" always means unique Spotify track_id.
"""
from __future__ import annotations

import datetime
import re
import sqlite3

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from tailorlist_mcp import dashboard
from tailorlist_mcp.analysis import comparison
from tailorlist_mcp.analysis.duplicates import song_key
from tailorlist_mcp.analysis import overview as ov
from tailorlist_mcp.analysis.formatting import fmt_duration
from tailorlist_mcp.analysis.genres import GENRE_FAMILIES, assign_family

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
MAX_SPLIT_VALUES = 25

DIMENSIONS = ("decade", "year", "artist", "album", "genre", "genre_family", "playlist", "month_added", "popularity_bin")
#: Dimensions that read in order (a chart axis), not by size.
_ORDERED = {"decade", "year", "month_added", "popularity_bin"}
METRICS = ("unique_tracks", "entries", "hours", "avg_popularity", "share")
_SORTS = {"popularity": "popularity_raw", "year": "release_year", "added": "_added", "duration": "duration_raw",
          "name": "_name_key", "artist": "_artist_key"}
_ASCENDING_BY_DEFAULT = {"name", "artist"}


class QueryError(ValueError):
    """The request can't be answered as asked (bad name, unknown field...). The message says why."""


class Filters(BaseModel):
    """Narrow the tracks a tool looks at. Every field is optional; the ones given are combined with AND."""

    model_config = ConfigDict(extra="forbid")

    playlists: list[str] | None = Field(None, description="Only these loaded playlists (names, unique parts of a name, IDs or links).")
    artist: str | None = Field(None, description="Artist name; matches primary or featured artists. Exact name if one exists, else part of a name.")
    album: str | None = Field(None, description="Album name (exact if one exists, else part of a name).")
    genre: str | None = Field(None, description="One exact genre tag of the primary artist, e.g. 'indie rock' (use collection_stats group_by='genre' to see the tags).")
    genre_family: str | None = Field(None, description="A genre family: " + ", ".join(GENRE_FAMILIES) + ", Other.")
    decade: int | None = Field(None, description="Release decade, e.g. 1990.")
    year_from: int | None = Field(None, description="Earliest release year.")
    year_to: int | None = Field(None, description="Latest release year.")
    popularity_min: int | None = Field(None, description="Lowest Spotify popularity (0-100).")
    popularity_max: int | None = Field(None, description="Highest Spotify popularity (0-100).")
    added_from: str | None = Field(None, description="Added to a playlist on or after this date (YYYY-MM-DD, or YYYY-MM).")
    added_to: str | None = Field(None, description="Added to a playlist on or before this date (YYYY-MM-DD, or YYYY-MM).")
    duration_min: float | None = Field(None, description="Shortest track, in minutes.")
    duration_max: float | None = Field(None, description="Longest track, in minutes.")
    text: str | None = Field(None, description="Text in the track name.")


# ---------------------------------------------------------------- frame and filters

def _frame(conn: sqlite3.Connection) -> pd.DataFrame:
    df = dashboard.tracks_frame(dashboard.records(conn))
    if df.empty:
        raise QueryError("Nothing loaded yet. Use list_my_playlists, ask which playlists to load, then load_playlists.")
    df["_tags"] = df["artist_genres"].fillna("").map(lambda g: [t.strip().lower() for t in g.split(",") if t.strip()])
    df["_added"] = pd.to_datetime(df["added_at"], errors="coerce", utc=True)
    return df


def _text_match(series: pd.Series, query: str) -> pd.Series:
    """Exact (case-insensitive) match when any row matches that way, else a substring match."""
    q = query.strip().casefold()
    exact = series.fillna("").str.casefold() == q
    return exact if exact.any() else series.fillna("").str.casefold().str.contains(q, regex=False)


def _bound(text: str, end: bool) -> pd.Timestamp:
    try:
        ts = pd.Timestamp(text, tz="UTC")
    except ValueError as exc:
        raise QueryError(f"'{text}' isn't a date. Use YYYY-MM-DD or YYYY-MM.") from exc
    if end:  # inclusive: up to the end of the day (or month)
        ts = ts + (pd.offsets.MonthEnd(0) if len(text.strip()) == 7 else pd.Timedelta(0)) + pd.Timedelta(days=1)
    return ts


def apply_filters(conn: sqlite3.Connection, df: pd.DataFrame, f: Filters | None) -> tuple[pd.DataFrame, list[str]]:
    """The rows that pass the filters, and a short description of each filter used."""
    if f is None:
        return df, []
    mask = pd.Series(True, index=df.index)
    used: list[str] = []
    if f.playlists:
        names = [dashboard.find_playlist(conn, ref)["name"] for ref in f.playlists]
        mask &= df["playlist_name"].isin(names)
        used.append("playlists=" + ", ".join(names))
    if f.artist:
        all_artists = (df["artist_name"].fillna("") + ", " + df["secondary_artists"].fillna("")).str.casefold()
        q = f.artist.strip().casefold()
        exact = all_artists.map(lambda s: q in [p.strip() for p in s.split(", ")])
        mask &= exact if exact.any() else df["artist_display"].fillna("").str.casefold().str.contains(q, regex=False)
        used.append(f"artist={f.artist}")
    if f.album:
        mask &= _text_match(df["album_name"], f.album)
        used.append(f"album={f.album}")
    if f.genre:
        q = f.genre.strip().lower()
        mask &= df["_tags"].map(lambda tags: q in tags)
        used.append(f"genre={f.genre}")
    if f.genre_family:
        families = {k.casefold(): k for k in [*GENRE_FAMILIES, "Other"]}
        fam = families.get(f.genre_family.strip().casefold())
        if not fam:
            raise QueryError(f"Unknown genre family '{f.genre_family}'. Families: {', '.join(families.values())}.")
        mask &= df["artist_genres"].map(lambda g: fam in assign_family(g))
        used.append(f"genre_family={fam}")
    if f.decade is not None:
        mask &= df["decade"] == f.decade
        used.append(f"decade={f.decade}s")
    if f.year_from is not None:
        mask &= df["release_year"] >= f.year_from
        used.append(f"year>={f.year_from}")
    if f.year_to is not None:
        mask &= df["release_year"] <= f.year_to
        used.append(f"year<={f.year_to}")
    if f.popularity_min is not None:
        mask &= df["popularity_raw"] >= f.popularity_min
        used.append(f"popularity>={f.popularity_min}")
    if f.popularity_max is not None:
        mask &= df["popularity_raw"] <= f.popularity_max
        used.append(f"popularity<={f.popularity_max}")
    if f.added_from:
        mask &= df["_added"] >= _bound(f.added_from, end=False)
        used.append(f"added>={f.added_from}")
    if f.added_to:
        mask &= df["_added"] < _bound(f.added_to, end=True)
        used.append(f"added<={f.added_to}")
    if f.duration_min is not None:
        mask &= df["duration_raw"] >= f.duration_min * 60000
        used.append(f"duration>={f.duration_min}min")
    if f.duration_max is not None:
        mask &= df["duration_raw"] <= f.duration_max * 60000
        used.append(f"duration<={f.duration_max}min")
    if f.text:
        mask &= df["track_name"].fillna("").str.casefold().str.contains(f.text.strip().casefold(), regex=False)
        used.append(f"text='{f.text}'")
    return df[mask], used


def _limit(limit: int | None) -> int:
    return max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))


def _cell(value) -> str:
    return "" if value is None else str(value).replace("|", "/").replace("\n", " ")


def _table(header: list[str], rows: list[list]) -> str:
    return "\n".join([" | ".join(header)] + [" | ".join(_cell(c) for c in row) for row in rows])


def _int_or_blank(v) -> str:
    return "" if pd.isna(v) else str(int(v))


# ---------------------------------------------------------------- collection_stats

def _dimension(df: pd.DataFrame, name: str) -> pd.Series:
    """One list of keys per row (empty list = the row has no value for this dimension)."""
    if name == "decade":
        return df["decade"].map(lambda v: [] if pd.isna(v) else [int(v)])
    if name == "year":
        return df["release_year"].map(lambda v: [] if pd.isna(v) else [int(v)])
    if name == "artist":
        return df["artist_name"].map(lambda v: [v] if v else [])
    if name == "album":
        return pd.Series(
            [[f"{a} ({ar})"] if isinstance(a, str) and a else [] for a, ar in zip(df["album_name"], df["artist_name"])],
            index=df.index,
        )
    if name == "genre":
        return df["_tags"]  # exact tags: a track counts once under each of its artist's tags
    if name == "genre_family":
        return df["artist_genres"].map(assign_family)
    if name == "playlist":
        return df["playlist_name"].map(lambda v: [v])
    if name == "month_added":
        return df["_added"].map(lambda v: [] if pd.isna(v) else [v.strftime("%Y-%m")])
    if name == "popularity_bin":
        size = ov.POPULARITY_BIN_SIZE
        return df["popularity_raw"].map(lambda v: [] if pd.isna(v) else [int(v // size * size)])
    raise QueryError(f"Unknown group_by '{name}'. Use one of: {', '.join(DIMENSIONS)}.")


def _label(name: str, key) -> str:
    if isinstance(key, float) and key.is_integer():  # iterrows turns integer keys into floats
        key = int(key)
    if name == "decade":
        return f"{key}s"
    if name == "popularity_bin":
        end = key + ov.POPULARITY_BIN_SIZE - 1
        return f"{key}" if key >= 100 else f"{key}-{min(end, 100)}"
    return str(key)


def _missing_note(df: pd.DataFrame, name: str, values: pd.Series) -> str | None:
    unique = df.assign(_n=values.map(len)).drop_duplicates("track_id")
    n = int((unique["_n"] == 0).sum())
    if not n:
        return None
    what = {"decade": "release year", "year": "release year", "album": "album", "genre": "genre",
            "month_added": "added date", "popularity_bin": "popularity", "artist": "artist"}.get(name, name)
    return f"{n} unique track{'s' if n != 1 else ''} {'have' if n != 1 else 'has'} no {what} and {'are' if n != 1 else 'is'} left out of the groups."


def collection_stats(
    conn: sqlite3.Connection,
    group_by: str,
    split_by: str | None = None,
    metrics: list[str] | None = None,
    filters: Filters | None = None,
    limit: int | None = None,
) -> str:
    """Counts and averages per group (optionally per group x split), as a small text table."""
    if group_by not in DIMENSIONS:
        raise QueryError(f"Unknown group_by '{group_by}'. Use one of: {', '.join(DIMENSIONS)}.")
    if split_by is not None and (split_by not in DIMENSIONS or split_by == group_by):
        raise QueryError(f"split_by must be a different dimension from group_by. Options: {', '.join(d for d in DIMENSIONS if d != group_by)}.")
    metrics = list(metrics or ["unique_tracks", "share"])
    bad = [m for m in metrics if m not in METRICS]
    if bad:
        raise QueryError(f"Unknown metrics {bad}. Use: {', '.join(METRICS)}.")
    limit = _limit(limit)

    df, used = apply_filters(conn, _frame(conn), filters)
    total_unique = int(df["track_id"].nunique())
    if df.empty:
        return f"No tracks match the filters ({'; '.join(used)})."

    gvals = _dimension(df, group_by)
    notes = [n for n in [_missing_note(df, group_by, gvals)] if n]
    long = df.assign(_g=gvals)
    if split_by:
        svals = _dimension(df, split_by)
        notes += [n for n in [_missing_note(df, split_by, svals)] if n]
        long = long.assign(_s=svals).explode("_s")
    long = long.explode("_g").dropna(subset=["_g"] + (["_s"] if split_by else []))
    keys = ["_g"] + (["_s"] if split_by else [])

    unique = long.drop_duplicates(keys + ["track_id"])
    agg = unique.groupby(keys).agg(
        unique_tracks=("track_id", "size"),
        hours=("duration_raw", lambda s: s.sum() / 3_600_000),
        avg_popularity=("popularity_raw", "mean"),
    )
    agg["entries"] = long.groupby(keys).size()
    agg["share"] = agg["unique_tracks"] / total_unique * 100
    agg = agg.reset_index()
    if agg.empty:
        return "No tracks have a value for that grouping." + (" " + " ".join(notes) if notes else "")

    # Keep the biggest groups (and, with a split, the biggest split values); then sort for reading.
    group_totals = agg.groupby("_g")["unique_tracks"].sum().sort_values(ascending=False, kind="stable")
    max_groups = limit
    if split_by:
        split_totals = agg.groupby("_s")["unique_tracks"].sum().sort_values(ascending=False, kind="stable")
        if len(split_totals) > MAX_SPLIT_VALUES:
            notes.append(f"{len(split_totals) - MAX_SPLIT_VALUES} of {len(split_totals)} {split_by} values with the fewest tracks are left out.")
            split_totals = split_totals.head(MAX_SPLIT_VALUES)
        agg = agg[agg["_s"].isin(split_totals.index)]
        max_groups = max(1, min(limit, MAX_LIMIT // max(1, len(split_totals))))
    if len(group_totals) > max_groups:
        notes.append(f"Showing the {max_groups} biggest of {len(group_totals)} {group_by} groups (raise limit for more).")
    keep = list(group_totals.head(max_groups).index)
    agg = agg[agg["_g"].isin(keep)]
    if group_by in _ORDERED:
        agg = agg.sort_values(["_g"] + (["_s"] if split_by else []))
    else:
        rank = {g: i for i, g in enumerate(keep)}
        agg = agg.assign(_rank=agg["_g"].map(rank)).sort_values(["_rank", "unique_tracks"], ascending=[True, False])

    def show(metric: str, v) -> str:
        if metric in ("unique_tracks", "entries"):
            return str(int(v))
        if pd.isna(v):
            return ""
        return f"{v:.1f}" if metric in ("avg_popularity", "share") else f"{v:.2f}"

    names = {"share": "share_%"}
    header = [group_by] + ([split_by] if split_by else []) + [names.get(m, m) for m in metrics]
    rows = [
        [_label(group_by, r["_g"])] + ([_label(split_by, r["_s"])] if split_by else []) + [show(m, r[m]) for m in metrics]
        for _, r in agg.iterrows()
    ]
    title = f"{', '.join(metrics)} by {group_by}" + (f" x {split_by}" if split_by else "")
    head = f"{title}. Selection: {total_unique:,} unique tracks" + (f" (filters: {'; '.join(used)})" if used else " (no filters)") + "."
    if group_by in ("genre", "genre_family") or split_by in ("genre", "genre_family"):
        notes.append("A track counts under every genre/family of its artist, so shares add up to more than 100%.")
    notes.append("unique_tracks, hours and avg_popularity count each Spotify track once per group; entries counts every playlist entry.")
    return "\n".join([head, _table(header, rows), *notes])


# ---------------------------------------------------------------- find_tracks

def find_tracks(
    conn: sqlite3.Connection,
    filters: Filters | None = None,
    sort: str = "popularity",
    ascending: bool | None = None,
    limit: int | None = None,
    include_links: bool = False,
) -> str:
    """Unique tracks that pass the filters, sorted, as a capped text listing."""
    if sort not in _SORTS:
        raise QueryError(f"Unknown sort '{sort}'. Use one of: {', '.join(_SORTS)}.")
    limit = _limit(limit)
    df, used = apply_filters(conn, _frame(conn), filters)
    if df.empty:
        return f"No tracks match the filters ({'; '.join(used) or 'none'})."
    df = df.assign(_name_key=df["track_name"].str.casefold(), _artist_key=df["artist_name"].str.casefold())
    grouped = df.groupby("track_id", sort=False)
    tracks = grouped.first()
    tracks["_playlists"] = grouped["playlist_name"].agg(lambda s: sorted(set(s)))
    tracks["_added"] = grouped["_added"].min()  # first time it was added to any matching playlist
    asc = (sort in _ASCENDING_BY_DEFAULT) if ascending is None else ascending
    col = _SORTS[sort]
    tracks = tracks.sort_values(col, ascending=asc, na_position="last", kind="stable")

    shown = tracks.head(limit)
    header = ["#", "Track", "Artist", "Album", "Year", "Duration", "Pop", "Added", "Playlists"] + (["Link"] if include_links else [])
    rows = []
    for i, (_, t) in enumerate(shown.iterrows(), start=1):
        pls = t["_playlists"]
        names = ", ".join(pls[:3]) + (f" +{len(pls) - 3}" if len(pls) > 3 else "")
        row = [i, t["track_name"], t["artist_display"], t["album_name"] or "", _int_or_blank(t["release_year"]),
               fmt_duration(t["duration_raw"] / 60000) if pd.notna(t["duration_raw"]) else "",
               _int_or_blank(t["popularity_raw"]), t["_added"].strftime("%Y-%m-%d") if pd.notna(t["_added"]) else "", names]
        if include_links:
            row.append(t["url"] or "")
        rows.append(row)
    head = (f"{len(tracks):,} unique tracks match" + (f" ({'; '.join(used)})" if used else " (no filters)")
            + f"; showing {len(shown)}, sorted by {sort} {'ascending' if asc else 'descending'}.")
    return "\n".join([head, _table(header, rows)])


# ---------------------------------------------------------------- find_duplicates

def find_duplicates(
    conn: sqlite3.Connection, playlists: list[str] | None = None, kind: str = "all", limit: int | None = None
) -> str:
    """Exact duplicates and different versions across (or within) the chosen playlists."""
    if kind not in ("all", "exact", "versions"):
        raise QueryError("kind must be 'all', 'exact' or 'versions'.")
    limit = _limit(limit)
    recs = dashboard.records(conn)
    if not recs:
        raise QueryError("Nothing loaded yet. Use list_my_playlists, ask which playlists to load, then load_playlists.")
    scope = "all loaded playlists"
    if playlists:
        names = [dashboard.find_playlist(conn, ref)["name"] for ref in playlists]
        recs = [r for r in recs if r["playlist_name"] in names]
        scope = ", ".join(names)
    out = [f"Duplicates in {scope} ({len({r['id'] for r in recs}):,} unique tracks, {len(recs):,} entries)."]

    if kind in ("all", "exact"):
        exact = comparison.exact_duplicates_across_playlists(recs)
        out.append(f"\nExact duplicates: {len(exact)} Spotify tracks saved more than once, across or inside playlists"
                   + (f" (showing {limit})." if len(exact) > limit else "."))
        exact = exact.sort_values(["count", "track_name"], ascending=[False, True]).head(limit)
        out.append(_table(["Track", "Artist", "Times", "Playlists", "Track ID"],
                          [[r.track_name, r.artist_display, r.count, r.playlists, r.track_id] for r in exact.itertuples()]))
    if kind in ("all", "versions"):
        versions = comparison.different_versions_across_playlists(recs)
        out.append(f"\nDifferent versions: {len(versions)} songs saved under several Spotify IDs (same song and primary "
                   "artist, ignoring suffixes such as \"- Remastered\", \"- Live\" or \"- Acoustic\")"
                   + (f" (showing {limit})." if len(versions) > limit else "."))
        out.append(_table(["Track", "Artist", "Versions", "Saved as", "Playlists", "Track IDs"],
                          [[r.track_name, r.artist_name, r.n_unique_ids, "; ".join(r.names), r.playlists, ",".join(r.track_ids)]
                           for r in versions.head(limit).itertuples()]))
    return "\n".join(out)


# ---------------------------------------------------------------- compare_playlists

def compare_playlists(conn: sqlite3.Connection, playlists: list[str] | None = None) -> str:
    """One row per playlist plus how many tracks each pair shares."""
    df = _frame(conn)
    if playlists:
        names = [dashboard.find_playlist(conn, ref)["name"] for ref in playlists]
        df = df[df["playlist_name"].isin(names)]
    follower_rows = {r["name"]: r["followers"] for r in conn.execute("SELECT name, followers FROM playlists")}

    counts = df.groupby("playlist_name").size()
    meta = comparison.playlist_meta(
        [{"name": n, "track_count": int(c), "followers": follower_rows.get(n)} for n, c in counts.items()]
    )
    table = comparison.size_ranking(meta).set_index("playlist_name")
    table["followers"] = table["followers"].astype("Int64")
    table["hours"] = df.groupby("playlist_name")["duration_raw"].sum() / 3_600_000
    dur = comparison.duration_by_playlist(df).set_index("playlist_name")
    table = table.join(dur).join(comparison.popularity_by_playlist(df).set_index("playlist_name"))
    table = table.join(comparison.genre_diversity(df).set_index("playlist_name"))
    era = comparison.release_era(df).set_index("playlist_name")
    era["median_year"] = df.groupby("playlist_name")["release_year"].median()
    table = table.join(era)
    age = comparison.playlist_age(df)
    table = table.join(age.set_index("playlist_name")[["first_added_str"]])
    dated = df.assign(_d=df["_added"]).dropna(subset=["_d"]).groupby("playlist_name")["_d"].max()
    table["last_added"] = dated.dt.strftime("%b %Y")

    members = {n: set(g["track_id"]) for n, g in df.groupby("playlist_name")}
    others: dict[str, set] = {n: set().union(*(m for k, m in members.items() if k != n)) for n in members}
    pairs = []
    ordered = list(table.index)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            shared = len(members[a] & members[b])
            if shared:
                pairs.append((shared, a, b))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))

    def num(v, fmt="{:.1f}"):
        return "" if pd.isna(v) else fmt.format(v)

    header = ["Playlist", "Entries", "Unique tracks", "Only here", "Followers", "Hours", "Avg min", "Shortest", "Longest",
              "Avg pop", "Genres", "Year median", "Oldest", "Newest", "First added", "Last added"]
    rows = []
    for name, r in table.iterrows():
        rows.append([
            name, int(r["total_tracks"]), len(members[name]), len(members[name] - others[name]),
            "" if pd.isna(r["followers"]) else int(r["followers"]), num(r["hours"]), num(r["avg_min"], "{:.2f}"),
            fmt_duration(r["min_min"]), fmt_duration(r["max_min"]), num(r["avg_popularity"]),
            "" if pd.isna(r["unique_genres"]) else int(r["unique_genres"]),
            num(r["median_year"], "{:.0f}"), num(r["min_year"], "{:.0f}"), num(r["max_year"], "{:.0f}"),
            r["first_added_str"] if isinstance(r["first_added_str"], str) else "", r["last_added"] if isinstance(r["last_added"], str) else "",
        ])
    out = [f"Comparison of {len(table)} playlists, largest first. Entries = tracks saved (a repeat counts twice); "
           "'Only here' = unique tracks in no other playlist of this comparison.", _table(header, rows)]
    extremes = comparison.duration_extremes(df)
    if extremes:
        out.append(f"Longest track: {extremes.longest.track_name} - {extremes.longest.artist_display} "
                   f"({fmt_duration(extremes.longest.minutes)}, {extremes.longest.playlist_name}). "
                   f"Shortest: {extremes.shortest.track_name} - {extremes.shortest.artist_display} "
                   f"({fmt_duration(extremes.shortest.minutes)}, {extremes.shortest.playlist_name}).")
    undated = comparison.playlists_without_dates(df, age)
    if undated:
        out.append("Playlists with no added dates: " + ", ".join(undated) + ".")
    if table["followers"].isna().all():
        out.append("Follower counts aren't cached yet: they are read the next time load_playlists runs.")
    out.append(f"Shared unique tracks per pair ({len(pairs)} pairs share at least one; top {min(len(pairs), 30)}):")
    out.append(_table(["Playlist A", "Playlist B", "Shared"], [[a, b, s] for s, a, b in pairs[:30]]) if pairs else "none")
    return "\n".join(out)


# ---------------------------------------------------------------- find_replaced_tracks

#: Unavailable tracks searched for alternatives per call (one or two Spotify searches each).
MAX_SEARCHED = 30


def find_replaced_tracks(
    conn: sqlite3.Connection, playlists: list[str] | None = None, limit: int | None = None, client=None
) -> str:
    """Popularity-0 tracks: the ones Spotify replaced with another version (and which one), and
    the ones not playable in the user's country, with alternatives found by search (needs client)."""
    limit = _limit(limit)
    names = None
    if playlists:
        names = [dashboard.find_playlist(conn, ref)["name"] for ref in playlists]
    rows = conn.execute(
        "SELECT t.id, t.name, t.album, t.release_date, t.isrc, t.url, t.relink_checked_at, t.current_id, "
        "t.current_popularity, t.current_album, t.current_url, t.unavailable, a.name AS artist, p.name AS playlist "
        "FROM tracks t JOIN playlist_tracks pt ON pt.track_id = t.id JOIN playlists p ON p.id = pt.playlist_id "
        "LEFT JOIN track_artists ta ON ta.track_id = t.id AND ta.position = 0 LEFT JOIN artists a ON a.id = ta.artist_id "
        "WHERE t.popularity = 0 ORDER BY a.name, t.name, p.name"
    ).fetchall()
    if not conn.execute("SELECT 1 FROM playlists LIMIT 1").fetchone():
        raise QueryError("Nothing loaded yet. Use list_my_playlists, ask which playlists to load, then load_playlists.")
    tracks: dict[str, dict] = {}
    for r in rows:
        if names is not None and r["playlist"] not in names:
            continue
        t = tracks.setdefault(r["id"], {**dict(r), "playlists": []})
        if r["playlist"] not in t["playlists"]:
            t["playlists"].append(r["playlist"])
    scope = ", ".join(names) if names else "all loaded playlists"
    replaced = [t for t in tracks.values() if t["current_id"]]
    unavailable = [t for t in tracks.values() if t["unavailable"]]
    unchecked = [t for t in tracks.values() if t["relink_checked_at"] is None]
    other = len(tracks) - len(replaced) - len(unavailable) - len(unchecked)

    out = [f"Popularity-0 tracks in {scope}: {len(tracks)}. Replaced by another version: {len(replaced)}; "
           f"not playable in your country: {len(unavailable)}; playable, simply unpopular: {other}"
           + (f"; not checked yet: {len(unchecked)} (load_playlists checks them)" if unchecked else "") + "."]
    if replaced:
        out.append(f"\nReplaced: Spotify plays the current version instead of the saved one; stats use the current "
                   f"version's popularity. Swapping the saved ID needs the write tools (a later phase)."
                   + (f" Showing {limit}." if len(replaced) > limit else ""))
        out.append(_table(
            ["Track", "Artist", "Playlists", "Saved from", "Now from", "Popularity now", "Saved link", "Current link"],
            [[t["name"], t["artist"], ", ".join(t["playlists"]), t["album"],
              t["current_album"] if t["current_album"] != t["album"] else "(same album)",
              t["current_popularity"], t["url"], t["current_url"]] for t in replaced[:limit]]))
    if unavailable:
        searched = unavailable[:MAX_SEARCHED] if client is not None else []
        out.append("\nNot playable in your country (no replacement from Spotify)"
                   + ("; alternatives found by searching Spotify (same artist and song, playable here):" if searched else ":"))
        rows_out = []
        for t in unavailable[:limit]:
            alts = _alternatives(client, t) if t in searched else []
            rows_out.append([t["name"], t["artist"], ", ".join(t["playlists"]), t["album"], t["url"],
                             " ; ".join(f"{a['name']} - {a['album']} ({a['year']}), popularity {a['popularity']}, {a['url']}"
                                        for a in alts) or ("none found" if t in searched else "")])
        out.append(_table(["Track", "Artist", "Playlists", "Album", "Saved link", "Alternatives"], rows_out))
    return "\n".join(out)


def _loose(name: str) -> str:
    """song_key without punctuation or spaces: "Viernes, 3AM" and "Viernes 3 AM" match."""
    return re.sub(r"\W+", "", song_key(name))


def _alternatives(client, t: dict, n: int = 3) -> list[dict]:
    """Playable versions of an unavailable track: same primary artist and song (version suffixes
    ignored), searched by ISRC first, then by name and artist. Most popular first."""
    queries_ = ([f"isrc:{t['isrc']}"] if t.get("isrc") else []) + [f'track:"{t["name"]}" artist:"{t["artist"]}"']
    key, artist = _loose(t["name"]), (t["artist"] or "").casefold()
    found: dict[str, dict] = {}
    for q in queries_:
        for hit in client.search_tracks(q, limit=10):
            first = ((hit.get("artists") or [{}])[0].get("name") or "").casefold()
            if hit.get("id") == t["id"] or first != artist or _loose(hit.get("name") or "") != key:
                continue
            album = hit.get("album") or {}
            found[hit["id"]] = {"name": hit.get("name"), "album": album.get("name"),
                                "year": (album.get("release_date") or "")[:4], "popularity": hit.get("popularity"),
                                "url": (hit.get("external_urls") or {}).get("spotify")}
        if found:  # keep only what plays in the user's country
            ids = list(found)
            playable = {(c.get("linked_from") or {}).get("id") or c.get("id")
                        for c in client.tracks(ids, market="from_token") if c and c.get("is_playable") is not False}
            found = {i: a for i, a in found.items() if i in playable}
        if found:
            break
    return sorted(found.values(), key=lambda a: -(a["popularity"] or 0))[:n]
