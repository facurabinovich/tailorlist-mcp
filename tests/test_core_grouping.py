"""analysis/grouping.py, ported from the original app's tests/test_core_grouping.py.

The original compared against EXPECTED output hashed from its gitignored data/dev_collection.json, which
isn't available here. What carries over: the release-date cases and their expected decades (EXPECTED["rd"]),
the contracts in the module docstrings (unmatched tracks, cluster names and indexes, PC columns, a track
repeated once per matching genre, the input not mutated), checked on a small frame.
"""
import pandas as pd
import pytest

from tailorlist_mcp.analysis.grouping import group_by_category, group_by_specific_genres, release_date_to_decade

RD_CASES = ["2014", "2014-03", "2014-03-15", "", None, "garbage", "1899-01-01", "2100", "abcd-01", "1900", "1901-05", 2014, "19"]
RD_EXPECTED = [2010, 2010, 2010, None, None, None, None, None, None, None, 1900, 2010, None]   # the original's EXPECTED["rd"]


@pytest.fixture
def frame():
    return pd.DataFrame({
        "track_id": ["a", "b", "c", "d", "e", "f"],
        "track_name": list("ABCDEF"),
        "artist_name": ["x"] * 6,
        "decade": [1990, 1990, 2000, None, 2000, 1990],
        "artist_genres": ["classic rock, rock", "pop", "hip hop, rap", "rock", "", "   "],
        "playlist_name": ["P"] * 6,
    })


def test_release_date_to_decade():
    assert [release_date_to_decade(x) for x in RD_CASES] == RD_EXPECTED


def test_category_both_sets_aside_tracks_missing_a_dimension(frame):
    before = frame.copy()
    clustered, unmatched = group_by_category(frame, True, True)
    assert dict(clustered.groupby("cluster_name")["track_id"].apply(list)) == {
        "Hip-Hop · 2000s": ["c"], "Pop · 1990s": ["b"], "Rock · 1990s": ["a"]}
    assert sorted(unmatched["track_id"]) == ["d", "e", "f"]            # no decade (d), no genre (e, f)
    assert list(clustered.sort_values("cluster")["cluster_name"]) == sorted(clustered["cluster_name"])  # index follows sorted names
    assert (clustered["PC1"] == 0).all() and (clustered["PC2"] == 0).all()
    pd.testing.assert_frame_equal(frame, before)                          # input not mutated


def test_category_one_dimension_and_none(frame):
    by_decade, um = group_by_category(frame, True, False)
    assert set(by_decade["cluster_name"]) == {"1990s", "2000s"} and list(um["track_id"]) == ["d"]
    by_genre, um = group_by_category(frame, False, True)
    assert set(by_genre["cluster_name"]) == {"Rock", "Pop", "Hip-Hop"} and sorted(um["track_id"]) == ["e", "f"]
    plain, um = group_by_category(frame, False, False)
    assert set(plain["cluster_name"]) == {"Ungrouped"} and um.empty


def test_a_multi_family_track_goes_to_its_first_family():
    df = pd.DataFrame({"track_id": ["a"], "decade": [1990], "artist_genres": ["latin pop"]})
    clustered, _ = group_by_category(df, False, True)
    assert list(clustered["cluster_name"]) == ["Pop"]        # Pop comes before Latin in GENRE_FAMILIES


def test_specific_genres_repeat_a_track_per_matching_genre(frame):
    before = frame.copy()
    out = group_by_specific_genres(frame, ["rock", "classic rock", "nonexistent"], False)
    assert sorted(zip(out["cluster_name"], out["track_id"])) == [("classic rock", "a"), ("rock", "a"), ("rock", "d")]
    assert list(out.sort_values("cluster")["cluster_name"].unique()) == ["classic rock", "rock"]
    pd.testing.assert_frame_equal(frame, before)


def test_specific_genres_by_decade_marks_unknown(frame):
    out = group_by_specific_genres(frame, ["rock"], True)
    assert sorted(out["cluster_name"]) == ["rock · 1990s", "rock · Unknown"]


def test_selection_with_unknown_genre_has_no_group(frame):
    assert group_by_specific_genres(frame, ["nonexistent genre"], True).empty
