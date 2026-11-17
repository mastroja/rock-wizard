"""What friends do together in RockWizard, kept in one small file next to the settings:

- the activity feed (who added what, what is ready, what went live),
- each person's colour,
- the hall of shame (how often someone met the Taylor Swift checkpoint, and how it went),
- hearts (each person's favourite songs),
- tonight's setlist.

A person is a name (the same name on two phones is the same person), compared without case.
Songs are keyed by "artist|title" in lower case, so a renamed folder keeps its hearts.
"""
import json
import threading
import time
from pathlib import Path

PATH = Path.home() / ".rockwizard_social.json"
KEEP_EVENTS = 300
COLORS = ["#8b5cf6", "#ec4899", "#f59e0b", "#10b981", "#3b82f6", "#ef4444", "#14b8a6", "#f97316", "#a3e635", "#e879f9"]
SHAME_EVENTS = ("gate", "pass", "fail", "timeout")

_lock = threading.RLock()
_data: dict | None = None


def _d() -> dict:
    global _data
    if _data is None:
        try:
            _data = json.loads(PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _data = {}
        for key, empty in (("events", []), ("next_id", 1), ("rev", 0), ("profiles", {}), ("shame", {}),
                           ("hearts", {}), ("setlist", [])):
            _data.setdefault(key, empty)
    return _data


def _save(bump=True) -> None:
    d = _d()
    if bump:
        d["rev"] += 1
    tmp = PATH.with_suffix(".part")
    try:
        tmp.write_text(json.dumps(d), encoding="utf-8")
        tmp.replace(PATH)
    except OSError:
        pass


def key_of(name: str) -> str:
    return " ".join((name or "").lower().split())


def song_key(artist: str, title: str) -> str:
    return f"{key_of(artist)}|{key_of(title)}"


def rev() -> int:
    """Goes up whenever anything here changes, so pages know to look again."""
    with _lock:
        return _d()["rev"]


# ---------- activity ----------
def log(kind: str, who: str, text: str, **extra) -> dict:
    """kind: added, ready, failed, published, downloaded, shame, setlist. `text` reads after the name."""
    with _lock:
        d = _d()
        e = {"id": d["next_id"], "t": time.time(), "kind": kind, "who": who, "text": text} | extra
        d["next_id"] += 1
        d["events"] = (d["events"] + [e])[-KEEP_EVENTS:]
        _save()
        return e


def events(after: int = 0, limit: int = 60) -> list[dict]:
    with _lock:
        return [e for e in _d()["events"] if e["id"] > after][-limit:]


def last_id() -> int:
    with _lock:
        return _d()["next_id"] - 1


# ---------- people ----------
def color_of(name: str) -> str:
    with _lock:
        p = _d()["profiles"].get(key_of(name)) or {}
    if p.get("color"):
        return p["color"]
    return COLORS[sum(key_of(name).encode()) % len(COLORS)]


def set_color(name: str, color: str) -> str:
    if color not in COLORS:
        raise ValueError("pick one of the colours shown")
    with _lock:
        _d()["profiles"].setdefault(key_of(name), {})["color"] = color
        _save()
    return color


def shame(name: str, event: str) -> dict:
    if event not in SHAME_EVENTS:
        raise ValueError("unknown event")
    with _lock:
        row = _d()["shame"].setdefault(key_of(name), {})
        row[event] = row.get(event, 0) + 1
        _save()
        return dict(row)


def shame_of(name: str) -> dict:
    with _lock:
        row = _d()["shame"].get(key_of(name)) or {}
    return {e: row.get(e, 0) for e in SHAME_EVENTS}


# ---------- hearts ----------
def heart_toggle(name: str, key: str) -> bool:
    """Returns whether the song is now one of this person's favourites."""
    with _lock:
        mine = _d()["hearts"].setdefault(key_of(name), [])
        on = key not in mine
        mine.append(key) if on else mine.remove(key)
        _save(bump=False)
        return on


def hearts_of(name: str) -> list[str]:
    with _lock:
        return list(_d()["hearts"].get(key_of(name)) or [])


def heart_counts() -> dict:
    out = {}
    with _lock:
        for keys in _d()["hearts"].values():
            for k in keys:
                out[k] = out.get(k, 0) + 1
    return out


# ---------- tonight's setlist ----------
def setlist() -> list[dict]:
    with _lock:
        return [dict(s) for s in _d()["setlist"]]


def setlist_add(key: str, folder: str, title: str, artist: str, by: str) -> bool:
    """False when the song is already on the list."""
    with _lock:
        sl = _d()["setlist"]
        if any(s["key"] == key for s in sl):
            return False
        sl.append({"key": key, "folder": folder, "title": title, "artist": artist, "by": by,
                   "t": time.time(), "played": False})
        _save()
        return True


def setlist_get(key: str) -> dict | None:
    with _lock:
        return next((dict(s) for s in _d()["setlist"] if s["key"] == key), None)


def setlist_remove(key: str) -> None:
    with _lock:
        d = _d()
        d["setlist"] = [s for s in d["setlist"] if s["key"] != key]
        _save()


def setlist_played(key: str, played: bool) -> None:
    with _lock:
        for s in _d()["setlist"]:
            if s["key"] == key:
                s["played"] = bool(played)
        _save()


def setlist_move(key: str, step: int) -> None:
    with _lock:
        sl = _d()["setlist"]
        i = next((n for n, s in enumerate(sl) if s["key"] == key), None)
        if i is None or not 0 <= i + step < len(sl):
            return
        sl[i], sl[i + step] = sl[i + step], sl[i]
        _save()


def setlist_clear(played_only: bool = False) -> None:
    with _lock:
        d = _d()
        d["setlist"] = [s for s in d["setlist"] if played_only and not s["played"]]
        _save()
