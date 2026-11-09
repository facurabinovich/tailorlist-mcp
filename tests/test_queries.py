"""collection_stats, find_tracks, find_duplicates, compare_playlists on a small hand-made cache."""
import pytest

from tailorlist_mcp import dashboard, queries, store
from tailorlist_mcp.queries import Filters

PL1, PL2, PL3 = "1" * 22, "2" * 22, "3" * 22

# (track id, name, [artist ids], release date, popularity, minutes, added_at)
P1 = [
    ("t1", "Alpha Song", ["a1"], "1994-05-01", 80, 3, "2024-01-10T10:00:00Z"),
    ("t2", "Beta Song", ["a1", "a2"], "1996", 60, 4, "2024-01-20T10:00:00Z"),
    ("t3", "Gamma", ["a2"], "2005-01-01", 40, 5, "2024-03-02T10:00:00Z"),
    ("t4", "Delta", ["a3"], "2021-02-02", 20, 2, "2024-03-05T10:00:00Z"),
]
P2 = [
    ("t2", "Beta Song", ["a1", "a2"], "1996", 60, 4, "2024-02-10T10:00:00Z"),   # also in P1
    ("t5", "Alpha Song", ["a1"], "2010-01-01", 50, 3, "2024-02-11T10:00:00Z"),  # other version of t1
    ("t5", "Alpha Song", ["a1"], "2010-01-01", 50, 3, "2024-02-12T10:00:00Z"),  # exact duplicate inside P2
]
P3 = [("t6", "Zeta", ["a3"], None, None, 1, None)]
GENRES = {"a1": ["rock", "indie rock"], "a2": ["pop"], "a3": []}  # a3: no genres


def _row(t):
    tid, name, artists, date, pop, minutes, added = t
    return {
        "track": {"id": tid, "name": name, "album": f"Album {tid}", "album_id": f"al{tid}", "release_date": date,
                  "duration_ms": minutes * 60000, "explicit": 0, "popularity": pop, "isrc": None,
                  "url": f"https://open.spotify.com/track/{tid}"},
        "artists": [(a, a.upper()) for a in artists],
        "added_at": added,
    }


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "cache.db")
    for pid, name, rows, followers in [(PL1, "One", P1, 5), (PL2, "Two", P2, 0), (PL3, "Three", P3, None)]:
        meta = {"id": pid, "name": name, "owner": {"id": "me"}, "snapshot_id": "s", "followers": {"total": followers} if followers is not None else None,
                "external_urls": {"spotify": f"https://open.spotify.com/playlist/{pid}"}}
        store.save_playlist(c, meta, [_row(t) for t in rows], 0)
    store.save_artist_details(c, [{"id": a, "genres": g, "popularity": 10} for a, g in GENRES.items()])
    yield c
    c.close()


def lines(text):
    return text.splitlines()


def test_stats_by_decade_counts_unique_tracks_and_shares(conn):
    out = lines(queries.collection_stats(conn, "decade"))
    assert out[0].startswith("unique_tracks, share by decade. Selection: 6 unique tracks")
    assert out[1] == "decade | unique_tracks | share_%"
    # t1 1994, t2 1996 -> 1990s; t3 2005 -> 2000s; t5 2010 and t4 2021; t6 has no year
    assert out[2:6] == ["1990s | 2 | 33.3", "2000s | 1 | 16.7", "2010s | 1 | 16.7", "2020s | 1 | 16.7"]
    assert "1 unique track has no release year and is left out of the groups." in out


def test_stats_metrics_entries_hours_and_popularity(conn):
    out = lines(queries.collection_stats(conn, "playlist", metrics=["entries", "unique_tracks", "hours", "avg_popularity"]))
    rows = {r.split(" | ")[0]: r.split(" | ")[1:] for r in out[2:5]}
    assert rows["Two"] == ["3", "2", "0.12", "55.0"]    # t5 twice: 3 entries, 2 unique tracks; 7 min = 0.12 h; (60+50)/2
    assert rows["One"] == ["4", "4", "0.23", "50.0"]


def test_stats_genre_is_exact_tags_and_family_uses_substrings(conn):
    genre = lines(queries.collection_stats(conn, "genre"))
    got = {r.split(" | ")[0]: r.split(" | ")[1] for r in genre[2:5]}
    assert got == {"rock": "3", "indie rock": "3", "pop": "1"}   # 'rock' is NOT counted for 'indie rock' alone
    fam = {r.split(" | ")[0]: r.split(" | ")[1] for r in lines(queries.collection_stats(conn, "genre_family"))[2:5]}
    assert fam["Rock"] == "3" and fam["Pop"] == "1" and fam["Other"] == "2"  # t4 and t6: no genres -> Other


def test_stats_split_and_filters(conn):
    out = lines(queries.collection_stats(conn, "decade", split_by="playlist", filters=Filters(playlists=["one", "two"])))
    assert out[1] == "decade | playlist | unique_tracks | share_%"
    rows = [r.split(" | ")[:3] for r in out[2:] if " | " in r and r[0].isdigit()]
    assert ["1990s", "One", "2"] in rows and ["1990s", "Two", "1"] in rows
    assert "filters: playlists=One, Two" in out[0]


def test_stats_month_added_and_popularity_bin(conn):
    out = lines(queries.collection_stats(conn, "month_added", metrics=["unique_tracks"]))
    assert out[2:5] == ["2024-01 | 2", "2024-02 | 2", "2024-03 | 2"]
    bins = lines(queries.collection_stats(conn, "popularity_bin", metrics=["unique_tracks"]))
    assert bins[2] == "20-24 | 1" and bins[-3].startswith("80-84 | 1")


def test_stats_limit_and_errors(conn):
    out = lines(queries.collection_stats(conn, "artist", limit=1))
    assert out[2].startswith("A1 | 3") and "Showing the 1 biggest of 3 artist groups" in "\n".join(out)
    with pytest.raises(queries.QueryError, match="Unknown group_by"):
        queries.collection_stats(conn, "color")
    with pytest.raises(queries.QueryError, match="different dimension"):
        queries.collection_stats(conn, "decade", split_by="decade")
    with pytest.raises(queries.QueryError, match="Unknown metrics"):
        queries.collection_stats(conn, "decade", metrics=["loudness"])


def test_filters(conn):
    def names(**kw):
        out = lines(queries.find_tracks(conn, Filters(**kw), sort="name"))
        return [r.split(" | ")[1] for r in out[2:]]

    assert names(artist="a2") == ["Beta Song", "Gamma"]            # primary or featured
    assert names(genre="indie rock") == ["Alpha Song", "Alpha Song", "Beta Song"]
    assert names(genre_family="pop") == ["Gamma"]
    assert names(decade=1990) == ["Alpha Song", "Beta Song"]
    assert names(year_from=2005, year_to=2010) == ["Alpha Song", "Gamma"]
    assert names(popularity_min=50, popularity_max=60) == ["Alpha Song", "Beta Song"]
    assert names(added_from="2024-03", added_to="2024-03-02") == ["Gamma"]
    assert names(added_to="2024-01") == ["Alpha Song", "Beta Song"]
    assert names(duration_min=4.5) == ["Gamma"]
    assert names(text="song", playlists=["Two"]) == ["Alpha Song", "Beta Song"]
    assert names(album="album t4") == ["Delta"]
    with pytest.raises(queries.QueryError, match="Unknown genre family"):
        names(genre_family="polka")
    with pytest.raises(queries.QueryError, match="isn't a date"):
        names(added_from="soon")
    with pytest.raises(dashboard.PlaylistNotFound):
        names(playlists=["Nope"])
    with pytest.raises(ValueError):   # unknown filter names are rejected, not ignored
        Filters(colour="red")


def test_find_tracks_sort_limit_and_links(conn):
    out = lines(queries.find_tracks(conn, None, sort="popularity", limit=2, include_links=True))
    assert out[0] == "6 unique tracks match (no filters); showing 2, sorted by popularity descending."
    assert out[1].endswith("| Playlists | Link")
    assert out[2].startswith("1 | Alpha Song | A1 | Album t1 | 1994 | 3:00 | 80 | 2024-01-10 | One")
    assert out[2].endswith("https://open.spotify.com/track/t1")
    oldest = lines(queries.find_tracks(conn, None, sort="year", ascending=True, limit=1))
    assert oldest[2].split(" | ")[1] == "Alpha Song"
    last = lines(queries.find_tracks(conn, None, sort="year", limit=6))[-1]
    assert last.split(" | ")[1] == "Zeta"        # no year sorts last
    # Beta Song is in two playlists: one row, both playlists, earliest add date
    beta = [r for r in lines(queries.find_tracks(conn, Filters(text="beta")))[2:]][0].split(" | ")
    assert beta[7:9] == ["2024-01-20", "One, Two"]
    with pytest.raises(queries.QueryError, match="Unknown sort"):
        queries.find_tracks(conn, None, sort="vibes")


def test_find_duplicates(conn):
    out = queries.find_duplicates(conn)
    assert "Exact duplicates: 2 Spotify tracks" in out and "Different versions: 1 songs" in out
    assert "Beta Song | A1 | 2 | One · Two | t2" in out
    assert "Alpha Song | A1 | 2 | One · Two (×2) | t1,t5" in out
    only_two = queries.find_duplicates(conn, ["Two"], kind="exact")
    assert "t5" in only_two and "t2" not in only_two and "Different versions" not in only_two
    with pytest.raises(queries.QueryError):
        queries.find_duplicates(conn, kind="loose")


def test_compare_playlists(conn):
    out = lines(queries.compare_playlists(conn))
    header = out[1].split(" | ")
    rows = {r.split(" | ")[0]: dict(zip(header, r.split(" | "))) for r in out[2:5]}
    assert [r.split(" | ")[0] for r in out[2:5]] == ["One", "Two", "Three"]   # largest first
    one, two = rows["One"], rows["Two"]
    assert (one["Entries"], one["Unique tracks"], one["Only here"], one["Followers"]) == ("4", "4", "3", "5")
    assert (two["Entries"], two["Unique tracks"], two["Only here"]) == ("3", "2", "1")  # t2 is shared; t5 only here
    assert one["Hours"] == "0.2" and one["Longest"] == "5:00" and one["Shortest"] == "2:00"
    assert (one["Year median"], one["Oldest"], one["Newest"]) == ("2000", "1994", "2021")
    assert (one["First added"], one["Last added"]) == ("Jan 2024", "Mar 2024")
    assert rows["Three"]["Followers"] == "" and rows["Three"]["First added"] == ""
    text = "\n".join(out)
    assert "Playlists with no added dates: Three." in text
    assert "One | Two | 1" in text                       # t2
    assert "Longest track: Gamma" in text
    scoped = queries.compare_playlists(conn, ["One", "Three"])
    assert "Comparison of 2 playlists" in scoped and "none" in scoped.splitlines()[-1]


def test_empty_cache(tmp_path):
    with store.connect(tmp_path / "e.db") as c:
        with pytest.raises(queries.QueryError, match="Nothing loaded"):
            queries.collection_stats(c, "decade")
