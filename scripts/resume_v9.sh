#!/bin/bash
# v9 resume after preflight blocks (prereg v9 R1, appendix B/C). For each run: archive the blocked
# result dirs (never started), re-run generate.sh (skips existing results, plan order), copy only the
# new result dirs to the host, then host_evaluate (skips existing evaluations). Sequential, one lane at a time
# per database slot; at most 3 lanes in parallel to reduce probe latency.
# Usage: resume_v9.sh <runs-root> <run> [<run> ...]
set -u
ROOT=$1; shift; HERE=/Users/administrator/poc-v7-private/scripts; H=root@10.30.11.112
lane() {
  run=$1; slot=$2; D=$ROOT/$run; RH=/opt/poc-oracle/runs/$run; day=$(date +%Y%m%d)
  for r in "$D"/results/*/; do t=$(basename "$r"); [ -f "$r/outcome.json" ] && continue
    mkdir -p "$D/preflight-blocked-$day"; mv "$r" "$D/preflight-blocked-$day/"
    ssh -o BatchMode=yes $H "test -d $RH/results/$t && mkdir -p $RH/preflight-blocked-$day && mv $RH/results/$t $RH/preflight-blocked-$day/ || true"
    echo "$(date -u +%FT%TZ) $run archived blocked $t" >> "$ROOT/resume.log"; done
  before=$(ls "$D/results")
  "$HERE/generate.sh" "$D" >> "$ROOT/resume.log" 2>&1
  echo "$(date -u +%FT%TZ) $run generate rc=$?" >> "$ROOT/resume.log"
  for t in $(ls "$D/results"); do echo "$before" | grep -qx "$t" && continue
    [ -f "$D/results/$t/outcome.json" ] || continue
    (cd "$D/results" && tar cf - --exclude=kiro-home "$t") | ssh -o BatchMode=yes $H "cd $RH/results && tar xf -"; done
  scp -q "$D/ops/status.log" "$H:$RH/generation-status.log"
  "$HERE/host_db.sh" up "$slot" >> "$ROOT/resume.log" 2>&1 && "$HERE/host_evaluate.sh" "$run" "$slot" >> "$ROOT/eval-resume-$run.log" 2>&1
  "$HERE/host_db.sh" down "$slot" >> "$ROOT/resume.log" 2>&1
  echo "$(date -u +%FT%TZ) $run resumed+evaluated" >> "$ROOT/resume.log"
}
i=0
for run in "$@"; do
  while [ "$(jobs -rp | wc -l)" -ge 3 ]; do sleep 20; done
  i=$((i+1)); lane "$run" "$i" & sleep 3
done
wait
echo "$(date -u +%FT%TZ) RESUME DONE" >> "$ROOT/resume.log"
