"""Characterization of core.duplicates on small fixed frames (values verified against the
pre-T20 inline logic of pages/2_Playlist_Detail.py)."""
import pandas as pd
import pytest

from tailorlist_mcp.analysis import duplicates as dup


def _records():
    return [
        {"id": "a", "name": "Song", "artists": "X", "playlist_name": "P1"},
        {"id": "a", "name": "Song", "artists": "X", "playlist_name": "P1"},   # exact duplicate
        {"id": "b", "name": "Song", "artists": "X", "playlist_name": "P1"},   # version of Song
        {"id": "c", "name": "Other", "artists": "Y", "playlist_name": "P1"},
        {"id": "a", "name": "Song", "artists": "X", "playlist_name": "P2"},
        {"id": "", "name": "NoId", "artists": "Z", "playlist_name": "P1"},
        {"id": None, "name": "NoId2", "artists": "Z", "playlist_name": "P1"},
    ]


def test_raw_records_frame_renames_and_filters_playlist():
    raw = dup.raw_records_frame(_records(), "P2")
    assert raw.columns.tolist() == ["track_id", "track_name", "artist_name", "playlist_name"]
    assert raw["track_id"].tolist() == ["a"]


def test_raw_records_frame_without_playlist_keeps_everything():
    assert len(dup.raw_records_frame(_records())) == 7


def test_raw_records_frame_empty_has_canonical_columns():
    assert dup.raw_records_frame([]).columns.tolist() == ["track_id", "track_name", "artist_name"]
    assert dup.raw_records_frame(_records(), "nope").columns.tolist() == ["track_id", "track_name", "artist_name"]


def test_drop_missing_ids_removes_empty_and_none():
    raw = dup.drop_missing_ids(dup.raw_records_frame(_records(), "P1"))
    assert raw["track_id"].tolist() == ["a", "a", "b", "c"]


def test_exact_duplicates_within_playlist():
    raw = dup.drop_missing_ids(dup.raw_records_frame(_records(), "P1"))
    d = dup.exact_duplicates(raw)
    assert d.columns.tolist() == ["track_id", "count", "track_name", "artist"]
    assert d.to_dict("records") == [{"track_id": "a", "count": 2, "track_name": "Song", "artist": "X"}]


def test_exact_duplicates_sorted_by_count_desc():
    raw = pd.DataFrame({
        "track_id": ["a", "b", "b", "b", "c", "c"],
        "track_name": ["A", "B", "B", "B", "C", "C"],
        "artist_name": ["x"] * 6,
    })
    d = dup.exact_duplicates(raw)
    assert d["track_id"].tolist() == ["b", "c"]
    assert d["count"].tolist() == [3, 2]


def test_exact_duplicates_uses_requested_artist_column():
    raw = pd.DataFrame({
        "track_id": ["a", "a"], "track_name": ["A", "A"],
        "artist_name": ["X", "X"], "artist_display": ["X feat. Q", "X feat. Q"],
    })
    assert dup.exact_duplicates(raw, "artist_display").iloc[0]["artist"] == "X feat. Q"


def test_exact_duplicates_across_playlists_lists_playlists():
    raw = dup.drop_missing_ids(dup.raw_records_frame(_records()))
    d = dup.exact_duplicates(raw, include_playlists=True)
    assert d.iloc[0]["track_id"] == "a" and d.iloc[0]["count"] == 3
    assert d.iloc[0]["playlists"] == ["P1", "P1", "P2"]


def test_exact_duplicates_empty_frame():
    raw = dup.drop_missing_ids(dup.raw_records_frame([]))
    assert dup.exact_duplicates(raw).empty


def test_different_versions():
    raw = dup.drop_missing_ids(dup.raw_records_frame(_records(), "P1"))
    v = dup.different_versions(raw)
    assert v.columns.tolist() == ["track_name", "artist_name", "n_unique_ids", "total", "names", "track_ids"]
    assert v.to_dict("records") == [{"track_name": "Song", "artist_name": "X", "n_unique_ids": 2, "total": 3,
                                     "names": ["Song"], "track_ids": ["a", "b"]}]


def test_different_versions_ignores_pure_exact_duplicates():
    raw = pd.DataFrame({"track_id": ["a", "a"], "track_name": ["A", "A"], "artist_name": ["x", "x"]})
    assert dup.different_versions(raw).empty


def test_different_versions_sorted_by_name_and_with_playlists():
    raw = pd.DataFrame({
        "track_id": ["1", "2", "3", "4"], "track_name": ["Zed", "Zed", "Abe", "Abe"],
        "artist_name": ["x"] * 4, "playlist_name": ["P1", "P2", "P1", "P1"],
    })
    v = dup.different_versions(raw, include_playlists=True)
    assert v["track_name"].tolist() == ["Abe", "Zed"]
    assert v.iloc[1]["playlists"] == ["P1", "P2"]


def test_different_versions_same_name_other_artist_is_not_a_version():
    raw = pd.DataFrame({"track_id": ["1", "2"], "track_name": ["S", "S"], "artist_name": ["x", "y"]})
    assert dup.different_versions(raw).empty


def test_different_versions_empty_frame():
    raw = dup.drop_missing_ids(dup.raw_records_frame([]))
    assert dup.different_versions(raw).empty


@pytest.mark.parametrize("name, base", [
    ("Come Together - Remastered 2009", "Come Together"),
    ("Something - 2019 Mix", "Something"),
    ("Love Me Do - Mono Version", "Love Me Do"),
    ("Heart of Gold - Live", "Heart of Gold"),
    ("Layla - Acoustic; Live at MTV Unplugged, 1992; 2013 Remaster", "Layla"),
    ("Persiana Americana - En Vivo", "Persiana Americana"),
    ("Hey Jude (Remastered 2015)", "Hey Jude"),
    ("Help! - Mono / Remastered", "Help!"),
    ("Song - Demo - Remastered", "Song"),
    ("Mi Enfermedad - Remasterizado 2019", "Mi Enfermedad"),
    ("Walk of Life - Radio Edit", "Walk of Life"),
    ("Song - Deepend Remix", "Song - Deepend Remix"),
    ("Song - From \"Grease\"", "Song - From \"Grease\""),
    ("Song (feat. Someone)", "Song (feat. Someone)"),
    ("Live and Let Die", "Live and Let Die"),
    ("Live", "Live"),
    ("Anti-Hero", "Anti-Hero"),
])
def test_strip_version_suffix(name, base):
    assert dup.strip_version_suffix(name) == base


def test_different_versions_ignores_version_suffixes():
    raw = pd.DataFrame({
        "track_id": ["1", "2", "3", "4", "5"],
        "track_name": ["Come Together - Remastered 2009", "Come Together", "come together - Live",
                       "Come Together - Deepend Remix", "Something"],
        "artist_name": ["The Beatles"] * 5,
    })
    v = dup.different_versions(raw)
    assert len(v) == 1
    row = v.iloc[0]
    assert row["track_name"] == "Come Together" and row["n_unique_ids"] == 3
    assert row["names"] == ["Come Together - Remastered 2009", "Come Together", "come together - Live"]
    assert row["track_ids"] == ["1", "2", "3"]


def test_inputs_not_mutated():
    raw = dup.raw_records_frame(_records(), "P1")
    before = raw.copy()
    dup.exact_duplicates(raw)
    dup.different_versions(raw)
    pd.testing.assert_frame_equal(raw, before)


def test_format_playlist_counts():
    assert dup.format_playlist_counts(["B", "A", "A"]) == "A (×2) · B"
    assert dup.format_playlist_counts(["Solo"]) == "Solo"
    assert dup.format_playlist_counts([]) == ""
