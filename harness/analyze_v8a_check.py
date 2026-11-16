#!/usr/bin/env python3
"""Self-check of the v8a analysis statistics (no data): Tango score test properties, type I error at the margin,
power under the planning scenarios used in the preregistration. Usage: analyze_v8a_check.py [--run DIR ...]
(with runs: also runs analyze_v8a on them as a format check)."""
import argparse
import json
import math
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_v8a as a  # noqa: E402


def simulate(q12, q21, n, reps, rng):
    hits = 0
    for _ in range(reps):
        b = c = 0
        for _ in range(n):
            u = rng.random()
            b += u < q12
            c += q12 <= u < q12 + q21
        hits += a.normal_upper(a.tango_z(b, c, n, -a.MARGIN)) < a.ALPHA_PRIMARY
    return hits / reps


def main():
    p = argparse.ArgumentParser(); p.add_argument('--run', action='append', default=[]); p.add_argument('--reps', type=int, default=4000)
    args = p.parse_args()
    out = {}
    # z = 0 at the observed difference; decreasing in delta0; lower bound is where z hits the critical value.
    for b, c, n in ((3, 5, 48), (0, 0, 48), (0, 4, 48), (10, 2, 48)):
        d = (b - c) / n
        lb = a.tango_lower(b, c, n, a.ALPHA_PRIMARY)
        zs = [a.tango_z(b, c, n, x / 100) for x in range(-60, int(d * 100))]
        assert abs(a.tango_z(b, c, n, d)) < 1e-9 if (b or c) else True
        assert all(x >= y - 1e-12 for x, y in zip(zs, zs[1:])), (b, c, n)
        assert lb <= d and (lb == -1.0 or abs(a.tango_z(b, c, n, lb) - a._z(1 - a.ALPHA_PRIMARY)) < 1e-6), (b, c, n, lb)
        out['b=%d c=%d n=%d' % (b, c, n)] = {'difference': round(d, 4), 'z_at_margin': round(a.tango_z(b, c, n, -a.MARGIN), 4),
                                             'p': round(a.normal_upper(a.tango_z(b, c, n, -a.MARGIN)), 5), 'lower_bound': round(lb, 4)}
    # Large n: the score test agrees with the Wald test.
    b, c, n = 500, 600, 10000
    d = (b - c) / n
    d0 = d - 0.01  # near the observed difference the restricted and observed variances agree
    wald = (d - d0) / math.sqrt(((b + c) / n - d * d) / n)
    assert abs(a.tango_z(b, c, n, d0) - wald) / wald < 0.02, (a.tango_z(b, c, n, d0), wald)
    assert abs(a._z(0.975) - 1.959964) < 1e-5
    rng = random.Random(20261116)
    n = 48
    out['type_I_at_margin'] = {'q12=0.02 q21=0.12': simulate(0.02, 0.12, n, args.reps, rng),
                               'q12=0.00 q21=0.10': simulate(0.00, 0.10, n, args.reps, rng)}
    assert all(v <= a.ALPHA_PRIMARY + 0.015 for v in out['type_I_at_margin'].values()), out['type_I_at_margin']
    out['power_n48'] = {'equal (q12=q21=0.02)': simulate(0.02, 0.02, n, args.reps, rng),
                        'equal (q12=q21=0.05)': simulate(0.05, 0.05, n, args.reps, rng),
                        '-3 pp (q12=0.01 q21=0.04)': simulate(0.01, 0.04, n, args.reps, rng),
                        '-5 pp (q12=0.02 q21=0.07)': simulate(0.02, 0.07, n, args.reps, rng)}
    for r in args.run:
        a.analyze([r], resamples=200)
    out['format_check_runs'] = len(args.run)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
