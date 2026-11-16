#!/bin/bash
# Generation side (operator Mac): every kit of a pulled run, in plan order, under the public
# network-restricted Seatbelt sandbox (isolate_net.py). The private repository, the public checkouts,
# agent config, the run's other kits/results and ops are denied to the sandbox.
# Usage: generate.sh <run-dir> [--fake-kiro <solutions-dir>]   (fake: hand-off control, no model)
set -u
D=$(cd "$1" && pwd -P); shift
PRIVATE=$(cd "$(dirname "$0")/.." && pwd -P)
PUBLIC=${PUBLIC_CHECKOUT:-$HOME/.paseo/worktrees/3q2d9qt9/v5-scattered-convention}
ISOLATE=$PUBLIC/benchmark/phase3/isolate_net.py
H=$HOME
if [ "${1:-}" = --fake-kiro ]; then
  S=$(cd "$2" && pwd -P); mkdir -p "$D/tools"; cp "$PRIVATE/harness/fake_kiro.py" "$D/tools/fake_kiro.py"; chmod 755 "$D/tools/fake_kiro.py"
  export FAKE_KIRO_SOLUTIONS=$S
fi
case "$D/" in "$PRIVATE"/*) echo "run directory must be outside $PRIVATE" >&2; exit 2 ;; esac
mkdir -p "$D/ops" "$D/results"; STATUS=$D/ops/status.log
ORDER=$(cat "$D/order.txt")
for t in $ORDER; do
  if [ -e "$D/results/$t" ]; then echo "$(date -u +%FT%TZ) $t skip-existing" >> "$STATUS"; continue; fi
  DENY=(--deny "$PRIVATE" --deny "$H/.paseo" --deny "$H/cil/ainative/enterprise_agent" --deny "$H/.kiro" --deny "$H/.codex" --deny "$D/ops")
  for o in $ORDER; do
    [ "$o" = "$t" ] && continue
    DENY+=(--deny "$D/kits/$o"); [ -e "$D/results/$o" ] && DENY+=(--deny "$D/results/$o")
  done
  CMD=()
  [ -n "${FAKE_KIRO_SOLUTIONS:-}" ] && CMD=(-- python3 -I "$D/kits/$t/worker.py" --output "$D/results/$t" --synthetic-test-driver "$D/tools/fake_kiro.py")
  echo "$(date -u +%FT%TZ) $t generate" >> "$STATUS"
  env -u ADMIN_DATABASE_URL -u DATABASE_URL -u POC_DB_RESET \
    perl -e 'alarm 900; exec @ARGV' python3 "$ISOLATE" --kit "$D/kits/$t" --output "$D/results/$t" "${DENY[@]}" \
      --allow-write "$H/Library/Caches" --allow-write "$H/go/pkg/mod" \
      --allow-write "$H/Library/Application Support/kiro-cli" --allow-write "$H/.aws/sso/cache" ${CMD[@]+"${CMD[@]}"} \
      > "$D/ops/$t.txt" 2>&1
  rc=$?; echo "$(date -u +%FT%TZ) $t worker_rc=$rc" >> "$STATUS"
  # A blocked preflight (quota/region) means the trial never started: stop and resume later (prereg rule R2).
  if [ ! -f "$D/results/$t/outcome.json" ]; then echo "$(date -u +%FT%TZ) $t PREFLIGHT-BLOCKED lane-stopped" >> "$STATUS"; exit 3; fi
done
echo "$(date -u +%FT%TZ) DONE" >> "$STATUS"
