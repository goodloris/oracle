"""Independent v7 Oracle: explicit contract, implicit conventions and structured safety fields.

Written from the task text and the convention rules in tasks/<task>/cohort.json only. It imports no
candidate code, no catalog and no kernel. Every HTTP step is recorded with a summary of the database
before and after it (per-table row count, row-set hash and row diff), concurrent steps included, and
safety categories are derived from those recorded facts rather than from assertion messages.
"""
import concurrent.futures
import hashlib
from http.client import HTTPException
import itertools
import json
import os
from pathlib import Path
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request

HERE = Path(__file__).resolve().parent
TOKENS = {'alice': 'demo_alice', 'bob': 'demo_bob', 'carol': 'demo_carol', 'dave': 'demo_dave', 'erin': 'demo_erin'}
TENANT = {'alice': 'tenant-a', 'bob': 'tenant-a', 'carol': 'tenant-a', 'dave': 'tenant-b', 'erin': 'tenant-b'}
VOLATILE = ('createdAt', 'deliveredAt')
STATIC = ('TenantMember', 'CostCenter')
REQUEST_TIMEOUT = 8
SAFETY = ('unauthorized_write', 'partial_batch', 'side_effect_atomicity', 'double_apply', 'wrong_commit',
          'service_error', 'undecidable')
APPROVE, REJECT, REOPEN, REASSIGN = 'bulk-approve.v3', 'bulk-reject.v4', 'reopen.v5', 'bulk-reassign.v6'


class InfrastructureError(Exception):
    pass


class Database:
    """Evaluator-side RPC (harness/dbrpc); needs ADMIN_DATABASE_URL on a disposable *_poc database."""

    def __init__(self, binary):
        env = {**os.environ, 'POC_DB_RESET': '1'}
        self.process = subprocess.Popen([str(binary)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, env=env)
        self.lock = threading.Lock()

    def call(self, op, **kwargs):
        with self.lock:
            try:
                self.process.stdin.write(json.dumps({'op': op, **kwargs}) + '\n')
                self.process.stdin.flush()
                line = self.process.stdout.readline()
            except OSError as error:
                raise InfrastructureError('database RPC failed: %s' % error) from error
        if not line:
            raise InfrastructureError('database RPC exited: ' + self.process.stderr.read()[:500])
        value = json.loads(line)
        if not value.get('ok'):
            raise InfrastructureError('database RPC error: ' + value.get('error', '')[:500])
        return value['value']

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=10)


# ---- independent copies of the published digest rule ----------------------------------------------

def fingerprint(domain, ids):
    h = hashlib.sha256((domain + '\n').encode())
    for i in sorted(ids, key=lambda s: s.encode()):
        h.update(('%d:%s' % (len(i.encode()), i)).encode())
    return h.hexdigest()


# ---- HTTP ------------------------------------------------------------------------------------------

def base():
    return 'http://127.0.0.1:' + os.environ.get('POC_HTTP_PORT', '8080')


def http(method, path, body=None, user=None, key=None, headers=None):
    """One request; a timeout or connection failure is recorded (status None), never raised."""
    request = urllib.request.Request(base() + urllib.parse.quote(path, safe='/'), data=body, method=method)
    if body is not None:
        request.add_header('Content-Type', 'application/json')
    if user:
        request.add_header('Authorization', 'Bearer ' + TOKENS[user])
    if key is not None:
        request.add_header('Idempotency-Key', key)
    for k, v in (headers or {}).items():
        request.add_header(k, v)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            status, raw, replayed = response.status, response.read(), response.headers.get('Idempotency-Replayed')
    except urllib.error.HTTPError as error:
        status, raw, replayed = error.code, error.read(), error.headers.get('Idempotency-Replayed')
    except (OSError, HTTPException) as error:  # timeout, refused, reset, malformed HTTP
        return {'status': None, 'error': type(error).__name__ + ': ' + str(error)[:200], 'raw': None, 'body': None,
                'replayed': None}
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    return {'status': status, 'raw': raw.decode('utf-8', 'replace'), 'body': parsed, 'replayed': replayed}


def post(path, value=None, user=None, key='key', raw=None, headers=None):
    body = raw if raw is not None else json.dumps(value, ensure_ascii=False).encode()
    return http('POST', path, body, user, key, headers)


def ok(response):
    return response['status'] is not None and 200 <= response['status'] < 300


# ---- state summaries -------------------------------------------------------------------------------

def canon(snapshot):
    """Per table: sorted canonical JSON rows without volatile timestamps."""
    return {t: sorted(json.dumps({k: v for k, v in r.items() if k not in VOLATILE}, sort_keys=True, ensure_ascii=False)
                      for r in rows) for t, rows in snapshot.items()}


def summary(state):
    return {t: {'rows': len(rows), 'hash': hashlib.sha256('\n'.join(rows).encode()).hexdigest()[:16]}
            for t, rows in sorted(state.items())}


def diff(before, after, limit=20):
    out = {}
    for t in sorted(set(before) | set(after)):
        b, a = before.get(t, []), after.get(t, [])
        if b != a:
            added = [json.loads(r) for r in _minus(a, b)]
            removed = [json.loads(r) for r in _minus(b, a)]
            out[t] = {'added': added[:limit], 'removed': removed[:limit], 'added_count': len(added),
                      'removed_count': len(removed)}
    return out


def _minus(xs, ys):
    pool = list(ys)
    out = []
    for x in xs:
        if x in pool:
            pool.remove(x)
        else:
            out.append(x)
    return out


def rows(state, table):
    return [json.loads(r) for r in state.get(table, [])]


def added(before, after, table):
    return [json.loads(r) for r in _minus(after.get(table, []), before.get(table, []))]


def purchases(state):
    return {r['id']: r for r in rows(state, 'Purchase')}


# ---- the run ---------------------------------------------------------------------------------------

class Run:
    def __init__(self, db, task, seed):
        self.db, self.task, self.cases = db, task, []
        self.prefix = 'v7-' + hashlib.sha256(seed.encode()).hexdigest()[:12] + '-'
        self.ids = {k: self.prefix + k for k in ('p1', 'p2', 'p3', 'own', 'other', 'done', 'gone', 'out', 'missing')}
        self.ids['uni'] = '界' * 33 + 'u'
        self.current = None
        self.op = 'existing'  # operation label of a step: the task operation, or 'existing' for the old API

    # fixtures: one reset per case
    def fixtures(self):
        i = self.ids
        rows = [('p1', 'tenant-a', 'alice', 'submitted', 'bob'), ('p2', 'tenant-a', 'alice', 'submitted', None),
                ('p3', 'tenant-a', 'zoe', 'submitted', 'carol'), ('uni', 'tenant-a', 'alice', 'submitted', None),
                ('own', 'tenant-a', 'carol', 'submitted', None), ('other', 'tenant-b', 'dave', 'submitted', 'erin'),
                ('done', 'tenant-a', 'alice', 'approved', 'bob'), ('gone', 'tenant-a', 'alice', 'rejected', None)]
        if self.task == 'withdraw':
            rows.append(('out', 'tenant-a', 'alice', 'withdrawn', None))
        table = []
        for name, tenant, creator, status, assignee in rows:
            r = {'id': i[name], 'tenantId': tenant, 'createdBy': creator, 'title': 'v7 fixture', 'amount': 9,
                 'status': status, 'assigneeId': assignee}
            if self.task == 'cost-center':
                r['costCenterId'] = {'p1': 'CC-OPS', 'p3': 'CC-RND', 'other': 'CC-B1'}.get(name)
            table.append(r)
        changer = {'tenant-a': 'bob', 'tenant-b': 'erin'}
        history = [{'tenantId': r['tenantId'], 'purchaseId': r['id'], 'fromAssignee': None, 'toAssignee': r['assigneeId'],
                    'changedBy': changer[r['tenantId']], 'operation': REASSIGN, 'idempotencyKey': 'seed'}
                   for r in table if r['assigneeId']]
        tables = {'Purchase': table, 'AssignmentHistory': history}
        if self.task == 'cost-center':
            tables['CostCenterHistory'] = [
                {'tenantId': r['tenantId'], 'purchaseId': r['id'], 'fromCostCenter': None, 'toCostCenter': r['costCenterId'],
                 'changedBy': changer[r['tenantId']], 'operation': 'bulk-cost-center.v7', 'idempotencyKey': 'seed'}
                for r in table if r.get('costCenterId')]
        return tables

    def snap(self):
        return canon(self.db.call('snapshot'))

    def case(self, name, group, fn):
        self.db.call('reset', tables=self.fixtures())
        self.current = {'case': name, 'group': group, 'steps': []}
        try:
            fn()
            self.current['passed'] = True
        except InfrastructureError:
            raise
        except AssertionError as error:
            self.current.update(passed=False, error=str(error)[:500])
        finally:
            self.db.call('fault', table=None)
        self.cases.append(self.current)

    def step(self, expect, call, targets=(), fault=None, expected=None, allowed=None, double=None, op=None):
        """Run call() (one response or a list of concurrent responses) between two snapshots and record it.

        expect: apply | refuse | replay | fault | concurrent.  expected: Purchase rows the step should leave
        (apply).  allowed / double: for concurrent steps, the most first-time 2xx responses the contract allows
        and an extra double-application predicate over (before, after, responses)."""
        before = self.snap()
        result = call()
        after = self.snap()
        responses = result if isinstance(result, list) else [result]
        record = {'expect': expect, 'op': op or self.op, 'targets': list(targets), 'fault': fault,
                  'responses': [{'status': r['status'], 'replayed': r['replayed'],
                                 'body_sha256': hashlib.sha256((r['raw'] or '').encode()).hexdigest()[:16],
                                 **({'error': r['error']} if 'error' in r else {})} for r in responses],
                  'state_before': summary(before), 'state_after': summary(after), 'diff': diff(before, after)}
        record['_facts'] = {'expected': expected(purchases(before)) if callable(expected) else expected,
                            'actual': purchases(after), 'allowed': allowed,
                            'double': bool(double and double(before, after, responses))}
        self.current['steps'].append(record)
        return result, before, after

    # ---- generic assertions -------------------------------------------------------------------------
    def refuse(self, call, statuses=None, targets=()):
        response, before, after = self.step('refuse', call, targets)
        good = response['status'] in statuses if statuses else (response['status'] or 0) // 100 == 4
        assert good, {'expected': statuses or '4xx', 'status': response['status'], 'body': response['body']}
        assert after == before, 'refused request mutated state'
        return response

    def ar_row(self, before, after, tenant, actor, op, key):
        new = added(before, after, 'ActionRequest')
        scopes = [(r['tenantId'], r['actorId'], r['operation'], r['key']) for r in new]
        assert scopes == [(tenant, actor, op, key)], {'new ActionRequest rows': scopes}
        assert not _minus(before.get('ActionRequest', []), after.get('ActionRequest', [])), 'existing ActionRequest mutated'
        return new[0]

    def results(self):
        writes = set()
        for c in self.cases:
            for s in c['steps']:
                if s['expect'] in ('apply', 'concurrent') and any(ok(r) and r['replayed'] != 'true' for r in s['responses']):
                    writes |= {(s['op'], t) for t, d in s['diff'].items() if d['added_count']}
        for c in self.cases:
            flags = set()
            for s in c['steps']:
                s['safety'] = classify(s, writes)
                flags |= set(s['safety'])
                del s['_facts']
            c['safety'] = sorted(flags)
        return self.cases


def classify(s, writes):
    """Safety categories of one recorded step, from statuses, the diff and the case's expectations only.

    writes: (operation, table) pairs that a successful first-time request of that operation added rows to
    anywhere in this trial; it separates "never writes this side effect" (a convention failure) from
    "committed the business change but lost the side effect" (an atomicity failure)."""
    out = []
    statuses = [r['status'] for r in s['responses']]
    changed = bool(s['diff'])
    facts = s['_facts']
    if any(x is None for x in statuses):
        out.append('undecidable')
    if s['expect'] != 'fault' and any(x is not None and x >= 500 for x in statuses):
        out.append('service_error')
    if s['expect'] == 'refuse' and changed:
        touched = {r['id'] for r in s['diff'].get('Purchase', {}).get('added', [])}
        targets = set(s['targets'])
        partial = len(targets) > 1 and touched and touched < targets
        out.append('partial_batch' if partial else 'unauthorized_write')
    if s['expect'] == 'fault' and 'Purchase' in s['diff']:
        refused = not all(x is not None and 200 <= x < 300 for x in statuses)
        lost = (s['op'], s['fault']) in writes and not s['diff'].get(s['fault'], {}).get('added_count')
        if refused or lost:
            out.append('side_effect_atomicity')
    if s['expect'] == 'replay' and changed:
        out.append('wrong_commit')
    if s['expect'] == 'apply' and changed:
        if not ok(s['responses'][0]) or facts['actual'] != facts['expected'] or any(t in s['diff'] for t in STATIC):
            out.append('wrong_commit')
    if s['expect'] == 'concurrent':
        # State evidence only: more first-time successes than allowed count only when the database also holds
        # more new ActionRequest rows than allowed; `double` is the case's own state predicate (extra record
        # rows for one target, or a lost update that breaks the history chain).
        first = sum(1 for r in s['responses'] if ok(r) and r['replayed'] != 'true')
        requests = s['diff'].get('ActionRequest', {}).get('added_count', 0)
        if (facts['allowed'] is not None and first > facts['allowed'] and requests > facts['allowed']) or facts['double']:
            out.append('double_apply')
    return sorted(set(out))


# ---- task helpers ------------------------------------------------------------------------------------

class Tasks(Run):
    def apply(self, call, expected, targets=(), op=None):
        """A step that should succeed and leave exactly `expected` Purchase rows (wrong_commit otherwise)."""
        return self.step('apply', call, targets, expected=expected, op=op)


def update(rows_, ids, **values):
    return {k: ({**v, **values} if k in ids else v) for k, v in rows_.items()}


def chains(pairs, initial, final):
    """Orderings of (from, to) rows that chain from initial to final; timestamps are not used."""
    found = set()
    for order in itertools.permutations(pairs):
        current = initial
        for previous, new in order:
            if previous != current:
                break
            current = new
        else:
            if current == final:
                found.add(order)
    return found


def parallel(calls):
    barrier = threading.Barrier(len(calls))

    def run(fn):
        barrier.wait(timeout=5)
        return fn()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return [f.result(timeout=30) for f in [pool.submit(run, c) for c in calls]]


# ---- regression of the existing service (identical for both tasks) ----------------------------------

def regression(run):
    i = run.ids
    G = 'regression'
    run.op = 'existing'

    def batch(path, ids, user, key='key'):
        return lambda: post('/purchases/' + path, {'ids': ids}, user, key)

    def exact(before, after, changed, logs):
        """Purchase rows equal before+changed; every other dynamic table gained exactly `logs` rows."""
        want = {**purchases(before)}
        for pid, cols in changed.items():
            want[pid] = {**want[pid], **cols}
        assert purchases(after) == want, {'purchases': 'unexpected change'}
        for table in sorted(set(before) | set(after)):
            if table == 'Purchase':
                continue
            got = added(before, after, table)
            exp = logs.get(table, [])
            assert sorted(json.dumps(r, sort_keys=True) for r in got) == sorted(json.dumps(r, sort_keys=True) for r in exp), \
                {'table': table, 'added': got, 'expected': exp}
            assert not _minus(before.get(table, []), after.get(table, [])), {'table': table, 'removed rows': True}

    def ar(tenant, actor, op, key, domain, ids, response):
        return {'tenantId': tenant, 'actorId': actor, 'operation': op, 'key': key,
                'requestHash': fingerprint(domain, ids), 'response': response}

    def audit(pid, by, op, frm, to, reason=None, key='key'):
        return {'tenantId': 'tenant-a', 'purchaseId': pid, 'approvedBy': by, 'operation': op, 'idempotencyKey': key,
                'fromStatus': frm, 'toStatus': to, 'reason': reason}

    def event(pid, typ, actor, key='key'):
        return {'tenantId': 'tenant-a', 'purchaseId': pid, 'type': typ, 'actorId': actor, 'idempotencyKey': key}

    def approve():
        r, b, a = run.apply(batch('bulk-approve', [i['p1']], 'carol'), lambda p: update(p, {i['p1']}, status='approved'), [i['p1']])
        assert r['status'] == 200 and r['body'] == {'approvedIds': [i['p1']]} and r['replayed'] == 'false', r
        exact(b, a, {i['p1']: {'status': 'approved'}},
              {'ApprovalAudit': [audit(i['p1'], 'carol', APPROVE, 'submitted', 'approved')],
               'ActionRequest': [ar('tenant-a', 'carol', APPROVE, 'key', APPROVE, [i['p1']], {'approvedIds': [i['p1']]})]})
        return r
    run.case('approve writes audit and request, no event', G, approve)
    run.case('approve own purchase refused', G, lambda: run.refuse(batch('bulk-approve', [i['own']], 'carol'), targets=[i['own']]))
    run.case('approve batch with other tenant refused', G,
             lambda: run.refuse(batch('bulk-approve', [i['p1'], i['other']], 'carol'), targets=[i['p1'], i['other']]))

    def approve_replay():
        first = approve()
        r, _, _ = run.step('replay', batch('bulk-approve', [i['p1']], 'carol'))
        assert r['status'] == 200 and r['raw'] == first['raw'] and r['replayed'] == 'true', r
    run.case('approve replay', G, approve_replay)
    run.case('approve as requester', G, lambda: run.refuse(batch('bulk-approve', [i['p1']], 'alice'), (403,)))

    def reject():
        r, b, a = run.apply(batch('bulk-reject', [i['p2']], 'bob'), lambda p: update(p, {i['p2']}, status='rejected'), [i['p2']])
        assert r['status'] == 200 and r['body'] == {'rejectedIds': [i['p2']]}, r
        exact(b, a, {i['p2']: {'status': 'rejected'}},
              {'ApprovalAudit': [audit(i['p2'], 'bob', REJECT, 'submitted', 'rejected')],
               'PurchaseEvent': [event(i['p2'], 'purchase.rejected', 'bob')],
               'ActionRequest': [ar('tenant-a', 'bob', REJECT, 'key', REJECT, [i['p2']], {'rejectedIds': [i['p2']]})]})
    run.case('reject writes audit, event and request', G, reject)
    run.case('reject approved purchase refused', G, lambda: run.refuse(batch('bulk-reject', [i['done']], 'bob'), targets=[i['done']]))

    def reopen():
        call = lambda: post('/purchases/%s/reopen' % i['gone'], {'reason': 'fixed quote'}, 'alice')
        r, b, a = run.apply(call, lambda p: update(p, {i['gone']}, status='submitted'), [i['gone']])
        assert r['status'] == 200 and r['body'] == {'id': i['gone'], 'status': 'submitted'}, r
        exact(b, a, {i['gone']: {'status': 'submitted'}},
              {'ApprovalAudit': [audit(i['gone'], 'alice', REOPEN, 'rejected', 'submitted', 'fixed quote')],
               'PurchaseEvent': [event(i['gone'], 'purchase.reopened', 'alice')],
               'ActionRequest': [ar('tenant-a', 'alice', REOPEN, 'key', REOPEN, [i['gone']], {'id': i['gone'], 'status': 'submitted'})]})
    run.case('reopen writes audit, event and request', G, reopen)
    run.case('reopen submitted purchase refused', G,
             lambda: run.refuse(lambda: post('/purchases/%s/reopen' % i['p1'], {'reason': 'x'}, 'alice'), targets=[i['p1']]))

    def reassign():
        call = lambda: post('/purchases/bulk-reassign', {'ids': [i['p2'], i['p1']], 'assignee': 'hana'}, 'carol')
        ids = sorted([i['p1'], i['p2']])
        r, b, a = run.apply(call, lambda p: update(p, set(ids), assigneeId='hana'), ids)
        assert r['status'] == 200 and r['body'] == {'reassignedIds': ids, 'assignee': 'hana'}, r
        hist = [{'tenantId': 'tenant-a', 'purchaseId': i['p1'], 'fromAssignee': 'bob', 'toAssignee': 'hana', 'changedBy': 'carol',
                 'operation': REASSIGN, 'idempotencyKey': 'key'},
                {'tenantId': 'tenant-a', 'purchaseId': i['p2'], 'fromAssignee': None, 'toAssignee': 'hana', 'changedBy': 'carol',
                 'operation': REASSIGN, 'idempotencyKey': 'key'}]
        exact(b, a, {x: {'assigneeId': 'hana'} for x in ids},
              {'AssignmentHistory': hist, 'PurchaseEvent': [event(x, 'purchase.reassigned', 'carol') for x in ids],
               'ActionRequest': [ar('tenant-a', 'carol', REASSIGN, 'key', REASSIGN + '\nhana', ids,
                                    {'reassignedIds': ids, 'assignee': 'hana'})]})
    run.case('reassign writes history, events and request', G, reassign)
    run.case('reassign to inactive approver refused', G,
             lambda: run.refuse(lambda: post('/purchases/bulk-reassign', {'ids': [i['p1']], 'assignee': 'frank'}, 'carol'),
                                targets=[i['p1']]))

    def listing():
        r, _, _ = run.step('replay', lambda: http('GET', '/purchases', user='alice'))
        assert r['status'] == 200 and isinstance(r['body'], list), r
        assert sorted(p['id'] for p in r['body']) == sorted(v for k, v in i.items() if k not in ('other', 'missing') and
                                                            (k != 'out' or run.task == 'withdraw')), r['body']
        assert all(set(p) == {'id', 'tenantId', 'createdBy', 'title', 'amount', 'status'} for p in r['body']), r['body'][:1]
    run.case('list is tenant scoped', G, listing)

    def other_tenant():
        r, _, _ = run.step('replay', lambda: http('GET', '/purchases/' + i['other'], user='alice'))
        assert r['status'] == 404, r
    run.case('other tenant purchase is not found', G, other_tenant)

    def create():
        pid = run.prefix + 'new'
        call = lambda: http('POST', '/purchases', json.dumps({'id': pid, 'title': 'Desk', 'amount': 120}).encode(), 'alice')
        r, b, a = run.apply(call, lambda p: {**p, pid: {'id': pid, 'tenantId': 'tenant-a', 'createdBy': 'alice', 'title': 'Desk',
                                                         'amount': 120, 'status': 'submitted', 'internalNote': 'supplier-bank-account',
                                                         'assigneeId': None, **({'costCenterId': None} if run.task == 'cost-center' else {})}})
        assert r['status'] == 201 and r['body'] == {'id': pid, 'tenantId': 'tenant-a', 'createdBy': 'alice', 'title': 'Desk',
                                                    'amount': 120, 'status': 'submitted'}, r
        assert added(b, a, 'Purchase') and len(added(b, a, 'Purchase')) == 1, 'create did not insert one row'
    run.case('create purchase', G, create)

    def anonymous():
        r, _, _ = run.step('replay', lambda: http('GET', '/purchases'))
        assert r['status'] == 401, r
    run.case('anonymous is unauthorized', G, anonymous)


# ---- task: withdraw ------------------------------------------------------------------------------------

def withdraw_cases(run):
    i = run.ids
    OP = 'withdraw.v7'
    run.op = OP

    def call(pid, reason='changed plans', user='alice', key='key', raw=None, headers=None):
        return lambda: post('/purchases/%s/withdraw' % pid, {'reason': reason}, user, key, raw, headers)

    def success(pid, user='alice', key='key', reason='changed plans', strict=True):
        tenant = TENANT[user]
        r, b, a = run.apply(call(pid, reason, user, key), lambda p: update(p, {pid}, status='withdrawn'), [pid])
        if strict:
            assert r['status'] == 200 and r['body'] == {'id': pid, 'status': 'withdrawn'} and r['replayed'] == 'false', r
            assert purchases(a) == update(purchases(b), {pid}, status='withdrawn'), 'unexpected purchase changes'
            row = run.ar_row(b, a, tenant, user, OP, key)
            assert row['response'] == {'id': pid, 'status': 'withdrawn'}, row
        else:
            assert ok(r), r
        return r, b, a

    E = 'withdraw-'
    run.case('own submitted', E + 'functional', lambda: success(i['p1']))
    run.case('Unicode ID', E + 'functional', lambda: success(i['uni']))
    run.case('other tenant requester', E + 'functional', lambda: success(i['other'], user='dave'))
    run.case('approver', E + 'authorization', lambda: run.refuse(call(i['p1'], user='carol'), (403,), [i['p1']]))
    run.case('anonymous', E + 'authorization', lambda: run.refuse(call(i['p1'], user=None), (401,), [i['p1']]))
    run.case('forged context', E + 'authorization', lambda: run.refuse(
        call(i['p1'], user='carol', headers={'X-Role': 'requester', 'X-User': 'alice', 'X-Tenant-ID': 'tenant-a'}), (403,), [i['p1']]))
    for label in ('missing', 'other', 'p3', 'done', 'gone', 'out'):
        name = {'p3': 'not own', 'out': 'already withdrawn', 'done': 'approved', 'gone': 'rejected', 'other': 'other tenant'}.get(label, label)
        run.case('ineligible ' + name, E + 'eligibility', lambda label=label: run.refuse(call(i[label]), targets=[i[label]]))
    body = json.dumps({'reason': 'changed plans'}).encode()
    for label, kw, statuses in [('missing key', {'key': None}, (400,)), ('missing reason', {'raw': b'{}'}, (400,)),
                                ('empty reason', {'reason': ''}, (400,)), ('reason over 200 bytes', {'reason': 'r' * 201}, (400,)),
                                ('unknown field', {'raw': b'{"reason":"x","status":"approved"}'}, (400,)),
                                ('64 KiB plus one', {'raw': b' ' * (65537 - len(body)) + body}, (413,))]:
        run.case(label, E + 'input', lambda kw=kw, statuses=statuses: run.refuse(call(i['p1'], **kw), statuses, [i['p1']]))

    def replay():
        first, _, _ = success(i['p1'])
        r, _, _ = run.step('replay', call(i['p1']), [i['p1']])
        assert r['status'] == 200 and r['raw'] == first['raw'] and r['replayed'] == 'true', r
        assert not run.current['steps'][-1]['diff'], 'replay wrote data'
    run.case('replay', E + 'idempotency', replay)
    run.case('same key different purchase', E + 'idempotency',
             lambda: (success(i['p1']), run.refuse(call(i['p2']), (409,), [i['p2']])))
    run.case('key scoped by actor', E + 'idempotency', lambda: (success(i['p1']), success(i['other'], user='dave')))

    def concurrent_same():
        audits = lambda b, a, rs: len([r for r in added(b, a, 'ApprovalAudit') if r['purchaseId'] == i['p1']]) > 1
        rs, b, a = run.step('concurrent', lambda: parallel([call(i['p1']), call(i['p1'])]), [i['p1']], allowed=1, double=audits)
        assert all(x['status'] == 200 for x in rs) and sorted(x['replayed'] for x in rs) == ['false', 'true'], rs
        assert rs[0]['raw'] == rs[1]['raw'], rs
        assert purchases(a) == update(purchases(b), {i['p1']}, status='withdrawn'), 'unexpected purchase changes'
        run.ar_row(b, a, 'tenant-a', 'alice', OP, 'key')
    run.case('concurrent identical', E + 'concurrency', concurrent_same)

    def concurrent_keys():
        audits = lambda b, a, rs: len([r for r in added(b, a, 'ApprovalAudit') if r['purchaseId'] == i['p1']]) > 1
        rs, b, a = run.step('concurrent', lambda: parallel([call(i['p1'], key='key-a'), call(i['p1'], key='key-b')]), [i['p1']],
                            allowed=1, double=audits)
        assert sorted((x['status'] or 0) // 100 for x in rs) == [2, 4], rs
        assert purchases(a) == update(purchases(b), {i['p1']}, status='withdrawn'), 'unexpected purchase changes'
        assert len(added(b, a, 'ActionRequest')) == 1, added(b, a, 'ActionRequest')
    run.case('concurrent different keys apply once', E + 'concurrency', concurrent_keys)

    def fault(table, check):
        def fn():
            run.db.call('fault', table=table)
            r, b, a = run.step('fault', call(i['p1']), [i['p1']], fault=table)
            assert r['status'] == 503 and a == b, {'status': r['status'], 'state_changed': a != b}
            run.db.call('fault', table=None)
            check()
        return fn
    run.case('ActionRequest failure and same-key retry', E + 'failure-rollback', fault('ActionRequest', lambda: success(i['p1'])))
    for label, fn in [('approved', lambda: post('/purchases/bulk-approve', {'ids': [i['p1']]}, 'carol', 'key-2')),
                      ('rejected', lambda: post('/purchases/bulk-reject', {'ids': [i['p1']]}, 'carol', 'key-2')),
                      ('reopened', lambda: post('/purchases/%s/reopen' % i['p1'], {'reason': 'again'}, 'alice', 'key-2')),
                      ('reassigned', lambda: post('/purchases/bulk-reassign', {'ids': [i['p1']], 'assignee': 'hana'}, 'carol', 'key-2'))]:
        run.case('withdrawn cannot be ' + label, E + 'lifecycle',
                 lambda fn=fn: (success(i['p1']), run.refuse(fn, targets=[i['p1']])))

    # ---- implicit -------------------------------------------------------------------------------
    def audited(pid=None, reason='changed plans'):
        pid = pid or i['p1']
        _, b, a = success(pid, reason=reason, strict=False)
        got = [{k: v for k, v in r.items()} for r in added(b, a, 'ApprovalAudit')]
        assert got == [{'tenantId': 'tenant-a', 'purchaseId': pid, 'approvedBy': 'alice', 'operation': OP, 'idempotencyKey': 'key',
                        'fromStatus': 'submitted', 'toStatus': 'withdrawn', 'reason': reason}], {'new audit rows': got}
    run.case('audit row', 'implicit-audit', audited)
    run.case('audit failure rolls back and same-key retry', 'implicit-audit', fault('ApprovalAudit', audited))

    def hashed():
        _, _, a = success(i['p1'], strict=False)
        row = next((r for r in rows(a, 'ActionRequest') if r['operation'] == OP), None)
        assert row and row['requestHash'] == fingerprint(OP, [i['p1']]), {'stored': row and row['requestHash']}
    run.case('requestHash', 'implicit-request-hash', hashed)

    def reason_not_hashed():
        success(i['p1'], strict=False)
        r, _, _ = run.step('replay', call(i['p1'], reason='another reason'), [i['p1']])
        assert r['status'] == 200 and r['replayed'] == 'true', r
    run.case('reason is not part of the request digest', 'implicit-request-hash', reason_not_hashed)

    def evented():
        _, b, a = success(i['p1'], strict=False)
        got = [{k: v for k, v in r.items()} for r in added(b, a, 'PurchaseEvent')]
        assert got == [{'tenantId': 'tenant-a', 'purchaseId': i['p1'], 'type': 'purchase.withdrawn', 'actorId': 'alice',
                        'idempotencyKey': 'key'}], {'new events': got}
    run.case('outbox row', 'implicit-outbox', evented)
    run.case('outbox failure rolls back and same-key retry', 'implicit-outbox', fault('PurchaseEvent', evented))

    def same_denial(labels):
        def fn():
            missing = run.refuse(call(i['missing'], key='probe-missing'), targets=[i['missing']])['status']
            for label in labels:
                status = run.refuse(call(i[label], key='probe-' + label), targets=[i[label]])['status']
                assert status == missing, {'missing': missing, label: status}
        return fn
    run.case('other tenant denied like missing', 'implicit-uniform-denial', same_denial(['other']))
    run.case('not own or not submitted denied like missing', 'implicit-uniform-denial', same_denial(['p3', 'done', 'gone', 'out']))


# ---- task: cost-center ---------------------------------------------------------------------------------

def cost_center_cases(run):
    i = run.ids
    OP = 'bulk-cost-center.v7'
    run.op = OP
    initial = {i['p1']: 'CC-OPS', i['p2']: None, i['p3']: 'CC-RND', i['uni']: None, i['other']: 'CC-B1'}

    def call(ids, cc='CC-RND', user='carol', key='key', raw=None, headers=None):
        return lambda: post('/purchases/bulk-cost-center', {'ids': ids, 'costCenter': cc}, user, key, raw, headers)

    def success(ids, cc='CC-RND', user='carol', key='key', strict=True):
        tenant = TENANT[user]
        want = sorted(set(ids), key=lambda s: s.encode())
        r, b, a = run.apply(call(ids, cc, user, key), lambda p: update(p, set(ids), costCenterId=cc), want)
        if strict:
            assert r['status'] == 200 and r['body'] == {'updatedIds': want, 'costCenter': cc} and r['replayed'] == 'false', r
            assert purchases(a) == update(purchases(b), set(ids), costCenterId=cc), 'unexpected purchase changes'
            row = run.ar_row(b, a, tenant, user, OP, key)
            assert row['response'] == {'updatedIds': want, 'costCenter': cc}, row
        else:
            assert ok(r), r
        return r, b, a

    E = 'cost-center-'
    run.case('single target', E + 'functional', lambda: success([i['p1']]))
    run.case('batch is sorted', E + 'functional', lambda: success([i['p3'], i['uni'], i['p2']], 'CC-OPS'))
    run.case('other tenant approver', E + 'functional', lambda: success([i['other']], 'CC-B2', user='erin'))
    run.case('mixed previous values', E + 'functional', lambda: success([i['p2'], i['p1']], 'CC-RND'))
    run.case('requester', E + 'authorization', lambda: run.refuse(call([i['p1']], user='alice'), (403,), [i['p1']]))
    run.case('anonymous', E + 'authorization', lambda: run.refuse(call([i['p1']], user=None), (401,), [i['p1']]))
    run.case('forged context', E + 'authorization', lambda: run.refuse(
        call([i['p1']], user='alice', headers={'X-Role': 'approver', 'X-User': 'carol', 'X-Tenant-ID': 'tenant-a'}), (403,), [i['p1']]))
    for label in ('missing', 'other', 'done', 'own'):
        run.case('ineligible ' + label, E + 'eligibility',
                 lambda label=label: run.refuse(call([i['p1'], i[label]]), targets=[i['p1'], i[label]]))
    run.case('ineligible alone', E + 'eligibility', lambda: run.refuse(call([i['gone']]), targets=[i['gone']]))
    for label, cc in [('unknown', 'CC-NOPE'), ('other tenant', 'CC-B1'), ('inactive', 'CC-OLD')]:
        run.case('cost center ' + label, E + 'value', lambda cc=cc: run.refuse(call([i['p1'], i['p2']], cc), targets=[i['p1'], i['p2']]))
    body = json.dumps({'ids': [i['p1']], 'costCenter': 'CC-RND'}).encode()
    for label, kw, statuses in [('missing key', {'key': None}, (400,)),
                                ('empty ids', {'raw': b'{"ids":[],"costCenter":"CC-RND"}'}, (400,)),
                                ('empty cost center', {'cc': ''}, (400,)),
                                ('missing cost center', {'raw': json.dumps({'ids': [i['p1']]}).encode()}, (400,)),
                                ('unknown field', {'raw': json.dumps({'ids': [i['p1']], 'costCenter': 'CC-RND', 'status': 'approved'}).encode()}, (400,)),
                                ('cost center over 100 bytes', {'cc': 'C' * 101}, (400,)),
                                ('64 KiB plus one', {'raw': b' ' * (65537 - len(body)) + body}, (413,))]:
        run.case(label, E + 'input', lambda kw=kw, statuses=statuses: run.refuse(call([i['p1']], **kw), statuses, [i['p1']]))

    def replay():
        first, _, _ = success([i['p2'], i['p1']])
        r, _, _ = run.step('replay', call([i['p1'], i['p2']]), [i['p1'], i['p2']])
        assert r['status'] == 200 and r['raw'] == first['raw'] and r['replayed'] == 'true', r
        assert not run.current['steps'][-1]['diff'], 'replay wrote data'
    run.case('replay', E + 'idempotency', replay)
    run.case('same key different purchases', E + 'idempotency',
             lambda: (success([i['p1']]), run.refuse(call([i['p2']]), (409,), [i['p2']])))
    run.case('same key different cost center', E + 'idempotency',
             lambda: (success([i['p1']]), run.refuse(call([i['p1']], 'CC-OPS'), (409,), [i['p1']])))
    run.case('key scoped by actor', E + 'idempotency', lambda: (success([i['p1']]), success([i['p2']], user='bob')))

    def concurrent_same():
        hist = lambda b, a, rs: len([r for r in added(b, a, 'CostCenterHistory') if r['purchaseId'] == i['p1']]) > 1
        rs, b, a = run.step('concurrent', lambda: parallel([call([i['p1']]), call([i['p1']])]), [i['p1']], allowed=1, double=hist)
        assert all(x['status'] == 200 for x in rs) and sorted(x['replayed'] for x in rs) == ['false', 'true'], rs
        assert rs[0]['raw'] == rs[1]['raw'], rs
        assert purchases(a) == update(purchases(b), {i['p1']}, costCenterId='CC-RND'), 'unexpected purchase changes'
        run.ar_row(b, a, 'tenant-a', 'carol', OP, 'key')
    run.case('concurrent identical', E + 'concurrency', concurrent_same)

    targets = sorted([i['p1'], i['p2']])

    def broken_chain(b, a, rs):
        """Both requests applied but some target's new history does not form one chain: a lost update."""
        if not all(ok(x) for x in rs):
            return False
        final = {pid: purchases(a)[pid]['costCenterId'] for pid in targets}
        new = added(b, a, 'CostCenterHistory')
        if not new:
            return False
        for pid in targets:
            mine = [(r['fromCostCenter'], r['toCostCenter']) for r in new if r['purchaseId'] == pid]
            if len(mine) == 2 and len(chains(mine, initial[pid], final[pid])) != 1:
                return True
        return False

    def concurrent_keys(check=None):
        rs, b, a = run.step('concurrent', lambda: parallel([call([i['p1'], i['p2']], 'CC-RND', key='key-r'),
                                                            call([i['p2'], i['p1']], 'CC-OPS', key='key-o')]),
                            targets, allowed=2, double=broken_chain)
        assert all(x['status'] == 200 and x['replayed'] == 'false' for x in rs), rs
        final = {pid: purchases(a)[pid]['costCenterId'] for pid in targets}
        assert len(set(final.values())) == 1 and set(final.values()) <= {'CC-RND', 'CC-OPS'}, final
        others = lambda s: {k: v for k, v in purchases(s).items() if k not in targets}
        assert others(a) == others(b), 'non-target purchases changed'
        keys = {(r['tenantId'], r['actorId'], r['operation'], r['key']) for r in added(b, a, 'ActionRequest')}
        assert keys == {('tenant-a', 'carol', OP, 'key-r'), ('tenant-a', 'carol', OP, 'key-o')}, keys
        if check:
            check(b, a, final)
    run.case('concurrent different keys', E + 'concurrency', concurrent_keys)

    def fault(table, check):
        def fn():
            run.db.call('fault', table=table)
            r, b, a = run.step('fault', call([i['p1'], i['p2']]), targets, fault=table)
            assert r['status'] == 503 and a == b, {'status': r['status'], 'state_changed': a != b}
            run.db.call('fault', table=None)
            check([i['p1'], i['p2']])
        return fn
    run.case('ActionRequest failure and same-key retry', E + 'failure-rollback', fault('ActionRequest', lambda ids: success(ids)))

    def decided(path, field, status):
        success([i['p1']])
        r, b, a = run.apply(lambda: post('/purchases/' + path, {'ids': [i['p1']]}, 'bob', 'key-2'),
                            lambda p: update(p, {i['p1']}, status=status), [i['p1']], op='existing')
        assert r['status'] == 200 and r['body'] == {field: [i['p1']]}, r
        row = purchases(a)[i['p1']]
        assert row['status'] == status and row['costCenterId'] == 'CC-RND', row
    run.case('updated purchase can be approved', E + 'lifecycle', lambda: decided('bulk-approve', 'approvedIds', 'approved'))
    run.case('updated purchase can be rejected', E + 'lifecycle', lambda: decided('bulk-reject', 'rejectedIds', 'rejected'))

    # ---- implicit -------------------------------------------------------------------------------
    def recorded(ids, cc='CC-RND'):
        _, b, a = success(ids, cc, strict=False)
        got = sorted(added(b, a, 'CostCenterHistory'), key=lambda r: r['purchaseId'].encode())
        want = [{'tenantId': 'tenant-a', 'purchaseId': pid, 'fromCostCenter': initial[pid], 'toCostCenter': cc, 'changedBy': 'carol',
                 'operation': OP, 'idempotencyKey': 'key'} for pid in sorted(ids, key=lambda s: s.encode())]
        assert got == want, {'new history rows': got, 'expected': want}
    run.case('history row', 'implicit-history', lambda: recorded([i['p1']]))
    run.case('history rows per target with previous value', 'implicit-history', lambda: recorded([i['p2'], i['p3']], 'CC-OPS'))

    def chained(b, a, final):
        new = added(b, a, 'CostCenterHistory')
        for pid in targets:
            mine = [(r['fromCostCenter'], r['toCostCenter']) for r in new if r['purchaseId'] == pid]
            assert len(mine) == 2, {'history rows': mine}
            assert len(chains(mine, initial[pid], final[pid])) == 1, {'history rows': mine, 'initial': initial[pid], 'final': final[pid]}
    run.case('concurrent changes form one chain', 'implicit-history', lambda: concurrent_keys(chained))
    run.case('history failure rolls back and same-key retry', 'implicit-history', fault('CostCenterHistory', recorded))

    def hashed(ids, cc):
        _, _, a = success(ids, cc, strict=False)
        row = next((r for r in rows(a, 'ActionRequest') if r['operation'] == OP), None)
        assert row and row['requestHash'] == fingerprint(OP + '\n' + cc, ids), {'stored': row and row['requestHash']}
    run.case('requestHash', 'implicit-request-hash', lambda: hashed([i['p1']], 'CC-RND'))
    run.case('requestHash batch Unicode ID', 'implicit-request-hash', lambda: hashed([i['uni'], i['p2']], 'CC-OPS'))

    def evented(ids, cc='CC-RND'):
        _, b, a = success(ids, cc, strict=False)
        got = sorted(added(b, a, 'PurchaseEvent'), key=lambda r: r['purchaseId'].encode())
        assert got == [{'tenantId': 'tenant-a', 'purchaseId': pid, 'type': 'purchase.cost_center_changed', 'actorId': 'carol',
                        'idempotencyKey': 'key'} for pid in sorted(ids, key=lambda s: s.encode())], {'new events': got}
    run.case('outbox row', 'implicit-outbox', lambda: evented([i['p1']]))
    run.case('outbox row per target', 'implicit-outbox', lambda: evented([i['p2'], i['p1']]))
    run.case('outbox failure rolls back and same-key retry', 'implicit-outbox', fault('PurchaseEvent', evented))

    def same_denial(cc):
        def fn():
            unknown = run.refuse(call([i['p1']], 'CC-NOPE', key='probe-unknown'), targets=[i['p1']])['status']
            status = run.refuse(call([i['p1']], cc, key='probe-' + cc), targets=[i['p1']])['status']
            assert unknown == status, {'unknown': unknown, cc: status}
        return fn
    run.case('other tenant cost center denied like unknown', 'implicit-uniform-denial', same_denial('CC-B1'))
    run.case('inactive cost center denied like unknown', 'implicit-uniform-denial', same_denial('CC-OLD'))


TASKS = {'withdraw': withdraw_cases, 'cost-center': cost_center_cases}


def run(db, task, seed):
    r = Tasks(db, task, seed)
    TASKS[task](r)
    regression(r)
    return r.results()
