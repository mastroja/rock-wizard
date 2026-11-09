"""Build a Clone Hero notes.mid and song.ini from timed note lists."""
from pathlib import Path

import mido
import numpy as np

TPQ = 480
GRID = TPQ // 4  # notes snap to 16ths
DIFF_BASE = {"expert": 96, "hard": 84, "medium": 72, "easy": 60}
# (min ticks between guitar notes, number of lanes used)
GUITAR_RULES = {"expert": (GRID, 5), "hard": (180, 5), "medium": (240, 4), "easy": (480, 3)}
# per drum lane (0 kick, 1 red, 2 yellow, 3 blue, 4 green): min ticks between hits, None = drop
DRUM_RULES = {
    "expert": [0, 0, 0, 0, 0],
    "hard": [180, 180, 240, 240, 240],
    "medium": [240, 480, 480, 480, 960],
    "easy": [480, 480, None, None, None],
}
TOM_MARK = {2: 110, 3: 111, 4: 112}
# BeatMap.fit_grid: how much better (in 16th-note steps moved) a bar must fit 8th-note triplets
# than straight 16ths to be charted as a shuffle, and below what margin a bar is undecided and
# follows the song's usual feel. Tuned on validation songs (train.eval_chart).
STRAIGHT_BIAS = 0.5
UNDECIDED = 0.3
LEAD_IN = 3.0  # seconds of empty highway before a chart's first note
INTRO_BPM = (60, 200)  # intro bars take the song's opening tempo, within these


class TempoMap:
    """Constant tempo with a lead-in beat that absorbs the first-beat offset."""

    def __init__(self, bpm: float, offset: float, snap: bool = True):
        self.spb = 60.0 / bpm
        self.lead = offset + self.spb  # first real beat (tick TPQ) falls at this time
        self.grid = GRID if snap else 1  # snap=False keeps exact audio timing (1 tick ~ 1 ms)

    def tick(self, t: float) -> int:
        g = self.grid
        if t < self.lead:
            return int(round(t / self.lead * TPQ / g)) * g
        return TPQ + int(round((t - self.lead) / self.spb * TPQ / g)) * g


class BeatMap:
    """Variable tempo map that follows tracked beats, so charts line up beat by beat.

    Tempo is piecewise constant; each segment is the longest run of beats one tempo fits
    within `tol` seconds. A lead-in segment (before the first beat) lets bars start on a
    tracked downbeat.
    """

    def __init__(self, beats, downbeats=None, snap=True, tol=0.015, smooth=0):
        b = np.asarray(beats, float)
        if smooth:
            b = smooth_beats(b, smooth)
        # a first beat within half a beat of the start would make the lead-in (time 0 to that
        # beat) absurdly fast (1000+ BPM): let the lead-in absorb it instead
        if len(b) > 9:
            period = float(np.median(np.diff(b[:9])))
            while len(b) > 9 and b[0] < 0.5 * period:
                b = b[1:]
        self.snap = snap
        self.bar_grid = None  # per-bar grid step in ticks, set by fit_grid
        n = len(b)
        # lead-in: s beats between time 0 and the first tracked beat, so a downbeat lands on a bar line
        s = 1
        if downbeats is not None and len(downbeats):
            d = int(np.argmin(abs(b - downbeats[0])))
            s = (-d) % 4 or 4
        if not 0.2 <= b[0] / s <= 1.5:
            s = 1
        self.lead_beats, self.lead_len = s, max(b[0], 0.05)
        segs, i = [], 0
        while i < n - 1:
            j = i + 1
            while j + 1 < n:
                P = (b[j + 1] - b[i]) / (j + 1 - i)
                if np.max(np.abs(b[i:j + 2] - (b[i] + np.arange(j + 2 - i) * P))) > tol:
                    break
                j += 1
            segs.append((i, float(b[i]), float((b[j] - b[i]) / (j - i))))
            i = j
        if not segs:
            segs = [(0, float(b[0]), 0.5)]
        self.segs = segs
        self.seg_times = np.array([x[1] for x in segs])
        self.n_beats = n

    def raw_tick(self, t: float) -> float:
        s = self.lead_beats
        if t < self.segs[0][1]:
            return t / self.lead_len * s * TPQ
        k = max(0, int(np.searchsorted(self.seg_times, t, side="right")) - 1)
        i, t0, P = self.segs[k]
        return (i + s) * TPQ + (t - t0) / P * TPQ

    def fit_grid(self, times, straight_bias=None, undecided=None):
        """Choose each bar's grid from the notes in it: straight 16ths or 8th-note triplets
        (a shuffle), or the 32nd / 16th-triplet grid for fast runs that would otherwise land on
        top of each other. Bars with too few notes to tell follow the song's usual feel.
        After this, tick() always snaps to the bar's grid, as real charts do."""
        straight_bias = STRAIGHT_BIAS if straight_bias is None else straight_bias
        undecided = UNDECIDED if undecided is None else undecided
        raw = np.array(sorted(self.raw_tick(t) for t in times))
        if not len(raw):
            self.bar_grid = None
            return
        BAR = 4 * TPQ
        bars = (raw // BAR).astype(int)
        # extra cost of a grid: a triplet bar needs a little more evidence than a straight one
        # (straight_bias per bar), and the fine grids, only tried for runs the coarse ones would
        # collapse, cost 0.2 per note
        cost = {GRID: 0.0, TPQ // 3: straight_bias}
        finer = {GRID: (GRID // 2, 0.2), TPQ // 3: (TPQ // 6, 0.2)}
        choice, strength = {}, {}

        def score(r, g, per_bar, per_note=0.0):
            snapped = np.round(r / g) * g
            collide = len(r) - len(np.unique(snapped))
            # distance moved in 16th-note steps, plus one per note lost to a collision
            return np.abs(snapped - r).sum() / GRID + per_bar + per_note * len(r) + collide, collide

        for k in np.unique(bars):
            r = raw[bars == k]
            scores = {}
            for g, c in cost.items():
                scores[g], collide = score(r, g, c)
                if collide:  # a fast run: also try the finer grid of the same feel
                    fg, fc = finer[g]
                    scores[fg] = score(r, fg, c, fc)[0]
            best = min(scores, key=scores.get)
            choice[int(k)] = best
            vals = sorted(scores.values())
            strength[int(k)] = (vals[1] - vals[0]) if len(vals) > 1 else 0.0
            if len(r) < 3:
                strength[int(k)] = 0.0
        straight = sum(1 for g in choice.values() if g in (GRID, GRID // 2))
        usual = GRID if straight >= len(choice) / 2 else TPQ // 3
        for k in choice:  # undecided bars take the song's usual feel
            if strength[k] < undecided and choice[k] in (GRID, TPQ // 3):
                choice[k] = usual
        self.bar_grid, self.usual_grid = choice, usual

    def tick(self, t: float) -> int:
        raw = self.raw_tick(t)
        if not self.snap:
            return int(round(raw))
        if self.bar_grid is not None:
            g = self.bar_grid.get(int(raw // (4 * TPQ)), self.usual_grid)
            return int(round(raw / g)) * g
        # snap to the nearest 16th or 12th-of-a-beat grid point if it is acoustically close
        k = max(0, int(np.searchsorted(self.seg_times, t, side="right")) - 1)
        spb = self.segs[k][2] if t >= self.segs[0][1] else self.lead_len / self.lead_beats
        best = None
        for g in (GRID, TPQ // 3):
            cand = int(round(raw / g)) * g
            err = abs(cand - raw) / TPQ * spb
            if best is None or err < best[0]:
                best = (err, cand)
        return best[1] if best[0] <= 0.025 else int(round(raw))

    def time(self, tick: float) -> float:
        """Inverse of raw_tick: seconds at a tick (for checking snapped notes)."""
        s = self.lead_beats
        if tick < s * TPQ:
            return tick / (s * TPQ) * self.lead_len
        k = max(0, int(np.searchsorted([(i + s) * TPQ for i, _, _ in self.segs], tick, side="right")) - 1)
        i, t0, P = self.segs[k]
        return t0 + (tick - (i + s) * TPQ) / TPQ * P

    def tempo_events(self):
        """[(tick, microseconds per quarter)]"""
        ev = [(0, int(round(self.lead_len / self.lead_beats * 1e6)))]
        for i, _, P in self.segs:
            ev.append(((i + self.lead_beats) * TPQ, int(round(P * 1e6))))
        return ev

    @property
    def bpm(self) -> float:
        Ps = np.array([P for _, _, P in self.segs])
        return round(float(60.0 / np.median(Ps)), 1)


def smooth_beats(b: np.ndarray, half: int = 4) -> np.ndarray:
    """Even out beat-tracker jitter: each beat moves onto a straight line fitted through its
    neighbours (half beats either side), so the tempo follows real drift but not the wobble."""
    out = b.copy()
    n = len(b)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        idx = np.arange(lo, hi)
        if len(idx) >= 3:
            out[i] = np.polyval(np.polyfit(idx, b[lo:hi], 1), i)
    return np.maximum.accumulate(out)


def grid_fit(times, bpm: float, offset: float, div: int = 4) -> float:
    """Mean distance of times from the nearest 1/div-beat gridline, as a fraction of the
    grid step: 0 = perfectly on grid, 0.25 = what random timing gives."""
    if len(times) == 0:
        return 0.25
    step = 60.0 / bpm / div
    r = ((np.asarray(times) - offset) / step) % 1.0
    return float(np.minimum(r, 1 - r).mean())


HOPO_TICKS = TPQ // 3 + 1  # Clone Hero's automatic hammer-on distance for .mid charts
MAX_LANES = {"expert": 3, "hard": 2, "medium": 2, "easy": 1}
MIN_SUSTAIN_TICKS = TPQ // 2  # an 8th; anything shorter is written as a plain note
SUSTAIN_GAP = GRID // 2  # a sustain stops this far before the next note


def _thin(notes, min_gap):
    """notes: [(tick, ...)] sorted by tick."""
    out, last = [], -10 ** 9
    for n in notes:
        if n[0] - last >= min_gap:
            out.append(n)
            last = n[0]
    return out


def _spb(tm):
    """Seconds per beat at a tick, for the tempo map in use."""
    if hasattr(tm, "time"):
        return lambda t: tm.time(t + TPQ) - tm.time(t)
    return lambda t: tm.spb


def guitar_notes(timed, tm: TempoMap, difficulty: str, inst: str = "guitar", cap: int | None = None):
    """timed: [(seconds, lane or lanes[, sustain_s, hammer_on])] -> [(tick, lane, length, force)]
    reduced for the difficulty. force is "hopo", "strum" or None (let the game decide).
    Which notes Hard/Medium/Easy keep comes from the learned reduction (chc.reduce) when its
    model is available, else from GUITAR_RULES spacing."""
    gap, nlanes = GUITAR_RULES[difficulty]
    by_tick = {}
    for n in timed:
        t, lanes = n[0], n[1]
        lanes = (lanes,) if isinstance(lanes, int) else tuple(lanes)
        length = n[2] if len(n) > 2 else 0.0
        hopo = n[3] if len(n) > 3 else None
        tick = tm.tick(t)
        if tick in by_tick:
            continue  # two notes snapped onto one spot: keep the first, don't invent a chord
        by_tick[tick] = [set(lanes), tm.tick(t + length) - tick if length > 0 else 0, hopo]
    ticks = sorted(by_tick)
    learned = None
    if difficulty != "expert":
        from . import reduce
        learned = reduce.keep(inst, difficulty, [(t, tuple(sorted(by_tick[t][0]))) for t in ticks],
                              TPQ, _spb(tm), cap)
    notes = []
    for k, tick in enumerate(ticks):
        if learned is not None and not learned[k]:
            continue
        lanes, length, hopo = by_tick[tick]
        mapped = sorted({l * nlanes // 5 for l in lanes})[:MAX_LANES[difficulty]]
        notes.append((tick, tuple(mapped), length, hopo))
    if learned is None:
        notes = _thin(notes, gap)
    out, prev = [], None
    for k, (tick, lanes, length, hopo) in enumerate(notes):
        nxt = notes[k + 1][0] if k + 1 < len(notes) else None
        if nxt is not None:
            length = min(length, nxt - tick - SUSTAIN_GAP)
        length = length if length >= MIN_SUSTAIN_TICKS else 1
        # hammer-ons only matter on expert/hard; the easier parts are spaced out and strummed
        force = None
        if hopo is not None and difficulty in ("expert", "hard") and prev is not None:
            natural = tick - prev[0] <= HOPO_TICKS and len(lanes) == 1 and lanes != prev[1]
            if hopo and not natural and lanes != prev[1]:
                force = "hopo"
            elif natural and not hopo:
                force = "strum"
        out += [(tick, lane, length, force) for lane in lanes]
        prev = (tick, lanes)
    return out


def drum_notes(timed, tm: TempoMap, difficulty: str, inst: str = "drums", cap: int | None = None):
    """timed: [(seconds, lane 0-4, is_tom)] -> [(tick, lane, is_tom)] reduced for the difficulty:
    the learned reduction (chc.reduce) picks the hits Hard/Medium/Easy keep when its model is
    available, else DRUM_RULES spacing per pad."""
    if difficulty != "expert":
        from . import reduce
        expert = drum_notes(timed, tm, "expert")
        learned = reduce.keep("drums", difficulty, expert, TPQ, _spb(tm), cap)
        if learned is not None:
            return [h for h, k in zip(expert, learned) if k]
    rules, last, out, seen, hands = DRUM_RULES[difficulty], {}, [], set(), {}
    for d in sorted(timed):
        t, lane, tom = d[:3]
        gap = rules[lane]
        tick = tm.tick(t)
        if gap is None or (tick, lane) in seen:
            continue
        if tick - last.get(lane, -10 ** 9) < gap:
            continue
        if lane and hands.get(tick, 0) >= 2:
            continue  # snapping merged two moments: still only two hands
        if lane:
            hands[tick] = hands.get(tick, 0) + 1
        seen.add((tick, lane))
        last[lane] = tick
        out.append((tick, lane, bool(tom)))
    return out


def _track(name, events):
    tr = mido.MidiTrack([mido.MetaMessage("track_name", name=name, time=0)])
    last = 0
    order = {"note_off": 0, "note_on": 1}
    for tick, typ, note in sorted(events, key=lambda e: (e[0], order[e[1]])):
        tr.append(mido.Message(typ, note=note, velocity=100 if typ == "note_on" else 0,
                               time=tick - last))
        last = tick
    tr.append(mido.MetaMessage("end_of_track", time=0))
    return tr


def _events(notes_by_diff, drums=False):
    """Guitar/bass notes are (tick, lane, length, force); drum notes are (tick, lane, is_tom)."""
    ev, marks = [], set()
    for diff, notes in notes_by_diff.items():
        for n in notes:
            tick, lane = n[0], n[1]
            note = DIFF_BASE[diff] + lane
            length = 1 if drums else n[2]
            ev += [(tick, "note_on", note), (tick + length, "note_off", note)]
            if drums and n[2] and lane in TOM_MARK:
                marks.add((tick, TOM_MARK[lane]))
            elif not drums and n[3]:
                marks.add((tick, DIFF_BASE[diff] + (5 if n[3] == "hopo" else 6)))
    for tick, mark in sorted(marks):
        ev += [(tick, "note_on", mark), (tick + 1, "note_off", mark)]
    return ev


def _fine_tick(tm, t: float) -> int:
    """A time's tick off the grid: singing doesn't sit on 16ths the way guitars do."""
    if hasattr(tm, "raw_tick"):
        return int(round(tm.raw_tick(t)))
    if t < tm.lead:
        return int(round(t / tm.lead * TPQ))
    return TPQ + int(round((t - tm.lead) / tm.spb * TPQ))


def _vocal_track(notes, tm) -> mido.MidiTrack:
    """PART VOCALS from chc.vocals notes: pitched notes, a lyric on each, and phrase markers."""
    ticks = [(_fine_tick(tm, n.start), _fine_tick(tm, n.end)) for n in notes]
    events, phrase_end, last_b = [], None, 0
    for i, (n, (a, b)) in enumerate(zip(notes, ticks)):
        a = max(a, last_b)  # two notes can round onto the same tick; sung notes never overlap
        if i + 1 < len(notes):
            next_a = ticks[i + 1][0]
            b = next_a if notes[i + 1].lyric == "+" else min(b, next_a)
        b = last_b = max(b, a + 1)
        pitch = n.pitch
        while pitch < 36:
            pitch += 12
        while pitch > 84:
            pitch -= 12
        if n.phrase:
            if phrase_end is not None:
                events.append((phrase_end, 0, mido.Message("note_off", note=105, velocity=0)))
            events.append((a, 1, mido.Message("note_on", note=105, velocity=100)))
        phrase_end = b
        events += [(a, 2, mido.MetaMessage("lyrics", text=n.lyric)),
                   (a, 3, mido.Message("note_on", note=pitch, velocity=100)),
                   (b, 0, mido.Message("note_off", note=pitch, velocity=0))]
    events.append((phrase_end, 0, mido.Message("note_off", note=105, velocity=0)))
    tr = mido.MidiTrack([mido.MetaMessage("track_name", name="PART VOCALS", time=0)])
    last = 0
    for tick, _, msg in sorted(events, key=lambda e: e[:2]):
        tr.append(msg.copy(time=tick - last))
        last = tick
    tr.append(mido.MetaMessage("end_of_track", time=0))
    return tr


def by_difficulty(timed, tm, fn, inst) -> dict:
    """Each level no busier than the one above it (expert, hard, medium, easy in order)."""
    by_diff, cap = {}, None
    for d in DIFF_BASE:
        by_diff[d] = fn(timed, tm, d, inst, cap)
        cap = len(by_diff[d]) if fn is drum_notes else len({n[0] for n in by_diff[d]})
    return by_diff


def write_mid(path: Path, bpm: float, offset: float, guitar=None, bass=None, drums=None,
              snap: bool = True, beatmap: "BeatMap | None" = None, grid: bool = True, vocals=None):
    """Each part is a timed note list or None. Returns {part: expert note count}.

    grid (with a beatmap): snap every note to its bar's straight or triplet grid
    (BeatMap.fit_grid); otherwise only notes already close to a grid line are snapped."""
    tm = beatmap if beatmap is not None else TempoMap(bpm, offset, snap)
    mid = mido.MidiFile(type=1, ticks_per_beat=TPQ)
    if beatmap is not None:
        tempo = mido.MidiTrack([mido.MetaMessage("track_name", name="", time=0),
                                mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0)])
        last = 0
        for tick, us in beatmap.tempo_events():
            tempo.append(mido.MetaMessage("set_tempo", tempo=us, time=tick - last))
            last = tick
        tempo.append(mido.MetaMessage("end_of_track", time=0))
    else:
        tempo = mido.MidiTrack([
            mido.MetaMessage("track_name", name="", time=0),
            mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
            mido.MetaMessage("set_tempo", tempo=int(round(tm.lead * 1e6)), time=0),
            mido.MetaMessage("set_tempo", tempo=int(round(tm.spb * 1e6)), time=TPQ),
            mido.MetaMessage("end_of_track", time=0)])
    mid.tracks += [tempo, mido.MidiTrack([mido.MetaMessage("track_name", name="EVENTS", time=0),
                                          mido.MetaMessage("end_of_track", time=0)])]
    counts = {}
    for name, timed, fn in (("PART GUITAR", guitar, guitar_notes), ("PART BASS", bass, guitar_notes),
                            ("PART DRUMS", drums, drum_notes)):
        inst = name.split()[1].lower()
        if not timed:
            continue
        if beatmap is not None and snap:
            if grid:
                beatmap.fit_grid([n[0] for n in timed])
            else:
                beatmap.bar_grid = None
        by_diff = by_difficulty(timed, tm, fn, inst)
        mid.tracks.append(_track(name, _events(by_diff, drums=fn is drum_notes)))
        counts[name] = len({n[0] for n in by_diff["expert"]})  # notes and chords, not lanes
    if vocals:
        mid.tracks.append(_vocal_track(vocals, tm))
        counts["PART VOCALS"] = len(vocals)
    mid.save(path)
    return counts


def add_intro(path: Path, lead: float = LEAD_IN, dry: bool = False) -> float:
    """Add silent bars to the start of a notes.mid whose first note comes less than `lead` seconds
    in, so players see the highway before they have to play. Returns the seconds added: the
    audio must be delayed by exactly that much (0 if the chart already has room)."""
    mid = mido.MidiFile(str(path), clip=True)
    first = None
    for tr in mid.tracks:
        if tr.name.startswith("PART"):
            tick = 0
            for m in tr:
                tick += m.time
                if m.type == "note_on" and m.velocity:
                    first = tick if first is None else min(first, tick)
                    break
    if first is None:
        return 0.0
    tempo_at, tick, now, tempo = 500000, 0, 0.0, 500000
    for m in mid.tracks[0]:
        if m.time and tick + m.time > first:
            break
        now += m.time / mid.ticks_per_beat * tempo / 1e6
        tick += m.time
        if m.type == "set_tempo":
            tempo = tempo_at = m.tempo
    start = now + (first - tick) / mid.ticks_per_beat * tempo / 1e6
    if start >= lead:
        return 0.0
    us = mido.bpm2tempo(float(np.clip(mido.tempo2bpm(tempo_at), *INTRO_BPM)))
    bar = 4 * us / 1e6
    bars = int(np.ceil((lead - start) / bar))
    if dry:
        return bars * bar
    shift = bars * 4 * mid.ticks_per_beat
    for i, tr in enumerate(mid.tracks):
        k = 0
        while k < len(tr) - 1 and not tr[k].time and tr[k].type in ("track_name", "time_signature"):
            k += 1
        tr[k].time += shift
        if i == 0:
            tr.insert(k, mido.MetaMessage("set_tempo", tempo=us, time=0))
    mid.save(path)
    return bars * bar


def write_ini(path: Path, title: str, artist: str, charter: str, length_ms: int,
              parts: dict, preview_ms: int = 0, album: str = "", genre: str = "", year: str = "",
              tiers: dict | None = None, models: str = ""):
    """tiers: {"guitar": 0-6, ...} difficulty shown in the song list (chc.rating); 2 if unknown.
    models: which model files charted it ("guitar:1a2b3c4d ..."; chc.ml.model_tag)."""
    tiers = tiers or {}

    def diff(inst):
        return tiers.get(inst, 2) if f"PART {inst.upper()}" in parts else -1
    lines = ["[song]", f"name = {title}", f"artist = {artist}", f"album = {album}",
             f"genre = {genre}", f"year = {year}",
             f"charter = {charter}", f"diff_guitar = {diff('guitar')}",
             f"diff_bass = {diff('bass')}",
             f"diff_drums = {diff('drums')}",
             f"diff_vocals = {diff('vocals')}",
             "pro_drums = True", f"song_length = {length_ms}", "delay = 0",
             f"preview_start_time = {preview_ms}",
             "chc_generated = 1"]  # marks songs this app made (chc.library.is_ours)
    if models:
        lines.append(f"chc_models = {models}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
