"""Keep the original of charts this app made before it started keeping them (chc.library.ORIGINAL).

python -m train.save_originals [--apply] [folder ...]

Only songs this app made, that have no kept original yet and no notes.chart beside the notes.mid
(Moonscraper saves .chart, so one there means the chart may already have been edited by hand and
its notes.mid can't be trusted to be what the app wrote). Nothing else in the folder changes.
Without --apply it only reports."""
import argparse
from pathlib import Path

from chc import library

FOLDERS = [r"G:\Clone Hero\songs\Quickplay", r"G:\Clone Hero\songs\my songs!"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="*", default=FOLDERS)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    done = have = skipped = 0
    for root in a.folders:
        for d in library.song_folders(Path(root)):
            if not library.is_ours(d) or not (d / "notes.mid").exists():
                continue
            if library.original_of(d):
                have += 1
            elif (d / "notes.chart").exists():
                skipped += 1
                print(f"skipped (has a notes.chart, may be hand-edited): {d.name}")
            elif not a.apply or library.save_original(d):
                done += 1
                print(f"{'kept' if a.apply else 'would keep'}: {d.name}")
    print(f"{done} original{'s' if done != 1 else ''} {'kept' if a.apply else 'to keep'}, {have} already had one, {skipped} skipped")


if __name__ == "__main__":
    main()
