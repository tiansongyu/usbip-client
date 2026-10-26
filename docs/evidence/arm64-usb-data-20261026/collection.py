"""All original ARM USB verdicts and exact public observations, no keys/disks."""
from pathlib import Path
import datetime,gzip,hashlib,io,json,re,subprocess,tarfile
ROOT=Path('/home/ubuntu/github/usbip-client');OUT=ROOT/'docs/evidence/arm64-usb-data-20261026'
def sha(b):return hashlib.sha256(b).hexdigest()
def load(p):return json.loads(p.read_text())
def write(n,d):(OUT/n).write_text(json.dumps(d,indent=2)+'\n')
folders=sorted(p for p in Path('/tmp').glob('rustusb-arm64-usb-*-20261026') if p.is_dir() and re.fullmatch(r'rustusb-arm64-usb-(?:native|usbfs)-v[46](?:-(?:attempt\d+|trace))?-20261026',p.name))
assert len(folders)>=8
raw={};matrix=[];secrets=[]
def add(n,p):
 assert n not in raw and p.is_file() and not p.is_symlink();raw[n]=p.read_bytes()
def tree(prefix,folder):
 for p in sorted(folder.rglob('*')):
  if not p.is_file() or p.is_symlink() or '.private' in p.parts or '__pycache__' in p.parts:continue
  assert p.suffix in ['.json','.jsonl','.log','.txt','.stdout','.stderr','.pcap','.der'],p
  add(prefix+'/'+str(p.relative_to(folder)),p)
for folder in folders:
 summary=load(folder/'summary.json');arm=load(folder/'arm-verdict.json')
 assert arm['inherited_summary_not_rewritten'] and arm['inherited_passed']==summary['passed'] and arm['inherited_summary_sha256']==sha((folder/'summary.json').read_bytes())
 assert arm['passed']==(summary['passed'] and all(arm['guards'].values()))
 matrix.append({'run':folder.name,'diagnostic_only':'-trace-' in folder.name,**summary,'arm_verdict':arm});tree('runs/'+folder.name,folder)
 for suffix in ['.log','.composition.json','.generated.py']:add('methods/'+folder.name+suffix,folder.with_name(folder.name+suffix))
 if '-trace-' in folder.name:
  add('methods/'+folder.name+'.trace-provenance.json',folder.with_name(folder.name+'.trace-provenance.json'))
  add('methods/rustusb-arm64-usb-trace-20261026.py',Path('/tmp/rustusb-arm64-usb-trace-20261026.py'))
 for p in folder.rglob('*'):
  if p.is_file() and (p.name=='ssh-key' or p.name.endswith(('-key.der','-key.pem'))):secrets.append(p.read_bytes())
for name,folder in [('initial',Path('/tmp/rustusb-arm64-usb-software-20261026')),('fixed',Path('/tmp/rustusb-arm64-usb-fixed-software-20261026'))]:
 tree('software/'+name,folder)
 add('software/'+name+'/host.log',folder.with_suffix('.log'))
for name in ['run-rustusb-arm64-usb-gates-20261026.py','run-rustusb-arm64-usb-fixed-gates-20261026.py','rustusb-arm64-usb-help-20261026.log','rustusb-arm64-usb-fixed-help-20261026.log','rustusb-arm64-usb-device-help-20261026.log']:add('methods/'+name,Path('/tmp')/name)
for diagnostic in sorted(Path('/tmp').glob('rustusb-arm64-local-hid*-20261026')):
 if not diagnostic.is_dir():continue
 assert (diagnostic/'summary.json').is_file(),diagnostic
 tree('diagnostics/'+diagnostic.name,diagnostic)
 for suffix in ['.py','.log']:add('diagnostics/'+diagnostic.name+suffix,diagnostic.with_name(diagnostic.name+suffix))
 for key in diagnostic.rglob('ssh-key'):secrets.append(key.read_bytes())
for p in sorted(Path('/tmp/rustusb-e62-qemu-source').rglob('*')):
 if p.is_file():add('research/qemu-v6.2.0/'+str(p.relative_to('/tmp/rustusb-e62-qemu-source')),p)
ci=Path('/tmp/rustusb-arm64-ci-artifacts-20261026/jammy')
add('inputs/original-CI-build.json',next(ci.glob('build-*.json')))
add('inputs/CI-download.json',ci/'download-receipt.json')
add('inputs/derived-jammy.json',Path('/tmp/rustusb-arm64-final-upgrade-packages-20261026/jammy/derivation.json'))
add('inputs/tool-packages.json',Path('/tmp/rustusb-arm64-tools-20261026/package-receipts.json'))
for release in ['jammy','noble']:add('inputs/'+release+'-preparation.json',Path('/tmp/rustusb-arm64-preparation-20261026')/release/'preparation.json')
heads=sorted({row['head'] for row in matrix})
for head in heads:
 for directory in ['scripts/qemu_arm64','scripts/qemu_installed_usb']:
  files=subprocess.check_output(['git','ls-tree','-r','--name-only',head,directory],cwd=ROOT,text=True).splitlines()
  for name in files:raw['source/'+head+'/'+name]=subprocess.check_output(['git','show',head+':'+name],cwd=ROOT)
 for name in subprocess.check_output(['git','ls-tree','-r','--name-only',head,'scripts/qemu'],cwd=ROOT,text=True).splitlines():
  if name.endswith('.py'):raw['source/'+head+'/'+name]=subprocess.check_output(['git','show',head+':'+name],cwd=ROOT)
for p in sorted(Path('/tmp/rustusb-e62-linux-source').rglob('*')):
 if p.is_file():add('research/linux-v5.15-attempt/'+str(p.relative_to('/tmp/rustusb-e62-linux-source')),p)
add('methods/actual-owner-receipts.json',Path('/tmp/rustusb-arm64-usb-owner-receipts-20261026.json'))
for n,b in raw.items():
 assert not any(s and s in b for s in secrets),'private key in '+n
 assert not re.search(b'-----BEGIN '+rb'(?:[A-Z0-9]+ )?PRIVATE KEY-----',b),n
 assert not re.search(rb'https://[^\s"<>]+[?&](?:sig|se)=',b),n
OUT.mkdir()
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
fixed=load(Path('/tmp/rustusb-arm64-usb-fixed-software-20261026/summary.json'))
write('originals-manifest.json',{'members':members,'member_count':len(members),'raw_bytes':sum(m['bytes'] for m in members)})
write('summary.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'paired_runs':len(matrix),'acceptance_paired_runs':sum(not r['diagnostic_only'] for r in matrix),'diagnostic_paired_runs':sum(r['diagnostic_only'] for r in matrix),'passed':sum(r['arm_verdict']['passed'] for r in matrix),'failed_retained':sum(not r['arm_verdict']['passed'] for r in matrix),'actual_QEMU_exits0':sum(v==0 for r in matrix for v in r['qemu_exits'].values()),'matrix':matrix,'software':{k:v for k,v in fixed.items() if k!='inputs'},'software_input_count':len(fixed['inputs']),'local_diagnostics':[{'run':p.name,**load(p/'summary.json')} for p in sorted(Path('/tmp').glob('rustusb-arm64-local-hid*-20261026')) if p.is_dir()],'CI_ARM_binary_source':'0316de7638c68b9360ec719194e9e0c4ae0fe454','package_scope':'Original Jammy native ARM build on both Ubuntu kernels, formal control-only same-source base revision upgrade; not separate Noble ELF data-path acceptance.','privacy':{'actual_private_key_file_sequences_checked':len(secrets),'private_disks_keys_variables_debs_elfs_archived':False,'archive_roundtrip':True},'archive':{'members':len(members),'bytes':(OUT/'originals.tar.gz').stat().st_size,'sha256':sha((OUT/'originals.tar.gz').read_bytes())}})
(OUT/'collection.py').write_bytes(Path(__file__).read_bytes())
(OUT/'README.md').write_text('# ARM64 USB data evidence\n\nAll original paired verdicts are preserved, including initial failed network setup. Inherited summaries are never rewritten; ARM outer guards are required for acceptance. Source, tools, parents, package/ELF identities, both software gates, guest observations and complete boot/product-window PCAP are separated. Exact member hashes/sizes are verified by archive read-back. Private images, disks, variables, keys, debs and ELFs are excluded. Synthetic ARM64 guest I/O is separate from physical board/device acceptance.\n')
write('manifest.json',{'files':[{'path':p.name,'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(OUT.iterdir()) if p.is_file() and p.name!='manifest.json']})
print(json.dumps({'members':len(members),'raw_bytes':sum(m['bytes'] for m in members),'bytes':(OUT/'originals.tar.gz').stat().st_size,'sha256':sha((OUT/'originals.tar.gz').read_bytes()),'paired_runs':len(matrix),'acceptance_paired_runs':sum(not r['diagnostic_only'] for r in matrix),'diagnostic_paired_runs':sum(r['diagnostic_only'] for r in matrix),'passed':sum(r['arm_verdict']['passed'] for r in matrix),'private_key_files_checked':len(secrets)}))
