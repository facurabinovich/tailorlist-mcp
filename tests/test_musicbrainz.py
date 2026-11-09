"""MusicBrainz client (mocked HTTP, injected clock and sleep) and the background fill job."""
import os
import sqlite3
import threading

import pytest

from tailorlist_mcp import __version__, genre_fill, store
from tailorlist_mcp.sources import musicbrainz as mb
from tailorlist_mcp.sources.musicbrainz import MusicBrainzClient, MusicBrainzError, Throttle


class FakeResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


class FakeSession:
    """Answers by URL path; a list means "one answer per call"."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, headers=None, timeout=None):
        path = url.removeprefix(mb.API + "/")
        self.calls.append((path, dict(params), headers))
        answer = self.routes.get(path)
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer or FakeResponse(404)


class Clock:
    """Fake time: sleeping advances it, so throttling is testable without waiting."""

    def __init__(self):
        self.now, self.slept = 100.0, []

    def time(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


@pytest.fixture
def clock():
    return Clock()


def client(session, clock):
    return MusicBrainzClient(session, Throttle(1.1, clock.time, clock.sleep), clock.sleep)


ARTIST = {"genres": [{"name": "Trip Hop", "count": 2}, {"name": "rock", "count": 9}], "tags": [{"name": "seen live", "count": 1}]}


def test_user_agent_is_the_project_url_without_any_email():
    assert mb.USER_AGENT == f"tailorlist-mcp/{__version__} ( https://app.tailorlist.app )"
    assert "@" not in mb.USER_AGENT


def test_throttle_spaces_requests_at_least_one_interval_apart(clock):
    t = Throttle(1.1, clock.time, clock.sleep)
    t.wait()
    t.wait()
    t.wait()
    assert clock.slept == pytest.approx([1.1, 1.1])
    clock.now += 5
    t.wait()  # long gap: no sleeping needed
    assert len(clock.slept) == 2


def test_lookup_by_spotify_url_then_genres_most_voted_first(clock):
    s = FakeSession({
        "url": FakeResponse(body={"relations": [{"type": "free streaming", "artist": {"id": "mbid1", "name": "X"}}]}),
        "artist/mbid1": FakeResponse(body=ARTIST),
    })
    found = client(s, clock).lookup("sp1", "X")
    assert (found.mbid, found.genres, found.source) == ("mbid1", ["rock", "trip hop"], "url")
    path, params, headers = s.calls[0]
    assert params["resource"] == "https://open.spotify.com/artist/sp1" and params["inc"] == "artist-rels"
    assert headers["User-Agent"] == mb.USER_AGENT
    assert [c[0] for c in s.calls] == ["url", "artist/mbid1"]


def test_name_search_accepts_only_score_100_exact_names(clock):
    s = FakeSession({
        "url": FakeResponse(404),
        "artist": FakeResponse(body={"artists": [
            {"id": "near", "name": "Gorillaz Tribute", "score": 100},
            {"id": "low", "name": "gorillaz", "score": 90},
            {"id": "good", "name": "GORILLAZ", "score": 100},
        ]}),
        "artist/good": FakeResponse(body=ARTIST),
    })
    found = client(s, clock).lookup("sp1", "Gorillaz")
    assert (found.mbid, found.source) == ("good", "search")
    assert s.calls[1][1]["query"] == 'artist:"Gorillaz"'

    s = FakeSession({"url": FakeResponse(404), "artist": FakeResponse(body={"artists": [{"id": "n", "name": "Other", "score": 100}]})})
    assert client(s, clock).lookup("sp1", "Gorillaz") is None


def test_name_search_skips_namesakes(clock):
    s = FakeSession({"url": FakeResponse(404), "artist": FakeResponse(body={"artists": [
        {"id": "a", "name": "Nirvana", "score": 100},
        {"id": "b", "name": "Nirvana", "score": 100},
    ]})})
    assert client(s, clock).lookup("sp1", "Nirvana") is None


def test_search_query_escapes_quotes(clock):
    s = FakeSession({"url": FakeResponse(404), "artist": FakeResponse(body={"artists": []})})
    client(s, clock).lookup("sp", 'Say "Hi"')
    assert s.calls[1][1]["query"] == 'artist:"Say \\"Hi\\""'


def test_free_form_tags_are_never_used_as_genres(clock):
    tags = {"tags": [{"name": "2008 universal fire victim", "count": 3}, {"name": "christmas", "count": 2}]}
    s = FakeSession({"url": FakeResponse(body={"relations": [{"artist": {"id": "m"}}]}), "artist/m": FakeResponse(body=tags)})
    assert client(s, clock).lookup("sp", "X") is None
    assert s.calls[-1][1]["inc"] == "genres"
    empty = FakeSession({"url": FakeResponse(body={"relations": [{"artist": {"id": "m"}}]}), "artist/m": FakeResponse(body={})})
    assert client(empty, clock).lookup("sp", "X") is None


def test_503_backs_off_and_retries_then_gives_up(clock):
    ok = FakeResponse(body={"relations": []})
    s = FakeSession({"url": [FakeResponse(503), FakeResponse(503, headers={"Retry-After": "7"}), ok]})
    assert client(s, clock).artist_by_spotify_id("sp") is None
    assert len(s.calls) == 3
    assert 2 in clock.slept and 7.0 in clock.slept          # exponential default, then Retry-After
    s = FakeSession({"url": FakeResponse(503)})
    with pytest.raises(MusicBrainzError, match="503"):
        client(s, clock).artist_by_spotify_id("sp")
    assert len(s.calls) == 5


def test_network_errors_are_retried_too(clock):
    import requests
    s = FakeSession({"url": [requests.ConnectionError("boom"), FakeResponse(body={"relations": []})]})
    assert client(s, clock).artist_by_spotify_id("sp") is None


def test_every_request_goes_through_the_throttle(clock):
    s = FakeSession({"url": FakeResponse(404), "artist": FakeResponse(body={"artists": []})})
    client(s, clock).lookup("sp", "X")
    assert clock.slept == pytest.approx([1.1])               # two requests, one wait between them


# ---------------------------------------------------------------- the background job

def _artist_cache(tmp_path, names=("Ann", "Bob", "Cy", "Dee")):
    """4 primary artists with empty Spotify genres (Ann has the most tracks), 1 with genres."""
    conn = store.connect(tmp_path / "cache.db")
    rows = []
    plan = {"Ann": 3, "Bob": 2, "Cy": 1, "Dee": 1}
    for name, n in plan.items():
        for i in range(n):
            rows.append({"track": {"id": f"{name}{i}", "name": f"S{i}", "album": "A", "album_id": "a", "release_date": "2000",
                                   "duration_ms": 1000, "explicit": 0, "popularity": 1, "isrc": None, "url": None},
                         "artists": [(name.lower(), name)], "added_at": None})
    rows.append({"track": {"id": "g0", "name": "G", "album": "A", "album_id": "a", "release_date": "2000", "duration_ms": 1,
                           "explicit": 0, "popularity": 1, "isrc": None, "url": None},
                 "artists": [("withgenres", "Gen")], "added_at": None})
    rows.append({"track": {"id": "f0", "name": "F", "album": "A", "album_id": "a", "release_date": "2000", "duration_ms": 1,
                           "explicit": 0, "popularity": 1, "isrc": None, "url": None},
                 "artists": [("main", "Main"), ("featured", "Featured")], "added_at": None})
    store.save_playlist(conn, {"id": "p" * 22, "name": "P", "snapshot_id": "s"}, rows, 0)
    store.save_artist_details(conn, [{"id": n.lower(), "genres": []} for n in plan] + [
        {"id": "withgenres", "genres": ["jazz"]}, {"id": "main", "genres": ["pop"]}, {"id": "featured", "genres": []}])
    return conn


class FakeMB:
    def __init__(self, answers, fail=()):
        self.answers, self.fail, self.asked = answers, set(fail), []

    def lookup(self, spotify_id, name):
        self.asked.append(spotify_id)
        if spotify_id in self.fail:
            raise MusicBrainzError("503")
        genres = self.answers.get(spotify_id)
        return mb.Found("mbid-" + spotify_id, genres, "url") if genres else None


def _fill(fake, clock=None, run_in_thread=False):
    clock = clock or Clock()
    return genre_fill.GenreFill(client_factory=lambda: fake, clock=clock.time, run_in_thread=run_in_thread), clock


def test_fill_asks_only_primary_artists_without_spotify_genres_most_tracks_first(tmp_path):
    conn = _artist_cache(tmp_path)
    fake = FakeMB({"ann": ["rock"], "bob": ["pop", "funk"]})
    job, _ = _fill(fake)
    status = job.start(conn, tmp_path / "cache.db")
    assert fake.asked == ["ann", "bob", "cy", "dee"]                 # not 'featured' (never primary), not the ones with genres
    assert (status["total"], status["checked"], status["found"], status["pending"], status["running"]) == (4, 4, 2, 0, False)
    row = conn.execute("SELECT genres, mb_genres, mb_source FROM artists WHERE id = 'bob'").fetchone()
    assert (row["genres"], row["mb_genres"], row["mb_source"]) == ("[]", '["pop", "funk"]', "url")   # Spotify's column untouched
    assert conn.execute("SELECT mb_genres FROM artists WHERE id = 'cy'").fetchone()[0] == "[]"      # "no result" is cached too
    assert "2 got genres from MusicBrainz" in genre_fill.describe(status)


def test_resume_skips_what_was_already_asked_and_retries_failures(tmp_path):
    conn = _artist_cache(tmp_path)
    fake = FakeMB({"ann": ["rock"], "bob": ["pop"], "cy": ["jazz"]}, fail={"bob"})
    job, _ = _fill(fake)
    status = job.start(conn, tmp_path / "cache.db")
    assert fake.asked == ["ann", "bob", "cy", "dee"]
    assert status["pending"] == 1 and status["checked"] == 3          # bob failed: not saved, still pending
    assert "Call fill_genres to resume" in genre_fill.describe(status)

    again = FakeMB({"bob": ["pop"]})
    job2, _ = _fill(again)
    status = job2.start(conn, tmp_path / "cache.db")
    assert again.asked == ["bob"]                                     # only the pending one; cy "dee" "ann" not asked again
    assert (status["pending"], status["found"]) == (0, 3)


def test_three_failures_in_a_row_stop_the_run_with_a_reason(tmp_path):
    conn = _artist_cache(tmp_path)
    fake = FakeMB({}, fail={"ann", "bob", "cy", "dee"})
    job, _ = _fill(fake)
    status = job.start(conn, tmp_path / "cache.db")
    assert fake.asked == ["ann", "bob", "cy"]
    assert "Stopped after 3 artists in a row failed" in status["error"]
    assert "Stopped after 3" in genre_fill.describe(status)


def test_no_result_is_asked_again_only_after_the_retry_window(tmp_path):
    conn = _artist_cache(tmp_path)
    job, clock = _fill(FakeMB({"ann": ["rock"]}))
    job.start(conn, tmp_path / "cache.db")
    clock.now += genre_fill.RETRY_AFTER + 10
    again = FakeMB({"cy": ["jazz"]})
    job2, _ = _fill(again, clock)
    job2.start(conn, tmp_path / "cache.db")
    assert again.asked == ["bob", "cy", "dee"]                        # Ann found genres: never again


def test_a_second_process_does_not_start_while_the_lock_is_fresh(tmp_path):
    conn = _artist_cache(tmp_path)
    clock = Clock()
    alive = f"{os.getpid()}-other"            # a live process (this one, under another id)
    assert store.acquire_job(conn, genre_fill.JOB, alive, int(clock.now), genre_fill.STALE_AFTER)
    fake = FakeMB({"ann": ["rock"]})
    job, _ = _fill(fake, clock)
    status = job.start(conn, tmp_path / "cache.db")
    assert fake.asked == [] and status["running"] and status["where"] == "another server process"
    assert "another server process" in genre_fill.describe(status)

    clock.now += genre_fill.STALE_AFTER + 1                           # its heartbeat stopped: taken over
    job.start(conn, tmp_path / "cache.db")
    assert fake.asked == ["ann", "bob", "cy", "dee"]


def test_runs_in_a_background_thread_and_start_returns_at_once(tmp_path):
    conn = _artist_cache(tmp_path)
    gate = threading.Event()

    class Slow(FakeMB):
        def lookup(self, spotify_id, name):
            gate.wait(5)
            return super().lookup(spotify_id, name)

    fake = Slow({"ann": ["rock"]})
    job, _ = _fill(fake, run_in_thread=True)
    status = job.start(conn, tmp_path / "cache.db")
    assert status["running"] and status["where"] == "this server process" and status["checked"] == 0
    assert job.start(conn, tmp_path / "cache.db")["running"]          # second call: progress only, no second run
    gate.set()
    job._thread.join(5)
    done = job.status(conn)
    assert (done["running"], done["checked"], done["pending"]) == (False, 4, 0)
    assert sorted(fake.asked) == ["ann", "bob", "cy", "dee"]          # each asked exactly once


def test_effective_genres_are_spotifys_else_musicbrainzs(tmp_path):
    from tailorlist_mcp import dashboard
    conn = _artist_cache(tmp_path)
    store.save_mb_result(conn, "ann", ["art rock"], "search", 1)
    store.save_mb_result(conn, "main", ["should be ignored"], "search", 1)   # Spotify has genres for it
    by_artist = {r["artists"]: r["artist_genres"] for r in dashboard.records(conn)}
    assert by_artist["Ann"] == "art rock" and by_artist["Main"] == "pop" and by_artist["Bob"] == ""


def test_old_caches_are_migrated_without_losing_data(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE playlists (id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_id TEXT, owner_name TEXT, snapshot_id TEXT,
                                url TEXT, skipped INTEGER NOT NULL DEFAULT 0, loaded_at INTEGER NOT NULL);
        CREATE TABLE artists (id TEXT PRIMARY KEY, name TEXT NOT NULL, genres TEXT, popularity INTEGER);
        INSERT INTO playlists VALUES ('p', 'Old', NULL, NULL, 's', NULL, 0, 1);
        INSERT INTO artists VALUES ('a', 'Ann', '[]', 3);
    """)
    old.commit()
    old.close()
    conn = store.connect(path)
    assert conn.execute("SELECT name, followers FROM playlists").fetchone()[:] == ("Old", None)
    assert conn.execute("SELECT name, genres, mb_genres, mb_checked_at FROM artists").fetchone()[:] == ("Ann", "[]", None, None)
    store.connect(path).close()          # migrating twice is harmless


def test_a_lock_left_by_a_killed_process_is_taken_over_at_once(tmp_path):
    conn = _artist_cache(tmp_path)
    clock = Clock()
    assert store.acquire_job(conn, genre_fill.JOB, "99999999-dead", int(clock.now), genre_fill.STALE_AFTER)
    fake = FakeMB({"ann": ["rock"]})
    job, _ = _fill(fake, clock)
    job.start(conn, tmp_path / "cache.db")
    assert fake.asked == ["ann", "bob", "cy", "dee"]       # heartbeat is fresh, but the owner pid doesn't exist
