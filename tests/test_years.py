"""Original release years: the MusicBrainz lookup (mocked HTTP), the background fill, and how the
years reach the records, stats and find_tracks."""
import pytest

from tailorlist_mcp import dashboard, queries, store, years
from tailorlist_mcp.analysis.duplicates import base_title, other_performance
from tailorlist_mcp.sources.musicbrainz import MusicBrainzError, loose_title
from test_musicbrainz import Clock, FakeResponse, FakeSession, client, clock  # noqa: F401 (fixture)

PL = "Y" * 22


def _rec(title, artist, date, disambiguation=""):
    return {"title": title, "first-release-date": date, "disambiguation": disambiguation,
            "artist-credit": [{"name": artist, "artist": {"name": artist}}]}


def test_first_release_year_takes_the_earliest_matching_recording(clock):
    body = {"recordings": [
        _rec("Hey Jude", "The Beatles", "2015-11-06"),                 # the 2015 remix, its own recording
        _rec("Hey Jude", "The Beatles", "1968-08-26"),
        _rec("Hey Jude", "The Beatles", "1964", "live, BBC"),           # marked live: skipped
        _rec("Hey Jude", "Wilson Pickett", "1960"),                     # another artist: skipped
        _rec("Hey Judy", "The Beatles", "1950"),                        # another title: skipped
    ]}
    session = FakeSession({"recording": FakeResponse(200, body)})
    assert client(session, clock).first_release_year("Hey Jude", "The Beatles") == 1968
    assert session.calls[0][1]["query"] == 'recording:"Hey Jude" AND artist:"The Beatles"'


def test_first_release_year_matches_titles_on_letters_and_falls_back_to_isrc(clock):
    session = FakeSession({"recording": FakeResponse(200, {"recordings": [_rec("Baby, You’re a Rich Man", "The Beatles", "1967-07-07")]})})
    assert client(session, clock).first_release_year("Baby You're A Rich Man", "The Beatles") == 1967
    session = FakeSession({"recording": FakeResponse(200, {"recordings": []}),
                           "isrc/GB123": FakeResponse(200, {"recordings": [{"first-release-date": "1994-03-01"}]})})
    assert client(session, clock).first_release_year("Life's a Bitch", "Nas", "GB123") == 1994


def test_titles_and_takes():
    assert base_title("Life's a Bitch (feat. AZ & Olu Dara)") == "Life's a Bitch"
    assert base_title("Hey Jude - Remastered 2015") == "Hey Jude"
    assert loose_title("Baby, You’re a Rich Man") == loose_title("Baby You're A Rich Man")
    assert other_performance("Heart of Gold - Live") and other_performance("Layla", "MTV Unplugged")
    assert not other_performance("Live and Let Die") and not other_performance("Hey Jude - Remastered 2015")


# ---------------------------------------------------------------- the fill and its effect

TRACKS = [  # id, name, artist, album, album date
    ("t1", "Hey Jude - Remastered 2015", "The Beatles", "1 (Remastered 2015)", "2000-11-13"),
    ("t2", "Hey Jude", "The Beatles", "Hey Jude", "1968-08-26"),          # same song: one lookup
    ("t3", "Heart of Gold - Live", "Neil Young", "Live at Massey Hall", "2007-03-13"),   # live: not looked up
    ("t4", "New Song", "Someone", "New Album", "2004-01-01"),            # MusicBrainz says later: ignored
    ("t5", "Lost Song", "Nobody", "Hits", "2010-01-01"),                 # not found
]


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "cache.db")
    rows = [{"track": {"id": t, "name": n, "album": al, "album_id": "al" + t, "release_date": d, "duration_ms": 1000,
                       "explicit": 0, "popularity": 50, "isrc": None, "url": "u"},
             "artists": [("ar_" + a, a)], "added_at": "2024-01-01T00:00:00Z"} for t, n, a, al, d in TRACKS]
    store.save_playlist(c, {"id": PL, "name": "Mix", "owner": {"id": "me"}, "snapshot_id": "s"}, rows, 0)
    yield c
    c.close()


class FakeMB:
    def __init__(self, answers, fail=()):
        self.answers, self.fail, self.asked = answers, set(fail), []

    def first_release_year(self, title, artist, isrc=None):
        self.asked.append((title, artist))
        if title in self.fail:
            raise MusicBrainzError("503")
        return self.answers.get(title)


def _fill(fake):
    clock = Clock()
    return years.YearFill(client_factory=lambda: fake, clock=clock.time, run_in_thread=False), clock


def test_fill_asks_once_per_song_and_skips_live_takes(conn):
    fake = FakeMB({"Hey Jude": 1968, "New Song": 2020})
    fill, _ = _fill(fake)
    status = fill.start(conn)
    assert fake.asked[0] == ("Hey Jude", "The Beatles")           # the most-saved song first
    assert sorted(fake.asked) == [("Hey Jude", "The Beatles"), ("Lost Song", "Nobody"), ("New Song", "Someone")]
    assert (status["total"], status["checked"], status["found"], status["pending"]) == (3, 3, 2, 0)
    assert "Done. 3 of 3 songs checked: 2 found" in years.describe(status, years.moved_tracks(conn))
    assert years.moved_tracks(conn) == 1                            # only t1 moves (t4 would move later)
    fake.asked.clear()
    fill.start(conn)
    assert fake.asked == []                                         # cached, nothing asked again


def test_a_failing_song_stays_pending(conn):
    fill, _ = _fill(FakeMB({"Hey Jude": 1968}, fail={"Lost Song"}))
    status = fill.start(conn)
    assert status["pending"] == 1 and status["checked"] == 2


def test_records_stats_and_find_tracks_use_the_original_year(conn):
    fill, _ = _fill(FakeMB({"Hey Jude": 1968, "Heart of Gold": 1972, "New Song": 2020}))
    fill.start(conn)
    recs = {r["id"]: r for r in dashboard.records(conn)}
    assert recs["t1"]["release_date"] == "1968" and recs["t1"]["album_release_date"] == "2000-11-13"
    assert recs["t2"]["release_date"] == "1968-08-26"               # album already 1968: unchanged
    assert recs["t3"]["release_date"] == "2007-03-13"               # live take keeps its album year
    assert recs["t4"]["release_date"] == "2004-01-01"               # never moved later
    out = queries.find_tracks(conn, sort="year_gap")
    assert out.startswith("1 unique tracks match") and "| Album year |" in out
    assert "Hey Jude - Remastered 2015 | THE BEATLES | 1 (Remastered 2015) | 1968 | 2000 |" in out.replace("The Beatles", "THE BEATLES")
    stats = queries.collection_stats(conn, "decade")
    assert "1960" in stats and "2000s" not in stats.split("\n")[2]
