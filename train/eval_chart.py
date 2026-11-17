"""python -m train.eval_chart [split] [repeat_thr, default per instrument] : score whole written charts, the way the app
makes them, under a few charting variants:

  now     notes snap to the beat grid only when already close to it (the app until now)
  grid    every note snaps to its bar's straight or triplet grid
  smooth  grid, plus beat times smoothed before the tempo map is built
  repeat  grid, plus repeated bars charted alike (chc.structure)
  both    smooth + repeat
  final   what the app does: grid + its per-instrument repeat settings
  clean   final, on the tidier tempo map (chart.BeatMap(clean=True))
CHC_VARIANTS=final,clean picks which to run; the networks run on the GPU when there is one (hide it
with CUDA_VISIBLE_DEVICES=-1 to run on the CPU while training uses it).

For each: notes at the right time F1, exact frets/pads F1, share of notes off the grid, and
how often bars with the same rhythm also get the same frets (real charts: usually 25-60%)."""
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import mido
import numpy as np

from chc import beats, chart, ml, structure
from chc import nn as cnn
from . import charts, data, metrics

VARIANTS = os.environ.get("CHC_VARIANTS", "now,grid,smooth,repeat,both").split(",")
SECT_THR = {}  # per-instrument override for the section variants (tuning)
INST = ["guitar", "bass", "drums"]


def tick_stats(path):
    """{inst: (off-grid share, same-rhythm bars with the same frets / pads)} for a notes.mid."""
    mid = mido.MidiFile(str(path), clip=True)
    tpq, out = mid.ticks_per_beat, {}
    for tr in mid.tracks:
        name = tr.name.strip().upper()
        if name not in ("PART GUITAR", "PART BASS", "PART DRUMS"):
            continue
        by = defaultdict(set)
        for tick, n, _ in charts._notes(tr):
            if 96 <= n <= 100:
                by[tick].add(n - 96)
        if len(by) < 20:
            continue
        ticks = np.array(sorted(by))
        off = float(np.mean((ticks % (tpq // 4) != 0) & (ticks % (tpq // 3) != 0)))
        bars = defaultdict(list)
        for t in ticks:
            bars[t // (4 * tpq)].append((t % (4 * tpq), tuple(sorted(by[t]))))
        rhythm = defaultdict(list)
        for ns in bars.values():
            if len(ns) >= 4:
                rhythm[tuple(p for p, _ in ns)].append(tuple(l for _, l in ns))
        same = total = 0
        for fr in rhythm.values():
            for i in range(len(fr)):
                for j in range(i + 1, len(fr)):
                    total += 1
                    same += fr[i] == fr[j]
        out[name.split()[1].lower()] = (off, same / total if total else np.nan)
    return out


def note_keys(notes, inst):
    """[(time, key)]: frets for guitar/bass, (pad, tom) for drums."""
    if inst == "drums":
        return [(n[0], (n[1], bool(n[2]))) for n in notes]
    return [(n[0], tuple(n[1])) for n in notes]


def chart_events(parsed, inst):
    if inst == "drums":
        return [(t, 0 if l == 0 else 1 if l == 1 else 2 + (l - 2) * 2 + int(tom)) for t, l, tom in parsed["drums"]]
    return [(n[0], l) for n in parsed[inst] for l in n[1]]


def main():
    split = sys.argv[1] if len(sys.argv) > 1 else "test"
    thr = float(sys.argv[2]) if len(sys.argv) > 2 else None  # None: structure.REPEAT_THR
    global DEVICE
    import torch
    DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    index = data.load_index()
    mix_model, _ = ml.load("mix", DEVICE)
    models = {k: ml.load(k, DEVICE)[0] for k in INST}

    def feat_file(i, s, k):
        """The app models' input for this song: v2 files for v2-kind models, else the v3
        song-id layout (htdemucs_ft stems) for e.g. the pitch-sharp guitar_hr / bass_hr."""
        fkind = ml._meta[k]["features"]
        return data.dm_path(i, k) if fkind == k else data.V3 / "feats" / f"{data.song_id(s)}_ft_{fkind}.npy"

    rows = [(i, s) for i, s in enumerate(index) if data.split_of(s) == split
            and data.feat_path(i, "mix").exists() and len(s["beats"]) > 16
            and any(feat_file(i, s, k).exists() and k in s["notes"] for k in INST)]
    print(f"{len(rows)} {split} songs, repeat threshold {thr}", flush=True)
    score = {v: {k: [] for k in INST} for v in VARIANTS}
    stats = {v: {k: [] for k in INST} for v in VARIANTS + ["real"]}
    reps = {v: {k: {} for k in INST} for v in VARIANTS}
    tmp = Path(tempfile.mkdtemp())
    for n, (i, s) in enumerate(rows):
        mix_feats = np.load(data.feat_path(i, "mix"))
        p = cnn.predict(mix_model, mix_feats, DEVICE)
        b, d = beats.track(p[:, 0], p[:, 1])
        if len(b) < 16:
            continue
        decoded, feats = {}, {}
        for k in INST:
            if k in s["notes"] and feat_file(i, s, k).exists():
                feats[k] = np.load(feat_file(i, s, k))
                prob = cnn.predict(models[k], feats[k], DEVICE)
                decoded[k] = ml.decode_drums(prob) if k == "drums" else \
                    ml.decode_fretted(k, prob, **ml.decode_settings(k))
        for v in VARIANTS:
            bm = chart.BeatMap(b, d, smooth=4 if v in ("smooth", "both") else 0, clean=v == "clean")
            parts = dict(decoded)
            if v in ("repeat", "both"):
                parts = {k: structure.make_consistent(k, notes, feats[k], bm, thr, method="bars")
                         for k, notes in parts.items()}
            elif v in ("final", "clean"):  # what the app does: per-instrument defaults
                parts = {k: structure.make_consistent(k, notes, feats[k], bm) for k, notes in parts.items()}
            elif v.startswith("sect"):  # section matching; "_inst": instrument only; "_vote": majority
                parts = {k: structure.make_consistent(k, notes, feats[k], bm, SECT_THR.get(k),
                                                      None if "_inst" in v else mix_feats,
                                                      best_copy="_vote" not in v)
                         for k, notes in parts.items()}
            out = tmp / f"{v}.mid"
            chart.write_mid(out, bm.bpm, 0.0, guitar=parts.get("guitar"), bass=parts.get("bass"),
                            drums=parts.get("drums"), snap=True, beatmap=bm, grid=v != "now")
            back = charts.parse_mid(out)
            st = tick_stats(out)
            for k in parts:
                score[v][k].append(metrics.score(chart_events(back, k), data.events(s, k)))
                c = metrics.repeats(note_keys(back[k], k), note_keys(s["notes"][k], k), s["downbeats"])
                reps[v][k] = {x: reps[v][k].get(x, 0) + c[x] for x in c}
                if k in st:
                    stats[v][k].append(st[k])
        real_mid = Path(s["folder"]) / "notes.mid"
        real = tick_stats(real_mid) if real_mid.exists() else {}  # .chart songs: no grid stats
        for k in decoded:
            if k in real:
                stats["real"][k].append(real[k])
        print(n, s["name"], flush=True)
    for k in INST:
        print(f"\n{k}:")
        print(f"  {'':8s} {'on time F1':>11s} {'exact F1':>9s} {'off grid':>9s} {'repeats alike':>14s}"
              f" {'real repeats kept':>18s} {'copied wrongly':>15s}")
        for v in VARIANTS + ["real"]:
            st = np.array(stats[v][k]) if stats[v][k] else np.zeros((0, 2))
            off = np.nanmean(st[:, 0]) if len(st) else np.nan
            same = np.nanmean(st[:, 1]) if len(st) else np.nan
            if v == "real":
                print(f"  {'real':8s} {'':>11s} {'':>9s} {off:9.1%} {same:14.1%}")
                continue
            sm = metrics.summarize(score[v][k]) if score[v][k] else None
            r = reps[v][k]
            kept = r.get("kept", 0) / max(1, r.get("real_pairs", 0))
            wrong = r.get("copied", 0) / max(1, r.get("our_pairs", 0))
            print(f"  {v:8s} {sm['onset_F1']:11.3f} {sm['exact_F1']:9.3f} {off:9.1%} {same:14.1%}"
                  f" {kept:18.1%} {wrong:15.1%}")


if __name__ == "__main__":
    main()
