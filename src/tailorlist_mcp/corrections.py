"""set_year and set_genre: the user's corrections win over MusicBrainz and Spotify.

A year is stored per song (the same (artist, song) key as fill_years) in `original_years` with source
'manual', so every saved version of the song gets it, in both directions (earlier or later than the
album). Genres are stored per artist in `artists.manual_genres`; store.genres_with_source is the one
place that ranks manual > Spotify > MusicBrainz.
"""
from __future__ import annotations

import datetime
import json
import re
import sqlite3
import time

from tailorlist_mcp import store, years
from tailorlist_mcp.analysis.duplicates import other_performance
from tailorlist_mcp.analysis.genres import assign_family
from tailorlist_mcp.export import track_id_from
from tailorlist_mcp.queries import QueryError

MIN_YEAR = 1860
MAX_CANDIDATES = 10
_ARTIST_RE = re.compile(r"^(?:https?://open\.spotify\.com/(?:intl-[a-z]+/)?artist/|spotify:artist:)?([A-Za-z0-9]{22})(?:[?/].*)?$")
_WHERE = {"album": "album", "musicbrainz": "MusicBrainz", "isrc": "MusicBrainz, by ISRC", "manual": "manual"}

_TRACKS_SQL = ("SELECT t.id, t.name, t.album, t.release_date, t.manual_year, a.name AS artist FROM tracks t "
               "JOIN track_artists ta ON ta.track_id = t.id AND ta.position = 0 JOIN artists a ON a.id = ta.artist_id")


# ---------------------------------------------------------------- finding the song

def _candidates(rows: list[sqlite3.Row]) -> str:
    shown = [f"{r['name']} - {r['artist']} ({r['id']})" for r in rows[:MAX_CANDIDATES]]
    return "; ".join(shown) + (f"; ... {len(rows) - MAX_CANDIDATES} more" if len(rows) > MAX_CANDIDATES else "")


def find_track(conn: sqlite3.Connection, ref: str, artist: str | None = None) -> sqlite3.Row:
    """One cached track from an ID, URI or link, or from its name (artist narrows it).

    A name that matches several different songs is an error listing them, so Claude can ask."""
    rows = conn.execute(_TRACKS_SQL + " ORDER BY t.name, a.name").fetchall()
    tid = track_id_from(ref)
    if tid:
        for r in rows:
            if r["id"] == tid:
                return r
        if len(ref.strip()) != 22:   # a link or URI: it names a track, so say it isn't cached
            raise QueryError(f"Track {tid} isn't in the cache. Use a track from a loaded playlist.")
    q = ref.strip().casefold()
    if not q:
        raise QueryError("Say which track: a Spotify track ID, URI or link, or its name.")
    named = [r for r in rows if r["name"].casefold() == q] or [r for r in rows if q in r["name"].casefold()]
    if artist and artist.strip():
        a = artist.strip().casefold()
        named = [r for r in named if r["artist"].casefold() == a] or [r for r in named if a in r["artist"].casefold()]
    if not named:
        raise QueryError(f"No track in the cache matches '{ref}'" + (f" by '{artist}'" if artist else "")
                         + ". Use find_tracks to get its ID.")
    songs: dict = {}
    for r in named:
        songs.setdefault(years.song_of(r["name"], r["artist"]) or r["id"], []).append(r)
    if len(songs) > 1:
        firsts = [group[0] for group in songs.values()]
        raise QueryError(f"'{ref}' matches {len(songs)} different songs: {_candidates(firsts)}. "
                         "Ask which one, then call again with its track ID.")
    return next(iter(songs.values()))[0]


# ---------------------------------------------------------------- set_year

def set_year(conn: sqlite3.Connection, track: str, year: int | None, source: str = "", artist: str | None = None,
             now: int | None = None) -> str:
    row = find_track(conn, track, artist)
    label = f"{row['name']} - {row['artist']}"
    take = other_performance(row["name"], row["album"])
    key = years.song_of(row["name"], row["artist"])
    if not key and not take:
        raise QueryError(f"{label} has no title or artist to key a year on.")
    same = [row] if take else [r for r in conn.execute(_TRACKS_SQL)
                               if years.song_of(r["name"], r["artist"]) == key and not other_performance(r["name"], r["album"])]
    known, sources = years.known_years(conn), years.year_sources(conn)
    date, origin = years.dating(known, row["name"], row["artist"], row["album"], row["release_date"], sources, row["manual_year"])
    old = date or str(years.album_year(row["release_date"]) or "")
    was = f"{old or 'no year'} ({_WHERE[origin]})"

    if not year:
        if take:   # this recording only
            if not row["manual_year"]:
                return f"{label} has no manual year to remove (it is {was})."
            with conn:
                conn.execute("UPDATE tracks SET manual_year = NULL, manual_year_note = NULL WHERE id = ?", (row["id"],))
            return (f"Removed the manual year for this {label} take (it was {old}); it is back to its album year "
                    f"({years.album_year(row['release_date']) or 'none'}). Only this recording was changed.")
        if not years.remove_manual(conn, key):
            return f"{label} has no manual year to remove (it is {was})."
        return (f"Removed the manual year for {label} (it was {old}). fill_years will look the song up again; until it "
                f"does, the album year ({years.album_year(row['release_date']) or 'none'}) stands. "
                f"Applies to {len(same)} tracks in the cache.")
    now_year = datetime.date.today().year
    if not MIN_YEAR <= int(year) <= now_year + 1:
        raise QueryError(f"Year {year} is out of range: use a year between {MIN_YEAR} and {now_year + 1}.")
    note = (source or "").strip()
    if take:
        with conn:
            conn.execute("UPDATE tracks SET manual_year = ?, manual_year_note = ? WHERE id = ?", (int(year), note or None, row["id"]))
        return (f"{label}: was {was} -> now {int(year)} ({note or 'manual'}). Live, demo or acoustic take: only this "
                "recording (1 track) was changed; the studio version keeps its own year.")
    years.save(conn, key, int(year), int(now if now is not None else time.time()), "manual", note or None)
    return (f"{label}: was {was} -> now {int(year)} ({note or 'manual'}). Applies to {len(same)} tracks in the cache "
            "(every saved version of the song), earlier or later than their album. It wins over MusicBrainz, "
            "and fill_years never overwrites it.")


# ---------------------------------------------------------------- set_genre

def find_artist(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    """A cached artist by Spotify ID or link, exact (casefolded) name, else a unique part of a name."""
    rows = conn.execute("SELECT id, name, genres, mb_genres, manual_genres FROM artists ORDER BY name").fetchall()
    m = _ARTIST_RE.match(ref.strip())
    if m:
        for r in rows:
            if r["id"] == m.group(1):
                return r
    q = ref.strip().casefold()
    if not q:
        raise QueryError("Say which artist: a name in the cache or a Spotify artist ID or link.")
    exact = [r for r in rows if r["name"].casefold() == q]
    found = exact or [r for r in rows if q in r["name"].casefold()]
    if len(found) == 1:
        return found[0]
    if not found:
        raise QueryError(f"No artist in the cache matches '{ref}'.")
    raise QueryError(f"'{ref}' matches {len(found)} artists: "
                     + "; ".join(f"{r['name']} ({r['id']})" for r in found[:MAX_CANDIDATES])
                     + ". Ask which one, then call again with its ID.")


def _clean(genres: list[str]) -> list[str]:
    """Lower-cased, stripped, no blanks or repeats, order kept."""
    return list(dict.fromkeys(t for g in genres or [] if (t := str(g).strip().lower())))


def _shown(tags: list[str], source: str | None) -> str:
    return f"{', '.join(tags)} ({source})" if tags else "none"


def set_genre(conn: sqlite3.Connection, artist: str, genres: list[str], source: str = "") -> str:
    row = find_artist(conn, artist)
    tracks = conn.execute("SELECT COUNT(*) FROM track_artists WHERE artist_id = ? AND position = 0", (row["id"],)).fetchone()[0]
    old_tags, old_source = store.genres_with_source(row["genres"], row["mb_genres"], row["manual_genres"])
    old = _shown(old_tags, old_source)
    new = _clean(genres)
    note = (source or "").strip()
    with conn:
        conn.execute("UPDATE artists SET manual_genres = ?, manual_source = ? WHERE id = ?",
                     (json.dumps(new) if new else None, (note or None) if new else None, row["id"]))
    if not new:
        back, back_source = store.genres_with_source(row["genres"], row["mb_genres"], None)
        return (f"{row['name']}: removed the manual genres (was {old}). Back to {_shown(back, back_source)}. "
                f"Primary artist of {tracks} tracks in the cache.")
    other = [t for t in new if assign_family(t) == ["Other"]]
    out = (f"{row['name']}: was {old} -> now {_shown(new, note or 'manual')}. Primary artist of {tracks} tracks in the cache. "
           f"'{new[0]}' maps to the genre family {', '.join(assign_family(new[0]))}.")
    if other:
        out += f" No family for: {', '.join(other)} (they count as Other in genre families)."
    return out
