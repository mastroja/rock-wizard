"""Score the vocal charter against human PART VOCALS charts, on Demucs vocal stems.

python -m train.eval_vocals [val|test] [--limit N] [--no-lyrics]

Pitch and Whisper results are cached in data/v3/vocal_cache, so retuning chc/vocals.py is quick.
Only totals are printed, never lyric text."""
import argparse
import json
from difflib import SequenceMatcher
from pathlib import Path

import mido
import numpy as np
import soundfile as sf

from chc import vocals
from . import charts, data

CACHE = data.V3 / "vocal_cache"


def human(song) -> list[tuple[float, float, int, str]] | None:
    """(start, end, pitch, lyric) of a song's PART VOCALS, or None if it isn't charted."""
    path = Path(song["folder"]) / "notes.mid"
    if not path.exists():
        return None
    mid = mido.MidiFile(str(path), clip=True)
    track = next((t for t in mid.tracks if t.name.strip().upper() == "PART VOCALS"), None)
    if track is None:
        return None
    tm, delay = charts.TempoMap(mid), song.get("delay", 0.0)
    lyr, tick = {}, 0
    for e in track:
        tick += e.time
        if e.type in ("lyrics", "text") and not e.text.startswith("["):
            lyr[tick] = e.text
    out = [(tm.sec(a) + delay, tm.sec(b) + delay, n, lyr.get(a, ""))
           for a, n, b in charts._notes(track) if vocals.LOW <= n <= vocals.HIGH]
    return sorted(out) if len(out) >= 30 and len(lyr) >= 30 else None


def cached(song, no_lyrics):
    """(pitch frames, heard words, published lines) for a song, computing what's missing."""
    CACHE.mkdir(parents=True, exist_ok=True)
    sid = data.song_id(song)
    pf, wf, lf = CACHE / f"{sid}.npy", CACHE / f"{sid}.words.json", CACHE / f"{sid}.lines.json"
    x, sr = None, 0
    if not pf.exists() or not wf.exists():
        x, sr = sf.read(data.V3 / "stems" / sid / "ft_vocals.flac", dtype="float32")
    if not pf.exists():
        np.save(pf, vocals.track_pitch(x, sr)[0])
    midi = np.load(pf)
    if not wf.exists():
        heard = vocals.transcribe(x, sr, midi)
        wf.write_text(json.dumps([[w.text, w.start, w.end, w.line_end] for w in heard]), encoding="utf-8")
    heard = [vocals.Word(*w) for w in json.loads(wf.read_text(encoding="utf-8"))]
    heard = [w for w in heard if vocals._is_sung(w, midi)]
    if no_lyrics:
        return midi, heard, None
    if not lf.exists():
        artist, _, title = song["name"].partition(" - ")
        lf.write_text(json.dumps(vocals.lookup_lyrics(artist, title, len(midi) * vocals.HOP)), encoding="utf-8")
    return midi, heard, json.loads(lf.read_text(encoding="utf-8"))


def timeline(notes, n):
    """Pitch per HOP frame from (start, end, pitch, ...) notes; 0 = silence."""
    line = np.zeros(n, int)
    for a, b, p, *_ in notes:
        line[int(a / vocals.HOP):int(b / vocals.HOP)] = p
    return line


def joined(lyrics):
    """Words of a lyric list, syllables put back together."""
    words, part = [], ""
    for s in lyrics:
        if s.startswith("+") or not s:
            continue
        t = s.rstrip("#^$%/ ")
        if t.endswith("-") or t.endswith("="):
            part += t[:-1]
        else:
            words.append(vocals._key(part + t))
            part = ""
    return [w for w in words if w]


def score(ours, theirs, n, tally):
    a, b = timeline([(x.start, x.end, x.pitch) for x in ours], n), timeline(theirs, n)
    tally["their_sung"] += int((b > 0).sum())
    tally["our_sung"] += int((a > 0).sum())
    both = (a > 0) & (b > 0)
    tally["both_sung"] += int(both.sum())
    # what the game checks: YARG accepts any octave and about a semitone of error
    d = np.abs(a[both] - b[both]) % 12
    tally["on_pitch"] += int((np.minimum(d, 12 - d) <= 1).sum())
    ot = np.array([x.start for x in ours if x.lyric != "+"])
    ht = np.array([h[0] for h in theirs if not h[3].startswith("+")])
    for tol, name in ((0.1, "hit100"), (0.05, "hit50")):
        tally[name] += int(sum(1 for t in ht if len(ot) and np.abs(ot - t).min() <= tol))
    tally["their_syl"] += len(ht)
    tally["our_syl"] += len(ot)
    w1, w2 = joined([x.lyric for x in ours]), joined([h[3] for h in theirs])
    tally["their_words"] += len(w2)
    tally["word_hits"] += sum(b.size for b in SequenceMatcher(None, w2, w1, autojunk=False).get_matching_blocks())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("split", nargs="?", default="val")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-lyrics", action="store_true")
    a = ap.parse_args()
    tally = dict.fromkeys(("their_sung our_sung both_sung on_pitch hit100 hit50 their_syl our_syl "
                           "their_words word_hits").split(), 0)
    done = found = 0
    for song in data.Layout("v3").index():
        if data.split_of(song) != a.split or not (data.V3 / "stems" / data.song_id(song) / "ft_vocals.flac").exists():
            continue
        theirs = human(song)
        if theirs is None:
            continue
        midi, heard, lines = cached(song, a.no_lyrics)
        found += lines is not None and vocals.align_lyrics(lines, heard, midi) is not None
        ours = vocals.from_parts(midi, heard, lines)
        score(ours, theirs, len(midi), tally)
        done += 1
        if a.limit and done >= a.limit:
            break
    t = tally
    print(f"{done} {a.split} songs, published lyrics used on {found}")
    print(f"singing time: found {t['both_sung'] / max(1, t['their_sung']):.1%} of theirs, "
          f"{t['both_sung'] / max(1, t['our_sung']):.1%} of ours is real")
    print(f"on pitch (any octave, within a semitone) while both sing: {t['on_pitch'] / max(1, t['both_sung']):.1%}")
    print(f"syllable starts within 100 ms: {t['hit100'] / max(1, t['their_syl']):.1%}, 50 ms: "
          f"{t['hit50'] / max(1, t['their_syl']):.1%} | ours {t['our_syl']} vs theirs {t['their_syl']}")
    print(f"words matching the human chart: {t['word_hits'] / max(1, t['their_words']):.1%}")


if __name__ == "__main__":
    main()
