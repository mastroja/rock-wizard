"""How charts play: per-instrument ratings people give RockWizard songs after playing them.

Each rating goes into the song's song.ini, where the app shows it (it is cleared when the song is
re-charted, since it was about the old chart), and is appended to data/feedback.jsonl together
with the models that made the chart (song.ini chc_models), so train.feedback can compare models
by how their charts actually play."""
import json
import time
from pathlib import Path

from . import tidy

LOG = Path(__file__).resolve().parent.parent / "data" / "feedback.jsonl"
INSTRUMENTS = ("guitar", "bass", "drums", "vocals")
RATINGS = ("good", "ok", "bad")
NOTE_KEY, BY_KEY = "chc_rating_note", "chc_rated_by"


def key(inst: str) -> str:
    return f"chc_rating_{inst}"


def is_rating_key(k: str) -> bool:
    return k.startswith("chc_rating_") or k == BY_KEY


def of(ini: dict) -> dict:
    """The song's ratings for the app: {"ratings": {inst: good|ok|bad}, "rating_note", "rated_by"}."""
    return {"ratings": {i: ini[key(i)] for i in INSTRUMENTS if ini.get(key(i)) in RATINGS},
            "rating_note": ini.get(NOTE_KEY, ""), "rated_by": ini.get(BY_KEY, "")}


def rate(folder: Path, by: str, inst: str | None = None, value: str = "", note: str | None = None):
    """inst + value rates one part ("" clears it); note is what's off, for the whole song."""
    ini = tidy.read_ini(folder)
    fields = {BY_KEY: by}
    if inst:
        fields[key(inst)] = value
    if note is not None:
        fields[NOTE_KEY] = note
    tidy.write_ini(folder, fields)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "song": folder.name, "artist": ini.get("artist", ""),
           "title": ini.get("name", ""), "inst": inst, "rating": value if inst else None, "note": note,
           "by": by, "models": ini.get("chc_models", "")}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
