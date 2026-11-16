# Tailorlist MCP

> Status: phases 1 to 3 work: reading your playlists, analysis, dashboards, grouping tracks into
> draft playlists, and writing to Spotify. Not available yet: audio features from ReccoBeats and
> grouping by sound (phase 4), and a packaged install (phase 5). Install is from source.

An MCP server that lets Claude read, analyze and reorganize **your own** Spotify library:
find duplicates across playlists, group thousands of tracks by genre family (grouping by sound
is planned, not available yet), compare playlists, and write the result back to Spotify as
playlists with the exact tracks.

## Why

Claude's official Spotify connector has five tools: `search` (up to 20 results),
`generate_playlist` (from a text prompt only), `save_to_library`, `remove_from_library` and
`get_currently_playing`. It can't read the full contents of a playlist, and it can't create a
playlist from an exact list of tracks. Reorganizing a collection you already have needs both.

## How it works

- Runs locally (stdio) and works with Claude Desktop and Claude Code.
- Uses **your own Spotify app** (free to register at developer.spotify.com, needs Spotify
  Premium). Your app, your token, your machine: nothing goes through a third-party server.
- Caches your collection in a local SQLite file. Claude only sees summaries and IDs, so a
  5,000-track library doesn't flood the conversation.
- Genres come from Spotify, filled in from MusicBrainz where Spotify has none. Release years
  can be dated by the original release, also from MusicBrainz.
- Planned (phase 4, not available yet): audio features from ReccoBeats, and grouping by sound.
- It previews every change before writing, and never deletes a playlist.

## Setup (for now, from source)

1. Install: `uv venv && uv pip install -e .`
2. Add it to Claude Code: `claude mcp add -s user tailorlist -- /path/to/.venv/bin/tailorlist-mcp`.
   For Claude Desktop, add it under `mcpServers` in `claude_desktop_config.json`.
3. Ask Claude to connect to Spotify (or run `.venv/bin/tailorlist-mcp setup` in a terminal). The
   first time, a setup page opens in your browser and walks you through registering your own
   Spotify app (free, about 3 minutes, the account needs Spotify Premium): create the app at
   <https://developer.spotify.com/dashboard>, add `http://127.0.0.1:8888/callback` as its
   Redirect URI, tick "Web API", and paste the app's Client ID in the page. No client secret
   and no file to edit. Then approve access in Spotify.

The Client ID is saved in `~/.tailorlist-mcp/.env`; you can also set `SPOTIFY_CLIENT_ID` (and
`SPOTIFY_REDIRECT_URI`) in the MCP server config, see `.env.example`. An app in Development
Mode allows up to 5 people: someone who already has an app can add you under its "User
Management", and then you only need their Client ID.

## Tools

Available now, grouped by what they do:

- **Setup and data:** `load_demo` (a demo collection of invented artists and songs, to try
  everything without a Spotify account), `connect_spotify`, `list_my_playlists`, `load_playlists` (playlists and
  your Liked Songs), `unload_playlists`, `forget_my_data`
- **Dashboards:** `show_overview`, `show_playlist`, `show_draft`
- **Questions (data tools):** `what_can_i_do` (the menu, plus what is worth doing in your
  library right now), `library_health` (everything that is off, in one report),
  `get_playlist_tracks`, `collection_stats`, `find_tracks`, `find_duplicates`, `find_orphans`
  (Liked Songs in no playlist, with a suggested playlist for each), `find_replaced_tracks`,
  `compare_playlists`, `fun_facts` (ready-made curiosities), `tour` (a guided story of the
  collection in charts; also a prompt), `explain`
- **Building playlists (drafts):** `group_tracks`, `assign_tracks` (groups by mood or occasion:
  Claude places the tracks in batches), `merge_playlists`, `split_playlist`,
  `edit_draft`, `organize_progress`,
  `reset_progress`, `export_tracks`
- **Writing to Spotify:** `create_playlist`, `write_draft`, `add_tracks`, `remove_tracks`,
  `replace_versions`, `sort_playlist`, `dedupe_playlist`, `undo_last_write`, `confirm_write`
- **Enrichment:** `fill_genres` (MusicBrainz genres for artists Spotify leaves empty, in the
  background, about 1 request per second), `fill_years` (original release year per song, also
  from MusicBrainz, about 1.6 seconds per song), `set_year` and `set_genre` (fix a year or an
  artist's genres by hand; they win over MusicBrainz and Spotify)

The dashboards open in your browser (`~/.tailorlist-mcp/overview.html`; set
`TAILORLIST_OPEN_BROWSER=0` to turn that off) and are also an MCP App, which Claude Desktop can
show inside the chat. Everything is stored in `~/.tailorlist-mcp` (`TAILORLIST_HOME` to change it).

### Writing is safe

- Every write tool only previews. It returns a plan and a code, and changes nothing on Spotify.
- `confirm_write(code)` is the only tool that writes. In your MCP client, keep `confirm_write`
  on "ask every time".
- `undo_last_write` reverts the last add, remove, replace, sort or dedupe. It also works as a preview first:
  `confirm_write` applies it.
- Playlists are never deleted by Tailorlist.
- Playlists created through the API come out public, because Spotify ignores the private flag.
  You can make them private in the Spotify app.

## Spotify's terms

Spotify's Developer Terms forbid using Spotify content "to train a machine learning or AI model
or otherwise ingest Spotify Content into a machine learning or AI model". This tool trains
nothing, but it does show your track and artist names to Claude, and Spotify hasn't said
whether that counts. You use it with your own Spotify app, under your own developer account,
for personal use. Read the [Developer Terms](https://developer.spotify.com/terms) and decide
for yourself.

## Origin

The analysis code (`src/tailorlist_mcp/analysis/`) is ported from the Tailorlist Streamlit app.

## License

MIT
[![M8ven Score](https://m8ven.ai/badge/mcp/facurabinovich-tailorlist-mcp-1v786t?v=3fc03b8f62e25d56ee89a00fd80e8434)](https://m8ven.ai/mcp/facurabinovich-tailorlist-mcp-1v786t?s=readme)
