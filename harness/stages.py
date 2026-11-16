#!/usr/bin/env python3
"""v8a stage wrapper for drafted / drafted-review kits (generation side, inside isolate_net.py).

Runs the trial's sessions in order on one candidate checkout, each as a fresh Kiro session through the
public phase 3 worker's own invoke() (kit worker.py, byte-identical to the public file and imported, not
copied), so every stage keeps its raw ACP events (<output>/<stage>/events.jsonl), stderr and parsed usage:

  draft    the trial's model; may change only internal/opdef/catalog.go
  review   (drafted-review only) claude-opus-5.5; may change only internal/opdef/catalog.go
  adapter  the trial's model; may change only internal/candidate/*.go; the catalog is frozen

After every session: scope check, then one commit. After draft and review: catalogcheck (compile +
opdef.Validate()); an invalid catalog ends the trial as draft-invalid, with no retry. The receipt (version 81)
lists every stage with its commit, generation record and catalog check; credits and seconds are summed over
the stages into `generation`. A bundle is written whenever at least one stage committed, so the evaluation
host can score the entry (C) even when a later stage failed.
Requires no evaluator checkout or private seed.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

CATALOG = 'internal/opdef/catalog.go'
RECEIPT = 81


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


def editable(kind, name):
    return name == CATALOG if kind == 'catalog' else (name.startswith('internal/candidate/') and name.endswith('.go'))


def changed(w, candidate):
    names = subprocess.check_output(['git', '-C', str(candidate), 'diff', '--name-only', '-z', 'HEAD'])
    names += subprocess.check_output(['git', '-C', str(candidate), 'ls-files', '--others', '--exclude-standard', '-z'])
    return sorted({n.decode() for n in names.split(b'\0') if n})


def total(stages):
    """Sum of the stage generations: credits (None if any stage lacks usage) and seconds."""
    gens = [s['generation'] for s in stages if s.get('generation')]
    credits = [(g.get('usage') or {}).get('credits') for g in gens]
    return {'status': 'completed' if gens and all(g['status'] == 'completed' for g in gens) else 'incomplete',
            'usage': None if not gens or any(c is None for c in credits) else {'credits': sum(credits)},
            'generation_seconds': round(sum(g.get('generation_seconds') or 0 for g in gens), 3),
            'turn_started': any(g.get('turn_started') for g in gens), 'stages': [s['name'] for s in stages if s.get('generation')]}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--kit', default=str(Path(__file__).resolve().parent))
    parser.add_argument('--output', required=True); parser.add_argument('--kiro-bin', default='kiro-cli')
    parser.add_argument('--synthetic-test-driver')
    a = parser.parse_args(); kit = Path(a.kit).resolve(); job = json.loads((kit / 'job.json').read_text())
    if bool(a.synthetic_test_driver) != (job['kind'] == 'synthetic-smoke'): raise ValueError('synthetic/real driver mismatch')
    if digest(kit / 'plan.json') != job['plan_sha256'] or digest(kit / 'manifest.json') != job['manifest_sha256']:
        raise ValueError('kit identity/checksum mismatch')
    if digest(Path(__file__)) != job['stages_sha256'] or digest(kit / 'worker.py') != job['worker_sha256'] \
            or digest(kit / 'catalogcheck.py') != job['catalogcheck_sha256']:
        raise ValueError('kit tools changed after preparation')
    candidate = kit / 'candidate'; m = json.loads((kit / 'manifest.json').read_text()); p = json.loads((kit / 'plan.json').read_text())
    t = job['trial']; initial = m['initial_sha']
    if (p['kind'] != job['kind'] or t not in p['trials'] or m['trial_id'] != t['id'] or not t.get('stages')
            or m['plan_sha256'] != job['plan_sha256'] or m['files'] != p['exports'][t['cell']]
            or m['source_base_sha'] != p['base_sha'] or job['model'] != p['model']
            or job['worker_sha256'] != p['frozen']['benchmark/phase3/generation_worker.py']
            or job['stages_sha256'] != p['frozen']['harness/stages.py'] or job['catalogcheck_sha256'] != p['frozen']['harness/catalogcheck.py']
            or job['reasoning_effort'] != p['reasoning_effort'] or job['generation_budget_seconds'] != p['generation_budget_seconds']
            or job.get('engine') != p.get('engine')):
        raise ValueError('kit plan/manifest mismatch')
    w = load('generation_worker', kit / 'worker.py'); check = load('catalogcheck', kit / 'catalogcheck.py')
    if w.git(candidate, 'rev-parse', 'HEAD') != initial or w.git(candidate, 'rev-list', '--count', 'HEAD') != '1' \
            or w.git(candidate, 'remote') or w.git(candidate, 'status', '--porcelain'):
        raise ValueError('kit candidate is not a clean isolated base')
    if set(w.git(candidate, 'ls-tree', '-r', '--name-only', 'HEAD').splitlines()) != set(m['files']): raise ValueError('unexpected kit files')
    for name, h in m['files'].items():
        if (candidate / name).is_symlink() or digest(candidate / name) != h: raise ValueError('kit source tamper: ' + name)
    out = Path(a.output).resolve()
    if out.is_relative_to(candidate): raise ValueError('output must be outside candidate')
    out.mkdir(parents=True, exist_ok=False)
    binary = a.synthetic_test_driver or a.kiro_bin
    try:
        version = subprocess.check_output([binary, '--version'], text=True, timeout=10).strip()
        probe = w.invoke(binary, job, candidate, 'Reply exactly READY. Do not read or modify files. Do not call tools.', out / 'probe', 30, 'read-only')
        ready = probe['status'] == 'completed' and probe['agent_messages'][-1:] == ['READY']
    except (OSError, subprocess.SubprocessError) as error:
        version = None; probe = {'status': 'runtime-blocked', 'error': str(error)}; ready = False
    w.save(out / 'preflight.json', {'ready': ready, 'cli_version': version, 'invocation': probe})
    if not ready: return 3
    stages, outcome, head = [], 'generated', initial
    opdef = (candidate / 'internal/opdef/opdef.go').read_bytes()
    for spec in t['stages']:
        stage = {'name': spec['name'], 'model': spec['model'], 'commit': None, 'generation': None, 'check': None}
        stages.append(stage)
        sjob = {**job, 'model': spec['model'], 'reasoning_effort': spec['reasoning_effort']}
        result = w.invoke(binary, sjob, candidate, spec['prompt'], out / spec['name'], spec['budget_seconds'], 'workspace-write')
        result['cli_version'] = version; stage['generation'] = result
        if result['status'] != 'completed':
            outcome = result['status']; break
        names = changed(w, candidate)
        if (any(not editable(spec['editable'], n) for n in names) or any(x.is_symlink() for x in candidate.rglob('*'))
                or w.git(candidate, 'remote') or w.git(candidate, 'rev-parse', 'HEAD') != head):
            outcome = 'scope-violation'; stage['scope'] = names; break
        w.command(candidate, 'add', '--all', '--', *names) if names else None
        w.command(candidate, '-c', 'user.name=POC v8a Stages', '-c', 'user.email=poc@example.invalid', 'commit', '--allow-empty', '-q',
                  '-m', 'v8a stage ' + spec['name'])
        head = stage['commit'] = w.git(candidate, 'rev-parse', 'HEAD')
        if spec['editable'] == 'catalog':
            # Same check as the evaluation host: the catalog compiles and opdef.Validate() passes. No retry.
            stage['check'] = {k: v for k, v in check.check(opdef, (candidate / CATALOG).read_bytes()).items() if k != 'entries'}
            if not stage['check']['valid']:
                outcome = 'draft-invalid'; break
    generation = total(stages)
    if any(s['commit'] for s in stages):
        (out / 'candidate.patch').write_bytes(subprocess.check_output(['git', '-C', str(candidate), 'diff', '--binary', initial, 'HEAD']))
        w.command(candidate, 'bundle', 'create', out / 'candidate.bundle', 'HEAD')
        w.save(out / 'receipt.json', {'version': RECEIPT, 'kind': job['kind'], 'plan_sha256': job['plan_sha256'], 'trial': t,
                                      'candidate_sha': w.git(candidate, 'rev-parse', 'HEAD'), 'manifest_sha256': job['manifest_sha256'],
                                      'bundle_sha256': digest(out / 'candidate.bundle'), 'outcome': outcome, 'stages': stages,
                                      'generation': generation})
    record = {'version': RECEIPT, 'kind': job['kind'], 'plan_sha256': job['plan_sha256'], 'trial': t, 'outcome': outcome,
              'passed': False, 'generation': generation, 'stages': stages}
    w.save(out / 'outcome.json', record)
    if outcome != 'generated' and not (out / 'receipt.json').exists():
        w.save(out / 'outcomes' / t['id'] / 'result.json', record)
    return 0 if outcome in ('generated', 'budget-exceeded', 'scope-violation', 'draft-invalid') else 3


if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as error: print('v8a stages error: ' + str(error), file=sys.stderr); sys.exit(1)
