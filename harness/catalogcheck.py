#!/usr/bin/env python3
"""Compile one catalog (internal/opdef: opdef.go + catalog.go) and report Validate(), All() and Digest().

Used on both sides with the same code: the stage wrapper (kit) runs it after the drafting and review
sessions (an entry that does not validate ends the trial), and the evaluation host runs it on the frozen
catalog of every drafted trial (authoritative validity, the entry-correctness Oracle input, and the catalog
digest the private Verifier is bound to). Standard library only; Go runs offline (GOPROXY=off).

Usage: catalogcheck.py --opdef OPDEF.go --catalog CATALOG.go   -> one JSON line
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

MAIN = '''package main

import (
	"encoding/json"
	"os"

	"catalogcheck/opdef"
)

func main() {
	out := map[string]any{"valid": true, "entries": opdef.All(), "digest": opdef.Digest()}
	if err := opdef.Validate(); err != nil {
		out["valid"], out["error"] = false, err.Error()
	}
	if err := json.NewEncoder(os.Stdout).Encode(out); err != nil {
		os.Exit(1)
	}
}
'''


def check(opdef_go, catalog_go, go=None, timeout=300):
    """{'valid', 'error', 'entries', 'digest', 'compiled'}; a catalog that does not compile is invalid."""
    go = go or os.environ.get('POC_GO', 'go')
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'opdef').mkdir()
        (tmp / 'go.mod').write_text('module catalogcheck\n\ngo 1.22\n')
        (tmp / 'main.go').write_text(MAIN)
        (tmp / 'opdef' / 'opdef.go').write_bytes(opdef_go)
        (tmp / 'opdef' / 'catalog.go').write_bytes(catalog_go)
        env = {**os.environ, 'GOPROXY': 'off', 'GOFLAGS': '-mod=mod', 'GOWORK': 'off', 'CGO_ENABLED': '0'}
        r = subprocess.run([go, 'run', '.'], cwd=tmp, capture_output=True, text=True, timeout=timeout, env=env)
    if r.returncode != 0:
        return {'valid': False, 'compiled': False, 'error': (r.stdout + r.stderr)[-1500:], 'entries': None, 'digest': None}
    value = json.loads(r.stdout)
    return {'valid': value['valid'], 'compiled': True, 'error': value.get('error'), 'entries': value['entries'],
            'digest': value['digest']}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--opdef', required=True)
    p.add_argument('--catalog', required=True)
    a = p.parse_args()
    print(json.dumps(check(Path(a.opdef).read_bytes(), Path(a.catalog).read_bytes())))
    return 0


if __name__ == '__main__':
    sys.exit(main())
