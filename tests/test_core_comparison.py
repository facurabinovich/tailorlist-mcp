"""Characterization tests for core/comparison.py on small fixed frames."""
from unittest.mock import ANY
import pandas as pd
import pytest

from tailorlist_mcp.analysis import comparison as cmp

MIN = 60000


def _frame():
    return pd.DataFrame({
        "track_id": ["a", "b", "c", "a"],
        "track_name": ["Alpha", "Beta", "Gamma", "Alpha"],
        "artist_display": ["X", "Y feat. Z", "W", "X"],
        "playlist_name": ["P1", "P1", "P2", "P2"],
        "popularity_raw": [80, 60, 40, 80],
        "release_year": [1995, 2005, None, 1995],
        "decade": [1990, 2000, None, 1990],
        "duration_raw": [3 * MIN, 5 * MIN, 2 * MIN + 30000, 3 * MIN],
        "added_at": ["2024-03-05", "2024-01-10", None, "2024-06-01"],
        "artist_genres": ["rock, Pop", "rock", None, "rock, pop"],
        "energy": [0.9, 0.5, 0.2, 0.9],
        "valence": [0.1, 0.3, 0.8, 0.1],
    })


def _records():
    return [
        {"id": "a", "name": "Alpha", "artists": "X", "playlist_name": "P1"},
        {"id": "a", "name": "Alpha", "artists": "X", "playlist_name": "P2"},
        {"id": "a", "name": "Alpha", "artists": "X", "playlist_name": "P2"},
        {"id": "b", "name": "Beta", "artists": "Y", "playlist_name": "P1"},
        {"id": "b2", "name": "Beta", "artists": "Y", "playlist_name": "P2"},
        {"id": "", "name": "NoId", "artists": "Q", "playlist_name": "P1"},
        {"id": "z", "name": "Zed", "artists": "Q", "playlist_name": "P1"},
    ]


def test_playlist_meta_renames_and_empty():
    meta = cmp.playlist_meta([{"name": "P1", "track_count": 3, "followers": 7}])
    assert meta.to_dict("records") == [{"playlist_name": "P1", "total_tracks": 3, "followers": 7}]
    assert cmp.playlist_meta([]).empty


def test_size_and_follower_rankings():
    meta = cmp.playlist_meta([{"name": "A", "track_count": 1, "followers": 9},
                              {"name": "B", "track_count": 5, "followers": 2}])
    assert cmp.size_ranking(meta)["playlist_name"].tolist() == ["B", "A"]
    assert cmp.follower_ranking(meta)["playlist_name"].tolist() == ["A", "B"]
    assert cmp.size_ranking(meta).index.tolist() == [0, 1]


def test_exact_duplicates_across_playlists():
    d = cmp.exact_duplicates_across_playlists(_records())
    assert d.columns.tolist() == ["track_id", "track_name", "artist_display", "count", "playlists"]
    assert d.to_dict("records") == [
        {"track_id": "a", "track_name": "Alpha", "artist_display": "X", "count": 3, "playlists": "P1 · P2 (×2)"}
    ]


def test_exact_duplicates_sorted_by_track_name():
    recs = [{"id": i, "name": n, "artists": "x", "playlist_name": "P"} for i, n in
            [("1", "Zed"), ("1", "Zed"), ("2", "Abe"), ("2", "Abe")]]
    assert cmp.exact_duplicates_across_playlists(recs)["track_name"].tolist() == ["Abe", "Zed"]


def test_different_versions_across_playlists():
    v = cmp.different_versions_across_playlists(_records())
    assert v.to_dict("records") == [
        {"track_name": "Beta", "artist_name": "Y", "n_unique_ids": 2, "total": 2, "names": ["Beta"],
         "track_ids": ANY, "playlists": "P1 · P2"}
    ]


def test_duplicates_empty_records():
    assert cmp.exact_duplicates_across_playlists([]).empty
    assert cmp.different_versions_across_playlists([]).empty
    assert cmp.exact_duplicates_across_playlists([]).columns.tolist()[-1] == "playlists"


def test_duplicates_ignore_missing_ids():
    recs = [{"id": None, "name": "N", "artists": "x", "playlist_name": "P"}] * 2
    assert cmp.exact_duplicates_across_playlists(recs).empty


def test_duration_extremes_in_minutes():
    e = cmp.duration_extremes(_frame())
    assert (e.longest.track_name, e.longest.minutes) == ("Beta", 5.0)
    assert (e.shortest.track_name, e.shortest.playlist_name, e.shortest.minutes) == ("Gamma", "P2", 2.5)
    assert cmp.duration_extremes(_frame().drop(columns="duration_raw")) is None


def test_duration_by_playlist():
    d = cmp.duration_by_playlist(_frame())
    assert d.columns.tolist() == ["playlist_name", "avg_min", "min_min", "max_min"]
    assert d.iloc[0].tolist() == ["P1", 4.0, 3.0, 5.0]
    assert cmp.duration_by_playlist(pd.DataFrame({"playlist_name": ["P"]})) is None


def test_popularity_and_axis_range():
    pop = cmp.popularity_by_playlist(_frame())
    assert pop["playlist_name"].tolist() == ["P1", "P2"]
    assert pop["avg_popularity"].tolist() == [70.0, 60.0]
    assert cmp.popularity_axis_range(pop) == (55.0, 78.0)
    assert cmp.popularity_axis_range(pd.DataFrame({"avg_popularity": [2.0, 99.0]})) == (0, 100)
    assert cmp.popularity_by_playlist(_frame().drop(columns="popularity_raw")) is None


def test_feature_leaders_and_heatmap():
    af = pd.DataFrame({"playlist_name": ["P1", "P2"], "energy": [0.7, 0.2],
                       "valence": [0.2, 0.8], "liveness": [None, None]})
    leaders = cmp.feature_leaders(af)
    assert [(l.feature, l.playlist_name, l.value) for l in leaders] == [("energy", "P1", 0.7), ("valence", "P2", 0.8)]
    heat = cmp.heatmap_matrix(af, ["energy", "valence", "liveness", "tempo"])
    assert heat.columns.tolist() == ["energy", "valence"] and heat.index.tolist() == ["P1", "P2"]


def test_feature_leaders_without_features():
    af = pd.DataFrame({"playlist_name": ["P1"]})
    assert cmp.feature_leaders(af) == [] and cmp.feature_leaders(pd.DataFrame()) == []
    assert cmp.heatmap_matrix(af, ["energy"]) is None


def test_count_unique_genres():
    assert cmp.count_unique_genres(pd.Series(["Rock, pop", "rock", None, " , "])) == 2


def test_genre_diversity_sorted_ascending():
    d = cmp.genre_diversity(_frame())
    assert d["unique_genres"].tolist() == [2, 2] and set(d["playlist_name"]) == {"P1", "P2"}
    more = cmp.genre_diversity(_frame().assign(artist_genres=["a, b, c", "a", None, "a"]))
    assert more.to_dict("records") == [{"playlist_name": "P2", "unique_genres": 1},
                                       {"playlist_name": "P1", "unique_genres": 3}]
    assert cmp.genre_diversity(_frame().drop(columns="artist_genres")) is None


def test_release_era():
    r = cmp.release_era(_frame())
    assert r.to_dict("records") == [
        {"playlist_name": "P2", "avg_release_year": 1995.0, "min_year": 1995.0, "max_year": 1995.0},
        {"playlist_name": "P1", "avg_release_year": 2000.0, "min_year": 1995.0, "max_year": 2005.0},
    ]
    assert cmp.release_era(_frame().drop(columns="release_year")) is None


def test_decade_mix_and_tracks():
    m = cmp.decade_mix(_frame())
    assert m.to_dict("records") == [
        {"playlist_name": "P1", "decade_label": "1990s", "count": 1},
        {"playlist_name": "P1", "decade_label": "2000s", "count": 1},
        {"playlist_name": "P2", "decade_label": "1990s", "count": 1},
    ]
    # "a" is de-duplicated to its first row (P1), so P2 has no 1990s tracks
    assert cmp.decade_tracks(_frame(), "P2", 1990).empty
    t = cmp.decade_tracks(_frame(), "P1", 1990)
    assert t.to_dict("records") == [{"Track": "Alpha", "Artist": "X", "Year": 1995, "Popularity": 80}]
    assert cmp.decade_mix(_frame().drop(columns="decade")) is None


def test_playlist_age_and_missing():
    age = cmp.playlist_age(_frame())
    assert age["playlist_name"].tolist() == ["P1", "P2"]
    assert age["first_added_str"].tolist() == ["Jan 2024", "Jun 2024"]
    df = _frame().assign(added_at=[None, None, "2024-02-01", None])
    age = cmp.playlist_age(df)
    assert age["playlist_name"].tolist() == ["P2"]
    assert cmp.playlists_without_dates(df, age) == ["P1"]
    assert cmp.playlist_age(_frame().drop(columns="added_at")) is None


def test_inputs_not_mutated():
    df, recs = _frame(), _records()
    before, recs_before = df.copy(), [dict(r) for r in recs]
    cmp.duration_extremes(df); cmp.duration_by_playlist(df); cmp.decade_tracks(df, "P1", 1990)
    cmp.playlist_age(df); cmp.exact_duplicates_across_playlists(recs); cmp.different_versions_across_playlists(recs)
    pd.testing.assert_frame_equal(df, before)
    assert recs == recs_before
