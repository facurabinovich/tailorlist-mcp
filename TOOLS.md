# Tools: the definitive list

Goal: replicate all, or nearly all, of the original Tailorlist app (`PROYECTOS/spotify-streamlit`)
as MCP tools. Built from a survey of its 9 pages, the 15 `core/` modules, the feature inventory in
its `SPEC.md` and the tools planned for the archived agent (`agent-archive` tag).

Status: ✅ done · ⏳ later phase. Approved by Facu 2026-11-09 (lean version: few fixed
dashboards, data tools for everything else).

## Approach

- **Few fixed dashboards.** Only for views that repeat and carry a lot: the overview, a
  playlist, and the draft of new playlists. They open as a browser page and as an MCP App
  (Claude Desktop shows it in the chat once the local-server bug is fixed). Claude gets a short
  text summary, not the page.
- **Data tools for everything else.** Claude answers and draws its own charts (artifacts in
  Claude Desktop) from small, already-aggregated tables: `collection_stats` for any count or
  average by any dimension, `find_tracks` for listings. That covers the artist and album pages,
  the comparison charts and every chart drill-down of the original, plus questions no fixed
  page answered.
- **Claude never gets the raw collection.** Tools return aggregates or capped listings, so a
  5,000-track library doesn't flood the conversation.
- **Metric definitions** (listening age, popularity, genre families, duplicates vs. versions)
  live in `explain(term)` (they were in the server instructions until Claude Code was seen
  truncating them). The server instructions hold the curator role; the `start` prompt lists
  what Tailorlist can do.
- If a section of the original is missed, it can come back as a fixed dashboard later.

## The tools

### Setup and data

| Tool | Replaces in the original | Phase |
|---|---|---|
| `connect_spotify` | (new: the app used its own Spotify app) | ✅ |
| `list_my_playlists` | Home: add playlists by URL | ✅ |
| `load_playlists(playlists, force)` | Home: review + load; opens the overview | ✅ |
| `unload_playlists(playlists)` | (new, asked by Facu) take playlists out of the local cache and the drafts; Spotify untouched; shows the overview of what remains | ✅ |
| `forget_my_data(also_log_out)` | Home: "Delete my session data", "Start over" | ✅ |
| `fill_genres()` | (new) MusicBrainz genres for artists Spotify leaves empty; background, ~1 req/s, cached | ✅ |
| `fill_years()` | (new, from the backlog) each song's original release year from MusicBrainz, so compilations and reissues stop skewing years and decades; background, ~1.6 s per song, cached | ✅ |
| `fetch_audio_features(playlists?)` | Enrich page: ReccoBeats with progress, retry of failed tracks, cancel, coverage stats | 4 |

### Dashboards (fixed views)

| Tool | Original page | Phase |
|---|---|---|
| `show_overview` | Overview: KPIs, oldest/newest, listening age, tracks per playlist, top artists, decades, popularity, most popular artists, favourite albums, genres, additions over time, duplicates | ✅ (audio averages ⏳4) |
| `show_playlist(playlist)` | Playlist Detail, see the 2a check below | ✅ |
| `show_draft(draft)` | Cluster Results: one card per group, unmatched tracks | ✅ |

### Data tools (Claude answers and charts)

| Tool | What | Phase |
|---|---|---|
| `get_playlist_tracks(playlist)` | every track of a playlist, in order (Playlist Detail "All Tracks") | ✅ |
| `collection_stats(group_by, split_by?, metrics?, filters?, limit?)` | counts and averages by decade, year, artist, album, genre, genre family, playlist, month added or popularity bin, optionally split by a second dimension (e.g. decade × playlist). Metrics: unique tracks, playlist entries, hours, average popularity, share. Same filters as `find_tracks` | ✅ |
| `find_tracks(filters, sort?, limit?)` | listings with filters: playlists, artist, album, genre or genre family, decade or year range, popularity range, added between, duration range, text in the name; sort by popularity, year, date added, duration. Audio features and mood ⏳4 | ✅ |
| `find_duplicates(playlists?, kind?)` | exact duplicates (same Spotify ID twice, across or inside playlists) and different versions (same song, different IDs), with the playlists each one is in | ✅ |
| `find_replaced_tracks(playlists?)` | tracks with popularity 0: album versions Spotify replaced (with the version it plays now, from relinking) and tracks not playable in the user's country (with alternatives from search). Stats use the current version's popularity. Not in the original app | ✅ |
| `explain(term)` | (the original's Glossary page) metric definitions, read when the user asks what a number means; moved out of the server instructions, which Claude Code truncated | ✅ |
| `compare_playlists(playlists?)` | one row per playlist: size, followers, hours, average duration, longest/shortest track, average popularity, genre diversity, release era (median, oldest, newest year), playlist age (first/last added), tracks only in that playlist; plus how many tracks each pair shares | ✅ |

### Building playlists (My Clusters + Cluster Results)

| Tool | Replaces | Phase |
|---|---|---|
| `group_tracks(mode, filters, …)` | My Clusters: pre-filters (playlists, decades, genre families, with a preview count) and the 3 modes: by category (decade / genre family / both), by specific genres (picked by family), by audio similarity (KMeans, auto k by elbow/silhouette or manual k, auto-named groups) ⏳4. Leaves out tracks already organized. Saves a **draft** and opens it | ✅ (KMeans ⏳4) |
| `edit_draft(draft, changes)` | rename a group, move tracks, assign unmatched tracks, drop or merge groups, keep a group visible, skip the rest | ✅ |
| `organize_progress()` / `reset_progress()` | My Clusters progress: tracks already placed in a playlist you built are "done", leave the pool for the next grouping, and count toward a % of the library organized (the 100 % banner). Reset starts over | ✅ (`write_draft` / `create_playlist` from a group mark exactly the written tracks done) |
| `create_playlist(name, tracks or draft group, description)` | export to Spotify (the app used Spotlistr); always previews and asks first; Claude suggests the name | ✅ |
| `write_draft(draft, groups?, names?)` | (new) one playlist per draft group in a single preview; marks the tracks done | ✅ |
| `add_tracks(playlist, tracks)` / `remove_tracks(playlist, tracks)` | (new) edit existing playlists, with preview; never delete playlists | ✅ |
| `replace_versions(playlist?)` | (new, from the backlog) swap replaced album versions for the current version, same position | ✅ |
| `undo_last_write()` | (new) revert the last add, remove or replace | ✅ |
| `confirm_write(code)` | (new) the only tool that writes: carries out a previewed plan; keep it on "ask every time" | ✅ |
| `export_tracks(tracks or group, format)` | the "Artist - Song" text for Spotlistr, track URLs, CSV | ✅ |

### Audio (Phase 4)

Audio DNA, mood map, radar, feature distributions, correlations, extreme tracks, KMeans and
the audio sections of the overview, playlist and comparison. Decided in Phase 4 with real
data: most of it should be `collection_stats` and `find_tracks` with audio metrics (Claude
charts), plus audio sections in `show_playlist`.

### From the backlog ([TODO.md](TODO.md))

| Tool | What |
|---|---|
| `find_replaced_tracks` ✅ / `replace_versions` ✅ | popularity-0 album versions and their current version, offer to swap |
| `fill_years` ✅ | original release year (MusicBrainz title + artist search, ISRC fallback), used by every stat; `find_tracks(sort="year_gap")` lists tracks saved from compilations. Swapping them for the original album's version: later |
| `sequence_tracks(curve)` | order a playlist by an energy/tempo curve (planned for the archived agent) |

## Phase 2a check: every non-audio section of the original, and what covers it

| Original | Section | Covered by |
|---|---|---|
| Overview | chart drill-downs (artist, decade, popularity bin, genre, month) | `find_tracks` |
| Overview | "Show top 20" for popular artists and albums | `collection_stats(group_by="artist"/"album")` |
| Playlist Detail | playlist picker, header KPIs (tracks, followers, duration, popularity) | `show_playlist` |
| Playlist Detail | track highlights (most/least popular, oldest/newest, longest/shortest) | `show_playlist` |
| Playlist Detail | averages, top artists, top genres | `show_playlist` |
| Playlist Detail | exact duplicates, different versions | `show_playlist` (full lists: `find_duplicates`) |
| Playlist Detail | era by decade and by year, tracks of a decade | `show_playlist`, `find_tracks` |
| Playlist Detail | growth over time, peak month, built in one session | `show_playlist` |
| Playlist Detail | all tracks | `show_playlist`, `get_playlist_tracks` |
| Artist / Album | artist or album picker (ranked badges) | `collection_stats(group_by="artist"/"album")` |
| Artist / Album | header card, genre pills, KPIs | `collection_stats` + `find_tracks(artist=…)` |
| Artist / Album | tracks, albums in your collection, jump to album | `find_tracks(artist=…/album=…)`, `collection_stats(group_by="album", filters={artist})` |
| Artist / Album | extremes (most/least popular, oldest/newest) | `find_tracks(artist=…, sort=…)` |
| Artist / Album | playlist distribution | `collection_stats(group_by="playlist", filters={artist})` |
| Artist / Album | popularity distribution, release timeline / year | `collection_stats(group_by="popularity_bin"/"year", filters={artist})` |
| Artist / Album | album release year and age | `find_tracks(album=…)` (release date) |
| Comparison | size, followers | `compare_playlists` |
| Comparison | exact duplicates, different versions | `find_duplicates(playlists)` |
| Comparison | duration (and extremes), popularity | `compare_playlists` |
| Comparison | genre diversity, release era, playlist age, playlists without dates | `compare_playlists` |
| Comparison | decade distribution, tracks of a playlist and decade | `collection_stats(group_by="decade", split_by="playlist")`, `find_tracks` |
| Comparison | "unique" tracks per playlist | `compare_playlists` |
| Home | duplicate note, unavailable tracks note | `load_playlists` results, `show_overview` |
| Glossary | metric definitions | server instructions |

Checked against Spotify on 2026-11-09: playlist `followers.total` is returned in Development
Mode (Alt7: 5, JC: 19).

## Not replicated

- **Home and session handling**: URL params (`?playlists=`, `?sid=`), session cookies, the
  7-day MySQL session. The local cache does this job.
- **Enrich page UI** (progress screen, rotating tips): progress goes through MCP progress
  notifications.
- **Fixed pages for Artist / Album and Comparison, and the Glossary page**: data tools and
  Claude's own charts instead (see Approach).
- **Contact page, analytics, sidebar navigation, global CSS, dev collection**: app plumbing.
- **Demo Collection**: already removed in the original.
- **Kaggle fallback DB**: dropped (licence).
- **Pre-trained models** (`models/*.pkl`, `ml/` notebooks): trained on the removed Demo
  Collection; My Clusters imports the KMeans path but clusters each collection on the fly.

## Phases with this list

1. ✅ Skeleton, overview, `get_playlist_tracks`.
2. Analysis without audio, in two parts:
   - **2a.** `fill_genres`, `collection_stats`, `find_tracks`, `find_duplicates`,
     `compare_playlists`, `show_playlist`, metric definitions in the instructions.
   - **2b ✅.** `group_tracks` (category and genre modes), `show_draft`, `edit_draft`,
     `organize_progress` / `reset_progress`.
3. ✅ Write: `create_playlist`, `write_draft`, `add_tracks`, `remove_tracks`, `replace_versions`,
   `undo_last_write`, `confirm_write`, `export_tracks` (plan in [PLAN.md](PLAN.md)).
4. Audio: `fetch_audio_features`, KMeans mode, audio metrics in the data tools and dashboards.
5. Launch.
