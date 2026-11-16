#!/usr/bin/env python3
"""Write (or --check) tasks/<task>/OPERATIONS.md, the read-only catalog document of the go-manual-doc cell.

The document's JSON blocks are the go-action-opdef catalog itself: they are produced by compiling the
exported internal/opdef package (opdef.go + the task's catalog.go) with a dumper that prints opdef.All().
The prose states the same execution semantics the protected kernel implements, so the two cells receive
equivalent facts; only who executes them differs.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DUMPER = '''package main

import (
	"encoding/json"
	"os"

	"dump/opdef"
)

func main() {
	if err := opdef.Validate(); err != nil {
		panic(err)
	}
	out := json.NewEncoder(os.Stdout)
	out.SetIndent("", "  ")
	_ = out.Encode(opdef.All())
}
'''

RECORDS = {
    'ApprovalAudit': 'one row per target: tenantId, purchaseId, approvedBy = the actor, operation = name, idempotencyKey = the '
                     'Idempotency-Key, fromStatus = from, toStatus = the status the target moved to, reason = the request reason '
                     '(NULL when the entry has no reason input).',
    'AssignmentHistory': 'one row per target: tenantId, purchaseId, fromAssignee = the assigneeId before the change (NULL if none), '
                         'toAssignee = the new value, changedBy = the actor, operation = name, idempotencyKey = the Idempotency-Key.',
    'CostCenterHistory': 'one row per target: tenantId, purchaseId, fromCostCenter = the costCenterId before the change (NULL if none), '
                         'toCostCenter = the new value, changedBy = the actor, operation = name, idempotencyKey = the Idempotency-Key.',
}
VALUES = {
    'assigneeId': 'the value must be a TenantMember of the current tenant with role approver and active = true.',
    'costCenterId': 'the value must be a CostCenter code of the current tenant with active = true.',
}


def catalog(task):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'opdef').mkdir()
        (tmp / 'opdef' / 'opdef.go').write_bytes((ROOT / 'harness/source/go-action-opdef/opdef.go.txt').read_bytes())
        (tmp / 'opdef' / 'catalog.go').write_bytes((ROOT / 'tasks' / task / 'catalog.go.txt').read_bytes())
        (tmp / 'go.mod').write_text('module dump\n\ngo 1.25.0\n')
        (tmp / 'main.go').write_text(DUMPER)
        out = subprocess.run([os.environ.get('POC_GO', 'go'), 'run', '.'], cwd=tmp, check=True, capture_output=True, text=True,
                             env={**os.environ, 'GOFLAGS': '-mod=mod'})
        return json.loads(out.stdout)


def render(task, entries):
    records = sorted({e['record'] for e in entries})
    columns = sorted({e['column'] for e in entries if e.get('column')})
    lines = ['# Operation catalog (reviewed, read-only)', '',
             'This is the reviewed catalog of the business operations of this service and the authoritative statement of their',
             'semantics. It is documentation: nothing executes it. Every Action is hand-written in internal/candidate/ and must',
             'implement its entry exactly. Changing this file needs owner review.', '',
             '## What every entry means', '',
             '- `name`: the versioned operation name, stored as ActionRequest.operation and as the operation of the record rows.',
             '- `kind`: `transition` moves Purchase.status from `from` to `to`; `field-update` sets Purchase.`column` to the request',
             '  parameter named `param` on targets whose status is `status`, and keeps the status.',
             '- `role`: the only role that may call the operation. `subject`: `own` = targets the actor created, `others` = targets',
             '  created by someone else. Targets must belong to the actor\'s tenant.',
             '- `response`: the array field of a batch response, holding the sorted target IDs; a field update also returns its',
             '  parameter (`{"<response>":[...],"<param>":value}`). An entry without `response` takes one target and responds',
             '  `{"id":ID,"status":to}`.',
             '- `inputs`: the request fields, targets (`ids`) first. `fingerprint`: the inputs bound into ActionRequest.requestHash.',
             '  The digest domain is `name`, followed by `"\\n"+value` for every fingerprint field after `ids`; the hash is',
             '  ledger.Fingerprint(domain, ids). Inputs not listed (such as `reason`) and the Idempotency-Key are never hashed.',
             '- `record`: the append-only table that gets, in the same transaction:']
    lines += ['  - `%s`: %s' % (r, RECORDS[r]) for r in records]
    lines += ['- `event`: the PurchaseEvent type written in the same transaction, one row per target: tenantId, purchaseId,',
              '  type = event, actorId = the actor, idempotencyKey = the Idempotency-Key. `eventExemption`: the entry writes no',
              '  event, for the stated reason.',
              '- `denial`: the one status every refused target or parameter value gets (missing, other tenant, wrong owner, wrong',
              '  state, or an unacceptable parameter value), so an Action never reveals whether or why a target exists.']
    lines += ['  For a field update of `%s`, %s' % (c, VALUES[c]) for c in columns]
    lines += ['- Every operation runs in one transaction with the ActionRequest row (idempotency scope: tenant, actor, operation,',
              '  key). A repeated key with the same fingerprint replays the stored response; a different fingerprint gets `denial`.',
              '  Any failed write, including the record, event or ActionRequest row, rolls the whole request back.', '',
              '## Entries', '']
    for e in entries:
        lines += ['### `%s`' % e['name'], '', '```json', json.dumps(e, indent=2, ensure_ascii=False), '```', '']
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('task', choices=('withdraw', 'cost-center'))
    p.add_argument('--check', action='store_true', help='exit 1 unless the committed document equals a fresh render')
    a = p.parse_args()
    text = render(a.task, catalog(a.task))
    path = ROOT / 'tasks' / a.task / 'OPERATIONS.md'
    if a.check:
        same = path.exists() and path.read_text() == text
        print(json.dumps({'task': a.task, 'document_matches_catalog': same}))
        return 0 if same else 1
    path.write_text(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
