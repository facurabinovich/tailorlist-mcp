"""Characterization tests for core.formatting.fmt_duration."""
import pytest

from tailorlist_mcp.analysis.formatting import fmt_duration


@pytest.mark.parametrize("minutes, expected", [
    (0, "0:00"),
    (3.5, "3:30"),
    (4.0, "4:00"),
    (3.1166, "3:07"),
    (125.25, "125:15"),
])
def test_fmt_duration(minutes, expected):
    assert fmt_duration(minutes) == expected


@pytest.mark.parametrize("minutes, expected", [
    (3.999, "4:00"),
    (0.9999, "1:00"),
    (59.995, "60:00"),
])
def test_fmt_duration_rounding_rollover(minutes, expected):
    # H6 (fixed): seconds that round to 60 carry into the minutes.
    assert fmt_duration(minutes) == expected
