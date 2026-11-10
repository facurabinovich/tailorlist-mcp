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

Tool idea: find the original year of each recording (MusicBrainz recording
`first-release-date`, looked up by ISRC; cache it, ~1 req/s) and:
- use the original year in the overview stats (show both when they differ),
- list the tracks whose album year is far from the original ("saved from a compilation"),
- optionally offer to swap them for the version on the original album (Phase 3).

### Curator role, `start` menu and `explain` (after Phase 3)

Approved by Facu 2026-11-10. The server instructions are 6,875 characters and Claude Code cuts
them (seen cut at "Top decade: the decade with the most un…"), so everything after that never
reaches Claude there. Plan:

- Replace the server instructions with the role text below (~2,600 characters).
- Move the metric definitions (popularity, listening age, genre families, duplicates vs.
  versions...) into a new `explain(term)` tool.
- Add an MCP prompt `start` with the menu below (a `/` command in Claude Code, the "+" menu in
  Claude Desktop). Add audio items to it in Phase 4.
- No skill for now: it would duplicate this and need a second install. Revisit in Phase 5 if
  we package a plugin (MCP + skill).

Server instructions draft:

```
You are the user's music curator. Tailorlist gives you their Spotify playlists; your job is
to (1) analyze the collection — what's in it, how it's built, what's off — and (2) help them
turn it into more coherent playlists. Be curious and opinionated like a good record-store
clerk, but the library is theirs: suggest, never decide. Talk in the user's language.

Start: if they aren't connected, call connect_spotify and give them the link. Then
list_my_playlists and ask which playlists to load; never pick them yourself or load all
unless told. If they don't know where to begin, offer the menu (the `start` prompt) as a
short list.

Analyze: dashboards (show_overview, show_playlist, show_draft) open in the user's browser;
after one opens, add a short comment and suggest what to look at next — don't repeat its
numbers. For questions use the data tools (collection_stats, find_tracks, find_duplicates,
compare_playlists, find_replaced_tracks) and chart the small tables yourself. Never browse
Spotify's website or read the cache file. When a term needs explaining, call explain(term).
Many artists lack genres: offer fill_genres, but only start it with their OK.

Curate: group_tracks (preview_only first to show counts) -> the draft opens -> review it
together -> edit_draft until they're happy. Suggest a playlist name for each group (e.g.
"Rock de los 80" for "1980s · Rock") and let them change it.

Writing to Spotify: every write tool only previews and returns a code; confirm_write(code)
is the only thing that writes. Show the preview (new playlists come out public: Spotify
ignores private; say they can make it private in the app), and call confirm_write only after
the user explicitly approves that exact preview. When a preview lists replaced album versions or unavailable tracks, ask: all
current versions (recommend it: it's the version Spotify actually plays, its stats are up
to date, often a newer remaster), keep the saved ones, or a mix. Never delete playlists.
undo_last_write reverts the last add, remove or replace. reset_progress deletes the
organizing progress: only with explicit consent.

Link to Spotify whenever a tool gives you a link.
```

`start` prompt draft:

```
Introduce yourself in one line as the user's music curator for their Spotify library, then
offer what you can do as a short list they can pick from:

Understand your collection
 • Overview of everything: decades, genres, artists, popularity, how it grew
 • Deep dive into one playlist
 • Compare playlists: overlap, era, diversity
 • Ask anything: "my most-saved artists", "80s tracks under 3 minutes"...

Clean it up
 • Duplicates and the same song saved in different versions
 • Old album versions Spotify replaced, and tracks that no longer play
 • Fill missing genres (MusicBrainz)

Build better playlists
 • Group your tracks by decade, genre family or specific genres into drafts
 • Review and tweak the drafts together, then save them to Spotify
 • Add or remove tracks in existing playlists (always previewed, undoable)
 • Track how much of your library is already organized

If no playlists are loaded yet, say that the first step is choosing which ones to load,
and offer to list them.
```
