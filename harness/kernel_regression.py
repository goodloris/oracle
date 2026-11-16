#!/usr/bin/env python3
"""Private kernel regression: the v8b overlay kernel pairs old values by id (public fix fad8adf, and the new amend kind).

Builds the pinned public tree with the amend overlay (harness/verifier.py), adds
harness/kernel/opkernel_v8b_test.go.txt, and runs it on a disposable *_poc database with ICU collation and with
the database default collation. Negative controls: the same test on the overlay kernel with positional pairing
(field update, or amend) must fail with the matching message.
Needs ADMIN_DATABASE_URL, DATABASE_URL (POC_DB_RESET is set here) and optionally POC_GO.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'harness'))
import verifier  # noqa: E402

TEST = '^TestV8bOldValuesPairByIDUnderLinguisticCollation$'
# Field update, pre-fix behaviour: values in database (collation) order, paired by position.
FIELD = [('''	previous := make([]*string, len(ids))
	for i, id := range ids {
		old, ok := byID[id]
		if !ok {
			return nil, &Rejection{op.Denial}
		}
		previous[i] = old
	}
	return previous, nil''', '''	previous := []*string{}
	for _, id := range ids {
		if _, ok := byID[id]; !ok {
			return nil, &Rejection{op.Denial}
		}
	}
	previous = append(previous, ordered...)
	return previous, nil'''),
         ('\t\tbyID[id] = old\n', '\t\tbyID[id] = old\n\t\tordered = append(ordered, old)\n'),
         ('\tbyID := map[string]*string{}\n', '\tbyID := map[string]*string{}\n\tvar ordered []*string\n')]
# Amend: old amounts taken in database order, paired by position.
AMEND = [('\told := map[string]int{}\n', '\told := map[string]int{}\n\tvar ordered []int\n'),
         ('\t\told[id] = amount\n', '\t\told[id] = amount\n\t\tordered = append(ordered, amount)\n'),
         ('\t\tfrom, ok := old[id]\n', '\t\t_, ok := old[id]\n\t\tfrom := ordered[i]\n')]
MESSAGE = {'field': 'assignment history pairs old value with its own target', 'amend': 'amend history pairs old value with its own target'}


def run(patch, collation):
    src = verifier.public()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        archive = subprocess.run(['git', '-C', str(src), 'archive', verifier.PUBLIC_COMMIT], check=True, capture_output=True).stdout
        subprocess.run(['tar', '-x', '-C', str(tmp)], input=archive, check=True)
        for p, template in verifier.overlay('amend').items():
            (tmp / p).write_bytes((ROOT / template).read_bytes())
        kernel = tmp / 'internal/opkernel/opkernel.go'
        text = kernel.read_text()
        for old, new in patch:
            assert text.count(old) == 1, old
            text = text.replace(old, new)
        kernel.write_text(text)
        (tmp / 'tasks/amend').mkdir(parents=True)
        (tmp / 'tasks/amend/schema.sql').write_bytes((ROOT / 'tasks/amend/schema.sql').read_bytes())
        (tmp / 'internal/opkernel/opkernel_v8b_test.go').write_bytes((ROOT / 'harness/kernel/opkernel_v8b_test.go.txt').read_bytes())
        r = subprocess.run([os.environ.get('POC_GO', 'go'), 'test', './internal/opkernel', '-count=1', '-v', '-run', TEST],
                           cwd=tmp, capture_output=True, text=True, timeout=900,
                           env={**os.environ, 'POC_DB_RESET': '1', 'V8B_ID_COLLATION': collation})
        out = r.stdout + r.stderr
        return {'returncode': r.returncode, 'pass': '--- PASS: TestV8b' in out, 'skip': '--- SKIP' in out,
                'not_linguistic': 'collation not linguistic' in out, 'tail': out[-600:]}


def mode(collation):
    fixed = run([], collation)
    out = {'fixed_kernel': fixed, 'ok': fixed['pass'] and not fixed['skip']}
    for name, patch in (('field', FIELD), ('amend', AMEND)):
        neg = run(patch, collation)
        out['positional_%s_negative' % name] = neg
        out['ok'] = out['ok'] and neg['returncode'] != 0 and not neg['pass'] and MESSAGE[name] in neg['tail']
    return out


def main():
    """ICU always; the database default collation too. A byte-order default (musl/alpine, C) cannot show the bug:
    it is reported as not applicable unless POC_REQUIRE_LINGUISTIC_DEFAULT=1 (set on the evaluation host)."""
    icu, default = mode('icu'), mode('default')
    required = os.environ.get('POC_REQUIRE_LINGUISTIC_DEFAULT') == '1'
    default['applicable'] = not default['fixed_kernel']['not_linguistic']
    report = {'public_commit': verifier.PUBLIC_COMMIT, 'icu': icu, 'database_default': default,
              'ok': icu['ok'] and (default['ok'] if (required or default['applicable']) else True)}
    print(json.dumps(report))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
