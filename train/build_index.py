"""python -m train.build_index <library_root> [out.json] [--v3] [--keep-ids old.json]

--v3 also keeps five-lane drum charts (converted to four-lane) and writes data/v3/index.json
by default. --keep-ids carries over each song's id and train/val/test split from an older index
when its folder was only renamed (same folder in another case, or the same notes), so cached
stems and features still match and no song moves between training and test. A song whose chart
changed under the same folder gets a fresh id: its audio may have changed too."""
import json
import sys
import zlib
from collections import Counter
from pathlib import Path

from . import data
from .charts import index_library

PARTS = ("guitar", "bass", "drums")


def keep_ids(songs, old_path):
    old = json.loads(Path(old_path).read_text(encoding="utf-8"))
    by_folder = {o["folder"].casefold(): o for o in old}
    by_notes = {}
    for o in old:
        by_notes.setdefault(json.dumps([o["notes"].get(k) for k in PARTS]), o)
    tally = {"same": 0, "renamed": 0, "chart changed": 0, "new": 0}
    for s in songs:
        notes = json.dumps([s["notes"].get(k) for k in PARTS])
        o = by_folder.get(s["folder"].casefold())
        if o is not None and json.dumps([o["notes"].get(k) for k in PARTS]) == notes:
            how = "same" if o["folder"] == s["folder"] else "renamed"
        elif o is not None:
            how = "chart changed"
        else:
            o = by_notes.get(notes)
            how = "renamed" if o is not None else "new"
        tally[how] += 1
        if how == "chart changed":
            s["id"] = f"{zlib.crc32((s['folder'] + '|chart changed').encode('utf-8')):08x}"
        elif how != "new":
            s["id"] = data.song_id(o)
        if o is not None and o["name"] != s["name"]:
            s["split_key"] = o.get("split_key", o["name"])
    print("against the older index:", tally)

if __name__ == "__main__":
    argv = sys.argv[1:]
    old_ids = None
    if "--keep-ids" in argv:
        k = argv.index("--keep-ids")
        old_ids = argv[k + 1]
        del argv[k:k + 2]
    args = [a for a in argv if not a.startswith("--")]
    v3 = "--v3" in argv
    root = Path(args[0])
    out = Path(args[1] if len(args) > 1 else "data/v3/index.json" if v3 else "data/index.json")
    songs = index_library(root, convert_five=v3)
    if old_ids:
        keep_ids(songs, old_ids)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(songs), encoding="utf-8")
    c = Counter(k for s in songs for k in s["notes"])
    n = {k: sum(len(s["notes"][k]) for s in songs if k in s["notes"]) for k in c}
    print(f"{len(songs)} songs indexed -> {out}")
    print("songs per instrument:", dict(c))
    print("notes per instrument:", n)
