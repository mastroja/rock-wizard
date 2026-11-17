"""A chart's Expert notes as plain numbers, for the note highway the web app draws.

Reads notes.mid (any resolution) or notes.chart, whoever made it, and never changes anything.
Unlike chc.rechart's readers it skips what it doesn't know instead of refusing the chart: this is
only for looking.

notes(folder) -> {"length": seconds, "parts": {"guitar": [[time, lanes, hold], ...], ...}}
  lanes is a bit mask. Guitar and bass: bits 0-4 are green, red, yellow, blue, orange and bit 5 is
  an open note. Drums: bit 0 is the kick and bits 1-4 are red, yellow, blue, green.
  hold is the sustain in seconds (0 for a plain note).
"""
import bisect
import re
from pathlib import Path

import mido

from . import tidy

MID_TRACKS = {"PART GUITAR": "guitar", "T1 GEMS": "guitar", "PART BASS": "bass", "PART RHYTHM": "rhythm",
              "PART DRUMS": "drums"}
CHART_PARTS = {"ExpertSingle": "guitar", "ExpertDoubleBass": "bass", "ExpertDoubleRhythm": "rhythm",
               "ExpertDrums": "drums"}
EXPERT = 96
OPEN = 32  # bit 5


class _Clock:
    """Ticks to seconds through a tempo map: [(tick, microseconds per beat)]."""

    def __init__(self, tempos, tpq: int, shift: float = 0.0):
        tempos = sorted(tempos) or [(0, 500000)]
        if tempos[0][0]:
            tempos.insert(0, (0, 500000))
        self.tpq, self.ticks, self.us, self.at = tpq, [t for t, _ in tempos], [u for _, u in tempos], [shift]
        for i in range(1, len(tempos)):
            self.at.append(self.at[-1] + (self.ticks[i] - self.ticks[i - 1]) / tpq * self.us[i - 1] / 1e6)

    def sec(self, tick: int) -> float:
        i = bisect.bisect_right(self.ticks, tick) - 1
        return self.at[i] + (tick - self.ticks[i]) / self.tpq * self.us[i] / 1e6


def _pack(hits: dict, clock: _Clock, min_hold: int) -> list:
    """hits: tick -> [lanes, longest length in ticks]."""
    out = []
    for tick in sorted(hits):
        lanes, length = hits[tick]
        hold = clock.sec(tick + length) - clock.sec(tick) if length >= min_hold else 0.0
        out.append([round(clock.sec(tick), 3), lanes, round(hold, 3)])
    return out


def _from_mid(path: Path, delay: float) -> dict:
    mid = mido.MidiFile(str(path), clip=True)
    tpq = mid.ticks_per_beat
    tempos, tracks = [], {}
    for t in mid.tracks:
        tick = 0
        for m in t:
            tick += m.time
            if m.type == "set_tempo":
                tempos.append((tick, m.tempo))
        inst = MID_TRACKS.get((t.name or "").strip().upper())
        if inst and inst not in tracks:
            tracks[inst] = t
    clock = _Clock(tempos, tpq, delay)
    parts = {}
    for inst, t in tracks.items():
        drums = inst == "drums"
        opens = any(m.is_meta and m.type == "text" and "ENHANCED_OPENS" in m.text for m in t)
        on, hits, tick = {}, {}, 0
        for m in t:
            tick += m.time
            if m.type == "note_on" and m.velocity:
                on.setdefault(m.note, tick)
            elif m.type in ("note_on", "note_off") and m.note in on:
                start, n = on.pop(m.note), m.note
                if drums:
                    bit = 1 if n in (95, 96) else 1 << (min(n, 100) - 96) if 97 <= n <= 101 else 0
                else:
                    bit = OPEN if n == 95 and opens else 1 << (n - 96) if 96 <= n <= 100 else 0
                if bit:
                    h = hits.setdefault(start, [0, 0])
                    h[0] |= bit
                    h[1] = max(h[1], 0 if drums else tick - start)
        if hits:
            parts[inst] = _pack(hits, clock, tpq // 3)
    return parts


def _from_chart(path: Path, delay: float) -> dict:
    res, offset, tempos, raw, section = 192, 0.0, [], {}, None
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        ln = line.strip()
        head = re.fullmatch(r"\[(.+)\]", ln)
        if head:
            section = head.group(1)
            continue
        if section == "Song":
            m = re.match(r"(\w+)\s*=\s*\"?([^\"]*)\"?", ln)
            if m and m.group(1) == "Resolution":
                res = int(float(m.group(2)))
            elif m and m.group(1) == "Offset":
                try:
                    offset = float(m.group(2))
                except ValueError:
                    pass
            continue
        m = re.match(r"(\d+)\s*=\s*(\w+)\s*(.*)", ln)
        if not m:
            continue
        tick, kind, a = int(m.group(1)), m.group(2), m.group(3).split()
        if section == "SyncTrack" and kind == "B" and a:
            tempos.append((tick, int(round(60e9 / int(a[0])))))
        elif section in CHART_PARTS and kind == "N" and len(a) >= 2:
            raw.setdefault(CHART_PARTS[section], []).append((tick, int(a[0]), int(a[1])))
    clock = _Clock(tempos, res, delay + offset)
    parts = {}
    for inst, rows in raw.items():
        drums, hits = inst == "drums", {}
        for tick, n, length in rows:
            if drums:
                bit = 1 if n in (0, 32) else 1 << min(n, 4) if 1 <= n <= 5 else 0
            else:
                bit = OPEN if n == 7 else 1 << n if 0 <= n <= 4 else 0
            if bit:
                h = hits.setdefault(tick, [0, 0])
                h[0] |= bit
                h[1] = max(h[1], 0 if drums else length)
        if hits:
            parts[inst] = _pack(hits, clock, 1)
    return parts


def notes(folder: Path) -> dict:
    folder = Path(folder)
    ini = tidy.read_ini(folder)
    try:
        delay = float(ini.get("delay", "0") or 0) / 1000
    except ValueError:
        delay = 0.0
    mid, chart = folder / "notes.mid", folder / "notes.chart"
    if mid.exists():
        parts = _from_mid(mid, delay)
    elif chart.exists():
        parts = _from_chart(chart, delay)
    else:
        raise FileNotFoundError("This song has no chart file")
    if "bass" not in parts and "rhythm" in parts:
        parts["bass"] = parts["rhythm"]  # co-op charts often carry a rhythm part in place of bass
    parts.pop("rhythm", None)
    last = max((p[-1][0] + p[-1][2] for p in parts.values() if p), default=0.0)
    try:
        length = float(ini.get("song_length", "0") or 0) / 1000
    except ValueError:
        length = 0.0
    return {"length": round(max(length, last + 2), 2), "parts": parts}
