#!/bin/bash
# Run 7 of the v3 plan (~10 h): a bigger guitar model. Every earlier run changed the inputs,
# outputs or loss of the same small network (1.5M weights, 12 s of audio at a time). This one has
# 11.4M weights (twice the filters, four self-attention layers after the recurrent one), learns
# on 36 s windows and transposes them by up to two semitones so 700 songs go further. Same
# outputs as the app's guitar. Tuned, compared with the app's guitar, and diagnosed.
#   bash train/run_v3g.sh      (progress: data/v3/run.log)
# Pause: bash train/pause.sh   Resume: bash train/resume.sh g
cd "$(dirname "$0")/.."
source train/run_lib.sh
keep_awake "v3 part 7 done"
log "v3 part 7 start"
step train_guitar_big "$PY -m train.train guitar_hr --data v3 --dm-src ft --steps 30000 --hidden 256 --dm-frac 0.6 --rel --size --layers 4 --wide 2 --win 3072 --batch 6 --pitch-aug 2 --lr 1e-3 --out models/v3/guitar_big.pt" \
     data/v3/train_guitar_big.log
step calibrate_guitar_big "$PY -m train.calibrate guitar models/v3/guitar_big.pt" data/v3/calibrate_guitar_big.log
step compare_guitar_big "$PY -m train.compare guitar models/guitar.pt models/v3/guitar_big.pt" data/v3/compare_guitar_big.log
grep -v Warning data/v3/compare_guitar_big.log >> $LOG
mkdir -p models/v3/big_diag && cp models/v3/guitar_big.pt models/v3/big_diag/guitar.pt
step diagnose_guitar_big "$PY -m train.diagnose models/v3/big_diag" data/v3/diagnose_guitar_big.log
grep -v Warning data/v3/diagnose_guitar_big.log >> $LOG
log "v3 part 7 done"
