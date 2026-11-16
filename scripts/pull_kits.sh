#!/bin/bash
# Copy a run's kits (no seed, no Oracle) from the host to a generation directory outside this repository.
# Usage: pull_kits.sh <run> <dest-root> [host]   -> <dest-root>/<run>/{kits,order.txt,plan.json}
set -eu
RUN=$1; DEST=$2; HOST=${3:-root@10.30.11.112}
PRIVATE=$(cd "$(dirname "$0")/.." && pwd -P)
mkdir -p "$DEST"; D=$(cd "$DEST" && pwd -P)/$RUN
case "$D/" in "$PRIVATE"/*) echo "destination must be outside $PRIVATE" >&2; exit 2 ;; esac
test ! -e "$D"; mkdir -p "$D"
scp -q -r "$HOST:/opt/poc-oracle/runs/$RUN/kits" "$HOST:/opt/poc-oracle/runs/$RUN/order.txt" "$HOST:/opt/poc-oracle/runs/$RUN/plan.json" "$D/"
test ! -e "$D/private" || { echo "seed directory must not be pulled" >&2; exit 1; }
echo "pulled $(ls "$D/kits" | wc -l | tr -d ' ') kits to $D"
