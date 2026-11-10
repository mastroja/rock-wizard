"""Review a song before it goes live, and publish it to the live library (Quickplay).

check() runs automatic checks on a song folder and returns findings, each one
  red    something that will break or can't be played: blocks publishing unless forced
  yellow worth a look: a stretch where the instrument plays but the chart is silent, busy
         charting over a quiet stem, odd tempo jumps, the song already being live...
  info   counts and ratings
with times where they apply, so a human knows exactly where to look.

publish() moves a reviewed song into the live folder (where Syncthing picks it up), backing up
an earlier RockWizard version of the same song first and refusing to sit next to a human chart.
"""
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import audio, library, tidy

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
AUDIO_EXT = (".ogg", ".opus", ".mp3", ".wav")
LEVELS = ("expert", "hard", "medium", "easy")


@dataclass
class Finding:
    level: str            # "red", "yellow" or "info"
    text: str
    times: list = field(default_factory=list)  # (start_s, end_s) spots to look at


@dataclass
class Report:
    folder: Path
    findings: list = field(default_factory=list)

    @property
    def verdict(self) -> str:
        levels = {f.level for f in self.findings}
        return "red" if "red" in levels else "yellow" if "yellow" in levels else "green"

    def add(self, level, text, times=None):
        self.findings.append(Finding(level, text, list(times or [])))


def _decode_mono(path: Path, sr: int = 8000) -> np.ndarray:
    r = subprocess.run([audio.ffmpeg_exe(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr),
                        "-f", "f32le", "-"], capture_output=True, creationflags=NO_WINDOW)
    return np.frombuffer(r.stdout, np.float32)


def _clock(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def _spans(flags: np.ndarray, min_len: int = 4) -> list:
    """Runs of True seconds at least min_len long -> [(start, end)]."""
    out, start = [], None
    for i, f in enumerate(list(flags) + [False]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if i - start >= min_len:
                out.append((float(start), float(i)))
            start = None
    return out


def _activity(stem: Path, notes_t: np.ndarray, length: int):
    """Per second: the stem's loudness (dB) and the chart's note count."""
    x = _decode_mono(stem)
    n = min(length, len(x) // 8000)
    rms = np.array([np.sqrt(np.mean(x[i * 8000:(i + 1) * 8000] ** 2) + 1e-12) for i in range(n)])
    db = 20 * np.log10(rms + 1e-9)
    counts = np.histogram(notes_t, bins=np.arange(n + 1))[0] if n else np.zeros(0)
    return db, counts


def check(folder: Path, live_dir: Path | None = None) -> Report:
    """Automatic checks for one song folder; live_dir is the live library (Quickplay)."""
    from train import charts  # chart reading lives with the training tools
    rep = Report(Path(folder))
    folder = Path(folder)
    ini = tidy.read_ini(folder)
    if not ini.get("name", "").strip():
        rep.add("red", "song.ini has no song title")
    if not ini.get("artist", "").strip():
        rep.add("yellow", "song.ini has no artist (the song still plays, but sorting and Tidy names need it)")
    if not ((folder / "album.jpg").exists() or (folder / "album.png").exists()):
        rep.add("info", "No album cover (Find cover art, below)")
    if library.is_downloaded(folder):
        # someone else's finished chart: the note checks below are for RockWizard's own charts
        if not any((folder / n).exists() for n in ("notes.mid", "notes.chart")):
            rep.add("red", "No chart (notes.mid or notes.chart) in the folder")
        if not any(p.suffix.lower() in AUDIO_EXT for p in folder.iterdir()):
            rep.add("red", "No audio files in the folder")
        rep.add("info", f"Downloaded chart by {ini.get('charter') or 'unknown'}: only the basics are checked")
        return rep
    mid = folder / "notes.mid"
    if not mid.exists():
        rep.add("red", "No chart (notes.mid) in the folder")
        return rep
    try:
        lv = charts.parse_levels(mid)
    except Exception as e:
        rep.add("red", f"The chart can't be read: {e}")
        return rep
    tpq, tm = lv["tpq"], lv["tempo"]
    sec = lambda tick: tm.sec(tick)

    # audio
    stems = {p.stem.lower(): p for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXT
             and not p.stem.lower().startswith(("preview", "crowd"))}
    if not stems:
        rep.add("red", "No audio files in the folder")
        return rep
    lengths = {name: len(_decode_mono(p, 1000)) / 1000.0 for name, p in stems.items()}
    song_len = max(lengths.values())
    if max(lengths.values()) - min(lengths.values()) > 2.0:
        rep.add("red", "Audio files have different lengths: " +
                ", ".join(f"{n} {_clock(v)}" for n, v in lengths.items()))

    # tempo
    # the first beat is only the lead-in (silence up to the first detected beat, no notes), so
    # it can be any length without mattering in game; judge the tempos from the first beat on
    ticks, bpms = list(tm.ticks), [60e6 / t for t in tm.tempos]
    if len(bpms) > 1 and ticks[1] <= tpq:
        ticks, bpms = ticks[1:], bpms[1:]
    if bpms and (min(bpms) < 30 or max(bpms) > 400):
        rep.add("red", f"Tempo out of range ({min(bpms):.0f}-{max(bpms):.0f} BPM)")
    jumps = [sec(tk) for (tk, a, b) in zip(ticks[1:], bpms, bpms[1:]) if max(a, b) / min(a, b) > 1.5]
    if jumps:
        rep.add("yellow", f"Tempo jumps sharply {len(jumps)} time{'s' if len(jumps) != 1 else ''}",
                [(t, t + 2) for t in jumps[:10]])

    totals = {}
    for inst in ("guitar", "bass", "drums"):
        levels = lv.get(inst)
        if not levels or not levels["expert"]:
            continue
        Name = inst.capitalize()
        ex = levels["expert"]
        times = np.array([sec(n[0]) for n in ex])
        totals[inst] = len(ex)
        if times.max() > song_len + 1.0:
            rep.add("red", f"{Name}: notes after the audio ends (last note {_clock(times.max())}, "
                           f"audio {_clock(song_len)})")
        # every difficulty, each lighter than the one above
        sizes = {lvl: len(levels.get(lvl, [])) for lvl in LEVELS}
        empty = [lvl for lvl in LEVELS[1:] if sizes[lvl] == 0]
        if empty:
            rep.add("red", f"{Name}: no notes on {', '.join(empty)}")
        for hi, lo in zip(LEVELS, LEVELS[1:]):
            if sizes[lo] > sizes[hi]:
                rep.add("yellow", f"{Name}: {lo.capitalize()} has more notes than {hi.capitalize()}")
        # playable?
        if inst == "drums":
            per_tick = {}
            for tick, lane, _ in ex:
                per_tick.setdefault(tick, []).append(lane)
            bad = [sec(t) for t, lanes in per_tick.items() if sum(1 for l in lanes if l) > 2]
            if bad:
                rep.add("red", f"Drums: {len(bad)} moment{'s' if len(bad) != 1 else ''} need three hands",
                        [(t, t + 1) for t in bad[:10]])
            lane_ticks = {}
            for tick, lane, _ in ex:
                lane_ticks.setdefault(lane, []).append(sec(tick))
        else:
            big = [sec(t) for t, lanes in ex if len(lanes) > 3]
            if big:
                rep.add("red", f"{Name}: {len(big)} chord{'s' if len(big) != 1 else ''} of 4+ frets",
                        [(t, t + 1) for t in big[:10]])
            lane_ticks = {}
            for tick, lanes in ex:
                for l in lanes:
                    lane_ticks.setdefault(l, []).append(sec(tick))
        too_fast = []
        for ts in lane_ticks.values():
            ts = np.sort(ts)
            too_fast += list(ts[1:][np.diff(ts) < 0.03])
        if too_fast:
            rep.add("red", f"{Name}: {len(too_fast)} note{'s' if len(too_fast) != 1 else ''} too close together "
                           "on one fret/pad (under 30 ms)", [(t, t + 1) for t in sorted(too_fast)[:10]])
        off = [t for t in (n[0] for n in ex) if t % (tpq // 8) and t % (tpq // 6)]
        if len(off) > 0.1 * len(ex):
            rep.add("yellow", f"{Name}: {len(off) / len(ex):.0%} of notes off the beat grid")

        # does the chart follow the audio? (only for the instrument's own stem)
        stem = stems.get(inst)
        if stem is not None:
            db, counts = _activity(stem, times, int(song_len) + 1)
            if len(db) > 20:
                playing = db > max(np.percentile(db, 60) - 6, -45)
                quiet = db < min(np.percentile(db, 15), -50)
                silent_chart = np.convolve(counts, np.ones(4), "same") == 0
                missed = _spans(playing & silent_chart, 6)
                phantom = _spans(quiet & (counts >= 2), 3)
                if missed:
                    rep.add("yellow", f"{Name}: plays but the chart is silent for {len(missed)} stretch"
                                      f"{'es' if len(missed) != 1 else ''} (" +
                            ", ".join(f"{_clock(a)}-{_clock(b)}" for a, b in missed[:6]) + ")", missed)
                if phantom:
                    rep.add("yellow", f"{Name}: notes where the {inst} stem is nearly silent (" +
                            ", ".join(f"{_clock(a)}-{_clock(b)}" for a, b in phantom[:6]) +
                            "), possibly another instrument", phantom)

    if not totals:
        rep.add("red", "The chart has no guitar, bass or drum notes")
    else:
        tiers = ", ".join(f"{k} {ini.get(f'diff_{k}', '?')}" for k in totals)
        rep.add("info", "Notes (Expert): " + ", ".join(f"{k} {v}" for k, v in totals.items()) +
                f"; difficulty {tiers}")

    # already live?
    if live_dir is not None and Path(live_dir).exists():
        a, t = library.names_of(folder)
        same = [p for p in library.find_existing(Path(live_dir), a, t, ours_only=False)
                if p.resolve() != folder.resolve()]
        for p in same:
            if library.is_ours(p):
                rep.add("yellow", f"An earlier RockWizard version is live (“{p.name}”); publishing replaces it")
            else:
                rep.add("yellow", f"A human-made chart of this song is already live (“{p.name}”)")
    return rep


def status(folder: Path) -> str:
    """draft, reviewed or published (song.ini chc_status)."""
    return tidy.read_ini(folder).get("chc_status", "draft") or "draft"


def set_status(folder: Path, value: str) -> None:
    ini = Path(folder) / "song.ini"
    try:
        text = ini.read_text(encoding="utf-8-sig")
    except OSError:
        return
    if re.search(r"(?mi)^\s*chc_status\s*=", text):
        text = re.sub(r"(?mi)^(\s*chc_status\s*=).*$", rf"\1 {value}", text)
    else:
        text = text.rstrip("\n") + f"\nchc_status = {value}\n"
    ini.write_text(text, encoding="utf-8")


def publish(folder: Path, live_dir: Path, force: bool = False) -> tuple[bool, str, Path | None]:
    """Move a song into the live library. Refuses with red findings (unless force) or when a
    human-made chart of the song is already live (unless force); an earlier RockWizard version
    is backed up first. Returns (done, message, new folder)."""
    folder, live_dir = Path(folder), Path(live_dir)
    rep = check(folder, live_dir)
    a, t = library.names_of(folder)
    same = [p for p in library.find_existing(live_dir, a, t, ours_only=False) if p.resolve() != folder.resolve()]
    human = [p for p in same if not library.is_ours(p)]
    reasons = []
    if rep.verdict == "red":
        reasons.append("The checks found red problems")
    if human:
        reasons.append(f"A human-made chart is already live (“{human[0].name}”), so both would show up")
    if reasons and not force:
        return False, ". ".join(reasons), None
    for p in same:
        if library.is_ours(p):
            library.backup(p, "replaced when publishing", move=True)
    dest = live_dir / folder.name
    if dest.exists():
        return False, f"“{dest.name}” already exists in the live folder", None
    set_status(folder, "published")
    shutil.move(str(folder), str(dest))
    return True, f"Published to {dest}", dest
