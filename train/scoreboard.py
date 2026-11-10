"""python -m train.scoreboard : every trained model on one page, per instrument.

Gathers what each run already left behind, so nothing is computed here:
  - the test-song scores of each candidate (data/v3/compare_<name>.log, the "new" column)
  - where guitar goes wrong (data/v3/diagnose_<name>.log: missed and extra notes, fast passages)
  - how human its charts look (data/v3/humanlike/result_<inst>_<fingerprint>.json; 50% would be
    indistinguishable, two human studios score about 79%)
  - how its charts played (data/feedback.jsonl, the ratings given in the app)
and marks the model the app is using (models/<inst>.pt, matched by file fingerprint).

Test songs are the same held-out set for every run, give or take a song that was renamed, so
columns can be read down. A new run shows up by itself once its logs exist; give it a line in
RUNS so it gets a readable name. Light imports only: the app serves this at /api/scoreboard."""
import hashlib
import json
import re
from pathlib import Path

from chc import feedback

ROOT = Path(__file__).resolve().parent.parent
V3, MODELS = ROOT / "data" / "v3", ROOT / "models"
STUDIOS = 0.79  # the detector on two human studios' charts (train.humanlike --studios)

# model file stem -> (run, what it tried)
RUNS = {
    "guitar_hr_ft": (1, "Sharper pitch input"), "guitar_hr_s6": (1, "Six-stem separation"),
    "bass_hr": (1, "Sharper pitch input"), "drums": (1, "Five-lane charts added"),
    "guitar_rel": (2, "Up / same / down outputs"), "bass_rel": (2, "Up / same / down outputs"),
    "drums_ct": (2, "Cymbal-or-tom mask"),
    "guitar_ctx": (3, "Whole-song pitch context"), "bass_ctx": (3, "Whole-song pitch context"),
    "guitar_size": (4, "Chord-size outputs"),
    "drums_hq": (5, "Full-quality audio"), "guitar_st": (5, "Stereo and keys input"),
    "guitar_focus": (6, "Weighted to fast notes"), "guitar_fast": (6, "Note-start output"),
    "guitar_big": (7, "Bigger network"),
}
# compare log label -> (key, column title, higher is better)
COMPARE = {"notes at the right time F1": ("timing", "Right time", True), "exact frets F1": ("exact", "Exact frets", True),
           "melody shape (same moves)": ("shape", "Melody shape", True), "hammer-on F1": ("hopo", "Hammer-ons", True),
           "sustain F1": ("sustain", "Holds", True), "pad-and-type F1": ("pad", "Right pad", True),
           "any-hit F1": ("hit", "Any hit", True)}
EXTRA = {"missed": ("Missed", False), "extra": ("Extra", False), "fast_missed": ("Missed when fast", False),
         "detector": ("Human-or-AI", False)}
PERCENT = {"shape", "missed", "extra", "fast_missed", "detector"}
ORDER = ["timing", "exact", "shape", "pad", "hit", "hopo", "sustain", "missed", "extra", "fast_missed", "detector"]
_tags: dict = {}


def tag(path: Path) -> str:
    """A model file's fingerprint, as chc.ml.model_tag gives it."""
    k = (str(path), path.stat().st_mtime)
    if k not in _tags:
        _tags[k] = hashlib.sha1(path.read_bytes()).hexdigest()[:8]
    return _tags[k]


def _compare(path: Path) -> tuple[dict, dict, int]:
    """(old column, new column, songs compared) of a train.compare log."""
    old, new, songs = {}, {}, 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\w+: (\d+) test songs in both", line)
        if m:
            old, new, songs = {}, {}, int(m.group(1))  # a log can hold more than one run: the last counts
        m = re.match(r"(.+?)\s{2,}([\d.]+)\s+([\d.]+)\s*$", line)
        if m and m.group(1).strip() in COMPARE:
            key = COMPARE[m.group(1).strip()][0]
            old[key], new[key] = float(m.group(2)), float(m.group(3))
    return old, new, songs


def _diagnose(path: Path, inst: str) -> dict:
    """Missed / extra notes on the separated stem and in fast passages, from a train.diagnose log."""
    out, inside = {}, False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("########"):
            inside = line.split()[1].rstrip(":") == inst
            out = {} if inside else out
        if not inside:
            continue
        m = re.search(r"demucs stem: .*?extra\s+([\d.]+)% of ours \| missed\s+([\d.]+)% of real", line)
        if m:
            out |= {"extra": float(m.group(1)) / 100, "missed": float(m.group(2)) / 100}
        m = re.search(r"missed notes in fast passages\s+([\d.]+)%", line)
        if m:
            out["fast_missed"] = float(m.group(1)) / 100
    return out


def _ratings() -> dict:
    """{(inst, model fingerprint): {"good": n, "ok": n, "bad": n}} from each song's latest rating."""
    latest = {}
    for r in feedback._rows():
        if r.get("inst") and "tags" not in r and r.get("rating") is not None:
            latest[(r["song"], r["inst"], feedback._model_of(r, r["inst"]))] = r["rating"]
    out = {}
    for (_, inst, model), rating in latest.items():
        if rating in feedback.RATINGS:
            out.setdefault((inst, model), dict.fromkeys(feedback.RATINGS, 0))[rating] += 1
    return out


def build() -> dict:
    """{"insts": [{"inst", "columns": [{key, label, higher, percent}], "rows": [...], "songs"}], "studios"}"""
    ratings = _ratings()
    insts = []
    for inst in ("guitar", "bass", "drums"):
        app = MODELS / f"{inst}.pt"
        app_tag = tag(app) if app.exists() else ""
        rows, songs, last_old = [], 0, {}
        for log in sorted(V3.glob(f"compare_{inst}*.log")):
            name = log.stem[len("compare_"):]
            old, new, n = _compare(log)
            if not new:
                continue
            f = MODELS / "v3" / f"{name}.pt"
            run, what = RUNS.get(name, (99, name))
            row = {"name": what, "run": run, "file": name, "tag": tag(f) if f.exists() else "", "values": dict(new)}
            for d in (V3 / f"diagnose_{name}.log", V3 / f"diagnose_{inst}_app.log"):
                # the "app" diagnosis was of whichever model the app used then: the chord-size guitar
                if d.exists() and (d.stem != f"diagnose_{inst}_app" or name == "guitar_size"):
                    row["values"] |= _diagnose(d, inst)
            rows.append(row)
            if run >= max((r["run"] for r in rows), default=0):
                songs, last_old = n, old
        if app_tag and all(r["tag"] != app_tag for r in rows) and last_old:
            rows.append({"name": "From before these runs", "run": 0, "file": app.name, "tag": app_tag, "values": dict(last_old)})
        for r in rows:
            h = V3 / "humanlike" / f"result_{inst}_{r['tag']}.json"
            if r["tag"] and h.exists():
                r["values"]["detector"] = json.loads(h.read_text(encoding="utf-8"))["stretch"]
            r["in_app"] = bool(r["tag"]) and r["tag"] == app_tag
            r["ratings"] = ratings.get((inst, r["tag"]), {})
        rows.sort(key=lambda r: (r["run"], r["file"]))
        keys = [k for k in ORDER if any(k in r["values"] for r in rows)]
        titles = {v[0]: (v[1], v[2]) for v in COMPARE.values()} | EXTRA
        cols = [{"key": k, "label": titles[k][0], "higher": titles[k][1], "percent": k in PERCENT} for k in keys]
        for c in cols:  # the best score in each column, among models that have one
            have = [r["values"][c["key"]] for r in rows if c["key"] in r["values"]]
            c["best"] = (max if c["higher"] else min)(have) if have else None
        if rows:
            insts.append({"inst": inst, "columns": cols, "rows": rows, "songs": songs})
    return {"insts": insts, "studios": STUDIOS}


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    board = build()
    for b in board["insts"]:
        print(f"\n######## {b['inst']} ({b['songs']} test songs)")
        print(f"  {'run':3s} {'':26s} " + " ".join(f"{c['label'][:11]:>11s}" for c in b["columns"]) + "   played (good/ok/bad)")
        for r in b["rows"]:
            cells = []
            for c in b["columns"]:
                v = r["values"].get(c["key"])
                s = "" if v is None else (f"{v:.1%}" if c["percent"] else f"{v:.3f}")
                cells.append(f"{s + ('*' if v is not None and v == c['best'] else ''):>11s}")
            played = "/".join(str(r["ratings"].get(k, 0)) for k in feedback.RATINGS) if r["ratings"] else ""
            print(f"  {r['run'] or '':<3} {r['name'][:26]:26s} " + " ".join(cells) + f"   {played:8s}{'  <- in the app' if r['in_app'] else ''}")
    print(f"\n* best in its column. Human-or-AI: lower is better, and two human studios score {board['studios']:.0%}.")


if __name__ == "__main__":
    main()
