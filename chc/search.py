"""Find songs online.

- community_charts: charts people already made, from Chorus (enchor.us), the Clone Hero chart
  search site. A real chart beats a generated one, so the app shows these first.
- song_details / cover_art: album, year, genre and album cover from MusicBrainz and the Cover
  Art Archive, for song.ini and album.jpg.
- youtube_search / youtube_download: find a song on YouTube and fetch only its audio (yt-dlp).
  Downloading from YouTube is against YouTube's terms of service; that choice is the user's.

Every call needs the internet and may fail; callers treat failures as "nothing found".
"""
import difflib
import json
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

UA = {"User-Agent": "RockWizard/0.1 (https://github.com/mastroja/rock-wizard)"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _request(url, data=None, timeout=12, headers=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={**UA, **({"Content-Type": "application/json"} if data is not None else {}),
                                          **(headers or {})},
                                 method="POST" if data is not None else "GET")
    return urllib.request.urlopen(req, timeout=timeout)


def _json(url, data=None, timeout=12):
    with _request(url, data, timeout) as r:
        return json.load(r)


def normalize(s: str) -> str:
    """Lower case, without (tags) / [tags], punctuation or extra spaces, for comparing names."""
    s = re.sub(r"[\(\[].*?[\)\]]", " ", (s or "").lower())
    s = re.sub(r"[^a-z0-9]+", " ", s.replace("&", " and "))
    return " ".join(s.split())


def similar(a: str, b: str) -> float:
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def parse_query(q: str) -> tuple[str, str]:
    """'Artist - Title' -> (artist, title); a bare search is all title."""
    if " - " in q:
        a, t = q.split(" - ", 1)
        return a.strip(), t.strip()
    return "", q.strip()


def is_url(s: str) -> bool:
    return bool(re.match(r"https?://\S+$", (s or "").strip()))


# ---------- community charts (Chorus / enchor.us) ----------
@dataclass
class Chart:
    name: str
    artist: str
    charter: str
    md5: str
    album: str = ""
    year: str = ""
    seconds: float = 0.0
    parts: str = ""  # e.g. "guitar, bass, drums"
    score: float = 0.0

    @property
    def download_url(self) -> str:
        return f"https://files.enchor.us/{self.md5}.sng"


def community_charts(artist: str, title: str, limit: int = 5) -> list[Chart]:
    """Existing charts of this song, best matches first."""
    if artist:
        body = {"name": {"value": title, "exact": False, "exclude": False},
                "artist": {"value": artist, "exact": False, "exclude": False}, "page": 1}
        res = _json("https://api.enchor.us/search/advanced", body)
    else:
        res = _json("https://api.enchor.us/search", {"search": title, "page": 1})
    out, seen = [], set()
    for c in res.get("data") or []:
        name_s = similar(c.get("name", ""), title)
        art_s = similar(c.get("artist", ""), artist) if artist else 1.0
        if name_s < 0.75 or art_s < 0.6 or c.get("md5") in seen:
            continue
        seen.add(c.get("md5"))
        parts = [p for p, k in (("guitar", "diff_guitar"), ("bass", "diff_bass"), ("drums", "diff_drums"),
                                ("vocals", "diff_vocals"), ("keys", "diff_keys"))
                 if (c.get(k) if c.get(k) is not None else -1) >= 0]
        out.append(Chart(c.get("name", ""), c.get("artist", ""), c.get("charter", "") or "unknown",
                         c["md5"], c.get("album") or "", str(c.get("year") or ""),
                         (c.get("song_length") or 0) / 1000.0, ", ".join(parts), name_s + art_s))
    out.sort(key=lambda c: -c.score)
    return out[:limit]


def download_chart(chart: Chart, songs_dir: Path) -> Path:
    """Save the chart's .sng file (which Clone Hero reads directly) into the songs folder."""
    safe = re.sub(r'[<>:"/\\|?*]', "", f"{chart.artist} - {chart.name} ({chart.charter})").strip()
    dest = songs_dir / f"{safe}.sng"
    tmp = dest.with_suffix(".part")
    songs_dir.mkdir(parents=True, exist_ok=True)
    with _request(chart.download_url, timeout=60) as r, open(tmp, "wb") as f:
        while True:
            block = r.read(1 << 16)
            if not block:
                break
            f.write(block)
    tmp.replace(dest)
    return dest


# ---------- song details (MusicBrainz + Cover Art Archive) ----------
@dataclass
class Details:
    title: str
    artist: str
    album: str = ""
    year: str = ""
    genre: str = ""
    seconds: float = 0.0
    release_group: str = ""


_mb_lock = threading.Lock()
_mb_last = [0.0]


def _mb(path: str, **params):
    """MusicBrainz allows one request per second per app, and answers 503 when it is busy:
    space requests out and retry a few times with a growing wait."""
    url = f"https://musicbrainz.org/ws/2/{path}?" + urllib.parse.urlencode({**params, "fmt": "json"})
    for attempt in range(4):
        with _mb_lock:
            wait = 1.1 - (time.time() - _mb_last[0])
            if wait > 0:
                time.sleep(wait)
            _mb_last[0] = time.time()
        try:
            return _json(url)
        except urllib.error.HTTPError as e:
            if e.code != 503 or attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))


def _straight(s: str) -> str:
    """Typographic quotes and dashes as the plain ones people type."""
    return (s or "").translate(str.maketrans("‘’‚′“”„‐‑‒", "''''\"\"\"---"))


def _spelled(s: str) -> str:
    """A name's letters and digits, tags in brackets included: 'Free Fallin’' is "Free Fallin'",
    'Song (live)' is not 'Song'."""
    return re.sub(r"[^a-z0-9]+", " ", _straight(s).lower().replace("&", " and ")).strip()


VARIANT = ("live", "demo", "remix", "edit", "acoustic", "instrumental", "rehears", "session", "karaoke",
           "re-record", "rerecord", "alternate", "unplugged", "radio")
ORIGINAL = ("album", "original", "studio", "single", "lp", "stereo", "explicit", "clean")


def _variant(note: str) -> bool:
    """Does MusicBrainz's note on a recording ('live, 1994: Tipperary', 'album version') mark it
    as something other than the song as first released?"""
    note = note.lower()
    if any(w in note for w in VARIANT):
        return True
    return ("version" in note or "mix" in note) and not any(w in note for w in ORIGINAL)


MB_PAGES = 3  # of 100 recordings: classics have hundreds, and the original must be among those ranked


def _recordings(query: str) -> list:
    """Every recording a MusicBrainz search finds, up to MB_PAGES pages."""
    out = []
    for page in range(MB_PAGES):
        res = _mb("recording", query=query, limit=100, offset=100 * page)
        out += res.get("recordings") or []
        if len(out) >= (res.get("count") or 0) or not res.get("recordings"):
            break
    return out


def song_details(artist: str, title: str, artist_match: float = 0.6) -> Details | None:
    """The song's original studio release: album, year, genre, length. Without an artist it is
    the earliest song of that name by anyone, which is rarely the one meant: see resolve().
    artist_match is how close the recording's artist must be to the one asked for."""
    if not title:
        return None
    # 'The Black Cat Bones' is listed as 'Black Cat Bones' (and the other way round): try both
    names = [artist] if artist else [""]
    if artist.lower().startswith("the "):
        names.append(artist[4:])
    elif artist:
        names.append("The " + artist)
    # recordings on an official album first, then anything; the ranking below picks the original.
    # (Not 'AND NOT secondarytype:compilation' and the like: that drops every recording that is
    # on any compilation at all, which for a well-known song is the original itself, and leaves
    # only its demos and live versions.)
    recs = []
    for name in names:
        base = f'recording:"{title}"' + (f' AND artist:"{name}"' if name else "")
        for q in (base + " AND status:official AND primarytype:album", base):
            recs = _recordings(q)
            if recs:
                break
        if recs:
            break
    fits = []
    for r in recs:
        credit = (r.get("artist-credit") or [{}])[0].get("name", "")
        if similar(r.get("title", ""), title) >= 0.8 and (not artist or similar(credit, artist) >= artist_match):
            fits.append((r, credit))
    most = max((len(r.get("releases") or []) for r, _ in fits), default=0)
    best = None
    for r, credit in fits:
        odd = _variant(r.get("disambiguation") or "")
        # '(Quadrophonic mix)', '(Remastered)'...: a variant unless the search asked for it
        tagged = _spelled(r.get("title", "")) != _spelled(title)
        # the original has been reissued over and over; a recording on a handful of releases
        # next to one on hundreds is some other take, whatever date it carries
        rare = len(r.get("releases") or []) * 4 < most
        first = r.get("first-release-date") or "9999"
        for rel in r.get("releases") or []:
            rg = rel.get("release-group") or {}
            # the earliest-released recording (the original), on an official studio album
            rank = (bool(artist) and normalize(credit) != normalize(artist), odd, tagged, rare, first,
                    0 if rel.get("status") == "Official" else 1,
                    0 if rg.get("primary-type") == "Album" and not rg.get("secondary-types") else 1,
                    rel.get("date") or "9999")
            if best is None or rank < best[0]:
                best = (rank, r, rel, credit)
    if best is None:
        return None
    _, r, rel, credit = best
    rg = rel.get("release-group") or {}
    rec_title = _straight(r.get("title", title))
    if "(" not in title and "[" not in title:
        rec_title = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]\s*$", "", rec_title) or rec_title  # drop '(Quadrophonic mix)'
    d = Details(rec_title, _straight(credit) or artist, rel.get("title", ""),
                (rel.get("date") or "")[:4], "", (r.get("length") or 0) / 1000.0, rg.get("id", ""))
    try:
        if d.release_group:
            rg = _mb(f"release-group/{d.release_group}", inc="genres")
            g = rg.get("genres") or []
            if g:
                d.genre = max(g, key=lambda x: x.get("count", 0))["name"].title()
            if rg.get("first-release-date"):
                d.year = rg["first-release-date"][:4]  # the album's original year, not a reissue's
    except Exception:
        pass
    return d


def lead_artist(artist: str) -> str:
    """'Peso Pluma, Gabito Ballesteros, Junior H', 'A feat. B', 'A x B' -> the first act named."""
    first = re.split(r"\s*(?:[,;]|\bfeat\b\.?|\bft\b\.?|\bfeaturing\b|\s[x×]\s)\s*", artist or "", maxsplit=1, flags=re.I)[0]
    return first.strip() or (artist or "").strip()


def lookup(artist: str, title: str) -> Details | None:
    """Song details for (artist, title). 'Song - Artist' names are tried the other way round
    too, so take the names from the result: it has them in the right order. None when unknown
    or offline, and when there is no artist to go by (resolve() handles that)."""
    if not artist or not title:
        return None
    try:
        d = song_details(artist, title)
        if not d and lead_artist(artist) != artist:
            d = song_details(lead_artist(artist), title)
        # swapped: only on a near-exact artist, or any song that shares its name with a band fits
        return d or song_details(title, artist, artist_match=0.85)
    except Exception:
        return None


# words in a video or file name that are about the upload, not the song
NOISE = {"official", "video", "audio", "lyrics", "lyric", "hd", "hq", "4k", "music", "feat", "ft", "featuring",
         "and", "the", "x", "with", "by", "full", "song", "remastered", "remaster", "version", "visualizer",
         "explicit", "oficial", "clip", "mv"}


def _find_words(phrase: list, text: list) -> set | None:
    """Where each word of phrase is in text (a close spelling counts); None when one is missing."""
    used = set()
    for w in phrase:
        hit = next((i for i, t in enumerate(text) if i not in used and
                    (t == w or (len(w) > 3 and difflib.SequenceMatcher(None, w, t).ratio() >= 0.8))), None)
        if hit is None:
            return None
        used.add(hit)
    return used


def identify(text: str, seconds: float = 0.0, guess: bool = False) -> tuple[str, str] | None:
    """(artist, title) of the song a loose name means: 'them changes thundercat', 'Zombie - The
    Cranberries lyrics', 'Nickleback Rockstar'. Asks Apple's iTunes catalogue, which takes words
    in any order and lists well-known songs first, and accepts a song only when its title and
    artist are both in the text with next to nothing left over.
    A text that is a title and nothing else could be anyone's song: it is accepted only when the
    song's length matches `seconds`, or when `guess` asks for the best-known song of that name."""
    plain = re.sub(r"[\(\[].*?[\)\]]", " ", _straight(text))
    words = normalize(plain).split()
    if not words:
        return None
    q = urllib.parse.urlencode({"term": " ".join(re.sub(r"[-–—|:\"/]+", " ", plain).split()), "entity": "song", "limit": 25})
    items = _json(f"https://itunes.apple.com/search?{q}").get("results", [])
    both, alone = [], []
    for n, it in enumerate(items):
        name, by = it.get("trackName") or "", it.get("artistName") or ""
        at = _find_words(normalize(name).split(), words)
        if not name or not by or at is None:
            continue
        rest = [w for i, w in enumerate(words) if i not in at]
        by_at = _find_words(normalize(lead_artist(by)).split(), rest)
        if by_at is not None:
            others = set(normalize(by).split()) | NOISE
            both.append((sum(1 for i, w in enumerate(rest) if i not in by_at and w not in others), n, by, name))
        elif not [w for w in rest if w not in NOISE]:
            length = (it.get("trackTimeMillis") or 0) / 1000.0
            if guess and not seconds or seconds and length and abs(length - seconds) <= 12:
                alone.append((n, by, name))
    if both and min(both)[0] <= 2:
        _, _, by, name = min(both)
    elif alone:
        _, by, name = min(alone)
    else:
        return None
    if "(" not in text and "[" not in text:
        name = re.sub(r"(\s*[\(\[][^\)\]]*[\)\]])+\s*$", "", name) or name  # 'Song (feat. X) [Remastered]'
    return _straight(by), _straight(name)


def resolve(artist: str, title: str, seconds: float = 0.0, guess: bool = False,
            hint: str = "") -> tuple[str, str, Details | None]:
    """The proper (artist, title) and the song's details, from names that may be the wrong way
    round, misspelt, or missing the artist. `hint` is a name that might be the artist (the
    channel a video is on); `seconds` and `guess` are for a title with no artist (identify).
    Names nobody knows come back as given, with no details: never somebody else's album."""
    d = lookup(artist, title)
    if not d and not artist and hint:
        try:
            d = song_details(hint, title, artist_match=0.85)
        except Exception:
            d = None
    if d:
        return d.artist, d.title, d
    try:
        found = identify(f"{artist} {title}", seconds, guess)
    except Exception:
        found = None
    if not found:
        return artist, title, None
    d = lookup(*found)
    return (d.artist, d.title, d) if d else (*found, None)


def is_song(video: str, channel: str, artist: str, title: str) -> bool:
    """Is a video, going by its title and channel, this song by this artist?"""
    if not artist or not title:
        return False
    words = normalize(_straight(video)).split()
    at = _find_words(normalize(title).split(), words)
    if at is None:
        return False
    rest = [w for i, w in enumerate(words) if i not in at]
    return (_find_words(normalize(lead_artist(artist)).split(), rest) is not None
            or _compact(channel_artist(channel)) == _compact(artist))


OTHER_TAKE = re.compile(r"\b(live|remix|acoustic|karaoke|tribute|cover|instrumental|demo|re-?recorded)\b", re.I)


def sample(artist: str, title: str) -> dict | None:
    """A 30-second preview and the album cover of a song, from Apple's iTunes catalogue (which
    gets the artist and title, nothing else): {"art": cover url, "audio": preview url}. None
    when iTunes has no song that is clearly this one. Network errors are raised."""
    q = urllib.parse.urlencode({"term": f"{artist} {title}".strip(), "entity": "song", "limit": 25})
    best = None
    for n, it in enumerate(_json(f"https://itunes.apple.com/search?{q}").get("results", [])):
        name, by, art = it.get("trackName") or "", it.get("artistName") or "", it.get("artworkUrl100") or ""
        if not it.get("previewUrl") or not art:
            continue
        t = similar(name, title)
        a = max(similar(by, artist), similar(lead_artist(by), lead_artist(artist))) if artist else 1.0
        if t < 0.8 or a < 0.75:
            continue
        # the record itself over live takes, remixes and covers; iTunes lists the best known first
        other = OTHER_TAKE.search(f"{name} {it.get('collectionName') or ''}") and not OTHER_TAKE.search(title)
        score = (t + a - (0.5 if other else 0), -n)
        if best is None or score > best[0]:
            best = (score, {"art": art.replace("100x100bb", "200x200bb"), "audio": it["previewUrl"]})
    return best[1] if best else None


def cover_art(details: Details, dest: Path) -> Path | None:
    """Download the album cover (500 px) to dest; None when there is none."""
    if not details or not details.release_group:
        return None
    try:
        with _request(f"https://coverartarchive.org/release-group/{details.release_group}/front-500",
                      timeout=20) as r:
            data = r.read()
        dest.write_bytes(data)
        return dest
    except Exception:
        return None


@dataclass
class Cover:
    url: str
    thumb: str
    album: str
    artist: str
    year: str = ""
    source: str = ""
    best: bool = False


COVER_HOSTS = ("mzstatic.com", "coverartarchive.org", "archive.org")  # where save_cover may download from
ALBUM_MATCH = 0.85  # how close iTunes' album and artist names must be to the song's own


def _album_covers(artist: str, album: str) -> list[Cover]:
    """iTunes covers of exactly this album by this artist. A track can be missing from iTunes'
    song search (Childish Gambino's Redbone is) while its album is there. Searches songs because
    iTunes' album search misses albums too ("Awaken, My Love!")."""
    if not artist or not album:
        return []
    try:
        q = urllib.parse.urlencode({"term": f"{artist} {album}", "entity": "song", "limit": 25})
        items = _json(f"https://itunes.apple.com/search?{q}").get("results", [])
    except Exception:
        return []
    found = {}
    for it in items:
        art = it.get("artworkUrl100") or ""
        name, by = it.get("collectionName", ""), it.get("artistName", "")
        if art and similar(name, album) >= ALBUM_MATCH and similar(by, artist) >= ALBUM_MATCH:
            found.setdefault((name, by), Cover(art.replace("100x100bb", "600x600bb"), art.replace("100x100bb", "200x200bb"),
                                               name, by, (it.get("releaseDate") or "")[:4], "iTunes"))
    return list(found.values())


def cover_candidates(artist: str, title: str, n: int = 8, album: str = "") -> list[Cover]:
    """Album covers for a song, the likeliest first: the album the song is known to be on, when
    given, then Apple's iTunes catalogue (no key needed, covers almost everything) and the
    MusicBrainz release this app uses for album/year.
    MusicBrainz is slow (rate limited, several queries), so it runs alongside and gets a few seconds."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as Late
    known = _album_covers(artist, album)
    pool = ThreadPoolExecutor(1)
    mb_job = pool.submit(lookup, artist, title)
    pool.shutdown(wait=False)
    try:
        q = urllib.parse.urlencode({"term": f"{artist} {title}".strip(), "entity": "song", "limit": 25})
        items = _json(f"https://itunes.apple.com/search?{q}").get("results", [])
    except Exception:
        items = []
    want_t, want_a = normalize(title), normalize(artist)
    seen = set()
    scored = []
    for it in items:
        art = it.get("artworkUrl100") or ""
        album = it.get("collectionName", "")
        if not art or (album, it.get("artistName")) in seen:
            continue
        seen.add((album, it.get("artistName")))
        score = similar(it.get("trackName", ""), title) + (similar(it.get("artistName", ""), artist) if artist else 0.5)
        # the original album over compilations, live albums and deluxe repackages
        if re.search(r"\b(greatest hits|best of|live|collection|essentials|hits)\b", album.lower()):
            score -= 0.3
        if normalize(it.get("trackName", "")) == want_t and (not want_a or normalize(it.get("artistName", "")) == want_a):
            score += 0.5
        scored.append((score, Cover(art.replace("100x100bb", "600x600bb"), art.replace("100x100bb", "200x200bb"),
                                    album, it.get("artistName", ""), (it.get("releaseDate") or "")[:4], "iTunes")))
    scored.sort(key=lambda s: -s[0])
    urls = {c.url for c in known}
    out = known + [c for _, c in scored if c.url not in urls]
    sure = known or (scored and scored[0][0] >= 2.0)
    try:
        d = mb_job.result(timeout=1.5 if sure else 8)  # iTunes sure: barely wait
    except Late:
        d = None
    if d and d.release_group:
        base = f"https://coverartarchive.org/release-group/{d.release_group}/front"
        mb = Cover(base + "-500", base + "-250", d.album, d.artist, d.year, "MusicBrainz")
        # MusicBrainz picks the original studio album, so it goes first when iTunes isn't sure
        if not sure:
            out.insert(0, mb)
        else:
            out.insert(1, mb)
    if out:
        out[0].best = True
    return out[:n]


def save_cover(url: str, folder: Path) -> Path:
    """Download a cover from one of COVER_HOSTS into folder/album.jpg (at most 600 px); an
    album.png is removed so Clone Hero shows the new one."""
    host = urllib.parse.urlparse(url).hostname or ""
    if not any(host == h or host.endswith("." + h) for h in COVER_HOSTS):
        raise ValueError(f"Covers can't be downloaded from {host}")
    with _request(url, timeout=20) as r:
        data = r.read()
    return write_cover(data, folder)


def write_cover(data: bytes, folder: Path) -> Path:
    """Save image bytes (any format Pillow reads) as folder/album.jpg, at most 600 px."""
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((600, 600))
    dest = Path(folder) / "album.jpg"
    img.save(dest, "JPEG", quality=90)
    (Path(folder) / "album.png").unlink(missing_ok=True)
    return dest


# ---------- YouTube (yt-dlp) ----------
@dataclass
class Video:
    title: str
    channel: str
    seconds: float
    url: str
    best: bool = False
    score: float = 0.0


BAD_WORDS = ("live", "cover", "karaoke", "instrumental", "remix", "reaction", "lesson", "tutorial",
             "sped up", "slowed", "8d", "nightcore", "acoustic", "backing track", "drum cam", "playthrough")


def youtube_search(artist: str, title: str, n: int = 5, expect_seconds: float = 0.0) -> list[Video]:
    """Top YouTube matches, the likeliest plain album recording first ('Artist - Topic' uploads
    and the artist's own channel rank high; other songs, live versions, covers etc. low)."""
    import yt_dlp
    query = f"{artist} {title}".strip()
    want = normalize(title)
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True,
                           "skip_download": True}) as y:
        info = y.extract_info(f"ytsearch{max(n * 2, 8)}:{query}", download=False)
    q = query.lower()
    out = []
    if not expect_seconds:
        # no known album length: trust the length most uploads of this song agree on
        # (music videos often add an intro or outro)
        lens = [float(e.get("duration") or 0) for e in info.get("entries") or []
                if e.get("duration") and want and want in normalize(e.get("title") or "")]
        if len(lens) >= 3:
            expect_seconds = float(np.median(lens))
    for e in info.get("entries") or []:
        title, channel = e.get("title") or "", e.get("channel") or e.get("uploader") or ""
        secs = float(e.get("duration") or 0)
        score = similar(title, query)
        if want and want not in normalize(title):
            score -= 1.5  # a different song (often from the same artist's channel)
        if channel.endswith(" - Topic"):
            score += 1.0  # YouTube's auto-generated album audio
        elif artist and similar(channel, artist) > 0.8:
            score += 0.6  # the artist's own channel
        score -= 0.8 * sum(1 for w in BAD_WORDS if w in title.lower() and w not in q)
        if expect_seconds and secs:
            score -= min(1.0, abs(secs - expect_seconds) / 30.0)  # far from the album length
        if secs and (secs < 60 or secs > 20 * 60):
            score -= 2
        url = e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}"
        out.append(Video(title, channel, secs, url, score=score))
    out.sort(key=lambda v: -v.score)
    out = out[:n]
    if out:
        out[0].best = True
    return out


def video_info(url: str) -> dict:
    """A video's title, channel and length in seconds, plus the song ('track') and 'artist'
    YouTube itself lists for it when it does; nothing is downloaded."""
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True,
                           "noplaylist": True}) as y:
        info = y.extract_info(url, download=False, process=False)
    artists = info.get("artists") or [info.get("artist") or info.get("creator") or ""]
    return {"title": info.get("title") or "", "channel": info.get("channel") or info.get("uploader") or "",
            "seconds": float(info.get("duration") or 0), "track": info.get("track") or "",
            "artist": lead_artist(artists[0] or "")}


def video_title(url: str) -> tuple[str, str]:
    """(title, channel) of a video, without downloading it."""
    info = video_info(url)
    return info["title"], info["channel"]


def names_from_info(info: dict) -> tuple[str, str]:
    """(artist, song) of a video_info: YouTube's own song listing when the video has one, or
    else a guess from the title and channel."""
    if info.get("track") and info.get("artist"):
        return info["artist"], clean_title(info["track"])
    return names_from_video(info.get("title", ""), info.get("channel", ""))


def _upload_tags_off(t: str) -> str:
    """A video title without what is about the upload: '(Official Video)', '[HD]', '| Lyrics',
    a trailing 'lyrics'. Every kind of dash between names becomes ' - '; quotes are kept."""
    t = re.sub(r"\s+(?:[–—―~]|-{2,}|//)\s+", " - ", _straight(t))
    t = re.sub(r"[\(\[][^\)\]]*(official|oficial|video|audio|lyric|letra|remaster|hd|hq|4k|visuali[sz]er|explicit)"
               r"[^\)\]]*[\)\]]", "", t, flags=re.I)
    t = re.sub(r"\[[^\]]*\]", "", t)  # [album], [HD]...: square brackets are never the song's name
    t = re.split(r"\s[|•]\s", t)[0]
    t = re.sub(r"(?:\s+-)?(?:\s+(?:with|official|music|lyrics?|hd|hq))*\s+(?:video|audio|lyrics|hd|hq)\s*$", "", t, flags=re.I)
    return " ".join(t.split())


def clean_title(t: str) -> str:
    """Video title -> song name: drop '(Official Video)', '[HD]', '| Lyrics' and similar."""
    return " ".join(_upload_tags_off(t).replace('"', "").split())


def _compact(s: str) -> str:
    return normalize(s).replace(" ", "")


def channel_artist(channel: str) -> str:
    """The act a channel is named after, if it is one: 'Weezer - Topic', 'TheKillersVEVO',
    'Tame Impala Official' -> 'Weezer', 'TheKillers', 'Tame Impala'."""
    c = (channel or "").removesuffix(" - Topic").strip()
    return re.sub(r"(?i)(\s*vevo|\s+official|\s+music|\s+band|\s+tv)+$", "", c).strip() or c


def names_from_file(stem: str) -> tuple[str, str]:
    """(artist, song) from an audio file name like 'Artist - Song - uploader (128k)'."""
    from .tidy import clean_name
    a, t = parse_query(stem.replace("_", " "))
    a, t = (clean_name(a) if a else ""), clean_name(t)
    parts = t.split(" - ")
    # converters often append ' - uploader': drop a last part that is one word or ALL CAPS
    if len(parts) > 1 and (" " not in parts[-1].strip() or parts[-1].isupper()):
        t = " - ".join(parts[:-1])
    return a, t


def names_from_video(title: str, channel: str) -> tuple[str, str]:
    """(artist, song) guessed from a video's title and channel."""
    raw = _upload_tags_off(title)
    artist = channel_artist(channel)
    quoted = re.match(r'^(.+?)\s*[-:]?\s*"([^"]{2,})"$', raw)  # Band "Song", Band - "Song"
    if quoted and " - " not in quoted.group(1):
        a, t = quoted.group(1).strip(), quoted.group(2).strip()
    else:
        a, t = parse_query(raw.replace('"', ""))
    if len(t) > 2 and t[0] == t[-1] == "'":
        t = t[1:-1].strip()  # 'Song' in single quotes; not the end of Free Fallin'
    if a and artist and _compact(t) == _compact(artist) and _compact(a) != _compact(artist):
        a, t = t, a  # 'Song - Artist' (reversed), told apart by the artist's own channel
    if not a and artist:  # 'Band Song' from the band's own channel
        parts = t.split()
        for n in range(1, min(len(parts), 6)):
            if _compact(" ".join(parts[:n])) == _compact(artist):
                a, t = " ".join(parts[:n]), " ".join(parts[n:]).strip(" -:")
                break
    if not a and channel.endswith(" - Topic"):
        a = artist  # Topic uploads are titled with the song alone
    return a, t


YOUTUBE_HOSTS = ("youtube.com", "youtu.be")
_streams: dict[str, tuple[float, str, dict]] = {}  # video address -> (found at, sound address, headers)


def open_sample(url: str, byte_range: str = ""):
    """An open connection to a YouTube video's sound, for playing a sample of a search result in
    the app: the response to pass on (status, headers, read()). Nothing is saved. `byte_range` is
    the browser's Range header, which lets it jump to the part it plays."""
    host = urllib.parse.urlparse(url).hostname or ""
    if not any(host == h or host.endswith("." + h) for h in YOUTUBE_HOSTS):
        raise ValueError("Only YouTube videos can be sampled")
    hit = _streams.get(url)
    for last_try in (False, True):
        if not hit or time.time() - hit[0] > 1800:
            import yt_dlp
            # a kind every browser plays as it comes: m4a sound, or else the small mp4
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
                                   "format": "bestaudio[ext=m4a]/18/best[ext=mp4]"}) as y:
                info = y.extract_info(url, download=False)
            hit = _streams[url] = (time.time(), info["url"], dict(info.get("http_headers") or {}))
        req = urllib.request.Request(hit[1], headers=hit[2] | ({"Range": byte_range} if byte_range else {}))
        try:
            return urllib.request.urlopen(req, timeout=20)
        except urllib.error.HTTPError:
            _streams.pop(url, None)  # the address went stale: look it up once more
            hit = None
            if last_try:
                raise


def youtube_download(url: str, dest_dir: Path, on_progress=lambda f: None,
                     stop: threading.Event | None = None) -> Path:
    """Download only the audio of a video into dest_dir; returns the file."""
    import yt_dlp
    dest_dir.mkdir(parents=True, exist_ok=True)

    def hook(d):
        if stop is not None and stop.is_set():
            raise yt_dlp.utils.DownloadCancelled("cancelled")
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                on_progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    opts = {"format": "bestaudio/best", "outtmpl": str(dest_dir / "download.%(ext)s"),
            "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
            "progress_hooks": [hook]}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
        path = Path(y.prepare_filename(info))
    on_progress(1.0)
    return path


def update_downloader() -> str:
    """Update yt-dlp (YouTube changes often break older versions). Returns a short message."""
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-U", "-q", "yt-dlp"],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        return "Update failed: " + (r.stderr.strip().splitlines() or ["unknown error"])[-1]
    v = subprocess.run([sys.executable, "-c", "import yt_dlp; print(yt_dlp.version.__version__)"],
                       capture_output=True, text=True, creationflags=NO_WINDOW).stdout.strip()
    return f"Downloader is up to date (version {v})."
