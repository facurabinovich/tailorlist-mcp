"""Characterization of core.overview on a small fixed frame (values verified against the
pre-T19 inline page logic, see tests/test_overview.py for the broader scenarios)."""
import numpy as np
import pandas as pd
import pytest

from tailorlist_mcp.analysis import overview as ov


@pytest.fixture
def df():
    return pd.DataFrame({
        "track_id":       ["a", "b", "c", "d", "e", "a"],
        "track_name":     ["A", "B", "C", "D", "E", "A"],
        "artist_name":    ["X", "X", "Y", "Y", "Z", "X"],
        "artist_display": ["X", "X", "Y", "Y", "Z", "X"],
        "playlist_name":  ["P1", "P1", "P2", "P2", "P3", "P2"],
        "album_name":     ["Al1", "Al1", "Al2", "Al2", "Al3", "Al1"],
        "popularity_raw": [80, 70, 60, 50, 40, 80],
        "release_year":   [1990, 2000, 1985, 2010, 2020, 1990],
        "decade":         [1990, 2000, 1980, 2010, 2020, 1990],
        "duration_raw":   [180000.0, 240000.0, 180000.0, 240000.0, 210000.0, 180000.0],
        "added_at":       ["2024-01-15", "2024-01-20", "2024-03-05", "2024-03-10", "2024-05-01", "2024-01-15"],
        "artist_genres":  ["rock,indie rock", "rock", "pop", "", None, "rock,indie rock"],
    })


def test_dedupe_tracks_keeps_first_and_does_not_mutate(df):
    d = ov.dedupe_tracks(df)
    assert d["track_id"].tolist() == ["a", "b", "c", "d", "e"]
    assert len(df) == 6


def test_compute_kpis(df):
    k = ov.compute_kpis(df)
    assert (k.total_tracks, k.total_artists, k.total_playlists) == (5, 3, 3)
    assert k.avg_popularity == pytest.approx(60.0)
    assert k.top_decade == 1980  # all decades tie once -> smallest
    assert k.avg_duration_min == pytest.approx(3.5)  # (3+4+3+4+3.5)/5


def test_compute_kpis_missing_optional_columns(df):
    k = ov.compute_kpis(df[["track_id", "artist_name", "playlist_name"]])
    assert (k.avg_popularity, k.top_decade, k.avg_duration_min) == (None, None, None)


def test_compute_kpis_all_nan_duration_and_decade(df):
    d = df.assign(duration_raw=np.nan, decade=np.nan)
    k = ov.compute_kpis(d)
    assert k.avg_duration_min is None and k.top_decade is None


def test_compute_kpis_empty():
    k = ov.compute_kpis(pd.DataFrame(columns=["track_id", "artist_name", "playlist_name", "popularity_raw"]))
    assert (k.total_tracks, k.total_artists, k.total_playlists) == (0, 0, 0)
    assert np.isnan(k.avg_popularity)


def test_listening_profile_uses_given_year(df):
    p = ov.compute_listening_profile(ov.dedupe_tracks(df), 2030)
    assert (p.median_year, p.est_birth, p.listening_age) == (2000, 1984, 46)


def test_listening_profile_none_without_years(df):
    assert ov.compute_listening_profile(df.drop(columns="release_year"), 2026) is None
    assert ov.compute_listening_profile(df.assign(release_year=np.nan), 2026) is None


def test_oldest_newest(df):
    r = ov.find_oldest_newest(ov.dedupe_tracks(df))
    assert (r.oldest["track_name"], r.newest["track_name"]) == ("C", "E")
    assert (r.oldest_year, r.newest_year, r.span) == (1985, 2020, 35)


def test_oldest_newest_none(df):
    assert ov.find_oldest_newest(df.drop(columns="release_year")) is None


def test_playlist_track_counts_counts_rows(df):
    c = ov.playlist_track_counts(df)
    assert dict(zip(c["playlist_name"], c["track_count"])) == {"P1": 2, "P2": 3, "P3": 1}
    assert c["playlist_name"].iloc[0] == "P2"


def test_top_artists_by_tracks(df):
    t = ov.top_artists_by_tracks(df, n=2)
    assert len(t) == 2
    assert t.iloc[0]["track_count"] == 2
    assert set(t["artist_name"]) == {"X", "Y"}


@pytest.mark.parametrize("n, expected", [(0, 1), (9, 1), (20, 2), (50, 5), (500, 5)])
def test_min_tracks_threshold(n, expected):
    assert ov.min_tracks_threshold(n) == expected


def test_top_popular_artists_threshold_and_columns(df):
    # 5 unique tracks -> threshold 1 -> everyone qualifies, ranked by mean popularity
    t = ov.top_popular_artists(df)
    assert list(t.columns) == ["artist_name", "track_count", "avg_popularity"]
    assert t["artist_name"].tolist() == ["X", "Y", "Z"]
    assert t["avg_popularity"].tolist() == [pytest.approx(76.666667), 55.0, 40.0]


def test_top_popular_artists_without_popularity(df):
    assert ov.top_popular_artists(df.drop(columns="popularity_raw")).empty


def test_top_albums(df):
    t = ov.top_albums(df)
    assert list(t.columns) == ["album_name", "artist_name", "track_count", "avg_popularity"]
    assert t["album_name"].tolist() == ["Al1", "Al2", "Al3"]
    assert ov.top_albums(df.drop(columns="album_name")).empty
    assert ov.top_albums(df.drop(columns="popularity_raw")).empty


def test_genre_track_counts_includes_subgenres(df):
    g = ov.genre_track_counts(df)
    counts = dict(zip(g["genre"], g["track_count"]))
    # "rock" is contained in "indie rock" too; tracks a and b -> 2 unique tracks
    assert counts == {"rock": 2, "indie rock": 1, "pop": 1}
    assert g["genre"].iloc[0] == "rock"


def test_genre_track_counts_no_genres_returns_empty_frame(df):
    g = ov.genre_track_counts(df.assign(artist_genres=""))
    assert g.empty and list(g.columns) == ["genre", "track_count"]


def test_top_genres_resets_index(df):
    g = ov.top_genres(ov.genre_track_counts(df), n=2)
    assert g.index.tolist() == [0, 1] and len(g) == 2


def test_untagged_tracks_summary(df):
    # unique tracks d (blank genre) and e (None) have no genre -> 2/5
    assert ov.untagged_tracks_summary(ov.dedupe_tracks(df)) == (2, 40.0)
    assert ov.untagged_tracks_summary(ov.dedupe_tracks(df).iloc[:3]) == (0, 0.0)


def test_decade_distribution(df):
    d = ov.decade_distribution(ov.dedupe_tracks(df))
    assert d["decade"].tolist() == [1980, 1990, 2000, 2010, 2020]
    assert d["count"].tolist() == [1, 1, 1, 1, 1]
    assert d["decade_label"].tolist()[0] == "1980s"


def test_decade_distribution_empty_cases(df):
    assert ov.decade_distribution(df.drop(columns="decade")).empty
    assert ov.decade_distribution(df.assign(decade=np.nan)).empty


def test_has_popularity(df):
    assert ov.has_popularity(df)
    assert not ov.has_popularity(df.drop(columns="popularity_raw"))
    assert not ov.has_popularity(df.assign(popularity_raw=np.nan))


@pytest.mark.parametrize("xs, bounds", [([52.5], (50.0, 55.0)), ([2.5, 7.5], (0.0, 5.0)), ([97.5], (95.0, 100.0))])
def test_popularity_bin_bounds(xs, bounds):
    assert ov.popularity_bin_bounds(xs) == bounds


def test_additions_timeline_counts_track_once_per_month(df):
    t = ov.additions_timeline(df)
    assert t["year_month"].tolist() == ["2024-01", "2024-03", "2024-05"]
    assert t["tracks_added"].tolist() == [2, 2, 1]


def test_additions_timeline_empty_cases(df):
    assert ov.additions_timeline(df.drop(columns="added_at")).empty
    assert ov.additions_timeline(df.assign(added_at=None)).empty
    assert ov.additions_timeline(df.assign(added_at="not a date")).empty


def test_track_listing_format(df):
    out = ov.track_listing(df.assign(release_year=[1990, np.nan, 1985, 2010, 2020, 1990]),
                           ["track_name", "artist_display", "popularity_raw", "release_year", "missing"])
    assert list(out.columns) == ["Track", "Artist", "Popularity", "Year"]
    assert out["Popularity"].tolist() == sorted(out["Popularity"].tolist(), reverse=True)
    assert 0 in out["Year"].tolist() and out["Year"].dtype.kind == "i"
    assert out.index.tolist() == list(range(len(out)))


def test_track_listing_sort_strictness(df):
    no_pop = df.drop(columns="popularity_raw")
    with pytest.raises(KeyError):
        ov.track_listing(no_pop, ["track_name"])
    assert len(ov.track_listing(no_pop, ["track_name"], strict_sort=False)) == 6


def test_tracks_for_artist(df):
    out = ov.tracks_for_artist(ov.dedupe_tracks(df), "X")
    assert list(out.columns) == ["Track", "Playlist", "Popularity", "Year"]
    assert out["Track"].tolist() == ["A", "B"]


def test_tracks_for_decade(df):
    out = ov.tracks_for_decade(ov.dedupe_tracks(df), 1990)
    assert out["Track"].tolist() == ["A"]
    assert list(out.columns) == ["Track", "Artist", "Playlist", "Popularity", "Year"]


def test_tracks_for_popularity_range_is_half_open(df):
    d = ov.dedupe_tracks(df)
    assert ov.tracks_for_popularity_range(d, 50, 60)["Track"].tolist() == ["D"]
    assert ov.tracks_for_popularity_range(d, 50, 61)["Track"].tolist() == ["C", "D"]


def test_tracks_for_genre_includes_subgenres(df):
    d = ov.dedupe_tracks(df)
    assert ov.tracks_for_genre(d, "rock")["Track"].tolist() == ["A", "B"]
    assert ov.tracks_for_genre(d, "ROCK")["Track"].tolist() == ["A", "B"]
    assert ov.tracks_for_genre(d, "indie rock")["Track"].tolist() == ["A"]


def test_tracks_for_month(df):
    assert ov.tracks_for_month(df, "2024-01")["Track"].tolist() == ["A", "B"]
    assert ov.tracks_for_month(df, "1999-01").empty
    no_pop = ov.tracks_for_month(df.drop(columns="popularity_raw"), "2024-03")
    assert list(no_pop.columns) == ["Track", "Artist", "Playlist", "Year"]
