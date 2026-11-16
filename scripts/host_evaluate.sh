#!/bin/bash
# On the evaluation host: restore + evaluate every generated trial of a run in plan order, then summarize.
# Usage: host_evaluate.sh <run> <lane> [host]    (the lane's database must be up: host_db.sh up <lane>)
set -eu
RUN=$1; LANE=$2; HOST=${3:-root@10.30.11.112}
case "$RUN" in v8b-*) ;; *) echo "v8b run names start with v8b-" >&2; exit 2 ;; esac
P=${PRIVATE_DIR:-/opt/poc-oracle/private-v8b}  # an older harness checkout for runs prepared at an earlier commit
ssh -o BatchMode=yes "$HOST" "set -u; cd $P; . scripts/host_env.sh $LANE; R=/opt/poc-oracle/runs/$RUN
  test \"\$(git rev-parse HEAD)\" = \"\$(cat \$R/harness_sha.txt)\" || { echo 'harness moved since prepare' >&2; exit 1; }
  mkdir -p \$R/eval \$R/evaluations \$R/ops
  for t in \$(cat \$R/order.txt); do
    [ -f \$R/results/\$t/receipt.json ] || { echo \"\$t not generated: \$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))[\"outcome\"])' \$R/results/\$t/outcome.json 2>/dev/null || echo missing)\"; continue; }
    [ -e \$R/evaluations/\$t ] && continue
    python3 harness/ctl.py restore --plan \$R/plan.json --trial \$t --bundle \$R/results/\$t/candidate.bundle --receipt \$R/results/\$t/receipt.json \
      --manifest \$R/kits/\$t/manifest.json --candidate \$R/eval/\$t >> \$R/ops/\$t.txt 2>&1 || { echo \"\$t restore failed\"; continue; }
    perl -e 'alarm 900; exec @ARGV' python3 harness/ctl.py evaluate --plan \$R/plan.json --trial \$t --candidate \$R/eval/\$t --manifest \$R/kits/\$t/manifest.json \
      --receipt \$R/results/\$t/receipt.json --fixtures \$R/private/fixtures.json --output \$R/evaluations/\$t 2>> \$R/ops/\$t.txt | tail -1
  done
  python3 harness/ctl.py summarize --plan \$R/plan.json --inputs \$R/results --inputs \$R/evaluations --output \$R/summary.json > /dev/null && echo \"summary: \$R/summary.json\""
