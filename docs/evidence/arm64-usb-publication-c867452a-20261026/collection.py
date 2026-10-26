"""Archive actual primary publication observations; no CI log/test recount."""
from pathlib import Path
import datetime,gzip,hashlib,io,json,re,subprocess,tarfile
ROOT=Path('/home/ubuntu/github/usbip-client');SOURCE=Path(__file__).parent;OUT=ROOT/'docs/evidence/arm64-usb-publication-c867452a-20261026';HEAD='c867452ae63de3e0dab8de430c3b98f1436b5f80'
sha=lambda b:hashlib.sha256(b).hexdigest()
def load(p):return json.loads(p.read_text())
runs=json.loads(load(SOURCE/'e62_runs_final.json')['content'])['workflow_runs'];assert len(runs)==3 and all(r['head_sha']==HEAD and r['status']=='completed' and r['conclusion']=='success' for r in runs)
jobs={}
for rid,n in [(40274949683,4),(40274949672,4),(40274949682,2)]:
 j=load(SOURCE/f'e62_jobs_final_{rid}.json')['jobs'];assert len(j)==n
 assert all(x['status']=='completed' and x['conclusion']=='success' for x in j)
 for x in j:
  assert all(s['status']=='completed' and s['conclusion']=='success' for s in x['steps'])
  required=['Verify native architecture and record the software environment','Formatting, lint, tests, release and documentation'] if rid==40274949683 else ['Verify architecture and actual GTK/WebKit versions','GUI formatting, Clippy, tests, release and rustdoc','Build matching base and native preview Debian packages','Upload native preview packages and build receipts'] if rid==40274949672 else []
  assert all(any(s['name']==name for s in x['steps']) for name in required)
 jobs[str(rid)]=j
http=load(SOURCE/'http-summary.json');assert http['head']==HEAD and http['passed'] and len(http['requests'])==10 and all(r['http_status']==200 and r['source_equal'] for r in http['requests'])
raw={str(p.relative_to(SOURCE)):p.read_bytes() for p in sorted(SOURCE.rglob('*')) if p.is_file()}
for n,b in raw.items():
 assert not re.search(b'-----BEGIN '+rb'(?:[A-Z0-9]+ )?PRIVATE KEY-----',b),n
 assert not re.search(rb'https://[^\s"<>]+[?&](?:sig|se)=',b),n
OUT.mkdir()
with (OUT/'originals.tar.gz').open('wb') as stream:
 with gzip.GzipFile(fileobj=stream,mode='wb',filename='',mtime=0) as gz:
  with tarfile.open(fileobj=gz,mode='w|') as tar:
   for n,b in sorted(raw.items()):
    m=tarfile.TarInfo(n);m.size=len(b);m.mode=0o644;tar.addfile(m,io.BytesIO(b))
members=[{'path':n,'bytes':len(b),'sha256':sha(b)} for n,b in sorted(raw.items())]
with tarfile.open(OUT/'originals.tar.gz') as tar:
 assert len(tar.getmembers())==len(members)
 for m in members:
  b=tar.extractfile(m['path']).read();assert len(b)==m['bytes'] and sha(b)==m['sha256']
def write(n,d):(OUT/n).write_text(json.dumps(d,indent=2)+'\n')
write('originals-manifest.json',{'members':members,'member_count':len(members),'raw_bytes':sum(m['bytes'] for m in members)})
write('receipt.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'content_head':HEAD,'primary_runs':[{k:r[k] for k in ['id','name','head_sha','status','conclusion','html_url']} for r in runs],'jobs':jobs,'http':http,'browser':load(SOURCE/'browser-observation.json'),'CI_scope':'Primary tool run/job/required-step metadata verified; no full CI logs or new artifacts downloaded/executed, no per-platform test recount. Final core and Pages responses are exact copies of earlier completed observations, not additional API calls.','test_scope':'Software remains two actual clean 1114/734 gates; eight ARM acceptance failures, one traced failure and five local diagnostics retain their original scope/verdicts. No production PM fix or full ARM data PASS.','privacy':{'private_key_and_signed_capability_marker_scan':True,'archive_roundtrip':True},'archive':{'members':len(members),'raw_bytes':sum(m['bytes'] for m in members),'bytes':(OUT/'originals.tar.gz').stat().st_size,'sha256':sha((OUT/'originals.tar.gz').read_bytes())}})
(OUT/'collection.py').write_bytes(Path(__file__).read_bytes())
(OUT/'README.md').write_text('# ARM USB investigation publication\n\nPrimary GitHub run/job snapshots, exact-source live HTTP observations and live DOM checks for content c867452a. Core four, GUI four and Pages two actual jobs succeed. This is publication evidence, not a new USB execution, per-platform test recount or product PM fix. Desktop/mobile DOM checks are not pixel review; temporary viewport is restored. Exact serialized structured tool responses are retained, not raw HTTP API wire captures.\n')
write('manifest.json',{'files':[{'path':p.name,'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(OUT.iterdir()) if p.is_file() and p.name!='manifest.json']})
print(json.dumps({'members':len(members),'raw_bytes':sum(m['bytes'] for m in members),'bytes':(OUT/'originals.tar.gz').stat().st_size,'sha256':sha((OUT/'originals.tar.gz').read_bytes())}))
