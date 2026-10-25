from pathlib import Path
import json,hashlib,shutil,subprocess,datetime
root=Path('/home/ubuntu/github/usbip-client');head='1ed00832943789564bdac5b472391a4226f29876';source=Path('/tmp/rustusb-automatic-rust-publication-1ed00832-20261025');dest=root/'docs/evidence/automatic-sharing-rust-publication-1ed00832-20261025'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def git(*args):return subprocess.check_output(['git',*args],cwd=root).decode().strip()
runs=json.loads(json.loads((source/'runs-final.json').read_text())['structuredContent']['content'])['workflow_runs'];expected={40162668994:'RustUSB software checks',40162669093:'RustUSB native GUI checks',40162669053:'Project website'}
assert {r['id']for r in runs}==set(expected)
for r in runs:assert r['head_sha']==head and r['name']==expected[r['id']]and r['status']=='completed'and r['conclusion']=='success'
matrices={}
for run_id,name in expected.items():
 jobs=json.loads((source/('jobs-'+str(run_id)+'-final.json')).read_text())['structuredContent']['jobs'];assert len(jobs)==(2 if run_id==40162669053 else 4)
 for job in jobs:
  assert job['status']=='completed'and job['conclusion']=='success'
  steps={s['name']:s for s in job['steps']}
  needed=['Verify native architecture and record the software environment','Formatting, lint, tests, release and documentation']if run_id==40162668994 else ['Verify architecture and actual GTK/WebKit versions','GUI formatting, Clippy, tests, release and rustdoc']if run_id==40162669093 else ['Check site assets and documentation links']if job['name']=='check'else ['Deploy website']
  for n in needed:assert steps[n]['status']=='completed'and steps[n]['conclusion']=='success',(name,job['id'],n)
 if run_id!=40162669053:assert {j['name']for j in jobs}=={'check (ubuntu-22.04, x86_64)','check (ubuntu-24.04, x86_64)','check (ubuntu-22.04-arm, aarch64)','check (ubuntu-24.04-arm, aarch64)'}
 matrices[str(run_id)]={'name':name,'jobs':[{'id':j['id'],'name':j['name'],'conclusion':j['conclusion']}for j in jobs]}
http=json.loads((source/'http-and-source.json').read_text());assert http['result']=='PASS'and http['content_head']==head
assert git('rev-parse','HEAD')==head and not git('status','--porcelain')and git('ls-remote','origin','refs/heads/main').split()[0]==head
shutil.copyfile('/tmp/check-rustusb-automatic-rust-publication-20261025.py',source/'check-http-source.py');shutil.copyfile(__file__,source/'collect-publication.py')
receipt={'result':'PASS','content_head':head,'local_gate_qemu_head':http['local_gate_head'],'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'workflows':matrices,'https_exact_resources':len(http['http']),'source_inputs_preserved':http['frozen_inputs_preserved'],'site_files':http['site_files'],'global_skill_files':http['global_skill_files'],'binaries':http['binaries'],'scope':'Original GitHub workflow/job API status, actual architecture and full gate step success; seven canonical HTTPS/Git exact bodies. No downloaded CI logs/ELFs or per-platform test recount. Final receipt-only commit does not become the tested source.'}
(source/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');dest.mkdir(exist_ok=False)
for p in source.rglob('*'):
 if p.is_file():
  d=dest/p.relative_to(source);d.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,d);assert p.read_bytes()==d.read_bytes()
manifest={'files':[]}
for p in sorted(dest.rglob('*')):
 if p.is_file():manifest['files'].append({'path':str(p.relative_to(dest)),'bytes':p.stat().st_size,'sha256':sha(p)})
(dest/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps({'files':len(manifest['files']),'bytes':sum(m['bytes']for m in manifest['files']),'receipt':receipt}))
