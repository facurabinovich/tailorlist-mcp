# TODO

Backlog of ideas that aren't scheduled in a phase yet. Phases live in [PLAN.md](PLAN.md).

## Backlog

### Superseded versions (popularity 0)

Spotify gives popularity 0 to album versions that were replaced by a newer release of the same
album (e.g. "One Headlight" from *Bringing Down The Horse*, "Luka" from *Solitude Standing*).
195 of 2,923 tracks in Facu's collection have popularity 0 (2026-11-09).

Tool idea: list those tracks, find the version Spotify currently serves (same ISRC or same name
and artist, popularity > 0) and offer to swap them in the playlist (with preview, Phase 3 write
tools).

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

### Different versions: ignore "Remastered" suffixes

Duplicate detection follows the original app: same name and artist under different IDs. So
"Come Together" and "Come Together - Remastered 2009" don't match. Normalizing suffixes like
"- Remastered 2009", "- Mono Version", "- 2022 Stereo Mix" would catch more versions.
