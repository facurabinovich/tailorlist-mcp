"""spotify_api retries/paging and spotify_auth PKCE, token refresh and the login callback."""
import base64
import hashlib
import json
import socket
import time
import urllib.request
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

from tailorlist_mcp import settings
from tailorlist_mcp.sources import spotify_auth
from tailorlist_mcp.sources.spotify_api import SpotifyClient, SpotifyError


def _resp(status=200, body=None, headers=None):
    r = MagicMock()
    r.status_code = status
    r.headers = headers or {}
    r.content = b"x" if body is not None else b""
    r.json.return_value = body
    r.text = json.dumps(body)
    return r


def _client(*responses):
    http = MagicMock()
    http.request.side_effect = list(responses)
    sleeps = []
    return SpotifyClient(token_provider=lambda: "tok", session=http, sleep=sleeps.append), http, sleeps


class TestSpotifyClient:
    def test_retries_429_with_retry_after_then_succeeds(self):
        client, http, sleeps = _client(_resp(429, {}, {"Retry-After": "3"}), _resp(200, {"id": "me"}))
        assert client.me() == {"id": "me"}
        assert sleeps == [3.0] and http.request.call_count == 2
        assert http.request.call_args.kwargs["headers"] == {"Authorization": "Bearer tok"}

    def test_client_error_raises_with_spotify_message(self):
        client, _, _ = _client(_resp(403, {"error": {"status": 403, "message": "Forbidden"}}))
        with pytest.raises(SpotifyError, match="403: Forbidden"):
            client.me()

    def test_gives_up_after_repeated_server_errors(self):
        client, _, sleeps = _client(*[_resp(502, {})] * 5)
        with pytest.raises(SpotifyError, match="kept failing"):
            client.me()
        assert len(sleeps) == 5

    def test_paged_follows_next(self):
        client, http, _ = _client(
            _resp(200, {"items": [1, 2], "next": "https://api.spotify.com/v1/next-page"}),
            _resp(200, {"items": [3], "next": None}),
        )
        assert list(client.playlist_items("p")) == [1, 2, 3]
        assert http.request.call_args_list[1].args[1] == "https://api.spotify.com/v1/next-page"

    def test_artists_batches_of_50_and_drops_nulls(self):
        ids = [f"a{i}" for i in range(55)]
        client, http, _ = _client(
            _resp(200, {"artists": [{"id": i} for i in ids[:50]]}),
            _resp(200, {"artists": [{"id": "a50"}, None]}),
        )
        assert len(client.artists(ids)) == 51
        assert http.request.call_args_list[1].kwargs["params"]["ids"] == ",".join(ids[50:])


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TAILORLIST_HOME", str(tmp_path))
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    return tmp_path


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestAuth:
    def test_challenge_is_s256_of_verifier(self):
        v = spotify_auth.make_verifier()
        assert 43 <= len(v) <= 128
        expected = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
        assert spotify_auth.challenge_for(v) == expected

    def test_settings_come_from_env_file_when_env_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAILORLIST_HOME", str(tmp_path))
        monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
        monkeypatch.delenv("SPOTIFY_REDIRECT_URI", raising=False)
        with pytest.raises(settings.NotConfigured):
            settings.spotify_app()
        (tmp_path / ".env").write_text('SPOTIFY_CLIENT_ID="abc"\n# comment\nSPOTIFY_REDIRECT_URI=http://127.0.0.1:8501\n')
        assert settings.spotify_app() == settings.SpotifyApp("abc", "http://127.0.0.1:8501")

    def test_access_token_without_login_raises(self, home):
        with pytest.raises(spotify_auth.NotLoggedIn):
            spotify_auth.access_token()

    def test_valid_token_is_returned_without_refresh(self, home, monkeypatch):
        spotify_auth.save_token({"access_token": "a", "refresh_token": "r", "expires_at": time.time() + 3600})
        monkeypatch.setattr(spotify_auth.requests, "post", MagicMock(side_effect=AssertionError))
        assert spotify_auth.access_token() == "a"

    def test_expired_token_is_refreshed_and_keeps_refresh_token(self, home, monkeypatch):
        spotify_auth.save_token({"access_token": "old", "refresh_token": "r", "expires_at": 0})
        post = MagicMock(return_value=_resp(200, {"access_token": "new", "expires_in": 3600}))
        monkeypatch.setattr(spotify_auth.requests, "post", post)
        assert spotify_auth.access_token() == "new"
        assert post.call_args.kwargs["data"] == {"grant_type": "refresh_token", "refresh_token": "r", "client_id": "cid"}
        assert spotify_auth.load_token()["refresh_token"] == "r"

    def test_revoked_refresh_token_deletes_login(self, home, monkeypatch):
        spotify_auth.save_token({"access_token": "old", "refresh_token": "r", "expires_at": 0})
        monkeypatch.setattr(spotify_auth.requests, "post", MagicMock(return_value=_resp(400, {"error": "invalid_grant"})))
        with pytest.raises(spotify_auth.NotLoggedIn):
            spotify_auth.access_token()
        assert spotify_auth.load_token() is None

    def test_login_callback_exchanges_code_and_saves_token(self, home, monkeypatch):
        monkeypatch.setenv("SPOTIFY_REDIRECT_URI", f"http://127.0.0.1:{_free_port()}/callback")
        post = MagicMock(return_value=_resp(200, {"access_token": "a", "refresh_token": "r", "expires_in": 3600}))
        monkeypatch.setattr(spotify_auth.requests, "post", post)
        session = spotify_auth.start_login()
        q = parse_qs(urlparse(session.url).query)
        assert q["code_challenge_method"] == ["S256"] and "client_secret" not in q
        assert spotify_auth.start_login() is session  # a pending login is reused

        redirect = settings.spotify_app().redirect_uri
        bad = urllib.request.urlopen(f"{redirect}?code=c&state=wrong").read().decode()
        assert "out of date" in bad and not session.done.is_set()
        ok = urllib.request.urlopen(f"{redirect}?code=c&state={q['state'][0]}").read().decode()
        assert "Connected" in ok and session.wait(5) and session.error is None
        assert post.call_args.kwargs["data"]["code_verifier"] == session.verifier
        assert spotify_auth.load_token()["access_token"] == "a"
