"""What charted a song, kept in its song.ini, and whether the app today would chart it differently.

song.ini fields, written by pipeline.write_chart:
  chc_models   guitar:<fingerprint> bass:<...> drums:<...> beats:<...>  (ml.model_tag of each model file)
  chc_stems    what the guitar model listened to: sw (BS-RoFormer SW), s6 (htdemucs_6s), ft (Demucs' other)
  chc_engine   ENGINE when it was charted: the charting code's version, raised whenever a change to it
               changes what charts come out (not for model changes: chc_models has those)
  chc_vocals   the vocal model, when the song has a vocal chart
  chc_charted  the day it was charted
  chc_names    the same in words, for reading song.ini by eye ("Guitar 7, Bass 4, ... Charting 2")

Model names (chc/model_names.json): a part's models are numbered in the order they went into the
app (Guitar 1, Guitar 2, ...), keyed by file fingerprint so a name never moves to another file.
Switching a new model into the app means adding its entry with the next number. The charting
code has its own count: Charting <ENGINE>.

Songs charted before these fields existed have chc_models (guitar, bass, drums) only, and count as
engine 1. Light imports only: the server lists songs with this.
"""
import datetime
import hashlib
import json
from pathlib import Path

ENGINE = 2
ENGINES = {
    1: "before version records",
    2: "tidier tempo map: bar 1 kept in place, one tempo for songs played to a click, no lead-in blip",
}
PARTS = ("guitar", "bass", "drums")
MODELS = Path(__file__).resolve().parent.parent / "models"
VOCALS = "whisper-turbo"
NAMES = Path(__file__).with_name("model_names.json")
_names: dict = {}  # (path, mtime) -> fingerprint, for naming fingerprints


def fields(c: dict) -> dict:
    """The song.ini fields for a chart made now: c is pipeline.chart_stems' result (plus "stems")."""
    from . import ml
    tags = [f"{k}:{ml.model_tag(k)}" for k in PARTS if c.get(k) and ml.available(k)]
    if c.get("beatmap") is not None and ml.available("mix"):
        tags.append(f"beats:{ml.model_tag('mix')}")
    out = {"chc_models": " ".join(tags), "chc_engine": ENGINE,
           "chc_charted": datetime.date.today().isoformat()}
    if c.get("guitar") and c.get("stems"):
        out["chc_stems"] = c["stems"]
    if c.get("vocals"):
        out["chc_vocals"] = VOCALS
    named = [registered(t)["name"] if registered(t) else f"{k} {t}" for k, t in
             (x.split(":", 1) for x in out["chc_models"].split())]
    out["chc_names"] = ", ".join(named + [f"Charting {ENGINE}"])
    return out


def registered(tag: str) -> dict | None:
    """The model_names.json entry for a fingerprint."""
    try:
        return json.loads(NAMES.read_text(encoding="utf-8")).get(tag)
    except (OSError, ValueError):
        return None


def record(ini: dict) -> dict:
    """What a song's song.ini says charted it."""
    models = dict(x.split(":", 1) for x in ini.get("chc_models", "").split() if ":" in x)
    try:
        engine = int(ini.get("chc_engine", "1") or 1)
    except ValueError:
        engine = 1
    return {"models": models, "engine": engine, "stems": ini.get("chc_stems", ""),
            "vocals": ini.get("chc_vocals", ""), "charted": ini.get("chc_charted", "")}


def current() -> dict:
    """The fingerprints the app charts with today."""
    from . import ml
    out = {k: ml.model_tag(k) for k in PARTS if ml.available(k)}
    if ml.available("mix"):
        out["beats"] = ml.model_tag("mix")
    return out


def outdated(ini: dict, now: dict | None = None) -> list[str]:
    """Why the app today would chart this song differently ([] when it wouldn't): the parts whose
    model has changed, and "charting" when the charting code is newer. The beat model counts only
    when the song recorded one (older songs are already behind on charting)."""
    now = current() if now is None else now
    rec = record(ini)
    out = [k for k in PARTS if k in rec["models"] and k in now and rec["models"][k] != now[k]]
    if "beats" in rec["models"] and rec["models"]["beats"] != now.get("beats"):
        out.append("beats")
    if rec["engine"] < ENGINE:
        out.append("charting")
    return out


def name_of(tag: str) -> str:
    """A readable name for a model fingerprint: its registered name and since when ("Guitar 7, Nov 10");
    for a model never registered, its training-run file and date ("guitar_sw, Nov 10"), or the
    fingerprint itself when no model file has it any more."""
    reg = registered(tag)
    if reg:
        day = datetime.date.fromisoformat(reg["since"])
        return f"{reg['name']}, {day:%b} {day.day}"
    for p in MODELS.rglob("*.pt"):
        if "resume" in p.name:
            continue
        k = (str(p), p.stat().st_mtime)
        if k not in _names:
            _names[k] = hashlib.sha1(p.read_bytes()).hexdigest()[:8]
    hits = [Path(p) for (p, _), t in _names.items() if t == tag and Path(p).exists()]
    if not hits:
        return tag
    # the training-run copy (models/v3/guitar_sw.pt) over the app's own guitar.pt or a diagnostics copy
    hits.sort(key=lambda p: (p.parent == MODELS, "diag" in p.parent.name, len(str(p))))
    p = hits[0]
    day = datetime.date.fromtimestamp(p.stat().st_mtime)
    return f"{p.stem}, {day:%b} {day.day}"


def describe(ini: dict) -> dict:
    """For the app: what charted the song, in words, and what is behind."""
    rec = record(ini)
    return {"models": {k: name_of(t) for k, t in rec["models"].items()}, "engine": rec["engine"],
            "engine_note": ENGINES.get(rec["engine"], ""), "stems": rec["stems"], "vocals": rec["vocals"],
            "charted": rec["charted"], "outdated": outdated(ini), "upgrades": upgrades(ini)}


def upgrades(ini: dict) -> list[str]:
    """What updating the song would change, in words: ["Guitar 5 → 7", "Charting 1 → 2"]."""
    now, rec, out = current(), record(ini), []
    for k in outdated(ini, now):
        if k == "charting":
            out.append(f"Charting {rec['engine']} → {ENGINE}")
            continue
        old, new = registered(rec["models"].get(k, "")), registered(now.get(k, ""))
        if old and new and old["name"].rsplit(" ", 1)[0] == new["name"].rsplit(" ", 1)[0]:
            out.append(f"{old['name']} → {new['name'].rsplit(' ', 1)[1]}")
        else:
            out.append(f"{(old or {}).get('name', k + ' model')} → {(new or {}).get('name', 'newer')}")
    return out
