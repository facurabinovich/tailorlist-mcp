"""Text bars and chart hints for the data tools.

An MCP server cannot make the host draw a chart, so each table that deserves one carries a
one-line hint (last line of the result) telling the model what to draw, and collection_stats
also gets a text bar column for hosts that cannot draw.
"""
from __future__ import annotations

import math

HINT_PREFIX = "Chart: "
FALLBACK_WITH_BARS = "If you can't draw charts here, show the table with its bar column instead."
FALLBACK_TABLE_ONLY = "If you can't draw charts here, show the table."

MIN_ROWS = 3
TOP_N = 15
_PARTIAL = "▏▎▍▌▋▊▉"  # 1/8 .. 7/8 of a block
_COLUMNS = {"decade", "popularity_bin"}
_LINES = {"year", "month_added"}


def bar(value, maximum, width: int = 12) -> str:
    """Horizontal bar of `value` scaled to `maximum`, in eighths of a block; a non-zero value shows at least ▏."""
    try:
        if value is None or maximum is None or math.isnan(value) or math.isnan(maximum) or value <= 0 or maximum <= 0:
            return ""
    except TypeError:
        return ""
    eighths = min(width * 8, max(1, int(value / maximum * width * 8)))
    full, rest = divmod(eighths, 8)
    return "█" * full + (_PARTIAL[rest - 1] if rest else "")


def _finish(parts: list[str], fallback: str) -> str:
    return HINT_PREFIX + " ".join(parts) + " " + fallback


def stats_hint(group_by: str, split_by: str | None, metrics: list[str], n_rows: int,
               n_groups: int, n_splits: int = 0) -> str | None:
    """Chart hint for a collection_stats table; `metrics` are the header labels (e.g. share_%)."""
    if n_rows < MIN_ROWS:
        return None
    first = metrics[0]
    ordered = group_by in _COLUMNS or group_by in _LINES
    if split_by:
        if ordered:
            kind = "grouped or stacked column chart" if 2 <= n_splits <= 4 else "stacked column chart"
            text = f"{kind}: x = {group_by} in order, one colour per {split_by}, y = {first}."
        else:
            kind = "grouped or stacked horizontal bars" if 2 <= n_splits <= 4 else "stacked horizontal bars"
            text = f"{kind}: one bar per {group_by}, one colour per {split_by}, value = {first}."
        if not ordered and n_groups > TOP_N:
            text += f" Chart the top {TOP_N}."
    elif group_by in _COLUMNS:
        text = f"column chart, x = {group_by} in the order given, y = {first}."
    elif group_by in _LINES:
        text = f"line chart (or columns if there are few points), x = {group_by} in order, y = {first}."
    else:
        text = f"horizontal bar chart, one bar per {group_by}, sorted as given (biggest first), value = {first}."
        if n_rows > TOP_N:
            text += f" Chart the top {TOP_N}."
    parts = [text]
    if first == "share_%" and ("genre" in (group_by, split_by) or "genre_family" in (group_by, split_by)):
        parts.append("Shares overlap (a track has several genres), so no pie chart.")
    if len(metrics) > 1:
        parts.append(f"Chart {first}; mention the other metrics in the text.")
    # A split table has no bar column (see collection_stats).
    return _finish(parts, FALLBACK_TABLE_ONLY if split_by else FALLBACK_WITH_BARS)


def compare_hint(n_playlists: int, has_pairs: bool) -> str | None:
    if n_playlists < 2:
        return None
    parts = ["one small bar chart per metric the user cares about (start with Unique tracks, Avg pop and Year median), one bar per playlist."]
    if has_pairs:
        parts.append("The shared-tracks pairs make a heatmap (playlist x playlist) when there are 4 or more playlists.")
    return _finish(parts, FALLBACK_TABLE_ONLY)


_TRACK_SORTS = {"popularity": "Pop", "year": "Year", "duration": "Duration", "added": "Added"}


def tracks_hint(sort: str, n_rows: int) -> str | None:
    if n_rows < 5 or sort not in _TRACK_SORTS:
        return None
    return _finish([f"optional: bar chart of the top 10 by {_TRACK_SORTS[sort]}."], FALLBACK_TABLE_ONLY)
