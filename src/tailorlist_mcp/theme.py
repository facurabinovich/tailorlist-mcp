"""The Tailorlist design system, in code: the page shell every HTML producer goes through.

ui/theme.css is the one stylesheet (tokens + shared components). The constants below are the same
hex values; tests check that theme.css, this module and the "chart style" glossary entry agree.
Every page template carries three placeholders (HEAD, BRAND, FOOTER) that `inline` fills, so a page
stays a single self-contained file (it is opened as a local file and served as an MCP App resource).
See DESIGN.md.
"""
from __future__ import annotations

import base64
import html
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

UI_DIR = Path(__file__).parent / "ui"

# ---- tokens (keep in sync with ui/theme.css; tests/test_theme.py checks it) ----
BG, PANEL, CARD, LINE = "#121212", "#181818", "#282828", "#333333"
TEXT, MUTED, DIM = "#FFFFFF", "#B3B3B3", "#888888"
ACCENT, ACCENT_HOVER, BRAND = "#1DB954", "#25D163", "#8FBF7A"
SERIES = ("#1DB954", "#3987E5", "#D95926", "#9085E9", "#C98500", "#D55181")
SERIES_OTHER = "#7A7A7A"
DANGER, WARN = "#E66767", "#F5A623"
MUTED_BAR = "#535353"      # the non-highlighted bars when one bar is highlighted (chart guide only)

TAGLINE = "Music, made to measure."
FOOTER_NOTE = "Runs on your computer. Nothing leaves it except calls to Spotify and MusicBrainz."
#: Plus Jakarta Sans (variable weight, latin subset; SIL Open Font License, see ui/fonts/OFL.txt). It is embedded in
#: every page, so pages work offline and inside the MCP App sandbox, and load nothing from a third party.
FONT_FILE = UI_DIR / "fonts" / "PlusJakartaSans-latin.woff2"

HEAD, BRAND_MARK, FOOTER = "<!--TAILORLIST_HEAD-->", "<!--TAILORLIST_BRAND-->", "<!--TAILORLIST_FOOTER-->"
PLACEHOLDERS = (HEAD, BRAND_MARK, FOOTER)

#: ui/<kind>.html -> the page name shown in the brand header and the <title>.
PAGES = {
    "overview": "Collection overview",
    "playlist": "Playlist detail",
    "draft": "Draft playlists",
    "setup": "Spotify setup",
}


def css() -> str:
    return (UI_DIR / "theme.css").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def font_face() -> str:
    data = base64.b64encode(FONT_FILE.read_bytes()).decode("ascii")
    return ('@font-face { font-family:"Plus Jakarta Sans"; font-style:normal; font-weight:200 800; font-display:swap; '
            f'src:url(data:font/woff2;base64,{data}) format("woff2"); }}\n')


def favicon_uri() -> str:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
           f'<rect width="64" height="64" rx="14" fill="{BG}"/>'
           f'<text x="29" y="46" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" '
           f'font-weight="800" font-size="42" fill="{TEXT}">T</text>'
           f'<circle cx="47" cy="43" r="5" fill="{BRAND}"/></svg>')
    return "data:image/svg+xml," + quote(svg, safe="/:=, ")


def head(title: str) -> str:
    return (
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Tailorlist · {html.escape(title)}</title>\n"
        f'<link rel="icon" type="image/svg+xml" href="{favicon_uri()}">\n'
        f"<style>\n{font_face()}{css()}</style>"
    )


def brand(title: str) -> str:
    return (
        '<header class="brand">'
        f'<div><span class="wordmark">Tailorlist<span class="dot">.</span></span>'
        f'<span class="tagline">{TAGLINE}</span> <span class="badge warn" id="demo-badge" hidden>Demo data</span></div>'
        f'<div class="brand-title">{html.escape(title)}</div></header>'
    )


def footer() -> str:
    return (
        '<footer class="site-footer">'
        '<span class="wordmark">Tailorlist<span class="dot">.</span></span>'
        f"<span>{FOOTER_NOTE}</span></footer>"
    )


def inline(page: str, title: str) -> str:
    """Fill the three placeholders of a page template. `title` is the page name ("Draft playlists")."""
    parts = {HEAD: head(title), BRAND_MARK: brand(title), FOOTER: footer()}
    return re.sub("|".join(re.escape(p) for p in PLACEHOLDERS), lambda m: parts[m.group(0)], page)


def template(kind: str) -> str:
    """ui/<kind>.html with the theme inlined (the dashboard data is embedded afterwards)."""
    return inline((UI_DIR / f"{kind}.html").read_text(encoding="utf-8"), PAGES[kind])


_SIMPLE = """<!doctype html>
<html lang="en">
<head>
<!--TAILORLIST_HEAD-->
</head>
<body class="narrow">
<!--TAILORLIST_BRAND-->
@@CARD@@
<!--TAILORLIST_FOOTER-->
</body>
</html>
"""


def simple_page(title: str, heading: str, body_html: str, tone: str = "ok") -> str:
    """A one-card page (login results, setup errors). `heading` is escaped; `body_html` is trusted
    markup (a plain string becomes one paragraph). tone: "ok" (green border) or "error" (red)."""
    if tone not in ("ok", "error"):
        raise ValueError("tone must be 'ok' or 'error'")
    body = body_html if (not body_html.strip() or body_html.lstrip().startswith("<")) else f"<p>{body_html}</p>"
    card = (f'<div class="card simple {"accent" if tone == "ok" else "danger"}">'
            f"<h1>{html.escape(heading)}</h1>{body}</div>")
    # One pass: the card is inserted after the shell is built, so its text is never scanned for placeholders.
    shell = inline(_SIMPLE, title)
    return shell.replace("@@CARD@@", card, 1)
