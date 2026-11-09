"""Give charts this app already made star power, solos and section names. Notes don't change, and
human charts are never touched.

python -m train.add_phrases [--apply] [folder ...]

Without --apply it only reports what it would add. Each changed notes.mid is backed up first."""
import argparse
from pathlib import Path

from chc import audio, library, phrases

FOLDERS = [r"G:\Clone Hero\songs\Quickplay", r"G:\Clone Hero\songs\my songs!"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="*", default=FOLDERS)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    changed = checked = 0
    for root in a.folders:
        for d in sorted(Path(root).iterdir()):
            mid = d / "notes.mid"
            if not d.is_dir() or not mid.exists() or not library.is_ours(d):
                continue
            checked += 1
            plan = phrases.add_to_mid(mid, dry=True)
            if not plan:
                continue
            if a.apply:
                audio.ensure_writable([mid])
                library.backup(d, "before star power", files=["notes.mid"])
                phrases.add_to_mid(mid)
            changed += 1
            print(f"{plan}  {d.name}")
    print(f"{changed} of our {checked} charts {'got' if a.apply else 'would get'} star power, solos or sections")


if __name__ == "__main__":
    main()
