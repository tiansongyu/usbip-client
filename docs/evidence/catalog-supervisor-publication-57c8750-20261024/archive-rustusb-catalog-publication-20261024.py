import hashlib,json,pathlib,shutil,subprocess
repo=pathlib.Path('/home/ubuntu/github/usbip-client')
source=pathlib.Path('/tmp/rustusb-catalog-publication-57c8750-20261024')
target=repo/'docs/evidence/catalog-supervisor-publication-57c8750-20261024'
head='57c8750e4bdcced2a0144882cc6f94d353a34852'
def load(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def structured(path):return load(path)['structuredContent']
run_data=structured(source/'runs-final.json');runs=json.loads(run_data['content'])['workflow_runs']
assert len(runs)==2 and all(r['head_sha']==head and r['status']=='completed' and r['conclusion']=='success' for r in runs)
software=structured(source/'jobs-40105141484-final.json')['jobs']
pages=structured(source/'jobs-40105141472-final.json')['jobs']
assert len(software)==4 and {j['name'] for j in software}=={'check (ubuntu-22.04, x86_64)','check (ubuntu-24.04, x86_64)','check (ubuntu-22.04-arm, aarch64)','check (ubuntu-24.04-arm, aarch64)'}
assert all(j['status']=='completed' and j['conclusion']=='success' for j in software+pages)
for job in software:
 for name in ['Verify native architecture and record the software environment','Formatting, lint, tests, release and documentation']:
  step=[s for s in job['steps'] if s['name']==name];assert len(step)==1 and step[0]['status']=='completed' and step[0]['conclusion']=='success'
assert {j['name'] for j in pages}=={'check','deploy'}
http=load(source/'http-and-source.json');assert len(http['http_resources'])==6 and all(r['http_status']==200 and r['exact_git_bytes'] for r in http['http_resources'])
for row in http['http_resources']:
 name=row['git_path'].removeprefix('website/');p=source/'http'/name
 assert sha(p)==row['sha256'] and p.read_bytes()==subprocess.check_output(['git','show',head+':'+row['git_path']],cwd=repo)
push=load(pathlib.Path('/tmp/rustusb-catalog-content-push-20261024/receipt.json'));assert push['verified'] and push['head']==head
target.mkdir(exist_ok=False)
entries=[]
for base,label in [(source,''),(pathlib.Path('/tmp/rustusb-catalog-content-push-20261024'),'content-push')]:
 for path in sorted(base.rglob('*')):
  if path.is_file():
   name=pathlib.Path(label)/path.relative_to(base);dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dest)
   entries.append({'path':str(name),'original':str(path),'bytes':path.stat().st_size,'sha256':sha(path)})
method=pathlib.Path(__file__);shutil.copyfile(method,target/method.name);entries.append({'path':method.name,'original':str(method),'bytes':method.stat().st_size,'sha256':sha(method)})
review={'result':'PASS','content_head':head,'software_run':40105141484,'software_jobs':[{'id':j['id'],'name':j['name'],'status':j['status'],'conclusion':j['conclusion']} for j in software],'pages_run':40105141472,'pages_jobs':[{'id':j['id'],'name':j['name'],'status':j['status'],'conclusion':j['conclusion']} for j in pages],'exact_live_http_resources':6,'local_software_and_qemu_source_head':'b6756265a4ae57ab8058b1f65133f2397af3f063','unchanged_inputs':http['unchanged_inputs'],'scope':'Primary run/job/step API only; full CI logs and per-platform executed ELF/test recount not fetched. Six HTTP bodies equal content Git bytes; no new browser execution. Complete G3/hardware/Phase4-7 not achieved.'}
(target/'review.json').write_text(json.dumps(review,indent=2)+'\n')
for entry in entries:
 assert sha(target/entry['path'])==entry['sha256'] and sha(pathlib.Path(entry['original']))==entry['sha256']
(target/'manifest.json').write_text(json.dumps({'content_head':head,'files':entries,'scope':review['scope']},indent=2)+'\n')
print(json.dumps({'files':len(entries),'bytes':sum(e['bytes'] for e in entries),'review':review}))
