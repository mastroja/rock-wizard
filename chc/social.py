"""What friends do together in RockWizard, kept in one small file next to the settings:

- the activity feed (who added what, what is ready, what went live),
- each person's colour,
- the hall of shame (how often someone met the Taylor Swift checkpoint, and how it went),
- hearts (each person's favourite songs),
- tonight's setlist,
- each person's avatar, built from a few simple parts and drawn by the web app,
- bands: a few people together, with their own set lists.

A person is a name (the same name on two phones is the same person), compared without case.
Songs are keyed by "artist|title" in lower case, so a renamed folder keeps its hearts.
"""
import json
import secrets
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
                           ("hearts", {}), ("setlist", []), ("bands", {})):
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


# An avatar is a choice from each list (the web app draws it; the background is the person's colour).
# The first choice is what an avatar saved before that part existed gets.
AVATAR_PARTS = {
    "face": ["round", "square", "long", "oval", "wide"],
    "skin": ["#f9d7b9", "#ffe0bd", "#eec09a", "#d9a074", "#c68642", "#b9784d", "#8d5a3b", "#5e3b28", "#3d2516",
             "#9ae6b4", "#a5b4fc", "#c4b5fd", "#93c5fd", "#fca5a5"],
    "hair": ["none", "short", "buzz", "long", "spiky", "mohawk", "curly", "afro", "bun", "ponytail", "pigtails",
             "side", "emo", "dreads"],
    "hair_color": ["#1f1a17", "#5b3a22", "#a0662f", "#e8c26b", "#d9d4cf", "#ffffff", "#64748b", "#c0392b", "#f97316",
                   "#ec4899", "#7c3aed", "#3b82f6", "#10b981"],
    "brows": ["none", "flat", "raised", "angry", "thick"],
    "eyes": ["dots", "happy", "wink", "big", "sleepy", "stars", "hearts", "angry", "lashes", "dizzy"],
    "mouth": ["smile", "grin", "open", "flat", "tongue", "teeth", "smirk", "o", "fangs", "sad"],
    "facial_hair": ["none", "stubble", "moustache", "goatee", "beard"],
    "glasses": ["none", "shades", "round", "stars", "hearts", "visor", "eyepatch"],
    "hat": ["none", "beanie", "cap", "cowboy", "crown", "horns", "halo", "headband", "headphones", "tophat", "wizard"],
    "top": ["tee", "hoodie", "jacket", "tank", "bandtee", "collar"],
    "top_color": ["#1f2937", "#f8fafc", "#ef4444", "#f97316", "#facc15", "#22c55e", "#3b82f6", "#8b5cf6", "#ec4899",
                  "#78350f"],
    "extra": ["none", "earrings", "nosering", "chain", "freckles", "blush", "facepaint", "scar"],
    "pattern": ["none", "rays", "dots", "stripes", "stars"],
}
_OLD_EXTRA = {"shades": ("glasses", "shades"), "headphones": ("hat", "headphones"), "headband": ("hat", "headband"),
              "crown": ("hat", "crown"), "beanie": ("hat", "beanie")}


def _avatar_clean(a: dict) -> dict:
    """Every part filled in and known; an avatar from before glasses and hats had their own lists
    keeps what it wore."""
    a = dict(a)
    if a.get("extra") in _OLD_EXTRA:
        k, v = _OLD_EXTRA[a.pop("extra")]
        a.setdefault(k, v)
    out = {k: str(a.get(k, opts[0])) for k, opts in AVATAR_PARTS.items()}
    if any(v not in AVATAR_PARTS[k] for k, v in out.items()):
        raise ValueError("unknown avatar part")
    return out


def avatar_of(name: str) -> dict | None:
    with _lock:
        a = (_d()["profiles"].get(key_of(name)) or {}).get("avatar")
    if not a:
        return None
    try:
        return _avatar_clean(a)
    except ValueError:
        return None


def set_avatar(name: str, avatar: dict | None) -> dict | None:
    """None goes back to the initials circle."""
    if avatar is not None:
        if not isinstance(avatar, dict):
            raise ValueError("unknown avatar")
        avatar = _avatar_clean(avatar)
    with _lock:
        prof = _d()["profiles"].setdefault(key_of(name), {})
        if avatar is None:
            prof.pop("avatar", None)
        else:
            prof["avatar"] = avatar
        _save()
    return avatar


def photo_of(name: str) -> int:
    """When this person's uploaded photo was saved (0: none). The photo itself is a file (server.py)."""
    with _lock:
        return int((_d()["profiles"].get(key_of(name)) or {}).get("photo") or 0)


def set_photo(name: str, version: int) -> None:
    with _lock:
        prof = _d()["profiles"].setdefault(key_of(name), {})
        if version:
            prof["photo"] = int(version)
        else:
            prof.pop("photo", None)
        _save()


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


# ---------- bands ----------
# A band's logo, like an avatar, is a choice from each list (the web app draws it with the band's name).
LOGO_COLORS = ["#ef4444", "#f97316", "#facc15", "#22c55e", "#14b8a6", "#3b82f6", "#8b5cf6", "#ec4899", "#f8fafc",
               "#9ca3af", "#111827", "#78350f"]
LOGO_PARTS = {
    "shape": ["circle", "shield", "hexagon", "diamond", "star", "banner", "burst", "none"],
    "icon": ["lightning", "guitar", "skull", "flame", "star", "crown", "wings", "drums", "mic", "note", "eye", "none"],
    "font": ["block", "metal", "serif", "script", "mono", "round", "stencil"],
    "effect": ["plain", "outline", "shadow", "glow", "gradient"],
    "color1": LOGO_COLORS, "color2": LOGO_COLORS, "text_color": LOGO_COLORS,
}


def _logo_clean(logo) -> dict:
    if not isinstance(logo, dict):
        raise ValueError("unknown logo")
    out = {k: str(logo.get(k, opts[0])) for k, opts in LOGO_PARTS.items()}
    if any(v not in LOGO_PARTS[k] for k, v in out.items()):
        raise ValueError("unknown logo part")
    return out


# {id: {"id", "name", "by" (who started it), "members": [names], "t", "logo": {...} or absent,
#       "setlists": [{"id", "name", "songs": [{"key", "folder", "title", "artist"}]}]}}
# Songs are found again by key, so a song that moved from staging to Quickplay stays on the list.
def _band(bid: str) -> dict:
    b = _d()["bands"].get(bid)
    if not b:
        raise KeyError("That band doesn't exist any more")
    return b


def _setlist(b: dict, sid: str) -> dict:
    s = next((s for s in b["setlists"] if s["id"] == sid), None)
    if not s:
        raise KeyError("That set list doesn't exist any more")
    return s


def _clean(name: str, what: str) -> str:
    name = " ".join(str(name or "").split())[:40]
    if not name:
        raise ValueError(f"Please type a name for the {what}")
    return name


def bands() -> list[dict]:
    with _lock:
        return json.loads(json.dumps(sorted(_d()["bands"].values(), key=lambda b: b["t"])))


def band(bid: str) -> dict:
    with _lock:
        return json.loads(json.dumps(_band(bid)))


def is_member(b: dict, name: str) -> bool:
    return key_of(name) in {key_of(m) for m in b["members"]}


def band_create(name: str, by: str) -> dict:
    with _lock:
        bid = secrets.token_hex(4)
        b = {"id": bid, "name": _clean(name, "band"), "by": by, "members": [by], "t": time.time(),
             "setlists": [{"id": secrets.token_hex(4), "name": "Main set", "songs": []}]}
        _d()["bands"][bid] = b
        _save()
        return json.loads(json.dumps(b))


def band_edit(bid: str, op: str, **a) -> None:
    """One change to a band; the server checks who may make it first."""
    with _lock:
        b = _band(bid)
        if op == "rename":
            b["name"] = _clean(a["name"], "band")
        elif op == "delete":
            del _d()["bands"][bid]
        elif op == "logo":
            if a.get("logo") is None:
                b.pop("logo", None)
            else:
                b["logo"] = _logo_clean(a["logo"])
        elif op == "add_member":
            if is_member(b, a["name"]):
                raise ValueError(f"{a['name']} is already in {b['name']}")
            b["members"].append(a["name"])
        elif op == "remove_member":
            b["members"] = [m for m in b["members"] if key_of(m) != key_of(a["name"])]
            if not b["members"]:
                del _d()["bands"][bid]
        elif op == "setlist_new":
            b["setlists"].append({"id": secrets.token_hex(4), "name": _clean(a["name"], "set list"), "songs": []})
        elif op == "setlist_rename":
            _setlist(b, a["setlist"])["name"] = _clean(a["name"], "set list")
        elif op == "setlist_delete":
            _setlist(b, a["setlist"])
            b["setlists"] = [s for s in b["setlists"] if s["id"] != a["setlist"]]
        elif op == "song_add":
            sl = _setlist(b, a["setlist"])
            if any(x["key"] == a["song"]["key"] for x in sl["songs"]):
                raise ValueError("That song is already on this set list")
            sl["songs"].append(a["song"])
        elif op in ("song_remove", "song_move"):
            songs = _setlist(b, a["setlist"])["songs"]
            i = int(a["index"])
            if not 0 <= i < len(songs):
                raise KeyError("That song isn't on the set list any more")
            if op == "song_remove":
                songs.pop(i)
            else:
                j = i + (1 if int(a["step"]) > 0 else -1)
                if 0 <= j < len(songs):
                    songs[i], songs[j] = songs[j], songs[i]
        else:
            raise ValueError("unknown change")
        _save()
