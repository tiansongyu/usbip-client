#!/usr/bin/env python3
"""Read four official saved CI responses only; no API/build/rerun/verifier calls."""
from pathlib import Path
import hashlib,json,re
BASE=Path('/tmp/rustusb-logging-ci-41fce5f-20261024');OUT=Path('/tmp/rustusb-logging-cache-precondition-fix-20261024');HEAD='41fce5ff60f6b548e9151ee2d4c6decdb2c99e7d';RUN=40052402641
ERROR='private HOME/.cache parent must already exist'
def digest(p):
 b=p.read_bytes();return {'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
originals={p.name:digest(p) for p in sorted(BASE.iterdir()) if p.is_file()}
checks=[]
def ck(name,v):checks.append({'assertion':name,'pass':bool(v)})
jobs=json.loads((BASE/'jobs-initial.response.json').read_text())['structuredContent']['jobs'];runresponse=json.loads((BASE/'runs-initial.response.json').read_text());runs=json.loads(runresponse['structuredContent']['content'])['workflow_runs'];run=next(r for r in runs if r['id']==RUN)
ck('official initial run exact pushhead',run['head_sha']==HEAD and run['event']=='push')
reports=[]
def documents(lines):
 text='\n'.join(lines);offset=0;end_previous=0;decoder=json.JSONDecoder();result=[]
 for l in lines:
  pos=offset+len(l)-len(l.lstrip());offset+=len(l)+1
  if pos<end_previous or not l.lstrip().startswith('{'):continue
  try:d,end=decoder.raw_decode(text,pos)
  except ValueError:continue
  if isinstance(d,dict):result.append(d);end_previous=end
 return result
for job in jobs:
 ident=job['id'];p=BASE/f'job-{ident}.response.json';response=json.loads(p.read_text());content=response['structuredContent']['content'];raw=content.encode();decoded=OUT/f'ci-job-{ident}.raw.log';decoded.write_bytes(raw)
 lines=[re.sub(r'^\ufeff?\d{4}-\d{2}-\d{2}T[^ ]+Z ','',l) for l in content.splitlines()]
 clean=[re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',l) for l in lines];docs=documents(clean)
 rows=[]
 for l in clean:
  m=re.match(r'test result: (\S+)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;',l)
  if m:rows.append({'result':m[1],'passed':int(m[2]),'failed':int(m[3]),'ignored':int(m[4]),'measured':int(m[5]),'filtered':int(m[6])})
 software={'default':sum(r['passed'] for r in rows[:21]),'all_features':sum(r['passed'] for r in rows[21:42]),'vendor':sum(r['passed'] for r in rows[42:])}
 bp=[d for d in docs if 'checks'in d and d.get('scope')=='local CLI output backpressure; no physical USB or kernel changes']
 signals=[d for d in docs if d.get('scope')=='ordinary-user loopback virtual profiles only; no physical USB, modules, ptrace, or network configuration' and 'cases'in d]
 logging=[d for d in docs if d.get('scope')=='actual logging subprocesses and loopback virtual memory devices; no host USB/root/helper/kernel mutation' and 'cases'in d]
 ck(str(ident)+' actual completed job failure',job['status']=='completed' and job['conclusion']=='failure')
 ck(str(ident)+' actual723=308/395/20 and43zero-fail/ignored rows',len(rows)==43 and software=={'default':308,'all_features':395,'vendor':20} and all(r['result']=='ok' and not any(r[k] for k in ['failed','ignored','measured','filtered']) for r in rows))
 for name in ['Formatting, lint, tests, release and documentation','Unprivileged virtual-server lifecycle smoke (all-features binary)','Unprivileged server signal lifecycle (four cases)']:
  ck(str(ident)+' official prior step success '+name,any(s['name']==name and s['conclusion']=='success' for s in job['steps']))
 ck(str(ident)+' official only logging lifecycle step failed',[s['name'] for s in job['steps'] if s['conclusion']=='failure']==['Bounded process logging lifecycle (six cases)'])
 ck(str(ident)+' raw oldBP original2 passed',len(bp)==1 and bp[0]['result']=='PASS' and [(x['mode'],x['exit']) for x in bp[0]['checks']]==[('sigint',0),('deadline',1)] and all(x['stdout_capacity']==4096 and not x['stdout_drained_after_fill'] and x['lease_socket_closed'] for x in bp[0]['checks']))
 ck(str(ident)+' raw old4signals all passed',len(signals)==1 and signals[0]['result']=='PASS' and len(signals[0]['cases'])==4 and all(x['result']=='PASS' for x in signals[0]['cases']) and all(x+': PASS' in clean for x in ['ready/sigint','ready/sigterm','blocked/sigint','blocked/sigterm']))
 ck(str(ident)+' exactly one actual new logging summary',len(logging)==1)
 lg=logging[0]
 ck(str(ident)+' new logging remains preconditionFAIL with0cases0children',lg['result']=='FAIL' and lg['completed'] and not lg['cases'] and not lg['children'] and lg['error']['type']=='RuntimeError' and lg['error']['message']==ERROR and not lg['collection_errors'] and not lg['forced_cleanup'])
 ck(str(ident)+' exact original leading cache guard source trace',f'require(cache.is_dir() and not cache.is_symlink(), "{ERROR}")' in lg['error']['traceback'])
 ck(str(ident)+' failed precondition source and executable guards preserved',lg['source_unchanged'] and lg['binary_unchanged'] and lg['sources_before']==lg['sources_after'] and lg['binary_before']==lg['binary_after'] and lg['sources_before']['head']==HEAD)
 script=next(x for x in lg['sources_before']['files'] if x['path']=='scripts/test-process-logging.py')
 ck(str(ident)+' actual failed script is initialc876 snapshot',script['sha256']=='c876cf054b9b37d36df71ec3b2854e467755c45326dfe0c302b8ca965c177bc1' and script['bytes']==38064)
 errors=[l for l in clean if l.startswith('##[error]')];ck(str(ident)+' sole Actions error is logging step exit1',errors==['##[error]Process completed with exit code 1.'])
 if ident==120747703286:ck('root prior decoded raw exact official content', (BASE/'job-120747703286.response.decoded.log').read_bytes()==raw)
 reports.append({'job_id':ident,'name':job['name'],'status':job['status'],'conclusion':job['conclusion'],'official_step_outcomes':[{'name':s['name'],'conclusion':s['conclusion']} for s in job['steps']],'software':software,'test_executions':sum(r['passed'] for r in rows),'result_rows':len(rows),'fail_ignored':sum(r['failed']+r['ignored'] for r in rows),'legacy_signal_cases':4,'legacy_backpressure_cases':2,'new_logging_cases':0,'new_logging_children':0,'primary_failure':lg['error'],'actual_script':script,'platform_scope':lg['host'],'actualUID':lg['uid'],'recorded_release_ELF':lg['binary_before'],'response':{'path':str(p),**digest(p)},'decoded':{'path':str(decoded),**digest(decoded)}})
ck('four official platforms and expected job IDs',len(reports)==4 and {r['job_id'] for r in reports}=={120747703286,120747703493,120747703505,120747703514})
ck('all original API and preexisting decoded bytes unchanged',originals=={p.name:digest(p) for p in sorted(BASE.iterdir()) if p.is_file()})
result={'schema_version':1,'review_result':'PASS' if all(x['pass'] for x in checks) else 'FAIL','actual_CI_result':'FAIL, all four jobs; preserve original','run_id':RUN,'head':HEAD,'initial_run_api_status':run['status'],'scope':'Offline official response/log parse, not rerun. New six logging cases never started on these four jobs; software723 and prior4signal/2BP passed per job. No hardware/QEMU evidence. Reviewer authored loggingrecord/worker and now one-line-call+privatecache harness fix; statistics method independent, not fully independent implementation review.','jobs':reports,'checks':checks,'limits':['Only saved full logs/API were examined; ephemeral runner /tmp artifacts not independently available or invented.','Old BP has no extra per-child /proc ELF witness; source/binary guard workflow association is separate.','Four prior CASE runs/localcleanf3 remain their own evidence; new script fixtures are not a replacement CI success.','No new CI or API call triggered. Saved initial run API was in_progress; four saved jobs and logs are terminal failure.'],'original_artifacts':originals}
p=OUT/'ci-four-failures-review.json';p.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'review_result':result['review_result'],'actual_CI_result':result['actual_CI_result'],'assertions':len(checks),'failures':[x for x in checks if not x['pass']],'jobs':[(r['name'],r['test_executions'],r['new_logging_cases']) for r in reports],'path':str(p),'integrity':digest(p)},indent=2))
