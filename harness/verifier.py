#!/usr/bin/env python3
"""Catalog-bound Verifier (`appctl operations`) for v7.

The public platform (PR #27, pinned commit below) is the Verifier's code. The private build overlays the
v7 catalog (tasks/<task>/catalog.go.txt) and the two vendored platform files that v7 changes
(harness/source/go-action-opdef/{opdef,opkernel}.go.txt), so the Verifier embeds exactly the catalog
the go-action-opdef kits carry. The public build is the unmodified public tree; on a v7 candidate it must
never return pass (control). The public checkout is read with `git archive` and never modified.

Usage: verifier.py --task T digest            # private catalog digest, as reported by the private appctl
       verifier.py --task T check --kind private|public --root CANDIDATE --base SHA
Needs POC_PUBLIC_PLATFORM (a checkout of the pinned commit) and optionally POC_GO.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_COMMIT = 'cbe1336af5eab5f1f3f2ac475703fb9d734f043e'
PUBLIC_CATALOG_DIGEST = 'sha256:98abea36467340ad3702d12341beb3894e649d42d9129cfac4e45e46a9de0055'
# Public files the private build replaces, with the public content they were derived from.
UPSTREAM = {'internal/opdef/catalog.go': '736429c18304e9a46b602e25af2510b4cf4fdbe592f904e5d7f21d4a63a0db92',
            'internal/opdef/opdef.go': 'da9bc05000573f68aca230999755c8aa283efd4e4aa8e24c5c3a52b320185432',
            'internal/opkernel/opkernel.go': '8655075e58fb76b69e7b316437fe23ce9d8070404d4beb9ee8a5d0e91e8360a1'}


def overlay(task):
    return {'internal/opdef/catalog.go': 'tasks/%s/catalog.go.txt' % task,
            'internal/opdef/opdef.go': 'harness/source/go-action-opdef/opdef.go.txt',
            'internal/opkernel/opkernel.go': 'harness/source/go-action-opdef/opkernel.go.txt'}


def public():
    path = Path(os.environ.get('POC_PUBLIC_PLATFORM', str(Path.home() / '.paseo/worktrees/3q2d9qt9/operation-semantics')))
    head = subprocess.check_output(['git', '-C', str(path), 'rev-parse', PUBLIC_COMMIT + '^{commit}'], text=True).strip()
    if head != PUBLIC_COMMIT: raise ValueError('public platform commit not available')
    for p, h in UPSTREAM.items():
        data = subprocess.check_output(['git', '-C', str(path), 'show', PUBLIC_COMMIT + ':' + p])
        if hashlib.sha256(data).hexdigest() != h: raise ValueError('upstream drift (re-bind needed): ' + p)
    return path


def manifest(task):
    """Plan record of the Verifier build: public commit, overlay hashes, private and public catalog digests."""
    return {'public_commit': PUBLIC_COMMIT, 'public_catalog_digest': PUBLIC_CATALOG_DIGEST,
            'overlay': {p: hashlib.sha256((ROOT / src).read_bytes()).hexdigest() for p, src in overlay(task).items()},
            'upstream': UPSTREAM}


def build(task, kind, out):
    """Build appctl into out/appctl-<kind>; the source tree is a fresh `git archive` of the pinned commit."""
    src = public()
    binary = Path(out) / ('appctl-' + kind)
    with tempfile.TemporaryDirectory() as tmp:
        archive = subprocess.run(['git', '-C', str(src), 'archive', PUBLIC_COMMIT], check=True, capture_output=True).stdout
        subprocess.run(['tar', '-x', '-C', tmp], input=archive, check=True)
        if kind == 'private':
            for p, template in overlay(task).items():
                (Path(tmp) / p).write_bytes((ROOT / template).read_bytes())
        subprocess.run([os.environ.get('POC_GO', 'go'), 'build', '-o', str(binary), './cmd/appctl'], cwd=tmp, check=True,
                       capture_output=True, timeout=600)
    return binary


def check(binary, root, base):
    r = subprocess.run([str(binary), 'operations', '--root', str(root), '--base', base], capture_output=True, text=True, timeout=300)
    try:
        report = json.loads(r.stdout)
    except ValueError:
        report = {'error': (r.stdout + r.stderr)[-500:]}
    return {'exit': r.returncode, 'verdict': report.get('verdict'), 'catalog': report.get('catalog'),
            'codes': sorted({f['code'] for f in report.get('findings') or []}), 'findings': (report.get('findings') or [])[:20],
            **({'error': report['error']} if 'error' in report else {})}


def digest(task):
    """The private catalog digest as reported by the private appctl itself (checked on an empty repository)."""
    with tempfile.TemporaryDirectory() as tmp:
        binary = build(task, 'private', tmp)
        repo = Path(tmp) / 'empty'
        repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=v7', '-c', 'user.email=v7@example.invalid', 'commit', '-q',
                        '--allow-empty', '-m', 'empty'], check=True)
        base = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
        result = check(binary, repo, base)
    if not result['catalog'] or result['catalog'] == PUBLIC_CATALOG_DIGEST: raise ValueError('private build did not embed the v7 catalog')
    return result['catalog']


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--task', choices=('withdraw', 'cost-center'), required=True)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('digest')
    c = sub.add_parser('check')
    c.add_argument('--kind', choices=('private', 'public'), required=True)
    c.add_argument('--root', required=True)
    c.add_argument('--base', required=True)
    a = p.parse_args()
    if a.command == 'digest':
        print(json.dumps({'task': a.task, 'catalog': digest(a.task), **manifest(a.task)}))
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        print(json.dumps(check(build(a.task, a.kind, tmp), a.root, a.base)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
