#!/bin/bash
# v8b confirmatory driver (operator Mac). Lanes = task x model; at most 4 run at once (prereg R5).
# Each lane: pull kits -> generate (network-restricted sandbox) -> push -> evaluate on the host.
# A lane takes any free database slot (1..4) when it starts, so 4 lanes always run while 4 are waiting
# (v7 assigned slots in a fixed order and sometimes ran only 3). A credit guard stops every lane once the
# measured total reaches the budget (prereg R5).
# Usage: run_v8b.sh <runs-root> <budget-credits> <run> [<run> ...]   (runs already prepared on the host)
set -u
ROOT=$1; BUDGET=$2; shift 2; RUNS=("$@")
HERE=$(cd "$(dirname "$0")" && pwd -P)
mkdir -p "$ROOT"; ROOT=$(cd "$ROOT" && pwd -P); LOG=$ROOT/driver.log; STOP=$ROOT/STOP
credits() { python3 - "$ROOT" <<'PY'
import glob, json, sys
t = 0.0
for f in glob.glob(sys.argv[1] + '/*/results/*/outcome.json'):
    u = (json.load(open(f)).get('generation') or {}).get('usage') or {}
    t += u.get('credits') or 0
print(round(t, 3))
PY
}
take_slot() {  # atomic: mkdir succeeds for exactly one lane
  while :; do
    for s in 1 2 3 4; do mkdir "$ROOT/slot-$s" 2>/dev/null && { echo $s; return; }; done
    sleep 10
  done
}
lane() {
  run=$1; db=$2; D=$ROOT/$run
  echo "$(date -u +%FT%TZ) $run start slot=$db" >> "$LOG"
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
  while [ ! -e "$ROOT/ALL-DONE" ]; do
    c=$(credits); if python3 -c "import sys;sys.exit(0 if float('$c')>=float('$BUDGET') else 1)"; then
      touch "$STOP"; echo "$(date -u +%FT%TZ) BUDGET reached ($c >= $BUDGET): stopping lanes" >> "$LOG"
      pkill -f "generate.sh $ROOT/" ; break; fi
    sleep 60; done
}
rm -f "$ROOT/ALL-DONE"; rmdir "$ROOT"/slot-? 2>/dev/null
guard & GP=$!
for run in "${RUNS[@]}"; do
  db=$(take_slot)
  ( lane "$run" "$db"; rmdir "$ROOT/slot-$db" ) &
  sleep 5
done
wait $(jobs -rp | grep -v "^$GP$") 2>/dev/null
touch "$ROOT/ALL-DONE"; kill $GP 2>/dev/null
echo "$(date -u +%FT%TZ) ALL DONE credits_total=$(credits)" >> "$LOG"
