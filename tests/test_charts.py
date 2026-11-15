"""Text bars, chart hints and where the data tools put them."""
import pytest

from tailorlist_mcp import charts, queries
from tailorlist_mcp.charts import FALLBACK_TABLE_ONLY, FALLBACK_WITH_BARS, HINT_PREFIX
from tailorlist_mcp.queries import Filters
from tests.test_queries import conn  # noqa: F401  (fixture: temp database, never the real cache)


def test_bar_scaling_eighths_and_caps():
    assert charts.bar(100, 100) == "█" * 12
    assert charts.bar(50, 100) == "█" * 6
    assert charts.bar(50, 100, width=4) == "██"
    assert charts.bar(1, 8, width=1) == "▏"
    assert charts.bar(5, 8, width=1) == "▋"
    assert charts.bar(7, 8, width=1) == "▉"
    assert charts.bar(150, 100) == "█" * 12
    assert len(charts.bar(99.9, 100)) <= 12


def test_bar_minimum_and_empty():
    assert charts.bar(0.0001, 1000) == "▏"
    for v in (0, -3, None, float("nan")):
        assert charts.bar(v, 10) == ""
    assert charts.bar(5, 0) == "" and charts.bar(5, None) == ""


def H(group, split=None, metrics=("unique_tracks", "share_%"), rows=5, groups=5, splits=0):
    return charts.stats_hint(group, split, list(metrics), rows, groups, splits)


def test_stats_hint_none_under_three_rows():
    assert H("decade", rows=2) is None and H("artist", rows=1) is None
    assert H("decade", rows=3) is not None


def test_stats_hint_ordered_dimensions():
    for g in ("decade", "popularity_bin"):
        assert f"column chart, x = {g} in the order given, y = unique_tracks" in H(g)
    for g in ("year", "month_added"):
        assert f"line chart (or columns if there are few points), x = {g} in order, y = unique_tracks" in H(g)


def test_stats_hint_categorical_and_top_15():
    for g in ("artist", "album", "genre", "genre_family", "playlist"):
        assert f"horizontal bar chart, one bar per {g}, sorted as given (biggest first), value = unique_tracks" in H(g)
    assert "top 15" not in H("artist", rows=15)
    assert "chart the top 15" in H("artist", rows=16).lower()


def test_stats_hint_split():
    h = H("decade", "playlist", rows=12, groups=4, splits=3)
    assert "grouped or stacked column chart: x = decade" in h and "one colour per playlist, y = unique_tracks" in h
    assert H("decade", "playlist", splits=5).startswith(HINT_PREFIX + "stacked column chart: x = decade")
    h = H("artist", "decade", splits=2)
    assert "grouped or stacked horizontal bars" in h and "one bar per artist" in h
    assert "stacked horizontal bars" in H("artist", "decade", splits=6)
    assert "top 15" in H("artist", "decade", rows=60, groups=20, splits=3).lower()


def test_stats_hint_share_genre_and_metric_labels():
    assert "no pie chart" in H("genre", metrics=("share_%",))
    assert "no pie chart" in H("decade", "genre_family", metrics=("share_%",), splits=5)
    assert "no pie chart" not in H("genre", metrics=("unique_tracks",))
    assert "no pie chart" not in H("decade", metrics=("share_%",))
    h = H("decade", metrics=("share_%", "hours"))
    assert "y = share_%" in h and "Chart share_%; mention the other metrics" in h
    assert "mention the other metrics" not in H("decade", metrics=("hours",))


def test_stats_hint_shape():
    h = H("artist", "decade", metrics=("share_%", "hours", "entries"), rows=60, groups=20, splits=3)
    assert h.startswith(HINT_PREFIX) and h.endswith(FALLBACK_TABLE_ONLY) and "\n" not in h and len(h) < 400
    assert len(H("decade")) < 250


def test_collection_stats_bar_column_and_hint(conn):
    out = queries.collection_stats(conn, "decade").splitlines()
    assert out[1].endswith("| share_% | bar") or out[1] == "decade | unique_tracks | share_% | bar"
    assert out[2].endswith("████████████") and out[3].split(" | ")[-1] == "██████"   # 2 vs 1 unique track
    assert out[-1].startswith(HINT_PREFIX) and out[-1].endswith(FALLBACK_WITH_BARS)
    assert "y = unique_tracks" in out[-1]


def test_collection_stats_bar_follows_first_metric(conn):
    out = queries.collection_stats(conn, "playlist", metrics=["hours", "unique_tracks"]).splitlines()
    assert "value = hours" in out[-1] and "mention the other metrics" in out[-1]
    assert out[1] == "playlist | hours | unique_tracks | bar"


def test_collection_stats_split_has_no_bar(conn):
    out = queries.collection_stats(conn, "decade", split_by="playlist").splitlines()
    assert out[1] == "decade | playlist | unique_tracks | share_%"
    assert out[-1].startswith(HINT_PREFIX) and "one colour per playlist" in out[-1]


def test_collection_stats_no_hint_under_three_rows_or_on_errors(conn):
    out = queries.collection_stats(conn, "decade", filters=Filters(playlists=["two"])).splitlines()   # 2 decades
    assert out[1].endswith("| bar") and not any(line.startswith(HINT_PREFIX) for line in out)
    assert HINT_PREFIX not in queries.collection_stats(conn, "decade", filters=Filters(playlists=["three"]))  # no year at all
    empty = queries.collection_stats(conn, "decade", filters=Filters(artist="nobody at all"))
    assert HINT_PREFIX not in empty and empty.startswith("No tracks match")


def test_compare_hint_rules(conn):
    out = queries.compare_playlists(conn).splitlines()
    assert out[-1].startswith(HINT_PREFIX) and out[-1].endswith(FALLBACK_TABLE_ONLY)
    assert "Unique tracks, Avg pop and Year median" in out[-1] and "heatmap" in out[-1]
    one = queries.compare_playlists(conn, ["One"])
    assert HINT_PREFIX not in one
    no_pairs = queries.compare_playlists(conn, ["One", "Three"])
    assert HINT_PREFIX in no_pairs and "heatmap" not in no_pairs
    assert charts.compare_hint(1, True) is None


def test_find_tracks_hint_rules(conn):
    out = queries.find_tracks(conn, None, sort="popularity", limit=6).splitlines()
    assert out[-1].startswith(HINT_PREFIX) and "top 10 by Pop" in out[-1] and out[-1].endswith(FALLBACK_TABLE_ONLY)
    assert "top 10 by Added" in queries.find_tracks(conn, None, sort="added").splitlines()[-1]
    assert HINT_PREFIX not in queries.find_tracks(conn, None, sort="popularity", limit=4)   # fewer than 5 rows
    assert HINT_PREFIX not in queries.find_tracks(conn, None, sort="name")                  # sort not chartable
    assert HINT_PREFIX not in queries.find_tracks(conn, Filters(artist="nobody at all"))
    assert charts.tracks_hint("year", 5) is not None and charts.tracks_hint("year", 4) is None


def test_other_tools_get_no_hint(conn):
    assert HINT_PREFIX not in queries.find_duplicates(conn)
