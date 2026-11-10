"""Training data access: cached features + onset targets, split by song."""
import json
import zlib
from pathlib import Path

import numpy as np

from chc import features, nn

TOL = 0.05  # seconds a detection may be off and still count


def load_index(path="data/index.json"):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def split_of(song: dict) -> str:
    h = zlib.crc32(song["name"].encode("utf-8")) % 100
    return "val" if h < 5 else "test" if h < 10 else "train"


def base(kind: str) -> str:
    return nn.instrument(kind)


def song_id(song: dict) -> str:
    """Stable name for a song's cached files (positions in the index change as songs are added)."""
    return f"{zlib.crc32(song['folder'].encode('utf-8')):08x}"


V3 = Path("data/v3")
PAUSE = V3 / "PAUSE"  # while this file exists, long runs stop at the next safe point


def paused() -> bool:
    return PAUSE.exists()


def save_npy(path: Path, arr) -> None:
    """np.save under a temporary name, then rename, so an interrupted save never leaves a
    truncated file that would later be taken as finished."""
    tmp = path.with_name(path.stem + ".part.npy")
    np.save(tmp, arr)
    tmp.replace(path)


class Layout:
    """Where a song's cached features live.

    v2: original-stem features in data/feats and htdemucs_ft features in data/feats_dm, named
        by position in data/index.json (tonight's run and earlier).
    v3: everything under data/v3, named by song id: stems/<id>/<stem>.flac from Demucs, and
        feats/<id>_<source>_<kind>.npy with source 'clean' (the song's own stem), 'ft'
        (htdemucs_ft), 's6' (htdemucs_6s, which has a real guitar stem) or 'sw' (BS-RoFormer SW,
        a better guitar stem: train.separate_sw).
    """

    def __init__(self, version="v2", dm_src="ft"):
        self.version, self.dm_src = version, dm_src

    def index(self):
        return load_index(V3 / "index.json" if self.version == "v3" else "data/index.json")

    def clean(self, i, song, kind) -> Path:
        if self.version == "v2":
            return feat_path(i, kind)
        return V3 / "feats" / f"{song_id(song)}_clean_{kind}.npy"

    def dm(self, i, song, kind) -> Path:
        if self.version == "v2":
            return dm_path(i, kind)
        return V3 / "feats" / f"{song_id(song)}_{self.dm_src}_{kind}.npy"


def events(song: dict, kind: str):
    """[(time_s, channel_index)] ground truth for this instrument."""
    kind = base(kind)
    if kind == "mix":
        return [(t, 0) for t in song["beats"]] + [(t, 1) for t in song["downbeats"]]
    out = []
    for n in song["notes"][kind]:
        if kind == "drums":
            t, lane, tom = n
            ch = 0 if lane == 0 else 1 if lane == 1 else 2 + (lane - 2) * 2 + int(tom)
            out.append((t, ch))
        else:
            t, lanes = n[0], n[1]
            out += [(t, lane) for lane in lanes]
    return out


def attributes(song: dict, kind: str):
    """Guitar/bass -> ([hammer-on note times], [(start, end) of sustains])."""
    hopo, sus = [], []
    for n in song["notes"][base(kind)]:
        if len(n) < 4:
            continue
        t, _, length, h = n
        if h:
            hopo.append(t)
        if length > 0:
            sus.append((t, t + length))
    return hopo, sus


def targets(evs, n_frames: int, n_ch: int, sigma=0.8) -> np.ndarray:
    y = np.zeros((n_frames, n_ch), np.float32)
    offs = np.arange(-2, 3)
    for t, c in evs:
        f = t * features.FPS
        f0 = int(round(f))
        for o in offs:
            i = f0 + o
            if 0 <= i < n_frames:
                y[i, c] = max(y[i, c], float(np.exp(-0.5 * ((i - f) / sigma) ** 2)))
    return y


def directions(song: dict, kind: str):
    """Guitar/bass -> [(time, 0 up / 1 same / 2 down)]: each note's lowest fret against the
    previous note's lowest fret."""
    notes = song["notes"][base(kind)]
    out = []
    for prev, n in zip(notes, notes[1:]):
        a, b = min(prev[1]), min(n[1])
        out.append((n[0], 0 if b > a else 1 if b == a else 2))
    return out


def chord_sizes(song: dict, kind: str):
    """Guitar/bass -> [(time, 0 single note / 1 two frets / 2 three or more)]."""
    return [(n[0], min(len(n[1]), 3) - 1) for n in song["notes"][base(kind)]]


def feat_path(i: int, kind: str) -> Path:
    return Path("data/feats") / f"{i:04d}_{kind}.npy"


def dm_path(i: int, kind: str) -> Path:
    """Features of the Demucs-separated stem (what the app hears) for the same song."""
    return Path("data/feats_dm") / f"{i:04d}_{base(kind)}.npy"


def songs_for(kind: str, split: str, index):
    return [(i, s) for i, s in enumerate(index)
            if (base(kind) in s["notes"] or (kind == "mix" and s["beats"]))
            and split_of(s) == split and feat_path(i, kind).exists()]


def f1(pred, truth, n_ch, tol=TOL):
    """pred: [(time, ch)], truth same. Returns (tp, fp, fn) with greedy one-to-one matching per channel."""
    tp = fp = fn = 0
    for c in range(n_ch):
        p = sorted(t for t, k in pred if k == c)
        g = sorted(t for t, k in truth if k == c)
        used = [False] * len(p)
        j = 0
        for t in g:
            while j < len(p) and p[j] < t - tol:
                j += 1
            m = j
            while m < len(p) and p[m] <= t + tol:
                if not used[m]:
                    used[m] = True
                    tp += 1
                    break
                m += 1
            else:
                fn += 1
        fp += used.count(False)
    return tp, fp, fn


def prf(tp, fp, fn):
    p = tp / max(1, tp + fp)
    r = tp / max(1, tp + fn)
    return p, r, 2 * p * r / max(1e-9, p + r)
