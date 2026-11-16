# 14. Last.fm genres (optional)

TODO.md, "Later": "optional, needs a per-user API key, noisier tags". After the launch. Small.

## Step 0: is it worth it?

On a copy of Facu's cache after a full `fill_genres`: how many primary artists still have no
genres (`health.genres_not_found`) and how many tracks they cover. With a Last.fm key of
Facu's (free, last.fm/api/account/create), ask `artist.getTopTags` for 30 of them and read the
answers: how many get usable genre tags. If fewer than a third do, stop and record it.

Read Last.fm's API terms of service before building (attribution, rate, storage) and put what
they require in PLAN.md "Terms and licences".

## Confirm with Facu first

A third source and a key the user must create: only worth it if step 0 shows a real gain.

## Design

- Key: `LASTFM_API_KEY` from the environment or `~/.tailorlist-mcp/.env` (`settings._setting`).
  Without it nothing changes anywhere; `fill_genres` adds one line: "N artists still have no
  genres. With a free Last.fm key (LASTFM_API_KEY) Tailorlist can try Last.fm for them."
- Client `sources/lastfm.py`: `GET https://ws.audioscrobbler.com/2.0/?method=artist.gettoptags
  &artist=<name>&autocorrect=1&api_key=<key>&format=json`; throttle 4 requests per second at
  most (use 0.5 s between calls); errors like the MusicBrainz client.
- Cleaning the tags (the "noisier" part): keep tags with `count >= 30` (Last.fm scales the top
  tag to 100), at most 5, lower-cased, and only tags that are genres: either a tag that already
  exists among the Spotify or MusicBrainz genres in the cache, or one that falls in a genre
  family (`genres.assign_family(tag) != ["Other"]`). That drops "seen live", "favorites",
  "british", "female vocalists", decades and the artist's own name.
- Store: columns `lastfm_genres`, `lastfm_checked_at` on `artists` (`_ADDED_COLUMNS`).
  Precedence in `store.genres_with_source`: manual > Spotify > MusicBrainz > Last.fm (the one
  place it lives).
- Job: `LastfmFill(BackgroundFill)` for primary artists with no manual, Spotify or MusicBrainz
  genres whose MusicBrainz lookup already ran. `fill_genres` starts it after the MusicBrainz job
  has nothing pending and a key is set; its text reports both sources.
- The overview's genre source note and `set_genre`'s result name Last.fm where they name sources.

## Steps

Step 0; client and cleaning with tests (fixtures of real answers saved from step 0, trimmed);
store and precedence; the job and `fill_genres` text; docs (README optional setup, `.env.example`).

## Verification

Both suites; a real run on the copy with Facu's key: before and after counts of artists without
genres, and 20 results read by hand for wrong genres.
