"""set_year, set_genre, the doubtful-year definition and filter (temp databases and a fake MusicBrainz only)."""
import sqlite3

import pytest

from tailorlist_mcp import corrections, dashboard, drafts, genre_fill, health, queries, store, years
from tailorlist_mcp.queries import QueryError
from test_musicbrainz import Clock, FakeResponse, FakeSession, client, clock  # noqa: F401 (fixture)
from test_years import FakeMB

PL = "Z" * 22


def I(name):  # a 22-character Spotify-looking ID
    return (name.replace("_", "") + "x" * 22)[:22]


def _t(tid, name, artist, album, date, isrc=None):
    return {"track": {"id": tid, "name": name, "album": album, "album_id": "al" + tid, "release_date": date, "duration_ms": 1000,
                      "explicit": 0, "popularity": 50, "isrc": isrc, "url": "u"},
            "artists": [(I("ar_" + artist.replace(" ", "")), artist)], "added_at": "2024-01-01T00:00:00Z"}


ROWS = [
    (I("tid_neville_1"), "Tell It Like It Is", "Aaron Neville", "Tell It Like It Is", "1967-01-01"),
    (I("tid_neville_2"), "Tell It Like It Is - Remastered", "Aaron Neville", "The Very Best Of", "2005-01-01"),
    (I("tid_neville_live"), "Tell It Like It Is - Live", "Aaron Neville", "Live Show", "1999-01-01"),
    (I("tid_comp"), "Old Hit", "Band One", "Greatest Hits", "2010-01-01"),
    (I("tid_isrc"), "Isrc Song", "Band Two", "Plain Album", "2010-01-01"),
    (I("tid_plain"), "Plain Song", "Band Three", "Plain Album", "2010-01-01"),
    (I("tid_dup1"), "Hello", "Adele", "25", "2015-01-01"),
    (I("tid_dup2"), "Hello", "Lionel Richie", "Can't Slow Down", "1983-01-01"),
]


def _save(c, snapshot="s"):
    store.save_playlist(c, {"id": PL, "name": "Mix", "owner": {"id": "me"}, "snapshot_id": snapshot}, [_t(*r) for r in ROWS], 0)


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "cache.db")
    _save(c)
    yield c
    c.close()


def _rec(conn, tid):
    return next(r for r in dashboard.records(conn) if r["id"] == tid)


def test_old_schema_database_migrates(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript("CREATE TABLE original_years (artist TEXT NOT NULL, song TEXT NOT NULL, year INTEGER, checked_at INTEGER NOT NULL, "
                      "PRIMARY KEY (artist, song));"
                      "CREATE TABLE artists (id TEXT PRIMARY KEY, name TEXT NOT NULL, genres TEXT, popularity INTEGER);"
                      "CREATE TABLE tracks (id TEXT PRIMARY KEY, name TEXT NOT NULL);"
                      "INSERT INTO original_years VALUES ('a', 's', 1970, 1);")
    raw.commit()
    raw.close()
    c = store.connect(path)

    def cols(t):
        return {r[1] for r in c.execute(f"PRAGMA table_info({t})")}

    assert {"source", "note"} <= cols("original_years")
    assert {"manual_genres", "manual_source"} <= cols("artists") and {"manual_year", "manual_year_note"} <= cols("tracks")
    assert tuple(c.execute("SELECT year, source FROM original_years").fetchone()) == (1970, None)   # old row: source NULL
    c.close()


def test_first_release_reports_the_path(clock):
    body = {"recordings": [{"title": "X", "first-release-date": "1990", "disambiguation": "",
                            "artist-credit": [{"name": "A", "artist": {"name": "A"}}]}]}
    assert client(FakeSession({"recording": FakeResponse(200, body)}), clock).first_release("X", "A") == (1990, "search")
    s = FakeSession({"recording": FakeResponse(200, {"recordings": []}),
                     "isrc/I1": FakeResponse(200, {"recordings": [{"first-release-date": "1994"}]})})
    assert client(s, clock).first_release("X", "A", "I1") == (1994, "isrc")
    assert client(FakeSession({"recording": FakeResponse(200, {"recordings": []})}), clock).first_release("X", "A") == (None, None)


def test_manual_year_earlier_and_later_than_the_album_applies_to_every_version(conn):
    out = corrections.set_year(conn, "Tell It Like It Is", 1966, "Wikipedia", artist="Aaron Neville")
    assert "was 1967 (album) -> now 1966 (Wikipedia)" in out and "2 tracks" in out      # the live take is not counted
    assert _rec(conn, I("tid_neville_2"))["release_date"] == "1966" and _rec(conn, I("tid_neville_2"))["year_source"] == "manual"
    assert _rec(conn, I("tid_neville_1"))["year_source"] == "manual"
    assert tuple(conn.execute("SELECT source, note FROM original_years").fetchone()) == ("manual", "Wikipedia")
    out = corrections.set_year(conn, I("tid_neville_2"), 1990)           # later than the album: allowed for manual years
    assert "was 1966 (manual) -> now 1990 (manual)" in out
    assert _rec(conn, I("tid_neville_1"))["release_date"] == "1990"
    assert _rec(conn, I("tid_neville_live"))["release_date"] == "1999-01-01"      # the live take keeps its album year
    assert years.moved_tracks(conn) == 2


def test_manual_year_removal_requeues_the_song(conn):
    corrections.set_year(conn, I("tid_plain"), 1980)
    assert _rec(conn, I("tid_plain"))["release_date"] == "1980"
    out = corrections.set_year(conn, I("tid_plain"), None)
    assert "Removed" in out and "fill_years will look the song up again" in out
    assert conn.execute("SELECT COUNT(*) FROM original_years").fetchone()[0] == 0
    assert _rec(conn, I("tid_plain"))["release_date"] == "2010-01-01"
    assert "no manual year" in corrections.set_year(conn, I("tid_plain"), 0)


def test_year_validation_and_ambiguity(conn):
    with pytest.raises(QueryError, match="between 1860"):
        corrections.set_year(conn, I("tid_plain"), 1500)
    with pytest.raises(QueryError, match="between 1860"):
        corrections.set_year(conn, I("tid_plain"), 3000)
    with pytest.raises(QueryError) as e:
        corrections.set_year(conn, "Hello", 1980)
    assert I("tid_dup1") in str(e.value) and I("tid_dup2") in str(e.value) and "Adele" in str(e.value)
    assert "Lionel" in corrections.set_year(conn, "Hello", 1983, artist="Lionel")
    with pytest.raises(QueryError, match="isn't in the cache"):
        corrections.set_year(conn, "https://open.spotify.com/track/" + "q" * 22, 1980)
    with pytest.raises(QueryError, match="No track"):
        corrections.set_year(conn, "Nothing like this", 1980)


def test_live_take_gets_its_own_year_only(conn):
    out = corrections.set_year(conn, I("tid_neville_live"), 1964, "user")
    assert "only this recording" in out and "studio version keeps its own year" in out
    live = _rec(conn, I("tid_neville_live"))
    assert live["release_date"] == "1964" and live["year_source"] == "manual" and live["album_release_date"] == "1999-01-01"
    assert _rec(conn, I("tid_neville_1"))["year_source"] == "album"              # studio untouched
    assert conn.execute("SELECT COUNT(*) FROM original_years").fetchone()[0] == 0
    corrections.set_year(conn, I("tid_neville_live"), 2020)                      # later than the album
    assert _rec(conn, I("tid_neville_live"))["release_date"] == "2020"
    assert years.moved_tracks(conn) == 1
    _save(conn, "s2")                                                         # the playlist is reloaded
    assert _rec(conn, I("tid_neville_live"))["release_date"] == "2020"
    assert "Removed" in corrections.set_year(conn, I("tid_neville_live"), None)
    assert _rec(conn, I("tid_neville_live"))["release_date"] == "1999-01-01"


def test_fill_never_overwrites_or_requeues_manual_rows(conn):
    corrections.set_year(conn, I("tid_plain"), 1980, "Wikipedia")
    fake = FakeMB({"Plain Song": 1990, "Old Hit": 1975})
    years.YearFill(client_factory=lambda: fake, clock=Clock().time, run_in_thread=False).start(conn)
    assert ("Plain Song", "Band Three") not in fake.asked and ("Old Hit", "Band One") in fake.asked
    assert _rec(conn, I("tid_plain"))["release_date"] == "1980"
    years.save(conn, years.song_of("Plain Song", "Band Three"), 1990, 5, "search")     # even a direct save keeps it
    assert _rec(conn, I("tid_plain"))["release_date"] == "1980"
    assert conn.execute("SELECT note FROM original_years WHERE song = 'plainsong'").fetchone()[0] == "Wikipedia"


def test_fill_records_the_source(conn):
    class Isrc(FakeMB):
        def first_release(self, title, artist, isrc=None):
            return (1970, "isrc") if title == "Isrc Song" else (None, None)

    years.YearFill(client_factory=lambda: Isrc({}), clock=Clock().time, run_in_thread=False).start(conn)
    assert conn.execute("SELECT source FROM original_years WHERE song = 'isrcsong'").fetchone()[0] == "isrc"
    assert _rec(conn, I("tid_isrc"))["year_source"] == "isrc"


def test_doubtful_definition_and_filter(conn):
    years.save(conn, years.song_of("Isrc Song", "Band Two"), 1970, 1, "isrc")
    for tid, expected in [(I("tid_comp"), "compilation album"), (I("tid_isrc"), "ISRC only"), (I("tid_plain"), None),
                          (I("tid_neville_2"), "compilation album"), (I("tid_neville_live"), None)]:
        r = _rec(conn, tid)
        assert years.doubtful_reason(r["name"], r["album"], r["year_source"]) == expected, tid
    assert health.COMPILATION_PATTERN is years.COMPILATION_PATTERN
    count, examples = health.doubtful_years(dashboard.records(conn))
    assert count == 3 and any("ISRC only" in e for e in examples)
    out = queries.find_tracks(conn, filters=queries.Filters(doubtful_year=True))
    assert out.startswith("3 unique tracks match (doubtful_year)")
    header = out.splitlines()[1]
    assert "Album" in header and header.endswith("Why | Track ID")
    assert f"compilation album | {I('tid_comp')}" in out and f"ISRC only | {I('tid_isrc')}" in out and I("tid_plain") not in out
    corrections.set_year(conn, I("tid_comp"), 2001)                                 # manual: no longer doubtful
    assert health.doubtful_years(dashboard.records(conn))[0] == 2
    assert 'find_tracks(filters={"doubtful_year": true})' in health.library_health(conn)
    # the classes that mirror Filters still take it
    assert drafts.TrackSelector(doubtful_year=True).doubtful_year is True
    assert "No tracks match" not in queries.collection_stats(conn, "album", filters=queries.Filters(doubtful_year=True))


# ---------------------------------------------------------------- genres

def _genres(conn, tid):
    return _rec(conn, tid)["artist_genres"]


def test_manual_genres_win_over_spotify_and_musicbrainz_and_can_be_removed(conn):
    store.save_artist_details(conn, [{"id": I("ar_BandOne"), "genres": ["pop"], "popularity": 1}])
    store.save_mb_result(conn, I("ar_BandTwo"), ["jazz"], "search", 1)
    assert _genres(conn, I("tid_comp")) == "pop" and _genres(conn, I("tid_isrc")) == "jazz"
    out = corrections.set_genre(conn, "Band One", [" Dream Pop ", "dream pop", "Shoegaze", "zzz"], "Wikipedia")
    assert "was pop (Spotify) -> now dream pop, shoegaze, zzz (Wikipedia)" in out and "No family for: zzz" in out
    assert _genres(conn, I("tid_comp")) == "dream pop, shoegaze, zzz"
    out = corrections.set_genre(conn, I("ar_BandTwo"), ["Blues"])
    assert "was jazz (MusicBrainz)" in out and _genres(conn, I("tid_isrc")) == "blues"
    out = corrections.set_genre(conn, "Band One", [])
    assert "Back to pop (Spotify)" in out and _genres(conn, I("tid_comp")) == "pop"
    assert "removed" in corrections.set_genre(conn, "https://open.spotify.com/artist/" + I("ar_BandTwo"), [])   # by link
    assert _genres(conn, I("tid_isrc")) == "jazz"


def test_fill_genres_skips_artists_with_manual_genres(conn):
    store.save_artist_details(conn, [{"id": a, "genres": [], "popularity": 1} for a in (I("ar_BandOne"), I("ar_BandTwo"))])

    def ids():
        return {r["id"] for r in store.mb_candidates(conn, 10**9, genre_fill.RETRY_AFTER)}

    assert {I("ar_BandOne"), I("ar_BandTwo")} <= ids()
    total = store.mb_counts(conn, 10**9, genre_fill.RETRY_AFTER)["total"]
    corrections.set_genre(conn, "Band One", ["rock"])
    assert I("ar_BandOne") not in ids() and I("ar_BandTwo") in ids()
    assert store.mb_counts(conn, 10**9, genre_fill.RETRY_AFTER)["total"] == total - 1


def test_artist_lookup_errors(conn):
    with pytest.raises(QueryError, match="matches 3 artists"):
        corrections.set_genre(conn, "Band", ["rock"])
    with pytest.raises(QueryError, match="No artist"):
        corrections.set_genre(conn, "Nobody Here", ["rock"])
    assert "Band Three" in corrections.set_genre(conn, "three", ["rock"])          # a unique part
