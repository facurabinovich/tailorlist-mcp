# 15. Tidal "step 0": can a personal app read and write playlists?

TODO.md, "After launch: other platforms", plan step 2. Half a day, with Facu. Nothing before the
launch. It is a spike: a throwaway script and a table of results, like the Spotify step 0 in
PLAN.md ("Own Spotify app in Development Mode").

## What Facu needs

A Tidal account (check whether a free account can register an app and whether reading or
writing playlists needs a paid plan: that is one of the answers this spike gives) and an app
registered at developer.tidal.com with a redirect URI on `http://127.0.0.1:<port>/callback`.

## Read first

The current Tidal developer docs: the authorization guide (PKCE, scopes), the API reference
(v2, JSON:API style; the OpenAPI document), the access tiers and the rate limits. TODO.md notes
what was not confirmed on 2026-11-15: "whether a personal Tidal app gets the THIRD_PARTY access
tier its playlist endpoints ask for, and its rate limits".

## The script (`scripts/tidal_step0.py`, not shipped in the wheel)

Standalone, `requests` only, token cached in a temp file outside the repo. It asks before each
write and prints every status and the first 500 characters of each answer:

1. PKCE login with scopes for reading and writing playlists (the docs' current names;
   `playlists.read`, `playlists.write` on 2026-11-15) and the user's collection.
2. Who am I, and the country.
3. List my playlists (count, paging).
4. Read one full playlist: every item, in order, with paging; which track fields come back
   (title, artists, album, duration, **ISRC**, popularity, release date, explicit, a link).
5. Look up a track by ISRC (take 10 ISRCs from Facu's Spotify cache: `SELECT isrc FROM tracks
   LIMIT 10` on a copy): how many resolve, and to how many candidates each.
6. Search by title and artist (the fallback when there is no ISRC match).
7. Create a playlist "test-tailorlist-tidal" (is private honoured?).
8. Add 10 exact tracks in order; add at a position; is the order kept? How many per call?
9. Reorder one item; remove one item (by ID or by position? what happens with a duplicate?).
10. Is there anything like Spotify's `snapshot_id` (an ETag, a `lastModified`)?
11. Rate limits: the headers on each answer; 30 quick reads to see when it throttles.
12. Artist genres or any genre field at all.

## Result

A section in PLAN.md "Own Tidal app (step 0, <date>)" with the same kind of table, plus:
which tier the app got and what it took; whether a free account works; what is missing compared
with Spotify (popularity, genres, relinking, added dates).

Verdict line for TODO.md: go (to plan 16) or no-go with the reason. Facu deletes the test
playlist by hand.
