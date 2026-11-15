"""The guided setup page: one local server for the setup form AND Spotify's redirect.

It binds the redirect URI's host and port (two servers can't share the port). Once the user
submits a valid Client ID, it saves it, creates a LoginSession (without its own server) and
sends the browser to Spotify; Spotify then redirects back to this same server, which hands
the request to that LoginSession.
"""
from __future__ import annotations

import html
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tailorlist_mcp import settings, setup_guide, theme
from tailorlist_mcp.sources import spotify_auth

_TEMPLATE = Path(__file__).parent.parent / "ui" / "setup.html"
_MAX_BODY = 4096
_TIMEOUT = 30 * 60


def _inline(text: str) -> str:
    """Escape, then render **bold** and `code`."""
    out = html.escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", out)


def render_page(redirect_uri: str, token: str, error: str = "", value: str = "") -> str:
    all_steps = setup_guide.steps(redirect_uri)
    intro = all_steps[0]
    steps_html = "".join(
        f'<div class="card step"><div class="num">{i}</div><div><h3>{html.escape(s["title"])}</h3>'
        f"<p>{_inline(s['detail'])}</p></div></div>"
        for i, s in enumerate(all_steps[1:], start=1)
    )
    values = {
        "INTRO": f"<h3>{html.escape(intro['title'])}</h3><p>{_inline(intro['detail'])}</p>",
        "STEPS": steps_html,
        "REDIRECT": html.escape(redirect_uri),
        "TOKEN": html.escape(token),
        "ERROR": f'<p class="error">{html.escape(error)}</p>' if error else "",
        "VALUE": html.escape(value, quote=True),
        "TROUBLE": "".join(f"<li>{_inline(t)}</li>" for t in setup_guide.troubleshooting()),
    }
    template = _TEMPLATE.read_text(encoding="utf-8")
    page = re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), ""), template)
    return theme.inline(page, theme.PAGES["setup"])


def _result_page(title: str, body: str, back: bool) -> str:
    link = '<p><a href="/setup">Back to the setup page</a></p>' if back else ""
    return spotify_auth.result_page(title, body, link)


class SetupSession:
    def __init__(self, redirect_uri: str):
        self.redirect_uri = redirect_uri
        parsed = urlparse(redirect_uri)
        self.host, self.port = parsed.hostname or "127.0.0.1", parsed.port or 80
        self.callback_path = parsed.path or "/"
        self.token = secrets.token_urlsafe(16)
        self.login: spotify_auth.LoginSession | None = None
        self.stopped = threading.Event()
        self._server: HTTPServer | None = None
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/setup"

    def finished(self) -> bool:
        return self.stopped.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self.stopped.wait(timeout)

    def stop(self) -> None:
        self.stopped.set()

    def begin_login(self) -> spotify_auth.LoginSession:
        """The login for the (now configured) app, served by this server. Reused while pending."""
        with self._lock:
            app = settings.spotify_app()
            login = self.login
            if login is not None and not login.done.is_set() and login.app == app:
                return login
            login = spotify_auth.LoginSession(app)
            self.login = login
            spotify_auth.set_current_login(login)
            return login

    def start(self) -> None:
        session = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (http.server API)
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                # Spotify's redirect first: a redirect URI without a path lands on "/", the setup page's address.
                if (parsed.path or "/") == (session.callback_path or "/") and session.login and ("code" in query or "error" in query):
                    title, body = session.login._handle(query)
                    self._send(200, _result_page(title, body, back=title != "Connected"))
                    return
                if parsed.path in ("/", "/setup"):
                    self._send(200, render_page(session.redirect_uri, session.token))
                    return
                self._send(404, _result_page("Not found", "", back=False))

            def do_POST(self):  # noqa: N802
                if urlparse(self.path).path != "/setup":
                    self._send(404, _result_page("Not found", "", back=False))
                    return
                try:
                    length = min(int(self.headers.get("Content-Length") or 0), _MAX_BODY)
                except ValueError:
                    length = 0
                form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
                sent_token = form.get("token", [""])[0]
                if not secrets.compare_digest(sent_token.encode(), session.token.encode()):
                    self._send(403, _result_page("Not allowed", "Open the setup page again and resubmit.", back=True))
                    return
                typed = form.get("client_id", [""])[0]
                client_id = setup_guide.valid_client_id(typed)
                if client_id is None:
                    error = "That doesn't look like a Client ID: it is 32 letters and digits. Copy it again from your app's Settings."
                    self._send(400, render_page(session.redirect_uri, session.token, error, typed[:100]))
                    return
                settings.save_client_id(client_id)
                login = session.begin_login()
                self.send_response(303)
                self.send_header("Location", login.url)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _send(self, status: int, page: str) -> None:
                data = page.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):  # keep stdout clean: it is the MCP channel
                pass

        try:
            self._server = HTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            raise RuntimeError(
                f"Can't listen on {self.host}:{self.port} for the setup page ({exc}). "
                "Another program is using that port, or a setup is already waiting."
            ) from exc
        self._server.timeout = 0.25
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        assert self._server is not None
        deadline = time.monotonic() + _TIMEOUT
        try:
            with self._server:
                while not self.stopped.is_set() and time.monotonic() < deadline:
                    self._server.handle_request()
                    login = self.login
                    if login is not None and login.done.is_set() and login.error is None:
                        break  # connected
        finally:
            if self.login is not None:
                self.login.stop()  # a pending login has no server any more
            self.stopped.set()


_current: SetupSession | None = None
_current_lock = threading.Lock()


def pending() -> SetupSession | None:
    with _current_lock:
        if _current is not None and not _current.finished():
            return _current
        return None


def start_setup() -> SetupSession:
    """Start (or reuse) the setup page server. Raises RuntimeError when the port is busy."""
    global _current
    with _current_lock:
        if _current is not None and not _current.finished():
            return _current
        session = SetupSession(settings.redirect_uri())
        session.start()
        _current = session
        return session
