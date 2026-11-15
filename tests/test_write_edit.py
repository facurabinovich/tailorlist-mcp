"""sort_playlist and dedupe_playlist: previews, the single replace call vs block moves, dates, undo, failures.
Uses the fake Spotify of test_write.py (positions, added dates, replace and reorder as Spotify does them)."""
import random

import pytest

from tailorlist_mcp import write
from test_write import NOW, OLD, FakeSpotify, code_of, conn, pid, tid  # noqa: F401  (conn is a fixture)

A = pid("A")


def day(i: int) -> str:
    return f"2020-01-01T00:{i // 60:02d}:{i % 60:02d}Z"


def make(items, dates=None, **kw):
    sp = FakeSpotify()
    sp.add_playlist(A, "Alpha", items, dates=dates, **kw)
    sp.add_playlist(pid("X"), "Theirs", [tid(1)], owner="other")
    return sp


def items(sp):
    return sp.playlists[A]["items"]


def by_date(sp):
    pl = sp.playlists[A]
    return dict(zip(pl["items"], pl["dates"]))


def ids(*ns):
    return [tid(n) for n in ns]


def set_track(sp, n, name=None, artist=None, album=None, date=None, pop=None, dur=None):
    t = sp.catalog[tid(n)]
    t["name"] = name or t["name"]
    if artist is not None:
        t["artists"] = [{"id": f"x{artist}", "name": artist}] if artist else []
    t["album"] = {**t["album"], "name": album or t["album"]["name"], "release_date": t["album"]["release_date"] if date is None else date}
    t["popularity"] = pop
    t["duration_ms"] = dur


# ---------------------------------------------------------------- the move planner

def apply_move(seq, start, before, n):
    """Spotify's reorder: insert_before counts before the block is taken out."""
    block, rest = seq[start:start + n], seq[:start] + seq[start + n:]
    at = before if before < start else before - n
    return rest[:at] + block + rest[at:]


@pytest.mark.parametrize("size", [0, 1, 2, 5, 40, 150])
def test_planner_turns_the_source_into_the_target(size):
    rng = random.Random(size)
    for _ in range(25):
        src = [f"t{rng.randrange(max(1, size // 2))}" for _ in range(size)]      # repeated IDs
        dst = rng.sample(src, len(src))
        moves = write.plan_moves(src, dst)
        cur = list(src)
        for start, before, n in moves:
            assert not start <= before <= start + n and n >= 1                  # never a no-op
            cur = apply_move(cur, start, before, n)
        assert cur == dst and len(moves) <= max(0, size - 1)


def test_planner_moves_runs_in_one_call():
    src = list("abcdefgh")
    assert write.plan_moves(src, list("fghabcde")) == [[5, 0, 3]]
    assert write.plan_moves(src, src) == []
    assert len(write.plan_moves(src, src[::-1])) == 7                            # worst case: reversal, n - 1


# ---------------------------------------------------------------- sort_playlist

def sort_fixture():
    sp = make(ids(1, 2, 3, 4, 5), dates=[day(i) for i in range(5)])
    set_track(sp, 1, "Delta", "Zed", "A1", "1990-05-01", 10, 200000)
    set_track(sp, 2, "alpha", "Amy", "B1", "1985-01-01", 90, 100000)
    set_track(sp, 3, "Charlie", "Amy", "A2", "2000-01-01", 50, 300000)
    set_track(sp, 4, "bravo", "Amy", "A2", "1985-01-01", 50, None)
    set_track(sp, 5, "Echo", "", "A5", "", None, 150000)
    # date added, by position of the tracks 1..5: 3rd, 1st, 5th, 2nd, 4th
    sp.playlists[A]["dates"] = [day(3), day(1), day(5), day(2), day(4)]
    return sp


@pytest.mark.parametrize("by, descending, expected", [
    ("year", False, [2, 4, 1, 3, 5]),            # 1985 tie keeps the current order; no year goes last
    ("year", True, [3, 1, 2, 4, 5]),
    ("artist", False, [4, 2, 3, 1, 5]),          # Amy: 1985 A2, 1985 B1, 2000; Zed; no artist last
    ("popularity", False, [1, 3, 4, 2, 5]),      # 50 tie keeps 3 before 4
    ("popularity", True, [2, 3, 4, 1, 5]),       # a tie is not reversed when descending
    ("added", False, [2, 4, 1, 5, 3]),
    ("name", False, [2, 4, 3, 1, 5]),
    ("duration", False, [2, 5, 1, 3, 4]),
    ("duration", True, [3, 1, 5, 2, 4]),
])
def test_sort_orders_small_playlist_with_one_replace_call(conn, by, descending, expected):
    sp = sort_fixture()
    before = by_date(sp)
    text = write.sort_playlist(conn, sp, "Alpha", by, descending)
    assert sp.calls == [] and "keeps its added date" in text and "1 Spotify call" in text
    write.confirm(sp, conn, code_of(text))
    assert items(sp) == ids(*expected)
    assert sp.calls == [("replace", A, 5)]
    assert by_date(sp) == before                                              # every date stays with its track


def test_sort_missing_values_and_unknown_option(conn):
    sp = sort_fixture()
    with pytest.raises(write.WriteError, match="year, artist, popularity, added, name, duration"):
        write.sort_playlist(conn, sp, "Alpha", "mood")
    assert sp.calls == []


def test_sort_already_sorted_returns_no_code(conn):
    sp = make(ids(1, 2, 3))
    text = write.sort_playlist(conn, sp, "Alpha", "name")
    assert "already in that order" in text and "confirm_write" not in text
    assert write.sort_playlist(conn, sp, "Alpha", "name", True).count("confirm_write") == 1


def big(n=150, seed=3):
    rng = random.Random(seed)
    order = ids(*range(1, n + 1))
    sp = make(order, dates=[day(i) for i in range(n)])
    for k in range(1, n + 1):
        sp.catalog[tid(k)]["popularity"] = rng.randrange(0, 40)                 # many ties
    return sp, order


def test_sort_large_playlist_uses_block_moves_and_keeps_every_date(conn):
    sp, order = big()
    before = by_date(sp)
    text = write.sort_playlist(conn, sp, "Alpha", "popularity", True)
    calls = int(text.split("it takes ")[1].split(" ")[0])
    write.confirm(sp, conn, code_of(text))
    pop = sp.catalog
    assert items(sp) == sorted(order, key=lambda t: -pop[t]["popularity"])     # python's sort is stable too
    assert [c[0] for c in sp.calls] == ["move"] * calls and calls > 1
    assert by_date(sp) == before
    assert list(by_date(sp)) == items(sp)


def test_sort_with_items_that_are_not_tracks_keeps_them_at_the_end(conn):
    sp = make(ids(3, 1, 2))
    sp.catalog["ep1"] = {"type": "episode", "id": "ep1", "name": "A podcast"}
    sp.playlists[A]["items"] = [tid(3), "ep1", tid(1), tid(2)]
    for n, p in ((1, 30), (2, 20), (3, 10)):
        sp.catalog[tid(n)]["popularity"] = p
    text = write.sort_playlist(conn, sp, "Alpha", "popularity", True)
    assert "not tracks" in text
    write.confirm(sp, conn, code_of(text))
    assert items(sp) == [tid(1), tid(2), tid(3), "ep1"]
    assert {c[0] for c in sp.calls} == {"move"}
    write.confirm(sp, conn, code_of(write.undo_last_write(conn, sp)))          # the episode goes back to its place
    assert items(sp) == [tid(3), "ep1", tid(1), tid(2)]


def test_sort_playlist_with_a_repeated_track_uses_moves(conn):
    sp = make(ids(2, 1, 2))
    for n, p in ((1, 10), (2, 20)):
        sp.catalog[tid(n)]["popularity"] = p
    write.confirm(sp, conn, code_of(write.sort_playlist(conn, sp, "Alpha", "popularity")))
    assert items(sp) == ids(1, 2, 2) and all(c[0] == "move" for c in sp.calls)


def test_sort_undo_restores_the_order_both_ways(conn):
    sp = sort_fixture()
    original = list(items(sp))
    write.confirm(sp, conn, code_of(write.sort_playlist(conn, sp, "Alpha", "name")))
    text = write.undo_last_write(conn, sp)
    assert "rewrite the playlist" in text
    write.confirm(sp, conn, code_of(text))
    assert items(sp) == original

    sp, order = big()
    before = by_date(sp)
    write.confirm(sp, conn, code_of(write.sort_playlist(conn, sp, "Alpha", "popularity")))
    assert items(sp) != order
    write.confirm(sp, conn, code_of(write.undo_last_write(conn, sp)))
    assert items(sp) == order and by_date(sp) == before


def test_sort_failure_midway_reports_the_moves_done_and_loses_nothing(conn):
    sp, order = big()
    code = code_of(write.sort_playlist(conn, sp, "Alpha", "popularity"))
    sp.fail_at = 4
    text = write.confirm(sp, conn, code)
    assert "INCOMPLETE" in text and "3 of " in text and "3 moves were done" in text and "new sort_playlist preview" in text
    assert sorted(items(sp)) == sorted(order) and items(sp) != order
    row = conn.execute("SELECT status FROM writes").fetchone()
    assert row["status"] == "partial"
    assert write.undo_last_write(conn, sp).startswith("The last write didn't finish")
    sp.fail_at = None
    write.confirm(sp, conn, code_of(write.sort_playlist(conn, sp, "Alpha", "popularity")))     # a new preview finishes it
    assert [sp.catalog[t]["popularity"] for t in items(sp)] == sorted(sp.catalog[t]["popularity"] for t in order)


def test_sort_aborts_if_the_playlist_changed_after_the_preview(conn):
    sp = sort_fixture()
    code = code_of(write.sort_playlist(conn, sp, "Alpha", "name"))
    sp.external_edit(A)
    with pytest.raises(write.WriteError, match="changed on Spotify"):
        write.confirm(sp, conn, code)
    assert sp.calls == []


@pytest.mark.parametrize("tool", [write.sort_playlist, write.dedupe_playlist])
def test_liked_songs_and_playlists_of_others_are_refused(conn, tool):
    sp = make(ids(1, 2))
    extra = ("year",) if tool is write.sort_playlist else ()
    with pytest.raises(write.WriteError, match="Liked Songs can't be edited"):
        tool(conn, sp, "Liked Songs", *extra)
    with pytest.raises(write.WriteError, match="belongs to"):
        tool(conn, sp, pid("X"), *extra)
    assert sp.calls == []


# ---------------------------------------------------------------- dedupe_playlist: exact

def test_dedupe_exact_in_a_small_playlist_is_one_replace_call(conn):
    sp = make(ids(1, 2, 3, 2, 4, 1, 5), dates=[day(i) for i in range(7)])
    text = write.dedupe_playlist(conn, sp, "Alpha")
    assert sp.calls == [] and "7 items now, 5 after" in text and "earliest added date" in text and "TODAY" not in text
    write.confirm(sp, conn, code_of(text))
    assert items(sp) == ids(1, 2, 3, 4, 5) and sp.calls == [("replace", A, 5)]
    assert by_date(sp) == {tid(1): day(0), tid(2): day(1), tid(3): day(2), tid(4): day(4), tid(5): day(6)}


def test_dedupe_exact_in_a_large_playlist_removes_and_adds_back_in_place(conn):
    base = ids(*range(1, 121))
    sp = make(base + ids(5, 7, 5), dates=[day(i) for i in range(123)])
    text = write.dedupe_playlist(conn, sp, "Alpha")
    assert "TODAY" in text and "Song 5 - Artist 5" in text and "Song 7 - Artist 0" in text and "3 Spotify calls" in text
    write.confirm(sp, conn, code_of(text))
    assert items(sp) == base
    assert [c[0] for c in sp.calls] == ["remove", "add", "add"] and sp.calls[1][3] == 4 and sp.calls[2][3] == 6
    dates = by_date(sp)
    assert dates[tid(5)] == dates[tid(7)] == NOW and dates[tid(6)] == day(5) and dates[tid(120)] == day(119)
    # undo: the copies come back where they were
    undo = write.undo_last_write(conn, sp)
    assert "today's added date" in undo
    write.confirm(sp, conn, code_of(undo))
    assert items(sp)[:120] == base and sorted(items(sp)) == sorted(base + ids(5, 7, 5))


def test_dedupe_large_undo_restores_the_exact_order(conn):
    original = ids(*range(1, 111)) + ids(3, 50, 3, 2)
    sp = make(original)
    write.confirm(sp, conn, code_of(write.dedupe_playlist(conn, sp, "Alpha", "exact")))
    assert items(sp) == ids(*range(1, 111))
    write.confirm(sp, conn, code_of(write.undo_last_write(conn, sp)))
    assert items(sp) == original


def test_dedupe_failure_between_remove_and_add_names_the_missing_tracks(conn):
    sp = make(ids(*range(1, 121)) + ids(5))
    code = code_of(write.dedupe_playlist(conn, sp, "Alpha"))
    sp.fail_at = 2
    text = write.confirm(sp, conn, code)
    assert "INCOMPLETE" in text and "NOT put back" in text and tid(5) in text
    assert tid(5) not in items(sp)


def test_no_duplicates_returns_no_code(conn):
    sp = make(ids(1, 2, 3))
    text = write.dedupe_playlist(conn, sp, "Alpha")
    assert "No duplicates" in text and "confirm_write" not in text


def test_dedupe_aborts_if_the_playlist_changed(conn):
    sp = make(ids(1, 1, 2))
    code = code_of(write.dedupe_playlist(conn, sp, "Alpha"))
    sp.external_edit(A)
    with pytest.raises(write.WriteError, match="changed on Spotify"):
        write.confirm(sp, conn, code)
    assert sp.calls == []


def test_dedupe_small_undo_restores_the_duplicates(conn):
    sp = make(ids(1, 2, 1, 3))
    write.confirm(sp, conn, code_of(write.dedupe_playlist(conn, sp, "Alpha")))
    undo = write.undo_last_write(conn, sp)
    assert "today's added date" in undo
    write.confirm(sp, conn, code_of(undo))
    assert items(sp) == ids(1, 2, 1, 3)


# ---------------------------------------------------------------- dedupe_playlist: versions

def versions_fixture(extra=()):
    """Two songs with two versions each: 10/11 (30 and 70 popular), 20/21 (80 and 20); positions 0..8."""
    sp = make(ids(1, 10, 2, 11, 3, 20, 4, 21, 5) + list(extra), dates=[day(i) for i in range(9 + len(extra))])
    for n, name, pop in ((10, "Song X", 30), (11, "Song X - Remastered 2009", 70), (20, "Other", 80), (21, "Other (Remastered)", 20)):
        set_track(sp, n, name, "Same Artist", pop=pop)
    return sp


def test_versions_without_a_decision_asks_and_gives_no_code(conn):
    sp = versions_fixture()
    text = write.dedupe_playlist(conn, sp, "Alpha")
    assert "confirm_write" not in text and "keep=" in text and "versions_rule" in text
    for t in (10, 11, 20, 21):
        assert tid(t) in text
    assert "Popularity" in text and "Album" in text and "Added" in text and sp.calls == []


@pytest.mark.parametrize("rule, stay", [("most_popular", (11, 20)), ("earliest_added", (10, 20))])
def test_versions_rule(conn, rule, stay):
    sp = versions_fixture()
    text = write.dedupe_playlist(conn, sp, "Alpha", versions_rule=rule)
    write.confirm(sp, conn, code_of(text))
    gone = {10, 11, 20, 21} - set(stay)
    assert items(sp) == [tid(n) for n in (1, 10, 2, 11, 3, 20, 4, 21, 5) if n not in gone]
    assert sp.calls == [("remove", A, 2)]
    assert by_date(sp)[tid(5)] == day(8)                                       # nothing else is touched


def test_versions_keep_and_partial_decision(conn):
    sp = versions_fixture()
    text = write.dedupe_playlist(conn, sp, "Alpha", keep=[f"https://open.spotify.com/track/{tid(10)}"])
    assert "LEFT UNTOUCHED" in text and tid(20) in text and tid(21) in text and "KEEP" in text
    write.confirm(sp, conn, code_of(text))
    assert tid(11) not in items(sp) and tid(10) in items(sp) and tid(20) in items(sp) and tid(21) in items(sp)
    undo = write.undo_last_write(conn, sp)
    write.confirm(sp, conn, code_of(undo))
    assert items(sp) == ids(1, 10, 2, 11, 3, 20, 4, 21, 5)


def test_versions_keep_beats_the_rule_and_is_validated(conn):
    sp = versions_fixture()
    write.confirm(sp, conn, code_of(write.dedupe_playlist(conn, sp, "Alpha", keep=ids(21), versions_rule="most_popular")))
    assert tid(21) in items(sp) and tid(20) not in items(sp) and tid(11) in items(sp)
    sp = versions_fixture()
    with pytest.raises(write.WriteError, match="keep one"):
        write.dedupe_playlist(conn, sp, "Alpha", keep=ids(10, 11))
    with pytest.raises(write.WriteError, match="not versions"):
        write.dedupe_playlist(conn, sp, "Alpha", keep=ids(1))
    with pytest.raises(write.WriteError, match="versions_rule"):
        write.dedupe_playlist(conn, sp, "Alpha", versions_rule="newest")
    with pytest.raises(write.WriteError, match="kind"):
        write.dedupe_playlist(conn, sp, "Alpha", kind="sloppy")


def test_kind_filters_exact_and_versions(conn):
    both = ids(2, 2)                                                             # exact duplicate on top of the versions
    sp = versions_fixture(both)
    write.confirm(sp, conn, code_of(write.dedupe_playlist(conn, sp, "Alpha", "versions", versions_rule="earliest_added")))
    assert items(sp).count(tid(2)) == 3 and tid(11) not in items(sp)
    sp = versions_fixture(both)
    text = write.dedupe_playlist(conn, sp, "Alpha", "exact")
    assert "Different versions" not in text and "LEFT" not in text
    write.confirm(sp, conn, code_of(text))
    assert items(sp).count(tid(2)) == 1 and tid(11) in items(sp)
    with pytest.raises(write.WriteError, match="only apply to versions"):
        write.dedupe_playlist(conn, sp, "Alpha", "exact", versions_rule="most_popular")


def test_exact_and_versions_in_a_small_playlist_are_one_replace_call(conn):
    sp = versions_fixture(ids(2, 2))
    write.confirm(sp, conn, code_of(write.dedupe_playlist(conn, sp, "Alpha", versions_rule="most_popular")))
    assert items(sp) == ids(1, 2, 11, 3, 20, 4, 5) and sp.calls == [("replace", A, 7)]


def test_exact_decided_and_undecided_versions_in_one_preview(conn):
    sp = versions_fixture(ids(2))
    text = write.dedupe_playlist(conn, sp, "Alpha")                              # exact can go ahead, versions wait
    assert "LEFT UNTOUCHED" in text and "confirm_write with code" in text
    write.confirm(sp, conn, code_of(text))
    assert items(sp).count(tid(2)) == 1 and tid(10) in items(sp) and tid(11) in items(sp)
