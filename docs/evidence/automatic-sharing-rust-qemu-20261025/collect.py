from pathlib import Path
import hashlib,json,shutil,tarfile,subprocess
ROOT=Path('/home/ubuntu/github/usbip-client');OUT=ROOT/'docs/evidence/automatic-sharing-rust-qemu-20261025'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def meta(p):return {'bytes':p.stat().st_size,'sha256':sha(p)}
software=Path('/tmp/rustusb-automatic-rust-software-20261025');gate=json.loads((software/'summary.json').read_text());head=gate['head'];assert head=='42e98ab36cb974022b49588055c3f0407a5f1b18'and gate['result']=='PASS'and all(gate['guards'].values())
assert [(v['exit'],v['passed'],v['failed'],v['ignored'])for v in gate['commands']]==[(0,1019,0,0),(0,11,0,0)]and gate['preflight']['schema_case_count']==50
assert not subprocess.check_output(['git','diff','20a15a01',head,'--','crates','apps','Cargo.toml','Cargo.lock'],cwd=ROOT)
for n,m in gate['binaries'].items():
 p=ROOT/('apps/rustusb-gui/target/release/rustusb-gui'if n=='rustusb-gui'else'target/release/'+n);assert meta(p)==m
OUT.mkdir(exist_ok=False)
for n in ['summary.json','core.log','gui.log','preflight.json']:shutil.copyfile(software/n,OUT/n)
shutil.copyfile('/tmp/run-rustusb-automatic-rust-gates-20261025.py',OUT/'run-gates.py');shutil.copyfile(__file__,OUT/'collect.py')
roots={n:Path('/tmp/rustusb-automatic-rust-'+n+'-20261025')for n in ['virtual-first-ipv4','virtual-ipv6','usbfs-first-ipv4','usbfs-ipv6','original-virtual-ipv4','original-usbfs-ipv4','original-native-ipv4']}
runs={}
for n,p in roots.items():
 v=json.loads((p/'summary.json').read_text());assert v['head']==head and v['result']=='PASS'and all(v[k]for k in ['source_guard','binary_guard','head_guard','clean_guard'])and v['guest_exit_codes']=={'server':0,'client':0}and v['forced_termination']==[]
 assert v['binary_sha256']=={n:m['sha256']for n,m in gate['binaries'].items()if n!='rustusb-gui'}
 d=OUT/'summaries'/(n+'.json');d.parent.mkdir(exist_ok=True);shutil.copyfile(p/'summary.json',d);runs[n]={'head':v['head'],'input_count':len(v['source_inputs']),'io_rounds':v['facts']['io_rounds'],'automatic_jobs':v['facts'].get('automatic_rules',{}).get('automatic_jobs'),'automatic_assertions':v['facts'].get('automatic_rules',{}).get('assertion_count')}
files={};archive=OUT/'original-artifacts.tar.gz'
with tarfile.open(archive,'w:gz')as tar:
 for n,base in roots.items():
  for p in sorted(base.rglob('*')):
   if not p.is_file():continue
   assert not p.is_symlink()and not p.name.endswith('-key.der')and p.name not in ['rustusb','rustusb-helper','rustusb-gui']and '.private'not in p.parts,p
   name=n+'/'+str(p.relative_to(base));files[name]=meta(p);tar.add(p,arcname=name,recursive=False)
with tarfile.open(archive)as tar:
 members=tar.getmembers();assert len(members)==len(files)
 for m in members:
  b=tar.extractfile(m).read();assert {'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}==files[m.name]
(OUT/'archive-manifest.json').write_text(json.dumps({'archive':meta(archive),'files':files},indent=2)+'\n')
result={'result':'PASS','tested_head':head,'product_source_identical_to':'20a15a016f9174d8dee3e46cc093b92f0e1a782c','software':gate['commands'],'preflight_schema_cases':50,'generated_guest_syntax_cases':2,'gate_inputs':len(gate['inputs']),'binaries':gate['binaries'],'qemu_invocations':7,'actual_guest_exits':[0]*14,'forced_termination':[],'runs':runs,'archive_files':len(files),'archive_uncompressed_bytes':sum(v['bytes']for v in files.values()),'archive':meta(archive),'scope':'Virtual remote rules and USBFS local fixed-pool rules through original v2 faults/real I/O on both IP families, plus original no-policy virtual/USBFS and E53 Native auto regressions. No physical remote safety receipt, dynamic pool, GUI, installation, new hardware/Windows or pending-URB claim.'}
(OUT/'validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
