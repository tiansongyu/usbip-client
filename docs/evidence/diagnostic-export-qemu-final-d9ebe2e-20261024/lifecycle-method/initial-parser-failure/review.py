"""Raw-byte lifecycle review. Original harness author, independent method only.

No original verifier/runner imports, subprocesses, Git, or product invocations.
"""
import argparse,base64,hashlib,json,re,struct
from decimal import Decimal
from pathlib import Path

PARSER=argparse.ArgumentParser();PARSER.add_argument('--evidence',type=Path,default=Path('/tmp/rustusb-diagnostic-qemu-second-d9ebe2e-20261024'));PARSER.add_argument('--output',type=Path,default=Path(__file__).parent);ARGS=PARSER.parse_args();E=ARGS.evidence;OUT=ARGS.output
OUT.mkdir(parents=True,exist_ok=True)
def sha(data):return hashlib.sha256(data).hexdigest()
def inventory(directory):return {str(p.relative_to(directory)):{'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(directory.rglob('*')) if p.is_file()}
def pairs(rows):
 result={}
 for key,value in rows:
  if key in result:raise ValueError('duplicate JSON key '+key)
  result[key]=value
 return result
def decode(raw):return json.loads(raw,object_pairs_hook=pairs)
def raw(role,name):return (E/(role+'-artifacts')/name).read_bytes()
def obj(role,name):return decode(raw(role,name))
def lines(role,name):return [decode(line) for line in raw(role,name).splitlines() if line]
def exact_one(items,predicate):
 found=[x for x in items if predicate(x)]
 if len(found)!=1:raise ValueError('expected exact one matching original, got '+str(len(found)))
 return found[0]
def require(ok,label):
 if not ok:raise ValueError(label)
def check(label,ok,**facts):
 require(ok,label);CHECKS.append({'check':label,'passed':True,**facts})
def status(rawtext):return {k:v.strip() for k,v in (line.split(':',1) for line in rawtext.decode().splitlines() if ':' in line)}
def proc(role,label,uid,elf):
 fields=status(raw(role,'proc-'+label+'.status'));text=raw(role,'proc-'+label+'.stat').decode();start=text.rfind(') ')
 pid=int(text.split(' ',1)[0]);starttime=int(text[start+2:].split()[19])
 require(start>0 and int(fields['Pid'])==pid and fields['Uid'].split()==[str(uid)]*4,'actual proc UID/PID '+label)
 require(raw(role,'proc-'+label+'.exe.sha256').decode().split()[0]==elf,'actual proc frozen ELF '+label)
 fds=lines(role,'proc-'+label+'-fds.jsonl');require(not any(r['target']=='/dev/ttyS2' for r in fds),'private host control descriptor not inherited '+label)
 if uid!=0:require(all(fields[k]=='0000000000000000' for k in ('CapInh','CapPrm','CapEff','CapAmb')),'actual nonroot capabilities '+label)
 return {'pid':pid,'starttime':starttime,'uid':uid,'fds':fds}
def identity(role,label,uid,elf):
 fields=status(raw(role,'identity-'+label+'.txt'))
 require(fields['Uid'].split()==[str(uid)]*4,'actual identity UID '+role+'/'+label)
 require(raw(role,'executable-'+label+'.txt').decode().split()[0]==elf,'actual executable bytes '+role+'/'+label)
 return int(fields['Pid'])
def business(state):return {'jobs':state['jobs'],'sessions':[{k:v for k,v in s.items() if k!='metrics'} for s in state['sessions']],'requests':state['requests']}
def owned_link(state,session):return exact_one(state['link_history'],lambda r:r['session_id']==session['id'] and r['instance_id']==session['instance_id'])['link']
def uptime(role,name):return Decimal(raw(role,name).split()[0].decode())

CHECKS=[];FACTS={};before=inventory(E);failed=None
first=Path('/tmp/rustusb-diagnostic-qemu-first-bde3aa9-20261024');first_before={n:sha((first/n).read_bytes()) for n in ('summary.json','server-artifacts/agent-1.log')}
try:
 s=decode((E/'summary.json').read_bytes())
 check('original terminal source scope remains exact',s['result']=='PASS' and bool(s['completed_utc']) and s['head']=='d9ebe2eadf8490081035199c91d17bbba41bc57e' and s['guest_exit_codes']=={'server':0,'client':0} and all(s[k] is True for k in ('source_guard','binary_guard','head_guard','clean_guard')),head=s['head'],guest_exits=s['guest_exit_codes'])
 for name,entry in s['artifact_manifest'].items():require(before[name]==entry,'actual original artifact manifest '+name)
 for name,entry in s['source_inputs'].items():require(before['source/'+name]==entry,'frozen source bytes '+name)
 check('all manifest originals and 209 captured source inputs match their own bytes/SHA',len(s['source_inputs'])==209,source_files=len(s['source_inputs']),manifest_files=len(s['artifact_manifest']),all_original_files=len(before))
 # Parse complete evidence framing directly, independently of secure.artifacts.
 for role in ('server','client'):
  evidence=(E/(role+'-evidence.log')).read_bytes().splitlines();decoded={};index=0
  while index<len(evidence):
   line=evidence[index];index+=1
   if not line.startswith(b'SECURE_ARTIFACT_BEGIN '):continue
   name,size=line[len(b'SECURE_ARTIFACT_BEGIN '):].rsplit(b' ',1);name=name.decode();require('/' not in name and name not in decoded,'unique fixed basename')
   content=[]
   while index<len(evidence) and evidence[index]!=b'SECURE_ARTIFACT_END '+name.encode():content.append(evidence[index]);index+=1
   require(index<len(evidence),'actual artifact end '+name);index+=1
   payload=base64.b64decode(b''.join(content),validate=True);require(len(payload)==int(size) and payload==raw(role,name),'exact base64 original '+name)
   decoded[name]={'bytes':len(payload),'sha256':sha(payload)}
  require(decoded==s['guest_artifacts'][role],'all decoded source artifacts agree '+role)
  require(set(decoded)=={p.name for p in (E/(role+'-artifacts')).iterdir() if p.is_file()},'no unframed derived guest artifacts '+role)
  text=(E/(role+'-evidence.log')).read_text();passes=[v for v in text.splitlines() if v.startswith('RUSTUSB_DIAGNOSTIC_RESULT: PASS')]
  require(passes==['RUSTUSB_DIAGNOSTIC_RESULT: PASS role='+role] and not any(x in text for x in ('RUSTUSB_DIAGNOSTIC_RESULT: FAIL','SECURE_ARTIFACT_ERROR','OVERSIZE','POWEROFF_RETURNED')),'unique final S1 result '+role)
  kernel=(E/(role+'-serial.log')).read_text();require('Kernel panic' not in kernel and 'Oops:' not in kernel,'final kernel fault '+role)
  FACTS.setdefault('kernel_warnings',{})[role]=[line for line in kernel.splitlines() if 'WARNING:' in line or 'BUG:' in line]
  check('complete '+role+' base64/raw artifact identity and final S0/S1 markers',True,artifacts=len(decoded))
 cli=s['binary_sha256']['rustusb'];helper=s['binary_sha256']['rustusb-helper'];require(cli=='9708a50ee836378cd64f8726dae36caded96d4fca39faae215aa30658ef49b48' and helper=='7c690c62dbc02b7e08b224e2f3cfe773c77bebc6a8e6c10ebd716b2696806322','original product ELF expected')
 actors={}
 for role in ('server','client'):
  h=identity(role,'helper',0,helper);a=identity(role,'agent-1',2000,cli);p=proc(role,'agent-start',2000,cli);require(a==p['pid'],'same broker exec/proc identity')
  require(raw(role,'wait-helper.txt')==b'0\n','actual helper wait0')
  actors[role]={'helper_pid':h,'broker':{k:v for k,v in p.items() if k!='fds'}}
 exporter=proc('server','export-held',1000,cli);tracer=proc('server','tracer-start',0,s['payload_sha256']['server']['usr/bin/prepare-gate'])
 require(raw('server','export-held.wait')==b'0\n','actual exporter wait0')
 check('actual root helper, nonroot brokers, UID1000 exporter and root tracer share expected ELF/UID receipts',True,actors=actors,exporter={k:v for k,v in exporter.items() if k!='fds'},tracer={k:v for k,v in tracer.items() if k!='fds'},product_sha256=s['binary_sha256'])
 # Gate identity and complete monotonic same-call timeline.
 c=obj('server','gate-candidate-1.json');hb=obj('server','gate-held-before.json');ha=obj('server','gate-held-after.json');ret=obj('server','gate-exit-1.json');ready=obj('server','gate-ready.json');armed=obj('server','gate-armed.json');events=lines('server','gate-events.jsonl')
 expected={'candidate':1,'tracee_pid':440,'tracee_starttime':370,'tid':447,'fd':27,'request':74,'file_device':2,'file_inode':413}
 for item in (c,hb,ha,ret):require(all(item[k]==v for k,v in expected.items()),'same actual gated syscall identity')
 require(hb['held'] is True and ha['held'] is True and c['event']=='held' and ret['event']=='exit' and ret['return_value']==0 and ret['is_error'] is False,'actual entry/held/exit statuses')
 for item in (c,ret,armed):require(item['nonce']=='prepare-stop-v1' and item['operation']=='resource' and item['tracer_pid']==tracer['pid'],'same actual nonce/mode/tracer')
 require(ready['tracee_pid']==actors['server']['broker']['pid']==440 and ready['tracee_starttime']==actors['server']['broker']['starttime']==370 and ready['tracer_pid']==tracer['pid'],'original ready owner')
 for mark in ('before','after'):
  p=proc('server','gate-'+mark,2000,cli);require(p['pid']==440 and p['starttime']==370,'same original process during gate')
  fd=exact_one(p['fds'],lambda f:f['fd']=='27');require(fd['target']=='/var/lib/rustusb/agent/state.next' and fd['stat']=='2:413:2000:600:regular file','actual FD maps to held WAL inode')
 entry=exact_one(events,lambda r:r['event']=='syscall_entry' and r['tid']==447 and r['fd']==27 and r.get('file_inode')==413);require(entry['syscall_nr']==74,'actual fsync syscall entry')
 release=exact_one(events,lambda r:r['event']=='released');ordered=[entry['monotonic_ns'],c['monotonic_ns'],hb['monotonic_ns'],ha['monotonic_ns'],release['monotonic_ns'],ret['monotonic_ns']]
 require(ordered==sorted(ordered) and len(set(ordered))==len(ordered),'actual complete same-call monotonic timeline')
 duration=ret['monotonic_ns']-c['monotonic_ns'];require(duration==1037464451 and duration<30_000_000_000,'actual 1.037464451s held duration')
 require(not any(r['event'] in ('failure','abort_cleanup','abort_released') for r in events),'no failure/watchdog substituted for explicit release')
 require(raw('server','gate-release-1')==b'release\n' and raw('server','gate-decision-1')==b'select\n','actual selected/release control files')
 require(raw('server','selected-committed.json')==raw('server','agent-gate-before.json')==raw('server','agent-gate-after.json'),'same saved commit bytes throughout gate')
 old=obj('server','selected-committed.json');draft=obj('server','selected-draft.json');after=obj('server','agent-after-release.json')
 require(draft['sequence']==old['sequence']+1 and any(a['sequence']==draft['sequence'] and a['actor_uid']==1000 and a['profile'] is None and a['action']=='denied' for a in draft['audit']),'actual selected withheld Denied work')
 require(business(old)==business(draft)==business(after),'withheld admission does not change original owner capabilities')
 require(any(a['sequence']==draft['sequence'] and a['actor_uid']==1000 and a['action']=='denied' for a in after['audit']),'withheld audit only committed after actual return')
 low=Decimal(hb['monotonic_ns'])/Decimal(10**9);high=Decimal(ha['monotonic_ns'])/Decimal(10**9)
 for name in ('export-held-before.uptime','export-held-after.uptime','rpc-post-export-begin.uptime','rpc-post-export-end.uptime'):
  t=uptime('server',name);require(low-Decimal('.02')<=t<=high+Decimal('.02'),'actual exporter/metadata receipt inside held interval '+name)
 receipt=obj('server','export-held.stdout');require(receipt['capture']=='complete' and receipt['summary']['complete'] and receipt['file']['result']=='written' and receipt['file']['receipt']['publication']=='published','actual complete exporter publication result')
 require(receipt['file']['receipt']['sha256']==sha(raw('server','diagnostic-bundle.json')),'actual exported bytes SHA')
 check('real original WAL entry remains held across exporter wait and independent observations until exact successful return',True,identity=expected,nonce=c['nonce'],tracer_pid=tracer['pid'],ordered_monotonic_ns=ordered,held_ns=duration,committed_sequence=old['sequence'],withheld_sequence=draft['sequence'],export_uptime=[str(uptime('server','export-held-before.uptime')),str(uptime('server','export-held-after.uptime'))])
 controls=[decode(line) for line in (E/'host-control.jsonl').read_bytes().splitlines()];commands=[r['command'] for r in controls]
 require(all(a['monotonic']<b['monotonic'] for a,b in zip(controls,controls[1:])),'actual host command monotonic order')
 order=['OBSERVE1','IO2','IO2_COMPLETE','CLOSE1','STOP1'];require([commands.index(v) for v in order]==sorted(commands.index(v) for v in order),'cross-guest I/O and later cleanup barriers')
 server_text=(E/'server-evidence.log').read_text();client_text=(E/'client-evidence.log').read_text()
 server_marks=['DIAG_GATE_HELD','DIAG_EXPORTED_GATE_HELD','DIAG_GATE_RELEASED','DIAG_SERVER_STOPPED'];require(all(server_text.count('\n'+x+'\n')==1 for x in server_marks) and [server_text.index(x) for x in server_marks]==sorted(server_text.index(x) for x in server_marks),'actual server marker sequence')
 require(client_text.index('AGENT_CLIENT_IO round=1 ')<client_text.index('AGENT_CLIENT_IO round=2 ')<client_text.index('LOG_CLIENT_STOPPED epoch=1'),'actual two completed client I/O rounds before local stop')
 qmp=[decode(line) for line in (E/'qmp-input.jsonl').read_bytes().splitlines()];require([r['round'] for r in qmp]==[1,2] and all(r['command']=='send-key' and r['arguments']=={'keys':[{'type':'qcode','data':str(r['round'])}],'hold-time':100} and r['return']=={} for r in qmp),'two actual QMP acknowledgements')
 expected_tx=b'';io=[]
 for round_id in (1,2):
  data=raw('client',f'input-{round_id}.bin');require(len(data)>0 and len(data)%24==0,'complete actual evdev x86_64 records')
  values=[struct.unpack('<qqHHi',data[i:i+24])[2:] for i in range(0,len(data),24)]
  require((1,round_id+1,1) in values and (1,round_id+1,0) in values,'actual KEY down/up '+str(round_id))
  rx=f'RUSTUSB-RX-{round_id}-0001:0123456789abcdef\n'.encode();tx=f'RUSTUSB-TX-{round_id}-0001:fedcba9876543210\n'.encode();expected_tx+=tx
  require(len(rx)==len(tx)==35 and raw('client',f'rx-{round_id}.bin')==(E/f'ftdi-host-injected-{round_id}.bin').read_bytes()==rx and raw('client',f'tx-{round_id}.bin')==tx,'actual bidirectional round bytes')
  io.append({'round':round_id,'linux_key_code':round_id+1,'evdev_records':len(values),'rx_bytes':35,'tx_bytes':35,'rx_sha256':sha(rx),'tx_sha256':sha(tx)})
 require((E/'ftdi-host-rx.bin').read_bytes()==expected_tx and len(expected_tx)==70,'actual host FTDI receives exactly both rounds')
 check('same paired run performs KEY1/KEY2 and exactly 70 bytes in each serial direction',True,rounds=io,host_control_order=order,scope='cross-guest order uses actual host handshakes; guest boot clocks are not compared to each other; no claim about a specific pending URB')
 # Preserve actual resource identities and prove exact retirement, not merely zeros.
 keys=obj('server','keys.json');state={r:obj(r,'agent-held-1.json') for r in ('server','client')};root={r:obj(r,'root-held-1.json') for r in ('server','client')};final={r:obj(r,'agent-stopped-1.json') for r in ('server','client')};retired={r:obj(r,'root-stopped-1.json') for r in ('server','client')}
 require(root['server']==obj('server','root-gate-before.json')==obj('server','root-gate-after.json'),'server original complete Root receipts survive diagnostic and second I/O')
 require(root['client']==obj('client','client-receipts-1.json')==obj('client','root-preserved.json'),'client original receipts/local generations survive both rounds')
 require(business(state['server'])==business(old) and business(state['client'])==business(obj('client','agent-preserved.json')),'actual original service owners preserved')
 proof_names={'native_export':'native_export_restored','native_stub':'native_stub_available','native_import':'native_import_released'};retirement_facts={}
 for role,count in (('server',4),('client',2)):
  held=state[role];ended=final[role];roots=root[role];endroot=retired[role]
  require(held['schema_version']==ended['schema_version']==roots['schema_version']==endroot['schema_version']==2,'actual schemas')
  require(len(held['sessions'])==len(ended['sessions'])==count and len(held['jobs'])==len(ended['jobs'])==(1 if role=='server' else 2),'exact nonempty owner histories')
  require({s['id'] for s in held['sessions']}=={s['id'] for s in ended['sessions']} and all(j['state']=='finished' for j in ended['jobs']),'same original IDs and actual finished jobs')
  require(roots['terminals']==[] and endroot['records']==endroot['exports']==[] and len(endroot['terminals'])==count,'zero current roots plus exact original terminal count')
  result=[]
  for h in held['sessions']:
   f=exact_one(ended['sessions'],lambda x:x['id']==h['id']);a=h['attempts'][0]
   require(len(h['attempts'])==len(f['attempts'])==1 and all(f[k]==h[k] for k in ('id','instance_id','job_id','profile','role','device_key','principal')),'exact original session identity')
   require({k:v for k,v in a.items() if k!='proof'}=={k:v for k,v in f['attempts'][0].items() if k!='proof'},'original capability attempt retained')
   require(h['terminal'] is False and h['operation_error'] is None and f['terminal'] is True and f['state']=='Disconnected' and f['evidence']=='released' and f['cleanup']=='verified','actual role lifecycle and verified cleanup')
   expected={'resource_id':a['resource_id'],'kind':a['kind'],'device_key':h['device_key'],'owner_uid':2000,'correlation':a['correlation'],'proof':proof_names[a['kind']]}
   require(sum(r['terminal']==expected and r['server_id']==('qemu-server' if role=='client' else None) for r in endroot['terminals'])==1,'exact original Root proof and server scope')
   require(f['attempts'][0]['proof'] in (None,expected['proof']),'no different cleanup proof substituted')
   if role=='client':cap=exact_one(roots['records'],lambda x:x['channel_id']==a['resource_id']);owner=cap['owner']
   else:
    export=exact_one(roots['exports'],lambda x:x['registration']['device_key']==h['device_key']);cap=export if h['role']=='export' else export['stub'];owner=export['owner']
   require(cap['correlation']==a['correlation'] and (cap.get('channel_id') or cap.get('lease_id'))==a['resource_id'] and owner['uid']==2000 and owner['pid']==actors[role]['broker']['pid'] and owner['starttime']==actors[role]['broker']['starttime'],'actual held root owner/received capability matches same broker and attempt')
   require(a['correlation']['instance_id']==h['instance_id'] and a['correlation']['session_id']==h['id'] and any(j['id']==h['job_id'] and j['instance_id']==h['instance_id'] and j['operator_uid']==1000 for j in ended['jobs']),'actual app/attempt/job relations')
   result.append({'session_id':h['id'],'role':h['role'],'resource_id':a['resource_id'],'proof':expected['proof'],'operation_error':f['operation_error'],'cleanup':f['cleanup']})
  retirement_facts[role]=result
 check('all six original capabilities have exact same-attempt root retirement proofs',True,retirements=retirement_facts)
 # Match metadata, host generations/full configuration, and actual imported ports.
 baseline=obj('server','host-before.json');host_held=obj('server','host-held-1.json');host_after=obj('server','host-gate-after.json');restored=obj('server','host-restored-1.json')
 require(len(baseline)==2 and host_held==host_after and restored==baseline==obj('server','host-stopped-1.json'),'actual original physical generation and complete restoration')
 for before,live in zip(baseline,host_held):
  dynamic={'device_driver','usbip_status','interfaces'}
  require({k:v for k,v in before.items() if k not in dynamic}=={k:v for k,v in live.items() if k not in dynamic} and live['device_driver']=='usbip-host' and live['usbip_status']==2 and live['interfaces']==[dict(i,driver='') for i in before['interfaces']],'same actual hardware/cfg/full IF while USED')
  e=exact_one(root['server']['exports'],lambda e:e['registration']['generation']['bus_id']==before['bus']);g=e['registration']['generation'];p=e['registration']['original_profile']
  require(g['sysfs_path']==before['path'] and str(g['filesystem_device'])+':'+str(g['inode'])==before['generation'] and g['device_number']==int(before['devnum']) and g['bus_number']==int(before['busnum']) and g['vendor_id']==int(before['vendor'],16) and g['product_id']==int(before['product'],16) and g['device_version']==int(before['release'],16),'original strong sysfs generation/descriptor identity')
  require(p=={'configuration':int(before['configuration']),'interfaces':[{'number':int(i['number'],16),'alternate':int(i['alt']),'class':int(i['class'],16),'subclass':int(i['subclass'],16),'protocol':int(i['protocol'],16),'driver':i['driver']} for i in before['interfaces']]} and e['registration']['original_device_driver']==before['device_driver'],'exact original driver/configuration/full-interface baseline')
 local=obj('client','local-identities-1.json');gone=obj('client','local-gone-1.json')
 def ports(name):
  return [line.split() for line in raw('client',name).decode().splitlines() if line.startswith(('hs ','ss '))]
 used=ports('vhci-held-1.txt');empty=ports('vhci-stopped-1.txt');require(len(used)==len(empty)>0 and sum(int(r[2])==6 for r in used)==2 and all(int(r[2])==4 for r in empty),'actual occupied ports then every original controller port NULL')
 for i,key in enumerate(keys):
  name=('hid','serial')[i];r=exact_one(root['client']['records'],lambda r:r['target']['device_key']==key);e=exact_one(root['server']['exports'],lambda e:e['registration']['device_key']==key);receipt=r['state']['receipt'];sess=receipt['session'];generation=sess['local_device_generation']
  require(r['target']['server_id']=='qemu-server' and r['target']['remote']==e['registration']['published_device'],'actual authenticated remote descriptor from original published registration')
  require(r['state']['phase']=='observed' and receipt['profile']['configuration']==1 and len(receipt['profile']['interfaces'])==1 and receipt['kernel_socket_inode']>0 and sess['status']==6,'complete actual local enumeration receipt')
  port=exact_one(used,lambda row:int(row[1])==sess['port']);require(port[6]==sess['local_bus_id']==local[name] and int(port[4],16)==sess['remote_device_id'] and local[name+'_generation']==str(generation['filesystem_device'])+':'+str(generation['inode']),'actual same local generation and VHCI port/dev mapping')
  require(gone[name]==local[name] and gone[name+'_exists'] is False,'explicit original local directory Gone observation')
 check('whole original driver/configuration/interface baseline and actual VHCI NULL/Gone independently restored',True,local_generations=local,baseline_drivers=[i['driver'] for b in baseline for i in b['interfaces']],vhci_ports=len(empty))
 # Preserve actual client-first State/Protocol, not a clean-EOF narrative.
 outcomes=[]
 require(raw('client','wait-agent-1.txt')==b'0\n' and raw('server','wait-tracer.txt')==b'0\n','actual client/tracer waits')
 require(any(r['event']=='tracee_exit' and r['tid']==440 and r['exit_code']==0 and r['signal']==0 for r in events) and any(r['event']=='finished' and r['tid']==440 and r['tracee_exit_code']==0 for r in events),'actual traced broker leader exit0 and final reaper receipt')
 for h in [r for r in state['server']['sessions'] if r['role']=='incoming']:
  peer=exact_one(state['client']['sessions'],lambda r:r['device_key']==h['device_key']);a=exact_one(final['server']['sessions'],lambda r:r['id']==h['id']);b=exact_one(final['client']['sessions'],lambda r:r['id']==peer['id']);sb=owned_link(state['server'],h);cb=owned_link(state['client'],peer);se=owned_link(final['server'],a);ce=owned_link(final['client'],b)
  require(sb['protocol']==cb['protocol']==se['protocol']==ce['protocol']=='framed_v2' and sb['phase']==cb['phase']=='open' and sb['end'] is cb['end'] is None,'same actual open framed connections before stop')
  require(sb['identity']==se['identity'] and cb['identity']==ce['identity'] and sb['identity']['tcp']=={'local':cb['identity']['tcp']['peer'],'peer':cb['identity']['tcp']['local']},'actual reversed original client/server TCP data tuple, same connections at terminal')
  require(se['phase']==ce['phase']=='ended' and se['end']=='protocol' and a['operation_error']=='state' and ce['end']=='local_stop' and b['operation_error'] is None,'actual State/Protocol server outcome with client local_stop and separate Verified proofs')
  outcomes.append({'server_session':h['id'],'client_session':peer['id'],'server_operation_error':a['operation_error'],'server_link_end':se['end'],'client_operation_error':b['operation_error'],'client_link_end':ce['end'],'cleanup':'verified','server_connection':sb['identity'],'client_connection':cb['identity']})
 check('both incoming State/Protocol outcomes remain correlated to original client-first local stop',True,outcomes=outcomes,scope='not clean EOF; no inference about untraced packet-reset cause')
 oldfail=decode((first/'summary.json').read_bytes());require(oldfail['result']=='FAIL' and (first/'server-artifacts/agent-1.log').read_bytes()==b'prepare-gate: invalid fixed test invocation\n','prior real FAIL remains unchanged')
 check('first launch failure remains a separate unchanged terminal FAIL',True,head=oldfail['head'],result=oldfail['result'])
 FACTS.update({'gate_hold_ns':duration,'USB_scope':{'paired_runs':1,'epochs':1,'guests':2,'io_rounds':2,'serial_bytes_each_direction':70,'original_capabilities_retired':6},'control_flow':'actual host IO2 after exported-held marker; IO2_COMPLETE only after actual second HID/FTDI bytes; final held receipt before explicit release; client local stop before server TERM','end_and_projection_scope':'Artifact alias/pagination/permission and metadata End details reviewed separately by N. This review checks actual complete export wait/byte receipt and its placement inside the same held syscall interval, not a raw trace of old End token.'})
except Exception as error:
 failed={'kind':type(error).__name__,'error':str(error)}
finally:
 after_inventory=inventory(E);unchanged=before==after_inventory
 first_unchanged=first_before=={n:sha((first/n).read_bytes()) for n in first_before}
 (OUT/'original-manifest.json').write_text(json.dumps({'before':before,'after_identical':unchanged,'first_failure_sha256':first_before,'first_failure_after_identical':first_unchanged},indent=2)+'\n')
 report={'result':'PASS' if failed is None and unchanged and first_unchanged else 'FAIL','scope':'Independent raw parsing method by original harness author; not independently implemented harness, not another USB execution. Original1300 host assertions are not USB case count. No imports of original runner/verifier, no product/compiler/QEMU/Git commands. Network/projection review delegated separately.','error':failed,'checks':CHECKS,'facts':FACTS,'input_evidence':str(E),'source_revision':'d9ebe2eadf8490081035199c91d17bbba41bc57e','originals_unchanged':unchanged,'first_failure_unchanged':first_unchanged}
 (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({'result':report['result'],'diagnostic_groups':len(CHECKS),'error':failed,'report':str(OUT/'report.json'),'sha256':sha((OUT/'report.json').read_bytes())}))
 if report['result']!='PASS':raise SystemExit(1)
