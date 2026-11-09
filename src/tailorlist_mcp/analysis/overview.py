"""Collection overview computations (no Streamlit, no session state, no plotting).

Pure functions behind the Overview page: headline KPIs, rankings (artists, albums, genres),
release-year facts, distributions (decade, popularity) and the additions timeline, plus the
"drill-down" track listings shown when a chart element is clicked.

Conventions shared by every function:
  - Input is a track DataFrame, one row per (track, playlist) pair, as returned by the app's
    collection loader. Expected columns (all optional unless a function says otherwise):
    track_id, track_name, artist_name (primary artist), artist_display, playlist_name,
    album_name, popularity_raw (Spotify 0-100), release_year, decade (e.g. 1990),
    duration_raw (milliseconds), added_at (date-like), artist_genres (comma-separated tags).
  - "Unique tracks" means unique track_id; a track saved in several playlists counts once.
  - Inputs are never mutated. Nothing reads the clock: the current year is a parameter.
  - When a needed column is missing a function returns None / an empty DataFrame, as documented
    on each function, instead of raising (except where explicitly noted).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

#: Width of the popularity histogram bins (0-5, 5-10, ...). The page's plotly histogram must use
#: the same width so that a clicked bar maps back to the right range.
POPULARITY_BIN_SIZE = 5

#: Columns shown in drill-down listings (artist drill-down omits artist_display).
_LISTING_COLUMNS = ["track_name", "artist_display", "playlist_name", "popularity_raw", "release_year"]
_LISTING_COLUMNS_NO_ARTIST = ["track_name", "playlist_name", "popularity_raw", "release_year"]
_LISTING_RENAME = {
    "track_name": "Track", "artist_display": "Artist",
    "playlist_name": "Playlist", "popularity_raw": "Popularity", "release_year": "Year",
}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CollectionKpis:
    """Headline numbers of the collection.

    Attributes:
        total_tracks: unique tracks (unique track_id).
        total_artists: distinct primary artist names over all rows.
        total_playlists: distinct playlist names.
        avg_popularity: mean Spotify popularity (0-100) of unique tracks; None when the
            popularity_raw column is missing; NaN when the column exists but has no values.
        top_decade: most frequent decade (e.g. 1990; the smallest one on ties), or None when
            no track has a decade.
        avg_duration_min: mean track length in minutes (duration_raw is milliseconds divided
            by 60000); None when the column is missing or has no values.
    """
    total_tracks: int
    total_artists: int
    total_playlists: int
    avg_popularity: float | None
    top_decade: int | None
    avg_duration_min: float | None


@dataclass(frozen=True)
class ListeningProfile:
    """Age-like statistics derived from release years.

    Attributes:
        median_year: median release year of unique tracks (truncated to int).
        est_birth: median_year - 16 (assumes the listener's taste forms at about 16).
        listening_age: current_year - est_birth, in years.
    """
    median_year: int
    est_birth: int
    listening_age: int


@dataclass(frozen=True)
class OldestNewest:
    """Oldest and newest unique tracks by release year.

    Attributes:
        oldest: the full row (pd.Series) of a track with the minimum release year.
        newest: the full row (pd.Series) of a track with the maximum release year.
        oldest_year: minimum release year.
        newest_year: maximum release year.
        span: newest_year - oldest_year, in years.
    """
    oldest: pd.Series
    newest: pd.Series
    oldest_year: int
    newest_year: int
    span: int


# ---------------------------------------------------------------------------
# Basics and KPIs
# ---------------------------------------------------------------------------

def dedupe_tracks(df: pd.DataFrame) -> pd.DataFrame:
    """One row per unique track.

    Args:
        df: tracks; must have a track_id column.
    Returns:
        A copy keeping the first row of each track_id (original index labels preserved).
    """
    return df.drop_duplicates(subset="track_id").copy()


def compute_kpis(df: pd.DataFrame) -> CollectionKpis:
    """Headline KPIs of the collection (see CollectionKpis for each field).

    Args:
        df: all (track, playlist) rows; must have track_id, artist_name and playlist_name.
            popularity_raw, decade and duration_raw are optional.
    Returns:
        CollectionKpis. Track-level fields (tracks, popularity, decade, duration) use unique
        tracks; artist and playlist counts use all rows. An empty frame gives zeros and
        NaN average popularity (if the column exists).
    """
    deduped = dedupe_tracks(df)
    avg_popularity = deduped["popularity_raw"].mean() if "popularity_raw" in deduped.columns else None
    top_decade = (
        int(deduped["decade"].mode()[0])
        if "decade" in deduped.columns and deduped["decade"].notna().any()
        else None
    )
    avg_duration_min = None
    if "duration_raw" in deduped.columns:
        raw = deduped["duration_raw"].mean()
        if pd.notna(raw):
            avg_duration_min = raw / 60000
    return CollectionKpis(
        total_tracks=len(deduped),
        total_artists=df["artist_name"].nunique(),
        total_playlists=df["playlist_name"].nunique(),
        avg_popularity=avg_popularity,
        top_decade=top_decade,
        avg_duration_min=avg_duration_min,
    )


def compute_listening_profile(deduped: pd.DataFrame, current_year: int) -> ListeningProfile | None:
    """Median era, estimated birth year and "listening age" from release years.

    Args:
        deduped: unique tracks (see dedupe_tracks); uses release_year.
        current_year: the year used as "now" (e.g. 2026); passed in so results are deterministic.
    Returns:
        ListeningProfile, or None when release_year is missing or has no values.
    """
    if "release_year" not in deduped.columns or not deduped["release_year"].notna().any():
        return None
    median_year = int(deduped["release_year"].dropna().median())
    est_birth = median_year - 16
    return ListeningProfile(median_year, est_birth, current_year - est_birth)


def find_oldest_newest(deduped: pd.DataFrame) -> OldestNewest | None:
    """Oldest and newest tracks by release year.

    Args:
        deduped: unique tracks (see dedupe_tracks); uses release_year (NaN rows ignored).
    Returns:
        OldestNewest, or None when release_year is missing or has no values. On ties the
        first matching row wins. The returned rows have release_year as read from the input
        (not cast), so callers should format it with int().
    """
    if "release_year" not in deduped.columns or not deduped["release_year"].notna().any():
        return None
    valid = deduped.dropna(subset=["release_year"]).copy()
    valid["release_year"] = valid["release_year"].astype(int)
    oldest = valid.loc[valid["release_year"].idxmin()]
    newest = valid.loc[valid["release_year"].idxmax()]
    oldest_year = int(valid["release_year"].min())
    newest_year = int(valid["release_year"].max())
    return OldestNewest(oldest, newest, oldest_year, newest_year, newest_year - oldest_year)


# ---------------------------------------------------------------------------
# Rankings
# ---------------------------------------------------------------------------

def playlist_track_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Rows per playlist.

    Args:
        df: all rows; needs playlist_name.
    Returns:
        DataFrame [playlist_name, track_count] sorted by track_count descending. Counts rows
        (not unique track_ids), so a track repeated within one playlist counts each time.
    """
    return (
        df.groupby("playlist_name")
        .size()
        .reset_index(name="track_count")
        .sort_values("track_count", ascending=False)
    )


def top_artists_by_tracks(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Primary artists ranked by number of unique tracks.

    Args:
        df: all rows; needs artist_name and track_id.
        n: how many artists to keep.
    Returns:
        DataFrame [artist_name, track_count] sorted by track_count descending, at most n rows.
    """
    return (
        df.groupby("artist_name")["track_id"]
        .nunique()
        .reset_index(name="track_count")
        .sort_values("track_count", ascending=False)
        .head(n)
    )


def min_tracks_threshold(n_unique_tracks: int) -> int:
    """Minimum unique tracks an artist needs to enter the "most popular artists" ranking.

    Args:
        n_unique_tracks: number of unique tracks in the collection.
    Returns:
        min(5, max(1, n_unique_tracks // 10)): 1 for tiny collections, growing to a cap of 5.
    """
    return min(5, max(1, n_unique_tracks // 10))


def top_popular_artists(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Artists ranked by average popularity, among artists with enough unique tracks.

    Args:
        df: all rows; needs artist_name, track_id and popularity_raw. The minimum track count
            is min_tracks_threshold(number of unique tracks in df).
        n: how many artists to keep.
    Returns:
        DataFrame [artist_name, track_count, avg_popularity] sorted by avg_popularity
        descending, at most n rows. Empty DataFrame (no columns) when popularity_raw is missing.
    """
    if "popularity_raw" not in df.columns:
        return pd.DataFrame()
    min_t = min_tracks_threshold(len(dedupe_tracks(df)))
    return (
        df.groupby("artist_name")
        .agg(track_count=("track_id", "nunique"), avg_popularity=("popularity_raw", "mean"))
        .query(f"track_count >= {min_t}")
        .sort_values("avg_popularity", ascending=False)
        .head(n)
        .reset_index()
    )


def top_albums(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    """Albums ranked by unique tracks saved, ties broken by average popularity.

    Args:
        df: all rows; needs album_name, artist_name, track_id and popularity_raw.
        n: how many albums to keep.
    Returns:
        DataFrame [album_name, artist_name, track_count, avg_popularity] sorted by track_count
        then avg_popularity, both descending, at most n rows. Empty DataFrame (no columns)
        when album_name or popularity_raw is missing.
    """
    if "album_name" not in df.columns or "popularity_raw" not in df.columns:
        return pd.DataFrame()
    return (
        df.groupby(["album_name", "artist_name"])
        .agg(track_count=("track_id", "nunique"), avg_popularity=("popularity_raw", "mean"))
        .sort_values(["track_count", "avg_popularity"], ascending=False)
        .head(n)
        .reset_index()
    )


def genre_track_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Unique tracks per genre tag, counting sub-genres.

    A track's artist_genres is a comma-separated string. For every distinct tag, the count is
    the number of unique tracks whose artist_genres CONTAINS that tag as a case-insensitive
    substring, so "rock" also counts tracks tagged "indie rock" or "argentine rock".

    Args:
        df: all rows; needs artist_genres, track_id.
    Returns:
        DataFrame [genre, track_count] sorted by track_count descending. Empty DataFrame with
        those two columns when no track has a genre.
    """
    genres_exploded = (
        df[df["artist_genres"].notna() & (df["artist_genres"] != "")]
        .assign(genre=lambda x: x["artist_genres"].str.split(","))
        .explode("genre")
        .assign(genre=lambda x: x["genre"].str.strip())
        .query("genre != ''")
    )
    rows = []
    for g in genres_exploded["genre"].unique().tolist():
        count = df[
            df["artist_genres"].notna() &
            df["artist_genres"].str.contains(g, case=False, regex=False)
        ]["track_id"].nunique()
        rows.append({"genre": g, "track_count": count})
    if not rows:
        return pd.DataFrame({"genre": pd.Series(dtype=object), "track_count": pd.Series(dtype="int64")})
    return pd.DataFrame(rows).sort_values("track_count", ascending=False)


def top_genres(genre_counts: pd.DataFrame, n: int = 30) -> pd.DataFrame:
    """Top n genres for the treemap.

    Args:
        genre_counts: output of genre_track_counts.
        n: how many genres to keep.
    Returns:
        The first n rows with a fresh 0..n-1 index.
    """
    return genre_counts.head(n).reset_index(drop=True)


def untagged_tracks_summary(deduped: pd.DataFrame) -> tuple[int, float]:
    """Unique tracks that have no genre data.

    Args:
        deduped: unique tracks (see dedupe_tracks); needs artist_genres.
    Returns:
        (count, percent): tracks whose artist_genres is NaN or blank, and that count as a
        percentage of all unique tracks rounded to 1 decimal (0.0 when count is 0).
    """
    no_genre = deduped[
        deduped["artist_genres"].isna() | (deduped["artist_genres"].str.strip() == "")
    ]
    count = len(no_genre)
    pct = round(count / len(deduped) * 100, 1) if count > 0 else 0.0
    return count, pct


# ---------------------------------------------------------------------------
# Distributions and timeline
# ---------------------------------------------------------------------------

def decade_distribution(deduped: pd.DataFrame) -> pd.DataFrame:
    """Unique tracks per decade.

    Args:
        deduped: unique tracks (see dedupe_tracks); uses decade.
    Returns:
        DataFrame [decade, count, decade_label] ordered by decade ascending, e.g.
        (1990, 12, "1990s"). Empty DataFrame (no columns) when decade is missing or has no values.
    """
    if "decade" not in deduped.columns or not deduped["decade"].notna().any():
        return pd.DataFrame()
    decade_counts = (
        deduped["decade"].dropna().astype(int)
        .value_counts().sort_index().reset_index()
    )
    decade_counts.columns = ["decade", "count"]
    decade_counts["decade_label"] = decade_counts["decade"].astype(str) + "s"
    return decade_counts


def has_popularity(deduped: pd.DataFrame) -> bool:
    """Whether at least one unique track has a popularity score (popularity section is shown)."""
    return "popularity_raw" in deduped.columns and bool(deduped["popularity_raw"].notna().any())


def popularity_bin_bounds(x_values: list[float], bin_size: int = POPULARITY_BIN_SIZE) -> tuple[float, float]:
    """Map clicked histogram bars back to a popularity range.

    Args:
        x_values: x coordinates (bin centers) of the clicked bars; must not be empty.
        bin_size: histogram bin width.
    Returns:
        (bin_min, bin_max): the left edge of the lowest clicked bin and bin_min + bin_size.
        The range is half-open: bin_min <= popularity < bin_max.
    """
    bin_min = (min(x_values) // bin_size) * bin_size
    return bin_min, bin_min + bin_size


def additions_timeline(df: pd.DataFrame) -> pd.DataFrame:
    """Tracks added per month.

    Args:
        df: all rows; uses added_at (date-like; unparseable values are ignored) and track_id.
    Returns:
        DataFrame [year_month ("YYYY-MM"), tracks_added] sorted chronologically. A track
        counts once per month even if saved in several playlists that month. Empty DataFrame
        when added_at is missing or has no values.
    """
    if "added_at" not in df.columns or not df["added_at"].notna().any():
        return pd.DataFrame()
    _df = df.copy()
    _df["added_at"] = pd.to_datetime(_df["added_at"], errors="coerce")
    return (
        _df.dropna(subset=["added_at"])
        .assign(year_month=lambda x: x["added_at"].dt.strftime("%Y-%m"))
        .drop_duplicates(subset=["track_id", "year_month"])
        .groupby("year_month")
        .size()
        .reset_index(name="tracks_added")
        .sort_values("year_month")
    )


# ---------------------------------------------------------------------------
# Drill-down listings (what the page shows after clicking a chart element)
# ---------------------------------------------------------------------------

def track_listing(tracks: pd.DataFrame, columns: list[str], strict_sort: bool = True) -> pd.DataFrame:
    """Format a set of tracks for display.

    Args:
        tracks: tracks to show.
        columns: candidate columns; only those present in tracks are kept, in the given order.
        strict_sort: True sorts by popularity_raw descending and raises KeyError if that
            column is absent; False sorts only when the column is present.
    Returns:
        DataFrame with release_year NaN replaced by 0 and cast to int, columns renamed to
        Track / Artist / Playlist / Popularity / Year, and a fresh 0..n-1 index.
    """
    out = tracks[[c for c in columns if c in tracks.columns]].copy()
    if strict_sort or "popularity_raw" in out.columns:
        out = out.sort_values("popularity_raw", ascending=False)
    if "release_year" in out.columns:
        out["release_year"] = out["release_year"].fillna(0).astype(int)
    return out.rename(columns=_LISTING_RENAME).reset_index(drop=True)


def tracks_for_artist(deduped: pd.DataFrame, artist_name: str) -> pd.DataFrame:
    """Listing of an artist's unique tracks, most popular first.

    Args:
        deduped: unique tracks; needs artist_name and popularity_raw.
        artist_name: exact primary-artist name.
    Returns:
        Listing with columns Track, Playlist, Popularity, Year (see track_listing).
    """
    return track_listing(deduped[deduped["artist_name"] == artist_name], _LISTING_COLUMNS_NO_ARTIST)


def tracks_for_decade(deduped: pd.DataFrame, decade: int) -> pd.DataFrame:
    """Listing of the unique tracks of one decade (e.g. 1990), most popular first.

    Args:
        deduped: unique tracks; needs decade and popularity_raw.
        decade: decade start year.
    Returns:
        Listing with columns Track, Artist, Playlist, Popularity, Year.
    """
    return track_listing(deduped[deduped["decade"].astype("Int64") == decade], _LISTING_COLUMNS)


def tracks_for_popularity_range(deduped: pd.DataFrame, bin_min: float, bin_max: float) -> pd.DataFrame:
    """Listing of unique tracks with bin_min <= popularity < bin_max, most popular first.

    Args:
        deduped: unique tracks; needs popularity_raw.
        bin_min: inclusive lower bound.
        bin_max: exclusive upper bound.
    Returns:
        Listing with columns Track, Artist, Playlist, Popularity, Year.
    """
    in_bin = deduped[(deduped["popularity_raw"] >= bin_min) & (deduped["popularity_raw"] < bin_max)]
    return track_listing(in_bin, _LISTING_COLUMNS)


def tracks_for_genre(deduped: pd.DataFrame, genre: str) -> pd.DataFrame:
    """Listing of unique tracks whose artist_genres contains genre (sub-genres included).

    Args:
        deduped: unique tracks; needs artist_genres and popularity_raw.
        genre: tag to look for, matched as a case-insensitive substring.
    Returns:
        Listing with columns Track, Artist, Playlist, Popularity, Year.
    """
    matching = deduped[
        deduped["artist_genres"].notna() &
        deduped["artist_genres"].str.contains(genre, case=False, regex=False)
    ]
    return track_listing(matching, _LISTING_COLUMNS)


def tracks_for_month(df: pd.DataFrame, year_month: str) -> pd.DataFrame:
    """Listing of tracks added in one month, one row per track, most popular first.

    Args:
        df: all rows; needs added_at and track_id.
        year_month: "YYYY-MM".
    Returns:
        Listing with columns Track, Artist, Playlist, Popularity, Year (Popularity and the
        sort are skipped when popularity_raw is missing).
    """
    _df_at = df.copy()
    _df_at["added_at"] = pd.to_datetime(_df_at["added_at"], errors="coerce")
    _df_at["_ym"] = _df_at["added_at"].dt.strftime("%Y-%m")
    month = _df_at[_df_at["_ym"] == year_month].drop_duplicates(subset="track_id")
    return track_listing(month, _LISTING_COLUMNS, strict_sort=False)
