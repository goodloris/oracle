#!/usr/bin/env python3
"""v7 positive/negative/safety/crash controls on real PostgreSQL, plus static harness checks.

Every control is a variant of a cell's export (tasks/<task>/controls.json): files added under
internal/candidate/ and exact-once string edits, optionally on top of another variant. A control is
committed, bundled with a worker-format receipt, restored and evaluated through the normal ctl.py path,
then compared with its expectation: outcome, the exact set of failed case groups, and the exact set of
safety categories with a non-zero count.

Usage: controls.py --task withdraw --work /outside/repo/dir [--only name ...] [--allow-dirty]
Needs DATABASE_URL, ADMIN_DATABASE_URL (a disposable *_poc database), POC_HTTP_PORT and optionally POC_GO.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'harness'))
import ctl  # noqa: E402

PY = sys.executable


def run(args, **kw):
    return subprocess.run(list(map(str, args)), capture_output=True, text=True, **kw)


def variant(spec, name):
    v = spec['variants'][name]
    if 'base' in v:
        parent = variant(spec, v['base'])
        return {'cell': parent['cell'], 'add': {**parent['add'], **v.get('add', {})}, 'edits': parent['edits'] + v.get('edits', [])}
    return {'cell': v['cell'], 'add': v.get('add', {}), 'edits': v.get('edits', [])}


def materialize(task, v, candidate):
    for path, src in v['add'].items():
        assert path.startswith('internal/candidate/') and path.endswith('.go'), path
        (candidate / path).write_bytes((ROOT / 'tasks' / task / src).read_bytes())
    for e in v['edits']:
        path = candidate / e['file']
        text = path.read_text()
        assert text.count(e['old']) == 1, ('edit must match exactly once', e['file'], e['old'][:80])
        path.write_text(text.replace(e['old'], e['new']))


def freeze(work, plan, trial, candidate, manifest, directory):
    """Commit a hand-written candidate and emit a worker-format (version 3) receipt."""
    directory.mkdir(parents=True)
    initial = json.loads(manifest.read_text())['initial_sha']
    ctl.process(['git', 'add', '--all'], cwd=candidate)
    ctl.process(['git', '-c', 'user.name=POC v7', '-c', 'user.email=poc@example.invalid', 'commit', '-q', '--allow-empty', '-m', 'v7 control'], cwd=candidate)
    ctl.process(['git', 'bundle', 'create', '-q', directory / 'candidate.bundle', 'HEAD'], cwd=candidate)
    (directory / 'candidate.patch').write_bytes(subprocess.check_output(['git', '-C', str(candidate), 'diff', '--binary', initial, 'HEAD']))
    p = json.loads(plan.read_text())
    t = next(x for x in p['trials'] if x['id'] == trial)
    ctl.save(directory / 'receipt.json', {'version': 3, 'kind': p['kind'], 'plan_sha256': ctl.digest(plan), 'trial': t,
                                          'candidate_sha': ctl.git(candidate, 'rev-parse', 'HEAD'), 'manifest_sha256': ctl.digest(manifest),
                                          'bundle_sha256': ctl.digest(directory / 'candidate.bundle'),
                                          'generation': {'status': 'completed', 'usage': {'credits': 0}, 'generation_seconds': 0,
                                                         'engine': 'control'}})


def evaluate_control(task, spec, name, work, plan, fixtures):
    v = variant(spec, name)
    base = work / 'controls' / name
    trial = v['cell'] + '--001'
    cand, manifest, gen = base / 'candidate', base / 'manifest.json', base / 'generation'
    r = run([PY, ROOT / 'harness/ctl.py', 'export', '--plan', plan, '--trial', trial, '--candidate', cand, '--manifest', manifest])
    assert r.returncode == 0, r.stderr
    materialize(task, v, cand)
    freeze(work, plan, trial, cand, manifest, gen)
    restored = base / 'restored'
    r = run([PY, ROOT / 'harness/ctl.py', 'restore', '--plan', plan, '--trial', trial, '--bundle', gen / 'candidate.bundle',
             '--receipt', gen / 'receipt.json', '--manifest', manifest, '--candidate', restored])
    assert r.returncode == 0, r.stderr
    r = run([PY, ROOT / 'harness/ctl.py', 'evaluate', '--plan', plan, '--trial', trial, '--candidate', restored, '--manifest', manifest,
             '--receipt', gen / 'receipt.json', '--fixtures', fixtures, '--output', base / 'evaluation'], timeout=900)
    assert (base / 'evaluation' / 'result.json').exists(), r.stderr[-2000:]
    return json.loads((base / 'evaluation' / 'result.json').read_text())


def verifier_check(public, control, result, restored, initial):
    """Private Verifier: references must pass. Public Verifier (public catalog): must never pass a v7 candidate;
    on a go-action-opdef candidate it must report CATALOG_DRIFT."""
    import verifier
    private = result.get('verifier') or {}
    pub = verifier.check(public, restored, initial)
    ok = pub['verdict'] != 'pass' and pub['exit'] != 0
    if control['cell'] == 'go-action-opdef': ok = ok and 'CATALOG_DRIFT' in pub['codes']
    if control['kind'] == 'reference': ok = ok and private.get('verdict') == 'pass'
    return ok, {'private': {k: private.get(k) for k in ('exit', 'verdict', 'codes', 'error') if k in private},
                'public': {k: pub.get(k) for k in ('exit', 'verdict', 'codes')}}


def compare(expect, result):
    failed = sorted({c['group'] for c in result.get('cases', []) if not c['passed']})
    flagged = sorted(k for k, v in result.get('safety', {}).items() if v['count'])
    got = {'outcome': result['outcome'], 'failed_groups': failed, 'safety': flagged}
    want = {'outcome': expect['outcome'], 'failed_groups': sorted(expect.get('failed_groups', [])), 'safety': sorted(expect.get('safety', []))}
    if expect['outcome'] == 'candidate-error':  # cases are not scored after a crash
        got.pop('failed_groups'); got.pop('safety'); want.pop('failed_groups'); want.pop('safety')
    return got == want, got, want


def static_checks(task):
    """Harness properties that need no database: clues, task text, prompts, document equivalence, kit contents."""
    c = ctl.contract(task)
    out = {}
    exports = {cell: ctl.files(task, cell) for cell in ctl.CELLS}
    missing = []
    for g, conv in c['conventions'].items():
        for cell, sources in conv['sources'].items():
            cells = [cell] + (['go-manual-doc'] if cell == 'go-manual' else [])
            for target in cells:
                for path, text in sources:
                    if text not in exports[target].get(path, b'').decode():
                        missing.append([g, target, path, text])
    out['clues_present'] = not missing
    if missing: out['missing_clues'] = missing
    out['task_text_has_no_forbidden_words'] = not [w for w in c['forbidden_in_task'] if w.lower() in c['requirements'].lower()]
    prompts = {cell: ctl.prompt(task, cell).splitlines() for cell in ctl.CELLS}
    lines = [set(p) for p in prompts.values()]
    out['prompts_differ_only_in_capability'] = all(len(p) == len(prompts['go-manual']) for p in prompts.values()) and \
        set.intersection(*lines) == set(prompts['go-manual']) - {c['capability']['go-manual']}
    m, d = exports['go-manual'], exports['go-manual-doc']
    out['doc_cell_is_manual_plus_document'] = set(d) - set(m) == {'docs/OPERATIONS.md'} and all(d[k] == v for k, v in m.items())
    r = run([PY, ROOT / 'harness/opdoc.py', task, '--check'], timeout=300)
    out['document_equals_catalog'] = r.returncode == 0
    allowed = ('go.mod', 'go.sum', 'TASK.md', 'SCHEMA.sql', '.gitignore', 'cmd/', 'internal/', 'docs/OPERATIONS.md')
    names = {n for e in exports.values() for n in e}
    out['kits_hold_only_service_sources'] = all(n.startswith(allowed) for n in names) and \
        not any('oracle' in n or 'seed' in n or 'controls' in n for n in names)
    other = [t for t in ctl.TASKS if t != task][0]
    leak = ctl.contract(other)['operation']
    out['no_other_task_operation_in_exports'] = not any(leak.encode() in data for e in exports.values() for data in e.values())
    public = os.environ.get('PUBLIC_WORKER')
    if public:
        out['vendored_worker_equals_public'] = ctl.digest(public) == ctl.digest(ROOT / ctl.WORKER)
    return out


def write_solutions(task, directory):
    """Reference internal/candidate/ trees per cell, for the fake kiro-cli hand-off control (never a real kit)."""
    import tempfile
    spec = json.loads((ROOT / 'tasks' / task / 'controls.json').read_text())
    directory = Path(directory).resolve()
    if directory.is_relative_to(ROOT): raise SystemExit('solutions directory must be outside the private repository')
    for cell in ctl.CELLS:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for name, data in ctl.files(task, cell).items():
                (tmp / name).parent.mkdir(parents=True, exist_ok=True); (tmp / name).write_bytes(data)
            materialize(task, variant(spec, 'ref-' + cell), tmp)
            target = directory / cell / 'internal' / 'candidate'
            target.mkdir(parents=True)
            for f in (tmp / 'internal' / 'candidate').iterdir():
                (target / f.name).write_bytes(f.read_bytes())
    print(json.dumps({'task': task, 'solutions': str(directory), 'cells': list(ctl.CELLS)}))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--write-solutions', help='only write the reference solutions for the fake kiro-cli and exit')
    p.add_argument('--task', choices=ctl.TASKS, required=True)
    p.add_argument('--work')
    p.add_argument('--only', action='append')
    p.add_argument('--allow-dirty', action='store_true')
    p.add_argument('--output')
    a = p.parse_args()
    if a.write_solutions:
        return write_solutions(a.task, a.write_solutions) or 0
    work = Path(a.work).resolve()
    if work.is_relative_to(ROOT): raise SystemExit('work directory must be outside the private repository')
    work.mkdir(parents=True, exist_ok=False)
    spec = json.loads((ROOT / 'tasks' / a.task / 'controls.json').read_text())
    report = {'task': a.task, 'harness_sha': ctl.git(ROOT, 'rev-parse', 'HEAD'), 'dirty': bool(ctl.git(ROOT, 'status', '--porcelain')),
              'static': static_checks(a.task), 'controls': []}
    fixtures, plan = work / 'private' / 'fixtures.json', work / 'plan.json'
    assert run([PY, ROOT / 'harness/ctl.py', 'fixtures', '--output', fixtures]).returncode == 0
    r = run([PY, ROOT / 'harness/ctl.py', 'plan', '--task', a.task, '--model', 'control', '--fixtures', fixtures, '--output', plan,
             '--synthetic', *(['--allow-dirty-harness'] if a.allow_dirty else [])])
    assert r.returncode == 0, r.stderr
    import verifier
    (work / 'bin').mkdir()
    public = verifier.build(a.task, 'public', work / 'bin')
    for control in spec['controls']:
        if a.only and control['variant'] not in a.only: continue
        result = evaluate_control(a.task, spec, control['variant'], work, plan, fixtures)
        good, got, want = compare(control['expect'], result)
        control = {**control, 'cell': variant(spec, control['variant'])['cell']}
        initial = json.loads((work / 'controls' / control['variant'] / 'manifest.json').read_text())['initial_sha']
        vok, vgot = verifier_check(public, control, result, work / 'controls' / control['variant'] / 'restored', initial)
        good = good and vok
        entry = {'variant': control['variant'], 'kind': control['kind'], 'ok': good, 'got': got, 'verifier': vgot,
                 **({} if good else {'want': want}), 'evaluation_seconds': result.get('evaluation_seconds'),
                 'safety_cases': {k: v['cases'] for k, v in result.get('safety', {}).items() if v['count']}}
        report['controls'].append(entry)
        print(json.dumps({k: entry[k] for k in ('variant', 'ok', 'got')}, ensure_ascii=False), flush=True)
    report['all_ok'] = all(v for k, v in report['static'].items() if k != 'missing_clues') and all(c['ok'] for c in report['controls'])
    text = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    (work / 'controls-report.json').write_text(text)
    if a.output: Path(a.output).write_text(text)
    print(json.dumps({'task': a.task, 'static': report['static'], 'controls': len(report['controls']),
                      'failed': [c['variant'] for c in report['controls'] if not c['ok']], 'all_ok': report['all_ok']}, ensure_ascii=False))
    return 0 if report['all_ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
