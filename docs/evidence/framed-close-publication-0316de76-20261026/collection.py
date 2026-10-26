"""Collect actual publication receipts separately from local software/QEMU."""
from pathlib import Path
import datetime,gzip,hashlib,io,json,re,subprocess,tarfile
ROOT=Path('/home/ubuntu/github/usbip-client')
IN=Path('/tmp/rustusb-framed-close-publication-0316de76-20261026')
OUT=ROOT/'docs/evidence/framed-close-publication-0316de76-20261026'
HEAD='0316de7638c68b9360ec719194e9e0c4ae0fe454'
def sha(b):return hashlib.sha256(b).hexdigest()
def load(p):return json.loads(p.read_text())
def write(n,d):(OUT/n).write_text(json.dumps(d,indent=2)+'\n')
runs=load(IN/'runs-final.json')['workflow_runs']
assert len(runs)==3 and all(r['head_sha']==HEAD and r['status']=='completed' and r['conclusion']=='success' for r in runs)
jobs={}
for run in runs:
 d=load(IN/('jobs-'+str(run['id'])+'-final.json'))['jobs'];assert all(j['status']=='completed' and j['conclusion']=='success' for j in d)
 jobs[str(run['id'])]=[{'id':j['id'],'name':j['name'],'conclusion':j['conclusion']} for j in d]
assert sorted(len(v) for v in jobs.values())==[2,4,4]
http=load(IN/'http.json');assert len(http['checks'])==10 and http['source_head']==HEAD and http['all_exact']
assert subprocess.check_output(['git','ls-remote','origin','refs/heads/main'],cwd=ROOT,text=True).split()[0]==HEAD
OUT.mkdir();raw={}
for p in sorted(IN.iterdir()):
 assert p.is_file() and not p.is_symlink() and p.suffix in ['.json','.log'],p
 b=p.read_bytes();assert not re.search(rb'https://[^\s"<>]+[?&](?:sig|se)=',b),p
 assert b'-----BEGIN '+b'PRIVATE KEY-----' not in b,p
 raw[p.name]=b
raw['collection.py']=Path(__file__).read_bytes()
raw['http-check.py']=Path('/tmp/check-rustusb-framed-publication-http-20261026.py').read_bytes()
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
write('receipt.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'published_content_head':HEAD,'normal_ssh_push_remote_main_verified':True,'runs':[{'id':r['id'],'name':r['name'],'conclusion':r['conclusion'],'url':r['html_url']} for r in runs],'jobs':jobs,'http':http,'job_log_counts':load(IN/'job-log-audit.json'),'artifact_metadata_only':load(IN/'artifacts-summary.json'),'scope':'Remote software CI/Pages only. Local1114 stays473, four USB pairs stay1b59 and coordinated pair stays393. CI package metadata is not installed ARM/USB validation.','archive':{'bytes':(OUT/'originals.tar.gz').stat().st_size,'sha256':sha((OUT/'originals.tar.gz').read_bytes()),'members':len(members),'roundtrip':True}})
(OUT/'README.md').write_text('# Framed-close publication receipt\n\nCI, job logs/counts, artifact metadata and ten exact-source HTTP checks belong to content0316de76. The original local software and QEMU sources retain their own identities. No CI package binary is downloaded, installed or executed by this receipt. Signed temporary download URLs and credentials are not archived.\n')
(OUT/'collection.py').write_bytes(Path(__file__).read_bytes())
write('manifest.json',{'files':[{'path':p.name,'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(OUT.iterdir()) if p.is_file() and p.name!='manifest.json']})
print(json.dumps({'members':len(members),'raw_bytes':sum(m['bytes'] for m in members),'jobs':sum(map(len,jobs.values())),'archive_sha256':sha((OUT/'originals.tar.gz').read_bytes())}))
