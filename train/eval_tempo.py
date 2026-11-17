"""python -m train.eval_tempo [val|test|both] : how good the app's tempo maps are, against human charts.

Runs the beat network once per song on the CPU (cached in .tmp/tempo_cache, so the GPU stays free for
training), then scores the tempo map built from it by the old and the new decoder:
  beat F     share of the human chart's beats with one of ours within 70 ms (F-measure)
  bar F      the same for bar lines (downbeats), within 100 ms
  bar lines  share of the human chart's bar lines that fall on our bar lines (70 ms), counted where
             both have one: shows bar 1 slipping to the wrong beat part-way through a song
  glitches   tempo markers more than 20% off the song's usual tempo (a 54 or 224 BPM blip)
  markers    tempo markers per song
  steady     of the songs a human charted at one tempo (at most 2 tempos), how many we did too
"""
import sys
from pathlib import Path

import numpy as np

from chc import beats, chart
from . import data
from .eval_beats import fmeasure

CACHE = Path(".tmp/tempo_cache")


def probs(i: int):
    p = CACHE / f"{i}.npy"
    if p.exists():
        return np.load(p)
    import torch
    from chc import nn as cnn
    global _model
    if "_model" not in globals():
        ck = torch.load("models/mix.pt", map_location="cpu", weights_only=False)
        _model = cnn.NoteNet("mix")
        _model.load_state_dict(ck["state"])
        _model.eval()
    out = cnn.predict(_model, np.load(data.feat_path(i, "mix")), "cpu")[:, :2]
    CACHE.mkdir(parents=True, exist_ok=True)
    np.save(p, out)
    return out


def bar_slip(est_down, ref_down, ref_beats, tol=0.07):
    """Of the human bar lines that sit on one of our beats, the share that are our bar lines too."""
    est_down = np.asarray(est_down)
    hit = tot = 0
    for r in ref_down:
        if len(est_down) and np.min(np.abs(est_down - r)) <= tol:
            hit += 1
        tot += 1
    return hit / max(1, tot)


def human_tempos(s) -> int:
    b = np.asarray(s["beats"])
    if len(b) < 8:
        return 99
    bpm = 60 / np.diff(b)
    return len({round(x, 0) for x in bpm})


def score(decode, songs):
    rows = []
    for i, s in songs:
        p = probs(i)
        bm = decode(p[:, 0], p[:, 1])
        if bm is None:
            continue
        ticks = np.arange(0, (bm.n_beats + bm.lead_beats + 4) * chart.TPQ, chart.TPQ)
        est_b = np.array([bm.time(t) for t in ticks])
        est_b = est_b[est_b <= max(s["beats"][-1], 1) + 1]
        bar_ticks = [t for t in ticks if t % (4 * chart.TPQ) == 0]  # the lead-in puts a downbeat on a bar line
        est_d = np.array([bm.time(t) for t in bar_ticks])
        ev = bm.tempo_events()
        bpms = np.array([60e6 / us for _, us in ev])
        med = np.median(bpms[1:]) if len(bpms) > 1 else bpms[0]
        body = bpms[1:]  # the lead-in (tick 0) is scored on its own: add_intro evens it out
        rows.append({
            "beatF": fmeasure(est_b, s["beats"]),
            "barF": fmeasure(est_d, s["downbeats"], 0.1),
            "glitch": int(np.sum((body > med * 1.2) | (body < med / 1.2))),
            "lead_off": bool(bpms[0] > med * 1.2 or bpms[0] < med / 1.2),
            "markers": len(ev),
            "steady_ref": human_tempos(s) <= 2,
            "steady_est": len({round(b, 1) for b in bpms[1:]}) <= 1,
        })
    return rows


def report(name, rows):
    a = lambda k: np.array([r[k] for r in rows], float)
    st = [r for r in rows if r["steady_ref"]]
    print(f"{name:>5}: {len(rows)} songs | beat F {a('beatF').mean():.3f} | bar F {a('barF').mean():.3f} | "
          f"songs with any glitch {np.mean(a('glitch') > 0) * 100:.0f}% ({int(a('glitch').sum())} glitches) | "
          f"lead-in off {np.mean(a('lead_off')) * 100:.0f}% | "
          f"markers median {int(np.median(a('markers')))} | steady songs kept steady "
          f"{sum(r['steady_est'] for r in st)}/{len(st)}")


def old(beat, down):
    b, d = beats.track(beat, down)
    return chart.BeatMap(b, d) if len(b) >= 16 else None


def new(beat, down):
    b, d = beats.track(beat, down)
    return chart.BeatMap(b, d, clean=True) if len(b) >= 16 else None


def main():
    split = sys.argv[1] if len(sys.argv) > 1 else "val"
    idx = data.load_index()
    splits = ["val", "test"] if split == "both" else [split]
    songs = [x for sp in splits for x in data.songs_for("mix", sp, idx)]
    report("old", score(old, songs))
    try:
        report("new", score(new, songs))
    except TypeError as e:  # the new decoder isn't in place yet
        print("new: not available:", e)


if __name__ == "__main__":
    main()
