#!/usr/bin/env python3
"""Pre-registered v8b analysis (prereg/preregistration-v8b.md). Reads run directories only; never calls a model.

Usage: analyze_v8b.py --run DIR [--run DIR ...] [--output analysis.json] [--seed 20261116] [--resamples 10000]
A run directory is a host run (`/opt/poc-oracle/runs/<run>`): plan.json, results/<trial>/ (worker outcome,
receipt, network.log, generation/events.jsonl) and evaluations/<trial>/result.json. One run per lane
(task x model). Blocks are (task, model, instance).

Outcomes: A (passed), E (explicit_passed), K (conventions_passed), D (outbox_passed), S (generation_seconds),
C (usage.credits), safety (seven structured categories).
Primary (the only test): H1 A go-action-opdef (given) > go-manual, exact one-sided paired sign test over the
blocks of all three tasks pooled, alpha 0.05. A block is valid when both cells are observed in it.
Per-task results are descriptive only (rates; the per-task sign-test p values are reported as exploratory).
Exploratory: S opdef < manual, one-sided Wilcoxon signed-rank (exact with mid-ranks for n <= 50, else normal
approximation with tie correction). Cost per correct result with a block bootstrap. Sensitivity analysis
drops, per hypothesis, every block in which one of its two trials made a retrieval attempt (v6 appendix A).
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import random
import re
import sys

CELLS = ('go-manual', 'go-action-opdef')
OBSERVED = {'passed', 'acceptance-failed', 'candidate-error', 'budget-exceeded', 'scope-violation'}
UNMEASURED = {'runtime-blocked', 'provider-error', 'protocol-error', 'infrastructure-blocked', 'not-run'}
SAFETY = ('unauthorized_write', 'partial_batch', 'side_effect_atomicity', 'double_apply', 'wrong_commit',
          'service_error', 'undecidable')
HYPOTHESES = {'H1': ('go-action-opdef', 'go-manual')}
ALPHA = 0.05
# Telemetry hosts never count as network-denied (v6 appendix C and F).
TELEMETRY = {'telemetry.eu-central-1.kiro.dev', 'client-telemetry.us-east-1.amazonaws.com',
             'telemetry.us-east-1.kiro.dev', 'app.kiro.dev'}
RETRIEVAL = re.compile(r'kiro-cli|\bq chat\b|web_search|\bcurl\b|\bwget\b|git clone|https?://(?!127\.0\.0\.1|localhost)')
USAGE_INCOMPLETE = 0.05


# ---- reading -------------------------------------------------------------------------------------

def shell_commands(path):
    out = []
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        u = e.get('data', {}).get('update') if e.get('type') == 'sessionUpdate' else None
        if isinstance(u, dict) and u.get('sessionUpdate') == 'tool_call' and isinstance(u.get('rawInput'), dict):
            c = u['rawInput'].get('command')
            if isinstance(c, str):
                out.append(c)
    return out


def denied_hosts(path):
    out = []
    if path.exists():
        for line in path.read_text().splitlines():
            e = json.loads(line)
            if e.get('decision') == 'deny' and (e.get('host') or e.get('target')) not in TELEMETRY:
                out.append(e.get('host') or e.get('target'))
    return out


def trial(run, t, plan):
    """One trial's record. Evaluation result wins over the worker outcome; no record at all = not-run."""
    res, ev = run / 'results' / t['id'], run / 'evaluations' / t['id'] / 'result.json'
    if ev.exists():
        r = json.loads(ev.read_text())
        if r.get('version') != 8:
            raise ValueError('not a v8b evaluation: %s' % ev)
    elif (res / 'outcome.json').exists():
        r = json.loads((res / 'outcome.json').read_text())
        if r['outcome'] == 'generated':
            raise ValueError('generated but not evaluated: %s' % t['id'])
    else:
        r = {'outcome': 'not-run', 'passed': False, 'generation': {}}
    if r.get('trial', t) != t:
        raise ValueError('foreign trial record: %s' % t['id'])
    outcome = r['outcome']
    if outcome not in OBSERVED | UNMEASURED:
        raise ValueError('unknown outcome %s: %s' % (outcome, t['id']))
    if (r.get('passed') is True) != (outcome == 'passed'):
        raise ValueError('inconsistent pass flag: %s' % t['id'])
    scored = outcome in ('passed', 'acceptance-failed')
    g = r.get('generation') or {}
    usage = (g.get('usage') or {}).get('credits')
    observed = outcome in OBSERVED
    return {'id': t['id'], 'cell': t['cell'], 'instance': t['instance'], 'outcome': outcome, 'observed': observed,
            'A': outcome == 'passed' if observed else None,
            'E': bool(r.get('explicit_passed')) if observed else None,
            'K': bool(r.get('conventions_passed')) if observed else None,
            'D': bool(r.get('outbox_passed')) if observed else None,
            'S': g.get('generation_seconds') if observed else None,
            'started': bool(g.get('turn_started')), 'credits': usage if type(usage) in (int, float) else None,
            'safety': {k: (r.get('safety') or {}).get(k, {'count': 0, 'cases': []}) for k in SAFETY} if scored else None,
            'verifier': (r.get('verifier') or {}).get('verdict') if scored else None,
            'retrieval_attempt': [c[:200] for c in shell_commands(res / 'generation' / 'events.jsonl') if RETRIEVAL.search(c)],
            'network_denied': denied_hosts(res / 'network.log')}


def load(runs):
    lanes, seen = [], set()
    for d in runs:
        d = Path(d)
        plan = json.loads((d / 'plan.json').read_text())
        lane = (plan['task'], plan['model'])
        if lane in seen:
            raise ValueError('duplicate lane %s' % (lane,))
        seen.add(lane)
        trials = [trial(d, t, plan) for t in plan['trials']]
        blocks = {}
        for x in trials:
            blocks.setdefault(x['instance'], {})[x['cell']] = x
        if any(set(b) != set(CELLS) for b in blocks.values()):
            raise ValueError('incomplete block in %s' % d)
        lanes.append({'task': plan['task'], 'model': plan['model'], 'blocks': [blocks[i] for i in sorted(blocks)]})
    return lanes


# ---- tests ---------------------------------------------------------------------------------------

def sign_p(wins, losses):
    """Exact one-sided paired sign test: P(X >= wins | n = wins + losses, 1/2)."""
    n = wins + losses
    return 1.0 if n == 0 else sum(math.comb(n, i) for i in range(wins, n + 1)) / 2 ** n


def midranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def wilcoxon_lower_p(diffs):
    """One-sided Wilcoxon signed-rank, H: differences tend to be negative. Returns (p, n, W+, method)."""
    d = [x for x in diffs if x != 0]
    n = len(d)
    if n == 0:
        return 1.0, 0, 0.0, 'none'
    ranks = midranks([abs(x) for x in d])
    w_plus = sum(r for r, x in zip(ranks, d) if x > 0)
    if n <= 50:
        doubled = [round(2 * r) for r in ranks]  # mid-ranks are multiples of 1/2
        counts = {0: 1}
        for r in doubled:
            nxt = dict(counts)
            for s, c in counts.items():
                nxt[s + r] = nxt.get(s + r, 0) + c
            counts = nxt
        target = round(2 * w_plus)
        return sum(c for s, c in counts.items() if s <= target) / 2 ** n, n, w_plus, 'exact'
    ties = Counter(abs(x) for x in d)
    mu = n * (n + 1) / 4
    var = n * (n + 1) * (2 * n + 1) / 24 - sum(t ** 3 - t for t in ties.values()) / 48
    z = (w_plus - mu + 0.5) / math.sqrt(var)
    return 0.5 * math.erfc(-z / math.sqrt(2)), n, w_plus, 'normal'


def blocks_of(lanes):
    return [b for lane in lanes for b in lane['blocks']]


def paired(blocks, hypothesis, metric='A', exclude_retrieval=False):
    hi, lo = HYPOTHESES[hypothesis]
    valid = wins = losses = excluded = 0
    for b in blocks:
        x, y = b[hi], b[lo]
        if not (x['observed'] and y['observed']):
            continue
        if exclude_retrieval and (x['retrieval_attempt'] or y['retrieval_attempt']):
            excluded += 1
            continue
        valid += 1
        wins += x[metric] and not y[metric]
        losses += y[metric] and not x[metric]
    return {'comparison': '%s > %s' % (hi, lo), 'metric': metric, 'valid_blocks': valid, 'only_higher': wins,
            'only_lower': losses, 'p_one_sided': sign_p(wins, losses), **({'excluded_retrieval_blocks': excluded} if exclude_retrieval else {})}


def hypotheses(blocks, exclude_retrieval=False):
    out = {h: paired(blocks, h, exclude_retrieval=exclude_retrieval) for h in HYPOTHESES}
    out['H1']['holds'] = out['H1']['p_one_sided'] < ALPHA
    return out


def seconds(blocks):
    diffs = [b['go-action-opdef']['S'] - b['go-manual']['S'] for b in blocks
             if b['go-action-opdef']['S'] is not None and b['go-manual']['S'] is not None]
    p, n, w, method = wilcoxon_lower_p(diffs)
    return {'comparison': 'S go-action-opdef < go-manual', 'valid_blocks': len(diffs), 'nonzero': n, 'w_plus': w,
            'method': method, 'p_one_sided': p,
            'median_difference_seconds': sorted(diffs)[len(diffs) // 2] if diffs else None}


# ---- cost ----------------------------------------------------------------------------------------

def cost_terms(trials):
    """(credits, seconds, correct, started, usage_missing) over the trials of one cell."""
    started = [t for t in trials if t['started'] or t['observed']]
    credits = sum(t['credits'] for t in started if t['credits'] is not None)
    secs = sum(t['S'] for t in started if t['S'] is not None)
    return credits, secs, sum(bool(t['A']) for t in trials), len(started), sum(t['credits'] is None for t in started)


def bootstrap(blocks, cell, rng, resamples):
    ratios = {'credits': [], 'seconds': []}
    undefined = 0
    n = len(blocks)
    for _ in range(resamples):
        sample = [blocks[rng.randrange(n)][cell] for _ in range(n)]
        c, s, a, _, _ = cost_terms(sample)
        if a == 0:
            undefined += 1
            continue
        ratios['credits'].append(c / a)
        ratios['seconds'].append(s / a)

    def interval(xs):
        if not xs:
            return None
        xs = sorted(xs)
        return [xs[int(0.025 * (len(xs) - 1))], xs[int(math.ceil(0.975 * (len(xs) - 1)))]]
    return {'ci95_credits_per_correct': interval(ratios['credits']), 'ci95_seconds_per_correct': interval(ratios['seconds']),
            'resamples_without_correct': undefined}


def cost(blocks, rng, resamples):
    out = {}
    for cell in CELLS:
        trials = [b[cell] for b in blocks]
        c, s, a, started, missing = cost_terms(trials)
        observed = [t for t in trials if t['observed']]
        oc = sum(t['credits'] for t in observed if t['credits'] is not None)
        os_ = sum(t['S'] for t in observed if t['S'] is not None)
        row = {'started': started, 'correct': a, 'credits_total': round(c, 6), 'seconds_total': round(s, 3),
               'usage_missing': missing, 'usage_incomplete': started > 0 and missing / started > USAGE_INCOMPLETE,
               'credits_per_correct': c / a if a else None, 'seconds_per_correct': s / a if a else None,
               'observed_only': {'credits_per_correct': oc / a if a else None, 'seconds_per_correct': os_ / a if a else None},
               'note': None if a else 'no correct result'}
        row.update(bootstrap(blocks, cell, rng, resamples) if a else {})
        out[cell] = row
    return out


# ---- descriptive ---------------------------------------------------------------------------------

def rates(trials):
    obs = [t for t in trials if t['observed']]
    return {'planned': len(trials), 'observed': len(obs), 'unmeasured': len(trials) - len(obs),
            'unmeasured_over_10pct': len(trials) and (len(trials) - len(obs)) / len(trials) > 0.10,
            **{m: sum(bool(t[m]) for t in obs) for m in ('A', 'E', 'K', 'D')}, 'outcomes': dict(Counter(t['outcome'] for t in trials))}


def safety_table(lanes):
    table = {c: {k: {'trials': 0, 'cases': 0} for k in SAFETY} for c in CELLS}
    nonzero = []
    for lane in lanes:
        for b in lane['blocks']:
            for cell, t in b.items():
                if not t['safety']:
                    continue
                flagged = {k: v for k, v in t['safety'].items() if v['count']}
                for k, v in flagged.items():
                    table[cell][k]['trials'] += 1
                    table[cell][k]['cases'] += v['count']
                if flagged:
                    nonzero.append({'task': lane['task'], 'model': lane['model'], 'trial': t['id'],
                                    'safety': {k: v['cases'] for k, v in flagged.items()}})
    return {'per_cell': table, 'nonzero_trials': nonzero}


def audit(lanes):
    out = []
    for lane in lanes:
        for b in lane['blocks']:
            for t in b.values():
                if t['retrieval_attempt'] or t['network_denied']:
                    out.append({'task': lane['task'], 'model': lane['model'], 'trial': t['id'],
                                'retrieval_attempt': t['retrieval_attempt'], 'network_denied': t['network_denied']})
    return out


def verifier_table(lanes):
    """Private Verifier verdict against the Oracle's A, per task and cell (additional analysis, never an outcome)."""
    out = {}
    for lane in lanes:
        for b in lane['blocks']:
            for cell, t in b.items():
                if t['verifier'] is None and t['outcome'] not in ('passed', 'acceptance-failed'):
                    continue
                row = out.setdefault(lane['task'], {}).setdefault(cell, Counter())
                row['%s/%s' % ('A' if t['A'] else 'notA', t['verifier'] or 'none')] += 1
    return {task: {cell: dict(c) for cell, c in cells.items()} for task, cells in out.items()}


def analyze(runs, seed=20261116, resamples=10000):
    lanes = load(runs)
    blocks = blocks_of(lanes)
    rng = random.Random(seed)
    tasks = sorted({lane['task'] for lane in lanes})
    per_task = []
    for task in tasks:
        tb = [b for lane in lanes if lane['task'] == task for b in lane['blocks']]
        per_task.append({'task': task, 'descriptive_only': True, 'rates': {c: rates([b[c] for b in tb]) for c in CELLS},
                         'exploratory_A_sign_test': paired(tb, 'H1'), 'cost': cost(tb, rng, resamples)})
    per_lane = []
    for lane in lanes:
        per_lane.append({'task': lane['task'], 'model': lane['model'],
                         'rates': {c: rates([b[c] for b in lane['blocks']]) for c in CELLS},
                         'exploratory_A_sign_tests': {h: paired(lane['blocks'], h) for h in HYPOTHESES},
                         'cost': cost(lane['blocks'], rng, resamples)})
    return {'version': 8, 'seed': seed, 'resamples': resamples, 'lanes': [[l['task'], l['model']] for l in lanes],
            'primary': hypotheses(blocks), 'per_task': per_task, 'verifier': verifier_table(lanes),
            'sensitivity_excluding_retrieval_attempt': hypotheses(blocks, exclude_retrieval=True),
            'exploratory_S_wilcoxon': seconds(blocks),
            'rates': {c: rates([b[c] for b in blocks]) for c in CELLS},
            'safety': safety_table(lanes), 'cost': cost(blocks, rng, resamples), 'per_lane': per_lane,
            'audit': audit(lanes), 'telemetry_hosts_not_counted': sorted(TELEMETRY)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--run', action='append', required=True)
    p.add_argument('--output')
    p.add_argument('--seed', type=int, default=20261116)
    p.add_argument('--resamples', type=int, default=10000)
    a = p.parse_args()
    value = analyze(a.run, a.seed, a.resamples)
    text = json.dumps(value, indent=2, ensure_ascii=False) + '\n'
    if a.output:
        Path(a.output).write_text(text)
    print(text, end='')
    return 0


if __name__ == '__main__':
    sys.exit(main())
