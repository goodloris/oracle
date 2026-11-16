#!/usr/bin/env python3
"""Entry-correctness Oracle (outcome C): is the drafted (or reviewed) catalog entry the reference entry?

Input is the compiled catalog (harness/catalogcheck.py: opdef.All() after `go run`), never source text.
The equivalence rules are fixed before any data (prereg/preregistration-v8a.md, "C"):

  E1  Compiled form only. Source-level differences are equivalent: struct-literal field order, constant
      identifiers (e.g. `Return` vs `ReturnForRevision`), named constants vs. their literal values
      (`IDs` vs "ids", `Others` vs "others", `UniformDenial` vs 409), formatting, comments.
  E2  The entry is found by its operation name (the requirements give it); its position in the catalog
      is irrelevant (All() is sorted by name).
  E3  An omitted field equals its zero value (`Separation: NoSeparation`, `Tier: nil`, `ConfirmAmount: false`,
      `Response: ""` are the same as leaving them out): the compiled JSON uses omitempty.
  E4  Inputs and Fingerprint are ordered lists and are compared as lists: Inputs is the request and handler
      parameter order, Fingerprint the digest order. (Validate fixes "ids" first; both tasks have at most one
      other element, so no other ordering can validate.)
  E5  Event and EventExemption form one logical field "event": an exemption never equals an event, and two
      exemptions are not compared by text (neither reference entry has one).
  E6  The other catalog entries must be exactly the five public entries (compiled form); a changed, removed or
      extra entry is the logical field "catalog".

Logical fields, each reported right or wrong: name, kind, role, subject, from, to, tier, status, column, param,
response, inputs, fingerprint, record, event, separation, confirmAmount, denial, catalog.
Categories for the error distribution: event, fingerprint, record, separation, authority (role, subject),
state (from, to, status, tier), shape (kind, column, param, response, inputs, confirmAmount), name, denial,
catalog. An entry that does not compile, or a missing entry, is wrong on every field it cannot show.
"""
import json
import sys

FIELDS = ('name', 'kind', 'role', 'subject', 'from', 'to', 'tier', 'status', 'column', 'param', 'response',
          'inputs', 'fingerprint', 'record', 'event', 'separation', 'confirmAmount', 'denial', 'catalog')
CATEGORY = {'event': 'event', 'fingerprint': 'fingerprint', 'record': 'record', 'separation': 'separation',
            'role': 'authority', 'subject': 'authority', 'from': 'state', 'to': 'state', 'status': 'state',
            'tier': 'state', 'kind': 'shape', 'column': 'shape', 'param': 'shape', 'response': 'shape',
            'inputs': 'shape', 'confirmAmount': 'shape', 'name': 'name', 'denial': 'denial', 'catalog': 'catalog'}
CATEGORIES = ('event', 'fingerprint', 'record', 'separation', 'authority', 'state', 'shape', 'name', 'denial', 'catalog')
KEYS = ('name', 'kind', 'role', 'subject', 'from', 'to', 'tier', 'status', 'column', 'param', 'response', 'inputs',
        'fingerprint', 'record', 'event', 'eventExemption', 'separation', 'confirmAmount', 'denial')


def logical(entry):
    """Logical field values of one compiled entry (E3: absent == zero value; E5: event pair)."""
    e = {k: entry.get(k) for k in KEYS}
    zero = {'tier': None, 'separation': '', 'confirmAmount': False, 'response': '', 'from': '', 'to': '',
            'status': '', 'column': '', 'param': '', 'event': '', 'eventExemption': ''}
    for k, z in zero.items():
        if e[k] is None or e[k] == z:
            e[k] = z
    out = {k: e[k] for k in FIELDS if k not in ('event', 'catalog')}
    out['event'] = ('event', e['event']) if e['event'] else ('exempt', bool(e['eventExemption']))
    return out


def compare(name, candidate, reference):
    """candidate/reference: catalogcheck results. Returns the C record of one catalog."""
    ref = {e['name']: e for e in reference['entries']}
    want = logical(ref[name])
    if not candidate.get('compiled'):
        fields = {f: False for f in FIELDS}
        return {'correct': False, 'valid': False, 'compiled': False, 'found': False, 'fields': fields,
                'wrong': list(FIELDS), 'categories': categories(fields)}
    got_all = {e['name']: e for e in candidate['entries']}
    public = {k: v for k, v in ref.items() if k != name}
    extras = sorted(set(got_all) - set(public))
    # E2: the entry is the one with the required name; failing that, the only non-public entry (its name is wrong).
    key = name if name in got_all else (extras[0] if len(extras) == 1 else None)
    entry = got_all.get(key) if key else None
    others_ok = all(got_all.get(k) == v for k, v in public.items()) and set(extras) <= {key}
    if entry is None:
        fields = {f: f == 'catalog' and others_ok for f in FIELDS}  # no entry: every entry field wrong
    else:
        got = logical(entry)
        fields = {f: got[f] == want[f] for f in FIELDS if f != 'catalog'}
        fields['catalog'] = others_ok
    wrong = [f for f in FIELDS if not fields[f]]
    return {'correct': not wrong, 'valid': bool(candidate.get('valid')), 'compiled': True, 'found': entry is not None,
            'fields': fields, 'wrong': wrong, 'categories': categories(fields),
            'entry': entry, 'extra_entries': [x for x in extras if x != key]}


def categories(fields):
    return {c: all(fields[f] for f in FIELDS if CATEGORY[f] == c) for c in CATEGORIES}


if __name__ == '__main__':
    a, b, n = sys.argv[1:4]
    print(json.dumps(compare(n, json.load(open(a)), json.load(open(b)))))
