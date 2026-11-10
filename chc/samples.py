"""Song previews for the Create page: a 30-second clip and the album cover (search.sample), kept
on disk so a song is only asked for once in a while. iTunes allows about 20 searches a minute,
so requests are spaced out and the suggested songs are fetched ahead of time in the background."""
import json
import threading
import time

from . import library, search

FILE = library.config_path().with_name(".rockwizard_samples.json")
KEEP = 14 * 86400   # a found preview is looked up again after this long (the links can move)
RETRY = 3 * 86400   # a song iTunes didn't have is asked for again after this long
GAP = 3.2           # seconds between requests
_cache: dict | None = None
_cache_lock = threading.Lock()
_net_lock = threading.Lock()
_last = 0.0
_warming = False


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _key(artist: str, title: str) -> str:
    return f"{search.normalize(artist)}|{search.normalize(title)}"


def _entry(artist: str, title: str) -> dict | None:
    with _cache_lock:
        return _load().get(_key(artist, title))


def _due(artist: str, title: str) -> bool:
    """Whether to ask iTunes (again) about this song."""
    e = _entry(artist, title)
    return not e or time.time() - e.get("at", 0) > (KEEP if e.get("audio") else RETRY)


def cached(artist: str, title: str) -> dict | None:
    """{"art", "audio"} when known (an old one still serves while warm() renews it), {} when
    iTunes is known not to have it, None when not asked yet."""
    e = _entry(artist, title)
    if e and e.get("audio"):
        return {"art": e["art"], "audio": e["audio"]}
    return None if _due(artist, title) else {}


def get(artist: str, title: str, renew: bool = False) -> dict:
    """The preview for a song, from the cache or iTunes; {} when there is none (or iTunes is busy).
    `renew` asks iTunes again when the cached answer is old."""
    global _last
    with _net_lock:  # one request at a time, GAP apart
        hit = cached(artist, title)
        if hit is not None and not (renew and _due(artist, title)):
            return hit
        time.sleep(max(0.0, _last + GAP - time.time()))
        try:
            found = search.sample(artist, title) or {}
        except Exception:
            return hit or {}  # offline or asked too often: not remembered, so it is tried again
        finally:
            _last = time.time()
        with _cache_lock:
            _load()[_key(artist, title)] = found | {"at": time.time()}
            try:
                FILE.write_text(json.dumps(_cache), encoding="utf-8")
            except OSError:
                pass
        return found


def warm(songs: list[tuple[str, str]]) -> None:
    """Fetch the previews not known yet, slowly, in the background (one run at a time)."""
    global _warming
    todo = [s for s in songs if _due(*s)]
    if _warming or not todo:
        return
    _warming = True

    def run():
        global _warming
        try:
            for artist, title in todo:
                get(artist, title, renew=True)
                time.sleep(GAP)  # leaves room for the previews someone is waiting for
        finally:
            _warming = False
    threading.Thread(target=run, daemon=True).start()
