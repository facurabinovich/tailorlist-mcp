# 16. Tidal: transfer first, then Tidal as a second source

TODO.md, "After launch: other platforms", plan step 3. Only if plan 15 passed. This is an outline
with the architecture decisions; the exact calls depend on what step 0 measured, so the executor
turns each phase into a detailed plan (in this folder, `16a-...`, `16b-...`) and gets Facu's OK
before coding it.

## Confirm with Facu first

Order: **transfer before source**. "Organize on Spotify, write to Tidal" needs only a Tidal login,
an ISRC lookup and playlist creation: about a third of the work, it is the feature people ask
for, and it tests the Tidal client for real before the bigger change. Tidal as a library to
analyze comes second.

## Phase A: transfer a playlist or a draft to Tidal

- `sources/tidal_auth.py` and `sources/tidal_api.py`, shaped like the Spotify pair (PKCE, token
  file `tidal_token.json` in the data dir, a client with `me`, `tracks_by_isrc`, `search_tracks`,
  `create_playlist`, `add_items`). `connect_tidal(client_id?)` with its own setup steps
  (`setup_guide.py` gets a second guide; the setup page takes a service name).
- `transfer.py`: match each Spotify track to a Tidal track: by ISRC first (several candidates:
  prefer the same album name, then the same duration within 2 seconds); then by title and primary
  artist (the same loose comparison as `queries._alternatives`), flagged "matched by name";
  else unmatched. Cache matches in `track_links (track_id, service, other_id, how, checked_at)`.
- Tool `transfer_playlist(playlist | draft + group(s), to="tidal", name?)`: PREVIEW ONLY: N of M
  matched by ISRC, K by name (listed, so the user can drop wrong ones with `skip=[...]`), the
  unmatched listed. `confirm_write(code)` creates the playlist on Tidal in order. The plan kind is
  new ("transfer"); the write log records the service. No undo (it would mean deleting a
  playlist); edits to Tidal playlists are phase B.
- The safety rules of PLAN.md "Phase 3" hold: two steps with a code, exact partial-failure
  reports, never delete.
- Check on the real API, per the TODO's warning ("the preview / undo / changed-since-preview
  logic is built on Spotify's exact behaviour"): order kept, batch size, what a failed batch
  leaves behind.

## Phase B: Tidal as a source

The change that touches everything; do it in this order, each step with the suite green:

1. **A source column, not a rewrite.** `playlists.source` ('spotify' default, 'tidal') and track
   IDs namespaced for other services (`tidal:<id>`), so a Spotify ID stays exactly as today and
   nothing in the analysis changes for Spotify users. Grep every regex that assumes a 22-character
   ID (`export._TRACK_RE`, `write._PLAYLIST_RE`, `collection._ID_RE`) and every place that builds
   a Spotify URI (`write.uri`).
2. **A small protocol** `sources/base.py` (`MusicSource`: `me`, `my_playlists`, `playlist_items`,
   `saved_tracks`, and for writing `create_playlist`, `add_items`, `remove_items`, plus capability
   flags `can_reorder`, `can_replace`, `has_popularity`, `has_artist_genres`, `has_relinking`).
   `SpotifyClient` satisfies it as is; `collection.load_playlists` and `write.py` take a source
   instead of assuming Spotify. The fake client in the tests becomes two.
3. **Reading**: `load_playlists` with Tidal playlists into the same tables through a
   `parse_item` for Tidal. What is missing per step 0 (popularity, genres) is `NULL`: genres come
   from MusicBrainz by name, years from MusicBrainz by ISRC and name, audio features from
   ReccoBeats only for tracks linked to a Spotify ID (state the coverage).
4. **Mixing rule** (decide with Facu): either one service loaded at a time (like demo and import:
   simplest, recommended first), or both with duplicates detected across services by ISRC.
5. **Writing to Tidal playlists** (add, remove, sort, dedupe, undo): each op checked against the
   real API first, as PLAN.md "Editing checks" did for Spotify; ops the service can't do are
   refused with a clear message, never emulated by delete-and-recreate.
6. Tool docstrings, instructions, menu, README stop saying "Spotify" where they mean "your
   library"; the name of the project already avoids it.

## Verification

Phase A: a real transfer of one of Facu's playlists and of a two-group draft; count matches by
hand on 30 tracks. Phase B: the full suite with both fakes; a real load of a Tidal playlist and
the overview for it.

## Not included

YouTube Music, Apple Music, Deezer (see the table in TODO.md: not viable or dropped).
