from pathlib import Path
import hashlib,json,shutil,tarfile,subprocess
ROOT=Path('/home/ubuntu/github/usbip-client');OUT=ROOT/'docs/evidence/automatic-sharing-runtime-20261025'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def meta(p):return {'bytes':p.stat().st_size,'sha256':sha(p)}
software=Path('/tmp/rustusb-automatic-rules-software-20261025');gate=json.loads((software/'summary.json').read_text());head=gate['head']
assert head=='20a15a016f9174d8dee3e46cc093b92f0e1a782c' and gate['result']=='PASS' and all(gate['guards'].values())
assert [(v['exit'],v['passed'],v['failed'],v['ignored'])for v in gate['commands']]==[(0,1019,0,0),(0,11,0,0)]
for name,m in gate['binaries'].items():
 p=ROOT/('apps/rustusb-gui/target/release/rustusb-gui'if name=='rustusb-gui'else'target/release/'+name);assert meta(p)==m
OUT.mkdir(exist_ok=False)
for n in ['summary.json','core.log','gui.log']:shutil.copyfile(software/n,OUT/n)
shutil.copyfile('/tmp/run-rustusb-automatic-rules-gates-20261025.py',OUT/'run-gates.py');shutil.copyfile(__file__,OUT/'collect.py')
for p in Path('/tmp/rustusb-automatic-rules-development-20261025').glob('*.log'):
 d=OUT/'development'/p.name;d.parent.mkdir(exist_ok=True);shutil.copyfile(p,d)
roots={
 'automatic-native-ipv4':Path('/tmp/rustusb-automatic-rules-qemu-first-ipv4-20261025'),
 'automatic-native-ipv6':Path('/tmp/rustusb-automatic-rules-qemu-ipv6-20261025'),
 'original-agent-ipv4':Path('/tmp/rustusb-automatic-rules-original-agent-ipv4-20261025'),
 'original-v2-ipv4':Path('/tmp/rustusb-automatic-rules-original-v2-ipv4-20261025'),
 'cli':Path('/tmp/rustusb-automatic-rules-cli-20261025'),
 'signals':Path('/tmp/rustusb-automatic-rules-signals-20261025')}
runs={}
for name,p in roots.items():
 v=json.loads((p/'summary.json').read_text());assert v['result']=='PASS';runs[name]=v
 if name not in ['cli','signals']:
  assert v['head']==head and all(v[k]for k in ['source_guard','binary_guard','head_guard','clean_guard']) and v['guest_exit_codes']=={'server':0,'client':0} and v['forced_termination']==[]
 elif name=='cli':assert v['elf_guard'] and len(v['cases'])==5 and not v.get('forced_test_cleanup')
 target=OUT/'summaries'/(name+'.json');target.parent.mkdir(exist_ok=True);shutil.copyfile(p/'summary.json',target)
files={}
archive=OUT/'original-artifacts.tar.gz'
with tarfile.open(archive,'w:gz')as tar:
 for name,base in roots.items():
  for p in sorted(base.rglob('*')):
   if not p.is_file():continue
   assert not p.is_symlink(),p
   assert p.name not in ['rustusb','rustusb-helper','rustusb-gui'] and not p.name.endswith('-key.der') and '.private'not in p.parts,p
   dest=name+'/'+str(p.relative_to(base));files[dest]=meta(p);tar.add(p,arcname=dest,recursive=False)
 for p in [Path('/tmp/rustusb-automatic-rules-backpressure-20261025.log'),Path('/tmp/rustusb-automatic-rules-signals-20261025.stdout')]:
  dest='local/'+p.name;files[dest]=meta(p);tar.add(p,arcname=dest,recursive=False)
with tarfile.open(archive)as tar:
 members=tar.getmembers();assert len(members)==len(files)
 for member in members:
  data=tar.extractfile(member).read();assert {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}==files[member.name]
(OUT/'archive-manifest.json').write_text(json.dumps({'archive':meta(archive),'files':files},indent=2)+'\n')
summary={'result':'PASS','tested_head':head,'software':gate['commands'],'gate_inputs':len(gate['inputs']),'binaries':gate['binaries'],'qemu_invocations':4,'actual_guest_exits':[0]*8,'forced_termination':[],'qemu_original_assertions':[runs[n]['facts']['assertion_count']for n in ['automatic-native-ipv4','automatic-native-ipv6','original-agent-ipv4']],'automatic_assertions_each':17,'qemu_io_rounds':{'automatic-native-ipv4':3,'automatic-native-ipv6':3,'original-agent-ipv4':3,'original-v2-ipv4':7},'archive_files':len(files),'archive_uncompressed_bytes':sum(v['bytes']for v in files.values()),'archive':meta(archive),'scope':'Fixed-pool Native local automatic Start on IPv4/IPv6, original no-policy agent/v2 regressions; local software/CLI only for other automation paths. No new GUI window, virtual/USBFS automatic QEMU, physical remote receipt, dynamic pool, installation, hardware or Windows acceptance.'}
(OUT/'validation.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary))
