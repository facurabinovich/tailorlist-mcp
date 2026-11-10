"""Metric definitions for the explain tool.

They used to sit at the end of the server instructions, where Claude Code cut them off (it
truncates long instructions). Like the original app's Glossary page, Claude reads them when the
user asks what a number means.
"""
from __future__ import annotations

from tailorlist_mcp.analysis.genres import GENRE_FAMILIES


def _families() -> str:
    lines = [f"  {name} = {', '.join(words)}" for name, words in GENRE_FAMILIES.items()]
    return (
        f"{', '.join(GENRE_FAMILIES)}, Other. A family matches when any of its keywords appears inside a "
        "genre tag (so \"baroque pop\" is Pop); a track can belong to several families; no match means "
        "Other. Keywords:\n" + "\n".join(lines) + "\n\"Other\" often holds adult standards, chanson, christmas, "
        "comedy, doo-wop, drone, easy listening, gospel, new age, schlager, soundtrack and very niche tags."
    )


#: term -> (aliases, definition). Aliases are matched as parts of the asked term too.
TERMS: dict[str, tuple[tuple[str, ...], str]] = {
    "unique tracks": (("entries", "tracks", "artists", "primary artist", "featured"),
        "Unique tracks: distinct Spotify track IDs; a track saved in two playlists counts once. Entries: every "
        "saved occurrence (it counts twice). Artists are PRIMARY artists only; featured artists show as "
        "\"A feat. B\" but aren't counted."),
    "popularity": (("avg popularity", "popular"),
        "Popularity: Spotify's 0-100 score from play counts and how recent they are. It reflects current global "
        "popularity, not the user's listening. Spotify gives 0 to album versions it replaced with a newer "
        "release (it plays the new one instead); for those, Tailorlist uses the popularity of the version "
        "Spotify plays now (find_replaced_tracks lists them). \"Avg popularity\" averages unique tracks."),
    "release year": (("year", "decade", "compilation", "reissue"),
        "Release year: the song's original release year when fill_years found one in MusicBrainz that is EARLIER "
        "than its album's (\"Hey Jude\" from the compilation \"1\" counts as 1968, not 2000); otherwise the "
        "album's release date. Live, demo and acoustic takes always keep their album year (they are other "
        "recordings). find_tracks shows \"Album year\" when the two differ; sort=\"year_gap\" lists those tracks. "
        "Decades, median era and listening age all use this year."),
    "top decade": (("median era", "era", "listening span", "span"),
        "Top decade: the decade with the most unique tracks. Median era: the median release year of unique "
        "tracks. Listening span: newest minus oldest release year."),
    "listening age": (("age", "birth year"),
        "Listening age: median era - 16 = estimated birth year (the age music taste forms); listening age = "
        "this year - that. Example: median 1994 -> born 1978 -> 48."),
    "playlist growth": (("growth", "added", "single session", "playlist age", "first added"),
        "Playlist growth: tracks added per month; a playlist whose tracks all share one date was built in a "
        "single session. Playlist age: when its first track was added."),
    "genres": (("genre", "tags", "untagged", "musicbrainz"),
        "Genres come from the primary artist (Spotify's tags, or MusicBrainz's when Spotify has none), not from "
        "the track. collection_stats group_by=\"genre\" counts EXACT tags (a track is counted under each tag of "
        "its artist). The dashboards' genre list counts sub-genres too: \"rock\" includes \"indie rock\". "
        "Tracks without any tag are \"untagged\"."),
    "genre families": (("family", "families"), ""),   # filled from GENRE_FAMILIES below
    "genre diversity": (("diversity",),
        "Genre diversity: distinct genre tags among a playlist's artists."),
    "duplicates": (("duplicate", "different versions", "versions", "remaster"),
        "Exact duplicate: the same Spotify ID saved more than once (inside one playlist or across playlists). "
        "Different versions: the same song and primary artist under different IDs (album vs. compilation, "
        "remaster, live...). Version suffixes are ignored when comparing names, so \"Song\", \"Song - Remastered "
        "2009\", \"Song - Live\" and \"Song (Acoustic Version)\" match; remixes count as different songs."),
    "replaced versions": (("replaced", "relink", "unavailable", "current version"),
        "Replaced versions: album versions Spotify swapped for a newer release; they show popularity 0 and "
        "Spotify plays the current version instead. Unavailable: not playable in the user's country, with no "
        "replacement. find_replaced_tracks lists both; replace_versions swaps the saved IDs for the current ones."),
    "organized": (("done", "progress", "organize"),
        "Organized (done): tracks already placed in a playlist built with Tailorlist (written from a draft group) "
        "or assigned by hand. They leave the pool of the next grouping and count toward organize_progress's %."),
}
TERMS["genre families"] = (TERMS["genre families"][0], "Genre families: " + _families())


def explain(term: str | None = None) -> str:
    """The definitions matching term (a term, an alias or a part of one); all of them for "all"; the
    list of terms when nothing matches."""
    asked = (term or "").strip().casefold()
    if asked in ("all", "everything", "*"):
        return "\n\n".join(text for _, text in TERMS.values())
    if asked:
        hits = [text for name, (aliases, text) in TERMS.items()
                if any(asked in k or k in asked for k in (name, *aliases))]
        if hits:
            return "\n\n".join(dict.fromkeys(hits))
    return ("Terms: " + ", ".join(TERMS) + '. Call explain with one of them, or "all".'
            + (f" Nothing matched '{term}'." if asked else ""))
