"""Clean up song folder names, e.g.
"Jimi Hendrix - Voodoo Child (Slight Return) (Live) (Harmonix)" -> "Jimi Hendrix - Voodoo Child",
and (with from_ini) give every song the same "Artist - Song Title" form, taken from the artist
and name in its song.ini ("voodoo_child_final" -> "Jimi Hendrix - Voodoo Child").

With caps, names get the library's capitals too: lowercase words get a capital first letter
("whats up" -> "Whats Up"), small words stay lowercase mid-title ("Ring of Fire"), words that
already have capitals are left alone (AC/DC, alt-J), and an artist already in the library keeps
its spelling there (dredg, blink-182).

Scanning never touches disk; apply() renames folders (and fixes the name/artist in song.ini)
and writes an undo log next to the songs folder root.
"""
from collections import Counter
import json
import re
from dataclasses import dataclass
from pathlib import Path
import unicodedata

SONG_MARKERS = ("song.ini", "notes.chart", "notes.mid")
NUMBERED = re.compile(r"^\d+[.)]\s+")  # "02. The Who - Squeeze Box": a setlist position
PARENS = re.compile(r"\s*\([^()]*\)")
BRACKETS = re.compile(r"\s*\[[^\[\]]*\]")
UNDO_LOG = ".chc_rename_log.json"
BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # not allowed in Windows folder names


def read_ini(folder: Path) -> dict:
    out = {}
    try:
        text = (folder / "song.ini").read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r"\s*([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$", line)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


def _key(name: str) -> str:
    """Name for comparing: no capitals or accents, '&' as 'and', letters and digits only."""
    plain = "".join(ch for ch in unicodedata.normalize("NFKD", name) if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", plain.lower().replace("&", "and"))


def _same_title(a: str, b: str) -> bool:
    """Same song title; "One/Two" in a song.ini is "One & Two" in a folder name."""
    return _key(a) == _key(b) or _key(a.replace("/", "&")) == _key(b) or _key(a) == _key(b.replace("/", "&"))


def _same_artist(a: str, b: str) -> bool:
    """Same act, even if one name is the fuller one ("Ramones" / "The Ramones",
    "Wings" / "Paul McCartney & Wings")."""
    x, y = ({_key(w) for w in n.split()} - {""} for n in (a, b))
    return _key(a) == _key(b) or bool(x and y and (x <= y or y <= x))


def _accents(text: str) -> int:
    return sum(1 for ch in text if ord(ch) > 127 and ch.isalpha())


def ini_name(folder: Path) -> str | None:
    """'Artist - Song Title' from the folder's song.ini, or None without both."""
    ini = read_ini(folder)
    artist, name = ini.get("artist", "").strip(), ini.get("name", "").strip()
    if not artist or not name:
        return None
    if name.lower().startswith(artist.lower() + " - "):
        return name  # the title already starts with the artist
    return f"{artist} - {name}"


def clean_name(name: str, parens: bool = True, brackets: bool = True) -> str:
    """Remove (...) and/or [...] groups, then tidy spaces and dangling separators."""
    out = name
    for _ in range(3):  # nested groups
        before = out
        if parens:
            out = PARENS.sub("", out)
        if brackets:
            out = BRACKETS.sub("", out)
        if out == before:
            break
    # (a final "." stays: "B.Y.O.B.", "L.A."; folder names lose it later, as Windows needs)
    out = re.sub(r"\s{2,}", " ", out).strip(" -_")
    out = re.sub(r"\s+-\s*$", "", out).strip()
    return out if out else name  # never produce an empty name


@dataclass
class Proposal:
    path: Path
    new_name: str
    note: str = ""      # why it cannot be applied, if so
    selected: bool = True
    ini_only: bool = False  # the folder name is fine; only song.ini's capitals change
    check: str = ""     # why it starts unticked: only a person can say which spelling is right

    @property
    def ok(self) -> bool:
        return not self.note


def is_song_folder(p: Path) -> bool:
    return p.is_dir() and any((p / m).exists() for m in SONG_MARKERS)


# ---------- capitals ----------
SMALL = {"a", "an", "the", "and", "but", "or", "nor", "for", "of", "on", "in", "at", "to", "by", "vs", "vs.",
         "with", "from", "as", "per", "via", "feat", "feat.", "ft", "ft.", "n", "o", "de", "la", "le", "x"}
STYLE_SMALL = {"a", "an", "the", "and", "or", "of", "on", "in", "at", "to", "for", "by", "with", "from"}


def fix_case(text: str, artist_map: dict | None = None) -> str:
    """Title capitals for a song or artist name. Only all-lowercase words change (or every word
    when the whole name is SHOUTED), so stylized names with capitals stay as they are."""
    if artist_map and _lkey(text) in artist_map:
        return artist_map[_lkey(text)]  # same letters, the library's capitals
    if not text or "<" in text:  # Clone Hero <color> tags: leave alone
        return text
    if re.search(r"[À-ÿ]", text):  # accented letters: probably not English, whose title rules differ
        return text
    letters = re.sub(r"[^A-Za-z]", "", text)
    # (a name of only one- and two-letter words, like "AC DC", is initials, not shouting)
    shouted = (len(text.split()) > 1 and letters.isupper() and len(letters) > 3
               and any(len(re.sub(r"[^A-Za-z]", "", w)) > 2 for w in text.split()))
    words = text.split(" ")
    out = []
    for i, w in enumerate(words):
        core = w.strip("\"'(“”‘[")
        if not core or not core[0].isalpha():  # "21st", "3's", "&": leave as they are
            out.append(w)
            continue
        if shouted:
            w = w.lower()
        elif w != w.lower():
            out.append(w)  # already has capitals: someone chose them
            continue
        prev = out[-1] if out else ""
        starts = i == 0 or prev.endswith((":", "-", "–", "(", "/")) or prev in ("-", "–") or w.startswith("(")
        last = i == len(words) - 1
        if core.lower() in SMALL and not starts and not last:
            out.append(w)
        else:
            k = next(j for j, ch in enumerate(w) if ch.isalpha())
            out.append(w[:k] + w[k].upper() + w[k + 1:])
    return " ".join(out)


def _lkey(name: str) -> str:
    """Name for matching spellings that differ only in capitals."""
    return " ".join(name.lower().split())


def artist_spellings(*roots: Path) -> dict:
    """How the library capitalizes each artist: {lowercased artist: most common spelling}.
    Only capitals are learned, never other spellings ('&' vs 'and', 'Blink 182' vs 'Blink-182')."""
    seen: dict[str, Counter] = {}

    def walk(d: Path):
        try:
            kids = [c for c in d.iterdir() if c.is_dir()]
        except OSError:
            return
        for c in kids:
            if is_song_folder(c):
                a = read_ini(c).get("artist", "").strip()
                if a and "<" not in a:
                    seen.setdefault(_lkey(a), Counter())[" ".join(a.split())] += 1
            else:
                walk(c)
    for r in roots:
        if r and Path(r).exists():
            walk(Path(r))
    def rank(kv):
        name, count = kv
        words = name.split()
        # deliberate styling first (KISS, DragonForce, TV), then standard title capitals with
        # small words lowercase ("Band of Horses"), then the most common spelling
        inner = sum(ch.isupper() for w in words for ch in w[1:])
        small_low = sum(1 for i, w in enumerate(words) if 0 < i < len(words) - 1 and w in SMALL)
        return inner, small_low, count
    return {k: _plain_small_words(max(c.items(), key=rank)[0]) for k, c in seen.items() if k}


def _plain_small_words(artist: str) -> str:
    """Charters often type "Band Of Horses"; the band is "Band of Horses". Only Title-case small
    words in mid-name change, so deliberate styling (KISS, TV) and foreign names (Al Di Meola) stay."""
    words = artist.split(" ")
    return " ".join(w.lower() if 0 < i < len(words) - 1 and w.istitle() and w.lower() in STYLE_SMALL else w
                    for i, w in enumerate(words))


def _split_name(name: str) -> tuple[str, str]:
    a, sep, t = name.partition(" - ")
    return (a, t) if sep else ("", name)


def _choose(folder: str, ini: str, trust_ini: bool = False) -> str:
    """Folder name or song.ini name, when both are this song's name. The same song keeps the
    folder's spelling (minus any setlist number), since song.ini is often the sloppier of the two,
    unless song.ini has accents the folder lacks ("Queensrÿche"). trust_ini: the user just typed
    the song.ini details, so a different artist name is a correction."""
    bare = NUMBERED.sub("", folder)
    (fa, ft), (ia, it) = _split_name(bare), _split_name(ini)
    artists_match = _key(fa) == _key(ia) if trust_ini else _same_artist(fa, ia)
    if _key(bare) == _key(ini) or (fa and artists_match and _same_title(ft, it)):
        return ini if _accents(ini) > _accents(bare) else bare
    return ini


def _word_change(folder: str, new: str) -> str:
    """Why this rename needs a person: the folder is already "Artist - Title" and song.ini has
    other words. Either side can be the typo ("Sliences", "All Nite" vs "All Night")."""
    fa, ft = _split_name(NUMBERED.sub("", folder))
    na, nt = _split_name(new)
    if not fa or (_same_artist(fa, na) and _same_title(ft, nt)):
        return ""
    return "song.ini words it differently, check which is right"


def proposed_name(c: Path, parens: bool = True, brackets: bool = True, from_ini: bool = True,
                  caps: bool = False, artist_map: dict | None = None, trust_ini: bool = False) -> str:
    """The tidy folder name for one song folder (the same rules as scan)."""
    cleaned = clean_name(c.name, parens, brackets)
    base = ini_name(c) if from_ini else None
    new = _choose(cleaned, clean_name(base, parens, brackets), trust_ini) if base else cleaned
    if caps:
        a, t = _split_name(new)
        new = f"{fix_case(a, artist_map)} - {fix_case(t)}" if a else fix_case(t)
    # "AC/DC" must not become "ACDC" or "One/Two" become "OneTwo"
    new = re.sub(r"\s*[/\\]\s*", " ", new)
    # Windows forbids some characters, and trailing dots or spaces
    return BAD_CHARS.sub("", new).rstrip(" .").strip() or c.name


def tidy_one(folder: Path, parens: bool = True, brackets: bool = True, caps: bool = True,
             artist_map: dict | None = None) -> Path:
    """Tidy a single song right away (after its details are edited, or a new conversion):
    clean the title in song.ini, fix capitals, and rename the folder 'Artist - Song Title'.
    Raises FileExistsError if that name is taken. Returns the folder's new path."""
    _fix_ini(folder, parens, brackets, caps, artist_map)
    target = folder.with_name(proposed_name(folder, parens, brackets, True, caps, artist_map, trust_ini=True))
    if str(target) != str(folder):  # Windows paths compare without capitals
        if target.exists() and target.resolve() != folder.resolve():
            raise FileExistsError(target.name)
        folder.rename(target)  # a case-only rename works on Windows too
    return target


def scan(root: Path, parens: bool = True, brackets: bool = True, from_ini: bool = True,
         caps: bool = False, artist_map: dict | None = None) -> list[Proposal]:
    """Find song folders under root whose names would change. from_ini names each song
    "Artist - Song Title" from its song.ini (when it has both); otherwise the folder name is
    only cleaned."""
    found: list[Proposal] = []
    taken: set[Path] = set()

    def walk(d: Path):
        try:
            kids = sorted(c for c in d.iterdir() if c.is_dir())
        except OSError:
            return
        for c in kids:
            if is_song_folder(c):
                new = proposed_name(c, parens, brackets, from_ini, caps, artist_map)
                if new == c.name and from_ini and _ini_fixes(c, parens, brackets, caps, artist_map):
                    found.append(Proposal(c, new, ini_only=True))
                elif new != c.name:
                    target = c.with_name(new)
                    note = ""
                    if target.exists() and target != c:
                        note = "a folder with that name already exists"
                    elif target in taken:
                        note = "another song would get the same name"
                    else:
                        taken.add(target)
                    check = "" if note else _word_change(clean_name(c.name, parens, brackets), new)
                    found.append(Proposal(c, new, note, selected=not (note or check), check=check))
            else:
                walk(c)
    walk(root)
    return found


def write_ini(folder: Path, fields: dict) -> None:
    """Set song.ini keys (replacing existing lines, adding the rest under [song]); other lines kept."""
    ini = folder / "song.ini"
    try:
        text = ini.read_text(encoding="utf-8-sig")
    except OSError:
        text = "[song]\n"
    for key, value in fields.items():
        value = " ".join(str(value).split())  # one line
        pat = re.compile(rf"(?mi)^(\s*{re.escape(key)}\s*=).*$")
        if pat.search(text):
            text = pat.sub(lambda m: f"{m.group(1)} {value}", text, count=1)
        elif re.search(r"(?mi)^\s*\[song\]\s*$", text):
            text = re.sub(r"(?mi)^(\s*\[song\]\s*)$", lambda m: f"{m.group(1)}\n{key} = {value}", text, count=1)
        else:
            text = text.rstrip("\n") + f"\n{key} = {value}\n"
    ini.write_text(text, encoding="utf-8")


def _ini_fixes(folder: Path, parens: bool, brackets: bool, caps: bool = False,
               artist_map: dict | None = None) -> tuple[str, str] | None:
    """(old text, new text) of song.ini with the tidy fixes, or None when nothing changes."""
    ini = folder / "song.ini"
    try:
        text = ini.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        m = re.match(r"(\s*(name|artist)\s*=\s*)(.*?)(\r?\n?)$", line, re.I)
        if not m:
            continue
        value = m.group(3)
        if m.group(2).lower() == "name":
            value = clean_name(value, parens, brackets)
            value = fix_case(value) if caps else value
        elif caps:
            value = fix_case(value, artist_map)
        if value.strip() != m.group(3).strip():  # only real changes, not stray spaces
            lines[i] = m.group(1) + value + m.group(4)
    new = "".join(lines)
    return (text, new) if new != text else None


def _fix_ini(folder: Path, parens: bool, brackets: bool, caps: bool = False,
             artist_map: dict | None = None) -> str | None:
    """Apply the song.ini fixes; returns the old text (for undo) when something changed."""
    fixes = _ini_fixes(folder, parens, brackets, caps, artist_map)
    if not fixes:
        return None
    try:
        (folder / "song.ini").write_text(fixes[1], encoding="utf-8")
    except OSError:
        return None
    return fixes[0]


def apply(proposals: list[Proposal], root: Path, parens: bool = True, brackets: bool = True,
          fix_ini: bool = True, caps: bool = False, artist_map: dict | None = None) -> tuple[int, list[str]]:
    """Rename the selected proposals. Returns (changed_count, error messages)."""
    done, errors, log = 0, [], []
    for p in proposals:
        if not (p.selected and p.ok):
            continue
        target = p.path.with_name(p.new_name)
        try:
            if str(target) != str(p.path):  # capitals-only renames too
                p.path.rename(target)
            entry = {"from": str(p.path), "to": str(target)}
            if fix_ini:
                old = _fix_ini(target, parens, brackets, caps, artist_map)
                if old is not None:
                    entry["ini"] = old
            log.append(entry)
            done += 1
        except OSError as e:
            errors.append(f"{p.path.name}: {e}")
    if log:
        try:
            (root / UNDO_LOG).write_text(json.dumps(log, indent=2), encoding="utf-8")
        except OSError:
            pass
    return done, errors


def can_undo(root: Path) -> bool:
    return (root / UNDO_LOG).exists()


def undo(root: Path) -> tuple[int, list[str]]:
    """Reverse the last apply(): folder names, and song.ini as it was before."""
    log_path = root / UNDO_LOG
    done, errors = 0, []
    try:
        entries = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, ["no undo information found"]
    for e in reversed(entries):
        try:
            if e["to"] != e["from"]:
                Path(e["to"]).rename(e["from"])
            if "ini" in e:
                (Path(e["from"]) / "song.ini").write_text(e["ini"], encoding="utf-8")
            done += 1
        except OSError as ex:
            errors.append(f"{Path(e['to']).name}: {ex}")
    if not errors:
        log_path.unlink(missing_ok=True)
    return done, errors
