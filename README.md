# Tailorlist MCP

> Work in progress. Phase 1 works: log in, list playlists, load them, summarize.

An MCP server that lets Claude read, analyze and reorganize **your own** Spotify library:
find duplicates across playlists, group thousands of tracks by genre family or by sound,
compare playlists, and write the result back to Spotify as playlists with the exact tracks.

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
- Genres come from Spotify, filled in from MusicBrainz where Spotify has none. Audio features
  come from ReccoBeats.
- It previews every change before writing, and never deletes a playlist.

## Setup (for now, from source)

1. Create an app at <https://developer.spotify.com/dashboard> (needs Spotify Premium). Add
   `http://127.0.0.1:8888/callback` as a Redirect URI and tick "Web API".
2. Install: `uv venv && uv pip install -e .`
3. Put your app's Client ID in `~/.tailorlist-mcp/.env` (see `.env.example`). No client
   secret needed.
4. Log in once: `.venv/bin/tailorlist-mcp login` (or ask Claude to call `connect_spotify`).
5. Add it to Claude Code: `claude mcp add -s user tailorlist -- /path/to/.venv/bin/tailorlist-mcp`.
   For Claude Desktop, add it under `mcpServers` in `claude_desktop_config.json`.

Tools so far: `connect_spotify`, `list_my_playlists`, `load_playlists`, `collection_summary`,
`forget_my_data`. Everything is stored in `~/.tailorlist-mcp` (`TAILORLIST_HOME` to change it).

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
