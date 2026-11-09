"""analysis/tracks.py (ported from core/tracks.py): release-year parsing, artist display."""
import pandas as pd
import pytest

from tailorlist_mcp.analysis.tracks import add_artist_display, parse_release_year


@pytest.mark.parametrize("raw,expected", [
    ("2014", 2014), ("2014-03", 2014), ("2014-03-15", 2014),
    ("", None), (None, None), ("1800", None), ("1900", None), ("1901", 1901),
    (" 2014 ", 2014), ("garbage", None), ("abcd-ef", None),
])
def test_parse_release_year(raw, expected):
    out = parse_release_year(pd.Series([raw]))
    assert (out.iloc[0] == expected) if expected is not None else pd.isna(out.iloc[0])


def test_parse_release_year_keeps_index_and_length():
    s = pd.Series(["2001", None, "1999-05"], index=[5, 6, 7])
    out = parse_release_year(s)
    assert list(out.index) == [5, 6, 7]
    assert out[5] == 2001 and pd.isna(out[6]) and out[7] == 1999


def test_add_artist_display():
    df = pd.DataFrame({"artist_name": ["A", "B", "C"], "secondary_artists": ["X, Y", "", None]})
    assert list(add_artist_display(df)["artist_display"]) == ["A feat. X, Y", "B", "C"]
    plain = add_artist_display(pd.DataFrame({"artist_name": ["A"]}))
    assert list(plain["artist_display"]) == ["A"]
