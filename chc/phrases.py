"""Star power, solo sections and section names for charts that only have notes.

Star power follows how the human charts in the library use it (train split, 679 songs): about one
phrase every 9 bars, a bar or two long, around 9 notes. Solos are stretches of busy guitar with no
singing. Sections come from the lyrics: lines that come back are the chorus. Works on a written
notes.mid, so existing songs can get it without touching a note."""
import re
from collections import defaultdict
from pathlib import Path

import mido
import numpy as np

from .chart import DIFF_BASE, TPQ

BAR = 4 * TPQ
STAR, SOLO = 116, 103
EXPERT = DIFF_BASE["expert"]
GAP_BARS, MIN_GAP, MAX_GAP = 12, 6, 18
MIN_NOTES, BEST_NOTES, MAX_NOTES = 4, 9, 18
LONG_NOTE = TPQ // 2
# tuned on train songs against human solo markers (.tmp/eval_phrases.py)
SOLO_BUSY, SOLO_MIN_BARS, SOLO_SHARE, SOLO_SINGLE, SOLO_MOVES = 0.8, 4, 0.5, 0.8, 0.5
CHUNK = 4          # bars per piece of a song when naming sections
SAME_WORDS = 0.5   # share of words two pieces must have in common to be the same lines
FAR = 3            # chunks apart two pieces must be to count as a repeat
PARTS = ("PART GUITAR", "PART BASS", "PART DRUMS")


def _abs(track):
    tick = 0
    for m in track:
        tick += m.time
        yield tick, m


def _spans(track) -> list:
    on, out = {}, []
    for tick, m in _abs(track):
        if m.type == "note_on" and m.velocity:
            on.setdefault(m.note, tick)
        elif m.type in ("note_on", "note_off") and m.note in on:
            out.append((on.pop(m.note), m.note, tick))
    return sorted(out)


def expert_notes(track) -> list:
    """(tick, end, lanes) of each Expert note or chord."""
    ends, lanes = {}, defaultdict(set)
    for a, n, b in _spans(track):
        if EXPERT <= n <= EXPERT + 4:
            ends[a] = max(ends.get(a, 0), b)
            lanes[a].add(n - EXPERT)
    return [(a, ends[a], tuple(sorted(lanes[a]))) for a in sorted(ends)]


def star_spans(notes: list) -> list:
    """(start tick, end tick) of star power phrases over (tick, end) notes."""
    if len(notes) < 30:
        return []
    ticks = np.array([n[0] for n in notes])
    ends = np.array([n[1] for n in notes])
    first, last = int(ticks[0]) // BAR, int(ticks[-1]) // BAR
    spans, prev = [], first - 2

    def window(b, length):
        return np.searchsorted(ticks, b * BAR), np.searchsorted(ticks, (b + length) * BAR)

    while prev + MIN_GAP <= last:
        best, score = None, -1e9
        for b in range(max(first, prev + MIN_GAP), min(last, prev + MAX_GAP) + 1):
            for length in (1, 2):
                i, j = window(b, length)
                n = j - i
                if not MIN_NOTES <= n <= MAX_NOTES:
                    continue
                held = ends[j - 1] - ticks[j - 1] >= LONG_NOTE  # players like a phrase ending on a long note
                s = -abs(b - (prev + GAP_BARS)) / 4 - abs(n - BEST_NOTES) / BEST_NOTES + 0.5 * held \
                    + 0.3 * (b % 4 == 0) - 0.2 * (length - 1)
                if s > score:
                    best, score = (b, length, i, j), s
        if best is None:
            prev += MAX_GAP
            continue
        b, length, i, j = best
        end = int(max(ends[j - 1], ticks[j - 1] + 1))
        if j < len(ticks):
            end = min(end, int(ticks[j]))
        spans.append((int(ticks[i]), end))
        prev = b + length - 1
    return spans


def _sung_bars(vocal_track) -> dict:
    """{bar: syllables sung in it}; empty when there's no vocal part."""
    if vocal_track is None:
        return {}
    bars = defaultdict(list)
    lyrics = {tick: m.text for tick, m in _abs(vocal_track) if m.type == "lyrics" and not m.text.startswith("[")}
    for tick, text in lyrics.items():
        word = re.sub(r"[^a-z']", "", text.lower())
        bars[tick // BAR].append(word)
    return bars


def solo_spans(notes: list, sung: dict) -> list:
    """(start, end) of stretches of lead guitar (busy, single notes, moving around) with no singing.
    Needs a vocal part: without one, busy guitar could be anything."""
    if not sung or len(notes) < 30:
        return []
    per_bar, prev = defaultdict(lambda: [0, 0, 0]), None  # notes, single notes, lane changes
    for tick, _, lanes in notes:
        row = per_bar[tick // BAR]
        row[0] += 1
        row[1] += len(lanes) == 1
        row[2] += prev is not None and lanes != prev
        prev = lanes
    usual = float(np.median([r[0] for r in per_bar.values()]))

    def lead(x):
        n, single, moved = per_bar[x] if x in per_bar else (0, 0, 0)
        return n >= SOLO_BUSY * usual and single >= SOLO_SINGLE * n and moved >= SOLO_MOVES * n

    last = max(per_bar)
    out, b = [], min(sung)  # not before the singing starts: that's the intro
    while b <= last:
        if sung.get(b) or b not in per_bar:
            b += 1
            continue
        a = b
        while b <= last and not sung.get(b):
            b += 1
        if b - a >= SOLO_MIN_BARS and sum(lead(x) for x in range(a, b)) / (b - a) >= SOLO_SHARE:
            inside = [n for n in notes if a * BAR <= n[0] < b * BAR]
            if inside:
                out.append((inside[0][0], max(inside[-1][1], inside[-1][0] + 1)))
    return out


def _same(a: set, b: set) -> bool:
    return len(a) >= 3 and len(b) >= 3 and len(a & b) / max(len(a), len(b)) >= SAME_WORDS


def section_names(first_bar: int, last_bar: int, sung: dict, solos: list) -> list:
    """[(bar, name)] for the song, from which lines repeat. Empty without a vocal part."""
    if not sung:
        return []
    n = (last_bar - first_bar) // CHUNK + 1
    words = [{w for x in range(first_bar + k * CHUNK, first_bar + (k + 1) * CHUNK) for w in sung.get(x, []) if w} for k in range(n)]
    solo_bars = {x for a, b in solos for x in range(a // BAR, b // BAR + 1)}
    first_voc = next((k for k, w in enumerate(words) if w), None)
    last_voc = next((k for k in range(n - 1, -1, -1) if words[k]), None)
    if first_voc is None:
        return []
    labels = []
    for k in range(n):
        bars = range(first_bar + k * CHUNK, first_bar + (k + 1) * CHUNK)
        if sum(x in solo_bars for x in bars) * 2 >= CHUNK:
            labels.append("Guitar Solo")
        elif k < first_voc:
            labels.append("Intro")
        elif k > last_voc:
            labels.append("Outro")
        elif not words[k]:
            labels.append("Break")
        elif any(abs(j - k) >= FAR and _same(words[k], words[j]) for j in range(n)):
            labels.append("Chorus")
        else:
            labels.append("Verse")
    for k in range(1, n - 1):  # one odd piece inside a section belongs to it
        if labels[k - 1] == labels[k + 1] != labels[k] and labels[k] in ("Verse", "Chorus", "Break"):
            labels[k] = labels[k - 1]
    runs, seen, total = [], defaultdict(int), defaultdict(int)
    for k, name in enumerate(labels):
        if not k or labels[k - 1] != name:
            runs.append((first_bar + k * CHUNK, name))
            total[name] += 1
    out = []
    for bar, name in runs:
        seen[name] += 1
        out.append((bar, f"{name} {seen[name]}" if total[name] > 1 else name))
    return out


def _with_notes(track, spans, note: int) -> mido.MidiTrack:
    """A copy of a track with one marker note added over each span."""
    msgs = [(t, m) for t, m in _abs(track) if m.type not in ("track_name", "end_of_track")]
    for a, b in spans:
        msgs += [(a, mido.Message("note_on", note=note, velocity=100)), (b, mido.Message("note_off", note=note, velocity=0))]
    msgs.sort(key=lambda e: (e[0], 0 if e[1].type == "note_off" or (e[1].type == "note_on" and not e[1].velocity) else 1))
    tr, last = mido.MidiTrack([mido.MetaMessage("track_name", name=track.name, time=0)]), 0
    for t, m in msgs:
        tr.append(m.copy(time=t - last))
        last = t
    tr.append(mido.MetaMessage("end_of_track", time=0))
    return tr


def add_to_mid(path: Path, only=PARTS, dry: bool = False) -> dict:
    """Add star power and solos to the named instrument tracks of a notes.mid if they have none,
    and section names if the song has none. Notes are never changed. Returns what was added."""
    mid = mido.MidiFile(str(path))
    if mid.ticks_per_beat != TPQ:
        return {}
    by = {t.name.strip().upper(): k for k, t in enumerate(mid.tracks)}
    sung = _sung_bars(mid.tracks[by["PART VOCALS"]] if "PART VOCALS" in by else None)
    added, solos = {}, []
    if "PART GUITAR" in by:
        solos = solo_spans(expert_notes(mid.tracks[by["PART GUITAR"]]), sung)
    all_ticks = []
    for name in PARTS:
        if name not in by:
            continue
        tr = mid.tracks[by[name]]
        notes = expert_notes(tr)
        all_ticks += [n[0] for n in notes]
        if name not in only:
            continue
        held = {n for _, n, _ in _spans(tr)}
        if STAR not in held and SOLO not in held:
            stars = star_spans(notes)
            part = solos if name == "PART GUITAR" else []
            added[name] = {"star": len(stars), "solo": len(part)}
            if not dry and (stars or part):
                new = _with_notes(tr, stars, STAR)
                mid.tracks[by[name]] = _with_notes(new, part, SOLO) if part else new
    if all_ticks and "EVENTS" in by and not any(m.type == "text" and "section" in m.text for m in mid.tracks[by["EVENTS"]]):
        names = section_names(min(all_ticks) // BAR, max(all_ticks) // BAR, sung, solos)
        added["sections"] = len(names)
        if names and not dry:
            ev = mid.tracks[by["EVENTS"]]
            msgs = [(t, m) for t, m in _abs(ev) if m.type not in ("track_name", "end_of_track")]
            msgs += [(bar * BAR, mido.MetaMessage("text", text=f"[section {name}]")) for bar, name in names]
            msgs.sort(key=lambda e: e[0])
            tr, last = mido.MidiTrack([mido.MetaMessage("track_name", name="EVENTS", time=0)]), 0
            for t, m in msgs:
                tr.append(m.copy(time=t - last))
                last = t
            tr.append(mido.MetaMessage("end_of_track", time=0))
            mid.tracks[by["EVENTS"]] = tr
    if not dry and added:
        tmp = Path(str(path) + ".part")
        mid.save(tmp)
        tmp.replace(path)
    return added
