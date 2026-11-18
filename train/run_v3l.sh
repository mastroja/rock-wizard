#!/bin/bash
# Run 11, first half (~3 h): the bigger training set. data/v3/index_next.json (train.build_index
# --keep-ids) adds the human charts that came into Quickplay since October: notes.chart songs,
# taps, rhythm and keys parts. It waits for run 10 to finish, swaps the new index in (the old one
# is kept as index_2026-10-24.json), separates the new songs with BS-RoFormer SW and caches their
# features. Training on them comes after, as its own run.
#   bash train/run_v3l.sh      (progress: data/v3/run.log)
# Pause: bash train/pause.sh   Resume: bash train/resume.sh l
cd "$(dirname "$0")/.."
until [ -f data/v3/done/layers_guitar_part ]; do sleep 300; done  # run 10's last step
source train/run_lib.sh
keep_awake "v3 part 11 data done"
log "v3 part 11 data start"
if [ -f data/v3/index_next.json ]; then
  mv data/v3/index.json data/v3/index_2026-10-24.json && mv data/v3/index_next.json data/v3/index.json
  log "index swapped: $(.venv/Scripts/python -c 'import json; print(len(json.load(open("data/v3/index.json", encoding="utf-8"))))') songs"
fi
step separate_sw_more "tools/sepenv/Scripts/python -m train.separate_sw" data/v3/separate_sw_more.log
step features_more "$PY -m train.feats_v3 guitar_hr,bass_hr,drums" data/v3/features_more.log
log "v3 part 11 data done"
