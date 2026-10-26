"""Exact public cold-install observations, kept separate from USB acceptance."""
from pathlib import Path
import gzip,hashlib,io,json,re,subprocess,tarfile
ROOT=Path('/home/ubuntu/github/usbip-client');OUT=ROOT/'docs/evidence/arm64-cold-installation-20261026';OUT.mkdir();raw={}
def sha(b):return hashlib.sha256(b).hexdigest()
def load(p):return json.loads(p.read_text())
def add(n,p):
 assert n not in raw and p.is_file() and not p.is_symlink();raw[n]=p.read_bytes()
def write(n,d):(OUT/n).write_text(json.dumps(d,indent=2)+'\n')
runs=[];secrets=[]
for release in ['jammy','noble']:
 folder=Path('/tmp/rustusb-arm64-cold-'+release+'-20261026');summary=load(folder/'summary.json');guards=load(folder/'arm-freeze-guards.json')
 assert summary['passed'] and summary['qemu_exit']==summary['first_qemu_exit']==summary['collect_exit']==0 and not summary['forced']
 assert all(summary[k] for k in ['source_guard','binary_guard','head_guard']) and all(guards[k] for k in ['head_guard','clean_guard','adapter_tool_guard'])
 for p in sorted(folder.rglob('*')):
  if not p.is_file() or p.is_symlink() or '.private' in p.parts:continue
  assert p.suffix in ['.json','.jsonl','.log','.stdout','.stderr','.tar','.der'],p
  add('runs/'+release+'/'+str(p.relative_to(folder)),p)
 for suffix in ['.composition.json','.generated.py','.log']:add('methods/'+release+suffix,folder.with_name(folder.name+suffix))
 with tarfile.open(folder/'guest-evidence.tar') as tar:
  guest={m.name:tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
  for name,b in guest.items():raw['guest/'+release+'/'+name.removeprefix('./')]=b
  checks=[json.loads(l) for l in guest['./checks.jsonl'].decode().splitlines()];assert all(c['passed'] for c in checks)
  library=load(folder/'fsync-gate-library.json');executed=json.loads(guest['./executed-fsync-library.json']);assert library['machine']=='AArch64' and library['compiled_in_actual_ARM_guest'] and library['sha256']==executed['sha256']
  assert json.loads(guest['./gui-exit.json'])['exit']==0 and json.loads(guest['./upgrade-exit.json'])['exit']==0
  rows=[json.loads(l) for l in guest['./fsync-gate.jsonl'].decode().splitlines()];entered=[r for r in rows if r['phase']=='entered'];returned=[r for r in rows if r['phase']=='returned' and r['result']==0]
  assert entered and returned and all(any(e['pid']==r['pid'] for e in entered) for r in returned)
  runs.append({'release':release,'head':guards['initial_head'],'summary':summary,'guards':{k:v for k,v in guards.items() if k!='frozen_inputs'},'check_records':len(checks),'distinct_labels':len({r['label'] for r in checks}),'all_checks_passed':True,'native_C_fixture':library,'actual_executed_fixture':executed,'fsync_entered':len(entered),'fsync_returned0':len(returned)})
 for p in folder.rglob('*'):
  if p.is_file() and (p.name=='ssh-key' or p.name.endswith(('-key.der','-key.pem'))):secrets.append(p.read_bytes())
gates=Path('/tmp/rustusb-arm64-installed-software-20261026');software=load(gates/'summary.json');assert software['result']=='PASS' and all(software['guards'].values())
assert len(software['inputs'])==731 and {r['head'] for r in runs}=={software['head']}
for p in gates.iterdir():
 if p.is_file():add('software/'+p.name,p)
add('methods/software-gates.py',Path('/tmp/run-rustusb-arm64-installed-gates-20261026.py'))
head=software['head']
for directory in ['scripts/qemu_arm64','scripts/qemu_package']:
 for name in subprocess.check_output(['git','ls-tree','-r','--name-only',head,directory],cwd=ROOT,text=True).splitlines():raw['source/'+head+'/'+name]=subprocess.check_output(['git','show',head+':'+name],cwd=ROOT)
for n,b in raw.items():
 assert not any(s and s in b for s in secrets),'private key in '+n
 assert b'-----BEGIN '+b'PRIVATE KEY-----' not in b and b'-----BEGIN '+b'OPENSSH PRIVATE KEY-----' not in b,n
 assert not re.search(rb'https://[^\s"<>]+[?&](?:sig|se)=',b),n
members=[{'path':n,'bytes':len(b),'sha256':sha(b)} for n,b in sorted(raw.items())]
with (OUT/'originals.tar.gz').open('wb') as stream:
 with gzip.GzipFile(fileobj=stream,mode='wb',filename='',mtime=0) as z:
  with tarfile.open(fileobj=z,mode='w|') as tar:
   for n,b in sorted(raw.items()):
    m=tarfile.TarInfo(n);m.size=len(b);m.mode=0o644;tar.addfile(m,io.BytesIO(b))
with tarfile.open(OUT/'originals.tar.gz','r:gz') as tar:
 assert len(tar.getmembers())==len(members)
 for m in members:
  b=tar.extractfile(m['path']).read();assert len(b)==m['bytes'] and sha(b)==m['sha256']
write('originals-manifest.json',{'members':members,'member_count':len(members),'raw_bytes':sum(m['bytes'] for m in members)})
write('summary.json',{'scope':'Two actual ARM64 cold package lifecycles and host software gates; no ARM USB I/O/hardware acceptance','runs':runs,'actual_qemu_exits0':4,'forced':False,'source_head':head,'CI_binary_source':'0316de7638c68b9360ec719194e9e0c4ae0fe454','software':{k:v for k,v in software.items() if k!='inputs'},'software_input_count':731,'exact_private_keys_checked':len(secrets),'private_bytes_archived':False,'archive':{'members':len(members),'bytes':(OUT/'originals.tar.gz').stat().st_size,'sha256':sha((OUT/'originals.tar.gz').read_bytes()),'roundtrip':True}})
(OUT/'collection.py').write_bytes(Path(__file__).read_bytes())
(OUT/'README.md').write_text('# ARM64 cold installation evidence\n\nBoth actual ARM64 full Ubuntu cold package cases pass their original install/GUI/guarded upgrade/reboot/remove/purge assertions. Four actual QEMU exits0, no forced cleanup. Host full core/GUI gates belong to the same clean76b4 source; ARM binaries belong to original CI0316 plus control-only same-source revision derivations. Nested guest tar members are individually hashed and inspected. This is not ARM USB I/O or physical hardware acceptance. Private keys, disks, variables, ZIP/deb/ELF bytes are excluded.\n')
write('manifest.json',{'files':[{'path':p.name,'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(OUT.iterdir()) if p.is_file() and p.name!='manifest.json']})
print(json.dumps({'members':len(members),'raw_bytes':sum(m['bytes'] for m in members),'bytes':(OUT/'originals.tar.gz').stat().st_size,'sha256':sha((OUT/'originals.tar.gz').read_bytes()),'private_keys_checked':len(secrets),'checks':[r['check_records'] for r in runs],'distinct':[r['distinct_labels'] for r in runs]}))
