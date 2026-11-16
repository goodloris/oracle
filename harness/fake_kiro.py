#!/usr/bin/env python3
"""Fake kiro-cli for the hand-off control: speaks the subset of `kiro-cli chat --output-format stream-json`
(ACP events) that benchmark/phase3/generation_worker.py parses, and never calls a model.

- `--version` prints a version line.
- The READY probe gets a successful turn whose final text is READY.
- The generation prompt copies $FAKE_KIRO_SOLUTIONS/<cell>/ (files relative to the candidate root) into the
  working directory, then reports a successful turn with a fixed credit usage.
The cell is read from ../job.json (the kit's job), so one solutions directory serves a whole run.
"""
import json
import os
from pathlib import Path
import shutil
import sys

CREDITS = 0.01


def emit(text, model, credits):
    events = [{'type': 'sessionUpdate', 'data': {'update': {'_meta': {'kiro': {'kind': 'turn_start'}}}}},
              {'type': 'sessionUpdate', 'data': {'update': {'configOptions': [{'id': 'model', 'currentValue': model}]}}},
              {'type': 'sessionUpdate', 'data': {'update': {'_meta': {'kiro': {'kind': 'session_usage',
                                                                              'credits': [{'unit': 'credit', 'usage': credits}]}}}}},
              {'type': 'runFinished', 'data': {'status': 'success', 'finalText': text}}]
    for e in events:
        print(json.dumps(e), flush=True)


def main():
    if sys.argv[1:] == ['--version']:
        print('fake-kiro-cli 0.0.0 (v7 hand-off control, no model)')
        return 0
    args = sys.argv[1:]
    model = args[args.index('--model') + 1] if '--model' in args else 'unknown'
    prompt = sys.stdin.read()
    if prompt.startswith('Reply exactly READY'):
        emit('READY', model, 0)
        return 0
    job = json.loads((Path.cwd().parent / 'job.json').read_text())
    source = Path(os.environ['FAKE_KIRO_SOLUTIONS']) / job['trial']['cell']
    for path in sorted(source.rglob('*')):
        if path.is_file():
            target = Path.cwd() / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    emit('done: copied the %s reference solution' % job['trial']['cell'], model, CREDITS)
    return 0


if __name__ == '__main__':
    sys.exit(main())
