#!/usr/bin/env python3
"""Read-only independent evidence parser. No harness imports or verification calls."""
import argparse,base64,collections,datetime,hashlib,json,pathlib,re,struct,subprocess
REPO=pathlib.Path('/home/ubuntu/github/usbip-client')
EXPECTED='1582f06776f4c1ea50e9b5109a9ad7d0b7cdbe16'
LOCK=json.loads(pathlib.Path('/tmp/rustusb-prepare-stop-legacy-nine-review-20261024/source-lock.json').read_text())
EXPECTED_BINARIES=None
def sha(b):return hashlib.sha256(b).hexdigest()
def identity(s):return tuple(s[k] for k in ['id','instance_id','job_id','profile','role','device_key','principal'])
def attempt(a):return (a['kind'],a['resource_id'],tuple(a['correlation'][k] for k in ['instance_id','session_id','attempt_id']))
class Audit:
 def __init__(self,root):self.root=root;self.checks=[];self.raw={};self.facts={};self.warnings={}
 def check(self,category,name,condition):
  self.checks.append({'category':category,'name':name,'pass':bool(condition)})
  if not condition:raise ValueError(category+': '+name)
 def read(self,role,name):return self.raw[role][name]
 def obj(self,role,name):return json.loads(self.read(role,name))
 def text(self,role,name):return self.read(role,name).decode()
 def run(self):
  summary=json.loads((self.root/'summary.json').read_text());self.summary=summary
  self.check('provenance','complete source/terminal summary',summary.get('completed_utc') and summary['head']==summary['head_after']==EXPECTED and summary['result']=='PASS' and summary['guest_exit_codes']=={'server':0,'client':0})
  self.check('provenance','all four original guards and clean source',all(summary[k] is True for k in ['source_guard','head_guard','binary_guard','clean_guard']) and summary['git_status_before']==summary['git_status_after']=='')
  source_count=0
  for name,entry in summary['source_inputs'].items():
   archived=(self.root/'source'/name).read_bytes();committed=subprocess.check_output(['git','show',EXPECTED+':'+name],cwd=REPO)
   self.check('source_gitblob',name,len(archived)==entry['bytes'] and sha(archived)==entry['sha256'] and archived==committed);source_count+=1
  self.check('provenance','exact current frozen input set includes service/core',summary['source_inputs']=={p:LOCK['source_hashes'][p] for p in LOCK['agent_inputs']})
  software_manifest_path=LOCK['software_manifest_git_path']
  software_manifest_bytes=pathlib.Path(software_manifest_path).read_bytes();software_manifest=json.loads(software_manifest_bytes)
  self.check('software_attribution','actual completed prepare-recording667 software source manifest mapped to 1582f06',sha(software_manifest_bytes)==LOCK['software_manifest_sha256'] and len(software_manifest)==LOCK['software_input_count'])
  for entry in software_manifest:
   name=entry['path'];current=subprocess.check_output(['git','show',EXPECTED+':'+name],cwd=REPO)
   self.check('software_attribution',name,len(current)==entry['bytes'] and sha(current)==entry['sha256'])
  self.check('software_attribution','actual two ELFs match root locked-build attribution',summary['binary_sha256']==EXPECTED_BINARIES)
  self.facts['software_attribution']={'head':EXPECTED,'software_manifest_sha256':sha(software_manifest_bytes),'selected_input_count':len(software_manifest),'expected_elf':EXPECTED_BINARIES,'scope':'Read-only exact Gitblob/ELF attribution; no execution or relabeling of software tests.'}
  for name,entry in summary['artifact_manifest'].items():
   b=(self.root/name).read_bytes();self.check('artifact_hash',name,len(b)==entry['bytes'] and sha(b)==entry['sha256'])
  pattern=re.compile(rb'^SECURE_ARTIFACT_BEGIN ([a-zA-Z0-9_.-]+) ([0-9]+)\n(.*?)^SECURE_ARTIFACT_END \1\s*$',re.M|re.S)
  bootids={};all_receipt_ids=[]
  for role in ['server','client']:
   log=(self.root/f'{role}-evidence.log').read_bytes();text=log.decode();found={}
   for block in pattern.finditer(log):
    name=block[1].decode();body=base64.b64decode(b''.join(block[3].split()),validate=True)
    self.check('raw_base64',role+'/'+name,name not in found and len(body)==int(block[2]) and body==(self.root/f'{role}-artifacts'/name).read_bytes() and sha(body)==summary['guest_artifacts'][role][name]['sha256'])
    found[name]=body
   self.raw[role]=found
   self.check('execution',role+' complete raw artifact framing',log.count(b'SECURE_ARTIFACT_BEGIN ')==len(found)==len(summary['guest_artifacts'][role]))
   self.check('execution',role+' single terminal PASS without collection failure',text.count('RUSTUSB_AGENT_RESULT: PASS role='+role)==1 and all(x not in text for x in ['RUSTUSB_AGENT_RESULT: FAIL','SECURE_ARTIFACT_ERROR','SECURE_ARTIFACT_OVERSIZE','SECURE_POWEROFF_RETURNED']))
   s0=(self.root/f'{role}-serial.log').read_text()
   self.check('execution',role+' complete S0 has no panic/Oops',all(x not in s0 for x in ['Kernel panic','Oops:']))
   self.warnings[role]=[{'line':i+1,'text':line} for i,line in enumerate(s0.splitlines()) if re.search(r'warning|\bWARN\b|\bERROR\b|vhci_device speed not set|urb->status -|usbip.*(?:fail|error|cannot)|usb .*error',line,re.I)]
   self.check('identity',role+' credential role/RNG evidence',f'SECURE_CREDENTIAL_ISOLATION role={role} own_key=true opposite_key=false ca_key=false' in text and 'SECURE_RNG_RTC_READY crng=true ' in text)
   for process in ['helper','agent-1','agent-2']:
    status=self.text(role,'identity-'+process+'.txt');uid=0 if process=='helper' else 2000
    fields={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in status.splitlines() if ':' in line}
    self.check('identity',role+'/'+process+' real UID/GID',[int(x) for x in fields['Uid'].split()]==[uid]*4 and [int(x) for x in fields['Gid'].split()]==[uid]*4)
    if uid:self.check('identity',role+'/'+process+' zero capabilities',all(int(fields[k],16)==0 for k in ['CapInh','CapPrm','CapEff','CapAmb']))
    program='rustusb-helper' if process=='helper' else 'rustusb';actual=self.text(role,'executable-'+process+'.txt').split()[0]
    self.check('identity',role+'/'+process+' actual /proc ELF equals build/payload',actual==summary['binary_sha256'][program]==summary['payload_sha256'][role]['usr/bin/'+program])
    all_receipt_ids.append({'role':role,'process':process,'pid':int(fields['Pid']),'uid':uid,'elf_sha256':actual})
   for epoch in [1,2]:
    actual=self.text(role,f'agent-identity-{epoch}.txt')
    self.check('identity',f'{role} epoch{epoch} control/data/socket modes',all(v in actual for v in ['control=2000:711','data=2000:700','socket=2000:666']))
   policy=self.obj(role,'helper-policy.json');manifest=self.obj(role,'agent-manifest.json')
   self.check('authorization',role+' broker/operator separation and no legacy trust',policy['trusted_network'] is False and policy['secure_broker_uid']==2000 and manifest['broker_uid']==2000 and manifest['operators'][0]['uid']==1000)
   self.check('authorization',role+' actual new store absent',self.text(role,'agent-before-denied.status')=='absent\n' and 'agent-before-denied.json' not in found)
   self.check('authorization',role+' public empty state',self.obj(role,'jobs-before-denied.json')['response']=={'result':'jobs','jobs':[],'next_after':None} and self.obj(role,'sessions-before-denied.json')['response']=={'result':'sessions','sessions':[],'next_after':None})
   denied=self.obj(role,'agent-after-denied.json')
   self.check('authorization',role+' two typed denials',all(self.obj(role,'denied-'+kind+'.json')['response']=={'result':'error','code':'permission'} for kind in ['uid','profile']))
   self.check('authorization',role+' denial leaves resources untouched',all(denied[k]==[] for k in ['jobs','sessions','requests']) and self.read(role,'root-before-denied.json')==self.read(role,'root-after-denied.json'))
   self.check('authorization',role+' real denied actor audit',[(x['actor_uid'],x['action'],x['failure']) for x in denied['audit']]==[(1002,'denied','permission'),(1000,'denied','permission')])
  disk_captures=[]
  session_fields={'id','instance_id','job_id','profile','role','device_key','principal','state','evidence','stop_requested','terminal','operation_error','cleanup','metrics','attempts'}
  raw_v1={'protocol':'raw_v1','identity':None,'sample_sequence':0,'elapsed_millis':0,'phase':'ended','probe':'unavailable_raw_v1','last_probe_sequence':None,'last_probe_enqueued_millis':None,'last_reply_sequence':None,'last_reply_millis':None,'last_round_trip_micros':None,'stats':None,'end':None}
  for role,files in self.raw.items():
   for filename,body in files.items():
    if not filename.endswith('.json'):continue
    document=json.loads(body)
    if not isinstance(document,dict) or not {'jobs','sessions','sequence','audit','requests'}<=set(document):continue
    self.check('private_disk2',role+'/'+filename+' exact private shape',set(document)=={'schema_version','sequence','jobs','sessions','audit','requests','link_history'} and document['schema_version']==2)
    sessions={s['id']:s for s in document['sessions']};history=document['link_history']
    self.check('private_disk2',role+'/'+filename+' unique complete session association',len(sessions)==len(document['sessions'])==len(history)==len({x['session_id'] for x in history}) and {x['session_id'] for x in history}==set(sessions))
    for row in history:
     session=sessions[row['session_id']]
     self.check('private_disk2',role+'/'+filename+'/'+row['session_id']+' immutable epoch and exact null/raw projection',set(row)=={'session_id','instance_id','link'} and row['instance_id']==session['instance_id'] and (row['link'] is None if session['role']=='export' else row['link'] in (None,raw_v1)))
     self.check('legacy_ipc',role+'/'+filename+'/'+row['session_id']+' Session/Metrics shape unchanged',set(session)==session_fields and set(session['metrics'])=={'app_rx_bytes','app_tx_bytes','elapsed_millis','saturated'})
     if session['role']!='export' and (session['state']=='Connected' or session['terminal'] and session['cleanup']=='verified'):
      self.check('private_disk2',role+'/'+filename+'/'+row['session_id']+' observed data session explicitly raw unavailable',row['link']==raw_v1)
    disk_captures.append({'role':role,'file':filename,'sessions':len(sessions),'sha256':sha(body)})
  self.check('private_disk2','nonvacuous state captures from both guests',len(disk_captures)>=4 and {x['role'] for x in disk_captures}=={'server','client'})
  self.facts['private_disk2']={'captures':disk_captures,'scope':'Private application disk2 only. Existing helper tracked disk2 is separately audited; TLS configs/manifest/IPC remain1. No v2 heartbeat observation is inferred from raw-v1 runs.'}
  self.facts['actual_processes']=all_receipt_ids
  keys=self.obj('server','keys.json');self.check('usb_identity','same two exact keys',keys==self.obj('client','keys.json') and len(set(keys))==2 and all(k.startswith('native:usb-') for k in keys));hid,serial=keys
  final={role:self.obj(role,'agent-stopped-2.json' if role=='server' else 'agent-final.json') for role in ['server','client']}
  roots={role:self.obj(role,'root-stopped-2.json' if role=='server' else 'root-final.json') for role in ['server','client']}
  for role in ['server','client']:
   state=final[role];expected=8 if role=='server' else 4;jobcount=2 if role=='server' else 4
   sessions=state['sessions'];jobs=state['jobs'];root=roots[role]
   self.check('history',role+' exact nonvacuous final cardinality',len(sessions)==len({s['id'] for s in sessions})==expected and len(jobs)==len({j['id'] for j in jobs})==jobcount)
   self.check('history',role+' exact final role distribution',collections.Counter(s['role'] for s in sessions)==({'export':4,'incoming':4} if role=='server' else {'import':4}))
   self.check('history',role+' two epochs and verified terminal state',len({j['instance_id'] for j in jobs})==2 and all(j['state']=='finished' and j['operator_uid']==1000 for j in jobs) and all(s['terminal'] and s['state']=='Disconnected' and s['cleanup']=='verified' for s in sessions))
   sid={s['id']:s for s in sessions};seen=[]
   for epoch,round_id in [(1,1),(2,3)]:
    held=self.obj(role,f'agent-held-{round_id}.json')
    profiles=['server'] if role=='server' else ['hid','serial']
    for profile in profiles:
     start=self.obj(role,f'start-{profile}-{epoch}.json')['response'];job=start['job']
     self.check('history',f'{role}/{profile}/{epoch} persisted start matches final job',start['result']=='started' and start['replayed'] is False and next(j for j in jobs if j['id']==job['id'])['instance_id']==job['instance_id'])
     rows=[s for s in held['sessions'] if s['job_id']==job['id']];self.check('history',f'{role}/{profile}/{epoch} held roles',len(rows)==(4 if role=='server' else 1) and collections.Counter(s['role'] for s in rows)==({'export':2,'incoming':2} if role=='server' else {'import':1}))
     for old in rows:
      new=sid[old['id']];seen.append(old['id'])
      self.check('history',role+'/'+old['id']+' retained original identities',identity(new)==identity(old) and old['instance_id']==job['instance_id'] and old['profile']==profile)
      self.check('history',role+'/'+old['id']+' exact attempt preserved',len(old['attempts'])==len(new['attempts'])==1 and attempt(old['attempts'][0])==attempt(new['attempts'][0]))
     evidence=(self.root/f'{role}-evidence.log').read_text();returned=evidence.index(f'AGENT_START_RETURNED role={role} profile={profile} epoch={epoch} wait=0');later=evidence.index(f'AGENT_CLIENT_WAIT_INPUT round={round_id}' if role=='client' else f'AGENT_SERVER_READY epoch={epoch}')
     self.check('execution',f'{role}/{profile}/{epoch} requesting CLI returned before I/O readiness',returned<later)
   self.check('history',role+' complete original history retained',set(seen)==set(sid) and len(seen)==expected)
   self.check('root_proof',role+' exact durable retirement total',root['schema_version']==2 and root['records']==root['exports']==[] and len(root['terminals'])==expected)
   all_attempts=[]
   for session in sessions:
    a=session['attempts'][0];all_attempts.append(a['correlation']['attempt_id']);required={'import':('native_import','native_import_released'),'export':('native_export','native_export_restored'),'incoming':('native_stub','native_stub_available')}[session['role']]
    corr=a['correlation'];self.check('root_proof',role+'/'+session['id']+' full application correlation',corr['instance_id']==session['instance_id'] and corr['session_id']==session['id'] and len(set(corr.values()))==3 and a['kind']==required[0])
    matched=[e for e in root['terminals'] if e['terminal']['correlation']==corr and e['terminal']['kind']==a['kind']]
    self.check('root_proof',role+'/'+session['id']+' exact one proof',len(matched)==1)
    t=matched[0]['terminal'];self.check('root_proof',role+'/'+session['id']+' proof identity/kind/strict criterion',t['resource_id']==a['resource_id'] and t['owner_uid']==2000 and t['device_key']==session['device_key'] and t['proof']==required[1] and a['proof'] in [None,required[1]] and matched[0]['server_id']==('qemu-server' if role=='client' else None))
   self.check('root_proof',role+' fresh attempts, no reused capabilities',len(set(all_attempts))==expected and len({s['attempts'][0]['resource_id'] for s in sessions})==expected)
   self.check('audit',role+' retained sequence/actor facts',state['sequence']==state['audit'][-1]['sequence'] and all(b['sequence']==a['sequence']+1 for a,b in zip(state['audit'],state['audit'][1:])) and all(x['actor_uid'] in [1000,1002,2000] for x in state['audit']))
   self.facts[role+'_sessions']=[{'id':s['id'],'instance_id':s['instance_id'],'job_id':s['job_id'],'role':s['role'],'key':s['device_key'],'attempt':s['attempts'][0],'evidence':s['evidence'],'operation_error':s['operation_error'],'cleanup':s['cleanup'],'metrics':s['metrics']} for s in sessions]
  # Independent kernel parser (fixed observed VHCI column format).
  vhci={}
  for name,count in [('vhci-held-1.txt',2),('vhci-target-stopped.txt',1),('vhci-before-kill.txt',1),('vhci-after-kill.txt',0),('vhci-recovered.txt',0),('vhci-held-3.txt',2),('vhci-final.txt',0)]:
   rows=[]
   for line in self.text('client',name).splitlines():
    f=line.split()
    if f and f[0] in ['hs','ss']:
     self.check('kernel',name+' full column shape',len(f)==7);rows.append({'hub':f[0],'port':int(f[1]),'status':int(f[2]),'speed':int(f[3]),'remote':int(f[4],16),'socket_display':f[5],'local':f[6]})
   self.check('kernel',name+' actual USED/NULL cardinality',bool(rows) and sum(r['status']==6 for r in rows)==count and all(r['status'] in [4,6] for r in rows));vhci[name]=rows
  receipt_sets=[]
  for round_id,epoch in [(1,1),(3,2)]:
   disk=self.obj('client',f'client-receipts-{round_id}.json');records=disk['records'];receipt_sets.append(records);live=self.obj('client',f'local-identities-{round_id}.json')
   self.check('kernel',f'round {round_id} observed receipt table',disk['schema_version']==2 and disk['exports']==[] and len(records)==2 and {r['target']['device_key'] for r in records}==set(keys))
   for r in records:
    typ='hid' if r['target']['device_key']==hid else 'serial';expected=(1575,1,3) if typ=='hid' else (1027,24577,2);receipt=r['state']['receipt'];s=receipt['session'];remote=r['target']['remote'];row=next(x for x in vhci[f'vhci-held-{round_id}.txt'] if x['port']==s['port']);generation=s['local_device_generation'];process=next(p for p in all_receipt_ids if p['role']=='client' and p['process']==f'agent-{epoch}')
    self.check('kernel',r['channel_id']+' matching live helper/row/generation',r['state']['phase']=='observed' and r['owner']['pid']==process['pid'] and r['owner']['uid']==2000 and r['owner']['starttime']>0 and s['status']==row['status']==6 and row['local']==s['local_bus_id']==live[typ] and row['remote']==s['remote_device_id']==(remote['bus_number']<<16)|remote['device_number'] and (s['vendor_id'],s['product_id'],remote['speed'])==expected and live[typ+'_generation']==f"{generation['filesystem_device']}:{generation['inode']}" and receipt['kernel_socket_inode']>0)
    self.check('kernel',r['channel_id']+' full IF/alternate receipt',receipt['profile']['configuration']==1 and len(receipt['profile']['interfaces'])==1 and receipt['profile']['interfaces'][0]['number']==0 and receipt['profile']['interfaces'][0]['alternate']==0 and (receipt['profile']['interfaces'][0]['class'],receipt['profile']['interfaces'][0]['subclass'],receipt['profile']['interfaces'][0]['protocol'])==((3,1,1) if typ=='hid' else (255,255,255)))
  self.check('kernel','two reuses retain bus names but have new address/inodes',all(next(r for r in receipt_sets[0] if r['target']['device_key']==key)['state']['receipt']['session']['local_bus_id']==next(r for r in receipt_sets[1] if r['target']['device_key']==key)['state']['receipt']['session']['local_bus_id'] and next(r for r in receipt_sets[0] if r['target']['device_key']==key)['state']['receipt']['session']['local_device_generation']!=next(r for r in receipt_sets[1] if r['target']['device_key']==key)['state']['receipt']['session']['local_device_generation'] for key in keys))
  # Raw hardware I/O; no inferred byte count from app metrics.
  transmitted=b''
  for round_id in [1,2,3]:
   rx=f'RUSTUSB-RX-{round_id}-0001:0123456789abcdef\n'.encode();tx=f'RUSTUSB-TX-{round_id}-0001:fedcba9876543210\n'.encode();transmitted+=tx
   self.check('real_io',f'FTDI round{round_id} exact 35B both directions',len(rx)==len(tx)==35 and self.read('client',f'rx-{round_id}.bin')==rx==(self.root/f'ftdi-host-injected-{round_id}.bin').read_bytes() and self.read('client',f'tx-{round_id}.bin')==tx)
  self.check('real_io','host really received serial concatenation',transmitted==(self.root/'ftdi-host-rx.bin').read_bytes() and len(transmitted)==105)
  for round_id in [1,3]:
   b=self.read('client',f'input-{round_id}.bin');self.check('real_io',f'evdev round{round_id} full records',bool(b) and len(b)%24==0);events=[struct.unpack('<qqHHi',b[o:o+24]) for o in range(0,len(b),24)];ke=[(x[2],x[3],x[4]) for x in events];self.check('real_io',f'evdev round{round_id} actual key down/up',(1,round_id+1,1) in ke and (1,round_id+1,0) in ke)
  qmp=[json.loads(l) for l in (self.root/'qmp-input.jsonl').read_text().splitlines()];self.check('real_io','actual QMP requests KEY1/KEY3 only',[e['round'] for e in qmp]==[1,3] and [e['arguments']['keys'][0]['data'] for e in qmp]==['1','3'])
  # Targeted stop: exact other-side capability must survive, not merely a new attachment.
  first={r['target']['device_key']:r for r in receipt_sets[0]};target=self.obj('client','root-target-stopped.json');prekill=self.obj('client','root-before-kill.json')
  self.check('targeted_stop','same serial import held through additional I/O',target['records']==prekill['records']==[first[serial]])
  initial_exports={r['registration']['device_key']:r for r in self.obj('server','root-held-1.json')['exports']};target_exports={r['registration']['device_key']:r for r in self.obj('server','root-target-stopped.json')['exports']}
  self.check('targeted_stop','only HID stub retired, exact serial stub retained',target_exports[hid]['stub'] is None and target_exports[serial]==initial_exports[serial])
  target_sessions=self.obj('server','server-target-stopped.json')['response']['sessions'];incoming={s['device_key']:s for s in target_sessions if s['role']=='incoming'}
  self.check('targeted_stop','actual requested session and unaffected serial state',incoming[hid]['terminal'] and incoming[hid]['cleanup']=='verified' and incoming[hid]['stop_requested'] and incoming[serial]['state']=='Connected' and not incoming[serial]['terminal'] and all(s['state']=='Available' and not s['terminal'] for s in target_sessions if s['role']=='export'))
  # Crash boundary and exact proof bridge.
  before=self.obj('client','agent-before-kill.json');after=self.obj('client','agent-after-kill.json');old=next(s for s in before['sessions'] if s['device_key']==serial);still=next(s for s in after['sessions'] if s['id']==old['id']);recovered=self.obj('client','client-serial-recovered.json')['response']['sessions'][0]
  self.check('crash_recovery','actual reap137 and old Connected held capability',self.text('client','wait-agent-kill.txt')=='137\n' and old['state']=='Connected' and old['cleanup'] is None and not old['terminal'] and len(old['attempts'])==1 and old['attempts'][0]['resource_id']==first[serial]['channel_id'])
  self.check('crash_recovery','app crash alone did not record release',identity(old)==identity(still) and still['state']=='Connected' and still['cleanup'] is None and not still['terminal'] and still['attempts']==old['attempts'])
  postroot=self.obj('client','root-after-kill.json');self.check('crash_recovery','helper retired after true kernel cleanup',postroot['records']==postroot['exports']==[] and len(postroot['terminals'])==2)
  release=[e['terminal'] for e in postroot['terminals'] if e['terminal']['correlation']==old['attempts'][0]['correlation']];self.check('crash_recovery','old exact root proof, not new epoch resource zero',len(release)==1 and release[0]['resource_id']==old['attempts'][0]['resource_id'] and release[0]['proof']=='native_import_released' and release[0]['device_key']==serial and release[0]['owner_uid']==2000)
  self.check('crash_recovery','new agent preserved old identities and adopted only exact proof',identity(recovered)==identity(old) and recovered['terminal'] and recovered['cleanup']=='verified' and recovered['evidence']=='root_journal_proof' and recovered['attempts'][0]['proof']==release[0]['proof'] and attempt(recovered['attempts'][0])==attempt(old['attempts'][0]))
  client_s0=(self.root/'client-serial.log').read_text();session=first[serial]['state']['receipt']['session'];self.check('crash_recovery','old local address actual kernel disconnect',f"usb {session['local_bus_id']}: USB disconnect, device number {session['local_device_address']}" in client_s0)
  # Server device lifetime: unchanged hardware, unconfigured on bind, full IF on import, full original on unexport.
  base=self.obj('server','host-before.json');dynamic={'configuration','num_interfaces','configuration_bytes','count_bytes','interfaces','device_driver','usbip_status'}
  hard=lambda rows:[{k:v for k,v in r.items() if k not in dynamic} for r in rows]
  self.check('driver_baseline','two actual original interface drivers',len(base)==2 and sorted(i['driver'] for r in base for i in r['interfaces'])==['ftdi_sio','usbhid'])
  for epoch,round_id in [(1,1),(2,3)]:
   unconfigured=self.obj('server',f'host-exported-{epoch}.json');held=self.obj('server',f'host-held-{round_id}.json');restored=self.obj('server',f'host-restored-{epoch}.json')
   self.check('driver_baseline',f'epoch{epoch} same original hardware generation',hard(base)==hard(unconfigured)==hard(held))
   self.check('driver_baseline',f'epoch{epoch} true null configuration on export',all(r['device_driver']=='usbip-host' and r['usbip_status']==1 and r['configuration']==r['num_interfaces']=='' and r['configuration_bytes']==r['count_bytes']==0 and r['interfaces']==[] for r in unconfigured))
   self.check('driver_baseline',f'epoch{epoch} actual configured whole IF while USED',all(r['device_driver']=='usbip-host' and r['usbip_status']==2 and int(r['configuration'])==int(r['num_interfaces'])==1 and r['interfaces']==[dict(i,driver='') for i in base[n]['interfaces']] for n,r in enumerate(held)))
   self.check('driver_baseline',f'epoch{epoch} complete original driver/configuration/IF restoration',restored==base)
   self.check('execution',f'server epoch{epoch} actual TERM wait0',self.text('server',f'wait-agent-{epoch}.txt')=='0\n')
  self.check('execution','client final actual TERM wait0',self.text('client','wait-agent-2.txt')=='0\n')
  controls=[json.loads(l) for l in (self.root/'host-control.jsonl').read_text().splitlines()];self.check('ordering','host control monotonic targeted-stop/crash/reuse sequence',all(b['monotonic']>a['monotonic'] for a,b in zip(controls,controls[1:])) and [x['mark'] for x in controls]==['keys','start','held-1','stop-hid','hid-stopped','kill-restart','client-recovered','restart-server','restart-client','held-3','close-client','closed-client','finish'])
  self.facts.update({'source_inputs':source_count,'manifest_files':len(summary['artifact_manifest']),'decoded_guest_files':{r:len(v) for r,v in self.raw.items()},'kernel':summary['kernel'],'family':summary['family'],'serial_each_direction_bytes':105,'hid_key_codes':[2,4],'agent_exit_codes':{'server_epoch1':0,'server_epoch2':0,'client_epoch1':137,'client_epoch2':0},'operation_cleanup_distinction':'Old HID client retained operation_error=io with verified cleanup; SIGKILL old serial has no completed operation return, and null operation_error is not a success claim.','local_gone_evidence':'Observed VHCI NULL, old-address kernel disconnect, exact native_import_released proof and archived successful guest absence assertion. No standalone sysfs-tree absence dump was captured.','pending_urb_scope':'Serial tty FD4 was closed after round2 before SIGKILL. Connected/held-capability death is demonstrated; no claim a specific URB or ioctl was pending at the signal.'})
 def configurations(self):
  host_der={name:(self.root/f'{name}-public.der').read_bytes() for name in ['ca','server','client']}
  fingerprints={name:sha(content) for name,content in host_der.items()}
  device_keys=self.obj('server','keys.json');family=self.summary['family'];records=[]
  for role in ['server','client']:
   original_manifest=self.obj(role,'agent-manifest.json');original_policy=self.obj(role,'helper-policy.json')
   names=['server'] if role=='server' else ['hid','serial']
   definitions=[{'id':name,'backend':'native' if role=='server' else 'client','config':'/etc/rustusb/'+('server.json' if role=='server' else 'client-'+name+'.json')} for name in names]
   required_manifest={'schema_version':1,'broker_uid':2000,'helper_socket':'/run/rustusb/control.sock','control_directory':'/run/agent','data_directory':'/var/lib/rustusb/agent','profiles':definitions,'operators':[{'uid':1000,'profiles':names,'actions':['read','start','stop','discover','audit']}]}
   self.check('actual_config',role+' actual manifest exact endpoint/profile/OS ACL',original_manifest==required_manifest)
   self.check('actual_config',role+' original helper broker policy exact role',original_policy['secure_broker_uid']==2000 and original_policy['allowed_uids']==[1000] and original_policy['export_device_ids']==original_policy['peers']==[] and original_policy['trusted_network'] is False)
   if role=='server':self.check('actual_config','actual helper Native pool equals published TLS keys',original_policy['secure_native_device_ids']==[key.split(':',1)[1] for key in device_keys])
   else:self.check('actual_config','actual helper import policy equals TLS logical server/device pair',original_policy['secure_imports']==[{'server_id':'qemu-server','device_keys':device_keys}])
   dirs={'/','/etc','/etc/rustusb','/run','/run/agent','/var','/var/lib','/var/lib/rustusb','/var/lib/rustusb/agent'}
   public_names={'agent.json','policy.json','ca.der','server.der','client.der','client-pin.txt'}|({ 'server.json'} if role=='server' else {'client-hid.json','client-serial.json'})
   private_path=f'/etc/rustusb/{role}-key.der';paths=dirs|{'/etc/rustusb/'+name for name in public_names}|{private_path};previous=None
   for epoch in [1,2]:
    prefix=f'actual-config-{epoch}-';m=self.obj(role,f'config-metadata-{epoch}.json');lines=[json.loads(line) for line in self.text(role,f'config-metadata-{epoch}.jsonl').splitlines()]
    self.check('config_metadata',f'{role} epoch{epoch} raw JSONL/full JSON identical',m==sorted(lines,key=lambda x:x['path']))
    table={entry['path']:entry for entry in m};self.check('config_metadata',f'{role} epoch{epoch} full fixed path coverage',len(m)==len(table)==len(paths) and set(table)==paths)
    self.check('actual_config',f'{role} epoch{epoch} actual manifest/policy byte identity',self.read(role,prefix+'agent.json')==self.read(role,'agent-manifest.json') and self.read(role,prefix+'policy.json')==self.read(role,'helper-policy.json'))
    for cert in ['ca','server','client']:
     captured=self.read(role,prefix+cert+'.der');self.check('actual_config',f'{role} epoch{epoch} actual {cert} DER bytes/SHA',captured==host_der[cert] and sha(captured)==fingerprints[cert])
    self.check('actual_config',f'{role} epoch{epoch} actual public pin raw exact bytes',self.read(role,prefix+'client-pin.txt')==(fingerprints['client']+'\n').encode())
    if role=='server':
     document=self.obj(role,prefix+'server.json')
     needed={'schema_version':1,'broker_uid':2000,'listen':'192.0.2.1:3241' if family=='ipv4' else '[fd42:7275:7374::1]:3241','devices':device_keys,'principals':[{'id':'qemu-client','pins':[fingerprints['client']],'devices':device_keys}],'ca_certificates':['/etc/rustusb/ca.der'],'certificate_chain':['/etc/rustusb/server.der'],'private_key':private_path}
     self.check('actual_config',f'server epoch{epoch} actual TLS complete JSON mapping',document==needed)
     application=self.obj('server',f'agent-held-{1 if epoch==1 else 3}.json');incoming=[s for s in application['sessions'] if s['role']=='incoming' and not s['terminal']]
     self.check('actual_config',f'server epoch{epoch} actual admitted principal/device matches captured config',len(incoming)==2 and {s['device_key'] for s in incoming}==set(document['devices']) and all(s['principal']==document['principals'][0]['id'] for s in incoming))
    else:
     for profile,key in zip(['hid','serial'],device_keys):
      document=self.obj(role,prefix+'client-'+profile+'.json');needed={'schema_version':1,'broker_uid':2000,'server_id':'qemu-server','endpoint':{'host':'192.0.2.1' if family=='ipv4' else 'fd42:7275:7374::1','port':3241},'server_name':'rustusb-qemu.test','device':key,'ca_certificates':['/etc/rustusb/ca.der'],'certificate_chain':['/etc/rustusb/client.der'],'private_key':private_path}
      self.check('actual_config',f'client epoch{epoch} actual {profile} full TLS JSON mapping',document==needed)
      observed=self.obj(role,f'client-{profile}-connected-{epoch}.json')['response']['sessions'];active=[s for s in observed if not s['terminal']]
      self.check('actual_config',f'client epoch{epoch} actual {profile} import uses captured device',len(active)==1 and active[0]['device_key']==document['device'] and active[0]['profile']==profile)
    for path,entry in table.items():
     self.check('config_metadata',f'{role} epoch{epoch} {path} exact observed path/identity',entry['canonical']==path and entry['inode']>0 and entry['device']>=0)
     if path in dirs:
      broker=path in ['/run/agent','/var/lib/rustusb/agent'];mode=int(entry['mode'],8)
      self.check('config_metadata',f'{role} epoch{epoch} {path} ancestor owner/mode',entry['kind']=='directory' and entry['captured_file'] is None and entry['uid']==entry['gid']==(2000 if broker else 0) and (mode==(0o711 if path=='/run/agent' else 0o700) if broker else mode&0o022==0))
     elif path==private_path:
      self.check('config_metadata',f'{role} epoch{epoch} own private key metadata only',entry['kind']=='private_key_metadata_only' and entry['uid']==entry['gid']==2000 and int(entry['mode'],8)==0o600 and entry['links']==1 and entry['bytes']>0 and entry['captured_file'] is None)
     else:
      capture=prefix+path.rsplit('/',1)[1];self.check('config_metadata',f'{role} epoch{epoch} {path} actual public file mode/size',entry['kind']=='public_file' and entry['uid']==entry['gid']==0 and int(entry['mode'],8)==0o644 and entry['links']==1 and entry['captured_file']==capture and entry['bytes']==len(self.read(role,capture)))
     if previous:
      self.check('config_metadata',f'{role} {path} actual same object/permissions across restart',all(entry[k]==previous[path][k] for k in ['canonical','kind','uid','gid','mode','links','device','inode']))
    if epoch==2:
     self.check('actual_config',role+' all actual public bytes retained across restart',all(self.read(role,prefix+name)==self.read(role,'actual-config-1-'+name) for name in public_names))
    previous=table
    records.append({'role':role,'epoch':epoch,'metadata_entries':len(m),'actual_public_files':{name:{'bytes':len(self.read(role,prefix+name)),'sha256':sha(self.read(role,prefix+name))} for name in sorted(public_names)},'private_key_metadata':table[private_path]})
   self.check('actual_config',role+' no private-key artifact captured',not any(name.endswith('-key.der') for name in self.raw[role]))
  self.facts['actual_configuration']={'captures':records,'public_certificate_der_sha256':fingerprints,'scope':'Actual post-startup guest public JSON/DER/pin bytes and path ownership metadata are captured in both epochs; private key content was neither exported nor independently hashed. This does not independently decrypt PCAP or attest private-key bytes.'}
 def report(self):
  result='PASS'
  try:self.run();self.configurations()
  except Exception as e:result='FAIL';self.error=f'{type(e).__name__}: {e}'
  return {'schema_version':1,'result':result,'evidence':str(self.root),'head':EXPECTED,'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'method':'Independent parser of raw base64/S0/journals/kernel records; no import/call of runner.verify, history_identity or pcap_review. Author of harness also authored this separate parser, so this is independent method, not independent reviewer identity.','checks':self.checks,'check_count':len(self.checks),'category_counts':dict(collections.Counter(x['category'] for x in self.checks)),'facts':self.facts,'preserved_kernel_warning_lines':self.warnings,'error':getattr(self,'error',None),'limits':['Assertion counts are audit comparisons, not USB cases.','Only supplied completed dataset audited; no QEMU, Cargo, build, product executable, USB operation or repository edit.','PCAP originals are hash-checked only here; this lifecycle report does not independently establish TLS packet classification.','Reuses the cb08672 actual public configuration/pin/metadata parser; all historical scripts/reports remain unchanged. Private key bytes remain deliberately uncollected.','No physical hardware, virtual/USBFS managed path, full Phase3 completion, performance, or pending-URB crash claim.']}
def main():
 global EXPECTED_BINARIES
 a=argparse.ArgumentParser();a.add_argument('--evidence',type=pathlib.Path,required=True);a.add_argument('--output',type=pathlib.Path,required=True);a.add_argument('--expected-binaries',type=pathlib.Path,required=True);args=a.parse_args()
 EXPECTED_BINARIES=json.loads(args.expected_binaries.read_text())
 if set(EXPECTED_BINARIES)!={'rustusb','rustusb-helper'} or not all(re.fullmatch('[0-9a-f]{64}',v) for v in EXPECTED_BINARIES.values()):raise ValueError('exact completed-build locked ELF map required')
 if args.output.exists():raise FileExistsError('preserve earlier reviewer result')
 r=Audit(args.evidence.resolve()).report();r['parser_sha256']=sha(pathlib.Path(__file__).read_bytes());r['source_lock_sha256']=sha(pathlib.Path('/tmp/rustusb-prepare-stop-legacy-nine-review-20261024/source-lock.json').read_bytes());r['reuse']={'original':'/tmp/rustusb-agent-config-independent-raw-audit-20261024.py','sha256':'6d60e2dd0c035c6bcb12f97f1fec0d206c62ceec786da1ac679f6f6b7236b706','changes':'1582f06 source/ELF and local software-manifest provenance; added strict private application disk2/association/raw-v1/unchanged DTO checks; all prior lifecycle/configuration assertions retained.'};args.output.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps({'result':r['result'],'checks':r['check_count'],'categories':r['category_counts'],'error':r['error'],'output':str(args.output)}));return int(r['result']!='PASS')
if __name__=='__main__':raise SystemExit(main())
