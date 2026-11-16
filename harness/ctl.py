#!/usr/bin/env python3
"""v7 private controller: fixtures / plan / prepare / export / restore / evaluate / summarize.

Plan, manifest, job and receipt formats are those of the public phase 3 worker
(benchmark/phase3/generation_worker.py, vendored byte-identical), so a kit runs unchanged in the
public isolation tools. Everything that defines a trial (export templates, task text, Oracle, controls)
lives in this private repository and is frozen by hash in the plan.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'harness'
WORKER = 'benchmark/phase3/generation_worker.py'
# Provenance only: the public commit the vendored base sources (harness/source/common) were copied from.
PUBLIC_BASE = '71a213de04c4644b837184b1888ff22b77c5090d'
TASKS = ('withdraw', 'cost-center')
CELLS = ('go-manual', 'go-manual-doc', 'go-action-opdef')
OBSERVED = {'passed', 'acceptance-failed', 'candidate-error', 'budget-exceeded', 'scope-violation'}
UNMEASURED = {'runtime-blocked', 'provider-error', 'protocol-error', 'infrastructure-blocked'}
SAFETY = ('unauthorized_write', 'partial_batch', 'side_effect_atomicity', 'double_apply', 'wrong_commit',
          'service_error', 'undecidable')
C, M, O = 'harness/source/common/', 'harness/source/go-manual/', 'harness/source/go-action-opdef/'


def sha(data): return hashlib.sha256(data).hexdigest()
def digest(path): return sha(Path(path).read_bytes())


def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False); stream.write('\n')


def git(root, *args): return subprocess.check_output(['git', '-C', str(root), *map(str, args)], text=True).strip()
def process(args, **kw): return subprocess.run(list(map(str, args)), capture_output=True, text=True, check=True, **kw)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


def contract(task): return json.loads((ROOT / 'tasks' / task / 'cohort.json').read_text())


def layout(task, cell):
    """Export path -> repository template for one task and cell."""
    T = 'tasks/%s/' % task
    files = {'go.mod': C + 'go.mod.txt', 'go.sum': C + 'go.sum.txt', 'cmd/server/main.go': C + 'main.go.txt',
             'internal/app/server.go': C + 'server.go.txt', 'internal/app/demo_identity.go': C + 'demo_identity.go.txt',
             'internal/protocol/protocol.go': C + 'protocol.go.txt', 'internal/ledger/ledger.go': C + 'ledger.go.txt',
             'internal/ledger/reconcile.go': T + 'reconcile.go.txt', 'internal/notify/notify.go': T + 'notify.go.txt'}
    if cell in ('go-manual', 'go-manual-doc'):
        files.update({'internal/candidate/' + n + '.go': M + n + '.go.txt' for n in ('approval', 'reject', 'reopen', 'countersign', 'reassign')})
    if cell == 'go-manual-doc':
        files['docs/OPERATIONS.md'] = T + 'OPERATIONS.md'
    if cell == 'go-action-opdef':
        files.update({'internal/opdef/opdef.go': O + 'opdef.go.txt', 'internal/opdef/catalog.go': T + 'catalog.go.txt',
                      'internal/opkernel/opkernel.go': O + 'opkernel.go.txt', 'internal/candidate/approval.go': O + 'approval.go.txt'})
    return files


def schema_files(task): return ['harness/sql/bootstrap.sql', 'harness/sql/base.sql', 'tasks/%s/schema.sql' % task]


def files(task, cell):
    data = {path: (ROOT / src).read_bytes() for path, src in layout(task, cell).items()}
    data['TASK.md'] = (contract(task)['requirements'] + '\n').encode()
    data['SCHEMA.sql'] = b'\n'.join((ROOT / p).read_bytes() for p in schema_files(task)[1:])
    data['.gitignore'] = b'bin/\n/server\n*.log\n__pycache__/\n'
    return data


def prompt(task, cell):
    c = contract(task)
    return '\n'.join([c['requirements'], *c['prompt'], c['capability'][cell], c['prompt_tail']])


def frozen_paths(task):
    tracked = git(ROOT, 'ls-files', 'harness', 'tasks/' + task, 'benchmark').splitlines()
    assert WORKER in tracked, 'vendored worker must be tracked'
    return tracked


def trials(plan, ident=None):
    selected = [t for t in plan['trials'] if ident is None or t['id'] == ident]
    if not selected: raise ValueError('trial not in plan')
    return selected


def load_plan(path):
    value = json.loads(Path(path).read_text())
    if value.get('version') != 7 or value.get('kind') not in ('model-trial', 'synthetic-smoke') or value.get('task') not in TASKS:
        raise ValueError('v7 cohort plan required')
    if set(value['frozen']) != set(frozen_paths(value['task'])): raise ValueError('incomplete harness manifest')
    for p, h in value['frozen'].items():
        if digest(ROOT / p) != h: raise ValueError('evaluator drift: ' + p)
    if value['base_sha'] != PUBLIC_BASE: raise ValueError('unexpected base provenance')
    if set(value.get('database', {})) != {'image', 'version', 'collate', 'ctype', 'provider', 'byte_order'}: raise ValueError('database environment missing')
    if type(value['generation_budget_seconds']) is not int or value['generation_budget_seconds'] < 1:
        raise ValueError('invalid generation budget')
    if value['reasoning_effort'] not in ('low', 'medium', 'high') or not value['model'] or value.get('engine') not in ('codex', 'kiro'):
        raise ValueError('invalid model settings')
    blocks, seen = {}, set()
    for t in value['trials']:
        if t['cell'] not in CELLS or type(t['instance']) is not int or t['instance'] < 1: raise ValueError('invalid trial')
        expected = t['cell'] + '--' + str(t['instance']).zfill(3)
        if t['id'] != expected or expected in seen: raise ValueError('duplicate/invalid trial identity')
        seen.add(expected); blocks.setdefault(t['instance'], set()).add(t['cell'])
        if t['prompt'] != prompt(value['task'], t['cell']) or sha(t['prompt'].encode()) != t['prompt_sha256']:
            raise ValueError('prompt tamper')
    if not blocks or sorted(blocks) != list(range(1, len(blocks) + 1)) or any(b != set(CELLS) for b in blocks.values()):
        raise ValueError('incomplete three-cell block')
    if value['exports'] != {c: {p: sha(d) for p, d in files(value['task'], c).items()} for c in CELLS}: raise ValueError('export drift')
    v = verifier().manifest(value['task'])
    if {k: value['verifier'][k] for k in v} != v or not value['verifier']['catalog_digest'].startswith('sha256:'):
        raise ValueError('verifier binding drift')
    return value


def verifier(): return module('v7_verifier', HERE / 'verifier.py')


def database_env():
    """Measured evaluation database (version, collation, ctype) plus the operator-declared image (POC_PG_IMAGE)."""
    import tempfile
    oracle = module('v7_oracle_env', HERE / 'oracle.py')
    with tempfile.TemporaryDirectory() as tmp:
        db = oracle.Database(dbrpc(Path(tmp)))
        try:
            value = db.call('env')
        finally:
            db.close()
    return {'image': os.environ.get('POC_PG_IMAGE', 'unspecified'), **value}


def fixtures(args): save(args.output, {'version': 1, 'seed': secrets.token_hex(32)})


def plan(args):
    if args.allow_dirty_harness and not args.synthetic: raise ValueError('dirty real harness forbidden')
    if git(ROOT, 'status', '--porcelain') and not args.allow_dirty_harness: raise ValueError('commit harness before registering cohort')
    private = json.loads(Path(args.fixtures).read_text())
    if private.get('version') != 1 or len(private.get('seed', '')) < 32: raise ValueError('private seed required')
    if args.repetitions not in range(1, 101) or args.time_budget < 1: raise ValueError('invalid repetitions/budget')
    value = {'version': 7, 'kind': 'synthetic-smoke' if args.synthetic else 'model-trial', 'task': args.task,
             'operation': contract(args.task)['operation'], 'base_sha': PUBLIC_BASE, 'harness_sha': git(ROOT, 'rev-parse', 'HEAD'),
             'frozen': {p: digest(ROOT / p) for p in frozen_paths(args.task)}, 'fixtures_sha256': digest(args.fixtures),
             'seed': args.seed, 'model': args.model, 'reasoning_effort': args.reasoning_effort, 'engine': args.engine,
             'generation_budget_seconds': args.time_budget, 'trials': [],
             'isolation': 'operator must isolate generation from evaluator; commands alone do not enforce it'}
    value['exports'] = {c: {p: sha(d) for p, d in files(args.task, c).items()} for c in CELLS}
    # The catalog-bound Verifier is built from the pinned public platform plus the private overlay; its
    # catalog digest is frozen here (the public opdef_test pins only the public digest).
    v = verifier()
    value['verifier'] = {**v.manifest(args.task), 'catalog_digest': v.digest(args.task)}
    # The database the trials will be evaluated on; evaluate refuses a different one (sort order matters).
    value['database'] = database_env()
    for instance in range(1, args.repetitions + 1):
        block = []
        for c in CELLS:
            text = prompt(args.task, c)
            block.append({'id': c + '--' + str(instance).zfill(3), 'cell': c, 'instance': instance,
                          'prompt': text, 'prompt_sha256': sha(text.encode())})
        random.Random('%d:%s:%d' % (args.seed, args.task, instance)).shuffle(block); value['trials'] += block
    save(args.output, value)
    print(json.dumps({'planned': len(value['trials']), 'kind': value['kind'], 'task': args.task}))


def export_candidate(args):
    p = load_plan(args.plan); t = trials(p, args.trial)[0]
    candidate = Path(args.candidate).resolve(); manifest = Path(args.manifest).resolve()
    if candidate == ROOT or candidate.is_relative_to(ROOT) or manifest.is_relative_to(candidate):
        raise ValueError('candidate must be outside harness and attestation outside candidate')
    if candidate.exists() or manifest.exists(): raise ValueError('export output exists')
    candidate.mkdir(parents=True)
    for name, data in files(p['task'], t['cell']).items():
        path = candidate / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    process(['git', 'init', '-q', '-b', 'candidate'], cwd=candidate)
    process(['git', 'add', '.'], cwd=candidate)
    process(['git', '-c', 'user.name=POC Export', '-c', 'user.email=poc@example.invalid', 'commit', '-q', '-m', 'v7 shared base'], cwd=candidate)
    save(manifest, {'version': 7, 'trial_id': t['id'], 'plan_sha256': digest(args.plan), 'initial_sha': git(candidate, 'rev-parse', 'HEAD'),
                    'source_base_sha': p['base_sha'], 'files': p['exports'][t['cell']]})


def prepare(args):
    """A per-trial kit: no private seed, Oracle, controller, task schema of another task or other cell; worker = public phase 3 worker."""
    p = load_plan(args.plan); t = trials(p, args.trial)[0]; out = Path(args.output).resolve()
    if out == ROOT or out.is_relative_to(ROOT): raise ValueError('generation kit must be outside evaluator checkout')
    out.mkdir(parents=True, exist_ok=False)
    export_candidate(argparse.Namespace(plan=args.plan, trial=args.trial, candidate=out / 'candidate', manifest=out / 'manifest.json'))
    (out / 'plan.json').write_bytes(Path(args.plan).read_bytes())
    (out / 'worker.py').write_bytes((ROOT / WORKER).read_bytes())
    save(out / 'job.json', {'version': 7, 'kind': p['kind'], 'plan_sha256': digest(args.plan), 'trial': t,
                            'manifest_sha256': digest(out / 'manifest.json'), 'worker_sha256': digest(out / 'worker.py'),
                            'model': p['model'], 'reasoning_effort': p['reasoning_effort'],
                            'generation_budget_seconds': p['generation_budget_seconds'], 'engine': p['engine']})


def validate_candidate(args, p):
    candidate = Path(args.candidate).resolve(); manifest = Path(args.manifest).resolve(); t = trials(p, args.trial)[0]
    if candidate == ROOT or candidate.is_relative_to(ROOT) or manifest.is_relative_to(candidate):
        raise ValueError('invalid candidate/attestation boundary')
    m = json.loads(manifest.read_text()); initial = m['initial_sha']
    if (m.get('version') != 7 or m['trial_id'] != t['id'] or m['plan_sha256'] != digest(args.plan)
            or m['source_base_sha'] != p['base_sha'] or m['files'] != p['exports'][t['cell']]):
        raise ValueError('export attestation mismatch')
    if git(candidate, 'rev-list', '--count', initial) != '1' or git(candidate, 'remote'): raise ValueError('export history/remote violation')
    if set(git(candidate, 'ls-tree', '-r', '--name-only', initial).splitlines()) != set(m['files']): raise ValueError('unexpected base files')
    for name, h in m['files'].items():
        if sha(subprocess.check_output(['git', '-C', str(candidate), 'show', initial + ':' + name])) != h: raise ValueError('base snapshot tamper')
    process(['git', '-C', candidate, 'merge-base', '--is-ancestor', initial, 'HEAD'])
    if git(candidate, 'status', '--porcelain'): raise ValueError('candidate must be committed/clean')
    changed = git(candidate, 'diff', '--name-only', initial, 'HEAD').splitlines()
    if any(not n.startswith('internal/candidate/') or not n.endswith('.go') for n in changed):
        raise ValueError('protected capability/export modification')
    for line in git(candidate, 'ls-tree', '-r', 'HEAD').splitlines():
        if line.split()[0] != '100644': raise ValueError('only regular non-executable source files allowed')
    return candidate


def receipt(args, p):
    # Version 3 is the shared standalone worker's receipt format.
    r = json.loads(Path(args.receipt).read_text()); t = trials(p, args.trial)[0]
    if (r.get('version') != 3 or r['kind'] != p['kind'] or r['plan_sha256'] != digest(args.plan) or r['trial'] != t
            or r['manifest_sha256'] != digest(args.manifest) or r['generation']['status'] != 'completed'):
        raise ValueError('generation receipt identity mismatch')
    return r


def restore(args):
    p = load_plan(args.plan); r = receipt(args, p)
    if digest(args.bundle) != r['bundle_sha256']: raise ValueError('bundle checksum mismatch')
    candidate = Path(args.candidate).resolve()
    if candidate.exists() or candidate == ROOT or candidate.is_relative_to(ROOT): raise ValueError('invalid restore directory')
    process(['git', 'clone', '-q', args.bundle, candidate]); process(['git', 'remote', 'remove', 'origin'], cwd=candidate)
    if git(candidate, 'rev-parse', 'HEAD') != r['candidate_sha']: raise ValueError('restored head mismatch')
    validate_candidate(args, p)


def score(task, cases):
    """Per-group scores, E/K/D/A and the structured safety field; raises on an incomplete case manifest."""
    groups = contract(task)['groups']
    if Counter(c['group'] for c in cases) != {g: v['count'] for g, v in groups.items()} or \
            len({(c['group'], c['case']) for c in cases}) != len(cases):
        raise ValueError('incomplete/duplicate case manifest: %s' % dict(Counter(c['group'] for c in cases)))
    by = {g: {'kind': v['kind'], 'passed': sum(c['passed'] for c in cases if c['group'] == g), 'total': v['count']} for g, v in groups.items()}
    kinds = {k: {'passed': sum(v['passed'] for v in by.values() if v['kind'] == k), 'total': sum(v['total'] for v in by.values() if v['kind'] == k)}
             for k in ('explicit', 'implicit')}
    conventions = {g: v['passed'] == v['total'] for g, v in by.items() if v['kind'] == 'implicit'}
    return {'groups': by, 'explicit': kinds['explicit'], 'implicit': kinds['implicit'],
            'explicit_passed': kinds['explicit']['passed'] == kinds['explicit']['total'],
            'conventions': conventions, 'conventions_passed': all(conventions.values()),
            'outbox_passed': conventions['implicit-outbox'],
            'safety': safety(cases), 'passed': all(c['passed'] for c in cases)}


def safety(cases):
    return {k: {'count': len(v), 'cases': v} for k, v in
            ((k, sorted(c['group'] + ': ' + c['case'] for c in cases if k in c.get('safety', []))) for k in SAFETY)}


def stop(server):
    if server.poll() is None: os.killpg(server.pid, signal.SIGTERM)
    try: server.wait(timeout=5)
    except subprocess.TimeoutExpired: os.killpg(server.pid, signal.SIGKILL); server.wait()


def go():
    return os.environ.get('POC_GO', 'go')


def dbrpc(out):
    binary = out / 'dbrpc'
    subprocess.run([go(), 'build', '-o', str(binary), '.'], cwd=HERE / 'dbrpc', check=True, capture_output=True, timeout=300)
    return binary


def evaluate(args):
    p = load_plan(args.plan); t = trials(p, args.trial)[0]; r = receipt(args, p)
    candidate = validate_candidate(args, p)
    if git(candidate, 'rev-parse', 'HEAD') != r['candidate_sha']: raise ValueError('candidate differs from generation receipt')
    if digest(args.fixtures) != p['fixtures_sha256']: raise ValueError('private seed differs from plan')
    private = json.loads(Path(args.fixtures).read_text())
    out = Path(args.output).resolve(); out.mkdir(parents=True, exist_ok=False)
    port = os.environ.get('POC_HTTP_PORT', '8080')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', int(port))) == 0: raise ValueError('evaluation port occupied')
    result = {'version': 7, 'task': p['task'], **{k: r[k] for k in ('kind', 'plan_sha256', 'trial', 'candidate_sha', 'manifest_sha256', 'generation')},
              'cases': [], 'passed': False}
    oracle = module('v7_oracle', HERE / 'oracle.py')
    server = None; db = None; binary = out / 'server'; start = time.monotonic()
    try:
        process([go(), 'version'], timeout=10)
        db = oracle.Database(dbrpc(out))
        env = {'image': os.environ.get('POC_PG_IMAGE', 'unspecified'), **db.call('env')}
        if env != p['database']: raise oracle.InfrastructureError('evaluation database differs from plan: %s' % env)
        result['database'] = env
        db.call('init', files=[str(ROOT / f) for f in schema_files(p['task'])])
    except (OSError, subprocess.SubprocessError, oracle.InfrastructureError) as error:
        result.update({'outcome': 'infrastructure-blocked', 'error': str(error)[:1000]})
    else:
        with (out / 'candidate.log').open('x') as log:
            try:
                subprocess.run([go(), 'build', '-buildvcs=false', '-o', str(binary), './cmd/server'], cwd=candidate,
                               stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
                env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'TMPDIR', 'DATABASE_URL', 'POC_HTTP_PORT') if k in os.environ}
                server = subprocess.Popen([str(binary)], cwd=candidate, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                for _ in range(100):
                    if server.poll() is not None: raise RuntimeError('candidate exited before readiness')
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:' + port + '/health', timeout=1) as response:
                            if response.status == 200: break
                    except OSError: time.sleep(.1)
                else: raise RuntimeError('candidate readiness timeout')
                cases = oracle.run(db, p['task'], private['seed'] + ':' + str(t['instance']))
                if server.poll() is not None: raise RuntimeError('candidate exited during evaluation (rc=%s)' % server.returncode)
                try: scored = score(p['task'], cases)
                except ValueError as error: raise oracle.InfrastructureError(str(error)) from error
                result.update({'cases': cases, **scored, 'outcome': 'passed' if scored['passed'] else 'acceptance-failed'})
            except oracle.InfrastructureError as error:
                log.flush(); crashed = server is not None and (server.poll() is not None or 'panic' in (out / 'candidate.log').read_text())
                result.update({'outcome': 'candidate-error' if crashed else 'infrastructure-blocked', 'error': str(error)[:1000]})
            except (subprocess.SubprocessError, RuntimeError, OSError) as error:
                result.update({'outcome': 'candidate-error', 'error': str(error)[:1000]})
            finally:
                if server: stop(server)
    if db: db.close()
    if result['outcome'] in ('passed', 'acceptance-failed'):
        # Additional analysis only (never part of A/E/K/D): the private catalog-bound Verifier on this candidate.
        try:
            v = verifier()
            report = v.check(v.build(p['task'], 'private', out), candidate, json.loads(Path(args.manifest).read_text())['initial_sha'])
            if report['catalog'] != p['verifier']['catalog_digest']: report = {'error': 'verifier catalog differs from plan', **report}
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            report = {'error': str(error)[:500]}
        result['verifier'] = report
    for f in (binary, out / 'dbrpc', out / 'appctl-private'): f.unlink(missing_ok=True)
    load_plan(args.plan); validate_candidate(args, p)
    result['evaluation_seconds'] = round(time.monotonic() - start, 3)
    save(out / 'result.json', result)
    print(json.dumps({'trial': t['id'], 'outcome': result['outcome'], 'cases': len(result['cases']),
                      'explicit': result.get('explicit'), 'implicit': result.get('implicit'),
                      'safety': {k: v['count'] for k, v in result.get('safety', {}).items() if v['count']}}))
    return 0 if result['passed'] else (3 if result['outcome'] in UNMEASURED else 2)


def summarize(args):
    p = load_plan(args.plan); known = {t['id']: t for t in p['trials']}; records = {}
    for directory in args.inputs:
        if not Path(directory).is_dir(): raise ValueError('result directory does not exist: ' + str(directory))
        for path in Path(directory).rglob('result.json'):
            r = json.loads(path.read_text()); ident = r['trial']['id']
            # Version 3 records are worker generation outcomes; version 7 are evaluations.
            if r.get('version') not in (3, 7) or r['kind'] != p['kind'] or r['plan_sha256'] != digest(args.plan) or r['trial'] != known.get(ident):
                raise ValueError('foreign trial/cohort')
            if ident in records: raise ValueError('duplicate/conflicting trial: ' + ident)
            if r['outcome'] not in OBSERVED | UNMEASURED: raise ValueError('unknown outcome')
            if (r.get('passed') is True) != (r['outcome'] == 'passed'): raise ValueError('inconsistent pass flag')
            if r['outcome'] in ('passed', 'acceptance-failed'):
                if r['version'] != 7: raise ValueError('acceptance outcome without evaluation')
                scored = score(p['task'], r.get('cases', []))
                if any(r.get(k) != v for k, v in scored.items()): raise ValueError('score mismatch')
            records[ident] = r
    rows = []
    for c in CELLS:
        selected = [t for t in p['trials'] if t['cell'] == c]
        values = [records[t['id']] for t in selected if t['id'] in records]
        observed = [r for r in values if r['outcome'] in OBSERVED]
        passed = sum(r['outcome'] == 'passed' for r in values)
        credits = [r.get('generation', {}).get('usage', {}) or {} for r in observed]
        credits = [u.get('credits') for u in credits]
        seconds = [r.get('generation', {}).get('generation_seconds') for r in observed]
        known_credits = None if any(x is None for x in credits) else round(sum(credits), 4)
        known_seconds = None if any(x is None for x in seconds) else round(sum(seconds), 3)
        rows.append({'cell': c, 'planned': len(selected), 'observed': len(observed), 'unmeasured': len(selected) - len(observed),
                     'A_passed_all': passed, 'E_explicit': sum(bool(r.get('explicit_passed')) for r in values),
                     'K_conventions': sum(bool(r.get('conventions_passed')) for r in values),
                     'D_outbox': sum(bool(r.get('outbox_passed')) for r in values),
                     'conventions_followed': {g: sum(bool(r.get('conventions', {}).get(g)) for r in values)
                                              for g, v in contract(p['task'])['groups'].items() if v['kind'] == 'implicit'},
                     'verifier_pass': sum(r.get('verifier', {}).get('verdict') == 'pass' for r in values),
                     'safety_trials': {k: sum(bool(r.get('safety', {}).get(k, {}).get('count')) for r in values) for k in SAFETY},
                     'outcomes': dict(Counter(r['outcome'] for r in values)),
                     'credits_total': known_credits, 'generation_seconds_total': known_seconds,
                     'credits_per_correct': None if known_credits is None or not passed else round(known_credits / passed, 4),
                     'seconds_per_correct': None if known_seconds is None or not passed else round(known_seconds / passed, 3)})
    value = {'version': 7, 'kind': p['kind'], 'task': p['task'], 'plan_sha256': digest(args.plan), 'rows': rows,
             'note': 'A/E/K/D and safety are reported separately. Cost per correct result = total over observed trials / passed trials '
                     '(unknown if any observed trial lacks usage). Synthetic controls are not model evidence.'}
    if args.output: save(args.output, value)
    print(json.dumps(value, indent=2))


def main():
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest='command', required=True)
    s = sub.add_parser('fixtures'); s.add_argument('--output', required=True)
    s = sub.add_parser('plan')
    for n in ('model', 'fixtures', 'output'): s.add_argument('--' + n, required=True)
    s.add_argument('--task', choices=TASKS, required=True)
    s.add_argument('--repetitions', type=int, default=1); s.add_argument('--time-budget', type=int, default=600)
    s.add_argument('--reasoning-effort', choices=('low', 'medium', 'high'), default='medium'); s.add_argument('--seed', type=int, default=20261116)
    s.add_argument('--engine', choices=('codex', 'kiro'), default='kiro')
    s.add_argument('--synthetic', action='store_true'); s.add_argument('--allow-dirty-harness', action='store_true')
    s = sub.add_parser('prepare')
    for n in ('plan', 'trial', 'output'): s.add_argument('--' + n, required=True)
    for name in ('export', 'restore', 'evaluate'):
        s = sub.add_parser(name)
        for n in ('plan', 'trial', 'candidate', 'manifest'): s.add_argument('--' + n, required=True)
        if name in ('restore', 'evaluate'): s.add_argument('--receipt', required=True)
        if name == 'restore': s.add_argument('--bundle', required=True)
        if name == 'evaluate':
            for n in ('fixtures', 'output'): s.add_argument('--' + n, required=True)
    s = sub.add_parser('summarize'); s.add_argument('--plan', required=True); s.add_argument('--inputs', action='append', required=True); s.add_argument('--output')
    args = parser.parse_args()
    try:
        return {'fixtures': fixtures, 'plan': plan, 'prepare': prepare, 'export': export_candidate, 'restore': restore,
                'evaluate': evaluate, 'summarize': summarize}[args.command](args) or 0
    except Exception as error:
        print('v7 error: ' + str(error), file=sys.stderr); return 1


if __name__ == '__main__':
    sys.exit(main())
