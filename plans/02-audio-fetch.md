# 02. Audio: fetch features from ReccoBeats

Phase 4, first step. Everything else in the phase (03-06) reads what this stores.

Follows the original's Enrich page (`spotify-streamlit/pages/_Enrich.py`,
`app_state/enrichment.py`, `core/enrichment.py`: outcomes enriched / skipped / failed, failed is
retried, skipped is not) with one big change measured on 2026-11-16.

## Measured on 2026-11-16 (real API, no key)

`GET https://api.reccobeats.com/v1/audio-features?ids=<id>,<id>,...`

- Accepts **Spotify track IDs** directly and up to **40 per call** (45 gives 400:
  `"getAudioFeatures.ids" ... "size must be between 1 and 40"`).
- Answer: `{"content": [{"id": "<ReccoBeats uuid>", "href": "https://open.spotify.com/track/<spotify id>",
  "isrc": "...", "acousticness": 0.00119, "danceability": 0.355, "energy": 0.918,
  "instrumentalness": 0.0, "key": 1, "liveness": 0.0971, "loudness": -4.36, "mode": 1,
  "speechiness": 0.0746, "tempo": 148.114, "valence": 0.24}]}`.
- **IDs it doesn't know are simply left out** of `content` (3 asked, 1 returned, status 200). Match
  the rows back by the ID at the end of `href`.
- The docs give no rate numbers: 429 with a `Retry-After` header, "avoid spam requests".

So a library of 2,923 tracks is 74 calls, not 5,846 (the ported client makes two calls per track).
At one call per second that is under two minutes.

## Confirm with Facu first

1. No `playlists` argument and no cancel (TOOLS.md listed both): the whole cache takes about
   2 minutes, so neither earns its place. The job covers every track in the cache.
2. Track IDs are sent to ReccoBeats, a third party. The footer says "Nothing leaves it except
   calls to Spotify and MusicBrainz": it and the README must name ReccoBeats (step 8).

## Design

### Store (`store.py`)

New table in `_SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS audio_features (     -- Phase 4: ReccoBeats, one row per track asked about
    track_id         TEXT PRIMARY KEY,
    status           TEXT NOT NULL,             -- 'ok' or 'missing' (ReccoBeats doesn't have it)
    checked_at       INTEGER NOT NULL,
    via              TEXT,                      -- 'id' (the saved ID) or 'current' (the version Spotify plays now)
    energy REAL, acousticness REAL, valence REAL, danceability REAL, speechiness REAL,
    instrumentalness REAL, liveness REAL, loudness REAL, tempo REAL,
    key INTEGER, mode INTEGER
);
```

Functions: `audio_candidates(conn, now, retry_after) -> list[Row]` (tracks with no row, or
'missing' older than `retry_after`; columns `id`, `current_id`; ordered by how many playlists hold
the track, then id), `save_audio(conn, rows: list[dict], now)`, `audio_counts(conn, now,
retry_after) -> {"total", "checked", "found", "pending"}` (total = tracks in the cache).
`_prune` also deletes `audio_features` rows whose track left the cache. A failed HTTP call saves
nothing, so those tracks stay pending (the original's "failed: retried on the next run").

### Client (`sources/reccobeats.py`, rewritten)

Replace the two-step functions with a class shaped like `MusicBrainzClient`:

- `ReccoBeatsClient(session=None, throttle=None, sleep=time.sleep)`; `BATCH = 40`;
  `MIN_INTERVAL = 1.0` second between calls (reuse `musicbrainz.Throttle`, its own instance);
  User-Agent `tailorlist-mcp/<version> ( https://app.tailorlist.app )`, never an email.
- `audio_features(ids: list[str]) -> dict[str, dict]`: one call for up to 40 IDs, returns
  `{spotify_id: features}` for the ones found, using the ID parsed from `href` (regex
  `track/([A-Za-z0-9]{22})`); rows whose ID wasn't asked for are ignored.
- 429: wait `Retry-After` (default 5 s, cap 60 s) and retry, up to 5 tries; 5xx and timeouts
  (10 s) retry with backoff; then raise `ReccoBeatsError`. A 400 raises at once.
- Keep `map_audio_features`' value cleaning (floats, ints, `None` when absent; a row with every
  metric `None` counts as not found) but drop the columns ReccoBeats never fills
  (`time_signature`, `track_number`, `disc_number`, `explicit`) and name the key column `key`.
- Rewrite `tests/test_reccobeats.py` for the new client: a batch, IDs left out, 429 with
  Retry-After, a 400, giving up after 5 tries, garbage JSON.

### The job (`audio_fill.py`, new) and a small generalisation of `BackgroundFill`

`genre_fill.BackgroundFill` handles one row per step and catches `MusicBrainzError` only. Change it
without altering what `GenreFill` and `YearFill` do:

- class attribute `ERRORS = (musicbrainz.MusicBrainzError,)`, used in the `except`;
- `_weight(row) -> int` (default 1): how many items a row stands for; `processed += _weight(row)`;
- `_process` may return an int (how many were found): `hits += int(found)`.

`AudioFill(BackgroundFill)`: `JOB = "audio_fill"`, `WHAT = "batches"`, `ERRORS =
(ReccoBeatsError,)`, `SECONDS_PER_ITEM = 0.03` (per track), built with
`client_factory=ReccoBeatsClient`. `_candidates` returns the pending tracks in chunks of 40;
`_weight` is the chunk size. `_process(client, conn, chunk, now)`:

1. ask for the chunk's saved IDs;
2. for the ones left out that have a `current_id` (the version Spotify plays now, set by the
   relink check), ask once more with those IDs and store the features under the SAVED track ID
   with `via='current'`;
3. save 'ok' rows for the found ones and 'missing' for the rest; return how many were found.

`RETRY_AFTER` 30 days for 'missing', like the genres. `describe(status, coverage)` in the style of
`years.describe`: running / not running with N left / done, always with the coverage sentence
("2,650 of 2,923 tracks have audio features (91%); 273 are not in ReccoBeats") and, when done, a
small table of coverage per playlist (Playlist | Tracks | With audio | %), worst first, capped at
15 rows. `FILL = AudioFill(client_factory=...)` at module level.

### Reaching the analysis (`dashboard.py`)

`records(conn)` adds to every record the nine features plus `key` and `mode` (LEFT JOIN on
`audio_features` where `status = 'ok'`; `None` when absent), under the names the ported modules
expect: `energy`, `acousticness`, `valence`, `danceability`, `speechiness`, `instrumentalness`,
`liveness`, `loudness`, `tempo`. `tracks_frame` needs no change (extra columns pass through);
make sure the columns exist as float even when every value is missing (a frame built only from
`None` gets `object` dtype: cast them). Run the full suite: tests that compare whole record dicts
need the new keys.

### The tool (`server.py`)

```python
@mcp.tool()
def fetch_audio_features(retry_missing: bool = False) -> str:
    """Get each track's audio features (energy, mood, danceability, tempo and more) from ReccoBeats
    (free, no account): needed to analyze and group by sound. ..."""
```

Same body as `fill_years`: nothing loaded -> ToolError; demo -> `demo.FILLED_IN` (reword that
constant to cover audio); else `audio_fill.describe(audio_fill.FILL.start(conn), ...)`.
`retry_missing=True` deletes the 'missing' rows first, so they are asked again now. The docstring
says: runs in the background and returns at once, call again for progress, about 2 minutes per
3,000 tracks, the track IDs are sent to ReccoBeats, start it only if the user agrees.

Also:

- `_audio_hint(conn)` next to `_genres_hint` / `_years_hint`, added to the `load_playlists` result
  when tracks are pending ("N tracks have no audio features yet. fetch_audio_features gets them
  from ReccoBeats in the background (about X min); offer it, don't start it unasked.").
- `menu.MENU`: under "Understand your collection" add `("Get the sound of your tracks (energy,
  mood, tempo) from ReccoBeats", ("fetch_audio_features",))`. `menu.CHECKS`: an `_audio` check after
  `_years`.
- `health.library_health`: one block "Audio features not fetched" with the count and the tool.
- `server.INSTRUCTIONS` (keep it under 2,450): replace
  `Many artists lack genres: offer fill_genres; compilations and reissues date songs by the album:
  offer fill_years. Start either only with their OK.` with
  `Offer fill_genres (many artists lack genres), fill_years (compilations date songs by the album)
  and fetch_audio_features (energy, mood, tempo; needed to group by sound). Start them only with
  their OK.` and pay for it by shortening
  `(recommend it: it's the version Spotify actually plays, its stats are up to date, often a newer
  remaster)` to `(recommend it: it's what Spotify plays, often a newer remaster)`.

### Demo (`demo.py`)

The demo must have audio features so 03-06 have something to show without a network call.
Generate them when the demo is loaded, for about 90% of its tracks, with their own generator
seeded per track (`random.Random(f"{SEED}:audio:{track_id}")`) so the existing random stream, and
with it the 507 tracks the tests count, doesn't move. Values by genre family: base levels per
family (Electronic: energy 0.75, danceability 0.7, acousticness 0.1, tempo 124; Folk and
Classical: energy 0.3, acousticness 0.8, tempo 100; Hip-Hop: speechiness 0.25, danceability 0.75;
Rock: energy 0.7, tempo 125; Jazz: acousticness 0.6, instrumentalness 0.4; others in between),
each with noise and clamped to its range; versions of a song copy the original's values with a
little noise. `demo.clear` needs nothing new if `_prune` covers the table. Tests: features exist
for most demo tracks, the same on every load, and `fetch_audio_features` on the demo makes no call.

### Text and docs (step 8)

- `theme.FOOTER_NOTE`: "Runs on your computer. Nothing leaves it except calls to Spotify,
  MusicBrainz and ReccoBeats." (check `tests/test_theme.py` and the page tests).
- README: "How it works" and "Enrichment" (the tool, what is sent, about 2 minutes), status line.
- TOOLS.md: the `fetch_audio_features` row (no `playlists`, no cancel, why), mark it done.
- PLAN.md: the measurement above under "What we measured".
- `glossary.TERMS`: one entry "audio features" (what they are, where they come from, coverage);
  the per-feature definitions come in plan 03.

## Steps

1. Store: table, functions, prune. Tests in `tests/test_audio_fill.py`.
2. Client rewrite and its tests.
3. `BackgroundFill` generalisation (run `tests/test_musicbrainz.py`, `tests/test_years.py`: nothing
   may change for the two existing jobs).
4. `audio_fill.py` and tests: resume after a failed batch, 'missing' not retried before 30 days,
   the `current_id` second pass, the lock, `run_in_thread=False` with a fake client and a tmp db.
5. `records()` and the frame; run the whole suite.
6. Demo features.
7. The tool, hints, menu, health, instructions.
8. Text and docs.

## Verification

- Both test suites.
- Real run on a COPY of Facu's cache (README rule 6): start the fill, poll until done, report
  coverage (overall, per playlist), how many came through `via='current'`, the time, any 429.
  Then check the real cache was not touched.
- Tell Facu the numbers; the run on his real cache is his to start from Claude.

## Out of scope

Using the features anywhere (03-06). `scikit-learn` as an extra (04).
