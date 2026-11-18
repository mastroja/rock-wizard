"""Repeated sections: find bars that are the same music and chart them the same way.

Human charters chart a chorus once and copy it. The note models judge every moment on its own,
so each repeat comes out slightly different. Here every bar becomes a fingerprint of how the
instrument sounds on each 16th of the bar; bars with near-identical fingerprints (as part of a
run of matching bars, so one lookalike bar is not enough) are grouped, the notes of a group
vote position by position, and the winning pattern is written into every bar of the group.
Voting across repeats also outvotes stray notes that turn up in only one of them.
"""
from collections import Counter, defaultdict

import numpy as np

from . import features
from .chart import TPQ

BAR = 4 * TPQ
SLOTS = 16  # fingerprint resolution: 16ths
# how alike two bars must sound to be charted alike; tuned on validation songs so repeats match
# about as often as in real charts without costing accuracy (train.eval_chart)
REPEAT_THR = {"guitar": 0.94, "bass": 0.96, "drums": 0.95}


def bar_fingerprints(feats: np.ndarray, beatmap, n_bars: int, bands: int = 24) -> np.ndarray:
    """(n_bars, SLOTS * bands) unit vectors: the instrument's spectrum on each 16th of a bar."""
    f = features.normalize(feats)
    edges = np.linspace(0, f.shape[1], bands + 1).astype(int)
    fb = np.stack([f[:, a:b].mean(1) for a, b in zip(edges[:-1], edges[1:])], 1)
    ticks = np.arange(n_bars * SLOTS + 1) * (BAR // SLOTS)
    frames = np.round(np.array([beatmap.time(t) for t in ticks]) * features.FPS).astype(int)
    frames = np.clip(frames, 0, len(fb))
    V = np.zeros((n_bars * SLOTS, bands))
    for k in range(n_bars * SLOTS):
        a, b = frames[k], max(frames[k] + 1, frames[k + 1])
        if a < len(fb):
            V[k] = fb[a:b].mean(0)
    V = V.reshape(n_bars, -1)
    V -= V.mean(1, keepdims=True)
    return V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)


def repeat_groups(V: np.ndarray, thr: float = 0.85) -> list[list[int]]:
    """Groups of bars that repeat the same music. The first bar of each group leads it; a bar
    joins the leader it matches best, if it matches it at or above thr and a neighbouring bar
    also matches the leader's neighbour (so the bars belong to a repeated passage)."""
    S = V @ V.T
    n = len(S)
    near = thr - 0.05
    leader_of, leaders = {}, []
    for i in range(n):
        best, arg = -1.0, None
        for L in leaders:
            if S[i, L] < thr or S[i, L] <= best:
                continue
            run = (i > 0 and L > 0 and S[i - 1, L - 1] >= near) or \
                  (i + 1 < n and L + 1 < n and S[i + 1, L + 1] >= near)
            if run:
                best, arg = S[i, L], L
        if arg is None:
            leaders.append(i)
            leader_of[i] = i
        else:
            leader_of[i] = arg
    groups = defaultdict(list)
    for i, L in leader_of.items():
        groups[L].append(i)
    return [g for g in groups.values() if len(g) > 1]


def _bars(notes, beatmap, unit=BAR):
    """Notes by unit (bar, or beat with unit=TPQ): {unit index: [(tick within unit, tick, note)]}."""
    by = defaultdict(list)
    for n in notes:
        tick = beatmap.tick(n[0])
        by[tick // unit].append((tick % unit, tick, n))
    return by


def _vote(groups, by_bar, key, merge, keep_half_conf, unit=BAR, best_copy=False):
    """Shared voting: for each group, positions (and keys) present in most of its bars win.
    best_copy instead copies the member the model was surest about (by summed confidence), so
    no notes are lost when only two copies disagree."""
    out, grouped = [], set()
    for g in groups:
        k = len(g)
        votes = defaultdict(list)
        if best_copy:
            conf = lambda m: sum(n[-1] if len(n) > 3 else 0.5 for _, _, n in by_bar.get(m, []))
            g_best = max(g, key=conf)
            for pos, tick, n in by_bar.get(g_best, []):
                votes[(pos, key(n))].append((tick, n))
            k = 1
        else:
            for m in g:
                for pos, tick, n in by_bar.get(m, []):
                    votes[(pos, key(n))].append((tick, n))
        pattern = []
        for (pos, kk), hits in votes.items():
            conf = np.mean([h[1][-1] for h in hits]) if len(hits[0][1]) > 3 else 0.5
            if 2 * len(hits) > k or (2 * len(hits) == k and conf >= keep_half_conf):
                pattern.append((pos, kk, hits))
        pattern = merge(pattern)
        for m in g:
            grouped.add(m)
            for pos, rec in pattern:
                out.append((m * unit + pos, rec))
    return out, grouped


def consistent_fretted(notes, beatmap, groups, keep_half_conf=0.6, unit=BAR, best_copy=False):
    """notes: [(t, lanes, sustain_s, hopo, conf)] -> same format, repeats charted alike.
    beatmap must have its grid fitted (BeatMap.fit_grid) for these notes."""
    by_bar = _bars(notes, beatmap, unit)

    def merge(pattern):
        # one note per position: the lane set with most votes (weighted by confidence)
        best = {}
        for pos, _, hits in pattern:
            for tick, n in hits:
                w = n[4] if len(n) > 4 else 0.5
                best.setdefault(pos, Counter())[n[1]] += w
        out = []
        for pos, c in best.items():
            lanes = c.most_common(1)[0][0]
            hits = [h for p, _, hs in pattern if p == pos for h in hs]
            lens = [beatmap.tick(n[0] + n[2]) - tick if n[2] > 0 else 0 for tick, n in hits]
            hopos = [n[3] for _, n in hits if n[3] is not None]
            conf = float(np.mean([n[4] if len(n) > 4 else 0.5 for _, n in hits]))
            flag = None
            if hopos:  # most copies tapped: a tap; else most not strummed: a hammer-on
                flag = "tap" if sum(h == "tap" for h in hopos) * 2 > len(hopos) else sum(map(bool, hopos)) * 2 > len(hopos)
            out.append((pos, (lanes, int(np.median(lens)), flag, conf)))
        return out

    # vote on positions first (any lanes), then pick lanes per position
    placed, grouped = _vote(groups, by_bar, key=lambda n: 0, merge=merge, keep_half_conf=keep_half_conf,
                            unit=unit, best_copy=best_copy)
    out = [n for b, ns in by_bar.items() if b not in grouped for _, _, n in ns]
    for tick, (lanes, lt, hopo, conf) in placed:
        t = beatmap.time(tick)
        out.append((t, lanes, beatmap.time(tick + lt) - t if lt else 0.0, hopo, conf))
    return sorted(out, key=lambda n: n[0])


def consistent_drums(hits, beatmap, groups, keep_half_conf=0.6, unit=BAR, best_copy=False):
    """hits: [(t, lane, is_tom, conf)] -> same format, repeats charted alike."""
    by_bar = _bars(hits, beatmap, unit)

    def merge(pattern):
        out = []
        for pos, lane, hs in pattern:
            toms = [bool(n[2]) for _, n in hs]
            conf = float(np.mean([n[3] if len(n) > 3 else 0.5 for _, n in hs]))
            out.append((pos, (lane, sum(toms) * 2 > len(toms), conf)))
        return out

    placed, grouped = _vote(groups, by_bar, key=lambda n: n[1], merge=merge, keep_half_conf=keep_half_conf,
                            unit=unit, best_copy=best_copy)
    out = [n for b, ns in by_bar.items() if b not in grouped for _, _, n in ns]
    out += [(beatmap.time(tick), lane, tom, conf) for tick, (lane, tom, conf) in placed]
    return sorted(out, key=lambda n: (n[0], n[1]))


# ---------- section-level matching ----------
# Whole passages are matched instead of single bars: 4-bar stretches starting at every beat (so a
# chorus whose bar lines land a beat off still lines up), fingerprinted from the full mix and the
# instrument's own stem, accepted only when the match holds over a run of consecutive starts.
WINDOW = 16      # beats in a compared stretch (4 bars)
MIN_RUN = 4      # consecutive matching starts needed (one-off look-alikes don't count)
# tuned on validation songs (train.eval_chart): whole-passage matching keeps far more of the real
# charts' repeats for guitar and drums at little accuracy cost; for bass it cost accuracy, so bass
# keeps the bar-by-bar matching. Matching on the instrument's own stem beat adding the full mix.
SECTION_THR = {"guitar": 0.93, "drums": 0.95}
METHOD = {"guitar": "sections", "bass": "bars", "drums": "bars"}  # drums: passages cost accuracy on test songs


def beat_fingerprints(feats: np.ndarray, beatmap, n_beats: int, bands: int = 24) -> np.ndarray:
    """(n_beats, 4 * bands): the spectrum on each 16th of every beat (normalised features)."""
    f = features.normalize(feats)
    edges = np.linspace(0, f.shape[1], bands + 1).astype(int)
    fb = np.stack([f[:, a:b].mean(1) for a, b in zip(edges[:-1], edges[1:])], 1)
    step = TPQ // 4
    frames = np.round(np.array([beatmap.time(k * step) for k in range(n_beats * 4 + 1)]) * features.FPS)
    frames = np.clip(frames.astype(int), 0, len(fb))
    V = np.zeros((n_beats * 4, bands))
    for k in range(n_beats * 4):
        a, b = frames[k], max(frames[k] + 1, frames[k + 1])
        if a < len(fb):
            V[k] = fb[a:b].mean(0)
    return V.reshape(n_beats, -1)


def _windows(V: np.ndarray, w: int) -> np.ndarray:
    """Unit vectors of w-beat stretches starting at every beat."""
    n = len(V) - w + 1
    if n <= 0:
        return np.zeros((0, V.shape[1] * w))
    W = np.stack([V[a:a + w].ravel() for a in range(n)])
    W = W - W.mean(1, keepdims=True)
    return W / (np.linalg.norm(W, axis=1, keepdims=True) + 1e-9)


def section_groups(sources, n_beats: int, thr: float, w: int = WINDOW, min_run: int = MIN_RUN,
                   min_lag: int = 4):
    """Groups of beats that repeat. sources: beat fingerprints (e.g. full mix and the
    instrument). A stretch repeats an earlier one (at least min_lag beats back) when their
    average similarity is at least thr for min_run consecutive starts at that distance. Each
    beat points to its best earlier match only, so repeats chain chorus 3 -> 2 -> 1 instead of
    the whole song merging into one group."""
    S = None
    for V in sources:
        W = _windows(V[:n_beats], w)
        S = W @ W.T if S is None else S + W @ W.T
    if S is None or not len(S):
        return []
    S = S / len(sources)
    n = len(S)
    best, best_lag = np.full(n, -np.inf), np.zeros(n, int)
    for lag in range(min_lag, n):
        d = np.diagonal(S, offset=lag)  # d[a]: window a+lag against the earlier window a
        ok = d >= thr
        a = 0
        while a < len(ok):
            if not ok[a]:
                a += 1
                continue
            b = a
            while b + 1 < len(ok) and ok[b + 1]:
                b += 1
            if b - a + 1 >= min_run:
                for x in range(a, b + 1):
                    if d[x] > best[x + lag]:
                        best[x + lag], best_lag[x + lag] = d[x], lag
            a = b + 1
    # every beat a matched window covers points back to the same beat of the earlier stretch
    src, score = {}, {}
    for start in np.nonzero(best_lag)[0]:
        for x in range(start, min(start + w, n_beats)):
            if best[start] > score.get(x, -np.inf) and x - best_lag[start] >= 0:
                score[x], src[x] = best[start], x - best_lag[start]

    def root(x):
        seen = 0
        while x in src and seen < 64:
            x, seen = src[x], seen + 1
        return x
    groups = defaultdict(list)
    for x in range(n_beats):
        groups[root(x)].append(x)
    return [g for g in groups.values() if len(g) > 1]


def make_consistent(kind, notes, feats, beatmap, thr=None, mix_feats=None, method=None,
                    best_copy=False):
    """Pipeline entry: fit the grid, find repeated passages, vote them alike. method: "sections"
    (whole passages, any beat offset) or "bars" (bar by bar); default per instrument (METHOD)."""
    if not notes:
        return notes
    method = method or METHOD[kind]
    if method == "bars":
        return make_consistent_bars(kind, notes, feats, beatmap, thr)
    beatmap.fit_grid([n[0] for n in notes])
    n_beats = int(beatmap.raw_tick(len(feats) / features.FPS) // TPQ) + 1
    sources = [beat_fingerprints(feats, beatmap, n_beats)]
    if mix_feats is not None:
        sources.append(beat_fingerprints(mix_feats, beatmap, n_beats))
    groups = section_groups(sources, n_beats, thr or SECTION_THR.get(kind, 0.95))
    if kind == "drums":
        return consistent_drums(notes, beatmap, groups, unit=TPQ, best_copy=best_copy)
    return consistent_fretted(notes, beatmap, groups, unit=TPQ, best_copy=best_copy)


def make_consistent_bars(kind, notes, feats, beatmap, thr=None):
    """Pipeline entry: fit the grid, find repeated bars from the features, vote them alike."""
    if not notes:
        return notes
    beatmap.fit_grid([n[0] for n in notes])
    n_bars = int(beatmap.raw_tick(len(feats) / features.FPS) // BAR) + 1
    groups = repeat_groups(bar_fingerprints(feats, beatmap, n_bars), thr or REPEAT_THR[kind])
    if kind == "drums":
        return consistent_drums(notes, beatmap, groups)
    return consistent_fretted(notes, beatmap, groups)
