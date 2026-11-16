#!/bin/bash
# Give the evaluation host the public platform commit the Verifier is built from (harness/verifier.py
# PUBLIC_COMMIT), without GitHub credentials there. Reads the local public checkout; never modifies it.
# Usage: sync_public_to_host.sh [public-checkout] [host]
set -eu
SRC=${1:-$HOME/.paseo/worktrees/3q2d9qt9/operation-semantics}; HOST=${2:-root@10.30.11.112}
COMMIT=$(python3 -c "import re,sys;print(re.search(r\"PUBLIC_COMMIT = '([0-9a-f]{40})'\",open(sys.argv[1]).read()).group(1))" "$(dirname "$0")/../harness/verifier.py")
B=$(mktemp -t public.XXXXXX).bundle
git -C "$SRC" bundle create -q "$B" "$COMMIT" 2>/dev/null || git -C "$SRC" bundle create -q "$B" HEAD
scp -q "$B" "$HOST:/opt/poc-oracle/public.bundle"
ssh -o BatchMode=yes "$HOST" "set -e; D=/opt/poc-oracle/public; if [ ! -d \$D/.git ]; then git init -q \$D; fi
  git -C \$D fetch -q /opt/poc-oracle/public.bundle '+HEAD:refs/remotes/bundle/head' 2>/dev/null || git -C \$D fetch -q /opt/poc-oracle/public.bundle
  git -C \$D cat-file -e $COMMIT^{commit} && git -C \$D checkout -q --detach $COMMIT && git -C \$D log -1 --format='public %h %s'"
rm -f "$B"
