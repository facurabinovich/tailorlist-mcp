"""Guided setup: Client ID validation, saving, the setup page server and connect_spotify."""
import socket
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import MagicMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from tailorlist_mcp import browser, server, settings, setup_guide
from tailorlist_mcp.sources import spotify_auth, spotify_setup

GOOD = "0123456789abcdef0123456789ABCDEF"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _req(url, data=None):
    """(status, headers, body) without following redirects."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    try:
        r = _opener.open(urllib.request.Request(url, data=body), timeout=5)
    except urllib.error.HTTPError as e:
        r = e
    return r.code, r.headers, r.read().decode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAILORLIST_HOME", str(tmp_path))
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    port = _free_port()
    monkeypatch.setenv("SPOTIFY_REDIRECT_URI", f"http://127.0.0.1:{port}/callback")
    monkeypatch.setenv("TAILORLIST_OPEN_BROWSER", "0")
    monkeypatch.setattr(spotify_auth, "_current", None)
    monkeypatch.setattr(spotify_setup, "_current", None)
    yield tmp_path
    setup = spotify_setup._current
    if setup is not None:
        setup.stop()
        setup.wait(5)
    if spotify_auth._current is not None:
        spotify_auth._current.stop()


@pytest.fixture
def setup(env):
    s = spotify_setup.start_setup()
    yield s
    s.stop()
    assert s.wait(5)


class TestGuide:
    @pytest.mark.parametrize("text,expected", [
        (GOOD, GOOD.lower()),
        (f"  {GOOD} ", GOOD.lower()),
        (f'"{GOOD}"', GOOD.lower()),
        (f"'{GOOD}'", GOOD.lower()),
        (GOOD[:-1], None),
        (GOOD + "0", None),
        ("g" * 32, None),
        ("", None),
        (None, None),
    ])
    def test_valid_client_id(self, text, expected):
        assert setup_guide.valid_client_id(text) == expected

    def test_text_has_everything(self):
        uri = "http://127.0.0.1:9999/callback"
        text = setup_guide.as_text(uri)
        assert uri in text and "Premium" in text
        assert all(s["title"] in text for s in setup_guide.steps(uri))
        assert all(t in text for t in setup_guide.troubleshooting())

    def test_page_uses_the_same_steps(self):
        uri = "http://127.0.0.1:9999/callback"
        page = spotify_setup.render_page(uri, "tok")
        assert uri in page and 'value="tok"' in page
        assert all(s["title"] in page for s in setup_guide.steps(uri))


class TestSaveClientId:
    def test_creates_file(self, env):
        settings.save_client_id(GOOD)
        assert (env / ".env").read_text() == f"SPOTIFY_CLIENT_ID={GOOD}\n"
        assert settings.spotify_app().client_id == GOOD

    def test_preserves_other_lines_and_replaces_old_id(self, env):
        (env / ".env").write_text("# my app\nSPOTIFY_CLIENT_ID=old\nSPOTIFY_REDIRECT_URI=http://127.0.0.1:1/x\n")
        settings.save_client_id(GOOD)
        assert (env / ".env").read_text() == f"# my app\nSPOTIFY_CLIENT_ID={GOOD}\nSPOTIFY_REDIRECT_URI=http://127.0.0.1:1/x\n"

    def test_appends_to_file_without_id(self, env):
        (env / ".env").write_text("# note\nOTHER=1")
        settings.save_client_id(GOOD)
        assert (env / ".env").read_text() == f"# note\nOTHER=1\nSPOTIFY_CLIENT_ID={GOOD}\n"

    def test_environment_still_wins(self, env, monkeypatch):
        settings.save_client_id(GOOD)
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "fromenv")
        assert settings.spotify_app().client_id == "fromenv"


class TestSetupSession:
    def test_get_page(self, setup):
        for path in ("/", "/setup"):
            status, _, body = _req(setup.url.replace("/setup", path))
            assert status == 200 and settings.redirect_uri() in body and setup.token in body

    def test_bad_id_is_rejected_and_not_saved(self, setup, env):
        status, _, body = _req(setup.url, {"client_id": "nope", "token": setup.token})
        assert status == 400 and "32 letters and digits" in body and 'value="nope"' in body
        assert not (env / ".env").exists()

    @pytest.mark.parametrize("token", ["wrong", None])
    def test_wrong_token_is_rejected(self, setup, env, token):
        data = {"client_id": GOOD}
        if token is not None:
            data["token"] = token
        status, _, _ = _req(setup.url, data)
        assert status == 403 and not (env / ".env").exists()

    def test_good_id_saves_and_redirects_to_spotify(self, setup, env):
        status, headers, _ = _req(setup.url, {"client_id": f" {GOOD} ", "token": setup.token})
        assert status == 303
        loc = urllib.parse.urlparse(headers["Location"])
        q = urllib.parse.parse_qs(loc.query)
        assert (loc.netloc, loc.path) == ("accounts.spotify.com", "/authorize")
        assert q["client_id"] == [GOOD.lower()] and q["redirect_uri"] == [settings.redirect_uri()]
        assert f"SPOTIFY_CLIENT_ID={GOOD.lower()}" in (env / ".env").read_text()
        assert spotify_auth._current is setup.login

    def test_callback_saves_token_and_stops(self, setup, monkeypatch):
        _req(setup.url, {"client_id": GOOD, "token": setup.token})
        monkeypatch.setattr(spotify_auth, "exchange_code", MagicMock(return_value={"access_token": "a"}))
        redirect = settings.redirect_uri()
        status, _, body = _req(f"{redirect}?code=c&state=wrong")
        assert "out of date" in body and "/setup" in body and not setup.finished()
        status, _, body = _req(f"{redirect}?code=c&state={setup.login.state}")
        assert status == 200 and "Connected" in body
        assert setup.wait(5)
        assert spotify_auth.load_token()["access_token"] == "a"

    def test_unknown_path_and_early_callback_are_404(self, setup):
        base = setup.url.rsplit("/", 1)[0]
        assert _req(f"{base}/nope")[0] == 404
        assert _req(f"{settings.redirect_uri()}?code=c&state=x")[0] == 404

    def test_second_start_reuses_and_busy_port_fails(self, setup):
        assert spotify_setup.start_setup() is setup and spotify_setup.pending() is setup
        with pytest.raises(RuntimeError):
            spotify_setup.SetupSession(settings.redirect_uri()).start()


class TestConnectSpotify:
    def test_not_configured_returns_steps(self, setup_free):
        text = server.connect_spotify()
        s = spotify_setup.pending()
        assert s is not None and s.url in text and "page was opened" not in text
        assert "free Spotify app" in text and "Premium" in text
        assert all(x["title"] in text for x in setup_guide.steps(settings.redirect_uri()))
        assert "connect_spotify(client_id=...)" in text

    def test_bad_client_id(self, env):
        with pytest.raises(ToolError, match="32 letters and digits"):
            server.connect_spotify(client_id="bad")
        assert not (env / ".env").exists()

    def test_good_client_id_returns_login_link(self, env):
        out = server.connect_spotify(client_id=GOOD)
        assert "accounts.spotify.com/authorize" in out and GOOD.lower() in out
        assert f"SPOTIFY_CLIENT_ID={GOOD.lower()}" in (env / ".env").read_text()

    def test_good_client_id_while_setup_pending_uses_its_server(self, setup):
        out = server.connect_spotify(client_id=GOOD)
        assert setup.login is not None and setup.login.url in out
        assert spotify_auth._current is setup.login

    def test_env_var_means_no_setup(self, env, monkeypatch):
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", GOOD)
        out = server.connect_spotify()
        assert "accounts.spotify.com/authorize" in out and spotify_setup.pending() is None

    def test_busy_port_still_returns_steps(self, env):
        with socket.socket() as blocker:
            port = int(settings.redirect_uri().rsplit(":", 1)[1].split("/")[0])
            blocker.bind(("127.0.0.1", port))
            blocker.listen()
            text = server.connect_spotify()
        assert "couldn't be started" in text and "tell it to you" in text
        assert all(x["title"] in text for x in setup_guide.steps(settings.redirect_uri()))


@pytest.fixture
def setup_free(env):
    yield
    s = spotify_setup._current
    if s is not None:
        s.stop()
        s.wait(5)


class TestOpenUrl:
    def test_disabled_never_launches(self, monkeypatch):
        monkeypatch.setenv("TAILORLIST_OPEN_BROWSER", "0")
        monkeypatch.setattr(browser.subprocess, "Popen", MagicMock(side_effect=AssertionError))
        assert browser.open_url("http://127.0.0.1:8888/setup") is False
