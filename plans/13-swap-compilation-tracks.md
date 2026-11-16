# 13. Swap compilation tracks for the original album's version

TODO.md, "Later" (left over from `fill_years`, decision 3 of 2026-11-10: "later, after seeing
how many tracks come from compilations"). After the launch. Edits `write.py`: not in parallel
with 12.

## Step 0: how many? (read-only, on a copy of Facu's cache)

Count and list, per playlist:

- tracks dated earlier than their album (`find_tracks(sort="year_gap")`: 724 after the full
  `fill_years` run of 2026-11-16);
- of those, the ones whose album looks like a compilation (`years.COMPILATION_PATTERN`) against
  the ones on a reissue or deluxe edition of the original album (same album, later date: these
  should NOT be swapped, the user has the right album);
- the size of the gap (a 1-year gap is usually a single released before the album: not a swap
  candidate).

Report the numbers to Facu. He decides whether the feature is worth building. Proposed scope if
yes: only compilation albums (by pattern, or Spotify's `album_type == "compilation"` when the
search result gives it) with a gap of 2 years or more.

## Design (if step 0 says go)

### Finding the original (`originals.py`, new)

For a candidate track, search Spotify: `track:"<base title>" artist:"<primary artist>"`, limit 10
(`SpotifyClient.search_tracks`; note from the relink work: search with `market=from_token` gives
403 with our scopes, so search without market and check playability with
`client.tracks(ids, market="from_token")`, as `queries._alternatives` does). Keep hits where:

- the primary artist is the same and the title matches on letters and digits
  (`queries._loose`, `duplicates.base_title`);
- it is not a live, demo or acoustic take (`duplicates.other_performance`), unless the saved one is;
- the album's `album_type` is "album" or "single", its name doesn't match the compilation
  pattern, and its release year is within 1 year of the song's original year (`original_years`);
- it is playable in the user's country.

Pick the hit whose album year is closest to the original year, then `album_type == "album"` over
"single", then the highest popularity. No hit: "no original album version found". Cache the
answer per track in a table `original_versions (track_id PRIMARY KEY, original_id, album, year,
popularity, url, checked_at)`, retry "none found" after 30 days; `_prune` covers it. One search
per track and one playability call per batch; cap a call at 50 tracks searched, like
`queries.MAX_SEARCHED`, and say how many are left.

### Tools

- `find_original_versions(playlists=None, limit=50)`: read-only listing: Track | Artist | Saved
  from (album, year) | Original (album, year) | Popularity saved -> original | links. Counts at
  the top: candidates, found, none found, not searched yet.
- `swap_to_originals(playlist=None, tracks=None, keep_saved=None)`: PREVIEW ONLY, through
  `_write_tool`. Builds the same `replace` op as `replace_versions` (pairs of position, old ID,
  new ID; undo already works for it). Without `tracks` it takes every found swap of the playlist
  (or of all editable loaded playlists, as one operation); `keep_saved` leaves some out. The
  preview states: the added date of swapped tracks becomes today; a track whose original is
  already in the playlist is removed instead of duplicated (list them). Factor the shared part
  out of `write.replace_versions` instead of copying it.

Claude asks the user before swapping ("all", "these", "none"); the docstring says so. Health:
`library_health` gets a line "Saved from compilations, original album available: N".

## Steps

Step 0 and Facu's decision; `originals.py` with a fake client; the table; the listing tool; the
write tool (refactor of `replace_versions` first, tests green before adding); menu, glossary
("original album version"), docs.

## Verification

Both suites. Real run: `find_original_versions` on two playlists of the copy (read-only, real
searches): read 20 results by hand and count wrong matches (wrong recording, re-recording,
"Taylor's Version"-style remakes); report the error rate before any write. One swap and its undo
on `test-tailorlist-scratch`.
