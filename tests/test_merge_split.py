"""merge_playlists and split_playlist: drafts only, on the small hand-made cache of test_queries."""
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tailorlist_mcp import drafts, merge_split, progress, server, store

from test_queries import conn, _row  # noqa: F401  (fixture and helper)
from test_server_drafts import home, text  # noqa: F401  (fixtures and helper)


def group_ids(d):
    return [g["tracks"] for g in d["groups"]]


def test_merge_union_order_and_duplicates(conn):
    d, t = merge_split.merge_playlists(conn, ["Two", "One"])          # given order: Two first
    assert d["mode"] == "merge" and len(d["groups"]) == 1
    assert d["groups"][0]["name"] == "Two + One"
    assert group_ids(d) == [["t2", "t5", "t1", "t3", "t4"]]
    assert "7 entries in total, 5 unique tracks in the merged group, 2 exact duplicates dropped" in t
    assert "1 song is present in several different versions" in t and "dedupe_playlist" in t
    assert t.endswith("Nothing was written to Spotify. write_draft creates the playlist.")
    assert drafts.draft_label(d) == "Merge of Two + One"


def test_merge_name_done_tracks_and_sources_untouched(conn):
    progress.mark_done(conn, ["t1"])
    before = conn.execute("SELECT playlist_id, position, track_id FROM playlist_tracks").fetchall()
    d, _ = merge_split.merge_playlists(conn, ["One", "Two", "One"], name="  Mix ")
    assert d["groups"][0]["name"] == "Mix" and "t1" in d["groups"][0]["tracks"]
    assert conn.execute("SELECT playlist_id, position, track_id FROM playlist_tracks").fetchall() == before


def test_merge_needs_two_distinct_playlists(conn):
    with pytest.raises(drafts.DraftError, match="at least 2"):
        merge_split.merge_playlists(conn, ["One", "one"])
    with pytest.raises(drafts.DraftError, match="at least 2"):
        merge_split.merge_playlists(conn, ["One"])


def test_merge_with_liked_songs(conn):
    store.save_playlist(conn, {"id": store.LIKED_ID, "name": store.LIKED_NAME, "snapshot_id": "s"},
                        [_row(("t9", "Nine", ["a2"], "1980", 10, 3, None)), _row(("t1", "Alpha Song", ["a1"], "1994-05-01", 80, 3, None))], 0)
    d, _ = merge_split.merge_playlists(conn, ["liked", "Three"])
    assert group_ids(d) == [["t9", "t1", "t6"]]
    assert drafts.draft_label(d) == "Merge of Liked Songs + Three"


def test_merged_draft_works_everywhere_drafts_are_read(conn):
    d, _ = merge_split.merge_playlists(conn, ["One", "Two"])
    loaded = drafts.load_draft(conn, d["id"])
    data = drafts.draft_data(conn, loaded, full=True)
    assert data["label"] == "Merge of One + Two" and data["totals"]["grouped"] == 5
    assert "Merge of One + Two" in drafts.summary(drafts.result_data(drafts.draft_data(conn, loaded)))
    assert drafts.list_drafts(conn)[0]["label"] == "Merge of One + Two"


def test_split_by_decade_includes_done_and_keeps_source(conn):
    progress.mark_done(conn, ["t1"])
    d, t, preview = merge_split.split_playlist(conn, "One", "decade")
    names = {g["name"]: g["tracks"] for g in d["groups"]}
    assert names == {"1990s": ["t1", "t2"], "2000s": ["t3"], "2020s": ["t4"]}
    assert d["source"] == {"kind": "split", "playlist": "One"} and d["include_done"] is True
    assert "One stays untouched. write_draft creates one new playlist per group." in t
    assert drafts.draft_label(d) == "Split of One by decade"
    assert preview["groups"] == 3


def test_split_unmatched_reason_preview_and_by_aliases(conn):
    d, t, p = merge_split.split_playlist(conn, "Three", "genre family", preview_only=True)
    assert d is None and p["unmatched"] == 1 and "Nothing was saved" in t
    assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0
    d, _, _ = merge_split.split_playlist(conn, "One", "both")
    assert [(u["id"], u["reason"]) for u in d["unmatched"]] == [("t4", "no genre")]
    assert drafts.draft_label(d) == "Split of One by genre family and decade"
    with pytest.raises(drafts.DraftError, match="by must be"):
        merge_split.split_playlist(conn, "One", "color")


def test_old_drafts_without_source_still_load(conn):
    server_draft = drafts.new_draft(conn, "category", "decade", [], False,
                                    {"pool": 1, "filters": [], "done_left_out": 0}, {"1990s": ["t1"]}, [], False)
    assert "source" not in server_draft
    assert drafts.list_drafts(conn)[0]["label"] == "by category: decade"


def test_tools_end_to_end(home):
    r = server.merge_playlists(["One", "Two"], name="Both")
    t = text(r)
    assert "Draft d1 (Merge of One + Two)" in t and "5 unique tracks" in t and r.structured_content["groups"][0]["name"] == "Both"
    with pytest.raises(ToolError, match="at least 2"):
        server.merge_playlists(["One"])
    with pytest.raises(ToolError, match="isn't loaded"):
        server.merge_playlists(["One", "Nope"])
    r = server.split_playlist("One", preview_only=True)
    assert "Nothing was saved" in text(r) and r.structured_content["preview"]["groups"] == 3
    r = server.split_playlist("One", "decade")
    assert "Split of One by decade" in text(r) and "One stays untouched" in text(r)
    assert json.loads(server.store.connect().execute("SELECT data FROM drafts WHERE id='d2'").fetchone()[0])["source"]["playlist"] == "One"
