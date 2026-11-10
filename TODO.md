# TODO

Backlog of ideas that aren't scheduled in a phase yet. Phases live in [PLAN.md](PLAN.md).

## Backlog

### Superseded versions (popularity 0)

Spotify gives popularity 0 to album versions that were replaced by a newer release of the same
album (e.g. "One Headlight" from *Bringing Down The Horse*, "Luka" from *Solitude Standing*).
195 of 2,923 tracks in Facu's collection have popularity 0 (2026-11-09).

Done 2026-11-10 (read-only part): load_playlists asks Spotify with the user's market, which
relinks a replaced track to the version it plays now (188 of 195; the other 7 aren't playable
in Argentina). Stats use the current version's popularity; `find_replaced_tracks` lists both
kinds, with search alternatives for the unavailable ones.

Done 2026-11-10 (Phase 3): `replace_versions` swaps the saved IDs for the current versions in the same positions, with preview, confirmation and `undo_last_write`.

### Original release year (compilations and reissues skew the years)

Spotify's `release_date` is the album's date. Remasters of the original album keep the original
date (*Abbey Road (Remastered 2009)* is 1969-09-26, fine), but compilations and later editions
don't: "Hey Jude", "Day Tripper", "I Feel Fine" saved from *1 (Remastered 2015)* count as
**2000**; *Love* as 2006, *Yellow Submarine Songtrack* as 1999, *Past Masters* as 1988. That
moves the decade chart, the median era and the listening age.

Plan agreed 2026-11-10, measured on Facu's library:
- Looking up by ISRC alone fails where it matters: MusicBrainz files new remasters/remixes as
  separate recordings, so "Hey Jude - Remastered 2015" gets 2015. Spotify's own search is weak
  too (Beatles "Super Deluxe" 2026 editions, Dinah Washington missing).
- MusicBrainz recording search by title (version suffixes and "feat." removed) + primary
  artist, earliest first-release-date among the matching recordings (titles compared on letters
  and digits only; recordings marked live/demo/remix/instrumental skipped), ISRC as fallback:
  Hey Jude 1968, Day Tripper 1965, Baby You're a Rich Man 1967, Rapper's Delight 1979. 25 random
  tracks: 22 same as the album year, 4 corrected, 1 not found. Some stay late (Dinah Washington
  1983, really 1959).
- Safety rule: the original year can only move a track EARLIER than its album year.
- One lookup per (primary artist, song), cached in its own table; "not found" retried after
  30 days. ~1.6 s per song at MusicBrainz's 1 request/s: about 80 minutes for 2,923 tracks the
  first time, in the background like fill_genres.
- Decisions: (1) one year everywhere: the original when known, else the album's (overview,
  collection_stats, find_tracks, group_tracks by decade, show_playlist); find_tracks shows
  "Album year" when it differs and sorts by the gap. (2) Live, demo and acoustic tracks keep
  their album year (they are other recordings). (3) Swapping a track for the version on the
  original album: later, after seeing how many tracks come from compilations.
- Tool: `fill_years()`, background, offered by Claude, started only with the user's OK.

Done 2026-11-10: `fill_years`, years.py, the original year in dashboard.records, `find_tracks`
"Album year" column and sort "year_gap". Left: swapping tracks for the original album's version.

### Doubtful years: let Claude check them on the web (after a full fill_years run)

Idea from Facu 2026-11-10. MusicBrainz still leaves some songs late (Aaron Neville "Tell It Like
It Is": 1987, really 1966). Claude (web search in Claude Desktop / claude.ai) could check the
doubtful ones, a batch of 10-20 at a time, at the user's request, never the whole library:
- `set_year(track, year, source)`: a manual correction that wins over MusicBrainz, stored with
  its source (e.g. "1966, Wikipedia"); the user's own corrections go through it too.
- A way to list the doubtful ones (e.g. a `find_tracks` filter): tracks on compilation-looking
  albums ("Best Of", "Greatest Hits", "Collection"...) still at their album year or not found,
  and songs MusicBrainz only answered through the ISRC fallback.
- Flow: Claude shows "current year -> year per source (link)" and saves with set_year after the
  user's OK.
First run fill_years on the whole library and count the doubtful ones: ~30 justifies the flow,
~5 only needs set_year.

### Curator role, `start` menu and `explain`

Done 2026-11-10. The server instructions are now the curator role (2,169 characters, down from
6,875 that Claude Code cut off), the metric definitions moved to `explain(term)`
(`glossary.py`), and the `start` MCP prompt lists what Tailorlist can do. No skill for now:
revisit in Phase 5 if we package a plugin (MCP + skill). Add audio items to `start` in Phase 4.
