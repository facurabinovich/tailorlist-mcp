"""find_orphans: Liked Songs that are in no other loaded playlist, each with a suggested playlist.

Cache only. An orphan is a liked track whose Spotify ID is in no other loaded playlist. The
suggestion scores each candidate playlist by how much of it shares the orphan's genre family and
release decade (both over the playlist's unique tracks), so it is a hint, not a verdict.
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from tailorlist_mcp import charts, dashboard, store
from tailorlist_mcp.analysis.duplicates import song_key
from tailorlist_mcp.analysis.genres import assign_family
from tailorlist_mcp.queries import DEFAULT_LIMIT, QueryError, _limit, _table

#: Lowest genre_share + decade_share (max 2.0) worth suggesting; below it the answer is "no clear fit".
#: 0.5 means, for example, a third of the playlist in the genre family plus a sixth in the decade.
MIN_SCORE = 0.5
MAX_SCORE = 2.0
NO_FIT = "no clear fit"


def _family(genres) -> str | None:
    """The first family of the primary artist's genres (the rule grouping uses); None without genres."""
    if not isinstance(genres, str) or not genres.strip():
        return None
    return assign_family(genres)[0]


def _shares(frame: pd.DataFrame) -> dict[str, dict]:
    """Per playlist id: share of its unique tracks in each family and in each decade, and its size."""
    out: dict[str, dict] = {}
    for pid, group in frame.drop_duplicates(["playlist_id", "track_id"]).groupby("playlist_id"):
        n = len(group)
        out[pid] = {
            "family": (group["fam"].value_counts() / n).to_dict(),
            "decade": (group["decade"].dropna().astype(int).value_counts() / n).to_dict(),
            "size": n,
        }
    return out


def _suggest(family, decade, shares: dict, names: dict[str, str]) -> tuple[str, float | None]:
    """The best-scoring playlist (ties: the larger one, then by name) or NO_FIT."""
    best: tuple[float, int, str] | None = None
    for pid, s in shares.items():
        score = (s["family"].get(family, 0.0) if family else 0.0) + (s["decade"].get(decade, 0.0) if decade is not None else 0.0)
        if best is None or (score, s["size"]) > best[:2] or ((score, s["size"]) == best[:2] and names[pid] < best[2]):
            best = (score, s["size"], names[pid])
    if best is None or best[0] < MIN_SCORE:
        return NO_FIT, None
    return best[2], best[0]


def count(recs: list[dict]) -> int:
    """How many Liked Songs are in no other loaded playlist (0 when Liked Songs isn't loaded)."""
    in_playlists = {r["id"] for r in dashboard.without_liked(recs)}
    if not in_playlists:
        return 0
    return len({r["id"] for r in recs if r["playlist_id"] == store.LIKED_ID} - in_playlists)


def find_orphans(conn: sqlite3.Connection, playlists: list[str] | None = None, limit: int | None = None) -> str:
    """Liked Songs in no other loaded playlist, with the playlist that fits each best."""
    limit = _limit(limit or DEFAULT_LIMIT)
    recs = dashboard.records(conn)
    liked = [r for r in recs if r["playlist_id"] == store.LIKED_ID]
    if not liked:
        raise QueryError('Liked Songs isn\'t loaded. Load it first with load_playlists(["liked"]).')
    others = dashboard.without_liked(recs)
    if not others:
        raise QueryError("No playlist besides Liked Songs is loaded. Load the playlists to check against "
                         "with load_playlists.")

    in_playlists = {r["id"] for r in others}
    frame = dashboard.tracks_frame(recs)
    frame["fam"] = frame["artist_genres"].map(_family)
    liked_tracks = frame[frame["playlist_id"] == store.LIKED_ID].drop_duplicates("track_id")
    orphans = liked_tracks[~liked_tracks["track_id"].isin(in_playlists)]
    if orphans.empty:
        return f"No orphans: all {len(liked_tracks):,} Liked Songs are in at least one loaded playlist."

    # Candidates: the chosen playlists, or all loaded ones; only the user's own when the cache knows who they
    # are (Liked Songs is saved with the user as owner). Collaborative playlists are not recorded, so they count as others'.
    rows = conn.execute("SELECT id, name, owner_id FROM playlists WHERE id != ?", (store.LIKED_ID,)).fetchall()
    if playlists:
        wanted = {dashboard.find_playlist(conn, ref)["id"] for ref in playlists}
        rows = [r for r in rows if r["id"] in wanted]
    me = conn.execute("SELECT owner_id FROM playlists WHERE id = ?", (store.LIKED_ID,)).fetchone()
    me = me["owner_id"] if me else None
    note = ""
    if me:
        owned = [r for r in rows if r["owner_id"] == me]
        if len(owned) < len(rows):
            note = f" {len(rows) - len(owned)} playlists made by others were left out of the suggestions."
        rows = owned
    if not rows:
        raise QueryError("No playlist of yours to suggest among (Tailorlist only adds to your own playlists).")
    names = {r["id"]: r["name"] for r in rows}
    shares = _shares(frame[frame["playlist_id"].isin(names)])

    # Another version of the same song (same song key and primary artist, other ID) in a loaded playlist.
    saved = {(song_key(r["name"]), (r["artists"] or "").casefold()) for r in others}
    other = [(song_key(n), (a or "").casefold()) in saved for n, a in zip(orphans["track_name"], orphans["artist_name"])]
    picks = [_suggest(f, None if pd.isna(d) else int(d), shares, names) for f, d in zip(orphans["fam"], orphans["decade"])]
    orphans = orphans.assign(other=other, suggested=[p[0] for p in picks], score=[p[1] for p in picks])

    counts = orphans["suggested"].value_counts()
    summary = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0] == NO_FIT, kv[0]))
    listing = orphans.assign(suggested_key=orphans["suggested"].str.casefold(),
                             artist_key=orphans["artist_name"].fillna("").str.casefold(),
                             name_key=orphans["track_name"].str.casefold())
    listing = listing.sort_values(["suggested_key", "artist_key", "name_key"], kind="stable")
    shown = listing.head(limit)

    n_other = int(orphans["other"].sum())
    out = [f"{len(orphans):,} of {len(liked_tracks):,} Liked Songs are in no loaded playlist. Only loaded playlists "
           f"count: a song may be in one you haven't loaded.{note}"]
    if n_other:
        out.append(f"Another version of the same song is already in a loaded playlist for {n_other:,} of them (marked in the listing).")
    out.append("\n" + _table(["Suggested playlist", "Orphans"], [[k, v] for k, v in summary]))
    table = []
    for t in shown.itertuples():
        year = "" if pd.isna(t.release_year) else str(int(t.release_year))
        fit = "" if t.score is None or pd.isna(t.score) else f"{round(t.score / MAX_SCORE * 100)}%"
        title = t.track_name + (" (other version in a playlist)" if t.other else "")
        table.append([title, t.artist_display, year, t.fam or "", t.suggested, fit, t.track_id])
    out.append("\n" + _table(["Track", "Artist", "Year", "Genre family", "Suggested playlist", "Fit", "Track ID"], table))
    if len(listing) > len(shown):
        out.append(f"\nShowing {len(shown)} of {len(listing)} orphans ({len(listing) - len(shown)} left out; raise `limit`, max 500).")
    hint = charts.orphans_hint(len(summary))
    if hint:
        out.append("\n" + hint)
    out.append("\nNext: add them with add_tracks(playlist, tracks) (preview, then confirm), or group them with group_tracks.")
    return "\n".join(out)
