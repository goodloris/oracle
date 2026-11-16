#!/bin/bash
# Run the real-PostgreSQL controls of one task on the evaluation host and copy the report back.
# Usage: host_controls.sh <lane> <task> <local-report.json> [host]
set -eu
LANE=$1; TASK=$2; OUT=$3; HOST=${4:-root@10.30.11.112}
P=${PRIVATE_DIR:-/opt/poc-oracle/private-v8b}
W=/opt/poc-oracle/runs/v8b-controls-$TASK-$(date -u +%Y%m%dT%H%M%SZ)
ssh -o BatchMode=yes "$HOST" "set -u; cd $P; . scripts/host_env.sh $LANE
  perl -e 'alarm 3000; exec @ARGV' python3 harness/controls.py --task $TASK --work $W 2>&1 | tail -1"
scp -q "$HOST:$W/controls-report.json" "$OUT"
