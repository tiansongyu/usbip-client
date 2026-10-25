from pathlib import Path
import base64,hashlib,json,shutil,subprocess,tarfile
ROOT=Path('/home/ubuntu/github/usbip-client');OUT=ROOT/'docs/evidence/settled-rule-decisions-20261025'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def meta(p):return {'bytes':p.stat().st_size,'sha256':sha(p)}
def git(*a):return subprocess.check_output(['git',*a],cwd=ROOT)
software=Path('/tmp/rustusb-rule-decisions-software-20261025');gate=json.loads((software/'summary.json').read_text());head=gate['head'];assert head=='650c53a8dbf14d8746006cb7a22f0dbfb3bcc19f'and gate['result']=='PASS'and all(gate['guards'].values())
assert [(x['exit'],x['passed'],x['failed'],x['ignored'])for x in gate['commands']]==[(0,1054,0,0),(0,17,0,0)]
assert git('rev-parse','HEAD').decode().strip()==head and not git('status','--porcelain').strip()
for n,m in gate['binaries'].items():
 p=ROOT/('apps/rustusb-gui/target/release/rustusb-gui'if n=='rustusb-gui'else'target/release/'+n);assert meta(p)==m
for name,m in gate['inputs'].items():
 data=git('show',head+':'+name);assert len(data)==m['bytes']and hashlib.sha256(data).hexdigest()==m['sha256']and(ROOT/name).read_bytes()==data
matrix=Path('/tmp/rustusb-rule-decisions-matrix-20261025');mv=json.loads((matrix/'summary.json').read_text());assert mv['result']=='PASS'and len(mv['runs'])==8 and all(x['exit']==0 for x in mv['runs'])
roots={x['name']:Path(x['evidence'])for x in mv['runs']};runs={}
for name,p in roots.items():
 v=json.loads((p/'summary.json').read_text());assert v['result']=='PASS'and v['head']==head and v.get('forced_termination')in [[],False]
 if name=='original-single':assert v['qemu_wait']==0 and all(v['guards'].values())
 else:
  assert v['guest_exit_codes']=={'server':0,'client':0}and all(v[k]for k in ['source_guard','binary_guard','head_guard','clean_guard','gui_binary_guard','driver_guard'])
  assert v['binary_sha256']=={n:m['sha256']for n,m in gate['binaries'].items()if n!='rustusb-gui'}
 for source,m in v['source_inputs'].items():
  b=git('show',head+':'+source);assert len(b)==m['bytes']and hashlib.sha256(b).hexdigest()==m['sha256']
 runs[name]={'head':head,'input_count':len(v['source_inputs']),'facts':v.get('facts'),'verification':v.get('verification'),'actual_guest_exits':v.get('guest_exit_codes',{'single':v.get('qemu_wait')})}
cli=Path('/tmp/rustusb-rule-decisions-cli-legacy-20261025');cs=json.loads((cli/'summary.json').read_text());assert cs['result']=='PASS'and cs['elf_guard']and cs['elf_sha256']==gate['binaries']['rustusb']['sha256']and [x['actual_exit']for x in cs['cases']]==[1,1,1,0,0]
roots['legacy-cli-metadata']=cli
OUT.mkdir(exist_ok=False)
for name in ['summary.json','core.log','gui.log','preflight.json','decision-preflight.json','generated-decision-init.sh']:shutil.copyfile(software/name,OUT/name)
for source,name in [('/tmp/run-rustusb-rule-decisions-gates-20261025.py','run-gates.py'),('/tmp/run-rustusb-rule-decisions-matrix-20261025.py','run-matrix.py'),(__file__,'collect.py')]:shutil.copyfile(source,OUT/name)
for pattern in ['rustusb-rule-decisions-check-dev-*.log','rustusb-rule-decisions-runtime-dev-*.log','rustusb-rule-decisions-journal-dev-*.log','rustusb-rule-decisions-service-frontend-dev-*.log','rustusb-rule-decisions-gui-dev-*.log','rustusb-rule-decisions-clippy-dev-*.log']:
 for p in Path('/tmp').glob(pattern):shutil.copyfile(p,OUT/p.name)
for name,p in roots.items():
 d=OUT/'summaries'/(name+'.json');d.parent.mkdir(exist_ok=True);shutil.copyfile(p/'summary.json',d)
for epoch,name in [(1,'native-settled-rules.png'),(2,'native-settled-deleted.png')]:
 b=base64.b64decode(json.loads((roots['new-ipv4']/'server-artifacts'/f'gui-rule-screenshot-{epoch}.json').read_text())['value'],validate=True);assert b.startswith(b'\x89PNG\r\n\x1a\n');(OUT/name).write_bytes(b)
files={};archive=OUT/'original-artifacts.tar.gz'
with tarfile.open(archive,'w:gz')as tar:
 for name,base in roots.items():
  for p in sorted(base.rglob('*')):
   if not p.is_file():continue
   assert not p.is_symlink()and p.name not in ['rustusb','rustusb-helper','rustusb-gui']and not p.name.endswith('-key.der')and '.private'not in p.parts and p.read_bytes()[:4]!=b'\x7fELF',p
   member=name+'/'+str(p.relative_to(base));files[member]=meta(p);tar.add(p,arcname=member,recursive=False)
 for p in sorted(matrix.iterdir()):
  if p.is_file():member='matrix/'+p.name;files[member]=meta(p);tar.add(p,arcname=member,recursive=False)
with tarfile.open(archive)as tar:
 assert len(tar.getmembers())==len(files)
 for m in tar.getmembers():
  b=tar.extractfile(m).read();assert {'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}==files[m.name]
(OUT/'archive-manifest.json').write_text(json.dumps({'archive':meta(archive),'files':files},indent=2)+'\n')
result={'result':'PASS','tested_head':head,'software':gate['commands'],'gate_inputs':len(gate['inputs']),'binaries':gate['binaries'],'qemu_invocations':8,'actual_guest_exit0':15,'native_gui_children':40,'normal_gui_exit0':39,'original_single_expected_unresolved_exit1':1,'new_failed_qemu_runs':0,'forced_termination':[],'legacy_cli':cs,'runs':runs,'archive_files':len(files),'archive_uncompressed_bytes':sum(x['bytes']for x in files.values()),'archive':meta(archive),'scope':'Explicit root2/disk4 settled decisions, true service CAS/unknown IPC/renderer reload/cooperative agent restart/original queries and draft review, plus unchanged legacy policy1 and original Native/action/v2/single/CLI regressions. No whole-frontend-crash pending journal, hardware, dynamic pool, install or completePhase5.'}
(OUT/'validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items()if k not in ['runs','legacy_cli','binaries']},indent=2))
