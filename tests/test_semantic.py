"""assign_tracks: Claude places tracks in groups, a batch at a time, into a normal draft."""
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tailorlist_mcp import drafts, progress, semantic, server, store, write

from test_queries import conn  # noqa: F401  (fixture: 6 tracks t1..t6, playlists One, Two, Three)
from test_server_drafts import home, text  # noqa: F401  (fixtures and helper)
from test_write import FakeSpotify, code_of, load, pid, sp, tid  # noqa: F401


def ids_in_batch(t: str) -> list[str]:
    """The track IDs of the batch table in a result text, in order."""
    rows = t.split("Track ID | Track | Artist | Year | Genres | Pop\n")[1].splitlines()
    return [r.split(" | ")[0] for r in rows]


def group_map(d):
    return {g["name"]: g["tracks"] for g in d["groups"]}


# ---------------------------------------------------------------- start

def test_start_saves_a_draft_with_everything_pending(conn):
    d = semantic.start(conn, "  moods for a\n road trip ", None, False)
    assert d["mode"] == "semantic" and d["groups"] == []
    assert d["source"] == {"kind": "semantic", "criteria": "moods for a road trip"}
    assert sorted(u["id"] for u in d["unmatched"]) == ["t1", "t2", "t3", "t4", "t5", "t6"]
    assert {u["reason"] for u in d["unmatched"]} == {"not placed yet"} and {u["kind"] for u in d["unmatched"]} == {"missing"}
    assert drafts.draft_label(d) == "By Claude: moods for a road trip"
    assert drafts.load_draft(conn, d["id"]) == d


def test_start_needs_criteria_and_a_pool(conn):
    for bad in (None, "", "   "):
        with pytest.raises(drafts.DraftError, match="criteria"):
            semantic.start(conn, bad, None, False)
    with pytest.raises(drafts.DraftError, match="No tracks match"):
        semantic.start(conn, "x", drafts.GroupFilters(decades=[1950]), False)
    assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0


def test_start_refuses_a_pool_that_is_too_large(conn, monkeypatch):
    monkeypatch.setattr(semantic, "MAX_POOL", 5)
    with pytest.raises(drafts.DraftError, match="6 tracks is too many.*max 5.*narrow the filters"):
        semantic.start(conn, "x", None, False)
    d = semantic.start(conn, "x", drafts.GroupFilters(playlists=["One"]), False)        # 4 tracks
    assert len(d["unmatched"]) == 4


def test_start_filters_and_done_tracks(conn):
    progress.mark_done(conn, ["t1"])
    d = semantic.start(conn, "x", drafts.GroupFilters(playlists=["One"]), False)
    assert sorted(u["id"] for u in d["unmatched"]) == ["t2", "t3", "t4"]
    d = semantic.start(conn, "x", drafts.GroupFilters(playlists=["One"]), True)
    assert sorted(u["id"] for u in d["unmatched"]) == ["t1", "t2", "t3", "t4"] and d["include_done"] is True


# ---------------------------------------------------------------- assigning

def test_assigning_creates_groups_and_shrinks_pending(conn):
    d = semantic.start(conn, "moods", None, False)
    rep = semantic.assign(conn, d, {"Chill": ["t3", "t1"], "Run": ["t2"]})
    assert rep == {"placed": 3, "skipped": [], "duplicates": []}
    d = drafts.load_draft(conn, d["id"])                                 # it was saved
    assert group_map(d) == {"Chill": ["t1", "t3"], "Run": ["t2"]}
    assert sorted(u["id"] for u in d["unmatched"]) == ["t4", "t5", "t6"]
    rep = semantic.assign(conn, d, {"chill": ["t4"], "Run": ["t5"]})      # existing groups by name, case-insensitive
    d = drafts.load_draft(conn, d["id"])
    assert group_map(d) == {"Chill": ["t1", "t3", "t4"], "Run": ["t2", "t5"]} and rep["placed"] == 2


def test_a_new_name_that_is_part_of_another_still_makes_its_own_group(conn):
    d = semantic.start(conn, "moods", None, False)
    semantic.assign(conn, d, {"Chill hip hop": ["t1"]})
    semantic.assign(conn, d, {"Chill": ["t2"]})
    assert group_map(d) == {"Chill hip hop": ["t1"], "Chill": ["t2"]}


def test_unknown_and_already_placed_ids_are_skipped_and_reported(conn):
    d = semantic.start(conn, "moods", None, False)
    semantic.assign(conn, d, {"A": ["t1"]})
    junk = [f"nope{i}" for i in range(12)]
    rep = semantic.assign(conn, d, {"B": ["t1", "t2", *junk]})
    assert rep["placed"] == 1 and rep["skipped"] == ["t1", *junk]
    assert group_map(d) == {"A": ["t1"], "B": ["t2"]}
    t = semantic.result_text(conn, d, report=rep)
    assert "Skipped 13 track IDs" in t and "t1, nope0" in t and "and 3 more" in t
    rep = semantic.assign(conn, d, {"C": ["nope1"]})                    # nothing valid: no group C
    assert rep["placed"] == 0 and "C" not in group_map(d)


def test_a_track_under_two_groups_goes_to_the_first(conn):
    d = semantic.start(conn, "moods", None, False)
    rep = semantic.assign(conn, d, {"A": ["t1", "t2"], "B": ["t2", "t3"]})
    assert group_map(d) == {"A": ["t1", "t2"], "B": ["t3"]} and rep["duplicates"] == ["t2"]
    assert "under more than one group: kept in the first (t2)" in semantic.result_text(conn, d, report=rep)


def test_too_many_groups_changes_nothing(conn, monkeypatch):
    monkeypatch.setattr(drafts, "MAX_GROUPS", 2)
    d = semantic.start(conn, "moods", None, False)
    with pytest.raises(drafts.DraftError, match="3 groups \\(max 2\\)"):
        semantic.assign(conn, d, {"A": ["t1"], "B": ["t2"], "C": ["t3"]})
    assert drafts.load_draft(conn, d["id"])["groups"] == []


def test_empty_assignments_only_get_the_next_batch(conn):
    d = semantic.start(conn, "moods", None, False)
    for a in (None, {}):
        assert semantic.assign(conn, d, a) == {"placed": 0, "skipped": [], "duplicates": []}
    assert len(drafts.load_draft(conn, d["id"])["unmatched"]) == 6


# ---------------------------------------------------------------- the batch

def test_batch_is_ordered_by_artist_then_name_and_capped(conn, monkeypatch):
    d = semantic.start(conn, "moods", None, False)
    t = semantic.result_text(conn, d, 50)
    assert t.splitlines()[0] == "Draft d1 (By Claude: moods): 0 of 6 placed, 6 left; no groups yet."
    assert "Track ID | Track | Artist | Year | Genres | Pop" in t
    first = ids_in_batch(t)
    assert sorted(first) == ["t1", "t2", "t3", "t4", "t5", "t6"]
    assert ids_in_batch(semantic.result_text(conn, d, 2)) == first[:2]        # a smaller batch walks the same order
    monkeypatch.setattr(semantic, "BATCH_MAX", 3)
    assert ids_in_batch(semantic.result_text(conn, d, 50)) == first[:3]       # capped
    assert len(ids_in_batch(semantic.result_text(conn, d, 0))) == 1           # at least one
    semantic.assign(conn, d, {"A": first[:2]})
    assert ids_in_batch(semantic.result_text(conn, d, 50)) == first[2:5][:3]    # consecutive calls walk the pool


def test_batch_row_content(conn):
    d = semantic.start(conn, "moods", None, False)
    t = semantic.result_text(conn, d)
    assert "t1 | Alpha Song | " in t and " | 1994 | rock, indie rock | 80" in t


def test_finishing_points_to_show_draft(conn):
    d = semantic.start(conn, "moods", None, False)
    rep = semantic.assign(conn, d, {"A": ["t1", "t2", "t3"], "B": ["t4", "t5", "t6"]})
    t = semantic.result_text(conn, d, report=rep)
    assert "6 of 6 placed, 0 left; groups: A 3, B 3." in t
    assert "Nothing is left to place" in t and "show_draft" in t and "write_draft" in t and "Track ID |" not in t


# ---------------------------------------------------------------- other drafts, old drafts, the draft data

def test_works_on_a_rule_made_draft_with_unmatched_tracks(conn):
    pool, counts = drafts.build_pool(conn, None)
    groups, unmatched = drafts.make_groups(conn, pool, counts, "category", "both", None, False)
    d = drafts.new_draft(conn, "category", "both", [], False, counts, groups, unmatched, False)
    left = [u["id"] for u in d["unmatched"]]
    assert left                                                           # t4 and t6 lack genre or decade
    rep = semantic.assign(conn, d, {"Misc": left, "Rock · 1990s": ["t1"]})   # t1 is placed already: skipped
    assert rep["placed"] == len(left) and rep["skipped"] == ["t1"]
    d = drafts.load_draft(conn, d["id"])
    assert group_map(d)["Misc"] == sorted(left) and d["unmatched"] == []
    assert sorted(d["assigned"]) == sorted(left)                           # saved like edit_draft's assign


def test_draft_label_list_summary_and_data_work_with_zero_groups(conn):
    d = semantic.start(conn, "Sunday morning", None, False)
    assert drafts.list_drafts(conn)[0] == {"id": "d1", "created": d["created"], "label": "By Claude: Sunday morning",
                                           "groups": 0, "tracks": 0, "unmatched": 6}
    data = drafts.draft_data(conn, d, full=True)
    assert data["mode"] == "semantic" and data["groups"] == [] and data["totals"]["grouped"] == 0
    assert data["unmatched"]["total"] == 6 and data["unmatched"]["by_reason"] == [{"reason": "not placed yet", "tracks": 6}]
    assert "By Claude: Sunday morning" in drafts.summary(drafts.result_data(drafts.draft_data(conn, d)))
    semantic.assign(conn, d, {"Calm": ["t1"]})
    assert drafts.draft_data(conn, d)["groups"][0]["name"] == "Calm"


def test_old_drafts_still_load(conn):
    pool, counts = drafts.build_pool(conn, None)
    groups, unmatched = drafts.make_groups(conn, pool, counts, "category", "decade", None, False)
    d = drafts.new_draft(conn, "category", "decade", [], False, counts, groups, unmatched, False)
    d.pop("source", None)
    store_json = json.dumps(d)
    conn.execute("UPDATE drafts SET data = ? WHERE id = ?", (store_json, d["id"]))
    assert drafts.draft_label(drafts.load_draft(conn, d["id"])) == "by category: decade"
    assert drafts.list_drafts(conn)[0]["label"] == "by category: decade"


# ---------------------------------------------------------------- the tool

def test_tool_start_then_continue_without_opening_the_dashboard(home):
    r = server.assign_tracks(criteria="moods", filters=drafts.GroupFilters(playlists=["One"]))
    t = text(r)
    assert t.splitlines()[0] == "Draft d1 (By Claude: moods): 0 of 4 placed, 4 left; no groups yet."
    assert "Opened the" not in t and "dashboard" not in t.split("Track ID")[0]
    assert r.structured_content["mode"] == "semantic" and not (home / "draft.html").exists()
    first = ids_in_batch(t)
    r = server.assign_tracks(draft="d1", assignments={"Chill": first[:2], "Run": first[2:3]})
    t = text(r)
    assert t.splitlines()[0].startswith("Draft d1 (By Claude: moods): 3 of 4 placed, 1 left; groups: Chill 2, Run 1.")
    assert ids_in_batch(t) == first[3:]
    r = server.assign_tracks(draft="d1", assignments={"Run": first[3:]})
    assert "Nothing is left to place" in text(r) and r.structured_content["totals"]["grouped"] == 4
    assert "Draft d1" in text(server.show_draft("d1"))                    # the other tools take over


def test_tool_errors(home):
    with pytest.raises(ToolError, match="criteria"):
        server.assign_tracks()
    with pytest.raises(ToolError, match="There are no drafts|no draft"):
        server.assign_tracks(draft="d9")
    with pytest.raises(ToolError, match="isn't loaded"):
        server.assign_tracks(criteria="x", filters=drafts.GroupFilters(playlists=["Nope"]))


def test_skip_unmatched_ends_the_loop_for_tracks_claude_did_not_know(home):
    server.assign_tracks(criteria="moods")
    server.assign_tracks(draft="d1", assignments={"Chill": ["t1"]})
    from pydantic import TypeAdapter
    ch = TypeAdapter(list[drafts.Change]).validate_python([{"op": "skip_unmatched"}])
    r = server.edit_draft("d1", ch)
    assert "skipped 5 unmatched" in text(r)
    assert "Nothing is left to place" in text(server.assign_tracks(draft="d1"))


# ---------------------------------------------------------------- write_draft reads the result

@pytest.fixture
def wconn(tmp_path):
    c = store.connect(tmp_path / "w.db")
    yield c
    c.close()


def test_write_draft_writes_a_semantic_draft(wconn, sp):
    load(sp, wconn)
    d = semantic.start(wconn, "moods", drafts.GroupFilters(playlists=["Alpha"]), False)
    semantic.assign(wconn, d, {"Chill": [tid(1), tid(2)], "Run": [tid(3)]})
    out = write.write_draft(wconn, sp, d["id"], now=1_000)
    assert '"Chill"' in out and '"Run"' in out
    assert sp.calls == []
    done = write.confirm(sp, wconn, code_of(out), now=1_001)
    assert "2 playlists created" in done
    names = {pl["name"]: pl["items"] for p, pl in sp.playlists.items() if p not in (pid("A"), pid("B"), pid("X"))}
    assert names == {"Chill": [tid(1), tid(2)], "Run": [tid(3)]}
