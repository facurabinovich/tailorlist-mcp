"""Single-playlist analysis behind the Playlist Detail page (no Streamlit, no session state, no plotting).

Pure functions that turn the collection DataFrame into everything the page shows for one
selected playlist: header KPIs, track highlights, averages, top artists, era breakdown, top
genres, growth over time and the full track table. Duplicates and versions live in
analysis.duplicates. Ported from the Streamlit app's core/playlist_detail.py without the audio
sections (audio averages, radar, mood map, similar playlists: Phase 4).

Conventions shared by every function:
  - The collection frame (df) has one row per (track, playlist) pair. Expected columns (all
    optional unless a function says otherwise): track_id, track_name, artist_name (primary
    artist), artist_display, playlist_name, popularity_raw (0-100), release_year, decade,
    duration_raw, added_at (date-like), artist_genres (comma-separated tags).
  - The playlist frame (pl_df) is the rows of one playlist as returned by playlist_tracks,
    where duration_raw is in MINUTES (the collection holds milliseconds).
  - Inputs are never mutated. Nothing reads the clock or the session.
  - Missing columns give None / an empty DataFrame as documented on each function.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import pandas as pd

from tailorlist_mcp.analysis.formatting import fmt_duration

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HeaderKpis:
    """Headline numbers of one playlist.

    Attributes:
        total_tracks: rows of the playlist (a track saved twice counts twice).
        unique_artists: distinct primary artist names.
        followers: Spotify followers of the playlist (0 when unknown).
        first_added: earliest added_at as "Mon YYYY", or "N/A" without an added_at column.
        last_added: latest added_at as "Mon DD, YYYY", or "N/A" without an added_at column.
    """
    total_tracks: int
    unique_artists: int
    followers: int
    first_added: str
    last_added: str


@dataclass(frozen=True)
class TrackHighlights:
    """Standout tracks of a playlist; each is a full row (pd.Series) or None when not computable.

    Attributes:
        longest, shortest: by duration_raw (None without that column).
        most_popular, least_popular: by popularity_raw (None without that column).
        oldest, newest: by release_year ignoring missing years (None when there is no year).
        On ties the first row wins.
    """
    longest: pd.Series | None
    shortest: pd.Series | None
    most_popular: pd.Series | None
    least_popular: pd.Series | None
    oldest: pd.Series | None
    newest: pd.Series | None


@dataclass(frozen=True)
class PlaylistAverages:
    """Averages and the most frequent artist of a playlist.

    Attributes:
        avg_duration: mean duration_raw (minutes for a playlist frame); None without the column.
        avg_popularity: mean popularity_raw; None without the column.
        avg_release_year: mean release_year ignoring missing; None without the column (NaN
            when the column has no values).
        top_artist: primary artist with the most rows (first one on ties).
        top_artist_count: number of rows of top_artist.
    """
    avg_duration: float | None
    avg_popularity: float | None
    avg_release_year: float | None
    top_artist: str
    top_artist_count: int


@dataclass(frozen=True)
class TopArtists:
    """The most represented artists of a playlist.

    Attributes:
        table: DataFrame [artist, tracks] by tracks descending, at most n rows.
        covered_pct: percent (0-100) of the playlist's rows covered by those artists.
    """
    table: pd.DataFrame
    covered_pct: float






@dataclass(frozen=True)
class PeakMonth:
    """The month with the most additions.

    Attributes:
        month: first day of the month.
        label: "Mon YYYY".
        count: tracks added that month.
    """
    month: pd.Timestamp
    label: str
    count: int


# ---------------------------------------------------------------------------
# Collection-level derivations
# ---------------------------------------------------------------------------



def playlist_meta(df: pd.DataFrame, uc_playlists: list[dict]) -> pd.DataFrame:
    """Rows and followers per playlist.

    Args:
        df: all rows; needs playlist_name.
        uc_playlists: playlist records from the user's Spotify import, dicts with "name" and
            optionally "followers"; the first record with a matching name is used.
    Returns:
        DataFrame [playlist_name, total_tracks, followers]; followers is 0 for playlists not
        in uc_playlists or without a followers key.
    """
    meta = df.groupby("playlist_name").size().reset_index(name="total_tracks")
    meta["followers"] = meta["playlist_name"].apply(
        lambda name: next((p.get("followers", 0) for p in uc_playlists if p["name"] == name), 0)
    )
    return meta




def playlist_tracks(df: pd.DataFrame, playlist: str) -> pd.DataFrame:
    """Rows of one playlist with duration converted to minutes.

    Args:
        df: all rows; needs playlist_name. duration_raw, when present, is milliseconds.
        playlist: exact playlist name.
    Returns:
        A copy of the playlist's rows (original index labels kept) where duration_raw is
        divided by 60000 (minutes), if the column exists.
    """
    pl_df = df[df["playlist_name"] == playlist].copy()
    if "duration_raw" in pl_df.columns:
        pl_df["duration_raw"] = pl_df["duration_raw"] / 60000
    return pl_df








# ---------------------------------------------------------------------------
# Header, highlights, averages, top artists
# ---------------------------------------------------------------------------

def header_kpis(pl_df: pd.DataFrame, followers: int) -> HeaderKpis:
    """Header numbers of a playlist (see HeaderKpis).

    Args:
        pl_df: the playlist's rows; needs artist_name; added_at is optional.
        followers: follower count to report (from playlist_meta).
    Returns:
        HeaderKpis. first_added / last_added use the min / max of added_at as given (string
        or datetime), so ISO-like strings order correctly.
    """
    if "added_at" in pl_df.columns:
        first_added = pd.to_datetime(pl_df["added_at"].min()).strftime("%b %Y")
        last_added = pd.to_datetime(pl_df["added_at"].max()).strftime("%b %d, %Y")
    else:
        first_added = "N/A"
        last_added = "N/A"
    return HeaderKpis(len(pl_df), pl_df["artist_name"].nunique(), followers, first_added, last_added)


def track_highlights(pl_df: pd.DataFrame) -> TrackHighlights:
    """Longest/shortest, most/least popular and oldest/newest tracks of a playlist.

    Args:
        pl_df: the playlist's rows; duration_raw, popularity_raw and release_year are optional
            (each missing column leaves its pair as None). A present column with no values at
            all raises (idxmax of all-NaN), except release_year which is None.
    Returns:
        TrackHighlights.
    """
    longest = shortest = most_pop = least_pop = oldest = newest = None
    if "duration_raw" in pl_df.columns:
        longest = pl_df.loc[pl_df["duration_raw"].idxmax()]
        shortest = pl_df.loc[pl_df["duration_raw"].idxmin()]
    if "popularity_raw" in pl_df.columns:
        most_pop = pl_df.loc[pl_df["popularity_raw"].idxmax()]
        least_pop = pl_df.loc[pl_df["popularity_raw"].idxmin()]
    if "release_year" in pl_df.columns:
        years = pl_df["release_year"].dropna()
        if not years.empty:
            oldest = pl_df.loc[years.idxmin()]
            newest = pl_df.loc[years.idxmax()]
    return TrackHighlights(longest, shortest, most_pop, least_pop, oldest, newest)


def playlist_averages(pl_df: pd.DataFrame) -> PlaylistAverages:
    """Average duration, popularity and release year, plus the top artist (see PlaylistAverages).

    Args:
        pl_df: the playlist's rows (not empty); needs artist_name.
    Returns:
        PlaylistAverages. Raises IndexError for an empty playlist.
    """
    counts = pl_df["artist_name"].value_counts()
    return PlaylistAverages(
        avg_duration=pl_df["duration_raw"].mean() if "duration_raw" in pl_df.columns else None,
        avg_popularity=pl_df["popularity_raw"].mean() if "popularity_raw" in pl_df.columns else None,
        avg_release_year=pl_df["release_year"].dropna().mean() if "release_year" in pl_df.columns else None,
        top_artist=counts.index[0],
        top_artist_count=int(counts.iloc[0]),
    )


def top_artists(pl_df: pd.DataFrame, n: int = 5) -> TopArtists:
    """The n artists with the most rows in the playlist.

    Args:
        pl_df: the playlist's rows (not empty); needs artist_name.
        n: how many artists to keep.
    Returns:
        TopArtists. Ties keep first-appearance order.
    """
    table = pl_df["artist_name"].value_counts().head(n).reset_index()
    table.columns = ["artist", "tracks"]
    return TopArtists(table, table["tracks"].sum() / len(pl_df) * 100)


# ---------------------------------------------------------------------------
# Era breakdown, genres
# ---------------------------------------------------------------------------

def decade_counts(pl_df: pd.DataFrame) -> pd.DataFrame:
    """Rows per decade of a playlist.

    Args:
        pl_df: the playlist's rows; uses decade (missing values ignored).
    Returns:
        DataFrame [decade (int), count, label ("1990s")] ordered by decade ascending. Empty
        DataFrame (no columns) when decade is missing.
    """
    if "decade" not in pl_df.columns:
        return pd.DataFrame()
    counts = pl_df["decade"].dropna().astype(int).value_counts().sort_index().reset_index()
    counts.columns = ["decade", "count"]
    counts["label"] = counts["decade"].astype(str) + "s"
    return counts


def year_counts(pl_df: pd.DataFrame) -> pd.DataFrame:
    """Rows per release year of a playlist.

    Args:
        pl_df: the playlist's rows; uses release_year (missing values ignored).
    Returns:
        DataFrame [year (int), count] ordered by year ascending. Empty DataFrame (no columns)
        when release_year is missing.
    """
    if "release_year" not in pl_df.columns:
        return pd.DataFrame()
    counts = pl_df["release_year"].dropna().astype(int).value_counts().sort_index().reset_index()
    counts.columns = ["year", "count"]
    return counts


def decade_track_listing(pl_df: pd.DataFrame, decade: int) -> pd.DataFrame:
    """Tracks of one decade of a playlist, for the drill-down table.

    Args:
        pl_df: the playlist's rows; needs decade and track_name/artist_display/release_year
            (those present are shown); popularity_raw is optional.
        decade: decade start year, e.g. 1990.
    Returns:
        DataFrame [Track, Artist, Year, Popularity] (columns present only), by popularity
        descending when popularity_raw exists, with Year NaN shown as 0 (Int64). Index is
        kept from pl_df (reset it before display).
    """
    in_decade = pl_df[pl_df["decade"].astype("Int64") == decade]
    if "popularity_raw" in pl_df.columns:
        cols = [c for c in ["track_name", "artist_display", "release_year", "popularity_raw"] if c in pl_df.columns]
        tracks = in_decade[cols].copy().sort_values("popularity_raw", ascending=False)
    else:
        cols = [c for c in ["track_name", "artist_display", "release_year"] if c in pl_df.columns]
        tracks = in_decade[cols].copy()
    if "release_year" in tracks.columns:
        tracks["release_year"] = tracks["release_year"].fillna(0).astype("Int64")
    return tracks.rename(columns={
        "track_name": "Track", "artist_display": "Artist",
        "release_year": "Year", "popularity_raw": "Popularity",
    })


def top_genres(pl_df: pd.DataFrame, n: int = 15) -> pd.DataFrame:
    """Most frequent genre tags of a playlist's rows.

    Each row's artist_genres is split on ", " and every tag is counted once per row (unlike
    core.overview.genre_track_counts, there is no sub-genre substring matching and no
    unique-track counting).

    Args:
        pl_df: the playlist's rows; needs artist_genres. Blank tags and missing values are ignored.
        n: how many genres to keep.
    Returns:
        DataFrame [genre, count] of the n most frequent genres sorted by count ASCENDING (so
        a horizontal chart shows the largest on top). Empty DataFrame (no columns) when
        artist_genres is missing.
    """
    if "artist_genres" not in pl_df.columns:
        return pd.DataFrame()
    tags = pl_df["artist_genres"].dropna().str.split(", ").explode().str.strip()
    tags = tags[tags != ""]
    top = tags.value_counts().head(n).reset_index()
    top.columns = ["genre", "count"]
    return top.sort_values("count")






# ---------------------------------------------------------------------------
# Growth over time
# ---------------------------------------------------------------------------

def monthly_additions(pl_df: pd.DataFrame) -> pd.DataFrame:
    """Rows added per calendar month.

    Args:
        pl_df: the playlist's rows; uses added_at (date-like). Missing dates form a "NaT" period.
    Returns:
        DataFrame [period ("YYYY-MM"), count] ordered by period. Empty DataFrame (no columns)
        when added_at is missing.
    """
    if "added_at" not in pl_df.columns:
        return pd.DataFrame()
    with warnings.catch_warnings():
        # Spotify's added_at ends in "Z" (UTC); months are taken in UTC, so the warning is noise.
        warnings.filterwarnings("ignore", message="Converting to PeriodArray")
        monthly = (
            pl_df.assign(_month=pd.to_datetime(pl_df["added_at"]).dt.to_period("M").astype(str))
            .groupby("_month")
            .size()
            .reset_index(name="count")
        )
    monthly.columns = ["period", "count"]
    return monthly


def growth_timeline(monthly: pd.DataFrame) -> pd.DataFrame:
    """Add a datetime "month" column (first day of the month) to monthly_additions output.

    Args:
        monthly: DataFrame [period "YYYY-MM", count] with at least one row.
    Returns:
        A copy with columns [period, count, month].
    """
    out = monthly.copy()
    out.columns = ["period", "count"]
    out["month"] = pd.to_datetime(out["period"], format="%Y-%m")
    return out


def peak_month(timeline: pd.DataFrame) -> PeakMonth:
    """The month with the most additions (first one on ties).

    Args:
        timeline: output of growth_timeline (not empty).
    Returns:
        PeakMonth.
    """
    idx = timeline["count"].idxmax()
    month = timeline.loc[idx, "month"]
    return PeakMonth(month, month.strftime("%b %Y"), int(timeline.loc[idx, "count"]))


def single_session_date(pl_df: pd.DataFrame) -> str | None:
    """Date of the first non-missing added_at, for the "built in a single session" card.

    Args:
        pl_df: the playlist's rows.
    Returns:
        The date as "Month DD, YYYY", or None when added_at is missing or has no values.
    """
    if "added_at" in pl_df.columns and pl_df["added_at"].notna().any():
        return pd.to_datetime(pl_df["added_at"].dropna().iloc[0]).strftime("%B %d, %Y")
    return None








# ---------------------------------------------------------------------------
# Full track table
# ---------------------------------------------------------------------------

def track_table(pl_df: pd.DataFrame) -> pd.DataFrame:
    """The "All Tracks" table of a playlist, formatted for display.

    Args:
        pl_df: the playlist's rows (duration_raw in minutes); needs track_name and
            artist_display. Optional columns are shown when present: release_year,
            duration_raw, popularity_raw, energy, valence, danceability, acousticness.
    Returns:
        DataFrame with columns Track, Artist, Year, Duration ("m:ss"), Popularity (int)
        (those present, in that order); missing
        release_year shown as 0. Original index kept (reset it before display). A missing
        popularity_raw value raises (cannot cast to int).
    """
    cols = ["track_name", "artist_display"]
    cols += [c for c in ["release_year", "duration_raw", "popularity_raw"] if c in pl_df.columns]
    table = pl_df[cols].copy()
    if "duration_raw" in table.columns:
        table["duration_raw"] = table["duration_raw"].apply(fmt_duration)
    if "release_year" in table.columns:
        table["release_year"] = table["release_year"].fillna(0).astype("Int64")
    if "popularity_raw" in table.columns:
        table["popularity_raw"] = table["popularity_raw"].astype(int)
    return table.rename(columns={
        "track_name": "Track", "artist_display": "Artist", "release_year": "Year",
        "duration_raw": "Duration", "popularity_raw": "Popularity",
    })
