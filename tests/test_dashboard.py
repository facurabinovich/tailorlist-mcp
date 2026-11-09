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


def test_playlist_data_follows_the_playlist_detail_page(conn):
    d = dashboard.playlist_data(conn, "beta")
    json.dumps(d)
    assert d["kpis"]["tracks"] == 4 and d["kpis"]["artists"] == 2          # AR1 and AR3 (primary only)
    assert d["kpis"]["followers"] == 7
    assert d["averages"]["top_artist"] == "AR1" and d["averages"]["top_artist_tracks"] == 2
    assert d["top_artists"]["items"] == [{"name": "AR1", "tracks": 2}, {"name": "AR3", "tracks": 2}]
    assert d["top_artists"]["covered_pct"] == 100
    assert d["highlights"]["oldest"]["value"] == "1987" and d["highlights"]["newest"]["value"] == "2010"
    assert d["duplicates"]["exact"]["count"] == 1 and d["duplicates"]["exact"]["items"][0]["track"] == "Three"
    assert d["duplicates"]["versions"]["count"] == 1 and d["duplicates"]["versions"]["items"][0]["versions"] == 2
    assert [x["decade"] for x in d["decades"]] == [1980, 1990, 2010]
    assert d["decade_tracks"]["1990"]["items"][0]["track"] == "Two"
    assert d["growth"]["peak"] is None and d["growth"]["single_session"] == "February 01, 2024"   # only one dated entry
    assert [t["track"] for t in d["tracks"]] == ["Two", "Three", "Three", "Two"] and d["tracks_total"] == 4
    assert d["tracks"][0]["duration"] == "3:00" and d["tracks"][0]["popularity"] == 50

    capped = dashboard.playlist_data(conn, "beta", track_limit=2)
    assert len(capped["tracks"]) == 2 and capped["tracks_total"] == 4
    assert "Beta: 4 tracks" in dashboard.playlist_summary(d)


def test_playlist_without_dates_or_popularity_does_not_break(conn):
    with conn:
        conn.execute("UPDATE playlist_tracks SET added_at = NULL WHERE playlist_id = ?", (PL_A,))
        conn.execute("UPDATE tracks SET popularity = NULL")
    d = dashboard.playlist_data(conn, "alpha")
    assert d["kpis"]["first_added"] == "N/A" and d["growth"]["single_session"] is None
    assert d["highlights"]["most_popular"] is None and d["tracks"][0]["popularity"] is None


def test_overview_reports_musicbrainz_fill(conn):
    store.save_mb_result(conn, "ar3", ["folk"], "url", 1)   # AR3 has no Spotify genres (FakeClient)
    d = dashboard.overview_data(conn)
    assert d["musicbrainz"] == {"without_spotify_genres": 1, "filled": 1, "pending": 0}
    assert "1 artists got their genres from MusicBrainz" in dashboard.model_summary(d)
