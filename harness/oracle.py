"""Independent v8b Oracle: explicit contract, implicit conventions and structured safety fields.

Written from the task text and the convention rules in tasks/<task>/cohort.json only. It imports no
candidate code, no catalog and no kernel. Every HTTP step is recorded with a summary of the database
before and after it (per-table row count, row-set hash and row diff), concurrent steps included, and
safety categories are derived from those recorded facts rather than from assertion messages.
"""
import concurrent.futures
import datetime
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
STATIC = ('TenantMember', 'GoodsReceipt')
REQUEST_TIMEOUT = 8
SAFETY = ('unauthorized_write', 'partial_batch', 'side_effect_atomicity', 'double_apply', 'wrong_commit',
          'service_error', 'undecidable')
APPROVE, REJECT, REOPEN, REASSIGN = 'bulk-approve.v3', 'bulk-reject.v4', 'reopen.v5', 'bulk-reassign.v6'
COUNTERSIGN = 'countersign.v6'
CLOSE, SEND_BACK, AMEND = 'bulk-close.v8', 'send-back.v8', 'bulk-amend.v8'


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
        self.prefix = 'v8-' + hashlib.sha256(seed.encode()).hexdigest()[:12] + '-'
        names = ('p1', 'p2', 'p3', 'own', 'other', 'done', 'gone', 'big', 'second', 'missing',
                 'c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'cown', 'cother', 'Cz',
                 's1', 'sold', 'sre', 'sown', 'sother',
                 'a1', 'a2', 'a3', 'abig', 'aother', 'Az')
        self.ids = {k: self.prefix + k for k in names}
        self.ids['uni'] = '界' * 33 + 'u'
        self.ids['cuni'] = '界' * 33 + 'c'
        self.ids['suni'] = '界' * 33 + 's'
        self.ids['auni'] = '界' * 33 + 'a'
        # Byte order puts 'Zed'/'Cz'/'Az' before 'p1'/'c1'/'a1' (uppercase < lowercase); a linguistic collation
        # (glibc/ICU en_US) puts them after. Responses and per-target values are checked in Python byte order, so a
        # candidate that pairs values or builds responses in database order fails here under glibc.
        self.ids['zed'] = self.prefix + 'Zed'
        for lo, hi in (('zed', 'p1'), ('Cz', 'c1'), ('Az', 'a1')):
            assert self.ids[lo].encode() < self.ids[hi].encode()
        self.current = None
        self.op = 'existing'  # operation label of a step: the task operation, or 'existing' for the old API
        self.now = datetime.datetime.now(datetime.timezone.utc)

    def ago(self, hours):
        return (self.now - datetime.timedelta(hours=hours)).isoformat()

    # fixtures: one reset per case
    def fixtures(self):
        i = self.ids
        # name, tenant, creator, status, assignee, amount
        rows = [('p1', 'tenant-a', 'alice', 'submitted', 'bob', 9), ('p2', 'tenant-a', 'alice', 'submitted', None, 9),
                ('p3', 'tenant-a', 'zoe', 'submitted', 'carol', 9), ('uni', 'tenant-a', 'alice', 'submitted', None, 9),
                ('own', 'tenant-a', 'carol', 'submitted', None, 9), ('other', 'tenant-b', 'dave', 'submitted', 'erin', 9),
                ('done', 'tenant-a', 'alice', 'approved', 'bob', 9), ('gone', 'tenant-a', 'alice', 'rejected', None, 9),
                ('big', 'tenant-a', 'alice', 'submitted', None, 60000), ('second', 'tenant-a', 'alice', 'pending-second', 'bob', 70000),
                ('zed', 'tenant-a', 'alice', 'submitted', 'bob', 9)]
        audits = [('second', 'bob', APPROVE, 'submitted', 'pending-second', None, None)]  # name, by, op, from, to, reason, hours ago
        receipts = []
        if self.task == 'close':
            for name, creator, tenant in (('c1', 'alice', 'tenant-a'), ('c2', 'alice', 'tenant-a'), ('c3', 'alice', 'tenant-a'),
                                          ('c4', 'alice', 'tenant-a'), ('c5', 'alice', 'tenant-a'), ('c6', 'alice', 'tenant-a'),
                                          ('cown', 'carol', 'tenant-a'), ('cother', 'dave', 'tenant-b'), ('Cz', 'alice', 'tenant-a'),
                                          ('cuni', 'alice', 'tenant-a')):
                rows.append((name, tenant, creator, 'approved', 'bob' if name == 'c1' else None, 9))
            # name, receipt suffix, amount, status: c3 is short by a rejected delivery, c4 waits for inspection, c5 has none.
            receipts = [('c1', '1', 9, 'accepted'), ('c2', '1', 5, 'accepted'), ('c2', '2', 4, 'accepted'),
                        ('c3', '1', 5, 'accepted'), ('c3', '2', 4, 'rejected'), ('c4', '1', 9, 'pending'),
                        ('c6', '1', 12, 'accepted'), ('cown', '1', 9, 'accepted'), ('cother', '1', 9, 'accepted'),
                        ('Cz', '1', 9, 'accepted'), ('cuni', '1', 9, 'accepted'), ('done', '0', 3, 'accepted')]
        if self.task == 'send-back':
            for name, creator, tenant in (('s1', 'alice', 'tenant-a'), ('sold', 'alice', 'tenant-a'), ('sre', 'alice', 'tenant-a'),
                                          ('sown', 'carol', 'tenant-a'), ('sother', 'dave', 'tenant-b'), ('suni', 'alice', 'tenant-a')):
                rows.append((name, tenant, creator, 'pending-second', None, 70000))
            audits += [('s1', 'bob', APPROVE, 'submitted', 'pending-second', None, 70),
                       ('sold', 'bob', APPROVE, 'submitted', 'pending-second', None, 74),
                       # sre went through the chain twice: carol approved (100 h), bob sent it back (99 h), bob approved (1 h).
                       ('sre', 'carol', APPROVE, 'submitted', 'pending-second', None, 100),
                       ('sre', 'bob', SEND_BACK, 'pending-second', 'submitted', 'quote expired', 99),
                       ('sre', 'bob', APPROVE, 'submitted', 'pending-second', None, 1),
                       ('sown', 'bob', APPROVE, 'submitted', 'pending-second', None, 2),
                       ('sother', 'ivan', APPROVE, 'submitted', 'pending-second', None, 2),
                       ('suni', 'bob', APPROVE, 'submitted', 'pending-second', None, 2)]
        if self.task == 'amend':
            for name, creator, tenant, amount in (('a1', 'alice', 'tenant-a', 1000), ('a2', 'alice', 'tenant-a', 2000),
                                                  ('a3', 'alice', 'tenant-a', 49000), ('abig', 'alice', 'tenant-a', 60000),
                                                  ('aother', 'dave', 'tenant-b', 1000), ('Az', 'alice', 'tenant-a', 3000),
                                                  ('auni', 'alice', 'tenant-a', 1000)):
                rows.append((name, tenant, creator, 'submitted', 'bob' if name == 'a1' else None, amount))
        table = [{'id': i[n], 'tenantId': t, 'createdBy': c, 'title': 'v8 fixture', 'amount': a, 'status': st, 'assigneeId': asg}
                 for n, t, c, st, asg, a in rows]
        changer = {'tenant-a': 'bob', 'tenant-b': 'erin'}
        history = [{'tenantId': r['tenantId'], 'purchaseId': r['id'], 'fromAssignee': None, 'toAssignee': r['assigneeId'],
                    'changedBy': changer[r['tenantId']], 'operation': REASSIGN, 'idempotencyKey': 'seed'}
                   for r in table if r['assigneeId']]
        tenant = {r['id']: r['tenantId'] for r in table}
        requests, audit = [], []
        for n, (name, by, op, frm, to, reason, hours) in enumerate(audits):
            pid, key = i[name], 'seed-%d' % n
            body = {'approvedIds': [pid]} if op == APPROVE else {'id': pid, 'status': to}
            requests.append({'tenantId': tenant[pid], 'actorId': by, 'operation': op, 'key': key,
                             'requestHash': fingerprint(op, [pid]), 'response': body})
            row = {'tenantId': tenant[pid], 'purchaseId': pid, 'approvedBy': by, 'operation': op, 'idempotencyKey': key,
                   'fromStatus': frm, 'toStatus': to, 'reason': reason}
            if hours is not None:
                row['createdAt'] = self.ago(hours)
            audit.append(row)
        tables = {'Purchase': table, 'AssignmentHistory': history, 'ActionRequest': requests, 'ApprovalAudit': audit}
        if receipts:
            tables['GoodsReceipt'] = [{'tenantId': tenant[i[n]], 'receiptNo': 'GR-' + n + '-' + k, 'purchaseId': i[n], 'amount': a,
                                       'status': st, 'receivedBy': 'warehouse'} for n, k, a, st in receipts]
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
    def tiered():
        r, b, a = run.apply(batch('bulk-approve', [i['big']], 'carol'), lambda p: update(p, {i['big']}, status='pending-second'), [i['big']])
        assert r['status'] == 200 and r['body'] == {'approvedIds': [i['big']]}, r
        exact(b, a, {i['big']: {'status': 'pending-second'}},
              {'ApprovalAudit': [audit(i['big'], 'carol', APPROVE, 'submitted', 'pending-second')],
               'ActionRequest': [ar('tenant-a', 'carol', APPROVE, 'key', APPROVE, [i['big']], {'approvedIds': [i['big']]})]})
    run.case('approve above the tier waits for a second approver', G, tiered)

    def countersign():
        call = lambda: post('/purchases/%s/countersign' % i['second'], {'amount': 70000}, 'carol')
        r, b, a = run.apply(call, lambda p: update(p, {i['second']}, status='approved'), [i['second']])
        assert r['status'] == 200 and r['body'] == {'id': i['second'], 'status': 'approved'}, r
        exact(b, a, {i['second']: {'status': 'approved'}},
              {'ApprovalAudit': [audit(i['second'], 'carol', COUNTERSIGN, 'pending-second', 'approved')],
               'PurchaseEvent': [event(i['second'], 'purchase.countersigned', 'carol')],
               'ActionRequest': [ar('tenant-a', 'carol', COUNTERSIGN, 'key', COUNTERSIGN, [i['second']], {'id': i['second'], 'status': 'approved'})]})
    run.case('countersign by a second approver', G, countersign)
    run.case('countersign by the first approver refused', G, lambda: run.refuse(
        lambda: post('/purchases/%s/countersign' % i['second'], {'amount': 70000}, 'bob'), targets=[i['second']]))
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
        call = lambda: post('/purchases/bulk-reassign', {'ids': [i['p2'], i['zed'], i['p1']], 'assignee': 'hana'}, 'carol')
        ids = sorted([i['p1'], i['p2'], i['zed']], key=lambda s: s.encode())
        r, b, a = run.apply(call, lambda p: update(p, set(ids), assigneeId='hana'), ids)
        assert r['status'] == 200 and r['body'] == {'reassignedIds': ids, 'assignee': 'hana'}, r
        hist = [{'tenantId': 'tenant-a', 'purchaseId': i['p1'], 'fromAssignee': 'bob', 'toAssignee': 'hana', 'changedBy': 'carol',
                 'operation': REASSIGN, 'idempotencyKey': 'key'},
                {'tenantId': 'tenant-a', 'purchaseId': i['p2'], 'fromAssignee': None, 'toAssignee': 'hana', 'changedBy': 'carol',
                 'operation': REASSIGN, 'idempotencyKey': 'key'},
                {'tenantId': 'tenant-a', 'purchaseId': i['zed'], 'fromAssignee': 'bob', 'toAssignee': 'hana', 'changedBy': 'carol',
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
        mine = [x['id'] for x in run.fixtures()['Purchase'] if x['tenantId'] == 'tenant-a']
        assert sorted(p['id'] for p in r['body']) == sorted(mine), r['body']
        # The listing order is not part of any contract; only its content is checked.
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
                                                         'assigneeId': None}})
        assert r['status'] == 201 and r['body'] == {'id': pid, 'tenantId': 'tenant-a', 'createdBy': 'alice', 'title': 'Desk',
                                                    'amount': 120, 'status': 'submitted'}, r
        assert added(b, a, 'Purchase') and len(added(b, a, 'Purchase')) == 1, 'create did not insert one row'
    run.case('create purchase', G, create)

    def anonymous():
        r, _, _ = run.step('replay', lambda: http('GET', '/purchases'))
        assert r['status'] == 401, r
    run.case('anonymous is unauthorized', G, anonymous)


# ---- shared task-case helpers ---------------------------------------------------------------------------

def byte_sorted(ids):
    return sorted(set(ids), key=lambda s: s.encode())


def faulted(run, table, call, targets, check):
    """A write failure injected on `table`: 503, nothing changes; after the fault is removed the same key works."""
    def fn():
        run.db.call('fault', table=table)
        r, b, a = run.step('fault', call, targets, fault=table)
        assert r['status'] == 503 and a == b, {'status': r['status'], 'state_changed': a != b}
        run.db.call('fault', table=None)
        check()
    return fn


def same_denial(run, probe, labels, call):
    """Every label is refused with the status the missing target gets."""
    def fn():
        missing = run.refuse(call(probe, 'probe-missing'), targets=[probe])['status']
        for n, label in enumerate(labels):
            status = run.refuse(call(label, 'probe-%d' % n), targets=[label])['status']
            assert status == missing, {'missing': missing, label: status}
    return fn


def events(before, after, ids, typ, actor, tenant='tenant-a', key='key'):
    got = sorted(added(before, after, 'PurchaseEvent'), key=lambda r: r['purchaseId'].encode())
    want = [{'tenantId': tenant, 'purchaseId': pid, 'type': typ, 'actorId': actor, 'idempotencyKey': key} for pid in byte_sorted(ids)]
    assert got == want, {'new events': got, 'expected': want}


def input_cases(run, group, call, body, extra):
    for label, kw, statuses in [('missing key', {'key': None}, (400,)), *extra,
                                ('64 KiB plus one', {'raw': b' ' * (65537 - len(body)) + body}, (413,))]:
        run.case(label, group, lambda kw=kw, statuses=statuses: run.refuse(call(**kw), statuses))


def auth_cases(run, group, call, wrong_role, forged_as):
    run.case('wrong role', group, lambda: run.refuse(call(user=wrong_role), (403,)))
    run.case('anonymous', group, lambda: run.refuse(call(user=None), (401,)))
    run.case('forged context', group, lambda: run.refuse(call(user=wrong_role, headers=forged_as), (403,)))


# ---- task: close (cross-entity rule in a batch transition) ------------------------------------------------

def close_cases(run):
    i = run.ids
    OP = CLOSE
    run.op = OP

    def call(ids, user='carol', key='key', raw=None, headers=None):
        return lambda: post('/purchases/bulk-close', {'ids': ids}, user, key, raw, headers)

    def success(ids, user='carol', key='key', strict=True):
        want = byte_sorted(ids)
        r, b, a = run.apply(call(ids, user, key), lambda p: update(p, set(ids), status='closed'), want)
        if strict:
            assert r['status'] == 200 and r['body'] == {'closedIds': want} and r['replayed'] == 'false', r
            assert purchases(a) == update(purchases(b), set(ids), status='closed'), 'unexpected purchase changes'
            row = run.ar_row(b, a, TENANT[user], user, OP, key)
            assert row['response'] == {'closedIds': want}, row
        else:
            assert ok(r), r
        return r, b, a

    E = 'close-'
    run.case('fully received in one delivery', E + 'functional', lambda: success([i['c1']]))
    run.case('accepted deliveries add up', E + 'functional', lambda: success([i['c2']]))
    run.case('over-received', E + 'functional', lambda: success([i['c6']]))
    run.case('batch is sorted', E + 'functional', lambda: success([i['c6'], i['cuni'], i['Cz'], i['c1'], i['c2']]))
    run.case('other tenant approver', E + 'functional', lambda: success([i['cother']], user='erin'))
    auth_cases(run, E + 'authorization', lambda **kw: call([i['c1']], **kw), 'alice',
               {'X-Role': 'approver', 'X-User': 'carol', 'X-Tenant-ID': 'tenant-a'})
    for label, name in [('missing', 'missing'), ('other tenant', 'cother'), ('own', 'cown'), ('not approved', 'p1'),
                        ('short by a rejected delivery', 'c3'), ('delivery not yet accepted', 'c4'), ('nothing received', 'c5')]:
        run.case('ineligible ' + label, E + 'eligibility',
                 lambda name=name: run.refuse(call([i['c1'], i[name]]), targets=[i['c1'], i[name]]))
    run.case('ineligible partly received alone', E + 'eligibility', lambda: run.refuse(call([i['done']]), targets=[i['done']]))
    body = json.dumps({'ids': [i['c1']]}).encode()
    input_cases(run, E + 'input', lambda **kw: call([i['c1']], **kw), body,
                [('empty ids', {'raw': b'{"ids":[]}'}, (400,)),
                 ('duplicate ids', {'raw': json.dumps({'ids': [i['c1'], i['c1']]}).encode()}, (400,)),
                 ('unknown field', {'raw': json.dumps({'ids': [i['c1']], 'status': 'closed'}).encode()}, (400,))])

    def replay():
        first, _, _ = success([i['c2'], i['c1']])
        r, _, _ = run.step('replay', call([i['c1'], i['c2']]), [i['c1'], i['c2']])
        assert r['status'] == 200 and r['raw'] == first['raw'] and r['replayed'] == 'true', r
        assert not run.current['steps'][-1]['diff'], 'replay wrote data'
    run.case('replay', E + 'idempotency', replay)
    run.case('same key different purchases', E + 'idempotency',
             lambda: (success([i['c1']]), run.refuse(call([i['c2']]), (409,), [i['c2']])))
    run.case('key scoped by actor', E + 'idempotency', lambda: (success([i['c1']]), success([i['c2']], user='bob')))

    def audits(pid):
        return lambda b, a, rs: len([r for r in added(b, a, 'ApprovalAudit') if r['purchaseId'] == pid]) > 1

    def concurrent_same():
        rs, b, a = run.step('concurrent', lambda: parallel([call([i['c1']]), call([i['c1']])]), [i['c1']], allowed=1,
                            double=audits(i['c1']))
        assert all(x['status'] == 200 for x in rs) and sorted(x['replayed'] for x in rs) == ['false', 'true'], rs
        assert rs[0]['raw'] == rs[1]['raw'], rs
        assert purchases(a) == update(purchases(b), {i['c1']}, status='closed'), 'unexpected purchase changes'
        run.ar_row(b, a, 'tenant-a', 'carol', OP, 'key')
    run.case('concurrent identical', E + 'concurrency', concurrent_same)

    def concurrent_keys():
        rs, b, a = run.step('concurrent', lambda: parallel([call([i['c1']], key='key-a'), call([i['c1'], i['c2']], key='key-b')]),
                            [i['c1'], i['c2']], allowed=1, double=audits(i['c1']))
        assert sorted((x['status'] or 0) // 100 for x in rs) == [2, 4], rs
        won = [i['c1']] if rs[0]['status'] == 200 else [i['c1'], i['c2']]
        assert purchases(a) == update(purchases(b), set(won), status='closed'), 'unexpected purchase changes'
        assert len(added(b, a, 'ActionRequest')) == 1, added(b, a, 'ActionRequest')
    run.case('concurrent overlapping batches apply once', E + 'concurrency', concurrent_keys)
    run.case('ActionRequest failure and same-key retry', E + 'failure-rollback',
             faulted(run, 'ActionRequest', call([i['c1'], i['c2']]), [i['c1'], i['c2']], lambda: success([i['c1'], i['c2']])))
    for label, fn in [('rejected', lambda: post('/purchases/bulk-reject', {'ids': [i['c1']]}, 'carol', 'key-2')),
                      ('approved', lambda: post('/purchases/bulk-approve', {'ids': [i['c1']]}, 'carol', 'key-2')),
                      ('reassigned', lambda: post('/purchases/bulk-reassign', {'ids': [i['c1']], 'assignee': 'hana'}, 'carol', 'key-2')),
                      ('closed again', lambda: post('/purchases/bulk-close', {'ids': [i['c1']]}, 'bob', 'key-2'))]:
        run.case('closed cannot be ' + label, E + 'lifecycle', lambda fn=fn: (success([i['c1']]), run.refuse(fn, targets=[i['c1']])))

    # ---- implicit -------------------------------------------------------------------------------
    def audited(ids):
        _, b, a = success(ids, strict=False)
        got = sorted(added(b, a, 'ApprovalAudit'), key=lambda r: r['purchaseId'].encode())
        want = [{'tenantId': 'tenant-a', 'purchaseId': pid, 'approvedBy': 'carol', 'operation': OP, 'idempotencyKey': 'key',
                 'fromStatus': 'approved', 'toStatus': 'closed', 'reason': None} for pid in byte_sorted(ids)]
        assert got == want, {'new audit rows': got, 'expected': want}
    run.case('audit row per target', 'implicit-audit', lambda: audited([i['c2'], i['Cz'], i['c1']]))
    run.case('audit failure rolls back and same-key retry', 'implicit-audit',
             faulted(run, 'ApprovalAudit', call([i['c1'], i['c2']]), [i['c1'], i['c2']], lambda: audited([i['c1'], i['c2']])))

    def hashed(ids):
        _, _, a = success(ids, strict=False)
        row = next((r for r in rows(a, 'ActionRequest') if r['operation'] == OP), None)
        assert row and row['requestHash'] == fingerprint(OP, ids), {'stored': row and row['requestHash']}
    run.case('requestHash', 'implicit-request-hash', lambda: hashed([i['c1']]))
    run.case('requestHash batch Unicode ID', 'implicit-request-hash', lambda: hashed([i['cuni'], i['c2']]))

    def evented(ids):
        _, b, a = success(ids, strict=False)
        events(b, a, ids, 'purchase.closed', 'carol')
    run.case('outbox row', 'implicit-outbox', lambda: evented([i['c1']]))
    run.case('outbox row per target', 'implicit-outbox', lambda: evented([i['c2'], i['c1']]))
    run.case('outbox failure rolls back and same-key retry', 'implicit-outbox',
             faulted(run, 'PurchaseEvent', call([i['c1'], i['c2']]), [i['c1'], i['c2']], lambda: evented([i['c1'], i['c2']])))
    single = lambda pid, key: call([pid], key=key)
    run.case('other tenant denied like missing', 'implicit-uniform-denial', same_denial(run, i['missing'], [i['cother']], single))
    run.case('own, not approved or not fully received denied like missing', 'implicit-uniform-denial',
             same_denial(run, i['missing'], [i['cown'], i['p1'], i['c3'], i['c4'], i['c5']], single))


# ---- task: send-back (approval chain with a time window) --------------------------------------------------

def send_back_cases(run):
    i = run.ids
    OP = SEND_BACK
    run.op = OP

    def call(pid, reason='quote changed', user='carol', key='key', raw=None, headers=None):
        return lambda: post('/purchases/%s/send-back' % pid, {'reason': reason}, user, key, raw, headers)

    def success(pid, user='carol', key='key', reason='quote changed', strict=True):
        r, b, a = run.apply(call(pid, reason, user, key), lambda p: update(p, {pid}, status='submitted'), [pid])
        if strict:
            assert r['status'] == 200 and r['body'] == {'id': pid, 'status': 'submitted'} and r['replayed'] == 'false', r
            assert purchases(a) == update(purchases(b), {pid}, status='submitted'), 'unexpected purchase changes'
            row = run.ar_row(b, a, TENANT[user], user, OP, key)
            assert row['response'] == {'id': pid, 'status': 'submitted'}, row
        else:
            assert ok(r), r
        return r, b, a

    E = 'send-back-'
    run.case('recent first approval', E + 'functional', lambda: success(i['second']))
    run.case('first approval 70 hours ago', E + 'functional', lambda: success(i['s1']))
    run.case('latest first approval decides', E + 'functional', lambda: success(i['sre']))
    run.case('Unicode ID', E + 'functional', lambda: success(i['suni']))
    run.case('other tenant approver', E + 'functional', lambda: success(i['sother'], user='erin'))
    auth_cases(run, E + 'authorization', lambda **kw: call(i['second'], **kw), 'alice',
               {'X-Role': 'approver', 'X-User': 'carol', 'X-Tenant-ID': 'tenant-a'})
    for label, name, user in [('missing', 'missing', 'carol'), ('other tenant', 'sother', 'carol'), ('own', 'sown', 'carol'),
                              ('submitted', 'p1', 'carol'), ('approved', 'done', 'carol'), ('own first approval', 'second', 'bob'),
                              ('own latest first approval', 'sre', 'bob'), ('first approval 74 hours ago', 'sold', 'carol')]:
        run.case('ineligible ' + label, E + 'eligibility',
                 lambda name=name, user=user: run.refuse(call(i[name], user=user), targets=[i[name]]))
    body = json.dumps({'reason': 'quote changed'}).encode()
    input_cases(run, E + 'input', lambda **kw: call(i['second'], **kw), body,
                [('missing reason', {'raw': b'{}'}, (400,)), ('empty reason', {'reason': ''}, (400,)),
                 ('reason over 200 bytes', {'reason': 'r' * 201}, (400,)),
                 ('unknown field', {'raw': b'{"reason":"x","status":"approved"}'}, (400,))])

    def replay():
        first, _, _ = success(i['second'])
        r, _, _ = run.step('replay', call(i['second']), [i['second']])
        assert r['status'] == 200 and r['raw'] == first['raw'] and r['replayed'] == 'true', r
        assert not run.current['steps'][-1]['diff'], 'replay wrote data'
    run.case('replay', E + 'idempotency', replay)
    run.case('same key different purchase', E + 'idempotency',
             lambda: (success(i['second']), run.refuse(call(i['s1']), (409,), [i['s1']])))
    run.case('key scoped by actor', E + 'idempotency', lambda: (success(i['second']), success(i['sother'], user='erin')))

    audits = lambda b, a, rs: len([r for r in added(b, a, 'ApprovalAudit') if r['purchaseId'] == i['second']]) > 1

    def concurrent_same():
        rs, b, a = run.step('concurrent', lambda: parallel([call(i['second']), call(i['second'])]), [i['second']], allowed=1, double=audits)
        assert all(x['status'] == 200 for x in rs) and sorted(x['replayed'] for x in rs) == ['false', 'true'], rs
        assert rs[0]['raw'] == rs[1]['raw'], rs
        assert purchases(a) == update(purchases(b), {i['second']}, status='submitted'), 'unexpected purchase changes'
        run.ar_row(b, a, 'tenant-a', 'carol', OP, 'key')
    run.case('concurrent identical', E + 'concurrency', concurrent_same)

    def concurrent_keys():
        rs, b, a = run.step('concurrent', lambda: parallel([call(i['second'], key='key-a'), call(i['second'], key='key-b')]),
                            [i['second']], allowed=1, double=audits)
        assert sorted((x['status'] or 0) // 100 for x in rs) == [2, 4], rs
        assert purchases(a) == update(purchases(b), {i['second']}, status='submitted'), 'unexpected purchase changes'
        assert len(added(b, a, 'ActionRequest')) == 1, added(b, a, 'ActionRequest')
    run.case('concurrent different keys apply once', E + 'concurrency', concurrent_keys)
    run.case('ActionRequest failure and same-key retry', E + 'failure-rollback',
             faulted(run, 'ActionRequest', call(i['second']), [i['second']], lambda: success(i['second'])))

    def approve_again():
        success(i['second'])
        r, _, a = run.apply(lambda: post('/purchases/bulk-approve', {'ids': [i['second']]}, 'carol', 'key-2'),
                            lambda p: update(p, {i['second']}, status='pending-second'), [i['second']], op='existing')
        assert r['status'] == 200 and r['body'] == {'approvedIds': [i['second']]}, r
    run.case('sent-back purchase goes through both levels again', E + 'lifecycle', approve_again)

    def previous_first_approver():
        approve_again()
        r, _, _ = run.apply(lambda: post('/purchases/%s/countersign' % i['second'], {'amount': 70000}, 'bob', 'key-3'),
                            lambda p: update(p, {i['second']}, status='approved'), [i['second']], op='existing')
        assert r['status'] == 200 and r['body'] == {'id': i['second'], 'status': 'approved'}, r
    run.case('earlier first approver may countersign after re-approval', E + 'lifecycle', previous_first_approver)
    run.case('sent-back purchase cannot be countersigned', E + 'lifecycle', lambda: (success(i['second']), run.refuse(
        lambda: post('/purchases/%s/countersign' % i['second'], {'amount': 70000}, 'bob', 'key-2'), targets=[i['second']])))
    run.case('sent-back purchase cannot be sent back again', E + 'lifecycle',
             lambda: (success(i['second']), run.refuse(call(i['second'], key='key-2'), targets=[i['second']])))

    # ---- implicit -------------------------------------------------------------------------------
    def audited(reason='quote changed'):
        _, b, a = success(i['second'], reason=reason, strict=False)
        got = added(b, a, 'ApprovalAudit')
        assert got == [{'tenantId': 'tenant-a', 'purchaseId': i['second'], 'approvedBy': 'carol', 'operation': OP, 'idempotencyKey': 'key',
                        'fromStatus': 'pending-second', 'toStatus': 'submitted', 'reason': reason}], {'new audit rows': got}
    run.case('audit row', 'implicit-audit', audited)
    run.case('audit failure rolls back and same-key retry', 'implicit-audit',
             faulted(run, 'ApprovalAudit', call(i['second']), [i['second']], audited))

    def hashed():
        _, _, a = success(i['second'], strict=False)
        row = next((r for r in rows(a, 'ActionRequest') if r['operation'] == OP and r['actorId'] == 'carol'), None)
        assert row and row['requestHash'] == fingerprint(OP, [i['second']]), {'stored': row and row['requestHash']}
    run.case('requestHash', 'implicit-request-hash', hashed)

    def reason_not_hashed():
        success(i['second'], strict=False)
        r, _, _ = run.step('replay', call(i['second'], reason='another reason'), [i['second']])
        assert r['status'] == 200 and r['replayed'] == 'true', r
    run.case('reason is not part of the request digest', 'implicit-request-hash', reason_not_hashed)

    def evented():
        _, b, a = success(i['second'], strict=False)
        events(b, a, [i['second']], 'purchase.sent_back', 'carol')
    run.case('outbox row', 'implicit-outbox', evented)
    run.case('outbox failure rolls back and same-key retry', 'implicit-outbox',
             faulted(run, 'PurchaseEvent', call(i['second']), [i['second']], evented))
    single = lambda pid, key: call(pid, key=key)
    run.case('other tenant denied like missing', 'implicit-uniform-denial', same_denial(run, i['missing'], [i['sother']], single))
    run.case('own, wrong state or expired denied like missing', 'implicit-uniform-denial',
             same_denial(run, i['missing'], [i['sown'], i['p1'], i['done'], i['sold']], single))

    def first_approver_denied_like_missing():
        missing = run.refuse(call(i['missing'], user='bob', key='probe-missing'), targets=[i['missing']])['status']
        status = run.refuse(call(i['second'], user='bob', key='probe-first'), targets=[i['second']])['status']
        assert status == missing, {'missing': missing, 'first approver': status}
    run.case('first approver denied like missing', 'implicit-uniform-denial', first_approver_denied_like_missing)


# ---- task: amend (all-or-nothing batch with one value per target, multi-field rules) ----------------------

def amend_cases(run):
    i = run.ids
    OP = AMEND
    run.op = OP
    initial = {r['id']: r['amount'] for r in run.fixtures()['Purchase']}

    def call(items, reason='supplier quote', user='alice', key='key', raw=None, headers=None):
        body = {'items': [{'id': pid, 'amount': amount} for pid, amount in items], 'reason': reason}
        return lambda: post('/purchases/bulk-amend', body, user, key, raw, headers)

    def response(items):
        return {'amended': [{'id': pid, 'amount': amount} for pid, amount in sorted(items, key=lambda x: x[0].encode())]}

    def expected(items):
        return lambda p: {k: ({**v, 'amount': dict(items)[k]} if k in dict(items) else v) for k, v in p.items()}

    def success(items, user='alice', key='key', reason='supplier quote', strict=True):
        ids = byte_sorted(pid for pid, _ in items)
        r, b, a = run.apply(call(items, reason, user, key), expected(items), ids)
        if strict:
            assert r['status'] == 200 and r['body'] == response(items) and r['replayed'] == 'false', r
            assert purchases(a) == expected(items)(purchases(b)), 'unexpected purchase changes'
            row = run.ar_row(b, a, TENANT[user], user, OP, key)
            assert row['response'] == response(items), row
        else:
            assert ok(r), r
        return r, b, a

    E = 'amend-'
    run.case('decrease', E + 'functional', lambda: success([(i['a1'], 900)]))
    run.case('increase by exactly ten percent', E + 'functional', lambda: success([(i['a1'], 1100)]))
    run.case('batch is sorted', E + 'functional', lambda: success([(i['a2'], 2100), (i['Az'], 3300), (i['a1'], 500), (i['auni'], 1000)]))
    run.case('up to the second-approval line', E + 'functional', lambda: success([(i['a3'], 50000)]))
    run.case('above the line stays above', E + 'functional', lambda: success([(i['abig'], 66000), (i['big'], 50001)]))
    run.case('other tenant requester', E + 'functional', lambda: success([(i['aother'], 1100)], user='dave'))
    auth_cases(run, E + 'authorization', lambda **kw: call([(i['a1'], 900)], **kw), 'carol',
               {'X-Role': 'requester', 'X-User': 'alice', 'X-Tenant-ID': 'tenant-a'})
    for label, name in [('missing', 'missing'), ('other tenant', 'aother'), ('not own', 'p3'), ('approved', 'done'),
                        ('rejected', 'gone'), ('waiting for countersignature', 'second')]:
        run.case('ineligible ' + label, E + 'eligibility',
                 lambda name=name: run.refuse(call([(i['a1'], 900), (i[name], 9)]), targets=[i['a1'], i[name]]))
    for label, items in [('increase above ten percent', [(i['a1'], 1101)]),
                         ('crosses the line upwards', [(i['a3'], 50001)]),
                         ('crosses the line downwards', [(i['abig'], 50000)]),
                         ('one bad value among good ones', [(i['a1'], 900), (i['a2'], 2201)])]:
        run.case('value ' + label, E + 'value', lambda items=items: run.refuse(call(items), targets=[pid for pid, _ in items]))

    good = [{'id': i['a1'], 'amount': 900}]
    raw = lambda **v: json.dumps({'items': good, 'reason': 'x', **v}).encode()
    input_cases(run, E + 'input', lambda **kw: call([(i['a1'], 900)], **kw), raw(),
                [('empty items', {'raw': raw(items=[])}, (400,)),
                 ('duplicate ids', {'raw': raw(items=good + [{'id': i['a1'], 'amount': 800}])}, (400,)),
                 ('amount zero', {'raw': raw(items=[{'id': i['a1'], 'amount': 0}])}, (400,)),
                 ('amount not an integer', {'raw': raw(items=[{'id': i['a1'], 'amount': 900.5}])}, (400,)),
                 ('missing reason', {'raw': json.dumps({'items': good}).encode()}, (400,)),
                 ('unknown item field', {'raw': raw(items=[{'id': i['a1'], 'amount': 900, 'status': 'approved'}])}, (400,))])

    def replay():
        first, _, _ = success([(i['a2'], 2100), (i['a1'], 900)])
        r, _, _ = run.step('replay', call([(i['a1'], 900), (i['a2'], 2100)]), [i['a1'], i['a2']])
        assert r['status'] == 200 and r['raw'] == first['raw'] and r['replayed'] == 'true', r
        assert not run.current['steps'][-1]['diff'], 'replay wrote data'
    run.case('replay', E + 'idempotency', replay)
    run.case('same key different amounts', E + 'idempotency',
             lambda: (success([(i['a1'], 900)]), run.refuse(call([(i['a1'], 950)]), (409,), [i['a1']])))
    run.case('same key different purchases', E + 'idempotency',
             lambda: (success([(i['a1'], 900)]), run.refuse(call([(i['a2'], 900)]), (409,), [i['a2']])))
    run.case('key scoped by actor', E + 'idempotency',
             lambda: (success([(i['a1'], 900)]), success([(i['aother'], 900)], user='dave')))

    hist = lambda b, a, rs: len([r for r in added(b, a, 'AmendmentHistory') if r['purchaseId'] == i['a1']]) > 1

    def concurrent_same():
        rs, b, a = run.step('concurrent', lambda: parallel([call([(i['a1'], 900)]), call([(i['a1'], 900)])]), [i['a1']],
                            allowed=1, double=hist)
        assert all(x['status'] == 200 for x in rs) and sorted(x['replayed'] for x in rs) == ['false', 'true'], rs
        assert rs[0]['raw'] == rs[1]['raw'], rs
        assert purchases(a) == expected([(i['a1'], 900)])(purchases(b)), 'unexpected purchase changes'
        run.ar_row(b, a, 'tenant-a', 'alice', OP, 'key')
    run.case('concurrent identical', E + 'concurrency', concurrent_same)

    targets = byte_sorted([i['a1'], i['a2']])
    values = {'key-r': {i['a1']: 1100, i['a2']: 2200}, 'key-o': {i['a1']: 1050, i['a2']: 2100}}

    def broken_chain(b, a, rs):
        """Both requests applied but some target's new history rows do not form one chain: a lost update."""
        if not all(ok(x) for x in rs):
            return False
        new = added(b, a, 'AmendmentHistory')
        for pid in targets:
            mine = [(r['fromAmount'], r['toAmount']) for r in new if r['purchaseId'] == pid]
            if len(mine) == 2 and len(chains(mine, initial[pid], purchases(a)[pid]['amount'])) != 1:
                return True
        return False

    def concurrent_keys(check=None):
        rs, b, a = run.step('concurrent', lambda: parallel([call(list(values['key-r'].items()), key='key-r'),
                                                            call(list(values['key-o'].items())[::-1], key='key-o')]),
                            targets, allowed=2, double=broken_chain)
        assert all(x['status'] == 200 and x['replayed'] == 'false' for x in rs), rs
        final = {pid: purchases(a)[pid]['amount'] for pid in targets}
        assert final in [{pid: v[pid] for pid in targets} for v in values.values()], final
        others = lambda s: {k: v for k, v in purchases(s).items() if k not in targets}
        assert others(a) == others(b), 'non-target purchases changed'
        keys = {(r['tenantId'], r['actorId'], r['operation'], r['key']) for r in added(b, a, 'ActionRequest')}
        assert keys == {('tenant-a', 'alice', OP, 'key-r'), ('tenant-a', 'alice', OP, 'key-o')}, keys
        if check:
            check(b, a)
    run.case('concurrent different keys', E + 'concurrency', concurrent_keys)
    run.case('ActionRequest failure and same-key retry', E + 'failure-rollback',
             faulted(run, 'ActionRequest', call([(i['a1'], 900), (i['a2'], 2100)]), targets,
                     lambda: success([(i['a1'], 900), (i['a2'], 2100)])))

    def decided(path, field, pid, amount, status):
        success([(pid, amount)])
        r, _, a = run.apply(lambda: post('/purchases/' + path, {'ids': [pid]}, 'bob', 'key-2'),
                            lambda p: update(p, {pid}, status=status), [pid], op='existing')
        assert r['status'] == 200 and r['body'] == {field: [pid]}, r
        assert purchases(a)[pid]['amount'] == amount, purchases(a)[pid]
    run.case('amended purchase up to the line is approved in one step', E + 'lifecycle',
             lambda: decided('bulk-approve', 'approvedIds', i['a3'], 50000, 'approved'))
    run.case('amended purchase above the line needs a second approver', E + 'lifecycle',
             lambda: decided('bulk-approve', 'approvedIds', i['abig'], 66000, 'pending-second'))
    run.case('amended purchase can be rejected', E + 'lifecycle', lambda: decided('bulk-reject', 'rejectedIds', i['a1'], 900, 'rejected'))

    # ---- implicit -------------------------------------------------------------------------------
    def recorded(items, reason='supplier quote'):
        _, b, a = success(items, reason=reason, strict=False)
        got = sorted(added(b, a, 'AmendmentHistory'), key=lambda r: r['purchaseId'].encode())
        want = [{'tenantId': 'tenant-a', 'purchaseId': pid, 'fromAmount': initial[pid], 'toAmount': amount, 'changedBy': 'alice',
                 'operation': OP, 'idempotencyKey': 'key', 'reason': reason} for pid, amount in sorted(items, key=lambda x: x[0].encode())]
        assert got == want, {'new history rows': got, 'expected': want}
    run.case('history row', 'implicit-history', lambda: recorded([(i['a1'], 900)]))
    run.case('history rows per target with previous amount', 'implicit-history',
             lambda: recorded([(i['a2'], 2100), (i['Az'], 3300), (i['a1'], 500)]))

    def chained(b, a):
        new = added(b, a, 'AmendmentHistory')
        for pid in targets:
            mine = [(r['fromAmount'], r['toAmount']) for r in new if r['purchaseId'] == pid]
            assert len(mine) == 2, {'history rows': mine}
            final = purchases(a)[pid]['amount']
            assert len(chains(mine, initial[pid], final)) == 1, {'history rows': mine, 'initial': initial[pid], 'final': final}
    run.case('concurrent amendments form one chain', 'implicit-history', lambda: concurrent_keys(chained))
    run.case('history failure rolls back and same-key retry', 'implicit-history',
             faulted(run, 'AmendmentHistory', call([(i['a1'], 900), (i['a2'], 2100)]), targets,
                     lambda: recorded([(i['a1'], 900), (i['a2'], 2100)])))

    def hashed(items):
        _, _, a = success(items, strict=False)
        row = next((r for r in rows(a, 'ActionRequest') if r['operation'] == OP), None)
        want = fingerprint(OP, ['%s=%d' % (pid, amount) for pid, amount in items])
        assert row and row['requestHash'] == want, {'stored': row and row['requestHash']}
    run.case('requestHash', 'implicit-request-hash', lambda: hashed([(i['a1'], 900)]))
    run.case('requestHash batch Unicode ID', 'implicit-request-hash', lambda: hashed([(i['auni'], 1050), (i['a2'], 1900)]))

    def reason_not_hashed():
        success([(i['a1'], 900)], strict=False)
        r, _, _ = run.step('replay', call([(i['a1'], 900)], reason='another reason'), [i['a1']])
        assert r['status'] == 200 and r['replayed'] == 'true', r
    run.case('reason is not part of the request digest', 'implicit-request-hash', reason_not_hashed)

    def evented(items):
        _, b, a = success(items, strict=False)
        events(b, a, [pid for pid, _ in items], 'purchase.amended', 'alice')
    run.case('outbox row', 'implicit-outbox', lambda: evented([(i['a1'], 900)]))
    run.case('outbox row per target', 'implicit-outbox', lambda: evented([(i['a2'], 2100), (i['a1'], 900)]))
    run.case('outbox failure rolls back and same-key retry', 'implicit-outbox',
             faulted(run, 'PurchaseEvent', call([(i['a1'], 900), (i['a2'], 2100)]), targets,
                     lambda: evented([(i['a1'], 900), (i['a2'], 2100)])))

    def value_denial():
        missing = run.refuse(call([(i['missing'], 900)], key='probe-missing'), targets=[i['missing']])['status']
        for n, items in enumerate([[(i['a1'], 1101)], [(i['a3'], 50001)], [(i['abig'], 50000)]]):
            status = run.refuse(call(items, key='probe-%d' % n), targets=[items[0][0]])['status']
            assert status == missing, {'missing': missing, str(items): status}
    run.case('unacceptable amounts denied like missing', 'implicit-uniform-denial', value_denial)
    run.case('other tenant or not own denied like missing', 'implicit-uniform-denial',
             same_denial(run, i['missing'], [i['aother'], i['p3'], i['done']], lambda pid, key: call([(pid, 9)], key=key)))


TASKS = {'close': close_cases, 'send-back': send_back_cases, 'amend': amend_cases}


def run(db, task, seed):
    r = Tasks(db, task, seed)
    TASKS[task](r)
    regression(r)
    return r.results()
