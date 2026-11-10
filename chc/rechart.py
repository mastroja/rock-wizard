"""Rechart a song part by part: the person chooses, per instrument and difficulty, what stays exactly
as it is, what the models redo and what gets made from its Expert part. Everything is built on the
song's own tempo map. Only runs on a song when asked to."""
import re
import shutil
import tempfile
import threading
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import mido
import numpy as np

from . import audio, chart, feedback, library, ml, phrases, pipeline, rating, structure, tidy, vocals

STEPS = ["Reading your chart", "Splitting it into instruments", "Writing the parts", "Saving the song"]
TPQ = chart.TPQ
LEVELS = ("Expert", "Hard", "Medium", "Easy")
DIFFS = tuple(chart.DIFF_BASE)  # expert, hard, medium, easy
INSTS = ("guitar", "bass", "drums")
PARTS = {"Single": "guitar", "DoubleBass": "bass", "Drums": "drums"}
TRACK = {"guitar": "PART GUITAR", "bass": "PART BASS", "drums": "PART DRUMS", "vocals": "PART VOCALS"}
MARKER = "chc_filled"  # lists what was generated; such songs are never used as training data
SONG_AUDIO = ("song.ogg", "song.opus", "song.mp3", "song.wav")
AUDIO_TYPES = (".ogg", ".opus", ".mp3", ".wav")
SPLIT_AUDIO = ("guitar", "rhythm", "bass", "drums", "vocals", "keys")
TAP, STAR, SOLO, DOUBLE_KICK = 104, 116, 103, 95
TOMS = set(chart.TOM_MARK.values())
OPEN = 7
CHART_HOPO = 65 / 192  # beats: .chart's automatic hammer-on distance
LINE = re.compile(r"^\s*(\d+)\s*=\s*([A-Z]+)\s+(.*?)\s*$")
MAX_SYLLABLE = 2.5  # seconds a sung syllable can last when nothing else ends it


class Unsupported(ValueError):
    pass


@dataclass
class Parsed:
    res: int = 192
    offset: float = 0.0
    tempos: list = field(default_factory=list)  # (chart tick, microseconds per beat)
    sigs: list = field(default_factory=list)    # (chart tick, numerator, denominator)
    events: list = field(default_factory=list)  # (chart tick, text)
    tracks: dict = field(default_factory=dict)  # (instrument, level) -> [(chart tick, kind, value, length)]
    words: list = field(default_factory=list)   # (chart tick, syllable)
    phrases: list = field(default_factory=list)  # (chart tick, "phrase_start" or "phrase_end")


@dataclass
class Source:
    """A song's chart in MIDI terms, whichever file it came from (ticks at TPQ)."""
    tempos: list
    base: list                 # tracks that stay as they are: tempo, events, other instruments
    parts: dict                # instrument -> track holding all its levels
    words: list = field(default_factory=list)    # (tick, syllable) sung words with no notes yet
    phrases: list = field(default_factory=list)  # (start tick, end tick)
    delay: float = 0.0
    chart: Path | None = None  # the notes.chart it was read from
    pro: bool = False          # drums with cymbals marked


def read_chart(path: Path) -> Parsed:
    """A Moonscraper .chart. Anything it can't carry over raises Unsupported, so nothing is lost."""
    p, section = Parsed(), None
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        ln = raw.strip()
        if not ln or ln in "{}":
            continue
        head = re.fullmatch(r"\[(.+)\]", ln)
        if head:
            section = head.group(1)
            level = next((v for v in LEVELS if section.startswith(v)), None)
            if section not in ("Song", "SyncTrack", "Events") and not (level and section[len(level):] in PARTS):
                raise Unsupported(f"this chart has a {section} part, which can't be carried over yet")
            continue
        if section == "Song":
            m = re.match(r"(\w+)\s*=\s*\"?([^\"]*)\"?", ln)
            if m and m.group(1) == "Resolution":
                p.res = int(m.group(2))
            elif m and m.group(1) == "Offset":
                p.offset = float(m.group(2))
            continue
        m = LINE.match(ln)
        if not m:
            continue
        tick, kind, rest = int(m.group(1)), m.group(2), m.group(3)
        if section == "SyncTrack":
            a = rest.split()
            if kind == "B":
                p.tempos.append((tick, int(round(60e9 / int(a[0])))))
            elif kind == "TS":
                p.sigs.append((tick, int(a[0]), 2 ** int(a[1]) if len(a) > 1 else 4))
        elif section == "Events":
            text = rest.strip('"')
            if text.startswith("lyric "):
                p.words.append((tick, text[6:]))
            elif text in ("phrase_start", "phrase_end"):
                p.phrases.append((tick, text))
            else:
                p.events.append((tick, text))
        else:
            level = next(v for v in LEVELS if section.startswith(v))
            key = (PARTS[section[len(level):]], level)
            a = rest.split()
            if kind == "N":
                p.tracks.setdefault(key, []).append((tick, "N", int(a[0]), int(a[1])))
            elif kind == "S" and a[0] == "2":
                p.tracks.setdefault(key, []).append((tick, "S", 2, int(a[1])))
            elif kind == "E" and rest.strip('"') in ("solo", "soloend"):
                p.tracks.setdefault(key, []).append((tick, "E", rest.strip('"'), 0))
            else:
                raise Unsupported(f"this chart uses a {kind} {rest} marker, which can't be carried over yet")
    if not p.tempos:
        raise Unsupported("this chart has no tempo map")
    return p


class ChartTempo(chart.BeatMap):
    """The song's own tempo map in the shape the note writers expect, with no beat tracking."""

    def __init__(self, tempos, delay: float = 0.0):
        self.snap, self.bar_grid, self.lead_beats, self.lead_len = True, None, 0, 1.0
        segs, t = [], delay
        for i, (tick, us) in enumerate(tempos):
            if i:
                t += (tick - tempos[i - 1][0]) / TPQ * tempos[i - 1][1] / 1e6
            segs.append((tick / TPQ, t, us / 1e6))
        self.segs = segs
        self.seg_times = [s[1] for s in segs]


def _absorb_offset(tempos, first: int, offset: float):
    """.chart Offset means audio time = chart time + Offset. A .mid has no such thing, so the empty
    bars before the first note change length instead: every note keeps its tick and its audio time."""
    if not offset:
        return tempos
    at = first - first % (4 * TPQ) or first - first % TPQ
    tm = ChartTempo(tempos)
    length = tm.time(at) + offset
    if not at or length < 0.05:
        raise Unsupported("the chart's audio offset is too large for its first notes")
    now = next(us for tick, us in reversed(tempos) if tick <= at)
    return [(0, int(round(length / (at / TPQ) * 1e6))), (at, now)] + [t for t in tempos if t[0] > at]


def _scale(res: int):
    return lambda tick: int(round(tick * TPQ / res))


def _phrase_events(items, t) -> list:
    """Star power and solo sections of a level, as midi events."""
    ev, solo = [], None
    for tick, kind, value, length in items:
        if kind == "S":
            ev += [(t(tick), "note_on", STAR), (max(t(tick + length), t(tick) + 1), "note_off", STAR)]
        elif kind == "E" and value == "solo":
            solo = t(tick)
        elif kind == "E" and solo is not None:
            ev += [(solo, "note_on", SOLO), (max(solo + 1, t(tick)), "note_off", SOLO)]
            solo = None
    return ev


def _hand_part(items, level: str, res: int, phrases: bool):
    """A guitar or bass level as the charter wrote it -> (midi events, whether it has open notes).
    Hammer-ons are spelled out wherever .mid's automatic rule differs from .chart's."""
    t = _scale(res)
    base = chart.DIFF_BASE[level.lower()]
    at = defaultdict(lambda: {"lanes": {}, "force": False, "tap": False})
    for tick, kind, value, length in items:
        if kind == "N":
            if value in (5, 6):
                at[tick]["force" if value == 5 else "tap"] = True
            else:
                at[tick]["lanes"][value] = length
    ev = _phrase_events(items, t) if phrases else []
    prev, opens = None, False
    for tick in sorted(at):
        n = at[tick]
        if not n["lanes"]:
            continue  # a flag with no note under it
        lanes = tuple(sorted(n["lanes"]))
        fresh = prev is not None and len(lanes) == 1 and lanes != prev[1]
        natural = fresh and tick - prev[0] <= CHART_HOPO * res
        hopo = natural != n["force"]
        mid_natural = fresh and t(tick) - t(prev[0]) <= chart.HOPO_TICKS
        tk = t(tick)
        for lane, length in n["lanes"].items():
            note = base - 1 if lane == OPEN else base + lane
            opens |= lane == OPEN
            ev += [(tk, "note_on", note), (t(tick + length) if length else tk + 1, "note_off", note)]
        if n["tap"]:
            ev += [(tk, "note_on", TAP), (tk + 1, "note_off", TAP)]
        elif hopo != mid_natural:
            mark = base + (5 if hopo else 6)
            ev += [(tk, "note_on", mark), (tk + 1, "note_off", mark)]
        prev = (tick, lanes)
    return ev, opens


def _hand_drums(items, level: str, res: int, phrases: bool, pro: bool):
    """A drum level as the charter wrote it -> midi events. In .chart a cymbal is flagged; in .mid
    a tom is, so pads with no cymbal flag get a tom marker on pro charts."""
    t = _scale(res)
    base = chart.DIFF_BASE[level.lower()]
    hits, cymbals = defaultdict(set), defaultdict(set)
    for tick, kind, value, _ in items:
        if kind != "N":
            continue
        if value in (66, 67, 68):
            cymbals[tick].add(value - 64)
        elif value == 32 and level == "Expert":
            hits[tick].add("2x")
        elif 0 <= value <= 4:
            hits[tick].add(value)
        else:
            raise Unsupported(f"this chart's {level} drums use note {value}, which can't be carried over yet")
    ev = _phrase_events(items, t) if phrases else []
    for tick, lanes in hits.items():
        tk = t(tick)
        for lane in lanes:
            note = DOUBLE_KICK if lane == "2x" else base + lane
            ev += [(tk, "note_on", note), (tk + 1, "note_off", note)]
            if pro and lane in chart.TOM_MARK and lane not in cymbals[tick]:
                mark = chart.TOM_MARK[lane]
                ev += [(tk, "note_on", mark), (tk + 1, "note_off", mark)]
    return ev


def _from_chart(path: Path, delay: float) -> Source:
    p = read_chart(path)
    if p.offset and delay:
        raise Unsupported("this chart has both an Offset and a song.ini delay")
    t = _scale(p.res)
    ticks = [t(i[0]) for items in p.tracks.values() for i in items if i[1] == "N"]
    if not ticks:
        raise Unsupported("this chart has no notes")
    tempos = _absorb_offset(sorted((t(k), us) for k, us in p.tempos), min(ticks), p.offset)
    pro = any(i[1] == "N" and i[2] in (66, 67, 68) for (inst, _), items in p.tracks.items() if inst == "drums" for i in items)
    parts = {}
    for inst in INSTS:
        hand = {lv: items for (i, lv), items in p.tracks.items() if i == inst}
        if not hand:
            continue
        top = "Expert" if "Expert" in hand else next(iter(hand))  # star power and solos live on one level
        ev, opens = [], False
        for lv, items in hand.items():
            if inst == "drums":
                ev += _hand_drums(items, lv, p.res, lv == top, pro)
            else:
                e, more = _hand_part(items, lv, p.res, lv == top)
                ev += e
                opens |= more
        tr = chart._track(TRACK[inst], list(set(ev)))
        if opens:
            tr.insert(1, mido.MetaMessage("text", text="[ENHANCED_OPENS]", time=0))
        parts[inst] = tr
    starts, phrases = None, []
    for tick, what in sorted(p.phrases):
        if what == "phrase_start":
            starts = t(tick)
        elif starts is not None:
            phrases.append((starts, t(tick)))
            starts = None
    base = [_tempo_track(tempos, [(t(k), n, d) for k, n, d in p.sigs]), _events_track([(t(k), x) for k, x in p.events])]
    return Source(tempos, base, parts, sorted((t(k), w) for k, w in p.words), phrases, delay, path, pro)


def _abs(track):
    """(absolute tick, message) of a track."""
    tick = 0
    for m in track:
        tick += m.time
        yield tick, m


def _spans(track) -> list:
    """(start tick, note, end tick) of every note in a track."""
    on, out = {}, []
    for tick, m in _abs(track):
        if m.type == "note_on" and m.velocity:
            on.setdefault(m.note, tick)
        elif m.type in ("note_on", "note_off") and m.note in on:
            out.append((on.pop(m.note), m.note, tick))
    return sorted(out)


def _from_mid(path: Path, delay: float) -> Source:
    mid = mido.MidiFile(str(path), clip=True)
    if mid.ticks_per_beat != TPQ:
        raise Unsupported(f"this chart runs at {mid.ticks_per_beat} ticks a beat, which can't be carried over yet")
    tempos = [(tick, m.tempo) for tick, m in _abs(mid.tracks[0]) if m.type == "set_tempo"]
    if not tempos:
        raise Unsupported("this chart has no tempo map")
    if tempos[0][0]:
        tempos.insert(0, (0, 500000))
    names = {v: k for k, v in TRACK.items()}
    base, parts = [], {}
    for t in mid.tracks:
        inst = names.get(t.name.strip().upper())
        if inst:
            parts[inst] = t
        else:
            base.append(t)
    src = Source(tempos, base, parts, delay=delay)
    if "vocals" in parts and vocal_state(parts["vocals"]) == "words":
        src.words = sorted((tick, m.text) for tick, m in _abs(parts["vocals"])
                           if m.type == "lyrics" and not m.text.startswith("["))
        src.phrases = [(a, b) for a, n, b in _spans(parts["vocals"]) if n == vocals.PHRASE_MARK]
    return src


def load(folder: Path) -> Source:
    c, m = folder / "notes.chart", folder / "notes.mid"
    if c.exists() and m.exists():
        raise Unsupported("this song has both a notes.chart and a notes.mid")
    delay = float(tidy.read_ini(folder).get("delay", "0") or 0) / 1000
    return _from_chart(c, delay) if c.exists() else _from_mid(m, delay)


def _has_opens(track) -> bool:
    return any(m.is_meta and m.type == "text" and "ENHANCED_OPENS" in m.text for m in track)


def _level_of(note: int, opens: bool, drums: bool) -> str | None:
    """Which difficulty a note in an instrument track belongs to, if it belongs to just one."""
    for lv, base in chart.DIFF_BASE.items():
        if (base - 1 if opens or (drums and lv == "expert") else base) <= note <= base + 6:
            return lv
    return None


def _levels_kept(track, levels, drums: bool) -> set:
    """The notes of these levels in a track, for checking nothing changed."""
    opens = _has_opens(track)
    return {s for s in _spans(track) if _level_of(s[1], opens, drums) in levels}


def _has_level(track, level: str, drums: bool) -> bool:
    base, opens = chart.DIFF_BASE[level], _has_opens(track)
    lo = base - 1 if opens else base
    return any(lo <= n <= base + 4 for _, n, _ in _spans(track))


def vocal_state(track) -> str | None:
    """'notes' for a sung part with pitches, 'words' for lyrics alone, None for neither."""
    if any(vocals.LOW <= n <= vocals.HIGH for _, n, _ in _spans(track)):
        return "notes"
    return "words" if any(m.type == "lyrics" and not m.text.startswith("[") for m in track) else None


def _hand_fretted(track, tm) -> list:
    """Expert guitar or bass as timed notes, the shape the reductions work from."""
    base, opens = chart.DIFF_BASE["expert"], _has_opens(track)
    at, flags = defaultdict(dict), []
    for a, n, b in _spans(track):
        if base <= n <= base + 4:
            at[a][n - base] = b - a
        elif n == base - 1 and opens:
            at[a][OPEN] = b - a
        elif n in (base + 5, base + 6, TAP):
            flags.append((a, b, n))
    ticks = sorted(at)
    forced = {}
    for a, b, n in flags:  # a marker can span several notes
        for k in ticks[bisect_left(ticks, a):bisect_left(ticks, max(b, a + 1))]:
            forced[k] = n
    out, prev = [], None
    for k in ticks:
        lanes = tuple(sorted(at[k]))
        fresh = prev is not None and len(lanes) == 1 and lanes != prev[1]
        hopo = fresh and k - prev[0] <= chart.HOPO_TICKS
        if k in forced:
            hopo = forced[k] != base + 6
        length = max(at[k].values())
        out.append((tm.time(k), tuple(0 if v == OPEN else v for v in lanes),
                    tm.time(k + length) - tm.time(k) if length > 1 else 0.0, hopo))
        prev = (k, lanes)
    return out


def _hand_hits(track, tm) -> list:
    """Expert drums as timed hits."""
    base, spans = chart.DIFF_BASE["expert"], _spans(track)
    toms = {m: [(a, max(b, a + 1)) for a, n, b in spans if n == m] for m in TOMS}
    out = []
    for a, n, _ in spans:
        lane = n - base
        if 0 <= lane <= 4:
            mark = chart.TOM_MARK.get(lane)
            out.append((tm.time(a), lane, bool(mark and any(x <= a < y for x, y in toms[mark]))))
    return out


def _sing_words(words, phrases, midi: np.ndarray, tm) -> list:
    """The person's own syllables where they placed them; only the melody comes from the vocal stem."""
    usual = float(np.nanmedian(midi)) if np.isfinite(midi).any() else 60.0
    times = [tm.time(k) for k, _ in words]
    ends = sorted(tm.time(b) for _, b in phrases)
    starts = sorted(a for a, _ in phrases)
    notes, last = [], int(round(usual))
    for i, (tick, text) in enumerate(words):
        a = times[i]
        stop = min(times[i + 1] if i + 1 < len(words) else a + MAX_SYLLABLE, a + MAX_SYLLABLE)
        at = bisect_left(ends, a + 1e-6)
        if at < len(ends):
            stop = min(stop, ends[at])
        stop = max(stop, a + vocals.MIN_NOTE)
        fa, fb = int(a / vocals.HOP), int(stop / vocals.HOP)
        here = midi[fa:fb]
        sung = np.flatnonzero(np.isfinite(here))
        if len(sung):
            last = int(round(float(np.nanmedian(here))))
            end = max(a + (int(sung[-1]) + 1) * vocals.HOP, a + vocals.MIN_NOTE)
        else:  # nothing clear under this syllable: take the pitch just around it
            around = midi[max(0, fa - 30):fb + 30]
            if np.isfinite(around).any():
                last = int(round(float(np.nanmedian(around))))
            end = min(stop, a + 0.25)
        new_phrase = i == 0 or any(words[i - 1][0] < s <= tick for s in starts)
        notes.append(vocals.Note(a, min(end, stop), last, text, new_phrase))
    if not starts:
        vocals._mark_phrases(notes, [False] * len(notes))
    return notes


def _audio_source(folder: Path) -> str | None:
    """'stems' for this app's four stems, 'mix' for one whole-song file, None if neither fits:
    a song.ogg next to separate guitar or drum tracks is only the backing."""
    names = [p.stem.lower() for p in folder.iterdir() if p.suffix.lower() in AUDIO_TYPES]
    if all((folder / f).exists() for f in library.OUR_STEMS) and "song" not in names:
        return "stems"
    if "song" in names and not any(n.startswith(SPLIT_AUDIO) for n in names):
        return "mix"
    return None


def _provenance(folder: Path, ini: dict):
    made = set(ini.get(MARKER, "").split())
    every_part_ai = library.is_ours(folder) and not ini.get(MARKER)

    def by(inst: str, level: str | None = None) -> str:
        return "ai" if every_part_ai or inst in made or f"{inst}:{level}" in made else "you"
    return by


def options(folder: Path) -> dict | None:
    """What the Rechart panel can offer for a song, or None when the song isn't one to change here:
    only charts made by hand in .chart form or marked as the owner's, or songs this app made or
    has rechart before."""
    ini = tidy.read_ini(folder)
    is_chart = (folder / "notes.chart").exists()
    if not is_chart and not (ini.get(MARKER) or library.is_ours(folder) or library.is_mine(folder)):
        return None
    if not is_chart and not (folder / "notes.mid").exists():
        return None
    try:
        src = load(folder)
    except (ValueError, OSError, EOFError) as e:
        return {"blocked": str(e)}
    can_ai = _audio_source(folder) is not None and all(ml.available(i) for i in INSTS)
    by = _provenance(folder, ini)
    parts = {}
    for inst in INSTS:
        tr, drums = src.parts.get(inst), inst == "drums"
        row = {}
        for lv in DIFFS:
            have = tr is not None and _has_level(tr, lv, drums)
            if lv == "expert":
                choices = ["keep"] * have + ["ai"] * can_ai + ["empty"]
            else:
                choices = ["keep"] * have + ["make", "empty"]
            row[lv] = {"have": have, "by": by(inst, lv) if have else None, "choices": choices, "pick": choices[0]}
        if row["expert"]["pick"] == "empty":
            for lv in DIFFS[1:]:
                if not row[lv]["have"]:
                    row[lv]["pick"] = "empty"
        parts[inst] = row
    tr = src.parts.get("vocals")
    state = "words" if src.words else (vocal_state(tr) if tr is not None else None)
    if state == "notes":
        choices = ["keep"] + ["ai"] * can_ai + ["none"]
    elif state == "words" and can_ai:
        choices = ["words", "ai", "none"]
    elif can_ai:
        choices = ["ai", "none"]
    else:
        choices = ["none"]
    sung = {"have": state, "by": by("vocals") if state else None, "choices": choices, "pick": choices[0]}
    styles = ["normal", "cleaner"] + ["busier"] * ml.available(ml.BUSIER)
    return {"parts": parts, "vocals": sung, "ai": can_ai, "levels": list(DIFFS), "styles": styles,
            "sections": _sections(src)}


def _sections(src: Source) -> list:
    """The song's named sections as [{name, from, to}] in ticks, for redoing just one."""
    marks = []
    for tr in src.base:
        if tr.name.strip().upper() != "EVENTS":
            continue
        for tick, m in _abs(tr):
            hit = re.fullmatch(r"\[(?:section |prc_)(.+)\]", m.text.strip()) if m.type == "text" else None
            if hit:
                marks.append((tick, hit.group(1).replace("_", " ")))
    marks.sort()
    ends = [a for a, _ in marks[1:]] + [None]
    return [{"name": name, "from": a, "to": b} for (a, name), b in zip(marks, ends)]


def _cut_expert(track, a: int, b, drums: bool):
    """A copy of a track without the Expert notes that start inside [a, b); b None means to the end."""
    opens, gone, msgs = _has_opens(track), set(), []
    on = {}
    for tick, m in _abs(track):
        if m.type in ("track_name", "end_of_track"):
            continue
        if m.type in ("note_on", "note_off") and _level_of(m.note, opens, drums) == "expert":
            if m.type == "note_on" and m.velocity:
                on[m.note] = tick >= a and (b is None or tick < b)
                if on[m.note]:
                    continue
            elif on.pop(m.note, False):
                continue
        msgs.append((tick, m))
    tr, last = mido.MidiTrack([mido.MetaMessage("track_name", name=track.name, time=0)]), 0
    for tick, m in msgs:
        tr.append(m.copy(time=tick - last))
        last = tick
    tr.append(mido.MetaMessage("end_of_track", time=0))
    return tr


def check_plan(plan: dict, opts: dict) -> dict:
    """The person's choices, validated against what this song can do. Raises ValueError."""
    out = {}
    for inst in INSTS:
        row, pick = opts["parts"][inst], plan.get(inst) or {}
        got = {lv: pick.get(lv, row[lv]["pick"]) for lv in DIFFS}
        for lv, v in got.items():
            if v not in row[lv]["choices"]:
                raise ValueError(f"{inst} {lv}: {v} isn't possible for this song")
        if inst == "drums" and got["expert"] != "keep" and any(got[lv] == "keep" for lv in DIFFS[1:]):
            raise ValueError("Keeping lower drum levels needs their Expert kept too: cymbals and toms are shared")
        if got["expert"] == "empty":
            got |= {lv: "empty" for lv in DIFFS[1:] if got[lv] == "make"}
        out[inst] = got
    v = plan.get("vocals", opts["vocals"]["pick"])
    if v not in opts["vocals"]["choices"]:
        raise ValueError(f"vocals: {v} isn't possible for this song")
    out["vocals"] = v
    style = plan.get("guitar_style", "normal")
    if style not in opts["styles"]:
        raise ValueError(f"{style} isn't a guitar style")
    out["guitar_style"] = style
    # redo one section: Expert parts set to AI change only inside it, and their lower levels follow
    section = plan.get("section")
    if section is not None:
        hit = next((s for s in opts["sections"] if s["from"] == section), None)
        if hit is None:
            raise ValueError("That section isn't in this song")
        if opts["vocals"]["have"] == "words":
            raise ValueError("Give this song's lyrics a melody first, then redo a section")
        out["vocals"] = "keep" if opts["vocals"]["have"] == "notes" else "none"  # vocals stay out of it
        redo = [i for i in INSTS if out[i]["expert"] == "ai"]
        if not redo or any(not opts["parts"][i]["expert"]["have"] for i in redo):
            raise ValueError("Pick an existing Expert part to redo in that section")
        for i in redo:
            out[i] |= {lv: "make" for lv in DIFFS[1:] if out[i][lv] != "empty"}
        out["section"] = (hit["from"], hit["to"])
    same = all(got[lv] == "keep" or (got[lv] == "empty" and not opts["parts"][i][lv]["have"])
               for i, got in ((i, out[i]) for i in INSTS) for lv in DIFFS)
    have_vocals = opts["vocals"]["have"]
    if same and (v == "keep" or (v == "none" and not have_vocals)):
        raise ValueError("Nothing to change: pick something to redo or add")
    return out


def _generated(inst, wav, tm, style="normal"):
    """(timed notes, by difficulty) for an instrument from its stem."""
    x, sr = audio.read_wav(wav)
    if inst == "drums":
        timed, feats = ml.drum_notes(x, sr, with_features=True)
        fn = chart.drum_notes
    else:
        timed, feats = ml.fretted_notes(inst, x, sr, with_features=True, style=style if inst == "guitar" else "normal")
        fn = chart.guitar_notes
    timed = structure.make_consistent(inst, timed, feats, tm)
    tm.snap = True
    tm.fit_grid([n[0] for n in timed])
    return timed, chart.by_difficulty(timed, tm, fn, inst)


def _rebuild(name: str, src, keep: set, expert_kept: bool, drums: bool, new_ev: list) -> mido.MidiTrack:
    """An instrument track: the kept levels message for message, plus the new events. Star power,
    solos and animations are written against the Expert part, so they stay only if it does."""
    if src is None:
        return chart._track(name, new_ev)
    opens, msgs = _has_opens(src), []
    for tick, m in _abs(src):
        if m.type in ("track_name", "end_of_track"):
            continue
        if m.type in ("note_on", "note_off"):
            lv = _level_of(m.note, opens, drums)
            if lv in keep or (lv is None and expert_kept):
                msgs.append((tick, m))
        elif m.type == "sysex" and len(m.data) > 4 and m.data[4] in (0, 1, 2, 3):
            if DIFFS[3 - m.data[4]] in keep:  # phrase-shift sysex: 0 easy ... 3 expert
                msgs.append((tick, m))
        elif expert_kept or (m.is_meta and m.type == "text" and "ENHANCED_OPENS" in m.text and keep):
            msgs.append((tick, m))
    msgs += [(tick, mido.Message(typ, note=note, velocity=100 if typ == "note_on" else 0)) for tick, typ, note in new_ev]
    # note-offs first at each tick, so one note can end where the next begins
    msgs.sort(key=lambda e: (e[0], 0 if e[1].type == "note_off" or (e[1].type == "note_on" and not e[1].velocity) else 1))
    tr, last = mido.MidiTrack([mido.MetaMessage("track_name", name=name, time=0)]), 0
    for tick, m in msgs:
        tr.append(m.copy(time=tick - last))
        last = tick
    tr.append(mido.MetaMessage("end_of_track", time=0))
    return tr


def _check_kept(path: Path, kept: dict) -> None:
    """Read the written file back: every note of every kept level must be there, unchanged."""
    mid = mido.MidiFile(str(path))
    for name, (want, levels, drums) in kept.items():
        tr = next(t for t in mid.tracks if t.name == name)
        if _levels_kept(tr, levels, drums) != want:
            raise RuntimeError(f"{name} didn't come through unchanged, so nothing was saved")


def run(folder: Path, plan: dict, on_step=lambda i: None, on_progress=lambda f: None,
        stop: threading.Event | None = None) -> Path:
    """Apply the person's choices. The original chart goes to backups first and is only replaced
    once the new notes.mid has been read back and checked. Returns the backup folder."""
    stop = stop or threading.Event()

    def check():
        if stop.is_set():
            raise pipeline.Cancelled()
    opts = options(folder)
    if opts is None or "blocked" in opts:
        raise Unsupported((opts or {}).get("blocked") or f"{folder.name} can't be recharted here")
    plan = check_plan(plan, opts)
    on_step(0)
    on_progress(0.02)
    files = [folder / n for n in ("notes.chart", "notes.mid", "song.ini") if (folder / n).exists()]
    audio.ensure_writable(files)
    ini = tidy.read_ini(folder)
    src = load(folder)
    tm = ChartTempo(src.tempos, src.delay)
    by = _provenance(folder, ini)
    needs_audio = plan["vocals"] in ("ai", "words") or any(plan[i]["expert"] == "ai" for i in INSTS)
    work = Path(tempfile.mkdtemp(prefix="chc_rechart_"))
    try:
        stems = {}
        if needs_audio:
            on_step(1)
            if _audio_source(folder) == "stems":
                stems = {n: audio.to_wav(folder / f"{n}.ogg", work / f"{n}.wav") for n in (*INSTS, "vocals")}
            else:
                wav = audio.to_wav(next(folder / n for n in SONG_AUDIO if (folder / n).exists()), work / "input.wav")
                extra = pipeline.guitar_stem_source() if plan["guitar"]["expert"] == "ai" else ""
                span = 0.4 if extra else 0.55
                pipeline._separate(wav, work, "htdemucs_ft", lambda f: on_progress(0.05 + span * f), stop)
                guitar = work / "other.wav"
                if extra:
                    guitar = pipeline.guitar_stem(wav, work, guitar, lambda f: on_progress(0.45 + 0.15 * f), stop)
                stems = {"guitar": guitar, "bass": work / "bass.wav", "drums": work / "drums.wav",
                         "vocals": work / "vocals.wav"}
            check()
        on_step(2)
        on_progress(0.62)
        tracks, kept, tiers, made, cleared = {}, {}, {}, set(), set()
        for inst in INSTS:
            pick, old, drums = plan[inst], src.parts.get(inst), inst == "drums"
            name = TRACK[inst]
            keep = {lv for lv in DIFFS if pick[lv] == "keep"}
            for lv in DIFFS:
                if pick[lv] in ("ai", "make") or (pick[lv] == "keep" and by(inst, lv) == "ai"):
                    made.add(f"{inst}:{lv}")
                if pick[lv] != "keep" and (pick[lv] != "empty" or opts["parts"][inst][lv]["have"]):
                    cleared.add(inst)
            if old is not None and len(keep) == sum(opts["parts"][inst][lv]["have"] for lv in DIFFS) \
                    and all(pick[lv] != "make" for lv in DIFFS) and pick["expert"] != "ai" \
                    and not plan.get("section"):
                tracks[name] = old  # nothing about this instrument changes
                kept[name] = (_levels_kept(old, keep, drums), keep, drums)
                continue
            fn = chart.drum_notes if drums else chart.guitar_notes
            built, new = None, {}
            style = plan["guitar_style"]
            if pick["expert"] == "ai" and plan.get("section"):
                # one section: the new Expert notes go in there, everything around it stays
                check()
                _, built = _generated(inst, stems[inst], tm, style)
                a, b = plan["section"]
                inside = [n for n in built["expert"] if n[0] >= a and (b is None or n[0] < b)]
                old = _rebuild(name, _cut_expert(old, a, b, drums), set(DIFFS), True, drums,
                               [e for e in chart._events({"expert": inside}, drums=drums)])
                keep = {lv for lv in DIFFS if pick[lv] == "keep"} | {"expert"}
                pick = pick | {"expert": "keep"}
                built = None
            if pick["expert"] == "ai":
                check()
                timed, built = _generated(inst, stems[inst], tm, style)
                new["expert"] = built["expert"]
                tiers[inst] = rating.tier(inst, timed)
            elif pick["expert"] == "keep" and any(pick[lv] == "make" for lv in DIFFS[1:]):
                tm.snap = False  # the thinned-out levels stay on the charter's exact ticks
                timed = (_hand_hits if drums else _hand_fretted)(old, tm)
                built = chart.by_difficulty(timed, tm, fn, inst)
            for lv in DIFFS[1:]:
                if pick[lv] == "make" and built:
                    new[lv] = built[lv]
            ev = chart._events(new, drums=drums)
            if drums and pick["expert"] == "keep":  # tom markers are shared, and the Expert part has them
                ev = [e for e in ev if e[2] not in TOMS]
            ev = list(set(ev))
            if keep or new:
                tracks[name] = _rebuild(name, old, keep, pick["expert"] == "keep", drums, ev)
                if keep:
                    kept[name] = (_levels_kept(old, keep, drums), keep, drums)
            on_progress({"guitar": 0.70, "bass": 0.78, "drums": 0.85}[inst])
            check()
        choice, sung = plan["vocals"], []
        on_progress(0.88)
        if choice == "keep":
            tracks["PART VOCALS"] = src.parts["vocals"]
            if by("vocals") == "ai":
                made.add("vocals")
        elif choice == "words":
            x, sr = audio.read_wav(stems["vocals"])
            midi, _ = vocals.track_pitch(x, sr)
            sung = _sing_words(src.words, src.phrases, midi, tm)
        elif choice == "ai" and vocals.available():
            try:
                x, sr = audio.read_wav(stems["vocals"])
                sung = vocals.chart(x, sr, ini.get("artist", ""), ini.get("name", ""))
            except Exception as e:  # the other parts are still worth having
                print(f"vocal chart failed: {e}")
        if sung:
            tracks["PART VOCALS"] = chart._vocal_track(sung, tm)
            made.add("vocals")
        check()
        on_step(3)
        on_progress(0.95)
        mid = mido.MidiFile(type=1, ticks_per_beat=TPQ)
        harmonies_stale = choice != "keep"
        mid.tracks += [t for t in src.base if not (harmonies_stale and t.name.strip().upper().startswith("HARM"))]
        mid.tracks += tracks.values()
        part = folder / "notes.mid.part"
        mid.save(part)
        try:
            phrases.add_to_mid(part, only=[TRACK[i] for i in INSTS if plan[i]["expert"] == "ai"])
            _check_kept(part, kept)
            audio.ensure_writable(files)
            saved = library.backup(folder, "before rechart", files=[f.name for f in files])
            if src.chart:
                n, errs = library.recycle([src.chart])
                if not n:
                    raise RuntimeError(errs[0])
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        part.replace(folder / "notes.mid")
        # what the app wrote this time, to compare hand fixes against later (kept parts aren't in it)
        library.save_original(folder, only=[TRACK[i] for i in INSTS if plan[i]["expert"] == "ai"]
                              + (["PART VOCALS"] if sung else []))
        tags = dict(x.split(":", 1) for x in ini.get("chc_models", "").split())
        tags |= {k: ml.model_tag(k) for k in tiers if ml.available(k)}
        if "guitar" in tiers and plan["guitar_style"] == "busier":
            tags["guitar"] = ml.model_tag(ml.BUSIER)
        fields = {MARKER: " ".join(sorted(made)), "chc_models": " ".join(f"{k}:{v}" for k, v in sorted(tags.items()))}
        fields |= {f"diff_{k}": v for k, v in tiers.items()}
        fields |= {f"diff_{i}": -1 for i in INSTS if TRACK[i] not in tracks}
        if sung:
            fields["diff_vocals"] = 2
        elif choice == "ai" or choice == "none":
            fields["diff_vocals"] = -1
        if src.chart:
            fields["sustain_cutoff_threshold"] = 2
        if src.pro or plan["drums"]["expert"] == "ai":
            fields["pro_drums"] = "True"
        # a rating was about the old chart of that part (data/feedback.jsonl keeps it)
        fields |= {feedback.key(i): "" for i in cleared if ini.get(feedback.key(i))}
        fields |= {feedback.tags_key(i): "" for i in cleared if ini.get(feedback.tags_key(i))}
        if choice != "keep" and ini.get(feedback.key("vocals")):
            fields[feedback.key("vocals")] = ""
        tidy.write_ini(folder, fields)
        on_progress(1.0)
        return saved
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _tempo_track(tempos, sigs) -> mido.MidiTrack:
    ev = [(k, 0, mido.MetaMessage("time_signature", numerator=n, denominator=d)) for k, n, d in sigs or [(0, 4, 4)]]
    ev += [(k, 1, mido.MetaMessage("set_tempo", tempo=us)) for k, us in tempos]
    tr = mido.MidiTrack([mido.MetaMessage("track_name", name="", time=0)])
    last = 0
    for k, _, msg in sorted(ev, key=lambda e: e[:2]):
        tr.append(msg.copy(time=k - last))
        last = k
    tr.append(mido.MetaMessage("end_of_track", time=0))
    return tr


def _events_track(events) -> mido.MidiTrack:
    tr = mido.MidiTrack([mido.MetaMessage("track_name", name="EVENTS", time=0)])
    last = 0
    for k, text in sorted(events):
        tr.append(mido.MetaMessage("text", text=f"[{text}]", time=k - last))
        last = k
    tr.append(mido.MetaMessage("end_of_track", time=0))
    return tr
