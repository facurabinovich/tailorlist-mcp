# 09. Claude Desktop extension (`.mcpb`), plugin with a skill, MCP Apps retest

Phase 5. Needs 08 (a published package, or at least a wheel that installs with `uvx`).

Three separate deliverables. The formats here change often and the planning model's knowledge of
them may be out of date: **each part starts by reading the current documentation**, and the plan
gives the decisions, not the exact file syntax.

## Part A. MCP Apps retest (15 minutes, with Facu, first)

On 2026-11-09 Claude Desktop 4.23118.0 did not render MCP Apps from local stdio servers (the
issue noted then: anthropics/claude-code#108122); the browser page has been the working path.

1. Note the Claude Desktop version. Restart it. Call `show_overview` (demo or real).
2. Does the dashboard render inside the chat? Do links open (`ui/open-link`)? Does the frame
   size itself (`ui/notifications/size-changed`)? Same for `show_playlist` and `show_draft`.
3. Check the state of the issue on GitHub (`gh issue view 108122 -R anthropics/claude-code`).

If it renders: the browser tab now opens in addition to the in-chat view. Propose to Facu (don't
decide) a setting so the browser page is only written, not opened, when the host renders apps;
the server cannot detect rendering, so it would be `TAILORLIST_OPEN_BROWSER=0` documented in the
README for Desktop users, or flipping the default. Fix any layout problem at the frame's width
with screenshots. If it does not render: record the version and date in TODO.md and stop.

## Part B. Claude Desktop extension (`.mcpb`)

"If feasible" in the TODO. Read first: the MCP Bundles spec and its examples (the repository
moved from `anthropics/dxt` to `modelcontextprotocol/mcpb`; check its README, `MANIFEST.md` and
the Python examples), and Claude Desktop's extension docs.

The feasibility question is Python dependencies: pandas and numpy are compiled per platform, so
bundling them inside one archive does not work across Windows, macOS and Linux. Decide in this
order:

1. If the manifest supports a server type that resolves dependencies with uv at install time
   (a "uv" server type existed in recent manifest versions): use it, pointing at the published
   package and version.
2. Else a manifest whose command is `uvx tailorlist-mcp==<version>`: works when the user has uv;
   the extension's description says so.
3. Else: not feasible; write why in TODO.md and stop.

Manifest content: name `tailorlist`, display name "Tailorlist", the version from `__version__`,
description (one line from the README), author, homepage, license MIT, icon (the wordmark dot on
the dark background: build a PNG from the favicon in `theme.favicon_uri()`; ask Facu for his logo
file if a real icon is wanted: `PROYECTOS/tailorlist-landing/tailorlist-landing/logo.png` has
black text, not usable on dark as is), `user_config` with one optional field for the Spotify
Client ID mapped to the `SPOTIFY_CLIENT_ID` env var (optional because the guided setup asks for
it anyway), the tool list if the format wants it (generate it from the registered tools so it
can't drift; a test compares them).

Put the manifest under `packaging/mcpb/` with a script `scripts/build_mcpb.sh` that builds the
archive into `dist/`. The release workflow (08) attaches it to the GitHub release.

Verify: install the built `.mcpb` in Facu's Claude Desktop (double click), on Windows natively;
run the demo. This is the first time the server runs outside WSL on his machine: expect path and
browser-opening issues (`browser.py`) and fix them.

## Part C. Plugin with a skill

A Claude Code plugin that installs the MCP server and a skill that guides the curation flow.
Read first: the Claude Code plugin docs (plugin manifest location and fields, how a plugin
declares MCP servers, the skills folder layout and `SKILL.md` frontmatter, marketplaces). The
`claude-code-guide` agent answers these.

Layout decision: the plugin lives in this repository, and the repository is its own marketplace,
so the install is two commands (`/plugin marketplace add facurabinovich/tailorlist-mcp`, then
install). The MCP server entry runs `uvx tailorlist-mcp`.

The skill, `curate-library` (name and description decide when it triggers: "Use when the user
wants to analyze, clean up or reorganize their Spotify library with Tailorlist: finding
duplicates, understanding their collection, building playlists by genre, decade, mood or sound,
fixing years and genres"). Its job is the long playbook that does not fit in the 2,450-character
server instructions. Sections to write, each short and concrete, with the tool calls:

1. First session: connected or not (`connect_spotify`, `load_demo`, `import_playlists`), choose
   playlists with the user (never load all unasked), what the overview is for.
2. Getting to know the collection: `tour`, `fun_facts`, `collection_stats` recipes (decade by
   playlist, top artists, genre families), one chart per answer, `explain("chart style")`.
3. Enrichment, always offered and never started unasked: `fill_genres`, `fill_years`,
   `fetch_audio_features`: what each fixes, how long it takes, how to report progress.
4. Clean-up order: `library_health` -> duplicates inside playlists (`dedupe_playlist`) -> replaced
   versions (`replace_versions`) -> doubtful years on the web (the flow of plan 01) -> orphans.
5. Building playlists: narrow the pool, `preview_only` first, pick the mode (category, genres,
   KMeans by sound, `assign_tracks` for moods and occasions), review the draft together,
   `edit_draft`, names, `sequence_tracks`, `write_draft`.
6. Writing safely: show the preview, the user approves that exact preview, `confirm_write`,
   public playlists, `undo_last_write`, never delete.
7. Manners: suggest and let the user decide; the user's language; link to Spotify; don't repeat
   dashboard numbers; never read the cache file or browse Spotify's site.

Write it from the code as it is on the day (tool names and arguments from `server.py`), not from
this list. Add a test that every tool named in the skill file as `name(` or `` `name` `` exists
(like `tests/test_tour.py` does for the tour). Keep `server.INSTRUCTIONS` as the short version;
the skill must not contradict it.

Verify: install the plugin from the local path in Claude Code, start a new session, ask "help me
organize my playlists" and check the skill loads and the first steps follow it; run it on the
demo end to end up to a `write_draft` refusal (the demo refuses writes).

## Docs

README: an "Install" section with the three ways (uvx config, `.mcpb` for Desktop, plugin for
Claude Code), each two lines. TODO.md: what works, what was not feasible and why.
