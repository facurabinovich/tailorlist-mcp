# TODO

Everything that is pending, in the order we plan to do it. Phases and their measurements live in
[PLAN.md](PLAN.md); the tool list in [TOOLS.md](TOOLS.md). Each item still needs its own plan
and Facu's OK before it is built, unless it is listed under "In progress".

Contents: [In progress](#in-progress) · [Next](#next) · [Phase 4: audio](#phase-4-audio) ·
[Phase 5: launch](#phase-5-launch) · [Later](#later) ·
[After launch: other platforms](#after-launch-other-platforms) · [Done](#done)

## In progress

Nothing running.

## Next

- **Run `fill_years` on the whole library** (about 75 minutes, 112 of 2,811 songs dated on
  2026-11-10) and count the doubtful years: that decides the next item.
- **Doubtful years: check them on the web with Claude** (the tools are done, see Done). After the
  full `fill_years` run: count the doubtful ones and try the flow on a batch of 10-20 in Claude
  Desktop (current year -> year per source, `set_year` after the user's OK).

## Phase 4: audio

Needs its own plan (check the original app first: Enrich page, `audio_dna.py`, `clustering.py`,
already ported and not wired to any tool).

- `fetch_audio_features(playlists?)`: ReccoBeats in the background, with progress, retry of
  failed tracks and coverage stats.
- KMeans mode in `group_tracks` (auto k by elbow/silhouette or manual k, auto-named groups).
- Audio metrics in `collection_stats` and `find_tracks`; audio sections in `show_overview` and
  `show_playlist`; audio items in the `start` prompt.
- `sequence_tracks(curve)`: order a playlist by an energy/tempo curve.
- **`scikit-learn` as an optional extra** (`tailorlist-mcp[audio]`): it is installed today and
  nothing uses it until this phase; the base install should stay light.

## Phase 5: launch

- **Onboarding friction** (raised by Facu 2026-11-15; proposal, not approved yet). Registering a
  Spotify app is the biggest barrier and can't be avoided for the full product (a shared app
  needs Extended Quota: an organization with ~250k monthly users; Development Mode is 5 users
  per app and the owner needs Premium). Reading a playlist from its link without the user's own
  app is not possible through the API, and scraping the web player breaks the terms. A ladder:
  0. Demo mode (below): no account at all.
  1. Import mode: analyze a file or a pasted list (Spotify's own "Download your data" export,
     a CSV from an export tool, "Artist - Song" text). Names only, so: duplicates and versions,
     top artists, MusicBrainz genres and years, decades, drafts, text export. No popularity,
     replaced versions or writing. Every result ends with one line saying what connecting adds.
     To check first: the real fields of the Spotify export (Facu's own export).
  2. Connected: everything. The guided setup is done (see Done).
  Also: one registered user can add up to 5 people to their app (README, "use a friend's app").

- **Release**: version is 0.0.1 with no tags or changelog; `uvx` install needs a PyPI release.
- README with the Spotify app walkthrough, Claude Desktop extension (`.mcpb`) if feasible,
  2-minute demo video, post.
- **Demo mode.** A sample collection to try the tools without creating a Spotify app (the
  biggest entry friction). The data must be synthetic, for the same reason the Kaggle DB was
  dropped.
- **Plugin with a skill.** Package the MCP with a skill that guides the curation flow.
- **MCP Apps**: retest in Claude Desktop (they did not render from local stdio servers on
  2026-11-09).

## Later

- **Listening history.** Top tracks/artists and recently played, crossed with the collection
  ("saved but never played"). Needs `user-top-read` and `user-read-recently-played` (a new login).
- **Snapshots.** Save a playlist's state before each write: `diff_playlist` and an undo deeper
  than one level. Mind the Spotify "no indefinite storage" clause (expire them).
- **Swap compilation tracks for the original album's version** (left over from `fill_years`),
  after seeing how many tracks come from compilations.
- **Last.fm genres** (optional, needs a per-user API key, noisier tags).

## After launch: other platforms

Researched 2026-11-15. The analysis and curation are nearly independent of Spotify; what changes
per platform is reading and writing playlists. It is also a hedge: in February 2026 Spotify
tightened Development Mode (Premium required, 5 users per app, restricted endpoints).

| Platform | Playlist read/write API | Fits "bring your own app" | Verdict |
|---|---|---|---|
| Tidal | Official, PKCE, scopes `playlists.read` / `playlists.write` | Yes, same model as today | Best second platform |
| YouTube Music | No official API. The YouTube Data API charges 50 units per added track out of 10,000 a day (~200 tracks/day) | Only with `ytmusicapi` (unofficial, browser cookies) | Experimental extra at most; fragile |
| Apple Music | Official, creates playlists and adds tracks | No: every user would need the Apple Developer Program (US$ 99/year per secondary sources) | Not viable with this model |
| Deezer | Exists, but the portal takes no new apps since at least February 2025 | No | Dropped for now |
| Navidrome / Subsonic | Open, `createPlaylist` | Yes, no terms to mind | Small niche, but the open-source MCP crowd |

Not confirmed: whether a personal Tidal app gets the "THIRD_PARTY" access tier its playlist
endpoints ask for, and its rate limits; whether `ytmusicapi` OAuth still works (reports say only
cookies do). Amazon Music and Tencent were not looked at.

What gets harder: popularity, artist genres, replaced versions and ReccoBeats hang on Spotify
IDs (MusicBrainz genres and years are portable: name and ISRC); the preview / undo / "changed
since the preview" logic is built on Spotify's exact behaviour and needs a real check per
platform; every platform doubles the fake client and the real runs.

Plan:
1. Nothing before the launch: finish phases 4 and 5 on Spotify only.
2. A Tidal "step 0" (half a day): can a personal app read and write playlists?
3. If it passes: Tidal as a second source, plus **transfer between platforms** by ISRC
   ("organize on Spotify, write to Tidal").
4. Navidrome third, if there is interest.

## Done

### Wave 4 (2026-11-16)

- **`assign_tracks(draft, assignments, criteria, filters)`** (`semantic.py`): semantic grouping.
  Claude places tracks in groups by free criteria ("for running", "Sunday morning"), 50 at a
  time, into a normal draft (`mode="semantic"`, label "By Claude: <criteria>"); tracks it doesn't
  recognise stay unplaced instead of being guessed; max 1,500 tracks per draft. Doesn't depend on
  ReccoBeats, so it is also the plan B for the audio mode.
- **`fun_facts(playlists, limit)`** (`facts.py`): up to 14 curiosities, each with its numbers, a
  chart hint and the call that digs deeper; a fact is left out when its data is thin.
- **`tour`** (`tour.py`): a guided story of the collection in 8 chapters, as a prompt and as a
  tool (hosts hide prompts). A test checks that every tool call in the script is registered.
- Checked: tests on Python 3.12 and 3.10, plus `fun_facts` (1.5 s) and an `assign_tracks` round
  on a copy of Facu's cache. Not checked yet: a real tour and a real semantic grouping in Claude
  Desktop.

### Unified design (2026-11-15)

Asked by Facu: one look for everything the MCP produces, like the original app. `ui/theme.css`
(tokens and shared components) and `theme.py` (the head, brand header and footer every page
gets; `simple_page` for one-card pages); the three dashboards, the setup page and the login
result pages all go through it. Plus Jakarta Sans is embedded (`ui/fonts/`, OFL), so pages load
nothing from the network. Chart rules for Claude live in `explain("chart style")` (the server
instructions point to it) with a six-colour series palette checked for colour-blind separation
on the dark surfaces. `DESIGN.md` has the tokens, the page skeleton and the checklist for new
pages. Checked: tests (no raw colours outside theme.css, every page complete) and screenshots of
the six pages with sample data. To check after a server restart: that Claude Code doesn't cut the
instructions (2,387 characters) and that Claude follows the chart style.

### Wave 3 (2026-11-15)

- **`merge_playlists(playlists, name)` / `split_playlist(playlist, by)`** (`merge_split.py`): both
  only build a draft; `write_draft` creates the playlists. Real run: split preview of one
  playlist (439 tracks, 6 decade groups).
- **`find_orphans(playlists, limit)`** (`orphans.py`): Liked Songs in no loaded playlist, with the
  user's playlist that fits best (share of the playlist in the same genre family + share in the
  same decade, at least 0.5 of 2). Real run: Facu's Liked Songs holds 1 track (already in a
  playlist), so the suggestions were only exercised by the tests.
- **`set_year` / `set_genre`** (`corrections.py`): manual corrections that win over MusicBrainz,
  Spotify and the album year; `fill_years` never overwrites them. Live, demo and acoustic takes
  get a per-recording year (`tracks.manual_year`). `original_years.source` records how each year
  was found ("search", "isrc", "manual").
- **Doubtful years list**: `find_tracks(filters={"doubtful_year": true})` with a "Why" column
  (compilation album / ISRC only); `library_health` uses the same definition. Real run before the
  full fill: 158 tracks.
- **`what_can_i_do()`** (`menu.py`): the menu (single source, also renders the `start` prompt)
  plus up to 3 suggestions from the cache. A test fails when a tool is missing from the menu.
  Real run: 1.1 s on 2,923 tracks.

### Guided setup (2026-11-15)

`connect_spotify` without a configured app starts a local setup page on the redirect URI's port
(`setup_guide.py`, `sources/spotify_setup.py`, `ui/setup.html`): the steps, a copy button for the
redirect URI, a box for the Client ID that saves it to `.env` and goes straight to the Spotify
login on the same server. The tool returns the same steps as text for Claude to walk the user
through, and takes the Client ID from the chat too (`connect_spotify(client_id=...)`);
`tailorlist-mcp setup` does it from a terminal. Checked: 30 tests, the page served and opened
from a temporary home. Not checked yet: a full first-time pass against Spotify (paste a new
Client ID, approve, land back on the page), and how the page looks.

### Visual analysis, layers 1-3 (2026-11-15)

Asked by Facu: the analysis should be visual and fun, with many charts, in any host.
`charts.py`: `collection_stats`, `compare_playlists` and `find_tracks` end with a one-line
`Chart:` hint (what to draw from that table); `collection_stats` without a split has a text
`bar` column for hosts that can't draw; the server instructions say to chart every table of 3+
rows, one chart per answer. To check after a server restart: that Claude Code doesn't cut the
instructions (2,353 characters).

### Wave 2 (2026-11-15)

- **`sort_playlist(playlist, by, descending)`** and **`dedupe_playlist(playlist, kind, keep,
  versions_rule)`**, with preview, `confirm_write` and undo. Real run on a scratch playlist:
  sort by name (one replace call), its undo, a sort with a duplicate (reorder path, 3 moves), a
  block move of two items, a dedupe (one replace call, the earliest date stays) and its undo
  (both copies back), all exact and with every added date kept. Open: `most_popular`
  ranks by the live popularity of the saved ID, so a replaced album version (popularity 0) loses.
- Liked Songs no longer counts as an exact duplicate unless it is named in `playlists`
  (overview, `find_duplicates`, `library_health`); "gold", "platinum" and "singles" left the
  compilation pattern.

### Wave 1 (2026-11-15)

- **README and CI**: status line and grouped tool list brought up to date, "Writing is safe"
  section; `.github/workflows/tests.yml` runs pytest on Python 3.10 and 3.12.
- **Liked Songs as a source**: the pseudo-playlist `liked` in `list_my_playlists` /
  `load_playlists` (`"liked"`, `"Liked Songs"` or its link). Spotify gives it no snapshot, so
  "<total>:<newest added date>" stands in. Read-only: the write tools refuse it and the
  whole-library `replace_versions` skips it; it can feed new playlists. Real run: listed and
  loaded, unchanged on the second load.
- **`library_health`** (`health.py`): ten checks, cache only. Real run on Facu's library
  (15 playlists, 2,923 tracks): 0.3 s, 21 lines. Open: with a playlist subset the genre and year
  checks stay library-wide (the report says so).

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
"Album year" column and sort "year_gap". Left: swapping tracks for the original album's version
(see Later).

### Curator role, `start` menu and `explain`

Done 2026-11-10. The server instructions are now the curator role (2,169 characters, down from
6,875 that Claude Code cut off), the metric definitions moved to `explain(term)`
(`glossary.py`), and the `start` MCP prompt lists what Tailorlist can do. No skill for now:
revisit in Phase 5 if we package a plugin (MCP + skill). Add audio items to `start` in Phase 4.
