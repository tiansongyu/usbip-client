"""Read-only original-byte review, authored by the harness author, no runner imports."""
import base64,hashlib,json,re,struct
from pathlib import Path
E=Path('/tmp/rustusb-prepare-stop-native-resource-job-68cc3ad-20261024');O=Path('/tmp/rustusb-prepare-native-red-raw-review-20261024');O.mkdir(exist_ok=False)
checks=[];manifest={}
def sha(b):return hashlib.sha256(b).hexdigest()
def check(ok,name):
 checks.append({'check':name,'passed':bool(ok)})
 if not ok:raise RuntimeError(name)
def get(role,name):return (E/(role+'-artifacts')/name).read_bytes()
def obj(role,name):return json.loads(get(role,name))
def server(name):return obj('server',name)
summary=json.loads((E/'summary.json').read_text())
check(summary['result']=='FAIL' and bool(summary['completed_utc']),'original run remains terminal FAIL')
check(summary['guest_exit_codes']=={'server':0,'client':0},'both actual QEMU guest processes exited zero; separate from test failure')
check(all(summary[k] for k in ['source_guard','binary_guard','head_guard','clean_guard']),'original source/binary/head/clean guards all true')
for role in ['server','client']:
 raw=(E/(role+'-evidence.log')).read_bytes();text=raw.decode();pattern=re.compile(r'^SECURE_ARTIFACT_BEGIN ([a-zA-Z0-9_.-]+) ([0-9]+)\n(.*?)^SECURE_ARTIFACT_END \1\s*$',re.M|re.S);matches=list(pattern.finditer(text));names=set()
 check(text.count('SECURE_ARTIFACT_BEGIN ')==len(matches),'all '+role+' artifact frames complete')
 for match in matches:
  name,count,payload=match.groups();check(name not in names,'unique '+role+'/'+name);names.add(name);data=base64.b64decode(''.join(payload.split()),validate=True);path=E/(role+'-artifacts')/name
  check(len(data)==int(count) and data==path.read_bytes(),'exact serial base64 bytes '+role+'/'+name);manifest[str(path.relative_to(E))]={'bytes':len(data),'sha256':sha(data)}
  check(summary['guest_artifacts'][role][name]=={'bytes':len(data),'sha256':sha(data)},'original manifest matches '+role+'/'+name)
 kernel=(E/(role+'-serial.log')).read_text(errors='replace');check('Kernel panic' not in kernel and 'Oops:' not in kernel,'final full '+role+' S0 no panic/Oops; other warnings retained')
for name,fact in summary['source_inputs'].items():
 p=E/'source'/name;b=p.read_bytes();check(fact=={'bytes':len(b),'sha256':sha(b)},'frozen input bytes '+name);manifest[str(p.relative_to(E))]=fact
selected,previous=server('selected-state.json'),server('state-before-ack.json');selection=server('selection.json');sessions=server('selected-sessions.json');held=server('root-held.json');baseline=server('host-before.json')
check(len(sessions)==2 and len(held['exports'])==2 and held['records']==held['terminals']==[],'two actual accepted Native exports, no initial retirement')
check(previous['sequence']+1==selected['sequence'] and selected['audit'][-1]['action']=='resource','exact second Resource unpublished state.next transition')
check(sum(a['resource_id'] is not None for s in previous['sessions'] for a in s['attempts'])==1 and all(s['attempts'][0]['resource_id'] for s in sessions),'first receipt acknowledged; second received owner has pending application Resource ACK')
expected=[]
for s in sessions:
 a=s['attempts'][0];r=next(r for r in held['exports'] if r['lease_id']==a['resource_id']);check(r['correlation']==a['correlation'] and r['bound'] is not None and r['stub'] is None,'exact original held correlation/bound receipt '+s['id']);expected.append({'resource_id':a['resource_id'],'kind':'native_export','device_key':s['device_key'],'owner_uid':2000,'correlation':a['correlation'],'proof':'native_export_restored'})
observations=[json.loads(line) for line in get('server','held-observations.jsonl').splitlines()];check(bool(observations),'nonempty actual held interval')
for row in observations:
 i=row['observation'];root=server(f'root-observe-{i}.json');host=server(f'host-observe-{i}.json');check(root==held,'original two capabilities still active at observation '+str(i));check(host==server('host-held.json') and host!=baseline,'both original drivers still bound to usbip-host during observation '+str(i));check(row['exact_root_terminal'] is False and row['baseline_equal'] is False,'actual missing cleanup facts '+str(i))
check(server('root-before-release.json')==held,'both original resources still retained immediately before explicit gate release')
check(server('held-outcome.json')['failed']==1 and server('held-outcome.json')['resource_released_while_gate_held'] is False,'guest latches actual held-cleanup failure')
number=selection['candidate'];candidate=server(f'gate-candidate-{number}.json');before=server('gate-held-before.json');after=server('gate-held-after.json');exited=server(f'gate-exit-{number}.json');identity=['candidate','tracee_pid','tracee_starttime','tid','fd','request','file_device','file_inode']
check(all(candidate[k]==before[k]==after[k]==exited[k] for k in identity),'same real fsync TID/FD/inode and owner before/after selected hold and exit')
check(before['held'] is True and after['held'] is True and before['monotonic_ns']<after['monotonic_ns']<exited['monotonic_ns'] and exited['return_value']==0 and exited['is_error'] is False,'tracer independently confirms held through missing-cleanup observations, then successful original syscall')
events=[json.loads(line) for line in get('server','gate-events.jsonl').splitlines()];released=[e for e in events if e['event']=='released'];check(len(released)==1 and after['monotonic_ns']<released[0]['monotonic_ns']<exited['monotonic_ns'],'sole explicit release follows held-after')
check(events[-1]['event']=='finished' and events[-1]['tracee_exit_code']==0 and get('server','wait-tracer.txt')==b'0\n','original agent and tracer actually reaped after release')
restored=server('root-after-release.json');proofs=[r['terminal'] for r in restored['terminals']];check(restored['records']==restored['exports']==[] and len(proofs)==2 and all(p in proofs for p in expected),'original two exact root terminal proofs only after original syscall released')
check(server('host-after-release.json')==baseline==server('host-final.json'),'both complete original driver/config/interface baselines restored after release and again after fresh reuse')
check(any(a['action']=='stop' and a['actor_uid']==1000 and a['job_id']==selection['job'] for a in server('agent-after-release.json')['audit']),'actual durable audit records authorized UID1000 stop of original job')
check(get('server','wait-stop.txt')==b'1\n','actual timed-out StopJob durable response retained as failure status, not a cleanup proof')
fresh=server('root-reuse-ready.json')['exports'];check(len(fresh)==2 and {r['lease_id'] for r in fresh}.isdisjoint({r['lease_id'] for r in held['exports']}),'fresh two actual grant IDs after cleanup')
check(get('client','rx-1.bin')==(E/'ftdi-host-injected-1.bin').read_bytes()==b'RUSTUSB-RX-1-0001:0123456789abcdef\n','fresh actual FTDI receive exact35')
check(get('client','tx-1.bin')==(E/'ftdi-host-rx.bin').read_bytes()==b'RUSTUSB-TX-1-0001:fedcba9876543210\n','fresh actual FTDI transmit exact35')
ev=get('client','input-1.bin');events=[struct.unpack('<qqHHi',ev[i:i+24])[2:] for i in range(0,len(ev)-23,24)];check(len(ev)>0 and len(ev)%24==0 and (1,2,1) in events and (1,2,0) in events,'fresh real evdev KEY1 down/up')
for role in ['server','client']:
 for name in ['helper','agent-1']+(['agent-2'] if role=='server' else []):
  status=dict(line.split(':',1) for line in get(role,'identity-'+name+'.txt').decode().splitlines() if ':' in line);check(status['Uid'].split()==(['0']*4 if name=='helper' else ['2000']*4),'actual '+role+'/'+name+' four UID fields');prog='rustusb-helper' if name=='helper' else 'rustusb';check(get(role,'executable-'+name+'.txt').decode().split()[0]==summary['binary_sha256'][prog],'actual '+role+'/'+name+' frozen ELF')
 check(obj(role,'root-final.json')['records']==obj(role,'root-final.json')['exports']==[],'final '+role+' root records empty after actual cooperative cleanup')
for p in E.rglob('*'):
 if p.is_file():b=p.read_bytes();manifest[str(p.relative_to(E))]={'bytes':len(b),'sha256':sha(b)}
report={'schema_version':1,'review_passed':True,'original_result':'FAIL','conclusion':'Actual baseline retains both accepted Native exports through held Resource ACK; explicit release then restores both and permits real fresh I/O. The failed run is not relabelled.','author_role':'Original harness author; separate raw-byte parsing method, not an independent implementer','runtime_head':summary['head'],'scope':'one original NativeResource/job run, raw lifecycle failure and subsequent cleanup/reuse; no anonymous control-UDS identity attribution, hardware, ARM or kernel-active claim','checks':checks,'check_count':len(checks),'count_scope':'diagnostic predicates, not additional tests or USB cases','held_observations':len(observations),'held_nanoseconds':exited['monotonic_ns']-candidate['monotonic_ns'],'manifest_entries':len(manifest)}
(O/'report.json').write_text(json.dumps(report,indent=2)+'\n');(O/'originals-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps({'review_passed':True,'original_result':'FAIL','checks':len(checks),'output':str(O)}))
