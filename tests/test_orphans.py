"""find_orphans on small hand-made caches (always a temp database, never the real cache)."""
import pytest

from tailorlist_mcp import orphans, queries, store


def _row(tid, name, artist="a1", date="1985-01-01"):
    return {
        "track": {"id": tid, "name": name, "album": f"Album {tid}", "album_id": f"al{tid}", "release_date": date,
                  "duration_ms": 180000, "explicit": 0, "popularity": 50, "isrc": None,
                  "url": f"https://open.spotify.com/track/{tid}"},
        "artists": [(artist, artist.upper())],
        "added_at": "2024-01-01T10:00:00Z",
    }


def _save(c, pid, name, rows, owner="me"):
    meta = {"id": pid, "name": name, "owner": {"id": owner}, "snapshot_id": "s", "followers": None,
            "external_urls": {"spotify": f"https://open.spotify.com/playlist/{pid}"}}
    store.save_playlist(c, meta, rows, 0)


@pytest.fixture
def conn(tmp_path):
    c = store.connect(tmp_path / "cache.db")
    yield c
    c.close()


@pytest.fixture
def loaded(conn):
    """Rock 80s and Jazz 50s playlists; Liked has rock-80s, jazz-50s, a 2010s rap song and an other version."""
    _save(conn, "1" * 22, "Rock 80s", [_row("r1", "Rock One"), _row("r2", "Rock Two"), _row("r3", "Hello")])
    _save(conn, "2" * 22, "Jazz 50s", [_row("j1", "Jazz One", "a2", "1955-01-01"), _row("j2", "Jazz Two", "a2", "1957-01-01")])
    _save(conn, store.LIKED_ID, store.LIKED_NAME, [
        _row("r1", "Rock One"),                                   # in a playlist: not an orphan
        _row("x1", "New Rock"),                                   # rock 80s orphan
        _row("x2", "New Jazz", "a2", "1956-01-01"),               # jazz 50s orphan
        _row("x3", "Rap Song", "a3", "2015-01-01"),               # fits nothing
        _row("x4", "Hello - Remastered 2011"),                    # another version of Hello
    ])
    store.save_artist_details(conn, [{"id": "a1", "genres": ["rock"], "popularity": 1},
                                     {"id": "a2", "genres": ["jazz"], "popularity": 1},
                                     {"id": "a3", "genres": ["hip hop"], "popularity": 1}])
    return conn


def test_liked_not_loaded(conn):
    _save(conn, "1" * 22, "Rock 80s", [_row("r1", "Rock One")])
    with pytest.raises(queries.QueryError, match=r"load_playlists\(\[\"liked\"\]\)"):
        orphans.find_orphans(conn)


def test_no_other_playlist(conn):
    _save(conn, store.LIKED_ID, store.LIKED_NAME, [_row("r1", "Rock One")])
    with pytest.raises(queries.QueryError, match="No playlist besides Liked Songs"):
        orphans.find_orphans(conn)


def test_no_orphans(conn):
    _save(conn, "1" * 22, "Rock 80s", [_row("r1", "Rock One"), _row("r2", "Rock Two")])
    _save(conn, store.LIKED_ID, store.LIKED_NAME, [_row("r1", "Rock One"), _row("r2", "Rock Two")])
    out = orphans.find_orphans(conn)
    assert out.startswith("No orphans")
    assert "\n" not in out


def test_clear_fit(loaded):
    out = orphans.find_orphans(loaded)
    assert out.startswith("4 of 5 Liked Songs are in no loaded playlist")
    lines = {line.split(" | ")[0]: line for line in out.splitlines()}
    assert lines["New Rock"].split(" | ")[4] == "Rock 80s"
    assert lines["New Rock"].split(" | ")[3] == "Rock"
    assert lines["New Jazz"].split(" | ")[4] == "Jazz 50s"
    assert lines["New Jazz"].split(" | ")[5] == "100%"      # (1.0 + 1.0) / 2.0
    assert "Chart: " in out
    assert "add_tracks(playlist, tracks)" in out and "group_tracks" in out


def test_no_clear_fit(loaded):
    out = orphans.find_orphans(loaded)
    rap = next(line for line in out.splitlines() if line.startswith("Rap Song"))
    cells = rap.split(" | ")
    assert cells[4] == "no clear fit" and cells[5] == ""
    assert "no clear fit | 1" in out        # its row in the per-playlist table


def test_playlists_restriction(loaded):
    out = orphans.find_orphans(loaded, playlists=["Jazz 50s"])
    jazz_only = [line for line in out.splitlines() if line.startswith("New Rock")][0]
    assert jazz_only.split(" | ")[4] != "Rock 80s"
    assert "Rock 80s | " not in out.split("Track | Artist")[0]


def test_other_version_flag(loaded):
    out = orphans.find_orphans(loaded)
    assert "already in a loaded playlist for 1 of them" in out
    assert "Hello - Remastered 2011 (other version in a playlist)" in out
    assert "New Rock (other version" not in out


def test_only_own_playlists_suggested(loaded):
    _save(loaded, "3" * 22, "Not mine", [_row("n1", "Rock A"), _row("n2", "Rock B"), _row("n3", "Rock C")], owner="someone")
    out = orphans.find_orphans(loaded)
    assert "Not mine | " not in out.split("Track | Artist")[0]
    assert "left out of the suggestions" in out


def test_cap(loaded):
    out = orphans.find_orphans(loaded, limit=2)
    assert "Showing 2 of 4 orphans (2 left out" in out
    assert sum(1 for line in out.splitlines() if line.count(" | ") == 6 and not line.startswith("Track")) == 2
