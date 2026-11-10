"""store + collection with a fake Spotify client and a temp SQLite file."""
import pytest

from tailorlist_mcp import collection, store
from tailorlist_mcp.sources.spotify_api import SpotifyError

PL_A = "A" * 22
PL_B = "B" * 22


def _track(tid, name, artists, year="2001-05-01", ms=180_000):
    return {
        "type": "track", "id": tid, "name": name, "duration_ms": ms, "explicit": False,
        "popularity": 50, "external_ids": {"isrc": f"ISRC{tid}"},
        "external_urls": {"spotify": f"https://open.spotify.com/track/{tid}"},
        "album": {"id": f"al{tid}", "name": f"Album {tid}", "release_date": year},
        "artists": [{"id": a, "name": a.upper()} for a in artists],
    }


class FakeClient:
    def __init__(self):
        self.snapshots = {PL_A: "s1", PL_B: "s1"}
        self.items = {
            PL_A: [
                {"added_at": "2024-01-01T00:00:00Z", "item": _track("t1", "One", ["ar1"])},
                {"added_at": "2024-01-02T00:00:00Z", "item": _track("t2", "Two", ["ar1", "ar2"], "1995")},
                {"added_at": None, "item": {"type": "episode", "id": "ep1"}},
                {"added_at": None, "item": {"type": "track", "id": None, "is_local": True, "name": "local"}},
            ],
            PL_B: [
                {"added_at": None, "item": _track("t2", "Two", ["ar1", "ar2"], "1995")},
                {"added_at": None, "item": _track("t3", "Three", ["ar3"], "1987")},
                {"added_at": None, "item": _track("t3", "Three", ["ar3"], "1987")},
            ],
        }
        self.item_calls = 0
        self.followers = 7

    def me(self):
        return {"id": "me", "display_name": "Facu"}

    def my_playlists(self):
        return [
            {"id": PL_A, "name": "Alpha", "owner": {"id": "me"}, "snapshot_id": self.snapshots[PL_A],
             "items": {"total": 4}, "external_urls": {"spotify": "https://open.spotify.com/playlist/a"}},
            {"id": PL_B, "name": "Beta", "owner": {"id": "other", "display_name": "Ana"},
             "snapshot_id": self.snapshots[PL_B], "tracks": {"total": 3}},
            None,
        ]

    def playlist(self, pid):
        if pid not in self.snapshots:
            raise SpotifyError("404")
        return {"id": pid, "name": {PL_A: "Alpha", PL_B: "Beta"}[pid], "owner": {"id": "me"},
                "snapshot_id": self.snapshots[pid], "external_urls": {"spotify": f"https://x/{pid}"},
                "followers": {"total": self.followers}}

    def playlist_items(self, pid):
        self.item_calls += 1
        return iter(self.items[pid])

    def artists(self, ids):
        genres = {"ar1": ["rock"], "ar2": ["rock", "pop"], "ar3": []}
        return [{"id": i, "genres": genres[i], "popularity": 10} for i in ids]


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "cache.db")
    yield c
    c.close()


def test_playlist_id_from_accepts_ids_uris_and_links():
    assert collection.playlist_id_from(PL_A) == PL_A
    assert collection.playlist_id_from(f"spotify:playlist:{PL_A}") == PL_A
    assert collection.playlist_id_from(f"https://open.spotify.com/playlist/{PL_A}?si=xyz") == PL_A
    assert collection.playlist_id_from("Alpha") is None


def test_load_reports_counts(conn):
    client = FakeClient()
    out = collection.load_playlists(client, conn, [PL_A, f"https://open.spotify.com/playlist/{PL_B}"])
    assert [r["status"] for r in out["results"]] == ["loaded", "loaded"]
    assert out["results"][0]["tracks"] == 2 and out["results"][0]["skipped"] == 2
    assert out["collection"] == {"playlists": 2, "entries": 5, "unique_tracks": 3, "artists": 3}


def test_unchanged_playlists_are_skipped_and_changed_ones_reloaded(conn):
    client = FakeClient()
    collection.load_playlists(client, conn, [PL_A, PL_B])
    calls = client.item_calls

    out = collection.load_playlists(client, conn, [PL_A])
    assert out["results"][0]["status"] == "unchanged" and client.item_calls == calls

    client.snapshots[PL_B] = "s2"
    client.items[PL_B] = [{"added_at": None, "item": _track("t4", "Four", ["ar4"])}]
    client.artists = lambda ids: [{"id": i, "genres": ["jazz"]} for i in ids]
    out = collection.load_playlists(client, conn, [PL_B])
    assert out["results"][0]["status"] == "loaded"
    # t3 and ar3 were only in the old Beta: pruned.
    assert out["collection"] == {"playlists": 2, "entries": 3, "unique_tracks": 3, "artists": 3}


def test_followers_are_saved_and_refreshed_even_when_the_playlist_is_unchanged(conn):
    client = FakeClient()
    collection.load_playlists(client, conn, [PL_A])
    assert conn.execute("SELECT followers FROM playlists").fetchone()[0] == 7
    client.followers = 9
    out = collection.load_playlists(client, conn, [PL_A])
    assert out["results"][0]["status"] == "unchanged"
    assert conn.execute("SELECT followers FROM playlists").fetchone()[0] == 9


def test_bad_refs_and_api_errors_are_reported_per_playlist(conn):
    out = collection.load_playlists(FakeClient(), conn, ["not a playlist", "Z" * 22])
    assert out["results"][0]["status"] == "error"
    assert out["results"][1] == {"id": "Z" * 22, "status": "error", "error": "404"}


def test_list_playlists_marks_ownership_and_cache_state(conn):
    client = FakeClient()
    collection.load_playlists(client, conn, [PL_A])
    client.snapshots[PL_A] = "s2"
    out = collection.list_playlists(client, conn)
    a, b = out["playlists"]
    assert out["user"] == "Facu"
    assert (a["mine"], a["tracks"], a["cache"]) == (True, 4, "changed")
    assert (b["mine"], b["tracks"], b["cache"], b["owner"]) == (False, 3, None, "Ana")


def test_forget_deletes_the_file(tmp_path):
    path = tmp_path / "cache.db"
    store.connect(path).close()
    assert store.forget(path) is True
    assert not path.exists()
    assert store.forget(path) is False


# ---------------------------------------------------------------- popularity 0: relinking

class RelinkClient(FakeClient):
    """t1 is a replaced album version (relinked to n1), t3 is unavailable, t2 is popular."""

    def __init__(self):
        super().__init__()
        for rows in self.items.values():
            for row in rows:
                if row["item"].get("id") in ("t1", "t3"):
                    row["item"]["popularity"] = 0
        self.track_calls = []

    def tracks(self, ids, market=None):
        self.track_calls.append((list(ids), market))
        out = []
        for i in ids:
            if market and i == "t1":
                out.append({"id": "n1", "popularity": 0, "is_playable": True, "linked_from": {"id": "t1"}})
            elif market and i in ("t3", "gone"):
                out.append({"id": i, "popularity": 0, "is_playable": False})
            elif market and i == "alt3":
                out.append({"id": "alt3", "is_playable": True})
            elif i == "n1":
                out.append({"id": "n1", "popularity": 66, "album": {"name": "Album t1"},
                            "external_urls": {"spotify": "https://open.spotify.com/track/n1"}})
            else:
                out.append(None)
        return out

    def search_tracks(self, query, limit=5):
        return [{"id": "gone", "name": "Three", "popularity": 80, "artists": [{"name": "AR3"}], "album": {}},
                {"id": "alt3", "name": "Three, - Remastered", "popularity": 40, "artists": [{"name": "AR3"}],
                 "album": {"name": "Best Of", "release_date": "2005-01-01"},
                 "external_urls": {"spotify": "https://open.spotify.com/track/alt3"}},
                {"id": "x", "name": "Three", "popularity": 90, "artists": [{"name": "Someone else"}], "album": {}}]


def test_load_relinks_popularity_zero_tracks(conn):
    client = RelinkClient()
    collection.load_playlists(client, conn, [PL_A, PL_B])
    asked, market = client.track_calls[0]
    assert sorted(asked) == ["t1", "t3"] and market == "from_token"
    t1 = conn.execute("SELECT * FROM tracks WHERE id = 't1'").fetchone()
    assert (t1["current_id"], t1["current_popularity"], t1["unavailable"]) == ("n1", 66, 0)
    assert conn.execute("SELECT unavailable, current_id FROM tracks WHERE id = 't3'").fetchone()[:] == (1, None)

    # Asked once: a forced reload keeps the answer while the track is still at popularity 0.
    client.track_calls.clear()
    collection.load_playlists(client, conn, [PL_A, PL_B], force=True)
    assert client.track_calls == []
    assert conn.execute("SELECT current_id FROM tracks WHERE id = 't1'").fetchone()[0] == "n1"


def test_stats_use_the_current_versions_popularity(conn):
    from tailorlist_mcp import dashboard
    collection.load_playlists(RelinkClient(), conn, [PL_A, PL_B])
    pops = {r["id"]: r["popularity"] for r in dashboard.records(conn)}
    assert pops == {"t1": 66, "t2": 50, "t3": 0}


def test_find_replaced_tracks(conn):
    from tailorlist_mcp import queries
    client = RelinkClient()
    collection.load_playlists(client, conn, [PL_A, PL_B])
    out = queries.find_replaced_tracks(conn, client=client)
    assert "Replaced by another version: 1; not playable in your country: 1" in out
    assert "One | AR1 | Alpha | Album t1 | (same album) | 66 |" in out
    assert "Three, - Remastered - Best Of (2005), popularity 40, https://open.spotify.com/track/alt3" in out
    assert "Someone else" not in out and "90" not in out and "80" not in out  # other artist; not playable here
    assert "Beta" in out and "Alpha" not in queries.find_replaced_tracks(conn, ["Beta"], client=client).split("Not playable")[1]
