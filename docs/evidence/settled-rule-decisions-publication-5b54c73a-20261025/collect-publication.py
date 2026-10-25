from pathlib import Path
import json,hashlib,subprocess,re,shutil,datetime
root=Path('/home/ubuntu/github/usbip-client');work=Path('/tmp/rustusb-rule-decisions-publication-5b54c73a-20261025');head='5b54c73a717106f90af0f417acefcf43599bfb0a'
def git(*args):return subprocess.check_output(['git',*args],cwd=root)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert git('rev-parse','HEAD').decode().strip()==head and not git('status','--porcelain').strip()
source=json.loads((work/'http-and-source.json').read_text());assert source['result']=='PASS'and source['content_head']==head
run_files=sorted(work.glob('runs-*.json'),key=lambda p:int(re.search(r'-(\d+)\.json$',p.name).group(1)));runs=json.loads(json.loads(run_files[-1].read_text())['structuredContent']['content'])['workflow_runs'];assert len(runs)==3
expected={'RustUSB software checks':(4,['Verify native architecture and record the software environment','Formatting, lint, tests, release and documentation']),'RustUSB native GUI checks':(4,['Verify architecture and actual GTK/WebKit versions','GUI formatting, Clippy, tests, release and rustdoc']),'Project website':(2,[])}
ci=[]
for r in runs:
 assert r['head_sha']==head and r['status']=='completed'and r['conclusion']=='success',r['id']
 count,steps=expected[r['name']]
 files=sorted(work.glob('jobs-'+str(r['id'])+'-*.json'),key=lambda p:int(re.search(r'-(\d+)\.json$',p.name).group(1)));f=files[-1];jobs=json.loads(f.read_text())['structuredContent']['jobs'];assert len(jobs)==count
 for j in jobs:
  assert j['run_id']==r['id']and j['status']=='completed'and j['conclusion']=='success',j['id']
  selected=steps if steps else ['Deploy website']if j['name']=='deploy'else ['Check site assets and documentation links']
  for name in selected:
   matched=[t for t in j['steps']if t['name']==name];assert len(matched)==1 and matched[0]['status']=='completed'and matched[0]['conclusion']=='success',(j['id'],name)
 ci.append({'name':r['name'],'run_id':r['id'],'head_sha':r['head_sha'],'status':r['status'],'conclusion':r['conclusion'],'run_response':run_files[-1].name,'jobs_response':f.name,'jobs':[{'id':j['id'],'name':j['name'],'status':j['status'],'conclusion':j['conclusion'],'steps':j['steps']}for j in jobs]})
(work/'check-publication.py').write_bytes(Path('/tmp/check-rustusb-rule-decisions-publication-20261025.py').read_bytes());(work/'collect-publication.py').write_bytes(Path(__file__).read_bytes())
receipt={'result':'PASS','content_head':head,'local_gate_and_qemu_head':'650c53a8dbf14d8746006cb7a22f0dbfb3bcc19f','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'ci':ci,'http_and_source':source,'scope':'Actual connector job/run conclusions and named gate/architecture steps, plus canonical HTTPS/Git byte equality. No CI logs, executed CI ELF sampling or per-platform test recount. The later receipt-only commit is not this tested content head.'}
(work/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
dest=root/'docs/evidence/settled-rule-decisions-publication-5b54c73a-20261025';assert not dest.exists();shutil.copytree(work,dest)
manifest={'files':[]}
for p in sorted(dest.rglob('*')):
 if p.is_file():manifest['files'].append({'path':str(p.relative_to(dest)),'bytes':p.stat().st_size,'sha256':sha(p)})
(dest/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'result':'PASS','runs':[x['run_id']for x in ci],'jobs':sum(len(x['jobs'])for x in ci),'files':len(manifest['files']),'bytes':sum(x['bytes']for x in manifest['files']),'destination':str(dest)}))
