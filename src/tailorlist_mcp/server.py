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

from tailorlist_mcp import __version__, browser, collection, dashboard, drafts, export, genre_fill, glossary, progress, queries, settings, store, write
from tailorlist_mcp.sources import spotify_auth
from tailorlist_mcp.sources.spotify_api import SpotifyClient, SpotifyError

INSTRUCTIONS = """\
You are the user's music curator. Tailorlist gives you their Spotify playlists; your job is
to (1) analyze the collection — what's in it, how it's built, what's off — and (2) help them
turn it into more coherent playlists. Be curious and opinionated like a good record-store
clerk, but the library is theirs: suggest, never decide. Talk in the user's language.

Start: if they aren't connected, call connect_spotify and give them the link. Then
list_my_playlists and ask which playlists to load; never pick them yourself or load all
unless told. If they don't know where to begin, offer the menu (the `start` prompt) as a
short list.

Analyze: dashboards (show_overview, show_playlist, show_draft) open in the user's browser;
after one opens, add a short comment and suggest what to look at next — don't repeat its
numbers. For questions use the data tools (collection_stats, find_tracks, find_duplicates,
compare_playlists, find_replaced_tracks) and chart the small tables yourself. Never browse
Spotify's website or read the cache file. When a term needs explaining, call explain(term).
Many artists lack genres: offer fill_genres, but only start it with their OK.

Curate: group_tracks (preview_only first to show counts) -> the draft opens -> review it
together -> edit_draft until they're happy. Suggest a playlist name for each group (e.g.
"Rock de los 80" for "1980s · Rock") and let them change it.

Writing to Spotify: every write tool only previews and returns a code; confirm_write(code)
is the only thing that writes. Show the preview (new playlists come out public: Spotify
ignores private; say they can make it private in the app), and call confirm_write only after
the user explicitly approves that exact preview. When a preview lists replaced album versions or unavailable tracks, ask: all
current versions (recommend it: it's the version Spotify actually plays, its stats are up
to date, often a newer remaster), keep the saved ones, or a mix. Never delete playlists.
undo_last_write reverts the last add, remove or replace. reset_progress deletes the
organizing progress: only with explicit consent.

Link to Spotify whenever a tool gives you a link.
"""

OVERVIEW_URI = "ui://tailorlist/overview.html"
PLAYLIST_URI = "ui://tailorlist/playlist.html"
DRAFT_URI = "ui://tailorlist/draft.html"


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
                      page_data: dict | None = None, open_browser: bool = True) -> CallToolResult:
    """Full data for the dashboard (structured), a short summary for Claude (text).

    The dashboard is also written as a local page and opened in the browser, because some
    hosts don't render MCP Apps (and the server can't tell whether this one did).
    page_data: the same dashboard with more detail than the tool result carries (the web page
    is a local file, so size doesn't matter there).
    open_browser: False refreshes the page file without opening another tab (edits of a draft).
    """
    parts = [intro] if intro else []
    if not data.get("empty"):
        page = browser.write_page(page_data or data, kind=kind)
        opened = browser.open_in_browser(page) if open_browser else False
        parts.append(
            (f"Opened the {kind} dashboard in the user's browser" if opened
             else f"Refreshed the {kind} dashboard page (reload the tab if it is open)" if not open_browser
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
apps.add_html_resource(
    DRAFT_URI,
    (Path(__file__).parent / "ui" / "draft.html").read_text(encoding="utf-8"),
    name="Tailorlist draft",
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


@apps.tool(resource_uri=OVERVIEW_URI)
def unload_playlists(playlists: list[str]) -> CallToolResult:
    """Take one or more playlists out of the local cache, so they leave the analysis, and show
    the overview of what remains. Nothing changes on Spotify: the playlists stay in the user's
    account and load_playlists brings them back.

    Tracks and artists that no other loaded playlist has leave the cache too, and leave any
    draft that had them.

    Args:
        playlists: names (or a unique part of the name), IDs or Spotify links of loaded playlists.
    """
    with closing(store.connect()) as conn:
        found, missing = [], []
        for ref in playlists:
            try:
                found.append(dashboard.find_playlist(conn, ref))
            except dashboard.PlaylistNotFound as exc:
                missing.append(str(exc))
        if missing:
            raise ToolError(" ".join(missing) + " Nothing was unloaded.")
        before = collection.overview(conn)
        store.remove_playlists(conn, list({p["id"] for p in found}))
        from_drafts = drafts.prune_unloaded_tracks(conn)
        after = collection.overview(conn)
        data = dashboard.overview_data(conn)
    names = ", ".join(dict.fromkeys(p["name"] for p in found))
    intro = (
        f"Unloaded from the local cache: {names}. Nothing changed on Spotify. "
        f"The cache went from {before['playlists']} playlists / {before['unique_tracks']:,} tracks "
        f"to {after['playlists']} / {after['unique_tracks']:,}."
        + (f" {from_drafts:,} track entries left the drafts." if from_drafts else "")
    )
    if data.get("empty"):
        return CallToolResult(content=[TextContent(type="text", text=intro + " Nothing is loaded now.")])
    return _dashboard_result(data, intro)


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


def _draft_result(conn, d: dict, intro: str = "", open_browser: bool = True) -> CallToolResult:
    full = drafts.draft_data(conn, d, full=True)
    data = drafts.result_data(drafts.draft_data(conn, d))
    return _dashboard_result(data, intro, kind="draft", summary=drafts.summary(data), page_data=full,
                             open_browser=open_browser)


@apps.tool(resource_uri=DRAFT_URI)
def group_tracks(
    mode: str,
    filters: drafts.GroupFilters | None = None,
    by: str = "both",
    genres: list[str] | None = None,
    also_by_decade: bool = False,
    include_done: bool = False,
    preview_only: bool = False,
) -> CallToolResult:
    """Group the loaded tracks into a draft of new playlists, and open the draft dashboard.

    Workflow: narrow the pool with `filters`, call with preview_only=true to see the counts, then
    run it. The draft is saved (ID like "d3") and nothing is written to Spotify. Tracks already
    organized ("done", see organize_progress) are left out unless include_done=true.

    Args:
        mode: "category" (one group per decade and/or genre family) or "genres" (one group per
            chosen genre tag). "kmeans" (by audio similarity) comes with audio features, later.
        filters: playlists, decades and genre_families to narrow the pool before grouping.
        by: category mode only: "decade", "genre_family" or "both" (e.g. "Rock · 1990s"). A track in
            several families goes to the first one. Tracks without a genre or a release decade
            (when that dimension is used) are listed as unmatched, with the reason.
        genres: genres mode: exact genre tags to make groups for (a track with several chosen tags
            is in each group). If omitted, filters.genre_families picks every tag of those families;
            with neither, you get the list of genres in the pool (nothing is saved).
        also_by_decade: genres mode: also split each genre group by decade ("rock · 1980s").
        include_done: include tracks already organized.
        preview_only: don't save a draft; return the pool counts and how many groups/unmatched.
    """
    m = mode.strip().lower()
    if m in ("kmeans", "audio", "audio similarity", "by audio similarity"):
        raise ToolError("Grouping by audio similarity (KMeans) comes with audio features in a later phase. "
                        "Use mode='category' or mode='genres' for now.")
    if m in ("genre", "specific_genres", "by specific genre", "by_genres"):
        m = "genres"
    if m not in ("category", "genres"):
        raise ToolError("mode must be 'category' or 'genres' ('kmeans' comes with audio features).")
    b = by.strip().lower().replace(" ", "_").replace("-", "_")
    b = {"genre": "genre_family", "family": "genre_family", "genres": "genre_family"}.get(b, b)
    if m == "category" and b not in ("decade", "genre_family", "both"):
        raise ToolError("by must be 'decade', 'genre_family' or 'both'.")
    with closing(store.connect()) as conn:
        try:
            pool, counts = drafts.build_pool(conn, filters, include_done)
            chosen = [g for g in (genres or []) if g.strip()]
            if m == "genres" and not chosen:
                if filters and filters.genre_families:
                    chosen = drafts.genres_of_families(pool, filters.genre_families)
                if not chosen:
                    menu = drafts.genre_menu(pool)
                    text = (f"Pool: {len(pool):,} tracks. Pick genres (exact tags) for mode='genres', or give "
                            "filters.genre_families to take all the tags of those families. Genres in the pool by family "
                            "(tracks, top tags):\n" + "\n".join(
                                f"- {x['family']}: {x['genres']} tags: "
                                + ", ".join(f"{t['genre']} ({t['tracks']})" for t in x["top"]) for x in menu))
                    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content={"preview": {"pool": counts, "genre_menu": menu}})
            if not len(pool):
                raise drafts.DraftError("No tracks match those filters" + (" (tracks already organized are left out; include_done=true brings them back)." if counts["done_left_out"] else "."))
            groups, unmatched = drafts.make_groups(conn, pool, counts, m, b, chosen, also_by_decade)
            label = drafts._label(b, m == "genres", also_by_decade)
            if len(groups) > drafts.MAX_GROUPS:
                raise drafts.DraftError(f"That would make {len(groups)} groups (max {drafts.MAX_GROUPS}): narrow the filters or pick fewer genres.")
            if preview_only:
                text = drafts.preview_text(counts, groups, unmatched, label)
                return CallToolResult(content=[TextContent(type="text", text=text + "\nNothing was saved. Run again without preview_only to make the draft.")],
                                      structured_content={"preview": {"pool": counts, "groups": len(groups), "unmatched": len(unmatched)}})
            if not groups:
                raise drafts.DraftError(drafts.preview_text(counts, groups, unmatched, label) + "\nNo group could be made: adjust the filters or the grouping.")
            d = drafts.new_draft(conn, m, b, chosen, also_by_decade, counts, groups, unmatched, include_done)
            return _draft_result(conn, d, intro=drafts.preview_text(counts, groups, unmatched, label))
        except (queries.QueryError, dashboard.PlaylistNotFound, drafts.DraftError) as exc:
            raise ToolError(str(exc)) from exc


@apps.tool(resource_uri=DRAFT_URI)
def show_draft(draft: str | None = None) -> CallToolResult:
    """Show a draft of new playlists as a dashboard (Cluster Results): one card per group with its
    size, hours, top artists, genres and decades and sample tracks, the unmatched tracks with the
    reason, and the overall progress bar. The full track lists are on the web page it opens.

    Args:
        draft: the draft ID ("d3"). Default: the latest one; the text lists the other drafts.
    """
    with closing(store.connect()) as conn:
        try:
            return _draft_result(conn, drafts.load_draft(conn, draft))
        except drafts.DraftError as exc:
            raise ToolError(str(exc)) from exc


@apps.tool(resource_uri=DRAFT_URI)
def edit_draft(draft: str, changes: list[drafts.Change]) -> CallToolResult:
    """Change a draft, then refresh its page. All the changes are applied in order, or none if one fails.
    Only the draft (and the organize progress it touches) changes; nothing is written to Spotify.

    Args:
        draft: the draft ID ("d3").
        changes: a list of operations, each with an "op":
          rename {group, to}; move {tracks, to, from_group?, create_if_missing?} between groups;
          assign {tracks, to} puts unmatched tracks in a group (saved as manual progress, like the
          original's "Save assignments"); drop_group {group} (its tracks stay in the pool, listed as
          left out); merge {groups, into?}; keep_visible {group, on?} (its tracks aren't marked done
          when written); skip_unmatched {} (marks the unmatched tracks without data as done, so they
          leave the pool); reopen {group} (a done group counts as not done).
          `tracks` picks tracks by artist, text (in the name), genre, genre_family, decade, year_from/to,
          popularity, track_ids, reason (unmatched only) or all=true. Group names accept a unique part.
    """
    with closing(store.connect()) as conn:
        try:
            d = drafts.load_draft(conn, draft)
            notes = drafts.apply_changes(conn, d, changes)
        except (queries.QueryError, dashboard.PlaylistNotFound, drafts.DraftError) as exc:
            raise ToolError(f"Nothing changed. {exc}") from exc
        intro = f"Draft {d['id']} updated ({len(notes)} change{'s' if len(notes) != 1 else ''}):\n" + "\n".join(f"- {n}" for n in notes)
        return _draft_result(conn, d, intro=intro, open_browser=False)


mcp = MCPServer("tailorlist", instructions=INSTRUCTIONS, version=__version__, extensions=[apps])



START_PROMPT = """\
Introduce yourself in one line as the user's music curator for their Spotify library, then
offer what you can do as a short list they can pick from:

Understand your collection
 • Overview of everything: decades, genres, artists, popularity, how it grew
 • Deep dive into one playlist
 • Compare playlists: overlap, era, diversity
 • Ask anything: "my most-saved artists", "80s tracks under 3 minutes"...

Clean it up
 • Duplicates and the same song saved in different versions
 • Old album versions Spotify replaced, and tracks that no longer play
 • Fill missing genres (MusicBrainz)

Build better playlists
 • Group your tracks by decade, genre family or specific genres into drafts
 • Review and tweak the drafts together, then save them to Spotify
 • Add or remove tracks in existing playlists (always previewed, undoable)
 • Track how much of your library is already organized

If no playlists are loaded yet, say that the first step is choosing which ones to load,
and offer to list them.
"""


@mcp.prompt(name="start", title="Start with Tailorlist",
            description="Your music curator introduces itself and lists what it can do with your Spotify library.")
def start() -> str:
    return START_PROMPT


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
    and primary artist under different IDs, ignoring suffixes such as "- Remastered 2009" or
    "- Live"), each with the playlists it is in and the IDs.

    Args:
        playlists: names, IDs or links; default all loaded playlists.
        kind: "all", "exact" or "versions".
        limit: rows per list (default 50, max 500).
    """
    return _answer(queries.find_duplicates, playlists, kind, limit)


@mcp.tool()
def explain(term: str = "") -> str:
    """What a Tailorlist number or term means: unique tracks vs. entries, popularity, release year,
    top decade, median era, listening age, playlist growth, genres, genre families (with their
    keywords), genre diversity, duplicates vs. different versions, replaced versions, organized.
    Call it before explaining a metric to the user.

    Args:
        term: a term or part of one ("listening age", "families"); "all" for every definition;
            empty for the list of terms.
    """
    return glossary.explain(term)


@mcp.tool()
async def find_replaced_tracks(playlists: list[str] | None = None, limit: int = queries.DEFAULT_LIMIT) -> str:
    """Tracks with popularity 0, explained: album versions Spotify replaced with a newer release
    (with the version it plays now, its album, popularity and link), and tracks not playable in the
    user's country (with playable alternatives found by searching Spotify). Read-only: nothing
    changes on Spotify. Stats already use the current version's popularity for replaced tracks.

    Args:
        playlists: names, IDs or links; default all loaded playlists.
        limit: rows per list (default 50, max 500).
    """
    client = _client()

    def work() -> str:
        with closing(store.connect()) as conn:
            try:
                collection.relink_popularity_zero(client, conn)  # tracks loaded before this check existed
                return queries.find_replaced_tracks(conn, playlists, limit, client=client)
            except (queries.QueryError, dashboard.PlaylistNotFound) as exc:
                raise ToolError(str(exc)) from exc

    return await anyio.to_thread.run_sync(lambda: _spotify_call(work))


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


# ---------------------------------------------------------------- Phase 3: writing (preview tools + confirm_write)

async def _write_tool(fn) -> str:
    """Run a preview/confirm function with a Spotify client and the cache; its errors become tool errors."""
    client = _client()

    def work() -> str:
        with closing(store.connect()) as conn:
            try:
                return fn(conn, client)
            except (write.WriteError, drafts.DraftError, queries.QueryError, dashboard.PlaylistNotFound, ValueError) as exc:
                raise ToolError(str(exc)) from exc

    return await anyio.to_thread.run_sync(lambda: _spotify_call(work))


@mcp.tool()
async def create_playlist(
    name: str,
    tracks: list[str] | None = None,
    draft: str | None = None,
    group: str | None = None,
    description: str | None = None,
    versions: str | None = None,
    keep_saved: list[str] | None = None,
    unavailable: str | None = None,
) -> str:
    """PREVIEW ONLY, writes nothing: a new playlist with exact tracks, in order. Returns the preview and a
    confirmation code; the playlist is created only when the user agrees and you call confirm_write(code).
    Spotify creates it PUBLIC (the user can make it private in the app afterwards). Suggest a good name yourself
    (e.g. "Rock de los 80" for the group "1980s - Rock") and let the user approve or change it. If a
    draft group is used, its tracks are marked done once written (unless the group is kept visible).

    Args:
        name: the playlist's name. If the account already has one with that name the preview says so and uses "<name> (2)".
        tracks: track IDs, spotify:track: URIs or links, in the order wanted. Or use draft + group instead.
        draft: a draft ID ("d3"); with group, the group's name (or a unique part).
        description: default "Made with Tailorlist on <date> from <playlists>" (300 characters max).
        versions: what to do with album versions Spotify replaced (see find_replaced_tracks): "current" (write
            the version Spotify plays now; recommended) or "saved" (keep the saved ones). With replaced versions
            in the tracks and no choice, NO code is returned: the preview lists them and the user must choose.
        keep_saved: with versions="current", track IDs to keep as saved anyway.
        unavailable: tracks not playable in the user's country: "keep" or "leave_out". Same rule as versions.
    """
    return await _write_tool(lambda conn, client: write.create_playlist(
        conn, client, name, tracks, draft, group, description, versions, keep_saved, unavailable))


@mcp.tool()
async def write_draft(
    draft: str,
    groups: list[str] | None = None,
    names: dict[str, str] | None = None,
    versions: str | None = None,
    keep_saved: list[str] | None = None,
    unavailable: str | None = None,
) -> str:
    """PREVIEW ONLY, writes nothing: one new playlist per draft group, all in one preview and one
    confirmation code. Writing it (confirm_write) creates the playlists and marks exactly the written
    tracks as done (not groups kept visible). The playlists come out PUBLIC (Spotify ignores private);
    suggest a name for each group (names) and show the preview before confirming.

    Args:
        draft: the draft ID ("d3").
        groups: which groups to write (names or unique parts); default all groups with tracks.
        names: {group: playlist name}; a group without an entry uses its own name. Names already in the account get " (2)".
        versions: what to do with album versions Spotify replaced (see find_replaced_tracks): "current" (write
            the version Spotify plays now; recommended) or "saved" (keep the saved ones). With replaced versions
            in the tracks and no choice, NO code is returned: the preview lists them and the user must choose.
        keep_saved: with versions="current", track IDs to keep as saved anyway.
        unavailable: tracks not playable in the user's country: "keep" or "leave_out". Same rule as versions.
    """
    return await _write_tool(lambda conn, client: write.write_draft(
        conn, client, draft, groups, names, versions, keep_saved, unavailable))


@mcp.tool()
async def add_tracks(playlist: str, tracks: list[str], position: int | None = None) -> str:
    """PREVIEW ONLY, writes nothing: add tracks to one of the user's own playlists (or a collaborative one).
    Reads the playlist live from Spotify; tracks already in it are skipped. Returns the preview and a code
    for confirm_write.

    Args:
        playlist: name, unique part of a name, ID or link.
        tracks: track IDs, spotify:track: URIs or links, in the order wanted.
        position: 0-based position to insert at (0 = the start); default the end.
    """
    return await _write_tool(lambda conn, client: write.add_tracks(conn, client, playlist, tracks, position))


@mcp.tool()
async def remove_tracks(playlist: str, tracks: list[str]) -> str:
    """PREVIEW ONLY, writes nothing: take tracks out of one of the user's own playlists (or a collaborative
    one). Reads the playlist live. Spotify removes EVERY copy of a track that is in the playlist twice: the
    preview says which. Returns the preview and a code for confirm_write. Never deletes playlists.

    Args:
        playlist: name, unique part of a name, ID or link.
        tracks: track IDs, spotify:track: URIs or links.
    """
    return await _write_tool(lambda conn, client: write.remove_tracks(conn, client, playlist, tracks))


@mcp.tool()
async def replace_versions(playlist: str | None = None) -> str:
    """PREVIEW ONLY, writes nothing: swap album versions Spotify replaced (popularity 0, see
    find_replaced_tracks) for the version it plays now, in the same position. Their "added" date becomes
    today. Without a playlist, covers every loaded playlist the user can edit, as ONE operation (one
    confirm_write, one undo_last_write). Returns the preview and a code.

    Args:
        playlist: name, unique part of a name, ID or link; default all loaded playlists.
    """
    return await _write_tool(lambda conn, client: write.replace_versions(conn, client, playlist))


@mcp.tool()
async def undo_last_write() -> str:
    """PREVIEW ONLY, writes nothing: revert the last confirmed add, remove or replace (all the playlists it
    touched). Not for created playlists (Tailorlist never deletes), refused if a playlist changed since, and an
    undo can't itself be undone. Returns the preview and a code for confirm_write."""
    return await _write_tool(lambda conn, client: write.undo_last_write(conn, client))


@mcp.tool()
async def confirm_write(code: str) -> str:
    """THE ONLY TOOL THAT WRITES TO SPOTIFY. Carries out exactly the plan a preview tool returned the code
    for. Call it only after the user has seen that preview and explicitly said yes to it; never on your own
    and never for a preview they haven't seen. Codes are single use and expire after 30 minutes. If the
    playlist or the draft changed since the preview, nothing is written and a new preview is needed. If
    something fails half way, the result says exactly what was and wasn't written.

    Args:
        code: the confirmation code from the preview.
    """
    return await _write_tool(lambda conn, client: write.confirm(client, conn, code))


@mcp.tool()
def export_tracks(playlist: str | None = None, draft: str | None = None, group: str | None = None, format: str = "text") -> str:
    """A track list as text, from the cache; writes nothing anywhere. "text" is "Artist - Song" (primary
    artist), one per line, for tools like Spotlistr; "urls" is Spotify track links; "csv" has group, track,
    artists, album, year, id and link.

    Args:
        playlist: a loaded playlist (name, unique part, ID or link), in playlist order. Or use draft.
        draft: a draft ID; with group one group, otherwise every group.
        group: the group's name (or a unique part).
        format: "text", "urls" or "csv".
    """
    with closing(store.connect()) as conn:
        try:
            return export.export_tracks(conn, playlist, draft, group, format)
        except (queries.QueryError, dashboard.PlaylistNotFound, drafts.DraftError, ValueError) as exc:
            raise ToolError(str(exc)) from exc


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
def organize_progress() -> str:
    """How much of the collection is organized: tracks already placed in a playlist built from a
    draft ("done", or skipped on purpose) or assigned to a group by hand ("manual") count toward the
    % of the library organized. Done tracks are left out of the next group_tracks. Reads the cache only.
    """
    with closing(store.connect()) as conn:
        return progress.describe(conn)


@mcp.tool()
def reset_progress(confirm: bool = False) -> str:
    """DESTRUCTIVE for the user's organize progress: forgets which tracks are organized (done and
    manual), so every track is back in the pool and the % goes to 0. Only the progress is cleared:
    drafts, the cache and Spotify are untouched. Ask the user first; with confirm=false it only says
    what would be cleared.

    Args:
        confirm: true only after the user agreed to clear their progress.
    """
    with closing(store.connect()) as conn:
        s = progress.summary(conn)
        n = conn.execute("SELECT COUNT(*) FROM cluster_progress").fetchone()[0]
        if not n:
            return "There is no progress to reset."
        if not confirm:
            return (f"Not reset. This would clear {n:,} progress rows ({s['percent']}% of the library organized, "
                    f"{s['done']:,} done and {s['manual']:,} manual) and can't be undone. If the user agrees, "
                    "call reset_progress with confirm=true.")
        return f"Progress reset: {progress.reset(conn):,} rows cleared. Drafts, the cache and Spotify were not touched."


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
