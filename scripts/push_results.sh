#!/bin/bash
# Copy generation results (bundles, receipts, network logs) of a run back to the evaluation host.
# Usage: push_results.sh <run> <dest-root> [host]
set -eu
RUN=$1; D=$(cd "$2/$RUN" && pwd -P); HOST=${3:-root@10.30.11.112}
ssh -o BatchMode=yes "$HOST" "test ! -e /opt/poc-oracle/runs/$RUN/results"
scp -q -r "$D/results" "$HOST:/opt/poc-oracle/runs/$RUN/results"
scp -q "$D/ops/status.log" "$HOST:/opt/poc-oracle/runs/$RUN/generation-status.log"
echo "pushed $(ls "$D/results" | wc -l | tr -d ' ') results"
