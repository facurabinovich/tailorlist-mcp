"""Cross-playlist comparison behind the Playlist Comparison page (no Streamlit, no session state, no plotting).

Pure functions that compare the playlists of a collection against each other: size and followers
rankings, duplicated tracks across playlists, longest/shortest tracks, popularity, audio-feature
leaders, genre diversity, release era, decade mix and playlist age. Per-playlist audio means
come from core.playlist_detail.playlist_audio_averages and the duplicate / version detection
from core.duplicates.

Conventions shared by every function:
  - The collection frame (df) has one row per (track, playlist) pair. Expected columns (all
    optional unless a function says otherwise): track_id, track_name, artist_display,
    playlist_name, popularity_raw (0-100), release_year, decade, duration_raw (MILLISECONDS),
    added_at (date-like), artist_genres (comma-separated tags) and the audio features.
  - Playlist metadata (meta) is the frame returned by playlist_meta: playlist_name,
    total_tracks, followers.
  - Raw records are the dicts of the import (id, name, artists, playlist_name ...), with the
    tracks that had no audio features included, so duplicates use real Spotify IDs.
  - Inputs are never mutated. Nothing reads the clock or the session.
  - Functions that need a column return None (or an empty frame, as documented) without it.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tailorlist_mcp.analysis import duplicates

#: Audio features that get a "which playlist tops it" leader, in display order.
LEADER_FEATURES = ["energy", "acousticness", "valence", "danceability",
                   "speechiness", "instrumentalness", "liveness"]

MS_PER_MINUTE = 60000


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TrackExtreme:
    """One track singled out for its duration.

    Attributes:
        track_name: title of the track.
        artist_display: artist text ("Primary feat. Secondary").
        playlist_name: playlist the row belongs to.
        minutes: duration in decimal minutes.
    """
    track_name: str
    artist_display: str
    playlist_name: str
    minutes: float


@dataclass(frozen=True)
class DurationExtremes:
    """Longest and shortest track of the whole collection.

    Attributes:
        longest: the row with the largest duration.
        shortest: the row with the smallest duration.
    """
    longest: TrackExtreme
    shortest: TrackExtreme


@dataclass(frozen=True)
class FeatureLeader:
    """The playlist with the highest average of one audio feature.

    Attributes:
        feature: audio feature column name (e.g. "energy").
        playlist_name: playlist with the highest mean.
        value: that mean.
    """
    feature: str
    playlist_name: str
    value: float


# ---------------------------------------------------------------------------
# Playlist metadata, size and followers
# ---------------------------------------------------------------------------

def playlist_meta(uc_playlists: list[dict]) -> pd.DataFrame:
    """Playlist metadata as a frame with canonical column names.

    Args:
        uc_playlists: playlist records of the import, dicts with name, track_count (Spotify's
            own track count), followers and any other keys (kept as extra columns).
    Returns:
        DataFrame with name/track_count renamed to playlist_name/total_tracks, one row per
        record in input order. With no records, an empty DataFrame without columns.
    """
    return pd.DataFrame(uc_playlists).rename(columns={"name": "playlist_name", "track_count": "total_tracks"})


def size_ranking(meta: pd.DataFrame) -> pd.DataFrame:
    """Playlists ordered by Spotify's track count, largest first.

    Args:
        meta: frame from playlist_meta; needs total_tracks.
    Returns:
        All columns of meta, sorted by total_tracks descending, index reset (rank = index + 1).
    """
    return meta.sort_values("total_tracks", ascending=False).reset_index(drop=True)


def follower_ranking(meta: pd.DataFrame) -> pd.DataFrame:
    """Playlists ordered by followers, most followed first.

    Args:
        meta: frame from playlist_meta; needs followers.
    Returns:
        All columns of meta, sorted by followers descending, index reset (rank = index + 1).
    """
    return meta.sort_values("followers", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Duplicates across playlists
# ---------------------------------------------------------------------------

def _occurrences(records: list[dict]) -> pd.DataFrame:
    """Raw frame (track_id, track_name, artist_name, playlist_name ...) without missing IDs."""
    raw = duplicates.raw_records_frame(records)
    if raw.empty:
        raw = pd.DataFrame(columns=["track_id", "track_name", "artist_name", "playlist_name"])
    return duplicates.drop_missing_ids(raw)


def exact_duplicates_across_playlists(records: list[dict]) -> pd.DataFrame:
    """Spotify IDs saved more than once, across or within playlists.

    Args:
        records: raw import records (enriched + skipped + failed), dicts with id, name,
            artists (a string) and playlist_name. Records without an ID are ignored.
    Returns:
        DataFrame [track_id, track_name, artist_display, count, playlists], one row per
        duplicated ID, sorted by track_name. track_name and artist_display come from the first
        occurrence of the ID; playlists is text such as "A (×2) · B". Ties in track_name keep
        track_id order. Empty (same columns) when nothing is duplicated.
    """
    raw = _occurrences(records)
    info = (raw.rename(columns={"artist_name": "artist_display"})
            .drop_duplicates("track_id")[["track_id", "track_name", "artist_display"]])
    found = duplicates.exact_duplicates(raw, include_playlists=True)[["track_id", "count", "playlists"]]
    found = found.sort_values("track_id").reset_index(drop=True)
    found["playlists"] = found["playlists"].apply(duplicates.format_playlist_counts)
    found = found.merge(info, on="track_id", how="left").sort_values("track_name").reset_index(drop=True)
    return found[["track_id", "track_name", "artist_display", "count", "playlists"]]


def different_versions_across_playlists(records: list[dict]) -> pd.DataFrame:
    """Songs saved under several Spotify IDs (same name and artist), across or within playlists.

    Args:
        records: raw import records, as for exact_duplicates_across_playlists.
    Returns:
        DataFrame [track_name, artist_name, n_unique_ids, total, playlists], one row per song,
        sorted by track_name; n_unique_ids is the number of versions and playlists is text
        such as "A (×2) · B". Empty when there are none.
    """
    found = duplicates.different_versions(_occurrences(records), include_playlists=True)
    found["playlists"] = found["playlists"].apply(duplicates.format_playlist_counts)
    return found


# ---------------------------------------------------------------------------
# Duration and popularity
# ---------------------------------------------------------------------------

def _extreme(row: pd.Series) -> TrackExtreme:
    return TrackExtreme(row["track_name"], row["artist_display"], row["playlist_name"], row["duration_raw"])


def duration_extremes(df: pd.DataFrame) -> DurationExtremes | None:
    """Longest and shortest track of the collection.

    Args:
        df: all rows; needs duration_raw in milliseconds, track_name, artist_display and
            playlist_name.
    Returns:
        DurationExtremes with durations in minutes, or None without a duration_raw column.
        The first row wins ties.
    """
    if "duration_raw" not in df.columns:
        return None
    minutes = df.assign(duration_raw=df["duration_raw"] / MS_PER_MINUTE)
    return DurationExtremes(
        longest=_extreme(minutes.loc[minutes["duration_raw"].idxmax()]),
        shortest=_extreme(minutes.loc[minutes["duration_raw"].idxmin()]),
    )


def duration_by_playlist(df: pd.DataFrame) -> pd.DataFrame | None:
    """Average, shortest and longest track duration per playlist.

    Args:
        df: all rows; needs playlist_name and duration_raw in milliseconds.
    Returns:
        DataFrame [playlist_name, avg_min, min_min, max_min] in minutes, one row per
        playlist ordered by playlist_name, or None without a duration_raw column.
    """
    if "duration_raw" not in df.columns:
        return None
    dur = (df.assign(duration_raw=df["duration_raw"] / MS_PER_MINUTE)
           .groupby("playlist_name")["duration_raw"].agg(["mean", "min", "max"]).reset_index())
    dur.columns = ["playlist_name", "avg_min", "min_min", "max_min"]
    return dur


def popularity_by_playlist(df: pd.DataFrame) -> pd.DataFrame | None:
    """Average Spotify popularity per playlist, most popular first.

    Args:
        df: all rows; needs playlist_name and popularity_raw (0-100).
    Returns:
        DataFrame [playlist_name, avg_popularity] sorted descending, or None without a
        popularity_raw column.
    """
    if "popularity_raw" not in df.columns:
        return None
    pop = df.groupby("playlist_name")["popularity_raw"].mean().reset_index()
    pop.columns = ["playlist_name", "avg_popularity"]
    return pop.sort_values("avg_popularity", ascending=False).reset_index(drop=True)


def popularity_axis_range(pop: pd.DataFrame) -> tuple[float, float]:
    """Zoomed (min, max) for a popularity chart axis.

    Args:
        pop: frame from popularity_by_playlist (needs avg_popularity).
    Returns:
        (max(0, lowest - 5), min(100, highest + 8)).
    """
    return max(0, pop["avg_popularity"].min() - 5), min(100, pop["avg_popularity"].max() + 8)


# ---------------------------------------------------------------------------
# Audio features
# ---------------------------------------------------------------------------

def feature_leaders(af_avg: pd.DataFrame) -> list[FeatureLeader]:
    """Playlist that tops each audio feature.

    Args:
        af_avg: per-playlist audio means (core.playlist_detail.playlist_audio_averages).
    Returns:
        One FeatureLeader per feature in LEADER_FEATURES that is present with at least one
        non-null value, in LEADER_FEATURES order. The first playlist wins ties.
    """
    leaders = []
    for feature in LEADER_FEATURES:
        if feature in af_avg.columns and af_avg[feature].notna().any():
            winner = af_avg.loc[af_avg[feature].idxmax()]
            leaders.append(FeatureLeader(feature, winner["playlist_name"], winner[feature]))
    return leaders


def heatmap_matrix(af_avg: pd.DataFrame, features: list[str]) -> pd.DataFrame | None:
    """Playlists x audio features matrix of means.

    Args:
        af_avg: per-playlist audio means.
        features: candidate feature columns, in display order.
    Returns:
        DataFrame indexed by playlist_name with the candidate features that are present and
        have a non-null value, or None when there are none.
    """
    available = [f for f in features if f in af_avg.columns and af_avg[f].notna().any()]
    if not available:
        return None
    return af_avg.set_index("playlist_name")[available]


# ---------------------------------------------------------------------------
# Genres, era and age
# ---------------------------------------------------------------------------

def count_unique_genres(genres: pd.Series) -> int:
    """Number of distinct genre tags in a series of comma-separated tag strings.

    Args:
        genres: one string per track (e.g. "rock, pop"); nulls are ignored.
    Returns:
        Distinct tags, compared case-insensitively after trimming; empty tags are skipped.
    """
    found = set()
    for value in genres.dropna():
        for part in str(value).split(","):
            part = part.strip()
            if part:
                found.add(part.lower())
    return len(found)


def genre_diversity(df: pd.DataFrame) -> pd.DataFrame | None:
    """Distinct genres per playlist, fewest first.

    Args:
        df: all rows; needs playlist_name and artist_genres.
    Returns:
        DataFrame [playlist_name, unique_genres] sorted ascending, or None without an
        artist_genres column. The last row is the most diverse playlist.
    """
    if "artist_genres" not in df.columns:
        return None
    diversity = df.groupby("playlist_name")["artist_genres"].apply(count_unique_genres).reset_index()
    diversity.columns = ["playlist_name", "unique_genres"]
    return diversity.sort_values("unique_genres", ascending=True)


def release_era(df: pd.DataFrame) -> pd.DataFrame | None:
    """Oldest, newest and mean release year per playlist, oldest average first.

    Args:
        df: all rows; needs playlist_name and release_year (nulls ignored).
    Returns:
        DataFrame [playlist_name, avg_release_year, min_year, max_year] sorted by
        avg_release_year ascending, or None without a release_year column.
    """
    if "release_year" not in df.columns:
        return None
    release = (df[df["release_year"].notna()].groupby("playlist_name")["release_year"]
               .agg(["mean", "min", "max"]).reset_index())
    release.columns = ["playlist_name", "avg_release_year", "min_year", "max_year"]
    return release.sort_values("avg_release_year", ascending=True)


def decade_mix(df: pd.DataFrame) -> pd.DataFrame | None:
    """Rows per playlist and decade.

    Args:
        df: all rows; needs playlist_name and decade (nulls ignored).
    Returns:
        DataFrame [playlist_name, decade_label, count] with labels like "1990s", or None
        without a decade column.
    """
    if "decade" not in df.columns:
        return None
    return (df[df["decade"].notna()]
            .assign(decade_label=lambda x: x["decade"].astype(int).astype(str) + "s")
            .groupby(["playlist_name", "decade_label"]).size().reset_index(name="count"))


def decade_tracks(df: pd.DataFrame, playlist: str, decade: int) -> pd.DataFrame:
    """Unique tracks of one playlist released in one decade.

    Args:
        df: all rows; needs track_id, playlist_name and decade. Optional columns
            track_name, artist_display, release_year, popularity_raw are returned if present.
        playlist: playlist_name to select.
        decade: decade start year, e.g. 1990.
    Returns:
        DataFrame [Track, Artist, Year, Popularity] (those present), one row per track_id
        (the collection is de-duplicated by track_id first, so a track saved in several
        playlists belongs only to the first one), sorted by Year with missing years as 0.
    """
    deduped = df.drop_duplicates(subset="track_id")
    mask = ((deduped["playlist_name"] == playlist) & deduped["decade"].notna()
            & (deduped["decade"].astype("Int64") == decade))
    cols = [c for c in ["track_name", "artist_display", "release_year", "popularity_raw"] if c in deduped.columns]
    tracks = deduped[mask][cols].copy()
    if "release_year" in tracks.columns:
        tracks = tracks.sort_values("release_year")
        tracks["release_year"] = tracks["release_year"].fillna(0).astype(int)
    tracks = tracks.rename(columns={"track_name": "Track", "artist_display": "Artist",
                                    "release_year": "Year", "popularity_raw": "Popularity"})
    return tracks.reset_index(drop=True)


def playlist_age(df: pd.DataFrame) -> pd.DataFrame | None:
    """When each playlist first received a track, oldest first.

    Args:
        df: all rows; needs playlist_name and added_at (date-like; nulls ignored).
    Returns:
        DataFrame [playlist_name, first_added (Timestamp), first_added_str ("Mon YYYY")]
        sorted by first_added ascending, or None without an added_at column. Playlists with
        no dated track are absent (see playlists_without_dates).
    """
    if "added_at" not in df.columns:
        return None
    age = (df[df["added_at"].notna()].assign(added_at=lambda x: pd.to_datetime(x["added_at"]))
           .groupby("playlist_name")["added_at"].min().reset_index())
    age.columns = ["playlist_name", "first_added"]
    age = age.sort_values("first_added", ascending=True).reset_index(drop=True)
    age["first_added_str"] = age["first_added"].dt.strftime("%b %Y")
    return age


def playlists_without_dates(df: pd.DataFrame, age: pd.DataFrame) -> list[str]:
    """Playlists of df that are missing from a playlist_age result.

    Args:
        df: all rows; needs playlist_name.
        age: frame from playlist_age.
    Returns:
        Playlist names sorted alphabetically.
    """
    return sorted(set(df["playlist_name"].unique()) - set(age["playlist_name"].tolist()))
