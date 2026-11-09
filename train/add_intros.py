"""Give charts this app already made their intro bars. Human charts are never touched.

python -m train.add_intros [--apply] [folder]

Without --apply it only reports what it would change."""
import argparse
from pathlib import Path

from chc import library, pipeline

QUICKPLAY = r"G:\Clone Hero\songs\Quickplay"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=QUICKPLAY)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    changed = checked = 0
    for d in sorted(Path(a.folder).iterdir()):
        if not d.is_dir() or not library.is_ours(d) or not (d / "notes.mid").exists():
            continue
        checked += 1
        lead = pipeline.add_intro(d, dry=not a.apply)
        if lead:
            changed += 1
            print(f"{lead:5.1f}s  {d.name}")
    print(f"{changed} of our {checked} charts {'got' if a.apply else 'need'} intro bars")


if __name__ == "__main__":
    main()
