#!/usr/bin/env python3
"""Offline status-only review of saved official GitHub connector responses."""
from pathlib import Path
import hashlib,json,datetime
BASE=Path(__file__).resolve().parent
HEAD='f095ff51b35134c3209d49d21b77ce695c9554b0'
EXPECTED={40032354147:{120683434465:'check (ubuntu-22.04-arm, aarch64)',120683434778:'check (ubuntu-22.04, x86_64)',120683434836:'check (ubuntu-24.04, x86_64)',120683434871:'check (ubuntu-24.04-arm, aarch64)'},40032354233:{120683434904:'check',120683516790:'deploy'}}
inputs=[];checks=[]
def read(name):
 p=BASE/name;b=p.read_bytes();inputs.append({'path':str(p),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()});v=json.loads(b)
 if v.get('isError'):raise ValueError('connector error: '+name)
 d=v.get('structuredContent',v)
 return json.loads(d['content']) if isinstance(d.get('content'),str) else d
def check(name,value):checks.append({'name':name,'pass':bool(value)})
runs=[]
for run_id,wanted in EXPECTED.items():
 run=read(f'run-{run_id}-final.connector.json');jobs=read(f'jobs-{run_id}-final.connector.json')['jobs']
 check(f'{run_id}:exact-head-push-attempt1',run['id']==run_id and run['head_sha']==HEAD and run['event']=='push' and run['run_attempt']==1)
 check(f'{run_id}:terminal-success',run['status']=='completed' and run['conclusion']=='success')
 check(f'{run_id}:exact-job-matrix',{j['id']:j['name'] for j in jobs}==wanted)
 check(f'{run_id}:all-jobs-success',all(j['run_id']==run_id and j['status']=='completed' and j['conclusion']=='success' for j in jobs))
 check(f'{run_id}:all-recorded-steps-success',all(s['status']=='completed' and s['conclusion']=='success' for j in jobs for s in j['steps']))
 runs.append({'id':run_id,'name':run['name'],'head_sha':run['head_sha'],'event':run['event'],'attempt':run['run_attempt'],'status':run['status'],'conclusion':run['conclusion'],'url':run['html_url'],'created_at':run['created_at'],'updated_at':run['updated_at'],'jobs':[{'id':j['id'],'name':j['name'],'status':j['status'],'conclusion':j['conclusion'],'steps':j['steps']} for j in jobs]})
check('official-final-bytes-stable',all(Path(x['path']).stat().st_size==x['bytes'] and hashlib.sha256(Path(x['path']).read_bytes()).hexdigest()==x['sha256'] for x in inputs))
report={'schema_version':1,'recorded_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'result':'PASS_STATUS_ONLY' if all(x['pass'] for x in checks) else 'FAIL_STATUS_ONLY','head':HEAD,'runs':runs,'checks':checks,'official_final_inputs':inputs,'parser':{'path':str(Path(__file__).resolve()),'bytes':Path(__file__).stat().st_size,'sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},'scope':['Read-only official GitHub API status verification for this exact content commit, not a new software-result or QEMU audit.','No complete job logs fetched, no626 count recomputed; detailed software counts remain attributed to the prior531e0c3 review.','The four platform labels are those of actual returned jobs. This status-only task does not independently inspect this run uname/tool versions or per-child ELF.','Pages check/deploy success is recorded. Root separately verifies live bytes/browser; deployment status alone does not establish the exact live response bytes.','No CI rerun/trigger, repository write, Cargo, product execution, or QEMU.']}
p=BASE/'status-review.json';p.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'path':str(p),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'result':report['result'],'runs':[{'id':r['id'],'conclusion':r['conclusion'],'jobs':len(r['jobs'])} for r in runs]}))
