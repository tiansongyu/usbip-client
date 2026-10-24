import pathlib,json,hashlib,subprocess,shutil
repo=pathlib.Path('/home/ubuntu/github/usbip-client');source=pathlib.Path('/tmp/rustusb-tui-settings-publication-627e571-20261024');target=repo/'docs/evidence/tui-settings-publication-627e571-20261024';head='627e5712b1de2d88797e2347862cc38d352e4b97'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def structured(p):return read(p)['structuredContent']
runs=json.loads(structured(source/'runs-final.json')['content'])['workflow_runs'];assert len(runs)==2 and all(r['head_sha']==head and r['status']=='completed'and r['conclusion']=='success'for r in runs)
software=structured(source/'jobs-40130778364-final.json')['jobs'];pages=structured(source/'jobs-40130778339-final.json')['jobs']
assert len(software)==4 and {j['name']for j in software}=={'check (ubuntu-22.04, x86_64)','check (ubuntu-24.04, x86_64)','check (ubuntu-22.04-arm, aarch64)','check (ubuntu-24.04-arm, aarch64)'}
assert len(pages)==2 and {j['name']for j in pages}=={'check','deploy'}
for j in software+pages:assert j['status']=='completed'and j['conclusion']=='success'
# Inspect only actual reported steps; do not invent full logs or per-platform test recounts.
for j in software:
 steps=j.get('steps');assert steps
 for name in ['Verify native architecture and record the software environment','Formatting, lint, tests, release and documentation','Unprivileged server signal lifecycle (four cases)','Unprivileged virtual-server lifecycle smoke (all-features binary)','Private diagnostic export lifecycle (nine cases)']:
  matches=[s for s in steps if s['name']==name];assert len(matches)==1 and matches[0]['status']=='completed'and matches[0]['conclusion']=='success'
http=read(source/'http-and-source.json');assert http['result']=='PASS'and len(http['http_resources'])==6 and all(r['status']==200 and r['exact_git_bytes']for r in http['http_resources'])
for r in http['http_resources']:
 p=source/'http'/r['git_path'].removeprefix('website/');assert sha(p)==r['sha256']and p.read_bytes()==subprocess.check_output(['git','show',head+':'+r['git_path']],cwd=repo)
push=pathlib.Path('/tmp/rustusb-tui-settings-content-push-20261024');assert read(push/'receipt.json')['verified']and read(push/'receipt.json')['head']==head
assert not subprocess.check_output(['git','status','--porcelain'],cwd=repo).strip()
target.mkdir(exist_ok=False);entries=[]
for root,label in [(source,''),(push,'content-push')]:
 for p in sorted(root.rglob('*')):
  if p.is_file():
   name=pathlib.Path(label)/p.relative_to(root);dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest);entries.append({'path':str(name),'original':str(p),'bytes':p.stat().st_size,'sha256':sha(p)})
for p in [pathlib.Path(__file__),pathlib.Path('/tmp/check-rustusb-tui-settings-publication-20261024.py')]:
 shutil.copyfile(p,target/p.name);entries.append({'path':p.name,'original':str(p),'bytes':p.stat().st_size,'sha256':sha(p)})
for row in entries:assert sha(target/row['path'])==row['sha256']and sha(pathlib.Path(row['original']))==row['sha256']
review={'result':'PASS','content_head':head,'software_run':40130778364,'software_jobs':[{'id':j['id'],'name':j['name'],'status':j['status'],'conclusion':j['conclusion'],'reported_steps':j.get('steps')}for j in software],'pages_run':40130778339,'pages_jobs':[{'id':j['id'],'name':j['name'],'status':j['status'],'conclusion':j['conclusion']}for j in pages],'http_resources':6,'inputs':http['unchanged_inputs'],'scope':'Exact primary run/job/reported-step outcomes and canonical HTTP bytes. No full CI logs/ELF/per-platform test recount fetched. Local 950 and fourQEMU/58PTY/8guest retain their exact5584 source. Website/skill unchanged, no new browser/pixel claim. Goal/complete G4/G3/hardware remain unfinished.'}
(target/'review.json').write_text(json.dumps(review,indent=2)+'\n');(target/'manifest.json').write_text(json.dumps({'files':entries,'scope':review['scope']},indent=2)+'\n');print(json.dumps({'files':len(entries),'bytes':sum(x['bytes']for x in entries),'software_jobs':[j['id']for j in software],'result':'PASS'}))
