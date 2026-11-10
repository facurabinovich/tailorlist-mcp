"""group_tracks / show_draft / edit_draft / organize_progress logic on the hand-made cache of test_queries.

t1, t2: rock, 1990s. t3: pop, 2000s. t5: rock, 2010s. t4: no genre, 2020s. t6: no genre, no release date.
"""
import json

import pytest

from tailorlist_mcp import dashboard, drafts, progress, queries, store
from tailorlist_mcp.drafts import GroupFilters

from test_queries import conn  # noqa: F401  (fixture)


def make(conn, mode="category", by="both", genres=None, also_by_decade=False, filters=None, include_done=False):
    pool, counts = drafts.build_pool(conn, filters, include_done)
    groups, unmatched = drafts.make_groups(conn, pool, counts, mode, by, genres, also_by_decade)
    return drafts.new_draft(conn, mode, by, genres, also_by_decade, counts, groups, unmatched, include_done)


def names(d):
    return {g["name"]: sorted(g["tracks"]) for g in d["groups"]}


def unmatched(d):
    return {u["id"]: u["reason"] for u in d["unmatched"]}


def edit(conn, d, *changes):
    from pydantic import TypeAdapter
    parsed = TypeAdapter(list[drafts.Change]).validate_python(list(changes))
    return drafts.apply_changes(conn, d, parsed)


def test_pool_counts_follow_the_three_levels(conn):
    pool, c = drafts.build_pool(conn, GroupFilters(playlists=["One"], decades=[1990, 2000], genre_families=["rock"]))
    assert (c["in_cache"], c["in_playlists"], c["after_decades"], c["after_families"], c["pool"]) == (6, 4, 3, 2, 2)
    assert sorted(pool["track_id"]) == ["t1", "t2"]
    assert c["filters"] == ["playlists=One", "decades=1990s, 2000s", "families=Rock"]


def test_pool_family_filter_keeps_genreless_tracks_like_the_original(conn):
    pool, _ = drafts.build_pool(conn, GroupFilters(genre_families=["Pop"]))
    assert sorted(pool["track_id"]) == ["t3", "t4", "t6"]       # t4 and t6 have no genre: they always pass
    with pytest.raises(queries.QueryError, match="Unknown genre family"):
        drafts.build_pool(conn, GroupFilters(genre_families=["Polka"]))


def test_pool_counts_a_track_in_two_playlists_once_and_lists_its_playlists(conn):
    pool, c = drafts.build_pool(conn, None)
    assert c["pool"] == 6 and pool.set_index("track_id").loc["t2", "playlists"] == ["One", "Two"]


def test_category_both_groups_and_explains_unmatched(conn):
    d = make(conn)
    assert names(d) == {"Rock · 1990s": ["t1", "t2"], "Pop · 2000s": ["t3"], "Rock · 2010s": ["t5"]}
    assert unmatched(d) == {"t4": drafts.NO_GENRE, "t6": drafts.NO_GENRE_NO_DECADE}
    assert d["id"] == "d1"


def test_category_one_dimension(conn):
    assert names(make(conn, by="decade")) == {"1990s": ["t1", "t2"], "2000s": ["t3"], "2010s": ["t5"], "2020s": ["t4"]}
    d = make(conn, by="genre_family")
    assert names(d) == {"Rock": ["t1", "t2", "t5"], "Pop": ["t3"]} and unmatched(d) == {"t4": drafts.NO_GENRE, "t6": drafts.NO_GENRE}


def test_genres_mode_unmatched_vs_left_out(conn):
    d = make(conn, "genres", genres=["Indie Rock", "pop"])
    assert names(d) == {"indie rock": ["t1", "t2", "t5"], "pop": ["t3"]}       # a1's tags: rock, indie rock
    d = make(conn, "genres", genres=["pop"])
    kinds = {u["id"]: (u["kind"], u["reason"]) for u in d["unmatched"]}
    assert kinds == {"t4": ("missing", drafts.NO_GENRE), "t6": ("missing", drafts.NO_GENRE),
                     "t1": ("left_out", drafts.NO_MATCH), "t2": ("left_out", drafts.NO_MATCH), "t5": ("left_out", drafts.NO_MATCH)}
    assert names(make(conn, "genres", genres=["rock"], also_by_decade=True)) == {
        "rock · 1990s": ["t1", "t2"], "rock · 2010s": ["t5"]}


def test_genre_menu_and_family_default_selection(conn):
    pool, _ = drafts.build_pool(conn, None)
    menu = {m["family"]: m for m in drafts.genre_menu(pool)}
    assert menu["Rock"]["top"] == [{"genre": "indie rock", "tracks": 3}, {"genre": "rock", "tracks": 3}] and "Pop" in menu
    assert drafts.genres_of_families(pool, ["rock"]) == ["indie rock", "rock"]


def test_done_tracks_leave_the_pool_unless_asked(conn):
    d = make(conn)
    progress.mark_done(conn, d["groups"][0]["tracks"])                       # Rock · 1990s: t1, t2
    pool, c = drafts.build_pool(conn, None)
    assert c["done_left_out"] == 2 and sorted(pool["track_id"]) == ["t3", "t4", "t5", "t6"]
    assert drafts.build_pool(conn, None, include_done=True)[1]["pool"] == 6


def test_draft_ids_are_short_and_only_the_latest_are_kept(conn):
    for _ in range(drafts.KEEP_DRAFTS + 3):
        make(conn)
    ids = [x["id"] for x in drafts.list_drafts(conn)]
    assert ids[0] == f"d{drafts.KEEP_DRAFTS + 3}" and len(ids) == drafts.KEEP_DRAFTS
    assert drafts.load_draft(conn, None)["id"] == ids[0] and drafts.load_draft(conn, "5")["id"] == "d5"
    with pytest.raises(drafts.DraftError, match="no draft"):
        drafts.load_draft(conn, "d1")


def test_no_drafts_is_a_clear_error(conn):
    with pytest.raises(drafts.DraftError, match="no drafts yet"):
        drafts.load_draft(conn, None)


# ---------------------------------------------------------------- the dashboard data

def test_draft_data_cards_and_caps(conn):
    d = make(conn)
    full = drafts.draft_data(conn, d, full=True)
    json.dumps(full)
    top = full["groups"][0]
    assert top["name"] == "Rock · 1990s" and top["size"] == 2 and top["hours"] == 0.1
    assert top["top_artists"][0] == {"name": "A1", "tracks": 2} and top["top_genres"][0]["tracks"] == 2
    assert top["decades"] == [{"decade": 1990, "tracks": 2}] and len(top["tracks"]) == 2 and top["samples"][0]["name"] == "Alpha Song"
    assert full["totals"] == {"groups": 3, "grouped": 4, "unmatched": 2, "left_out": 0, "skipped": 0}
    assert full["unmatched"]["by_reason"] == [{"reason": drafts.NO_GENRE, "tracks": 1}, {"reason": drafts.NO_GENRE_NO_DECADE, "tracks": 1}]
    assert full["progress"]["total"] == 6 and full["progress"]["percent"] == 0
    capped = drafts.draft_data(conn, d)
    assert "tracks" not in capped["groups"][0]
    assert "Draft d1" in drafts.summary(capped) and "get their OK" in drafts.summary(capped)


def test_result_data_is_capped(conn):
    d = make(conn)
    data = drafts.draft_data(conn, d)
    big = {"groups": [dict(data["groups"][0], name=f"G{i}", samples=data["groups"][0]["samples"] * 3) for i in range(200)],
           "unmatched": {"items": [{"name": "x" * 50}] * 2000}, "left_out": {"items": []}}
    out = drafts.result_data(big)
    assert len(json.dumps(out)) <= drafts.RESULT_BYTES and out["trimmed"]


# ---------------------------------------------------------------- edit_draft

def test_rename_move_and_the_empty_group_disappears(conn):
    d = make(conn)
    notes = edit(conn, d, {"op": "rename", "group": "pop", "to": "Pop hits"},
                 {"op": "move", "tracks": {"track_ids": ["t5"]}, "to": "Pop hits"})
    assert names(d) == {"Rock · 1990s": ["t1", "t2"], "Pop hits": ["t3", "t5"]}      # Rock · 2010s emptied -> gone
    assert notes[0] == "renamed 'Pop · 2000s' to 'Pop hits'" and notes[1] == "moved 1 tracks to 'Pop hits'"
    assert drafts.load_draft(conn, "d1")["groups"][1]["name"] == "Pop hits"            # persisted


def test_move_by_artist_and_typos_are_errors(conn):
    d = make(conn)
    edit(conn, d, {"op": "move", "tracks": {"artist": "a2"}, "to": "Rock · 2010s", "from_group": "1990s"})
    assert names(d)["Rock · 2010s"] == ["t2", "t5"]
    with pytest.raises(drafts.DraftError, match="There is no group 'Jazz'"):
        edit(conn, d, {"op": "move", "tracks": {"all": True}, "to": "Jazz", "from_group": "2010s"})
    edit(conn, d, {"op": "move", "tracks": {"track_ids": ["t3"]}, "to": "Jazz", "create_if_missing": True})
    assert "Jazz" in names(d)


def test_assign_unmatched_saves_manual_progress_and_follows_renames(conn):
    d = make(conn)
    edit(conn, d, {"op": "assign", "tracks": {"reason": "no genre and no"}, "to": "Pop"})
    assert unmatched(d) == {"t4": drafts.NO_GENRE} and "t6" in names(d)["Pop · 2000s"]
    assert progress.load(conn) == {"t6": {"status": "manual", "cluster_name": "Pop · 2000s"}}
    edit(conn, d, {"op": "rename", "group": "Pop · 2000s", "to": "Pop"})
    assert progress.load(conn)["t6"]["cluster_name"] == "Pop"
    s = progress.summary(conn)
    assert (s["manual"], s["done"], s["organized"], s["percent"]) == (1, 0, 1, 16.7)


def test_dropping_the_group_releases_manual_assignments(conn):
    d = make(conn)
    edit(conn, d, {"op": "assign", "tracks": {"all": True}, "to": "Pop · 2000s"})
    assert unmatched(d) == {} and len(progress.load(conn)) == 2
    edit(conn, d, {"op": "drop_group", "group": "Pop"})
    assert progress.load(conn) == {} and "Pop · 2000s" not in names(d)
    assert {u["id"] for u in d["unmatched"]} == {"t3", "t4", "t6"}                    # kept as left out / unmatched again
    assert {u["id"]: u["kind"] for u in d["unmatched"]}["t3"] == "left_out"


def test_merge(conn):
    d = make(conn)
    edit(conn, d, {"op": "keep_visible", "group": "2010s"},
         {"op": "merge", "groups": ["Rock · 1990s", "Rock · 2010s"], "into": "Rock"})
    assert names(d)["Rock"] == ["t1", "t2", "t5"] and len(d["groups"]) == 2
    assert [g for g in d["groups"] if g["name"] == "Rock"][0]["keep_visible"]
    with pytest.raises(drafts.DraftError, match="listed twice"):
        edit(conn, d, {"op": "merge", "groups": ["Rock", "rock"]})
    with pytest.raises(drafts.DraftError, match="already a group called"):
        edit(conn, d, {"op": "rename", "group": "Pop", "to": "rock"})


def test_skip_unmatched_marks_them_done_and_leaves_the_pool(conn):
    d = make(conn)
    edit(conn, d, {"op": "skip_unmatched"})
    assert d["unmatched"] == [] and sorted(d["skipped"]) == ["t4", "t6"]
    assert {t: p["status"] for t, p in progress.load(conn).items()} == {"t4": "done", "t6": "done"}
    assert drafts.build_pool(conn, None)[1]["pool"] == 4
    with pytest.raises(drafts.DraftError, match="no unmatched tracks to skip"):
        edit(conn, d, {"op": "skip_unmatched"})


def test_skip_does_not_touch_left_out_tracks(conn):
    d = make(conn, "genres", genres=["pop"])
    edit(conn, d, {"op": "skip_unmatched"})
    assert sorted(u["id"] for u in d["unmatched"]) == ["t1", "t2", "t5"]              # have genres, just not the chosen one
    assert set(progress.load(conn)) == {"t4", "t6"}


def test_keep_visible_and_reopen_drop_done_rows(conn):
    d = make(conn)
    progress.mark_done(conn, ["t1", "t2", "t3"])
    edit(conn, d, {"op": "reopen", "group": "Rock · 1990s"})
    assert set(progress.load(conn)) == {"t3"}
    edit(conn, d, {"op": "keep_visible", "group": "Pop"})
    assert progress.load(conn) == {} and [g for g in d["groups"] if g["name"] == "Pop · 2000s"][0]["keep_visible"]


def test_a_failing_change_leaves_everything_as_it_was(conn):
    d = make(conn)
    before = json.dumps(drafts.load_draft(conn, "d1"))
    with pytest.raises(drafts.DraftError):
        edit(conn, d, {"op": "assign", "tracks": {"all": True}, "to": "Pop"}, {"op": "rename", "group": "nope", "to": "x"})
    assert json.dumps(drafts.load_draft(conn, "d1")) == before and progress.load(conn) == {}


def test_selector_needs_something_and_changes_are_strict(conn):
    d = make(conn)
    with pytest.raises(drafts.DraftError, match="Say which tracks"):
        edit(conn, d, {"op": "move", "tracks": {}, "to": "Pop"})
    with pytest.raises(Exception):
        edit(conn, d, {"op": "rename", "group": "Pop", "to": "x", "colour": "red"})   # extra field
    with pytest.raises(Exception):
        edit(conn, d, {"op": "explode"})


# ---------------------------------------------------------------- progress

def test_marked_tracks_show_a_group_as_done(conn):
    """What write.confirm does after a group was written: its tracks become done (a manual row is upgraded)."""
    d = make(conn)
    edit(conn, d, {"op": "assign", "tracks": {"track_ids": ["t6"]}, "to": "Pop"})
    assert progress.mark_done(conn, ["t3", "t6"], "Pop") == 2
    assert {t: p["status"] for t, p in progress.load(conn).items()} == {"t3": "done", "t6": "done"}
    data = drafts.draft_data(conn, d)
    assert [g["name"] for g in data["groups"] if g["done"]] == ["Pop · 2000s"]


def test_progress_summary_and_complete(conn):
    assert progress.summary(conn)["percent"] == 0 and "Nothing organized yet" in progress.describe(conn)
    progress.mark_done(conn, ["t1", "t2", "gone-from-cache"])
    s = progress.summary(conn)
    assert (s["done"], s["total"], s["percent"], s["complete"]) == (2, 6, 33.3, False)      # rows of unloaded tracks don't count
    progress.mark_done(conn, ["t3", "t4", "t5", "t6"])
    s = progress.summary(conn)
    assert s["complete"] and s["percent"] == 100.0 and "100%" in progress.describe(conn)


def test_manual_rows_never_downgrade_a_done_track(conn):
    progress.mark_done(conn, ["t1"])
    progress.save_manual(conn, {"t1": "X", "t2": "Y"})
    assert progress.load(conn) == {"t1": {"status": "done", "cluster_name": None}, "t2": {"status": "manual", "cluster_name": "Y"}}


def test_reset_clears_only_progress_rows(conn):
    d = make(conn)
    progress.mark_done(conn, ["t1", "t2"])
    tables = ("playlists", "tracks", "track_artists", "artists", "playlist_tracks", "drafts", "jobs")
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    assert progress.reset(conn) == 2 and progress.load(conn) == {}
    assert {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables} == before
    assert drafts.load_draft(conn, "d1")["groups"] == d["groups"]


def test_migration_is_additive(tmp_path):
    """An older cache (no progress or drafts tables) opens and keeps its data."""
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE playlists (id TEXT PRIMARY KEY, name TEXT NOT NULL, loaded_at INTEGER NOT NULL)")
    old.execute("INSERT INTO playlists VALUES ('p', 'Old', 1)")
    old.commit(); old.close()
    c = store.connect(path)
    assert c.execute("SELECT name FROM playlists").fetchone()[0] == "Old"
    assert c.execute("SELECT COUNT(*) FROM cluster_progress").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0
    c.close()


def test_tracks_assigned_by_hand_are_not_listed_as_unmatched_again(conn):
    d = make(conn)
    edit(conn, d, {"op": "assign", "tracks": {"track_ids": ["t4"]}, "to": "Pop"})
    d2 = make(conn)
    assert unmatched(d2) == {"t6": drafts.NO_GENRE_NO_DECADE}
    assert d2["pool"]["manual_hidden"] == 1
