#!/usr/bin/env python3
"""Pre-registered v9 analysis (prereg/preregistration-v9.md). Reads run directories only; never calls a model.

Usage: analyze_v9.py --run DIR [--run DIR ...] [--output analysis.json] [--seed 20261118] [--resamples 10000]
A run directory is a host run (`/opt/poc-oracle/runs/v9-<task>-<model>`): plan.json, results/<trial>/ (worker or
stage-wrapper outcome, receipt, network.log, per-session events.jsonl) and evaluations/<trial>/result.json.
One run per lane (task x model). Blocks are (task, model, instance); each block has the three cells.

Outcomes: A (passed), E, K, D, C (drafted cells: the frozen entry equals the reference entry, harness/entry_oracle.py),
S (seconds, all sessions), credits (all sessions), safety (seven structured categories).
H1 (primary): A drafted-rule is non-inferior to given, margin 5 percentage points: Tango's score test for paired
proportions, H0: p(drafted-rule) - p(given) <= -0.05, one-sided alpha 0.025, all blocks (up to 96 pairs).
H2 (secondary): A drafted-rule > drafted-norule, exact one-sided paired sign test, alpha 0.05 (a single secondary
hypothesis: no family). Each hypothesis has its own valid blocks: both of its cells observed in the block.
C, cost and safety are descriptive. Sensitivity: drop, per hypothesis, every block in which one of its two trials made
a retrieval attempt (any session); localhost probes (127.0.0.1, localhost) are not retrieval.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import random
import re
import sys

CELLS = ('given', 'drafted-norule', 'drafted-rule')
STAGED = ('drafted-norule', 'drafted-rule')
OBSERVED = {'passed', 'acceptance-failed', 'candidate-error', 'budget-exceeded', 'scope-violation', 'draft-invalid'}
UNMEASURED = {'runtime-blocked', 'provider-error', 'protocol-error', 'infrastructure-blocked', 'not-run'}
SAFETY = ('unauthorized_write', 'partial_batch', 'side_effect_atomicity', 'double_apply', 'wrong_commit',
          'service_error', 'undecidable')
FIELDS = ('name', 'kind', 'role', 'subject', 'from', 'to', 'tier', 'status', 'column', 'param', 'response',
          'inputs', 'fingerprint', 'record', 'event', 'separation', 'confirmAmount', 'denial', 'catalog')
CATEGORIES = ('event', 'fingerprint', 'record', 'separation', 'authority', 'state', 'shape', 'name', 'denial', 'catalog')
H1 = ('drafted-rule', 'given')
H2 = ('drafted-rule', 'drafted-norule')
MARGIN, ALPHA_PRIMARY, ALPHA_SECONDARY = 0.05, 0.025, 0.05
TELEMETRY = {'telemetry.eu-central-1.kiro.dev', 'client-telemetry.us-east-1.amazonaws.com',
             'telemetry.us-east-1.kiro.dev', 'app.kiro.dev'}
# v8a flagged two qwen `curl 127.0.0.1:8080/health` probes; v9 pre-registers that local probes are not retrieval.
RETRIEVAL = re.compile(r'kiro-cli|\bq chat\b|web_search|\bwget\b|git clone|\bcurl\b(?!.*\b(?:127\.0\.0\.1|localhost)\b)|https?://(?!127\.0\.0\.1|localhost)')
SESSIONS = ('generation', 'draft', 'adapter')
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


def trial(run, t):
    """One trial's record. Evaluation result wins over the generation outcome; no record at all = not-run."""
    res, ev = run / 'results' / t['id'], run / 'evaluations' / t['id'] / 'result.json'
    if ev.exists():
        r = json.loads(ev.read_text())
        if r.get('version') != 9:
            raise ValueError('not a v9 evaluation: %s' % ev)
    elif (res / 'outcome.json').exists():
        r = json.loads((res / 'outcome.json').read_text())
        if r['outcome'] == 'generated' or (res / 'receipt.json').exists():
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
    observed = outcome in OBSERVED
    scored = outcome in ('passed', 'acceptance-failed')
    g = r.get('generation') or {}
    usage = (g.get('usage') or {}).get('credits')
    entry = r.get('entry') or {}
    frozen = (entry.get('stages') or {}).get(entry.get('frozen_stage') or '') or {}
    stages = {s['name']: {'credits': ((s.get('generation') or {}).get('usage') or {}).get('credits'),
                          'seconds': (s.get('generation') or {}).get('generation_seconds')} for s in r.get('stages') or []}
    commands = [c for s in SESSIONS for c in shell_commands(res / s / 'events.jsonl')]
    staged = t['cell'] in STAGED
    return {'id': t['id'], 'cell': t['cell'], 'instance': t['instance'], 'outcome': outcome, 'observed': observed,
            'A': outcome == 'passed' if observed else None,
            'E': bool(r.get('explicit_passed')) if observed else None,
            'K': bool(r.get('conventions_passed')) if observed else None,
            'D': bool(r.get('outbox_passed')) if observed else None,
            # A staged trial that never froze an entry (it ended in its first session) has C = 0.
            'C': bool(entry.get('C')) if observed and staged else None,
            'wrong': (frozen.get('C') or {}).get('wrong') if observed and staged and frozen else None,
            'S': g.get('generation_seconds') if observed else None,
            'started': bool(g.get('turn_started')), 'credits': usage if type(usage) in (int, float) else None,
            'stages': stages,
            'safety': {k: (r.get('safety') or {}).get(k, {'count': 0, 'cases': []}) for k in SAFETY} if scored else None,
            'retrieval_attempt': [c[:200] for c in commands if RETRIEVAL.search(c)],
            'network_denied': denied_hosts(res / 'network.log')}


def load(runs):
    lanes, seen = [], set()
    for d in runs:
        d = Path(d)
        plan = json.loads((d / 'plan.json').read_text())
        if plan.get('experiment') != 'v9':
            raise ValueError('not a v9 plan: %s' % d)
        lane = (plan['task'], plan['model'])
        if lane in seen:
            raise ValueError('duplicate lane %s' % (lane,))
        seen.add(lane)
        blocks = {}
        for x in (trial(d, t) for t in plan['trials']):
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


def tango_z(b, c, n, delta0):
    """Tango (1998) score statistic for H0: p1 - p2 = delta0 with matched pairs.
    b = pairs with only the first cell passing, c = only the second; n = valid pairs."""
    A = 2 * n
    B = -b - c + (2 * n - b + c) * delta0
    C = -c * delta0 * (1 - delta0)
    q21 = (math.sqrt(max(B * B - 4 * A * C, 0.0)) - B) / (2 * A)
    var = n * (2 * q21 + delta0 * (1 - delta0))
    if var <= 0:
        return math.inf if b - c - n * delta0 > 0 else (-math.inf if b - c - n * delta0 < 0 else 0.0)
    return (b - c - n * delta0) / math.sqrt(var)


def normal_upper(z): return 0.5 * math.erfc(z / math.sqrt(2))


def tango_lower(b, c, n, alpha):
    """Lower confidence bound: the smallest delta0 in [-1, d] not rejected at one-sided alpha (bisection)."""
    if n == 0:
        return -1.0
    zc = _z(1 - alpha)
    lo, hi = -1.0 + 1e-12, (b - c) / n
    if tango_z(b, c, n, lo) <= zc:
        return -1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if tango_z(b, c, n, mid) > zc:
            lo = mid
        else:
            hi = mid
    return hi


def _z(p):
    """Standard normal quantile by bisection (no dependencies)."""
    lo, hi = -10.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if 0.5 * math.erfc(-mid / math.sqrt(2)) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def blocks_of(lanes):
    return [b for lane in lanes for b in lane['blocks']]


def pairs(blocks, hi, lo, metric='A', exclude_retrieval=False):
    valid = wins = losses = excluded = 0
    for b in blocks:
        x, y = b[hi], b[lo]
        if not (x['observed'] and y['observed']):
            continue
        if exclude_retrieval and (x['retrieval_attempt'] or y['retrieval_attempt']):
            excluded += 1
            continue
        valid += 1
        wins += bool(x[metric]) and not y[metric]
        losses += bool(y[metric]) and not x[metric]
    return valid, wins, losses, excluded


def tango_upper(b, c, n, alpha):
    """Upper confidence bound: the largest delta0 in [d, 1] not rejected by the one-sided test of H0: delta >= delta0."""
    if n == 0:
        return 1.0
    zc = _z(1 - alpha)
    lo, hi = (b - c) / n, 1.0 - 1e-12
    if tango_z(b, c, n, hi) >= -zc:
        return 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if tango_z(b, c, n, mid) < -zc:
            hi = mid
        else:
            lo = mid
    return lo


def hypotheses(blocks, exclude_retrieval=False):
    n, b, c, ex = pairs(blocks, H1[0], H1[1], exclude_retrieval=exclude_retrieval)
    z = tango_z(b, c, n, -MARGIN) if n else None
    p = normal_upper(z) if n else 1.0
    out = {'H1': {'comparison': '%s non-inferior to %s (margin %.2f)' % (H1[0], H1[1], MARGIN), 'test': 'Tango score, one-sided',
                  'valid_blocks': n, 'only_' + H1[0]: b, 'only_' + H1[1]: c, 'difference': (b - c) / n if n else None,
                  'z': z, 'critical_z': _z(1 - ALPHA_PRIMARY), 'p_one_sided': p, 'alpha': ALPHA_PRIMARY,
                  'lower_bound': tango_lower(b, c, n, ALPHA_PRIMARY) if n else None,
                  'upper_bound': tango_upper(b, c, n, ALPHA_PRIMARY) if n else None,
                  'holds': bool(n) and p < ALPHA_PRIMARY, **({'excluded_retrieval_blocks': ex} if exclude_retrieval else {})}}
    n, w, l, ex = pairs(blocks, H2[0], H2[1], exclude_retrieval=exclude_retrieval)
    p = sign_p(w, l)
    out['H2'] = {'comparison': '%s > %s' % H2, 'test': 'exact paired sign, one-sided', 'valid_blocks': n, 'only_higher': w,
                 'only_lower': l, 'p_one_sided': p, 'alpha': ALPHA_SECONDARY, 'holds': p < ALPHA_SECONDARY,
                 **({'excluded_retrieval_blocks': ex} if exclude_retrieval else {})}
    return out


# ---- entry correctness (descriptive) ------------------------------------------------------------------

CATEGORY = {'event': 'event', 'fingerprint': 'fingerprint', 'record': 'record', 'separation': 'separation',
            'role': 'authority', 'subject': 'authority', 'from': 'state', 'to': 'state', 'status': 'state', 'tier': 'state',
            'kind': 'shape', 'column': 'shape', 'param': 'shape', 'response': 'shape', 'inputs': 'shape', 'confirmAmount': 'shape',
            'name': 'name', 'denial': 'denial', 'catalog': 'catalog'}


def entry_table(trials):
    """C, and the error distribution by logical field and category."""
    obs = [t for t in trials if t['observed']]
    out = {'observed': len(obs), 'C': sum(bool(t['C']) for t in obs), 'no_frozen_entry': sum(t['wrong'] is None for t in obs)}
    wrong = [t['wrong'] for t in obs if t['wrong'] is not None]
    out['wrong_by_field'] = {f: sum(f in w for w in wrong) for f in FIELDS}
    out['wrong_by_category'] = {c: sum(any(CATEGORY[f] == c for f in w) for w in wrong) for c in CATEGORIES}
    out['A_given_C'] = {'C=1': [sum(bool(t['A']) for t in obs if t['C']), sum(bool(t['C']) for t in obs)],
                        'C=0': [sum(bool(t['A']) for t in obs if not t['C']), sum(not t['C'] for t in obs)]}
    return out


# ---- cost ----------------------------------------------------------------------------------------

def cost_terms(trials):
    started = [t for t in trials if t['started'] or t['observed']]
    credits = sum(t['credits'] for t in started if t['credits'] is not None)
    secs = sum(t['S'] for t in started if t['S'] is not None)
    return credits, secs, sum(bool(t['A']) for t in trials), len(started), sum(t['credits'] is None for t in started)


def interval(xs):
    if not xs:
        return None
    xs = sorted(xs)
    return [xs[int(0.025 * (len(xs) - 1))], xs[int(math.ceil(0.975 * (len(xs) - 1)))]]


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
    return {'ci95_credits_per_correct': interval(ratios['credits']), 'ci95_seconds_per_correct': interval(ratios['seconds']),
            'resamples_without_correct': undefined}


def cost(blocks, rng, resamples):
    out = {}
    for cell in CELLS:
        trials = [b[cell] for b in blocks]
        c, s, a, started, missing = cost_terms(trials)
        row = {'started': started, 'correct': a, 'credits_total': round(c, 6), 'seconds_total': round(s, 3),
               'usage_missing': missing, 'usage_incomplete': started > 0 and missing / started > USAGE_INCOMPLETE,
               'credits_per_correct': c / a if a else None, 'seconds_per_correct': s / a if a else None,
               'note': None if a else 'no correct result'}
        if cell in STAGED:
            row['per_stage'] = {st: {'credits': round(sum(t['stages'].get(st, {}).get('credits') or 0 for t in trials), 6),
                                     'seconds': round(sum(t['stages'].get(st, {}).get('seconds') or 0 for t in trials), 3)}
                                for st in ('draft', 'adapter')}
        row.update(bootstrap(blocks, cell, rng, resamples) if a else {})
        out[cell] = row
    return out


# ---- descriptive ---------------------------------------------------------------------------------

def rates(trials):
    obs = [t for t in trials if t['observed']]
    return {'planned': len(trials), 'observed': len(obs), 'unmeasured': len(trials) - len(obs),
            'unmeasured_over_10pct': bool(trials) and (len(trials) - len(obs)) / len(trials) > 0.10,
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
    return [{'task': lane['task'], 'model': lane['model'], 'trial': t['id'], 'retrieval_attempt': t['retrieval_attempt'],
             'network_denied': t['network_denied']}
            for lane in lanes for b in lane['blocks'] for t in b.values() if t['retrieval_attempt'] or t['network_denied']]


def analyze(runs, seed=20261118, resamples=10000):
    lanes = load(runs)
    blocks = blocks_of(lanes)
    rng = random.Random(seed)
    per_lane = [{'task': l['task'], 'model': l['model'], 'rates': {c: rates([b[c] for b in l['blocks']]) for c in CELLS},
                 'entry': {c: entry_table([b[c] for b in l['blocks']]) for c in STAGED},
                 'cost': cost(l['blocks'], rng, resamples)} for l in lanes]
    return {'version': 9, 'experiment': 'v9', 'seed': seed, 'resamples': resamples, 'lanes': [[l['task'], l['model']] for l in lanes],
            'hypotheses': hypotheses(blocks),
            'sensitivity_excluding_retrieval_attempt': hypotheses(blocks, exclude_retrieval=True),
            'rates': {c: rates([b[c] for b in blocks]) for c in CELLS},
            'entry': {c: entry_table([b[c] for b in blocks]) for c in STAGED},
            'safety': safety_table(lanes), 'cost': cost(blocks, rng, resamples), 'per_lane': per_lane,
            'audit': audit(lanes), 'telemetry_hosts_not_counted': sorted(TELEMETRY)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--run', action='append', required=True)
    p.add_argument('--output')
    p.add_argument('--seed', type=int, default=20261118)
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
