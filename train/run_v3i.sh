#!/bin/bash
# Run 8, second half (~11 h): the big guitar model of run 7 again, on BS-RoFormer SW's guitar
# stem (run_v3h.sh made the stems) in place of Demucs' "other" stem, which lumps guitar together
# with keyboards. The recipe is run 7's to the letter, so the stem is the only thing that changes.
# Tuned, compared with run 7's model on the test songs both have stems for, and diagnosed.
#   bash train/run_v3i.sh      (progress: data/v3/run.log)
# Pause: bash train/pause.sh   Resume: bash train/resume.sh i
cd "$(dirname "$0")/.."
source train/run_lib.sh
keep_awake "v3 part 8 training done"
log "v3 part 8 training start"
step features_sw "$PY -m train.feats_v3 guitar_hr" data/v3/features_sw.log
step train_guitar_sw "$PY -m train.train guitar_hr --data v3 --dm-src sw --steps 30000 --hidden 256 --dm-frac 0.6 --rel --size --layers 4 --wide 2 --win 3072 --batch 6 --pitch-aug 2 --lr 1e-3 --out models/v3/guitar_sw.pt" \
     data/v3/train_guitar_sw.log
step calibrate_guitar_sw "$PY -m train.calibrate guitar models/v3/guitar_sw.pt" data/v3/calibrate_guitar_sw.log
step compare_guitar_sw "$PY -m train.compare guitar models/v3/guitar_big.pt models/v3/guitar_sw.pt" data/v3/compare_guitar_sw.log
grep -v Warning data/v3/compare_guitar_sw.log >> $LOG
mkdir -p models/v3/sw_diag && cp models/v3/guitar_sw.pt models/v3/sw_diag/guitar.pt
step diagnose_guitar_sw "$PY -m train.diagnose models/v3/sw_diag" data/v3/diagnose_guitar_sw.log
grep -v Warning data/v3/diagnose_guitar_sw.log >> $LOG
log "v3 part 8 training done"
