"""Phase 3: write to Spotify, always in two steps.

A write tool (create_playlist, write_draft, add_tracks, remove_tracks, replace_versions,
undo_last_write) only builds a plan, stores it in `pending_writes` and returns the preview with a
single-use code (30 minutes). `confirm` is the only function that writes: it carries out exactly
the previewed plan, and aborts if the playlist or the draft changed since.

"Changed" is judged by content, not by snapshot_id: GET playlists/{id} returns a stale snapshot_id for several
seconds after a write (measured 2026-11-10), while the items endpoint is up to date at once. The playlist's content
hash (its ordered track IDs) is taken at the preview, checked at confirmation, and stored after every write for the undo.
Every Spotify call that goes through is logged in `writes`; the log feeds the undo.

Playlists are never deleted or unfollowed, and only playlists the user owns (or collaborative ones)
are edited. See PLAN.md, "Phase 3 plan".

Plan shapes (JSON in pending_writes):
  create: {"kind": "create", "draft": {"id", "stamp"} | None, "items": [{"name", "description",
           "ids": [track IDs to write, in order], "pairs": [[draft track ID, written ID]], "group", "keep_visible"}]}
  add / remove / replace / undo: {"kind", "items": [{"playlist_id", "name", "url", "content", "ops": [...]}]}
  ops: {"op": "add", "ids", "position"} | {"op": "remove", "ids", "positions": [[pos, id]]} |
       {"op": "replace", "pairs": [[pos, old, new]]} | {"op": "insert", "entries": [[pos, id]]} (undo of remove)
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from datetime import date

from tailorlist_mcp import collection, drafts, progress
from tailorlist_mcp.export import track_id_from, track_meta
from tailorlist_mcp.queries import _table
from tailorlist_mcp.sources.spotify_api import MAX_PER_CALL, SpotifyClient, SpotifyError

CODE_TTL = 30 * 60
DESCRIPTION_MAX = 300
SHOWN = 30                     # rows of a preview table before "... and N more"
_CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_PLAYLIST_RE = re.compile(r"^[A-Za-z0-9]{22}$|playlist[/:]([A-Za-z0-9]{22})")


class WriteError(ValueError):
    """The request can't be done as asked. The message says why."""


def uri(track_id: str) -> str:
    return f"spotify:track:{track_id}"


def _now(now: int | None) -> int:
    return int(time.time()) if now is None else now


# ---------------------------------------------------------------- pending plans (the codes)

def _store_plan(conn: sqlite3.Connection, plan: dict, now: int) -> str:
    with conn:
        conn.execute("DELETE FROM pending_writes WHERE expires_at < ?", (now,))
        while True:
            code = "".join(secrets.choice(_CODE_CHARS) for _ in range(6))
            if not conn.execute("SELECT 1 FROM pending_writes WHERE code = ?", (code,)).fetchone():
                break
        conn.execute("INSERT INTO pending_writes (code, created_at, expires_at, plan) VALUES (?, ?, ?, ?)",
                     (code, now, now + CODE_TTL, json.dumps(plan)))
    return code


def _claim_plan(conn: sqlite3.Connection, code: str, now: int) -> dict:
    """Take the plan of a code and burn the code (single use, even if the write then aborts)."""
    code = (code or "").strip().upper()
    with conn:
        row = conn.execute("SELECT plan, expires_at FROM pending_writes WHERE code = ?", (code,)).fetchone()
        if row:
            conn.execute("DELETE FROM pending_writes WHERE code = ?", (code,))
    if not row:
        raise WriteError(f"Code '{code}' is not valid: it was never issued, was already used or is gone. "
                         "Nothing was written. Ask for a new preview.")
    if row["expires_at"] < now:
        raise WriteError(f"Code '{code}' expired (codes last {CODE_TTL // 60} minutes). Nothing was written. "
                         "Ask for a new preview.")
    return json.loads(row["plan"])


def _with_code(conn: sqlite3.Connection, plan: dict, preview: str, now: int, what: str = "this") -> str:
    code = _store_plan(conn, plan, now)
    return (f"{preview}\n\nNOTHING HAS BEEN WRITTEN YET. Show {what} to the user and ask for their explicit OK. "
            f"Only then call confirm_write with code {code} (single use, valid {CODE_TTL // 60} minutes).")


# ---------------------------------------------------------------- the write log

def _log_start(conn, op_id: str, kind: str, pid: str | None, name: str | None, ops, now: int) -> int:
    with conn:
        return conn.execute("INSERT INTO writes (op_id, at, kind, playlist_id, playlist_name, ops, status) "
                            "VALUES (?, ?, ?, ?, ?, ?, 'running')", (op_id, now, kind, pid, name, json.dumps(ops))).lastrowid


def _log_batch(conn, row_id: int, batch: dict) -> None:
    row = conn.execute("SELECT batches FROM writes WHERE id = ?", (row_id,)).fetchone()
    batches = json.loads(row["batches"]) + [batch]
    with conn:
        conn.execute("UPDATE writes SET batches = ?, snapshot_id = ? WHERE id = ?",
                     (json.dumps(batches), batch.get("snapshot_id") or None, row_id))


def _log_end(conn, row_id: int, status: str, content: str | None = None) -> None:
    with conn:
        conn.execute("UPDATE writes SET status = ?, content_hash = ? WHERE id = ?", (status, content, row_id))


# ---------------------------------------------------------------- Spotify reads

def _playlist_ref_id(ref: str) -> str | None:
    m = _PLAYLIST_RE.search(ref.strip())
    return (m.group(1) or m.group(0)) if m else None


def resolve_playlist(client: SpotifyClient, ref: str, me: dict | None = None) -> dict:
    """A playlist of the user's account by ID, link, name or unique part of a name -> Spotify's
    playlist object. Only the user's own or collaborative playlists can be edited."""
    pid = _playlist_ref_id(ref)
    if not pid:
        wanted = ref.strip().casefold()
        mine = [p for p in client.my_playlists() if p and p.get("id")]
        exact = [p for p in mine if (p.get("name") or "").casefold() == wanted]
        part = [p for p in mine if wanted and wanted in (p.get("name") or "").casefold()]
        if exact:
            pid = exact[0]["id"]
        elif len(part) == 1:
            pid = part[0]["id"]
        elif part:
            raise WriteError(f"'{ref}' matches several of your playlists: {', '.join(p['name'] for p in part[:10])}.")
        else:
            raise WriteError(f"'{ref}' is not one of your playlists.")
    me = me or client.me()
    meta = client.playlist(pid)
    if (meta.get("owner") or {}).get("id") != me.get("id") and not meta.get("collaborative"):
        raise WriteError(f"'{meta.get('name')}' belongs to {(meta.get('owner') or {}).get('display_name') or 'someone else'} "
                         "and isn't collaborative: Tailorlist only edits your own playlists.")
    return meta


def live_rows(client: SpotifyClient, playlist_id: str) -> list[dict]:
    """Every row of the playlist as Spotify has it now, by position. id is None for episodes,
    local files and unavailable items (they take a position but can't be edited here)."""
    rows = []
    for pos, row in enumerate(client.playlist_items(playlist_id)):
        item = row.get("item") or row.get("track") or {}
        ok = item.get("type", "track") == "track" and not item.get("is_local") and item.get("id")
        rows.append({"pos": pos, "id": item["id"] if ok else None, "name": item.get("name") or "",
                     "artist": ", ".join(a.get("name") or "" for a in item.get("artists") or []),
                     "album": (item.get("album") or {}).get("name")})
    return rows


def content_hash(rows: list[dict]) -> str:
    return hashlib.sha1("|".join(str(r["id"]) for r in rows).encode()).hexdigest()


def _live_playlist(client: SpotifyClient, ref: str) -> tuple[dict, list[dict]]:
    meta = resolve_playlist(client, ref)
    return meta, live_rows(client, meta["id"])


# ---------------------------------------------------------------- tracks to write

def _metas(client: SpotifyClient, conn: sqlite3.Connection, ids: list[str]) -> dict[str, dict]:
    """Cache info for each ID; the ones the cache doesn't hold are read from Spotify (relinking too)."""
    meta = track_meta(conn, ids)
    unknown = [i for i in dict.fromkeys(ids) if i not in meta]
    if unknown:
        for asked, t in zip(unknown, client.tracks(unknown, market="from_token")):
            if not t:
                raise WriteError(f"Spotify has no track with ID {asked}.")
            artists = [a.get("name") or "" for a in t.get("artists") or []]
            moved = t.get("id") and t["id"] != asked and (t.get("linked_from") or {}).get("id") == asked
            meta[asked] = {"id": asked, "name": t.get("name") or "", "album": None if moved else (t.get("album") or {}).get("name"),
                           "year": ((t.get("album") or {}).get("release_date") or "")[:4], "url": f"https://open.spotify.com/track/{asked}",
                           "current_id": t["id"] if moved else None,
                           "current_album": (t.get("album") or {}).get("name") if moved else None,
                           "unavailable": t.get("is_playable") is False and not moved,
                           "artist_list": artists, "artist": artists[0] if artists else "", "artists": ", ".join(artists)}
    return meta


def parse_track_ids(refs: list[str]) -> list[str]:
    ids = []
    for r in refs:
        tid = track_id_from(r)
        if not tid:
            raise WriteError(f"'{r}' is not a Spotify track ID, URI or link.")
        ids.append(tid)
    return list(dict.fromkeys(ids))


def _choices(meta: dict[str, dict], ids: list[str], versions: str | None, keep_saved: set[str],
             unavailable: str | None) -> dict:
    """Apply the user's decision on replaced and unavailable tracks to one group.

    Returns pairs (draft ID, ID to write), and the tracks still waiting for a decision."""
    pairs, ask_replaced, ask_unavailable, left_out, swapped = [], [], [], [], []
    for tid in ids:
        m = meta[tid]
        if m["current_id"]:
            if versions is None:
                ask_replaced.append(m)
                continue
            if versions == "current" and tid not in keep_saved:
                pairs.append((tid, m["current_id"]))
                swapped.append(m)
                continue
        elif m["unavailable"]:
            if unavailable is None:
                ask_unavailable.append(m)
                continue
            if unavailable == "leave_out":
                left_out.append(m)
                continue
        pairs.append((tid, tid))
    return {"pairs": pairs, "ask_replaced": ask_replaced, "ask_unavailable": ask_unavailable,
            "left_out": left_out, "swapped": swapped}


def _question(group: str, c: dict) -> str:
    out = [f'Group "{group}" needs a decision before it can be written.']
    if c["ask_replaced"]:
        same = [m for m in c["ask_replaced"] if m["album"] and m["album"] == m["current_album"]]
        other = [m for m in c["ask_replaced"] if m not in same]
        out.append(f"{len(c['ask_replaced'])} tracks are album versions Spotify replaced with a newer release (it plays the current "
                   "version instead; the saved one has popularity 0).")
        if same:
            out.append(f"Same album ({len(same)}): only the release was refreshed.\n"
                       + _table(["Track", "Artist", "Album"], [[m["name"], m["artist"], m["album"]] for m in same[:SHOWN]])
                       + (f"\n... and {len(same) - SHOWN} more" if len(same) > SHOWN else ""))
        if other:
            out.append(f"Other album ({len(other)}): the current version is on a different album.\n"
                       + _table(["Track", "Artist", "Saved album", "Current album"],
                                [[m["name"], m["artist"], m["album"] or "?", m["current_album"]] for m in other[:SHOWN]])
                       + (f"\n... and {len(other) - SHOWN} more" if len(other) > SHOWN else ""))
    if c["ask_unavailable"]:
        out.append(f"{len(c['ask_unavailable'])} tracks are not playable in the user's country (no replacement).\n"
                   + _table(["Track", "Artist", "Album"], [[m["name"], m["artist"], m["album"]] for m in c["ask_unavailable"][:SHOWN]]))
    return "\n".join(out)


_ASK_HELP = ("\nNo confirmation code yet: ask the user, then call the tool again with their choice.\n"
             "- Replaced versions: versions=\"current\" (all current; recommended: it is the version Spotify actually plays, its "
             "popularity and stats are up to date and it is often a newer remaster), versions=\"saved\" (keep the saved ones), "
             "or versions=\"current\" with keep_saved=[track IDs] for current except some.\n"
             "- Tracks not playable in the user's country: unavailable=\"keep\" or unavailable=\"leave_out\".")


# ---------------------------------------------------------------- names and descriptions

def _unique_name(name: str, taken: set[str]) -> tuple[str, bool]:
    """name, or "name (2)", "name (3)"... when the account already has one (casefolded). -> (name, renamed)."""
    if name.casefold() not in taken:
        taken.add(name.casefold())
        return name, False
    n = 2
    while f"{name} ({n})".casefold() in taken:
        n += 1
    taken.add(f"{name} ({n})".casefold())
    return f"{name} ({n})", True


def make_description(day: str, origins: list[str]) -> str:
    """"Made with Tailorlist on <date> from <playlists>", cut to Spotify's 300 characters."""
    head = f"Made with Tailorlist on {day}"
    if not origins:
        return head
    text = f"{head} from {', '.join(origins)}"
    if len(text) <= DESCRIPTION_MAX:
        return text
    shown = []
    for i, o in enumerate(origins):
        rest = len(origins) - i - 1
        cand = f"{head} from {', '.join(shown + [o])}" + (f" and {rest} more" if rest else "")
        if len(cand) > DESCRIPTION_MAX:
            break
        shown.append(o)
    left = len(origins) - len(shown)
    if not shown:
        return (f"{head} from {len(origins)} playlists")[:DESCRIPTION_MAX]
    return f"{head} from {', '.join(shown)} and {left} more"


def _origins(conn: sqlite3.Connection, ids: list[str]) -> list[str]:
    """The loaded playlists the tracks come from, the one with most of them first."""
    counts: dict[str, int] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(
                f"SELECT p.name, COUNT(DISTINCT pt.track_id) AS n FROM playlist_tracks pt JOIN playlists p ON p.id = pt.playlist_id "
                f"WHERE pt.track_id IN ({','.join('?' * len(chunk))}) GROUP BY p.id", chunk):
            counts[r["name"]] = counts.get(r["name"], 0) + r["n"]
    return [n for n, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _draft_stamp(conn: sqlite3.Connection, draft_id: str) -> str | None:
    """updated_at plus a hash of the draft: updated_at only has one-second resolution."""
    row = conn.execute("SELECT updated_at, data FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    return f"{row['updated_at']}:{hashlib.sha1(row['data'].encode()).hexdigest()}" if row else None


# ---------------------------------------------------------------- previews: new playlists

def _preview_create(conn: sqlite3.Connection, client: SpotifyClient, entries: list[dict],
                    versions: str | None, keep_saved: list[str] | None, unavailable: str | None,
                    draft: dict | None, now: int) -> str:
    """entries: {"name", "group", "ids", "keep_visible", "description"} (group/keep_visible None outside a draft)."""
    if versions not in (None, "current", "saved"):
        raise WriteError('versions must be "current" or "saved".')
    if unavailable not in (None, "keep", "leave_out"):
        raise WriteError('unavailable must be "keep" or "leave_out".')
    keep = set(parse_track_ids(keep_saved or []))
    all_ids = [t for e in entries for t in e["ids"]]
    if not all_ids:
        raise WriteError("There are no tracks to write.")
    meta = _metas(client, conn, all_ids)
    chosen = [_choices(meta, e["ids"], versions, keep, unavailable) for e in entries]
    asks = [_question(e["name"] if not e.get("group") else e["group"], c) for e, c in zip(entries, chosen)
            if c["ask_replaced"] or c["ask_unavailable"]]
    if asks:
        return "\n\n".join(asks) + "\n" + _ASK_HELP
    taken = {(p.get("name") or "").casefold() for p in client.my_playlists() if p}
    day = date.fromtimestamp(now).isoformat()
    items, lines, notes = [], [], []
    for n, (e, c) in enumerate(zip(entries, chosen), 1):
        name = e["name"].strip()
        if not name:
            raise WriteError("A playlist needs a name.")
        name, renamed = _unique_name(name, taken)
        if renamed:
            notes.append(f'You already have a playlist called "{e["name"].strip()}": the new one is called "{name}" '
                         "(ask the user if they prefer another name).")
        desc = e.get("description")
        if desc is None:
            desc = make_description(day, _origins(conn, e["ids"]))
        if len(desc) > DESCRIPTION_MAX:
            raise WriteError(f"The description is {len(desc)} characters; Spotify allows {DESCRIPTION_MAX}.")
        pairs = c["pairs"]
        write_ids = list(dict.fromkeys(w for _, w in pairs))
        if not write_ids:
            raise WriteError(f'"{name}" would have no tracks.')
        items.append({"name": name, "description": desc, "ids": write_ids, "pairs": [list(p) for p in pairs],
                      "group": e.get("group"), "keep_visible": bool(e.get("keep_visible"))})
        marks = ("will be marked done" if e.get("group") and not e.get("keep_visible") else
                 "kept visible: not marked done" if e.get("group") else "")
        head = f'{n}. "{name}": {len(write_ids)} tracks'
        if e.get("group"):
            head += f' (draft {draft["id"]}, group "{e["group"]}"{"; " + marks if marks else ""})'
        lines.append(head + f"\n   Description: {desc}")
        extras = []
        if c["swapped"]:
            extras.append(f"{len(c['swapped'])} replaced versions written as the current version")
        if versions == "saved" and any(meta[t]["current_id"] for t in e["ids"]):
            extras.append("replaced versions kept as saved")
        if c["left_out"]:
            extras.append(f"{len(c['left_out'])} tracks not playable in the user's country left out: "
                          + "; ".join(f"{m['name']} - {m['artist']}" for m in c["left_out"][:10]))
        if len(write_ids) < len(pairs):
            extras.append(f"{len(pairs) - len(write_ids)} repeated after choosing versions, written once")
        if extras:
            lines.append("   " + "; ".join(extras))
        first: dict[str, str] = {}
        for d_id, w_id in pairs:
            first.setdefault(w_id, d_id)
        shown = [[i, meta[d_id]["name"] + (" (current version)" if w_id != d_id else ""), meta[d_id]["artist"], meta[d_id]["album"] or ""]
                 for i, (w_id, d_id) in enumerate(list(first.items())[:SHOWN], 1)]
        lines.append(_table(["#", "Track", "Artist", "Album"], shown) + (f"\n   ... and {len(write_ids) - SHOWN} more" if len(write_ids) > SHOWN else ""))
    head = [f"Preview: create {len(items)} playlist{'s' if len(items) != 1 else ''}.",
            "Visibility: Spotify creates playlists made through its API as PUBLIC (it ignores a private request; checked "
            "2026-11-10). To make one private: in the Spotify app, ... > Make private."]
    plan = {"kind": "create", "items": items,
            "draft": {"id": draft["id"], "stamp": _draft_stamp(conn, draft["id"])} if draft else None}
    return _with_code(conn, plan, "\n".join(head + notes + [""] + lines), now, "this preview")


def create_playlist(conn: sqlite3.Connection, client: SpotifyClient, name: str, tracks: list[str] | None = None,
                    draft: str | None = None, group: str | None = None, description: str | None = None,
                    versions: str | None = None, keep_saved: list[str] | None = None,
                    unavailable: str | None = None, now: int | None = None) -> str:
    now = _now(now)
    if bool(tracks) == bool(draft or group):
        raise WriteError("Give either tracks, or a draft and one of its groups.")
    d = None
    if tracks:
        ids, entry = parse_track_ids(tracks), {}
    else:
        if not (draft and group):
            raise WriteError("A draft group needs both the draft and the group.")
        d = drafts.load_draft(conn, draft)
        g = drafts._find_group(d["groups"], group)
        ids, entry = list(g["tracks"]), {"group": g["name"], "keep_visible": g.get("keep_visible")}
    return _preview_create(conn, client, [{"name": name, "ids": ids, "description": description, **entry}],
                           versions, keep_saved, unavailable, d, now)


def write_draft(conn: sqlite3.Connection, client: SpotifyClient, draft: str, groups: list[str] | None = None,
                names: dict[str, str] | None = None, versions: str | None = None,
                keep_saved: list[str] | None = None, unavailable: str | None = None, now: int | None = None) -> str:
    now = _now(now)
    d = drafts.load_draft(conn, draft)
    chosen = [drafts._find_group(d["groups"], g) for g in groups] if groups else list(d["groups"])
    renames = {drafts._find_group(d["groups"], k)["name"]: v for k, v in (names or {}).items()}
    entries = [{"name": renames.get(g["name"]) or g["name"], "group": g["name"], "ids": list(g["tracks"]),
                "keep_visible": g.get("keep_visible")} for g in chosen if g["tracks"]]
    if not entries:
        raise WriteError("The chosen groups have no tracks.")
    return _preview_create(conn, client, entries, versions, keep_saved, unavailable, d, now)


# ---------------------------------------------------------------- previews: editing playlists

def _item(meta: dict, rows: list[dict], ops: list[dict]) -> dict:
    return {"playlist_id": meta["id"], "name": meta.get("name"), "url": (meta.get("external_urls") or {}).get("spotify"),
            "content": content_hash(rows), "ops": ops}


def add_tracks(conn: sqlite3.Connection, client: SpotifyClient, playlist: str, tracks: list[str],
               position: int | None = None, now: int | None = None) -> str:
    now = _now(now)
    ids = parse_track_ids(tracks)
    meta, rows = _live_playlist(client, playlist)
    have = {r["id"] for r in rows if r["id"]}
    new = [t for t in ids if t not in have]
    skipped = [t for t in ids if t in have]
    info = _metas(client, conn, ids)
    if not new:
        return f'Nothing to add: all {len(ids)} tracks are already in "{meta["name"]}". Nothing changes on Spotify.'
    if position is not None and not 0 <= position <= len(rows):
        raise WriteError(f'position must be between 0 and {len(rows)} ("{meta["name"]}" has {len(rows)} items); omit it to append.')
    where = f"at position {position} (0 = the start)" if position is not None else "at the end"
    out = [f'Preview: add {len(new)} tracks to "{meta["name"]}" ({(meta.get("external_urls") or {}).get("spotify") or ""}), '
           f'{where}. The playlist has {len(rows)} items now.']
    if skipped:
        out.append(f"Skipped, already in the playlist ({len(skipped)}): " + "; ".join(f"{info[t]['name']} - {info[t]['artist']}" for t in skipped[:10]))
    out.append(_table(["#", "Track", "Artist", "Album"], [[i, info[t]["name"], info[t]["artist"], info[t]["album"] or ""]
                                                         for i, t in enumerate(new[:SHOWN], 1)])
               + (f"\n... and {len(new) - SHOWN} more" if len(new) > SHOWN else ""))
    plan = {"kind": "add", "items": [_item(meta, rows, [{"op": "add", "ids": new, "position": position}])]}
    return _with_code(conn, plan, "\n".join(out), now, "this preview")


def remove_tracks(conn: sqlite3.Connection, client: SpotifyClient, playlist: str, tracks: list[str],
                  now: int | None = None) -> str:
    now = _now(now)
    ids = parse_track_ids(tracks)
    meta, rows = _live_playlist(client, playlist)
    by_id: dict[str, list[dict]] = {}
    for r in rows:
        if r["id"] in ids:
            by_id.setdefault(r["id"], []).append(r)
    missing = [t for t in ids if t not in by_id]
    if not by_id:
        return f'Nothing to remove: none of those {len(ids)} tracks is in "{meta["name"]}". Nothing changes on Spotify.'
    found = [t for t in ids if t in by_id]
    out = [f'Preview: remove {len(found)} tracks from "{meta["name"]}" ({(meta.get("external_urls") or {}).get("spotify") or ""}). '
           f"It has {len(rows)} items now."]
    dups = [t for t in found if len(by_id[t]) > 1]
    if dups:
        out.append("WARNING: Spotify removes EVERY copy of a track. These are in the playlist more than once and all copies go: "
                   + "; ".join(f"{by_id[t][0]['name']} ({len(by_id[t])} copies)" for t in dups))
    if missing:
        out.append(f"Not in the playlist, ignored ({len(missing)}): " + ", ".join(missing[:10]))
    out.append(_table(["Position", "Track", "Artist"], [[", ".join(str(r["pos"]) for r in by_id[t]), by_id[t][0]["name"], by_id[t][0]["artist"]]
                                                        for t in found[:SHOWN]])
               + (f"\n... and {len(found) - SHOWN} more" if len(found) > SHOWN else ""))
    positions = sorted([r["pos"], t] for t in found for r in by_id[t])
    plan = {"kind": "remove", "items": [_item(meta, rows, [{"op": "remove", "ids": found, "positions": positions}])]}
    return _with_code(conn, plan, "\n".join(out), now, "this preview")


def replace_versions(conn: sqlite3.Connection, client: SpotifyClient, playlist: str | None = None,
                     now: int | None = None) -> str:
    """Swap replaced album versions for the version Spotify plays now, in the same position."""
    now = _now(now)
    collection.relink_popularity_zero(client, conn, now)   # tracks loaded before the check existed
    replaced = {r["id"]: r for r in conn.execute(
        "SELECT t.id, t.name, t.album, t.current_id, t.current_album FROM tracks t WHERE t.current_id IS NOT NULL")}
    if not replaced:
        return "No replaced versions found in the loaded playlists (see find_replaced_tracks). Nothing to swap."
    me = client.me()
    if playlist:
        candidates = [playlist]
    else:
        candidates = [r["id"] for r in conn.execute(
            "SELECT DISTINCT p.id FROM playlists p JOIN playlist_tracks pt ON pt.playlist_id = p.id "
            f"WHERE pt.track_id IN ({','.join('?' * len(replaced))}) ORDER BY p.name", list(replaced))]
    items, lines, notes = [], [], []
    total = same = 0
    for ref in candidates:
        try:
            meta = resolve_playlist(client, ref, me)
        except WriteError as exc:
            if playlist:
                raise
            notes.append(f"Skipped: {exc}")
            continue
        rows = live_rows(client, meta["id"])
        have = {r["id"] for r in rows if r["id"]}
        pairs, already = [], set()
        for r in rows:
            if r["id"] in replaced:
                new = replaced[r["id"]]["current_id"]
                if new in have:
                    already.add(r["id"])
                else:
                    pairs.append([r["pos"], r["id"], new])
        if already:
            notes.append(f'"{meta["name"]}": {len(already)} replaced tracks already have their current version in the same playlist; '
                         "left alone (remove the saved copy with remove_tracks if you want).")
        if not pairs:
            continue
        items.append(_item(meta, rows, [{"op": "replace", "pairs": pairs}]))
        for pos, old, _ in pairs:
            m = replaced[old]
            total += 1
            same += m["album"] == m["current_album"]
            lines.append([meta["name"], pos, m["name"], track_meta(conn, [old])[old]["artist"], m["album"], m["current_album"]])
    if not items:
        return "\n".join(["Nothing to swap in the playlists checked."] + notes)
    out = [f"Preview: swap {total} replaced album versions for the version Spotify plays now, in {len(items)} playlist"
           f"{'s' if len(items) != 1 else ''}, each in the same position. {same} on the same album, {total - same} on another album. "
           "Their \"added\" date becomes today (Spotify limitation). One confirmation, one undo_last_write undoes it all.",
           _table(["Playlist", "Position", "Track", "Artist", "Saved album", "Current album"], lines[:SHOWN])
           + (f"\n... and {len(lines) - SHOWN} more" if len(lines) > SHOWN else "")] + notes
    return _with_code(conn, {"kind": "replace", "items": items}, "\n".join(out), now, "this preview")


# ---------------------------------------------------------------- running ops (the only Spotify writes)

def _chunks(seq: list, n: int = MAX_PER_CALL):
    for i in range(0, len(seq), n):
        yield i, seq[i:i + n]


def _runs(entries: list[list]) -> list[tuple[int, list[str]]]:
    """[[pos, id]] sorted -> [(first position, ids)] for each run of consecutive positions."""
    runs: list[tuple[int, list[str]]] = []
    for pos, tid in sorted(entries):
        if runs and runs[-1][0] + len(runs[-1][1]) == pos:
            runs[-1][1].append(tid)
        else:
            runs.append((pos, [tid]))
    return runs


def _add(client, pid, ids: list[str], position: int | None, record: Callable[[dict], None]) -> None:
    for i, chunk in _chunks(ids):
        pos = None if position is None else position + i
        snap = client.add_items(pid, [uri(t) for t in chunk], pos)
        record({"call": "add", "tracks": len(chunk), "ids": chunk, "position": pos, "snapshot_id": snap})


def _remove(client, pid, ids: list[str], record: Callable[[dict], None]) -> None:
    for _, chunk in _chunks(ids):
        snap = client.remove_items(pid, [uri(t) for t in chunk])
        record({"call": "remove", "tracks": len(chunk), "ids": chunk, "snapshot_id": snap})


def run_op(client: SpotifyClient, pid: str, op: dict, record: Callable[[dict], None]) -> None:
    """Carry out one op, calling record(batch) after every Spotify call that went through."""
    kind = op["op"]
    if kind == "add":
        _add(client, pid, op["ids"], op.get("position"), record)
    elif kind == "remove":
        _remove(client, pid, op["ids"], record)
    elif kind == "insert":                                  # restore removed tracks, earliest position first
        for start, ids in _runs(op["entries"]):
            _add(client, pid, ids, start, record)
    elif kind == "replace":
        # Insert each run of new versions right after the run of old ones, then drop the old ones:
        # nothing is lost if it stops half way, and the new ones end up exactly where the old were.
        inserted = 0
        for start, ids in _runs([[pos, new] for pos, _, new in op["pairs"]]):
            _add(client, pid, ids, start + len(ids) + inserted, record)
            inserted += len(ids)
        _remove(client, pid, list(dict.fromkeys(old for _, old, _ in op["pairs"])), record)
    else:
        raise WriteError(f"Unknown operation {kind!r}.")


def _planned_calls(ops: list[dict]) -> int:
    n = 0
    for op in ops:
        k = op["op"]
        if k == "add":
            n += -(-len(op["ids"]) // MAX_PER_CALL)
        elif k == "remove":
            n += -(-len(op["ids"]) // MAX_PER_CALL)
        elif k == "insert":
            n += sum(-(-len(ids) // MAX_PER_CALL) for _, ids in _runs(op["entries"]))
        elif k == "replace":
            n += sum(-(-len(ids) // MAX_PER_CALL) for _, ids in _runs([[p, nw] for p, _, nw in op["pairs"]]))
            n += -(-len({o for _, o, _ in op["pairs"]}) // MAX_PER_CALL)
    return n


def _inverse(op: dict) -> dict:
    if op["op"] == "add":
        return {"op": "remove", "ids": op["ids"], "positions": []}
    if op["op"] == "remove":
        return {"op": "insert", "entries": op["positions"]}
    if op["op"] == "replace":
        return {"op": "replace", "pairs": [[pos, new, old] for pos, old, new in op["pairs"]]}
    raise WriteError("This write can't be undone.")


# ---------------------------------------------------------------- confirm_write

def _batch_lines(batches: list[dict]) -> str:
    return "; ".join(f"{b['call']} {b['tracks']}" + (f" at {b['position']}" if b.get("position") is not None else "") for b in batches)


def _check_unchanged(client: SpotifyClient, conn: sqlite3.Connection, plan: dict) -> None:
    """Abort (nothing written) if the draft or any playlist changed since the preview."""
    if plan.get("draft"):
        d = plan["draft"]
        if _draft_stamp(conn, d["id"]) != d["stamp"]:
            raise WriteError(f"Draft {d['id']} changed after the preview (or is gone). Nothing was written. Ask for a new preview.")
    for item in plan["items"] if plan["kind"] != "create" else []:
        if content_hash(live_rows(client, item["playlist_id"])) != item["content"]:
            raise WriteError(f'"{item["name"]}" changed on Spotify after the preview. Nothing was written. Ask for a new preview.')


def _reload(client, conn, playlist_ids: list[str]) -> str:
    cached = [p for p in playlist_ids if conn.execute("SELECT 1 FROM playlists WHERE id = ?", (p,)).fetchone()]
    if not cached:
        return ""
    try:
        collection.load_playlists(client, conn, cached, force=True)
    except SpotifyError as exc:
        return f"\nThe local copy of the edited playlists could not be refreshed ({exc}); call load_playlists with force=true."
    return f"\nRe-read {len(cached)} edited playlist{'s' if len(cached) != 1 else ''} from Spotify: the cache and dashboards are up to date."


def confirm(client: SpotifyClient, conn: sqlite3.Connection, code: str, now: int | None = None) -> str:
    """Carry out a previewed plan. The only function that writes to Spotify."""
    now = _now(now)
    plan = _claim_plan(conn, code, now)
    _check_unchanged(client, conn, plan)
    op_id = f"{(code or '').strip().upper()}-{now}"   # codes can be issued again once burnt; the time keeps ops apart
    if plan["kind"] == "create":
        return _confirm_create(client, conn, plan, op_id, now)
    return _confirm_edit(client, conn, plan, op_id, now)


def _confirm_create(client, conn, plan: dict, op_id: str, now: int) -> str:
    results, stopped, ok = [], False, 0
    for n, item in enumerate(plan["items"], 1):
        if stopped:
            results.append(f'{n}. "{item["name"]}": NOT written ({len(item["ids"])} tracks).')
            continue
        row = None
        written: list[str] = []
        try:
            pl = client.create_playlist(item["name"], item["description"], public=False)  # asked anyway, in case Spotify honours it again
            url = (pl.get("external_urls") or {}).get("spotify") or ""
            row = _log_start(conn, op_id, "create", pl["id"], item["name"], {"name": item["name"], "tracks": len(item["ids"])}, now)

            def record(b, row=row):
                _log_batch(conn, row, b)
                written.extend(b["ids"])
            _add(client, pl["id"], item["ids"], None, record)
            _log_end(conn, row, "done")
            status = "done"
        except SpotifyError as exc:
            stopped, status = True, "partial" if row else "failed"
            if row:
                _log_end(conn, row, "partial" if written else "failed")
            err = str(exc)
        marked = 0
        if item.get("group") and not item.get("keep_visible") and written:
            done = set(written)
            marked = progress.mark_done(conn, [d for d, w in item["pairs"] if w in done], item["group"])
        if status == "done":
            ok += 1
            real = ""
            try:
                actual = client.playlist(pl["id"]).get("public")
                real = (" Visibility: public (... > Make private in the Spotify app to hide it)." if actual
                        else " Visibility: private.")
            except SpotifyError:
                pass
            results.append(f'{n}. Created "{item["name"]}": {len(written)} of {len(item["ids"])} tracks. {url}{real}'
                           + (f" {marked} tracks marked done." if marked else ""))
        elif row:
            results.append(f'{n}. "{item["name"]}" was created ({url}) but only {len(written)} of {len(item["ids"])} tracks went in '
                           f"(the first {len(written)}, in order). Failed: {err}."
                           + (f" {marked} tracks marked done." if marked else ""))
        else:
            results.append(f'{n}. "{item["name"]}": NOT created. {err}')
    head = (f"Done: {ok} playlist{'s' if ok != 1 else ''} created." if ok == len(plan["items"]) else
            f"STOPPED: {ok} of {len(plan['items'])} playlists complete. The rest is picked up with a new preview (groups already marked done leave the pool).")
    return head + "\n" + "\n".join(results) + "\nNew playlists are not loaded into the analysis; call load_playlists if the user wants them there."


def _confirm_edit(client, conn, plan: dict, op_id: str, now: int) -> str:
    kind = plan["kind"]
    results, stopped, touched = [], False, []
    for item in plan["items"]:
        ops = item["ops"]
        if stopped:
            results.append(f'"{item["name"]}": NOT changed.')
            continue
        row = _log_start(conn, op_id, kind, item["playlist_id"], item["name"], ops, now)
        batches: list[dict] = []

        def record(b, row=row, batches=batches):
            _log_batch(conn, row, b)
            batches.append(b)
        touched.append(item["playlist_id"])
        try:
            for op in ops:
                run_op(client, item["playlist_id"], op, record)
        except SpotifyError as exc:
            stopped = True
            _log_end(conn, row, "partial" if batches else "failed")
            results.append(f'"{item["name"]}": INCOMPLETE, {len(batches)} of {_planned_calls(ops)} calls went through '
                           f"({_batch_lines(batches) or 'none'}). Failed: {exc}. Look at the playlist before trying again.")
        else:
            _log_end(conn, row, "done", content_hash(live_rows(client, item["playlist_id"])))
            results.append(f'"{item["name"]}": {_describe_done(ops)} ({len(batches)} calls). {item.get("url") or ""}')
    if kind == "undo":
        undone = [b for b in plan["items"]]
        with conn:
            for item in undone:
                conn.execute("UPDATE writes SET undone = 1 WHERE op_id = ? AND playlist_id = ? AND status = 'done'", (plan["of_op"], item["playlist_id"]))
    head = "STOPPED, not everything was done." if stopped else "Done."
    return head + "\n" + "\n".join(results) + _reload(client, conn, touched)


def _describe_done(ops: list[dict]) -> str:
    parts = []
    for op in ops:
        k = op["op"]
        if k == "add":
            parts.append(f"added {len(op['ids'])} tracks")
        elif k == "remove":
            parts.append(f"removed {len(op['ids'])} tracks")
        elif k == "insert":
            parts.append(f"put back {len(op['entries'])} tracks")
        elif k == "replace":
            parts.append(f"swapped {len(op['pairs'])} tracks")
    return ", ".join(parts)


# ---------------------------------------------------------------- undo

def undo_last_write(conn: sqlite3.Connection, client: SpotifyClient, now: int | None = None) -> str:
    """Preview reverting the last confirmed add, remove or replace (all its playlists)."""
    now = _now(now)
    last = conn.execute("SELECT op_id, kind FROM writes ORDER BY id DESC LIMIT 1").fetchone()
    if not last:
        return "There is nothing to undo: no write has been confirmed yet."
    if last["kind"] == "create":
        return ("The last write created playlists. Tailorlist never deletes playlists, so that can't be undone from here: "
                "the user can delete them in Spotify.")
    if last["kind"] == "undo":
        return "The last write was already an undo, and an undo can't be undone. Ask for the change again if needed."
    rows = conn.execute("SELECT * FROM writes WHERE op_id = ? ORDER BY id", (last["op_id"],)).fetchall()
    if any(r["status"] != "done" for r in rows):
        return ("The last write didn't finish (see the confirm_write result), so it can't be undone automatically. "
                "Check the playlist on Spotify.")
    items = []
    for r in rows:
        meta = client.playlist(r["playlist_id"])
        live = live_rows(client, r["playlist_id"])
        if content_hash(live) != r["content_hash"]:
            return (f'Refused: "{r["playlist_name"]}" changed after that write, so undoing it could disturb newer edits. '
                    "Nothing was changed.")
        ops = [_inverse(op) for op in reversed(json.loads(r["ops"]))]
        items.append(_item(meta, live, ops) | {"name": r["playlist_name"]})
    out = [f"Preview: undo the last write ({last['kind']}), on {len(items)} playlist{'s' if len(items) != 1 else ''}. "
           "Tracks put back show today's added date (Spotify limitation). An undo can't be undone."]
    for it in items:
        what = []
        for op in it["ops"]:
            what.append({"remove": f"remove {len(op.get('ids') or [])} tracks that were added",
                         "insert": f"put back {len(op.get('entries') or [])} removed tracks in their old positions",
                         "replace": f"swap {len(op.get('pairs') or [])} tracks back to the saved versions"}[op["op"]])
        out.append(f'- "{it["name"]}": ' + "; ".join(what))
    return _with_code(conn, {"kind": "undo", "of_op": last["op_id"], "items": items}, "\n".join(out), now, "this preview")
