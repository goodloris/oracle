#!/bin/bash
# v8a confirmatory driver (operator Mac). Lanes = task x model; at most 4 run at once (prereg R5).
# Each lane: pull kits -> generate (network-restricted sandbox) -> push -> evaluate on the host.
# A lane takes any free database slot (1..4) when it starts and frees it when it ends (v7 assigned slots in
# a fixed order, so a lane could wait for a busy slot while another slot was free).
# A credit guard stops every lane once the measured total (all stages, review included) reaches the budget.
# Usage: run_v8a.sh <runs-root> <budget-credits> <run> [<run> ...]   (runs already prepared on the host)
set -u
ROOT=$1; BUDGET=$2; shift 2; RUNS=("$@")
HERE=$(cd "$(dirname "$0")" && pwd -P)
for r in "${RUNS[@]}"; do case "$r" in v8a-*) ;; *) echo "v8a run names start with v8a-: $r" >&2; exit 2 ;; esac; done
mkdir -p "$ROOT"; LOG=$ROOT/driver.log; STOP=$ROOT/STOP
ls -d "$ROOT"/slot-* >/dev/null 2>&1 && { echo "stale slot claims in $ROOT (a previous driver did not finish): remove them first" >&2; exit 2; }
credits() { python3 - "$ROOT" <<'PY'
import glob, json, sys
t = 0.0
for f in glob.glob(sys.argv[1] + '/*/results/*/outcome.json'):
    u = (json.load(open(f)).get('generation') or {}).get('usage') or {}
    t += u.get('credits') or 0
print(round(t, 3))
PY
}
# claim: atomically take the first free slot (mkdir is atomic); prints the slot number.
claim() {
  while :; do
    for s in 1 2 3 4; do mkdir "$ROOT/slot-$s" 2>/dev/null && { echo "$s"; return; }; done
    sleep 20
  done
}
lane() {
  run=$1; D=$ROOT/$run
  [ -d "$D/kits" ] || "$HERE/pull_kits.sh" "$run" "$ROOT" >> "$LOG" 2>&1 || { echo "$(date -u +%FT%TZ) $run pull-failed" >> "$LOG"; return; }
  [ -e "$STOP" ] && { echo "$(date -u +%FT%TZ) $run skipped (budget stop)" >> "$LOG"; return; }
  "$HERE/generate.sh" "$D" >> "$LOG" 2>&1; rc=$?
  echo "$(date -u +%FT%TZ) $run generate rc=$rc credits_total=$(credits)" >> "$LOG"
  "$HERE/push_results.sh" "$run" "$ROOT" >> "$LOG" 2>&1
  db=$(claim); echo "$(date -u +%FT%TZ) $run evaluate on slot $db" >> "$LOG"
  "$HERE/host_db.sh" up "$db" >> "$LOG" 2>&1 && "$HERE/host_evaluate.sh" "$run" "$db" >> "$ROOT/eval-$run.log" 2>&1
  "$HERE/host_db.sh" down "$db" >> "$LOG" 2>&1; rmdir "$ROOT/slot-$db"
  echo "$(date -u +%FT%TZ) $run evaluated" >> "$LOG"
}
guard() {  # stop generation once the budget is reached; trials already started finish
  while pgrep -f "run_v8a.sh $ROOT" >/dev/null; do
    c=$(credits); if python3 -c "import sys;sys.exit(0 if float('$c')>=float('$BUDGET') else 1)"; then
      touch "$STOP"; echo "$(date -u +%FT%TZ) BUDGET reached ($c >= $BUDGET): stopping lanes" >> "$LOG"
      pkill -f "generate.sh $ROOT/" ; break; fi
    sleep 60; done
}
guard & GP=$!
for run in "${RUNS[@]}"; do
  while [ "$(jobs -rp | grep -vc "^$GP$")" -ge 4 ]; do sleep 20; done
  lane "$run" &
  sleep 5
done
wait $(jobs -rp | grep -v "^$GP$") 2>/dev/null
kill $GP 2>/dev/null
echo "$(date -u +%FT%TZ) ALL DONE credits_total=$(credits)" >> "$LOG"
