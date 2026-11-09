"""Audio features for the note models: log-frequency spectrograms on a 256-sample hop.

Used both to build the training set and at inference time, so they must stay identical.
"""
import numpy as np
import torch

SR = 22050
HOP = 256
FPS = SR / HOP  # 86.13 frames per second

# kind -> (n_fft, n_bins, f_min, f_max, log-spaced?)
KINDS = {
    "guitar": (4096, 160, 60.0, 9000.0),
    "bass": (4096, 128, 28.0, 2500.0),
    "drums": (2048, 96, 25.0, 11000.0),
    "mix": (2048, 96, 25.0, 11000.0),  # full song, for beat tracking
    # sharper-in-time variants (shorter analysis window) for A/B tests
    "guitar2": (2048, 160, 60.0, 9000.0),
    "bass2": (2048, 128, 28.0, 2500.0),
    # pitch-sharp variants: a short window for timing stacked on a long window with three
    # bins per semitone, so neighbouring notes (one fret apart) land in different bins
    "guitar_hr": [(2048, 96, 60.0, 9000.0), (8192, 216, 70.0, 4200.0)],
    "bass_hr": [(2048, 64, 28.0, 2500.0), (8192, 192, 35.0, 1400.0)],
    # full-bandwidth drums at 44.1 kHz: hi-hats and cymbals live largely above the 11 kHz the
    # 22 kHz kinds can hear, which is what tells a hi-hat from a snare
    "drums_hq": [(4096, 128, 25.0, 20000.0)],
}
# several audio signals through the same banks, stacked as the model's input channels (stored
# side by side in one array, channel after channel): stereo guitar = the middle of the guitar
# mix (leads), its sides (doubled rhythm guitars panned left/right) and the keys stem (so the
# model can tell keys from guitar)
STACKS = {"guitar_st": ["mid", "side", "keys"]}
KINDS["guitar_st"] = KINDS["guitar_hr"]
RATES = {"drums_hq": 44100}  # kinds that need audio at a higher sample rate than SR
_banks: dict = {}


def rate(kind: str) -> int:
    """Sample rate a feature kind expects its audio at (frames per second stay FPS)."""
    return RATES.get(kind, SR)


def hop(kind: str) -> int:
    return HOP * rate(kind) // SR


def parts(kind: str) -> list:
    """The (n_fft, n_bins, f_min, f_max) spectrogram banks a feature kind stacks."""
    k = KINDS[kind]
    return k if isinstance(k, list) else [k]


def n_bins(kind: str) -> int:
    """Bins per input channel."""
    return sum(p[1] for p in parts(kind))


def channels(kind: str) -> int:
    """Input channels (audio signals) a kind stacks; 1 for ordinary kinds."""
    return len(STACKS.get(kind, [None]))


def _bank(part: tuple, device, sr: int = SR) -> torch.Tensor:
    key = (part, str(device), sr)
    if key not in _banks:
        n_fft, n_bins, lo, hi = part
        freqs = np.fft.rfftfreq(n_fft, 1 / sr)
        centers = np.geomspace(lo, hi, n_bins + 2)
        fb = np.zeros((n_bins, len(freqs)), np.float32)
        # never narrower than ~2 FFT bins (1 for the long pitch window, which is already fine)
        min_w = (1 if n_fft >= 8192 else 2) * sr / n_fft
        for i in range(n_bins):
            l, c, r = centers[i], centers[i + 1], centers[i + 2]
            l, r = min(l, c - min_w), max(r, c + min_w)
            up = (freqs - l) / (c - l)
            down = (r - freqs) / (r - c)
            fb[i] = np.clip(np.minimum(up, down), 0, None)
        fb /= np.maximum(fb.sum(axis=1, keepdims=True), 1e-9) / 2.0
        _banks[key] = torch.from_numpy(fb).to(device)
    return _banks[key]


def compute(wave: np.ndarray, kind: str, device="cuda") -> np.ndarray:
    """wave: mono float32 at rate(kind) (SR for most kinds), or (channels, samples) for STACKS
    kinds. Returns (frames, channels * bins) float16 log-magnitudes at FPS frames per second."""
    if wave.ndim == 2:
        return np.concatenate([compute(np.ascontiguousarray(w), kind, device) for w in wave], axis=1)
    x = torch.from_numpy(wave).to(device)
    sr, h = rate(kind), hop(kind)
    outs = []
    for part in parts(kind):
        n_fft = part[0]
        win = torch.hann_window(n_fft, device=device)
        spec = torch.stft(x, n_fft, h, window=win, center=True, return_complex=True).abs()
        outs.append(torch.log1p(30.0 * (_bank(part, device, sr) @ spec)))
    out = torch.cat(outs, 0).T.contiguous()  # centred frames line up across window sizes
    return out.half().cpu().numpy()


def song_profile(f: np.ndarray) -> np.ndarray:
    """Whole-song summary of normalised features: each bin's average and 90th-percentile level.
    For pitch-sharp features this says which notes the song uses, i.e. its pitch range, which
    charters spread over the five frets (models see only ~12 s of audio at a time otherwise)."""
    f = np.asarray(f[:: max(1, len(f) // 4000)], dtype=np.float32)
    return np.concatenate([f.mean(0), np.percentile(f, 90, axis=0)]).astype(np.float32)


def transpose(f: np.ndarray, kind: str, semitones: int) -> np.ndarray:
    """Normalised features as if the song were played `semitones` higher (training augmentation:
    a chart barely changes with the key, so each song teaches the model several times over)."""
    out, at = [], 0
    for _, bins, lo, hi in parts(kind):
        part = f[:, at:at + bins]
        at += bins
        n = int(round(semitones * (bins + 1) / (12 * np.log2(hi / lo))))
        moved = np.full_like(part, part.min())  # the bins nothing moves into stay silent
        if n > 0:
            moved[:, n:] = part[:, :-n]
        elif n < 0:
            moved[:, :n] = part[:, -n:]
        else:
            moved = part
        out.append(moved)
    return np.concatenate(out, axis=1)


def normalize(f: np.ndarray) -> np.ndarray:
    """Per-song standardisation so quiet and loud mixes look alike."""
    f = f.astype(np.float32)
    return (f - f.mean()) / (f.std() + 1e-6)
