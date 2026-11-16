#!/usr/bin/env python3
"""Positive/negative controls for harness/analyze_v7.py on synthetic run directories (no model, no database).

Two synthetic lanes are written to a temporary directory with hand-chosen outcomes, so every test statistic
below is known in closed form. The checks fail loudly on any difference.
"""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_v7 as av  # noqa: E402

SAFE = {k: {'count': 0, 'cases': []} for k in av.SAFETY}


def write(root, task, model, spec):
    """spec: instance -> cell -> dict(outcome, S, credits, started, safety, cmd, deny)."""
    run = root / ('%s-%s' % (task, model))
    trials = []
    for inst, cells in spec.items():
        for cell, s in cells.items():
            t = {'id': '%s--%03d' % (cell, inst), 'cell': cell, 'instance': inst}
            trials.append(t)
            res, ev = run / 'results' / t['id'], run / 'evaluations' / t['id']
            res.mkdir(parents=True)
            g = {'generation_seconds': s.get('S', 100.0), 'turn_started': s.get('started', True)}
            if s.get('credits', 1.0) is not None:
                g['usage'] = {'credits': s.get('credits', 1.0)}
            o = s['outcome']
            if o in ('passed', 'acceptance-failed'):
                ev.mkdir(parents=True)
                safety = {k: {'count': len(v), 'cases': v} for k, v in {**{k: [] for k in av.SAFETY}, **s.get('safety', {})}.items()}
                (ev / 'result.json').write_text(json.dumps({'version': 7, 'trial': t, 'outcome': o, 'passed': o == 'passed',
                    'explicit_passed': o == 'passed' or s.get('E', False), 'conventions_passed': o == 'passed', 'outbox_passed': o == 'passed',
                    'safety': safety, 'generation': g}))
            else:
                (res / 'outcome.json').write_text(json.dumps({'version': 3, 'trial': t, 'outcome': o, 'passed': False, 'generation': g}))
            if s.get('cmd'):
                (res / 'generation').mkdir()
                (res / 'generation' / 'events.jsonl').write_text(json.dumps({'type': 'sessionUpdate', 'data': {'update': {
                    'sessionUpdate': 'tool_call', 'rawInput': {'command': s['cmd']}}}}) + '\n')
            (res / 'network.log').write_text('\n'.join(json.dumps(e) for e in [
                {'event': 'start'}, {'decision': 'deny', 'host': 'telemetry.us-east-1.kiro.dev'},
                *([{'decision': 'deny', 'host': s['deny']}] if s.get('deny') else [])]) + '\n')
    (run / 'plan.json').write_text(json.dumps({'version': 7, 'task': task, 'model': model, 'trials': trials}))
    return run


P, F = 'passed', 'acceptance-failed'


def lanes(root):
    # Lane 1 (withdraw/opus), 8 blocks: opdef wins 6 discordant, doc wins 3 vs manual 1, opdef vs doc 3:0.
    one = {}
    for i in range(1, 9):
        one[i] = {'go-action-opdef': {'outcome': P if i <= 7 else F, 'S': 50.0 + i},
                  'go-manual-doc': {'outcome': P if i in (1, 2, 3, 4) else F, 'S': 90.0},
                  'go-manual': {'outcome': P if i == 1 or i == 8 else F, 'S': 100.0}}
    # unmeasured manual in block 8 (provider-error after start, usage known): H1 and H2 lose this block
    one[8]['go-manual'] = {'outcome': 'provider-error', 'S': 5.0, 'credits': 0.5}
    # a retrieval attempt in block 2 (manual) and a non-telemetry denial in block 3
    one[2]['go-manual']['cmd'] = 'curl https://github.com/x/y'
    one[3]['go-manual-doc']['deny'] = 'proxy.golang.org'
    # safety on two trials
    one[4]['go-manual']['safety'] = {'double_apply': ['withdraw-concurrency: concurrent different keys apply once']}
    one[5]['go-manual-doc']['safety'] = {'unauthorized_write': ['a', 'b'], 'service_error': ['a']}
    # Lane 2 (cost-center/haiku), 2 blocks: one never run (preflight blocked), one usage missing.
    two = {1: {'go-action-opdef': {'outcome': P, 'S': 40.0, 'credits': None},
               'go-manual-doc': {'outcome': F, 'S': 80.0},
               'go-manual': {'outcome': F, 'S': 41.0}},
           2: {'go-action-opdef': {'outcome': 'scope-violation', 'S': 30.0},
               'go-manual-doc': {'outcome': 'runtime-blocked', 'S': 1.0, 'started': False, 'credits': None},
               'go-manual': {'outcome': 'candidate-error', 'S': 70.0, 'credits': 2.0}}}
    return [write(root, 'withdraw', 'opus', one), write(root, 'cost-center', 'haiku', two)]


def expect(label, got, want, failures):
    if got != want:
        failures.append({'check': label, 'got': got, 'want': want})


def main():
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        runs = lanes(Path(tmp))
        r = av.analyze(runs, seed=20261116, resamples=2000)
        again = av.analyze(runs, seed=20261116, resamples=2000)
        other = av.analyze(runs, seed=7, resamples=2000)
        h = r['primary_and_secondary']
        # H1: blocks with opdef and manual observed: lane1 1-7 (block 8 manual unmeasured) + lane2 1-2 = 9.
        # discordant: opdef only = lane1 2..7 (6) + lane2 1 (1) = 7; manual only = 0.  p = 1/2^7.
        expect('H1 denominator', h['H1']['valid_blocks'], 9, failures)
        expect('H1 counts', (h['H1']['only_higher'], h['H1']['only_lower']), (7, 0), failures)
        expect('H1 p', h['H1']['p_one_sided'], 1 / 128, failures)
        expect('H1 holds (alone, no correction)', h['H1']['holds'], True, failures)
        # H2 doc vs manual: lane1 1-7 (doc wins 2,3,4 = 3; manual only none) + lane2 block1 (both fail); block2 doc unmeasured.
        expect('H2 denominator', h['H2']['valid_blocks'], 8, failures)
        expect('H2 counts', (h['H2']['only_higher'], h['H2']['only_lower']), (3, 0), failures)
        expect('H2 p', h['H2']['p_one_sided'], 1 / 8, failures)
        # H3 opdef vs doc: lane1 1-8 (opdef only 5,6,7 = 3; doc only 0) + lane2 block1 (opdef only) = 9 blocks, 4:0.
        expect('H3 denominator', h['H3']['valid_blocks'], 9, failures)
        expect('H3 counts', (h['H3']['only_higher'], h['H3']['only_lower']), (4, 0), failures)
        expect('H3 p', h['H3']['p_one_sided'], 1 / 16, failures)
        # Holm over {H2: 0.125, H3: 0.0625}: H3 -> 2*0.0625 = 0.125, H2 -> max(0.125, 0.125) = 0.125.
        expect('Holm H3', h['H3']['p_holm'], 0.125, failures)
        expect('Holm H2', h['H2']['p_holm'], 0.125, failures)
        expect('secondary do not hold', (h['H2']['holds'], h['H3']['holds']), (False, False), failures)
        expect('Holm order-independent', av.holm({'b': 0.01, 'a': 0.04}), {'b': 0.02, 'a': 0.04}, failures)
        expect('Holm monotone', av.holm({'x': 0.03, 'y': 0.02}), {'y': 0.04, 'x': 0.04}, failures)
        # Sensitivity: lane1 block 2 has a retrieval attempt in go-manual -> dropped from H1 and H2 only.
        s = r['sensitivity_excluding_retrieval_attempt']
        expect('sensitivity denominators', (s['H1']['valid_blocks'], s['H2']['valid_blocks'], s['H3']['valid_blocks']), (8, 7, 9), failures)
        expect('sensitivity H1 p', s['H1']['p_one_sided'], 1 / 64, failures)
        # Wilcoxon: S diffs opdef - manual over blocks where both are observed (S is None for unmeasured):
        # lane1 1..7: 51-100 .. 57-100 (all negative), lane2 b1: 40-41 = -1, b2: 30-70 = -40  -> 9 negative, W+ = 0.
        w = r['exploratory_S_wilcoxon']
        expect('Wilcoxon n', (w['valid_blocks'], w['nonzero'], w['w_plus'], w['method']), (9, 9, 0, 'exact'), failures)
        expect('Wilcoxon p', w['p_one_sided'], 1 / 512, failures)
        expect('Wilcoxon ties exact', av.wilcoxon_lower_p([-1, -1, 2])[0], 5 / 8, failures)
        expect('Wilcoxon zeros dropped', av.wilcoxon_lower_p([0, 0, -3])[:2], (0.5, 1), failures)
        big = av.wilcoxon_lower_p([(-1) ** i * (i + 1) for i in range(60)])
        expect('Wilcoxon normal path', (big[3], 0.3 < big[0] < 0.7), ('normal', True), failures)
        expect('sign test', (av.sign_p(5, 1), av.sign_p(0, 0), av.sign_p(0, 3)), (7 / 64, 1.0, 1.0), failures)
        # Safety table and listing.
        st = r['safety']
        expect('safety manual double_apply', st['per_cell']['go-manual']['double_apply'], {'trials': 1, 'cases': 1}, failures)
        expect('safety doc unauthorized_write', st['per_cell']['go-manual-doc']['unauthorized_write'], {'trials': 1, 'cases': 2}, failures)
        expect('safety nonzero trials', sorted(x['trial'] for x in st['nonzero_trials']), ['go-manual--004', 'go-manual-doc--005'], failures)
        # Cost. opdef: started = 10 trials (lane1 8, lane2 2); credits known 9 x 1.0 (lane2 b1 missing) = 9; correct = 7 + 1 = 8.
        c = r['cost']['go-action-opdef']
        expect('opdef cost terms', (c['started'], c['correct'], c['credits_total'], c['usage_missing']), (10, 8, 9.0, 1), failures)
        expect('opdef credits/correct', c['credits_per_correct'], 9 / 8, failures)
        expect('opdef seconds/correct', c['seconds_per_correct'], (sum(50.0 + i for i in range(1, 9)) + 40 + 30) / 8, failures)
        expect('usage incomplete flag (1/10 > 5%)', c['usage_incomplete'], True, failures)
        # manual: provider-error after start counts (credits 0.5), candidate-error counts (2.0); correct = 1 (lane1 block 1).
        m = r['cost']['go-manual']
        expect('manual cost terms', (m['started'], m['correct'], m['credits_total'], m['usage_missing']), (10, 1, 7 + 0.5 + 1 + 2.0, 0), failures)
        expect('manual observed-only credits/correct', m['observed_only']['credits_per_correct'], 7 + 1 + 2.0, failures)
        # doc: runtime-blocked never started -> not in cost; usage missing only counted among started.
        d = r['cost']['go-manual-doc']
        expect('doc cost terms', (d['started'], d['correct'], d['usage_missing']), (9, 4, 0), failures)
        expect('bootstrap reproducible', r['cost'], again['cost'], failures)
        expect('bootstrap seed matters', r['cost']['go-action-opdef']['ci95_credits_per_correct'] is not None and
               (r['cost'] != other['cost']), True, failures)
        lo, hi = c['ci95_credits_per_correct']
        expect('bootstrap interval brackets the estimate', lo <= c['credits_per_correct'] <= hi, True, failures)
        # Rates and audit.
        expect('lane1 manual rates', {k: r['per_lane'][0]['rates']['go-manual'][k] for k in ('observed', 'unmeasured', 'A')},
               {'observed': 7, 'unmeasured': 1, 'A': 1}, failures)
        expect('audit', sorted((x['trial'], bool(x['retrieval_attempt']), tuple(x['network_denied'])) for x in r['audit']),
               [('go-manual--002', True, ()), ('go-manual-doc--003', False, ('proxy.golang.org',))], failures)
        # Negative controls: corrupted inputs are refused, not analysed.
        bad = Path(tmp) / 'withdraw-opus' / 'evaluations' / 'go-manual--001' / 'result.json'
        good = bad.read_text()
        v = json.loads(good); v['passed'] = False; bad.write_text(json.dumps(v))
        try:
            av.analyze(runs, resamples=10); failures.append({'check': 'inconsistent pass flag refused', 'got': 'accepted'})
        except ValueError:
            pass
        bad.write_text(good)
        try:
            av.analyze([runs[0], runs[0]], resamples=10); failures.append({'check': 'duplicate lane refused', 'got': 'accepted'})
        except ValueError:
            pass
        # Direction: swapping the two compared cells must make H1 non-significant.
        swapped = av.paired(av.blocks_of(av.load(runs)), 'H1')
        av.HYPOTHESES['H1'] = ('go-manual', 'go-action-opdef')
        try:
            flipped = av.paired(av.blocks_of(av.load(runs)), 'H1')
        finally:
            av.HYPOTHESES['H1'] = ('go-action-opdef', 'go-manual')
        expect('direction matters', (swapped['p_one_sided'] < 0.05, flipped['p_one_sided'] == 1.0), (True, True), failures)
    print(json.dumps({'ok': not failures, 'failures': failures}, indent=2))
    return 0 if not failures else 1


if __name__ == '__main__':
    sys.exit(main())
