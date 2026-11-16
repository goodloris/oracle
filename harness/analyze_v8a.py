#!/usr/bin/env python3
"""Pre-registered v8a analysis (prereg/preregistration-v8a.md). Reads run directories only; never calls a model.

Usage: analyze_v8a.py --run DIR [--run DIR ...] [--output analysis.json] [--seed 20261116] [--resamples 10000]
A run directory is a host run (`/opt/poc-oracle/runs/v8a-<task>-<model>`): plan.json, results/<trial>/ (worker or
stage-wrapper outcome, receipt, network.log, per-session events.jsonl) and evaluations/<trial>/result.json.
One run per lane (task x model). Blocks are (task, model, instance); each block has the four cells.

Outcomes: A (passed), E, K, D, C (staged cells: the frozen entry equals the reference entry, harness/entry_oracle.py),
C_draft (drafted-review: the entry before review), S (seconds, all sessions), credits (all sessions, review included),
safety (seven structured categories).
Primary H1: A drafted-review is non-inferior to given, margin 10 percentage points, Tango's score test for paired
proportions (H0: p(drafted-review) - p(given) <= -0.10), in a two-stage group-sequential design with total one-sided
alpha 0.025 spent by the O'Brien-Fleming-type Lan-DeMets function at information fractions 0.5 and 1.0
(boundaries() below). Stage 1 = blocks 1..8 of the four-cell runs; stage 2 = blocks 9..16 of the two-cell runs
(given, drafted-review), run only when stage1_decision == 'continue'. Stage-1 rules, on the H1 statistic only:
  stop-noninferior  z(-0.10) > c1                              (non-inferiority shown at alpha1)
  stop-futility     z(-0.10) < -c1, i.e. the one-sided (1 - alpha1) upper bound of the difference is below -0.10
  continue          otherwise
Stage 2 (all H1 blocks of both stages): non-inferior iff z(-0.10) > c2 (alpha2). The futility stop is non-binding:
it does not change c2.
Secondary H2: A drafted > manual, H3: A drafted-review > drafted: exact one-sided paired sign tests on the stage-1
blocks only, Holm over {H2, H3} at family alpha 0.05. Each hypothesis has its own valid blocks: both of its cells
observed in the block. C, cost and every other descriptive analysis use stage-1 blocks only.
Sensitivity: drop, per hypothesis, every block in which one of its two trials made a retrieval attempt (any session).
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import random
import re
import sys

CELLS = ('manual', 'given', 'drafted', 'drafted-review')
STAGED = ('drafted', 'drafted-review')
OBSERVED = {'passed', 'acceptance-failed', 'candidate-error', 'budget-exceeded', 'scope-violation', 'draft-invalid'}
UNMEASURED = {'runtime-blocked', 'provider-error', 'protocol-error', 'infrastructure-blocked', 'not-run'}
SAFETY = ('unauthorized_write', 'partial_batch', 'side_effect_atomicity', 'double_apply', 'wrong_commit',
          'service_error', 'undecidable')
FIELDS = ('name', 'kind', 'role', 'subject', 'from', 'to', 'tier', 'status', 'column', 'param', 'response',
          'inputs', 'fingerprint', 'record', 'event', 'separation', 'confirmAmount', 'denial', 'catalog')
CATEGORIES = ('event', 'fingerprint', 'record', 'separation', 'authority', 'state', 'shape', 'name', 'denial', 'catalog')
H1 = ('drafted-review', 'given')
SUPERIORITY = {'H2': ('drafted', 'manual'), 'H3': ('drafted-review', 'drafted')}
MARGIN, ALPHA_PRIMARY, ALPHA_FAMILY = 0.10, 0.025, 0.05
INFORMATION = (0.5, 1.0)  # planned information fractions (48 and 96 pairs); boundaries are not re-computed for actual n
STAGE2_CELLS = ('given', 'drafted-review')
TELEMETRY = {'telemetry.eu-central-1.kiro.dev', 'client-telemetry.us-east-1.amazonaws.com',
             'telemetry.us-east-1.kiro.dev', 'app.kiro.dev'}
RETRIEVAL = re.compile(r'kiro-cli|\bq chat\b|web_search|\bcurl\b|\bwget\b|git clone|https?://(?!127\.0\.0\.1|localhost)')
SESSIONS = ('generation', 'draft', 'review', 'adapter')
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
        if r.get('version') != 8:
            raise ValueError('not a v8a evaluation: %s' % ev)
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
    draft = (entry.get('stages') or {}).get('draft') or {}
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
            'C_draft': bool(entry.get('C_draft')) if observed and staged else None,
            'wrong': (frozen.get('C') or {}).get('wrong') if observed and staged and frozen else None,
            'draft_wrong': (draft.get('C') or {}).get('wrong') if observed and staged and draft else None,
            'review_changed': entry.get('review_changed_catalog'),
            'S': g.get('generation_seconds') if observed else None,
            'started': bool(g.get('turn_started')), 'credits': usage if type(usage) in (int, float) else None,
            'stages': stages,
            'safety': {k: (r.get('safety') or {}).get(k, {'count': 0, 'cases': []}) for k in SAFETY} if scored else None,
            'retrieval_attempt': [c[:200] for c in commands if RETRIEVAL.search(c)],
            'network_denied': denied_hosts(res / 'network.log')}


def load(runs):
    """(stage-1 lanes, stage-2 lanes). A stage-2 lane must match a stage-1 lane (task, model, harness) and use other blocks."""
    lanes = {1: {}, 2: {}}
    harness = {}
    for d in runs:
        d = Path(d)
        plan = json.loads((d / 'plan.json').read_text())
        if plan.get('experiment') != 'v8a':
            raise ValueError('not a v8a plan: %s' % d)
        stage = plan.get('stage', 1)
        cells = CELLS if stage == 1 else STAGE2_CELLS
        lane = (plan['task'], plan['model'])
        if lane in lanes[stage]:
            raise ValueError('duplicate lane %s in stage %d' % (lane, stage))
        blocks = {}
        for x in (trial(d, t) for t in plan['trials']):
            blocks.setdefault(x['instance'], {})[x['cell']] = x
        if any(set(b) != set(cells) for b in blocks.values()):
            raise ValueError('incomplete block in %s' % d)
        lanes[stage][lane] = {'task': plan['task'], 'model': plan['model'], 'blocks': [blocks[i] for i in sorted(blocks)]}
        harness[(stage, lane)] = plan['harness_sha']
    for lane, l2 in lanes[2].items():
        l1 = lanes[1].get(lane)
        if l1 is None or harness[(1, lane)] != harness[(2, lane)]:
            raise ValueError('stage-2 lane %s without a matching stage-1 lane' % (lane,))
        if {b[H1[1]]['instance'] for b in l1['blocks']} & {b[H1[1]]['instance'] for b in l2['blocks']}:
            raise ValueError('stage-2 blocks overlap stage 1 in %s' % (lane,))
    return list(lanes[1].values()), list(lanes[2].values())


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


def holm(ps):
    order = sorted(ps, key=lambda k: (ps[k], k))
    adj, running = {}, 0.0
    for i, k in enumerate(order):
        running = max(running, min(1.0, (len(ps) - i) * ps[k]))
        adj[k] = running
    return adj


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


def spend(t, alpha=ALPHA_PRIMARY):
    """O'Brien-Fleming-type Lan-DeMets spending, one-sided: alpha(t) = 2 - 2 Phi(z_{1-alpha/2} / sqrt(t))."""
    return 2 - 2 * (1 - normal_upper(_z(1 - alpha / 2) / math.sqrt(t)))


def _cross2(c1, c2, rho, steps=4000):
    """P(Z1 < c1, Z2 > c2) for standard normals with correlation rho (Simpson over Z1 in [-10, c1])."""
    lo = -10.0
    h = (c1 - lo) / steps
    total = 0.0
    for i in range(steps + 1):
        z = lo + i * h
        w = 1 if i in (0, steps) else (4 if i % 2 else 2)
        total += w * math.exp(-z * z / 2) / math.sqrt(2 * math.pi) * normal_upper((c2 - rho * z) / math.sqrt(1 - rho * rho))
    return total * h / 3


def boundaries(alpha=ALPHA_PRIMARY, information=INFORMATION):
    """Efficacy boundaries of the two looks: c1 from alpha(t1); c2 such that P(Z1 < c1, Z2 > c2) = alpha(1) - alpha(t1)
    with corr(Z1, Z2) = sqrt(t1/t2). Nominal alphas are 1 - Phi(c)."""
    t1, t2 = information
    a1 = spend(t1, alpha)
    c1 = _z(1 - a1)
    rho, target = math.sqrt(t1 / t2), spend(t2, alpha) - a1
    lo, hi = 0.0, 6.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if _cross2(c1, mid, rho) > target:
            lo = mid
        else:
            hi = mid
    c2 = (lo + hi) / 2
    return {'spending': "O'Brien-Fleming-type Lan-DeMets", 'alpha': alpha, 'information': list(information),
            'cumulative_alpha': [a1, spend(t2, alpha)], 'c1': c1, 'c2': c2, 'alpha1': a1, 'alpha2': normal_upper(c2)}


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


def h1_look(blocks, c, alpha, exclude_retrieval=False):
    n, b, cc, ex = pairs(blocks, H1[0], H1[1], exclude_retrieval=exclude_retrieval)
    z = tango_z(b, cc, n, -MARGIN) if n else None
    return {'valid_blocks': n, 'only_drafted-review': b, 'only_given': cc, 'difference': (b - cc) / n if n else None,
            'z': z, 'critical_z': c, 'nominal_alpha': alpha, 'p_one_sided': normal_upper(z) if n else 1.0,
            'lower_bound': tango_lower(b, cc, n, alpha) if n else None, 'upper_bound': tango_upper(b, cc, n, alpha) if n else None,
            **({'excluded_retrieval_blocks': ex} if exclude_retrieval else {})}


def stage1_decision(look, bounds):
    """The pre-registered stage-1 rule, on the H1 statistic only."""
    if look['valid_blocks'] and look['z'] > bounds['c1']:
        return 'stop-noninferior'
    if look['valid_blocks'] and look['z'] < -bounds['c1']:  # (1 - alpha1) upper bound of the difference below -margin
        return 'stop-futility'
    return 'continue'


def h1(stage1, stage2, exclude_retrieval=False, bounds=None, strict=True):
    """Group-sequential H1. stage2: the stage-2 blocks (empty if not run). Returns looks, stage1_decision, final_decision."""
    bounds = bounds or boundaries()
    first = h1_look(stage1, bounds['c1'], bounds['alpha1'], exclude_retrieval)
    decision = stage1_decision(first, bounds)
    out = {'comparison': '%s non-inferior to %s (margin %.2f)' % (H1[0], H1[1], MARGIN), 'test': 'Tango score, one-sided',
           'boundaries': bounds, 'stage1': first, 'stage1_decision': decision}
    if decision != 'continue':
        if stage2 and strict:
            raise ValueError('stage-2 data present although stage 1 stopped (%s)' % decision)
        out['final_decision'] = 'noninferior' if decision == 'stop-noninferior' else 'worse'
    elif not stage2:
        out['final_decision'] = 'pending-stage2'
    else:
        final = h1_look(stage1 + stage2, bounds['c2'], bounds['alpha2'], exclude_retrieval)
        out['stage2'] = final
        out['final_decision'] = 'noninferior' if final['valid_blocks'] and final['z'] > bounds['c2'] else 'not-noninferior'
    out['holds'] = out['final_decision'] == 'noninferior' if out['final_decision'] != 'pending-stage2' else None
    return out


def hypotheses(stage1, stage2, exclude_retrieval=False):
    # The sensitivity analysis follows its own stage-1 decision; stage-2 data run under the main decision is not an error there.
    out = {'H1': h1(stage1, stage2, exclude_retrieval, strict=not exclude_retrieval)}
    ps = {}
    for h, (hi, lo) in SUPERIORITY.items():
        n, w, l, ex = pairs(stage1, hi, lo, exclude_retrieval=exclude_retrieval)
        ps[h] = sign_p(w, l)
        out[h] = {'comparison': '%s > %s' % (hi, lo), 'test': 'exact paired sign, one-sided, stage-1 blocks', 'valid_blocks': n,
                  'only_higher': w, 'only_lower': l, 'p_one_sided': ps[h],
                  **({'excluded_retrieval_blocks': ex} if exclude_retrieval else {})}
    for h, p in holm(ps).items():
        out[h]['p_holm'] = p
        out[h]['holds'] = p < ALPHA_FAMILY
    return out


# ---- entry correctness (descriptive) ------------------------------------------------------------------

CATEGORY = {'event': 'event', 'fingerprint': 'fingerprint', 'record': 'record', 'separation': 'separation',
            'role': 'authority', 'subject': 'authority', 'from': 'state', 'to': 'state', 'status': 'state', 'tier': 'state',
            'kind': 'shape', 'column': 'shape', 'param': 'shape', 'response': 'shape', 'inputs': 'shape', 'confirmAmount': 'shape',
            'name': 'name', 'denial': 'denial', 'catalog': 'catalog'}


def entry_table(trials):
    """C, the error distribution by logical field and category, and (drafted-review) what the review did."""
    obs = [t for t in trials if t['observed']]
    out = {'observed': len(obs), 'C': sum(bool(t['C']) for t in obs), 'no_frozen_entry': sum(t['wrong'] is None for t in obs)}
    wrong = [t['wrong'] for t in obs if t['wrong'] is not None]
    out['wrong_by_field'] = {f: sum(f in w for w in wrong) for f in FIELDS}
    out['wrong_by_category'] = {c: sum(any(CATEGORY[f] == c for f in w) for w in wrong) for c in CATEGORIES}
    out['A_given_C'] = {'C=1': [sum(bool(t['A']) for t in obs if t['C']), sum(bool(t['C']) for t in obs)],
                        'C=0': [sum(bool(t['A']) for t in obs if not t['C']), sum(not t['C'] for t in obs)]}
    if obs and obs[0]['cell'] == 'drafted-review':
        out['C_draft'] = sum(bool(t['C_draft']) for t in obs)
        out['review'] = {'fixed': sum(not t['C_draft'] and t['C'] for t in obs), 'broke': sum(t['C_draft'] and not t['C'] for t in obs),
                         'kept_correct': sum(t['C_draft'] and t['C'] for t in obs),
                         'kept_wrong': sum(not t['C_draft'] and not t['C'] for t in obs),
                         'changed_catalog': sum(bool(t['review_changed']) for t in obs)}
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
                                for st in ('draft', 'review', 'adapter')}
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


def analyze(runs, seed=20261116, resamples=10000):
    lanes, lanes2 = load(runs)
    blocks, blocks2 = blocks_of(lanes), blocks_of(lanes2)
    rng = random.Random(seed)
    per_lane = [{'task': l['task'], 'model': l['model'], 'rates': {c: rates([b[c] for b in l['blocks']]) for c in CELLS},
                 'entry': {c: entry_table([b[c] for b in l['blocks']]) for c in STAGED},
                 'cost': cost(l['blocks'], rng, resamples)} for l in lanes]
    primary = hypotheses(blocks, blocks2)
    return {'version': 8, 'experiment': 'v8a', 'seed': seed, 'resamples': resamples, 'lanes': [[l['task'], l['model']] for l in lanes],
            'stage2_lanes': [[l['task'], l['model']] for l in lanes2],
            'primary_and_secondary': primary,
            'stage1_decision': primary['H1']['stage1_decision'], 'final_decision': primary['H1']['final_decision'],
            'sensitivity_excluding_retrieval_attempt': hypotheses(blocks, blocks2, exclude_retrieval=True),
            'rates': {c: rates([b[c] for b in blocks]) for c in CELLS},
            'entry': {c: entry_table([b[c] for b in blocks]) for c in STAGED},
            'safety': safety_table(lanes), 'cost': cost(blocks, rng, resamples), 'per_lane': per_lane,
            'audit': audit(lanes + lanes2), 'telemetry_hosts_not_counted': sorted(TELEMETRY)}


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
