"""How charts play: per-instrument ratings people give RockWizard songs after playing them.

Each rating goes into the song's song.ini, where the app shows it (it is cleared when the song is
re-charted, since it was about the old chart), and is appended to data/feedback.jsonl together
with the models that made the chart (song.ini chc_models), so train.feedback can compare models
by how their charts actually play.

The owner can also tag what's off ("too many notes"). Tags are votes that nudge how the app turns
the current model's output into notes: once MIN_VOTES more charts say "too many" than "too few",
new charts move a step, and further votes move them further. A vote only counts for the model
that made that chart, so a new model starts clean. Training and test code never sees any of this
(train/* call ml.decode_*, and only ml.fretted_notes and ml.drum_notes are tuned)."""
import json
import time
from pathlib import Path

from . import tidy

LOG = Path(__file__).resolve().parent.parent / "data" / "feedback.jsonl"
INSTRUMENTS = ("guitar", "bass", "drums", "vocals")
RATINGS = ("good", "ok", "bad")
NOTE_KEY, BY_KEY = "chc_rating_note", "chc_rated_by"

# what can be tagged; a tag is an issue plus "+" (too many/long) or "-" (too few/short)
ISSUES = {"guitar": ("notes", "chords", "sustains", "hopos"), "bass": ("notes", "sustains", "hopos"), "drums": ("notes",)}
MIN_VOTES = 2
# (change per vote, largest change), sized from how far each setting moved real charts: +0.03 takes
# about 6% of guitar notes off, while bass and drums need roughly twice that
NOTE_STEP = {"guitar": (0.03, 0.12), "bass": (0.06, 0.18), "drums": (0.05, 0.15)}
HOLD_STEP = (0.05, 0.15)
CHORD_FACTOR, CHORD_MAX_STEPS = 0.85, 4


def key(inst: str) -> str:
    return f"chc_rating_{inst}"


def tags_key(inst: str) -> str:
    return f"chc_rating_tags_{inst}"


def is_rating_key(k: str) -> bool:
    return k.startswith("chc_rating_") or k == BY_KEY


def of(ini: dict) -> dict:
    """The song's ratings for the app: {"ratings": {inst: good|ok|bad}, "issues": {inst: [tags]},
    "issue_options", "rating_note", "rated_by"}."""
    return {"ratings": {i: ini[key(i)] for i in INSTRUMENTS if ini.get(key(i)) in RATINGS},
            "issues": {i: ini.get(tags_key(i), "").split() for i in ISSUES}, "issue_options": ISSUES,
            "rating_note": ini.get(NOTE_KEY, ""), "rated_by": ini.get(BY_KEY, "")}


def _chart_stamp(folder: Path) -> int:
    """Tells versions of a song's chart apart, so votes on a re-chart add to the earlier ones."""
    mid = folder / "notes.mid"
    return int(mid.stat().st_mtime) if mid.exists() else 0


def _log(folder: Path, ini: dict, by: str, inst, rating, note, tags=None) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "song": folder.name, "artist": ini.get("artist", ""),
           "title": ini.get("name", ""), "inst": inst, "rating": rating, "note": note,
           "by": by, "models": ini.get("chc_models", "")}
    if tags is not None:
        row |= {"tags": tags, "chart": _chart_stamp(folder)}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def rate(folder: Path, by: str, inst: str | None = None, value: str = "", note: str | None = None):
    """inst + value rates one part ("" clears it); note is what's off, for the whole song."""
    ini = tidy.read_ini(folder)
    fields = {BY_KEY: by}
    if inst:
        fields[key(inst)] = value
        if value in ("", "good") and ini.get(tags_key(inst)):  # nothing left to complain about
            fields[tags_key(inst)] = ""
    if note is not None:
        fields[NOTE_KEY] = note
    tidy.write_ini(folder, fields)
    _log(folder, ini, by, inst, value if inst else None, note)
    if tags_key(inst or "") in fields:
        _log(folder, ini, by, inst, value, None, [])


def clean_tags(inst: str, tags: list) -> list:
    """Valid tags for this part, one direction per issue (the last one given). Raises ValueError."""
    if inst not in ISSUES:
        raise ValueError(f"{inst} has no settings to tune")
    picked = {}
    for t in tags:
        issue, sign = str(t)[:-1], str(t)[-1:]
        if issue not in ISSUES[inst] or sign not in ("+", "-"):
            raise ValueError(f"Unknown issue: {t}")
        picked[issue] = sign
    return [i + s for i, s in picked.items()]


def set_issues(folder: Path, by: str, inst: str, tags: list) -> None:
    ini = tidy.read_ini(folder)
    tidy.write_ini(folder, {tags_key(inst): " ".join(tags), BY_KEY: by})
    _log(folder, ini, by, inst, ini.get(key(inst), ""), None, tags)


def _rows() -> list:
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _model_of(row: dict, inst: str) -> str:
    return dict(m.split(":", 1) for m in (row.get("models") or "").split() if ":" in m).get(inst, "")


def votes(inst: str, model: str) -> dict:
    """{issue: charts saying too many, minus charts saying too few} for this model."""
    latest = {}
    for r in _rows():  # oldest first, so a chart's latest tags win
        if r.get("inst") == inst and "tags" in r and _model_of(r, inst) == model:
            latest[(r["song"], r.get("chart", 0))] = r["tags"]
    out = {}
    for tags in latest.values():
        for t in tags:
            out[t[:-1]] = out.get(t[:-1], 0) + (1 if t[-1] == "+" else -1)
    return out


def _steps(net: int) -> int:
    """Votes -> how many steps to move: nothing until MIN_VOTES agree."""
    if abs(net) < MIN_VOTES:
        return 0
    return (abs(net) - MIN_VOTES + 1) * (1 if net > 0 else -1)


def tuned(inst: str, settings: dict, model: str) -> dict:
    """Decoding settings with this model's votes applied."""
    out = dict(settings)

    def shift(name, n, step, cap):
        if name in out:
            out[name] = max(0.05, min(0.95, out[name] + max(-cap, min(cap, n * step))))
    for issue, net in votes(inst, model).items():
        n = _steps(net)
        if issue == "notes":
            shift("thr", n, *NOTE_STEP[inst])
        elif issue == "sustains":
            shift("sus", n, *HOLD_STEP)  # surer of holding = shorter holds
        elif issue == "hopos":
            shift("hopo", n, *HOLD_STEP)
        elif issue == "chords" and "size_bias" in out:
            k = max(-CHORD_MAX_STEPS, min(CHORD_MAX_STEPS, n))
            out["size_bias"] = [b * CHORD_FACTOR ** k for b in out["size_bias"]]
    return out


def summary(models: dict) -> list:
    """What the votes add up to, for the app: [{inst, issue, net, steps}]."""
    return [{"inst": i, "issue": issue, "net": net, "steps": _steps(net), "needs": MIN_VOTES}
            for i in ISSUES for issue, net in votes(i, models.get(i, "")).items() if net]
