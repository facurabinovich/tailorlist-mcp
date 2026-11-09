"""Grouping of tracks by explicit categories (no clustering algorithm, no Streamlit).

Three pure functions over a track DataFrame:
  - release_date_to_decade: Spotify release_date string -> decade.
  - group_by_category: one group per genre family and/or decade; incomplete tracks are
    returned separately as "unmatched".
  - group_by_specific_genres: one group per chosen genre tag (a track may appear in several).

Inputs are explicit; nothing is read from session state or globals, and inputs are never mutated.
"""
from __future__ import annotations

import pandas as pd

from tailorlist_mcp.analysis.genres import assign_family as _assign_family


def release_date_to_decade(rd: str | None) -> int | None:
    """Derive the decade (e.g. 2010) from a Spotify release_date string.

    Args:
        rd: release date such as "2014", "2014-03" or "2014-03-15"; empty/None allowed.
            Only the first four characters are read.
    Returns:
        The decade as an int (2014-03-15 -> 2010), or None when rd is empty, not numeric
        in its first four characters, or the year is outside 1901..2099.
    """
    if not rd:
        return None
    try:
        y = int(str(rd)[:4])
        return y // 10 * 10 if 1900 < y < 2100 else None
    except (ValueError, TypeError):
        return None


def group_by_category(
    df: pd.DataFrame,
    group_by_decade: bool,
    group_by_genre: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Group tracks by genre family and/or decade; tracks missing a required dimension are set aside.

    Args:
        df: tracks. Uses "artist_genres" (comma-separated tags; used when group_by_genre)
            and "decade" (int or NaN; used when group_by_decade). Missing display/audio
            columns (release_year, artist_display, energy, valence, danceability,
            acousticness, tempo, speechiness) are added empty. The input is not modified.
        group_by_decade: split groups by decade ("1990s").
        group_by_genre: split groups by primary genre family (see core.genres).
    Returns:
        (df_clustered, df_unmatched). df_clustered adds _genre_group, _decade_group,
        cluster_name (e.g. "Rock · 1990s", or "Ungrouped" if both flags are off), cluster
        (index into the alphabetically sorted names), PC1 and PC2 (both 0.0).
        df_unmatched holds tracks with an unknown genre (when group_by_genre) or unknown
        decade (when group_by_decade); it has the helper columns but no cluster columns.
    """
    df = df.copy()
    if "release_year" not in df.columns:
        df["release_year"] = ""
    if "artist_display" not in df.columns:
        df["artist_display"] = df.get("artist_name", "")
    for _audio_col in ["energy", "valence", "danceability", "acousticness", "tempo", "speechiness"]:
        if _audio_col not in df.columns:
            df[_audio_col] = float("nan")

    if group_by_genre and "artist_genres" in df.columns:
        df["_genre_group"] = df["artist_genres"].apply(
            lambda g: "Unknown Genre" if (not g or pd.isna(g) or str(g).strip() == "")
            else _assign_family(g)[0]
        )
    else:
        df["_genre_group"] = None

    if group_by_decade and "decade" in df.columns:
        df["_decade_group"] = df["decade"].apply(
            lambda d: f"{int(d)}s" if pd.notna(d) else "Unknown Decade"
        )
    else:
        df["_decade_group"] = None

    # Tracks missing any required grouping field go to manual assignment
    _unmatched_mask = pd.Series(False, index=df.index)
    if group_by_genre:
        _unmatched_mask |= (df["_genre_group"] == "Unknown Genre")
    if group_by_decade:
        _unmatched_mask |= (df["_decade_group"] == "Unknown Decade")

    df_unmatched = df[_unmatched_mask].copy()
    df = df[~_unmatched_mask].copy()

    def _build_name(row):
        parts = []
        if group_by_genre and row["_genre_group"]:
            parts.append(row["_genre_group"])
        if group_by_decade and row["_decade_group"]:
            parts.append(row["_decade_group"])
        return " · ".join(parts) if parts else "Ungrouped"

    df["cluster_name"] = df.apply(_build_name, axis=1)
    unique_names = sorted(df["cluster_name"].unique())
    name_to_idx = {n: i for i, n in enumerate(unique_names)}
    df["cluster"] = df["cluster_name"].map(name_to_idx)
    df["PC1"] = 0.0
    df["PC2"] = 0.0
    return df, df_unmatched


def group_by_specific_genres(
    df: pd.DataFrame,
    selected_genres: list,
    group_by_decade: bool,
) -> pd.DataFrame:
    """Build one group per selected genre tag; a track joins every group whose tag it carries.

    Args:
        df: tracks with an "artist_genres" column (comma-separated tags, matched exactly
            after stripping whitespace; None/empty means no tags). "decade" is used when
            group_by_decade. Missing display/audio columns are added empty. Not modified.
        selected_genres: exact genre tags to build groups for, e.g. ["rock", "hip hop"].
        group_by_decade: also split each genre group by decade ("rock · 1970s", "Unknown"
            when the decade is missing).
    Returns:
        DataFrame of the matching tracks (a track repeated once per matching genre) with
        cluster_name, cluster (index into the sorted names), PC1 and PC2 (0.0). Genres
        with no tracks produce no group; tracks matching no selected genre are dropped.
        An empty DataFrame when nothing matches.
    """
    df = df.copy()
    if "release_year" not in df.columns:
        df["release_year"] = ""
    if "artist_display" not in df.columns:
        df["artist_display"] = df.get("artist_name", "")
    for _audio_col in ["energy", "valence", "danceability", "acousticness", "tempo", "speechiness"]:
        if _audio_col not in df.columns:
            df[_audio_col] = float("nan")

    chunks = []
    for genre in selected_genres:
        matching = df[df["artist_genres"].apply(
            lambda g: genre in {t.strip() for t in (g or "").split(",")}
        )].copy()
        if matching.empty:
            continue
        if group_by_decade and "decade" in matching.columns:
            matching["cluster_name"] = genre + " · " + matching["decade"].apply(
                lambda d: f"{int(d)}s" if pd.notna(d) else "Unknown"
            )
        else:
            matching["cluster_name"] = genre
        chunks.append(matching)

    if not chunks:
        df_clust = pd.DataFrame()
    else:
        df_clust = pd.concat(chunks, ignore_index=True)
        unique_names = sorted(df_clust["cluster_name"].unique())
        name_to_idx = {n: i for i, n in enumerate(unique_names)}
        df_clust["cluster"] = df_clust["cluster_name"].map(name_to_idx)
        df_clust["PC1"] = 0.0
        df_clust["PC2"] = 0.0
    return df_clust
