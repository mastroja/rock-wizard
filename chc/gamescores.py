"""YARG's own scores, read so the Scores page fills itself.

YARG keeps every play in scores.db (SQLite): the song's name and artist, and the profile that
played it. Profiles live in profiles.json next to it, each with its own id (every YARG install,
release, nightly or the Deck's, has its own profiles, even ones with the same name). A profile is
linked to a person here (server.import_game_scores); its scores are logged as theirs.

Only instruments the Scores page has are read (guitar, bass, drums, vocals), and plays with bots,
replays or slowed down songs are left out. A score is logged only when it beats that person's
best, so reading the same file again changes nothing."""
import datetime
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

# YARG.Core's Instrument: 0 five-fret guitar, 1 bass, 20-22 four-lane / pro / five-lane drums, 50 vocals
YARG_INST = {0: "guitar", 1: "bass", 20: "drums", 21: "drums", 22: "drums", 50: "vocals"}
YARG_DIFF = {1: "easy", 2: "medium", 3: "hard", 4: "expert", 5: "expert"}  # 0 beginner: not on the page

YARG_HOME = Path.home() / "AppData" / "LocalLow" / "YARC" / "YARG"
DEFAULTS = [YARG_HOME / "release" / "scores" / "scores.db", YARG_HOME / "nightly" / "scores" / "scores.db"]


def sources(extra_dir: str = "") -> list[Path]:
    """YARG's score files on this PC, plus any under extra_dir (a folder synced from the Steam Deck)."""
    found = [p for p in DEFAULTS if p.is_file()]
    if extra_dir and Path(extra_dir).is_dir():
        found += sorted(Path(extra_dir).rglob("scores.db"))
    return found


def where(path: Path) -> str:
    """A short name for the YARG install a score file belongs to: release or nightly on this PC,
    else the Steam Deck's (the only other place sources() reads from)."""
    install = Path(path).parent.parent
    return install.name if install.parent == YARG_HOME else "Steam Deck"


def _when(ticks) -> float:
    """YARG's play time (.NET ticks of the local clock) as a Unix time."""
    try:
        return (datetime.datetime(1, 1, 1) + datetime.timedelta(microseconds=int(ticks) // 10)).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return 0.0


def _query(path: Path, sql: str) -> list:
    """Rows from a copy of the database: YARG may have it open."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "scores.db"
        shutil.copy(path, copy)
        con = sqlite3.connect(copy)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()


def read_yarg(path: Path) -> list[dict]:
    """[{"profile", "player", "t" (when it was played), "artist", "title", "inst", "diff", "pct", "stars", "score"}]."""
    rows = _query(path,
                  "select s.PlayerId, p.Name, g.Date, g.SongArtist, g.SongName, s.Instrument, s.Difficulty, s.Percent, s.Stars, s.Score "
                  "from PlayerScores s join GameRecords g on g.Id = s.GameRecordId "
                  "left join Players p on p.Id = s.PlayerId "
                  "where coalesce(s.IsReplay, 0) = 0 and coalesce(g.PlayedWithReplay, 0) = 0 "
                  "and coalesce(g.HasBots, 0) = 0 and coalesce(g.SongSpeed, 1) >= 1")
    out = []
    for pid, player, date, artist, title, inst, diff, pct, stars, score in rows:
        if inst in YARG_INST and diff in YARG_DIFF and pct is not None and 0 <= pct <= 1:
            out.append({"profile": pid or "", "player": player or "", "t": _when(date), "artist": artist or "", "title": title or "",
                        "inst": YARG_INST[inst], "diff": YARG_DIFF[diff], "pct": round(100 * pct, 1),
                        "stars": stars, "score": score})
    return out


def profiles(paths) -> list[dict]:
    """Every YARG profile next to these score files, played or not: [{"id", "name", "where", "plays"}].
    Bots are left out."""
    out = {}
    for path in paths:
        here = where(path)
        try:
            plays = dict(_query(path, "select PlayerId, count(*) from PlayerScores group by PlayerId"))
            names = dict(_query(path, "select Id, Name from Players"))
        except (OSError, sqlite3.Error):
            plays, names = {}, {}
        listed = Path(path).parent.parent / "profiles" / "profiles.json"
        try:
            for p in json.loads(listed.read_text(encoding="utf-8-sig")):
                if not p.get("IsBot") and p.get("Id"):
                    names.setdefault(p["Id"], p.get("Name") or "")
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        for pid, name in names.items():
            out[pid] = {"id": pid, "name": name or "", "where": here, "plays": plays.get(pid, 0)}
    return sorted(out.values(), key=lambda p: (-p["plays"], p["name"].lower(), p["where"]))
