"""python -m train.layers [guitar|bass] [splits, default val,test] [model, default the app's]

How often a chart mixes two guitar parts in the same bar: a fast single-note riff (the "riff"
layer: single frets, not held) going on under chords or held notes (the "chord" layer). Human
charters usually chart one part per section, the one that leads there; the note model hears both
in the guitar stem and charts some of each, which reads as a tangle.

Per bar (the song's real downbeats) it reports, for the app's model and for the real charts:
  mixed bars       share of bars with notes from both layers (at least 2 of each, neither under 25%)
  extras in mixed  share of our notes missing from the real chart that sit in bars we mix and
                   the real chart doesn't
and the ceiling for charting one layer per stretch: on-time F1 as charted, and if each bar (or
block of 4 / 8 bars, as a section would be) kept only whichever of chord / riff / both matches
the real chart best. A rule that picks the layer can't beat that oracle; how close it gets is
the measure.
"""
import sys
from collections import Counter

import numpy as np
import torch

from chc import ml
from chc import nn as cnn
from . import data, metrics, tune_holds

MIXED_MIN, MIXED_SHARE = 2, 0.25


def layer(n) -> str:
    """'chord' for chords and held notes, 'riff' for single unheld notes."""
    return "chord" if len(n[1]) >= 2 or n[2] > 0 else "riff"


def bar_of(times, downbeats):
    return np.searchsorted(downbeats, times, side="right") - 1


def mixed(notes) -> bool:
    c = Counter(layer(n) for n in notes)
    tot = c["chord"] + c["riff"]
    return tot > 0 and min(c["chord"], c["riff"]) >= MIXED_MIN and min(c["chord"], c["riff"]) / tot >= MIXED_SHARE


def match(pred_t, real_t):
    """(tp, fp, fn) and which predictions matched, onsets within metrics.TOL, one to one."""
    used = np.zeros(len(real_t), bool)
    hit = np.zeros(len(pred_t), bool)
    for k, t in enumerate(pred_t):
        if not len(real_t):
            break
        j = np.searchsorted(real_t, t)
        best = None
        for c in (j - 1, j, j + 1):
            if 0 <= c < len(real_t) and not used[c] and abs(real_t[c] - t) <= metrics.TOL:
                if best is None or abs(real_t[c] - t) < abs(real_t[best] - t):
                    best = c
        if best is not None:
            used[best] = hit[k] = True
    tp = int(hit.sum())
    return tp, len(pred_t) - tp, len(real_t) - tp, hit


def main():
    inst = sys.argv[1] if len(sys.argv) > 1 else "guitar"
    splits = (sys.argv[2] if len(sys.argv) > 2 else "val,test").split(",")
    path = sys.argv[3] if len(sys.argv) > 3 else f"models/{inst}.pt"
    ck, kind, model, layout = tune_holds.load(path)
    settings = {"thr": 0.35, "ratios": None, "hopo": 0.5, "sus": 0.5, "rel": 1.0, **ck.get("decode", {})}
    tag = ml.model_tag(inst)
    if len(sys.argv) <= 3:  # the app's own model: its holds rule lives in ml.PLAY_HOLDS
        settings |= ml.PLAY_HOLDS.get(tag, {})
    if inst in ml.PLAY_MAX_CHORD:
        settings["max_chord"] = ml.PLAY_MAX_CHORD[inst]
    rows = [(i, s) for i, s in enumerate(layout.index()) if inst in s["notes"]
            and data.split_of(s) in splits and layout.dm(i, s, kind).exists()]
    print(f"{inst} ({tag}): {len(rows)} songs from {'+'.join(splits)}")
    BLOCK = 4  # bars per stretch, about a phrase
    styles = Counter()      # real chart's style per block
    extra_by = Counter()    # (block style, our extra note's layer)
    tot = {"as charted": [0, 0, 0], "oracle per 4 bars": [0, 0, 0], "oracle per 8 bars": [0, 0, 0]}
    per_song = []
    for i, s in rows:
        with torch.no_grad():
            p = cnn.predict(model, np.load(layout.dm(i, s, kind)), tune_holds.DEV)
        real = [n for n in s["notes"][inst] if len(n) >= 4]
        db = np.array(s["downbeats"], float)
        if len(db) < 8 or not real:
            continue
        ours = ml.decode_fretted(inst, p, **settings)
        ot = np.array([n[0] for n in ours])
        rt = np.array([n[0] for n in real])
        ob, rb = bar_of(ot, db) // BLOCK, bar_of(rt, db) // BLOCK
        tp, fp, fn, hit = match(ot, rt)
        tot["as charted"] = [x + y for x, y in zip(tot["as charted"], (tp, fp, fn))]
        song = Counter()
        for blk in np.unique(np.concatenate([ob, rb])):
            rl = Counter(layer(n) for n, b in zip(real, rb) if b == blk)
            n_r = rl["chord"] + rl["riff"]
            style = "silent" if n_r == 0 else "chords" if rl["chord"] >= 0.6 * n_r else                 "riff" if rl["riff"] >= 0.6 * n_r else "both"
            styles[style] += 1
            for n, b, h in zip(ours, ob, hit):
                if b == blk and not h:
                    extra_by[(style, layer(n))] += 1
                    song[(style, layer(n))] += 1
        wrong = song[("chords", "riff")] + song[("riff", "chord")]
        per_song.append((wrong, sum(song.values()), s["name"]))
        # oracle: per stretch of w bars keep chord / riff / both, whichever matches best there;
        # the kept notes are then matched across the whole song
        for w, key in ((4, "oracle per 4 bars"), (8, "oracle per 8 bars")):
            obw, rbw = bar_of(ot, db) // w, bar_of(rt, db) // w
            keep_mask = np.zeros(len(ours), bool)
            for blk in np.unique(obw):
                idx = np.where(obw == blk)[0]
                rr = np.sort(rt[rbw == blk])
                best = None
                for keep in ("both", "chord", "riff"):
                    sel = [k for k in idx if keep == "both" or layer(ours[k]) == keep]
                    a1, b1, c1, _ = match(np.sort(ot[sel]), rr)
                    f = 2 * a1 / max(1, 2 * a1 + b1 + c1)
                    if best is None or f > best[0] + 1e-9:
                        best = (f, sel)
                keep_mask[best[1]] = True
            a1, b1, c1, _ = match(ot[keep_mask], rt)
            tot[key] = [x + y for x, y in zip(tot[key], (a1, b1, c1))]
    n_ex = sum(extra_by.values())
    print(f"4-bar stretches of the real charts: " + ", ".join(f"{k} {v / sum(styles.values()):.0%}" for k, v in styles.most_common()))
    print(f"our {n_ex} extra notes (not in the real chart), by what the real chart plays in that stretch:")
    for (st, ly), v in extra_by.most_common():
        print(f"  {ly:5s} notes where the real chart plays {st:7s}: {v / n_ex:5.1%}")
    for k, (tp, fp, fn) in tot.items():
        print(f"  {k:20s} on-time F1 {2 * tp / max(1, 2 * tp + fp + fn):.3f}  extras {fp:6d}  missed {fn:6d}")
    per_song.sort(reverse=True)
    print("songs with the most extras from the other part (riff under chords, or chords over a riff):")
    for wrong, n, name in per_song[:10]:
        print(f"  {wrong:4d} of {n:4d} extras  {name}")

if __name__ == "__main__":
    main()
