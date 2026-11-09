"""Overview dashboard data, computed with the original app's Overview logic (analysis/overview.py)
and its cross-playlist duplicate detection (analysis/comparison.py).

The cache is turned into the same "raw records" the Streamlit app kept in session (one dict per
playlist entry) and the same track frame its pages used (core/tracks.py conventions), so the
numbers match what the Overview page showed. One deliberate difference: artist_name is the
primary artist only (the app's KPI said "primary artists only" but its artist_name held every
artist joined by commas). Secondary artists go to artist_display ("A feat. B").
"""
from __future__ import annotations

import datetime
import math
import sqlite3

import pandas as pd

from tailorlist_mcp.analysis import comparison
from tailorlist_mcp.analysis import overview as ov
from tailorlist_mcp.analysis.tracks import add_artist_display, parse_release_year

TOP_N = 20


def records(conn: sqlite3.Connection) -> list[dict]:
    """One dict per playlist entry, shaped like the Streamlit app's session records."""
    artists_by_track: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute(
        "SELECT ta.track_id, ta.position, a.name, a.genres FROM track_artists ta "
        "JOIN artists a ON a.id = ta.artist_id ORDER BY ta.track_id, ta.position"
    ):
        artists_by_track.setdefault(r["track_id"], []).append(r)

    out = []
    for r in conn.execute(
        "SELECT pt.playlist_id, pt.position, pt.added_at, p.name AS playlist_name, t.* "
        "FROM playlist_tracks pt JOIN playlists p ON p.id = pt.playlist_id "
        "JOIN tracks t ON t.id = pt.track_id ORDER BY p.name, pt.position"
    ):
        artists = artists_by_track.get(r["id"], [])
        primary = artists[0] if artists else None
        genres = _genres(primary["genres"]) if primary else []
        out.append({
            "id": r["id"],
            "name": r["name"],
            "artists": primary["name"] if primary else "",
            "secondary_artists": ", ".join(a["name"] for a in artists[1:]),
            "playlist_name": r["playlist_name"],
            "album": r["album"],
            "popularity": r["popularity"],
            "duration_ms": r["duration_ms"],
            "release_date": r["release_date"],
            "added_at": r["added_at"],
            # Original app: genres of the primary artist, comma-separated.
            "artist_genres": ", ".join(genres),
            "url": r["url"],
        })
    return out


def _genres(raw: str | None) -> list[str]:
    import json
    return json.loads(raw) if raw else []


_RENAMES = {"id": "track_id", "name": "track_name", "artists": "artist_name",
            "popularity": "popularity_raw", "album": "album_name", "duration_ms": "duration_raw"}


def tracks_frame(recs: list[dict]) -> pd.DataFrame:
    """The app's track frame (core/tracks.py yc_tracks_frame conventions)."""
    df = pd.DataFrame(recs).rename(columns=_RENAMES)
    if df.empty:
        return df
    df["release_year"] = parse_release_year(df["release_date"])
    df["decade"] = (df["release_year"] // 10 * 10).where(df["release_year"].notna())
    return add_artist_display(df)


# ---------------------------------------------------------------- JSON helpers

def _num(v, digits: int | None = None):
    if v is None:
        return None
    try:
        if isinstance(v, float) and math.isnan(v):
            return None
    except TypeError:
        pass
    if hasattr(v, "item"):
        v = v.item()
    if digits is not None and isinstance(v, float):
        return round(v, digits)
    return v


def _rows(df: pd.DataFrame, columns: dict[str, str], digits: int = 1) -> list[dict]:
    if df is None or df.empty:
        return []
    return [
        {out: _num(row[col], digits) if not isinstance(row[col], str) else row[col] for col, out in columns.items()}
        for _, row in df.iterrows()
    ]


def _fmt_duration(minutes: float | None) -> str | None:
    if minutes is None:
        return None
    total = int(round(minutes * 60))
    return f"{total // 60}:{total % 60:02d}"


def _track_card(row: pd.Series | None) -> dict | None:
    if row is None:
        return None
    return {"name": row["track_name"], "artist": row["artist_display"],
            "year": int(row["release_year"]), "url": row.get("url")}


# ---------------------------------------------------------------- the dashboard

def overview_data(conn: sqlite3.Connection, current_year: int | None = None) -> dict:
    current_year = current_year or datetime.date.today().year
    recs = records(conn)
    if not recs:
        return {"empty": True}
    df = tracks_frame(recs)
    deduped = ov.dedupe_tracks(df)

    kpis = ov.compute_kpis(df)
    profile = ov.compute_listening_profile(deduped, current_year)
    extremes = ov.find_oldest_newest(deduped)
    untagged_count, untagged_pct = ov.untagged_tracks_summary(deduped)

    playlists = {
        r["name"]: {"url": r["url"], "hours": r["hours"]}
        for r in conn.execute(
            "SELECT p.name, p.url, ROUND(SUM(t.duration_ms) / 3600000.0, 1) AS hours FROM playlists p "
            "JOIN playlist_tracks pt ON pt.playlist_id = p.id JOIN tracks t ON t.id = pt.track_id GROUP BY p.id"
        )
    }
    counts = ov.playlist_track_counts(df)
    playlist_rows = [
        {"name": r["playlist_name"], "tracks": int(r["track_count"]),
         "hours": playlists.get(r["playlist_name"], {}).get("hours"),
         "url": playlists.get(r["playlist_name"], {}).get("url")}
        for _, r in counts.iterrows()
    ]

    exact = comparison.exact_duplicates_across_playlists(recs)
    versions = comparison.different_versions_across_playlists(recs)
    in_several = int((df.groupby("track_id")["playlist_name"].nunique() > 1).sum())

    pop = deduped["popularity_raw"].dropna() if ov.has_popularity(deduped) else pd.Series(dtype=float)
    pop_bins = []
    if not pop.empty:
        size = ov.POPULARITY_BIN_SIZE
        binned = (pop // size * size).astype(int).value_counts().sort_index()
        pop_bins = [{"from": int(k), "to": int(k) + size, "tracks": int(v)} for k, v in binned.items()]

    return {
        "kpis": {
            "unique_tracks": kpis.total_tracks,
            "artists": kpis.total_artists,
            "playlists": kpis.total_playlists,
            "avg_popularity": _num(kpis.avg_popularity, 1),
            "top_decade": kpis.top_decade,
            "avg_duration": _fmt_duration(kpis.avg_duration_min),
            "entries": len(df),
            "hours": round(float(deduped["duration_raw"].sum()) / 3_600_000, 1),
        },
        "oldest": _track_card(extremes.oldest if extremes else None),
        "newest": _track_card(extremes.newest if extremes else None),
        "listening": None if profile is None else {
            "median_year": profile.median_year, "est_birth": profile.est_birth,
            "listening_age": profile.listening_age, "span": extremes.span if extremes else None,
        },
        "playlists": playlist_rows,
        "top_artists": _rows(ov.top_artists_by_tracks(df, TOP_N), {"artist_name": "name", "track_count": "tracks"}),
        "popular_artists": _rows(
            ov.top_popular_artists(df, 5),
            {"artist_name": "name", "track_count": "tracks", "avg_popularity": "popularity"},
        ),
        "min_tracks_for_popular": ov.min_tracks_threshold(len(deduped)),
        "top_albums": _rows(
            ov.top_albums(df, 5),
            {"album_name": "album", "artist_name": "artist", "track_count": "tracks", "avg_popularity": "popularity"},
        ),
        "genres": _rows(ov.top_genres(ov.genre_track_counts(df), 30), {"genre": "genre", "track_count": "tracks"}),
        "untagged": {"tracks": untagged_count, "percent": untagged_pct},
        "decades": [
            {"decade": int(r["decade"]), "tracks": int(r["count"])}
            for _, r in ov.decade_distribution(deduped).iterrows()
        ],
        "popularity": pop_bins,
        "timeline": _rows(ov.additions_timeline(df), {"year_month": "month", "tracks_added": "tracks"}),
        "duplicates": {
            "tracks_in_several_playlists": in_several,
            "exact": {"count": len(exact), "items": _rows(
                exact.sort_values("count", ascending=False).head(TOP_N),
                {"track_name": "track", "artist_display": "artist", "count": "times", "playlists": "playlists"},
            )},
            "versions": {"count": len(versions), "items": _rows(
                versions.head(TOP_N),
                {"track_name": "track", "artist_name": "artist", "n_unique_ids": "versions", "playlists": "playlists"},
            )},
        },
    }


def model_summary(data: dict) -> str:
    """Short text for Claude (the dashboard carries the full data)."""
    if data.get("empty"):
        return "Nothing loaded yet. Use list_my_playlists, ask which playlists to load, then load_playlists."
    k = data["kpis"]
    lines = [
        f"Collection: {k['unique_tracks']:,} unique tracks "
        f"({k['entries']:,} playlist entries), {k['artists']:,} primary artists, {k['playlists']} playlists, "
        f"{k['hours']} h. Top decade {k['top_decade']}s, avg popularity {k['avg_popularity']}, "
        f"avg duration {k['avg_duration']}.",
    ]
    if data.get("listening"):
        li = data["listening"]
        lines.append(f"Median release year {li['median_year']}, span {li['span']} years.")
    lines.append("Top artists: " + ", ".join(f"{a['name']} ({a['tracks']})" for a in data["top_artists"][:8]) + ".")
    lines.append("Top genres (sub-genres counted, e.g. 'rock' includes 'indie rock'): "
                 + ", ".join(f"{g['genre']} ({g['tracks']})" for g in data["genres"][:8])
                 + f". {data['untagged']['tracks']} tracks ({data['untagged']['percent']}%) have no genre.")
    lines.append("Decades: " + ", ".join(f"{d['decade']}s {d['tracks']}" for d in data["decades"]) + ".")
    d = data["duplicates"]
    lines.append(
        f"Duplicates: {d['tracks_in_several_playlists']} tracks are in more than one playlist; "
        f"{d['exact']['count']} Spotify IDs saved more than once; {d['versions']['count']} songs saved "
        "as different versions (same name and artist, different IDs)."
    )
    lines.append("Don't repeat these numbers as a long text: they are in the dashboard. "
                 "Add a short comment and suggest next steps.")
    return "\n".join(lines)


# ---------------------------------------------------------------- one playlist's tracks

class PlaylistNotFound(LookupError):
    pass


def _match_key(name: str) -> str:
    """Casefolded name with flag emoji turned into their letters (🇨🇰7 -> ck7)."""
    out = []
    for ch in name:
        code = ord(ch)
        out.append(chr(code - 0x1F1E6 + ord("a")) if 0x1F1E6 <= code <= 0x1F1FF else ch)
    return "".join(out).casefold().strip()


def find_playlist(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    """A cached playlist by ID, Spotify link, exact name or a unique part of its name."""
    from tailorlist_mcp.collection import playlist_id_from

    rows = conn.execute("SELECT id, name, url FROM playlists ORDER BY name").fetchall()
    pid = playlist_id_from(ref)
    for r in rows:
        if r["id"] == pid:
            return r
    wanted = _match_key(ref)
    exact = [r for r in rows if _match_key(r["name"]) == wanted]
    if exact:
        return exact[0]
    partial = [r for r in rows if wanted and wanted in _match_key(r["name"])]
    if len(partial) == 1:
        return partial[0]
    loaded = ", ".join(r["name"] for r in rows) or "none"
    if partial:
        raise PlaylistNotFound(f"'{ref}' matches several loaded playlists: {', '.join(r['name'] for r in partial)}.")
    raise PlaylistNotFound(f"'{ref}' isn't loaded. Loaded playlists: {loaded}. Load it first with load_playlists.")


def playlist_track_table(conn: sqlite3.Connection, ref: str, include_links: bool = False) -> str:
    """The original app's "All Tracks" table (Track, Artist, Year, Duration, Popularity) as text,
    in playlist order, plus album and date added."""
    pl = find_playlist(conn, ref)
    recs = [r for r in records(conn) if r["playlist_name"] == pl["name"]]
    # records() collapses nothing: same playlist rows, in playlist order.
    df = tracks_frame(recs)
    lines = [f"{pl['name']}: {len(df)} tracks. {pl['url'] or ''}".rstrip(),
             "# | Track | Artist | Album | Year | Duration | Popularity | Added"
             + (" | Link" if include_links else "")]
    for i, row in enumerate(df.itertuples(index=False), start=1):
        year = int(row.release_year) if row.release_year == row.release_year and row.release_year else ""
        dur = _fmt_duration(row.duration_raw / 60000) if row.duration_raw == row.duration_raw else ""
        pop = int(row.popularity_raw) if row.popularity_raw == row.popularity_raw else ""
        added = row.added_at[:10] if isinstance(row.added_at, str) else ""
        cells = [str(i), row.track_name, row.artist_display, row.album_name or "", str(year), dur, str(pop), added]
        if include_links:
            cells.append(row.url or "")
        lines.append(" | ".join(cells))
    return "\n".join(lines)
