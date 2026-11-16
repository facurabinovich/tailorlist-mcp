# 01. Doubtful years: check them on the web with Claude

TODO.md, "Next". The tools exist (`find_tracks(filters={"doubtful_year": true})`, `set_year`). What
is left is to run the flow for real on Facu's 41 doubtful tracks (26 dated by ISRC only, 15 on
compilation albums with no earlier release found) and fix whatever the run shows.

This is a session with Facu, not a coding task. It needs a host with web search and the
`tailorlist` MCP attached: Claude Desktop (what the TODO asks for) or Claude Code (`WebSearch` is
a deferred tool there). It writes to the real cache through `set_year`, which is the point.

## The flow to run

1. `find_tracks(filters={"doubtful_year": true}, limit=20)`. The table has Track, Artist, Album,
   Year, Why, Track ID.
2. For each track search the web for the song's first release (query: `"<title>" "<artist>" song
   release year`; prefer Wikipedia, Discogs, MusicBrainz's own page, the artist's site). Take the
   earliest release of that recording by that artist (a single counts; a later remaster does not;
   a cover is another song).
3. Show Facu one table: Track | Artist | Current year | Proposed year | Source (link) | Note.
   Leave a row as "keep" when the current year is right, and "unsure" when sources disagree.
4. Only for the rows Facu approves: `set_year(track=<Track ID>, year=<year>, source="<site>")`.
5. Repeat with the next 20 (the list shrinks: a manual year is no longer doubtful).
6. Finish with `find_tracks(filters={"doubtful_year": true})` and `library_health()`.

## What to watch and write down

- Does Claude follow the flow from the `set_year` docstring alone, or does it need to be told?
  (It should: batch of 10-20, table first, OK before writing.)
- Does it try to browse Spotify (the instructions forbid it)?
- How many of the 26 "ISRC only" years were right. If nearly all are, propose to Facu to stop
  flagging ISRC-only years (`years.doubtful_reason`); if many are wrong, keep it.
- How many of the 15 compilation tracks really had an earlier release that MusicBrainz missed.
- Tracks where `set_year` refused or matched the wrong song (name collisions): record the exact
  call and message.
- Time per batch.

## Likely small fixes (only if the run shows the need; propose first)

- `find_tracks` with `doubtful_year`: add the saved track's Spotify link (`include_links=true`
  already does it) to the instructions of the flow if Claude asks for it.
- A confirmed-right year stays "doubtful" forever: `set_year` with the same year marks it manual
  and removes it from the list. If that is confusing, add a line to the `set_year` docstring:
  "to confirm a year that is already right, set it to the same value".
- If Claude checks more than 20 at a time and the answers get sloppy, tighten the docstring.

Code touched, if any: `corrections.py`, `years.py`, the `set_year` docstring in `server.py`,
tests in `tests/test_corrections.py`.

## Done when

The doubtful list is empty or holds only rows Facu chose to leave; TODO.md "Next" is moved to
Done with: how many years changed, how many were already right, the ISRC-only accuracy, and any
fix made.
