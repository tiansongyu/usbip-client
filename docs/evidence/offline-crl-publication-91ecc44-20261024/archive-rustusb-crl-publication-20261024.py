import hashlib,json,pathlib,shutil,subprocess
repo=pathlib.Path('/home/ubuntu/github/usbip-client');source=pathlib.Path('/tmp/rustusb-crl-publication-91ecc44-20261024');target=repo/'docs/evidence/offline-crl-publication-91ecc44-20261024';head='91ecc44fe458de40ff806b80d7a73b7e24440a64'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def data(p):return read(p)['structuredContent']
runs=json.loads(data(source/'runs-final.json')['content'])['workflow_runs'];assert len(runs)==2 and all(r['head_sha']==head and r['status']=='completed' and r['conclusion']=='success' for r in runs)
software=data(source/'jobs-40121403621-final.json')['jobs'];pages=data(source/'jobs-40121403729-final.json')['jobs'];assert len(software)==4 and {j['name'] for j in software}=={'check (ubuntu-22.04, x86_64)','check (ubuntu-24.04, x86_64)','check (ubuntu-22.04-arm, aarch64)','check (ubuntu-24.04-arm, aarch64)'}
assert all(j['status']=='completed' and j['conclusion']=='success' for j in software+pages) and {j['name'] for j in pages}=={'check','deploy'}
http=read(source/'http-and-source.json');assert len(http['http_resources'])==6 and all(r['http_status']==200 and r['exact_git_bytes'] for r in http['http_resources'])
for r in http['http_resources']:
 name=r['git_path'].removeprefix('website/');p=source/'http'/name;assert sha(p)==r['sha256'] and p.read_bytes()==subprocess.check_output(['git','show',head+':'+r['git_path']],cwd=repo)
push=pathlib.Path('/tmp/rustusb-crl-content-push-20261024');assert read(push/'receipt.json')['verified'] and read(push/'receipt.json')['head']==head
target.mkdir(exist_ok=False);entries=[]
for root,label in [(source,''),(push,'content-push')]:
 for p in sorted(root.rglob('*')):
  if p.is_file():
   name=pathlib.Path(label)/p.relative_to(root);dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest);entries.append({'path':str(name),'original':str(p),'bytes':p.stat().st_size,'sha256':sha(p)})
for p in [pathlib.Path(__file__),pathlib.Path('/tmp/check-rustusb-crl-publication-20261024.py')]:
 shutil.copyfile(p,target/p.name);entries.append({'path':p.name,'original':str(p),'bytes':p.stat().st_size,'sha256':sha(p)})
for row in entries:assert sha(target/row['path'])==row['sha256'] and sha(pathlib.Path(row['original']))==row['sha256']
review={'result':'PASS','content_head':head,'software_run':40121403621,'software_jobs':[{'id':j['id'],'name':j['name'],'status':j['status'],'conclusion':j['conclusion'],'reported_steps':j.get('steps')} for j in software],'pages_run':40121403729,'pages_jobs':[{'id':j['id'],'name':j['name'],'status':j['status'],'conclusion':j['conclusion']} for j in pages],'exact_live_http_resources':6,'software_source_head':'fcb487cf242d968ef5a75f375b91f8e2ad82897c','final_qemu_source_head':'e441bb89086b13a95ffb0964ee46d35c7ed2ee32','unchanged_inputs':http['unchanged_inputs'],'scope':'Primary run/job conclusions only. Missing software step details are not invented; full logs/per-platform executed ELF/test recount not fetched. No new browser/pixel/USB run. All seven site/four global files unchanged. Complete G3/hardware/Phase4-7 not achieved.'}
(target/'review.json').write_text(json.dumps(review,indent=2)+'\n');(target/'manifest.json').write_text(json.dumps({'content_head':head,'files':entries,'scope':review['scope']},indent=2)+'\n');print(json.dumps({'files':len(entries),'bytes':sum(r['bytes'] for r in entries),'review':review}))
