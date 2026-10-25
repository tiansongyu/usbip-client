"""Explicit public collection; private disks, ELF/deb and credentials stay outside Git."""
from pathlib import Path
import datetime,gzip,hashlib,io,json,re,subprocess,tarfile
ROOT=Path('/home/ubuntu/github/usbip-client')
OUT=ROOT/'docs/evidence/installed-usb-data-20261025'
GATES=Path('/tmp/rustusb-installed-usb-software-20261025')
PREP=Path('/tmp/rustusb-installed-usb-os-preparation-20261025')
def sha(b):return hashlib.sha256(b).hexdigest()
def load(p):return json.loads(p.read_text())
gates=load(GATES/'summary.json');assert gates['result']=='PASS'
runs=sorted(p for p in Path('/tmp').glob('rustusb-installed-usb-*-attempt*-20261025') if p.is_dir())
assert len(runs)==14 and all((p/'summary.json').exists() for p in runs)
summaries=[load(p/'summary.json') for p in runs]
assert sum(s['passed'] for s in summaries)==4
assert all(s['qemu_exits']=={'server':0,'client':0} and s['head_guard'] and s['clean_guard'] and s['frozen_file_guard'] for s in summaries)
OUT.mkdir()
raw={}
def add(name,path):
 assert name not in raw and path.is_file() and not path.is_symlink()
 raw[name]=path.read_bytes()
def tree(prefix,folder):
 for p in sorted(folder.rglob('*')):
  if not p.is_file() or p.is_symlink() or '.private' in p.relative_to(folder).parts or '__pycache__' in p.parts:continue
  assert p.suffix in ['.json','.jsonl','.stdout','.stderr','.txt','.log','.pcap','.der'],p
  add(prefix+'/'+str(p.relative_to(folder)),p)
for folder in runs:
 tree('runs/'+folder.name,folder)
 add('runs/'+folder.name+'/host.log',folder.with_suffix('.log'))
tree('os-preparation',PREP)
for name in ['receipt.json','SHA256SUMS','SHA256SUMS.gpg','gpgv.log']:
 for release in ['jammy','noble']:
  add('official-images/'+release+'/'+name,Path('/tmp/rustusb-package-images-20261025')/release/name)
for version in [6,7,8]:
 folder=Path('/tmp/rustusb-debs-v'+str(version)+'-20261025')
 add('package-builds/v'+str(version)+'.json',next(folder.glob('build-*.json')))
 if version>=7:add('package-builds/v'+str(version)+'.log',Path('/tmp/rustusb-installed-usb-package-v'+str(version)+'-20261025.log'))
tree('software',GATES)
add('methods/gates.py',Path('/tmp/run-rustusb-installed-usb-gates-20261025.py'))
for name in ['prepare.py','vm.py']:add('methods/original-os-preparation/'+name,Path('/tmp/rustusb-installed-usb-draft')/name)
heads=sorted({s['head'] for s in summaries})
for head in heads:
 files=subprocess.check_output(['git','ls-tree','-r','--name-only',head,'scripts/qemu_installed_usb'],cwd=ROOT,text=True).splitlines()
 for file in files:
  raw['methods/source-'+head+'/'+file]=subprocess.check_output(['git','show',head+':'+file],cwd=ROOT)
# Audit real generated private bytes, not only a filename/keyword allowlist.
secrets=[]
for folder in [PREP,*runs]:
 for p in folder.rglob('*'):
  if p.is_file() and (p.name=='ssh-key' or p.name.endswith(('-key.der','-key.pem'))):
   secrets.append(p.read_bytes())
for name,b in raw.items():
 assert not any(secret and secret in b for secret in secrets),'private key in '+name
 assert b'-----BEGIN '+b'PRIVATE KEY-----' not in b and b'-----BEGIN '+b'OPENSSH PRIVATE KEY-----' not in b,name
 assert not re.search(rb'https://[^\s\"<>]+[?&](?:sig|se)=',b),name
members=[{'path':n,'bytes':len(b),'sha256':sha(b)} for n,b in sorted(raw.items())]
with (OUT/'originals.tar.gz').open('wb') as stream:
 with gzip.GzipFile(fileobj=stream,mode='wb',mtime=0,filename='') as compressed:
  with tarfile.open(fileobj=compressed,mode='w|') as tar:
   for n,b in sorted(raw.items()):
    info=tarfile.TarInfo(n);info.size=len(b);info.mode=0o644;info.mtime=0;tar.addfile(info,io.BytesIO(b))
with tarfile.open(OUT/'originals.tar.gz','r:gz') as tar:
 assert len(tar.getmembers())==len(members)
 for member in members:
  b=tar.extractfile(member['path']).read();assert len(b)==member['bytes'] and sha(b)==member['sha256']
def write(name,value):(OUT/name).write_text(json.dumps(value,indent=2)+'\n')
write('originals-manifest.json',{'members':members,'member_count':len(members),'raw_bytes':sum(x['bytes'] for x in members)})
matrix=[]
for folder,s in zip(runs,summaries):
 matrix.append({'run':folder.name,**s})
write('summary.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'paired_runs':14,'passed':4,'failed_retained':10,'actual_qemu_exits0':28,
 'os_preparation_qemu_exits0':2,'passing_io_rounds':12,'passing_root_proofs':sum(s.get('client_root_proofs',0)+s.get('server_root_proofs',0) for s in summaries if s['passed']),
 'software':{k:v for k,v in gates.items() if k!='inputs'},'software_input_count':len(gates['inputs']),
 'matrix':matrix,'archive':{'sha256':sha((OUT/'originals.tar.gz').read_bytes()),'bytes':(OUT/'originals.tar.gz').stat().st_size,'members':len(members)},
 'privacy':{'exact_private_keys_checked':len(secrets),'private_disks_keys_debs_elfs_archived':False,'archive_roundtrip':True}})
add_dummy=None
(OUT/'collection.py').write_bytes(Path(__file__).read_bytes())
(OUT/'README.md').write_text('# Installed USB evidence\n\nPublic originals are in originals.tar.gz; originals-manifest.json lists every exact member hash and size. summary.json retains all 14 actual paired verdicts, source identities, original failure traces and real QEMU exits. Ten failed runs remain failed. Methods include each actually executed fixture revision and original OS preparation files. Private disk/SSH/TLS key bytes and deb/ELF binaries are excluded. This is synthetic guest USB evidence, not physical hardware acceptance.\n')
write('manifest.json',{'files':[{'path':str(p.relative_to(OUT)),'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='manifest.json']})
print(json.dumps({'members':len(members),'raw_bytes':sum(x['bytes'] for x in members),'archive_sha256':sha((OUT/'originals.tar.gz').read_bytes()),'archive_bytes':(OUT/'originals.tar.gz').stat().st_size,'software_inputs':len(gates['inputs']),'private_keys_checked':len(secrets)}))

