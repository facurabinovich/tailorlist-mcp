# 03. Audio in the data tools

Phase 4, second step. Needs 02 (features in `dashboard.records`). After this, 04, 05 and 06 can
run in parallel.

TOOLS.md decided: "most of it should be `collection_stats` and `find_tracks` with audio metrics
(Claude charts)". This plan does that, adds one tool for the Audio DNA page, and ports the
per-playlist audio helpers that 05 also needs.

Original code it follows: `core/audio_dna.py` (already ported to `analysis/audio_dna.py`: mood
quadrants, feature means, correlations, extremes, value formatting), `pages/5_Audio_DNA.py`,
`core/playlist_detail.py` (`playlist_audio_averages`, `add_speechiness_log`, `compare_audio`,
`normalize_tempo`, `radar_profile`, `mood_map_available`, `mood_map_points`,
`similarity_features`, `similar_playlists`, `with_similarity_pct`: these were cut from the port),
`core/comparison.py` audio leaders (ported), `core/glossary.py` (audio terms).

## Confirm with Facu first

A new data tool, `audio_profile(playlists?)`. Without it Claude needs five or six calls to
rebuild the Audio DNA page.

## Design

Rule for every audio number: it is computed over the tracks that have features, and the result
says so ("based on 2,650 of 2,923 tracks with audio features"). With no features at all, the
audio paths answer one line: "No audio features yet: fetch_audio_features gets them from
ReccoBeats (offer it to the user)."

### 1. Port the missing helpers (`analysis/playlist_detail.py`)

Copy the ten functions and their two result classes (`AudioComparison`, `RadarProfile`) and
constants (`AUDIO_FEATURE_COLUMNS`, `SIMILARITY_FEATURES`, the tempo range) back from
`spotify-streamlit/core/playlist_detail.py`, unchanged, with their tests from the original's
`tests/` if it has them (adapt the imports). 05 uses them for the playlist page.

### 2. Filters (`queries.Filters`, `apply_filters`)

`Filters` is part of the schema of several tools, so keep it compact. Three new fields:

- `audio: dict[str, list[float | None]] | None`: "Audio feature ranges, feature: [min, max], either
  may be null. Features: energy, valence, danceability, acousticness, instrumentalness, liveness,
  speechiness (0 to 1), tempo (BPM), loudness (dB). E.g. {"energy": [0.7, null], "tempo": [120, 140]}."
- `mood: str | None`: "Happy (high valence, high energy), Turbulent (low, high), Peaceful (high,
  low) or Dark (low, low); the cut is 0.5" (`audio_dna.MOOD_THRESHOLD`, a value equal to it counts
  as high).
- `has_audio: bool | None`: true = only tracks with audio features, false = only tracks without.

An unknown feature name or mood is a `QueryError` listing the options. A track without features
never passes `audio` or `mood`. `drafts.TrackSelector` inherits the fields: check `edit_draft`
still works and its docstring mentions them.

### 3. `collection_stats`

- Metrics: `avg_energy`, `avg_valence`, `avg_danceability`, `avg_acousticness`,
  `avg_instrumentalness`, `avg_liveness`, `avg_speechiness`, `avg_tempo`, `avg_loudness` (mean over
  unique tracks of the group that have the feature; two decimals, tempo and loudness one) and
  `with_audio` (unique tracks of the group that have features).
- Dimensions: `mood` (the four quadrants, in `MOOD_NAMES` order), `tempo_bin` (steps of 10 BPM,
  label "120-129", ordered), and `<feature>_bin` for the seven 0-1 features (steps of 0.1, label
  "0.7-0.8", ordered). Add them to `_ORDERED` (not `mood`), to `_label`, `_missing_note` ("no
  audio features") and to `charts._COLUMNS` so the hint says column chart.
- Keep the generated lists (`DIMENSIONS`, `METRICS`) built from `config.AUDIO_FEATURES` rather
  than typed out. Update the docstrings in `server.py` (short: "audio: avg_<feature>, with_audio;
  group_by mood, tempo_bin, <feature>_bin").

### 4. `find_tracks`

- `sort` accepts the nine feature names (descending by default; tracks without the feature go
  last and are not shown when the sort is a feature).
- When the sort is a feature, or `audio` / `mood` is in the filters, the listing gains columns:
  the features involved (sort feature first, then the filtered ones), or Energy, Valence, Tempo
  when only `mood` is used. Format with `audio_dna.format_extreme_value`. This covers the
  original's "extreme tracks" (most and least of each feature: `ascending=true`).
- `charts.tracks_hint`: a feature sort gets "optional: bar chart of the top 10 by <feature>".

### 5. `compare_playlists`

When any playlist has features: columns Energy, Valence, Dance, Acoustic, Tempo (playlist means
from `playlist_audio_averages`) and "Audio %" (coverage). After the table, the leaders line from
the ported `comparison` module (the playlist that tops each feature: the original's "Most
Energetic", "Happiest"...). No features: the table is exactly as today.

### 6. New tool `audio_profile(playlists: list[str] | None = None) -> str` (`audio.py`, new)

One text answer, built on `analysis/audio_dna.py`:

1. Coverage line.
2. Profile table: one row per feature (the seven 0-1 features, then tempo and loudness), columns
   Feature | Collection | one column per playlist (at most 8 playlists, the largest; with more, a
   note names the tool for the rest: `collection_stats(group_by="playlist",
   metrics=["avg_energy", ...])`).
3. Mood: Happy / Turbulent / Peaceful / Dark with count and share (`mood_summary`).
4. Strongest relations: `top_correlations(correlation_matrix(...), 3)` as "energy and loudness
   move together (r = 0.71)"; skip under 30 tracks.
5. One sentence in words: `clustering.verbose_description` of the collection means. Careful:
   importing `analysis/clustering.py` imports scikit-learn. Move `verbose_description` (pure
   Python) to `analysis/audio_dna.py` and re-export it from `clustering.py`, so this tool never
   needs scikit-learn.
6. `Chart:` hint: "horizontal bars of the seven 0-1 features (collection, or one colour per
   playlist when comparing up to 3); the moods as one stacked bar".

Register it in `server.py` through `_answer`.

### 7. Glossary, menu, tour, facts, chart style

- `glossary.TERMS`: port from `core/glossary.py` the definitions of energy, valence, danceability,
  acousticness, instrumentalness, liveness, speechiness, tempo, loudness, key and mode, and "mood"
  (the quadrants). Short, one entry "audio features" listing them plus one per feature is too
  many: make three entries: "audio features" (the nine, one line each), "mood" (quadrants),
  "audio coverage" (why some tracks have none). Update the `explain` docstring's list of terms.
- Chart style (`glossary._chart_style`): one line: "Audio profiles: horizontal bars of the 0-1
  features; a radar only to compare 2 or 3 profiles. Mood: one stacked bar of the four quadrants,
  in the order Happy, Turbulent, Peaceful, Dark."
- `menu.MENU`: "The sound of your collection: energy, mood, tempo, and how playlists differ"
  (`audio_profile`); extend the "Ask anything" line with an audio example ("calm songs from the
  90s"). The `start` prompt follows from `MENU` (this closes the TODO line "audio items in the
  start prompt").
- `tour.CHAPTERS`: a chapter "The sound of it" before "Your playlists compared", data
  `audio_profile()`, skipped when there are no features. Update the "8 chapters" / "6 to 8" texts
  (tour docstring, prompt description, TOOLS.md, README).
- `facts.py`: three facts, each left out under 50% coverage or 20 tracks: "your mood" (the biggest
  quadrant and its share), "fastest and slowest" (tempo extremes), "your dance floor" (the
  playlist with the highest danceability, needs 2+ playlists). `MAX_LIMIT` goes from 14 to 17;
  update the docstring and docs that say "up to 14".

## Steps

1. Port the helpers (1) and move `verbose_description` (6.5).
2. Filters (2) with tests in `tests/test_queries.py`.
3. `collection_stats` (3), `find_tracks` (4), `compare_playlists` (5), with tests.
4. `audio.py` and the tool (6), tests in `tests/test_audio.py`.
5. Glossary, chart style, menu, tour, facts (7). `tests/test_menu.py`, `test_tour.py`,
   `test_glossary.py`, `test_facts.py` must pass (they check that tools and texts agree).
6. Docs: TOOLS.md (the audio rows, the Audio section rewritten as "what covers each part of the
   Audio DNA page", like the Phase 2a table), README tool list.

Tests use a small frame with hand-made features, plus one case with no features at all and one
with partial coverage for each tool.

## Verification

Both suites. On the copy of Facu's cache that 02 filled: `audio_profile()`, `collection_stats`
by decade with `avg_energy` and `avg_valence`, `collection_stats(group_by="mood")`,
`find_tracks(sort="tempo")`, `find_tracks(filters={"mood": "Dark", "decade": 1990})`,
`compare_playlists()`. Read the outputs: do the numbers make musical sense (a known calm playlist
low on energy)? Report timings (the 2,923-track cache answers the current tools in 0.3 to 1.5 s).

## Out of scope

KMeans (04), dashboards (05), sequencing (06). Histograms on a log axis for instrumentalness and
speechiness (the bins of 0.1 are enough for Claude's charts).
