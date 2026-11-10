"""Duplicate and "different version" detection over raw track records (no Streamlit, no session state).

Two related questions, both answered from raw Spotify records (real Spotify IDs, including the
tracks that had no audio features) rather than from the normalized collection frame:

  - Exact duplicates: the same Spotify ID saved more than once.
  - Different versions: the same song and primary artist saved under different Spotify IDs
    (separate recordings, remasters, live versions...). Song names are compared after removing
    version suffixes (song_key), so "Song" and "Song - Remastered 2009" match; the original app
    compared the exact names.

Data shape ("raw frame"): one row per saved occurrence of a track, with columns
track_id (Spotify ID), track_name, artist_name and playlist_name. Build it from the raw session
records (dicts with id / name / artists / playlist_name) with raw_records_frame, then drop
rows without an ID with drop_missing_ids. All functions work on one playlist or on many: pass
only the rows to analyse. Inputs are never mutated.

Used today for one playlist (Playlist Detail). The functions accept an optional
include_playlists flag so a cross-playlist analysis can get, for every group, the list of
playlists it occurs in (one entry per occurrence).
"""
from __future__ import annotations

import re

import pandas as pd

#: Columns of the raw frame produced by raw_records_frame.
RAW_FRAME_COLUMNS = ["track_id", "track_name", "artist_name"]

_RAW_RENAMES = {"id": "track_id", "name": "track_name", "artists": "artist_name"}


# A trailing " - ..." segment, or a trailing "(...)" / "[...]", that names a version of the same
# song: remasters, mono/stereo mixes, single/radio/album edits, live, acoustic and demo takes
# (Spanish too: "Remasterizado", "En Vivo"). Remixes are kept as different songs.
_VERSION_WORDS = re.compile(
    r"\b(remaster\w*|mono|stereo|mix|version|edit|live|en vivo|unplugged|acoustic|ac[uú]stic[oa]|demo"
    r"|anniversary|aniversario)\b",
    re.IGNORECASE,
)
_REMIX = re.compile(r"\bremix", re.IGNORECASE)
_TRAILING_SEGMENT = re.compile(r"^(?P<base>.+?)\s*(?:\s-\s(?P<dash>[^-]+)|[(\[](?P<paren>[^()\[\]]+)[)\]])\s*$")


def strip_version_suffix(name: str) -> str:
    """The song name without trailing version suffixes, original case kept.

    "Come Together - Remastered 2009" -> "Come Together"; "Heart of Gold - Live" -> "Heart of
    Gold"; "Hey Jude (Remastered 2015)" -> "Hey Jude". Several suffixes are removed one after
    the other. Remixes ("Song - Deepend Remix") and other suffixes ("Song - From 'X'",
    "Song (feat. Y)") are kept. Never returns an empty string.
    """
    name = str(name).strip()
    while True:
        m = _TRAILING_SEGMENT.match(name)
        if not m:
            return name
        suffix = m.group("dash") or m.group("paren")
        if not _VERSION_WORDS.search(suffix) or _REMIX.search(suffix):
            return name
        name = m.group("base").strip()


_OTHER_PERFORMANCE = re.compile(r"\b(live|en vivo|unplugged|demo|acoustic|ac[uú]stic[oa])\b", re.IGNORECASE)
_FEAT = re.compile(r"\s*[(\[](?:feat|ft|featuring|with)\.?\s[^()\[\]]*[)\]]\s*$", re.IGNORECASE)


def other_performance(name: str, album: str | None = None) -> bool:
    """A live, demo or acoustic take: its version suffix (or its album's name) says so.

    "Heart of Gold - Live" and any track of "MTV Unplugged" are; "Live and Let Die" isn't."""
    name = str(name)
    suffix = name[len(strip_version_suffix(name)):]
    return bool(_OTHER_PERFORMANCE.search(suffix)) or bool(album and re.search(
        r"\b(live|en vivo|unplugged|in concert)\b", album, re.IGNORECASE))


def base_title(name: str) -> str:
    """The song title to look up: version suffixes and a trailing "(feat. X)" removed."""
    name = strip_version_suffix(name)
    while True:
        cut = _FEAT.sub("", name).strip()
        if cut == name or not cut:
            return name
        name = strip_version_suffix(cut)


def song_key(name: str) -> str:
    """Comparison key for different versions: strip_version_suffix, case-folded."""
    return strip_version_suffix(name).casefold()


def raw_records_frame(records: list[dict], playlist_name: str | None = None) -> pd.DataFrame:
    """Raw track records as a frame with canonical column names.

    Args:
        records: raw Spotify records, dicts with keys id, name, artists (a string) and
            playlist_name; other keys are kept as extra columns. Typically the enriched,
            skipped and failed lists of the collection concatenated.
        playlist_name: when given, only records whose playlist_name equals it are kept.
    Returns:
        DataFrame with id/name/artists renamed to track_id/track_name/artist_name. With no
        matching records, an empty DataFrame with columns track_id, track_name, artist_name.
    """
    selected = [t for t in records if playlist_name is None or t.get("playlist_name") == playlist_name]
    if not selected:
        return pd.DataFrame(columns=RAW_FRAME_COLUMNS)
    return pd.DataFrame(selected).rename(columns=_RAW_RENAMES)


def drop_missing_ids(raw: pd.DataFrame) -> pd.DataFrame:
    """Remove rows whose track_id is missing (NaN/None) or an empty string.

    Args:
        raw: raw frame; must have a track_id column.
    Returns:
        The remaining rows (a filtered view; original index labels preserved).
    """
    return raw[raw["track_id"].notna() & (raw["track_id"].astype(str) != "")]


def exact_duplicates(
    raw: pd.DataFrame, artist_column: str = "artist_name", include_playlists: bool = False
) -> pd.DataFrame:
    """Spotify IDs that appear more than once.

    Args:
        raw: raw frame without missing IDs (see drop_missing_ids); needs track_id, track_name
            and artist_column (plus playlist_name when include_playlists is True).
        artist_column: column holding the artist text to report (first occurrence wins).
        include_playlists: also return a "playlists" column with the playlist_name of every
            occurrence, in input order (repeated when a playlist saves the track twice).
    Returns:
        DataFrame [track_id, count, track_name, artist] (+ playlists), one row per duplicated
        ID, sorted by count descending (tie order unspecified). track_name and artist are the
        first occurrence's. Empty when nothing is duplicated.
    """
    aggregations = dict(
        count=("track_id", "count"), track_name=("track_name", "first"), artist=(artist_column, "first")
    )
    if include_playlists:
        aggregations["playlists"] = ("playlist_name", list)
    groups = raw.groupby("track_id").agg(**aggregations).reset_index()
    return groups[groups["count"] > 1].sort_values("count", ascending=False).reset_index(drop=True)


def different_versions(raw: pd.DataFrame, include_playlists: bool = False) -> pd.DataFrame:
    """Songs saved under more than one Spotify ID (same song_key and artist_name).

    Args:
        raw: raw frame without missing IDs; needs track_id, track_name and artist_name (plus
            playlist_name when include_playlists is True). Rows with a missing name or artist
            are ignored.
        include_playlists: also return a "playlists" column (see exact_duplicates).
    Returns:
        DataFrame [track_name, artist_name, n_unique_ids, total, names, track_ids]
        (+ playlists), one row per song with total > 1 occurrences and n_unique_ids > 1 distinct
        IDs, sorted by track_name. track_name is the first occurrence's name without version
        suffixes; names lists the distinct saved names and track_ids the distinct IDs, both in
        input order. n_unique_ids is the number of versions. Empty when there are none. A song
        that is only an exact duplicate (same ID twice) is not a version.
    """
    columns = ["track_name", "artist_name", "n_unique_ids", "total", "names", "track_ids"]
    if include_playlists:
        columns.append("playlists")
    rows = raw.dropna(subset=["track_name", "artist_name"])
    if rows.empty:
        return pd.DataFrame(columns=columns)
    rows = rows.assign(_key=rows["track_name"].map(song_key))
    aggregations = dict(
        track_name=("track_name", "first"),
        n_unique_ids=("track_id", "nunique"),
        total=("track_id", "count"),
        names=("track_name", lambda s: list(dict.fromkeys(s))),
        track_ids=("track_id", lambda s: list(dict.fromkeys(s))),
    )
    if include_playlists:
        aggregations["playlists"] = ("playlist_name", list)
    groups = rows.groupby(["_key", "artist_name"], sort=False).agg(**aggregations).reset_index()
    groups["track_name"] = groups["track_name"].map(strip_version_suffix)
    found = groups[(groups["total"] > 1) & (groups["n_unique_ids"] > 1)]
    return found.sort_values("track_name", kind="stable")[columns].reset_index(drop=True)


def format_playlist_counts(playlists: list[str]) -> str:
    """Summarise a list of playlist names (one per occurrence) as readable text.

    Args:
        playlists: playlist_name of every occurrence, e.g. ["B", "A", "A"].
    Returns:
        Names sorted alphabetically and joined with " · "; a name occurring more than once
        gets a count suffix: ["B", "A", "A"] -> "A (×2) · B". Empty string for an empty list.
    """
    counts: dict = {}
    for name in playlists:
        counts[name] = counts.get(name, 0) + 1
    parts = [f"{name} (×{n})" if n > 1 else name for name, n in sorted(counts.items())]
    return " · ".join(parts)
