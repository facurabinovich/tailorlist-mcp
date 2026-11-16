# 06. `sequence_tracks`: order a playlist by an energy or tempo curve

Phase 4. Needs 02 (features in the cache). Can run in parallel with 04 and 05 (this plan owns
`write.py` and one new tool function in `server.py`).

There is no original: it was planned for the archived agent (tag `agent-archive` in
`spotify-streamlit`; look at its planned tool list for the wording, nothing was built). The
machinery is `sort_playlist`'s (`write.py`: `sort_playlist`, `plan_moves`, ops `set` and `move`,
their undo in `_inverse`).

## Confirm with Facu first

Two targets: an existing playlist (a previewed write, like `sort_playlist`) and a draft group (it
reorders the group inside the draft, nothing is written; the playlist is then created in that
order). The second is the common case: "build me a running playlist that ramps up".

## Design

```python
sequence_tracks(curve: str, playlist: str | None = None, draft: str | None = None,
                group: str | None = None, feature: str = "energy", waves: int | None = None)
```

- `feature`: "energy" (default), "tempo", "valence" or "danceability".
- `curve`:
  - "rise": lowest to highest.
  - "fall": highest to lowest.
  - "peak": builds up to a high point about 70% of the way in, then comes down (a party, a run
    with a cool-down).
  - "valley": starts high, dips in the middle, ends high.
  - "wave": alternates high and low stretches (intervals); `waves` = number of crests, default
    one per 12 tracks, at least 2.

### The ordering (pure function, `sequencing.py`, new)

`order(values: list[float], curve, waves=None) -> list[int]` (indexes into `values`, in play order):

1. Target shape `f(t)` for `t = i / (n - 1)`: rise `t`; fall `1 - t`; peak `t / 0.7` up to 0.7 then
   `(1 - t) / 0.3`; valley `abs(2t - 1)`; wave `0.5 - 0.5 * cos(2π * waves * t)`.
2. Rank the positions by their target (ties: earlier position first) and the tracks by their
   value (ties: current order). The track with the i-th lowest value goes to the position with
   the i-th lowest target. This is deterministic and keeps the real values on the chosen shape.

Tracks without the feature keep their relative order and go at the end. Fewer than half of the
tracks with the feature, or fewer than 4: refuse, and say `fetch_audio_features` when none has.

### Playlist target (`write.sequence_tracks`)

Exactly `sort_playlist`'s shape: `_live_playlist`, build `src` and `dst`, "nothing to do" when
equal, one `set` op up to 100 items without duplicates, else `plan_moves`; `_with_code`. Feature
values come from the cache (`audio_features`) by track ID; a track in the playlist but not in the
cache has no value. Use plan kind "sort" unless `confirm` / `_describe_done` / the undo text need
their own wording: grep for `"sort"` in `write.py` and add "sequence" wherever it is matched
(the undo docstring in `server.py` lists the kinds too). Refuses Liked Songs and the demo like the
other write tools (`_write_tool` does the demo part).

Preview text: what and where, the curve in words, how many items move, how many Spotify calls,
"every track keeps its added date", how many tracks have no feature and go last, then the first
30 rows (# | Track | Artist | <Feature>) and a text sparkline of the whole order (one character
per track or per bucket of tracks when over 60, from `▁▂▃▄▅▆▇█`). End with a `Chart:` hint: "line
chart of <feature> by position".

### Draft target (`drafts.py`)

`drafts.sequence_group(conn, d, group, feature, curve, waves) -> str`: reorders `g["tracks"]`,
`save_draft`, returns the same listing and sparkline with "Draft dN updated; nothing was written
to Spotify. write_draft creates the playlist in this order." First verify that `write_draft` and
`create_playlist(draft, group)` write the tracks in the group's stored order (read
`write._preview_create`; the draft page sorts its card by popularity, which is only a view). If
they reorder, fix that so the stored order is what gets written, with a test.

### Server tool

One `async` tool. With `playlist`: `_write_tool(...)`. With `draft` (+ `group`; a draft with one
group needs no `group`): open the cache and call `drafts.sequence_group`, no Spotify client.
Both or neither given: ToolError. Docstring starts with "PREVIEW ONLY for a playlist ..." in the
style of `sort_playlist` and names the curves in one line each.

Menu: extend the line "Add, remove, sort or de-duplicate tracks in existing playlists" with
"or order them by an energy curve" and add `sequence_tracks` to its tools.

## Steps

1. `sequencing.py` and `tests/test_sequencing.py`: each curve on 10 known values (assert the
   exact order), ties, missing values last, `waves`.
2. `write.sequence_tracks` + tests in `tests/test_write_edit.py` with the fake Spotify used for
   `sort_playlist` (short playlist = one `set`; over 100 or with a duplicate = moves; undo
   restores the order; nothing to do).
3. `drafts.sequence_group` + tests; the order-on-write check.
4. Server tool, menu, docs (TOOLS.md backlog row done, README tool list, `undo_last_write` text).

## Verification

Both suites. Real run on the scratch playlist `test-tailorlist-scratch` (ask Facu first if it
doesn't exist): preview and confirm a "peak" by energy, read the playlist back and compare the
order, undo, compare again; check the added dates did not change. And one draft group sequenced
on the copy of the cache.

## Out of scope

Smoothing transitions between neighbours (key or tempo matching). Sequencing while creating a
playlist from loose track IDs.
