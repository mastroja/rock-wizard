"""Guitar stem worker for guitar models trained on BS-RoFormer SW stems (train.separate_sw).

    .tmp/sepenv/Scripts/python -m chc.sw_worker <input.wav> <out_dir>

Runs in the separator's own Python environment (see train.separate_sw for how it is set up and
why its library versions are pinned). Writes guitar.wav to out_dir and prints "PROGRESS <0..1>"
lines on stdout, like chc.worker. The six stems must add back up to the song, or it fails: a
broken environment gives near-silent stems without any error.
"""
import logging
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
MODEL = "BS-Roformer-SW.ckpt"
MODEL_DIR = ROOT / ".tmp" / "sepmodels"
STEMS = ("guitar", "piano", "other", "bass", "drums", "vocals")
MAX_LEFT_OUT = 0.4  # same check as train.separate_sw


class _TqdmSpy:
    """Receives tqdm's stderr output and turns '45%|' into PROGRESS lines."""

    def write(self, s):
        m = re.findall(r"(\d+)%\|", s)
        if m:
            print(f"PROGRESS {int(m[-1]) / 100 * 0.95:.2f}", flush=True)

    def flush(self):
        pass


def _rms(x) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    work = out / "sw_all"
    work.mkdir(exist_ok=True)
    from audio_separator.separator import Separator
    mix, sr = sf.read(src, dtype="float32", always_2d=True)
    print("PROGRESS 0.02", flush=True)
    sep = Separator(model_file_dir=str(MODEL_DIR), output_dir=str(work), output_format="FLAC",
                    log_level=logging.WARNING)
    sep.load_model(model_filename=MODEL)
    real_err, sys.stderr = sys.stderr, _TqdmSpy()
    try:
        sep.separate(str(src))
    finally:
        sys.stderr = real_err
    got = {}
    for f in work.glob("*_(*"):
        w, wsr = sf.read(f, dtype="float32", always_2d=True)
        got[re.search(r"\((\w+)\)", f.name).group(1).lower()] = w
    if set(got) != set(STEMS):
        raise SystemExit(f"expected {len(STEMS)} stems, got {sorted(got)}")
    m = min(len(mix), *(len(v) for v in got.values()))
    left_out = _rms(mix[:m] - sum(v[:m] for v in got.values())) / max(_rms(mix[:m]), 1e-9)
    if left_out > MAX_LEFT_OUT:
        raise SystemExit(f"the stems don't add up to the song (they leave out {left_out:.0%} of it): "
                         "check the separator environment's library versions")
    pcm = (np.clip(got["guitar"], -1, 1) * 32767).astype("<i2")
    sf.write(out / "guitar.wav", pcm, wsr, subtype="PCM_16")
    for f in work.iterdir():
        f.unlink()
    work.rmdir()
    print(f"LEFT_OUT {left_out:.2f}", flush=True)
    print("PROGRESS 1.00", flush=True)


if __name__ == "__main__":
    main()
