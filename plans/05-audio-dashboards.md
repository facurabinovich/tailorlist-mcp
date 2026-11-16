# 05. Audio sections in `show_overview` and `show_playlist`

Phase 4. Needs 02 and 03 (the ported helpers in `analysis/playlist_detail.py`). Can run in
parallel with 04 and 06 (this plan owns `dashboard.py`, `ui/overview.html`, `ui/playlist.html`).

Original code it follows: `pages/2_Playlist_Detail.py` section 5 "Audio DNA radar" (lines
~320-375: playlist against the collection, `pd_core.radar_profile`), the mood map (lines
~510-590: valence on x, energy on y, `pd_core.mood_map_points`), "similar playlists" (lines
~685-720: `similar_playlists`, `with_similarity_pct`); `pages/5_Audio_DNA.py` for the collection
(mood quadrants, average profile); `pages/4_Playlist_Comparison.py` lines ~315 for the audio
leaders. Check `pages/1_Overview.py` for any audio tile before designing the overview section
(none was found by grep on 2026-11-16; TOOLS.md still lists "audio averages" for it).

## Confirm with Facu first

The playlist page shows the playlist against the collection as paired horizontal bars, not as a
radar chart: bars are easier to read, work at 400 px and match the rest of the pages. (Design
judgment is delegated; this line is here so he knows.)

## Design

Read DESIGN.md. Every section below is hidden when the data has no features, replaced by one
`.notice`: "No audio features yet. Ask Claude to fetch them (fetch_audio_features)." On the
overview only; the playlist page just omits the sections.

Every section states its coverage in a caption ("2,650 of 2,923 tracks").

### Overview (`dashboard.overview_data`, `ui/overview.html`)

New key `"audio"` (or `None`):

```json
{"with_audio": 2650, "total": 2923,
 "means": [{"feature": "Energy", "value": 0.64}, ...seven 0-1 features in RADAR_FEATURES order],
 "tempo": 121.4, "loudness": -8.2,
 "moods": [{"name": "Happy", "tracks": 900, "percent": 34.0}, ...],
 "description": "Songs that are mid energy, electric, ..."}
```

from `audio_dna.feature_means`, `mood_summary` and `verbose_description` over the deduplicated
frame. One section "The sound of it", placed after the genres: a two-column grid. Left card:
the bar list of the seven means (0 to 1 scale, value at the end), two small tiles for average
tempo (BPM) and loudness (dB), and the sentence. Right card: "Mood": one stacked horizontal bar
of the four quadrants in `--series-1..4`, with a legend below (name, share, count) and a
one-line explanation of each quadrant in `--dim`.

`model_summary` gains one line: "Sound: energy 0.64, valence 0.48, 121 BPM; moods: Happy 34%,
Turbulent 28%, Peaceful 12%, Dark 26% (2,650 of 2,923 tracks have audio features)." or, with
none, the hint to offer `fetch_audio_features` (same wording as the genres and years hints).

### Playlist (`dashboard.playlist_data`, `ui/playlist.html`)

New key `"audio"` (or `None`), built with the helpers 03 ported:

```json
{"with_audio": 410, "total": 439,
 "profile": [{"feature": "Energy", "playlist": 0.71, "collection": 0.64}, ...],
 "tempo": {"playlist": 126.0, "collection": 121.4},
 "extremes": {"most_energetic": card, "calmest": card, "happiest": card, "darkest": card,
              "fastest": card, "slowest": card},
 "mood_points": [{"x": 0.31, "y": 0.82, "name": "...", "artist": "..."}],
 "moods": [...as in the overview...],
 "similar": [{"name": "Alt7", "percent": 93}, ...up to 5]}
```

- `profile`: `playlist_audio_averages` on the whole frame, `compare_audio(af_avg, name)`.
- `extremes`: highest and lowest energy, valence and tempo, as highlight cards (`_hl_card`, value
  formatted with `audio_dna.format_extreme_value`).
- `mood_points`: `mood_map_points(pl_df)`; the tool result carries none (count only), the web
  page all (cap 2,000).
- `similar`: `similar_playlists` + `with_similarity_pct`; needs at least 2 playlists with features.

Sections, after "Top genres": 

1. "Sound": paired bars per feature (playlist in the accent, a thin marker or a second muted
   bar for the collection, legend "this playlist / your collection"), the tempo pair as a tile.
2. "Mood map": an inline SVG scatter, valence left to right, energy bottom to top, a cross at
   0.5 / 0.5, the four quadrant names in the corners in `--dim`, circles in the accent at 0.6
   opacity with a `<title>` tooltip; beside it the quadrant shares. Hide it under 5 points.
3. "Standouts by sound": the six extremes as `.hl` cards.
4. "Playlists that sound like this one": a `.rank` list with the similarity percent and a
   `.meter`.

`playlist_summary` gains one line with the profile differences that stand out ("More energetic
than your collection (0.71 vs 0.64), slower (112 vs 121 BPM). Sounds most like Alt7 (93%).").

### Sample data and screenshots

Add audio to the sample builders in `.claude/design-review/build_samples.py` (untracked; if it is
missing, build samples from the demo collection with `demo.load` on a temp cache, which has
features after 02). Screenshot the two pages at desktop width and about 400 px, with features
and without. Check `tests/test_theme.py` (no raw colours; pages complete) and
`tests/test_dashboard.py`.

## Steps

1. `overview_data` + `model_summary`, tests (with features, without, partial coverage).
2. `ui/overview.html` section, screenshots.
3. `playlist_data` + `playlist_summary`, tests.
4. `ui/playlist.html` sections, screenshots.
5. Docs: TOOLS.md (`show_overview` no longer "audio averages ⏳4", `show_playlist` audio
   sections), the two tool docstrings in `server.py` (one clause each), DESIGN.md (the scatter
   and paired-bar components, in two lines).

## Verification

Both suites. On the copy of Facu's cache: write the two pages (`browser.write_page` through the
tools with `TAILORLIST_OPEN_BROWSER=0`), screenshot them, and show Facu the four images. Check
the tool result's size did not jump (the structured content must stay small: no `mood_points`).

## Out of scope

The Audio DNA page as its own dashboard (TOOLS.md: data tools instead; `audio_profile` from 03).
Feature histograms and the correlation heatmap in pages.
