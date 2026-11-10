"""Unpack .sng files (the single-file song package Chorus / enchor.us serves) into a normal song
folder: song.ini, notes.mid or notes.chart, audio and art. The app's library, review, rechart
and the Steam Deck sync all work on folders, so downloaded charts are kept as folders.

Format (github.com/mdsitton/SngFileFormat), little-endian:
  "SNGPKG", uint32 version, 16-byte xor mask
  uint64 metadataLen, uint64 count, count x (int32 len, key, int32 len, value)  -> song.ini [song]
  uint64 fileMetaLen, uint64 count, count x (byte len, name, uint64 size, uint64 offset)
  uint64 fileDataLen, file data; each file's bytes are masked with
      mask[i % 16] ^ (i & 0xFF), i counting from 0 within that file
"""
import re
import struct
from pathlib import Path

import numpy as np

MAGIC = b"SNGPKG"


class BadSng(ValueError):
    pass


def read(path: Path) -> tuple[dict, dict]:
    """(song.ini fields, {relative file name: bytes})."""
    buf = Path(path).read_bytes()
    if buf[:6] != MAGIC:
        raise BadSng(f"{Path(path).name} is not a .sng file")
    mask = np.frombuffer(buf[10:26], dtype=np.uint8)
    pos = 26

    def u64():
        nonlocal pos
        v = struct.unpack_from("<Q", buf, pos)[0]
        pos += 8
        return v

    def text(n):
        nonlocal pos
        s = buf[pos:pos + n].decode("utf-8", "replace")
        pos += n
        return s

    def i32_text():
        nonlocal pos
        n = struct.unpack_from("<i", buf, pos)[0]
        pos += 4
        return text(n)

    try:
        meta_len = u64()
        end = pos + meta_len
        meta = {}
        for _ in range(u64()):
            k = i32_text()
            meta[k] = i32_text()
        pos = end
        index_len = u64()
        end = pos + index_len
        index = []
        for _ in range(u64()):
            n = buf[pos]
            pos += 1
            name = text(n)
            index.append((name, u64(), u64()))
        pos = end
        data_len = u64()
        data_start = pos
    except (struct.error, IndexError) as e:
        raise BadSng(f"{Path(path).name} is cut short or damaged") from e
    # offsets are from the start of the file (the reference packer); fall back to the data section
    absolute = all(data_start <= off and off + size <= len(buf) for _, size, off in index)
    files = {}
    for name, size, off in index:
        start = off if absolute else data_start + off
        if start + size > len(buf) or (not absolute and off + size > data_len):
            raise BadSng(f"{Path(path).name} is cut short (file {name})")
        raw = np.frombuffer(buf, dtype=np.uint8, count=size, offset=start)
        i = np.arange(size, dtype=np.uint64)
        key = mask[(i % 16).astype(np.intp)] ^ (i & 0xFF).astype(np.uint8)
        files[name] = (raw ^ key).tobytes()
    return meta, files


def _ini_text(meta: dict) -> str:
    lines = ["[song]"] + [f"{k} = {v}" for k, v in meta.items()]
    return "\n".join(lines) + "\n"


def unpack(path: Path, dest: Path | None = None) -> Path:
    """Write the package out as a folder (default: next to it, same name without .sng) and
    return the folder. The .sng itself is left for the caller to remove."""
    path = Path(path)
    meta, files = read(path)
    if not any(n.lower() in ("notes.mid", "notes.chart") for n in files):
        raise BadSng(f"{path.name} has no chart in it")
    dest = Path(dest) if dest else path.with_suffix("")
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    for name, blob in files.items():
        rel = Path(*[p for p in re.split(r"[\\/]", name) if p not in ("", ".", "..")])
        out = (dest / rel).resolve()
        if root not in out.parents:
            continue  # never write outside the song folder
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(blob)
    if "song.ini" not in {n.lower() for n in files}:
        (dest / "song.ini").write_text(_ini_text(meta), encoding="utf-8")
    return dest


def free_folder(parent: Path, name: str) -> Path:
    """parent/name, or parent/name (2), (3)... if that is taken: nothing already there is replaced."""
    dest, n = Path(parent) / name, 2
    while dest.exists():
        dest, n = Path(parent) / f"{name} ({n})", n + 1
    return dest
