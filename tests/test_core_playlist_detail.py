"""Port of the original core.playlist_detail tests (audio sections left out). Characterization of playlist_detail on a small fixed frame (values verified against the
pre-T20 inline logic of pages/2_Playlist_Detail.py; see tests/test_playlist_detail.py for the
broader scenarios)."""
import numpy as np
import pandas as pd
import pytest

from tailorlist_mcp.analysis import playlist_detail as pdc


@pytest.fixture
def df():
    return pd.DataFrame({
        "track_id":       ["a", "b", "c", "d", "e", "f"],
        "track_name":     ["A", "B", "C", "D", "E", "F"],
        "artist_name":    ["X", "X", "Y", "Y", "Z", "X"],
        "artist_display": ["X", "X", "Y", "Y feat. Q", "Z", "X"],
        "playlist_name":  ["P1", "P1", "P1", "P1", "P2", "P2"],
        "popularity_raw": [80, 70, 60, 50, 40, 30],
        "release_year":   [1990.0, 2000.0, 1985.0, np.nan, 2020.0, 1990.0],
        "decade":         [1990.0, 2000.0, 1980.0, np.nan, 2020.0, 1990.0],
        "duration_raw":   [180000.0, 240000.0, 210000.0, 300000.0, 120000.0, 60000.0],
        "added_at":       ["2024-01-15", "2024-01-20", "2024-03-05", "2024-03-10", "2024-05-01", "2024-05-02"],
        "artist_genres":  ["rock, indie rock", "rock", "pop", None, "jazz", ""],
        "energy":         [0.8, 0.6, 0.4, np.nan, 0.2, 0.3],
        "valence":        [0.5, 0.5, 0.7, 0.1, 0.2, 0.3],
        "danceability":   [0.7, 0.6, 0.5, 0.4, 0.3, 0.2],
        "acousticness":   [0.2, 0.3, 0.6, 0.1, 0.8, 0.9],
        "speechiness":    [0.05, 0.04, 0.06, 0.03, 0.02, 0.01],
        "instrumentalness": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5],
        "tempo":          [120.0, 130.0, 90.0, 140.0, 80.0, 100.0],
    })


@pytest.fixture
def p1(df):
    return pdc.playlist_tracks(df, "P1")






def test_playlist_meta(df):
    meta = pdc.playlist_meta(df, [{"name": "P1", "followers": 5}, {"name": "P1", "followers": 9}, {"name": "P2"}])
    assert meta.to_dict("records") == [
        {"playlist_name": "P1", "total_tracks": 4, "followers": 5},   # first matching record wins
        {"playlist_name": "P2", "total_tracks": 2, "followers": 0},   # no followers key -> 0
    ]




def test_playlist_tracks_converts_to_minutes_without_mutating(df):
    p1 = pdc.playlist_tracks(df, "P1")
    assert p1["duration_raw"].tolist() == [3.0, 4.0, 3.5, 5.0]
    assert df["duration_raw"].iloc[0] == 180000.0
    assert p1.index.tolist() == [0, 1, 2, 3]








def test_header_kpis(p1):
    k = pdc.header_kpis(p1, 12)
    assert (k.total_tracks, k.unique_artists, k.followers) == (4, 2, 12)
    assert (k.first_added, k.last_added) == ("Jan 2024", "Mar 10, 2024")


def test_header_kpis_without_added_at(p1):
    k = pdc.header_kpis(p1.drop(columns="added_at"), 0)
    assert (k.first_added, k.last_added) == ("N/A", "N/A")


def test_track_highlights(p1):
    h = pdc.track_highlights(p1)
    assert (h.longest["track_name"], h.shortest["track_name"]) == ("D", "A")
    assert (h.most_popular["track_name"], h.least_popular["track_name"]) == ("A", "D")
    assert (h.oldest["track_name"], h.newest["track_name"]) == ("C", "B")     # NaN year of D ignored


def test_track_highlights_missing_columns(p1):
    h = pdc.track_highlights(p1[["track_name", "artist_display"]])
    assert h == pdc.TrackHighlights(None, None, None, None, None, None) or all(
        getattr(h, f) is None for f in ("longest", "shortest", "most_popular", "least_popular", "oldest", "newest"))
    no_years = p1.assign(release_year=np.nan)
    assert pdc.track_highlights(no_years).oldest is None


def test_playlist_averages(p1):
    a = pdc.playlist_averages(p1)
    assert a.avg_duration == pytest.approx(3.875)
    assert a.avg_popularity == pytest.approx(65.0)
    assert a.avg_release_year == pytest.approx((1990 + 2000 + 1985) / 3)
    assert (a.top_artist, a.top_artist_count) == ("X", 2)       # X and Y tie -> first by appearance


def test_playlist_averages_missing_columns(p1):
    a = pdc.playlist_averages(p1[["artist_name"]])
    assert (a.avg_duration, a.avg_popularity, a.avg_release_year) == (None, None, None)


def test_top_artists(p1):
    t = pdc.top_artists(p1, 1)
    assert t.table.to_dict("records") == [{"artist": "X", "tracks": 2}]
    assert t.covered_pct == pytest.approx(50.0)
    assert pdc.top_artists(p1).covered_pct == pytest.approx(100.0)


def test_decade_and_year_counts(p1):
    d = pdc.decade_counts(p1)
    assert d.to_dict("records") == [
        {"decade": 1980, "count": 1, "label": "1980s"},
        {"decade": 1990, "count": 1, "label": "1990s"},
        {"decade": 2000, "count": 1, "label": "2000s"},
    ]
    y = pdc.year_counts(p1)
    assert y["year"].tolist() == [1985, 1990, 2000] and y["count"].tolist() == [1, 1, 1]
    assert pdc.decade_counts(p1.drop(columns="decade")).empty
    assert pdc.year_counts(p1.drop(columns="release_year")).empty


def test_decade_track_listing(df):
    out = pdc.decade_track_listing(df, 1990)
    assert out.columns.tolist() == ["Track", "Artist", "Year", "Popularity"]
    assert out["Track"].tolist() == ["A", "F"]                  # popularity descending
    assert str(out["Year"].dtype) == "Int64"


def test_decade_track_listing_without_popularity(df):
    out = pdc.decade_track_listing(df.drop(columns="popularity_raw"), 1990)
    assert out.columns.tolist() == ["Track", "Artist", "Year"]
    assert out["Track"].tolist() == ["A", "F"]


def test_top_genres(p1):
    g = pdc.top_genres(p1)
    assert g.to_dict("records")[-1] == {"genre": "rock", "count": 2}    # ascending: biggest last
    assert set(g["genre"]) == {"rock", "indie rock", "pop"}
    assert len(pdc.top_genres(p1, 2)) == 2
    assert pdc.top_genres(p1.drop(columns="artist_genres")).empty




def test_monthly_additions_and_growth(p1):
    m = pdc.monthly_additions(p1)
    assert m.to_dict("records") == [{"period": "2024-01", "count": 2}, {"period": "2024-03", "count": 2}]
    t = pdc.growth_timeline(m)
    assert t["month"].tolist() == [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-03-01")]
    assert "month" not in m.columns
    peak = pdc.peak_month(t)
    assert (peak.label, peak.count, peak.month) == ("Jan 2024", 2, pd.Timestamp("2024-01-01"))   # tie -> first


def test_monthly_additions_without_added_at(p1):
    assert pdc.monthly_additions(p1.drop(columns="added_at")).empty


def test_single_session_date(p1):
    assert pdc.single_session_date(p1) == "January 15, 2024"
    assert pdc.single_session_date(p1.drop(columns="added_at")) is None
    assert pdc.single_session_date(p1.assign(added_at=None)) is None










def test_track_table(p1):
    t = pdc.track_table(p1)
    assert t.columns.tolist() == ["Track", "Artist", "Year", "Duration", "Popularity"]
    assert t["Duration"].tolist() == ["3:00", "4:00", "3:30", "5:00"]
    assert t["Year"].tolist() == [1990, 2000, 1985, 0]                 # NaN year -> 0
    assert str(t["Year"].dtype) == "Int64"
    assert t["Popularity"].tolist() == [80, 70, 60, 50]


def test_track_table_minimal_columns(p1):
    t = pdc.track_table(p1[["track_name", "artist_display"]])
    assert t.columns.tolist() == ["Track", "Artist"]


def test_track_table_missing_popularity_value_raises(p1):
    bad = p1.copy()
    bad["popularity_raw"] = bad["popularity_raw"].astype(float)
    bad.loc[0, "popularity_raw"] = np.nan
    with pytest.raises(Exception):
        pdc.track_table(bad)


