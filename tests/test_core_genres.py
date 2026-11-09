"""Genre families, family filter and preview filter.

Ported from the original app's tests/test_my_clusters.py (TC-01 to TC-21: TestAssignFamily,
TestTrackMatchesFamilies, TestPreviewFilter) against analysis/genres.py. The fixture is the original's.
"""
import pandas as pd
import pytest

from tailorlist_mcp.analysis.genres import (
    assign_family as _assign_family,
    preview_filter as _preview_filter,
    track_matches_families as _track_matches_families,
)


@pytest.fixture
def genre_df():
    """8 tracks across 2 playlists with varied decade and genre data."""
    return pd.DataFrame({
        "track_id":      [f"t{i}" for i in range(8)],
        "playlist_name": ["PL1"] * 4 + ["PL2"] * 4,
        "decade":        pd.array([1990, 1990, 2000, 2000, 1990, 2000, 2000, 2000], dtype="Int64"),
        "artist_genres": [
            "rock alternative",   # t0 PL1
            "hip hop trap",       # t1 PL1
            "pop dance pop",      # t2 PL1
            "xylophone ambient",  # t3 PL1  <- unrecognized
            "rock punk",          # t4 PL2
            "latin pop",          # t5 PL2
            "",                   # t6 PL2  <- empty
            "jazz bebop",         # t7 PL2
        ],
    })


class TestAssignFamily:

    def test_TC01_none_returns_other(self):
        assert _assign_family(None) == ["Other"]

    def test_TC02_empty_string_returns_other(self):
        assert _assign_family("") == ["Other"]

    def test_TC03_whitespace_returns_other(self):
        assert _assign_family("   ") == ["Other"]

    def test_TC04_nan_returns_other(self):
        assert _assign_family(float("nan")) == ["Other"]

    def test_TC05_rock_matched(self):
        assert "Rock" in _assign_family("alternative rock")

    def test_TC06_hip_hop_matched(self):
        assert "Hip-Hop" in _assign_family("hip hop trap")

    def test_TC07_latin_pop_matches_two_families(self):
        result = _assign_family("latin pop")
        assert "Latin" in result
        assert "Pop" in result

    def test_TC08_unrecognized_genre_returns_other(self):
        assert _assign_family("xylophone abstract fusion") == ["Other"]


# ---------------------------------------------------------------------------
# TC-09 – TC-15  _track_matches_families
# ---------------------------------------------------------------------------

class TestTrackMatchesFamilies:

    def test_TC09_none_with_other_selected(self):
        assert _track_matches_families(None, ["Other"]) is True

    def test_TC10_none_without_other(self):
        # Genre-less tracks always pass the family filter (deliberate 2026-05-20 change); the old mirror had diverged.
        assert _track_matches_families(None, ["Rock"]) is True

    def test_TC11_empty_string_with_other(self):
        assert _track_matches_families("", ["Other"]) is True

    def test_TC12_rock_matches_rock_family(self):
        assert _track_matches_families("alternative rock", ["Rock"]) is True

    def test_TC13_rock_does_not_match_pop(self):
        assert _track_matches_families("alternative rock", ["Pop"]) is False

    def test_TC14_unrecognized_genre_with_other(self):
        assert _track_matches_families("xylophone fusion", ["Other"]) is True

    def test_TC15_unrecognized_genre_without_other(self):
        assert _track_matches_families("xylophone fusion", ["Rock", "Pop"]) is False


# ---------------------------------------------------------------------------
# TC-16 – TC-21  _preview_filter
# ---------------------------------------------------------------------------

class TestPreviewFilter:

    def test_TC16_single_playlist(self, genre_df):
        count = _preview_filter(genre_df, ["PL1"], [], [], [])
        assert count == 4

    def test_TC17_two_playlists(self, genre_df):
        count = _preview_filter(genre_df, ["PL1", "PL2"], [], [], [])
        assert count == 8

    def test_TC18_decade_filter(self, genre_df):
        # 1990: t0, t1 (PL1) + t4 (PL2) = 3
        count = _preview_filter(genre_df, ["PL1", "PL2"], ["1990"], [], [])
        assert count == 3

    def test_TC19_raw_genre_filter(self, genre_df):
        # "rock" appears in t0 and t4 only
        count = _preview_filter(genre_df, ["PL1", "PL2"], [], [], ["rock"])
        assert count == 2

    def test_TC20_family_filter_rock_only(self, genre_df):
        all_fams = ["Rock", "Pop", "Hip-Hop", "Electronic", "Jazz", "Classical", "Folk", "Latin", "Other"]
        # Rock matches t0 "rock alternative" and t4 "rock punk"
        count = _preview_filter(genre_df, ["PL1", "PL2"], [], ["Rock"], [], all_fams)
        # Genre-less tracks always pass the family filter (deliberate 2026-05-20 change); the old mirror had diverged.
        assert count == 3

    def test_TC21_zero_result_when_no_match(self, genre_df):
        # "Classical" only — nothing in the genre_df has classical keywords
        all_fams = ["Rock", "Pop", "Hip-Hop", "Electronic", "Jazz", "Classical", "Folk", "Latin", "Other"]
        count = _preview_filter(genre_df, ["PL1"], [], ["Classical"], [], all_fams)
        assert count == 0
