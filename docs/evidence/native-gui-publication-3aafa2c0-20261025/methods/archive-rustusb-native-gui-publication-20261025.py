import pathlib,json,hashlib,shutil,datetime
root=pathlib.Path('/home/ubuntu/github/usbip-client');base=pathlib.Path('/tmp/rustusb-native-gui-publication-final-20261025');out=root/'docs/evidence/native-gui-publication-3aafa2c0-20261025';out.mkdir(parents=True,exist_ok=False)
raw=json.loads((base/'runs-complete.json').read_text());runs=json.loads(raw['structuredContent']['content'])['workflow_runs'];assert len(runs)==3
expected={'RustUSB software checks':40142928056,'RustUSB native GUI checks':40142928072,'Project website':40142928094}
state=[]
for r in runs:
 assert r['head_sha']=='3aafa2c0af534f8bf2352d11dfdf6845259af246' and r['status']=='completed'and r['conclusion']=='success'and expected[r['name']]==r['id']
 jobs=json.loads((base/f'jobs-complete-{r["id"]}.json').read_text())['structuredContent']['jobs']
 assert len(jobs)==(2 if r['name']=='Project website'else 4)
 for j in jobs:
  assert j['run_id']==r['id']and j['status']=='completed'and j['conclusion']=='success'
  if r['name']=='RustUSB software checks':required=['Verify native architecture and record the software environment','Formatting, lint, tests, release and documentation','Unprivileged server signal lifecycle (four cases)','Private diagnostic export lifecycle (nine cases)']
  elif r['name']=='RustUSB native GUI checks':required=['Install the pinned Rust toolchain and native WebView prerequisites','Verify architecture and actual GTK/WebKit versions','GUI formatting, Clippy, tests, release and rustdoc']
  else:required=['Deploy website']if j['name']=='deploy'else['Check site assets and documentation links']
  for name in required:assert any(s['name']==name and s['status']=='completed'and s['conclusion']=='success'for s in j['steps']),name
 state.append({'id':r['id'],'name':r['name'],'head':r['head_sha'],'status':r['status'],'conclusion':r['conclusion'],'jobs':[{'id':j['id'],'name':j['name'],'status':j['status'],'conclusion':j['conclusion']}for j in jobs]})
http=json.loads((base/'http-and-source.json').read_text());assert http['result']=='PASS'and len(http['http'])==7
files=[]
for label,source in [('final',base),('earlier-77634c48',pathlib.Path('/tmp/rustusb-native-gui-publication-77634c48-20261025'))]:
 for p in sorted(source.rglob('*')):
  if p.is_file():
   n='originals/'+label+'/'+str(p.relative_to(source));dest=out/n;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest);assert dest.read_bytes()==p.read_bytes();files.append({'path':n,'bytes':dest.stat().st_size,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()})
for name in ['archive-rustusb-native-gui-publication-20261025.py','check-rustusb-native-gui-publication-20261025.py','check-rustusb-native-gui-final-publication-20261025.py']:
 p=pathlib.Path('/tmp')/name;dest=out/'methods'/name;dest.parent.mkdir(exist_ok=True);shutil.copyfile(p,dest);files.append({'path':str(dest.relative_to(out)),'bytes':dest.stat().st_size,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()})
(out/'manifest.json').write_text(json.dumps(sorted(files,key=lambda x:x['path']),indent=2)+'\n')
receipt={'result':'PASS','content_head':'3aafa2c0af534f8bf2352d11dfdf6845259af246','runs':state,'canonical_http_resources':7,'core_inputs_preserved':222,'gui_non_readme_inputs_preserved':138,'site_files':8,'global_skill_files':4,'files':len(files),'bytes':sum(f['bytes']for f in files),'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'Primary API completed/success and explicit step states, actual canonical HTTPS/Git bytes, normal SSH push and unchanged code/ELF/global skill. CI logs/ELF and per-platform test counts were not fetched/recounted. Not a new native USB/security/hardware acceptance.'}
(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print({'result':receipt['result'],'files':receipt['files'],'bytes':receipt['bytes']})
