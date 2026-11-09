"""Track-frame helpers ported from the Tailorlist app's core/tracks.py: release-year parsing
and the "Primary feat. Secondary" artist display."""
from __future__ import annotations

import pandas as pd


def parse_release_year(release_date_series: pd.Series) -> pd.Series:
    """
    Robustly extract year from Spotify release_date which can be:
    - "2014"          (year only)
    - "2014-03"       (year-month)
    - "2014-03-15"    (full date)
    Empty/missing values and implausible years (<= 1900) become None.
    """
    def _extract(val):
        if pd.isna(val) or val == "":
            return None
        val = str(val).strip()
        # Year only: "2014"
        if len(val) == 4 and val.isdigit():
            return int(val)
        # Year-month: "2014-03"
        if len(val) == 7:
            try:
                return int(val[:4])
            except ValueError:
                return None
        # Full date: "2014-03-15"
        try:
            return pd.to_datetime(val, errors="coerce").year
        except Exception:
            return None

    result = release_date_series.apply(_extract)
    # Replace implausible years with None
    return result.where(result > 1900, other=None)


def add_artist_display(df: pd.DataFrame) -> pd.DataFrame:
    """Add artist_display column: 'Primary feat. Secondary' when secondary artists exist."""
    if "secondary_artists" not in df.columns:
        df["artist_display"] = df["artist_name"]
        return df
    mask = df["secondary_artists"].notna() & (df["secondary_artists"] != "")
    df["artist_display"] = df["artist_name"].copy()
    df.loc[mask, "artist_display"] = (
        df.loc[mask, "artist_name"] + " feat. " + df.loc[mask, "secondary_artists"]
    )
    return df
