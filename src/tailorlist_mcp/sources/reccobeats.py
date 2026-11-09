"""ReccoBeats audio-features client and the local fallback DB (no Streamlit).

Moved from pipeline/enrich.py when the Demo Collection ETL was removed: these are the
pieces the Your Collection enrichment still uses.
  - fetch_audio_features: two-step ReccoBeats lookup for one Spotify track ID.
  - fetch_fallback_features: audio features from the local SQLite fallback DB
    (data/audio_fallback.db) for tracks ReccoBeats doesn't have.
  - map_audio_features: ReccoBeats response -> flat audio-feature dict.
"""

import logging
import os
import re
import sqlite3

import requests

logger = logging.getLogger(__name__)

_FALLBACK_DB = os.path.join(os.path.dirname(__file__), "..", "data", "audio_fallback.db")

_LOOKUP_ENDPOINT   = "https://api.reccobeats.com/v1/track"
_FEATURES_ENDPOINT = "https://api.reccobeats.com/v1/track/{reccobeats_id}/audio-features"


def fetch_audio_features(spotify_id: str) -> tuple[dict | None, str]:
    """
    Two-step ReccoBeats lookup for one track.

    Step 1: GET /v1/track?ids={spotify_id} → extract content[0].id
    Step 2: GET /v1/track/{reccobeats_id}/audio-features → audio features

    Returns:
        (data_dict, "ok")   — step 2 succeeded; data_dict is the features JSON
        (None,      "skip") — step 1 returned empty content (track not in ReccoBeats)
        (None,      "fail") — step 1 HTTP error OR step 2 failed
    """
    # ── Step 1: resolve Spotify ID → ReccoBeats internal ID ──────────────────
    try:
        resp1 = requests.get(_LOOKUP_ENDPOINT, params={"ids": spotify_id}, timeout=10)
        resp1.raise_for_status()
        payload = resp1.json()
    except requests.exceptions.Timeout:
        logger.warning("ReccoBeats step-1 timeout for %s", spotify_id)
        return None, "fail"
    except requests.exceptions.RequestException as exc:
        logger.warning("ReccoBeats step-1 request error for %s: %s", spotify_id, exc)
        return None, "fail"
    except ValueError:
        logger.warning("ReccoBeats step-1 invalid JSON for %s", spotify_id)
        return None, "fail"

    content = payload.get("content", [])
    if not content:
        logger.debug("ReccoBeats step-1 empty content for %s — SKIP", spotify_id)
        return None, "skip"

    reccobeats_id = str(content[0]["id"])
    if not re.match(r'^[a-zA-Z0-9_-]{1,100}$', reccobeats_id):
        logger.warning("ReccoBeats returned unexpected id format for %s: %r", spotify_id, reccobeats_id)
        return None, "fail"

    # ── Step 2: fetch audio features ──────────────────────────────────────────
    url2 = _FEATURES_ENDPOINT.format(reccobeats_id=reccobeats_id)
    try:
        resp2 = requests.get(url2, timeout=10)
        if resp2.status_code == 404:
            logger.debug("ReccoBeats step-2 404 for %s (rb_id=%s)", spotify_id, reccobeats_id)
            return None, "fail"
        resp2.raise_for_status()
        return resp2.json(), "ok"
    except requests.exceptions.Timeout:
        logger.warning("ReccoBeats step-2 timeout for %s", spotify_id)
        return None, "fail"
    except requests.exceptions.RequestException as exc:
        logger.warning("ReccoBeats step-2 request error for %s: %s", spotify_id, exc)
        return None, "fail"
    except ValueError:
        logger.warning("ReccoBeats step-2 invalid JSON for %s", spotify_id)
        return None, "fail"


def fetch_fallback_features(spotify_id: str) -> dict | None:
    """
    Try to recover audio features from local SQLite fallback DB.
    Returns a mapped dict compatible with map_audio_features output, or None if not found.
    """
    if not os.path.exists(_FALLBACK_DB):
        return None
    try:
        conn = sqlite3.connect(_FALLBACK_DB)
        cursor = conn.execute(
            "SELECT energy, acousticness, valence, tempo, danceability, "
            "loudness, instrumentalness, speechiness, liveness, mode, duration_ms "
            "FROM audio_features WHERE spotify_id = ?",
            (spotify_id,)
        )
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return None
        return {
            "energy":           row[0],
            "acousticness":     row[1],
            "valence":          row[2],
            "tempo":            row[3],
            "danceability":     row[4],
            "loudness":         row[5],
            "instrumentalness": row[6],
            "speechiness":      row[7],
            "liveness":         row[8],
            "mode":             row[9],
            "duration_ms":      row[10],
        }
    except Exception as exc:
        logger.warning("Fallback DB lookup failed for %s: %s", spotify_id, exc)
        return None


def map_audio_features(spotify_id: str, data: dict) -> dict | None:
    """
    Map ReccoBeats audio-features response to dim_audio_features column values.

    ReccoBeats returns 'key'; our schema uses 'key_value' (reserved word workaround).
    Fields not returned by ReccoBeats (time_signature, track_number, disc_number)
    stay NULL; explicit stays False.

    Returns None if no audio metrics are present in the response.
    """
    def _f(key: str) -> float | None:
        val = data.get(key)
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def _i(key: str) -> int | None:
        val = data.get(key)
        if val is None:
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    # ReccoBeats returns 'key'; our schema column is 'key_value' (reserved word)
    key_val = _i("key")

    mapped = {
        "spotify_id":        spotify_id,
        "danceability":      _f("danceability"),
        "energy":            _f("energy"),
        "key_value":         key_val,
        "loudness":          _f("loudness"),
        "mode":              _i("mode"),
        "speechiness":       _f("speechiness"),
        "acousticness":      _f("acousticness"),
        "instrumentalness":  _f("instrumentalness"),
        "liveness":          _f("liveness"),
        "valence":           _f("valence"),
        "tempo":             _f("tempo"),
        # ReccoBeats does not provide these columns
        "time_signature":    None,
        "track_number":      None,
        "disc_number":       None,
        "explicit":          False,
    }

    audio_metrics = (
        "danceability", "energy", "loudness", "speechiness",
        "acousticness", "instrumentalness", "liveness", "valence", "tempo",
    )
    if all(mapped[f] is None for f in audio_metrics):
        logger.warning("ReccoBeats returned no usable audio metrics for %s", spotify_id)
        return None

    return mapped
