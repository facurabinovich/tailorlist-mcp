"""The menu, the start prompt and what_can_i_do (always a temp database, never the real cache)."""
import anyio
import pytest

from tailorlist_mcp import menu, progress, server, store

#: Tools being built in parallel; empty this set after integration.
PENDING_TOOLS: set[str] = set()
#: Registered tools that are plumbing and have no line of their own in the menu.
PLUMBING_TOOLS = {"confirm_write", "what_can_i_do"}


def _row(tid, name="Song", artist="a1", album=None, date="2000-01-01"):
    return {
        "track": {"id": tid, "name": name, "album": album or f"Album {tid}", "album_id": f"al{tid}", "release_date": date,
                  "duration_ms": 180000, "explicit": 0, "popularity": 50, "isrc": None,
                  "url": f"https://open.spotify.com/track/{tid}"},
        "artists": [(artist, artist.upper())],
        "added_at": "2024-01-01T10:00:00Z",
    }


def _save(c, n, name, rows, pid=None):
    pid = pid or str(n) * 22
    meta = {"id": pid, "name": name, "owner": {"id": "me"}, "snapshot_id": "s", "followers": None,
            "external_urls": {"spotify": f"https://open.spotify.com/playlist/{pid}"}}
    store.save_playlist(c, meta, rows, 0)


@pytest.fixture
def empty(tmp_path):
    c = store.connect(tmp_path / "cache.db")
    yield c
    c.close()


@pytest.fixture
def clean(empty):
    """Genres and years all known, Liked Songs loaded, everything organized: nothing to suggest."""
    c = empty
    _save(c, 1, "One", [_row("t1", "Alpha"), _row("t2", "Beta")])
    _save(c, 2, "Liked Songs", [_row("t1", "Alpha")], pid=store.LIKED_ID)
    store.save_artist_details(c, [{"id": "a1", "genres": ["rock"], "popularity": 1}])
    c.execute("INSERT INTO original_years (artist, song, year, checked_at) SELECT 'a1', lower(name), 2000, 1 FROM tracks")
    progress.mark_done(c, ["t1", "t2"])
    c.commit()
    return c


@pytest.fixture
def messy(empty):
    """Duplicate inside a playlist, a replaced version, genres and years not looked up, partly organized."""
    c = empty
    _save(c, 1, "One", [_row("t1", "Alpha"), _row("t1", "Alpha"), _row("t2", "Beta")])
    store.save_artist_details(c, [{"id": "a1", "genres": [], "popularity": 1}])
    c.execute("UPDATE tracks SET current_id = 'new', current_popularity = 40 WHERE id = 't2'")
    progress.mark_done(c, ["t1"])
    c.commit()
    return c


def _registered():
    async def go():
        tools = await server.mcp.list_tools()
        return {t.name for t in (tools.tools if hasattr(tools, "tools") else tools)}
    return anyio.run(go)


def test_every_menu_tool_is_registered_or_pending():
    assert menu.menu_tools() - _registered() <= PENDING_TOOLS


def test_every_registered_tool_is_in_the_menu_or_plumbing():
    assert _registered() - menu.menu_tools() <= PLUMBING_TOOLS


def test_start_prompt_has_every_menu_line_and_is_the_server_prompt():
    text = menu.start_prompt()
    for title, items in menu.MENU:
        assert title in text
        for line, _ in items:
            assert line in text
    assert server.START_PROMPT == text == server.start()
    assert text.startswith("Introduce yourself") and "offer to list them" in text


def test_status_line_with_playlists(messy):
    assert menu.status_line(messy) == "Loaded: 1 playlist, 2 unique tracks."


def test_status_line_nothing_loaded(empty):
    line = menu.status_line(empty)
    assert "first step is choosing playlists" in line and "list_my_playlists" in line and "connect_spotify" in line


def test_suggestions_empty_cache(empty):
    assert menu.suggestions(empty) == []
    text = menu.what_can_i_do(empty)
    assert "Worth doing now" not in text and "Everything Tailorlist can do" in text


def test_suggestions_nothing_off(clean):
    assert menu.suggestions(clean) == []
    assert "Worth doing now" not in menu.what_can_i_do(clean)


def test_suggestions_several_things_off_capped_in_priority_order(messy):
    found = menu.suggestions(messy)
    assert len(found) == menu.MAX_SUGGESTIONS == 3
    assert found[0].startswith("1 song is saved twice inside the same playlist: dedupe_playlist")
    assert "replace_versions" in found[1] and "fill_genres" in found[2]
    text = menu.what_can_i_do(messy)
    print(text)
    assert "Worth doing now in your library:\n1. 1 song is saved" in text
    assert "Show this to the user as a short list" in text


def test_organized_suggestion_only_between_0_and_100(clean):
    assert menu._organized(clean, []) is None                  # 100%


def test_organized_suggestion_partly_organized(messy):
    assert menu._organized(messy, []) == "50% of your library is organized: group_tracks builds drafts from the rest."


def test_liked_songs_not_loaded_is_suggested(empty):
    _save(empty, 1, "One", [_row("t1")])
    assert "Liked Songs are not loaded" in menu._liked_not_loaded(empty, [])


def test_what_can_i_do_tool_reads_the_cache_it_is_given(tmp_path, monkeypatch):
    real_connect = store.connect
    monkeypatch.setattr(store, "connect", lambda *a: real_connect(tmp_path / "cache.db"))
    assert "Everything Tailorlist can do" in server.what_can_i_do()
