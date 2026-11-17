#!/bin/bash
# Run 9 (~5 h): bass and drums on BS-RoFormer SW's stems (run_v3h.sh made them), which gave
# guitar a small win in run 8. Each recipe is the earlier run's to the letter, so the stem is
# the only thing that changes: bass is run 2's bass_rel (the app's bass model), drums is run 2's
# drums_ct. Bass is tuned and compared with the app's; drums are compared with the app's (an
# older data set, so few test songs in common) and with drums_ct (same recipe, Demucs stems).
#   bash train/run_v3j.sh      (progress: data/v3/run.log)
# Pause: bash train/pause.sh   Resume: bash train/resume.sh j
cd "$(dirname "$0")/.."
source train/run_lib.sh
keep_awake "v3 part 9 training done"
log "v3 part 9 training start"
step features_sw_bd "$PY -m train.feats_v3 bass_hr,drums" data/v3/features_sw_bd.log
step train_bass_sw "$PY -m train.train bass_hr --data v3 --dm-src sw --steps 25000 --hidden 192 --dm-frac 0.6 --rel --out models/v3/bass_sw.pt" \
     data/v3/train_bass_sw.log
step train_drums_sw "$PY -m train.train drums --data v3 --dm-src sw --steps 20000 --hidden 192 --dm-frac 0.6 --out models/v3/drums_sw.pt" \
     data/v3/train_drums_sw.log
step calibrate_bass_sw "$PY -m train.calibrate bass models/v3/bass_sw.pt" data/v3/calibrate_bass_sw.log
step compare_bass_sw "$PY -m train.compare bass models/v3/bass_rel.pt models/v3/bass_sw.pt" data/v3/compare_bass_sw.log
grep -v Warning data/v3/compare_bass_sw.log >> $LOG
step compare_drums_sw "$PY -m train.compare drums models/drums.pt models/v3/drums_sw.pt" data/v3/compare_drums_sw.log
grep -v Warning data/v3/compare_drums_sw.log >> $LOG
step compare_drums_sw_ct "$PY -m train.compare drums models/v3/drums_ct.pt models/v3/drums_sw.pt" data/v3/compare_drums_sw_ct.log
grep -v Warning data/v3/compare_drums_sw_ct.log >> $LOG
log "v3 part 9 training done"
