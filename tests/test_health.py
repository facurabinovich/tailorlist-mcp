"""library_health on small hand-made caches (always a temp database, never the real cache)."""
import pytest

from tailorlist_mcp import health, queries, store, years


def _row(tid, name="Song", artist="a1", album=None, date="2000-01-01", pop=50):
    return {
        "track": {"id": tid, "name": name, "album": album or f"Album {tid}", "album_id": f"al{tid}", "release_date": date,
                  "duration_ms": 180000, "explicit": 0, "popularity": pop, "isrc": None,
                  "url": f"https://open.spotify.com/track/{tid}"},
        "artists": [(artist, artist.upper())],
        "added_at": "2024-01-01T10:00:00Z",
    }


def _save(c, n, name, rows, skipped=0):
    pid = str(n) * 22
    meta = {"id": pid, "name": name, "owner": {"id": "me"}, "snapshot_id": "s", "followers": None,
            "external_urls": {"spotify": f"https://open.spotify.com/playlist/{pid}"}}
    store.save_playlist(c, meta, rows, skipped)


@pytest.fixture
def empty(tmp_path):
    c = store.connect(tmp_path / "cache.db")
    yield c
    c.close()


@pytest.fixture
def conn(empty):
    c = empty
    _save(c, 1, "One", [_row("t1", "Alpha"), _row("t1", "Alpha"), _row("t2", "Beta"), _row("t3", "Gamma", "a2")], skipped=3)
    _save(c, 2, "Two", [_row("t2", "Beta"), _row("t4", "Alpha - Remastered", album="Other"), _row("t5", "Delta", "a2")])
    store.save_artist_details(c, [{"id": "a1", "genres": ["rock"], "popularity": 1}, {"id": "a2", "genres": [], "popularity": 1}])
    c.execute("UPDATE tracks SET popularity = 0, current_id = 'new', current_popularity = 40 WHERE id = 't3'")
    c.execute("UPDATE tracks SET popularity = 0, unavailable = 1 WHERE id = 't5'")
    c.commit()
    return c


def test_exact_duplicates_inside_and_across(conn):
    recs = health.dashboard.records(conn)
    assert health.exact_inside_playlist(recs) == (1, ["Alpha - A1 (x2)"])
    assert health.exact_across_playlists(recs) == (1, ["Beta - A1 (2 playlists)"])


def test_duplicate_counts_match_find_duplicates(conn):
    recs = health.dashboard.records(conn)
    inside, across = health.exact_inside_playlist(recs)[0], health.exact_across_playlists(recs)[0]
    text = queries.find_duplicates(conn)
    assert f"Exact duplicates: {inside + across} Spotify tracks" in text    # no track here is both inside and across
    assert f"Different versions: {health.different_versions(recs)[0]} songs" in text


def test_different_versions(conn):
    assert health.different_versions(health.dashboard.records(conn)) == (1, ["Alpha - A1 (2 versions)"])


def test_replaced_unavailable_and_skipped(conn):
    recs = health.dashboard.records(conn)
    assert health.replaced_versions(conn, recs) == (1, ["Gamma - A2"])
    assert health.unavailable_tracks(conn, recs) == (1, ["Delta - A2"])
    assert health.skipped_items(conn, {"One", "Two"}) == (3, ["One (3)"])
    assert health.skipped_items(conn, {"Two"}) == (0, [])


def test_genres_and_years_use_the_fill_definitions(conn):
    import time
    assert health.genres_not_looked_up(conn) == (1, [])          # a2 has no Spotify genres, never asked
    assert health.genres_not_found(conn) == (0, [])
    store.save_mb_result(conn, "a2", [], "search", int(time.time()))
    assert health.genres_not_looked_up(conn) == (0, [])
    assert health.genres_not_found(conn) == (1, [])
    assert health.years_not_dated(conn)[0] == years.FILL._counts(conn, int(time.time()))["pending"] > 0
    for key in years._songs(conn):
        years.save(conn, key, None, int(time.time()))
    assert health.years_not_dated(conn) == (0, [])


def test_doubtful_years_skips_compilations_with_an_earlier_original_year(empty):
    c = empty
    _save(c, 1, "One", [_row("c1", "Old Hit", "a1", album="The Very Best Of", date="2005-01-01"),
                        _row("c2", "Known Hit", "a1", album="Greatest Hits", date="2005-01-01"),
                        _row("c3", "Plain", "a1", album="Regular Album", date="2005-01-01")])
    years.save(c, years.song_of("Known Hit", "a1"), 1980, 1)       # dated by its original year: no longer doubtful
    count, examples = health.doubtful_years(health.dashboard.records(c))
    assert (count, examples) == (1, ["Old Hit - A1 (The Very Best Of, 2005)"])


def test_near_identical_thresholds(empty):
    c = empty
    ids = [f"x{i}" for i in range(10)]
    _save(c, 1, "Big", [_row(i) for i in ids] + [_row(f"y{i}") for i in range(10)])
    _save(c, 2, "Copy", [_row(i) for i in ids[:8]] + [_row("z1"), _row("z2")])      # 8 of 10 = 80% of the smaller
    _save(c, 3, "Less", [_row(i) for i in ids[:7]] + [_row("w1"), _row("w2"), _row("w3")])  # 70%
    _save(c, 4, "Tiny", [_row(i) for i in ids[:9]])      # 100% of itself but only 9 tracks
    count, examples = health.near_identical_playlists(health.dashboard.records(c))
    assert count == 1 and examples == ["Big (20) / Copy (10): 8 shared (80% of the smaller)"]


def test_report_blocks_and_no_issues_line(conn):
    text = health.library_health(conn)
    lines = text.splitlines()
    assert lines[0] == "Library health: 2 playlists, 5 unique tracks."
    assert "Exact duplicates inside a playlist (same track saved twice): 1" in text
    assert "  Full list: find_duplicates(kind=\"exact\")" in lines
    assert any(l.startswith("Skipped items") and l.endswith(": 3") for l in lines)
    assert any(l.startswith("No issues:") and "near-identical playlists" in l for l in lines)
    assert "No issues" not in "\n".join(lines[:-1])
    assert len(lines) < 60


def test_all_clear_collapses_to_one_line(empty):
    _save(empty, 1, "One", [_row("t1", "Alpha"), _row("t2", "Beta")])
    store.save_artist_details(empty, [{"id": "a1", "genres": ["rock"], "popularity": 1}])
    import time
    for key in years._songs(empty):
        years.save(empty, key, None, int(time.time()))
    lines = health.library_health(empty).splitlines()
    assert len(lines) == 2 and lines[1].startswith("No issues: exact duplicates inside a playlist;")


def test_playlist_subset(conn):
    text = health.library_health(conn, ["Two"])
    assert text.startswith("Library health: 1 playlists, 3 unique tracks.")
    assert "Exact duplicates inside" not in text.split("No issues")[0]
    assert "(library-wide, not only these playlists)" in text
    assert "Near-identical" not in text                  # skipped with a single playlist
    assert "Not playable in your country: 1" not in text and "Tracks not playable in your country: 1" in text
    assert "Replaced album versions" not in text.split("No issues")[0]


def test_empty_cache(empty):
    with pytest.raises(queries.QueryError, match="Nothing loaded yet"):
        health.library_health(empty)


def test_liked_songs_is_not_a_duplicate_unless_named(empty):
    rows = [_row(f"{i:022d}", name=f"S{i}") for i in range(3)]
    _save(empty, 1, "Mix", rows)
    store.save_playlist(empty, {"id": store.LIKED_ID, "name": store.LIKED_NAME, "snapshot_id": "s"}, rows[:2], 0)
    assert "exact duplicates across playlists" in health.library_health(empty).splitlines()[-1]
    assert "Exact duplicates: 0 " in queries.find_duplicates(empty, kind="exact")
    assert "Liked Songs is left out" in queries.find_duplicates(empty, kind="exact")
    named = queries.find_duplicates(empty, ["Mix", "liked"], kind="exact")
    assert "Exact duplicates: 2 " in named and "left out" not in named
    assert "Exact duplicates across playlists" in health.library_health(empty, ["Mix", "liked"])


def test_gold_in_an_album_title_is_not_a_compilation(empty):
    _save(empty, 1, "Mix", [_row("a" * 22, album="Mellow Gold"), _row("b" * 22, album="Greatest Hits")])
    count, examples = health.doubtful_years(health.dashboard.records(empty))
    assert count == 1 and "Greatest Hits" in examples[0]
