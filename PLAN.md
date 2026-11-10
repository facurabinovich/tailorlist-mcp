# Plan

Goal: a good open-source MCP that lets Claude organize your own Spotify library.
Audience: portfolio/visibility and people actually using it. Not monetized.

## Decisions (2026-11-09)

- **Claude is the agent.** We ship tools, not a chat UI. Replaces the Ollama agent
  (F1/F2) of the Streamlit app, archived there under the `agent-archive` tag.
- **Local stdio server, bring-your-own Spotify app.** Spotify Development Mode allows 5
  users per app, so each user registers their own app and uses 1 of its 5 slots.
  Requires Spotify Premium (dev-mode owner requirement).
- **SQLite cache, not MySQL.** Nothing to install. Claude handles summaries and IDs only.
- **Writes are safe:** preview before writing; create and edit playlists, never delete them.
- **Genres:** Spotify first, MusicBrainz for the artists Spotify leaves empty. Last.fm maybe
  later (needs a per-user API key, noisier tags).
- **Audio features:** ReccoBeats only, cached, with backoff on 429. No fallback DB (see below).
- **Local first.** The main goal is using it on my own machine. Publishing goes ahead with
  the Spotify AI-clause risk stated in the README; Spotify is asked in parallel.
- **Spotify terms in the design:** the SQLite store is a refreshable cache, not an archive;
  a tool deletes all cached data; tool results link back to Spotify; no "Spotify" or "Spot..."
  in the name; personal, non-commercial use.

## What we measured

### Official Spotify connector in Claude (Claude Desktop, 2026-11-09)

Five tools: `generate_playlist(prompt, language)`, `search(prompt, language, limit<=20)`,
`save_to_library(uri)`, `remove_from_library(uri)`, `get_currently_playing()`.
OAuth scopes have no `playlist-read-private`. Asked to create "test-tailorlist" with 10 exact
songs, `generate_playlist` made an AI playlist named "Hip Hop Classics & Eclectic Vibes" and
returned no track list. It can't read a playlist or create one from exact tracks.

### Own Spotify app in Development Mode (step 0, 2026-11-09)

| Check | Result |
|---|---|
| List my playlists | 23 (20 owned) |
| Read a full owned playlist | 438/438 via `GET playlists/{id}/items` (rows use `item`) |
| Track fields | album, artists, duration, explicit, ISRC, popularity, ... |
| Batch artists | `GET artists?ids=` works (20 per call) |
| Artist genres | sparse: 14 of 35 artists have any |
| Create playlist | `POST me/playlists` |
| Add exact tracks in order | `POST playlists/{id}/items`, 10/10 in order |
| Remove a track | `DELETE playlists/{id}/items` with body `{"items": [{"uri": ...}]}` |

### MusicBrainz genres

12 of 12 artists that Spotify left empty got good genres (Gorillaz, Milky Chance, Gotye,
Patrick Watson, Soda Stereo, Charly García, Mobb Deep, ...), all exact name matches.
No API key. Rate limit about 1 request/second (503 when faster), so: descriptive
User-Agent, backoff, persistent cache, background fill, only for artists Spotify leaves empty.
Estimate for 5,000 tracks: about 900 lookups, ~15 minutes the first time.

## Phases

1. **Skeleton.** Done 2026-11-09. MCP stdio server (`mcp` 2.x `MCPServer`), PKCE login with
   the user's app (no client secret), SQLite cache that re-reads a playlist only when its
   snapshot changes. Tools: `connect_spotify`, `list_my_playlists`, `load_playlists`,
   `collection_summary`, `forget_my_data`. Connected to Claude Code and Claude Desktop (via
   `wsl.exe`). Real run: 22 playlists, 4,547 entries, 4,239 unique tracks, 1,990 artists
   loaded in 18 s (pages of 100, artists 50 per call, 4 playlists in parallel); 636 artists
   (792 tracks) have no Spotify genres.
2. **Analysis.** (2a done 2026-11-09: `fill_genres`, `collection_stats`, `find_tracks`, `find_duplicates`, `compare_playlists`, `show_playlist`, metric definitions in the instructions; 2b done 2026-11-09: `group_tracks` (category and genres modes), `show_draft`, `edit_draft`, `organize_progress`, `reset_progress`; KMeans waits for phase 4.) Genre families (Spotify + MusicBrainz), duplicates, playlist comparison,
   `cluster` (by genre family, by category, KMeans). Tool shapes from the archived
   `agent/tools.py` of the Streamlit app.
3. **Write.** Create and edit playlists, always previewed and confirmed. Detailed plan below
   (agreed with Facu 2026-11-10).
4. **Audio features.** ReccoBeats, Audio DNA.
5. **Launch.** README with the Spotify app walkthrough, `uvx` install, Claude Desktop
   extension if feasible, 2-minute demo video, post. README states the Spotify AI-clause
   risk.

## Phase 3 plan: writing to Spotify (agreed 2026-11-10)

The original app exported each group through Spotlistr (pages/_Cluster_Results.py: name
`"<group> — <date>"`, "Artist - Song" text or URLs, then a separate "mark these tracks as done"
confirmation). Here the MCP writes the playlists itself. The current scopes
(`playlist-modify-public`, `playlist-modify-private`) are enough: no new login.

### Safety rules (every write tool)

1. **Two steps with a code.** The first call writes nothing: it returns a preview (playlist,
   tracks, changes, visibility) and a confirmation code. Only a second call with
   `confirm="<code>"` writes, and it writes exactly the previewed plan. Codes are single-use and
   expire after 30 minutes. Pending plans live in a `pending_writes` table.
2. **Abort if the playlist changed** on Spotify between preview and confirmation (`snapshot_id`).
3. **Never delete playlists.** Only playlists the user owns (or collaborative ones) are edited.
4. **Re-read after writing:** the touched playlists are reloaded, so cache and dashboards agree.
5. **Write log** (`writes` table): what was done, where, the positions and the snapshot after
   the write. It feeds the undo.

### Decisions

- **Visibility:** Claude asks the user before the preview; the preview states it plainly;
  private when nobody said otherwise.
- **Names:** Claude suggests a name for each new playlist (e.g. "Rock de los 80" for the group
  "1980s · Rock"); the preview shows it and the user approves or changes it. The group name is the
  fallback. The date and the origin go in the description, not the name: "Made with Tailorlist on
  2026-11-10 from Alt7, People7" (Spotify caps descriptions at 300 characters). If a playlist with
  that name already exists in the account, the preview warns and proposes "<name> (2)".
- **Done marking:** automatic once a draft group is written (`drafts.mark_group_written`),
  except groups flagged keep_visible. The preview says so.
- **Replaced and unavailable tracks in new playlists:** the user decides each time. When a group
  holds replaced album versions (see `find_replaced_tracks`), the preview stops and lists them in
  two blocks, "same album" and "other album" (saved album vs. current album; 101 vs. 87 of 188 in
  Facu's library), and Claude asks: all current, keep the saved ones, or current except some.
  Claude recommends "all current" and says why: it is the version Spotify actually plays, its
  stats (popularity) are up to date, and it is often a newer remaster. Tracks not playable in the
  user's country are listed the same way (keep or leave out). The tool has no default: without a
  choice it returns no confirmation code, so the final preview always includes the choice.
- **Removing a track that is in a playlist twice** removes every copy (Spotify API); accepted,
  the preview says so.
- **Undo now:** `undo_last_write` reverts the last add, remove or replace. Not for created
  playlists (undoing would mean deleting). Tracks put back show today's added date (Spotify
  limitation). Refused if the playlist changed after that write.

### Tools

| Tool | What |
|---|---|
| `create_playlist(name, tracks or draft+group, description?, public?)` | new playlist with exact tracks, in order |
| `write_draft(draft, groups?, names?, public?)` | one playlist per draft group, all in one preview; marks the tracks done |
| `add_tracks(playlist, tracks, position?)` | add to an existing playlist; tracks already in it are skipped |
| `remove_tracks(playlist, tracks)` | take tracks out of a playlist |
| `replace_versions(playlist?)` | swap the replaced album versions (see `find_replaced_tracks`) for the current version, same position; their added date becomes today |
| `undo_last_write()` | revert the last add, remove or replace (two steps, like the rest) |
| `export_tracks(draft+group or playlist, format)` | "Artist - Song" text (Spotlistr), track URLs or CSV; writes nothing |

### Steps

1. Spotify write client (create, add with position, remove; batches of 100), `pending_writes`
   and `writes` tables, the preview/confirm mechanism. Check on `test-tailorlist` that adding at
   a position and the remove body behave as in step 0.
2. `create_playlist` and `write_draft`, with names, description, visibility, collision check and
   done marking.
3. `add_tracks` and `remove_tracks`.
4. `replace_versions`.
5. `undo_last_write`.
6. `export_tracks`.
7. Server instructions, TOOLS.md, this plan.

Verification: tests with a fake client; a real run on `test-tailorlist` (create, add, remove,
replace, undo) and writing a real two-group draft.

## Terms and licences (checked 2026-11-09)

- **Kaggle fallback DB: dropped.** It came from "Spotify 1.2M+ Songs"
  (kaggle.com/datasets/rodolfofigueroa/spotify-12m-songs, 2020). Its licence is "Unknown"
  and the data was pulled from the Spotify API, so redistributing it would also break
  Spotify Developer Terms IV.3.1 (no databases of Spotify Content).
- **ReccoBeats** (Terms of Service, 2026-05-25): free, no API key, allowed for "personal and
  commercial projects", no attribution required. Rate limits are unpublished (429 +
  `Retry-After`) and the service can change or shut down without notice.
- **Spotify** (Developer Terms v10 and Developer Policy, effective 2025-05-15):
  - Policy III.14 / Terms IV.2.1: no training on Spotify Content "or otherwise ingest Spotify
    Content into a machine learning or AI model". "Ingest" is not defined; whether an LLM
    reading track names in a conversation counts is unclear. **Open risk; asked Spotify.**
  - Terms IV.3.1-2: no compilations or databases, no indefinite storage; local caching only
    temporary and "strictly necessary".
  - Policy I.1.2: users must be able to disconnect; delete their data.
  - Policy II.4: attribute content to Spotify and link back to the album, playlist or track.
  - Policy VI.2 / Terms VII.1.3: the name can't start with "Spot" or include "Spotify".
  - Terms III.1.1 / Policy IV.2: private personal use, no commercial use.

Tool list (definitive): [TOOLS.md](TOOLS.md). Backlog of unscheduled ideas: [TODO.md](TODO.md).

## Open questions

- Spotify's answer on the AI clause (new thread in the Spotify for Developers forum, where
  developer.spotify.com/support sends policy questions). A clear answer is unlikely: staff usually point back to
  the Developer Policy.
