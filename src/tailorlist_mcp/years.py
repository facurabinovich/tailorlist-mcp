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

import sqlite3

from tailorlist_mcp.analysis.duplicates import base_title, other_performance
from tailorlist_mcp.genre_fill import BackgroundFill
from tailorlist_mcp.sources.musicbrainz import loose_title

JOB = "year_fill"


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
    return {(r["artist"], r["song"]): r for r in conn.execute("SELECT artist, song, year, checked_at FROM original_years")}


def known_years(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    return {(r["artist"], r["song"]): r["year"] for r in conn.execute(
        "SELECT artist, song, year FROM original_years WHERE year IS NOT NULL")}


def original_date(known: dict, name: str, artist: str, album: str | None, release_date: str | None) -> str | None:
    """The year to date a track by when MusicBrainz knows an earlier one than its album, else None."""
    if other_performance(name, album):
        return None
    key = song_of(name, artist)
    year = known.get(key) if key else None
    own = album_year(release_date)
    return str(year) if year and (own is None or year < own) else None


def save(conn: sqlite3.Connection, key: tuple[str, str], year: int | None, now: int) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO original_years (artist, song, year, checked_at) VALUES (?, ?, ?, ?)",
                     (key[0], key[1], year, now))


class YearFill(BackgroundFill):
    JOB = JOB
    WHAT = "songs"
    SECONDS_PER_ITEM = 1.6          # one search per song (two when the ISRC fallback runs) at 1 per second

    def _pending(self, conn, now):
        checked = _checked(conn)
        songs = _songs(conn)
        pending = [(k, s) for k, s in songs.items()
                   if k not in checked or (checked[k]["year"] is None and checked[k]["checked_at"] < now - self.RETRY_AFTER)]
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
        year = client.first_release_year(song["title"], song["artist"], song["isrc"])
        save(conn, key, year, now)
        return year is not None


def moved_tracks(conn: sqlite3.Connection) -> int:
    """Tracks now dated earlier than their album (the effect of the fill so far)."""
    known = known_years(conn)
    if not known:
        return 0
    n = 0
    for r in conn.execute(
        "SELECT t.name, t.album, t.release_date, a.name AS artist FROM tracks t "
        "JOIN track_artists ta ON ta.track_id = t.id AND ta.position = 0 JOIN artists a ON a.id = ta.artist_id"
    ):
        n += original_date(known, r["name"], r["artist"], r["album"], r["release_date"]) is not None
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
