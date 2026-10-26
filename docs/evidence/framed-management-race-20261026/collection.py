"""Explicit E60 public evidence; preserve negatives and source attribution."""
from pathlib import Path
import datetime,gzip,hashlib,io,json,re,subprocess,tarfile
ROOT=Path('/home/ubuntu/github/usbip-client')
OUT=ROOT/'docs/evidence/framed-management-race-20261026'
RESEARCH=Path('/tmp/rustusb-management-race-20261025')
def sha(b):return hashlib.sha256(b).hexdigest()
def load(p):return json.loads(p.read_text())
def git(*args,cwd=ROOT):return subprocess.check_output(['git',*args],cwd=cwd,text=True).strip()
def write(n,d):(OUT/n).write_text(json.dumps(d,indent=2)+'\n')
assert not git('status','--porcelain')
OUT.mkdir()
raw={}
def add(n,p):
 assert n not in raw and p.is_file() and not p.is_symlink(),(n,p)
 raw[n]=p.read_bytes()
def tree(n,p):
 for f in sorted(p.rglob('*')):
  if not f.is_file() or f.is_symlink() or '.private' in f.parts or '__pycache__' in f.parts:continue
  assert f.suffix in ['.json','.jsonl','.log','.txt','.stdout','.stderr','.pcap','.der','.py'],f
  add(n+'/'+str(f.relative_to(p)),f)
runs=[Path('/tmp/rustusb-management-race-'+x+'-20261026') for x in ['native-v4','native-v6','usbfs-v4','usbfs-v6','native-v4-old-to-new','coordinated-v6-to-v10']]
matrix=[]
for p in runs:
 d=load(p/'summary.json');assert d['qemu_exits']=={'server':0,'client':0}
 assert all(d[k] for k in ['head_guard','clean_guard','frozen_file_guard'])
 assert d['passed']==('old-to-new' not in p.name)
 matrix.append({'run':p.name,**d});tree('runs/'+p.name,p);add('runs/'+p.name+'/host.log',p.with_suffix('.log'))
tree('development',RESEARCH)
gates=Path('/tmp/rustusb-management-race-final-software-20261026')
tree('software/final',gates);tree('software/failed-candidate',Path('/tmp/rustusb-management-race-software-20261025'))
final=load(gates/'summary.json');assert final['result']=='PASS' and all(final['guards'].values()) and len(final['inputs'])==722
for p in sorted(Path('/tmp').glob('*management-race*.py')):add('methods/'+p.name,p)
for v in [9,10]:
 folder=Path('/tmp/rustusb-debs-v'+str(v)+'-20261026')
 add('package-builds/v'+str(v)+'.json',next(folder.glob('build-*.json')))
add('package-builds/v6.json',next(Path('/tmp/rustusb-debs-v6-20261025').glob('build-*.json')))
cohorts={
 'b3cdd482':['initial-concurrent.log'], 'fededbe2':['initial-delayed-read.log'],
 '41540391':['fixed-delayed-read.log','fixed-transport-suite.log'], '800f94c9':['graceful-transport-suite.log'],
 '980445be':['final-transport-suite.log'], '5d265aa7':['diagnostic-half-close.log'],
 'da111ead':['diagnostic-transport-suite.log','diagnostic-positive-only.log'],
 '805c267a':['retained-transport-suite.log'],'d6707a77':['retained-noerror-transport-suite.log'],
 'd329fe32':['owned-close-transport-suite.log','stability/summary.json'],
 'b9fc7a6c':['active-grace-transport-suite.log','active-grace-concurrent.log'],
 '7c45dadf':['active-grace-typed-transport-suite.log','active-grace-typed-concurrent.log','active-stability/summary.json'],
 '530c93ab':['management-close-stage-diagnostic.log'],
 '15c8d099':['peer-drain-rebuilt-transport-suite.log'],
 'c89dec61':['final-peer-drain-transport-suite.log','final-peer-drain-concurrent.log','peer-drain-stability/summary.json']}
source_files=['crates/rustusb-transport/src/close.rs','crates/rustusb-transport/src/lib.rs','crates/rustusb-transport/src/link.rs','crates/rustusb-transport/src/link/close_tests.rs','crates/rustusb-transport/src/link/tests.rs','crates/rustusb-transport/src/tls/tests/link.rs','crates/rustusb-transport/src/tls/tests/close.rs','crates/rustusb-transport/src/tls/tests.rs','crates/rustusb-service/src/jobs/management_tests.rs','crates/rustusb-service/src/jobs/link.rs']
mapping=[]
for prefix,logs in cohorts.items():
 head=git('rev-parse',prefix);mapping.append({'head':head,'logs':logs})
 for name in source_files:
  if subprocess.run(['git','cat-file','-e',head+':'+name],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:
   raw['sources/'+head+'/'+name]=subprocess.check_output(['git','show',head+':'+name],cwd=ROOT)
for head in {final['head'],*(r['head'] for r in matrix)}:
 for name in git('ls-tree','-r','--name-only',head,'scripts/qemu_installed_usb').splitlines():
  raw['sources/'+head+'/'+name]=subprocess.check_output(['git','show',head+':'+name],cwd=ROOT)
control=Path('/tmp/rustusb-management-race-old-control-20261025')
file='crates/rustusb-transport/src/tls/tests/link.rs'
assert git('status','--porcelain',cwd=control).splitlines()==['M '+file] # strip removes leading status space
assert (control/file).read_bytes()==(ROOT/file).read_bytes()
add('control/exact-test-overlay.rs',control/file)
log=(RESEARCH/'isolated-old-control-retention.log').read_text()
assert str(control) in log and 'peer owner ended before actual final read' in log and '0 passed; 1 failed' in log
control_receipt={'head':git('rev-parse','HEAD',cwd=control),'dirty_test_overlay':[file],'overlay_sha256':sha((control/file).read_bytes()),'current_test_identical':True,'isolated_target':'/tmp/rustusb-management-race-old-target-20261025','actual_exit':101,'not_a_clean_control_build':True,'verbose_source_path_verified':True}
raw['control/isolated-receipt.json']=(json.dumps(control_receipt,indent=2)+'\n').encode()
secrets=[]
for folder in runs:
 for p in folder.rglob('*'):
  if p.is_file() and (p.name=='ssh-key' or p.name.endswith(('-key.der','-key.pem'))):secrets.append(p.read_bytes())
for n,b in raw.items():
 assert not any(secret and secret in b for secret in secrets),'private key in '+n
 assert b'-----BEGIN '+b'PRIVATE KEY-----' not in b and b'-----BEGIN '+b'OPENSSH PRIVATE KEY-----' not in b,n
 assert not re.search(rb'https://[^\s"<>]+[?&](?:sig|se)=',b),n
members=[{'path':n,'bytes':len(b),'sha256':sha(b)} for n,b in sorted(raw.items())]
with (OUT/'originals.tar.gz').open('wb') as stream:
 with gzip.GzipFile(fileobj=stream,mode='wb',mtime=0,filename='') as compressed:
  with tarfile.open(fileobj=compressed,mode='w|') as tar:
   for n,b in sorted(raw.items()):
    info=tarfile.TarInfo(n);info.size=len(b);info.mode=0o644;info.mtime=0;tar.addfile(info,io.BytesIO(b))
with tarfile.open(OUT/'originals.tar.gz','r:gz') as tar:
 assert len(tar.getmembers())==len(members)
 for m in members:
  b=tar.extractfile(m['path']).read();assert len(b)==m['bytes'] and sha(b)==m['sha256']
write('originals-manifest.json',{'members':members,'member_count':len(members),'raw_bytes':sum(x['bytes'] for x in members)})
write('summary.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'paired_runs':6,'passed':5,'failed_retained':1,'actual_qemu_exits0':12,'passing_io_rounds':15,'passing_root_proofs':70,'matrix':matrix,'software':{k:v for k,v in final.items() if k!='inputs'},'software_input_count':722,'development_source_map':mapping,'invalid_current_source_attribution':['development/peer-drain-transport-suite.log','development/peer-drain-concurrent.log'],'isolated_old_control':control_receipt,'archive':{'sha256':sha((OUT/'originals.tar.gz').read_bytes()),'bytes':(OUT/'originals.tar.gz').stat().st_size,'members':len(members)},'privacy':{'exact_private_keys_checked':len(secrets),'private_disks_keys_debs_elfs_archived':False,'archive_roundtrip':True}})
(OUT/'collection.py').write_bytes(Path(__file__).read_bytes())
(OUT/'README.md').write_text('# Framed management close evidence\n\nPublic originals, failed candidates, isolated old-source control, fixed-round stability, both complete software gates, package receipts and all six installed QEMU pairs are retained in originals.tar.gz. originals-manifest.json gives every exact member hash/size. Five paired operations pass; mixed-version migration remains failed. Private keys, disks, debs and ELFs are excluded. Development logs with contaminated build attribution are explicitly excluded from acceptance. Source identities and tests are separate from hardware compatibility. See ../../research/framed-management-race-validation.md for scope and corrections.\n')
write('manifest.json',{'files':[{'path':str(p.relative_to(OUT)),'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='manifest.json']})
print(json.dumps({'members':len(members),'raw_bytes':sum(x['bytes'] for x in members),'archive_bytes':(OUT/'originals.tar.gz').stat().st_size,'archive_sha256':sha((OUT/'originals.tar.gz').read_bytes()),'private_keys_checked':len(secrets)}))
