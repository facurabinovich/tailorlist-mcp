"""fun_facts on small hand-made caches (always a temp database, never the real cache)."""
import pytest

from tailorlist_mcp import facts, store
from tailorlist_mcp.queries import QueryError


def _row(tid, name=None, artist="a1", album=None, date="2005-01-01", pop=50, dur=180000, added="2024-01-01T10:00:00Z"):
    return {
        "track": {"id": tid, "name": name or f"Song {tid}", "album": album or f"Album {tid}", "album_id": f"al{tid}",
                  "release_date": date, "duration_ms": dur, "explicit": 0, "popularity": pop, "isrc": None,
                  "url": f"https://open.spotify.com/track/{tid}"},
        "artists": [(artist, artist.upper())],
        "added_at": added,
    }


def _save(c, n, name, rows, pid=None):
    pid = pid or str(n) * 22
    meta = {"id": pid, "name": name, "owner": {"id": "me"}, "snapshot_id": "s", "followers": None,
            "external_urls": {"spotify": f"https://open.spotify.com/playlist/{pid}"}}
    store.save_playlist(c, meta, rows, 0)


@pytest.fixture
def empty(tmp_path):
    c = store.connect(tmp_path / "cache.db")
    yield c
    c.close()


@pytest.fixture
def lib(empty):
    """Three playlists. a1 is the artist (6 songs), Big Hit is in all three, most songs are from the 2000s."""
    c = empty
    hit = _row("hit", "Big Hit", "a1", "Greatest", "2005-05-05", pop=90, added="2015-03-01T10:00:00Z")
    one = [hit] + [_row(f"a{i}", artist="a1", album="Long Album", date="2004-06-01", pop=40,
                        added="2020-05-%02dT10:00:00Z" % (i + 1)) for i in range(5)]
    one += [_row("old", "Ancient", "a2", date="1965-01-01", pop=5, dur=900000, added="2012-01-01T10:00:00Z"),
            _row("new", "Fresh", "a3", date="2024-02-01", pop=0, dur=30000, added="2020-05-20T10:00:00Z")]
    two = [hit] + [_row(f"b{i}", artist=f"b{i}", date="1990-01-01", pop=60 + i, added="2020-05-10T10:00:00Z") for i in range(6)]
    three = [hit, _row("c1", artist="c1", date="2005-01-01", added="2018-07-01T10:00:00Z"),
             _row("c2", artist="c2", date="2007-01-01", added="2019-07-01T10:00:00Z")]
    _save(c, 1, "One", one)
    _save(c, 2, "Two", two)
    _save(c, 3, "Three", three)
    store.save_artist_details(c, [{"id": "a1", "genres": ["rock"], "popularity": 1}, {"id": "a2", "genres": ["jazz"], "popularity": 1},
                                  {"id": "b0", "genres": ["indie pop"], "popularity": 1}, {"id": "b1", "genres": ["rock"], "popularity": 1},
                                  {"id": "b2", "genres": ["salsa"], "popularity": 1}, {"id": "b3", "genres": ["rock"], "popularity": 1}])
    c.commit()
    return c


def _titles(text):
    return [line.split(": ", 1)[0].split(". ", 1)[1] for line in text.splitlines() if line[:1].isdigit() and ". " in line[:4]]


def _ctx(conn, named=False):
    return facts._context(facts.dashboard.records(conn), named, 2026)


def test_full_output(lib):
    text = facts.fun_facts(lib, current_year=2026)
    print(text)
    assert text.startswith("Fun facts: 3 playlists, 16 unique tracks.")
    assert "chart at most one per answer" in text and "explain('chart style')" in text
    assert "   More: " in text and "   Chart: " in text
    assert "1. " in text


def test_your_decade(lib):
    f = facts.your_decade(_ctx(lib))
    assert f.text == "Half of your library is from the 2000s: 8 of 16 songs."
    assert f.tool == 'collection_stats(group_by="decade")'


def test_decade_absent_when_spread_out(empty):
    _save(empty, 1, "P", [_row(f"t{i}", artist=f"x{i}", date=f"{1950 + i * 10}-01-01") for i in range(7)])
    assert facts.your_decade(_ctx(empty)) is None


def test_listening_age_uses_the_existing_definition(lib):
    f = facts.listening_age(_ctx(lib))
    assert "listening age at" in f.text and f.tool == 'explain("listening age")'


def test_your_artist(lib):
    f = facts.your_artist(_ctx(lib))
    assert "A1 is your most-saved artist: 6 songs" in f.text and "across 1 playlist" not in f.text
    assert "across 3 playlists" in f.text          # Big Hit puts a1 in Two and Three as well


def test_your_artist_absent_below_three(empty):
    _save(empty, 1, "P", [_row("t1", artist="x"), _row("t2", artist="x"), _row("t3", artist="y")])
    assert facts.your_artist(_ctx(empty)) is None


def test_long_tail(empty):
    rows = [_row(f"t{i}", artist=f"x{i}") for i in range(12)] + [_row("u1", artist="x0"), _row("u2", artist="x1")]
    _save(empty, 1, "P", rows)
    assert "10 of 12 artists" in facts.long_tail(_ctx(empty)).text


def test_long_tail_absent_for_few_artists(empty):
    _save(empty, 1, "P", [_row(f"t{i}", artist=f"x{i}") for i in range(5)])
    assert facts.long_tail(_ctx(empty)) is None


def test_oldest_and_newest(lib):
    f = facts.oldest_and_newest(_ctx(lib))
    assert '"Ancient"' in f.text and '"Fresh"' in f.text and "59 years" in f.text


def test_oldest_and_newest_absent_for_one_year(empty):
    _save(empty, 1, "P", [_row(f"t{i}") for i in range(5)])
    assert facts.oldest_and_newest(_ctx(empty)) is None


def test_hours_of_music(empty):
    _save(empty, 1, "P", [_row(f"t{i}", dur=3_600_000 * 5) for i in range(11)])       # 55 hours
    f = facts.hours_of_music(_ctx(empty))
    assert "55 hours" in f.text and "2 days and 7 hours" in f.text and f.chart is None


def test_hours_absent_under_an_hour(empty):
    _save(empty, 1, "P", [_row("t1", dur=60000)])
    assert facts.hours_of_music(_ctx(empty)) is None


def test_longest_and_shortest(lib):
    f = facts.longest_and_shortest(_ctx(lib))
    assert '"Ancient"' in f.text and "15:00" in f.text and '"Fresh"' in f.text and "0:30" in f.text


def test_longest_absent_when_all_equal(empty):
    _save(empty, 1, "P", [_row(f"t{i}") for i in range(6)])
    assert facts.longest_and_shortest(_ctx(empty)) is None


def test_mainstream_skips_popularity_zero_for_the_rarest(lib):
    f = facts.mainstream_meter(_ctx(lib))
    assert '"Big Hit"' in f.text and "(90)" in f.text
    assert '"Ancient"' in f.text and "(5)" in f.text and '"Fresh"' not in f.text


def test_mainstream_absent_without_popularity(empty):
    _save(empty, 1, "P", [_row(f"t{i}", pop=None) for i in range(6)])
    assert facts.mainstream_meter(_ctx(empty)) is None


def test_busiest_month(lib):
    f = facts.busiest_month(_ctx(lib))
    assert "May 2020" in f.text and "of everything you ever added" in f.text


def test_busiest_month_absent_with_one_month(empty):
    _save(empty, 1, "P", [_row(f"t{i}") for i in range(6)])
    assert facts.busiest_month(_ctx(empty)) is None


def test_loyal_since(lib):
    f = facts.loyal_since(_ctx(lib))
    assert '"Ancient"' in f.text and "January 1, 2012" in f.text and "growing for 8 years" in f.text


def test_loyal_since_absent_inside_a_year(empty):
    _save(empty, 1, "P", [_row("t1", added="2024-01-01T00:00:00Z"), _row("t2", added="2024-03-01T00:00:00Z")])
    assert facts.loyal_since(_ctx(empty)) is None


def test_everywhere_song(lib):
    f = facts.everywhere_song(_ctx(lib))
    assert '"Big Hit"' in f.text and "in 3 of your 3 playlists" in f.text


def test_everywhere_needs_three_playlists(lib):
    lib.execute("DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = 'hit'", ("3" * 22,))
    lib.commit()
    assert facts.everywhere_song(_ctx(lib)) is None


def test_everywhere_ignores_liked_unless_named(lib):
    _save(lib, 4, "Liked Songs", [_row("hit", "Big Hit", "a1", "Greatest", "2005-05-05")], pid=store.LIKED_ID)
    lib.execute("DELETE FROM playlist_tracks WHERE playlist_id = ? AND track_id = 'hit'", ("3" * 22,))
    lib.commit()
    assert facts.everywhere_song(_ctx(lib)) is None          # One + Two only: Liked does not count
    assert facts.everywhere_song(_ctx(lib, named=True)) is not None


def test_genre_mix(lib):
    f = facts.genre_mix(_ctx(lib))
    assert f.text.startswith("Rock leads") and "families are in the mix" in f.text


def test_genre_mix_absent_without_genres(empty):
    _save(empty, 1, "P", [_row(f"t{i}") for i in range(6)])
    assert facts.genre_mix(_ctx(empty)) is None


def test_time_traveller(lib):
    f = facts.time_traveller(_ctx(lib))
    assert '"One" is your time traveller: it goes from 1965 to 2024, 59 years' in f.text
    assert 'most focused is "Three"' not in f.text or "(2005 to 2007)" in f.text


def test_time_traveller_needs_two_playlists(empty):
    _save(empty, 1, "P", [_row("t1", date="1960-01-01"), _row("t2", date="2020-01-01")])
    assert facts.time_traveller(_ctx(empty)) is None


def test_one_album_love(lib):
    f = facts.one_album_love(_ctx(lib))
    assert '"Long Album" by A1' in f.text and "5 songs" in f.text
    assert f.tool == 'find_tracks(filters={"album": "Long Album"})'


def test_one_album_absent_for_singles(empty):
    _save(empty, 1, "P", [_row(f"t{i}") for i in range(6)])
    assert facts.one_album_love(_ctx(empty)) is None


def test_no_fact_crashes_without_dates_years_genres_or_popularity(empty):
    rows = [_row(f"t{i}", artist=f"x{i % 3}", date=None, pop=None, added=None) for i in range(8)]
    _save(empty, 1, "P", rows)
    _save(empty, 2, "Q", rows[:4])
    c = _ctx(empty)
    for fn in facts.FACTS:
        fn(c)
    assert facts.fun_facts(empty).startswith("Fun facts: 2 playlists")


def test_limit_and_cap(lib):
    assert len(_titles(facts.fun_facts(lib, limit=3))) == 3
    assert len(_titles(facts.fun_facts(lib, limit=99))) <= facts.MAX_LIMIT
    assert len(_titles(facts.fun_facts(lib, limit=0))) >= 1


def test_strong_facts_come_first(lib):
    flags = [f.strong for f in facts.collect(_ctx(lib))]
    assert flags == sorted(flags, reverse=True)


def test_playlists_restriction(lib):
    text = facts.fun_facts(lib, playlists=["Three"])
    assert text.startswith("Fun facts: 1 playlist, 3 unique tracks.")
    assert "time traveller" not in text


def test_empty_cache(empty):
    with pytest.raises(QueryError, match="Nothing loaded yet"):
        facts.fun_facts(empty)


def test_the_tool_reads_the_cache_it_is_given(tmp_path, monkeypatch):
    from tailorlist_mcp import server
    path = tmp_path / "cache.db"
    c = store.connect(path)
    _save(c, 1, "P", [_row(f"t{i}", artist=f"x{i}") for i in range(3)])
    c.close()
    real = store.connect
    monkeypatch.setattr(server.store, "connect", lambda *a, **k: real(path))
    assert server.fun_facts(limit=2).startswith("Fun facts: 1 playlist, 3 unique tracks.")
