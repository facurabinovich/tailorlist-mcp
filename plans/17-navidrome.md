# 17. Navidrome / Subsonic as a third source

TODO.md, "After launch: other platforms", plan step 4: "Navidrome third, if there is interest".
Needs plan 16 phase B (the `MusicSource` protocol and the `source` column). An outline; detail it
when the time comes.

## Gate

Evidence of interest (issues or comments asking for it after the launch). Without it, don't
start. Facu has no Navidrome server: the executor runs one locally for the checks (the official
Docker image with a folder of a few tagged, freely licensed tracks), or uses the public Navidrome
demo server for read-only calls. If Docker isn't available on the machine, say so and stop
rather than building around it.

## What is different from Spotify and Tidal

- No app to register and no terms to mind: the user gives a server URL, a user name and a
  password (`NAVIDROME_URL`, `NAVIDROME_USER`, `NAVIDROME_PASSWORD` in the environment or `.env`;
  never echoed in tool results). Auth is the Subsonic token scheme: `t = md5(password + salt)`
  and `s = salt` on every request, with `v`, `c=tailorlist-mcp`, `f=json`.
- API (Subsonic, which Navidrome implements): `ping`, `getPlaylists`, `getPlaylist` (entries with
  title, artist, album, year, duration, genre, `musicBrainzId` when the files are tagged),
  `createPlaylist` (also replaces a playlist's songs when given its ID), `updatePlaylist` (add
  songs, remove by index), `search3`, `getStarred2` (the "liked" equivalent). Check the OpenSubsonic
  extensions Navidrome supports (ISRC and multiple artists arrived there).
- The data is the user's own files: the genre comes from the file tag (one per track, not per
  artist), there is no popularity, and "added" is when the file was added to the playlist only if
  the server reports it.
- Matching to other services (transfer, ReccoBeats) is by MusicBrainz recording ID or ISRC when
  the tags have them, else by title and artist.

## Outline

1. `sources/subsonic_api.py` satisfying `MusicSource`; `connect_navidrome()` checks `ping`.
2. `parse_item` for Subsonic entries; track IDs `subsonic:<id>`; the track's own genre goes where
   the analysis expects the artist's genres (decide: store per-track genres in a new column and
   have `dashboard.records` prefer them when the source has no artist genres).
3. Reading and the analysis tools on a loaded Navidrome library (popularity-based outputs already
   tolerate missing popularity after plan 07).
4. Writing: `createPlaylist` for new playlists; edits through `updatePlaylist`; each op checked
   on the local server first; preview, code, confirm, log and undo as everywhere.
5. Transfer between services through `track_links` (plan 16 phase A), in both directions where
   the target can be written.
6. README: setup for self-hosters, what is and isn't available.

## Verification

The suite with a third fake; a real load, an overview, a created playlist and an edit on the
local test server.
