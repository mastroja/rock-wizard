"""Learned note transcription: separated stem -> chart notes, using the trained models."""
import hashlib
from pathlib import Path

import numpy as np
import torch

from . import features, feedback, nn

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
_cache: dict = {}
_settings: dict = {}  # per-kind decoding thresholds saved in the model file
_meta: dict = {}  # per-kind feature kind and expected stem source
_tags: dict = {}  # (model file, mtime) -> fingerprint


def available(kind: str) -> bool:
    return (MODEL_DIR / f"{kind}.pt").exists()


def model_tag(kind: str) -> str:
    """Short fingerprint of the model file (song.ini chc_models), so ratings of played charts
    can be matched to the model that made them (train.feedback)."""
    p = MODEL_DIR / f"{kind}.pt"
    k = (str(p), p.stat().st_mtime)
    if k not in _tags:
        _tags[k] = hashlib.sha1(p.read_bytes()).hexdigest()[:8]
    return _tags[k]


def load(kind: str, device="cuda"):
    """kind is the instrument ('guitar'); the model file says which features it was trained on."""
    if kind not in _cache:
        ck = torch.load(MODEL_DIR / f"{kind}.pt", map_location=device, weights_only=False)
        fkind = ck.get("kind", kind)
        if fkind not in features.KINDS or nn.instrument(fkind) != nn.instrument(kind):
            fkind = kind
        n_out = ck.get("n_out") or ck["state"]["out.weight"].shape[0]  # older models have 5 outputs
        m = nn.NoteNet(fkind, ck.get("hidden", 128), n_out, ck.get("context", False), ck.get("down", nn.DOWN),
                       ck.get("layers", 0), ck.get("wide", 1)).to(device)
        m.load_state_dict(ck["state"])
        m.eval()
        _cache[kind] = (m, ck.get("thr", 0.4))
        _meta[kind] = {"features": fkind, "stems": ck.get("dm_src", "ft")}
        if ck.get("decode"):
            _settings[kind] = ck["decode"]
    return _cache[kind]


def stem_source(kind: str) -> str:
    """Which separator's stem this model expects: 'ft' (htdemucs_ft), 's6' (htdemucs_6s) or
    'sw' (BS-RoFormer SW, which the app cannot make yet: train.separate_sw)."""
    if not available(kind):
        return "ft"
    load(kind, "cuda" if torch.cuda.is_available() else "cpu")
    return _meta[kind]["stems"]


def to_model_rate(wave: np.ndarray, sr: int, device="cuda", target: int = features.SR) -> np.ndarray:
    if sr == target:
        return wave.astype(np.float32)
    import torchaudio
    x = torch.from_numpy(wave.astype(np.float32)).to(device)
    return torchaudio.functional.resample(x, sr, target).cpu().numpy()


def probabilities(kind: str, wave: np.ndarray, sr: int, device=None, with_features=False):
    """Per-frame model probabilities (T, outputs); with_features also returns the input features."""
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    model, _ = load(kind, device)
    fkind = _meta[kind]["features"]
    feats = features.compute(to_model_rate(wave, sr, device, features.rate(fkind)), fkind, device)
    prob = nn.predict(model, feats, device)
    return (prob, feats) if with_features else prob


def hits(kind: str, prob: np.ndarray, thr=None):
    model_thr = load(kind, "cpu")[1] if thr is None else thr
    return nn.pick(prob, model_thr)


# Real charts: guitar chords are mostly 2 notes and rarely 3; bass is almost all single notes.
MAX_CHORD = {"guitar": 3, "bass": 2}
# how sure the model must be of an extra lane, relative to the strongest one, to add it
CHORD_RATIO = {"guitar": (0.80, 0.93), "bass": (0.88, 1.01)}
MIN_SUSTAIN = 0.30  # seconds; shorter held notes are charted as plain notes


def decode_settings(kind: str) -> dict:
    """Decoding thresholds tuned on validation songs (train.calibrate), else defaults."""
    return {"thr": 0.35, "ratios": CHORD_RATIO[nn.instrument(kind)], "hopo": 0.5, "sus": 0.5, "rel": 1.0,
            "size_bias": [1.0, 1.0], **_settings.get(kind, {})}


def follow_directions(lanes, segs, rel_p, weight):
    """Re-place each note's frets so the chart follows the predicted melody shape.

    lanes: each note's lane tuple from the per-note pick; segs: each note's five lane
    probabilities; rel_p: each note's (up, same, down) probabilities against the previous note.
    A Viterbi pass picks every note's lowest fret (keeping its chord shape) to balance how
    likely those frets are against agreeing with the up/same/down calls, weighted by `weight`.
    """
    K = len(lanes)
    if K < 2 or weight <= 0:
        return lanes
    NEG = -1e9
    lowest = [min(l) for l in lanes]
    span = [max(l) - lo for l, lo in zip(lanes, lowest)]

    def unary(k, a):
        if a + span[k] > 4:
            return NEG
        shape = [l - lowest[k] + a for l in lanes[k]]
        return float(np.log(segs[k][shape] + 1e-4).mean())

    score = np.array([[unary(0, a) for a in range(5)]])
    back = []
    for k in range(1, K):
        r = np.log(rel_p[k] / max(rel_p[k].sum(), 1e-6) + 1e-4)
        row, ptr = np.full(5, NEG), np.zeros(5, int)
        for a in range(5):
            u = unary(k, a)
            if u == NEG:
                continue
            trans = score[-1] + weight * np.array([r[0 if a > b else 1 if a == b else 2] for b in range(5)])
            j = int(np.argmax(trans))
            row[a], ptr[a] = u + trans[j], j
        score = np.vstack([score[-1:], row])
        back.append(ptr)
    a = int(np.argmax(score[-1]))
    anchors = [a]
    for ptr in reversed(back):
        a = int(ptr[a])
        anchors.append(a)
    anchors.reverse()
    return [tuple(sorted(l - lo + a for l in ln)) for ln, lo, a in zip(lanes, lowest, anchors)]


BUSIER = "guitar_busier"  # catches more fast notes and adds more extras than the default guitar
CLEANER_THR = 0.1         # how much surer the model must be of a note for the cleaner style
# Note threshold chosen by the owner for a specific model file, over its calibrated value. Bass
# 33b5d457: 0.40 -> 0.30 misses a quarter fewer notes on test songs for ~2 points more extras.
# Keyed by fingerprint so a new model uses its own; new charts only, never train/* (decode_settings).
PLAY_THR = {"33b5d457": 0.30}
# Most frets a note may have in new charts, where that is tighter than MAX_CHORD. Humans chart a
# chord on 0.4% of bass notes and the app did on 3.3%, the detector's biggest bass tell
# (train.humanlike): single notes took it from 85.7% to 79.0% and exact-fret F1 from .480 to .500.
PLAY_MAX_CHORD = {"bass": 1}
# Hold rule for a specific model file (decode_fretted's sus / min_hold / gap_hold), over its
# calibrated one. Tuned on val songs, checked on test and through the full charting path
# (2026-11-09, guitar 35fecfd4: {"sus": 0.2, "min_hold": 0.3, "gap_hold": (0.75, 0.2)} gets
# hold-or-not right 92.0% vs 90.9% and catches 54% of real holds vs 36%, for 23% vs 15% of ours
# not being in the human chart; the earlier guitar 1cb37c91 wanted gap_hold (0.5, 0.2)).
# Switched on by the owner the same day.
PLAY_HOLDS: dict = {"35fecfd4": {"sus": 0.2, "min_hold": 0.3, "gap_hold": (0.75, 0.2)}}


def play_settings(kind: str, model: str | None = None) -> dict:
    """The decoding settings new charts are made with: the model's calibrated ones plus the
    owner's choices above. train/* scores models with decode_settings instead, except
    train.humanlike, which judges charts as the app makes them."""
    model = model or kind
    settings = decode_settings(model)
    if model == kind:
        settings["thr"] = PLAY_THR.get(model_tag(kind), settings["thr"])
        settings |= PLAY_HOLDS.get(model_tag(kind), {})
    if kind in PLAY_MAX_CHORD:
        settings["max_chord"] = PLAY_MAX_CHORD[kind]
    return settings


def fretted_notes(kind: str, wave: np.ndarray, sr: int, with_features=False, style: str = "normal"):
    """Guitar/bass -> [(time_s, (lanes...), sustain_s, hammer_on or None, confidence)]
    (plus the model's input features with with_features, for finding repeated sections).
    style: "normal", "cleaner" (fewer notes) or "busier" (guitar only: the model that charts more)."""
    model = BUSIER if style == "busier" else kind
    load(model)
    prob, feats = probabilities(model, wave, sr, with_features=True)
    settings = play_settings(kind, model)
    if model == kind:  # what the owner has said about this model's charts
        settings = feedback.tuned(kind, settings, model_tag(kind))
    if style == "cleaner":
        settings["thr"] += CLEANER_THR
    notes = decode_fretted(kind, prob, **settings)
    return (notes, feats) if with_features else notes


GAP_HOLD_MAX = 4.0  # seconds of gap a gap-triggered hold is measured against (so a song's last note isn't held for a minute)


def _hold_run(sustain: np.ndarray, thr: float) -> int:
    """Frames the sustain output stays above thr from the start of this stretch."""
    held = sustain > thr
    return int(np.argmin(held)) if not held.all() else len(held)


def decode_fretted(kind: str, prob: np.ndarray, thr=0.35, ratios=None, hopo=0.5, sus=0.5, rel=1.0,
                   size_bias=(1.0, 1.0), max_chord=None, min_hold=None, gap_hold=None):
    """Model probabilities -> notes.

    Note times are peaks of the strongest lane probability. A second (third) lane joins
    the note only when the model is nearly as sure of it as of the strongest lane, and
    chords never exceed MAX_CHORD lanes. Models trained with the hammer-on/sustain outputs
    also give each note a sustain length and a hammer-on flag; older models give 0 / None.
    Models with up/same/down outputs then re-place frets along the melody (follow_directions).
    Models with chord-size outputs choose how many frets each note gets from those instead
    (size_bias scales the 2- and 3-fret chances, tuned so chords are as common as in real charts),
    then take that many of the likeliest lanes. Models with an onset output (after those) take
    note times from it instead of the strongest lane.

    Holds: a note is held for as long as the sustain output stays above `sus`, if that is at
    least min_hold seconds (default MIN_SUSTAIN). gap_hold=(seconds, level) also holds a note the
    rule missed when the next note is at least that far away and the sustain output averages
    `level` right after the note: humans hold most notes with half a second or more of space
    after them, and the model's output often dips too early to say so.
    """
    ratios = ratios or CHORD_RATIO[kind]
    most = max_chord or MAX_CHORD[kind]
    hopo_thr, sus_thr = hopo, sus
    lanes_p = prob[:, :5]
    has_attr = prob.shape[1] >= 7
    has_rel = prob.shape[1] >= 10
    has_size = prob.shape[1] >= 13
    bias = np.array([1.0, *size_bias])
    onset = prob[:, 13:14] if prob.shape[1] >= 14 else lanes_p.max(axis=1, keepdims=True)
    frames = [fr for fr, _, _ in nn.pick(onset, thr)]
    out, segs, rels = [], [], []
    for k, fr in enumerate(frames):
        seg = lanes_p[max(0, fr - 2): fr + 3].max(axis=0)
        segs.append(seg)
        if has_rel:
            rels.append(prob[max(0, fr - 2): fr + 3, 7:10].max(axis=0))
        order = np.argsort(-seg)
        if has_size:
            n = int(np.argmax(prob[max(0, fr - 2): fr + 3, 10:13].max(axis=0) * bias)) + 1
            lanes = [int(l) for l in order[:min(n, most)]]
        else:
            lanes = [int(order[0])]
            for j in range(1, most):
                if seg[order[j]] >= max(0.3, ratios[j - 1] * seg[order[0]]):
                    lanes.append(int(order[j]))
                else:
                    break
        length, hopo = 0.0, None
        if has_attr:
            hopo = bool(prob[max(0, fr - 2): fr + 3, 5].max() > hopo_thr)
            nxt = frames[k + 1] if k + 1 < len(frames) else len(prob)
            after = prob[fr + 2: nxt, 6]
            n = _hold_run(after, sus_thr)
            length = (n + 2) / features.FPS if n else 0.0
            if length < (MIN_SUSTAIN if min_hold is None else min_hold):
                length = 0.0
            if not length and gap_hold and len(after):
                gap = min((nxt - fr) / features.FPS, GAP_HOLD_MAX)
                if gap >= gap_hold[0] and after[:10].mean() >= gap_hold[1]:
                    length = max((_hold_run(after, 0.1) + 2) / features.FPS, min(gap - 0.12, 0.5 * gap + 0.2))
        out.append((fr / features.FPS, tuple(sorted(lanes)), round(length, 3), hopo, float(seg[order[0]])))
    if has_rel and rel > 0 and out:
        placed = follow_directions([n[1] for n in out], segs, rels, rel)
        out = [(n[0], lanes, *n[2:]) for n, lanes in zip(out, placed)]
    return out


def drum_notes(wave: np.ndarray, sr: int, thr=None, with_features=False):
    """Drums -> [(time_s, lane 0-4, is_tom, confidence)] with lane 0 kick, 1 red, 2 yellow, 3 blue, 4 green.

    Hits within ~35 ms are one moment of the kit; each moment keeps at most one hit per lane
    (cymbal or tom, whichever is likelier), one kick and the two likeliest hand hits, since a
    drummer has two hands.
    """
    prob, feats = probabilities("drums", wave, sr, with_features=True)
    if thr is None:
        thr = feedback.tuned("drums", {"thr": _cache["drums"][1]}, model_tag("drums"))["thr"]
    notes = decode_drums(prob, thr)
    return (notes, feats) if with_features else notes


def decode_drums(prob: np.ndarray, thr=None):
    """Drum model probabilities -> [(time_s, lane, is_tom)] (see drum_notes)."""
    raw = []
    for fr, c, p in hits("drums", prob, thr):
        lane = 0 if c == 0 else 1 if c == 1 else 2 + (c - 2) // 2
        raw.append((fr, lane, bool(c >= 2 and (c - 2) % 2 == 1), p))
    out, k = [], 0
    while k < len(raw):
        j = k
        while j + 1 < len(raw) and raw[j + 1][0] - raw[k][0] <= 3:
            j += 1
        group = raw[k:j + 1]
        k = j + 1
        best = {}
        for h in group:
            if h[1] not in best or h[3] > best[h[1]][3]:
                best[h[1]] = h
        hands = sorted((h for l, h in best.items() if l != 0), key=lambda h: -h[3])[:2]
        keep = hands + ([best[0]] if 0 in best else [])
        t = max(keep, key=lambda h: h[3])[0] / features.FPS  # one shared time keeps it one chord
        out += [(t, h[1], h[2], h[3]) for h in keep]
    return sorted(out)


def beat_times(wave: np.ndarray, sr: int):
    """Full-song audio -> (beat_times, downbeat_times) in seconds."""
    from . import beats
    prob = probabilities("mix", wave, sr)
    return beats.track(prob[:, 0], prob[:, 1])
