"""Collect public E61 environment proof, not ARM product acceptance."""
from pathlib import Path
import gzip,hashlib,io,json,re,subprocess,tarfile
ROOT=Path('/home/ubuntu/github/usbip-client');OUT=ROOT/'docs/evidence/arm64-environment-20261026';OUT.mkdir()
PREP=Path('/tmp/rustusb-arm64-preparation-20261026');raw={}
def sha(b):return hashlib.sha256(b).hexdigest()
def add(n,p):
 assert n not in raw and p.is_file() and not p.is_symlink();raw[n]=p.read_bytes()
def tree(prefix,folder):
 for p in sorted(folder.rglob('*')):
  if not p.is_file() or p.is_symlink() or '.private' in p.parts or '__pycache__' in p.parts:continue
  assert p.suffix in ['.json','.jsonl','.log','.stdout','.stderr','.txt','.py'],p
  add(prefix+'/'+str(p.relative_to(folder)),p)
def load(p):return json.loads(p.read_text())
def write(n,d):(OUT/n).write_text(json.dumps(d,indent=2)+'\n')
rows=load(PREP/'summary.json');assert len(rows)==2 and all(r['passed'] and r['qemu_exit']==0 and r['head_guard'] and r['clean_guard'] for r in rows)
assert {r['source_head'] for r in rows}=={'dd3f93d8'+subprocess.check_output(['git','rev-parse','dd3f93d8'],cwd=ROOT,text=True).strip()[8:]}
tree('preparation',PREP);add('preparation/host.log',PREP.with_suffix('.log'))
for suffix in ['.composition.json','.generated.py']:add('methods/preparation'+suffix,PREP.with_name(PREP.name+suffix))
for release in ['jammy','noble']:
 image=Path('/tmp/rustusb-arm64-images-20261026')/release
 for name in ['receipt.json','SHA256SUMS','SHA256SUMS.gpg','gpgv.log']:add('images/'+release+'/'+name,image/name)
 ci=Path('/tmp/rustusb-arm64-ci-artifacts-20261026')/release
 for p in ci.glob('*.json'):add('CI/'+release+'/'+p.name,p)
 for kind,folder in [('prototype',Path('/tmp/rustusb-arm64-upgrade-packages-20261026')),('formal',Path('/tmp/rustusb-arm64-final-upgrade-packages-20261026'))]:add('derived/'+kind+'/'+release+'.json',folder/release/'derivation.json')
add('CI/package-inspection.json',Path('/tmp/rustusb-arm64-ci-artifacts-20261026/package-inspection.json'))
tools=Path('/tmp/rustusb-arm64-tools-20261026');add('tools/package-receipts.json',tools/'package-receipts.json');add('tools/download.log',tools/'download.log')
for package in ['qemu-efi-aarch64','qemu-system-arm']:add('tools/'+package+'-copyright.txt',tools/'root/usr/share/doc'/package/'copyright')
for p in sorted(Path('/tmp').glob('*rustusb-arm64*.py')):add('methods/'+p.name,p)
for name in ['rustusb-arm64-upgrade-package-plan-20261026.md','rustusb-arm64-package-adapter-plan-20261026.md','rustusb-arm64-ci-artifacts-attempt01-20261026.log','rustusb-arm64-ci-artifacts-20261026.log','rustusb-arm64-images-20261026.log','rustusb-arm64-upgrade-packages-20261026.log','rustusb-arm64-final-upgrade-packages-20261026.log','rustusb-arm64-f9-ci-observation-20261026.json']:add('development/'+name,Path('/tmp')/name)
for p in Path('/tmp').glob('rustusb-arm64-package-*preflight*-20261026*'):
 if p.is_file() and p.suffix in ['.json','.py','.log']:add('preflight/'+p.name,p)
head=subprocess.check_output(['git','rev-parse','dd3f93d8'],cwd=ROOT,text=True).strip()
for path in ['scripts/qemu_arm64/arm_vm.py','scripts/qemu_arm64/prepare.py','scripts/qemu_installed_usb/prepare.py','scripts/qemu_installed_usb/vm.py']:
 raw['executed-source/'+head+'/'+path]=subprocess.check_output(['git','show',head+':'+path],cwd=ROOT)
secrets=[]
for p in PREP.rglob('ssh-key'):secrets.append(p.read_bytes())
for p in Path('/tmp/rustusb-arm64-ci-artifacts-20261026/.private').glob('*.json'):
 d=load(p)['structuredContent'];secrets.append(d['file_uri']['download_url'].encode())
for n,b in raw.items():
 assert not any(s and s in b for s in secrets),'private material in '+n
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
write('summary.json',{'scope':'ARM64 product-free environment/input preparation only; no RustUSB installation/GUI/USB I/O acceptance','preparation':rows,'original_source_head':head,'CI_source_head':'0316de7638c68b9360ec719194e9e0c4ae0fe454','inspected_ARM_ELFs':6,'CI_packages':4,'derived_same_source_packages':4,'exact_private_keys_and_download_capabilities_checked':len(secrets),'private_bytes_in_archive':False,'archive':{'bytes':(OUT/'originals.tar.gz').stat().st_size,'sha256':sha((OUT/'originals.tar.gz').read_bytes()),'members':len(members),'roundtrip':True}})
(OUT/'collection.py').write_bytes(Path(__file__).read_bytes())
(OUT/'README.md').write_text('# ARM64 environment checkpoint\n\nOriginals retain both successful product-free preparations, image signatures/hashes, actual architecture and module outputs, tool/CI/derived-package identities, all setup failures and read-only observations. This is not installed RustUSB, GUI or USB I/O acceptance. Private images, disks, firmware variables, keys, signed capabilities, ZIP/deb/ELF bytes are excluded. Every public archive member is hash/size checked; exact private key/capability bytes are checked absent.\n')
write('manifest.json',{'files':[{'path':p.name,'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(OUT.iterdir()) if p.is_file() and p.name!='manifest.json']})
print(json.dumps({'members':len(members),'raw_bytes':sum(m['bytes'] for m in members),'archive':(OUT/'originals.tar.gz').stat().st_size,'sha256':sha((OUT/'originals.tar.gz').read_bytes()),'private_keys_and_caps_checked':len(secrets)}))
