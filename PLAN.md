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
- **Audio features:** ReccoBeats; the Kaggle-derived fallback DB is optional and its licence
  must be checked before shipping it.

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

1. **Skeleton.** FastMCP stdio server, OAuth with the user's app (token cached), SQLite
   collection cache. Tools: `list_my_playlists`, `load_playlists`, `collection_summary`.
   Connected to Claude Desktop and Claude Code.
2. **Analysis.** Genre families (Spotify + MusicBrainz), duplicates, playlist comparison,
   `cluster` (by genre family, by category, KMeans). Tool shapes from the archived
   `agent/tools.py` of the Streamlit app.
3. **Write.** `create_playlist(name, track_ids)`, `add_tracks`, `remove_tracks`, with preview.
4. **Audio features.** ReccoBeats, optional fallback download, Audio DNA.
5. **Launch.** README with the Spotify app walkthrough, `uvx` install, Claude Desktop
   extension if feasible, 2-minute demo video, post.

## Open questions

- Licence of the Kaggle-derived audio-feature fallback DB.
- ReccoBeats terms of use.
- Spotify developer terms about using API data with AI.
