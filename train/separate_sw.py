"""python -m train.separate_sw [max_songs | id,id,...] : separate every indexed song again with
BS-RoFormer SW, a six-stem separator with real guitar and piano stems, and keep its stems next to
the Demucs ones as data/v3/stems/<id>/sw_<stem>.flac (mono, at the feature sample rate).

Why: on 20 held-out songs (2026-11-09) the guitar model, with no retraining, charted better from
this guitar stem than from Demucs' "other": melody shape 77.8% vs 73.1% (80.5% on the real guitar
track), exact frets 34.8% vs 31.2%, extra notes 18.2% vs 22.8%, and far fewer extras on songs with
keyboards. About 37 s a song and 1.4 GB of GPU memory.

Run it with the separator's own Python, not the project's (run_v3h.sh does):
    .tmp/sepenv/Scripts/python -m train.separate_sw
That environment is a venv whose site-packages has a .pth pointing at the project's .venv (for
torch), plus `pip install --no-deps audio-separator audioread ml-collections pydub
"rotary-embedding-torch>=0.6.1,<0.7.0" "beartype==0.23.1"`. The rotary-embedding-torch pin
matters: with a newer one the model loads and runs without any error but its stems are close to
silent. So every song is checked: the six stems must add back up to the song, or nothing is kept.

Finished songs are skipped, so it can be stopped (train/pause.sh) and run again."""
import logging
import re
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

from . import data
from .demucs_feats import full_mix, to_mono22
from .separate_v3 import save_flac

MODEL = "BS-Roformer-SW.ckpt"
MODEL_DIR = Path(".tmp/sepmodels")
WORK = Path(".tmp/sepwork")  # one song's mix and full-size stems at a time, deleted afterwards
STEMS = ("guitar", "piano", "other", "bass", "drums", "vocals")
MAX_LEFT_OUT = 0.4  # share of the song (by RMS) the stems may fail to account for; a sound run leaves ~0.1


def _rms(x) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def main():
    from audio_separator.separator import Separator
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    limit = int(arg) if arg.isdigit() else 0
    only = set(arg.split(",")) if arg and not arg.isdigit() else None
    WORK.mkdir(parents=True, exist_ok=True)
    sep = Separator(model_file_dir=str(MODEL_DIR), output_dir=str(WORK), output_format="FLAC", log_level=logging.WARNING)
    sep.load_model(model_filename=MODEL)
    order = [s for s in data.Layout("v3").index() if "mix" in s["stems"] and (only is None or data.song_id(s) in only)]
    np.random.default_rng(0).shuffle(order)  # any prefix is a fair sample of the library
    left = sum(1 for s in order if not all((data.V3 / "stems" / data.song_id(s) / f"sw_{k}.flac").exists() for k in STEMS))
    print(f"{left} of {len(order)} songs to separate on {sep.torch_device}", flush=True)
    t0, done, failed_in_a_row = time.time(), 0, 0
    for n, s in enumerate(order):
        if limit and done >= limit:
            break
        if data.paused():
            print("paused", flush=True)
            sys.exit(3)
        sid = data.song_id(s)
        out = data.V3 / "stems" / sid
        if all((out / f"sw_{k}.flac").exists() for k in STEMS):
            continue
        try:
            mix = full_mix(s["stems"]["mix"]).numpy()
            if mix.shape[1] < 44100 * 20:
                continue
            scale = max(float(np.abs(mix).max()), 1.0)  # stems summed can clip: scale down for the file, back up after
            sf.write(WORK / f"{sid}.wav", (mix / scale).T, 44100, subtype="PCM_16")
            sep.separate(str(WORK / f"{sid}.wav"))
            got = {}
            for f in WORK.glob(f"{sid}_(*"):
                w, sr = sf.read(f, dtype="float32", always_2d=True)
                got[re.search(r"\((\w+)\)", f.name).group(1).lower()] = to_mono22(torch.from_numpy(w.T), sr) * scale
            if set(got) != set(STEMS):
                raise RuntimeError(f"expected {len(STEMS)} stems, got {sorted(got)}")
            ref = to_mono22(torch.from_numpy(mix), 44100)
            m = min(len(ref), *(len(v) for v in got.values()))
            left_out = _rms(ref[:m] - sum(v[:m] for v in got.values())) / max(_rms(ref[:m]), 1e-9)
            if left_out > MAX_LEFT_OUT:
                raise RuntimeError(f"the stems don't add up to the song (they leave out {left_out:.0%} of it): "
                                   "check the separator environment's library versions")
            out.mkdir(parents=True, exist_ok=True)
            for k in STEMS:
                save_flac(out / f"sw_{k}.flac", got[k])
            done, failed_in_a_row = done + 1, 0
            print(f"{n} {len(order)} {s['name']} ({(time.time() - t0) / done:.0f}s/song, stems leave out {left_out:.0%})", flush=True)
        except Exception as e:  # keep going on a bad file, but not when every song fails
            failed_in_a_row += 1
            print("FAILED", s["name"], e, flush=True)
            if failed_in_a_row >= 3:
                print("three songs in a row failed: stopping", flush=True)
                sys.exit(1)
        finally:
            for f in WORK.glob(f"{sid}*"):
                f.unlink(missing_ok=True)
    print(f"separated {done} songs in {(time.time() - t0) / 3600:.1f} h", flush=True)


if __name__ == "__main__":
    main()
