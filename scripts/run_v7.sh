#!/bin/bash
# v7 confirmatory driver (operator Mac). Lanes = task x model; at most 4 run at once (prereg R5).
# Each lane: pull kits -> generate (network-restricted sandbox) -> push -> evaluate on the host.
# A credit guard stops every lane once the measured total reaches the budget (prereg R5).
# Usage: run_v7.sh <runs-root> <budget-credits> <run> [<run> ...]   (runs already prepared on the host)
set -u
ROOT=$1; BUDGET=$2; shift 2; RUNS=("$@")
HERE=$(cd "$(dirname "$0")" && pwd -P)
mkdir -p "$ROOT"; LOG=$ROOT/driver.log; STOP=$ROOT/STOP
credits() { python3 - "$ROOT" <<'EOF'
import glob, json, sys
t = 0.0
for f in glob.glob(sys.argv[1] + '/*/results/*/outcome.json'):
    u = (json.load(open(f)).get('generation') or {}).get('usage') or {}
    t += u.get('credits') or 0
print(round(t, 3))
EOF
}
lane() {
  run=$1; db=$2; D=$ROOT/$run
  [ -d "$D/kits" ] || "$HERE/pull_kits.sh" "$run" "$ROOT" >> "$LOG" 2>&1 || { echo "$(date -u +%FT%TZ) $run pull-failed" >> "$LOG"; return; }
  [ -e "$STOP" ] && { echo "$(date -u +%FT%TZ) $run skipped (budget stop)" >> "$LOG"; return; }
  "$HERE/generate.sh" "$D" >> "$LOG" 2>&1; rc=$?
  echo "$(date -u +%FT%TZ) $run generate rc=$rc credits_total=$(credits)" >> "$LOG"
  "$HERE/push_results.sh" "$run" "$ROOT" >> "$LOG" 2>&1
  "$HERE/host_db.sh" up "$db" >> "$LOG" 2>&1 && "$HERE/host_evaluate.sh" "$run" "$db" >> "$ROOT/eval-$run.log" 2>&1
  "$HERE/host_db.sh" down "$db" >> "$LOG" 2>&1
  echo "$(date -u +%FT%TZ) $run evaluated" >> "$LOG"
}
guard() {  # stop generation once the budget is reached; trials already started finish
  while pgrep -f "run_v7.sh $ROOT" >/dev/null; do
    c=$(credits); if python3 -c "import sys;sys.exit(0 if float('$c')>=float('$BUDGET') else 1)"; then
      touch "$STOP"; echo "$(date -u +%FT%TZ) BUDGET reached ($c >= $BUDGET): stopping lanes" >> "$LOG"
      pkill -f "generate.sh $ROOT/" ; break; fi
    sleep 60; done
}
guard & GP=$!
i=0
for run in "${RUNS[@]}"; do
  while [ "$(jobs -rp | grep -vc "^$GP$")" -ge 4 ]; do sleep 20; done
  db=$(( i % 4 + 1 )); i=$((i+1))
  # a lane's database slot must not be shared by two running lanes: wait for that slot
  while [ -e "$ROOT/slot-$db" ]; do sleep 20; done
  ( touch "$ROOT/slot-$db"; lane "$run" "$db"; rm -f "$ROOT/slot-$db" ) &
  sleep 5
done
wait $(jobs -rp | grep -v "^$GP$") 2>/dev/null
kill $GP 2>/dev/null
echo "$(date -u +%FT%TZ) ALL DONE credits_total=$(credits)" >> "$LOG"
