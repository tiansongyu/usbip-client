import gzip,hashlib,io,json,pathlib,tarfile
R=pathlib.Path('/home/ubuntu/github/usbip-client');O=pathlib.Path('/tmp/rustusb-import-power-docs-20261026');D=R/'docs/evidence/native-import-power-docs-20261026';D.mkdir();sha=lambda b:hashlib.sha256(b).hexdigest();files={}
for p in sorted(O.rglob('*')):
 if not p.is_file() or any(x.startswith('.') for x in p.relative_to(O).parts) or p.is_relative_to(O/'preview'):continue
 files['originals/'+str(p.relative_to(O))]=(p,p.read_bytes())
for n in ['rustusb-import-power-audit-20261026.py','rustusb-import-power-docs-check-20261026.py','rustusb-import-power-update-docs-20261026.py','rustusb-import-power-preview-20261026.py','rustusb-import-power-collection-20261026.log']:
 p=pathlib.Path('/tmp')/n;files['methods/'+n]=(p,p.read_bytes())
raw=io.BytesIO();members=[]
with tarfile.open(fileobj=raw,mode='w') as t:
 for n,(p,b) in sorted(files.items()):
  assert b'-----BEGIN PRIVATE KEY-----' not in b and b'-----BEGIN OPENSSH PRIVATE KEY-----' not in b
  i=tarfile.TarInfo(n);i.size=len(b);i.mode=0o644;t.addfile(i,io.BytesIO(b));members.append({'path':n,'original':str(p),'bytes':len(b),'sha256':sha(b)})
data=gzip.compress(raw.getvalue(),mtime=0);(D/'originals.tar.gz').write_bytes(data)
with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as t:
 assert t.getnames()==[r['path'] for r in members]
 for r in members:
  b=t.extractfile(r['path']).read();assert len(b)==r['bytes'] and sha(b)==r['sha256'] and b==pathlib.Path(r['original']).read_bytes()
(D/'originals-manifest.json').write_text(json.dumps({'files':members},indent=2)+'\n');(D/'collection.py').write_bytes(pathlib.Path(__file__).read_bytes());(D/'summary.json').write_text(json.dumps({'result':'PASS','archive':{'members':len(members),'raw_bytes':sum(r['bytes'] for r in members),'bytes':len(data),'sha256':sha(data)},'scope':'Documentation/link/browser observations, same-author raw audit and its two failed development predicates. No new USB execution; no pixel/clipboard/reduced-motion claim. Preview actual SIGINT exit130 expected. First and final QA source scopes retained.','global_skill':'Four source files and Codex symlink unchanged'},indent=2)+'\n');(D/'README.md').write_text('# Import-power documentation QA\n\nExact public methods, captured browser observations, final documentation snapshots, site/link checks and original raw-audit failures are indexed here. The main archive keeps its original bytes, including the empty collector-stdout snapshot; the separate final stdout receipt and explicit third-audit predicate record its later print. This is integrity QA, not another USB run or a separate security audit.\n');rows=[{'path':p.name,'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(D.iterdir()) if p.is_file()];(D/'manifest.json').write_text(json.dumps({'files':rows},indent=2)+'\n');print(json.dumps({'members':len(members),'bytes':len(data),'sha256':sha(data)}))
