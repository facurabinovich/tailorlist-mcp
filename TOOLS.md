# Tools: the definitive list

Goal: replicate all, or nearly all, of the original Tailorlist app (`PROYECTOS/spotify-streamlit`)
as MCP tools. Built from a survey of its 9 pages, the 15 `core/` modules, the feature inventory in
its `SPEC.md` and the tools planned for the archived agent (`agent-archive` tag).

Status: ✅ done · ⏳ later phase. Approved by Facu 2026-11-09 (order and the Phase 2 split).

## How the original maps to an MCP

- **Pages become views.** Every analysis page becomes a dashboard tool. The dashboard opens as a
  page in the browser and as an MCP App (Claude Desktop shows it in the chat once the
  local-server bug is fixed). Claude gets a short text summary, not the whole page.
- **Chart clicks become a query tool.** The original listed tracks when you clicked a bar
  (artist, decade, popularity bin, genre, month). Here Claude calls `find_tracks` with the same
  filter.
- **The playlist-building flow becomes draft tools.** My Clusters + Cluster Results (filters, 3
  grouping modes, cards, manual assignment, rename, export) become: group into a draft, show
  it, edit it, write it to Spotify.
- **Home and Enrich go away as pages.** Adding playlists by URL is `list_my_playlists` +
  `load_playlists`; enrichment is a tool with progress.

## The tools

### Setup and data

| Tool | Replaces in the original | Phase |
|---|---|---|
| `connect_spotify` | (new: the app used its own Spotify app) | ✅ |
| `list_my_playlists` | Home: add playlists by URL | ✅ |
| `load_playlists(playlists, force)` | Home: review + load; opens the overview | ✅ |
| `forget_my_data(also_log_out)` | Home: "Delete my session data", "Start over" | ✅ |
| `fill_genres()` | (new) MusicBrainz genres for artists Spotify leaves empty; background, ~1 req/s | 2 |
| `fetch_audio_features(playlists?)` | Enrich page: ReccoBeats with progress and ETA, retry of failed tracks, cancel; coverage stats (Audio DNA's enrichment stats) | 4 |

### Views (dashboards)

| Tool | Original page | Sections | Phase |
|---|---|---|---|
| `show_overview` | Overview | KPIs, oldest/newest, listening age, tracks per playlist, top artists, decades, popularity, most popular artists, favourite albums, genres, additions over time, duplicates | ✅ (audio averages ⏳4) |
| `show_playlist(playlist)` | Playlist Detail | header KPIs, track highlights, averages, top artists, exact duplicates, different versions, era (decade/year, with tracks per decade), top genres, growth over time (peak month, single-session date), all tracks; Audio DNA, mood map and similar playlists (audio-based) ⏳4 | 2 |
| `show_artist(artist)` | Artist / Album (artist mode) | header card, genre pills, tracks, albums in your collection (jump to an album), extremes, playlist distribution, popularity, release timeline; audio fingerprint ⏳4 | 2 |
| `show_album(album, artist)` | Artist / Album (album mode) | same as artist, for one album, with its release year and age | 2 |
| `compare_playlists(playlists?)` | Playlist Comparison | size, followers*, exact duplicates, different versions, duration (with longest/shortest track), popularity, genre diversity, release era, decade mix (tracks per playlist and decade), playlist age (and playlists without dates); audio feature leaders and heatmap ⏳4 | 2 |
| `show_audio_dna(playlists?)` | Audio DNA | mood map, global fingerprint, feature distributions, playlist radar, correlations, extreme tracks | 4 |

\* Followers: check that Development Mode still returns them.

### Queries (Claude reads these, no dashboard)

| Tool | Replaces | Phase |
|---|---|---|
| `get_playlist_tracks(playlist)` | Playlist Detail "All Tracks" | ✅ |
| `find_tracks(filters, sort, limit)` | every chart drill-down (artist, decade, year, popularity bin, genre, month added) + the archived agent's `find_tracks`/`top_tracks`. Filters: playlists, artist, album, genre or family, decade/year range, popularity range, added between, audio features and mood ⏳4 | 2 |
| `find_duplicates(playlists?, kind)` | full lists of exact duplicates and different versions (Playlist Detail, Comparison) | 2 |
| `genre_families(playlists?)` | genre-family breakdown (My Clusters pre-filter counts, archived `genre_families`) | 2 |
| `glossary(term?)` | Glossary page (search) | 2 |

### Building playlists (My Clusters + Cluster Results)

| Tool | Replaces | Phase |
|---|---|---|
| `group_tracks(mode, filters, …)` | My Clusters: pre-filters (playlists, decades, genre families, with a preview count) and the 3 modes: by category (decade / genre family / both), by specific genres (picked by family), by audio similarity (KMeans, auto k by elbow/silhouette or manual k, auto-named groups) ⏳4. Leaves out tracks already organized. Saves a **draft** and opens it | 2 (KMeans 4) |
| `show_draft(draft)` | Cluster Results: one card per group, unmatched tracks | 2 |
| `edit_draft(draft, changes)` | rename a group, move tracks, assign unmatched tracks, drop or merge groups, keep a group visible, skip the rest | 2 |
| `organize_progress()` / `reset_progress()` | My Clusters progress: tracks already placed in a playlist you built are "done" (saved in the cache), leave the pool for the next grouping, and count toward a % of the library organized (the 100 % banner). Reset starts over | 2 (marking done on write: 3) |
| `create_playlist(name, tracks or draft group, description)` | export to Spotify (the app used Spotlistr); always previews and asks first | 3 |
| `add_tracks(playlist, tracks)` / `remove_tracks(playlist, tracks)` | (new) edit existing playlists, with preview; never delete playlists | 3 |
| `export_tracks(tracks or group, format)` | the "Artist - Song" text for Spotlistr, track URLs, CSV | 3 |

### From the backlog ([TODO.md](TODO.md))

| Tool | What | Phase |
|---|---|---|
| `find_superseded_versions` | popularity-0 album versions and their current version, offer to swap | backlog |
| `original_years` | original release year (MusicBrainz by ISRC), compilations flagged, stats on original years | backlog |
| `sequence_tracks(curve)` | order a playlist by an energy/tempo curve (planned for the archived agent) | backlog |

## Not replicated

- **Home and session handling**: URL params (`?playlists=`, `?sid=`), session cookies, the
  7-day MySQL session. The local cache does this job.
- **Enrich page UI** (progress screen, rotating tips): progress goes through the MCP progress
  notifications.
- **Contact page, analytics, sidebar navigation, global CSS, dev collection**: app plumbing.
- **Demo Collection**: already removed in the original.
- **Kaggle fallback DB**: dropped (licence).
- **Pre-trained models** (`models/*.pkl`, `ml/` notebooks): trained on the removed Demo
  Collection; My Clusters imports the KMeans path but clusters each collection on the fly.

## Phases with this list

1. ✅ Skeleton, overview, `get_playlist_tracks`.
2. Analysis without audio, in two parts:
   - **2a.** `fill_genres`, `show_playlist`, `show_artist`, `show_album`, `compare_playlists`,
     `find_tracks`, `find_duplicates`, `genre_families`, `glossary`.
   - **2b.** `group_tracks` (category and genre modes), `show_draft`, `edit_draft`,
     `organize_progress` / `reset_progress`.
3. Write: `create_playlist`, `add_tracks`, `remove_tracks`, `export_tracks`.
4. Audio: `fetch_audio_features`, `show_audio_dna`, KMeans mode, audio sections of every view,
   audio filters in `find_tracks`.
5. Launch.
