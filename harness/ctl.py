#!/usr/bin/env python3
"""v8a private controller: fixtures / plan / prepare / export / restore / evaluate / summarize.

v8a asks how much of the v7 gain survives when an Agent drafts the catalog entry itself, and how much an
automatic AI review recovers. Four cells per block:

  manual          hand-written, as v7 go-manual (public phase 3 worker, one session)
  given           the reviewed entry is in internal/opdef, as v7 go-action-opdef (public worker, one session)
  drafted         session 1 drafts the entry (only internal/opdef/catalog.go); it must pass opdef.Validate()
                  or the trial ends (draft-invalid); session 2 (same model, fresh session) writes the adapter
                  against the frozen catalog
  drafted-review  as drafted, with one fixed reviewer session (claude-opus-5.5) between the two

Single-session kits run the public worker unchanged. Staged kits run harness/stages.py, which drives the
public worker's own invoke()/parse_kiro() per stage (raw ACP events and usage kept per stage) and commits
each stage; the receipt lists every stage commit. Kits carry a per-cell view of the plan without anything
derived from the reference entry (see view()). Everything that defines a trial is frozen by hash in the plan.
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
STAGES = 'harness/stages.py'
CHECK = 'harness/catalogcheck.py'
PROMPTS = 'harness/prompts.json'
# Provenance only: the public commit the vendored base sources (harness/source/common) were copied from.
PUBLIC_BASE = '71a213de04c4644b837184b1888ff22b77c5090d'
VERSION = 8
TASKS = ('return', 'delivery-site')
CELLS = ('manual', 'given', 'drafted', 'drafted-review')
STAGED = ('drafted', 'drafted-review')
OBSERVED = {'passed', 'acceptance-failed', 'candidate-error', 'budget-exceeded', 'scope-violation', 'draft-invalid'}
UNMEASURED = {'runtime-blocked', 'provider-error', 'protocol-error', 'infrastructure-blocked'}
SAFETY = ('unauthorized_write', 'partial_batch', 'side_effect_atomicity', 'double_apply', 'wrong_commit',
          'service_error', 'undecidable')
CATALOG = 'internal/opdef/catalog.go'
C, M, O = 'harness/source/common/', 'harness/source/go-manual/', 'harness/source/go-action-opdef/'
RECEIPT_STAGED = 81  # receipt version written by harness/stages.py (3 is the public worker's)


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
def prompts(): return json.loads((ROOT / PROMPTS).read_text())


def platform(task):
    """The platform version of a task: public fad8adf opdef/opkernel, or the task's column overlay."""
    d = 'tasks/%s/platform/' % task if (ROOT / 'tasks' / task / 'platform').is_dir() else 'harness/source/platform/'
    return {'internal/opdef/opdef.go': d + 'opdef.go.txt', 'internal/opkernel/opkernel.go': d + 'opkernel.go.txt'}


def reference_catalog(task): return 'tasks/%s/catalog.go.txt' % task
def base_catalog(): return 'harness/source/platform/catalog.go.txt'  # the public five entries (fad8adf)


def layout(task, cell):
    """Export path -> repository template for one task and cell (drafted and drafted-review are identical)."""
    T = 'tasks/%s/' % task
    files = {'go.mod': C + 'go.mod.txt', 'go.sum': C + 'go.sum.txt', 'cmd/server/main.go': C + 'main.go.txt',
             'internal/app/server.go': C + 'server.go.txt', 'internal/app/demo_identity.go': C + 'demo_identity.go.txt',
             'internal/protocol/protocol.go': C + 'protocol.go.txt', 'internal/ledger/ledger.go': C + 'ledger.go.txt',
             'internal/ledger/reconcile.go': T + 'reconcile.go.txt', 'internal/notify/notify.go': T + 'notify.go.txt'}
    if cell == 'manual':
        files.update({'internal/candidate/' + n + '.go': M + n + '.go.txt' for n in ('approval', 'reject', 'reopen', 'countersign', 'reassign')})
    else:
        files.update(platform(task))
        files.update({CATALOG: reference_catalog(task) if cell == 'given' else base_catalog(),
                      'internal/candidate/approval.go': O + 'approval.go.txt'})
    return files


def schema_files(task): return ['harness/sql/bootstrap.sql', 'harness/sql/base.sql', 'tasks/%s/schema.sql' % task]


def files(task, cell):
    data = {path: (ROOT / src).read_bytes() for path, src in layout(task, cell).items()}
    data['TASK.md'] = (contract(task)['requirements'] + '\n').encode()
    data['SCHEMA.sql'] = b'\n'.join((ROOT / p).read_bytes() for p in schema_files(task)[1:])
    data['.gitignore'] = b'bin/\n/server\n*.log\n__pycache__/\n'
    return data


def fill(lines, task):
    c = contract(task)
    return [line.format(title=c['title'], adapter=c['adapter'], existing=c['existing']) for line in lines]


def prompt(task, kind):
    """kind: manual | given | draft | review | adapter (the adapter stage gets exactly the given prompt)."""
    P, req = prompts(), contract(task)['requirements']
    if kind in ('manual', 'given', 'adapter'):
        cap = P['capability']['given' if kind == 'adapter' else kind]
        return '\n'.join([req, *fill(P['common'], task), *fill([cap], task), P['tail']])
    return '\n'.join([req, *fill(P[kind], task), P['tail']])


def stage_specs(task, cell, model, effort, budget):
    """The ordered sessions of a staged cell; editable: 'catalog' (internal/opdef/catalog.go) or 'candidate'."""
    P = prompts()
    names = ['draft', 'review', 'adapter'] if cell == 'drafted-review' else ['draft', 'adapter']
    out = []
    for n in names:
        text = prompt(task, n)
        reviewer = n == 'review'
        out.append({'name': n, 'model': P['reviewer_model'] if reviewer else model,
                    'reasoning_effort': P['reviewer_reasoning_effort'] if reviewer else effort,
                    'editable': 'candidate' if n == 'adapter' else 'catalog', 'budget_seconds': budget,
                    'prompt': text, 'prompt_sha256': sha(text.encode())})
    return out


def editable(kind, name):
    return name == CATALOG if kind == 'catalog' else (name.startswith('internal/candidate/') and name.endswith('.go'))


def frozen_paths(task):
    tracked = git(ROOT, 'ls-files', 'harness', 'tasks/' + task, 'benchmark').splitlines()
    for p in (WORKER, STAGES, CHECK, PROMPTS):
        assert p in tracked, p + ' must be tracked'
    return tracked


def trials(plan, ident=None):
    selected = [t for t in plan['trials'] if ident is None or t['id'] == ident]
    if not selected: raise ValueError('trial not in plan')
    return selected


def view(p, cell):
    """The plan as a kit of `cell` sees it. It keeps what the workers check (identity, trials, own export,
    worker/stage hashes, budgets) and drops everything derived from the reference entry: other cells' export
    hashes (the given cell's catalog.go), the hashes of tasks/ and harness/ files (the reference catalog,
    Oracle, controls) and the Verifier binding (reference catalog digest). A plain hash of the reference
    catalog would let a drafting session test guesses offline."""
    kit = [WORKER] + ([STAGES, CHECK] if cell in STAGED else [])
    return {**{k: v for k, v in p.items() if k not in ('frozen', 'exports', 'verifier', 'fixtures_sha256')},
            'view': cell, 'frozen': {k: p['frozen'][k] for k in kit}, 'exports': {cell: p['exports'][cell]}}


def view_bytes(p, cell): return (json.dumps(view(p, cell), indent=2, ensure_ascii=False) + '\n').encode()
def view_sha(p, cell): return sha(view_bytes(p, cell))


def load_plan(path):
    value = json.loads(Path(path).read_text())
    if value.get('version') != VERSION or value.get('experiment') != 'v8a' or value.get('kind') not in ('model-trial', 'synthetic-smoke') \
            or value.get('task') not in TASKS:
        raise ValueError('v8a cohort plan required')
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
        if t['cell'] in STAGED:
            want = stage_specs(value['task'], t['cell'], value['model'], value['reasoning_effort'], value['generation_budget_seconds'])
            if t.get('stages') != want or 'prompt' in t: raise ValueError('stage/prompt tamper')
        elif t['prompt'] != prompt(value['task'], t['cell']) or sha(t['prompt'].encode()) != t['prompt_sha256'] or 'stages' in t:
            raise ValueError('prompt tamper')
    if not blocks or sorted(blocks) != list(range(1, len(blocks) + 1)) or any(b != set(CELLS) for b in blocks.values()):
        raise ValueError('incomplete four-cell block')
    if value['exports'] != {c: {p: sha(d) for p, d in files(value['task'], c).items()} for c in CELLS}: raise ValueError('export drift')
    v = verifier().manifest(value['task'])
    if {k: value['verifier'][k] for k in v} != v or value['verifier'].get('reference_catalog_digest') != reference(value['task'])['digest']:
        raise ValueError('verifier binding drift')
    return value


def verifier(): return module('v8a_verifier', HERE / 'verifier.py')
def checker(): return module('v8a_catalogcheck', ROOT / CHECK)
def entry_oracle(): return module('v8a_entry_oracle', HERE / 'entry_oracle.py')


def compile_catalog(task, catalog_bytes):
    return checker().check((ROOT / platform(task)['internal/opdef/opdef.go']).read_bytes(), catalog_bytes, go())


def reference(task):
    r = compile_catalog(task, (ROOT / reference_catalog(task)).read_bytes())
    if not r['valid']: raise ValueError('reference catalog does not validate: %s' % r['error'])
    return r


def database_env():
    """Measured evaluation database (version, collation, ctype) plus the operator-declared image (POC_PG_IMAGE)."""
    import tempfile
    oracle = module('v8a_oracle_env', HERE / 'oracle.py')
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
    value = {'version': VERSION, 'experiment': 'v8a', 'kind': 'synthetic-smoke' if args.synthetic else 'model-trial', 'task': args.task,
             'operation': contract(args.task)['operation'], 'cells': list(CELLS), 'base_sha': PUBLIC_BASE,
             'harness_sha': git(ROOT, 'rev-parse', 'HEAD'),
             'frozen': {p: digest(ROOT / p) for p in frozen_paths(args.task)}, 'fixtures_sha256': digest(args.fixtures),
             'seed': args.seed, 'model': args.model, 'reasoning_effort': args.reasoning_effort, 'engine': args.engine,
             'generation_budget_seconds': args.time_budget, 'trials': [],
             'isolation': 'operator must isolate generation from evaluator; commands alone do not enforce it'}
    value['exports'] = {c: {p: sha(d) for p, d in files(args.task, c).items()} for c in CELLS}
    # Frozen here: the Verifier rules and platform version (public commit + overlay hashes) and the reference
    # catalog digest. A drafted trial's catalog differs per trial: its digest is recorded per trial at evaluation.
    value['verifier'] = {**verifier().manifest(args.task), 'reference_catalog_digest': reference(args.task)['digest']}
    value['database'] = database_env()
    for instance in range(1, args.repetitions + 1):
        block = []
        for c in CELLS:
            t = {'id': c + '--' + str(instance).zfill(3), 'cell': c, 'instance': instance}
            if c in STAGED:
                t['stages'] = stage_specs(args.task, c, args.model, args.reasoning_effort, args.time_budget)
            else:
                text = prompt(args.task, c)
                t.update(prompt=text, prompt_sha256=sha(text.encode()))
            block.append(t)
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
    process(['git', '-c', 'user.name=POC Export', '-c', 'user.email=poc@example.invalid', 'commit', '-q', '-m', 'v8a shared base'], cwd=candidate)
    save(manifest, {'version': VERSION, 'trial_id': t['id'], 'plan_sha256': view_sha(p, t['cell']), 'initial_sha': git(candidate, 'rev-parse', 'HEAD'),
                    'source_base_sha': p['base_sha'], 'files': p['exports'][t['cell']]})


def prepare(args):
    """A per-trial kit: the cell's plan view, no seed/Oracle/reference; worker = public phase 3 worker (+ stage wrapper)."""
    p = load_plan(args.plan); t = trials(p, args.trial)[0]; out = Path(args.output).resolve()
    if out == ROOT or out.is_relative_to(ROOT): raise ValueError('generation kit must be outside evaluator checkout')
    out.mkdir(parents=True, exist_ok=False)
    export_candidate(argparse.Namespace(plan=args.plan, trial=args.trial, candidate=out / 'candidate', manifest=out / 'manifest.json'))
    (out / 'plan.json').write_bytes(view_bytes(p, t['cell']))
    (out / 'worker.py').write_bytes((ROOT / WORKER).read_bytes())
    job = {'version': VERSION, 'kind': p['kind'], 'plan_sha256': digest(out / 'plan.json'), 'trial': t,
           'manifest_sha256': digest(out / 'manifest.json'), 'worker_sha256': digest(out / 'worker.py'),
           'model': p['model'], 'reasoning_effort': p['reasoning_effort'],
           'generation_budget_seconds': p['generation_budget_seconds'], 'engine': p['engine']}
    if t['cell'] in STAGED:
        (out / 'stages.py').write_bytes((ROOT / STAGES).read_bytes())
        (out / 'catalogcheck.py').write_bytes((ROOT / CHECK).read_bytes())
        job.update(stages_sha256=digest(out / 'stages.py'), catalogcheck_sha256=digest(out / 'catalogcheck.py'))
    save(out / 'job.json', job)


def chain(candidate, initial, r, t):
    """Stage commits of a staged receipt: each the single child of the previous, each changing only its editable paths."""
    parent, commits = initial, []
    for s, spec in zip(r['stages'], t['stages']):
        if s['name'] != spec['name']: raise ValueError('stage order mismatch')
        if not s.get('commit'): break
        if git(candidate, 'rev-parse', s['commit'] + '^') != parent or git(candidate, 'rev-list', '--count', s['commit']) != str(len(commits) + 2):
            raise ValueError('stage commit chain violation: ' + s['name'])
        changed = git(candidate, 'diff', '--name-only', parent, s['commit']).splitlines()
        if any(not editable(spec['editable'], n) for n in changed): raise ValueError('stage scope violation: ' + s['name'])
        commits.append((s['name'], s['commit'])); parent = s['commit']
    return commits


def validate_candidate(args, p, r):
    candidate = Path(args.candidate).resolve(); manifest = Path(args.manifest).resolve(); t = trials(p, args.trial)[0]
    if candidate == ROOT or candidate.is_relative_to(ROOT) or manifest.is_relative_to(candidate):
        raise ValueError('invalid candidate/attestation boundary')
    m = json.loads(manifest.read_text()); initial = m['initial_sha']
    if (m.get('version') != VERSION or m['trial_id'] != t['id'] or m['plan_sha256'] != view_sha(p, t['cell'])
            or m['source_base_sha'] != p['base_sha'] or m['files'] != p['exports'][t['cell']]):
        raise ValueError('export attestation mismatch')
    if git(candidate, 'rev-list', '--count', initial) != '1' or git(candidate, 'remote'): raise ValueError('export history/remote violation')
    if set(git(candidate, 'ls-tree', '-r', '--name-only', initial).splitlines()) != set(m['files']): raise ValueError('unexpected base files')
    for name, h in m['files'].items():
        if sha(subprocess.check_output(['git', '-C', str(candidate), 'show', initial + ':' + name])) != h: raise ValueError('base snapshot tamper')
    process(['git', '-C', candidate, 'merge-base', '--is-ancestor', initial, 'HEAD'])
    if git(candidate, 'status', '--porcelain'): raise ValueError('candidate must be committed/clean')
    for line in git(candidate, 'ls-tree', '-r', 'HEAD').splitlines():
        if line.split()[0] != '100644': raise ValueError('only regular non-executable source files allowed')
    if t['cell'] in STAGED:
        commits = chain(candidate, initial, r, t)
        if not commits or git(candidate, 'rev-parse', 'HEAD') != commits[-1][1]: raise ValueError('staged head mismatch')
        return candidate, commits
    if git(candidate, 'rev-list', '--count', 'HEAD') != '2': raise ValueError('single-session history violation')
    if any(not editable('candidate', n) for n in git(candidate, 'diff', '--name-only', initial, 'HEAD').splitlines()):
        raise ValueError('protected capability/export modification')
    return candidate, [('generation', git(candidate, 'rev-parse', 'HEAD'))]


def receipt(args, p):
    r = json.loads(Path(args.receipt).read_text()); t = trials(p, args.trial)[0]
    staged = t['cell'] in STAGED
    if (r.get('version') != (RECEIPT_STAGED if staged else 3) or r['kind'] != p['kind'] or r['plan_sha256'] != view_sha(p, t['cell'])
            or r['trial'] != t or r['manifest_sha256'] != digest(args.manifest)):
        raise ValueError('generation receipt identity mismatch')
    if not staged and r['generation']['status'] != 'completed': raise ValueError('incomplete single-session receipt')
    if staged:
        names, planned = [s['name'] for s in r['stages']], [s['name'] for s in t['stages']]
        # A trial that ended early lists the stages that ran (the last one is where it ended).
        if r['outcome'] not in OBSERVED | UNMEASURED | {'generated'} or not names or names != planned[:len(names)] \
                or (r['outcome'] == 'generated') != (names == planned and all(s.get('commit') for s in r['stages'])):
            raise ValueError('invalid staged receipt')
    return r


def restore(args):
    p = load_plan(args.plan); r = receipt(args, p)
    if digest(args.bundle) != r['bundle_sha256']: raise ValueError('bundle checksum mismatch')
    candidate = Path(args.candidate).resolve()
    if candidate.exists() or candidate == ROOT or candidate.is_relative_to(ROOT): raise ValueError('invalid restore directory')
    process(['git', 'clone', '-q', args.bundle, candidate]); process(['git', 'remote', 'remove', 'origin'], cwd=candidate)
    if git(candidate, 'rev-parse', 'HEAD') != r['candidate_sha']: raise ValueError('restored head mismatch')
    validate_candidate(args, p, r)


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


def show(candidate, commit, path): return subprocess.check_output(['git', '-C', str(candidate), 'show', commit + ':' + path])


def entry_record(p, candidate, commits, r):  # noqa: C901
    """C for a staged trial: the frozen catalog (after review, if any) against the reference entry, plus the draft's own C."""
    ref, eo = reference(p['task']), entry_oracle()
    catalogs = {n: c for n, c in commits if n in ('draft', 'review')}
    out = {}
    for n, c in catalogs.items():
        data = show(candidate, c, CATALOG)
        check = compile_catalog(p['task'], data)
        out[n] = {'commit': c, 'catalog_sha256': sha(data), 'valid': check['valid'], 'error': check['error'], 'digest': check['digest'],
                  **{'C': eo.compare(p['operation'], check, ref)}}
        reported = next(s for s in r['stages'] if s['name'] == n).get('check') or {}
        out[n]['generation_side_valid'] = reported.get('valid')
    # The frozen entry is the reviewed one in drafted-review: without a completed review there is none (C = 0).
    frozen = 'review' if any(s['name'] == 'review' for s in r['stages']) else 'draft'
    frozen = frozen if frozen in out else None
    value = {'stages': out, 'frozen_stage': frozen, 'C': bool(frozen and out[frozen]['C']['correct']),
             'C_draft': bool('draft' in out and out['draft']['C']['correct'])}
    if 'review' in out:
        value['review_changed_catalog'] = out['review']['catalog_sha256'] != out['draft']['catalog_sha256']
    return value


def evaluate(args):
    p = load_plan(args.plan); t = trials(p, args.trial)[0]; r = receipt(args, p)
    candidate, commits = validate_candidate(args, p, r)
    if git(candidate, 'rev-parse', 'HEAD') != r['candidate_sha']: raise ValueError('candidate differs from generation receipt')
    if digest(args.fixtures) != p['fixtures_sha256']: raise ValueError('private seed differs from plan')
    private = json.loads(Path(args.fixtures).read_text())
    out = Path(args.output).resolve(); out.mkdir(parents=True, exist_ok=False)
    port = os.environ.get('POC_HTTP_PORT', '8080')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', int(port))) == 0: raise ValueError('evaluation port occupied')
    result = {'version': VERSION, 'experiment': 'v8a', 'task': p['task'],
              **{k: r[k] for k in ('kind', 'plan_sha256', 'trial', 'candidate_sha', 'manifest_sha256', 'generation')},
              'cases': [], 'passed': False}
    if t['cell'] in STAGED:
        result['stages'] = r['stages']
    staged_outcome = r.get('outcome', 'generated')
    oracle = module('v8a_oracle', HERE / 'oracle.py')
    server = None; db = None; binary = out / 'server'; start = time.monotonic()
    # The catalog this trial runs on: the reference (manual is bound to it too) or the trial's frozen draft.
    catalog = (ROOT / reference_catalog(p['task'])).read_bytes()
    base = json.loads(Path(args.manifest).read_text())['initial_sha']
    if t['cell'] in STAGED:
        result['entry'] = entry_record(p, candidate, commits, r)
        frozen = result['entry']['frozen_stage']
        if frozen:
            catalog = show(candidate, result['entry']['stages'][frozen]['commit'], CATALOG)
            base = result['entry']['stages'][frozen]['commit']
            host_valid = result['entry']['stages'][frozen]['valid']
            if (staged_outcome == 'draft-invalid') == host_valid:
                staged_outcome = 'infrastructure-blocked'
                result['error'] = 'generation-side and host catalog validity differ'
        else:  # e.g. the review session did not complete: there is no frozen catalog and the trial never ran
            catalog = show(candidate, [c for n, c in commits if n in ('draft', 'review')][-1], CATALOG)
            if staged_outcome == 'generated': raise ValueError('generated staged trial without a frozen catalog')
    result['catalog'] = {'sha256': sha(catalog), 'digest': compile_catalog(p['task'], catalog)['digest']}
    if staged_outcome != 'generated':
        result.update(outcome=staged_outcome)
    else:
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
        # Additional analysis only (never part of A/E/K/D/C): the private Verifier bound to this trial's catalog.
        try:
            v = verifier()
            report = v.check(v.build(p['task'], 'private', out, catalog), candidate, base)
            if report['catalog'] != result['catalog']['digest']: report = {'error': 'verifier catalog differs from the trial catalog', **report}
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            report = {'error': str(error)[:500]}
        result['verifier'] = report
    for f in (binary, out / 'dbrpc', out / 'appctl-private'): f.unlink(missing_ok=True)
    load_plan(args.plan); validate_candidate(args, p, r)
    result['evaluation_seconds'] = round(time.monotonic() - start, 3)
    save(out / 'result.json', result)
    print(json.dumps({'trial': t['id'], 'outcome': result['outcome'], 'cases': len(result['cases']),
                      'explicit': result.get('explicit'), 'implicit': result.get('implicit'),
                      'C': result.get('entry', {}).get('C'), 'wrong': (result.get('entry', {}).get('stages', {}).get(
                          result.get('entry', {}).get('frozen_stage') or '', {}).get('C') or {}).get('wrong'),
                      'safety': {k: v['count'] for k, v in result.get('safety', {}).items() if v['count']}}))
    return 0 if result['passed'] else (3 if result['outcome'] in UNMEASURED else 2)


def summarize(args):
    p = load_plan(args.plan); known = {t['id']: t for t in p['trials']}; records = {}
    for directory in args.inputs:
        if not Path(directory).is_dir(): raise ValueError('result directory does not exist: ' + str(directory))
        for path in Path(directory).rglob('result.json'):
            r = json.loads(path.read_text()); ident = r['trial']['id']
            # Version 3 records are worker generation outcomes, RECEIPT_STAGED stage-wrapper outcomes, VERSION evaluations.
            if r.get('version') not in (3, RECEIPT_STAGED, VERSION) or r['kind'] != p['kind'] or r['trial'] != known.get(ident) \
                    or r['plan_sha256'] != view_sha(p, r['trial']['cell']):
                raise ValueError('foreign trial/cohort')
            if ident in records: raise ValueError('duplicate/conflicting trial: ' + ident)
            if r['outcome'] not in OBSERVED | UNMEASURED: raise ValueError('unknown outcome')
            if (r.get('passed') is True) != (r['outcome'] == 'passed'): raise ValueError('inconsistent pass flag')
            if r['outcome'] in ('passed', 'acceptance-failed'):
                if r['version'] != VERSION: raise ValueError('acceptance outcome without evaluation')
                scored = score(p['task'], r.get('cases', []))
                if any(r.get(k) != v for k, v in scored.items()): raise ValueError('score mismatch')
            records[ident] = r
    rows = []
    for c in CELLS:
        selected = [t for t in p['trials'] if t['cell'] == c]
        values = [records[t['id']] for t in selected if t['id'] in records]
        observed = [r for r in values if r['outcome'] in OBSERVED]
        passed = sum(r['outcome'] == 'passed' for r in values)
        credits = [(r.get('generation', {}).get('usage') or {}).get('credits') for r in observed]
        seconds = [r.get('generation', {}).get('generation_seconds') for r in observed]
        known_credits = None if any(x is None for x in credits) else round(sum(credits), 4)
        known_seconds = None if any(x is None for x in seconds) else round(sum(seconds), 3)
        row = {'cell': c, 'planned': len(selected), 'observed': len(observed), 'unmeasured': len(selected) - len(observed),
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
               'seconds_per_correct': None if known_seconds is None or not passed else round(known_seconds / passed, 3)}
        if c in STAGED:
            row['C_entry_correct'] = sum(bool(r.get('entry', {}).get('C')) for r in observed)
            row['C_draft_correct'] = sum(bool(r.get('entry', {}).get('C_draft')) for r in observed)
            row['stage_credits'] = {s: round(sum(((x.get('generation') or {}).get('usage') or {}).get('credits') or 0
                                                 for r in values for x in r.get('stages', []) if x['name'] == s), 4)
                                    for s in ('draft', 'review', 'adapter')}
        rows.append(row)
    value = {'version': VERSION, 'experiment': 'v8a', 'kind': p['kind'], 'task': p['task'], 'rows': rows,
             'note': 'A/E/K/D, C and safety are reported separately. Cost per correct result = total over observed trials (all stages, '
                     'review included) / passed trials (unknown if any observed trial lacks usage). Synthetic controls are not model evidence.'}
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
        print('v8a error: ' + str(error), file=sys.stderr); return 1


if __name__ == '__main__':
    sys.exit(main())
