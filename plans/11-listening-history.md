# 11. Listening history

TODO.md, "Later". Top tracks and artists and recently played, crossed with the collection.
After the launch. No original (the Streamlit app never asked for these scopes).

## Step 0 (before any design is trusted): do the endpoints work in Development Mode?

In February 2026 Spotify tightened Development Mode and restricted endpoints. With Facu, on his
app, after a login that includes the two new scopes:

- `GET /v1/me/top/tracks?time_range=short_term|medium_term|long_term&limit=50&offset=0`: does it
  answer, and how deep does `offset` go (`total`)?
- `GET /v1/me/top/artists` (same parameters).
- `GET /v1/me/player/recently-played?limit=50`: it only ever returns the last 50 plays; confirm.

Write the results in PLAN.md "What we measured". If the endpoints are refused: stop, record it
in TODO.md, the item is dropped.

## Confirm with Facu first

- A new login is needed (two more scopes); existing users are asked to reconnect only when they
  first use this tool.
- Honest naming: Spotify gives no play counts. "Saved but never played" (the TODO's words)
  cannot be known; what can be said is "saved, and in none of your top lists". The tool uses
  that wording.

## Design

### Scopes and login (`settings.py`, `sources/spotify_auth.py`)

Add `user-top-read` and `user-read-recently-played` to `settings.SCOPES`. The saved token
records the scopes it was granted (`token["scope"]`). A helper `spotify_auth.has_scopes(names)`;
the new tool, when they are missing, answers: "Listening history needs a new permission. Ask the
user to reconnect: connect_spotify(reconnect=true)" and `connect_spotify` gets that argument
(start a login even when a token exists). Nothing else asks for a re-login: the old token keeps
working for everything else. Test: an old token without the scopes still loads playlists.

### Client (`sources/spotify_api.py`)

`top_tracks(time_range)`, `top_artists(time_range)` (paged with `offset` as far as step 0
showed), `recently_played()`.

### Store

```sql
CREATE TABLE IF NOT EXISTS listening (        -- top lists and recent plays; refreshed, never archived
    kind       TEXT NOT NULL,                 -- top_tracks_short / _medium / _long, top_artists_..., recent
    rank       INTEGER NOT NULL,
    item_id    TEXT NOT NULL,                 -- track or artist ID
    name       TEXT, artist TEXT, played_at TEXT,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (kind, rank)
);
```

Refreshed when older than 24 hours or on `refresh=true`; `store.forget` already deletes it (same
file). Rows are replaced on refresh, never accumulated (Spotify terms: no indefinite storage).

### Tool `listening_history(view="summary", time_range="medium", limit=50, refresh=False)`

`time_range`: "short" (about 4 weeks), "medium" (6 months), "long" (years).

- `summary`: top 10 tracks and artists of the range, how many of the top tracks are in the loaded
  playlists, the playlist that holds the most of them.
- `top_tracks` / `top_artists`: the list, each row with "in playlists: A, B" or "in no loaded
  playlist".
- `not_saved`: top tracks (all three ranges) that are in no loaded playlist nor Liked Songs:
  candidates to add (`add_tracks`).
- `not_in_top`: per playlist, how many of its tracks are in none of the top lists, and for one
  playlist (`playlist=`) the listing. Says clearly what it is and is not.
- `playlists_ranked`: playlists by the share of their tracks that appear in the top lists ("the
  playlist you actually listen to").
- `recent`: the last 50 plays with time and whether each is saved.

Each view ends with a `Chart:` hint. Demo and import modes: "needs a connected library".

Also: a `queries.Filters.in_top: bool | None` field (tracks in any top list), so `find_tracks`
and `collection_stats` can cross it with everything else; two facts in `facts.py` ("your top
artist right now is not your most-saved artist", "N of your top 50 are in no playlist"); a menu
line; a glossary term "top lists" (what Spotify's ranges mean, no play counts).

## Steps

Step 0; scopes and reconnect; client; store; the tool; filter, facts, menu, glossary; docs
(README scopes list and privacy, TOOLS.md).

## Verification

Both suites (fake client). Real run with Facu after he reconnects: each view once; check an old
token path on a copy of `token.json` without the new scopes.
