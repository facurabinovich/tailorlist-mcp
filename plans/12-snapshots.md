# 12. Snapshots: `diff_playlist` and an undo deeper than one level

TODO.md, "Later". After the launch. Edits `write.py`: don't run in parallel with 06 or 13.

Today `undo_last_write` inverts the ops of the last confirmed write only, and is refused when
the playlist changed since. A snapshot is the playlist's full ordered content saved just before
each write, so any earlier state can be compared and restored.

## Confirm with Facu first

Expiry: a snapshot lives 30 days and each playlist keeps its last 10 (Spotify Developer Terms
IV.3: no indefinite storage; the cache is not an archive). Proposed numbers.

## Design

### Store

```sql
CREATE TABLE IF NOT EXISTS snapshots (         -- a playlist's content just before a confirmed write; expires
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id  TEXT NOT NULL,
    playlist_name TEXT,
    taken_at     INTEGER NOT NULL,
    reason       TEXT NOT NULL,                -- "before sort (code ABCD)", "manual"
    op_id        TEXT,                         -- the write it precedes
    content_hash TEXT NOT NULL,
    items        TEXT NOT NULL                 -- JSON [[track_id, added_at], ...] in order
);
```

`snapshots.take(conn, playlist_id, name, rows, reason, op_id, now)` skips the insert when the
newest snapshot of that playlist has the same `content_hash`; `snapshots.expire(conn, now)`
(older than 30 days, or beyond the 10 newest per playlist) runs on every `take`. Constants
`KEEP_DAYS = 30`, `KEEP_PER_PLAYLIST = 10`. `unload_playlists` does not delete snapshots (the
playlist still exists on Spotify); `forget_my_data` does (same file).

### Taking them (`write.py`)

In `confirm`, for edits (`_confirm_edit`): the live rows are already read for the "changed since
the preview" check (`_check_unchanged`, `live_rows`); pass them to `snapshots.take` before the
first Spotify call, one per playlist the plan touches. Created playlists get none (nothing
existed). No extra Spotify calls: verify that by counting calls in the fake client in a test.

### Tools

- `playlist_history(playlist)`: the snapshots of a playlist (ID like "s12", when, why, tracks)
  and the confirmed writes since (`writes` table). Text only.
- `diff_playlist(playlist, snapshot=None, against="now")`: what changed between a snapshot
  (default: the newest) and the playlist now (read live when connected, else the cache; say
  which): added, removed, and moved tracks (moved = same track, different position after removing
  the added and removed ones; report the count and the first 30). Two snapshots can be compared
  with `against="s9"`.
- `restore_snapshot(playlist, snapshot)`: PREVIEW ONLY, through `_write_tool`, returns a code for
  `confirm_write`. Plan: when the snapshot has at most 100 items, no duplicates, and every item
  is a track: one `set` op (tracks that stay keep their added date; re-added ones get today's:
  say which). Otherwise ops in this order: `remove` what the snapshot doesn't have, `insert` what
  is missing at its snapshot position, then `move` (`plan_moves`) to the snapshot's order. The
  preview lists what comes back, what leaves, how many calls, and the added-date caveat. The
  restore is itself a write: it takes its own snapshot first and `undo_last_write` can revert it.

`undo_last_write` stays as it is (fast path for the last write). Its refusal message when the
playlist changed since points to `playlist_history` and `restore_snapshot`.

Menu: "See what changed in a playlist and go back to an earlier state" (three tools). Glossary:
"snapshot". README "Writing is safe": one bullet, with the 30 days.

## Steps

1. Store and `snapshots.py` with expiry. Tests (dedupe by hash, expiry by age and by count).
2. Taking them in `confirm`. Tests with the fake Spotify of `tests/test_write.py`.
3. `playlist_history`, `diff_playlist`. Tests.
4. `restore_snapshot` for the short case, then the long case. Tests: restore after add, after
   remove, after sort, after dedupe, with a duplicate in the snapshot, over 100 tracks; undo of a
   restore.
5. Docs.

## Verification

Both suites. Real run on `test-tailorlist-scratch`: add, sort, remove (three snapshots), diff
against the first, restore it, read the playlist back and compare order and content; check the
real cache's `snapshots` table only has scratch rows.
