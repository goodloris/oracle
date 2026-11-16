#!/usr/bin/env python3
"""Standalone generation worker. Requires no evaluator checkout or private seed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as stream:json.dump(value,stream,indent=2);stream.write('\n')
def git(root,*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
def command(root,*args):return subprocess.run(['git','-C',str(root),*map(str,args)],check=True,capture_output=True)

# Kiro generation agent: only local file/shell tools, no MCP servers or subagents.
# It runs under a fresh per-invocation KIRO_HOME so global steering, skills, agents,
# memories and settings cannot leak into the trial. Authentication is not copied.
KIRO_AGENT={'name':'poc-v3-generator',
 'description':'enterprise_agent v3 generation agent: local file/shell tools only',
 'prompt':'You are a coding agent completing one bounded task in the current Git checkout. Follow the task instructions exactly.',
 'tools':['fs_read','fs_write','execute_bash'],'allowedTools':['fs_read','fs_write','execute_bash'],
 'resources':[],'hooks':{},'mcpServers':{},'includeMcpJson':False}

def parse_kiro(text,returncode,timed_out):
    """Classify kiro-cli `--output-format stream-json` (ACP) events."""
    events=[];malformed=False
    for line in text.splitlines():
        if not line.strip():continue
        try:
            event=json.loads(line)
            if not isinstance(event,dict) or not isinstance(event.get('type'),str):raise ValueError('event')
            events.append(event)
        except ValueError:malformed=True
    meta=[e['data']['update'].get('_meta',{}).get('kiro',{}) for e in events
          if e['type']=='sessionUpdate' and isinstance(e.get('data',{}).get('update'),dict)]
    started=any(m.get('kind')=='turn_start' for m in meta)
    finished=[e.get('data',{}) for e in events if e['type']=='runFinished']
    errors=any(e['type']=='runError' for e in events) or any(f.get('status')!='success' for f in finished)
    models=[o.get('currentValue') for e in events if e['type']=='sessionUpdate'
            for o in e['data']['update'].get('configOptions',[]) if o.get('id')=='model']
    credits=[m['credits'] for m in meta if m.get('kind')=='session_usage' and isinstance(m.get('credits'),list)]
    usage=None
    if len(finished)==1 and credits:
        values=[c.get('usage') for c in credits[-1] if c.get('unit')=='credit']
        if values and all(type(v) in (int,float) and v>=0 for v in values):
            # Kiro reports metered credits, not tokens; token counts stay unknown.
            usage={'credits':sum(values)}
    if timed_out:status='budget-exceeded' if started else 'runtime-blocked'
    elif returncode!=0 or errors:status='provider-error' if started else 'runtime-blocked'
    elif malformed or len(finished)!=1 or not started:status='protocol-error'
    else:status='completed'
    return {'status':status,'usage':usage,'turn_started':started,'run_finished':len(finished)==1,
            'reported_model':models[-1] if models else None,'event_count':len(events),
            'agent_messages':[f.get('finalText','') for f in finished if f.get('status')=='success'],
            'errors':[e.get('data',{}).get('message','') for e in events if e['type']=='runError']}

def invoke(binary,job,candidate,prompt,directory,timeout,sandbox):
    directory.mkdir(parents=True,exist_ok=False)
    env={k:v for k,v in os.environ.items() if k not in
         ('ADMIN_DATABASE_URL','DATABASE_URL','POC_DB_RESET','POC_MUTATION','POC_ALLOW_MUTATIONS')}
    engine=job.get('engine','codex')
    if engine=='kiro':
        home=directory/'kiro-home';(home/'agents').mkdir(parents=True)
        (home/'agents'/'poc-v3-generator.json').write_text(json.dumps(KIRO_AGENT))
        env['KIRO_HOME']=str(home)
        # Kiro has no workspace-write sandbox: read-only is approximated by trusting no tools.
        cmd=[binary,'chat','--no-interactive','--output-format','stream-json','--agent-engine','v3',
             '--agent','poc-v3-generator','--model',job['model'],'--effort',job['reasoning_effort'],
             '--trust-tools=' if sandbox=='read-only' else '--trust-all-tools']
    else:
        cmd=[binary,'exec','--json','--ephemeral','--color','never','--sandbox',sandbox,
             '--model',job['model'],'-C',str(candidate),'-c','model_reasoning_effort='+json.dumps(job['reasoning_effort']),'-']
    start=time.monotonic();timed_out=False
    with (directory/'events.jsonl').open('x') as stdout,(directory/'stderr.log').open('x') as stderr:
        child=subprocess.Popen(cmd,cwd=candidate,env=env,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,text=True,start_new_session=True)
        try:child.communicate(prompt,timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out=True
            try:os.killpg(child.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
    if engine=='kiro':
        result=parse_kiro((directory/'events.jsonl').read_text(),child.returncode,timed_out)
        result.update({'engine':'kiro','generation_seconds':round(time.monotonic()-start,3),
          'requested_model':job['model'],'reasoning_effort':job['reasoning_effort'],'sandbox':sandbox,
          'command':cmd,'returncode':child.returncode,'timed_out':timed_out})
        save(directory/'invocation.json',result);return result
    events=[];malformed=False
    for line in (directory/'events.jsonl').read_text().splitlines():
        if not line.strip():continue
        try:
            event=json.loads(line)
            if not isinstance(event,dict) or not isinstance(event.get('type'),str):raise ValueError('event')
            events.append(event)
        except ValueError:malformed=True
    started=any(e['type']=='turn.started' for e in events)
    completed=[e for e in events if e['type']=='turn.completed']
    errors=any(e['type'] in ('error','turn.failed') for e in events)
    usage=None
    if len(completed)==1 and isinstance(completed[0].get('usage'),dict):
        usage={k:v for k,v in completed[0]['usage'].items() if k in
               ('input_tokens','output_tokens','cached_input_tokens','reasoning_output_tokens') and type(v) is int and v>=0} or None
    if timed_out:status='budget-exceeded' if started else 'runtime-blocked'
    elif child.returncode!=0 or errors:status='provider-error' if started else 'runtime-blocked'
    elif malformed or len(completed)!=1 or not started:status='protocol-error'
    else:status='completed'
    result={'status':status,'usage':usage,'generation_seconds':round(time.monotonic()-start,3),
      'requested_model':job['model'],'reasoning_effort':job['reasoning_effort'],'sandbox':sandbox,'command':cmd,
      'returncode':child.returncode,'timed_out':timed_out,
      'agent_messages':[e.get('item',{}).get('text','') for e in events if e['type']=='item.completed' and e.get('item',{}).get('type')=='agent_message']}
    save(directory/'invocation.json',result);return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--kit',default=str(Path(__file__).resolve().parent))
    parser.add_argument('--output',required=True);parser.add_argument('--codex-bin',default='codex')
    parser.add_argument('--kiro-bin',default='kiro-cli');parser.add_argument('--synthetic-test-driver')
    a=parser.parse_args();kit=Path(a.kit).resolve();job=json.loads((kit/'job.json').read_text())
    if bool(a.synthetic_test_driver)!=(job['kind']=='synthetic-smoke'):raise ValueError('synthetic/real driver mismatch')
    # These are transport attestations, not signatures against a malicious operator.
    if digest(kit/'plan.json')!=job['plan_sha256'] or digest(kit/'manifest.json')!=job['manifest_sha256']:
        raise ValueError('kit identity/checksum mismatch')
    if digest(Path(__file__))!=job['worker_sha256']:raise ValueError('worker changed after preparation')
    candidate=kit/'candidate';m=json.loads((kit/'manifest.json').read_text());p=json.loads((kit/'plan.json').read_text())
    t=job['trial'];initial=m['initial_sha']
    if (p['kind']!=job['kind'] or t not in p['trials'] or m['trial_id']!=t['id']
        or m['plan_sha256']!=job['plan_sha256'] or m['files']!=p['exports'][t['cell']]
        or m['source_base_sha']!=p['base_sha'] or job['model']!=p['model']
        or job['worker_sha256']!=p['frozen']['benchmark/phase3/generation_worker.py']
        or job['reasoning_effort']!=p['reasoning_effort'] or job['generation_budget_seconds']!=p['generation_budget_seconds']
        or job.get('engine','codex')!=p.get('engine','codex')):
        raise ValueError('kit plan/manifest mismatch')
    if git(candidate,'rev-parse','HEAD')!=initial or git(candidate,'rev-list','--count','HEAD')!='1' or git(candidate,'remote') or git(candidate,'status','--porcelain'):
        raise ValueError('kit candidate is not a clean isolated base')
    if set(git(candidate,'ls-tree','-r','--name-only','HEAD').splitlines())!=set(m['files']):raise ValueError('unexpected kit files')
    for name,h in m['files'].items():
        if (candidate/name).is_symlink() or digest(candidate/name)!=h:raise ValueError('kit source tamper: '+name)
    out=Path(a.output).resolve()
    if out.is_relative_to(candidate):raise ValueError('output must be outside candidate')
    out.mkdir(parents=True,exist_ok=False)
    binary=a.synthetic_test_driver or (a.kiro_bin if job.get('engine','codex')=='kiro' else a.codex_bin)
    try:
        version=subprocess.check_output([binary,'--version'],text=True,timeout=10).strip()
        check=invoke(binary,job,candidate,'Reply exactly READY. Do not read or modify files. Do not call tools.',out/'probe',30,'read-only')
        ready=check['status']=='completed' and check['agent_messages'][-1:]==['READY']
    except (OSError,subprocess.SubprocessError) as error:
        version=None;check={'status':'runtime-blocked','error':str(error)};ready=False
    save(out/'preflight.json',{'ready':ready,'cli_version':version,'invocation':check})
    if not ready:return 3
    result=invoke(binary,job,candidate,t['prompt'],out/'generation',job['generation_budget_seconds'],'workspace-write')
    result['cli_version']=version
    outcome=result['status']
    if outcome=='completed':
        changed=subprocess.check_output(['git','-C',str(candidate),'diff','--name-only','-z','HEAD'])
        changed+=subprocess.check_output(['git','-C',str(candidate),'ls-files','--others','--exclude-standard','-z'])
        names=sorted({n.decode() for n in changed.split(b'\0') if n})
        if (any(not n.startswith('internal/candidate/') or not n.endswith('.go') for n in names)
            or any(path.is_symlink() for path in candidate.rglob('*')) or git(candidate,'remote')
            or git(candidate,'rev-parse','HEAD')!=initial):
            outcome='scope-violation'
        else:
            command(candidate,'add','--all','internal/candidate')
            command(candidate,'-c','user.name=POC v3 Worker','-c','user.email=poc@example.invalid','commit','--allow-empty','-m','v3 worker candidate')
            (out/'candidate.patch').write_bytes(subprocess.check_output(['git','-C',str(candidate),'diff','--binary',initial,'HEAD']))
            command(candidate,'bundle','create',out/'candidate.bundle','HEAD')
            save(out/'receipt.json',{'version':3,'kind':job['kind'],'plan_sha256':job['plan_sha256'],'trial':t,
              'candidate_sha':git(candidate,'rev-parse','HEAD'),'manifest_sha256':job['manifest_sha256'],
              'bundle_sha256':digest(out/'candidate.bundle'),'generation':result})
            outcome='generated'
    save(out/'outcome.json',{'version':3,'kind':job['kind'],'plan_sha256':job['plan_sha256'],
         'trial':t,'outcome':outcome,'passed':False,'generation':result})
    if outcome!='generated':save(out/'outcomes'/t['id']/'result.json',json.loads((out/'outcome.json').read_text()))
    return 0 if outcome in ('generated','budget-exceeded','scope-violation') else 3

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:print('v3 worker error: '+str(error),file=sys.stderr);sys.exit(1)
