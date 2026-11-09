"""The MCP server (stdio). Run `tailorlist-mcp` for the server, `tailorlist-mcp login` to log in
from a terminal, `tailorlist-mcp logout` to forget the login and the cache.
"""
from __future__ import annotations

import sys
import webbrowser
from contextlib import closing

import anyio
import anyio.from_thread
import anyio.to_thread
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from tailorlist_mcp import __version__, collection, settings, store
from tailorlist_mcp.sources import spotify_auth
from tailorlist_mcp.sources.spotify_api import SpotifyClient, SpotifyError

INSTRUCTIONS = """\
Tailorlist reads, analyzes and reorganizes the user's own Spotify playlists.

Workflow: list_my_playlists -> ask the user which playlists to load -> load_playlists
(copies them into a local cache) -> collection_summary. If a tool says the user isn't
connected, call connect_spotify and give the user the link it returns.

Never pick playlists to load on your own, and never load all of them unless the user says
so: show the list and ask which ones. Playlists already in the cache ("current") don't need
loading again.

Data comes from Spotify. When you mention playlists or tracks, include their Spotify links
where the tool gave you one.
"""

mcp = MCPServer("tailorlist", instructions=INSTRUCTIONS, version=__version__)


def _client() -> SpotifyClient:
    try:
        spotify_auth.access_token()
    except settings.NotConfigured as exc:
        raise ToolError(str(exc)) from exc
    except spotify_auth.NotLoggedIn as exc:
        raise ToolError(f"{exc} Call connect_spotify to log in.") from exc
    return SpotifyClient()


def _spotify_call(fn):
    try:
        return fn()
    except SpotifyError as exc:
        raise ToolError(str(exc)) from exc
    except spotify_auth.NotLoggedIn as exc:
        raise ToolError(f"{exc} Call connect_spotify to log in.") from exc


@mcp.tool()
def connect_spotify() -> str:
    """Connect Tailorlist to the user's Spotify account (only needed once).

    Returns who is connected, or a login link the user has to open in their browser.
    After they approve access, call this again to confirm.
    """
    try:
        settings.spotify_app()
    except settings.NotConfigured as exc:
        raise ToolError(str(exc)) from exc
    try:
        spotify_auth.access_token()
        me = SpotifyClient().me()
        return f"Connected to Spotify as {me.get('display_name') or me.get('id')}."
    except spotify_auth.NotLoggedIn:
        pass
    except SpotifyError as exc:
        raise ToolError(str(exc)) from exc
    try:
        session = spotify_auth.start_login()
    except RuntimeError as exc:
        raise ToolError(str(exc)) from exc
    # No webbrowser.open here: a browser launched from this process would inherit stdout,
    # which is the MCP channel. Claude shows the link instead.
    return (
        "Not connected yet. Ask the user to open this link, approve access, and tell you when "
        f"they're done; then call connect_spotify again:\n{session.url}"
    )


@mcp.tool()
def list_my_playlists() -> dict:
    """The user's Spotify playlists: id, name, track count, whether they own it, its link, and
    whether it is already in the local cache ("current", "changed" since loading, or null).

    Show the list to the user and ask which playlists they want to load before calling
    load_playlists.
    """
    client = _client()
    with closing(store.connect()) as conn:
        return _spotify_call(lambda: collection.list_playlists(client, conn))


@mcp.tool()
async def load_playlists(playlists: list[str], ctx: Context, force: bool = False) -> dict:
    """Copy playlists from Spotify into the local cache so they can be analyzed.

    Only load the playlists the user chose. If they haven't said which ones, ask them first
    (list_my_playlists shows the options); don't load all of them unless they ask for that.

    Args:
        playlists: playlist IDs or Spotify playlist links of the playlists the user chose.
        force: re-read playlists even if they haven't changed since the last load.

    Returns per-playlist results and the collection size. Playlists that haven't changed are
    skipped. Large playlists take a few seconds each.
    """
    client = _client()

    def progress(done: int, total: int, name: str) -> None:
        try:
            anyio.from_thread.run(ctx.report_progress, done, total, name)
        except Exception:
            pass

    def work() -> dict:
        with closing(store.connect()) as conn:
            return collection.load_playlists(client, conn, playlists, force=force, progress=progress)

    return await anyio.to_thread.run_sync(lambda: _spotify_call(work))


@mcp.tool()
def collection_summary() -> dict:
    """Overview of everything loaded: playlists with sizes and hours, tracks shared between
    playlists or repeated inside one, top artists, Spotify genres and decades.
    """
    with closing(store.connect()) as conn:
        return collection.summary(conn)


@mcp.tool()
def forget_my_data(also_log_out: bool = False) -> str:
    """Delete the local cache of the user's playlists. With also_log_out, delete the saved
    Spotify login too. Nothing on Spotify is changed.
    """
    had_cache = store.forget()
    logged_out = spotify_auth.delete_token() if also_log_out else False
    parts = ["Local cache deleted." if had_cache else "There was no local cache."]
    if also_log_out:
        parts.append("Spotify login deleted." if logged_out else "There was no saved login.")
    return " ".join(parts)


def _login_cli() -> int:
    try:
        session = spotify_auth.start_login()
    except (settings.NotConfigured, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print("Open this link to connect Tailorlist to Spotify:\n", session.url, sep="\n", flush=True)
    try:
        webbrowser.open(session.url)
    except Exception:
        pass
    if not session.wait(timeout=300):
        print("Timed out waiting for the login.", file=sys.stderr)
        return 1
    if session.error:
        print(session.error, file=sys.stderr)
        return 1
    me = SpotifyClient().me()
    print(f"Connected as {me.get('display_name') or me.get('id')}. Token saved in {settings.token_path()}")
    return 0


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["login"]:
        sys.exit(_login_cli())
    if args[:1] == ["logout"]:
        store.forget()
        spotify_auth.delete_token()
        print("Login and local cache deleted.")
        return
    if args and args[0] not in ("serve",):
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
