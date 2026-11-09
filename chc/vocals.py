"""The vocal track YARG sings along to: notes from the vocal stem's pitch, words from published
lyrics (lrclib.net) timed by Whisper, or from Whisper alone when no lyrics are found."""
import json
import re
import unicodedata
import urllib.error
import urllib.parse
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from . import search

HOP = 0.01  # seconds per pitch frame
MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "whisper"
WHISPER = "turbo"
LOW, HIGH = 36, 84  # the MIDI notes a vocal track may use
PHRASE_MARK = 105
# tuned on validation songs (train.eval_vocals)
VOICED = 0.45      # pitch confidence above which a frame counts as sung
QUIET_DB = 32.0    # frames this far below the song's loud singing are silence
SPLIT = 0.75       # semitones a pitch must move from the note's own to start a new note
SPLIT_HOLD = 6     # frames it must stay there
MIN_NOTE = 0.07    # seconds
JOIN = 0.05        # silence shorter than this doesn't end a note
SNAP = 0.12        # a word starts on a note starting this close to it
REACH = 0.12       # how far outside a word a note can be and still be sung on it
PHRASE_GAP = 0.6   # silence that ends a phrase
LONG_PHRASE = 9.0  # a phrase this long ends at the next small gap
MATCH_FLOOR = 0.35 # published lyrics are only used if Whisper heard this share of them
MIN_WORDS, MIN_NOTES = 20, 30  # fewer than these: an instrumental, so no vocal track
LISTEN_AGAIN = 1.0 # seconds of singing without a word that earn a second listen
BRIDGE = 0.7       # pauses this short don't split a stretch of singing


@dataclass
class Word:
    text: str
    start: float
    end: float
    line_end: bool = False


@dataclass
class Note:
    start: float
    end: float
    pitch: int
    lyric: str = ""
    phrase: bool = False  # the first note of a phrase


def available() -> bool:
    try:
        import torchcrepe  # noqa: F401
        import whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _device() -> str:
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def track_pitch(x: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """(MIDI pitch per HOP frame, NaN where nothing is sung; loudness in dB per frame)."""
    import librosa
    import torch
    import torchcrepe
    y = librosa.resample(np.asarray(x, np.float32), orig_sr=sr, target_sr=16000)
    hop = int(16000 * HOP)
    f0, conf = torchcrepe.predict(torch.from_numpy(y)[None], 16000, hop, 65.0, 1100.0, "full",
                                  batch_size=1024, device=_device(), return_periodicity=True)
    conf = torchcrepe.filter.median(conf, 5)[0].cpu().numpy()
    f0 = f0[0].cpu().numpy()
    n = len(f0)
    rms = librosa.feature.rms(y=y, frame_length=hop * 4, hop_length=hop)[0]
    rms = np.pad(rms, (0, max(0, n - len(rms))))[:n]
    db = 20 * np.log10(rms + 1e-6)
    midi = 69 + 12 * np.log2(np.maximum(f0, 1.0) / 440.0)
    midi[(conf < VOICED) | (db < np.percentile(db, 95) - QUIET_DB)] = np.nan
    return midi, db


def find_notes(midi: np.ndarray) -> list[Note]:
    """Runs of steady sung pitch -> notes: a new one starts after silence or when the pitch
    settles somewhere else."""
    notes, n, i = [], len(midi), 0
    while i < n:
        if np.isnan(midi[i]):
            i += 1
            continue
        a, b, away, gap = i, i, 0, 0
        j = i + 1
        while j < n:
            if np.isnan(midi[j]):
                gap += 1
                if gap * HOP > JOIN:
                    break
            else:
                gap = 0
                here = np.nanmedian(midi[a:j])
                away = away + 1 if abs(midi[j] - here) > SPLIT else 0
                if away >= SPLIT_HOLD:
                    b = j - SPLIT_HOLD + 1
                    break
                b = j + 1
            j += 1
        else:
            b = max(b, a + 1)
        if (b - a) * HOP >= MIN_NOTE:
            notes.append(Note(a * HOP, b * HOP, int(round(float(np.nanmedian(midi[a:b]))))))
        i = max(b, a + 1)
    return notes


_DIGRAPH = {"ch", "sh", "th", "ph", "wh", "gh", "ck", "qu", "tr", "br", "cr", "dr", "fr", "gr", "pr",
            "bl", "cl", "fl", "gl", "pl", "sl", "st", "sp", "sc", "sk", "sm", "sn", "sw", "wr", "kn"}


def syllables(word: str) -> list[str]:
    """Split a word into sung syllables, one note each: "broken" -> ["bro", "ken"]."""
    low = word.lower()
    is_v = [c in "aeiouy" for c in low]
    groups, i = [], 0  # (first, last+1) of each vowel group
    while i < len(low):
        if is_v[i]:
            j = i
            while j < len(low) and is_v[j]:
                j += 1
            groups.append((i, j))
            i = j
        else:
            i += 1
    if len(groups) > 1:
        a, b = groups[-1]
        tail = low[a:]
        before = low[a - 1] if a else ""
        silent = (tail == "e" and not (before == "l" and a >= 2 and low[a - 2] not in "aeiouy")) \
            or (tail == "es" and before not in "sxz" and low[a - 2:a] not in ("ch", "sh")) \
            or (tail == "ed" and before not in "td") or tail in ("e's", "ed'")
        if silent:
            groups.pop()
    if len(groups) < 2:
        return [word]
    cuts = []
    for (_, end), (start, _) in zip(groups, groups[1:]):
        cons = low[end:start]
        if len(cons) <= 1:
            cuts.append(end)
        elif cons[-2:] in _DIGRAPH and (len(cons) == 2 or cons[:2] not in _DIGRAPH):
            cuts.append(start - 2)
        else:
            cuts.append(end + 1 if cons[:2] not in _DIGRAPH else end + 2)
    return [word[a:b] for a, b in zip([0] + cuts, cuts + [len(word)])]


def clean_word(text: str) -> str:
    """A word as a lyric: no symbols the chart format reads as commands, and Latin-1 only."""
    t = text.replace("’", "'").replace("‘", "'")
    t = unicodedata.normalize("NFC", t).encode("latin1", "ignore").decode("latin1")
    t = re.sub(r"[#^+=$%/\\\[\]{}\"*_~<>|]", "", t).strip(" \t.,;:!?-")
    return t


def _key(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def lookup_lyrics(artist: str, title: str, length: float) -> list[list[str]] | None:
    """Published lyrics as lines of words, or None if lrclib doesn't have this song."""
    q = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
    def fetch(url):
        try:
            with search._request(url, timeout=10) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return None
    hits = fetch(f"https://lrclib.net/api/search?{q}")
    if not artist or not hits:
        return None
    hits = [h for h in hits if (h.get("syncedLyrics") or h.get("plainLyrics"))
            and abs((h.get("duration") or length) - length) <= 12]
    if not hits:
        return None
    best = min(hits, key=lambda h: (not h.get("syncedLyrics"), abs((h.get("duration") or length) - length)))
    text = best.get("syncedLyrics") or best["plainLyrics"]
    lines = [re.sub(r"\[[^\]]*\]", "", ln).split() for ln in text.splitlines()]
    lines = [[w for w in map(clean_word, ln) if w] for ln in lines]
    return [ln for ln in lines if ln] or None


def _hear(model, y: np.ndarray, at: float = 0.0) -> list[Word]:
    """Whisper on 16 kHz audio starting at `at` seconds. It normally skips what it isn't sure is
    speech, which loses whole sung sections; _is_sung drops whatever it invents instead."""
    import torch
    r = model.transcribe(y, word_timestamps=True, condition_on_previous_text=False, verbose=None,
                         no_speech_threshold=None, logprob_threshold=None, temperature=0.0,  # same result every run
                         fp16=torch.cuda.is_available())
    out = []
    for seg in r["segments"]:
        ws = [Word(clean_word(w["word"]), float(w["start"]) + at, float(w["end"]) + at)
              for w in seg.get("words", [])]
        ws = [w for w in ws if w.text and w.end > w.start]
        if ws:
            ws[-1].line_end = True
            out += ws
    return out


def _unheard(midi: np.ndarray, words: list[Word]) -> list[tuple[float, float]]:
    """Stretches of singing (start, end in seconds) that no heard word covers."""
    left = ~np.isnan(midi)
    for w in words:
        left[max(0, int((w.start - 0.2) / HOP)):int((w.end + 0.2) / HOP)] = False
    idx = np.flatnonzero(left)
    if not len(idx):
        return []
    runs = np.split(idx, np.flatnonzero(np.diff(idx) * HOP > BRIDGE) + 1)
    return [(r[0] * HOP, (r[-1] + 1) * HOP) for r in runs if len(r) * HOP >= LISTEN_AGAIN]


def transcribe(x: np.ndarray, sr: int, midi: np.ndarray) -> list[Word]:
    """What Whisper hears in the vocal stem, word by word with times. Sung stretches it missed get
    a second listen on their own, where the rest of the song can't drown them out."""
    import librosa
    import torch
    import whisper
    model = whisper.load_model(WHISPER, device=_device(), download_root=str(MODEL_DIR))
    y = librosa.resample(np.asarray(x, np.float32), orig_sr=sr, target_sr=16000)
    out = _hear(model, y)
    extra = []
    for a, b in _unheard(midi, [w for w in out if _is_sung(w, midi)]):
        start = max(0.0, a - 1.0)
        got = _hear(model, y[int(start * 16000):int((b + 1.0) * 16000)], start)
        extra += [w for w in got if a - 0.3 < (w.start + w.end) / 2 < b + 0.3]
    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return sorted(out + extra, key=lambda w: w.start)


def _is_sung(w: Word, midi: np.ndarray) -> bool:
    """Whisper invents words over silence: keep words with singing under them."""
    part = midi[int(w.start / HOP):max(int(w.end / HOP), int(w.start / HOP) + 1)]
    return part.size > 0 and np.mean(~np.isnan(part)) > 0.25


def _spread(words: list[str], a: float, b: float, line_ends: list[bool]) -> list[Word]:
    """Words shared across a stretch of time by length."""
    weight = np.array([max(1, len(w)) for w in words], float)
    edges = a + (b - a) * np.concatenate([[0], np.cumsum(weight)]) / weight.sum()
    return [Word(w, float(s), float(e), le) for w, s, e, le in zip(words, edges, edges[1:], line_ends)]


def align_lyrics(lines: list[list[str]], heard: list[Word], midi: np.ndarray) -> list[Word] | None:
    """Published words timed by what Whisper heard. None when they don't match (wrong song or
    version). Stretches only Whisper heard (repeats the published text leaves out) are kept."""
    text, ends = [], []
    for ln in lines:
        text += ln
        ends += [False] * (len(ln) - 1) + [True]
    keys, heard_keys = [_key(w) for w in text], [_key(w.text) for w in heard]
    sm = SequenceMatcher(None, keys, heard_keys, autojunk=False)
    if not text or sum(b.size for b in sm.get_matching_blocks()) < MATCH_FLOOR * len(text):
        return None
    out: list[Word] = []
    ops = sm.get_opcodes()
    for k, (op, i1, i2, j1, j2) in enumerate(ops):
        if op == "equal":
            out += [Word(text[i], heard[j].start, heard[j].end, ends[i])
                    for i, j in zip(range(i1, i2), range(j1, j2))]
        elif op == "replace":
            pub, got = i2 - i1, j2 - j1
            if got > 2 * pub + 2:  # far more was sung than published here: a repeat the text leaves out
                out += heard[j1:j2]
            else:
                out += _spread(text[i1:i2], heard[j1].start, heard[j2 - 1].end, ends[i1:i2])
        elif op == "insert":
            if j2 - j1 >= 3:
                out += heard[j1:j2]
        else:  # published but unheard: spread over the singing between its neighbours
            a = heard[j1 - 1].end if j1 else 0.0
            b = heard[j1].start if j1 < len(heard) else len(midi) * HOP
            frames = np.flatnonzero(~np.isnan(midi[int(a / HOP):int(b / HOP)]))
            if len(frames) * HOP >= 0.1 * (i2 - i1):
                span = a + frames[0] * HOP, a + (frames[-1] + 1) * HOP
                out += _spread(text[i1:i2], *span, ends[i1:i2])
    return out


def _split(note: Note, at: float) -> tuple[Note, Note]:
    return Note(note.start, at, note.pitch), Note(at, note.end, note.pitch)


def sing(notes: list[Note], words: list[Word], midi: np.ndarray) -> list[Note]:
    """Put words on notes, one syllable each: notes are split where a word starts inside them,
    and made from the pitch under a word nothing was found for. Notes with no word are dropped."""
    notes = sorted(notes, key=lambda n: n.start)
    for w in words:  # word starts land on note starts, or split the note they fall in
        starts = np.array([n.start for n in notes])
        near = int(np.argmin(np.abs(starts - w.start))) if len(starts) else -1
        if near >= 0 and abs(starts[near] - w.start) <= SNAP:
            w.start = float(starts[near])
            continue
        inside = next((i for i, n in enumerate(notes)
                       if n.start + MIN_NOTE < w.start < n.end - MIN_NOTE), None)
        if inside is not None:
            notes[inside:inside + 1] = _split(notes[inside], w.start)
    by_word: list[list[Note]] = [[] for _ in words]
    for n in notes:
        best, overlap = None, -REACH
        for i, w in enumerate(words):
            o = min(n.end, w.end) - max(n.start, w.start)
            if o > overlap:
                best, overlap = i, o
        if best is not None:
            by_word[best].append(n)
    out, line_ends = [], []
    for w, ns in zip(words, by_word):
        if not ns:
            part = midi[int(w.start / HOP):int(w.end / HOP) + 1]
            if not part.size or np.all(np.isnan(part)):
                continue
            ns = [Note(w.start, max(w.end, w.start + MIN_NOTE), int(round(float(np.nanmedian(part)))))]
        merged = [ns[0]]
        for n in ns[1:]:  # the same pitch again inside a word is one held note
            if n.pitch == merged[-1].pitch and n.start - merged[-1].end <= JOIN * 3:
                merged[-1].end = n.end
            else:
                merged.append(n)
        syl = syllables(w.text)
        while len(merged) < len(syl):
            i = max(range(len(merged)), key=lambda k: merged[k].end - merged[k].start)
            n = merged[i]
            merged[i:i + 1] = _split(n, (n.start + n.end) / 2)
        for k, n in enumerate(merged):
            n.lyric = "+" if k >= len(syl) else syl[k] + ("-" if k < len(syl) - 1 else "")
            line_ends.append(w.line_end and k == len(merged) - 1)
        out += merged
    return _mark_phrases(out, line_ends)


def _mark_phrases(notes: list[Note], line_ends: list[bool]) -> list[Note]:
    begin, start = True, 0.0
    for i, n in enumerate(notes):
        n.phrase = begin
        if begin:
            start = n.start
        gap = notes[i + 1].start - n.end if i + 1 < len(notes) else PHRASE_GAP
        begin = line_ends[i] or gap >= PHRASE_GAP or (n.end - start >= LONG_PHRASE and gap > 0.1)
    return notes


def chart(x: np.ndarray, sr: int, artist: str = "", title: str = "") -> list[Note]:
    """Vocal stem -> sung notes with lyrics; empty for an instrumental."""
    midi, _ = track_pitch(x, sr)
    heard = [w for w in transcribe(x, sr, midi) if _is_sung(w, midi)]
    return from_parts(midi, heard, lookup_lyrics(artist, title, len(x) / sr) if title else None)


def from_parts(midi: np.ndarray, heard: list[Word], lines: list[list[str]] | None) -> list[Note]:
    words = (align_lyrics(lines, heard, midi) if lines else None) or heard
    if len(words) < MIN_WORDS:
        return []
    out = sing(find_notes(midi), [Word(w.text, w.start, w.end, w.line_end) for w in words], midi)
    return out if len(out) >= MIN_NOTES else []
