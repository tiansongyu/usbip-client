"""Fixed-source research and actual original-Linux PM controls, no product verdict."""
from pathlib import Path
import datetime,gzip,hashlib,io,json,re,subprocess,tarfile
ROOT=Path('/home/ubuntu/github/usbip-client');OUT=ROOT/'docs/evidence/native-import-power-research-20261026';DIAG=Path('/tmp/rustusb-arm64-local-hid-delay-20261026');HEAD='84045dfb1a3997f7c6dcb7ca8630a01ed4431363'
sha=lambda b:hashlib.sha256(b).hexdigest()
load=lambda p:json.loads(p.read_text())
s=load(DIAG/'summary.json');assert s['head']==HEAD and s['qemu_exit']==0 and s['expected_controls_satisfied'] and all(s['guards'].values()) and [t['passed'] for t in s['tests']]==[False,True,True]
raw={}
def add(n,p):assert n not in raw and p.is_file() and not p.is_symlink();raw[n]=p.read_bytes()
for prefix,folder in [('kernel',Path('/tmp/rustusb-e63-kernel-pm-research-20261026')),('owner',Path('/tmp/rustusb-e63-owner-pm-research-20261026')),('diagnostic',DIAG)]:
 for p in sorted(folder.rglob('*')):
  if p.is_file() and '.private' not in p.parts and '__pycache__' not in p.parts and not p.is_symlink():add(prefix+'/'+str(p.relative_to(folder)),p)
for suffix in ['.py','.log']:add('methods/'+DIAG.name+suffix,DIAG.with_name(DIAG.name+suffix))
for name in ['scripts/qemu_arm64/arm_vm.py','scripts/qemu_installed_usb/vm.py','scripts/qemu/run-physical-qemu.py','crates/rustusb-native/src/lib.rs','crates/rustusb-native/src/channel.rs','crates/rustusb-daemon/src/linux.rs','crates/rustusb-daemon/src/native_channel.rs']:
 raw['source/'+HEAD+'/'+name]=subprocess.check_output(['git','show',HEAD+':'+name],cwd=ROOT)
secrets=[p.read_bytes() for p in (DIAG/'.private').rglob('ssh-key')] if (DIAG/'.private').exists() else [p.read_bytes() for p in DIAG.rglob('ssh-key')]
assert len(secrets)==1
for n,b in raw.items():
 assert not any(key in b for key in secrets),n
 assert not re.search(b'-----BEGIN '+rb'(?:[A-Z0-9]+ )?PRIVATE KEY-----',b),n
 assert not re.search(rb'https://[^\s"<>]+[?&](?:sig|se)=',b),n
for method,active,delay in [('auto-original-send-key','suspended','2000'),('negative-delay-original-send-key','active','-1'),('allow-original-send-key','active','-1')]:
 name=next(n for n in raw if n.startswith('diagnostic/guest/') and n.endswith('-power-'+method+'.stdout'));text=raw[name].decode();prefix='/sys/devices/platform/vhci_hcd.0/usb3/3-1/power/'
 assert prefix+'runtime_status\n'+active+'\n' in text and prefix+'autosuspend_delay_ms\n'+delay+'\n' in text
 assert prefix+'control\nauto\n' in text
for test in s['tests'][1:]:assert [1,2,1] in test['events'] and [1,2,0] in test['events']
raw['methods/actual-owner-terminal.json']=(json.dumps({'type':'transcription of actual write_stdin terminal return','session_id':69621,'actual_host_exit':0,'actual_qemu_exit':0,'not_a_product_fix_or_original_matrix_acceptance':True})+'\n').encode()
OUT.mkdir()
with (OUT/'originals.tar.gz').open('wb') as stream:
 with gzip.GzipFile(fileobj=stream,mode='wb',filename='',mtime=0) as gz:
  with tarfile.open(fileobj=gz,mode='w|') as tar:
   for n,b in sorted(raw.items()):m=tarfile.TarInfo(n);m.size=len(b);m.mode=0o644;tar.addfile(m,io.BytesIO(b))
members=[{'path':n,'bytes':len(b),'sha256':sha(b)} for n,b in sorted(raw.items())]
with tarfile.open(OUT/'originals.tar.gz') as tar:
 assert len(tar.getmembers())==len(members)
 for m in members:
  b=tar.extractfile(m['path']).read();assert len(b)==m['bytes'] and sha(b)==m['sha256']
def write(n,d):(OUT/n).write_text(json.dumps(d,indent=2)+'\n')
write('originals-manifest.json',{'members':members,'member_count':len(members),'raw_bytes':sum(m['bytes'] for m in members)})
write('summary.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'diagnostic':s,'scope':'Original Linux USBIP single-guest controls, not RustUSB fix or original ARM matrix acceptance. Same bus/probe; no independent per-stage sysfs inode-generation sampling. Production policy still requires pinned generation checks.','research':'Two read-only component audits mandated by writing-technical-design; fixed v5.15/v6.8 and pinned rustix1.1.5, no new component skill per user instructions. HTTP429 and later successful necessary primary-mirror reads retained.','privacy':{'actual_private_key_file_sequences_checked':len(secrets),'private_payloads_excluded':True,'roundtrip':True},'archive':{'members':len(members),'raw_bytes':sum(m['bytes'] for m in members),'bytes':(OUT/'originals.tar.gz').stat().st_size,'sha256':sha((OUT/'originals.tar.gz').read_bytes())}})
(OUT/'collection.py').write_bytes(Path(__file__).read_bytes())
(OUT/'README.md').write_text('# Native import PM research\n\nOriginal failures remain failed. New official-Linux USBIP control at clean84045dfb deliberately expects no HID events under auto/2000ms/suspended, exact events after only imported delay=-1, and exact events after explicit control=auto. Actual host/QEMU exit0 and frozen guards pass. This is diagnosis, not a production fix, full ARM matrix or independently sampled per-stage generation receipt. Fixed primary-source and ownership audits remain recommendations until implementation/validation. Private keys/disks/firmware variables are excluded; exact public members are verified by read-back.\n')
write('manifest.json',{'files':[{'path':p.name,'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(OUT.iterdir()) if p.is_file() and p.name!='manifest.json']})
print(json.dumps({'members':len(members),'raw_bytes':sum(m['bytes'] for m in members),'bytes':(OUT/'originals.tar.gz').stat().st_size,'sha256':sha((OUT/'originals.tar.gz').read_bytes())}))
