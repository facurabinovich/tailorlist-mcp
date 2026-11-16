# 07. Import mode: analyze a file or a pasted list, no Spotify app

Phase 5, "Onboarding friction", step 1 of the ladder (demo = 0, done; connected = 2, done).
Independent of the audio plans; it touches `server.py`, `demo.py`, `write.py`, `menu.py`, so
don't run it in parallel with 02 or 06.

No original: the Streamlit app only read playlists from Spotify. The pattern to copy is the demo
(`demo.py`): a collection in the cache that is not the user's connected library, with a marker,
a badge on the pages, writes refused, no network lookups that need Spotify.

## Confirm with Facu first

1. The proposal itself is "not approved yet" in TODO.md.
2. Imported data never mixes with playlists loaded from Spotify (like the demo): importing is
   refused while real playlists are loaded, and `load_playlists` removes the imports after saying
   so. Mixing would double-count the same song under two IDs.
3. Facu's own Spotify export, to check the real fields ("To check first" in TODO.md). The shapes
   below are from public descriptions of the export, not from a file we have seen: the parser is
   tolerant and step 1 is to compare with his file when he has it. The export takes Spotify up to
   several days to deliver; without it, build on the fixtures and mark the format "not verified
   against a real export" in TODO.md.

## What can be imported

`import_playlists(path: str | None = None, text: str | None = None, name: str | None = None)`

- `path`: a file or a folder on the user's computer (the server is local, it can read it).
  - Spotify's "Download your data" export: a folder or zip holding `Playlist1.json` (and
    `Playlist2.json`...) shaped `{"playlists": [{"name", "lastModifiedDate", "items": [{"track":
    {"trackName", "artistName", "albumName", "trackUri"}, "episode", "localTrack", "addedDate"}]}]}`,
    and `YourLibrary.json` shaped `{"tracks": [{"artist", "album", "track", "uri"}]}` (the Liked
    Songs). Items whose `track` is null (episodes, local files) are counted as skipped.
  - A CSV from an export tool. Detect the columns by name, case-insensitively, with these
    aliases: track = "Track Name" / "Track name" / "title" / "name"; artist = "Artist Name(s)" /
    "Artist name" / "artist"; album = "Album Name" / "Album"; uri = "Track URI" / "Spotify - id" /
    "uri"; isrc = "ISRC"; added = "Added At"; release = "Release Date" / "Album Release Date";
    duration = "Duration (ms)"; popularity = "Popularity"; playlist = "Playlist name" (one file
    with several playlists). One file without a playlist column is one playlist named after the
    file. (Exportify and TuneMyMusic are the two formats to cover; get a sample of each from
    their sites' documentation or ask Facu to export one playlist.)
  - A text file with one "Artist - Song" per line.
- `text`: the same "Artist - Song" lines pasted in the chat (the separator is " - "; lines
  without it are skipped and counted; cap 2,000 lines). `name` names that playlist (default
  "Imported list").

Path safety: resolve the path, accept only `.json`, `.csv`, `.txt`, `.zip` or a folder of them,
cap each file at 50 MB, never follow a path given inside the file.

## How it is stored (`imports.py`, new)

As ordinary rows, so every analysis tool works unchanged:

- Playlists: id `import-<10 hex of sha1(name + source file)>`, `owner_id = "import"`,
  `owner_name = "Imported"`, `snapshot_id = "import"`, `url = None`.
- Tracks: when the row has a Spotify track URI or ID, use the real ID and
  `https://open.spotify.com/track/<id>` as the link. Otherwise a synthetic ID: `"imp"` + 19
  characters of base62 from sha1 of the casefolded "artist|title|album" (22 characters, so the
  ID regexes accept it). Fields not in the source stay `NULL` (popularity, duration, release
  date...).
- Artists: synthetic ID `"impa"` + 18 base62 characters from the casefolded name; several artists
  in one cell are split on ", " (Exportify writes "A, B"; the export gives one name) and the
  first is the primary. `genres = '[]'` so `fill_genres` picks them up. Check
  `MusicBrainzClient.lookup(id, name)`: with a non-Spotify ID its URL lookup must find nothing and
  fall through to the name search without an error (add a guard if it queries MusicBrainz with
  the fake ID).
- Use `store.save_playlist` with rows shaped like `store.parse_item`'s output.

`imports.is_import(conn)` like `demo.is_demo` (any playlist with `owner_id = "import"`), and
`imports.clear(conn)`. Put the shared question in one place: `collection_kind(conn) -> "demo" |
"import" | "spotify" | None`, and make the existing demo checks go through it where both matter.

## What changes around it

- **Never mixed**: `import_playlists` refuses when real or demo playlists are loaded ("unload
  them first" / "the demo is removed first": remove the demo silently like `load_playlists` does,
  refuse over real ones). `load_playlists` clears imports (report it in its result, like
  `cleared_demo`). `load_demo` refuses over imports.
- **Writes**: `write.refuse_demo` becomes `refuse_offline`: "This is an imported list: connect
  your Spotify (connect_spotify) to write playlists. export_tracks gives the list as text."
- **Tools that need Spotify data**: `find_replaced_tracks` and `replace_versions` answer that they
  need a connected library. `find_orphans` works if `YourLibrary.json` was imported: store it as
  the playlist with id `store.LIKED_ID`? No: keep it as an import playlist named "Liked Songs"
  and leave `find_orphans` saying it needs Liked Songs loaded from Spotify. Popularity-based
  outputs already tolerate missing popularity (`ov.has_popularity`); run every data tool on an
  import with no popularity and no dates and fix what breaks (expect: `facts.py`,
  `health.py`, sorting by popularity, `show_playlist` highlights).
- **Years and genres**: `fill_genres` and `fill_years` run on imports (they need only names;
  `years.dating` already uses MusicBrainz's year when the album has none). The import result
  offers both: without them an import has no genres and often no decades.
- **Badge**: the pages' "Demo data" badge element gets the text from the data (`data.badge`):
  "Demo data" or "Imported data". `_dashboard_result` sets it.
- **One line on what connecting adds**, at the end of every result while an import is loaded:
  `imports.NOTE = "Imported list: names only. Connecting Spotify adds popularity, replaced
  versions, Liked Songs and writing playlists back (connect_spotify)."` Add it in `_answer`,
  `_dashboard_result` and the draft tools (where `demo.status` is added today), not in each tool.
- **Menu and instructions**: `menu.MENU` "Get started": "Analyze an exported file or a pasted
  list, no Spotify app needed" (`import_playlists`); `status_line` for imports. In
  `server.INSTRUCTIONS` change the last-but-one line to `No Spotify app, or just trying it: offer
  load_demo, or import_playlists for their own exported file or pasted list.` and keep the total
  under the limit (README rule 9).

## Steps

1. Parsers (pure functions: bytes or text in, `[(playlist name, [rows])]` out) with fixture files
   in `tests/fixtures/import/` for each format, including broken rows, a BOM, semicolon CSVs,
   quoted commas, empty lines, episodes in the export. `tests/test_imports.py`.
2. Storing, `is_import`, `clear`, `collection_kind`, the never-mixed rules. Tests.
3. The tool, write refusal, note, badge, menu, instructions.
4. Run every tool on an import with only names (see "Tools that need Spotify data") and fix.
5. Docs: README ("Three ways to try it": demo, import, connect; how to request the Spotify export:
   Account > Privacy > Download your data), TOOLS.md, TODO.md.

## Verification

Both suites. A real run on a temp home: export one of Facu's playlists to text with
`export_tracks` from his cache copy, import that text into the empty temp cache, run
`library_health`, `collection_stats(group_by="artist")`, `fill_genres` for a minute (real
MusicBrainz), `show_overview` (screenshot with the badge), `group_tracks`, `export_tracks`, and
check a write tool refuses. When Facu's export arrives: import it and list every field the parser
did not expect.

## Out of scope

Matching imported names to Spotify IDs (needs the app: that is "connected"). Apple Music or
YouTube exports.
