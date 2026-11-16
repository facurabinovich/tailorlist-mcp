"""The demo collection (always a temp database, never the real cache; nothing here touches the network)."""
import json

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tailorlist_mcp import (browser, collection, dashboard, demo, drafts, genre_fill, health, menu, merge_split,
                            orphans, queries, server, store, write, years)

from test_collection import FakeClient, PL_A


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A temp data directory for the server tools: no login, no network, no browser."""
    monkeypatch.setenv("TAILORLIST_HOME", str(tmp_path))
    monkeypatch.setenv("TAILORLIST_OPEN_BROWSER", "0")
    for key in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REDIRECT_URI"):
        monkeypatch.delenv(key, raising=False)

    def no_network(*args, **kwargs):
        raise AssertionError("the demo must not call Spotify or MusicBrainz")

    monkeypatch.setattr(server, "_client", no_network)
    monkeypatch.setattr(genre_fill.FILL, "start", no_network)
    monkeypatch.setattr(years.FILL, "start", no_network)
    return tmp_path


@pytest.fixture
def conn(home):
    c = store.connect(home / "cache.db")
    yield c
    c.close()


@pytest.fixture
def loaded(conn):
    demo.load(conn)
    return conn


def text(result) -> str:
    return result.content[0].text


# ---------------------------------------------------------------- the generator

def test_build_is_deterministic():
    assert demo.build(7) == demo.build(7)
    assert demo.build(7) != demo.build(8)


def test_size_and_ids():
    data = demo.build()
    assert 8 <= len(data) <= 10
    ids = {row["track"]["id"] for _, rows in data for row in rows}
    assert 500 <= len(ids) <= 700
    assert all(i.startswith(demo.DEMO_PREFIX) and len(i) == 22 for i in ids)
    assert all(p["id"] == store.LIKED_ID or p["id"].startswith(demo.DEMO_PREFIX) for p, _ in data)
    assert all(demo.is_demo_id(i) for i in ids)
    assert not demo.is_demo_id("0" * 22)


def test_no_famous_names():
    gen = demo._generate()
    assert not [n for n in gen.facts["artists"].values() if demo._famous_artist(n)]
    names = {row["track"]["name"] for _, rows in gen.playlists for row in rows}
    assert not [n for n in names if demo._famous_song(n)]


# ---------------------------------------------------------------- every tool has something to show

def test_load_is_idempotent(conn):
    first = demo.load(conn)
    counts = conn.execute("SELECT (SELECT COUNT(*) FROM playlists), (SELECT COUNT(*) FROM tracks), "
                          "(SELECT COUNT(*) FROM playlist_tracks), (SELECT COUNT(*) FROM artists)").fetchone()[:]
    assert demo.load(conn) == first
    again = conn.execute("SELECT (SELECT COUNT(*) FROM playlists), (SELECT COUNT(*) FROM tracks), "
                         "(SELECT COUNT(*) FROM playlist_tracks), (SELECT COUNT(*) FROM artists)").fetchone()[:]
    assert again == counts
    assert demo.is_demo(conn) and not demo.has_real_playlists(conn)


def test_the_planted_problems_are_found(loaded):
    c = loaded
    report = health.library_health(c)
    for expected in ("Exact duplicates inside a playlist", "Different versions", "Replaced album versions",
                     "not playable", "Artists without genres", "Doubtful years", "Near-identical playlists"):
        assert expected in report
    assert "Exact duplicates inside a playlist (same track saved twice): 0" not in report
    dup = queries.find_duplicates(c)
    assert "Exact duplicates:" in dup and "Different versions" in dup
    assert "in no loaded playlist" in orphans.find_orphans(c)
    doubtful = queries.find_tracks(c, queries.Filters(doubtful_year=True))
    assert "compilation album" in doubtful and "Greatest Hits" in doubtful
    replaced = queries.find_replaced_tracks(c)
    assert "Replaced:" in replaced and "(Deluxe Edition)" in replaced
    compared = queries.compare_playlists(c)
    assert "Road trip" in compared and "Summer drive" in compared and "Liked Songs" in compared


def test_group_split_and_what_can_i_do(loaded, home):
    pool, counts = drafts.build_pool(loaded, None, False)
    groups, unmatched = drafts.make_groups(loaded, pool, counts, "category", "both", [], False)
    assert len(groups) >= 5
    _, preview_text, preview = merge_split.split_playlist(loaded, "Road trip", "both", True)
    assert preview and "groups" in preview_text
    out = menu.what_can_i_do(loaded)
    assert out.startswith(demo.status(loaded)) and "demo collection" in out and "Worth doing now" in out


def test_draft_tools_work_on_the_demo(loaded):
    r = server.group_tracks("category")
    assert "Draft d1" in text(r) and r.structured_content["demo"] is True
    r = server.merge_playlists(["Road trip", "Workout"])
    assert r.structured_content["demo"] is True


def test_data_tools_do_not_call_the_network(loaded):
    assert demo.FILLED_IN in server.fill_genres()
    assert demo.FILLED_IN in server.fill_years()
    out = anyio.run(server.find_replaced_tracks)
    assert demo.FILLED_IN in out and "Replaced:" in out


# ---------------------------------------------------------------- the server tools

def test_load_demo_tool(home):
    out = server.load_demo()
    assert "invented artists and songs" in out and "Road trip: " in out
    assert "what_can_i_do" in out and "connect_spotify" in out
    assert out == server.load_demo()               # reloading gives the same text


def test_load_demo_refused_over_real_playlists(conn, home):
    collection.load_playlists(FakeClient(), conn, [PL_A])
    with pytest.raises(demo.DemoError, match="your own playlists"):
        demo.load(conn)
    with pytest.raises(ToolError, match="your own playlists"):
        server.load_demo()
    assert not demo.is_demo(conn)


def test_loading_real_playlists_clears_the_demo(loaded):
    drafts_before = server.group_tracks("category")                    # a draft made from the demo
    assert drafts_before.structured_content["demo"]
    out = collection.load_playlists(FakeClient(), loaded, [PL_A])
    assert out["cleared_demo"] is True
    assert not demo.is_demo(loaded) and demo.has_real_playlists(loaded)
    assert [r["name"] for r in loaded.execute("SELECT name FROM playlists")] == ["Alpha"]
    assert loaded.execute("SELECT COUNT(*) FROM tracks WHERE id LIKE 'demo%'").fetchone()[0] == 0
    assert loaded.execute("SELECT COUNT(*) FROM original_years").fetchone()[0] == 0
    assert loaded.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0
    assert collection.load_playlists(FakeClient(), loaded, [PL_A])["cleared_demo"] is False


def test_clear_leaves_real_playlists_alone(conn):
    collection.load_playlists(FakeClient(), conn, [PL_A])
    assert demo.clear(conn) is False
    assert collection.overview(conn)["playlists"] == 1


WRITE_CALLS = [
    ("create_playlist", ("New", ["t1"])),
    ("add_tracks", ("Road trip", ["t1"])),
    ("remove_tracks", ("Road trip", ["t1"])),
    ("replace_versions", ()),
    ("sort_playlist", ("Road trip", "year")),
    ("dedupe_playlist", ("Road trip",)),
    ("write_draft", ("d1",)),
    ("confirm_write", ("ABCD",)),
    ("undo_last_write", ()),
]


@pytest.mark.parametrize("tool,args", WRITE_CALLS, ids=[t for t, _ in WRITE_CALLS])
def test_every_write_tool_refuses_the_demo(loaded, tool, args):
    with pytest.raises(ToolError) as exc:
        anyio.run(getattr(server, tool), *args)
    assert str(exc.value) == demo.ONLY_FOR_DEMO


def test_every_registered_write_tool_is_covered():
    registered = {t.name for t in anyio.run(server.mcp.list_tools)}
    assert {name for name, _ in WRITE_CALLS} <= registered
    with pytest.raises(write.WriteError):
        c = store.connect(":memory:")
        demo.load(c)
        write.refuse_demo(c)


def test_list_my_playlists_shows_the_demo_when_not_connected(loaded):
    out = server.list_my_playlists()
    assert out["demo"] is True and out["playlists"][0]["name"] == "Liked Songs"
    assert {p["name"] for p in out["playlists"]} >= {"Road trip", "Summer drive", "Workout"}
    assert all(p["tracks"] for p in out["playlists"])


def test_list_my_playlists_is_real_when_connected(loaded, monkeypatch):
    monkeypatch.setattr(server, "_connected", lambda: True)
    with pytest.raises(AssertionError, match="must not call"):          # went on to ask Spotify (our trap)
        server.list_my_playlists()


def test_forget_my_data_removes_the_demo(home):
    server.load_demo()
    assert (home / "cache.db").exists()
    server.forget_my_data()
    with __import__("contextlib").closing(store.connect()) as c:
        assert not demo.is_demo(c)
        assert c.execute("SELECT COUNT(*) FROM playlists").fetchone()[0] == 0


def test_menu_status_line_offers_the_demo_when_nothing_is_loaded(conn):
    assert "load_demo" in menu.status_line(conn)
    assert "load_demo" in menu.menu_tools()


def test_instructions_stay_short():
    assert len(server.INSTRUCTIONS) < 2450 and "load_demo" in server.INSTRUCTIONS


# ---------------------------------------------------------------- the dashboards

def test_dashboards_carry_the_demo_flag(loaded, home):
    overview = server.show_overview()
    assert overview.structured_content["demo"] is True and text(overview).startswith(demo.status(loaded))
    assert json.loads(_embedded(home / "overview.html"))["demo"] is True
    playlist = server.show_playlist("Road trip")
    assert playlist.structured_content["demo"] is True and text(playlist).startswith(demo.status(loaded))
    assert json.loads(_embedded(home / "playlist.html"))["demo"] is True
    draft = server.show_draft(drafts.load_draft(loaded, None)["id"]) if _has_draft(loaded) else None
    assert draft is None or draft.structured_content["demo"] is True


def _has_draft(conn) -> bool:
    return bool(conn.execute("SELECT 1 FROM drafts").fetchone())


def _embedded(path) -> str:
    html = path.read_text(encoding="utf-8")
    start = html.index('<script id="tailorlist-data" type="application/json">') + len('<script id="tailorlist-data" type="application/json">')
    return html[start:html.index("</script>", start)].replace("<\\/", "</")


def test_show_draft_after_a_draft(loaded, home):
    server.group_tracks("category")
    r = server.show_draft()
    assert r.structured_content["demo"] is True
    assert json.loads(_embedded(home / "draft.html"))["demo"] is True


def test_no_demo_flag_with_real_data(conn, home):
    collection.load_playlists(FakeClient(), conn, [PL_A])
    r = server.show_overview()
    assert "demo" not in r.structured_content and "Demo" not in text(r)
    assert "demo" not in json.loads(_embedded(home / "overview.html"))


def test_the_badge_is_in_the_header_and_hidden_by_default():
    html = browser.theme.template("overview")
    assert 'id="demo-badge" hidden>Demo data<' in html
    for kind in ("overview", "playlist", "draft"):
        assert 'getElementById("demo-badge").hidden = !data.demo' in (browser.theme.UI_DIR / f"{kind}.html").read_text(encoding="utf-8")
