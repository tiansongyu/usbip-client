import datetime,gzip,hashlib,io,json,pathlib,tarfile
R=pathlib.Path('/home/ubuntu/github/usbip-client');O=pathlib.Path('/tmp/rustusb-import-power-publication-20261026');D=R/'docs/evidence/native-import-power-publication-45c01bb-20261026';head=(O/'content-head.txt').read_text().strip();sha=lambda b:hashlib.sha256(b).hexdigest()
runs=json.loads(json.loads((O/'runs-final.json').read_text())['structuredContent']['content'])['workflow_runs'];assert len(runs)==3 and all(r['head_sha']==head and r['status']=='completed' and r['conclusion']=='success' for r in runs)
jobsets={}
for i,count in [(40296008987,4),(40296008856,4),(40296008970,2)]:
 jobs=json.loads((O/f'jobs-final-{i}.json').read_text())['structuredContent']['jobs'];assert len(jobs)==count and all(j['status']=='completed' and j['conclusion']=='success' for j in jobs);jobsets[str(i)]=[{'id':j['id'],'name':j['name'],'status':j['status'],'conclusion':j['conclusion']} for j in jobs]
http=json.loads((O/'http-summary.json').read_text());assert http['passed'] and len(http['responses'])==10 and all(r['matches_content_commit'] for r in http['responses'])
rows=[];raw=io.BytesIO();paths=sorted(p for p in O.rglob('*') if p.is_file() and not any(x.startswith('.') for x in p.relative_to(O).parts))
with tarfile.open(fileobj=raw,mode='w') as t:
 for p in paths:
  b=p.read_bytes();assert b'-----BEGIN PRIVATE KEY-----' not in b and b'-----BEGIN OPENSSH PRIVATE KEY-----' not in b
  n=str(p.relative_to(O));i=tarfile.TarInfo(n);i.size=len(b);i.mode=0o644;t.addfile(i,io.BytesIO(b));rows.append({'path':n,'original':str(p),'bytes':len(b),'sha256':sha(b)})
data=gzip.compress(raw.getvalue(),mtime=0)
with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as t:
 assert t.getnames()==[r['path'] for r in rows]
 for r in rows:
  b=t.extractfile(r['path']).read();assert len(b)==r['bytes'] and sha(b)==r['sha256'] and b==pathlib.Path(r['original']).read_bytes()
D.mkdir();(D/'originals.tar.gz').write_bytes(data);(D/'originals-manifest.json').write_text(json.dumps({'files':rows},indent=2)+'\n');(D/'collection.py').write_bytes(pathlib.Path(__file__).read_bytes());summary={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'content_head':head,'actual_usb_software_head':'ed1e8fef84c096feaa337484a4b75f145d6db278','result':'PASS','jobs':jobsets,'canonical_http_bodies_equal_content_commit':10,'browser':'Actual live desktop/mobile DOM, known-failure copy, new link, responsive/lazy images, console and reset observed; no pixel review. Public tab8 retained.','scope':'Primary run/job/step metadata only; no full CI logs or per-platform test-count reconstruction. This later documentation commit does not relabel USB or local software provenance.','archive':{'members':len(rows),'raw_bytes':sum(r['bytes'] for r in rows),'bytes':len(data),'sha256':sha(data)}};(D/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');(D/'README.md').write_text('# Import-power validation publication\n\nContent45c01bb2 was normally pushed over SSH. Exact source snapshots, primary workflow/job/step metadata, ten canonical HTTP responses and live CUA observations are retained. Local software and USB execution remain attributed to ed1e8fef. No screenshot/pixel or full-CI-log claim.\n');public=[{'path':p.name,'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(D.iterdir()) if p.is_file()];(D/'manifest.json').write_text(json.dumps({'files':public},indent=2)+'\n');print(json.dumps(summary['archive']))
