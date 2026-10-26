"""Remote status/HTTP metadata only; do not relabel the actual ARM binaries."""
from pathlib import Path
import gzip,hashlib,io,json,re,subprocess,tarfile
ROOT=Path('/home/ubuntu/github/usbip-client');IN=Path('/tmp/rustusb-arm64-publication-96a93c38-20261026');OUT=ROOT/'docs/evidence/arm64-publication-96a93c38-20261026'
HEAD='96a93c38172c9646cae688524b276ee7f3ba1bdb'
def sha(b):return hashlib.sha256(b).hexdigest()
def load(p):return json.loads(p.read_text())
def write(n,d):(OUT/n).write_text(json.dumps(d,indent=2)+'\n')
runs=load(IN/'runs-final.json')['workflow_runs'];assert len(runs)==3 and all(r['head_sha']==HEAD and r['status']=='completed' and r['conclusion']=='success' for r in runs)
jobs={}
for run in runs:
 rows=load(IN/('jobs-'+str(run['id'])+'-final.json'))['jobs'];assert all(j['status']=='completed' and j['conclusion']=='success' for j in rows)
 required=['Formatting, lint, tests, release and documentation','Verify native architecture and record the software environment'] if run['id']==40268921591 else ['GUI formatting, Clippy, tests, release and rustdoc','Verify architecture and actual GTK/WebKit versions','Build matching base and native preview Debian packages','Upload native preview packages and build receipts'] if run['id']==40268921583 else []
 for job in rows:
  for name in required:assert any(s['name']==name and s['conclusion']=='success' for s in job['steps']),(job['id'],name)
 jobs[str(run['id'])]=[{'id':j['id'],'name':j['name'],'conclusion':j['conclusion'],'required_successful_steps':required} for j in rows]
assert sorted(map(len,jobs.values()))==[2,4,4]
http=load(IN/'http.json');assert http['source_head']==HEAD and http['all_exact'] and len(http['checks'])==10
artifacts=load(IN/'artifacts-final.json')['artifacts'];assert len(artifacts)==4
assert subprocess.check_output(['git','ls-remote','origin','refs/heads/main'],cwd=ROOT,text=True).split()[0]==HEAD
OUT.mkdir();raw={}
for p in sorted(IN.iterdir()):
 assert p.is_file() and p.suffix in ['.json','.log'],p
 b=p.read_bytes();assert not re.search(rb'https://[^\s"<>]+[?&](?:sig|se)=',b),p
 raw[p.name]=b
raw['collection.py']=Path(__file__).read_bytes();raw['http-check.py']=Path('/tmp/check-rustusb-arm64-publication-http-20261026.py').read_bytes()
members=[{'path':n,'bytes':len(b),'sha256':sha(b)} for n,b in sorted(raw.items())]
with (OUT/'originals.tar.gz').open('wb') as stream:
 with gzip.GzipFile(fileobj=stream,mode='wb',filename='',mtime=0) as compressed:
  with tarfile.open(fileobj=compressed,mode='w|') as tar:
   for n,b in sorted(raw.items()):
    m=tarfile.TarInfo(n);m.size=len(b);m.mode=0o644;tar.addfile(m,io.BytesIO(b))
with tarfile.open(OUT/'originals.tar.gz','r:gz') as tar:
 assert len(tar.getmembers())==len(members)
 for m in members:
  b=tar.extractfile(m['path']).read();assert len(b)==m['bytes'] and sha(b)==m['sha256']
write('originals-manifest.json',{'members':members,'member_count':len(members),'raw_bytes':sum(m['bytes'] for m in members)})
write('receipt.json',{'content_head':HEAD,'normal_SSH_push_remote_main_verified':True,'runs':[{'id':r['id'],'name':r['name'],'conclusion':r['conclusion'],'url':r['html_url']} for r in runs],'jobs':jobs,'http':http,'artifact_metadata':[{k:a.get(k) for k in ['id','name','size_in_bytes','digest','expired']} for a in artifacts],'current_CI_artifact_binaries_downloaded_or_executed':False,'current_CI_test_counts_recounted':False,'scope':'Remote source96 status/required steps/metadata and exact HTTP only. Local1114/731 and ARM cold remain76b4, preparation staysdd3, executed ARM CI payload stays0316 plus audited control-only revisions.','archive':{'bytes':(OUT/'originals.tar.gz').stat().st_size,'sha256':sha((OUT/'originals.tar.gz').read_bytes()),'members':len(members),'roundtrip':True}})
(OUT/'collection.py').write_bytes(Path(__file__).read_bytes())
(OUT/'README.md').write_text('# ARM64 publication receipt\n\nContent96a93c38 native CI/required steps, four artifact metadata records, Pages check/deploy and ten exact-source HTTP checks. New CI package binaries are not downloaded or executed, and their test counts are not recounted here. Executed ARM payload0316, cold/software76b4 and prepdd3 keep their original identities.\n')
write('manifest.json',{'files':[{'path':p.name,'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(OUT.iterdir()) if p.is_file() and p.name!='manifest.json']})
print(json.dumps({'members':len(members),'raw_bytes':sum(m['bytes'] for m in members),'bytes':(OUT/'originals.tar.gz').stat().st_size,'sha256':sha((OUT/'originals.tar.gz').read_bytes()),'successful_jobs':sum(map(len,jobs.values()))}))
