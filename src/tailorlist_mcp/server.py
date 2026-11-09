"""The MCP server (stdio). Run `tailorlist-mcp` for the server, `tailorlist-mcp login` to log in
from a terminal, `tailorlist-mcp logout` to forget the login and the cache.
"""
from __future__ import annotations

import sys
import webbrowser
from contextlib import closing
from pathlib import Path

import anyio
import anyio.from_thread
import anyio.to_thread
from mcp.server.apps import Apps
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, TextContent

from tailorlist_mcp import __version__, browser, collection, dashboard, genre_fill, queries, settings, store
from tailorlist_mcp.sources import spotify_auth
from tailorlist_mcp.sources.spotify_api import SpotifyClient, SpotifyError

INSTRUCTIONS = """\
Tailorlist reads, analyzes and reorganizes the user's own Spotify playlists.

Workflow: list_my_playlists -> ask the user which playlists to load -> load_playlists.
load_playlists opens the overview dashboard by itself (in the user's browser, and inside the
chat where the app supports it); show_overview opens it again later; show_playlist does the
same for one playlist. If a tool says the user isn't connected, call connect_spotify and give
the user the link it returns.

Never pick playlists to load on your own, and never load all of them unless the user says
so: show the list and ask which ones. Playlists already in the cache ("current") don't need
loading again.

After a dashboard opens, don't repeat its numbers as a long text: add a short comment and
suggest what to look at next. Don't claim a dashboard is on screen: say it was opened in the
user's browser (or saved as a page).

For anything about the user's playlists (what's in one, counts, artists...), use these tools:
the cache has every track. Don't browse Spotify's website or look for the cache file.
  - collection_stats: counts and averages by decade, year, artist, album, genre, genre family,
    playlist, month added or popularity bin (optionally split by a second one). Returns a small
    table: draw the chart yourself (an artifact, where the app has them).
  - find_tracks: listings with filters (playlist, artist, album, genre, family, years, popularity,
    dates, duration, text) and sorting. Capped: narrow the filters instead of asking for more.
  - find_duplicates, compare_playlists, get_playlist_tracks: duplicates, playlist-vs-playlist
    numbers, one playlist's tracks in order.
  - fill_genres: Spotify leaves many artists without genres; this looks them up in MusicBrainz in
    the background (a few minutes per hundred artists). Offer it when many tracks have no genre;
    don't start it without the user's OK. Call it again to see progress.
Playlist arguments accept a name, a unique part of a name, an ID or a Spotify link.

Data comes from Spotify. When you mention playlists or tracks, include their Spotify links
where the tool gave you one.

Definitions (use them when the user asks what a number means):
- Unique tracks: distinct Spotify track IDs; a track saved in two playlists counts once.
  Entries: every saved occurrence (it counts twice). Artists are PRIMARY artists only;
  featured artists show as "A feat. B" but aren't counted.
- Popularity: Spotify's 0-100 score from play counts and how recent they are. It reflects
  current global popularity, not the user's listening, and is 0 for album versions Spotify
  replaced with a newer release. "Avg popularity" averages unique tracks.
- Release year: the album's release date, so compilations and reissues count with the
  album's year, not the song's original year.
- Top decade: the decade with the most unique tracks. Median era: the median release year of
  unique tracks. Listening span: newest minus oldest release year.
- Listening age: median era - 16 = estimated birth year (the age music taste forms); listening
  age = this year - that. Example: median 1994 -> born 1978 -> 48.
- Playlist growth: tracks added per month; a playlist whose tracks all share one date was
  built in a single session.
- Genres come from the primary artist (Spotify's tags, or MusicBrainz's when Spotify has
  none), not from the track. collection_stats group_by="genre" counts EXACT tags (a track is
  counted under each tag of its artist). The dashboards' genre list counts sub-genres too:
  "rock" includes "indie rock". Tracks without any tag are "untagged".
- Genre families: Rock, Pop, Hip-Hop, Electronic, Jazz, Classical, Folk, Latin, Reggae, Other.
  A family matches when any of its keywords appears inside a genre tag (so "baroque pop" is
  Pop); a track can belong to several families; no match means Other. Keywords: Rock = rock,
  punk, metal, grunge, hardcore, emo, shoegaze; Pop = pop; Hip-Hop = hip hop, rap, trap, drill,
  r&b, soul, grime; Electronic = electronic, edm, house, techno, trance, ambient, synthwave,
  disco, new wave, dubstep, drum and bass, electro, downtempo, chillwave, darkwave, cold wave,
  eurodance, italo; Jazz = jazz, bebop, swing, blues, big band, boogie; Classical = classical,
  opera, orchestra, chamber; Folk = folk, acoustic, singer-songwriter, country, americana,
  bluegrass, newgrass, honky tonk; Latin = latin, reggaeton, salsa, cumbia, bossa nova,
  bachata, tango, bolero, merengue, mariachi, ranchera, mpb, trova, soca, calypso, folklore,
  folclor, candombe, murga, villancico; Reggae = reggae, dancehall, ska, ragga, riddim, dub.
  "Other" often holds adult standards, chanson, christmas, comedy, doo-wop, drone, easy
  listening, gospel, new age, schlager, soundtrack and very niche tags.
- Exact duplicate: the same Spotify ID saved more than once (inside one playlist or across
  playlists). Different versions: the same song name and primary artist under different IDs
  (album vs. compilation, remaster, live...). Remaster suffixes aren't normalized, so
  "Song" and "Song - Remastered 2009" are not matched.
- Genre diversity: distinct genre tags among a playlist's artists. Playlist age: when its
  first track was added.
"""

OVERVIEW_URI = "ui://tailorlist/overview.html"
PLAYLIST_URI = "ui://tailorlist/playlist.html"


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


def _dashboard_result(data: dict, intro: str = "", kind: str = "overview", summary: str | None = None,
                      page_data: dict | None = None) -> CallToolResult:
    """Full data for the dashboard (structured), a short summary for Claude (text).

    The dashboard is also written as a local page and opened in the browser, because some
    hosts don't render MCP Apps (and the server can't tell whether this one did).
    page_data: the same dashboard with more detail than the tool result carries (the web page
    is a local file, so size doesn't matter there).
    """
    parts = [intro] if intro else []
    if not data.get("empty"):
        page = browser.write_page(page_data or data, kind=kind)
        opened = browser.open_in_browser(page)
        parts.append(
            (f"Opened the {kind} dashboard in the user's browser" if opened
             else f"Saved the {kind} dashboard as a web page")
            + f" ({page}). Claude Desktop may also show it inside the chat, but it can't be"
            " confirmed from here: don't claim it is on screen; mention the browser page."
        )
    parts.append(summary if summary is not None else dashboard.model_summary(data))
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(parts))],
        structured_content=data,
    )


def _genres_hint(conn) -> str:
    """One line for load_playlists when artists lack genres and fill_genres could help."""
    import time
    counts = store.mb_counts(conn, int(time.time()), genre_fill.RETRY_AFTER)
    if not counts["pending"]:
        return ""
    return (f"\n{counts['pending']} artists have no genres from Spotify. fill_genres can look them up in "
            "MusicBrainz in the background (a few minutes per hundred artists); offer it to the user, don't start it unasked.")


# ---------------------------------------------------------------- dashboard tools (MCP Apps)

apps = Apps()
apps.add_html_resource(
    OVERVIEW_URI,
    (Path(__file__).parent / "ui" / "overview.html").read_text(encoding="utf-8"),
    name="Tailorlist overview",
)
apps.add_html_resource(
    PLAYLIST_URI,
    (Path(__file__).parent / "ui" / "playlist.html").read_text(encoding="utf-8"),
    name="Tailorlist playlist",
)


@apps.tool(resource_uri=OVERVIEW_URI)
async def load_playlists(playlists: list[str], ctx: Context, force: bool = False) -> CallToolResult:
    """Copy playlists from Spotify into the local cache and show the overview dashboard.

    Only load the playlists the user chose. If they haven't said which ones, ask them first
    (list_my_playlists shows the options); don't load all of them unless they ask for that.

    Args:
        playlists: playlist IDs or Spotify playlist links of the playlists the user chose.
        force: re-read playlists even if they haven't changed since the last load.

    Playlists that haven't changed are skipped. The dashboard covers everything in the cache.
    """
    client = _client()

    def progress(done: int, total: int, name: str) -> None:
        try:
            anyio.from_thread.run(ctx.report_progress, done, total, name)
        except Exception:
            pass

    def work() -> CallToolResult:
        with closing(store.connect()) as conn:
            loaded = collection.load_playlists(client, conn, playlists, force=force, progress=progress)
            data = dashboard.overview_data(conn)
            hint = _genres_hint(conn)
        data["load"] = loaded["results"]
        lines = []
        for r in loaded["results"]:
            if r.get("status") == "loaded":
                lines.append(f"- {r['name']}: loaded, {r['tracks']} tracks" + (f", {r['skipped']} skipped (episodes/local files)" if r["skipped"] else ""))
            elif r.get("status") == "unchanged":
                lines.append(f"- {r['name']}: unchanged since last load")
            else:
                lines.append(f"- {r.get('name') or r.get('id') or r.get('ref')}: {r.get('status')} - {r.get('error')}")
        return _dashboard_result(data, "Load results:\n" + "\n".join(lines) + hint)

    return await anyio.to_thread.run_sync(lambda: _spotify_call(work))


@apps.tool(resource_uri=OVERVIEW_URI)
def show_overview() -> CallToolResult:
    """Show the overview dashboard of the loaded playlists again: KPIs, oldest/newest track,
    playlists, top artists, decades, popularity, albums, genres, additions over time and
    duplicates. load_playlists already shows it after loading.
    """
    with closing(store.connect()) as conn:
        return _dashboard_result(dashboard.overview_data(conn))


@apps.tool(resource_uri=PLAYLIST_URI)
def show_playlist(playlist: str) -> CallToolResult:
    """Show the dashboard of one loaded playlist: header numbers, track highlights, averages, top
    artists, duplicates inside it, era by decade and year, top genres, growth over time and all
    its tracks. Opens in the user's browser (and inside the chat where the app supports it).

    Args:
        playlist: the playlist's name (or a unique part of it), ID or Spotify link.
    """
    with closing(store.connect()) as conn:
        try:
            full = dashboard.playlist_data(conn, playlist)
            data = dashboard.playlist_data(conn, playlist, track_limit=dashboard.TRACKS_IN_RESULT,
                                           decade_limit=dashboard.DECADE_TRACKS_IN_RESULT)
        except dashboard.PlaylistNotFound as exc:
            raise ToolError(str(exc)) from exc
    return _dashboard_result(data, kind="playlist", summary=dashboard.playlist_summary(data), page_data=full)


mcp = MCPServer("tailorlist", instructions=INSTRUCTIONS, version=__version__, extensions=[apps])


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
def get_playlist_tracks(playlist: str, include_links: bool = False) -> str:
    """Every track of a loaded playlist, in playlist order: track, artist (with featured
    artists), album, year, duration, popularity and date added. Reads the local cache, so it
    always has the full playlist.

    Args:
        playlist: the playlist's name (or a unique part of it), ID or Spotify link.
        include_links: add each track's Spotify link (longer output).
    """
    with closing(store.connect()) as conn:
        try:
            return dashboard.playlist_track_table(conn, playlist, include_links)
        except dashboard.PlaylistNotFound as exc:
            raise ToolError(str(exc)) from exc


def _answer(fn, *args, **kwargs) -> str:
    """Run a data tool on the cache; its "can't answer that" errors become tool errors."""
    with closing(store.connect()) as conn:
        try:
            return fn(conn, *args, **kwargs)
        except (queries.QueryError, dashboard.PlaylistNotFound) as exc:
            raise ToolError(str(exc)) from exc


@mcp.tool()
def collection_stats(
    group_by: str,
    split_by: str | None = None,
    metrics: list[str] | None = None,
    filters: queries.Filters | None = None,
    limit: int = queries.DEFAULT_LIMIT,
) -> str:
    """Counts and averages of the loaded playlists grouped by one dimension (optionally also split
    by a second one), as a small table you can chart. Examples: decade x playlist, top 20 artists,
    tracks per genre family, additions per month, one artist's albums (filters.artist).

    Args:
        group_by: decade, year, artist (primary), album, genre (exact tags), genre_family,
            playlist, month_added or popularity_bin (steps of 5).
        split_by: optional second dimension from the same list; one row per group x split value.
        metrics: any of unique_tracks, entries, hours, avg_popularity, share (% of the selection's
            unique tracks). Default: unique_tracks and share.
        filters: narrow the tracks first (same filters as find_tracks).
        limit: groups to return, biggest first (default 50, max 500). Time dimensions and
            popularity bins are returned in order.
    """
    return _answer(queries.collection_stats, group_by, split_by, metrics, filters, limit)


@mcp.tool()
def find_tracks(
    filters: queries.Filters | None = None,
    sort: str = "popularity",
    ascending: bool | None = None,
    limit: int = queries.DEFAULT_LIMIT,
    include_links: bool = False,
) -> str:
    """List unique tracks of the loaded playlists that pass the filters: track, artist, album, year,
    duration, popularity, first date added and the playlists it is in. Capped at `limit` rows (the
    header says how many match), so narrow the filters instead of asking for everything.

    Args:
        filters: playlists, artist, album, genre (exact tag), genre_family, decade, year_from/to,
            popularity_min/max, added_from/to, duration_min/max (minutes), text (in the name).
        sort: popularity, year, added, duration, name or artist. Default order is descending
            (ascending for name and artist) unless `ascending` says otherwise.
        limit: rows to return (default 50, max 500).
        include_links: add each track's Spotify link (longer output).
    """
    return _answer(queries.find_tracks, filters, sort, ascending, limit, include_links)


@mcp.tool()
def find_duplicates(playlists: list[str] | None = None, kind: str = "all", limit: int = queries.DEFAULT_LIMIT) -> str:
    """Duplicates among the loaded playlists (or only the ones given): exact duplicates (the same
    Spotify ID saved more than once, inside or across playlists) and different versions (same song
    name and primary artist under different IDs), each with the playlists it is in and the IDs.

    Args:
        playlists: names, IDs or links; default all loaded playlists.
        kind: "all", "exact" or "versions".
        limit: rows per list (default 50, max 500).
    """
    return _answer(queries.find_duplicates, playlists, kind, limit)


@mcp.tool()
def compare_playlists(playlists: list[str] | None = None) -> str:
    """One row per playlist (or only the ones given): size, followers, hours, average, shortest and
    longest duration, average popularity, genre diversity, release era (median, oldest, newest year),
    first and last added, and the unique tracks that are in no other playlist; then how many tracks
    each pair of playlists shares. A data tool: draw charts from it yourself.

    Args:
        playlists: names, IDs or links; default all loaded playlists.
    """
    return _answer(queries.compare_playlists, playlists)


@mcp.tool()
def fill_genres() -> str:
    """Fill in the genres Spotify leaves empty, using MusicBrainz (free, about 1 request per second).

    Runs in the background and returns at once; call it again to see progress (it never starts a
    second run). Only primary artists with no Spotify genres are looked up, the most-used first;
    results are saved as they arrive, so every other tool uses them right away and a stopped run
    resumes where it left off. Takes a few minutes per hundred artists. Start it only if the user
    agrees.
    """
    with closing(store.connect()) as conn:
        if not conn.execute("SELECT 1 FROM artists LIMIT 1").fetchone():
            raise ToolError("Nothing loaded yet. Use list_my_playlists, ask which playlists to load, then load_playlists.")
        return genre_fill.describe(genre_fill.FILL.start(conn))


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
