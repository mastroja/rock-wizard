"""Read charted songs from a Clone Hero library into note lists (seconds, lanes)."""
import bisect
import re
from pathlib import Path

import mido

TRACKS = {"PART GUITAR": "guitar", "PART BASS": "bass", "PART DRUMS": "drums", "PART DRUM": "drums"}
STEM_EXT = (".ogg", ".opus", ".mp3", ".wav")


def read_ini(path: Path) -> dict:
    out = {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r"\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$", line)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


class TempoMap:
    def __init__(self, mid: mido.MidiFile):
        events, tick = [], 0
        for m in mid.tracks[0]:
            tick += m.time
            if m.type == "set_tempo":
                events.append((tick, m.tempo))
        if not events or events[0][0] != 0:
            events.insert(0, (0, 500000))
        self.tpq = mid.ticks_per_beat
        self.ticks, self.secs, self.tempos = [], [], []
        t = 0.0
        for i, (tk, tempo) in enumerate(events):
            if i:
                t += (tk - events[i - 1][0]) / self.tpq * events[i - 1][1] / 1e6
            self.ticks.append(tk)
            self.secs.append(t)
            self.tempos.append(tempo)

    def sec(self, tick: int) -> float:
        i = bisect.bisect_right(self.ticks, tick) - 1
        return self.secs[i] + (tick - self.ticks[i]) / self.tpq * self.tempos[i] / 1e6


def _notes(track):
    """[(tick, note, off_tick)] for every note-on/off pair."""
    open_, out, tick = {}, [], 0
    for m in track:
        tick += m.time
        if m.type == "note_on" and m.velocity > 0:
            open_.setdefault(m.note, []).append(tick)
        elif m.type in ("note_off", "note_on") and open_.get(m.note):
            out.append((open_[m.note].pop(0), m.note, tick))
    return out


def solo_spans(path: Path, delay: float = 0.0) -> list[tuple[float, float]]:
    """Guitar solo sections marked in a notes.mid (note 103), in seconds."""
    mid = mido.MidiFile(str(path), clip=True)
    tm = TempoMap(mid)
    for tr in mid.tracks:
        if tr.name.strip().upper() == "PART GUITAR":
            return [(tm.sec(a) + delay, tm.sec(b) + delay) for a, n, b in _notes(tr) if n == 103]
    return []


def parse_mid(path: Path) -> dict:
    """Return {'guitar': [(t, lanes)], 'bass': [...], 'drums': [(t, lane, tom)], 'five_lane': bool}.

    guitar/bass lanes is a sorted tuple of 0-4. drums lane: 0 kick, 1 red, 2 yellow, 3 blue, 4 green
    (orange in five-lane charts); tom is True for tom hits on yellow/blue/green.
    """
    mid = mido.MidiFile(str(path), clip=True)
    tm = TempoMap(mid)
    out = {"guitar": [], "bass": [], "drums": [], "five_lane": False}
    for tr in mid.tracks:
        kind = TRACKS.get(tr.name.strip().upper())
        if not kind:
            continue
        notes = _notes(tr)
        if kind == "drums":
            toms = {n: [(a, b) for a, nn, b in notes if nn == n] for n in (110, 111, 112)}
            if any(n == 101 for _, n, _ in notes):
                out["five_lane"] = True
            hits = {}
            if out["five_lane"]:
                # five-lane kit -> four-lane pro drums, the way Clone Hero converts it:
                # yellow cymbal, blue tom, orange -> green cymbal, green -> green tom
                five = {95: (0, False), 96: (0, False), 97: (1, False), 98: (2, False),
                        99: (3, True), 100: (4, False), 101: (4, True)}
                for tick, n, _ in sorted(notes, key=lambda x: (x[0], x[1])):
                    if n not in five:
                        continue
                    lane, tom = five[n]
                    if (tick, lane) in hits and n == 101:  # orange + green together
                        lane = 3
                    if (tick, lane) not in hits:
                        hits[(tick, lane)] = tom
            else:
                for tick, n, _ in notes:
                    lane = {95: 0, 96: 0, 97: 1, 98: 2, 99: 3, 100: 4}.get(n)
                    if lane is None:
                        continue
                    tom = lane in (2, 3, 4) and any(a <= tick < b for a, b in toms[110 + lane - 2])
                    hits[(tick, lane)] = tom
            out["drums"] = sorted((tm.sec(t), lane, tom) for (t, lane), tom in hits.items())
        else:
            groups, ends = {}, {}
            for tick, n, off in notes:
                if 96 <= n <= 100:
                    groups.setdefault(tick, set()).add(n - 96)
                    ends[tick] = max(ends.get(tick, 0), off)
            forced_hopo = [(a_, b_) for a_, n, b_ in notes if n == 101]
            forced_strum = [(a_, b_) for a_, n, b_ in notes if n == 102]
            # snap near-simultaneous notes (<= 10 ticks apart) into one chord
            merged, last = [], None
            for tick in sorted(groups):
                if last is not None and tick - last[0] <= 10:
                    last[1] |= groups[tick]
                    last[2] = max(last[2], ends[tick])
                else:
                    last = [tick, set(groups[tick]), ends[tick]]
                    merged.append(last)
            thr = mid.ticks_per_beat // 3 + 1  # natural HOPO distance
            res, prev = [], None
            for tick, lanes, end in merged:
                natural = (prev is not None and tick - prev[0] <= thr and len(lanes) == 1
                           and lanes != prev[1])
                f_h = any(a_ <= tick < b_ for a_, b_ in forced_hopo)
                f_s = any(a_ <= tick < b_ for a_, b_ in forced_strum)
                hopo = (natural and not f_s) or (f_h and prev is not None)
                length = tm.sec(end) - tm.sec(tick) if end - tick > mid.ticks_per_beat // 3 else 0.0
                res.append((tm.sec(tick), tuple(sorted(lanes)), round(length, 3), bool(hopo)))
                prev = (tick, lanes)
            out[kind] = res
    return out


LEVELS = {"expert": 96, "hard": 84, "medium": 72, "easy": 60}


def parse_levels(path: Path) -> dict:
    """Every difficulty of a chart, in ticks: {"tpq", "tempo": TempoMap, "five_lane",
    "guitar"/"bass": {level: [(tick, lanes)]}, "drums": {level: [(tick, lane, tom)]}}.
    Used to learn how charters reduce Expert to Hard, Medium and Easy."""
    mid = mido.MidiFile(str(path), clip=True)
    out = {"tpq": mid.ticks_per_beat, "tempo": TempoMap(mid), "five_lane": False}
    for tr in mid.tracks:
        kind = TRACKS.get(tr.name.strip().upper())
        if not kind:
            continue
        notes = _notes(tr)
        if kind == "drums" and any(n == 101 for _, n, _ in notes):
            out["five_lane"] = True
        toms = {n: [(a, b) for a, nn, b in notes if nn == n] for n in (110, 111, 112)}
        levels = {}
        for level, base in LEVELS.items():
            if kind == "drums":
                hits = set()
                for tick, n, _ in notes:
                    lane = n - base if base <= n <= base + 4 else (0 if level == "expert" and n == 95 else None)
                    if lane is None:
                        continue
                    tom = lane in (2, 3, 4) and any(a <= tick < b for a, b in toms[110 + lane - 2])
                    hits.add((tick, lane, tom))
                levels[level] = sorted(hits)
            else:
                by = {}
                for tick, n, _ in notes:
                    if base <= n <= base + 4:
                        by.setdefault(tick, set()).add(n - base)
                levels[level] = [(t, tuple(sorted(l))) for t, l in sorted(by.items())]
        out[kind] = levels
    return out


def beat_times(mid: mido.MidiFile, tm: TempoMap) -> tuple[list[float], list[float]]:
    """(beats, downbeats) in seconds from the tempo map and time signatures (before song.ini delay)."""
    end = max(sum(m.time for m in tr) for tr in mid.tracks)
    sigs, tick = [], 0
    for m in mid.tracks[0]:
        tick += m.time
        if m.type == "time_signature":
            sigs.append((tick, m.numerator, m.denominator))
    if not sigs or sigs[0][0] != 0:
        sigs.insert(0, (0, 4, 4))
    beats, downs = [], []
    tpq = mid.ticks_per_beat
    for k, (start, num, den) in enumerate(sigs):
        stop = sigs[k + 1][0] if k + 1 < len(sigs) else end
        step = tpq * 4 // den
        n = 0
        t = start
        while t < stop:
            beats.append(tm.sec(t))
            if n % num == 0:
                downs.append(tm.sec(t))
            n += 1
            t += step
    return beats, downs


def find_stem(folder: Path, name: str):
    for ext in STEM_EXT:
        p = folder / f"{name}{ext}"
        if p.exists():
            return p
    return None


def drum_stems(folder: Path) -> list[Path]:
    p = find_stem(folder, "drums")
    if p:
        return [p]
    parts = [find_stem(folder, f"drums_{i}") for i in range(1, 5)]
    return [q for q in parts if q]


SKIP_PREFIX = ("preview", "crowd", "album")


def mix_stems(folder: Path) -> list[Path]:
    """Every audio file that makes up the song (song.ogg alone is only the leftover backing)."""
    return sorted(p for p in folder.iterdir()
                  if p.suffix.lower() in STEM_EXT and not p.stem.lower().startswith(SKIP_PREFIX))


def index_library(root: Path, convert_five: bool = False) -> list[dict]:
    """One record per usable song folder with chart notes and the stems to learn from.

    convert_five keeps five-lane drum charts (converted to four-lane) instead of dropping them.
    """
    songs, extra = [], []
    for mid_path in sorted(root.rglob("notes.mid")):
        folder = mid_path.parent
        ini = read_ini(folder / "song.ini")
        if ini.get("chc_filled"):  # partly generated by this app (chc.rechart): not a human reference
            continue
        try:
            delay = float(ini.get("delay", "0") or 0) / 1000.0
        except ValueError:
            delay = 0.0
        try:
            chart = parse_mid(mid_path)
        except Exception:
            continue
        try:
            mid = mido.MidiFile(str(mid_path), clip=True)
            bt, dt = beat_times(mid, TempoMap(mid))
        except Exception:
            bt, dt = [], []
        rec = {"folder": str(folder), "name": folder.name, "delay": delay,
               "beats": [t + delay for t in bt], "downbeats": [t + delay for t in dt],
               "five_lane": chart["five_lane"] or ini.get("five_lane_drums", "").lower() == "true",
               "stems": {}, "notes": {}}
        g, b = find_stem(folder, "guitar"), find_stem(folder, "bass")
        d = drum_stems(folder)
        full = find_stem(folder, "song")
        if full:
            rec["stems"]["song"] = [str(full)]
        allstems = mix_stems(folder)
        if allstems:
            rec["stems"]["mix"] = [str(x) for x in allstems]
        # charts are kept even without an isolated stem: Demucs can make one from the full mix
        if chart["guitar"]:
            rec["notes"]["guitar"] = [(t + delay, list(l), ln, int(h)) for t, l, ln, h in chart["guitar"]]
            if g:
                rec["stems"]["guitar"] = [str(g)]
        if chart["bass"]:
            rec["notes"]["bass"] = [(t + delay, list(l), ln, int(h)) for t, l, ln, h in chart["bass"]]
            if b:
                rec["stems"]["bass"] = [str(b)]
        if chart["drums"] and (convert_five or not rec["five_lane"]):
            rec["notes"]["drums"] = [(t + delay, lane, int(tom)) for t, lane, tom in chart["drums"]]
            if d:
                rec["stems"]["drums"] = [str(x) for x in d]
        has_stem = any(k in rec["stems"] for k in ("guitar", "bass", "drums"))
        if has_stem:
            songs.append(rec)
        elif rec["notes"] and "mix" in rec["stems"]:
            extra.append(rec)
    # songs with only a full mix go last so cached features (named by position) still line up
    return songs + extra
