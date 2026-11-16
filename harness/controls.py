#!/usr/bin/env python3
"""v8a controls: static harness checks, entry-correctness Oracle controls, real-PostgreSQL controls of the
full Oracle (all four cells, staged cells through their stage commits), Verifier assertions.

A control is a variant of a cell's export (tasks/<task>/controls.json): files added under internal/candidate/
and exact-once string edits, optionally on top of another variant. A staged variant lists its stages: catalog
stages set internal/opdef/catalog.go to the reference catalog plus edits (or leave it unchanged), the adapter
stage adds/edits internal/candidate/. Each stage is one commit; a stage-wrapper-format receipt (version 81) is
written with the same catalog check the kit runs (an invalid catalog ends the trial as draft-invalid). The
control is restored and evaluated through the normal ctl.py path and compared with its expectation: outcome,
the exact set of failed case groups, the exact set of non-zero safety categories, and for staged controls C,
C_draft and the wrong logical fields of the frozen entry.

Entry controls (tasks/<task>/entry_controls.json) need Go but no database: the reference entry and its
equivalent rewrites must be correct, and each single-field error variant must be wrong on exactly that field.

Usage: controls.py --task return --work /outside/repo/dir [--only name ...] [--allow-dirty] [--skip-db]
Needs DATABASE_URL, ADMIN_DATABASE_URL (a disposable *_poc database), POC_HTTP_PORT and optionally POC_GO.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

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
        return {**parent, 'add': {**parent.get('add', {}), **v.get('add', {})}, 'edits': parent.get('edits', []) + v.get('edits', [])}
    return {'add': {}, 'edits': [], **v}


def edit(text, edits, where):
    for e in edits:
        assert text.count(e['old']) == 1, ('edit must match exactly once', where, e['old'][:80])
        text = text.replace(e['old'], e['new'])
    return text


def materialize(task, v, candidate):
    for path, src in v.get('add', {}).items():
        assert path.startswith('internal/candidate/') and path.endswith('.go'), path
        (candidate / path).write_bytes((ROOT / 'tasks' / task / src).read_bytes())
    by = {}
    for e in v.get('edits', []):
        by.setdefault(e['file'], []).append(e)
    for f, es in by.items():
        (candidate / f).write_text(edit((candidate / f).read_text(), es, f))


def catalog_text(task, spec):
    """A catalog stage's catalog.go: the reference catalog plus edits."""
    return edit((ROOT / ctl.reference_catalog(task)).read_text(), spec.get('edits', []), 'catalog')


def commit(candidate, message):
    ctl.process(['git', 'add', '--all'], cwd=candidate)
    ctl.process(['git', '-c', 'user.name=POC v8a', '-c', 'user.email=poc@example.invalid', 'commit', '-q', '--allow-empty', '-m', message], cwd=candidate)
    return ctl.git(candidate, 'rev-parse', 'HEAD')


def generation(model='control'):
    return {'status': 'completed', 'usage': {'credits': 0}, 'generation_seconds': 0, 'engine': 'control', 'requested_model': model,
            'turn_started': True}


def freeze(task, plan, trial, candidate, manifest, directory, v):
    """Commit a hand-written candidate (one commit, or one per stage) and emit a worker-format receipt."""
    directory.mkdir(parents=True)
    initial = json.loads(manifest.read_text())['initial_sha']
    p = json.loads(plan.read_text())
    t = next(x for x in p['trials'] if x['id'] == trial)
    base = {'kind': p['kind'], 'plan_sha256': ctl.view_sha(p, t['cell']), 'trial': t, 'manifest_sha256': ctl.digest(manifest)}
    if t['cell'] in ctl.STAGED:
        opdef = (candidate / 'internal/opdef/opdef.go').read_bytes()
        stages, outcome = [], 'generated'
        for s in t['stages']:
            sv = next((x for x in v['stages'] if x['name'] == s['name']), {})
            record = {'name': s['name'], 'model': s['model'], 'generation': generation(s['model']), 'check': None}
            if s['editable'] == 'catalog':
                if not sv.get('unchanged'):
                    (candidate / ctl.CATALOG).write_text(catalog_text(task, sv))
            else:
                materialize(task, sv, candidate)
            record['commit'] = commit(candidate, 'v8a control stage ' + s['name'])
            stages.append(record)
            if s['editable'] == 'catalog':
                check = ctl.compile_catalog(task, (candidate / ctl.CATALOG).read_bytes())
                record['check'] = {k: v_ for k, v_ in check.items() if k != 'entries'}
                if not check['valid']:
                    outcome = 'draft-invalid'; break
        gen = {'status': 'completed', 'usage': {'credits': 0}, 'generation_seconds': 0, 'turn_started': True, 'stages': [s['name'] for s in stages]}
        receipt = {'version': ctl.RECEIPT_STAGED, **base, 'outcome': outcome, 'stages': stages, 'generation': gen}
    else:
        materialize(task, v, candidate)
        commit(candidate, 'v8a control')
        receipt = {'version': 3, **base, 'generation': generation()}
    ctl.process(['git', 'bundle', 'create', '-q', directory / 'candidate.bundle', 'HEAD'], cwd=candidate)
    (directory / 'candidate.patch').write_bytes(subprocess.check_output(['git', '-C', str(candidate), 'diff', '--binary', initial, 'HEAD']))
    ctl.save(directory / 'receipt.json', {**receipt, 'candidate_sha': ctl.git(candidate, 'rev-parse', 'HEAD'),
                                          'bundle_sha256': ctl.digest(directory / 'candidate.bundle')})


def evaluate_control(task, spec, name, work, plan, fixtures):
    v = variant(spec, name)
    base = work / 'controls' / name
    trial = v['cell'] + '--001'
    cand, manifest, gen = base / 'candidate', base / 'manifest.json', base / 'generation'
    r = run([PY, ROOT / 'harness/ctl.py', 'export', '--plan', plan, '--trial', trial, '--candidate', cand, '--manifest', manifest])
    assert r.returncode == 0, r.stderr
    freeze(task, plan, trial, cand, manifest, gen, v)
    restored = base / 'restored'
    r = run([PY, ROOT / 'harness/ctl.py', 'restore', '--plan', plan, '--trial', trial, '--bundle', gen / 'candidate.bundle',
             '--receipt', gen / 'receipt.json', '--manifest', manifest, '--candidate', restored])
    assert r.returncode == 0, r.stderr
    r = run([PY, ROOT / 'harness/ctl.py', 'evaluate', '--plan', plan, '--trial', trial, '--candidate', restored, '--manifest', manifest,
             '--receipt', gen / 'receipt.json', '--fixtures', fixtures, '--output', base / 'evaluation'], timeout=900)
    assert (base / 'evaluation' / 'result.json').exists(), r.stderr[-2000:]
    return v, json.loads((base / 'evaluation' / 'result.json').read_text())


def verifier_check(task, public, cell, kind, result, restored):
    """Private Verifier (bound to the trial's catalog, run by evaluate): references must pass. Public Verifier (public
    catalog): must never pass a v8a candidate; on a kernel-cell candidate it must report CATALOG_DRIFT."""
    import verifier
    if result['outcome'] not in ('passed', 'acceptance-failed'):
        return True, {'skipped': result['outcome']}
    private = result.get('verifier') or {}
    base = (result.get('entry') or {}).get('stages', {}).get((result.get('entry') or {}).get('frozen_stage') or '', {}).get('commit') \
        or ctl.git(restored, 'rev-list', '--max-parents=0', 'HEAD')
    pub = verifier.check(public, restored, base)
    ok = pub['verdict'] != 'pass' and pub['exit'] != 0
    if cell != 'manual': ok = ok and 'CATALOG_DRIFT' in pub['codes']
    if kind == 'reference': ok = ok and private.get('verdict') == 'pass' and private.get('catalog') == result['catalog']['digest']
    return ok, {'private': {k: private.get(k) for k in ('exit', 'verdict', 'codes', 'catalog', 'error') if k in private},
                'public': {k: pub.get(k) for k in ('exit', 'verdict', 'codes')}}


def compare(expect, result):
    failed = sorted({c['group'] for c in result.get('cases', []) if not c['passed']})
    flagged = sorted(k for k, v in result.get('safety', {}).items() if v['count'])
    got = {'outcome': result['outcome'], 'failed_groups': failed, 'safety': flagged}
    want = {'outcome': expect['outcome'], 'failed_groups': sorted(expect.get('failed_groups', [])), 'safety': sorted(expect.get('safety', []))}
    if expect['outcome'] not in ('passed', 'acceptance-failed'):  # cases are not scored after a crash or an invalid draft
        for k in ('failed_groups', 'safety'): got.pop(k); want.pop(k)
    if 'entry' in expect:
        e = result.get('entry') or {}
        st = e.get('stages', {}).get(e.get('frozen_stage') or '', {})
        got['entry'] = {'C': e.get('C'), 'C_draft': e.get('C_draft'), 'wrong': (st.get('C') or {}).get('wrong')}
        want['entry'] = {'C': expect['entry']['C'], 'C_draft': expect['entry'].get('C_draft', expect['entry']['C']),
                         'wrong': expect['entry'].get('wrong', [])}
    return got == want, got, want


# ---- entry-correctness Oracle controls (Go, no database) -----------------------------------------------------

def entry_controls(task):
    spec = json.loads((ROOT / 'tasks' / task / 'entry_controls.json').read_text())
    ref = ctl.reference(task)
    eo = ctl.entry_oracle()
    name = ctl.contract(task)['operation']
    out = []
    for c in spec['controls']:
        if c.get('source') == 'base':
            text = (ROOT / ctl.base_catalog()).read_text()
        else:
            text = catalog_text(task, c)
        check = ctl.compile_catalog(task, text.encode())
        r = eo.compare(name, check, ref)
        want = c['expect_wrong']
        ok = sorted(r['wrong']) == sorted(want) and r['correct'] == (not want) and \
            ('expect_valid' not in c or check['valid'] == c['expect_valid'])
        out.append({'name': c['name'], 'kind': c['kind'], 'ok': ok, 'wrong': r['wrong'], 'valid': check['valid'],
                    **({} if ok else {'want': want, 'error': check.get('error')})})
    fields = {f for c in spec['controls'] if c['kind'] == 'field-error' for f in c['expect_wrong']}
    return {'controls': out, 'fields_covered': sorted(fields), 'every_field_has_a_variant': fields == set(eo.FIELDS),
            'ok': all(x['ok'] for x in out) and fields == set(eo.FIELDS)}


# ---- static checks ---------------------------------------------------------------------------------------------

def static_checks(task):
    c = ctl.contract(task)
    out = {}
    exports = {cell: ctl.files(task, cell) for cell in ctl.CELLS}
    missing = []
    for g, conv in c['conventions'].items():
        for cell, sources in conv['sources'].items():
            for target in [x for x in ctl.CELLS if (x if x in ('manual', 'given') else 'drafted') == cell]:
                for path, text in sources:
                    if text not in exports[target].get(path, b'').decode():
                        missing.append([g, target, path, text])
    out['clues_present'] = not missing
    if missing: out['missing_clues'] = missing
    out['task_text_has_no_forbidden_words'] = not [w for w in c['forbidden_in_task'] if w.lower() in c['requirements'].lower()]
    manual, given = ctl.prompt(task, 'manual').splitlines(), ctl.prompt(task, 'given').splitlines()
    out['manual_given_prompts_differ_only_in_capability'] = len(manual) == len(given) and \
        [i for i, (a, b) in enumerate(zip(manual, given)) if a != b] == [len(manual) - 2]
    out['adapter_stage_prompt_equals_given_prompt'] = ctl.prompt(task, 'adapter') == ctl.prompt(task, 'given')
    out['every_prompt_starts_with_the_requirements'] = all(ctl.prompt(task, k).startswith(c['requirements'] + '\n')
                                                          for k in ('manual', 'given', 'draft', 'review', 'adapter'))
    d, g = exports['drafted'], exports['given']
    out['drafted_export_is_given_without_the_entry'] = exports['drafted-review'] == d and set(d) == set(g) and \
        [k for k in d if d[k] != g[k]] == [ctl.CATALOG] and ctl.sha(d[ctl.CATALOG]) == '736429c18304e9a46b602e25af2510b4cf4fdbe592f904e5d7f21d4a63a0db92'
    pub, ref = (ROOT / ctl.base_catalog()).read_text().splitlines(), (ROOT / ctl.reference_catalog(task)).read_text().splitlines()
    import difflib
    removed = [x for x in difflib.ndiff(pub, ref) if x.startswith('- ')]
    out['reference_catalog_is_public_plus_one_entry'] = not removed and len(ref) - len(pub) in (7, 8)
    r = ctl.reference(task)
    names = [e['name'] for e in r['entries']]
    out['reference_catalog_validates_with_the_task_entry'] = r['valid'] and c['operation'] in names and len(names) == 6
    plat = ctl.platform(task)
    import verifier
    out['platform_is_public_or_documented_overlay'] = all(
        ctl.digest(ROOT / src) == verifier.UPSTREAM[path] for path, src in plat.items()) if plat['internal/opdef/opdef.go'].startswith('harness/') \
        else all(ctl.digest(ROOT / src) != verifier.UPSTREAM[path] for path, src in plat.items())
    allowed = ('go.mod', 'go.sum', 'TASK.md', 'SCHEMA.sql', '.gitignore', 'cmd/', 'internal/')
    names = {n for e in exports.values() for n in e}
    out['kits_hold_only_service_sources'] = all(n.startswith(allowed) for n in names) and \
        not any('oracle' in n or 'seed' in n or 'controls' in n for n in names)
    other = [t for t in ctl.TASKS if t != task][0]
    leak = ctl.contract(other)['operation'].encode()
    out['no_other_task_operation_in_exports'] = not any(leak in data for e in exports.values() for data in e.values())
    # The entry itself must not leak into what a drafting kit sees: export, plan view.
    entry = next(e for e in r['entries'] if e['name'] == c['operation'])
    secrets_ = [entry['event'], ctl.sha((ROOT / ctl.reference_catalog(task)).read_bytes()), r['digest'], ctl.sha(g[ctl.CATALOG])]
    out['drafted_export_has_no_entry'] = c['operation'].encode() not in d[ctl.CATALOG]
    fake = {'version': ctl.VERSION, 'experiment': 'v8a', 'kind': 'synthetic-smoke', 'task': task, 'frozen': {p: ctl.digest(ROOT / p) for p in ctl.frozen_paths(task)},
            'exports': {cell: {p: ctl.sha(x) for p, x in exports[cell].items()} for cell in ctl.CELLS}, 'verifier': {'reference_catalog_digest': r['digest']},
            'fixtures_sha256': 'f' * 64,
            'trials': [{'id': x + '--001', 'cell': x, **({'stages': ctl.stage_specs(task, x, 'm', 'medium', 600)} if x in ctl.STAGED
                                                       else {'prompt': ctl.prompt(task, x)})} for x in ctl.CELLS]}
    views = {cell: ctl.view_bytes(fake, cell).decode() for cell in ctl.STAGED + ('manual',)}
    out['kit_plan_views_hold_nothing_derived_from_the_entry'] = not [s for s in secrets_ for text in views.values() if s in text]
    public = os.environ.get('PUBLIC_WORKER')
    if public:
        out['vendored_worker_equals_public'] = ctl.digest(public) == ctl.digest(ROOT / ctl.WORKER)
    return out


def write_solutions(task, directory):
    """Reference trees for the fake kiro-cli hand-off control (never a real kit): <cell>/generation for single-session
    cells; <cell>/draft (catalog), <cell>/adapter (internal/candidate) for staged cells, and no review files (the
    review accepts the draft), so every trial must pass."""
    spec = json.loads((ROOT / 'tasks' / task / 'controls.json').read_text())
    directory = Path(directory).resolve()
    if directory.is_relative_to(ROOT): raise SystemExit('solutions directory must be outside the private repository')
    for cell in ctl.CELLS:
        v = variant(spec, 'ref-' + cell)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for name, data in ctl.files(task, cell).items():
                (tmp / name).parent.mkdir(parents=True, exist_ok=True); (tmp / name).write_bytes(data)
            if cell in ctl.STAGED:
                draft = next(s for s in v['stages'] if s['name'] == 'draft')
                target = directory / cell / 'draft' / 'internal' / 'opdef'
                target.mkdir(parents=True); (target / 'catalog.go').write_text(catalog_text(task, draft))
                materialize(task, next(s for s in v['stages'] if s['name'] == 'adapter'), tmp)
                target = directory / cell / 'adapter' / 'internal' / 'candidate'
            else:
                materialize(task, v, tmp)
                target = directory / cell / 'generation' / 'internal' / 'candidate'
            target.mkdir(parents=True)
            for f in (tmp / 'internal' / 'candidate').iterdir():
                if f.read_bytes() != ctl.files(task, cell).get('internal/candidate/' + f.name):
                    (target / f.name).write_bytes(f.read_bytes())
    print(json.dumps({'task': task, 'solutions': str(directory), 'cells': list(ctl.CELLS)}))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--write-solutions', help='only write the reference solutions for the fake kiro-cli and exit')
    p.add_argument('--task', choices=ctl.TASKS, required=True)
    p.add_argument('--work')
    p.add_argument('--only', action='append')
    p.add_argument('--allow-dirty', action='store_true')
    p.add_argument('--skip-db', action='store_true', help='static and entry controls only')
    p.add_argument('--output')
    a = p.parse_args()
    if a.write_solutions:
        return write_solutions(a.task, a.write_solutions) or 0
    work = Path(a.work).resolve()
    if work.is_relative_to(ROOT): raise SystemExit('work directory must be outside the private repository')
    work.mkdir(parents=True, exist_ok=False)
    spec = json.loads((ROOT / 'tasks' / a.task / 'controls.json').read_text())
    report = {'task': a.task, 'harness_sha': ctl.git(ROOT, 'rev-parse', 'HEAD'), 'dirty': bool(ctl.git(ROOT, 'status', '--porcelain')),
              'static': static_checks(a.task), 'entry_oracle': entry_controls(a.task), 'controls': []}
    print(json.dumps({'static': report['static'], 'entry_oracle_ok': report['entry_oracle']['ok']}, ensure_ascii=False), flush=True)
    if not a.skip_db:
        fixtures, plan = work / 'private' / 'fixtures.json', work / 'plan.json'
        assert run([PY, ROOT / 'harness/ctl.py', 'fixtures', '--output', fixtures]).returncode == 0
        r = run([PY, ROOT / 'harness/ctl.py', 'plan', '--task', a.task, '--model', 'control', '--fixtures', fixtures, '--output', plan,
                 '--synthetic', *(['--allow-dirty-harness'] if a.allow_dirty else [])])
        assert r.returncode == 0, r.stderr
        import verifier
        (work / 'bin').mkdir()
        public = verifier.build(a.task, 'public', work / 'bin')
        report['reference_digest_matches_appctl'] = verifier.digest(a.task) == ctl.reference(a.task)['digest']
        linguistic = not json.loads(plan.read_text())['database']['byte_order']
        for control in spec['controls']:
            if a.only and control['variant'] not in a.only: continue
            v, result = evaluate_control(a.task, spec, control['variant'], work, plan, fixtures)
            expect = control.get('expect_linguistic', control['expect']) if linguistic else control['expect']
            good, got, want = compare(expect, result)
            vok, vgot = verifier_check(a.task, public, v['cell'], control['kind'], result, work / 'controls' / control['variant'] / 'restored')
            good = good and vok
            entry = {'variant': control['variant'], 'kind': control['kind'], 'cell': v['cell'], 'ok': good, 'got': got, 'verifier': vgot,
                     'collation': 'linguistic' if linguistic else 'byte-order', 'catalog_digest': result.get('catalog', {}).get('digest'),
                     **({} if good else {'want': want, 'error': result.get('error')}), 'evaluation_seconds': result.get('evaluation_seconds'),
                     'safety_cases': {k: x['cases'] for k, x in result.get('safety', {}).items() if x['count']}}
            report['controls'].append(entry)
            print(json.dumps({k: entry[k] for k in ('variant', 'ok', 'got')}, ensure_ascii=False), flush=True)
        if not a.only and a.task == 'delivery-site':
            # Private kernel regression (deliverySiteId overlay kernel pairs old values by id) + positional negative control.
            r = run([PY, ROOT / 'harness/kernel_regression.py'], timeout=2000)
            try: report['kernel_regression'] = json.loads(r.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError): report['kernel_regression'] = {'ok': False, 'error': (r.stdout + r.stderr)[-800:]}
    report['all_ok'] = all(v for k, v in report['static'].items() if k != 'missing_clues') and report['entry_oracle']['ok'] \
        and all(c['ok'] for c in report['controls']) and report.get('kernel_regression', {'ok': True})['ok'] \
        and report.get('reference_digest_matches_appctl', True)
    text = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    (work / 'controls-report.json').write_text(text)
    if a.output: Path(a.output).write_text(text)
    print(json.dumps({'task': a.task, 'static': report['static'], 'entry_oracle': report['entry_oracle']['ok'], 'controls': len(report['controls']),
                      'kernel_regression': report.get('kernel_regression', {}).get('ok'),
                      'failed': [c['variant'] for c in report['controls'] if not c['ok']] +
                                [c['name'] for c in report['entry_oracle']['controls'] if not c['ok']],
                      'all_ok': report['all_ok']}, ensure_ascii=False))
    return 0 if report['all_ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
