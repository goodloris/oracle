#!/usr/bin/env python3
"""Positive/negative controls of the v8a analysis (prereg/preregistration-v8a.md). Never reads model output.

1. Tango score test: z = 0 at the observed difference, monotone in delta0, confidence bounds at the critical value,
   agreement with the Wald statistic near the observed difference.
2. Group-sequential boundaries: the O'Brien-Fleming-type Lan-DeMets spending at t = 0.5 and 1.0, recomputed by an
   independent route (Monte Carlo of the correlated look statistics, 400 000 draws) and by direct integration:
   P(Z1 > c1) = alpha(0.5); P(Z1 > c1 or Z2 > c2) = alpha(1) = 0.025.
3. The four decision paths on synthetic blocks run through the same functions the analysis uses (hypotheses()):
   stop-noninferior, stop-futility, continue -> noninferior, continue -> not-noninferior; plus the exact boundary
   (z just above / below c1), stage-2 data after a stop (must be refused), and secondary hypotheses on stage-1 blocks only.
4. Operating characteristics of the whole design with Tango's statistic (simulation): type I error at the margin,
   power under the planning scenarios, probability of each stage-1 decision.
5. With --run: analyze_v8a on real run directories (format check).
Usage: analyze_v8a_check.py [--run DIR ...] [--reps N]   -> JSON report, exit 0 iff every check holds.
"""
import argparse
import json
import math
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_v8a as a  # noqa: E402

CHECKS = []


def check(name, ok, **detail):
    CHECKS.append({'check': name, 'ok': bool(ok), **detail})
    return ok


def tango_properties():
    for b, c, n in ((3, 5, 48), (0, 0, 48), (0, 4, 48), (10, 2, 48), (2, 8, 96)):
        d = (b - c) / n
        zs = [a.tango_z(b, c, n, x / 100) for x in range(-60, 60)]
        lb, ub = a.tango_lower(b, c, n, 0.025), a.tango_upper(b, c, n, 0.025)
        check('tango b=%d c=%d n=%d' % (b, c, n),
              (not (b or c) or abs(a.tango_z(b, c, n, d)) < 1e-9) and all(x >= y - 1e-12 for x, y in zip(zs, zs[1:]))
              and lb <= d <= ub and abs(a.tango_z(b, c, n, lb) - a._z(0.975)) < 1e-6 and abs(a.tango_z(b, c, n, ub) + a._z(0.975)) < 1e-6,
              difference=round(d, 4), z_margin=round(a.tango_z(b, c, n, -a.MARGIN), 4), lower=round(lb, 4), upper=round(ub, 4))
    b, c, n = 500, 600, 10000
    d = (b - c) / n
    wald = 0.01 / math.sqrt(((b + c) / n - d * d) / n)
    check('tango ~ wald near the estimate', abs(a.tango_z(b, c, n, d - 0.01) - wald) / wald < 0.02)


def boundary_checks(rng):
    bd = a.boundaries()
    t1 = a.INFORMATION[0]
    # closed form of the spending function at t1 and t = 1
    closed = 2 * (1 - 0.5 * math.erfc(-(a._z(1 - 0.025 / 2) / math.sqrt(t1)) / math.sqrt(2)))
    check('alpha1 = spending(0.5) closed form', abs(bd['alpha1'] - closed) < 1e-12 and abs(bd['cumulative_alpha'][1] - 0.025) < 1e-12,
          alpha1=bd['alpha1'])
    check('c1 = Phi^-1(1 - alpha1)', abs(a.normal_upper(bd['c1']) - bd['alpha1']) < 1e-12, c1=bd['c1'])
    rho = math.sqrt(t1)
    integral = bd['alpha1'] + a._cross2(bd['c1'], bd['c2'], rho, steps=20000)
    check('integral: P(Z1>c1 or Z2>c2) = 0.025', abs(integral - 0.025) < 1e-7, value=integral)
    hits1 = hits = 0
    draws = 400000
    for _ in range(draws):
        z1 = rng.gauss(0, 1)
        z2 = rho * z1 + math.sqrt(1 - rho * rho) * rng.gauss(0, 1)
        hits1 += z1 > bd['c1']
        hits += z1 > bd['c1'] or z2 > bd['c2']
    se1, se = math.sqrt(bd['alpha1'] / draws), math.sqrt(0.025 / draws)
    check('monte carlo: P(Z1>c1) = alpha1', abs(hits1 / draws - bd['alpha1']) < 4 * se1, value=hits1 / draws)
    check('monte carlo: overall crossing = 0.025', abs(hits / draws - 0.025) < 4 * se, value=hits / draws)
    check('nominal alphas near the stated values', abs(bd['alpha1'] - 0.0015) < 0.0001 and abs(bd['alpha2'] - 0.0245) < 0.0001,
          alpha1=round(bd['alpha1'], 6), alpha2=round(bd['alpha2'], 6), c1=round(bd['c1'], 4), c2=round(bd['c2'], 4))
    return bd


# ---- synthetic blocks ----------------------------------------------------------------------------------------

def t(cell, inst, passed, observed=True):
    return {'id': '%s--%03d' % (cell, inst), 'cell': cell, 'instance': inst, 'observed': observed, 'A': passed if observed else None,
            'retrieval_attempt': []}


def blocks(pattern, first=1, secondary=True):
    """pattern: list of (drafted-review A, given A) per block; stage-1 blocks also get manual=False, drafted=True."""
    out = []
    for k, (dr, g) in enumerate(pattern):
        i = first + k
        b = {'drafted-review': t('drafted-review', i, dr), 'given': t('given', i, g)}
        if secondary:
            b.update({'manual': t('manual', i, False), 'drafted': t('drafted', i, True)})
        out.append(b)
    return out


def pattern(n, only_dr, only_given, both=None):
    both = n - only_dr - only_given if both is None else both
    p = [(True, False)] * only_dr + [(False, True)] * only_given + [(True, True)] * both
    return p + [(False, False)] * (n - len(p))


def decisions(bd):
    z = lambda b, c, n: a.tango_z(b, c, n, -a.MARGIN)
    cases = {
        # 48 pairs, drafted-review better in 6 blocks: z(-0.10) > c1
        'stop-noninferior': (pattern(48, 6, 0), None, 'stop-noninferior', 'noninferior'),
        # drafted-review worse by 17 of 48: upper (1-alpha1) bound below -0.10
        'stop-futility': (pattern(48, 0, 17), None, 'stop-futility', 'worse'),
        # equal in stage 1 (z = 2.31 < c1), equal again in stage 2: 96 pairs, z = 3.27 > c2
        'continue-noninferior': (pattern(48, 0, 0), pattern(48, 0, 0), 'continue', 'noninferior'),
        # drafted-review worse by 4/48 in both stages: z at 96 pairs below c2
        'continue-not-noninferior': (pattern(48, 1, 5), pattern(48, 1, 5), 'continue', 'not-noninferior'),
        'continue-pending': (pattern(48, 0, 0), [], 'continue', 'pending-stage2'),
    }
    for name, (p1, p2, d1, final) in cases.items():
        s1 = blocks(p1)
        s2 = blocks(p2, first=49, secondary=False) if p2 else []
        r = a.hypotheses(s1, s2)['H1']
        n1, b1, c1 = len(p1), sum(x and not y for x, y in p1), sum(y and not x for x, y in p1)
        check('decision ' + name, r['stage1_decision'] == d1 and r['final_decision'] == final
              and abs(r['stage1']['z'] - z(b1, c1, n1)) < 1e-12,
              stage1_z=round(r['stage1']['z'], 4), final_z=round(r['stage2']['z'], 4) if 'stage2' in r else None,
              stage1_decision=r['stage1_decision'], final_decision=r['final_decision'])
    # futility rule = upper confidence bound statement
    p = pattern(48, 0, 17)
    r = a.hypotheses(blocks(p), [])['H1']['stage1']
    check('futility: upper (1-alpha1) bound below -margin', r['upper_bound'] < -a.MARGIN, upper=round(r['upper_bound'], 4))
    p = pattern(48, 0, 12)
    r = a.hypotheses(blocks(p), [])['H1']
    check('no futility stop when the upper bound reaches -margin', r['stage1_decision'] == 'continue' and r['stage1']['upper_bound'] >= -a.MARGIN,
          upper=round(r['stage1']['upper_bound'], 4))
    # exact boundary: a bounds dict with c1 placed just above / below the observed z
    p = pattern(48, 4, 0)
    zz = z(4, 0, 48)
    for eps, want in ((1e-9, 'continue'), (-1e-9, 'stop-noninferior')):
        r = a.h1(blocks(p), [], bounds={**bd, 'c1': zz + eps})
        check('boundary c1 %+g' % eps, r['stage1_decision'] == want, z=zz)
    # stage-2 data after a stop is refused
    try:
        a.hypotheses(blocks(pattern(48, 6, 0)), blocks(pattern(48, 0, 0), first=49, secondary=False))
        refused = False
    except ValueError:
        refused = True
    check('stage-2 data after a stop is refused', refused)
    # unmeasured blocks leave the denominator; secondaries ignore stage-2 blocks
    s1 = blocks(pattern(48, 0, 0))
    s1[0]['given'] = t('given', 1, None, observed=False)
    r = a.hypotheses(s1, blocks(pattern(48, 0, 0), first=49, secondary=False))
    check('denominator excludes unmeasured blocks', r['H1']['stage1']['valid_blocks'] == 47 and r['H1']['stage2']['valid_blocks'] == 95)
    check('secondary hypotheses use stage-1 blocks only', r['H2']['valid_blocks'] == 48 and r['H3']['valid_blocks'] == 48 and r['H2']['holds'])


# ---- operating characteristics ---------------------------------------------------------------------------------

def design(q12, q21, reps, rng, bd, n1=48, n2=48):
    """Whole design with Tango's statistic. q12 = P(only drafted-review passes), q21 = P(only given passes)."""
    counts = {'stop-noninferior': 0, 'stop-futility': 0, 'continue': 0}
    holds = 0

    def draw(n):
        b = c = 0
        for _ in range(n):
            u = rng.random()
            b += u < q12
            c += q12 <= u < q12 + q21
        return b, c
    for _ in range(reps):
        b1, c1 = draw(n1)
        zz = a.tango_z(b1, c1, n1, -a.MARGIN)
        d = 'stop-noninferior' if zz > bd['c1'] else ('stop-futility' if zz < -bd['c1'] else 'continue')
        counts[d] += 1
        if d == 'stop-noninferior':
            holds += 1
        elif d == 'continue':
            b2, c2 = draw(n2)
            holds += a.tango_z(b1 + b2, c1 + c2, n1 + n2, -a.MARGIN) > bd['c2']
    return {'P(H1 holds)': holds / reps, **{'P(%s)' % k: v / reps for k, v in counts.items()}}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--run', action='append', default=[])
    p.add_argument('--reps', type=int, default=4000)
    args = p.parse_args()
    rng = random.Random(20261116)
    tango_properties()
    bd = boundary_checks(rng)
    decisions(bd)
    oc = {'at margin (q12=0.02, q21=0.12)': design(0.02, 0.12, args.reps, rng, bd),
          'at margin (q12=0.00, q21=0.10)': design(0.00, 0.10, args.reps, rng, bd),
          'equal (q12=q21=0.02)': design(0.02, 0.02, args.reps, rng, bd),
          'equal (q12=q21=0.05)': design(0.05, 0.05, args.reps, rng, bd),
          '-3 pp (q12=0.01, q21=0.04)': design(0.01, 0.04, args.reps, rng, bd),
          '-20 pp (q12=0.00, q21=0.20)': design(0.00, 0.20, args.reps, rng, bd)}
    for k in ('at margin (q12=0.02, q21=0.12)', 'at margin (q12=0.00, q21=0.10)'):
        check('type I at the margin <= 0.025 + 3 se: ' + k, oc[k]['P(H1 holds)'] <= 0.025 + 3 * math.sqrt(0.025 / args.reps),
              value=oc[k]['P(H1 holds)'])
    for r in args.run:
        a.analyze([r], resamples=200)
    if args.run:
        v = a.analyze(args.run, resamples=200)
        check('analysis of %d run(s) has both decisions' % len(args.run), v['stage1_decision'] in ('stop-noninferior', 'stop-futility', 'continue')
              and v['final_decision'] in ('noninferior', 'not-noninferior', 'worse', 'pending-stage2'),
              stage1_decision=v['stage1_decision'], final_decision=v['final_decision'])
    report = {'boundaries': bd, 'checks': CHECKS, 'operating_characteristics': oc, 'reps': args.reps,
              'all_ok': all(c['ok'] for c in CHECKS)}
    print(json.dumps(report, indent=2))
    return 0 if report['all_ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
