#!/usr/bin/env python3
"""v9 platform event rules (private overlay of internal/opdef; to be ported to the public platform).

Two rules on top of opdef.Validate(), applied to every catalog entry:

  R1  An entry declares an Event that a registered consumer handles (consumedEvents), and no two entries share
      an event (the existing uniqueness check). An entry without an Event fails.
  R2  Only an operation on the protected exemption list (exemptOperations; today bulk-approve.v3) may carry an
      EventExemption; any other entry that declares one fails.

The registry (internal/opdef/registry.go) is platform-maintained and protected: it is generated from the consumer
list in internal/notify (the `case "<event>":` arms of audience()), so a new event becomes valid only once a
consumer for it exists. apply() turns an opdef.go (public fad8adf or a task's column overlay) into the rule version
by exact-once edits (the rule call in Validate, eventRule, and registry.go as a catalog package source, so the
catalog-bound Verifier requires it byte-identical); registry() renders registry.go from a notify.go. Both are deterministic, so the rule
platform is never stored separately (static controls compare the exports with apply()/registry()).

Usage: platform_rules.py diff OPDEF.go        # the rule patch as a unified diff (porting reference)
       platform_rules.py registry NOTIFY.go    # registry.go
"""
import difflib
import re
import sys

EXEMPT = ('bulk-approve.v3',)

CALL_OLD = '''		if err := o.validate(); err != nil {
			return fmt.Errorf("%s: %w", o.Name, err)
		}
'''
CALL_NEW = CALL_OLD + '''		if err := o.eventRule(); err != nil {
			return fmt.Errorf("%s: %w", o.Name, err)
		}
'''
FUNC_ANCHOR = '\nfunc (o Operation) validate() error {\n'
FUNC = '''
// eventRule is the platform event rule. An operation on the protected
// exemption list (registry.go) may carry an EventExemption; every other entry
// must declare an Event that a registered consumer handles. registry.go is
// generated from the protected consumer list in internal/notify, so an event
// becomes valid only once a consumer for it exists. Entries cannot exempt
// themselves.
func (o Operation) eventRule() error {
	exempt := slices.Contains(exemptOperations, o.Name)
	switch {
	case o.EventExemption != "" && !exempt:
		return fmt.Errorf("event exemption is reserved for the operations on the protected exemption list")
	case !exempt && !slices.Contains(consumedEvents, o.Event):
		return fmt.Errorf("event %q has no registered consumer in internal/notify", o.Event)
	}
	return nil
}
'''


EMBED = ('//go:embed catalog.go opdef.go\n', '//go:embed catalog.go opdef.go registry.go\n')
SOURCES = ('[]string{"catalog.go", "opdef.go"}', '[]string{"catalog.go", "opdef.go", "registry.go"}')


def apply(opdef):
    """The rule version of an opdef.go (str)."""
    for old in (CALL_OLD, FUNC_ANCHOR, EMBED[0], SOURCES[0]):
        assert opdef.count(old) == 1, old
    assert 'eventRule' not in opdef
    return opdef.replace(CALL_OLD, CALL_NEW).replace(FUNC_ANCHOR, FUNC + FUNC_ANCHOR).replace(*EMBED).replace(*SOURCES)


def consumers(notify):
    """Events with a consumer: the case arms of audience() in internal/notify/notify.go."""
    body = notify[notify.index('func audience('):]
    body = body[:body.index('\n}\n')]
    events = re.findall(r'^\tcase "([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+)":$', body, re.M)
    assert events and len(set(events)) == len(events), events
    return sorted(events)


def registry(notify):
    lines = ['package opdef', '',
             '// Platform-maintained and protected. consumedEvents is generated from the',
             '// consumer list in internal/notify (audience); do not edit by hand. Validate',
             '// requires every entry to declare one of these events unless its operation',
             '// is on exemptOperations.',
             'var consumedEvents = []string{']
    lines += ['\t"%s",' % e for e in consumers(notify)]
    lines += ['}', '',
              '// exemptOperations may carry an EventExemption instead of an Event.',
              'var exemptOperations = []Name{%s}' % ', '.join('"%s"' % n for n in EXEMPT), '']
    return '\n'.join(lines)


def main():
    cmd, path = sys.argv[1:3]
    text = open(path).read()
    if cmd == 'diff':
        sys.stdout.writelines(difflib.unified_diff(text.splitlines(True), apply(text).splitlines(True), 'a/internal/opdef/opdef.go',
                                                   'b/internal/opdef/opdef.go'))
    else:
        sys.stdout.write(registry(text))
    return 0


if __name__ == '__main__':
    sys.exit(main())
