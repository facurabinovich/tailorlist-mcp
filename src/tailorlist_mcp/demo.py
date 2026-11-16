"""Demo mode: a sample collection so every read and curation tool can be tried without a Spotify account.

Everything here is invented: artists, songs and albums come from word lists, so no real catalogue data
ships with Tailorlist. Only the genre tags ("indie rock", "bossa nova") are real words. The generator is
deterministic (random.Random(seed), no network, no data files), and writes through the normal store
functions, so the tools cannot tell the demo from a loaded library, except for the "demo" prefix of
every ID (real Spotify IDs never start with it) and the demo user as owner.

Demo and real data never mix: load() refuses over real playlists, and collection.load_playlists clears
the demo first. The write tools refuse the demo (write.refuse_demo), because it has no Spotify behind it.

Planted on purpose, so that every tool has something to show (see _generate):
  - exact duplicates inside one playlist ("Road trip", "Dance floor") and across playlists (overlaps,
    and "Summer drive", built from 90% of "Road trip": a near-identical pair);
  - the same song in several versions: remasters ("... - Remastered 2011") and live takes ("... - Live");
  - two compilation-looking albums ("Greatest Hits", "The Very Best of ...") whose tracks carry the
    album's year, so find_tracks(doubtful_year) and library_health list them;
  - artists without genres (looked up "in MusicBrainz", nothing found);
  - popularity-0 tracks: replaced versions with a current version, two unavailable ones and one simply
    unpopular (the same columns relinking sets, so find_replaced_tracks and the stats work offline);
  - a small "Liked Songs" with tracks that are in no playlist (find_orphans);
  - playlists of different eras and genre families (compare, split, group), one built in a single
    day ("Workout") and one grown over years ("Road trip").
"""
from __future__ import annotations

import datetime
import random
import sqlite3
import string
import time
from dataclasses import dataclass, field

from tailorlist_mcp import store

DEMO_PREFIX = "demo"
USER_ID = "demo-listener"
USER_NAME = "Demo listener"

SEED = 7
ARTISTS = 120
NO_GENRE_ARTISTS = 6

ONLY_FOR_DEMO = ("This is the demo collection: connect your Spotify (connect_spotify) to write playlists.")
FILLED_IN = "This is the demo collection: its genres and original years are already filled in, so nothing is looked up."
REAL_LOADED = ("You already have your own playlists loaded; the demo is for trying Tailorlist before connecting.")


class DemoError(ValueError):
    """The demo can't be loaded."""


# ---------------------------------------------------------------- word lists (all invented combinations)

_ADJ = ["Velvet", "Paper", "Hollow", "Neon", "Amber", "Quiet", "Copper", "Midnight", "Silver", "Wandering", "Crooked",
        "Glass", "Burning", "Little", "Gentle", "Rusty", "Electric", "Salt", "Distant", "Fading", "Golden", "Sleepy",
        "Wild", "Thin", "Brave", "Lonely", "Crimson", "Pale", "Slow", "Tender", "Bitter", "Sunny", "Marble", "Cobalt",
        "Feral", "Plain", "Restless", "Dusty", "Frozen", "Open", "Second", "Tiny", "Orange", "Painted", "Smoky"]
_PLURAL = ["Lanterns", "Foxes", "Harbors", "Orchards", "Satellites", "Mountains", "Pilots", "Carousels", "Embers", "Tides",
           "Kites", "Rivers", "Engines", "Meadows", "Sparrows", "Windows", "Ghosts", "Compasses", "Ferries", "Clocks",
           "Maps", "Echoes", "Moths", "Rooftops", "Trains", "Letters", "Islands", "Streets", "Violins", "Sailors",
           "Shadows", "Gardens", "Bridges", "Candles", "Radios", "Wolves", "Bicycles", "Dreamers", "Ravens", "Fields"]
_NOUN = ["Harbor", "Lantern", "Orchard", "Satellite", "Mountain", "Carousel", "Ember", "Tide", "Kite", "River", "Engine",
         "Meadow", "Sparrow", "Window", "Compass", "Ferry", "Clock", "Map", "Echo", "Rooftop", "Train", "Letter",
         "Island", "Street", "Violin", "Shadow", "Garden", "Bridge", "Candle", "Radio", "Wolf", "Bicycle", "Raven",
         "Field", "Summer", "Winter", "Morning", "Evening", "Highway", "Lighthouse", "Staircase", "Paper Boat"]
_VERBING = ["Waiting", "Running", "Falling", "Dancing", "Drifting", "Sleeping", "Chasing", "Counting", "Burning",
            "Wandering", "Singing", "Hiding", "Floating", "Climbing", "Walking", "Dreaming", "Leaving", "Waking"]
_PLACE = ["Lisbon Road", "Cedar Hill", "the Old Mill", "Marlowe Bay", "Harrow Street", "Fenwick", "Larkspur", "Dunmore",
          "Westerly", "Ashgrove", "Pinecrest", "Tallow Lane", "the Salt Flats", "Mirrorlake", "Copperfield", "Ravenscar"]
_FIRST = ["Marlo", "Tessa", "Odile", "Brannigan", "Isolde", "Cato", "Wren", "Dario", "Ines", "Leopold", "Calla", "Ansel",
          "Maren", "Ottoline", "Teodor", "Sabine", "Hollis", "Linnea", "Gideon", "Posy", "Rufus", "Elodie", "Barnaby",
          "Soledad", "Fenn", "Mireille", "Jasper", "Noor", "Casimir", "Thea", "Lucho", "Verity", "Oskar", "Delphine"]
_LAST = ["Quillfeather", "Marrow", "Vandermeer", "Oakhurst", "Pennyworth", "Alder", "Castellanos", "Brightwater", "Lindqvist",
         "Hartigan", "Moreau", "Tamsin", "Ferreira", "Wickham", "Strand", "Okonkwo", "Dellaquila", "Halloran", "Mercer",
         "Ambrose", "Kowalczyk", "Larkin", "Delacroix", "Winterbourne", "Bellweather", "Ashdown", "Ruiz-Calder"]
_GROUP = ["Club", "Collective", "Society", "Orchestra", "Parade", "Brigade", "Ensemble", "Union", "Radio", "Choir"]
_TOWN = ["Harbor", "Orchard", "Lantern", "Meadow", "Copper", "Granite", "Cinder", "Willow", "Sable", "Thistle"]

# Names a generated artist or song must never equal or contain (a guard, not a source: nothing here is produced).
_FAMOUS_ARTISTS = {
    "the beatles", "beatles", "queen", "madonna", "prince", "adele", "drake", "eminem", "beyonce", "rihanna", "shakira",
    "bjork", "sting", "cher", "elvis", "bob dylan", "bowie", "radiohead", "nirvana", "metallica", "coldplay", "u2", "abba",
    "oasis", "blur", "pink floyd", "led zeppelin", "rolling stones", "taylor swift", "kanye", "jay-z", "springsteen",
    "michael jackson", "lennon", "mccartney", "miles davis", "coltrane", "louis armstrong", "sinatra", "johnny cash",
    "bob marley", "daft punk", "the clash", "the cure", "the smiths", "the who", "the doors", "the police", "the killers",
    "the strokes", "arcade fire", "lady gaga", "bruno mars", "ed sheeran", "billie eilish", "dua lipa", "the weeknd",
    "nina simone", "ella fitzgerald", "fleetwood mac", "genesis", "yes", "kiss", "journey", "boston", "chicago", "europe",
    "bread", "traffic", "cream", "wham", "tool", "muse", "air", "pulp", "portishead", "massive attack", "the verve",
    "garbage", "blondie", "heart", "kansas", "free", "nickelback", "rammstein", "pavarotti", "mozart", "beethoven", "bach",
    "chopin", "vivaldi", "gardel", "piazzolla", "caetano", "gilberto", "santana", "shakira", "bad bunny", "rosalia",
}
_FAMOUS_SONGS = {
    "yesterday", "imagine", "thriller", "hello", "purple rain", "creep", "believe", "hey jude", "let it be", "bohemian rhapsody",
    "hotel california", "smells like teen spirit", "stairway to heaven", "wonderwall", "respect", "faith", "fire", "help",
    "angel", "royals", "halo", "happy", "firework", "heroes", "changes", "sunshine", "paranoid", "yellow", "clocks",
    "bad guy", "africa", "jump", "stay", "alive", "zombie", "toxic", "lose yourself", "dreams", "time", "money", "wild thing",
}


def _famous_artist(name: str) -> bool:
    key = name.casefold()
    return key in _FAMOUS_ARTISTS or any(f in key for f in _FAMOUS_ARTISTS if len(f) >= 5)


def _famous_song(title: str) -> bool:
    return title.casefold() in _FAMOUS_SONGS


# Real genre tags: (tag, family, first start year, last start year of an artist playing it).
_GENRES = [
    ("classic rock", "Rock", 1960, 1985), ("hard rock", "Rock", 1965, 2000), ("punk rock", "Rock", 1975, 2005),
    ("post-punk", "Rock", 1977, 2010), ("alternative rock", "Rock", 1985, 2020), ("grunge", "Rock", 1988, 1998),
    ("indie rock", "Rock", 1990, 2022), ("shoegaze", "Rock", 1988, 2022), ("garage rock", "Rock", 1960, 2020),
    ("psychedelic rock", "Rock", 1965, 1975), ("heavy metal", "Rock", 1970, 2000),
    ("synth-pop", "Pop", 1980, 2022), ("dream pop", "Pop", 1985, 2022), ("indie pop", "Pop", 1990, 2022),
    ("art pop", "Pop", 1975, 2022), ("pop", "Pop", 1960, 2022), ("baroque pop", "Pop", 1965, 2015),
    ("soul", "Hip-Hop", 1960, 1985), ("hip hop", "Hip-Hop", 1982, 2022), ("r&b", "Hip-Hop", 1985, 2022),
    ("neo soul", "Hip-Hop", 1995, 2022),
    ("disco", "Electronic", 1972, 1985), ("new wave", "Electronic", 1978, 1990), ("ambient", "Electronic", 1975, 2022),
    ("house", "Electronic", 1988, 2022), ("techno", "Electronic", 1988, 2022), ("downtempo", "Electronic", 1993, 2022),
    ("synthwave", "Electronic", 2006, 2022), ("electronica", "Electronic", 1995, 2022),
    ("jazz", "Jazz", 1960, 2022), ("cool jazz", "Jazz", 1960, 1975), ("bebop", "Jazz", 1960, 1968),
    ("blues", "Jazz", 1960, 2000), ("swing", "Jazz", 1960, 1975), ("jazz fusion", "Jazz", 1970, 1990),
    ("classical", "Classical", 1960, 2022), ("neoclassical", "Classical", 1995, 2022),
    ("folk", "Folk", 1960, 2022), ("singer-songwriter", "Folk", 1965, 2022), ("country", "Folk", 1960, 2022),
    ("americana", "Folk", 1995, 2022), ("bluegrass", "Folk", 1960, 2000),
    ("bossa nova", "Latin", 1960, 1975), ("cumbia", "Latin", 1960, 2022), ("latin", "Latin", 1960, 2022),
    ("tango", "Latin", 1960, 1990),
    ("reggae", "Reggae", 1965, 1990), ("ska", "Reggae", 1979, 1995), ("dub", "Reggae", 1972, 2000),
    ("dancehall", "Reggae", 1985, 2022),
]
_FAMILY_WEIGHTS = {"Rock": 28, "Pop": 16, "Hip-Hop": 8, "Electronic": 14, "Jazz": 8, "Classical": 4, "Folk": 12,
                   "Latin": 5, "Reggae": 5}


# ---------------------------------------------------------------- the generator

@dataclass
class _Artist:
    id: str
    name: str
    genres: list[str]
    family: str
    popularity: int
    start: int
    shown_genres: bool = True


@dataclass(eq=False)   # compared and hashed by identity: one object per catalog track
class _Song:
    """One catalog track: the parsed row store.save_playlist takes, plus what the playlists choose by."""
    track: dict
    artists: list[tuple[str, str]]
    year: int            # the song's real year (the album's, except on compilations)
    family: str
    genres: list[str]
    kind: str = "song"   # song, compilation, remaster, live


@dataclass
class Collection:
    playlists: list[tuple[dict, list[dict]]]
    artists: list[dict]                         # store.save_artist_details shapes
    no_genres: list[str]                        # artist IDs looked up in MusicBrainz "with nothing found"
    years: list[tuple[tuple[str, str], int | None]]
    relinks: dict[str, dict]                    # store.save_relinks shapes
    facts: dict = field(default_factory=dict)   # what was planted, for tests


class _Ids:
    """Fake Spotify-shaped IDs: 22 base-62 characters that start with "demo", never repeated."""

    def __init__(self, rng: random.Random) -> None:
        self.rng, self.used = rng, set()

    def new(self) -> str:
        chars = string.digits + string.ascii_letters
        while True:
            value = DEMO_PREFIX + "".join(self.rng.choice(chars) for _ in range(22 - len(DEMO_PREFIX)))
            if value not in self.used:
                self.used.add(value)
                return value


def _artist_name(rng: random.Random) -> str:
    kind = rng.randrange(6)
    if kind == 0:
        return f"The {rng.choice(_ADJ)} {rng.choice(_PLURAL)}"
    if kind == 1:
        return f"{rng.choice(_ADJ)} {rng.choice(_PLURAL)}"
    if kind == 2:
        return f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
    if kind == 3:
        return f"{rng.choice(_FIRST)} & the {rng.choice(_PLURAL)}"
    if kind == 4:
        return f"{rng.choice(_TOWN)} {rng.choice(_GROUP)}"
    return f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"


def _song_title(rng: random.Random) -> str:
    kind = rng.randrange(12)
    if kind == 0:
        return f"{rng.choice(_ADJ)} {rng.choice(_NOUN)}"
    if kind == 1:
        return f"{rng.choice(_VERBING)} in the {rng.choice(_NOUN)}"
    if kind == 2:
        return f"The {rng.choice(_NOUN)} of {rng.choice(_PLACE)}"
    if kind == 3:
        return f"{rng.choice(_FIRST)}'s {rng.choice(_NOUN)}"
    if kind == 4:
        return f"Letters to {rng.choice(_FIRST)}"
    if kind == 5:
        return f"{rng.choice(_ADJ)} {rng.choice(_PLURAL)}"
    if kind == 6:
        return f"Slow {rng.choice(_NOUN)}"
    if kind == 7:
        return f"{rng.choice(_VERBING)} for {rng.choice(_NOUN)}"
    if kind == 8:
        return f"Before the {rng.choice(_NOUN)}"
    if kind == 9:
        return f"{rng.choice(_NOUN)} Song"
    if kind == 10:
        return f"{rng.choice(_PLACE)} Blues".replace("the ", "")
    return f"{rng.choice(_ADJ)} {rng.choice(_NOUN)} ({rng.choice(['for', 'with', 'after'])} {rng.choice(_FIRST)})"


def _album_title(rng: random.Random) -> str:
    kind = rng.randrange(5)
    if kind == 0:
        return f"{rng.choice(_ADJ)} {rng.choice(_PLURAL)}"
    if kind == 1:
        return f"Songs from the {rng.choice(_NOUN)}"
    if kind == 2:
        return f"The {rng.choice(_NOUN)} Sessions"
    if kind == 3:
        return f"{rng.choice(_PLACE).replace('the ', '').title()} Nights"
    return f"{rng.choice(_ADJ)} {rng.choice(_NOUN)}"


def _make_artists(rng: random.Random, ids: _Ids) -> list[_Artist]:
    artists, names = [], set()
    families = list(_FAMILY_WEIGHTS)
    while len(artists) < ARTISTS:
        name = _artist_name(rng)
        if name.casefold() in names or _famous_artist(name):
            continue
        start = min(2021, int(rng.triangular(1960, 2022, 1996)))
        family = rng.choices(families, [_FAMILY_WEIGHTS[f] for f in families])[0]
        options = [g for g in _GENRES if g[1] == family and g[2] <= start <= g[3]]
        if not options:
            continue
        names.add(name.casefold())
        main = rng.choice(options)
        genres = [main[0]]
        if rng.random() < 0.6:                               # a second tag, usually of another family
            other = rng.choice([g for g in _GENRES if g[2] <= start <= g[3] and g[0] != main[0]])
            genres.append(other[0])
        if rng.random() < 0.15:
            genres.append(rng.choice(options)[0])
        popularity = max(3, min(95, int(rng.betavariate(2.4, 2.6) * 100)))
        artists.append(_Artist(ids.new(), name, list(dict.fromkeys(genres)), family, popularity, start))
    for a in rng.sample(artists, NO_GENRE_ARTISTS):          # planted: artists with no genres at all
        a.shown_genres = False
    return artists


def _duration_ms(rng: random.Random, family: str, genres: list[str]) -> int:
    long = family in ("Electronic", "Classical") or "ambient" in genres or "jazz fusion" in genres
    seconds = rng.gauss(300 if long else 214, 80 if long else 42)
    return int(max(95, min(540, seconds)) * 1000)


def _date_in(rng: random.Random, year: int) -> str:
    return f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"


def _make_catalog(rng: random.Random, ids: _Ids, artists: list[_Artist]) -> list[_Song]:
    songs, titles = [], set()
    for a in artists:
        end = min(2025, a.start + rng.randint(0, 16))
        years = sorted(rng.sample(range(a.start, end + 1), min(end - a.start + 1, rng.choice([1, 2, 2, 3]))))
        for year in years:
            album, album_id, album_date = _album_title(rng), ids.new(), _date_in(rng, year)
            for _ in range(rng.randint(3, 6)):
                title = _song_title(rng)
                if title.casefold() in titles or _famous_song(title):
                    continue
                titles.add(title.casefold())
                pop = max(1, min(100, int(a.popularity + rng.gauss(0, 12))))
                credited = [(a.id, a.name)]
                if rng.random() < 0.08:
                    other = rng.choice(artists)
                    if other.id != a.id:
                        credited.append((other.id, other.name))
                songs.append(_Song(
                    track={"id": ids.new(), "name": title, "album": album, "album_id": album_id, "release_date": album_date,
                           "duration_ms": _duration_ms(rng, a.family, a.genres),
                           "explicit": int(rng.random() < (0.4 if a.family == "Hip-Hop" else 0.05)),
                           "popularity": pop, "isrc": _isrc(rng, year), "url": None},
                    artists=credited, year=year, family=a.family, genres=a.genres))
    return songs


def _isrc(rng: random.Random, year: int) -> str:
    # "ZZ" is no country: these can't be the code of a real recording.
    return f"ZZ{''.join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(3))}{year % 100:02d}{rng.randint(0, 99999):05d}"


def _copy_song(rng: random.Random, ids: _Ids, s: _Song, name: str, album: str, year: int, kind: str) -> _Song:
    """Another release of the same song: new IDs, same artists."""
    track = {**s.track, "id": ids.new(), "name": name, "album": album, "album_id": ids.new(),
             "release_date": _date_in(rng, year), "isrc": _isrc(rng, year),
             "popularity": max(1, min(100, int(s.track["popularity"] + rng.gauss(-4, 8))))}
    return _Song(track, list(s.artists), s.year, s.family, s.genres, kind)


def _pick(rng: random.Random, catalog: list[_Song], n: int, weight, exclude: set[str] = frozenset()) -> list[_Song]:
    """n distinct songs, weighted random (Efraimidis-Spirakis); weight 0 never."""
    scored = []
    for s in catalog:
        w = weight(s)
        draw = rng.random()
        if w > 0 and s.track["id"] not in exclude:
            scored.append((draw ** (1.0 / w), s.track["id"], s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [s for _, _, s in scored[:n]]


def _stamp(moment: datetime.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dates(rng: random.Random, n: int, style: str) -> list[str]:
    """n sorted added_at stamps. grow: a slow start and more every year; single: one afternoon; bursts: a few big days."""
    if style == "single":
        moment = datetime.datetime(2024, 5, 11, 10, 5, 0)
        out = []
        for _ in range(n):
            moment += datetime.timedelta(seconds=rng.randint(8, 95))
            out.append(moment)
        return [_stamp(m) for m in out]
    first = datetime.datetime(2018, 4, 1) if style == "grow" else datetime.datetime(2019, 1, 1)
    span = int((datetime.datetime(2025, 10, 1) - first).total_seconds())
    out = []
    bursts = [first + datetime.timedelta(seconds=rng.randrange(span)) for _ in range(rng.randint(3, 5))]
    for _ in range(n):
        if style == "grow":
            out.append(first + datetime.timedelta(seconds=int(span * rng.random() ** 0.55)))
        elif rng.random() < 0.55:
            out.append(rng.choice(bursts) + datetime.timedelta(seconds=rng.randint(0, 3 * 3600)))
        else:
            out.append(first + datetime.timedelta(seconds=rng.randrange(span)))
    return [_stamp(m) for m in sorted(out)]


def _generate(seed: int = SEED) -> Collection:
    rng = random.Random(seed)
    ids = _Ids(rng)
    artists = _make_artists(rng, ids)
    catalog = _make_catalog(rng, ids, artists)
    by_id = {a.id: a for a in artists}

    def fit(families, lo=1960, hi=2025, prefer=()):
        def weight(s: _Song) -> float:
            if s.family not in families or not lo <= s.year <= hi:
                return 0.015
            return 1.0 + (2.0 if any(g in s.genres for g in prefer) else 0.0)
        return weight

    # name -> (size, weight, date style, followers)
    plan = {
        "Road trip": (118, fit({"Rock", "Pop", "Folk"}, 1968, 2019), "grow", 3),
        "Sunday morning": (66, fit({"Folk", "Jazz", "Classical"}, prefer=("singer-songwriter", "folk", "classical")), "bursts", 1),
        "Workout": (82, fit({"Electronic", "Hip-Hop", "Rock"}, 1995, 2025, ("hip hop", "techno", "house")), "single", None),
        "Late night jazz": (58, fit({"Jazz", "Hip-Hop", "Latin"}, 1960, 1998, ("jazz", "bossa nova", "soul", "blues")), "grow", 8),
        "90s forever": (98, fit({"Rock", "Pop", "Hip-Hop", "Electronic", "Reggae"}, 1990, 1999), "bursts", None),
        "Deep focus": (68, fit({"Electronic", "Classical"}, 1975, 2025, ("ambient", "downtempo", "neoclassical")), "bursts", 2),
        "Dance floor": (90, fit({"Pop", "Electronic", "Hip-Hop", "Latin", "Reggae"}, 1975, 2025, ("disco", "house", "latin", "dancehall")),
                        "bursts", 5),
    }
    seqs: dict[str, list[_Song]] = {}
    for name, (size, weight, _, _) in plan.items():
        seqs[name] = _pick(rng, catalog, size, weight)
        rng.shuffle(seqs[name])

    # Planted: compilation albums of two older artists, in "Road trip". Their tracks carry the album's year.
    veterans = [a for a in artists if a.start <= 1982 and a.family in ("Rock", "Pop", "Folk") and a.shown_genres]
    compilations: list[_Song] = []
    for a, (album, album_year) in zip(rng.sample(veterans, 2), (("Greatest Hits", 2004), (None, 2012))):
        album = album or f"The Very Best of {a.name}"
        album_id, album_date = ids.new(), _date_in(rng, album_year)
        for _ in range(10):
            title = _song_title(rng)
            real_year = a.start + rng.randint(0, 12)
            track = {"id": ids.new(), "name": title, "album": album, "album_id": album_id, "release_date": album_date,
                     "duration_ms": _duration_ms(rng, a.family, a.genres), "explicit": 0,
                     "popularity": max(5, min(100, int(a.popularity + rng.gauss(8, 10)))), "isrc": _isrc(rng, real_year), "url": None}
            compilations.append(_Song(track, [(a.id, a.name)], real_year, a.family, a.genres, "compilation"))
    for s in compilations:
        seqs["Road trip"].insert(rng.randrange(len(seqs["Road trip"]) + 1), s)

    # Planted: "Summer drive", 90% the same tracks as "Road trip": a near-identical pair.
    road = [s for s in dict.fromkeys(seqs["Road trip"]) if s.kind == "song"]
    seqs["Summer drive"] = rng.sample(road, 46)
    seqs["Summer drive"] += _pick(rng, catalog, 5, fit({"Rock", "Pop"}, 1968, 2019), {s.track["id"] for s in seqs["Summer drive"]})
    rng.shuffle(seqs["Summer drive"])
    plan["Summer drive"] = (0, None, "bursts", None)

    # Planted: other versions of a song saved in another playlist (remasters) and live takes.
    names = list(seqs)
    originals = [s for name in names if name != "Summer drive" for s in seqs[name] if s.kind == "song"]
    originals = list(dict.fromkeys(originals))
    versions: list[tuple[_Song, str]] = []
    for original, kind in zip(rng.sample(originals, 8), ["remaster"] * 6 + ["live"] * 2):
        home = next(n for n in names if original in seqs[n])
        if kind == "remaster":
            year = rng.choice([2009, 2011, 2015, 2019, 2021])
            other = _copy_song(rng, ids, original, f"{original.track['name']} - Remastered {year}",
                               f"{original.track['album']} ({year} Remaster)", year, kind)
        else:
            other = _copy_song(rng, ids, original, f"{original.track['name']} - Live",
                               f"Live at {rng.choice(_PLACE).replace('the ', 'the ')}", min(2025, original.year + rng.randint(2, 9)), kind)
        away = rng.choice([n for n in names if n not in (home, "Summer drive")])
        seqs[away].insert(rng.randrange(len(seqs[away]) + 1), other)
        versions.append((other, away))

    # Planted: the same track twice in one playlist.
    duplicated: list[tuple[str, _Song]] = []
    for name, count in (("Road trip", 4), ("Dance floor", 2)):
        for s in rng.sample([s for s in seqs[name] if s.kind == "song"], count):
            seqs[name].insert(rng.randrange(seqs[name].index(s) + 1, len(seqs[name]) + 1), s)
            duplicated.append((name, s))

    # Planted: popularity-0 tracks (replaced by a current version, unavailable, or just unpopular).
    relinks: dict[str, dict] = {}
    pool = [s for s in originals if s.kind == "song" and s not in {v for v, _ in versions}]
    flagged = rng.sample(pool, 9)
    for i, s in enumerate(flagged):
        s.track["popularity"] = 0
        if i < 6:
            relinks[s.track["id"]] = {"current_id": ids.new(), "current_popularity": rng.randint(25, 70),
                                      "current_album": f"{s.track['album']} (Deluxe Edition)", "current_url": None}
        elif i < 8:
            relinks[s.track["id"]] = {"unavailable": True}
        else:
            relinks[s.track["id"]] = {"unavailable": False}

    # "Liked Songs": songs from the playlists plus ones that are in none of them (orphans).
    in_playlists = {s.track["id"] for q in seqs.values() for s in q}
    liked = rng.sample(list(dict.fromkeys(s for q in seqs.values() for s in q if s.kind in ("song", "compilation"))), 48)
    orphans = _pick(rng, catalog, 48, lambda s: 1.0, in_playlists)
    liked = liked + orphans
    rng.shuffle(liked)

    # Rows with their dates.
    playlists: list[tuple[dict, list[dict]]] = []

    def meta(pid: str, name: str, followers: int | None) -> dict:
        out = {"id": pid, "name": name, "owner": {"id": USER_ID, "display_name": USER_NAME}, "snapshot_id": f"demo-{seed}"}
        if followers is not None:
            out["followers"] = {"total": followers}
        return out

    for name in ("Road trip", "Summer drive", "Sunday morning", "Workout", "Late night jazz", "90s forever", "Deep focus", "Dance floor"):
        _, _, style, followers = plan[name]
        stamps = _dates(rng, len(seqs[name]), style)
        rows = [{"track": s.track, "artists": s.artists, "added_at": d} for s, d in zip(seqs[name], stamps)]
        playlists.append((meta(ids.new(), name, followers), rows))
    stamps = sorted(_dates(rng, len(liked), "bursts"), reverse=True)              # Liked Songs lists the newest first
    playlists.append((meta(store.LIKED_ID, store.LIKED_NAME, None),
                      [{"track": s.track, "artists": s.artists, "added_at": d} for s, d in zip(liked, stamps)]))

    # The original year of every song (what fill_years would have found), except the compilations' tracks.
    from tailorlist_mcp import years as years_mod
    years: dict[tuple[str, str], int | None] = {}
    placed = {row["track"]["id"] for _, rows in playlists for row in rows}      # no years for songs in no playlist
    for s in catalog + [v for v, _ in versions] + compilations:
        if s.kind == "live" or s.track["id"] not in placed:
            continue                                   # live takes keep their album year, fill_years skips them
        key = years_mod.song_of(s.track["name"], s.artists[0][1])
        if key:
            years[key] = None if s.kind == "compilation" else s.year
    details = [{"id": a.id, "genres": a.genres if a.shown_genres else [], "popularity": a.popularity} for a in artists]
    return Collection(
        playlists=playlists, artists=details, no_genres=[a.id for a in artists if not a.shown_genres],
        years=list(years.items()), relinks=relinks,
        facts={"compilation_albums": sorted({s.track["album"] for s in compilations}),
               "versions": [(v.track["name"], away) for v, away in versions],
               "duplicated": [(n, s.track["name"]) for n, s in duplicated],
               "replaced": [t for t, r in relinks.items() if r.get("current_id")],
               "unavailable": [t for t, r in relinks.items() if r.get("unavailable")],
               "orphans": [s.track["id"] for s in orphans], "artists": {a.id: a.name for a in artists}})


def build(seed: int = SEED) -> list[tuple[dict, list[dict]]]:
    """The demo playlists as (playlist, rows) in the shapes store.save_playlist takes. Deterministic."""
    return _generate(seed).playlists


# ---------------------------------------------------------------- the cache

def _is_demo_row(pid: str, owner_id: str | None) -> bool:
    return pid.startswith(DEMO_PREFIX) or (pid == store.LIKED_ID and owner_id == USER_ID)


def _demo_ids(conn: sqlite3.Connection) -> list[str]:
    return [r["id"] for r in conn.execute("SELECT id, owner_id FROM playlists") if _is_demo_row(r["id"], r["owner_id"])]


def is_demo(conn: sqlite3.Connection) -> bool:
    """Whether the cache holds the demo collection."""
    return bool(_demo_ids(conn))


def has_real_playlists(conn: sqlite3.Connection) -> bool:
    demo = set(_demo_ids(conn))
    return any(r["id"] not in demo for r in conn.execute("SELECT id FROM playlists"))


def is_demo_id(value: str) -> bool:
    """A demo playlist or track ID (or a link to one)."""
    return DEMO_PREFIX in value and any(part.startswith(DEMO_PREFIX) and len(part) >= 22 for part in
                                         value.replace(":", "/").replace("?", "/").split("/"))


def clear(conn: sqlite3.Connection) -> bool:
    """Remove the demo playlists, their tracks and artists, and the drafts, progress and years that refer to them.

    Returns whether there was a demo. Real playlists are not touched."""
    ids = _demo_ids(conn)
    if not ids:
        return False
    from tailorlist_mcp import drafts, years as years_mod
    marks = ",".join("?" * len(ids))
    keys = []
    for r in conn.execute(
        "SELECT DISTINCT t.name, a.name AS artist FROM tracks t JOIN playlist_tracks pt ON pt.track_id = t.id "
        "JOIN track_artists ta ON ta.track_id = t.id AND ta.position = 0 JOIN artists a ON a.id = ta.artist_id "
        f"WHERE pt.playlist_id IN ({marks})", ids):
        key = years_mod.song_of(r["name"], r["artist"])
        if key:
            keys.append(key)
    store.remove_playlists(conn, ids)
    with conn:
        conn.executemany("DELETE FROM original_years WHERE artist = ? AND song = ?", keys)
        conn.execute("DELETE FROM cluster_progress WHERE track_id NOT IN (SELECT id FROM tracks)")
        if not conn.execute("SELECT 1 FROM playlists LIMIT 1").fetchone():
            conn.execute("DELETE FROM drafts")             # nothing else is loaded: every draft was made from the demo
    drafts.prune_unloaded_tracks(conn)
    return True


def load(conn: sqlite3.Connection, seed: int = SEED) -> dict:
    """Fill the cache with the demo collection (again, if it is already there). Raises DemoError over real playlists."""
    if has_real_playlists(conn):
        raise DemoError(REAL_LOADED)
    clear(conn)
    data = _generate(seed)
    now = int(time.time())
    for playlist, rows in data.playlists:
        store.save_playlist(conn, playlist, rows, 0)
    store.save_artist_details(conn, data.artists)
    for artist_id in data.no_genres:                       # "looked up in MusicBrainz, found nothing"
        store.save_mb_result(conn, artist_id, [], None, now)
    from tailorlist_mcp import years as years_mod
    for key, year in data.years:
        years_mod.save(conn, key, year, now, "search")
    store.save_relinks(conn, data.relinks, now)
    return {"playlists": [{"id": p["id"], "name": p["name"], "tracks": len(rows)} for p, rows in data.playlists]}


def listing(conn: sqlite3.Connection) -> dict:
    """list_my_playlists for the demo (same shape as collection.list_playlists), Liked Songs first."""
    rows = conn.execute(
        "SELECT p.id, p.name, COUNT(pt.track_id) AS tracks FROM playlists p LEFT JOIN playlist_tracks pt ON pt.playlist_id = p.id "
        "GROUP BY p.id ORDER BY p.id = ? DESC, p.name", (store.LIKED_ID,)).fetchall()
    return {"user": USER_NAME, "demo": True,
            "note": "This is the demo collection (invented artists and songs), already loaded. connect_spotify switches to your own library.",
            "playlists": [{"id": r["id"], "name": r["name"], "tracks": r["tracks"], "mine": True, "owner": USER_NAME,
                           "cache": "current", "url": None} for r in rows]}


def status(conn: sqlite3.Connection) -> str:
    """The line that tells the user this is the demo."""
    playlists = conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]
    tracks = conn.execute("SELECT COUNT(DISTINCT track_id) FROM playlist_tracks").fetchone()[0]
    return (f"Demo collection loaded (invented artists and songs): {playlists:,} playlists, {tracks:,} tracks. "
            "connect_spotify switches to your own library.")

