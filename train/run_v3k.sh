#!/bin/bash
# Run 10 (~4 h): part choice. Where a song layers a riff under chords, human charters chart one
# part per section; Guitar 7 hears both and mostly follows the riff. This fine-tunes Guitar 7
# (same recipe, lower learning rate) with two extra outputs per bar: does the real chart play the
# chord part or the riff here (train.data.bar_parts). The note outputs share the network, so they
# may follow the chosen part better; the part outputs are also there for decoding to use later.
# Tuned, then compared with the app's Guitar 7 and checked for mixed bars (train.layers).
#   bash train/run_v3k.sh      (progress: data/v3/run.log)
# Pause: bash train/pause.sh   Resume: bash train/resume.sh k
cd "$(dirname "$0")/.."
source train/run_lib.sh
keep_awake "v3 part 10 training done"
log "v3 part 10 training start"
step train_guitar_part "$PY -m train.train guitar_hr --data v3 --dm-src sw --steps 12000 --hidden 256 --dm-frac 0.6 --rel --size --part --layers 4 --wide 2 --win 3072 --batch 6 --pitch-aug 2 --lr 3e-4 --init models/guitar.pt --out models/v3/guitar_part.pt" \
     data/v3/train_guitar_part.log
step calibrate_guitar_part "$PY -m train.calibrate guitar models/v3/guitar_part.pt" data/v3/calibrate_guitar_part.log
step compare_guitar_part "$PY -m train.compare guitar models/guitar.pt models/v3/guitar_part.pt" data/v3/compare_guitar_part.log
grep -v Warning data/v3/compare_guitar_part.log >> $LOG
step layers_guitar_app "$PY -m train.layers guitar val,test" data/v3/layers_guitar_app.log
step layers_guitar_part "$PY -m train.layers guitar val,test models/v3/guitar_part.pt" data/v3/layers_guitar_part.log
log "v3 part 10 training done"
