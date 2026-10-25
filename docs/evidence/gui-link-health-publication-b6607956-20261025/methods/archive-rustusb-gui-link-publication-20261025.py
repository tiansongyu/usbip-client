import pathlib,json,shutil,hashlib
root=pathlib.Path('/home/ubuntu/github/usbip-client');source=pathlib.Path('/tmp/rustusb-gui-link-publication-20261025');dest=root/'docs/evidence/gui-link-health-publication-b6607956-20261025'
head='b6607956905964a8b5e86ba4f73bab3b814f05ad';runs=json.loads((source/'runs-final.json').read_text())['structuredContent']['content'];rows=json.loads(runs)['workflow_runs'];assert len(rows)==3
byname={r['name']:r for r in rows};facts=[]
for name,expected in [('RustUSB software checks',4),('RustUSB native GUI checks',4),('Project website',2)]:
 row=byname[name];assert row['head_sha']==head and row['status']=='completed'and row['conclusion']=='success'
 jobs=json.loads((source/f'jobs-final-{row["id"]}.json').read_text())['structuredContent']['jobs'];assert len(jobs)==expected and all(j['status']=='completed'and j['conclusion']=='success'for j in jobs)
 if expected==4:
  assert {j['name']for j in jobs}=={'check (ubuntu-22.04, x86_64)','check (ubuntu-24.04, x86_64)','check (ubuntu-22.04-arm, aarch64)','check (ubuntu-24.04-arm, aarch64)'}
  for j in jobs:
   steps=j['steps'];required=['Verify architecture and actual GTK/WebKit versions','GUI formatting, Clippy, tests, release and rustdoc']if name=='RustUSB native GUI checks'else['Verify native architecture and record the software environment','Formatting, lint, tests, release and documentation']
   for needle in required:assert any(s['name']==needle and s['status']=='completed'and s['conclusion']=='success'for s in steps)
 facts.append({'name':name,'id':row['id'],'url':row['html_url'],'head_sha':row['head_sha'],'status':row['status'],'conclusion':row['conclusion'],'jobs':[{'id':j['id'],'name':j['name'],'status':j['status'],'conclusion':j['conclusion']}for j in jobs]})
http=json.loads((source/'http-and-source.json').read_text());assert http['result']=='PASS'and http['content_head']==head and len(http['http'])==7
assert all(x['status']==200 and x['exact_git_bytes']for x in http['http'])
dest.mkdir(parents=True,exist_ok=False)
for p in source.rglob('*'):
 if p.is_file():target=dest/'originals'/p.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
for n in ['archive-rustusb-gui-link-publication-20261025.py','check-rustusb-gui-link-publication-20261025.py']:target=dest/'methods'/n;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(pathlib.Path('/tmp')/n,target)
files=[p for p in dest.rglob('*')if p.is_file()];manifest=[{'path':str(p.relative_to(dest)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in sorted(files)]
for x in manifest:assert hashlib.sha256((dest/x['path']).read_bytes()).hexdigest()==x['sha256']
receipt={'result':'PASS','content_head':head,'workflows':facts,'https':http['http'],'files':len(files),'bytes':sum(x['bytes']for x in manifest),'preserved_non_readme_inputs':http['frozen_non_readme_inputs_preserved'],'preserved_global_skill_files':http['global_skill_files'],'scope':'Exact pushed content, original workflow/job step states and HTTPS bodies. Not downloaded CI logs/ELFs, not a new per-platform test recount, native USB or hardware acceptance.'}
(dest/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');(dest/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'result':'PASS','files':len(files),'bytes':receipt['bytes'],'runs':[r['id']for r in facts]}))
