"""The menu of everything Tailorlist does, and what is worth doing now in the user's library.

MENU is the single source: the `start` prompt and the what_can_i_do tool are both rendered from it,
so they can't drift apart. suggestions() reads the cache only (no Spotify, no network): each entry
of CHECKS is a small function (conn, recs) -> one line or None, highest priority first. To add a
suggestion, write the function and append it to CHECKS.
"""
from __future__ import annotations

import sqlite3
from typing import Callable

from tailorlist_mcp import dashboard, health, orphans, progress, store

MAX_SUGGESTIONS = 3

#: (section title, [(line the user reads, tools behind it)])
MENU: list[tuple[str, list[tuple[str, tuple[str, ...]]]]] = [
    ("Get started", [
        ("Connect your Spotify account", ("connect_spotify",)),
        ("See your playlists and choose which ones to load (or unload)", ("list_my_playlists", "load_playlists", "unload_playlists")),
        ("Read the tracks of one playlist", ("get_playlist_tracks",)),
    ]),
    ("Understand your collection", [
        ("Overview of everything: decades, genres, artists, popularity, how it grew", ("show_overview",)),
        ("Deep dive into one playlist", ("show_playlist",)),
        ("Compare playlists: overlap, era, diversity", ("compare_playlists",)),
        ("Ask anything: \"my most-saved artists\", \"80s tracks under 3 minutes\"...", ("collection_stats", "find_tracks")),
        ("What any number or term means", ("explain",)),
        ("A guided tour of your collection in charts", ("tour",)),
        ("Fun facts about your library", ("fun_facts",)),
    ]),
    ("Clean it up", [
        ("Health check: everything that is off, in one report", ("library_health",)),
        ("Duplicates and the same song saved in different versions", ("find_duplicates",)),
        ("Old album versions Spotify replaced, and tracks that no longer play", ("find_replaced_tracks", "replace_versions")),
        ("Fill missing genres (MusicBrainz)", ("fill_genres",)),
        ("Date songs by their original release, not the compilation's (MusicBrainz)", ("fill_years",)),
        ("Fix a wrong year or genre by hand, e.g. after checking doubtful years on the web", ("set_year", "set_genre")),
        ("Liked Songs that are in no playlist, with a suggested playlist for each", ("find_orphans",)),
    ]),
    ("Build better playlists", [
        ("Group your tracks by decade, genre family or specific genres into drafts", ("group_tracks",)),
        ("Group by mood or occasion (\"for running\", \"Sunday morning\"): Claude places the tracks, you review", ("assign_tracks",)),
        ("Review and tweak the drafts together, then save them to Spotify", ("show_draft", "edit_draft", "write_draft", "create_playlist")),
        ("Add, remove, sort or de-duplicate tracks in existing playlists (always previewed, undoable)",
         ("add_tracks", "remove_tracks", "sort_playlist", "dedupe_playlist")),
        ("Merge several playlists without duplicates, or split a big one by decade or genre (as drafts)",
         ("merge_playlists", "split_playlist")),
        ("Track how much of your library is already organized", ("organize_progress", "reset_progress")),
    ]),
    ("Take it with you", [
        ("Export a track list as text, links or CSV", ("export_tracks",)),
        ("Undo the last change made to Spotify", ("undo_last_write",)),
        ("Forget my data: delete the local copy and optionally log out", ("forget_my_data",)),
    ]),
]

START_INTRO = """\
Introduce yourself in one line as the user's music curator for their Spotify library, then
offer what you can do as a short list they can pick from:
"""
START_OUTRO = """\
If no playlists are loaded yet, say that the first step is choosing which ones to load,
and offer to list them.
"""


def menu_text() -> str:
    return "\n\n".join(title + "\n" + "\n".join(f" • {line}" for line, _ in items) for title, items in MENU)


def menu_tools() -> set[str]:
    return {tool for _, items in MENU for _, tools in items for tool in tools}


def start_prompt() -> str:
    return f"{START_INTRO}\n{menu_text()}\n\n{START_OUTRO}"


def _n(count: int, one: str, many: str) -> str:
    return f"{count:,} {one if count == 1 else many}"


def _duplicates_inside(conn, recs):
    count = health.exact_inside_playlist(dashboard.without_liked(recs))[0]
    if count:
        return (f"{_n(count, 'song is', 'songs are')} saved twice inside the same playlist: "
                "dedupe_playlist cleans them (previewed, undoable).")


def _replaced(conn, recs):
    count = health.replaced_versions(conn, recs)[0]
    if count:
        return (f"{_n(count, 'song has', 'songs have')} an album version Spotify replaced: "
                "find_replaced_tracks lists them and replace_versions swaps them (previewed, undoable).")


def _near_identical(conn, recs):
    count = health.near_identical_playlists(recs)[0]
    if count:
        return f"{_n(count, 'pair of playlists is', 'pairs of playlists are')} nearly identical: compare_playlists shows which ones."


def _genres(conn, recs):
    count = health.genres_not_looked_up(conn)[0]
    if count:
        return f"{_n(count, 'artist has', 'artists have')} no genres yet: fill_genres looks them up in MusicBrainz."


def _years(conn, recs):
    count = health.years_not_dated(conn)[0]
    if count:
        return f"{_n(count, 'song is', 'songs are')} not dated by original release yet: fill_years fixes compilation years."


def _liked_not_loaded(conn, recs):
    if not conn.execute("SELECT 1 FROM playlists WHERE id = ?", (store.LIKED_ID,)).fetchone():
        return "Liked Songs are not loaded: load_playlists can load them, then find_orphans can show the ones in no playlist."


def _orphans(conn, recs):
    n = orphans.count(recs)
    if n:
        return f"{_n(n, 'Liked Song is', 'Liked Songs are')} in no loaded playlist: find_orphans suggests a playlist for each."


def _doubtful_years(conn, recs):
    n = health.doubtful_years(recs)[0]
    if n:
        return (f"{_n(n, 'song has', 'songs have')} a doubtful year (compilation albums, ISRC-only matches): "
                'find_tracks(filters={"doubtful_year": true}) lists them and set_year fixes them.')


def _organized(conn, recs):
    s = progress.summary(conn)
    if 0 < s["percent"] < 100:
        return f"{s['percent']:g}% of your library is organized: group_tracks builds drafts from the rest."


#: Highest priority first. Append a function here to add a suggestion.
CHECKS: list[Callable[[sqlite3.Connection, list[dict]], str | None]] = [
    _duplicates_inside, _replaced, _near_identical, _orphans, _genres, _years, _doubtful_years,
    _liked_not_loaded, _organized,
]


def suggestions(conn: sqlite3.Connection) -> list[str]:
    recs = dashboard.records(conn)
    if not recs:
        return []
    out = []
    for check in CHECKS:
        line = check(conn, recs)
        if line:
            out.append(line)
            if len(out) == MAX_SUGGESTIONS:
                break
    return out


def status_line(conn: sqlite3.Connection) -> str:
    playlists = conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
    tracks = conn.execute("SELECT COUNT(DISTINCT track_id) FROM playlist_tracks").fetchone()[0]
    if not playlists:
        return ("Nothing is loaded yet. The first step is choosing playlists: list_my_playlists shows them "
                "(if they are not connected to Spotify yet, connect_spotify comes first).")
    return f"Loaded: {_n(playlists, 'playlist', 'playlists')}, {_n(tracks, 'unique track', 'unique tracks')}."


def what_can_i_do(conn: sqlite3.Connection) -> str:
    out = [status_line(conn)]
    found = suggestions(conn)
    if found:
        out += ["", "Worth doing now in your library:"] + [f"{i}. {s}" for i, s in enumerate(found, 1)]
    out += ["", "Everything Tailorlist can do:", "", menu_text(), "",
            "Show this to the user as a short list in their language, lead with what is worth doing now, "
            "and ask what they want to do."]
    return "\n".join(out)
