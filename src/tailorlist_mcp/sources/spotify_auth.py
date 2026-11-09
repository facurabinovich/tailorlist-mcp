"""Spotify login with the user's own app: Authorization Code with PKCE, no client secret.

The flow:
  1. start_login() opens a small HTTP server on the redirect URI's host and port and
     returns the Spotify authorize URL for the user to open.
  2. Spotify redirects the browser back to that server with a code; the server exchanges
     it for tokens, saves them to token.json and stops.
  3. access_token() returns a valid access token, refreshing it when it is about to expire.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from tailorlist_mcp import settings

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"

# Refresh this many seconds before the token actually expires.
_EXPIRY_MARGIN = 60


class NotLoggedIn(RuntimeError):
    """No saved Spotify token: the user has to log in first."""


# ---------------------------------------------------------------- token file

def load_token() -> dict | None:
    path = settings.token_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_token(token: dict) -> None:
    path = settings.token_path()
    path.write_text(json.dumps(token), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def delete_token() -> bool:
    path = settings.token_path()
    if path.is_file():
        path.unlink()
        return True
    return False


def _with_expiry(payload: dict, previous: dict | None = None) -> dict:
    """Token response -> stored token. A refresh may omit refresh_token: keep the old one."""
    token = {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token") or (previous or {}).get("refresh_token"),
        "scope": payload.get("scope") or (previous or {}).get("scope", ""),
        "expires_at": int(time.time()) + int(payload.get("expires_in", 3600)),
    }
    return token


# ---------------------------------------------------------------- PKCE pieces

def make_verifier() -> str:
    return secrets.token_urlsafe(64)[:128]


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorize_url(app: settings.SpotifyApp, verifier: str, state: str) -> str:
    params = {
        "client_id": app.client_id,
        "response_type": "code",
        "redirect_uri": app.redirect_uri,
        "scope": " ".join(settings.SCOPES),
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": challenge_for(verifier),
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(app: settings.SpotifyApp, code: str, verifier: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": app.redirect_uri,
            "client_id": app.client_id,
            "code_verifier": verifier,
        },
        timeout=20,
    )
    resp.raise_for_status()
    return _with_expiry(resp.json())


def refresh(app: settings.SpotifyApp, token: dict) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": app.client_id,
        },
        timeout=20,
    )
    if resp.status_code in (400, 401):
        # Revoked or expired refresh token: the user has to log in again.
        delete_token()
        raise NotLoggedIn("The saved Spotify login is no longer valid. Log in again.")
    resp.raise_for_status()
    return _with_expiry(resp.json(), previous=token)


_refresh_lock = threading.Lock()


def access_token() -> str:
    """A valid access token, refreshed and saved when needed. Raises NotLoggedIn."""
    with _refresh_lock:
        token = load_token()
        if not token or not token.get("access_token"):
            raise NotLoggedIn("Not logged in to Spotify yet.")
        if token.get("expires_at", 0) - _EXPIRY_MARGIN > time.time():
            return token["access_token"]
        if not token.get("refresh_token"):
            raise NotLoggedIn("The saved Spotify login expired. Log in again.")
        token = refresh(settings.spotify_app(), token)
        save_token(token)
        return token["access_token"]


# ---------------------------------------------------------------- login server

_PAGE = """<!doctype html><meta charset="utf-8"><title>Tailorlist</title>
<body style="font-family:system-ui;max-width:32rem;margin:4rem auto;padding:0 1rem">
<h1>{title}</h1><p>{body}</p></body>"""


class LoginSession:
    """One login attempt: a one-shot callback server plus the URL the user has to open."""

    def __init__(self, app: settings.SpotifyApp):
        self.app = app
        self.verifier = make_verifier()
        self.state = secrets.token_urlsafe(16)
        self.url = authorize_url(app, self.verifier, self.state)
        self.done = threading.Event()
        self.error: str | None = None
        self._server: HTTPServer | None = None

    def start(self) -> None:
        parsed = urlparse(self.app.redirect_uri)
        host, port = parsed.hostname or "127.0.0.1", parsed.port or 80
        session = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (http.server API)
                query = parse_qs(urlparse(self.path).query)
                if "code" not in query and "error" not in query:
                    self._reply(404, "Not found", "")
                    return
                title, body = session._handle(query)
                self._reply(200, title, body)

            def _reply(self, status: int, title: str, body: str) -> None:
                html = _PAGE.format(title=title, body=body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)

            def log_message(self, *args):  # keep stdout clean: it is the MCP channel
                pass

        try:
            self._server = HTTPServer((host, port), Handler)
        except OSError as exc:
            raise RuntimeError(
                f"Can't listen on {host}:{port} for the Spotify redirect ({exc}). "
                "Another program is using that port, or a login is already waiting."
            ) from exc
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        assert self._server is not None
        with self._server:
            while not self.done.is_set():
                self._server.handle_request()

    def _handle(self, query: dict) -> tuple[str, str]:
        if query.get("state", [""])[0] != self.state:
            return "Login failed", "The login link is out of date. Start the login again."
        try:
            if "error" in query:
                self.error = f"Spotify returned: {query['error'][0]}"
                return "Login cancelled", "Spotify didn't grant access. You can close this tab."
            save_token(exchange_code(self.app, query["code"][0], self.verifier))
            return "Connected", "Tailorlist can now read your playlists. You can close this tab."
        except requests.RequestException as exc:
            self.error = f"Token exchange failed: {exc}"
            return "Login failed", "Couldn't finish the login. Try again."
        finally:
            self.done.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self.done.wait(timeout)

    def stop(self) -> None:
        self.done.set()


_current: LoginSession | None = None
_current_lock = threading.Lock()


def start_login() -> LoginSession:
    """Start (or reuse) the pending login and return it. Its .url is what the user opens."""
    global _current
    with _current_lock:
        if _current is not None and not _current.done.is_set():
            return _current
        session = LoginSession(settings.spotify_app())
        session.start()
        _current = session
        return session
