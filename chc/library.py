"""Songs on disk: which ones this app made, backups before replacing, duplicates.

- is_ours: a song folder this app generated (song.ini marker, or for older songs the app's own
  layout: its four stems, no song.ogg, and a chart without section names). Only these are ever
  re-charted or replaced; real charts (Quickplay, downloads) are never touched.
- backup: copy a song's chart files (or move the whole folder) to "chart backups\\<date> <label>"
  next to the Clone Hero songs folder, outside it so Clone Hero doesn't list them twice.
- find_duplicates: song folders that are the same song under different names ("Artist - Song",
  "Song - Artist", "artist - song (Live) [HQ]", "01. Artist - Song"...).
- recycle: send folders to the Windows Recycle Bin (restorable), never delete outright.
"""
import ctypes
import hashlib
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import mido

from . import tidy

MARKER = "chc_generated"  # song.ini key written by this app
OUR_STEMS = ("guitar.ogg", "bass.ogg", "drums.ogg", "vocals.ogg")


def config_path() -> Path:
    """The app's settings file. Settings saved under the app's old name move over once."""
    new, old = Path.home() / ".rockwizard.json", Path.home() / ".clone_hero_converter.json"
    if old.exists() and not new.exists():
        old.rename(new)
    return new


def is_ours(folder: Path) -> bool:
    ini = tidy.read_ini(folder)
    if ini.get(MARKER):
        return True
    if not all((folder / f).exists() for f in OUR_STEMS) or (folder / "song.ogg").exists():
        return False
    mid = folder / "notes.mid"
    if not mid.exists():
        return False
    try:
        for tr in mido.MidiFile(str(mid), clip=True).tracks:
            if tr.name.strip().upper() == "EVENTS" and any(m.type == "text" for m in tr):
                return False  # real charts name their sections; ours leave EVENTS empty
    except Exception:
        return False
    return True


def backup_root(path: Path) -> Path:
    """'chart backups' next to the Clone Hero songs folder (the nearest parent called 'songs'),
    so backups are outside what Clone Hero scans."""
    p = Path(path).resolve()
    for parent in [p, *p.parents]:
        if parent.name.lower() == "songs":
            return parent.parent / "chart backups"
    return p.parent / "chart backups"


def backup(folder: Path, label: str, files=None, move=False) -> Path:
    """Back up a song folder (just `files` if given) into chart backups\\<date> <label>\\.
    move=True moves the whole folder there instead of copying."""
    dest_root = backup_root(folder) / f"{time.strftime('%Y-%m-%d')} {label}"
    dest = dest_root / folder.name
    k = 2
    while dest.exists():
        dest = dest_root / f"{folder.name} ({k})"
        k += 1
    dest_root.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(folder), str(dest))
    else:
        dest.mkdir()
        for f in files or [p.name for p in folder.iterdir() if p.is_file()]:
            if (folder / f).exists():
                shutil.copy2(folder / f, dest / f)
    return dest


def song_key(artist: str, title: str) -> str:
    """Same-song key: no capitals, tags in brackets, punctuation, '&' vs 'and' or a leading 'The'."""
    def norm(s):
        s = re.sub(r"[\(\[].*?[\)\]]", " ", (s or "").lower()).replace("&", " and ")
        s = re.sub(r"^\s*(\d+\s*[.)-]\s*)", "", s)  # '01. Artist'
        s = re.sub(r"^\s*the\s+", "", s)
        return re.sub(r"[^a-z0-9]+", "", s)
    return f"{norm(artist)}|{norm(title)}"


def names_of(folder: Path) -> tuple[str, str]:
    """(artist, title) of a song folder: from song.ini, else from its name."""
    ini = tidy.read_ini(folder)
    if ini.get("artist") and ini.get("name"):
        return ini["artist"], ini["name"]
    name = re.sub(r"^\s*\d+\s*[.)-]\s*", "", folder.name)
    if " - " in name:
        a, t = name.split(" - ", 1)
        return a.strip(), t.strip()
    return "", name


def find_existing(root: Path, artist: str, title: str, ours_only=True) -> list[Path]:
    """Song folders under root that are this song (either name order)."""
    keys = {song_key(artist, title), song_key(title, artist)}
    found = []
    for folder in song_folders(root):
        if song_key(*names_of(folder)) in keys and (not ours_only or is_ours(folder)):
            found.append(folder)
    return found


def song_folders(root: Path) -> list[Path]:
    out = []

    def walk(d: Path):
        try:
            kids = sorted(c for c in d.iterdir() if c.is_dir())
        except OSError:
            return
        for c in kids:
            (out.append(c) if tidy.is_song_folder(c) else walk(c))
    walk(Path(root))
    return out


def prepare_target(songs_dir: Path, artist: str, title: str, folder_name: str) -> list[Path]:
    """Before writing a new song: back up (move away) earlier versions this app made of the same
    song, whether under the same folder name or an older one, so the library ends up with one copy.
    Returns the backup locations."""
    moved = []
    target = songs_dir / folder_name
    olds = find_existing(songs_dir, artist, title) if songs_dir.exists() else []
    if target.exists() and is_ours(target) and target not in olds:
        olds.append(target)
    for old in olds:
        moved.append(backup(old, "replaced by newer conversion", move=True))
    return moved


@dataclass
class Copy:
    folder: Path
    artist: str
    title: str
    charter: str
    parts: list = field(default_factory=list)
    audio_files: int = 0
    size_mb: float = 0.0
    modified: float = 0.0
    ours: bool = False
    chart_hash: str = ""
    keep: bool = False       # the suggested copy to keep
    identical: bool = False  # same chart file as the kept copy: a true leftover duplicate


def describe(folder: Path) -> Copy:
    ini = tidy.read_ini(folder)
    a, t = names_of(folder)
    parts = [p for p in ("guitar", "bass", "drums", "keys", "vocals")
             if ini.get(f"diff_{p}", "-1").strip() not in ("-1", "")]
    files = [p for p in folder.iterdir() if p.is_file()]
    audio = [p for p in files if p.suffix.lower() in (".ogg", ".opus", ".mp3", ".wav")]
    chart = next((folder / n for n in ("notes.mid", "notes.chart") if (folder / n).exists()), None)
    digest = hashlib.md5(chart.read_bytes()).hexdigest() if chart else ""
    charter = re.sub(r"<[^>]*>", "", ini.get("charter", ini.get("frets", "")))  # strip colour tags
    return Copy(folder, a, t, charter or "unknown", parts, len(audio),
                sum(p.stat().st_size for p in files) / 1e6,
                max((p.stat().st_mtime for p in files), default=0.0), is_ours(folder), digest)


def find_duplicates(root: Path) -> list[list[Copy]]:
    """Groups (2+) of song folders that are the same song. In each group the suggested copy to
    keep is marked: a human-made chart over a generated one, then more instruments charted,
    more audio files, the cleanly named copy, the newer one. Copies whose chart file is identical to the kept one are
    true leftovers (identical=True); the rest are different versions (live, 2x bass pedal, another
    charter...) that someone may well want to keep."""
    by_key = {}
    for folder in song_folders(root):
        a, t = names_of(folder)
        k1, k2 = song_key(a, t), song_key(t, a)
        key = k1 if k1 in by_key or k2 not in by_key else k2  # either name order
        by_key.setdefault(key, []).append(folder)
    groups = []
    for folders in by_key.values():
        if len(folders) < 2:
            continue
        copies = [describe(f) for f in folders]
        # a human chart, more instruments, more audio; between equals the cleanly named copy
        # ("Artist - Song" rather than "... (Live)" / "01. ..."), then the newer one
        clean = lambda c: c.folder.name == tidy.clean_name(c.folder.name) and not c.folder.name[:1].isdigit()
        best = max(copies, key=lambda c: (not c.ours, len(c.parts), c.audio_files, clean(c),
                                          -len(c.folder.name), c.modified))
        best.keep = True
        for c in copies:
            c.identical = not c.keep and bool(c.chart_hash) and c.chart_hash == best.chart_hash
        groups.append(sorted(copies, key=lambda c: not c.keep))
    return sorted(groups, key=lambda g: g[0].folder.name.lower())


def recycle(paths) -> tuple[int, list[str]]:
    """Send files/folders to the Windows Recycle Bin. Returns (count, errors)."""
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 4, 16, 64, 1024
    done, errors = 0, []
    for p in paths:
        op = SHFILEOPSTRUCTW(None, FO_DELETE, str(Path(p).resolve()) + "\0", None,
                             FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI, 0, None, None)
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if rc == 0 and not op.fAnyOperationsAborted:
            done += 1
        else:
            errors.append(f"{Path(p).name}: could not move to the Recycle Bin (code {rc})")
    return done, errors
