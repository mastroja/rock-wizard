#!/bin/bash
# Resume (or start) a v3 run: bash train/resume.sh a|b|...|i   (a = part 1 ... g = part 7, h = part 8 separation, i = part 8 training)
# Finished steps are skipped and a paused training run continues from its last snapshot.
# Runs in the foreground; launch it detached (e.g. Start-Process) to keep it going.
cd "$(dirname "$0")/.."
rm -f data/v3/PAUSE
exec bash train/run_v3${1:-a}.sh
