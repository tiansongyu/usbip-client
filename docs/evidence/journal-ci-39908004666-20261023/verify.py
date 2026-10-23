from pathlib import Path
import re,json,hashlib,subprocess
base=Path(__file__).parent
run=json.loads((base/'run.json').read_text());jobs=json.loads((base/'jobs.json').read_text())['jobs'];head='933e720531fd96b7a23616ed687d59b816e0ce09'
checks=[]
def check(name,value):
 checks.append({'name':name,'passed':bool(value)})
 if not value:raise AssertionError(name)
check('run complete on exact tested push',run['id']==39908004666 and run['head_sha']==head and run['event']=='push' and run['status']=='completed' and run['conclusion']=='success')
check('four native jobs',len(jobs)==4 and {j['name'] for j in jobs}=={'check (ubuntu-22.04, x86_64)','check (ubuntu-24.04, x86_64)','check (ubuntu-22.04-arm, aarch64)','check (ubuntu-24.04-arm, aarch64)'})
blobs={};reports=[]
for job in jobs:
 prefix=job['name'];raw=(base/f"job-{job['id']}.log").read_text();lines=[re.sub(r'^\ufeff?\d{4}-\d\d-\d\dT[0-9:.]+Z ','',l) for l in raw.splitlines()];text='\n'.join(lines)
 check(prefix+' completed success',job['status']=='completed' and job['conclusion']=='success' and all(s['conclusion']=='success' for s in job['steps']))
 test_results=re.findall(r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored;',text)
 check(prefix+' 250 tests in 31 groups, none failed or ignored',len(test_results)==31 and sum(int(r[0]) for r in test_results)==250 and all(r[1:]==('0','0') for r in test_results))
 check(prefix+' no error marker','##[error]' not in raw and 'test result: FAILED' not in raw)
 objects=[]
 for match in re.finditer(r'(?m)^\{',text):
  try:value,_=json.JSONDecoder().raw_decode(text[match.start():])
  except json.JSONDecodeError:continue
  if isinstance(value,dict):objects.append(value)
 back=[o for o in objects if len(o.get('checks',[]))==2 and o.get('result')=='PASS'];signals=[o for o in objects if len(o.get('cases',[]))==4]
 check(prefix+' exact backpressure and signal summaries',len(back)==len(signals)==1)
 back=back[0];signal=signals[0]
 check(prefix+' two unprivileged undrained backpressure cases',back['host_uid']!=0 and {r['mode'] for r in back['checks']}=={'sigint','deadline'} and all(r['stdout_capacity']==4096 and r['stdout_drained_after_fill'] is False and r['lease_socket_closed'] is True and r['exit']==(0 if r['mode']=='sigint' else 1) for r in back['checks']))
 expected_arch='aarch64' if 'aarch64' in prefix else 'x86_64'
 check(prefix+' native rust host and actual architecture',signal['host'][-1]==expected_arch and f'host: {expected_arch}-unknown-linux-gnu' in text and signal['host_uid']!=0)
 expected_os='22.04' if '22.04' in prefix else '24.04'
 check(prefix+' actual Ubuntu version',f'VERSION_ID="{expected_os}"' in text)
 check(prefix+' four signal results and source/ELF guards',signal['result']=='PASS' and signal['source_unchanged'] is True and signal['binary_unchanged'] is True and signal['source_before']['head']==head and signal['source_before']['dirty'] is False and signal['source_after']['dirty'] is False and {(c['mode'],c['signal']) for c in signal['cases']}=={(m,s) for m in ['ready','blocked'] for s in ['sigint','sigterm']} and all(c['result']=='PASS' and c['final_returncode']==0 and c['forced_cleanup'] is False and all(a['passed'] is True for a in c['assertions']) for c in signal['cases']))
 for entry in signal['source_before']['files']:
  path=entry['path']
  if path not in blobs:blobs[path]=subprocess.check_output(['git','show',head+':'+path])
  check(prefix+' exact signal input '+path,len(blobs[path])==entry['length'] and hashlib.sha256(blobs[path]).hexdigest()==entry['sha256'])
 reports.append({'job_id':job['id'],'name':prefix,'tests':250,'test_groups':31,'backpressure':back,'signal_case_count':4,'signal_assertion_count':sum(len(c['assertions']) for c in signal['cases']),'source_file_count':len(signal['source_before']['files']),'architecture':expected_arch,'ubuntu':expected_os})
report={'schema_version':1,'result':'PASS','commit':head,'run_id':run['id'],'url':run['html_url'],'checks':checks,'jobs':reports,'scope':'GitHub connector decoded job logs saved as UTF-8, plus official REST run/jobs. CI software and local virtual signals only; no QEMU/physical hardware. Per-case artifact files were not uploaded by this workflow; their printed hashes are not reconstructed raw artifacts.'}
(base/'verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'result':'PASS','checks':len(checks),'jobs':len(reports),'tests_per_job':250}))
