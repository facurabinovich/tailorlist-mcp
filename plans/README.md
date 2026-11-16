# Plans for everything left in TODO.md

Written 2026-11-16 by the planning session (Fable 5.2), to be executed later by Sonnet 5.5
sessions. One file per item of [TODO.md](../TODO.md). Each plan is self-contained: read this
README first, then the plan, then the files it names. The design decisions are already made in
the plan; where a decision is Facu's, the plan says so under "Confirm with Facu first".

## Order and what can run in parallel

| # | Plan | Needs | Size | Kind |
|---|---|---|---|---|
| 01 | [Doubtful years, checked on the web](01-doubtful-years-web-check.md) | nothing | 1 h | a session with Facu, little code |
| 02 | [Audio: fetch features from ReccoBeats](02-audio-fetch.md) | nothing | large | code |
| 03 | [Audio in the data tools](03-audio-data-tools.md) | 02 | large | code |
| 04 | [Audio: KMeans mode in group_tracks](04-audio-kmeans.md) | 03 | medium | code |
| 05 | [Audio sections in the dashboards](05-audio-dashboards.md) | 03 | medium | code + design |
| 06 | [sequence_tracks](06-sequence-tracks.md) | 02 | medium | code |
| 07 | [Import mode](07-import-mode.md) | nothing (Facu's export helps) | large | code |
| 08 | [Release: version, changelog, PyPI, README](08-release.md) | 02-06 done or cut | medium | infra + docs |
| 09 | [.mcpb, plugin with a skill, MCP Apps retest](09-packaging-mcpb-plugin-skill.md) | 08 | medium | packaging |
| 10 | [Demo video, post, MCP directories](10-launch-content.md) | 08 | small | writing |
| 11 | [Listening history](11-listening-history.md) | after launch | medium | code |
| 12 | [Snapshots, diff, deeper undo](12-snapshots.md) | after launch | medium | code |
| 13 | [Swap compilation tracks for the original album's](13-swap-compilation-tracks.md) | after launch | medium | code |
| 14 | [Last.fm genres](14-lastfm-genres.md) | after launch | small | code |
| 15 | [Tidal step 0](15-tidal-step0.md) | after launch | half a day | spike with Facu |
| 16 | [Tidal: transfer, then a second source](16-tidal-source-and-transfer.md) | 15 passed | large | code |
| 17 | [Navidrome / Subsonic](17-navidrome.md) | 16, and interest | medium | code |

Parallel runs (separate worktrees, see "Worktrees" below):

- 01 and 07 are independent of the audio work and of each other.
- 02 goes alone and first. 03 goes alone after it.
- After 03: **04, 05 and 06 can run at the same time.** 04 owns `drafts.py`, `ui/draft.html` and
  the `group_tracks` function in `server.py`; 05 owns `dashboard.py`, `ui/overview.html`,
  `ui/playlist.html`; 06 owns `write.py` plus one new function in `server.py`. They collide only
  in `server.py` (different functions), `menu.py` and the docs.
- 11, 12, 13 and 14 are independent of each other, but 12 and 13 both edit `write.py`: run them
  one after the other.

In a parallel run the executors do NOT edit `TODO.md`, `TOOLS.md`, `README.md` or `PLAN.md`: each
returns the doc lines in its report and whoever integrates applies them once.

## Rules for every executor

Read `PLAN.md`, `TOOLS.md` and `DESIGN.md` once. Then:

1. **Facu's OK.** Every plan still needs Facu's yes before it is built (TODO.md says so). If you
   are started on a plan, assume he said yes to the plan as written; anything under "Confirm with
   Facu first" that he has not answered, ask before coding that part. Do not add limits,
   defaults or features the plan doesn't have: propose them and wait.
2. **Check the original first.** Before a metric or feature, look at how the Streamlit app did it
   (`/mnt/c/Users/facur/Documentos/PROYECTOS/spotify-streamlit`: `core/`, `pages/`, `app_state/`)
   and port that logic. Each plan names the original code it follows.
3. **Environment.** The venv is on the Linux side:
   `~/.venvs/tailorlist-mcp/bin/python -m pytest -q -p no:cacheprovider` (Python 3.12) and
   `~/.venvs/tailorlist-mcp-py310/bin/python -m pytest -q -p no:cacheprovider` (3.10, pandas 2.3:
   it treats missing dates differently). Both must pass before the work is called done. `mcp` is
   2.x: `from mcp.server.mcpserver import MCPServer`.
4. **Tests never touch the real cache** (`~/.tailorlist-mcp/cache.db`). Use
   `store.connect(tmp_path / "cache.db")`. Code that opens its own connection takes the file from
   the caller's connection (`genre_fill._file_of`) or an explicit argument, never the default.
   After adding tests that run a background job or a new table, compare the real cache's row
   counts before and after the run.
5. **No network in tests.** HTTP clients take an injectable session (see `tests/test_musicbrainz.py`:
   `FakeSession`, `FakeResponse`, `Clock`) or are patched (`tests/test_reccobeats.py`). There is no
   `conftest.py` that blocks requests, so a forgotten fake really calls out.
6. **Real run on a copy.** `mkdir -p /tmp/tl && cp ~/.tailorlist-mcp/cache.db /tmp/tl/ &&
   TAILORLIST_HOME=/tmp/tl TAILORLIST_OPEN_BROWSER=0 ~/.venvs/tailorlist-mcp/bin/python -c "..."`.
   Writing to Spotify for a check uses only the scratch playlist `test-tailorlist-scratch`
   (ask Facu before recreating it; he deletes it by hand). Keep verification proportionate: the
   test suite plus one real run. No fake harnesses, no spy modules.
7. **Product text is English** (tool docstrings, results, pages). Chat with Facu in Spanish.
   Text for Claude never claims something is on the user's screen.
8. **A new tool is not done until:** it is registered in `server.py`; it is in `menu.MENU`
   (`tests/test_menu.py` fails otherwise); `TOOLS.md` and the README tool list have it; new terms
   are in `glossary.TERMS`; it behaves on the demo collection (no network calls, writes refused:
   see `demo.py` and how `fill_genres` / `_write_tool` handle it); it returns aggregates or capped
   listings, never the raw collection; listings of 3+ rows end with a `Chart:` hint (`charts.py`).
9. **Server instructions budget.** `server.INSTRUCTIONS` is 2,440 characters and
   `tests/test_demo.py` asserts `< 2450` (Claude Code truncated a 6,875-character version; 2,440 was
   confirmed to arrive whole). Don't add to it unless the plan gives the exact edit; when it
   grows, pay for it by trimming, and keep the phrases other tests look for ("music curator",
   "explain(term)", "suggest, never decide", "the user explicitly approves that exact", "load_demo").
10. **Design.** Any page or chart goes through `theme.py` / `ui/theme.css` and `DESIGN.md`: tokens
    only (`tests/test_theme.py` rejects raw colours outside `theme.css`), brand header and footer,
    nothing loaded from the network. Before calling a page done, render it with sample data and
    look at a screenshot at desktop width and about 400 px: `.claude/design-review/shoot.sh` and
    `build_samples.py` (untracked, local; headless Edge from WSL). Design calls are yours to make;
    show Facu the result.
11. **Schema changes** go in `store._SCHEMA` (new tables) and `store._ADDED_COLUMNS` (new columns
    on existing tables), so existing caches migrate. `store._prune` and `store.forget` must cover
    new per-track data (the cache is not an archive: Spotify Developer Terms IV.3).
12. **Git.** Work uncommitted unless whoever started you says to commit. One commit per feature,
    message in English, **no `Co-Authored-By` or any AI attribution line** (Facu's rule; it
    overrides the tool's default). Never push: pushing, tagging and publishing are Facu's calls,
    proposed with evidence (test results, real runs, what is unverified).
13. **When done:** move the item in `TODO.md` to "Done" with the date, what was built and what
    was checked (tests, real run, what is not verified yet), and update `PLAN.md` phases if a
    phase closed. A restart of the MCP server is needed before Claude Code or Desktop see new
    tools: tell Facu.

## Worktrees

Agent worktrees are created from `origin/main`, not from local `HEAD`: make sure local `main` is
pushed or fast-forward the worktree first. To collect a worktree's work: `git add -A` inside it,
then in the main tree `git -C <worktree> diff --cached | git apply --3way`.

## How the code is laid out (what the plans assume)

- `server.py`: every tool. Dashboards are `@apps.tool(resource_uri=...)` and return
  `_dashboard_result(data, ...)`; data tools are `@mcp.tool()` and go through `_answer(fn, ...)`;
  write tools go through `_write_tool`.
- `dashboard.records(conn)` is the single reading of the cache: one dict per playlist entry.
  `dashboard.tracks_frame(recs)` turns it into the frame every analysis uses (`track_id`,
  `track_name`, `artist_name` = primary artist, `popularity_raw`, `duration_raw`, `release_year`,
  `decade`, `artist_genres`, `playlist_name`...). `queries._frame(conn)` adds `_tags` and `_added`.
- `queries.py`: `Filters`, `apply_filters`, `collection_stats`, `find_tracks`, `compare_playlists`.
- `drafts.py`: pool, grouping, the draft JSON (`drafts` table), cards for `ui/draft.html`.
- `write.py`: previews store a plan (`pending_writes`), `confirm` runs it, `writes` logs it, the undo
  inverts the ops (`set`, `move`, `add`, `remove`, `replace`, `dedupe`).
- `genre_fill.BackgroundFill`: the background-job machinery (thread, lock row in `jobs`, resume);
  `GenreFill` and `years.YearFill` are its two users.
- `analysis/`: the ported core of the Streamlit app. `analysis/audio_dna.py` and
  `analysis/clustering.py` are ported and not wired to any tool yet.
- `demo.py`: the synthetic collection; `demo.is_demo(conn)`.
- `menu.py`: `MENU` (renders `what_can_i_do` and the `start` prompt) and `CHECKS` (suggestions).

## Decisions collected from the plans: all approved by Facu on 2026-11-16

The "Confirm with Facu first" items listed here are answered: yes to each as proposed. Other points
under that heading in a plan (step-0 results, numbers to share, things he must provide) still need him.

1. 02: `fetch_audio_features` takes no `playlists` argument and has no cancel (the batch endpoint
   makes a whole library a 2-minute job). TOOLS.md had both.
2. 02: the footer and README must now say that track IDs go to ReccoBeats.
3. 03: a new data tool `audio_profile(playlists?)` (the Audio DNA page in one call).
4. 04: KMeans groups always get descriptive names (the original numbered them when k > 5); k is 2-10.
5. 05: the playlist page compares with paired bars instead of a radar chart.
6. 06: `sequence_tracks` works on an existing playlist (previewed write) and on a draft group.
7. 07: imported data behaves like the demo: it never mixes with playlists loaded from Spotify.
8. 08: first public version number 0.1.0; PyPI publishing through GitHub trusted publishing.
9. 12: snapshots expire after 30 days and 10 per playlist.
10. 16: do the transfer to Tidal before Tidal as a source.
