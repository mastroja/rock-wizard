#!/bin/bash
# Run 11 training (~7 h): guitar and bass on the bigger set (run_v3l.sh separated and featurized
# the 190 songs that came into Quickplay since October). Each recipe is the app model's to the
# letter (Guitar 7 = run 8's guitar_sw, Bass 5 = run 9's bass_sw), trained from scratch, so the
# bigger set is what changes. Guitar also learns which notes are taps (--tap: 27k tapped notes in
# the set); bass has under a thousand, too few, so it doesn't. Each is tuned (calibrate picks the
# tap threshold too) and compared with the app's model on the test songs.
#   bash train/run_v3m.sh      (progress: data/v3/run.log)
# Pause: bash train/pause.sh   Resume: bash train/resume.sh m
cd "$(dirname "$0")/.."
until [ -f data/v3/done/features_more ]; do sleep 300; done  # run 11's data step
source train/run_lib.sh
keep_awake "v3 part 11 training done"
log "v3 part 11 training start"
step train_guitar_tap "$PY -m train.train guitar_hr --data v3 --dm-src sw --steps 30000 --hidden 256 --dm-frac 0.6 --rel --size --tap --layers 4 --wide 2 --win 3072 --batch 6 --pitch-aug 2 --lr 1e-3 --out models/v3/guitar_tap.pt" \
     data/v3/train_guitar_tap.log
step calibrate_guitar_tap "$PY -m train.calibrate guitar models/v3/guitar_tap.pt" data/v3/calibrate_guitar_tap.log
step compare_guitar_tap "$PY -m train.compare guitar models/guitar.pt models/v3/guitar_tap.pt" data/v3/compare_guitar_tap.log
grep -v Warning data/v3/compare_guitar_tap.log >> $LOG
step train_bass_more "$PY -m train.train bass_hr --data v3 --dm-src sw --steps 25000 --hidden 192 --dm-frac 0.6 --rel --out models/v3/bass_more.pt" \
     data/v3/train_bass_more.log
step calibrate_bass_more "$PY -m train.calibrate bass models/v3/bass_more.pt" data/v3/calibrate_bass_more.log
step compare_bass_more "$PY -m train.compare bass models/bass.pt models/v3/bass_more.pt" data/v3/compare_bass_more.log
grep -v Warning data/v3/compare_bass_more.log >> $LOG
log "v3 part 11 training done"
