"""The Phase 2b tools as the server exposes them (error messages, text sizes, reset safety)."""
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tailorlist_mcp import progress, server
from tailorlist_mcp.drafts import GroupFilters

from test_queries import conn  # noqa: F401  (fixture)


@pytest.fixture
def home(conn, tmp_path, monkeypatch):
    path = Path(conn.execute("PRAGMA database_list").fetchone()["file"])
    monkeypatch.setenv("TAILORLIST_HOME", str(path.parent))
    monkeypatch.setenv("TAILORLIST_OPEN_BROWSER", "0")
    return path.parent


def text(result) -> str:
    return result.content[0].text


def test_kmeans_is_a_clear_error(home):
    with pytest.raises(ToolError, match="audio features"):
        server.group_tracks("kmeans")
    with pytest.raises(ToolError, match="must be 'category' or 'genres'"):
        server.group_tracks("random")


def test_preview_saves_nothing_then_group_tracks_opens_the_draft(home):
    r = server.group_tracks("category", preview_only=True)
    assert "Nothing was saved" in text(r) and "4 tracks in 3 groups" in text(r) and r.structured_content["preview"]["groups"] == 3
    r = server.group_tracks("category")
    t = text(r)
    assert "Draft d1" in t and "Rock · 1990s" in t and "no genre and no release decade" in t
    assert "Saved the draft dashboard as a web page" in t and "on screen" in t       # browser opening is off in tests
    assert r.structured_content["groups"][0]["name"] == "Rock · 1990s" and "tracks" not in r.structured_content["groups"][0]
    assert (home / "draft.html").exists() and "Rock · 1990s" in (home / "draft.html").read_text(encoding="utf-8")


def test_genres_mode_without_genres_lists_them(home):
    r = server.group_tracks("genres", GroupFilters(playlists=["One"]))
    assert "indie rock (" in text(r) and "preview" in r.structured_content
    r = server.group_tracks("genres", GroupFilters(genre_families=["Pop"]))      # all pop tags
    assert "Draft d1" in text(r) and "- pop: 1 track," in text(r)


def test_filters_with_no_match_and_unknown_playlist(home):
    with pytest.raises(ToolError, match="No tracks match"):
        server.group_tracks("category", GroupFilters(decades=[1950]))
    with pytest.raises(ToolError, match="isn't loaded"):
        server.group_tracks("category", GroupFilters(playlists=["Nope"]))


def test_edit_and_show_draft_roundtrip(home):
    server.group_tracks("category")
    from pydantic import TypeAdapter
    ch = TypeAdapter(list[server.drafts.Change]).validate_python([{"op": "rename", "group": "pop", "to": "Pop hits"}])
    r = server.edit_draft("d1", ch)
    assert "renamed 'Pop · 2000s' to 'Pop hits'" in text(r) and "Refreshed the draft dashboard page" in text(r)
    assert "Pop hits" in text(server.show_draft())
    with pytest.raises(ToolError, match="Nothing changed. There is no group"):
        server.edit_draft("d1", TypeAdapter(list[server.drafts.Change]).validate_python([{"op": "drop_group", "group": "zzz"}]))
    with pytest.raises(ToolError, match="no draft"):
        server.show_draft("d9")


def test_reset_progress_needs_confirmation_and_only_clears_progress(home, conn):
    assert server.reset_progress() == "There is no progress to reset."
    progress.mark_done(conn, ["t1", "t2"])
    assert "Nothing organized" not in server.organize_progress() and "33.3%" in server.organize_progress()
    out = server.reset_progress()
    assert out.startswith("Not reset") and progress.summary(conn)["done"] == 2
    assert "2 rows cleared" in server.reset_progress(confirm=True) and progress.summary(conn)["done"] == 0
    assert conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0] == 3


def test_instructions_state_the_ok_rule():
    assert "get their OK before anything is written" in server.INSTRUCTIONS


def test_unload_playlists_leaves_spotify_alone_and_prunes_drafts(home, conn):
    server.group_tracks("category")
    before = {t for g in server.drafts.load_draft(conn, None)["groups"] for t in g["tracks"]}

    r = server.unload_playlists(["one"])
    t = text(r)
    assert "Unloaded from the local cache: One. Nothing changed on Spotify." in t
    assert "from 3 playlists" in t and "to 2 /" in t
    names = {row["name"] for row in conn.execute("SELECT name FROM playlists")}
    assert names == {"Two", "Three"}
    after = {t for g in server.drafts.load_draft(conn, None)["groups"] for t in g["tracks"]}
    loaded = {row[0] for row in conn.execute("SELECT id FROM tracks")}
    assert after <= loaded and after <= before


def test_unload_unknown_playlist_unloads_nothing(home, conn):
    with pytest.raises(ToolError, match="Nothing was unloaded"):
        server.unload_playlists(["two", "nope"])
    assert conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0] == 3
