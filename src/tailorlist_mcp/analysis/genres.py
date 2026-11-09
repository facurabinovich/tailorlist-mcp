"""Genre-family logic (no Streamlit): maps raw Spotify artist-genre strings onto
broad families and answers family-filter questions about tracks."""
from __future__ import annotations

import pandas as pd

GENRE_FAMILIES: dict[str, list[str]] = {
    # Checked in order — first matching family wins.
    # Keywords are matched as substrings of the full genre string (case-insensitive).
    "Rock":       ["rock", "punk", "metal", "grunge", "hardcore", "emo", "shoegaze"],
    "Pop":        ["pop"],  # "pop" alone covers baroque pop, indie pop, synth-pop, afropop, etc.
    "Hip-Hop":    ["hip hop", "rap", "trap", "drill", "r&b", "soul", "grime"],
    "Electronic": ["electronic", "edm", "house", "techno", "trance", "ambient", "synthwave",
                   "disco", "new wave", "dubstep", "drum and bass", "electro", "downtempo",
                   "chillwave", "darkwave", "cold wave", "eurodance", "italo"],
    "Jazz":       ["jazz", "bebop", "swing", "blues", "big band", "boogie"],
    "Classical":  ["classical", "opera", "orchestra", "chamber"],
    "Folk":       ["folk", "acoustic", "singer-songwriter", "country", "americana", "bluegrass",
                   "newgrass", "honky tonk"],
    "Latin":      ["latin", "reggaeton", "salsa", "cumbia", "bossa nova", "bachata", "tango",
                   "bolero", "merengue", "mariachi", "ranchera", "mpb", "trova", "soca",
                   "calypso", "folklore", "folclor", "candombe", "murga", "villancico"],
    "Reggae":     ["reggae", "dancehall", "ska", "ragga", "riddim", "dub"],
}


def assign_family(genre_str: str | None) -> list[str]:
    """Map a raw artist-genre string (e.g. "indie rock, baroque pop") to family labels.

    Returns every family in GENRE_FAMILIES whose keywords appear (case-insensitive
    substring match) in the string, in GENRE_FAMILIES order. A track can belong to
    several families. Returns ["Other"] when the string is empty/None/NaN or matches
    no family.
    """
    if not genre_str or pd.isna(genre_str) or str(genre_str).strip() == "":
        return ["Other"]
    genres_lower = genre_str.lower()
    matched = [
        family for family, keywords in GENRE_FAMILIES.items()
        if any(kw in genres_lower for kw in keywords)
    ]
    return matched if matched else ["Other"]


def track_matches_families(genre_str: str | None, selected_fams: list[str]) -> bool:
    """Return True if a track passes a genre-family filter.

    True when ANY of the track's raw genre tags match ANY selected family's keywords,
    or when "Other" is selected and the genre string matches no known family keyword.
    Tracks with NO genre data (None/NaN/empty) always pass — they belong to no family,
    so the family filter cannot exclude them (they surface in the unmatched section).
    """
    if not genre_str or pd.isna(genre_str) or str(genre_str).strip() == "":
        return True  # genre-less tracks are never filtered out by family selection
    genres_lower = genre_str.lower()
    for fam in selected_fams:
        if fam == "Other":
            continue
        keywords = GENRE_FAMILIES.get(fam, [])
        if any(kw in genres_lower for kw in keywords):
            return True
    # No family matched — check if Other is selected (catches unrecognized genres)
    all_known_keywords = [kw for kws in GENRE_FAMILIES.values() for kw in kws]
    is_unrecognized = not any(kw in genres_lower for kw in all_known_keywords)
    if is_unrecognized and "Other" in selected_fams:
        return True
    return False


def preview_filter(
    df: pd.DataFrame,
    playlists: list[str],
    decades: list[str],
    families: list[str],
    raw_genres: list[str],
    all_families_available: list[str] | None = None,
) -> int:
    """Return the number of tracks left in `df` after applying all filters.

    Filters, applied in order: `playlists` (match on `playlist_name`; empty = all),
    `decades` (match on the `decade` column as strings like "1990"; empty = all),
    then genre: if `raw_genres` is non-empty, keep tracks whose `artist_genres`
    contains any of them as a substring; otherwise, if `all_families_available`
    is given and `families` differs from it as a set, keep tracks passing
    track_matches_families (genre-less tracks always pass). Zero-cost preview:
    does not modify `df`.
    """
    out = df[df["playlist_name"].isin(playlists)].copy() if playlists else df.copy()
    if decades and "decade" in out.columns:
        out = out[out["decade"].astype("Int64").astype(str).isin(decades)]
    if "artist_genres" in out.columns:
        if raw_genres:
            out = out[out["artist_genres"].apply(
                lambda g: any(rg in (g or "") for rg in raw_genres)
            )]
        elif all_families_available is not None and set(families) != set(all_families_available):
            out = out[out["artist_genres"].apply(
                lambda g: track_matches_families(g, families)
            )]
    return len(out)
