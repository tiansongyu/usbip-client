"""Finalize metadata-only CI/public HTTP publication evidence; no artifact execution."""
from pathlib import Path
import hashlib,json,shutil,subprocess
ROOT=Path('/home/ubuntu/github/usbip-client')
TMP=Path('/tmp/rustusb-installed-usb-publication-a028cb1b-20261025')
OUT=ROOT/'docs/evidence/installed-usb-publication-a028cb1b-20261025'
HEAD='a028cb1bc20f6500db586b6f516c5263cceb040d'
def load(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==HEAD
assert not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip()
r=load(TMP/'runs-final.json');runs=json.loads(r['structuredContent']['content'])['workflow_runs']
assert len(runs)==3 and all(x['head_sha']==HEAD and x['status']=='completed' and x['conclusion']=='success' for x in runs)
jobs={}
for run in runs:
 j=load(TMP/('jobs-final-'+str(run['id'])+'.json'))['structuredContent']['jobs']
 assert all(x['status']=='completed' and x['conclusion']=='success' for x in j)
 assert len(j)==(2 if run['name']=='Project website' else 4)
 jobs[run['name']]=[{'id':x['id'],'name':x['name'],'status':x['status'],'conclusion':x['conclusion'],'steps':x['steps']} for x in j]
artifacts=load(TMP/'artifacts-final.json')['structuredContent']['artifacts']
assert len(artifacts)==4 and all(not a['expired'] for a in artifacts)
http=load(TMP/'http/receipt.json');assets=load(TMP/'http/asset-receipt.json')
assert len(http)==3 and len(assets)==6 and all(x['status']==200 and x['matches_local'] for x in [*http,*assets])
for p in [TMP/'http/index.html',TMP/'http/README.md',TMP/'http/report.md']:
 local={'index.html':'website/index.html','README.md':'README.md','report.md':'docs/research/installed-usb-data-validation.md'}[p.name]
 assert p.read_bytes()==(ROOT/local).read_bytes()
changed=subprocess.check_output(['git','diff','--name-only','5e867949',HEAD,'--','crates','apps','vendor','Cargo.toml','Cargo.lock','rust-toolchain.toml','packaging','.github'],cwd=ROOT,text=True).splitlines()
assert not changed
OUT.mkdir()
for p in sorted(TMP.rglob('*')):
 if p.is_file():
  q=OUT/p.relative_to(TMP);q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q)
shutil.copyfile(__file__,OUT/'collect.py')
summary={'source_head':HEAD,'ci_runs':[{'id':r['id'],'name':r['name'],'status':r['status'],'conclusion':r['conclusion'],'url':r['html_url']} for r in runs],
 'jobs':jobs,'artifact_metadata':artifacts,'artifact_scope':'metadata only this delivery; no CI zip download, inspection, execution or installed ARM acceptance',
 'public_http':http,'runtime_assets':assets,'unchanged_product_source_build_package_workflows':True,
 'retained_collection_error':'Two generic website tree traversals requested hidden deployment marker .nojekyll and got404; marker is not a served runtime asset. Incorrect maintenance README hypothesis was discarded after actual listing. Correct explicit runtime selection passed.',
 'local_software_scope':'1094 executions / 717 inputs at clean d6e68915; QEMU stays at dd9390c4/d6e68915; do not reattribute to content/docs HEAD'}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
files=[{'path':str(p.relative_to(OUT)),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(OUT.rglob('*')) if p.is_file()]
(OUT/'manifest.json').write_text(json.dumps({'files':files},indent=2)+'\n')
for row in files:
 p=OUT/row['path'];assert p.stat().st_size==row['bytes'] and sha(p)==row['sha256']
print(json.dumps({'files':len(files),'bytes':sum(x['bytes'] for x in files),'ci_runs':len(runs),'jobs':sum(len(x) for x in jobs.values()),'artifacts_metadata':len(artifacts),'http_200_exact':len(http)+len(assets)}))

