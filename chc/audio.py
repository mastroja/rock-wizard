"""Audio helpers: bundled ffmpeg, conversion to wav, wav reading."""
import subprocess
import time
import wave
from pathlib import Path

import numpy as np

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ffmpeg_exe() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _run(cmd, data: bytes | None = None):
    r = subprocess.run(cmd, input=data, capture_output=True, creationflags=NO_WINDOW)
    if r.returncode:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[-400:])


def to_wav(src: Path, dest: Path) -> Path:
    """Decode any audio file to 44.1 kHz stereo 16-bit wav."""
    _run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src),
          "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(dest)])
    return dest


def to_ogg(wav: Path, dest: Path, lead: float = 0.0) -> Path:
    """Encode to ogg, with `lead` seconds of silence added at the start."""
    delay = ["-af", f"adelay={int(round(lead * 1000))}:all=1"] if lead else []
    out = [*delay, "-c:a", "libvorbis", "-q:a", "6", str(dest)]
    if Path(wav).suffix.lower() == ".wav":
        # 16-bit wavs (the stems) go in as raw samples: ffmpeg's wav reader takes PCM that happens to
        # look like an S/PDIF bitstream (IEC 61937) for AAC, and then fails on garbage
        try:
            with wave.open(str(wav), "rb") as w:
                sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
                pcm = w.readframes(w.getnframes()) if width == 2 else None
        except (wave.Error, EOFError):
            pcm = None  # not a plain PCM wav: ffmpeg reads it
        if pcm is not None:
            _run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "s16le", "-ar", str(sr), "-ac", str(ch),
                  "-i", "pipe:0", *out], pcm)
            return dest
    _run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(wav), *out])
    return dest


def add_lead(ogg: Path, lead: float) -> None:
    """Add `lead` seconds of silence to the start of an ogg, in place."""
    tmp = ogg.with_name(ogg.stem + ".part.ogg")
    to_ogg(ogg, tmp, lead)
    for wait in (0.5, 1, 2):  # Syncthing can hold a file for a moment
        try:
            return tmp.replace(ogg)
        except PermissionError:
            time.sleep(wait)
    tmp.replace(ogg)


def ensure_writable(paths) -> None:
    """Stop before anything changes if a game has one of these files open."""
    for p in map(Path, paths):
        try:  # renaming needs the same access replacing the file does; opening it isn't enough
            probe = p.with_name(p.name + ".probe")
            p.rename(probe)
            probe.rename(p)
        except PermissionError:
            raise RuntimeError(f"{Path(p).name} is open in another program. Close the game or "
                               "leave this song, then try again") from None


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Return (mono float32 samples, sample rate) from a 16-bit wav."""
    with wave.open(str(path), "rb") as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = w.readframes(n)
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def write_wav(path: Path, data: np.ndarray, sr: int) -> None:
    """data: float array shaped (channels, samples)."""
    pcm = (np.clip(data, -1, 1).T * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(data.shape[0])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()
