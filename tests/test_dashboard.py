"""dashboard: cache -> the original app's records/frame -> overview data and model summary."""
import json

import pytest

from tailorlist_mcp import collection, dashboard, store

from test_collection import PL_A, PL_B, FakeClient, _track


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "cache.db")
    client = FakeClient()
    # t5 is "Two" again under another ID: a different version of the same song.
    client.items[PL_B].append({"added_at": "2024-02-01T00:00:00Z", "item": _track("t5", "Two", ["ar1"], "2010")})
    collection.load_playlists(client, c, [PL_A, PL_B])
    yield c
    c.close()


def test_records_use_primary_artist_and_its_genres(conn):
    recs = {(r["playlist_name"], r["id"]): r for r in dashboard.records(conn)}
    two = recs[("Alpha", "t2")]
    assert two["artists"] == "AR1" and two["secondary_artists"] == "AR2"
    assert two["artist_genres"] == "rock"  # primary artist only, as in the original app
    assert len(recs) == 5  # Beta has t3 twice: same key, so 6 entries collapse to 5 here


def test_overview_numbers(conn):
    d = dashboard.overview_data(conn, current_year=2026)
    k = d["kpis"]
    assert (k["unique_tracks"], k["entries"], k["playlists"]) == (4, 6, 2)
    assert k["artists"] == 2  # primary artists: AR1, AR3 (AR2 is only featured)
    assert d["oldest"]["name"] == "Three" and d["oldest"]["year"] == 1987
    assert d["newest"]["year"] == 2010
    assert d["top_artists"][0] == {"name": "AR1", "tracks": 3}
    assert [x["decade"] for x in d["decades"]] == [1980, 1990, 2000, 2010]

    dup = d["duplicates"]
    assert dup["tracks_in_several_playlists"] == 1                 # t2 in Alpha and Beta
    assert {i["track"] for i in dup["exact"]["items"]} == {"Two", "Three"}  # t2 across, t3 inside Beta
    assert dup["versions"]["count"] == 1 and dup["versions"]["items"][0]["track"] == "Two"

    json.dumps(d)  # must be JSON-serializable for the dashboard
    assert "Collection: 4 unique tracks" in dashboard.model_summary(d)


def test_empty_cache(tmp_path):
    with store.connect(tmp_path / "empty.db") as c:
        d = dashboard.overview_data(c)
    assert d == {"empty": True}
    assert "Nothing loaded yet" in dashboard.model_summary(d)


def test_playlist_track_table(conn):
    out = dashboard.playlist_track_table(conn, "beta").splitlines()
    assert out[0].startswith("Beta: 4 tracks.")
    assert out[2].startswith("1 | Two | AR1 feat. AR2 | Album t2 | 1995 | 3:00 | 50")
    assert [line.split(" | ")[1] for line in out[2:]] == ["Two", "Three", "Three", "Two"]  # order and repeats kept
    assert dashboard.playlist_track_table(conn, PL_A).startswith("Alpha: 2 tracks.")
    with pytest.raises(dashboard.PlaylistNotFound, match="isn't loaded"):
        dashboard.playlist_track_table(conn, "Gamma")


def test_flag_emoji_names_match_their_letters():
    assert dashboard._match_key("🇨🇰7") == "ck7"
