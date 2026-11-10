"""python -m train.train <kind> [--steps N] [--out models/x.pt] [--dm-frac 0.6]
                           [--data v2|v3] [--dm-src ft|s6|sw] [--rel] [--size] [--onset]
                           [--focus 3] [--solo-frac 0.25] [--down 2]

kind is the feature/model kind (guitar, guitar_hr, bass, bass_hr, drums, mix). --data v3 reads
the song-id layout built by separate_v3 + feats_v3; --dm-src picks which Demucs model's stems
to learn from (s6 = htdemucs_6s's real guitar stem, sw = BS-RoFormer SW's, see separate_sw); --rel adds up/same/down fret outputs.
--focus weighs fast runs and guitar solos more in the loss (the most missed notes), --solo-frac
starts that share of windows inside a solo, --onset adds a lane-free note-start output and
--down sets how coarse in time the recurrent layer runs."""
import argparse
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn as tnn

from chc import features
from chc import nn as cnn
from . import charts, data

WIN = 1024  # frames per training window (~12 s); overridden by --win
FAST = 0.15  # seconds: notes closer than this to a neighbour count as a fast run (train.diagnose)


def _in(t, t0, t1):
    return t0 - 0.1 <= t <= t1 + 0.1


class Bank:
    """Memory-mapped features plus lazily built targets for a set of songs."""

    def __init__(self, kind, items, path, n_out=None, context=False, focus=1.0, solo_frac=0.0,
                 onset=False, pitch_aug=0):
        """path(i, song, kind) -> feature file. focus: loss weight for frames in fast runs and
        guitar solos; solo_frac: share of windows placed in a solo; onset: last output is a
        lane-free note start; pitch_aug: transpose windows by up to this many semitones."""
        self.kind, self.items = kind, items
        self.focus, self.solo_frac, self.onset, self.pitch_aug = focus, solo_frac, onset, pitch_aug
        self.solos = {}  # guitar solo sections (seconds) per song, from the chart's solo markers
        self.n_ch = n_out or len(cnn.CHANNELS[kind])
        self.n_lanes = cnn.n_lanes(kind)
        self.feats, self.stats, self.evs, self.attrs, self.dirs, self.sizes = {}, {}, {}, {}, {}, {}
        self.profiles = {}  # whole-song feature profile per song (models trained with --context)
        # converted five-lane drum charts always make blue a tom and yellow a cymbal, which real
        # pro-drum charts often don't: those songs teach kick, snare and timing, not cymbal vs tom
        self.no_cym_tom = {i for i, s in items if cnn.instrument(kind) == "drums" and s.get("five_lane")}
        for i, s in items:
            f = np.load(path(i, s, kind), mmap_mode="r")
            self.feats[i] = f
            sample = np.asarray(f[:: max(1, len(f) // 400)], dtype=np.float32)
            self.stats[i] = (float(sample.mean()), float(sample.std() + 1e-6))
            if context:
                m_, sd_ = self.stats[i]
                part = np.asarray(f[:: max(1, len(f) // 4000)], dtype=np.float32)
                self.profiles[i] = features.song_profile((part - m_) / sd_)
            self.evs[i] = data.events(s, kind)
            if self.n_ch > self.n_lanes:
                self.attrs[i] = data.attributes(s, kind)
            if self.n_ch > self.n_lanes + 2:
                self.dirs[i] = data.directions(s, kind)
            if self.n_ch > self.n_lanes + 5:
                self.sizes[i] = data.chord_sizes(s, kind)
            mid = Path(s["folder"]) / "notes.mid"
            if (focus != 1.0 or solo_frac) and cnn.instrument(kind) == "guitar" and mid.exists():
                try:
                    spans = charts.solo_spans(mid, s.get("delay", 0.0))
                except Exception:
                    spans = []
                if spans:
                    self.solos[i] = spans
        self.solo_items = [(i, s) for i, s in items if i in self.solos]
        lengths = np.array([sum(b - a for a, b in self.solos[i]) for i, _ in self.solo_items], float)
        self.solo_w = lengths / lengths.sum() if len(lengths) else None
        # sample songs in proportion to their length
        self.w = np.array([len(self.feats[i]) for i, _ in items], float)
        self.w /= self.w.sum()

    def window(self, rng):
        if self.solo_frac and self.solo_items and rng.random() < self.solo_frac:
            # a window with a solo in it: solos are a small part of songs but the most missed notes
            i, s = self.solo_items[rng.choice(len(self.solo_items), p=self.solo_w)]
            f = self.feats[i]
            s0, s1 = self.solos[i][rng.integers(len(self.solos[i]))]
            at = int(rng.uniform(s0, s1) * features.FPS)
            a = int(np.clip(at - rng.integers(0, WIN), 0, max(0, len(f) - WIN)))
        else:
            i, s = self.items[rng.choice(len(self.items), p=self.w)]
            f = self.feats[i]
            a = int(rng.integers(0, max(1, len(f) - WIN)))
        x = np.asarray(f[a:a + WIN], dtype=np.float32)
        m, sd = self.stats[i]
        x = (x - m) / sd
        if self.pitch_aug:
            x = features.transpose(x, self.kind, int(rng.integers(-self.pitch_aug, self.pitch_aug + 1)))
        t0, t1 = a / features.FPS, (a + len(x)) / features.FPS
        ev = [(t - t0, c) for t, c in self.evs[i] if _in(t, t0, t1)]
        y = np.zeros((len(x), self.n_ch), np.float32)
        nl = self.n_lanes
        y[:, :nl] = data.targets(ev, len(x), nl)
        if i in self.attrs:
            hopo, sus = self.attrs[i]
            y[:, nl] = data.targets([(t - t0, 0) for t in hopo if _in(t, t0, t1)], len(x), 1)[:, 0]
            for s0, s1 in sus:
                if s1 >= t0 and s0 <= t1:
                    lo = max(0, int(round((s0 - t0) * features.FPS)) + 2)
                    hi = min(len(x), int(round((s1 - t0) * features.FPS)))
                    y[lo:hi, nl + 1] = 1.0
        if i in self.dirs:
            d = [(t - t0, c) for t, c in self.dirs[i] if _in(t, t0, t1)]
            y[:, nl + 2:nl + 5] = data.targets(d, len(x), 3)
        if i in self.sizes:
            z = [(t - t0, c) for t, c in self.sizes[i] if _in(t, t0, t1)]
            y[:, nl + 5:nl + 8] = data.targets(z, len(x), 3)
        starts = np.array(sorted({t for t, _ in ev}))
        if self.onset:
            y[:, -1] = data.targets([(t, 0) for t in starts], len(x), 1)[:, 0]
        n = len(x)
        if len(x) < WIN:
            x = np.pad(x, ((0, WIN - len(x)), (0, 0)))
            y = np.pad(y, ((0, WIN - len(y)), (0, 0)))
        m = np.ones_like(y)  # which targets count towards the loss
        if i in self.no_cym_tom:
            m[:, 2:] = 0.0  # drums channels 2-7: yellow/blue/green, cymbal or tom
        if self.focus != 1.0:
            w = np.ones(len(m), np.float32)
            if len(starts) > 1:
                gap = np.diff(starts)
                fast = np.r_[False, gap < FAST] | np.r_[gap < FAST, False]
                for t in starts[fast]:
                    fr = int(round(t * features.FPS))
                    w[max(0, fr - 3): max(0, min(n, fr + 4))] = self.focus
            for s0, s1 in self.solos.get(i, []):
                lo, hi = max(0, int((s0 - t0) * features.FPS)), min(n, int((s1 - t0) * features.FPS))
                if hi > lo:
                    w[lo:hi] = self.focus
            m *= w[:, None]
        return x, y, m, self.profiles.get(i, np.zeros(0, np.float32))


def attribute_scores(probs, songs, kind):
    """Calls on the true notes: (hammer-on P/R/F1, sustain P/R/F1, direction accuracy or None,
    chord-size accuracy or None)."""
    h = [0, 0, 0]
    s = [0, 0, 0]
    d_ok = d_n = z_ok = z_n = 0
    nl = cnn.n_lanes(kind)
    for p, song in zip(probs, songs):
        notes = song["notes"][data.base(kind)]
        dirs = dict(data.directions(song, kind)) if p.shape[1] >= nl + 5 else {}
        sizes = dict(data.chord_sizes(song, kind)) if p.shape[1] >= nl + 8 else {}
        for n in notes:
            if len(n) < 4:
                continue
            fr = int(round(n[0] * features.FPS))
            if not 0 <= fr < len(p):
                continue
            ph = p[max(0, fr - 2): fr + 3, nl].max() > 0.5
            ps = p[fr + 4: fr + 9, nl + 1].mean() > 0.5 if fr + 9 <= len(p) else False
            for acc, pred, true in ((h, ph, bool(n[3])), (s, ps, n[2] > 0)):
                if pred and true:
                    acc[0] += 1
                elif pred:
                    acc[1] += 1
                elif true:
                    acc[2] += 1
            if n[0] in dirs:
                d_n += 1
                d_ok += int(np.argmax(p[max(0, fr - 2): fr + 3, nl + 2:nl + 5].max(axis=0))) == dirs[n[0]]
            if n[0] in sizes:
                z_n += 1
                z_ok += int(np.argmax(p[max(0, fr - 2): fr + 3, nl + 5:nl + 8].max(axis=0))) == sizes[n[0]]
    return data.prf(*h), data.prf(*s), (d_ok / d_n if d_n else None), (z_ok / z_n if z_n else None)


def evaluate(model, items, kind, device, path, thresholds=None, grid=(0.2, 0.3, 0.4, 0.5, 0.6)):
    """Return (best lane-aware (P,R,F1), thr), any-lane (P,R,F1), attribute scores or None."""
    n_l = cnn.n_lanes(kind)
    probs, truths = [], []
    for i, s in items:
        probs.append(cnn.predict(model, np.load(path(i, s, kind)), device))
        truths.append(data.events(s, kind))
    best = None
    for thr in (grid if thresholds is None else [thresholds]):
        tp = fp = fn = 0
        for p, tr in zip(probs, truths):
            pred = [(fr / features.FPS, c) for fr, c, _ in cnn.pick(p[:, :n_l], thr)]
            a, b, c = data.f1(pred, tr, n_l)
            tp, fp, fn = tp + a, fp + b, fn + c
        res = (data.prf(tp, fp, fn), thr)
        if best is None or res[0][2] > best[0][2]:
            best = res
    # lane-agnostic score at the best threshold (is there a note here at all?)
    tp = fp = fn = 0
    for p, tr in zip(probs, truths):
        pred = [(fr / features.FPS, 0) for fr, c, _ in cnn.pick(p[:, :n_l], best[1])]
        a, b, c = data.f1(pred, [(t, 0) for t, _ in tr], 1)
        tp, fp, fn = tp + a, fp + b, fn + c
    attrs = None
    if model.n_out > n_l:
        attrs = attribute_scores(probs, [s for _, s in items], kind)
    return best, data.prf(tp, fp, fn), attrs


def split_items(layout, kind, index):
    """(clean train, Demucs train, Demucs val, clean val) as [(i, song)] with features on disk."""
    inst = data.base(kind)
    have = [(i, s) for i, s in enumerate(index) if inst in s["notes"]]
    clean = [(i, s) for i, s in have if layout.clean(i, s, kind).exists()]
    dm = [(i, s) for i, s in have if layout.dm(i, s, kind).exists()]
    tr = lambda xs: [x for x in xs if data.split_of(x[1]) == "train"]
    va = lambda xs: [x for x in xs if data.split_of(x[1]) == "val"]
    return tr(clean), tr(dm), va(dm), va(clean)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=list(cnn.CHANNELS))
    ap.add_argument("--steps", type=int, default=6000)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-songs", type=int, default=0)
    ap.add_argument("--hidden", type=int, default=128)
    ap.add_argument("--win", type=int, default=1024)
    ap.add_argument("--dm-frac", type=float, default=0.5,
                    help="share of training windows taken from Demucs-separated stems")
    ap.add_argument("--data", choices=["v2", "v3"], default="v2")
    ap.add_argument("--dm-src", choices=["ft", "s6", "sw"], default="ft")
    ap.add_argument("--rel", action="store_true", help="also learn up/same/down fret moves")
    ap.add_argument("--size", action="store_true", help="also learn how many frets each note has (needs --rel)")
    ap.add_argument("--onset", action="store_true",
                    help="also learn a lane-free note-start output, used for note times (needs --size)")
    ap.add_argument("--focus", type=float, default=1.0,
                    help="loss weight for fast runs and guitar solos (1 = like everything else)")
    ap.add_argument("--solo-frac", type=float, default=0.0,
                    help="share of guitar training windows placed inside a solo")
    ap.add_argument("--down", type=int, default=cnn.DOWN,
                    help="the recurrent layer runs at 1/down of the frame rate (smaller = finer timing)")
    ap.add_argument("--context", action="store_true",
                    help="also give the model the whole song's feature profile (its pitch range)")
    ap.add_argument("--layers", type=int, default=0, help="self-attention layers after the recurrent one")
    ap.add_argument("--wide", type=int, default=1, help="multiplies the convolution filters")
    ap.add_argument("--pitch-aug", type=int, default=0, help="transpose training windows by up to this many semitones")
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--save-every", type=int, default=1000,
                    help="steps between resumable snapshots (also taken when paused)")
    args = ap.parse_args()
    global WIN
    WIN = args.win
    out = Path(args.out or f"models/{args.kind}.pt")
    out.parent.mkdir(parents=True, exist_ok=True)
    device = "cuda"
    layout = data.Layout(args.data, args.dm_src)
    index = layout.index()
    train_items, dm_items, val_dm, val_clean = split_items(layout, args.kind, index)
    if args.max_songs:
        train_items, dm_items = train_items[:args.max_songs], dm_items[:args.max_songs]
    if cnn.instrument(args.kind) == "drums":
        # judge drums on real pro-drum charts only (converted five-lane cymbal/tom labels are guesses)
        val_dm = [x for x in val_dm if not x[1].get("five_lane")]
        val_clean = [x for x in val_clean if not x[1].get("five_lane")]
    # score on Demucs stems when we have them: that is what the app feeds the model
    val_items, val_path = (val_dm, layout.dm) if val_dm else (val_clean, layout.clean)
    print(f"{args.kind} ({args.data}, Demucs {args.dm_src}): {len(train_items)} original-stem + "
          f"{len(dm_items)} Demucs-stem train songs / {len(val_items)} val songs "
          f"({'Demucs' if val_dm else 'original'} stems)", flush=True)
    n_out = len(cnn.CHANNELS[args.kind])
    if args.rel:
        if cnn.n_lanes(args.kind) == n_out:
            raise SystemExit("--rel is for guitar/bass")
        n_out += len(cnn.REL)
    if args.size:
        if not args.rel:
            raise SystemExit("--size needs --rel (the outputs come after the direction ones)")
        n_out += len(cnn.SIZE)
    if args.onset:
        if not args.size:
            raise SystemExit("--onset needs --size (the output comes after the chord-size ones)")
        n_out += len(cnn.ONSET)
    banks, weights = [], []
    if train_items:
        banks.append(Bank(args.kind, train_items, layout.clean, n_out, args.context, args.focus,
                          args.solo_frac, args.onset, args.pitch_aug))
        weights.append(1 - args.dm_frac if dm_items else 1.0)
    if dm_items and args.dm_frac > 0:
        banks.append(Bank(args.kind, dm_items, layout.dm, n_out, args.context, args.focus,
                          args.solo_frac, args.onset, args.pitch_aug))
        weights.append(args.dm_frac if train_items else 1.0)
    weights = np.array(weights) / sum(weights)
    rng = np.random.default_rng(0)
    if args.focus != 1.0 or args.solo_frac:
        print(f"solo sections in {sum(len(b.solo_items) for b in banks)} training songs", flush=True)
    model = cnn.NoteNet(args.kind, args.hidden, n_out, args.context, args.down, args.layers, args.wide).to(device)
    n_l = cnn.n_lanes(args.kind)
    print("params", sum(p.numel() for p in model.parameters()), flush=True)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-2)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=args.steps, pct_start=0.1)
    pw = torch.full((n_out,), 6.0, device=device)
    if n_out > n_l:
        pw[n_l], pw[n_l + 1] = 3.0, 1.0  # hammer-on flags are sparse; sustain spans are not
    if n_out > n_l + 2:
        pw[n_l + 2:] = 3.0
    if args.onset:
        pw[-1] = 6.0  # note starts are as sparse as the lanes' ones
    bce = tnn.BCEWithLogitsLoss(pos_weight=pw, reduction="none")

    def lossf(logits, y, m):
        return (bce(logits, y) * m).sum() / m.sum().clamp(min=1.0)
    t0 = time.time()
    run, best_f, start = 0.0, -1.0, 1

    def save(thr):
        torch.save({"state": model.state_dict(), "kind": args.kind, "thr": thr, "hidden": args.hidden,
                    "n_out": n_out, "data": args.data, "dm_src": args.dm_src, "rel": args.rel,
                    "context": args.context, "size": args.size, "onset": args.onset, "down": args.down,
                    "focus": args.focus, "solo_frac": args.solo_frac, "layers": args.layers, "wide": args.wide,
                    "win": args.win, "pitch_aug": args.pitch_aug}, out)

    # resumable snapshot: everything needed to carry on exactly where training stopped
    snap = out.with_name(out.stem + ".resume.pt")

    def snapshot(step):
        tmp = snap.with_name(snap.stem + ".part.pt")
        torch.save({"model": model.state_dict(), "opt": opt.state_dict(), "sched": sched.state_dict(),
                    "step": step, "rng": rng.bit_generator.state, "run": run, "best_f": best_f,
                    "steps": args.steps}, tmp)
        tmp.replace(snap)

    if snap.exists():
        st = torch.load(snap, map_location=device, weights_only=False)
        if st["steps"] == args.steps:
            model.load_state_dict(st["model"])
            opt.load_state_dict(st["opt"])
            sched.load_state_dict(st["sched"])
            rng.bit_generator.state = st["rng"]
            run, best_f, start = st["run"], st["best_f"], st["step"] + 1
            print(f"resuming at step {start}", flush=True)

    for step in range(start, args.steps + 1):
        model.train()
        xs, ys, ms, ps = zip(*(banks[rng.choice(len(banks), p=weights)].window(rng) for _ in range(args.batch)))
        x = torch.from_numpy(np.stack(xs)).to(device)
        y = torch.from_numpy(np.stack(ys)).to(device)
        m = torch.from_numpy(np.stack(ms)).to(device)
        prof = torch.from_numpy(np.stack(ps)).to(device) if args.context else None
        x = x + 0.05 * torch.randn_like(x)  # light noise augmentation
        with torch.autocast("cuda", dtype=torch.bfloat16):
            loss = lossf(model(x, prof).float(), y, m)
        opt.zero_grad(set_to_none=True)
        loss.backward()
        tnn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        sched.step()
        run = 0.98 * run + 0.02 * loss.item() if step > 1 else loss.item()
        if step % 100 == 0:
            print(f"step {step} loss {run:.4f} {time.time() - t0:.0f}s", flush=True)
        if step % 2500 == 0 or step == args.steps:
            ((p, r, f), thr), anyl, attrs = evaluate(model, val_items, args.kind, device, val_path)
            msg = f"  VAL thr {thr}: P {p:.3f} R {r:.3f} F1 {f:.3f} | any-lane F1 {anyl[2]:.3f}"
            if attrs:
                msg += f" | hopo F1 {attrs[0][2]:.3f} sustain F1 {attrs[1][2]:.3f}"
                if attrs[2] is not None:
                    msg += f" | direction acc {attrs[2]:.3f}"
                if attrs[3] is not None:
                    msg += f" | chord size acc {attrs[3]:.3f}"
            print(msg, flush=True)
            if f > best_f:  # keep the best checkpoint, not just the last
                best_f = f
                save(thr)
        if step < args.steps and (step % args.save_every == 0 or (step % 100 == 0 and data.paused())):
            snapshot(step)
            if data.paused():
                print(f"paused at step {step}", flush=True)
                raise SystemExit(3)
    snap.unlink(missing_ok=True)
    print(f"FINAL best val lane-aware F1 {best_f:.3f} -> {out}", flush=True)


if __name__ == "__main__":
    main()
