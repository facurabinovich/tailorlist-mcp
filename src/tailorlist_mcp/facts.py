"""fun_facts: ready-made curiosities about the collection, from the cache only.

Each entry of FACTS is a small function (Ctx) -> Fact | None, in the order they are told. It returns
None when the data doesn't support the fact or it would be dull (a "dominant decade" under 25%). The
shared frame is built once, with the same records and track frame the other data tools use, and the
metrics that already exist (listening age, oldest and newest, average popularity) are reused from
analysis/overview.py, not re-implemented.

To add a fact, write the function and append it to FACTS.
"""
from __future__ import annotations

import datetime
import sqlite3
from dataclasses import dataclass

import pandas as pd

from tailorlist_mcp import dashboard, store
from tailorlist_mcp.analysis import overview as ov
from tailorlist_mcp.analysis.formatting import fmt_duration
from tailorlist_mcp.analysis.genres import assign_family
from tailorlist_mcp.queries import QueryError

DEFAULT_LIMIT = 8
MAX_LIMIT = 14

#: Below these, a fact is not worth telling.
MIN_DECADE_SHARE = 0.25
MIN_ARTIST_TRACKS = 3
MIN_TAIL_ARTISTS = 10
MIN_TAIL_SHARE = 0.4
MIN_TRACKS = 5
MIN_EVERYWHERE = 3
MIN_ALBUM_TRACKS = 4
MIN_SPAN_DAYS = 365


@dataclass(frozen=True)
class Fact:
    title: str
    text: str
    chart: str | None = None      # one-line hint, same style as charts.py
    tool: str | None = None       # the call that digs deeper
    strong: bool = False          # a clear signal: told before the others


@dataclass
class Ctx:
    df: pd.DataFrame              # one row per playlist entry
    uniq: pd.DataFrame            # one row per unique track
    shared: pd.DataFrame          # entries counted for "in how many playlists" (no Liked Songs unless named)
    year: int                     # "now", for the listening age


def _n(value) -> str:
    return f"{int(value):,}"


def _pct(share: float) -> str:
    return f"{share * 100:.0f}%"


def _portion(share: float) -> str:
    """'Half', 'A third'... when the share is close to one, else the percentage."""
    for lo, hi, word in ((0.45, 0.55, "Half"), (0.30, 0.37, "A third"), (0.23, 0.27, "A quarter"), (0.62, 0.70, "Two thirds")):
        if lo <= share <= hi:
            return word
    return _pct(share)


def _song(row: pd.Series) -> str:
    return f'"{row["track_name"]}" by {row["artist_display"]}'


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _quoted(name: str) -> str:
    return '"' + str(name).replace('"', '\\"') + '"'


# ---------------------------------------------------------------- the facts

def your_decade(c: Ctx) -> Fact | None:
    dated = c.uniq["decade"].dropna().astype(int)
    if len(dated) < MIN_TRACKS:
        return None
    counts = dated.value_counts().sort_index()
    top = int(counts.idxmax())            # the earliest on ties
    share = counts[top] / len(dated)
    if share < MIN_DECADE_SHARE:
        return None
    return Fact("Your decade",
                f"{_portion(share)} of your library is from the {top}s: {_n(counts[top])} of {_n(len(dated))} songs.",
                f"column chart of the decades in time order, the {top}s in green and the rest muted.",
                'collection_stats(group_by="decade")', strong=share >= 0.4)


def listening_age(c: Ctx) -> Fact | None:
    if len(c.uniq["release_year"].dropna()) < MIN_TRACKS:
        return None
    p = ov.compute_listening_profile(c.uniq, c.year)
    if p is None:
        return None
    return Fact("Your listening age",
                f"Half of your songs came out before {p.median_year} and half after. That puts your listening age at "
                f"{p.listening_age}, as if you were born in {p.est_birth}.",
                None, 'explain("listening age")')


def your_artist(c: Ctx) -> Fact | None:
    named = c.uniq[c.uniq["artist_name"].fillna("") != ""]
    if named.empty:
        return None
    counts = named["artist_name"].value_counts()
    top_n = counts.max()
    if top_n < MIN_ARTIST_TRACKS:
        return None
    artist = sorted(counts[counts == top_n].index)[0]
    in_playlists = c.df.loc[c.df["artist_name"] == artist, "playlist_name"].nunique()
    where = "in 1 playlist" if in_playlists == 1 else f"across {in_playlists} playlists"
    return Fact("Your artist",
                f"{artist} is your most-saved artist: {_n(top_n)} songs, {_pct(top_n / len(c.uniq))} of your library, {where}.",
                f"horizontal bar chart of the top 10 artists, {artist} in green and the rest muted.",
                'collection_stats(group_by="artist", limit=10)', strong=top_n / len(c.uniq) >= 0.03)


def long_tail(c: Ctx) -> Fact | None:
    named = c.uniq[c.uniq["artist_name"].fillna("") != ""]
    counts = named["artist_name"].value_counts()
    if len(counts) < MIN_TAIL_ARTISTS:
        return None
    ones = int((counts == 1).sum())
    share = ones / len(counts)
    if share < MIN_TAIL_SHARE:
        return None
    return Fact("The long tail",
                f"{_portion(share)} of your artists have exactly one saved song: {_n(ones)} of {_n(len(counts))} artists.",
                None, 'collection_stats(group_by="artist", limit=20)')


def oldest_and_newest(c: Ctx) -> Fact | None:
    e = ov.find_oldest_newest(c.uniq)
    if e is None or e.span < 1:
        return None
    return Fact("Oldest and newest",
                f"Your oldest song is {_song(e.oldest)} ({e.oldest_year}) and your newest is {_song(e.newest)} "
                f"({e.newest_year}): {e.span} years of music.",
                None, 'find_tracks(sort="year", ascending=true, limit=5)', strong=e.span >= 40)


def hours_of_music(c: Ctx) -> Fact | None:
    ms = _num(c.uniq["duration_raw"]).sum()
    hours = ms / 3_600_000
    if hours < 1:
        return None
    whole = int(hours)
    days, rest = divmod(whole, 24)
    if days:
        listening = f"{days} day{'s' if days != 1 else ''} and {rest} hour{'s' if rest != 1 else ''}"
    else:
        listening = f"{whole} hour{'s' if whole != 1 else ''}"
    many = c.df["playlist_name"].nunique() >= 3
    return Fact("Hours of music",
                f"Your library is {_n(whole)} hours of music: {listening} of non-stop listening.",
                "horizontal bar chart of the hours per playlist." if many else None,
                'collection_stats(group_by="playlist", metrics=["hours"])' if many else None, strong=days >= 7)


def longest_and_shortest(c: Ctx) -> Fact | None:
    d = c.uniq[_num(c.uniq["duration_raw"]) > 0]
    if len(d) < MIN_TRACKS:
        return None
    dur = _num(d["duration_raw"])
    longest, shortest = d.loc[dur.idxmax()], d.loc[dur.idxmin()]
    if dur.max() == dur.min():
        return None
    return Fact("Longest and shortest",
                f"Your longest song is {_song(longest)} ({fmt_duration(dur.max() / 60000)}) and your shortest is "
                f"{_song(shortest)} ({fmt_duration(dur.min() / 60000)}).",
                None, 'find_tracks(sort="duration", limit=5)')


def mainstream_meter(c: Ctx) -> Fact | None:
    pop = _num(c.uniq["popularity_raw"]).dropna()
    if len(pop) < MIN_TRACKS:
        return None
    avg = pop.mean()
    feel = ("deep in niche territory" if avg < 35 else "a little off the beaten path" if avg < 50
            else "a healthy mix of hits and deep cuts" if avg < 65 else "mostly hits")
    live = c.uniq.loc[pop.index]
    top = live.loc[pop.idxmax()]
    text = f"Your average popularity is {avg:.0f} out of 100: {feel}. The most popular is {_song(top)} ({int(pop.max())})"
    rare = pop[pop > 0]                        # 0 means a replaced version: not a real score
    if not rare.empty and rare.min() != pop.max():
        low = live.loc[rare.idxmin()]
        text += f" and the rarest is {_song(low)} ({int(rare.min())})."
    else:
        text += "."
    return Fact("Mainstream meter", text, "column chart of the popularity bins in order.",
                'collection_stats(group_by="popularity_bin")', strong=avg < 35 or avg >= 65)


def busiest_month(c: Ctx) -> Fact | None:
    t = ov.additions_timeline(c.df)
    if t.empty or len(t) < 3:
        return None
    peak = t.loc[t["tracks_added"].idxmax()]
    total = int(t["tracks_added"].sum())
    if peak["tracks_added"] < 2 * t["tracks_added"].mean():
        return None
    month = pd.Timestamp(peak["year_month"] + "-01").strftime("%B %Y")
    return Fact("Your busiest month",
                f"{month} was your busiest month: {_n(peak['tracks_added'])} songs added, "
                f"{_pct(peak['tracks_added'] / total)} of everything you ever added.",
                "line chart of the songs added per month in order, the peak labelled.",
                'collection_stats(group_by="month_added")', strong=peak["tracks_added"] / total >= 0.1)


def _added(c: Ctx) -> pd.DataFrame:
    d = c.df.assign(_when=pd.to_datetime(c.df["added_at"], errors="coerce", utc=True))
    return d.dropna(subset=["_when"])


def loyal_since(c: Ctx) -> Fact | None:
    d = _added(c)
    if d.empty:
        return None
    first, last = d.loc[d["_when"].idxmin()], d["_when"].max()
    span = last - first["_when"]
    if span < pd.Timedelta(days=MIN_SPAN_DAYS):
        return None
    years = int(span.days // 365.25)
    return Fact("Loyal since",
                f"The first song you saved was {_song(first)}, on {first['_when'].strftime('%B %d, %Y').replace(' 0', ' ')}. "
                f"Your collection has been growing for {years} year{'s' if years != 1 else ''}.",
                None, 'find_tracks(sort="added", ascending=true, limit=5)')


def everywhere_song(c: Ctx) -> Fact | None:
    if c.shared.empty:
        return None
    where = c.shared.groupby("track_id")["playlist_name"].nunique()
    top = where.max()
    if top < MIN_EVERYWHERE:
        return None
    ids = sorted(where[where == top].index)
    row = c.shared[c.shared["track_id"] == ids[0]].iloc[0]
    return Fact("The everywhere song",
                f"{_song(row)} is in {top} of your {c.shared['playlist_name'].nunique()} playlists: "
                "the song you never leave out.", None, 'find_duplicates(kind="exact")', strong=True)


def genre_mix(c: Ctx) -> Fact | None:
    tagged = c.uniq[c.uniq["artist_genres"].fillna("").str.strip() != ""]
    if len(tagged) < MIN_TRACKS:
        return None
    counts: dict[str, int] = {}
    for g in tagged["artist_genres"]:
        for fam in assign_family(g):
            counts[fam] = counts.get(fam, 0) + 1
    real = {k: v for k, v in counts.items() if k != "Other"}
    if not real:
        return None
    top = sorted(real, key=lambda k: (-real[k], k))[0]
    return Fact("Genre mix",
                f"{top} leads your library: {_pct(real[top] / len(tagged))} of the songs with genres ({_n(real[top])} of "
                f"{_n(len(tagged))}). {len(real)} genre {'family is' if len(real) == 1 else 'families are'} in the mix.",
                "horizontal bar chart of the genre families, " + top + " in green and the rest muted.",
                'collection_stats(group_by="genre_family")')


def time_traveller(c: Ctx) -> Fact | None:
    d = c.df.dropna(subset=["release_year"])
    spans = d.groupby("playlist_name")["release_year"].agg(["min", "max", "size"])
    if len(spans) < 2:
        return None
    spans["span"] = (spans["max"] - spans["min"]).astype(int)
    ranked = spans.sort_values(["span", "size"], ascending=[False, False])
    wide_name, wide = ranked.index[0], ranked.iloc[0]
    if wide["span"] < 1:
        return None
    text = (f'"{wide_name}" is your time traveller: it goes from {int(wide["min"])} to {int(wide["max"])}, '
            f'{wide["span"]} years.')
    sized = spans[spans["size"] >= MIN_TRACKS]
    if len(sized) >= 2:
        tight = sized.sort_values(["span", "size"], ascending=[True, False])
        name, row = tight.index[0], tight.iloc[0]
        if name != wide_name:
            text += (f' The most focused is "{name}", all within {row["span"]} '
                     f'year{"s" if row["span"] != 1 else ""} ({int(row["min"])} to {int(row["max"])}).')
    return Fact("Time traveller playlist", text,
                "horizontal bar chart of the years each playlist spans (oldest to newest song), one bar per playlist.",
                "compare_playlists()")


def one_album_love(c: Ctx) -> Fact | None:
    d = c.uniq[(c.uniq["album_name"].fillna("") != "")]
    if d.empty:
        return None
    counts = d.groupby(["album_name", "artist_name"]).size().sort_values(ascending=False)
    top_n = counts.max()
    if top_n < MIN_ALBUM_TRACKS:
        return None
    album, artist = sorted(counts[counts == top_n].index)[0]
    return Fact("One-album love",
                f'You saved {_n(top_n)} songs from "{album}" by {artist}: no other album gets that much love.'
                if (counts == top_n).sum() == 1 else
                f'You saved {_n(top_n)} songs from "{album}" by {artist}, the most of any album.',
                None, f"find_tracks(filters={{\"album\": {_quoted(album)}}})")


#: Told in this order.
FACTS = [your_decade, listening_age, your_artist, long_tail, oldest_and_newest, hours_of_music,
         longest_and_shortest, mainstream_meter, busiest_month, loyal_since, everywhere_song, genre_mix,
         time_traveller, one_album_love]


# ---------------------------------------------------------------- the tool

def _context(recs: list[dict], named: bool, year: int) -> Ctx:
    df = dashboard.tracks_frame(recs)
    shared = df if named else df[df["playlist_name"] != store.LIKED_NAME]
    return Ctx(df, ov.dedupe_tracks(df), shared, year)


def collect(c: Ctx) -> list[Fact]:
    """Every fact the data supports: the strong ones first, then the rest, each group in FACTS order."""
    found = [f for f in (fn(c) for fn in FACTS) if f]
    return [f for f in found if f.strong] + [f for f in found if not f.strong]


def fun_facts(conn: sqlite3.Connection, playlists: list[str] | None = None, limit: int = DEFAULT_LIMIT,
              current_year: int | None = None) -> str:
    recs = dashboard.records(conn)
    if not recs:
        raise QueryError("Nothing loaded yet. Use list_my_playlists, ask which playlists to load, then load_playlists.")
    if playlists:
        wanted = {dashboard.find_playlist(conn, ref)["name"] for ref in playlists}
        recs = [r for r in recs if r["playlist_name"] in wanted]
    c = _context(recs, bool(playlists), current_year or datetime.date.today().year)
    limit = max(1, min(limit or DEFAULT_LIMIT, MAX_LIMIT))
    facts = collect(c)[:limit]
    n_playlists = c.df["playlist_name"].nunique()
    out = [f"Fun facts: {n_playlists} playlist{'s' if n_playlists != 1 else ''}, {_n(len(c.uniq))} unique tracks."]
    if not facts:
        out.append("Not enough data for fun facts yet (it needs more tracks, dates or genres).")
        return "\n".join(out)
    out.append("")
    for i, f in enumerate(facts, 1):
        out.append(f"{i}. {f.title}: {f.text}")
        if f.chart:
            out.append(f"   Chart: {f.chart}")
        if f.tool:
            out.append(f"   More: {f.tool}")
    out += ["", "For you: tell these as a story in the user's language. Pick the 3 or 4 best instead of reading them all, "
            "and chart at most one per answer, following explain('chart style')."]
    return "\n".join(out)
