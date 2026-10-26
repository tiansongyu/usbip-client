"""Collect explicit public E63 methods, failures and actual-source receipts after owner closure."""
import datetime,gzip,hashlib,io,json,pathlib,subprocess,tarfile
R=pathlib.Path('/home/ubuntu/github/usbip-client');T=pathlib.Path('/tmp');D=R/'docs/evidence/native-import-power-20261026'
sha=lambda b:hashlib.sha256(b).hexdigest()
files={}
def add(p,label):
 p=pathlib.Path(p);assert p.is_file() and not p.is_symlink(),p
 b=p.read_bytes();assert not b.startswith((b'\x7fELF',b'!<arch>\n')),p
 assert label not in files,label;files[label]=(p,b)
def tree(p,label):
 p=pathlib.Path(p)
 for q in sorted(p.rglob('*')):
  rel=q.relative_to(p)
  if any(x.startswith('.') for x in rel.parts) or q.is_symlink() or not q.is_file():continue
  if q.suffix in ['.deb','.zip','.qcow2','.fd','.pyc']:continue
  add(q,label+'/'+str(rel))
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip();assert head=='ed1e8fef84c096feaa337484a4b75f145d6db278'
assert not subprocess.check_output(['git','status','--porcelain'],cwd=R).strip()
software=json.loads((T/'rustusb-native-import-power-software-20261026/summary.json').read_text());assert software['result']=='PASS'
for n,s in {**software['inputs'],**software['binaries']}.items():
 b=(R/n).read_bytes();assert len(b)==s['bytes'] and sha(b)==s['sha256'],n
for n in software['inputs']:add(R/n,'executed-source/'+n)
installed=[]
for arch in ['amd64','arm64']:
 for backend in ['native','usbfs']:
  for fam in [4,6]:
   n=f'rustusb-import-power-{arch}-{backend}-v{fam}-20261026';p=T/n
   j=json.loads((p/'summary.json').read_text());assert all(j[k] for k in ['head_guard','clean_guard','frozen_file_guard']);assert j['head']==head and j['qemu_exits']=={'server':0,'client':0}
   tree(p,'installed/'+n)
   for q in sorted(T.glob(n+'.*')):
    if q.is_file():add(q,'methods/'+q.name)
   installed.append({'case':n,'summary':j,'scope':'pre-product boot failure; forced QMP quit retained' if not j['passed'] else 'original installed USB assertions'})
n='rustusb-import-power-arm64-usbfs-v6-boottrace-20261026';p=T/n;j=json.loads((p/'summary.json').read_text());v=json.loads((p/'boot-trace-verdict.json').read_text());assert j['passed'] and v['passed'] and j['qemu_exits']=={'server':0,'client':0}
tree(p,'installed/'+n)
for q in sorted(T.glob(n+'.*')):
 if q.is_file():add(q,'methods/'+q.name)
installed.append({'case':n,'summary':j,'scope':'independent diagnostic trial with metadata xHCI tracing; not a fix for prior boot stall'})
regressions=[]
for n in ['legacy-linux-server','virtual-ipv4','virtual-ipv6']:
 p=T/('rustusb-import-power-'+n+'-20261026');j=json.loads((p/'summary.json').read_text());o=json.loads(p.with_name(p.name+'.owner.json').read_text());assert j['result']=='PASS' and o['result']=='PASS' and j['guest_exit_codes']=={'server':0,'client':0}
 tree(p,'regressions/'+n)
 for q in [p.with_name(p.name+'.log'),p.with_name(p.name+'.owner.json')]:add(q,'methods/'+q.name)
 regressions.append({'case':n,'summary':{k:j[k] for k in ['result','guest_exit_codes']},'owner':o})
for n in ['rustusb-native-import-power-software-20261026','rustusb-import-power-development-20261026','rustusb-native-import-power-amd64-packages-20261026','rustusb-native-import-power-arm64-ci-20261026','rustusb-native-import-power-arm64-upgrades-20261026']:tree(T/n,n)
for pattern in ['rustusb-import-power-*-20261026.py','rustusb-import-power-*-20261026.log','rustusb-native-import-power-*-20261026.py','rustusb-native-import-power-*-20261026.log','run-rustusb-native-import-power-*-20261026.py','rustusb-import-power-ci-*-20261026.json']:
 for q in sorted(T.glob(pattern)):
  if q.name=='rustusb-import-power-collect-20261026.py' or any(src==q for src,b in files.values()):continue
  add(q,'methods/'+q.name)
# Fail closed on all actual live-key files and signed-capability values; never expose their bodies.
secrets=[];secretfiles=[]
for root in [*(T/x['case'] for x in installed),T/'rustusb-native-import-power-arm64-ci-20261026']:
 for q in root.rglob('*'):
  if not q.is_file() or not any(part.startswith('.') for part in q.relative_to(root).parts):continue
  if ('key' in q.name.lower() and q.suffix in ['.pem','.der']) or q.name=='ssh-key':
   b=q.read_bytes();secrets.append(b);secretfiles.append(str(q))
  if q.name.endswith('-download.json'):
   def walk(v):
    if isinstance(v,str) and v.startswith('https://') and ('?' in v or 'sig=' in v):secrets.append(v.encode())
    elif isinstance(v,dict):
     for x in v.values():walk(x)
    elif isinstance(v,list):
     for x in v:walk(x)
   walk(json.loads(q.read_text()))
for name,(p,b) in files.items():
 assert b'-----BEGIN PRIVATE KEY-----' not in b and b'-----BEGIN RSA PRIVATE KEY-----' not in b and b'-----BEGIN OPENSSH PRIVATE KEY-----' not in b,name
 assert all(secret not in b for secret in secrets if len(secret)>15),name
# Write nothing into the repository until every expected original summary exists above.
D.mkdir();manifest=[];raw=io.BytesIO()
with tarfile.open(fileobj=raw,mode='w') as archive:
 for name,(p,b) in sorted(files.items()):
  info=tarfile.TarInfo(name);info.size=len(b);info.mode=0o644;info.mtime=0;archive.addfile(info,io.BytesIO(b));manifest.append({'path':name,'original':str(p),'bytes':len(b),'sha256':sha(b)})
data=gzip.compress(raw.getvalue(),mtime=0);(D/'originals.tar.gz').write_bytes(data)
with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
 assert archive.getnames()==[r['path'] for r in manifest]
 for row in manifest:
  b=archive.extractfile(row['path']).read();assert len(b)==row['bytes'] and sha(b)==row['sha256']
(D/'originals-manifest.json').write_text(json.dumps({'files':manifest},indent=2)+'\n')
summary={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'executed_head':head,'software':{k:v for k,v in software.items() if k!='inputs'},'software_inputs':len(software['inputs']),'installed_cases':installed,'regressions':regressions,'positive_installed_pairs':8,'positive_regression_pairs':3,'failed_preproduct_boot_pairs':1,'qemu_exits_zero':24,'scope':'8 installed success categories (one diagnostic traced ARM USBFS-v6), 3 original regression pairs, one preserved forced pre-product boot failure. No hardware/system-sleep/general PM/boot-stability claim. Package control revisions contain same code; not old-code migration.','privacy':{'actual_private_key_files_checked':len(secretfiles),'secret_sequences_including_capabilities':len(secrets),'all_private_payloads_excluded':True,'member_readback':True},'archive':{'members':len(manifest),'raw_bytes':sum(x['bytes'] for x in manifest),'bytes':len(data),'sha256':sha(data)}}
(D/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');(D/'collection.py').write_bytes(pathlib.Path(__file__).read_bytes());(D/'README.md').write_text('# Native import runtime power validation\n\nExact public original records, methods, prior failures and executed-source snapshots are indexed by `originals-manifest.json`. `summary.json` keeps the traced success separate from the original forced boot failure. No private keys, signed capability URLs, disks, firmware, packages or executables are included. Source and payload revisions, package audits and actual cleanup verdicts retain their separate provenance.\n')
public=[{'path':p.name,'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(D.iterdir()) if p.is_file()];(D/'manifest.json').write_text(json.dumps({'files':public},indent=2)+'\n');print(json.dumps({'archive':summary['archive'],'privacy':summary['privacy']}))
