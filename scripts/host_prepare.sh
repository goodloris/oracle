#!/bin/bash
# On the evaluation host: fixtures (seed stays there) + plan + one kit per trial, in plan order.
# Usage: host_prepare.sh <run> <task> <model> <repetitions> [--synthetic] [host]
set -eu
RUN=$1; TASK=$2; MODEL=$3; REPS=$4; SYN=${5:-}; HOST=${6:-root@10.30.11.112}
[ "$SYN" = --synthetic ] || [ -z "$SYN" ] || { echo "5th argument must be --synthetic or empty" >&2; exit 2; }
case "$RUN" in *[!A-Za-z0-9._-]*|'') echo "run name: [A-Za-z0-9._-]+" >&2; exit 2 ;; esac
ssh -o BatchMode=yes "$HOST" "set -eu; cd /opt/poc-oracle/private; . scripts/host_env.sh 1; test -z \"\$(git status --porcelain)\"
  R=/opt/poc-oracle/runs/$RUN; test ! -e \$R; mkdir -p \$R/private \$R/kits; chmod 700 \$R/private
  python3 harness/ctl.py fixtures --output \$R/private/fixtures.json
  python3 harness/ctl.py plan --task '$TASK' --model '$MODEL' --repetitions '$REPS' --fixtures \$R/private/fixtures.json --output \$R/plan.json $SYN
  python3 -c 'import json,sys; print(\" \".join(t[\"id\"] for t in json.load(open(sys.argv[1]))[\"trials\"]))' \$R/plan.json > \$R/order.txt
  for t in \$(cat \$R/order.txt); do python3 harness/ctl.py prepare --plan \$R/plan.json --trial \$t --output \$R/kits/\$t; done
  git rev-parse HEAD > \$R/harness_sha.txt; echo \"prepared \$(wc -w < \$R/order.txt) kits in \$R at \$(cat \$R/harness_sha.txt)\""
