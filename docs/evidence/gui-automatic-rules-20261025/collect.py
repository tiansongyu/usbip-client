from pathlib import Path
import base64,hashlib,json,shutil,subprocess,tarfile
ROOT=Path('/home/ubuntu/github/usbip-client');OUT=ROOT/'docs/evidence/gui-automatic-rules-20261025'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def meta(p):return {'bytes':p.stat().st_size,'sha256':sha(p)}
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)
software=Path('/tmp/rustusb-gui-rules-software-20261025');gate=json.loads((software/'summary.json').read_text());software_head=gate['head'];qemu_head=git('rev-parse','HEAD').decode().strip()
assert gate['result']=='PASS'and all(gate['guards'].values()) and [(x['exit'],x['passed'],x['failed'],x['ignored'])for x in gate['commands']]==[(0,1027,0,0),(0,15,0,0)]
assert software_head=='7ef7f4923dc97bef98a971b1fc248e36cf670a67'and qemu_head.startswith('e668c045')
assert not git('diff',software_head,qemu_head,'--','crates','apps','Cargo.toml','Cargo.lock')
for n,m in gate['binaries'].items():
 p=ROOT/('apps/rustusb-gui/target/release/rustusb-gui'if n=='rustusb-gui'else'target/release/'+n);assert meta(p)==m
for name,m in gate['inputs'].items():
 data=git('show',software_head+':'+name);assert len(data)==m['bytes']and hashlib.sha256(data).hexdigest()==m['sha256']
matrix=Path('/tmp/rustusb-gui-rules-matrix-03-20261025');mv=json.loads((matrix/'summary.json').read_text());assert mv['result']=='PASS'and len(mv['runs'])==6 and all(x['exit']==0 for x in mv['runs'])
roots={'failed-dom-ipv4':Path('/tmp/rustusb-gui-rules-new-ipv4-20261025'),'failed-verifier-ipv4':Path('/tmp/rustusb-gui-rules-new-ipv4-02-20261025')}
roots.update({x['name']:Path(x['evidence'])for x in mv['runs']})
runs={}
for name,p in roots.items():
 v=json.loads((p/'summary.json').read_text());failed=name.startswith('failed-');assert v['result']==('FAIL'if failed else'PASS')
 assert v.get('forced_termination')in [[],False]
 if name=='original-single':
  assert v['qemu_wait']==0
  assert all(v['guards'].values())
 else:
  assert v['guest_exit_codes']=={'server':0,'client':0}and all(v[k]for k in ['source_guard','binary_guard','head_guard','clean_guard','gui_binary_guard','driver_guard'])
  assert v['binary_sha256']=={n:m['sha256']for n,m in gate['binaries'].items()if n!='rustusb-gui'}
 if not failed:assert v['head']==qemu_head
 for source,m in v['source_inputs'].items():
  b=git('show',v['head']+':'+source);assert len(b)==m['bytes']and hashlib.sha256(b).hexdigest()==m['sha256'],(name,source)
 runs[name]={'result':v['result'],'head':v['head'],'input_count':len(v['source_inputs']),'error':v.get('error'),'facts':v.get('facts'), 'binaries':v.get('binaries'),'guest_exit_codes':v.get('guest_exit_codes'),'single_guest_exit':v.get('qemu_wait'),'verification':v.get('verification')}
OUT.mkdir(exist_ok=False)
for name in ['summary.json','core.log','gui.log','preflight.json']:shutil.copyfile(software/name,OUT/name)
for source,name in [('/tmp/run-rustusb-gui-rules-gates-20261025.py','run-gates.py'),('/tmp/run-rustusb-gui-rules-matrix-20261025.py','run-matrix-01.py'),('/tmp/run-rustusb-gui-rules-matrix-02-20261025.py','run-matrix-02.py'),('/tmp/run-rustusb-gui-rules-matrix-03-20261025.py','run-matrix-03.py'),(__file__,'collect.py')]:shutil.copyfile(source,OUT/name)
for source in ['/tmp/rustusb-gui-rules-frontend-dev-01-20261025.log','/tmp/rustusb-gui-rules-bridge-dev-02-20261025.log','/tmp/rustusb-gui-rules-clippy-dev-01-20261025.log','/tmp/rustusb-gui-rules-second-verifier-diagnostic-20261025.log','/tmp/rustusb-gui-rules-second-verifier-review-20261025.log']:
 p=Path(source);shutil.copyfile(p,OUT/p.name)
for name,p in roots.items():
 d=OUT/'summaries'/(name+'.json');d.parent.mkdir(exist_ok=True);shutil.copyfile(p/'summary.json',d)
for epoch,name in [(1,'native-rules.png'),(2,'native-rules-deleted.png')]:
 p=roots['new-ipv4']/'server-artifacts'/f'gui-rule-screenshot-{epoch}.json';b=base64.b64decode(json.loads(p.read_text())['value'],validate=True);assert b.startswith(b'\x89PNG\r\n\x1a\n');(OUT/name).write_bytes(b)
archive=OUT/'original-artifacts.tar.gz';files={}
with tarfile.open(archive,'w:gz')as tar:
 for name,base in roots.items():
  for p in sorted(base.rglob('*')):
   if not p.is_file():continue
   assert not p.is_symlink()and p.name not in ['rustusb','rustusb-helper','rustusb-gui']and not p.name.endswith('-key.der')and '.private'not in p.parts,p
   assert p.read_bytes()[:4]!=b'\x7fELF',p
   item=name+'/'+str(p.relative_to(base));files[item]=meta(p);tar.add(p,arcname=item,recursive=False)
 for name,base in [('matrix-01',Path('/tmp/rustusb-gui-rules-matrix-20261025')),('matrix-02',Path('/tmp/rustusb-gui-rules-matrix-02-20261025')),('matrix-03',matrix)]:
  for p in sorted(base.iterdir()):
   if p.is_file():item=name+'/'+p.name;files[item]=meta(p);tar.add(p,arcname=item,recursive=False)
with tarfile.open(archive)as tar:
 assert len(tar.getmembers())==len(files)
 for m in tar.getmembers():
  b=tar.extractfile(m).read();assert {'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}==files[m.name]
(OUT/'archive-manifest.json').write_text(json.dumps({'archive':meta(archive),'files':files},indent=2)+'\n')
result={'result':'PASS','software_head':software_head,'qemu_head':qemu_head,'product_source_identical_between_heads':True,'software':gate['commands'],'gate_inputs':len(gate['inputs']),'binaries':gate['binaries'],'final_qemu_invocations':6,'final_actual_guest_exit0':11,'failed_qemu_invocations':2,'failed_actual_guest_exit0':4,'failed_runs_remain_failed':True,'forced_termination':[],'runs':runs,'archive_files':len(files),'archive_uncompressed_bytes':sum(x['bytes']for x in files.values()),'archive':meta(archive),'scope':'Native GUI fixed-profile rules, true unknown IPC outcome/renderer reload/original query, CRUD and independent actual jobs/cleanup; original actions, v2 link and single GUI regressions. No settled-negative recovery, crash-persistent UUIDs, dynamic pool, physical remote receipt, installation/hardware/fullPhase5.'}
(OUT/'validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items()if k not in ['runs','binaries']},indent=2))
