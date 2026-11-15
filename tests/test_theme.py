"""The design system: every page goes through theme.py, carries the shell, and colours live in one place."""
import re
from pathlib import Path

import pytest

from tailorlist_mcp import browser, dashboard, drafts, glossary, theme
from tailorlist_mcp.sources import spotify_auth, spotify_setup

from test_queries import conn  # noqa: F401  (fixture)

SRC = Path(theme.__file__).parent
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
TEMPLATES = sorted((SRC / "ui").glob("*.html"))


def _draft_data(conn):
    pool, counts = drafts.build_pool(conn, None)
    groups, unmatched = drafts.make_groups(conn, pool, counts, "category", "both", [], False)
    d = drafts.new_draft(conn, "category", "both", [], False, counts, groups, unmatched, False)
    return drafts.draft_data(conn, d, full=True)


def _pages(conn, tmp_path):
    """Every page the product makes, as (name, html)."""
    out = {}
    for kind, data in (("overview", dashboard.overview_data(conn)),
                       ("playlist", dashboard.playlist_data(conn, "One")),
                       ("draft", _draft_data(conn))):
        out[kind] = browser.write_page(data, tmp_path / f"{kind}.html", kind=kind).read_text(encoding="utf-8")
    out["setup"] = spotify_setup.render_page("http://127.0.0.1:9999/callback", "tok", error="Bad id", value="x")
    out["login-ok"] = spotify_auth.result_page("Connected", "You can close this tab.")
    out["login-error"] = spotify_auth.result_page("Login failed", "Try again.")
    out["setup-error"] = spotify_setup._result_page("Not allowed", "Open the setup page again.", back=True)
    return out


def test_templates_carry_the_placeholders_and_no_colours():
    assert {t.stem for t in TEMPLATES} == set(theme.PAGES) | {"setup"} and TEMPLATES
    for t in TEMPLATES:
        text = t.read_text(encoding="utf-8")
        for ph in theme.PLACEHOLDERS:
            assert text.count(ph) == 1, f"{t.name} needs exactly one {ph}"
        assert '<html lang="en">' in text
        assert not HEX.search(text), f"{t.name} has a colour literal: {HEX.search(text).group(0)}"
        assert "rgba(" not in text and "<link" not in text


def test_python_page_strings_have_no_colours():
    for name in ("sources/spotify_auth.py", "sources/spotify_setup.py", "browser.py"):
        text = (SRC / name).read_text(encoding="utf-8")
        assert not HEX.search(text), f"{name} has a colour literal"


def test_every_page_is_complete(conn, tmp_path):
    for name, page in _pages(conn, tmp_path).items():
        assert "<!--TAILORLIST_" not in page, name
        assert re.search(r"<title>Tailorlist · [^<]+</title>", page), name
        assert 'lang="en"' in page and 'rel="icon"' in page and "data:image/svg+xml" in page, name
        assert 'class="brand"' in page and "Music, made to measure." in page, name
        assert 'class="wordmark">Tailorlist<span class="dot">.</span>' in page, name
        assert 'class="site-footer"' in page and theme.FOOTER_NOTE in page, name
        assert "@font-face" in page and "data:font/woff2;base64," in page, name
        assert "fonts.googleapis.com" not in page and "fonts.gstatic.com" not in page, name   # nothing loaded from a third party
        for token in ("--bg:#121212", "--accent:#1DB954", "--brand:#8FBF7A", "--series-6:#D55181", "--danger:#E66767"):
            assert token in page, (name, token)


def test_brand_title_matches_the_page(conn, tmp_path):
    pages = _pages(conn, tmp_path)
    for kind in theme.PAGES:
        assert f'<div class="brand-title">{theme.PAGES[kind]}</div>' in pages[kind]
        assert f"<title>Tailorlist · {theme.PAGES[kind]}</title>" in pages[kind]


def test_simple_page_tones_and_escaping():
    ok = theme.simple_page("Spotify login", "Connected", "Done.")
    err = theme.simple_page("Spotify login", "<b>x</b>", "Oops", tone="error")
    assert "card simple accent" in ok and "card simple danger" in err
    assert "&lt;b&gt;x&lt;/b&gt;" in err and "<p>Oops</p>" in err
    with pytest.raises(ValueError):
        theme.simple_page("t", "h", "b", tone="loud")


def test_embedded_data_is_never_taken_for_a_placeholder(conn, tmp_path):
    data = dashboard.overview_data(conn)
    data["playlists"][0]["name"] = "<!--TAILORLIST_FOOTER--> </script>"
    page = browser.write_page(data, tmp_path / "o.html", kind="overview").read_text(encoding="utf-8")
    assert page.count('class="site-footer"') == 1 and "<\\/script>" in page


def _css_tokens() -> dict[str, str]:
    root = re.search(r":root\s*\{(.*?)\n\}", theme.css(), re.S).group(1)
    return {m.group(1): m.group(2) for m in re.finditer(r"--([\w-]+)\s*:\s*(#[0-9A-Fa-f]{6})\b", root)}


def test_css_has_no_colours_outside_root():
    css = theme.css()
    outside = css.replace(re.search(r":root\s*\{.*?\n\}", css, re.S).group(0), "")
    assert not HEX.search(outside)
    assert not re.search(r"rgba?\(\s*\d", outside)     # use the --accent-rgb token


def test_theme_py_and_css_agree():
    tokens = _css_tokens()
    expected = {
        "bg": theme.BG, "panel": theme.PANEL, "card": theme.CARD, "line": theme.LINE,
        "text": theme.TEXT, "muted": theme.MUTED, "dim": theme.DIM,
        "accent": theme.ACCENT, "accent-hover": theme.ACCENT_HOVER, "brand": theme.BRAND,
        "series-other": theme.SERIES_OTHER, "danger": theme.DANGER, "warn": theme.WARN,
        **{f"series-{i}": c for i, c in enumerate(theme.SERIES, start=1)},
    }
    for name, value in expected.items():
        assert tokens.get(name, "").upper() == value.upper(), name
    assert len(theme.SERIES) == 6 and theme.SERIES[0] == theme.ACCENT
    rgb = re.search(r"--accent-rgb:\s*(\d+),\s*(\d+),\s*(\d+)", theme.css()).groups()
    assert tuple(int(x) for x in rgb) == tuple(int(theme.ACCENT[i:i + 2], 16) for i in (1, 3, 5))


def test_glossary_chart_style_uses_the_same_constants():
    text = glossary.explain("chart style")
    assert text.startswith("Tailorlist chart style")
    for alias in ("charts", "chart", "design"):
        assert glossary.explain(alias) == text
    hexes = {h.upper() for h in HEX.findall(text)}
    allowed = {theme.BG, theme.CARD, theme.LINE, theme.TEXT, theme.MUTED, theme.ACCENT, theme.MUTED_BAR,
               theme.SERIES_OTHER, theme.BRAND, *theme.SERIES}
    assert hexes == {h.upper() for h in allowed}
    assert ", ".join(theme.SERIES) in text
    tokens = _css_tokens()
    for h in hexes - {theme.MUTED_BAR.upper()}:
        assert h in {v.upper() for v in tokens.values()}, h
    assert "\n\n" not in text
