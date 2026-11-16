#!/bin/bash
# Push this private repo to the trusted evaluation host without leaving GitHub
# credentials there. Usage: sync_to_host.sh [host] [dir]
set -eu
HOST=${1:-root@10.30.11.112}; DIR=${2:-/opt/poc-oracle/private}
cd "$(dirname "$0")/.."
B=$(mktemp -t oracle.XXXXXX).bundle
git bundle create "$B" --all
ssh -o BatchMode=yes "$HOST" "mkdir -p $DIR.bundles"
scp -q "$B" "$HOST:$DIR.bundles/latest.bundle"
ssh -o BatchMode=yes "$HOST" "set -e; if [ -d $DIR/.git ]; then git -C $DIR fetch -q $DIR.bundles/latest.bundle '+refs/heads/*:refs/remotes/bundle/*' && git -C $DIR checkout -q -B main bundle/main; else git clone -q $DIR.bundles/latest.bundle $DIR && git -C $DIR remote remove origin; fi; git -C $DIR log -1 --format='%h %s'"
rm -f "$B"
