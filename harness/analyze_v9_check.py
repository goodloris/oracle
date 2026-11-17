#!/usr/bin/env python3
"""Positive/negative controls of the v9 analysis (prereg/preregistration-v9.md). Never reads model output.

1. Tango score test: z = 0 at the observed difference, monotone in delta0, confidence bounds at the critical value.
2. Decisions (margin 10 pp) and the descriptive 95% interval / +-5 pp flag on synthetic blocks through hypotheses(): H1 holds / does not hold, the exact boundary, unmeasured blocks
   leaving the denominator; H2 holds / does not hold.
3. Retrieval audit: localhost probes are not retrieval, external fetches are.
4. Operating characteristics (simulation): type I error at the 5-point margin, power of H1 at 96 pairs under the
   planning scenarios (margin 10 pp; 5 pp for reference).
5. With --run: analyze_v9 on real run directories (format check).
Usage: analyze_v9_check.py [--run DIR ...] [--reps N]   -> JSON report, exit 0 iff every check holds.
"""
import argparse
import json
import math
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_v9 as a  # noqa: E402

CHECKS = []


def check(name, ok, **detail):
    CHECKS.append({'check': name, 'ok': bool(ok), **detail})


def t(cell, inst, passed, observed=True, retrieval=()):
    return {'id': '%s--%03d' % (cell, inst), 'cell': cell, 'instance': inst, 'observed': observed,
            'A': passed if observed else None, 'retrieval_attempt': list(retrieval)}


def blocks(rows):
    """rows: (given A, drafted-norule A, drafted-rule A) per block."""
    return [{'given': t('given', i + 1, g), 'drafted-norule': t('drafted-norule', i + 1, n), 'drafted-rule': t('drafted-rule', i + 1, r)}
            for i, (g, n, r) in enumerate(rows)]


def tango_properties():
    zc = a._z(0.975)
    for b, c, n in ((0, 0, 96), (2, 2, 96), (1, 4, 96), (6, 1, 96)):
        d = (b - c) / n
        zs = [a.tango_z(b, c, n, x / 100) for x in range(-40, 40)]
        lb, ub = a.tango_lower(b, c, n, 0.025), a.tango_upper(b, c, n, 0.025)
        check('tango b=%d c=%d n=%d' % (b, c, n),
              (not (b or c) or abs(a.tango_z(b, c, n, d)) < 1e-9) and all(x >= y - 1e-12 for x, y in zip(zs, zs[1:]))
              and lb <= d <= ub and abs(a.tango_z(b, c, n, lb) - zc) < 1e-6 and abs(a.tango_z(b, c, n, ub) + zc) < 1e-6,
              z_margin=round(a.tango_z(b, c, n, -a.MARGIN), 4), lower=round(lb, 4))


def decisions():
    T, F = True, False
    # H1 holds: 96 blocks, rule and given agree everywhere (z(-0.05) = 2.25 > 1.96)
    r = a.hypotheses(blocks([(T, F, T)] * 96))
    check('H1 holds when rule equals given', r['H1']['holds'] and r['H1']['valid_blocks'] == 96, z=round(r['H1']['z'], 4))
    # margin is 10 pp (v8a's), the descriptive band 5 pp
    check('margin 0.10, band 0.05', a.MARGIN == 0.10 and a.EQUIVALENCE_BAND == 0.05)
    # H1 holds at -3 pp of 96 (lower bound above -10 pp) but the 95% interval is not within +-5 pp
    r = a.hypotheses(blocks([(T, T, F)] * 3 + [(T, T, T)] * 93))
    d = r['H1']['descriptive_interval']
    check('H1 holds at -3 pp; interval not within 5 pp', r['H1']['holds'] and r['H1']['lower_bound'] > -0.10 and not d['within_5pp']
          and d['ci95'] == [r['H1']['lower_bound'], r['H1']['upper_bound']], z=round(r['H1']['z'], 4), ci95=[round(x, 4) for x in d['ci95']])
    # H1 fails at -7.3 pp (the v8a estimate: 2 vs 9 of 96)
    r = a.hypotheses(blocks([(F, T, T)] * 2 + [(T, T, F)] * 9 + [(T, T, T)] * 85))
    check('H1 fails at the v8a result (2:9 of 96)', not r['H1']['holds'], z=round(r['H1']['z'], 4), lower=round(r['H1']['lower_bound'], 4))
    # H1 fails when rule is clearly worse
    r = a.hypotheses(blocks([(T, T, F)] * 20 + [(T, T, T)] * 76))
    check('H1 fails at -20.8 pp; upper bound below -margin', not r['H1']['holds'] and r['H1']['upper_bound'] < -a.MARGIN)
    # within 5 pp: no discordant pair in 96 gives a 95% interval of about +-3.85 pp
    r = a.hypotheses(blocks([(T, F, T)] * 96))
    check('equal arms: interval within 5 pp', r['H1']['descriptive_interval']['within_5pp'], ci95=[round(x, 4) for x in r['H1']['descriptive_interval']['ci95']])
    # boundary: the decision is z > z_0.975
    zc = a._z(0.975)
    for rows in ([(T, T, F)] * 6 + [(T, T, T)] * 90, [(T, T, F)] * 5 + [(T, T, T)] * 91, [(F, T, T)] * 1 + [(T, T, T)] * 95):
        r = a.hypotheses(blocks(rows))['H1']
        check('H1 decision equals z > critical (z=%.3f)' % r['z'], r['holds'] == (r['z'] > zc))
    # unmeasured blocks leave the denominator
    bl = blocks([(T, T, T)] * 96)
    bl[0]['given'] = t('given', 1, None, observed=False)
    bl[1]['drafted-norule'] = t('drafted-norule', 2, None, observed=False)
    r = a.hypotheses(bl)
    check('denominators: H1 95, H2 95', r['H1']['valid_blocks'] == 95 and r['H2']['valid_blocks'] == 95)
    # H2 holds: rule passes 8 blocks norule fails, none the other way (p = 2^-8)
    r = a.hypotheses(blocks([(T, F, T)] * 8 + [(T, T, T)] * 88))
    check('H2 holds at 8:0', r['H2']['holds'] and abs(r['H2']['p_one_sided'] - 2 ** -8) < 1e-15)
    r = a.hypotheses(blocks([(T, F, T)] * 4 + [(T, T, F)] * 2 + [(T, T, T)] * 90))
    check('H2 fails at 4:2', not r['H2']['holds'], p=r['H2']['p_one_sided'])
    # sensitivity excludes retrieval blocks per hypothesis
    bl = blocks([(T, F, T)] * 96)
    bl[0]['drafted-rule'] = t('drafted-rule', 1, True, retrieval=['curl https://example.com'])
    r = a.hypotheses(bl, exclude_retrieval=True)
    check('sensitivity drops retrieval blocks', r['H1']['valid_blocks'] == 95 and r['H1']['excluded_retrieval_blocks'] == 1)


def retrieval():
    yes = ['curl https://pkg.go.dev/x', 'wget http://a.b', 'git clone https://github.com/x/y', 'kiro-cli chat', 'curl -s example.com']
    no = ['curl -s http://127.0.0.1:8080/health', 'curl localhost:8080/health', 'go run ./cmd/opcheck', 'go test ./...']
    check('retrieval regex', all(a.RETRIEVAL.search(c) for c in yes) and not any(a.RETRIEVAL.search(c) for c in no))


def power(q12, q21, n, reps, rng, margin=a.MARGIN):
    hits = 0
    for _ in range(reps):
        b = c = 0
        for _ in range(n):
            u = rng.random()
            b += u < q12
            c += q12 <= u < q12 + q21
        hits += a.normal_upper(a.tango_z(b, c, n, -margin)) < a.ALPHA_PRIMARY
    return hits / reps


def main():
    p = argparse.ArgumentParser(); p.add_argument('--run', action='append', default=[]); p.add_argument('--reps', type=int, default=4000)
    args = p.parse_args()
    rng = random.Random(20261118)
    tango_properties(); decisions(); retrieval()
    scen = {'equal, discordant 1% each (q12=q21=0.01)': (0.01, 0.01), 'equal, 2% each (v8a given failure rate)': (0.02, 0.02),
            'equal, 3% each': (0.03, 0.03), 'rule 1 pp lower (0.02 / 0.03)': (0.02, 0.03), 'rule 2 pp lower (0.01 / 0.03)': (0.01, 0.03),
            'rule 3 pp lower (0.00 / 0.03)': (0.00, 0.03), 'rule 5 pp lower (0.01 / 0.06)': (0.01, 0.06)}
    oc = {'margin 10 pp, 96 pairs (pre-registered)': {k: power(*v, 96, args.reps, rng) for k, v in scen.items()},
          'margin 5 pp, 96 pairs (not used; for reference)': {k: power(*v, 96, args.reps, rng, margin=0.05) for k, v in scen.items()}}
    ti = {k: power(*v, 96, args.reps, rng) for k, v in {'at margin (0.00 / 0.10)': (0.0, 0.10), 'at margin (0.02 / 0.12)': (0.02, 0.12)}.items()}
    for k, v in ti.items():
        check('type I at the margin <= 0.025 + 3 se: ' + k, v <= 0.025 + 3 * math.sqrt(0.025 / args.reps), value=v)
    if args.run:
        v = a.analyze(args.run, resamples=200)
        check('analysis of %d run(s)' % len(args.run), isinstance(v['hypotheses']['H1']['holds'], bool), lanes=v['lanes'])
    report = {'checks': CHECKS, 'power': oc, 'type_I_at_margin': ti, 'reps': args.reps, 'all_ok': all(c['ok'] for c in CHECKS)}
    print(json.dumps(report, indent=2))
    return 0 if report['all_ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
