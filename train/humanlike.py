"""python -m train.humanlike [model folder] : can a detector tell our charts from human ones?

On held-out songs the models (default: the app's, in models/) chart guitar and bass the way the
app does: tracked beats, repeats charted alike, snapped to the grid, written to a .mid. Each
chart, ours and the human one of the same song, is cut into 8-bar stretches described by plain
statistics: how busy, which grid spots, which frets, chords, jumps, holds, hammer-ons, how much
comes back. A classifier then learns to tell the two apart and is scored on songs it didn't
learn from.

  50% = can't tell ours from a human's; 100% = always can.

Matching one human's chart note for note is capped by how much two humans disagree; this isn't,
and the statistics that give ours away say what to fix.

  python -m train.humanlike --studios   the yardstick, from human charts only: the same detector
                                        on two random halves of one studio's charts (a coin flip)
                                        and on Neversoft-era vs Harmonix charts (79% of guitar
                                        stretches, 78% of bass, 2026-11-09). Ours don't need to
                                        beat a coin flip, only to be as close as two studios are.

Model output and beats are cached in
data/v3/humanlike (delete it to start over). Runs on the CPU when the GPU is hidden
(CUDA_VISIBLE_DEVICES=-1), so it can run beside a training run."""
import json
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path

import mido
import numpy as np
import torch

from chc import beats, chart, ml, structure
from chc import nn as cnn
from . import charts, data

TPQ = chart.TPQ
CACHE = data.V3 / "humanlike"
BARS, MIN_NOTES = 8, 12
INSTS = ("guitar", "bass")
COUNTS = {"notes per bar", "longest rest (beats)", "variety of gaps", "average fret", "frets used",
          "different note shapes", "different chord shapes", "average streak of one note", "average hold (beats)"}


def read_chart(path: Path, inst: str):
    """A chart's Expert notes in ticks (480 a beat), or None without that part:
    [(tick, lanes, hold ticks, hammer-on, bar, tick within the bar)]. Bars follow the chart's
    time signatures; chords, holds and hammer-ons are read the way train.charts.parse_mid does."""
    mid = mido.MidiFile(str(path), clip=True)
    tpb = mid.ticks_per_beat
    track = next((tr for tr in mid.tracks if charts.TRACKS.get(tr.name.strip().upper()) == inst), None)
    if track is None:
        return None
    raw = charts._notes(track)
    groups, ends = {}, {}
    for tick, n, off in raw:
        if 96 <= n <= 100:
            groups.setdefault(tick, set()).add(n - 96)
            ends[tick] = max(ends.get(tick, 0), off)
    forced_hopo = [(a, b) for a, n, b in raw if n == 101]
    forced_strum = [(a, b) for a, n, b in raw if n == 102]
    merged, last = [], None
    for tick in sorted(groups):
        if last is not None and tick - last[0] <= 10:
            last[1] |= groups[tick]
            last[2] = max(last[2], ends[tick])
        else:
            last = [tick, set(groups[tick]), ends[tick]]
            merged.append(last)
    sigs, tick = [], 0
    for m in mid.tracks[0]:
        tick += m.time
        if m.type == "time_signature":
            sigs.append((tick, max(1, m.numerator * 4 * tpb // m.denominator)))
    if not sigs or sigs[0][0] != 0:
        sigs.insert(0, (0, 4 * tpb))
    starts, before = [], 0  # (first tick, bar length, bars before it)
    for k, (t, length) in enumerate(sigs):
        starts.append((t, length, before))
        if k + 1 < len(sigs):
            before += -(-(sigs[k + 1][0] - t) // length)
    firsts = [s[0] for s in starts]
    scale = TPQ / tpb
    out, prev = [], None
    for tick, lanes, end in merged:
        natural = prev is not None and tick - prev[0] <= tpb // 3 + 1 and len(lanes) == 1 and lanes != prev[1]
        f_h = any(a <= tick < b for a, b in forced_hopo)
        f_s = any(a <= tick < b for a, b in forced_strum)
        hopo = (natural and not f_s) or (f_h and prev is not None)
        t0, length, b0 = starts[bisect_right(firsts, tick) - 1]
        hold = end - tick if end - tick > tpb // 3 else 0
        out.append((int(round(tick * scale)), tuple(sorted(lanes)), int(round(hold * scale)), bool(hopo),
                    b0 + (tick - t0) // length, int(round((tick - t0) % length * scale))))
        prev = (tick, lanes)
    return out


def _entropy(counter) -> float:
    p = np.array(list(counter.values()), float)
    p /= p.sum()
    return float(-(p * np.log2(p)).sum())


def describe(ns, keyed, rhythm_n, exact_n, filled) -> dict:
    """Statistics of one stretch of a chart. keyed: {bar: (rhythm, rhythm with frets)} for its bars
    with 3+ notes; rhythm_n / exact_n count those across the whole song."""
    nan = float("nan")
    n = len(ns)
    tick = np.array([x[0] for x in ns])
    lanes = [x[1] for x in ns]
    hold = np.array([x[2] for x in ns])
    pos = np.array([x[5] for x in ns])
    pb, gaps = pos % TPQ, np.diff(tick)
    f = {"notes per bar": n / BARS, "empty bars": 1 - filled / BARS,
         "on the beat": np.mean(pb == 0), "on the 8th offbeat": np.mean(pb == 240),
         "on 16th offbeats": np.mean((pb == 120) | (pb == 360)),
         "on triplet spots": np.mean((pb == 160) | (pb == 320)),
         "off the usual grid": np.mean(~np.isin(pb, (0, 120, 160, 240, 320, 360))),
         "on the bar's first beat": np.mean(pos == 0),
         "gaps: faster than 16ths": np.mean(gaps < 120), "gaps: 16th notes": np.mean(gaps == 120),
         "gaps: 8th notes": np.mean(gaps == 240), "gaps: quarter notes": np.mean(gaps == 480),
         "gaps: longer than a beat": np.mean(gaps > 480), "variety of gaps": _entropy(Counter(gaps.tolist())),
         "longest rest (beats)": gaps.max() / TPQ}
    ks = list(keyed.values())
    back = [k for k in ks if rhythm_n[k[0]] > 1]
    f["bars whose rhythm comes back"] = len(back) / len(ks) if ks else nan
    f["bars that come back note for note"] = np.mean([exact_n[k[1]] > 1 for k in ks]) if ks else nan
    f["same rhythm, same frets"] = np.mean([exact_n[k[1]] > 1 for k in back]) if back else nan
    flat = [x for ls in lanes for x in ls]
    for i, name in enumerate(("green", "red", "yellow", "blue", "orange")):
        f[f"{name} share"] = flat.count(i) / len(flat)
    size = np.array([len(x) for x in lanes])
    chords = [x for x in lanes if len(x) >= 2]
    f |= {"average fret": float(np.mean(flat)), "frets used": len(set(flat)), "chords": np.mean(size >= 2),
          "3-fret chords": np.mean(size >= 3),
          "chords that skip a fret": np.mean([max(x) - min(x) > len(x) - 1 for x in chords]) if chords else nan,
          "different note shapes": len(set(lanes)), "different chord shapes": len(set(chords))}
    low = np.array([min(x) for x in lanes])
    d = np.diff(low)
    same = np.array([lanes[i] == lanes[i - 1] for i in range(1, n)])
    nz = d[d != 0]
    f |= {"same note as the one before": same.mean(), "moves one fret": np.mean(~same & (np.abs(d) == 1)),
          "jumps 2+ frets": np.mean(np.abs(d) >= 2), "jumps 3+ frets": np.mean(np.abs(d) >= 3),
          "direction flips": np.mean(np.sign(nz[1:]) != np.sign(nz[:-1])) if len(nz) > 2 else nan,
          "average streak of one note": n / (1 + int((~same).sum())),
          "switches chord <-> single": np.mean((size[1:] >= 2) != (size[:-1] >= 2))}
    held = hold > 0
    nxt = np.r_[tick[1:], tick[-1] + 10 ** 6]
    f |= {"held notes": held.mean(), "average hold (beats)": hold[held].mean() / TPQ if held.any() else nan,
          "holds that run into the next note": np.mean(tick[held] + hold[held] >= nxt[held] - 60) if held.any() else nan,
          "hammer-ons": np.mean([x[3] for x in ns])}
    tok = [(lanes[i], int(gaps[i]) if i < n - 1 else -1) for i in range(n)]
    grams = Counter(tuple(tok[i:i + 4]) for i in range(n - 3))
    f["4-note patterns that come back"] = sum(c for c in grams.values() if c > 1) / max(1, n - 3)
    return {k: float(v) for k, v in f.items()}


def stretches(notes) -> list[dict]:
    """describe() for every 8-bar stretch of a chart with enough notes."""
    by_bar = defaultdict(list)
    for x in notes:
        by_bar[x[4]].append(x)
    keyed = {b: (tuple(x[5] for x in ns), tuple((x[5], x[1]) for x in ns)) for b, ns in by_bar.items() if len(ns) >= 3}
    rhythm_n, exact_n = Counter(k[0] for k in keyed.values()), Counter(k[1] for k in keyed.values())
    out = []
    for b0 in range(min(by_bar), max(by_bar) + 1, BARS):
        span = range(b0, b0 + BARS)
        ns = [x for b in span for x in by_bar.get(b, [])]
        if len(ns) >= MIN_NOTES:
            out.append(describe(ns, {b: keyed[b] for b in span if b in keyed}, rhythm_n, exact_n,
                                sum(1 for b in span if b in by_bar)))
    return out


def tracked_beats(song, v2_index, mix_model, device):
    """(beats, downbeats) from the app's beat tracker on the full mix, cached."""
    f = CACHE / f"beats_{data.song_id(song)}.npz"
    if f.exists():
        z = np.load(f)
        return z["b"], z["d"]
    i = v2_index.get(song["folder"])
    if i is None or not data.feat_path(i, "mix").exists():
        return None, None
    p = cnn.predict(mix_model, np.load(data.feat_path(i, "mix")), device)
    b, d = beats.track(p[:, 0], p[:, 1])
    np.savez(f, b=np.asarray(b), d=np.asarray(d))
    return np.asarray(b), np.asarray(d)


def our_chart(song, inst, model, tag, settings, b, d, device):
    """The app's chart of this part for a held-out song, as a .mid (cached)."""
    sid = data.song_id(song)
    out = CACHE / f"{sid}_{inst}_{tag}_{int(round(settings['thr'] * 100))}.mid"
    if out.exists():
        return out
    feats_file = data.V3 / "feats" / f"{sid}_{ml._meta[inst]['stems']}_{ml._meta[inst]['features']}.npy"
    if not feats_file.exists():
        return None
    feats = np.load(feats_file)
    pf = CACHE / f"{sid}_{inst}_{tag}.npy"
    if pf.exists():
        prob = np.load(pf)
    else:
        prob = cnn.predict(model, feats, device)
        data.save_npy(pf, prob.astype(np.float32))
    bm = chart.BeatMap(b, d)
    notes = structure.make_consistent(inst, ml.decode_fretted(inst, prob, **settings), feats, bm)
    tmp = out.with_suffix(".part")
    chart.write_mid(tmp, bm.bpm, 0.0, snap=True, beatmap=bm, **{inst: notes})
    tmp.replace(out)
    return out


def detect(X, y, groups):
    """Each stretch's chance of being ours, from a model that never saw its song."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import GroupKFold
    oof = np.zeros(len(y))
    for tr, te in GroupKFold(5).split(X, y, groups):
        m = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.06, max_iter=300, random_state=0)
        oof[te] = m.fit(X[tr], y[tr]).predict_proba(X[te])[:, 1]
    return oof


def scores(y, oof, groups) -> dict:
    from sklearn.metrics import roc_auc_score
    pairs = []
    for g in np.unique(groups):
        ours, human = oof[(groups == g) & (y == 1)], oof[(groups == g) & (y == 0)]
        if len(ours) and len(human):
            pairs.append(ours.mean() > human.mean())
    return {"stretch": float(np.mean((oof > 0.5) == y)), "auc": float(roc_auc_score(y, oof)), "pair": float(np.mean(pairs))}


def tells(X, y, names) -> list:
    """[(statistic, human average, our average, how well it alone separates them 0.5-1)]"""
    from sklearn.metrics import roc_auc_score
    out = []
    for j, name in enumerate(names):
        ok = ~np.isnan(X[:, j])
        if ok.sum() < 20 or len(set(y[ok])) < 2:
            continue
        a = roc_auc_score(y[ok], X[ok, j])
        out.append((name, float(X[ok & (y == 0), j].mean()), float(X[ok & (y == 1), j].mean()), max(a, 1 - a)))
    return sorted(out, key=lambda t: -t[3])


# which studio charted a song, from song.ini's icon (the game it shipped in)
STUDIOS = {"Neversoft era (Guitar Hero 3 on)": {"gh3", "gha", "ghwt", "ghm", "ghsh", "ghvh", "gh5", "bh", "ghwor"},
           "Harmonix (Guitar Hero 1-2, Rock Band)": {"gh1", "gh2", "gh80s", "rb1", "rb2", "rb3", "rbn", "tbrb", "lrb", "gdrb"}}


def studios():
    """The yardstick: how well the same detector tells two human studios apart, and two random
    halves of one studio (which should be a coin flip). Human charts only, every indexed song."""
    from chc import tidy
    rng = np.random.default_rng(0)
    by = {k: [] for k in STUDIOS}
    for s in data.Layout("v3").index():
        icon = tidy.read_ini(Path(s["folder"])).get("icon", "").lower()
        for k, icons in STUDIOS.items():
            if icon in icons and (Path(s["folder"]) / "notes.mid").exists():
                by[k].append(s)
    a, b = by.values()
    for inst in INSTS:
        feats = {}
        for s in a + b:
            notes = read_chart(Path(s["folder"]) / "notes.mid", inst) if inst in s["notes"] else None
            if notes:
                feats[s["folder"]] = stretches(notes)

        def run(group0, group1):
            n = min(len(group0), len(group1))  # equal songs per side, so 50% means "can't tell"
            rows, y, groups = [], [], []
            for label, songs in ((0, group0), (1, group1)):
                for g, i in enumerate(rng.permutation(len(songs))[:n]):
                    for f in feats[songs[i]["folder"]]:
                        rows.append(f)
                        y.append(label)
                        groups.append(2 * g + label)
            names = list(rows[0])
            X, y, groups = np.array([[r[k] for k in names] for r in rows]), np.array(y), np.array(groups)
            oof = detect(X, y, groups)
            whole = np.mean([(oof[groups == g].mean() > 0.5) == (g % 2) for g in np.unique(groups)])
            from sklearn.metrics import roc_auc_score
            return n, float(np.mean((oof > 0.5) == y)), float(roc_auc_score(y, oof)), float(whole), tells(X, y, names)[:5]
        have = [[s for s in grp if s["folder"] in feats] for grp in (a, b)]
        half = rng.permutation(len(have[0]))
        halves = [[have[0][i] for i in half[::2]], [have[0][i] for i in half[1::2]]]
        print(f"\n######## {inst}: human charts only")
        for title, (g0, g1) in (("two random halves of the Neversoft-era charts", halves), (" vs ".join(STUDIOS), have)):
            n, acc, auc, whole, top = run(g0, g1)
            print(f"  {title}: {n} songs a side | right about a stretch {acc:.1%} (AUC {auc:.3f}) | names a whole chart's side {whole:.0%}")
            if g0 is have[0]:
                print("    biggest differences: " + "; ".join(f"{t[0]} {t[1]:.2f} vs {t[2]:.2f}" for t in top))


def main():
    if "--studios" in sys.argv:
        return studios()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        torch.set_num_threads(4)  # leave the rest to whatever else is running
    CACHE.mkdir(parents=True, exist_ok=True)
    mix_model, _ = ml.load("mix", device)
    if len(sys.argv) > 1:
        ml.MODEL_DIR = Path(sys.argv[1])
    v2_index = {s["folder"]: i for i, s in enumerate(data.load_index())}
    held = [s for s in data.Layout("v3").index() if data.split_of(s) != "train"]
    for inst in INSTS:
        if not ml.available(inst):
            continue
        model, _ = ml.load(inst, device)
        tag = ml.model_tag(inst)
        settings = ml.decode_settings(inst)
        settings["thr"] = ml.PLAY_THR.get(tag, settings["thr"])  # as the app charts with this model
        rows, groups, labels, songs = [], [], [], {}
        for s in held:
            mid = Path(s["folder"]) / "notes.mid"
            if inst not in s["notes"] or not mid.exists():
                continue
            b, d = tracked_beats(s, v2_index, mix_model, device)
            if b is None or len(b) < 16:
                continue
            ours = our_chart(s, inst, model, tag, settings, b, d, device)
            human = read_chart(mid, inst)
            if ours is None or not human:
                continue
            made = read_chart(ours, inst)
            if not made:
                continue
            for label, notes in ((0, human), (1, made)):
                for f in stretches(notes):
                    rows.append(f)
                    groups.append(len(songs))
                    labels.append(label)
            songs[len(songs)] = s["name"]
        names = list(rows[0])
        X = np.array([[r[k] for k in names] for r in rows])
        y, groups = np.array(labels), np.array(groups)
        oof = detect(X, y, groups)
        sc = scores(y, oof, groups)
        rng = np.random.default_rng(0)  # control: swap ours and human for half the songs
        flip = rng.random(len(songs)) < 0.5
        y_fake = np.where(flip[groups], 1 - y, y)
        fake = scores(y_fake, detect(X, y_fake, groups), groups)
        print(f"\n######## {inst} (model {tag}, note threshold {settings['thr']:.2f}): {len(songs)} held-out songs, "
              f"{int((y == 0).sum())} human stretches, {int((y == 1).sum())} of ours")
        print(f"  detector: right about an 8-bar stretch {sc['stretch']:.1%} of the time (AUC {sc['auc']:.3f}) | "
              f"names ours out of a song's two charts {sc['pair']:.0%} | swapped-label check {fake['stretch']:.1%}")
        print(f"  what gives ours away:{'':22s} {'human':>8s} {'ours':>8s}  alone")
        found = tells(X, y, names)
        for name, h, o, a in found:
            fmt = (lambda v: f"{v:8.2f}") if name in COUNTS else (lambda v: f"{v:8.1%}")
            print(f"    {name:38s} {fmt(h)} {fmt(o)}  {a:.2f}")
        per_song = sorted((float(oof[(groups == g) & (y == 1)].mean()), songs[g]) for g in songs if ((groups == g) & (y == 1)).any())
        print("  our most human-like charts: " + "; ".join(f"{n} ({p:.0%})" for p, n in per_song[:4]))
        print("  our least human-like charts: " + "; ".join(f"{n} ({p:.0%})" for p, n in per_song[-4:]))
        np.savez(CACHE / f"result_{inst}_{tag}.npz", X=X, y=y, groups=groups, oof=oof, names=np.array(names))
        (CACHE / f"result_{inst}_{tag}.json").write_text(json.dumps(
            {"inst": inst, "model": tag, "thr": settings["thr"], "songs": len(songs), **sc, "swapped": fake,
             "tells": found}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
