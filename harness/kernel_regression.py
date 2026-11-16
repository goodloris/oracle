#!/usr/bin/env python3
"""Private kernel regression: the v7 overlay kernel pairs old field values by id (public fix fad8adf).

Builds the pinned public tree with the cost-center overlay (harness/verifier.py), adds
harness/kernel/opkernel_v7_test.go.txt, and runs it on a disposable *_poc database with ICU collation.
Negative control: the same test on the overlay kernel with the pre-fix positional pairing must fail.
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

FIXED = '''	previous := make([]*string, len(ids))
	for i, id := range ids {
		old, ok := byID[id]
		if !ok {
			return nil, &Rejection{op.Denial}
		}
		previous[i] = old
	}
	return previous, nil'''
# Pre-fix behaviour: values in database (collation) order, paired by position.
POSITIONAL = '''	previous := []*string{}
	for _, id := range ids {
		if _, ok := byID[id]; !ok {
			return nil, &Rejection{op.Denial}
		}
	}
	for _, old := range ordered {
		previous = append(previous, old)
	}
	return previous, nil'''


def run(kernel_patch):
    src = verifier.public()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        archive = subprocess.run(['git', '-C', str(src), 'archive', verifier.PUBLIC_COMMIT], check=True, capture_output=True).stdout
        subprocess.run(['tar', '-x', '-C', str(tmp)], input=archive, check=True)
        for p, template in verifier.overlay('cost-center').items():
            (tmp / p).write_bytes((ROOT / template).read_bytes())
        kernel = tmp / 'internal/opkernel/opkernel.go'
        text = kernel.read_text()
        if kernel_patch:
            assert text.count(FIXED) == 1 and text.count('\t\tbyID[id] = old\n') == 1
            text = text.replace(FIXED, POSITIONAL).replace('\t\tbyID[id] = old\n', '\t\tbyID[id] = old\n\t\tordered = append(ordered, old)\n')
            text = text.replace('\tbyID := map[string]*string{}\n', '\tbyID := map[string]*string{}\n\tvar ordered []*string\n')
            kernel.write_text(text)
        (tmp / 'tasks/cost-center').mkdir(parents=True)
        (tmp / 'tasks/cost-center/schema.sql').write_bytes((ROOT / 'tasks/cost-center/schema.sql').read_bytes())
        (tmp / 'internal/opkernel/opkernel_v7_test.go').write_bytes((ROOT / 'harness/kernel/opkernel_v7_test.go.txt').read_bytes())
        r = subprocess.run([os.environ.get('POC_GO', 'go'), 'test', './internal/opkernel', '-count=1', '-v',
                            '-run', '^TestV7FieldUpdatesPairOldValuesByIDUnderLinguisticCollation$'],
                           cwd=tmp, capture_output=True, text=True, timeout=900, env={**os.environ, 'POC_DB_RESET': '1'})
        out = r.stdout + r.stderr
        return {'returncode': r.returncode, 'pass': '--- PASS: TestV7' in out, 'skip': '--- SKIP' in out, 'tail': out[-600:]}


def main():
    fixed, positional = run(False), run(True)
    report = {'public_commit': verifier.PUBLIC_COMMIT, 'fixed_kernel': fixed, 'positional_kernel_negative': positional,
              'ok': fixed['pass'] and not fixed['skip'] and positional['returncode'] != 0 and not positional['pass']
              and 'pairs old value with its own target' in positional['tail']}
    print(json.dumps(report))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
