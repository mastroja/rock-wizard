#!/bin/bash
# Run 8, first half (~8 h): separate every song again with BS-RoFormer SW, which has real guitar
# and piano stems (train/separate_sw.py says why and how its environment is set up). Stems only:
# features and the guitar retraining on them come after, as their own steps.
#   bash train/run_v3h.sh      (progress: data/v3/run.log and data/v3/separate_sw.log)
# Pause: bash train/pause.sh   Resume: bash train/resume.sh h
cd "$(dirname "$0")/.."
source train/run_lib.sh
keep_awake "v3 part 8 separation done"
log "v3 part 8 separation start"
step separate_sw ".tmp/sepenv/Scripts/python -m train.separate_sw" data/v3/separate_sw.log
log "v3 part 8 separation done"
