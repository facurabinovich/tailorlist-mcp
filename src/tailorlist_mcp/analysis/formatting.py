"""Display formatting helpers (pure, no streamlit)."""


def fmt_duration(minutes: float) -> str:
    """Format decimal minutes as m:ss (rounds total seconds, so 3.999 -> "4:00")."""
    total_sec = int(round(minutes * 60))
    m, s = divmod(total_sec, 60)
    return f"{m}:{s:02d}"


def short_name(name: str, max_len: int = 22) -> str:
    """Truncate to max_len characters, ending in an ellipsis when cut."""
    return name if len(name) <= max_len else name[:max_len - 1] + "…"
