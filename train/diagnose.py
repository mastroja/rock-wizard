"""python -m train.diagnose [model folder] : where do the guitar charts go wrong? (bass alongside)
The models in the folder (default: the app's models/) are diagnosed, guitar.pt and bass.pt.

On held-out songs (val + test), with the app's current models:
1. clean vs Demucs: the same model on the song's original isolated stem and on the Demucs stem,
   to see how much separation costs
2. error breakdown: extra notes (not in the real chart), missed notes, and for notes found at the
   right time whether the frets are exact, the right shape shifted up/down, a different chord
   size, or just different
3. where: inside vs outside guitar solos (the charts mark them), songs with vs without a keys
   part (keyboards share the guitar's Demucs stem), fast passages, and the best / worst songs
"""
from collections import defaultdict
from pathlib import Path

import mido
import numpy as np

from chc import ml
from chc import nn as cnn
from . import charts, data, metrics


def has_keys(path: Path) -> bool:
    return any(tr.name.strip().upper() in ("PART KEYS", "PART REAL_KEYS_X") for tr in mido.MidiFile(str(path), clip=True).tracks)


def breakdown(pred, gt, mask_pred=None, mask_gt=None):
    """Counts of extra / missed / found notes, and how found notes' frets compare."""
    m = metrics.match([p[0] for p in pred], [g[0] for g in gt])
    c = defaultdict(int)
    hit_gt = set(m.values())
    for k, p in enumerate(pred):
        if mask_pred is not None and not mask_pred[k]:
            continue
        c["pred"] += 1
        if k not in m:
            c["extra"] += 1
            continue
        a, b = tuple(sorted(p[1])), tuple(sorted(gt[m[k]][1]))
        if a == b:
            c["exact"] += 1
        elif len(a) != len(b):
            c["chord size"] += 1
        elif tuple(x - a[0] for x in a) == tuple(x - b[0] for x in b):
            c["same shape, shifted"] += 1
        else:
            c["different"] += 1
    for j, g in enumerate(gt):
        if mask_gt is not None and not mask_gt[j]:
            continue
        c["real"] += 1
        c["missed"] += j not in hit_gt
    return c


def add(a, b):
    for k, v in b.items():
        a[k] = a.get(k, 0) + v
    return a


def fmt(c):
    found = max(1, c.get("pred", 0) - c.get("extra", 0))
    return (f"extra {c.get('extra', 0) / max(1, c.get('pred', 0)):5.1%} of ours | "
            f"missed {c.get('missed', 0) / max(1, c.get('real', 0)):5.1%} of real | found notes: "
            f"exact {c.get('exact', 0) / found:5.1%}, shifted {c.get('same shape, shifted', 0) / found:5.1%}, "
            f"chord size {c.get('chord size', 0) / found:5.1%}, different {c.get('different', 0) / found:5.1%}")


def main():
    import sys
    if len(sys.argv) > 1:
        ml.MODEL_DIR = Path(sys.argv[1])
    index = data.Layout("v3").index()
    for inst in ("guitar", "bass"):
        if not ml.available(inst):
            continue
        model, _ = ml.load(inst)
        fkind = ml._meta[inst]["features"]
        settings = ml.decode_settings(inst)
        tot = {"clean": {}, "demucs": {}}
        shape = {"clean": {}, "demucs": {}}
        solo_in, solo_out, keys_yes, keys_no, fast, slow = {}, {}, {}, {}, {}, {}
        per_song = []
        n_keys = {True: 0, False: 0}
        for s in index:
            if data.split_of(s) == "train" or inst not in s["notes"]:
                continue
            sid = data.song_id(s)
            paths = {"clean": data.V3 / "feats" / f"{sid}_clean_{fkind}.npy",
                     "demucs": data.V3 / "feats" / f"{sid}_{ml._meta[inst]['stems']}_{fkind}.npy"}
            mid = Path(s["folder"]) / "notes.mid"
            if not all(p.exists() for p in paths.values()) or not mid.exists():
                continue  # also skips songs renamed or removed from the library since indexing
            gt = [(n[0], tuple(n[1])) for n in s["notes"][inst]]
            preds = {}
            for name, p in paths.items():
                prob = cnn.predict(model, np.load(p), "cuda")
                preds[name] = [(n[0], tuple(n[1])) for n in ml.decode_fretted(inst, prob, **settings)]
                add(tot[name], breakdown(preds[name], gt))
                add(shape[name], metrics.contour(preds[name], gt))
            pred = preds["demucs"]
            if inst == "guitar":
                spans = charts.solo_spans(mid, s.get("delay", 0.0))
                in_solo = lambda t: any(a <= t < b for a, b in spans)
                mp = np.array([in_solo(p[0]) for p in pred])
                mg = np.array([in_solo(g[0]) for g in gt])
                if spans:
                    add(solo_in, breakdown(pred, gt, mp, mg))
                    add(solo_out, breakdown(pred, gt, ~mp, ~mg))
                k_ = has_keys(mid)
                n_keys[k_] += 1
                add(keys_yes if k_ else keys_no, breakdown(pred, gt))
            gtt = np.array([g[0] for g in gt])
            fast_g = np.r_[False, np.diff(gtt) < 0.15] | np.r_[np.diff(gtt) < 0.15, False]
            add(fast, breakdown(pred, gt, np.ones(len(pred), bool) & False, fast_g))
            add(slow, breakdown(pred, gt, np.ones(len(pred), bool) & False, ~fast_g))
            c = metrics.contour(pred, gt)
            b = breakdown(pred, gt)
            per_song.append((c["direction"] / max(1, c["pairs"]), b["extra"] / max(1, b["pred"]),
                             b["missed"] / max(1, b["real"]), s["name"]))
        print(f"\n######## {inst}: {len(per_song)} held-out songs")
        for name in ("clean", "demucs"):
            sc = shape[name]
            print(f"  {name:6s} stem: melody shape {sc['direction'] / max(1, sc['pairs']):5.1%} | {fmt(tot[name])}")
        print(f"  missed notes in fast passages {fast.get('missed', 0) / max(1, fast.get('real', 0)):5.1%}"
              f" vs elsewhere {slow.get('missed', 0) / max(1, slow.get('real', 0)):5.1%}")
        if inst == "guitar":
            print(f"  inside solos : {fmt(solo_in)}")
            print(f"  outside solos: {fmt(solo_out)}")
            print(f"  songs with a keys part ({n_keys[True]}): {fmt(keys_yes)}")
            print(f"  songs without keys ({n_keys[False]}): {fmt(keys_no)}")
        per_song.sort()
        print("  worst songs (melody shape, extra, missed):")
        for sh, ex, mi, name in per_song[:6]:
            print(f"    {sh:5.1%} {ex:5.1%} {mi:5.1%}  {name}")
        print("  best songs:")
        for sh, ex, mi, name in per_song[-4:]:
            print(f"    {sh:5.1%} {ex:5.1%} {mi:5.1%}  {name}")


if __name__ == "__main__":
    main()
