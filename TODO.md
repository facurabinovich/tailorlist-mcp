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

Left for Phase 3: offer to swap the saved IDs for the current versions (with preview).

### Original release year (compilations and reissues skew the years)

Spotify's `release_date` is the album's date. Remasters of the original album keep the original
date (*Abbey Road (Remastered 2009)* is 1969-09-26, fine), but compilations and later editions
don't: "Hey Jude", "Day Tripper", "I Feel Fine" saved from *1 (Remastered 2015)* count as
**2000**; *Love* as 2006, *Yellow Submarine Songtrack* as 1999, *Past Masters* as 1988. That
moves the decade chart, the median era and the listening age.

Tool idea: find the original year of each recording (MusicBrainz recording
`first-release-date`, looked up by ISRC; cache it, ~1 req/s) and:
- use the original year in the overview stats (show both when they differ),
- list the tracks whose album year is far from the original ("saved from a compilation"),
- optionally offer to swap them for the version on the original album (Phase 3).
