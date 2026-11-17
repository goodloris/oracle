#!/bin/bash
# Push this private repo to the trusted evaluation host without leaving GitHub
# credentials there; the host checkout is set to BRANCH (default: the current branch).
# Usage: sync_to_host.sh [host] [dir] [branch]
set -eu
cd "$(dirname "$0")/.."
HOST=${1:-root@10.30.11.112}; DIR=${2:-/opt/poc-oracle/private-v9}; BRANCH=${3:-$(git rev-parse --abbrev-ref HEAD)}
case "$BRANCH" in *[!A-Za-z0-9._/-]*|'') echo "invalid branch name" >&2; exit 2 ;; esac
B=$(mktemp -t oracle.XXXXXX).bundle
git bundle create -q "$B" --all
ssh -o BatchMode=yes "$HOST" "mkdir -p $DIR.bundles"
scp -q "$B" "$HOST:$DIR.bundles/latest.bundle"
ssh -o BatchMode=yes "$HOST" "set -e; if [ ! -d $DIR/.git ]; then git clone -q $DIR.bundles/latest.bundle $DIR && git -C $DIR remote remove origin; fi
  git -C $DIR fetch -q $DIR.bundles/latest.bundle '+refs/heads/*:refs/remotes/bundle/*'
  test -z \"\$(git -C $DIR status --porcelain)\" || { echo 'host checkout is dirty' >&2; exit 1; }
  git -C $DIR checkout -q -B '$BRANCH' 'bundle/$BRANCH'; git -C $DIR log -1 --format='$BRANCH %h %s'"
rm -f "$B"
