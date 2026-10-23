import hashlib,json,pathlib,re,subprocess,zipfile
root=pathlib.Path('/home/ubuntu/github/usbip-client');base=pathlib.Path('/tmp');head='eaa19366486b8b99412efbfce1f13d49f73fe0cc'
jobs=json.loads((base/'rustusb-agent-ci-jobs-connector-eaa1936-20261023.json').read_text())['structuredContent']['jobs'];checks=[];reports=[]
def require(value,name):
 if not value:raise AssertionError(name)
 checks.append(name)
with zipfile.ZipFile(base/'rustusb-agent-ci-eaa1936-20261023.zip') as archive:
 require(len(jobs)==4,'four native jobs')
 for job in jobs:
  require(job['status']=='completed' and job['conclusion']=='success',job['name']+' completed success')
  name=next(n for n in archive.namelist() if '/' not in n and n.endswith(job['name']+'.txt'))
  raw=archive.read(name);decoded=json.loads((base/f"rustusb-agent-ci-job-{job['id']}-connector-20261023.json").read_text())['structuredContent']['content']
  require(raw.decode()==decoded,job['name']+' connector logs equal official ZIP bytes decoded')
  require(head in decoded,job['name']+' exact checkout HEAD in original log')
  directory=job['name']+'/'
  step=archive.read(next(n for n in archive.namelist() if n.startswith(directory+'5_'))).decode()
  results=[tuple(map(int,m)) for m in re.findall(r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored;',step)]
  require(len(results)==39 and sum(m[0] for m in results)==495 and all(m[1:]==(0,0) for m in results),job['name']+' 495 tests/39 results/zero failed ignored')
  for number in [3,4,6,7,8]:require(any(s['number']==number and s['conclusion']=='success' for s in job['steps']),job['name']+f' step {number} success')
  local=archive.read(next(n for n in archive.namelist() if n.startswith(directory+'7_'))).decode()
  require(all(f'"mode": "{mode}"' in local for mode in ['sigint','deadline']),job['name']+' both backpressure scenarios')
  signal=archive.read(next(n for n in archive.namelist() if n.startswith(directory+'8_'))).decode()
  require(all(case+': PASS' in signal for case in ['ready/sigint','ready/sigterm','blocked/sigint','blocked/sigterm']),job['name']+' four signal scenarios')
  reports.append({'job_id':job['id'],'name':job['name'],'flat_zip_entry':name,'log_bytes':len(raw),'log_sha256':hashlib.sha256(raw).hexdigest(),'test_executions':495,'result_lines':39})
after=json.loads((base/'rustusb-application-software-second-20261023/inputs-after.json').read_text())
for item in after:
 blob=subprocess.check_output(['git','show',head+':'+item['path']],cwd=root);require(hashlib.sha256(blob).hexdigest()==item['sha256'],'software source equals CI Git blob '+item['path'])
out={'scope':'independent CI original-log audit; not USB/VM/hardware/performance test count','run_id':39979747581,'head':head,'result':'PASS','checks':checks,'check_count':len(checks),'jobs':reports,'software_selected_inputs_verified':len(after)}
(base/'rustusb-agent-ci-audit-eaa1936-20261023.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({k:out[k] for k in ['run_id','result','check_count','software_selected_inputs_verified']}))
