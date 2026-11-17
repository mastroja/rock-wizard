"""Drop-in pipeline: audio file -> stems -> rough chart -> Clone Hero song folder."""
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import analysis, audio, chart, feedback, ml, phrases, rating, structure, version, vocals

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ROOT = Path(__file__).resolve().parent.parent
# BS-RoFormer SW runs in its own Python environment (see train.separate_sw)
SW_PYTHON = ROOT / ".tmp" / "sepenv" / "Scripts" / "python.exe"
SW_MODEL = ROOT / ".tmp" / "sepmodels" / "BS-Roformer-SW.ckpt"
STEM_TO_CH = {"other": "guitar", "bass": "bass", "drums": "drums", "vocals": "vocals"}
STEPS = ["Reading your song", "Splitting it into instruments", "Finding the beat",
         "Writing the notes", "Saving the song folder"]
SEP = re.compile(r'[<>:"/\\|?*]')


class Cancelled(Exception):
    pass


@dataclass
class Result:
    song_dir: Path
    preview_dir: Path
    stems: dict = field(default_factory=dict)   # label -> preview wav path
    counts: dict = field(default_factory=dict)  # part -> expert note count
    bpm: float = 0.0
    seconds: float = 0.0
    replaced: list = field(default_factory=list)  # backups of earlier versions it replaced


def parse_name(path: Path) -> tuple[str, str]:
    stem = path.stem
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return a.strip(), t.strip()
    return "", stem.replace("_", " ").strip()


def _separate(wav: Path, out: Path, model: str, on_progress, stop: threading.Event):
    if model == "sw":
        cmd = [str(SW_PYTHON), "-m", "chc.sw_worker", str(wav), str(out)]
    else:
        cmd = [sys.executable, "-m", "chc.worker", str(wav), str(out), model]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            cwd=ROOT, creationflags=NO_WINDOW)
    tail = []
    for line in proc.stdout:
        if stop.is_set():
            proc.terminate()
            proc.wait()
            raise Cancelled()
        if line.startswith("PROGRESS"):
            on_progress(float(line.split()[1]))
        else:
            tail.append(line.rstrip())
    if proc.wait() != 0:
        raise RuntimeError("Splitting failed:\n" + "\n".join(tail[-6:]))


def guitar_stem_source() -> str:
    """The extra separator the guitar model needs on top of htdemucs_ft: 's6', 'sw' or '' (none)."""
    src = ml.stem_source("guitar") if ml.available("guitar") else "ft"
    if src == "sw" and not (SW_PYTHON.exists() and SW_MODEL.exists()):
        print("guitar model wants BS-RoFormer SW stems but its environment is missing: using Demucs' other",
              file=sys.stderr)
        return ""
    return src if src in ("s6", "sw") else ""


def sw_wanted() -> bool:
    """Whether any model the app charts with needs the BS-RoFormer SW separator (and it is there)."""
    if not (SW_PYTHON.exists() and SW_MODEL.exists()):
        return False
    return any(ml.available(k) and ml.stem_source(k) == "sw" for k in ("guitar", "bass"))


def extra_stems() -> bool:
    """Whether model_stems will run a separator on top of htdemucs_ft (for progress bars)."""
    return bool(guitar_stem_source()) or sw_wanted()


def stem_label(guitar_wav: Path, other_wav: Path) -> str:
    """What guitar_stem gave the guitar model, for the song's version record (chc.version)."""
    if Path(guitar_wav) == Path(other_wav):
        return "ft"
    return "s6" if Path(guitar_wav).parent.name == "six" else "sw"


def model_stems(wav: Path, work: Path, other_wav: Path, bass_wav: Path, on_progress,
                stop: threading.Event) -> tuple[Path, Path]:
    """The stems the guitar and bass models were trained on, from the whole song `wav`, given
    Demucs' "other" and bass stems. Falls back to Demucs' if the SW separator fails (the SW-trained
    models still chart well from those)."""
    src = guitar_stem_source()
    bass_sw = ml.available("bass") and ml.stem_source("bass") == "sw" and SW_PYTHON.exists() and SW_MODEL.exists()
    guitar, bass = other_wav, bass_wav
    if src == "s6":
        # htdemucs_6s splits guitar from keys/synths
        _separate(wav, work / "six", "htdemucs_6s", on_progress if not bass_sw else lambda f: on_progress(0.5 * f), stop)
        guitar = work / "six" / "guitar.wav"
    if src == "sw" or bass_sw:
        try:
            _separate(wav, work / "sw", "sw", on_progress if src != "s6" else lambda f: on_progress(0.5 + 0.5 * f), stop)
            if src == "sw":
                guitar = work / "sw" / "guitar.wav"
            if bass_sw:
                bass = work / "sw" / "bass.wav"
        except Cancelled:
            raise
        except Exception as e:
            print(f"SW stems failed, using Demucs' other and bass: {e}", file=sys.stderr)
    return guitar, bass


def chart_stems(guitar_wav: Path, bass_wav: Path, drums_wav: Path, mix_wav: Path,
                progress=lambda f: None, check=lambda: None, notes_started=lambda: None,
                vocal_wav: Path | None = None, artist: str = "", title: str = "") -> dict:
    """Separated stems (wav) and the full mix -> the chart's notes per part plus its tempo map.
    Shared by making a new song and updating an existing song's chart from its own stems."""
    drums_x, sr = audio.read_wav(drums_wav)
    beatmap = None
    if ml.available("mix"):
        mix_x, mix_sr = audio.read_wav(mix_wav)
        b, d = ml.beat_times(mix_x, mix_sr)
        if len(b) >= 16:
            beatmap = chart.BeatMap(b, d, clean=True)
    heur_drums, env = analysis.drum_hits(drums_x, sr)
    bpm, offset = analysis.estimate_tempo(env, sr)
    if beatmap is not None:
        bpm = beatmap.bpm
    check()
    progress(0.35)
    notes_started()
    other_x, _ = audio.read_wav(guitar_wav)
    bass_x, _ = audio.read_wav(bass_wav)
    feats = {}  # model input features per part, for finding repeated sections
    if ml.available("drums"):
        drum_timed, feats["drums"] = ml.drum_notes(drums_x, sr, with_features=True)
    else:
        lane_of = {"K": 0, "S": 1, "H": 2, "C": 4}
        drum_timed = [(t, lane_of[c], False) for t, c in heur_drums]
    check()
    progress(0.6)
    if ml.available("guitar"):
        gtimed, feats["guitar"] = ml.fretted_notes("guitar", other_x, sr, with_features=True)
    else:
        att = analysis.guitar_attacks(other_x, sr)
        gt_ = [t for t, _ in att]
        gtimed = list(zip(gt_, analysis.local_lanes([h for _, h in att], gt_)))
    check()
    progress(0.8)
    if ml.available("bass"):
        btimed, feats["bass"] = ml.fretted_notes("bass", bass_x, sr, with_features=True)
    else:
        bp = analysis.pitched_notes(bass_x, sr, 35, 300, sensitivity=1.1, min_gap=0.12)
        bt_ = [t for t, _ in bp]
        btimed = list(zip(bt_, analysis.local_lanes([p for _, p in bp], bt_)))
    if beatmap is not None:
        # every note on its bar's grid, and repeated sections charted the same way
        snap = True
        parts = {"guitar": gtimed, "bass": btimed, "drums": drum_timed}
        for k, f in feats.items():
            parts[k] = structure.make_consistent(k, parts[k], f, beatmap)
        gtimed, btimed, drum_timed = parts["guitar"], parts["bass"], parts["drums"]
    else:
        # No tracked beats: only snap to a fixed grid when the song really sits on one;
        # live or loose recordings are charted at their exact audio times instead.
        fit = chart.grid_fit([g[0] for g in gtimed] + [d[0] for d in drum_timed], bpm, offset)
        snap = fit < 0.15
    check()
    progress(0.9)
    sung = []
    if vocal_wav is not None and vocals.available():
        try:
            sung = vocals.chart(*audio.read_wav(vocal_wav), artist, title)
        except Exception as e:  # a song without a vocal track still beats no song
            print(f"vocal chart failed: {e}", file=sys.stderr)
    progress(1.0)
    return {"guitar": gtimed, "bass": btimed, "drums": drum_timed, "vocals": sung, "beatmap": beatmap,
            "bpm": bpm, "offset": offset, "snap": snap}


def write_chart(song_dir: Path, c: dict, title: str, artist: str, charter: str, length: float,
                details=None, preview_ms: int | None = None) -> dict:
    """notes.mid and song.ini (with difficulty ratings and song details) for chart_stems' result."""
    counts = chart.write_mid(song_dir / "notes.mid", c["bpm"], c["offset"], guitar=c["guitar"],
                             bass=c["bass"], drums=c["drums"], snap=c["snap"], beatmap=c["beatmap"],
                             vocals=c["vocals"])
    c["lead"] = chart.add_intro(song_dir / "notes.mid")  # the caller delays the audio to match
    phrases.add_to_mid(song_dir / "notes.mid")
    length += c["lead"]
    d = details
    # each instrument's difficulty tier for the song list, judged from its Expert chart
    tiers = {k: rating.tier(k, c[k]) for k in ("guitar", "bass", "drums") if c[k]}
    if preview_ms is None:
        preview_ms = int(min(30, max(0, length - 40)) * 1000)
    else:
        preview_ms += int(c["lead"] * 1000)
    chart.write_ini(song_dir / "song.ini", title, artist, charter, int(length * 1000), counts,
                    preview_ms=preview_ms, album=d.album if d else "", genre=d.genre if d else "",
                    year=d.year if d else "", tiers=tiers, extra=version.fields(c))
    from . import library
    library.save_original(song_dir)  # what the app wrote, to compare hand fixes against later
    return counts


UPDATE_STEPS = ["Reading the song's audio", "Finding the guitar", "Finding the beat", "Writing the new notes"]


def update_song(song_dir: Path, on_step=lambda i: None, on_progress=lambda frac: None,
                stop: threading.Event | None = None) -> Path:
    """Re-chart a song this app made, from the stems already in its folder (no original audio
    needed). The guitar model hears what it was trained on: the folder keeps Demucs' "other" stem
    for playing along, so the stems are added back into the whole song and that is split again for
    the guitar (guitar_stem). The old notes.mid and song.ini are backed up first; audio, cover,
    name and song details are kept. Returns the backup folder."""
    from . import library
    from .search import Details
    stop = stop or threading.Event()

    def check():
        if stop.is_set():
            raise Cancelled()
    if not library.is_ours(song_dir):
        raise ValueError(f"{song_dir.name} was not made by this app, so it is left alone")
    ini = library.tidy.read_ini(song_dir)
    work = Path(tempfile.mkdtemp(prefix="chc_update_"))
    try:
        on_step(0)
        on_progress(0.02)
        wavs = {n: audio.to_wav(song_dir / f"{n}.ogg", work / f"{n}.wav")
                for n in ("guitar", "bass", "drums", "vocals")}
        # the four stems add back up to the whole song, for finding the beat
        waves = [audio.read_wav(w) for w in wavs.values()]
        n = max(x.shape[-1] for x, _ in waves)
        mix = sum(np.pad(x, [(0, 0)] * (x.ndim - 1) + [(0, n - x.shape[-1])]) for x, _ in waves)
        audio.write_wav(work / "mix.wav", mix[None], waves[0][1])
        length = audio.duration(work / "mix.wav")
        check()
        on_step(1)
        on_progress(0.08)
        guitar_wav, bass_wav = model_stems(work / "mix.wav", work, wavs["guitar"], wavs["bass"],
                                           lambda f: on_progress(0.08 + 0.3 * f), stop)
        check()
        on_step(2)
        on_progress(0.4)
        c = chart_stems(guitar_wav, bass_wav, wavs["drums"], work / "mix.wav",
                        lambda f: on_progress(0.4 + 0.55 * f), check, lambda: on_step(3),
                        wavs["vocals"], ini.get("artist", ""), ini.get("name", ""))
        c["stems"] = stem_label(guitar_wav, wavs["guitar"])
        c["bass_stems"] = "ft" if bass_wav == wavs["bass"] else "sw"
        check()
        audio.ensure_writable(song_dir / name for name in library.OUR_STEMS)
        saved = library.backup(song_dir, "before chart update", files=["notes.mid", "song.ini", *library.OUR_STEMS])
        d = Details(ini.get("name", ""), ini.get("artist", ""), ini.get("album", ""),
                    ini.get("year", ""), ini.get("genre", ""))
        try:
            preview = int(float(ini.get("preview_start_time", "")))
        except ValueError:
            preview = None
        write_chart(song_dir, c, ini.get("name", song_dir.name), ini.get("artist", ""),
                    ini.get("charter", ""), length, d, preview)
        # fields write_chart doesn't write (review status, who added the song, ...) are kept,
        # except ratings: they were about the old chart (data/feedback.jsonl keeps them)
        new = library.tidy.read_ini(song_dir)
        library.tidy.write_ini(song_dir, {k: v for k, v in ini.items() if k not in new and not feedback.is_rating_key(k)})
        if c["lead"]:
            for name in library.OUR_STEMS:
                audio.add_lead(song_dir / name, c["lead"])
        on_progress(1.0)
        return saved
    finally:
        shutil.rmtree(work, ignore_errors=True)


def add_intro(song_dir: Path, dry: bool = False) -> float:
    """Give a chart this app already made its intro bars, without re-charting it: silent bars in
    notes.mid and the same silence on its stems. Returns the seconds added."""
    from . import library
    if not library.is_ours(song_dir):
        raise ValueError(f"{song_dir.name} was not made by this app, so it is left alone")
    mid = song_dir / "notes.mid"
    lead = chart.add_intro(mid, dry=True)
    if dry or not lead:
        return lead
    audio.ensure_writable(song_dir / name for name in library.OUR_STEMS)
    library.backup(song_dir, "before intro bars", files=["notes.mid", "song.ini", *library.OUR_STEMS])
    for name in library.OUR_STEMS:
        audio.add_lead(song_dir / name, lead)
    chart.add_intro(mid)
    kept = library.original_of(song_dir)
    if kept:
        chart.add_intro(kept)  # the kept original gets the same bars, so it stays in step with the chart
    ini = library.tidy.read_ini(song_dir)
    fields = {}
    for key, add in (("song_length", int(lead * 1000)), ("preview_start_time", int(lead * 1000))):
        try:
            fields[key] = int(float(ini.get(key, ""))) + add
        except ValueError:
            pass
    library.tidy.write_ini(song_dir, fields)
    return lead


def make_song(src: Path, artist: str, title: str, songs_dir: Path, charter: str = "",
              model: str = "htdemucs_ft", on_step=lambda i: None,
              on_progress=lambda frac: None, stop: threading.Event | None = None,
              details=None, cover: Path | None = None) -> Result:
    """Run everything. on_step(i) marks STEPS[i] as started; on_progress is 0..1 overall.
    details (search.Details) fills album/genre/year in song.ini; cover becomes album.jpg."""
    stop = stop or threading.Event()

    def check():
        if stop.is_set():
            raise Cancelled()

    name = SEP.sub("", f"{artist} - {title}" if artist else title).strip() or "Untitled"
    song_dir = songs_dir / name
    preview_dir = Path(tempfile.mkdtemp(prefix="chc_preview_"))
    work = Path(tempfile.mkdtemp(prefix="chc_work_"))
    try:
        on_step(0); on_progress(0.02)
        wav = audio.to_wav(src, work / "input.wav")
        length = audio.duration(wav)
        check()

        on_step(1)
        extra = extra_stems()
        span = 0.5 if extra else 0.65
        _separate(wav, preview_dir, model, lambda f: on_progress(0.05 + span * f), stop)
        check()
        guitar_wav, bass_wav = model_stems(wav, work, preview_dir / "other.wav", preview_dir / "bass.wav",
                                           lambda f: on_progress(0.55 + 0.15 * f), stop)
        check()

        on_step(2); on_progress(0.72)
        c = chart_stems(guitar_wav, bass_wav, preview_dir / "drums.wav", wav,
                        lambda f: on_progress(0.72 + 0.21 * f), check, lambda: on_step(3),
                        preview_dir / "vocals.wav", artist, title)
        c["stems"] = stem_label(guitar_wav, preview_dir / "other.wav")
        c["bass_stems"] = "ft" if bass_wav == preview_dir / "bass.wav" else "sw"
        check()

        on_step(4); on_progress(0.93)
        # earlier versions this app made of the same song (same or older name) go to backups
        from . import library
        replaced = library.prepare_target(songs_dir, artist, title, name)
        song_dir.mkdir(parents=True, exist_ok=True)
        counts = write_chart(song_dir, c, title, artist, charter, length, details)
        bpm = c["bpm"]
        if cover is not None and Path(cover).exists():
            shutil.copy(cover, song_dir / "album.jpg")
        for demucs_name, ch_name in STEM_TO_CH.items():
            audio.to_ogg(preview_dir / f"{demucs_name}.wav", song_dir / f"{ch_name}.ogg", c["lead"])
            check()
        on_progress(1.0)
        stems = {"Guitar & everything else": preview_dir / "other.wav",
                 "Bass": preview_dir / "bass.wav", "Drums": preview_dir / "drums.wav",
                 "Vocals": preview_dir / "vocals.wav"}
        return Result(song_dir, preview_dir, stems, counts, bpm, length, replaced)
    except BaseException:
        shutil.rmtree(preview_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
