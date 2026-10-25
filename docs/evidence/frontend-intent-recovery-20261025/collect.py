from pathlib import Path
import base64,hashlib,json,re,shutil,subprocess,tarfile
ROOT=Path('/home/ubuntu/github/usbip-client');OUT=ROOT/'docs/evidence/frontend-intent-recovery-20261025'
SOFTWARE=Path('/tmp/rustusb-frontend-recovery-software-20261025');MATRIX=Path('/tmp/rustusb-frontend-recovery-matrix-20261025')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def meta(p):return {'bytes':p.stat().st_size,'sha256':sha(p)}
def git(*a):return subprocess.check_output(['git',*a],cwd=ROOT)
def source_check(summary):
 for name,info in summary['source_inputs'].items():
  b=git('show',summary['head']+':'+name);assert len(b)==info['bytes']and hashlib.sha256(b).hexdigest()==info['sha256'],name
G=json.loads((SOFTWARE/'summary.json').read_text());assert G['result']=='PASS'and all(G['guards'].values())and G['head']=='f174f434f7e86659692226fcd9b7e43b87a57e15'
assert [(x['exit'],x['passed'],x['failed'],x['ignored'])for x in G['commands']]==[(0,1076,0,0),(0,18,0,0)]
V=json.loads((MATRIX/'summary.json').read_text());assert V['result']=='PASS'and len(V['runs'])==16 and all(x['exit']==0 for x in V['runs'])and all(V['guards'].values())
HEAD=V['head'];assert git('rev-parse','HEAD').decode().strip()==HEAD and not git('status','--porcelain').strip()
for n,info in G['inputs'].items():
 b=git('show',G['head']+':'+n);assert len(b)==info['bytes']and hashlib.sha256(b).hexdigest()==info['sha256']
changed=[n for n,m in G['inputs'].items()if meta(ROOT/n)!=m];assert set(changed)=={'scripts/qemu_frontend_recovery/run.py','scripts/qemu_frontend_recovery/guest.sh'}
for n,info in V['source_inputs'].items():
 b=git('show',HEAD+':'+n);assert len(b)==info['bytes']and hashlib.sha256(b).hexdigest()==info['sha256']and(ROOT/n).read_bytes()==b
for n,info in G['binaries'].items():assert meta(ROOT/('apps/rustusb-gui/target/release/rustusb-gui'if n=='rustusb-gui'else'target/release/'+n))==info
roots={x['name']:Path(x['evidence'])for x in V['runs']};runs={};guests=0;native=[];ptys=0;supplement={}
for name,p in roots.items():
 s=json.loads((p/'summary.json').read_text());assert s['result']=='PASS'and s['head']==HEAD and s.get('forced_termination')in [[],False]
 source_check(s)
 if name=='gui-single':
  assert s['qemu_wait']==0 and all(s['guards'].values());guests+=1
  assert (p/'guest-artifacts/gui-wait.txt').read_text().strip()=='1'
  native.append({'run':name,'role':'single','label':'original-single','actual_exit':1,'scope':'original expected unresolved outcome; actual join retained'})
 else:
  assert s['guest_exit_codes']=={'server':0,'client':0}and all(s[k]for k in ['source_guard','binary_guard','head_guard','clean_guard'])
  if not name.startswith('tui-'):assert s['gui_binary_guard']and s['driver_guard']
  assert s['binary_sha256']=={n:m['sha256']for n,m in G['binaries'].items()if n!='rustusb-gui'};guests+=2
  for role in ['client','server']:
   d=p/(role+'-artifacts')
   for q in sorted(d.glob('gui-*.wait')):
    if q.with_suffix('.pid').exists():native.append({'run':name,'role':role,'label':q.stem,'actual_exit':int(q.read_text().strip())})
 facts=s.get('facts',{});ptys+=sum(x.get('pty_children',0)for x in facts.values()if isinstance(x,dict))
 if name in ['new-ipv4','new-ipv6']:
  assert facts['frontend_process_recovery']['assertion_count']==78 and facts['frontend_process_recovery']['planned_native_kill137']==4
  d=p/'client-artifacts';actor=(d/'recovery-start-agent-stopped.txt').read_text();assert re.search(r'^State:\s+T',actor,re.M)and re.search(r'^Uid:\s+2000\s+2000\s+2000\s+2000$',actor,re.M)
  state=json.loads((d/'recovery-start-pending-document.json').read_text());manifest=json.loads((d/'actual-config-1-agent.json').read_text());expected=hashlib.sha256(b'rustusb-frontend-recovery-v1\0'+(manifest['control_directory']+'/control.sock').encode()).hexdigest();assert state['endpoint']==expected and state['broker_uid']==manifest['broker_uid']
  for role,tag in [('client','start'),('server','rule-positive'),('server','rule-negative')]:
   a=p/(role+'-artifacts');saved=json.loads((a/('recovery-'+tag+'-pending-document.json')).read_text());manifest=json.loads((a/'actual-config-1-agent.json').read_text());assert saved['endpoint']==hashlib.sha256(b'rustusb-frontend-recovery-v1\0'+(manifest['control_directory']+'/control.sock').encode()).hexdigest()
  supplement[name]={'actual_broker_stopped_state_T_and_uid2000':True,'all_pending_endpoint_digests_match_actual_public_manifest':True,'scope':'Read-only same-author audit of original captured proc/public-manifest/private-local-record bytes; no rerun or original verdict change.'}
 runs[name]={'head':s['head'],'input_count':len(s['source_inputs']),'facts':facts,'verification':s.get('verification'),'actual_guest_exits':s.get('guest_exit_codes',{'single':s.get('qemu_wait')})}
assert guests==31 and ptys==96 and len(native)==60
assert sum(x['actual_exit']==0 for x in native)==51 and sum(x['actual_exit']==137 for x in native)==8 and sum(x['actual_exit']==1 for x in native)==1,native
failures={}
for label,path,head in [('failed-01','/tmp/rustusb-frontend-recovery-new-ipv4-20261025',G['head']),('failed-02','/tmp/rustusb-frontend-recovery-new-ipv4-02-20261025','f717b0c0')]:
 p=Path(path);s=json.loads((p/'summary.json').read_text());assert s['result']=='FAIL'and s['head'].startswith(head)and s['guest_exit_codes']=={'server':0,'client':0}and not s['forced_termination']and all(s[k]for k in ['source_guard','binary_guard','head_guard','clean_guard','gui_binary_guard','driver_guard']);source_check(s);roots[label]=p;failures[label]={'head':s['head'],'verdict':'FAIL','error':s['error'],'guest_exits':s['guest_exit_codes'],'forced_termination':s['forced_termination'],'scope':'Readonly-window helper incorrectly required enabled Start; retained original failure, not reclassified by later success.'}
cli=Path('/tmp/rustusb-frontend-recovery-cli-legacy-20261025');cs=json.loads((cli/'summary.json').read_text());assert cs['result']=='PASS'and cs['elf_guard']and cs['elf_sha256']==G['binaries']['rustusb']['sha256']and [x['actual_exit']for x in cs['cases']]==[1,1,1,0,0];roots['legacy-cli-metadata']=cli
OUT.mkdir(exist_ok=False)
for name in ['summary.json','core.log','gui.log','preflight.json','decision-preflight.json','generated-decision-init.sh','recovery-preflight.json','generated-recovery-init.sh']:shutil.copyfile(SOFTWARE/name,OUT/name)
for src,name in [('/tmp/run-rustusb-frontend-recovery-gates-20261025.py','run-gates.py'),('/tmp/run-rustusb-frontend-recovery-matrix-20261025.py','run-matrix.py'),('/tmp/rustusb-frontend-recovery-dependency-review-20261025.json','dependency-review.json'),(__file__,'collect.py')]:shutil.copyfile(src,OUT/name)
for pattern in ['rustusb-frontend-recovery-check-dev-*.log','rustusb-frontend-recovery-test-dev-*.log','rustusb-frontend-recovery-gui-dev-*.log','rustusb-frontend-recovery-clippy-dev-*.log','rustusb-frontend-recovery-*-host-20261025.log']:
 for p in Path('/tmp').glob(pattern):shutil.copyfile(p,OUT/p.name)
for name,p in roots.items():
 d=OUT/'summaries'/(name+'.json');d.parent.mkdir(exist_ok=True);shutil.copyfile(p/'summary.json',d)
for epoch,name in [(1,'native-recovered-rules.png'),(2,'native-recovered-deleted.png')]:
 b=base64.b64decode(json.loads((roots['new-ipv4']/'server-artifacts'/f'gui-rule-screenshot-{epoch}.json').read_text())['value'],validate=True);assert b.startswith(b'\x89PNG\r\n\x1a\n');(OUT/name).write_bytes(b)
(OUT/'supplemental-originals-review.json').write_text(json.dumps(supplement,indent=2)+'\n')
(OUT/'native-processes.json').write_text(json.dumps(native,indent=2)+'\n')
files={};archive=OUT/'original-artifacts.tar.gz'
with tarfile.open(archive,'w:gz')as tar:
 for name,base in roots.items():
  for p in sorted(base.rglob('*')):
   if not p.is_file():continue
   assert not p.is_symlink()and p.name not in ['rustusb','rustusb-helper','rustusb-gui']and not p.name.endswith('-key.der')and '.private'not in p.parts and p.read_bytes()[:4]!=b'\x7fELF',p
   member=name+'/'+str(p.relative_to(base));files[member]=meta(p);tar.add(p,arcname=member,recursive=False)
 for p in sorted(MATRIX.iterdir()):
  if p.is_file():member='matrix/'+p.name;files[member]=meta(p);tar.add(p,arcname=member,recursive=False)
with tarfile.open(archive)as tar:
 assert len(tar.getmembers())==len(files)
 for m in tar.getmembers():
  b=tar.extractfile(m).read();assert {'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}==files[m.name]
(OUT/'archive-manifest.json').write_text(json.dumps({'archive':meta(archive),'files':files},indent=2)+'\n')
result={'result':'PASS','software_head':G['head'],'qemu_head':HEAD,'changed_fixture_inputs_since_gate':changed,'software':G['commands'],'gate_inputs':len(G['inputs']),'binaries':G['binaries'],'final_qemu_invocations':16,'final_actual_guest_exit0':guests,'native_gui_children':len(native),'cooperative_native_exit0':51,'planned_native_kill137':8,'original_single_expected_unresolved_exit1':1,'actual_tui_pty_children_exit0':ptys,'original_failed_qemu_runs':failures,'forced_termination':[],'legacy_cli':cs,'runs':runs,'archive_files':len(files),'archive_uncompressed_bytes':sum(x['bytes']for x in files.values()),'archive':meta(archive),'scope':'Actual whole native process crashes/restarts, both UUID namespaces, original typed rule protocol/payload/last draft and exact query; private writer failures/refusal. Original Native264/I/O/Root proofs, rule/action/v2/single and full shared-TUI regressions retained. No full phase, power-loss durability, general USB crash recovery, hardware, dynamic pools or installation acceptance.'}
(OUT/'validation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:result[k]for k in ['result','software_head','qemu_head','final_qemu_invocations','final_actual_guest_exit0','native_gui_children','actual_tui_pty_children_exit0','archive_files','archive_uncompressed_bytes','archive']}))
