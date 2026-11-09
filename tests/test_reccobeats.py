"""spotify/reccobeats: ReccoBeats client and fallback DB (moved from pipeline/enrich.py in T28).

HTTP is mocked: conftest blocks real outgoing requests.
"""
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
import requests

import tailorlist_mcp.sources.reccobeats as rb


def _resp(json_data=None, status=200, json_error=False):
    r = MagicMock()
    r.status_code = status
    r.raise_for_status.side_effect = (
        requests.exceptions.HTTPError(f"{status}") if status >= 400 else None
    )
    if json_error:
        r.json.side_effect = ValueError("bad json")
    else:
        r.json.return_value = json_data
    return r


class TestFetchAudioFeatures:
    def test_two_step_lookup_returns_features(self):
        step1 = _resp({"content": [{"id": "rb-123"}]})
        step2 = _resp({"energy": 0.8})
        with patch.object(rb.requests, "get", side_effect=[step1, step2]) as get:
            assert rb.fetch_audio_features("sp1") == ({"energy": 0.8}, "ok")
        assert get.call_args_list[0].kwargs["params"] == {"ids": "sp1"}
        assert get.call_args_list[1].args[0].endswith("/v1/track/rb-123/audio-features")

    def test_unknown_track_is_skip(self):
        with patch.object(rb.requests, "get", return_value=_resp({"content": []})):
            assert rb.fetch_audio_features("sp1") == (None, "skip")

    @pytest.mark.parametrize("step1", [
        requests.exceptions.Timeout(),
        requests.exceptions.ConnectionError(),
    ])
    def test_step1_network_errors_are_fail(self, step1):
        with patch.object(rb.requests, "get", side_effect=step1):
            assert rb.fetch_audio_features("sp1") == (None, "fail")

    def test_step1_invalid_json_is_fail(self):
        with patch.object(rb.requests, "get", return_value=_resp(json_error=True)):
            assert rb.fetch_audio_features("sp1") == (None, "fail")

    def test_unexpected_reccobeats_id_is_fail(self):
        with patch.object(rb.requests, "get", return_value=_resp({"content": [{"id": "../x"}]})):
            assert rb.fetch_audio_features("sp1") == (None, "fail")

    def test_step2_404_is_fail(self):
        steps = [_resp({"content": [{"id": "rb1"}]}), _resp(status=404)]
        with patch.object(rb.requests, "get", side_effect=steps):
            assert rb.fetch_audio_features("sp1") == (None, "fail")


class TestMapAudioFeatures:
    def test_maps_and_casts_fields(self):
        out = rb.map_audio_features("sp1", {"energy": "0.5", "key": "7", "mode": 1, "tempo": 120})
        assert out["spotify_id"] == "sp1"
        assert out["energy"] == 0.5
        assert out["key_value"] == 7
        assert out["mode"] == 1
        assert out["tempo"] == 120.0
        assert out["danceability"] is None
        assert out["explicit"] is False
        assert out["time_signature"] is None

    def test_no_audio_metrics_returns_none(self):
        assert rb.map_audio_features("sp1", {"key": 3, "mode": 1}) is None

    def test_unparseable_values_become_none(self):
        out = rb.map_audio_features("sp1", {"energy": "high", "valence": 0.2})
        assert out["energy"] is None and out["valence"] == 0.2


class TestFetchFallbackFeatures:
    def test_missing_db_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rb, "_FALLBACK_DB", str(tmp_path / "nope.db"))
        assert rb.fetch_fallback_features("sp1") is None

    def test_reads_row_from_fallback_db(self, tmp_path, monkeypatch):
        db = tmp_path / "fb.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE audio_features (spotify_id TEXT, energy REAL, acousticness REAL, "
            "valence REAL, tempo REAL, danceability REAL, loudness REAL, instrumentalness REAL, "
            "speechiness REAL, liveness REAL, mode INTEGER, duration_ms INTEGER)"
        )
        conn.execute("INSERT INTO audio_features VALUES ('sp1',0.1,0.2,0.3,100,0.4,-5,0.0,0.05,0.1,1,200000)")
        conn.commit(); conn.close()
        monkeypatch.setattr(rb, "_FALLBACK_DB", str(db))
        assert rb.fetch_fallback_features("sp1") == {
            "energy": 0.1, "acousticness": 0.2, "valence": 0.3, "tempo": 100,
            "danceability": 0.4, "loudness": -5, "instrumentalness": 0.0,
            "speechiness": 0.05, "liveness": 0.1, "mode": 1, "duration_ms": 200000,
        }
        assert rb.fetch_fallback_features("unknown") is None

    def test_broken_db_returns_none(self, tmp_path, monkeypatch):
        db = tmp_path / "broken.db"
        db.write_text("not a sqlite file")
        monkeypatch.setattr(rb, "_FALLBACK_DB", str(db))
        assert rb.fetch_fallback_features("sp1") is None
