"""Audio DNA analysis (no Streamlit, no session state, no plotting).

Pure functions behind the Audio DNA page: playlist filtering, mood quadrants, average audio
profiles (collection and per playlist), feature histograms, correlations, extreme tracks and
enrichment counts. Their results back both the dashboard and agent tools.

Conventions shared by every function:
  - The collection frame (df) has one row per (track, playlist) pair. Expected columns (all
    optional unless a function says otherwise): track_id, track_name, artist_display,
    playlist_name, popularity_raw (0-100) and the audio features energy, acousticness,
    valence, danceability, speechiness, instrumentalness, liveness (all 0-1), loudness (dB)
    and tempo (BPM).
  - The "unique frame" is the selected rows deduplicated by track_id (first row wins), so a
    track saved in several playlists counts once. Per-playlist profiles deduplicate within
    the playlist only.
  - Inputs are never mutated. Nothing reads the clock or the session.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from tailorlist_mcp.config import AUDIO_FEATURES, NORMALIZED_FEATURES

#: The 7 normalized (0-1) features drawn on the radar charts, in axis order.
RADAR_FEATURES = ["energy", "acousticness", "valence", "danceability",
                  "speechiness", "instrumentalness", "liveness"]

#: Display names of the audio features.
FEATURE_LABELS = {
    "energy": "Energy", "acousticness": "Acousticness", "valence": "Valence",
    "danceability": "Danceability", "speechiness": "Speechiness",
    "instrumentalness": "Instrumentalness", "liveness": "Liveness",
    "loudness": "Loudness", "tempo": "Tempo",
}

#: (column, label) pairs of the features whose extreme tracks are listed, in display order.
EXTREME_FEATURES = [
    ("energy", "Energy"),
    ("acousticness", "Acousticness"),
    ("valence", "Valence"),
    ("danceability", "Danceability"),
    ("instrumentalness", "Instrumentalness"),
    ("liveness", "Liveness"),
    ("loudness", "Loudness (dB)"),
    ("tempo", "Tempo (BPM)"),
    ("speechiness", "Speechiness"),
]

#: Features whose histograms use a log10 axis (they are mostly near zero).
LOG_SCALE_FEATURES = ("instrumentalness", "speechiness")

#: Valence/energy cut-off between the mood quadrants (a value equal to it counts as high).
MOOD_THRESHOLD = 0.5

#: Mood quadrant names in display order.
MOOD_NAMES = ["Happy", "Turbulent", "Peaceful", "Dark"]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlaylistSelection:
    """Rows of the selected playlists.

    Attributes:
        rows: every (track, playlist) row of the selected playlists.
        unique: rows deduplicated by track_id (the frame all charts use).
    """
    rows: pd.DataFrame
    unique: pd.DataFrame


@dataclass(frozen=True)
class MoodQuadrantStat:
    """Size of one mood quadrant.

    Attributes:
        name: Happy (valence and energy high), Turbulent (valence low, energy high),
            Peaceful (valence high, energy low) or Dark (both low).
        count: tracks in the quadrant.
        pct: percent (0-100) of all tracks, 0.0 when there are none.
    """
    name: str
    count: int
    pct: float


@dataclass(frozen=True)
class EnrichmentStats:
    """Unique-track counts by audio-feature coverage.

    Attributes:
        with_audio_features: unique track ids that were enriched.
        without_audio_features: unique ids skipped or failed and never enriched.
        total: the sum of both.
    """
    with_audio_features: int
    without_audio_features: int
    total: int


@dataclass(frozen=True)
class Histogram:
    """Histogram of one feature.

    Attributes:
        centers: bin centers (log10 units when log_scale is True).
        counts: tracks per bin.
        log_scale: True when the values were log10-transformed.
    """
    centers: list[float]
    counts: np.ndarray
    log_scale: bool


# ---------------------------------------------------------------------------
# Selection and coverage
# ---------------------------------------------------------------------------

def select_playlists(df: pd.DataFrame, selected: list[str]) -> PlaylistSelection:
    """Keep the rows of the selected playlists, plus their unique-track version.

    Args:
        df: collection frame (needs playlist_name and track_id).
        selected: playlist names to keep; unknown names match nothing.

    Returns:
        PlaylistSelection with every selected row and the track_id-deduplicated frame.
    """
    rows = df[df["playlist_name"].isin(selected)]
    return PlaylistSelection(rows=rows, unique=rows.drop_duplicates(subset="track_id"))


def has_audio_data(df: pd.DataFrame) -> bool:
    """True when at least one radar feature column has a non-missing value."""
    return any(f in df.columns and df[f].notna().any() for f in RADAR_FEATURES)


def enrichment_stats(enriched: list[dict], skipped: list[dict], failed: list[dict]) -> EnrichmentStats:
    """Count unique tracks with and without audio features.

    Args:
        enriched: track dicts (key "id" = Spotify track id) that got audio features.
        skipped: track dicts that were skipped during enrichment.
        failed: track dicts whose lookup failed.

    Returns:
        EnrichmentStats. Entries without an id are ignored; an id that is both enriched and
        skipped/failed counts as enriched only.
    """
    enriched_ids = {t["id"] for t in enriched if t.get("id")}
    missing_ids = {t["id"] for t in skipped + failed if t.get("id")} - enriched_ids
    return EnrichmentStats(
        with_audio_features=len(enriched_ids),
        without_audio_features=len(missing_ids),
        total=len(enriched_ids) + len(missing_ids),
    )


# ---------------------------------------------------------------------------
# Mood
# ---------------------------------------------------------------------------

def mood_quadrants(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Split tracks into the four valence x energy mood quadrants.

    Args:
        df: frame with valence and energy.

    Returns:
        Dict name -> rows, in MOOD_NAMES order. A value equal to MOOD_THRESHOLD counts as
        high. Rows with a missing valence or energy fall in no quadrant.
    """
    high_v = df["valence"] >= MOOD_THRESHOLD
    low_v = df["valence"] < MOOD_THRESHOLD
    high_e = df["energy"] >= MOOD_THRESHOLD
    low_e = df["energy"] < MOOD_THRESHOLD
    return {
        "Happy": df[high_v & high_e],
        "Turbulent": df[low_v & high_e],
        "Peaceful": df[high_v & low_e],
        "Dark": df[low_v & low_e],
    }


def quadrant_pct(count: int, total: int) -> float:
    """Percent (0-100) of `count` over `total`; 0.0 when total is 0."""
    return count / total * 100 if total else 0.0


def mood_summary(df: pd.DataFrame) -> list[MoodQuadrantStat]:
    """Count and share of tracks per mood quadrant (percent is over all rows of df).

    Args:
        df: frame with valence and energy.

    Returns:
        One MoodQuadrantStat per quadrant in MOOD_NAMES order.
    """
    return [MoodQuadrantStat(name, len(sub), quadrant_pct(len(sub), len(df)))
            for name, sub in mood_quadrants(df).items()]


def marker_sizes(sub: pd.DataFrame) -> list[float]:
    """Scatter marker sizes from popularity: popularity_raw / 100 * 8 + 3 (3 to 11).

    Missing popularity counts as 0. Without a popularity_raw column every size is 6.
    """
    if "popularity_raw" in sub.columns:
        return (sub["popularity_raw"].astype(float).fillna(0.0) / 100 * 8 + 3).tolist()
    return [6] * len(sub)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

def feature_means(df: pd.DataFrame, features: list[str] | None = None) -> list[float]:
    """Mean of each feature over df (missing values ignored; NaN if none).

    Args:
        df: frame holding every requested feature column.
        features: columns to average; defaults to RADAR_FEATURES.

    Returns:
        One mean per feature, in order.
    """
    return [df[f].mean() for f in (RADAR_FEATURES if features is None else features)]


def playlist_feature_means(df: pd.DataFrame, playlist: str, features: list[str] | None = None) -> list[float]:
    """Mean of each feature over one playlist's unique tracks.

    Args:
        df: collection frame (playlist_name, track_id and the features).
        playlist: playlist name; no match gives NaN means.
        features: columns to average; defaults to RADAR_FEATURES.

    Returns:
        One mean per feature, in order.
    """
    sub = df[df["playlist_name"] == playlist].drop_duplicates(subset="track_id")
    return feature_means(sub, features)


# ---------------------------------------------------------------------------
# Distributions and correlations
# ---------------------------------------------------------------------------

def distribution_features(df: pd.DataFrame) -> list[str]:
    """Features to histogram: the normalized features plus tempo, those present in df."""
    return [f for f in NORMALIZED_FEATURES + ["tempo"] if f in df.columns]


def uses_log_scale(vals: pd.Series, feature: str) -> bool:
    """True when the histogram of `feature` should use a log10 axis.

    That holds for LOG_SCALE_FEATURES with more than 10 positive values.
    """
    return feature in LOG_SCALE_FEATURES and (vals > 0).sum() > 10


def feature_histogram(vals: pd.Series, feature: str, n_bins: int = 30) -> Histogram:
    """Histogram of one feature.

    Args:
        vals: feature values (NaN must already be dropped).
        feature: column name; decides the scale (see uses_log_scale).
        n_bins: number of bin edges for the log case (n_bins - 1 bins) and bins otherwise.

    Returns:
        Histogram. On the log scale only values above 0 are counted, as log10(v + 1e-4).
    """
    log_scale = bool(uses_log_scale(vals, feature))
    if log_scale:
        log_vals = np.log10(vals[vals > 0] + 1e-4)
        bins = np.linspace(log_vals.min(), log_vals.max(), n_bins)
        counts, edges = np.histogram(log_vals, bins=bins)
    else:
        counts, edges = np.histogram(vals, bins=n_bins)
    centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(edges) - 1)]
    return Histogram(centers=centers, counts=counts, log_scale=log_scale)


def correlation_features(df: pd.DataFrame) -> list[str]:
    """AUDIO_FEATURES columns present in df, in config order."""
    return [f for f in AUDIO_FEATURES if f in df.columns]


def correlation_matrix(df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    """Pearson correlation of the features over rows complete in all of them, rounded to 2 decimals."""
    return df[features].dropna().corr(method="pearson").round(2)


def top_correlations(corr: pd.DataFrame, n: int = 3) -> pd.DataFrame:
    """The n strongest feature pairs of a correlation matrix.

    Args:
        corr: square matrix from correlation_matrix.
        n: how many pairs to return.

    Returns:
        DataFrame [feat_a, feat_b, r, abs_r] over the upper triangle (no diagonal, no mirror
        pairs), sorted by abs_r descending.
    """
    mask = np.triu(np.ones(corr.shape), k=1).astype(bool)
    flat = corr.where(mask).stack().reset_index()
    flat.columns = ["feat_a", "feat_b", "r"]
    return flat.assign(abs_r=flat["r"].abs()).sort_values("abs_r", ascending=False).head(n)


# ---------------------------------------------------------------------------
# Extreme tracks
# ---------------------------------------------------------------------------

def extreme_tracks(df: pd.DataFrame, feature: str, n: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The tracks with the highest and lowest value of a feature.

    Args:
        df: unique-track frame with track_name, artist_display, playlist_name and the feature.
        feature: column to rank.
        n: rows per side.

    Returns:
        (highest, lowest), each with columns [track_name, artist_display, playlist_name,
        feature]. For LOG_SCALE_FEATURES the lowest ignores zeros when at least n rows are
        above zero; otherwise zeros are included.
    """
    cols = ["track_name", "artist_display", "playlist_name", feature]
    top = df.nlargest(n, feature)[cols]
    if feature in LOG_SCALE_FEATURES:
        non_zero = df[df[feature] > 0]
        bottom = (non_zero.nsmallest(n, feature) if len(non_zero) >= n else df.nsmallest(n, feature))[cols]
    else:
        bottom = df.nsmallest(n, feature)[cols]
    return top, bottom


def format_extreme_value(val: float, feature: str) -> str:
    """Format a feature value for display.

    Tempo and loudness get 1 decimal, instrumentalness and speechiness 4, the rest 3. A result
    of "0.000", "0.0000" or "0.0001" is shown as "< 0.001".
    """
    if feature in ("tempo", "loudness"):
        result = f"{val:.1f}"
    elif feature in LOG_SCALE_FEATURES:
        result = f"{val:.4f}"
    else:
        result = f"{val:.3f}"
    if result in ("0.000", "0.0000", "0.0001"):
        return "< 0.001"
    return result
