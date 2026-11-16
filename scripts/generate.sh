#!/bin/bash
# Generation side (operator Mac): every kit of a pulled run, in plan order, under the public
# network-restricted Seatbelt sandbox (isolate_net.py). Both private repositories (this one and v8b's), the
# public checkouts, agent config, the run's other kits/results and ops are denied to the sandbox.
# Single-session kits run the public worker (kit worker.py); staged kits (drafted, drafted-review) run the
# private stage wrapper (kit stages.py), which drives the same public worker once per stage.
# Usage: generate.sh <run-dir> [--fake-kiro <solutions-dir>]   (fake: hand-off control, no model)
set -u
D=$(cd "$1" && pwd -P); shift
PRIVATE=$(cd "$(dirname "$0")/.." && pwd -P)
OTHER_PRIVATE=${OTHER_PRIVATE:-$HOME/poc-v8b-private}
PUBLIC=${PUBLIC_CHECKOUT:-$HOME/.paseo/worktrees/3q2d9qt9/v5-scattered-convention}
ISOLATE=$PUBLIC/benchmark/phase3/isolate_net.py
H=$HOME
# Kiro auth refresh hosts first, the model runtime host last (v8 allowlist, network isolation control 23/23).
# Re-run discovery + isolation_net_control.py when the account region moves (v6 appendix F).
export KIRO_ALLOW_HOSTS=${KIRO_ALLOW_HOSTS:-cognito-identity.us-east-1.amazonaws.com,oidc.us-east-1.amazonaws.com,management.us-east-1.kiro.dev,runtime.us-east-1.kiro.dev}
FAKE=()
if [ "${1:-}" = --fake-kiro ]; then
  S=$(cd "$2" && pwd -P); mkdir -p "$D/tools"; cp "$PRIVATE/harness/fake_kiro.py" "$D/tools/fake_kiro.py"; chmod 755 "$D/tools/fake_kiro.py"
  export FAKE_KIRO_SOLUTIONS=$S; FAKE=(--synthetic-test-driver "$D/tools/fake_kiro.py")
fi
case "$D/" in "$PRIVATE"/*|"$OTHER_PRIVATE"/*) echo "run directory must be outside both private repositories" >&2; exit 2 ;; esac
mkdir -p "$D/ops" "$D/results"; STATUS=$D/ops/status.log
ORDER=$(cat "$D/order.txt")
for t in $ORDER; do
  if [ -e "$D/results/$t" ]; then echo "$(date -u +%FT%TZ) $t skip-existing" >> "$STATUS"; continue; fi
  DENY=(--deny "$PRIVATE" --deny "$OTHER_PRIVATE" --deny "$H/.paseo" --deny "$H/cil/ainative/enterprise_agent" --deny "$H/.kiro" --deny "$H/.codex" --deny "$D/ops")
  for o in $ORDER; do
    [ "$o" = "$t" ] && continue
    DENY+=(--deny "$D/kits/$o"); [ -e "$D/results/$o" ] && DENY+=(--deny "$D/results/$o")
  done
  CMD=(); ALARM=900
  if [ -f "$D/kits/$t/stages.py" ]; then
    # one 600 s budget per session (2 or 3 sessions) plus preflight and catalog checks
    ALARM=2400; CMD=(-- python3 -I "$D/kits/$t/stages.py" --output "$D/results/$t" ${FAKE[@]+"${FAKE[@]}"})
  elif [ ${#FAKE[@]} -gt 0 ]; then
    CMD=(-- python3 -I "$D/kits/$t/worker.py" --output "$D/results/$t" "${FAKE[@]}")
  fi
  echo "$(date -u +%FT%TZ) $t generate" >> "$STATUS"
  env -u ADMIN_DATABASE_URL -u DATABASE_URL -u POC_DB_RESET \
    perl -e "alarm $ALARM; exec @ARGV" python3 "$ISOLATE" --kit "$D/kits/$t" --output "$D/results/$t" "${DENY[@]}" \
      --allow-write "$H/Library/Caches" --allow-write "$H/go/pkg/mod" \
      --allow-write "$H/Library/Application Support/kiro-cli" --allow-write "$H/.aws/sso/cache" ${CMD[@]+"${CMD[@]}"} \
      > "$D/ops/$t.txt" 2>&1
  rc=$?; echo "$(date -u +%FT%TZ) $t worker_rc=$rc" >> "$STATUS"
  # A blocked preflight (quota/region) means the trial never started: stop and resume later (prereg rule R1).
  if [ ! -f "$D/results/$t/outcome.json" ]; then echo "$(date -u +%FT%TZ) $t PREFLIGHT-BLOCKED lane-stopped" >> "$STATUS"; exit 3; fi
done
echo "$(date -u +%FT%TZ) DONE" >> "$STATUS"
