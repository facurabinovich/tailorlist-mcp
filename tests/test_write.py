"""Phase 3: previews, confirm_write, partial failures, undo, export, with a fake Spotify that keeps playlists in memory."""
import json

import pytest

from tailorlist_mcp import collection, drafts, export, progress, store, write
from tailorlist_mcp.sources.spotify_api import SpotifyError


def tid(n: int) -> str:
    return f"track{n:017d}"          # 22 characters


def pid(c: str) -> str:
    return c * 22


class FakeSpotify:
    """Playlists in memory with Spotify's semantics: add at a position, remove every copy, snapshot per write."""

    def __init__(self, n_tracks=400):
        self.me_id = "me"
        self.catalog = {}
        for n in range(1, n_tracks + 1):
            self.catalog[tid(n)] = {"type": "track", "id": tid(n), "name": f"Song {n}", "popularity": 50, "is_playable": True,
                                    "album": {"id": f"al{n}", "name": f"Album {n}", "release_date": "1995-01-01"},
                                    "artists": [{"id": f"ar{n % 7}", "name": f"Artist {n % 7}"}],
                                    "external_urls": {"spotify": f"https://open.spotify.com/track/{tid(n)}"}}
        self.playlists = {}
        self.calls = []                 # every write call: (method, playlist, ...)
        self.fail_at = None             # raise on the Nth write call (1-based)
        self.snap = 0
        self.lag = False

    # -- fixtures
    def add_playlist(self, p, name, items, owner="me", collaborative=False, public=True):
        self.playlists[p] = {"name": name, "owner": owner, "items": list(items), "snapshot": self._snap(),
                             "collaborative": collaborative, "public": public}

    def replaced(self, old, new, same_album=False):
        """old is an album version Spotify replaced with new (popularity 0, relinks to new)."""
        self.catalog[old]["popularity"] = 0
        self.catalog[old]["album"] = dict(self.catalog[old]["album"])
        self.catalog[new]["name"] = self.catalog[old]["name"]
        if same_album:
            self.catalog[new]["album"] = dict(self.catalog[old]["album"])
        self.relink = getattr(self, "relink", {})
        self.relink[old] = new

    def _snap(self):
        self.snap += 1
        return f"snap{self.snap}"

    def external_edit(self, p):
        """Someone else adds a track."""
        self.playlists[p]["items"].append(tid(399))
        self.playlists[p]["snapshot"] = self._snap()

    def _write(self, *call):
        self.calls.append(call)
        if self.fail_at == len(self.calls):
            raise SpotifyError("boom (simulated)")

    # -- reads
    def me(self):
        return {"id": self.me_id, "display_name": "Me"}

    def _meta(self, p):
        pl = self.playlists[p]
        # lag: GET playlists/{id} keeps returning the snapshot from before the last write (seen on the real API)
        snap = pl.get("old_snapshot") or pl["snapshot"] if self.lag else pl["snapshot"]
        return {"id": p, "name": pl["name"], "owner": {"id": pl["owner"], "display_name": pl["owner"]}, "snapshot_id": snap,
                "external_urls": {"spotify": f"https://open.spotify.com/playlist/{p}"}, "followers": {"total": 0},
                "collaborative": pl["collaborative"], "public": pl["public"],
                "items": {"total": len(pl["items"])}}

    def my_playlists(self):
        return [self._meta(p) for p in self.playlists]

    def playlist(self, p):
        if p not in self.playlists:
            raise SpotifyError("404")
        return self._meta(p)

    def playlist_items(self, p):
        for t in self.playlists[p]["items"]:
            yield {"added_at": "2024-01-01T00:00:00Z", "item": self.catalog[t]}

    def tracks(self, ids, market=None):
        out = []
        for i in ids:
            if i not in self.catalog:
                out.append(None)
            elif market and i in getattr(self, "relink", {}):
                new = self.catalog[self.relink[i]]
                out.append({**new, "linked_from": {"id": i}})
            elif market and not self.catalog[i]["is_playable"]:
                out.append({**self.catalog[i], "is_playable": False})
            else:
                out.append(self.catalog[i])
        return out

    def artists(self, ids):
        return [{"id": a, "genres": ["rock"], "popularity": 50} for a in ids]

    # -- writes
    def create_playlist(self, name, description="", public=False):
        self._write("create", name)
        p = pid(chr(ord("N") + len([c for c in self.calls if c[0] == "create"]) - 1))
        # like the real thing seen on 2026-11-10: the response says what was asked, the playlist is public
        self.add_playlist(p, name, [], public=True)
        self.playlists[p]["description"] = description
        return {**self._meta(p), "public": public}

    def add_items(self, p, uris, position=None):
        self._write("add", p, len(uris), position)
        ids = [u.rsplit(":", 1)[1] for u in uris]
        items = self.playlists[p]["items"]
        at = len(items) if position is None else position
        items[at:at] = ids
        self.playlists[p]["old_snapshot"] = self.playlists[p]["snapshot"]
        self.playlists[p]["snapshot"] = self._snap()
        return self.playlists[p]["snapshot"]

    def remove_items(self, p, uris, snapshot_id=None):
        self._write("remove", p, len(uris))
        gone = {u.rsplit(":", 1)[1] for u in uris}
        self.playlists[p]["items"] = [t for t in self.playlists[p]["items"] if t not in gone]
        self.playlists[p]["old_snapshot"] = self.playlists[p]["snapshot"]
        self.playlists[p]["snapshot"] = self._snap()
        return self.playlists[p]["snapshot"]

    def writes(self):
        return [c for c in self.calls]


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def sp():
    s = FakeSpotify()
    s.add_playlist(pid("A"), "Alpha", [tid(n) for n in range(1, 11)])
    s.add_playlist(pid("B"), "Beta", [tid(n) for n in range(8, 16)] + [tid(8)])          # t8 twice
    s.add_playlist(pid("X"), "Theirs", [tid(1)], owner="other")
    return s


def load(sp, conn, *ids):
    collection.load_playlists(sp, conn, list(ids) or [pid("A"), pid("B")], force=True)


def code_of(text: str) -> str:
    assert "confirm_write with code " in text, text
    return text.split("confirm_write with code ")[1].split(" ")[0]


# ---------------------------------------------------------------- the two-step mechanism

def test_create_preview_writes_nothing_and_confirm_writes_exactly_the_plan(conn, sp):
    load(sp, conn)
    text = write.create_playlist(conn, sp, "Mine", [tid(n) for n in (5, 3, 9)], now=1_000)
    assert sp.calls == []                                        # a preview never writes
    assert "NOTHING HAS BEEN WRITTEN" in text and "private" in text.lower() and "Song 5" in text
    assert "Made with Tailorlist on" in text and "from Alpha" in text
    result = write.confirm(sp, conn, code_of(text), now=1_100)
    assert sp.calls == [("create", "Mine"), ("add", pid("N"), 3, None)]
    assert sp.playlists[pid("N")]["items"] == [tid(5), tid(3), tid(9)]                 # exact tracks, in order
    assert "Created \"Mine\": 3 of 3 tracks" in result
    assert "Visibility: public" in result                         # the real visibility is read back and reported
    row = conn.execute("SELECT * FROM writes").fetchone()
    assert (row["kind"], row["status"], row["playlist_id"]) == ("create", "done", pid("N"))
    assert json.loads(row["batches"])[0]["tracks"] == 3 and row["snapshot_id"]


def test_codes_are_single_use_and_expire(conn, sp):
    load(sp, conn)
    code = code_of(write.create_playlist(conn, sp, "Mine", [tid(1)], now=1_000))
    write.confirm(sp, conn, code, now=1_001)
    with pytest.raises(write.WriteError, match="not valid"):
        write.confirm(sp, conn, code, now=1_002)                 # replay
    old = code_of(write.create_playlist(conn, sp, "Other", [tid(2)], now=1_000))
    with pytest.raises(write.WriteError, match="expired"):
        write.confirm(sp, conn, old, now=1_000 + write.CODE_TTL + 1)
    with pytest.raises(write.WriteError, match="not valid"):
        write.confirm(sp, conn, "ZZZZZZ", now=1_000)
    assert len([c for c in sp.calls if c[0] == "create"]) == 1


def test_confirm_takes_the_code_case_insensitively_and_ignores_spaces(conn, sp):
    load(sp, conn)
    code = code_of(write.create_playlist(conn, sp, "Mine", [tid(1)], now=1_000))
    assert "Done" in write.confirm(sp, conn, f" {code.lower()} ", now=1_001)


def test_playlist_changed_since_preview_aborts_with_nothing_written(conn, sp):
    load(sp, conn)
    code = code_of(write.add_tracks(conn, sp, "Alpha", [tid(100)], now=1_000))
    sp.external_edit(pid("A"))                                    # someone edits the playlist after the preview
    with pytest.raises(write.WriteError, match="changed on Spotify"):
        write.confirm(sp, conn, code, now=1_001)
    assert sp.calls == []


def test_draft_changed_since_preview_aborts(conn, sp):
    load(sp, conn)
    d = drafts.new_draft(conn, "category", "both", None, False, {}, {"G": [tid(1), tid(2)]}, [], False)
    code = code_of(write.write_draft(conn, sp, d["id"], now=1_000))
    d["groups"][0]["name"] = "Renamed"
    drafts.save_draft(conn, d)                                    # what edit_draft does
    with pytest.raises(write.WriteError, match="Draft d1 changed"):
        write.confirm(sp, conn, code, now=1_001)
    assert sp.calls == [] and progress.done_ids(conn) == set()


# ---------------------------------------------------------------- new playlists from drafts

def make_draft(conn, groups, keep=()):
    d = drafts.new_draft(conn, "category", "both", None, False, {}, groups, [], False)
    for g in d["groups"]:
        g["keep_visible"] = g["name"] in keep
    drafts.save_draft(conn, d)
    return d


def test_write_draft_creates_one_playlist_per_group_and_marks_exactly_those_tracks_done(conn, sp):
    load(sp, conn)
    d = make_draft(conn, {"Rock 80s": [tid(1), tid(2)], "Pop": [tid(3), tid(4)], "Kept": [tid(5)]}, keep=("Kept",))
    text = write.write_draft(conn, sp, d["id"], names={"Rock 80s": "Rock de los 80"}, now=1_000)
    assert '"Rock de los 80"' in text and '"Pop"' in text and "will be marked done" in text and "not marked done" in text
    assert sp.calls == []
    out = write.confirm(sp, conn, code_of(text), now=1_001)
    assert "3 playlists created" in out
    names = [pl["name"] for p, pl in sp.playlists.items() if p not in (pid("A"), pid("B"), pid("X"))]
    assert names == ["Rock de los 80", "Pop", "Kept"]
    assert progress.done_ids(conn) == {tid(1), tid(2), tid(3), tid(4)}        # the keep_visible group is not marked
    assert progress.load(conn)[tid(1)]["cluster_name"] == "Rock 80s"
    assert len(conn.execute("SELECT 1 FROM writes").fetchall()) == 3


def test_name_collision_proposes_a_numbered_name(conn, sp):
    load(sp, conn)
    text = write.create_playlist(conn, sp, "alpha", [tid(1)], now=1_000)
    assert 'already have a playlist called "alpha"' in text and '"alpha (2)"' in text
    write.confirm(sp, conn, code_of(text), now=1_001)
    assert sp.playlists[pid("N")]["name"] == "alpha (2)"
    d = make_draft(conn, {"Pop": [tid(3)], "Pop ": [tid(4)]})
    text = write.write_draft(conn, sp, d["id"], now=1_002)
    assert '"Pop"' in text and '"Pop (2)"' in text                 # also between the groups of one preview


def test_description_is_capped_at_300_characters():
    short = write.make_description("2026-11-10", ["Alt7", "People7"])
    assert short == "Made with Tailorlist on 2026-11-10 from Alt7, People7"
    long = write.make_description("2026-11-10", [f"Playlist number {n:03d} with a long name" for n in range(40)])
    assert len(long) <= 300 and long.endswith(" more") and "from Playlist number 000" in long
    assert write.make_description("2026-11-10", []) == "Made with Tailorlist on 2026-11-10"


def test_a_long_playlist_is_written_in_batches_of_100_and_logged_batch_by_batch(conn, sp):
    load(sp, conn)
    ids = [tid(n) for n in range(1, 251)]
    out = write.confirm(sp, conn, code_of(write.create_playlist(conn, sp, "Big", ids, now=1)), now=2)
    assert [c[2] for c in sp.calls if c[0] == "add"] == [100, 100, 50]
    assert sp.playlists[pid("N")]["items"] == ids and "250 of 250" in out
    batches = json.loads(conn.execute("SELECT batches FROM writes").fetchone()["batches"])
    assert [b["tracks"] for b in batches] == [100, 100, 50]


def test_partial_failure_is_reported_exactly(conn, sp):
    load(sp, conn)
    big = [tid(n) for n in range(1, 151)]                          # group 2 needs two batches
    d = make_draft(conn, {"One": [tid(151), tid(152)], "Two": big, "Three": [tid(300)]})
    text = write.write_draft(conn, sp, d["id"], now=1)
    sp.fail_at = 5                                                 # calls: create One, add One, create Two, add Two (100), add Two (50) fails
    out = write.confirm(sp, conn, code_of(text), now=2)
    assert out.startswith("STOPPED: 1 of 3 playlists complete")
    assert 'Created "One": 2 of 2 tracks' in out
    assert '"Two" was created' in out and "only 100 of 150 tracks went in" in out and "boom (simulated)" in out
    assert '3. "Three": NOT written (1 tracks)' in out
    assert progress.done_ids(conn) == {tid(151), tid(152)} | set(big[:100])        # exactly what was written
    rows = conn.execute("SELECT playlist_name, status FROM writes ORDER BY id").fetchall()
    assert [(r["playlist_name"], r["status"]) for r in rows] == [("One", "done"), ("Two", "partial")]
    assert not any(pl["name"] == "Three" for pl in sp.playlists.values())


def test_create_failing_at_the_first_call_creates_nothing(conn, sp):
    load(sp, conn)
    text = write.create_playlist(conn, sp, "Mine", [tid(1)], now=1)
    sp.fail_at = 1
    out = write.confirm(sp, conn, code_of(text), now=2)
    assert "NOT created" in out and conn.execute("SELECT COUNT(*) FROM writes").fetchone()[0] == 0


# ---------------------------------------------------------------- replaced and unavailable tracks

@pytest.fixture
def sp_replaced(sp):
    sp.replaced(tid(2), tid(302), same_album=True)
    sp.replaced(tid(3), tid(303))                                  # different album
    sp.catalog[tid(4)]["is_playable"] = False
    sp.catalog[tid(4)]["popularity"] = 0
    return sp


def test_replaced_and_unavailable_tracks_stop_the_preview_until_the_user_chooses(conn, sp_replaced):
    sp = sp_replaced
    load(sp, conn)
    d = make_draft(conn, {"G": [tid(1), tid(2), tid(3), tid(4)]})
    text = write.write_draft(conn, sp, d["id"], now=1)
    assert "confirm_write with code" not in text and "No confirmation code" in text
    assert "Same album (1)" in text and "Other album (1)" in text and "not playable" in text and "Current album" in text
    assert "recommended" in text
    assert sp.calls == [] and conn.execute("SELECT COUNT(*) FROM pending_writes").fetchone()[0] == 0
    # one choice is not enough: the unplayable one still needs its own
    text = write.write_draft(conn, sp, d["id"], versions="current", now=1)
    assert "confirm_write with code" not in text and "not playable" in text and "Other album" not in text


def test_choosing_current_versions_writes_the_current_ids_and_marks_the_draft_ones(conn, sp_replaced):
    sp = sp_replaced
    load(sp, conn)
    d = make_draft(conn, {"G": [tid(1), tid(2), tid(3), tid(4)]})
    text = write.write_draft(conn, sp, d["id"], versions="current", keep_saved=[tid(3)], unavailable="leave_out", now=1)
    assert "2 replaced" not in text and "1 replaced versions written as the current version" in text and "left out" in text
    write.confirm(sp, conn, code_of(text), now=2)
    assert sp.playlists[pid("N")]["items"] == [tid(1), tid(302), tid(3)]       # current for t2, saved kept for t3, t4 left out
    assert progress.done_ids(conn) == {tid(1), tid(2), tid(3)}               # the left-out track stays in the pool


def test_choosing_saved_and_keep(conn, sp_replaced):
    sp = sp_replaced
    load(sp, conn)
    text = write.create_playlist(conn, sp, "S", [tid(2), tid(4)], versions="saved", unavailable="keep", now=1)
    write.confirm(sp, conn, code_of(text), now=2)
    assert sp.playlists[pid("N")]["items"] == [tid(2), tid(4)]


def test_bad_choice_values_are_refused(conn, sp):
    load(sp, conn)
    with pytest.raises(write.WriteError, match="versions must be"):
        write.create_playlist(conn, sp, "S", [tid(1)], versions="newest")
    with pytest.raises(write.WriteError, match="unavailable must be"):
        write.create_playlist(conn, sp, "S", [tid(1)], unavailable="maybe")


# ---------------------------------------------------------------- add and remove

def test_add_tracks_skips_the_ones_already_there_and_inserts_at_the_position(conn, sp):
    load(sp, conn)
    text = write.add_tracks(conn, sp, "Alpha", [tid(1), tid(50), tid(51)], position=2, now=1)
    assert "Skipped, already in the playlist (1)" in text and "add 2 tracks" in text and "position 2" in text
    out = write.confirm(sp, conn, code_of(text), now=2)
    assert sp.playlists[pid("A")]["items"][:5] == [tid(1), tid(2), tid(50), tid(51), tid(3)]
    assert "added 2 tracks" in out
    assert "Re-read 1 edited playlist" in out
    # the cache agrees with Spotify
    cached = [r[0] for r in conn.execute("SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position", (pid("A"),))]
    assert cached == sp.playlists[pid("A")]["items"]


def test_add_tracks_edge_cases(conn, sp):
    load(sp, conn)
    assert "Nothing to add" in write.add_tracks(conn, sp, "Alpha", [tid(1)])
    with pytest.raises(write.WriteError, match="position must be between 0 and 10"):
        write.add_tracks(conn, sp, "Alpha", [tid(50)], position=11)
    with pytest.raises(write.WriteError, match="not a Spotify track"):
        write.add_tracks(conn, sp, "Alpha", ["nope"])
    with pytest.raises(write.WriteError, match="Spotify has no track"):
        write.add_tracks(conn, sp, "Alpha", ["x" * 22])
    assert sp.calls == []


def test_add_accepts_links_and_uris(conn, sp):
    load(sp, conn)
    text = write.add_tracks(conn, sp, "Alpha", [f"https://open.spotify.com/track/{tid(60)}?si=abc", f"spotify:track:{tid(61)}"])
    write.confirm(sp, conn, code_of(text))
    assert sp.playlists[pid("A")]["items"][-2:] == [tid(60), tid(61)]


def test_only_own_or_collaborative_playlists_are_edited(conn, sp):
    load(sp, conn)
    with pytest.raises(write.WriteError, match="Tailorlist only edits your own playlists"):
        write.add_tracks(conn, sp, "Theirs", [tid(50)])
    sp.playlists[pid("X")]["collaborative"] = True
    assert "Preview: add 1 tracks" in write.add_tracks(conn, sp, "Theirs", [tid(50)])
    with pytest.raises(write.WriteError, match="not one of your playlists"):
        write.add_tracks(conn, sp, "No such list", [tid(50)])


def test_remove_warns_that_every_copy_goes_and_confirms(conn, sp):
    load(sp, conn)
    text = write.remove_tracks(conn, sp, "Beta", [tid(8), tid(9), tid(200)], now=1)
    assert "EVERY copy" in text and "Song 8 (2 copies)" in text and "Not in the playlist, ignored (1)" in text
    assert "0, 8" in text                                         # t8 is at positions 0 and 8
    out = write.confirm(sp, conn, code_of(text), now=2)
    assert tid(8) not in sp.playlists[pid("B")]["items"] and tid(9) not in sp.playlists[pid("B")]["items"]
    assert "removed 2 tracks" in out
    assert "Nothing to remove" in write.remove_tracks(conn, sp, "Beta", [tid(200)])


# ---------------------------------------------------------------- replace_versions

def test_replace_versions_keeps_positions_across_runs_and_duplicates(conn, sp_replaced):
    sp = sp_replaced
    sp.playlists[pid("A")]["items"] = [tid(1), tid(2), tid(3), tid(5), tid(2), tid(6), tid(3), tid(7)]   # t2 twice, t3 twice
    sp.playlists[pid("A")]["snapshot"] = sp._snap()
    load(sp, conn)
    before = list(sp.playlists[pid("A")]["items"])
    text = write.replace_versions(conn, sp, now=1)
    assert "swap 4 replaced album versions" in text
    assert "Their \"added\" date becomes today" in text
    out = write.confirm(sp, conn, code_of(text), now=2)
    expect = [{tid(2): tid(302), tid(3): tid(303)}.get(t, t) for t in before]
    assert sp.playlists[pid("A")]["items"] == expect
    cached = [r[0] for r in conn.execute("SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position", (pid("A"),))]
    assert cached == expect and "Re-read" in out
    assert "swapped 4 tracks" in out


def test_replace_versions_neighbours_share_one_call_and_other_playlists_are_untouched(conn, sp_replaced):
    sp = sp_replaced
    load(sp, conn)
    text = write.replace_versions(conn, sp, "Alpha", now=1)         # t2 and t3 are neighbours
    write.confirm(sp, conn, code_of(text), now=2)
    assert [c[0] for c in sp.calls] == ["add", "remove"] and sp.calls[0][2:] == (2, 3)        # one insert of both, right after the old pair
    assert sp.playlists[pid("A")]["items"][:4] == [tid(1), tid(302), tid(303), tid(4)]
    assert tid(2) not in sp.playlists[pid("B")]["items"] and tid(302) not in sp.playlists[pid("B")]["items"]


def test_replace_versions_covers_every_editable_loaded_playlist_as_one_operation(conn, sp_replaced):
    sp = sp_replaced
    sp.playlists[pid("B")]["items"].append(tid(2))
    sp.playlists[pid("B")]["snapshot"] = sp._snap()
    sp.add_playlist(pid("T"), "TheirsToo", [tid(3)], owner="other")
    load(sp, conn, pid("A"), pid("B"), pid("T"))
    text = write.replace_versions(conn, sp, now=1)
    assert "in 2 playlists" in text and "Skipped" in text
    write.confirm(sp, conn, code_of(text), now=2)
    assert sp.playlists[pid("T")]["items"] == [tid(3)]               # not theirs to edit
    assert tid(302) in sp.playlists[pid("B")]["items"]
    assert len({r["op_id"] for r in conn.execute("SELECT op_id FROM writes")}) == 1


def test_replace_versions_leaves_alone_a_track_whose_current_version_is_already_in_the_playlist(conn, sp_replaced):
    sp = sp_replaced
    sp.playlists[pid("A")]["items"].append(tid(302))
    sp.playlists[pid("A")]["snapshot"] = sp._snap()
    load(sp, conn)
    text = write.replace_versions(conn, sp, "Alpha", now=1)
    assert "already have their current version" in text and "swap 1 replaced" in text


def test_replace_versions_with_nothing_to_do(conn, sp):
    load(sp, conn)
    assert "No replaced versions" in write.replace_versions(conn, sp)


# ---------------------------------------------------------------- undo

def test_undo_an_add(conn, sp):
    load(sp, conn)
    before = list(sp.playlists[pid("A")]["items"])
    write.confirm(sp, conn, code_of(write.add_tracks(conn, sp, "Alpha", [tid(50), tid(51)], position=3, now=1)), now=2)
    text = write.undo_last_write(conn, sp, now=3)
    assert "remove 2 tracks that were added" in text
    out = write.confirm(sp, conn, code_of(text), now=4)
    assert sp.playlists[pid("A")]["items"] == before and "Re-read" in out
    # an undo can't be undone, and the add can't be undone twice
    assert "can't be undone" in write.undo_last_write(conn, sp, now=5)


def test_undo_a_remove_restores_every_copy_in_its_position(conn, sp):
    load(sp, conn)
    before = list(sp.playlists[pid("B")]["items"])
    write.confirm(sp, conn, code_of(write.remove_tracks(conn, sp, "Beta", [tid(8), tid(12)], now=1)), now=2)
    text = write.undo_last_write(conn, sp, now=3)
    assert "put back 3 removed tracks" in text
    write.confirm(sp, conn, code_of(text), now=4)
    assert sp.playlists[pid("B")]["items"] == before


def test_undo_a_replace_across_playlists(conn, sp_replaced):
    sp = sp_replaced
    load(sp, conn)
    before = {p: list(sp.playlists[p]["items"]) for p in (pid("A"), pid("B"))}
    write.confirm(sp, conn, code_of(write.replace_versions(conn, sp, now=1)), now=2)
    assert sp.playlists[pid("A")]["items"] != before[pid("A")]
    write.confirm(sp, conn, code_of(write.undo_last_write(conn, sp, now=3)), now=4)
    assert {p: sp.playlists[p]["items"] for p in before} == before


def test_undo_is_refused_when_the_playlist_changed_since(conn, sp):
    load(sp, conn)
    write.confirm(sp, conn, code_of(write.add_tracks(conn, sp, "Alpha", [tid(50)], now=1)), now=2)
    sp.external_edit(pid("A"))
    assert "changed after that write" in write.undo_last_write(conn, sp, now=3)


def test_a_stale_snapshot_id_from_spotify_does_not_block_the_undo_but_a_real_change_does(conn, sp):
    """GET playlists/{id} returns the old snapshot_id for seconds after a write: content decides, not the snapshot."""
    load(sp, conn)
    sp.lag = True
    write.confirm(sp, conn, code_of(write.add_tracks(conn, sp, "Alpha", [tid(50)], now=1)), now=2)
    text = write.undo_last_write(conn, sp, now=3)
    assert "remove 1 tracks that were added" in text
    sp.external_edit(pid("A"))
    with pytest.raises(write.WriteError, match="changed on Spotify"):
        write.confirm(sp, conn, code_of(text), now=4)


def test_undo_aborts_if_the_playlist_changes_between_preview_and_confirmation(conn, sp):
    load(sp, conn)
    write.confirm(sp, conn, code_of(write.add_tracks(conn, sp, "Alpha", [tid(50)], now=1)), now=2)
    code = code_of(write.undo_last_write(conn, sp, now=3))
    sp.external_edit(pid("A"))
    with pytest.raises(write.WriteError, match="changed on Spotify"):
        write.confirm(sp, conn, code, now=4)


def test_undo_not_for_created_playlists_or_an_empty_log(conn, sp):
    load(sp, conn)
    assert "nothing to undo" in write.undo_last_write(conn, sp)
    write.confirm(sp, conn, code_of(write.create_playlist(conn, sp, "Mine", [tid(1)], now=1)), now=2)
    assert "never deletes playlists" in write.undo_last_write(conn, sp)


def test_undo_of_an_unfinished_write_is_refused(conn, sp):
    load(sp, conn)
    code = code_of(write.add_tracks(conn, sp, "Alpha", list(tid(n) for n in range(20, 260)), now=1))
    sp.fail_at = 3
    sp.calls.clear()
    out = write.confirm(sp, conn, code, now=2)
    assert "INCOMPLETE, 2 of 3 calls went through" in out and "add 100" in out
    assert "didn't finish" in write.undo_last_write(conn, sp)


# ---------------------------------------------------------------- previews never write

def test_every_preview_tool_leaves_spotify_untouched(conn, sp_replaced):
    sp = sp_replaced
    load(sp, conn)
    d = make_draft(conn, {"G": [tid(1)]})
    write.create_playlist(conn, sp, "x", [tid(1)])
    write.create_playlist(conn, sp, "y", draft=d["id"], group="G")
    write.write_draft(conn, sp, d["id"])
    write.add_tracks(conn, sp, "Alpha", [tid(60)])
    write.remove_tracks(conn, sp, "Alpha", [tid(1)])
    write.replace_versions(conn, sp)
    write.undo_last_write(conn, sp)
    assert sp.calls == [] and progress.done_ids(conn) == set()
    assert conn.execute("SELECT COUNT(*) FROM pending_writes").fetchone()[0] == 6


def test_create_playlist_needs_tracks_or_a_draft_group(conn, sp):
    load(sp, conn)
    with pytest.raises(write.WriteError, match="either tracks"):
        write.create_playlist(conn, sp, "x")
    with pytest.raises(write.WriteError, match="both the draft and the group"):
        write.create_playlist(conn, sp, "x", draft="d1")


# ---------------------------------------------------------------- export

def test_export_formats(conn, sp):
    load(sp, conn)
    d = make_draft(conn, {"One": [tid(1), tid(2)], "Two": [tid(3)]})
    text = export.export_tracks(conn, playlist="Alpha", fmt="text").splitlines()
    assert text[0].startswith("Playlist Alpha: 10 tracks") and text[1] == "Artist 1 - Song 1"
    urls = export.export_tracks(conn, draft=d["id"], group="Two", fmt="urls")
    assert urls.splitlines()[-1] == f"https://open.spotify.com/track/{tid(3)}"
    both = export.export_tracks(conn, draft=d["id"])
    assert "## One (2)" in both and "## Two (1)" in both
    rows = export.export_tracks(conn, draft=d["id"], fmt="csv").splitlines()
    assert rows[0] == "group,track,artists,album,year,id,url" and rows[1].startswith("One,Song 1,Artist 1,Album 1,1995")
    with pytest.raises(ValueError, match="either a playlist"):
        export.export_tracks(conn)
    with pytest.raises(ValueError, match="format"):
        export.export_tracks(conn, playlist="Alpha", fmt="xml")


def test_primary_artist_only_in_spotlistr_lines():
    rows = [{"artist": "A", "name": "Song"}, {"artist": "A", "name": "Song"}]
    assert export.spotlistr_lines(rows) == "A - Song\nA - Song"       # duplicates kept
