"""fill_years: the year a song was first released, from MusicBrainz.

Spotify dates a track by its album, so "Hey Jude" saved from the compilation *1* counts as 2000.
This background job (same machinery as fill_genres: one request per second, resumable, one run at a
time across processes) asks MusicBrainz once per (primary artist, song) for the earliest release
and caches it in `original_years`. dashboard.records then dates each track by that year when it is
EARLIER than the album's (it never moves a track later), so every stat, filter and grouping by
year or decade uses it.

Live, demo and acoustic takes keep their album year: they are other recordings (decided with Facu
2026-11-10). See TODO.md, "Original release year".
"""
from __future__ import annotations

import re
import sqlite3

from tailorlist_mcp.analysis.duplicates import base_title, other_performance
from tailorlist_mcp.genre_fill import BackgroundFill
from tailorlist_mcp.sources.musicbrainz import loose_title

JOB = "year_fill"
#: Album titles that look like compilations (matched case-insensitively): their year is often the reissue's.
COMPILATION_PATTERN = re.compile(
    r"best of|greatest hits|the hits|collection|anthology|essential|compilation|very best|ultimate",
    re.IGNORECASE,
)


def song_of(name: str, artist: str) -> tuple[str, str] | None:
    """The cache key of a track: (casefolded primary artist, title on letters and digits)."""
    song = loose_title(base_title(name))
    who = (artist or "").strip().casefold()
    return (who, song) if song and who else None


def album_year(release_date: str | None) -> int | None:
    try:
        year = int((release_date or "")[:4])
    except ValueError:
        return None
    return year if year > 1900 else None


def _songs(conn: sqlite3.Connection) -> dict[tuple[str, str], dict]:
    """Every song to date: key -> {title, artist, isrc, tracks}. Live/demo/acoustic takes are left out."""
    out: dict[tuple[str, str], dict] = {}
    for r in conn.execute(
        "SELECT t.id, t.name, t.album, t.isrc, a.name AS artist FROM tracks t "
        "JOIN track_artists ta ON ta.track_id = t.id AND ta.position = 0 JOIN artists a ON a.id = ta.artist_id"
    ):
        if other_performance(r["name"], r["album"]):
            continue
        key = song_of(r["name"], r["artist"])
        if not key:
            continue
        s = out.setdefault(key, {"title": base_title(r["name"]), "artist": r["artist"], "isrc": r["isrc"], "tracks": 0})
        s["tracks"] += 1
    return out


def _checked(conn: sqlite3.Connection) -> dict[tuple[str, str], sqlite3.Row]:
    return {(r["artist"], r["song"]): r for r in conn.execute("SELECT artist, song, year, checked_at, source FROM original_years")}


def known_years(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """Every known year per song, MusicBrainz's and manual ones."""
    return {(r["artist"], r["song"]): r["year"] for r in conn.execute(
        "SELECT artist, song, year FROM original_years WHERE year IS NOT NULL")}


def year_sources(conn: sqlite3.Connection) -> dict[tuple[str, str], str | None]:
    """Where each known year came from: 'search', 'isrc', 'manual', or None for rows from before it was recorded."""
    return {(r["artist"], r["song"]): r["source"] for r in conn.execute(
        "SELECT artist, song, source FROM original_years WHERE year IS NOT NULL")}


def dating(known: dict, name: str, artist: str, album: str | None, release_date: str | None,
           sources: dict | None = None, track_year: int | None = None) -> tuple[str | None, str]:
    """(the year to date a track by, where it comes from); (None, "album") when the album's year stands.

    A manual year wins over the album year in both directions. MusicBrainz's year (source 'search' or
    'isrc') only counts when it is EARLIER than the album's. Live/demo/acoustic takes keep their album year
    unless they have their own manual year (track_year, from set_year on that recording), which wins both ways."""
    if track_year:
        return str(track_year), "manual"
    if other_performance(name, album):
        return None, "album"
    key = song_of(name, artist)
    year = known.get(key) if key else None
    if not year:
        return None, "album"
    source = (sources or {}).get(key)
    if source == "manual":
        return str(year), "manual"
    own = album_year(release_date)
    if own is None or year < own:
        return str(year), "isrc" if source == "isrc" else "musicbrainz"
    return None, "album"


def original_date(known: dict, name: str, artist: str, album: str | None, release_date: str | None,
                  sources: dict | None = None, track_year: int | None = None) -> str | None:
    """The year to date a track by when a known one beats its album's, else None (see dating)."""
    return dating(known, name, artist, album, release_date, sources, track_year)[0]


def doubtful_reason(name: str, album: str | None, year_source: str | None) -> str | None:
    """Why a track's year is doubtful, or None. The one definition (health, find_tracks).

    Doubtful: not a live/demo/acoustic take, no manual year, and either its album looks like a compilation
    while the track still has the album's year ("compilation album"), or its year was found only through
    the ISRC fallback ("ISRC only")."""
    if other_performance(name, album) or year_source == "manual":
        return None
    if year_source == "isrc":
        return "ISRC only"
    if year_source == "album" and album and COMPILATION_PATTERN.search(album):
        return "compilation album"
    return None


def save(conn: sqlite3.Connection, key: tuple[str, str], year: int | None, now: int,
         source: str | None = None, note: str | None = None) -> None:
    """Store a year for a song. A manual row is only ever replaced by another manual one (the fill must not undo set_year)."""
    with conn:
        conn.execute("INSERT INTO original_years (artist, song, year, checked_at, source, note) VALUES (?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(artist, song) DO UPDATE SET year = excluded.year, checked_at = excluded.checked_at, "
                     "source = excluded.source, note = excluded.note "
                     "WHERE original_years.source IS NOT 'manual' OR excluded.source = 'manual'",
                     (key[0], key[1], year, now, source, note))


def remove_manual(conn: sqlite3.Connection, key: tuple[str, str]) -> bool:
    """Delete a manual year so fill_years looks the song up again. False when there was none."""
    with conn:
        return conn.execute("DELETE FROM original_years WHERE artist = ? AND song = ? AND source = 'manual'", key).rowcount > 0


class YearFill(BackgroundFill):
    JOB = JOB
    WHAT = "songs"
    SECONDS_PER_ITEM = 1.6          # one search per song (two when the ISRC fallback runs) at 1 per second

    def _pending(self, conn, now):
        checked = _checked(conn)
        songs = _songs(conn)
        pending = [(k, s) for k, s in songs.items()
                   if (k not in checked or (checked[k]["year"] is None and checked[k]["checked_at"] < now - self.RETRY_AFTER))
                   and not (k in checked and checked[k]["source"] == "manual")]
        return songs, checked, pending

    def _counts(self, conn, now):
        songs, checked, pending = self._pending(conn, now)
        done = [k for k in songs if k in checked]
        return {"total": len(songs), "checked": len(done), "found": sum(1 for k in done if checked[k]["year"] is not None),
                "pending": len(pending)}

    def _candidates(self, conn, now):
        _, _, pending = self._pending(conn, now)
        return sorted(pending, key=lambda ks: (-ks[1]["tracks"], ks[1]["artist"], ks[1]["title"]))

    def _process(self, client, conn, row, now):
        key, song = row
        year, source = client.first_release(song["title"], song["artist"], song["isrc"])
        save(conn, key, year, now, source)
        return year is not None


def moved_tracks(conn: sqlite3.Connection) -> int:
    """Tracks now dated differently from their album (the fill's effect so far, plus manual years)."""
    known = known_years(conn)
    sources = year_sources(conn)
    n = 0
    for r in conn.execute(
        "SELECT t.name, t.album, t.release_date, t.manual_year, a.name AS artist FROM tracks t "
        "JOIN track_artists ta ON ta.track_id = t.id AND ta.position = 0 JOIN artists a ON a.id = ta.artist_id"
    ):
        date = original_date(known, r["name"], r["artist"], r["album"], r["release_date"], sources, r["manual_year"])
        n += date is not None and int(date) != album_year(r["release_date"])
    return n


def describe(status: dict, moved: int) -> str:
    """Text for Claude. Never claims anything is on screen."""
    total, found, checked = status["total"], status["found"], status["checked"]
    if not total:
        return "Nothing to date: no tracks loaded (live, demo and acoustic takes keep their album year)."
    effect = (f" {moved} tracks are now dated by their original release instead of their album (compilations, "
              "reissues); every stat and grouping by year or decade uses it.") if moved else ""
    if status["running"]:
        minutes = max(1, round((status["seconds_left"] or 0) / 60))
        return (f"Finding original release years in MusicBrainz in the background ({status['where']}): {checked} of "
                f"{total} songs checked, {found} found. About {minutes} min left (MusicBrainz allows 1 request per "
                f"second). Call fill_years again to see progress; the other tools use the years as they arrive.{effect}")
    if status["pending"]:
        return (f"Not running. {checked} of {total} songs checked, {found} found, {status['pending']} left."
                + (f" The last run stopped early: {status['error']}" if status["error"] else "")
                + f" Call fill_years to resume.{effect}")
    return (f"Done. {checked} of {total} songs checked: {found} found in MusicBrainz, {checked - found} not found "
            f"(they keep their album year).{effect}")


#: The one fill of this server process.
FILL = YearFill()
