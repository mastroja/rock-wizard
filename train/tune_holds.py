"""python -m train.tune_holds <guitar|bass> <model.pt> [--save]

Tunes when a model's notes are held, using the decoding the app charts with (ml.decode_fretted:
sus threshold, min_hold, gap_hold), on validation songs, then checks the choice on test songs.

train.calibrate picks `sus` by averaging the sustain output a few frames after each real note,
which is not how decode_fretted reads it (how long the output stays above `sus`, then a minimum
length), so its choice can leave many real holds as plain notes. This scores whole hold rules:
  hold F1     a note found at the right time is held in both charts (as train.compare's sustain F1)
  right       of those notes, share where held-or-not agrees with the real chart
  length      for notes held in both, share whose length is within 25% of the real one
Picks the rule with the best hold F1 on validation among those within half a point of the best
held-or-not agreement (so it doesn't buy F1 by holding everything). --save writes it into the model file's decode settings (sus, min_hold,
gap_hold), which ml.decode_settings then uses: only for a training-run file, since it changes the
file's fingerprint (an app model gets ml.PLAY_HOLDS instead). Runs on the CPU unless a GPU is visible.
"""
import hashlib
import itertools
import sys

import numpy as np
import torch

from chc import features, ml
from chc import nn as cnn
from . import data, metrics

SUS = (0.2, 0.3, 0.4, 0.5, 0.6)
MIN_HOLD = (0.2, 0.3, 0.4)
GAP_HOLD = (None, (0.5, 0.2), (0.75, 0.2), (0.75, 0.3), (1.0, 0.2), (1.0, 0.3))
DEV = "cuda" if torch.cuda.is_available() else "cpu"


def load(path):
    ck = torch.load(path, map_location=DEV, weights_only=False)
    kind = ck.get("kind")
    m = cnn.NoteNet(kind, ck.get("hidden", 128), ck["state"]["out.weight"].shape[0], ck.get("context", False),
                    ck.get("down", cnn.DOWN), ck.get("layers", 0), ck.get("wide", 1)).to(DEV)
    m.load_state_dict(ck["state"])
    m.eval()
    return ck, kind, m, data.Layout(ck.get("data", "v2"), ck.get("dm_src", "ft"))


def probs(inst, ck, kind, model, layout, split):
    out = []
    for i, s in enumerate(layout.index()):
        if inst in s["notes"] and data.split_of(s) == split and layout.dm(i, s, kind).exists():
            with torch.no_grad():
                out.append((cnn.predict(model, np.load(layout.dm(i, s, kind)), DEV), s["notes"][inst]))
    return out


def score(inst, rows, settings):
    tp = fp = fn = agree = seen = close = 0
    for p, real in rows:
        got = ml.decode_fretted(inst, p, **settings)
        times = np.array([g[0] for g in got])
        if not len(times):
            continue
        for n in real:
            if len(n) < 4:
                continue
            j = int(np.argmin(abs(times - n[0])))
            if abs(times[j] - n[0]) > metrics.TOL:
                continue
            pred, true = got[j][2] > 0, n[2] > 0
            seen += 1
            agree += pred == true
            tp, fp, fn = tp + (pred and true), fp + (pred and not true), fn + (true and not pred)
            if pred and true:
                close += abs(got[j][2] / n[2] - 1) <= 0.25
    return {"hold F1": data.prf(tp, fp, fn)[2], "right": agree / max(1, seen), "length": close / max(1, tp),
            "caught": tp / max(1, tp + fn), "ours real": tp / max(1, tp + fp)}


def choose(inst, base, rows):
    """The hold rule to use: rows are (probabilities, real notes) of validation songs. Returns
    (rule, its score, every (score, rule) tried)."""
    tries = []
    for sus, mh, gh in itertools.product(SUS, MIN_HOLD, GAP_HOLD):
        rule = {"sus": sus, "min_hold": mh, "gap_hold": gh}
        tries.append((score(inst, rows, base | rule), rule))
    top_right = max(sc["right"] for sc, _ in tries)
    ok = [t for t in tries if t[0]["right"] >= top_right - 0.005]
    sc, rule = max(ok, key=lambda t: t[0]["hold F1"])
    return rule, sc, tries


def show(name, sc):
    return (f"{name:34s} hold F1 {sc['hold F1']:.3f}  right {sc['right']:.1%}  length {sc['length']:.1%}  "
            f"caught {sc['caught']:.0%} of real holds  {sc['ours real']:.0%} of ours real")


def main():
    inst, path = sys.argv[1:3]
    ck, kind, model, layout = load(path)
    base = {"thr": 0.35, "ratios": None, "hopo": 0.5, "sus": 0.5, "rel": 1.0, **ck.get("decode", {})}
    if inst in ml.PLAY_MAX_CHORD:
        base["max_chord"] = ml.PLAY_MAX_CHORD[inst]
    tag = hashlib.sha1(open(path, "rb").read()).hexdigest()[:8]
    val, test = probs(inst, ck, kind, model, layout, "val"), probs(inst, ck, kind, model, layout, "test")
    print(f"{inst} {path}: {len(val)} val songs, {len(test)} test songs ({DEV})")
    now = {k: base.get(k) for k in ("sus", "min_hold", "gap_hold")}
    print(show(f"calibrated rule {now}", score(inst, val, base)), "(val)")
    if ml.PLAY_HOLDS.get(tag):
        print(show(f"app's rule {ml.PLAY_HOLDS[tag]}", score(inst, val, base | ml.PLAY_HOLDS[tag])), "(val)")
    rule, sc, tries = choose(inst, base, val)
    for s2, r2 in sorted(tries, key=lambda t: -t[0]["hold F1"])[:5]:
        print(show(f"  {r2}", s2))
    print(show(f"chosen {rule}", sc), "(val)")
    print("test songs:")
    print(show("  calibrated rule", score(inst, test, base)))
    if ml.PLAY_HOLDS.get(tag):
        print(show("  app's rule", score(inst, test, base | ml.PLAY_HOLDS[tag])))
    print(show("  chosen rule", score(inst, test, base | rule)))
    if "--save" in sys.argv:
        ck["decode"] = {**ck.get("decode", {}), **rule}
        torch.save(ck, path)
        print("saved", ck["decode"], "->", path)


if __name__ == "__main__":
    main()
