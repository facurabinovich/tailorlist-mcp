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

from tailorlist_mcp import store
from tailorlist_mcp.analysis import comparison
from tailorlist_mcp.analysis import duplicates as dup
from tailorlist_mcp.analysis import overview as ov
from tailorlist_mcp.analysis import playlist_detail as pd_core
from tailorlist_mcp.analysis.formatting import fmt_duration
from tailorlist_mcp.analysis.tracks import add_artist_display, parse_release_year

TOP_N = 20


def records(conn: sqlite3.Connection) -> list[dict]:
    """One dict per playlist entry, shaped like the Streamlit app's session records."""
    artists_by_track: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute(
        "SELECT ta.track_id, ta.position, a.name, a.genres, a.mb_genres FROM track_artists ta "
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
        # Spotify's genres, else MusicBrainz's (only looked up for artists Spotify left empty).
        genres = (_genres(primary["genres"]) or _genres(primary["mb_genres"])) if primary else []
        out.append({
            "id": r["id"],
            "name": r["name"],
            "artists": primary["name"] if primary else "",
            "secondary_artists": ", ".join(a["name"] for a in artists[1:]),
            "playlist_name": r["playlist_name"],
            "album": r["album"],
            # Option A (2026-11-10): a replaced album version (popularity 0) counts with the
            # popularity of the version Spotify serves instead.
            "popularity": r["current_popularity"]
            if r["popularity"] == 0 and r["current_id"] and r["current_popularity"] is not None else r["popularity"],
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
        "musicbrainz": _musicbrainz_info(conn),
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


def _musicbrainz_info(conn: sqlite3.Connection) -> dict:
    """How many primary artists Spotify left without genres, and how many MusicBrainz filled in."""
    import time
    from tailorlist_mcp import genre_fill
    c = store.mb_counts(conn, int(time.time()), genre_fill.RETRY_AFTER)
    return {"without_spotify_genres": c["total"], "filled": c["found"], "pending": c["pending"]}


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
    mbz = data.get("musicbrainz") or {}
    if mbz.get("filled"):
        lines.append(f"{mbz['filled']} artists got their genres from MusicBrainz (Spotify had none).")
    if mbz.get("pending"):
        lines.append(f"{mbz['pending']} artists without Spotify genres haven't been looked up: fill_genres can fill them "
                     "from MusicBrainz in the background (it takes a few minutes per hundred artists).")
    lines.append("Decades: " + ", ".join(f"{d['decade']}s {d['tracks']}" for d in data["decades"]) + ".")
    d = data["duplicates"]
    lines.append(
        f"Duplicates: {d['tracks_in_several_playlists']} tracks are in more than one playlist; "
        f"{d['exact']['count']} Spotify IDs saved more than once; {d['versions']['count']} songs saved "
        "as different versions (same song and artist, different IDs)."
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


# ---------------------------------------------------------------- one playlist (show_playlist)

TOP_ARTISTS_N = 5
TOP_GENRES_N = 15
DUPLICATES_SHOWN = 20
DECADE_TRACKS_SHOWN = 30
TRACKS_IN_RESULT = 50    # rows of the track table that travel in the tool result (the web page gets them all)
DECADE_TRACKS_IN_RESULT = 10


def _hl_card(row: pd.Series | None, value: str) -> dict | None:
    if row is None:
        return None
    return {"name": row["track_name"], "artist": row["artist_display"], "value": value, "url": row.get("url")}


def playlist_data(conn: sqlite3.Connection, ref: str, track_limit: int | None = None,
                  decade_limit: int = DECADE_TRACKS_SHOWN) -> dict:
    """Everything the Playlist Detail page showed for one playlist (without its audio sections),
    computed with the ported analysis/playlist_detail.py. track_limit caps the "all tracks" table and
    decade_limit the rows listed per decade (smaller for the tool result, full for the web page)."""
    pl = find_playlist(conn, ref)
    followers_row = conn.execute("SELECT followers FROM playlists WHERE id = ?", (pl["id"],)).fetchone()
    followers = followers_row["followers"] if followers_row else None
    recs = [r for r in records(conn) if r["playlist_name"] == pl["name"]]
    if not recs:
        return {"empty": True, "name": pl["name"], "url": pl["url"]}
    df = tracks_frame(recs)
    pl_df = pd_core.playlist_tracks(df, pl["name"])          # duration in minutes, as the original page
    # The original assumes these columns have values; a playlist with none of them would raise.
    if not pl_df["added_at"].notna().any():
        pl_df = pl_df.drop(columns="added_at")
    for col in ("popularity_raw", "duration_raw"):
        if not pl_df[col].notna().any():
            pl_df = pl_df.drop(columns=col)

    kpis = pd_core.header_kpis(pl_df, followers or 0)
    hl = pd_core.track_highlights(pl_df)
    avgs = pd_core.playlist_averages(pl_df)
    top = pd_core.top_artists(pl_df, TOP_ARTISTS_N)

    def card(row, fmt):
        return None if row is None else _hl_card(row, fmt(row))

    raw = dup.drop_missing_ids(dup.raw_records_frame(recs))
    exact = dup.exact_duplicates(raw, "artist_name")
    versions = dup.different_versions(raw)

    decades = pd_core.decade_counts(pl_df)
    years = pd_core.year_counts(pl_df)
    decade_tracks = {}
    for d in decades["decade"].tolist() if not decades.empty else []:
        listing = pd_core.decade_track_listing(pl_df, int(d)).reset_index(drop=True)
        decade_tracks[str(int(d))] = {
            "total": len(listing),
            "items": _rows(listing.head(decade_limit),
                           {c: o for c, o in {"Track": "track", "Artist": "artist", "Year": "year",
                                              "Popularity": "popularity"}.items() if c in listing.columns}, 0),
        }

    genres = pd_core.top_genres(pl_df, TOP_GENRES_N).sort_values("count", ascending=False)
    monthly = pd_core.monthly_additions(pl_df)
    growth: dict = {"single_session": None, "timeline": [], "peak": None, "months": len(monthly)}
    if len(monthly) <= 1:
        growth["single_session"] = pd_core.single_session_date(pl_df)
    else:
        timeline = pd_core.growth_timeline(monthly)
        peak = pd_core.peak_month(timeline)
        growth["timeline"] = [{"month": r["period"], "tracks": int(r["count"])} for _, r in timeline.iterrows()]
        growth["peak"] = {"label": peak.label, "tracks": peak.count}

    pop_complete = "popularity_raw" in pl_df.columns and bool(pl_df["popularity_raw"].notna().all())
    table = pd_core.track_table(pl_df if pop_complete else pl_df.drop(columns=["popularity_raw"], errors="ignore"))
    tracks = []
    for i, (idx, row) in enumerate(table.iterrows(), start=1):
        src = pl_df.loc[idx]
        added = src["added_at"][:10] if "added_at" in pl_df.columns and isinstance(src["added_at"], str) else ""
        if "Popularity" in table.columns:
            popularity = int(row["Popularity"])
        else:
            popularity = int(src["popularity_raw"]) if "popularity_raw" in pl_df.columns and pd.notna(src["popularity_raw"]) else None
        tracks.append({
            "n": i, "track": row["Track"], "artist": row["Artist"], "album": src["album_name"] or "",
            "year": int(row["Year"]) or None, "duration": row.get("Duration"), "popularity": popularity,
            "added": added, "url": src["url"],
        })
    total = len(tracks)
    if track_limit is not None:
        tracks = tracks[:track_limit]

    return {
        "name": pl["name"], "url": pl["url"],
        "kpis": {
            "tracks": kpis.total_tracks, "artists": kpis.unique_artists, "followers": followers,
            "first_added": kpis.first_added, "last_added": kpis.last_added,
            "hours": round(float(pl_df["duration_raw"].sum()) / 60, 1) if "duration_raw" in pl_df.columns else None,
        },
        "highlights": {
            "longest": card(hl.longest, lambda r: fmt_duration(r["duration_raw"])),
            "shortest": card(hl.shortest, lambda r: fmt_duration(r["duration_raw"])),
            "most_popular": card(hl.most_popular, lambda r: f"{int(r['popularity_raw'])} pts"),
            "least_popular": card(hl.least_popular, lambda r: f"{int(r['popularity_raw'])} pts"),
            "oldest": card(hl.oldest, lambda r: str(int(r["release_year"]))),
            "newest": card(hl.newest, lambda r: str(int(r["release_year"]))),
        },
        "averages": {
            "duration": _fmt_duration(avgs.avg_duration) if avgs.avg_duration is not None else None,
            "popularity": _num(avgs.avg_popularity, 1),
            "release_year": None if avgs.avg_release_year is None or pd.isna(avgs.avg_release_year) else round(avgs.avg_release_year),
            "top_artist": avgs.top_artist, "top_artist_tracks": avgs.top_artist_count,
        },
        "top_artists": {"items": _rows(top.table, {"artist": "name", "tracks": "tracks"}, 0),
                        "covered_pct": round(top.covered_pct), "n": TOP_ARTISTS_N},
        "duplicates": {
            "exact": {"count": len(exact), "items": _rows(
                exact.head(DUPLICATES_SHOWN), {"track_name": "track", "artist": "artist", "count": "times"}, 0)},
            "versions": {"count": len(versions), "items": _rows(
                versions.head(DUPLICATES_SHOWN), {"track_name": "track", "artist_name": "artist", "n_unique_ids": "versions"}, 0)},
        },
        "decades": [{"decade": int(r["decade"]), "tracks": int(r["count"])} for _, r in decades.iterrows()],
        "years": [{"year": int(r["year"]), "tracks": int(r["count"])} for _, r in years.iterrows()],
        "decade_tracks": decade_tracks,
        "genres": _rows(genres, {"genre": "genre", "count": "tracks"}, 0),
        "growth": growth,
        "tracks": tracks, "tracks_total": total,
    }


def playlist_summary(data: dict) -> str:
    """Short text for Claude (the dashboard carries the full data)."""
    if data.get("empty"):
        return f"{data['name']} has no tracks in the cache."
    k, a = data["kpis"], data["averages"]
    lines = [
        f"{data['name']}: {k['tracks']} tracks, {k['artists']} primary artists"
        + (f", {k['hours']} h" if k["hours"] is not None else "")
        + (f", {k['followers']} followers" if k["followers"] is not None else "")
        + f". Added {k['first_added']} to {k['last_added']}.",
        f"Average popularity {a['popularity']}, average release year {a['release_year']}, average duration {a['duration']}. "
        f"Top artist {a['top_artist']} ({a['top_artist_tracks']} tracks); top 5 cover {data['top_artists']['covered_pct']}% of the playlist.",
    ]
    if data["decades"]:
        lines.append("Decades: " + ", ".join(f"{d['decade']}s {d['tracks']}" for d in data["decades"]) + ".")
    if data["genres"]:
        lines.append("Top genres (exact tags, per entry): " + ", ".join(f"{g['genre']} ({g['tracks']})" for g in data["genres"][:6]) + ".")
    g = data["growth"]
    if g["peak"]:
        lines.append(f"Grew over {g['months']} months, peak {g['peak']['label']} ({g['peak']['tracks']} tracks added).")
    elif g["single_session"]:
        lines.append(f"Built in a single session on {g['single_session']}.")
    d = data["duplicates"]
    lines.append(f"Inside this playlist: {d['exact']['count']} Spotify tracks saved more than once; "
                 f"{d['versions']['count']} songs saved as different versions.")
    lines.append("Don't repeat these numbers as a long text. Add a short comment and suggest next steps "
                 "(find_tracks, collection_stats, find_duplicates, compare_playlists).")
    return "\n".join(lines)
