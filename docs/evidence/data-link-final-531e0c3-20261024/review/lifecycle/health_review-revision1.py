#!/usr/bin/env python3
"""Standalone raw lifecycle review. Standard library only; no harness imports.
Current reviewer authored Rust guest fragments/verifier and related service code.
Independent evidence method does not mean independent author identity.
"""
import base64,collections,hashlib,ipaddress,json,re,struct,subprocess,traceback
from pathlib import Path
BASE=Path('/tmp/rustusb-data-link-ready-531e0c3-20261024')
OUT=Path(__file__).resolve().parent
REPO=Path('/home/ubuntu/github/usbip-client')
HEAD='531e0c32a4b0e7252ca771ab4f10042c71c8c195'
PHASES=('c2s','s2c','both')
ELF=json.loads((OUT/'compat/expected-binaries.json').read_bytes())
GLOBAL={r['path']:r for r in json.loads((BASE/'inputs-before.json').read_bytes())}
def sha(x):return hashlib.sha256(x).hexdigest()
def obj(p):return json.loads(p.read_bytes())
def meta(p):
 b=p.read_bytes();return dict(path=str(p),bytes=len(b),sha256=sha(b))
def unique(rows,fn):
 matches=[x for x in rows if fn(x)]
 if len(matches)!=1:raise AssertionError('expected one exact record; got '+str(len(matches)))
 return matches[0]
def socket_address(s):
 if s.startswith('['):host,port=s[1:].split(']:')
 else:host,port=s.rsplit(':',1)
 return str(ipaddress.ip_address(host)),int(port)
def tcp(frame):
 if len(frame)<14:raise AssertionError('truncated ethernet')
 kind=int.from_bytes(frame[12:14],'big');packet=frame[14:]
 if kind==0x800:
  if len(packet)<20:raise AssertionError('short IPv4')
  if packet[9]!=6:return None
  n=(packet[0]&15)*4;total=int.from_bytes(packet[2:4],'big')
  if n<20 or total>len(packet) or int.from_bytes(packet[6:8],'big')&0x3fff:raise AssertionError('invalid TCP IPv4')
  src,dst=str(ipaddress.ip_address(packet[12:16])),str(ipaddress.ip_address(packet[16:20]));body=packet[n:total];family=4
 elif kind==0x86dd:
  if len(packet)<40:raise AssertionError('short IPv6')
  nh=packet[6];limit=40+int.from_bytes(packet[4:6],'big');n=40
  if limit>len(packet):raise AssertionError('invalid IPv6 length')
  # Actual captures use one HBH only for MLD; still follow it if it carries TCP.
  if nh==0:
   if n+2>limit:raise AssertionError('short HBH')
   nh=packet[n];n+=(packet[n+1]+1)*8
  if nh!=6:return None
  if n>limit:raise AssertionError('invalid TCP HBH')
  src,dst=str(ipaddress.ip_address(packet[8:24])),str(ipaddress.ip_address(packet[24:40]));body=packet[n:limit];family=6
 else:return None
 if len(body)<20:raise AssertionError('short TCP')
 offset=(body[12]>>4)*4
 if offset<20 or offset>len(body):raise AssertionError('TCP data offset')
 sport,dport,seq,ack=struct.unpack('!HHII',body[:12])
 return dict(flow=[family,src,sport,dst,dport],sequence=seq,acknowledgement=ack,flags=body[13],payload_length=len(body)-offset)
def pcap(p):
 raw=p.read_bytes()
 if raw[:4]==b'\xd4\xc3\xb2\xa1':end='<'
 elif raw[:4]==b'\xa1\xb2\xc3\xd4':end='>'
 else:raise AssertionError('unsupported pcap magic')
 if len(raw)<24 or struct.unpack(end+'I',raw[20:24])[0]!=1:raise AssertionError('not Ethernet pcap')
 frames=[];offset=24
 while offset<len(raw):
  if offset+16>len(raw):raise AssertionError('partial pcap header')
  sec,usec,n,original=struct.unpack(end+'IIII',raw[offset:offset+16]);offset+=16
  if n!=original or offset+n>len(raw):raise AssertionError('truncated pcap packet')
  frames.append(raw[offset:offset+n]);offset+=n
 return frames
class Review:
 def __init__(self,name):self.name=name;self.base=BASE/name;self.checks=[];self.facts={};self.raw={};self.logs={}
 def check(self,category,label,value):
  self.checks.append(dict(category=category,label=label,passed=bool(value)))
  if not value:raise AssertionError(category+': '+label)
 def data(self,role,name):return json.loads(self.raw[role][name])
 def proof(self,root,session,physical=False):
  self.check('rootproof','exact one attempt '+session['id'],len(session['attempts'])==1)
  a=session['attempts'][0];terms=root['terminals']if physical else[x['terminal']for x in root['terminals']]
  t=unique(terms,lambda x:x['correlation']==a['correlation'])
  expected={'native_import':'native_import_released','native_stub':'native_stub_available','native_export':'native_export_restored','usbfs_grant':'physical_baseline_restored'}
  self.check('rootproof','full original namespace/identity/UID '+session['id'],t['kind']==a['kind'] and t['device_key']==session['device_key'] and t['resource_id']==a['resource_id'] and t['owner_uid']==2000 and t['proof']==expected[a['kind']] and a['proof']in(None,t['proof']) and a['correlation']['instance_id']==session['instance_id'] and a['correlation']['session_id']==session['id'])
  return t
 def state(self,role,name):
  d=self.data(role,name);sessions=d['sessions'];h=d['link_history']
  self.check('private_disk','disk2 and exact health/session association '+role+'/'+name,set(d)=={'schema_version','sequence','jobs','sessions','audit','requests','link_history'} and d['schema_version']==2 and len(h)==len(sessions)==len({x['session_id']for x in h}) and {x['session_id']for x in h}=={s['id']for s in sessions})
  for entry in h:
   session=unique(sessions,lambda x:x['id']==entry['session_id'])
   self.check('private_disk','qualified same epoch '+entry['session_id'],set(entry)=={'session_id','instance_id','link'} and entry['instance_id']==session['instance_id'])
  return d
 def health(self,role,phase,stage,session):
  d=self.data(role,f'health-{phase}-{role}-{stage}.json');h=unique(d['response']['sessions'],lambda x:x['session_id']==session['id'])
  self.check('health','actual IPC1 bounded observation '+role+phase+stage,d['schema_version']==1 and d['journal_healthy']is True and d['response']['result']=='session_health' and d['response']['next_after']is None and h['instance_id']==session['instance_id'] and h['role']==session['role'])
  return h
 def vhci(self,name,count):
  rows=[x.split()for x in self.raw['client'][name].decode().splitlines()if x.startswith(('hs ','ss '))]
  self.check('kernel','actual VHCI '+name,bool(rows)and sum(int(x[2])==6 for x in rows)==count and all(int(x[2])in(4,6)for x in rows));return rows
 def artifacts(self):
  s=obj(self.base/'summary.json');self.s=s
  self.check('provenance','terminal clean exact source/binaries',s['result']=='PASS' and s['head']==s['head_after']==HEAD and s['git_status_before']==s['git_status_after']=='' and all(s[k]is True for k in ['source_guard','binary_guard','head_guard','clean_guard']) and s['binary_sha256']==ELF and s['guest_exit_codes']=={'server':0,'client':0})
  self.check('provenance','all-features product',s['build_features']==['rust-backend','physical-nusb'])
  for path,row in s['source_inputs'].items():
   g=GLOBAL[path];b=(self.base/'source'/path).read_bytes()
   self.check('source_blob',path,sha(b)==row['sha256']==g['sha256'] and len(b)==row['bytes']==g['bytes'])
  for path,row in s['artifact_manifest'].items():
   b=(self.base/path).read_bytes()
   self.check('artifact_hash',path,sha(b)==row['sha256'] and len(b)==row['bytes'])
  decoded={};livecounts={}
  for role in ['client','server']:
   text=(self.base/f'{role}-evidence.log').read_text();self.logs[role]=text
   self.check('guest','one complete PASS and no collector failure '+role,text.count(f'RUSTUSB_LINK_RESULT: PASS role={role}')==1 and all(x not in text for x in ['RUSTUSB_LINK_RESULT: FAIL','SECURE_ARTIFACT_ERROR','SECURE_ARTIFACT_OVERSIZE','SECURE_POWEROFF_RETURNED']))
   kernel=(self.base/f'{role}-serial.log').read_text(errors='replace')
   self.check('guest','no panic/Oops '+role,'Kernel panic'not in kernel and 'Oops:'not in kernel)
   raw={}
   for m in re.finditer(r'^SECURE_ARTIFACT_BEGIN ([\w.-]+) (\d+)\n(.*?)^SECURE_ARTIFACT_END \1\s*$',text,re.M|re.S):
    name,n,b64=m.groups();b=base64.b64decode(''.join(b64.split()),validate=True)
    self.check('base64',role+'/'+name,name not in raw and len(b)==int(n) and b==(self.base/f'{role}-artifacts'/name).read_bytes());raw[name]=b
   self.check('base64','exact final file coverage '+role,set(raw)=={p.name for p in(self.base/f'{role}-artifacts').iterdir()if p.is_file()})
   self.raw[role]=raw;decoded[role]=len(raw);live={}
   for m in re.finditer(r'^LINK_RAW_BEGIN ([\w.-]+) (\d+)\n(.*?)^LINK_RAW_END \1\s*$',text,re.M|re.S):
    name,n,b64=m.groups();b=base64.b64decode(''.join(b64.split()),validate=True)
    self.check('live_base64',role+'/'+name,len(b)==int(n)and b==raw[name]and b==(self.base/'live'/role/name).read_bytes()and(name not in live or live[name]==b));live[name]=b
   livecounts[role]=len(live)
   self.check('elf','actual proc executable receipts '+role,raw['executable-agent-1.txt'].decode().split()[0]==ELF['rustusb'] and raw['executable-helper.txt'].decode().split()[0]==ELF['rustusb-helper'])
   self.check('identity','actual broker/helper and TERM0 '+role,re.search(r'^Uid:\s+2000\s+2000\s+2000\s+2000$',raw['identity-agent-1.txt'].decode(),re.M) and re.search(r'^Uid:\s+0\s+0\s+0\s+0$',raw['identity-helper.txt'].decode(),re.M) and raw['wait-agent-1.txt'].strip()==b'0')
   self.check('credentials','actual role key/RNG marker '+role,f'SECURE_CREDENTIAL_ISOLATION role={role} own_key=true opposite_key=false ca_key=false'in text and 'SECURE_RNG_RTC_READY crng=true'in text and not any(name.endswith('-key.der')for name in raw))
  self.facts.update(decoded_guest_files=decoded,decoded_live_files=livecounts,source_inputs=len(s['source_inputs']),artifact_manifest_files=len(s['artifact_manifest']),binary_sha256=ELF,kernel=s['kernel'])
 def network(self):
  events=[json.loads(x)for x in(self.base/'valve.jsonl').read_text().splitlines()];control=[json.loads(x)for x in(self.base/'host-control.jsonl').read_text().splitlines()];self.events=events;self.arms={e['rule_id']:e for e in events if e['event']=='arm'};self.disarms={e['rule_id']:e for e in events if e['event']=='disarm'}
  self.check('valve','exact three directions and monotonic complete log',set(self.arms)==set(self.disarms)==set(PHASES) and [e['index']for e in events]==list(range(1,len(events)+1)) and all(a['monotonic_ns']<=b['monotonic_ns']for a,b in zip(events,events[1:])) and events[-1]['event']=='close'and events[-1]['finished']and events[-1]['failure']is None)
  start=unique(control,lambda e:e['command']=='START')['monotonic_ns'];fds=events[0]['endpoint_fds']
  for role in ['client','server']:
   ready=self.data(role,'network-ready.json');ack=unique(control,lambda e:e['role']==role and e['command']=='NETWORK_READY_ACK')
   observations=[json.loads(x)for x in self.raw[role]['network-ready-observations.jsonl'].decode().splitlines()]
   self.check('readiness','actual carrier gate before START '+role,ready['carrier']==1 and ready['operstate']=='up'and observations[-1]==ready and ack['monotonic_ns']<start and ack['observed']['carrier']==1 and ack['observed']['operstate']=='up')
   for n,h in ack['observed']['artifact_sha256'].items():self.check('readiness','exact acknowledged source '+role+n,sha(self.raw[role][n])==h)
   if role=='client':self.check('readiness','guest probes after host ACK',self.logs[role].index('LINK_NETWORK_READY_ACK role=client')<self.logs[role].index('LINK_NETWORK_PROBES_START'))
  actual={};packets={};decoded={}
  for role in ['client','server']:
   frames=pcap(self.base/f'{role}-external.pcap');actual[role]=collections.Counter(sha(b)for b in frames)
   for b in frames:decoded[sha(b)]=tcp(b)
  expected={r:collections.Counter()for r in actual};forwarded=set();drops=collections.Counter()
  for e in events:
   if e['event']=='packet':
    self.check('packet','unique actual packet '+str(e['packet_id']),e['packet_id']not in packets and e['sha256']in decoded)
    packets[e['packet_id']]=e;expected[e['origin']][e['sha256']]+=1
    if e['packet']['kind']=='tcp':
     p=decoded[e['sha256']];self.check('packet','independent TCP decode '+str(e['packet_id']),p is not None and all(p[k]==e['packet'][k]for k in p))
    if e['action']=='drop':
     arm=self.arms[e['rule_id']];disarm=self.disarms[e['rule_id']];p=decoded[e['sha256']]
     self.check('drop','exact bound TCP tuple/direction/window '+str(e['packet_id']),p is not None and p['flow']==arm['flow'] and arm['index']<e['index']<disarm['index'] and (arm['direction']=='both'or e['origin']==('client'if arm['direction']=='c2s'else'server')))
     drops[(e['rule_id'],e['origin'])]+=1
   elif e['event']=='forward':
    p=packets[e['packet_id']];self.check('forward','queued original delivered once '+str(e['packet_id']),p['action']=='queue'and e['packet_id']not in forwarded and e['origin']==p['origin']and e['destination']!=p['origin']);forwarded.add(e['packet_id']);expected[e['destination']][p['sha256']]+=1
  for role in actual:self.check('pcap','all captured frame multiplicities equal original ingress plus real forwards '+role,actual[role]==expected[role])
  self.check('valve','no queued packet loss',forwarded=={n for n,e in packets.items()if e['action']=='queue'})
  for phase in PHASES:
   a,d=self.arms[phase],self.disarms[phase]
   self.check('valve','retained exact endpoint FDs '+phase,a['endpoint_fds']==d['endpoint_fds']==fds and a['buffers_empty']and d['buffers_empty'])
   self.check('valve','actual drops match retired counters '+phase,d['dropped']=={r:drops[(phase,r)]for r in ['client','server']}and sum(d['dropped'].values())>0)
   self.check('valve','terminal evidence existed before network recovery '+phase,d['terminal_evidence_sha256']==sha((self.base/'bindings'/f'{phase}-terminal.json').read_bytes()) and all(e['monotonic_ns']>=d['monotonic_ns']for e in control if e['command']=='NEXT_'+phase))
   # TCP sequence ownership is independently visible before arming, not inferred from port reuse.
   syns=[p for p in packets.values()if p['index']<a['index']and p['packet']['kind']=='tcp'and p['packet']['flow']==a['flow']and p['packet']['flags']&2]
   self.check('valve','actual TCP handshake sequence identity '+phase,any(p['origin']=='client'and p['packet']['sequence']==a['syn_sequences'][0]for p in syns)and any(p['origin']=='server'and p['packet']['sequence']==a['syn_sequences'][1]for p in syns))
  self.facts['wire_ownership']={'pcap_frames':{r:sum(c.values())for r,c in actual.items()},'original_packets':len(packets),'forwarded':len(forwarded),'drops':{p:{r:drops[(p,r)]for r in actual}for p in PHASES},'scope':'Independent Ethernet frame-hash accounting and TCP header/tuple/sequence/drop parsing; encrypted h2 PING bytes are not decoded or claimed visible.'}
 def lifecycle(self):
  backend=self.s['backend'];keys=self.data('client','keys.json');self.check('keys','two identical real device keys',keys==self.data('server','keys.json')==obj(self.base/'keys.json')and len(set(keys))==2)
  finals={role:self.state(role,'agent-final.json')for role in ['client','server']};serials={};targets={'client':[],'server':[]};facts=[]
  baseline=None if backend=='virtual'else self.data('server','host-before.json')
  if baseline is not None:self.check('baseline','complete original driver/configuration/generation restored',baseline==self.data('server','host-restored.json'))
  grants=self.data('server','root-initial-exported.json')if backend=='usbfs'else None
  for phase in PHASES:
   phasefact={'phase':phase,'endpoints':[]};pair={}
   for role,wanted in [('client','import'),('server','incoming')]:
    before=self.state(role,'agent-pre-'+phase+'.json');after=self.state(role,'agent-fault-'+phase+'.json')
    target=unique(before['sessions'],lambda x:x['role']==wanted and x['device_key']==keys[0]and not x['terminal']);end=unique(after['sessions'],lambda x:x['id']==target['id']);targets[role].append(target)
    self.check('lifecycle','same actual target terminal '+role+phase,all(target[k]==end[k]for k in ['id','instance_id','job_id','device_key','role','profile','attempts'])and end['terminal']and end['state']=='Disconnected'and end['cleanup']=='verified')
    previous=unique(before['sessions'],lambda x:x['role']==wanted and x['device_key']==keys[1]and not x['terminal']);retained=unique(after['sessions'],lambda x:x['id']==previous['id'])
    beforelink=unique(before['link_history'],lambda x:x['session_id']==previous['id'])['link'];afterlink=unique(after['link_history'],lambda x:x['session_id']==previous['id'])['link']
    identity=(previous['id'],previous['instance_id'],previous['job_id'],previous['attempts'],beforelink['identity'])
    if role in serials:self.check('sibling','same original serial session/attempt/TCP across faults '+role+phase,serials[role]==identity)
    serials[role]=identity
    self.check('sibling','original serial remains Connected and same actual link '+role+phase,all(previous[k]==retained[k]for k in ['id','instance_id','job_id','device_key','attempts','state','cleanup','terminal'])and retained['state']=='Connected'and retained['operation_error']is None and beforelink['identity']==afterlink['identity']and afterlink['end']is None)
    prior=self.health(role,phase,'pre',target);terminal=self.health(role,phase,'terminal',end);a=prior['link'];z=terminal['link'];pair[role]=a['identity']
    self.check('health','pre-arm matched reply/RTT on this actual live data connection '+role+phase,prior['availability']=='live'and a['protocol']=='framed_v2'and a['phase']=='open'and a['end']is None and a['last_reply_sequence']is not None and 0<a['last_reply_sequence']<=a['last_probe_sequence']and 0<a['last_round_trip_micros']and a['last_reply_millis']<=a['elapsed_millis'])
    arm=self.arms[phase];binding=arm['binding'];original=self.raw[role][f'health-{phase}-{role}-pre.json']
    self.check('health','arm source binds actual session/epoch/connection '+role+phase,binding[role+'_session']==target['id']and binding[role+'_instance']==target['instance_id']and binding[role+'_connection']==a['identity']['connection']and binding[role+'_health_sha256']==sha(original)and original==(self.base/'bindings'/f'{phase}-{role}-health.json').read_bytes())
    self.check('health','same link actual terminal sample '+role+phase,terminal['availability']=='owner_ended'and z['identity']==a['identity']and z['phase']=='ended'and z['end']is not None and z['elapsed_millis']>=a['elapsed_millis']and z['sample_sequence']>=a['sample_sequence'])
    elapsed=None
    if z['end']=='probe_timeout':
     elapsed=z['elapsed_millis']-z['last_probe_enqueued_millis'];self.check('deadline','actual outstanding probe 3..5 seconds '+role+phase,z['probe']=='timed_out'and end['operation_error']=='timeout'and 3000<=elapsed<=5000 and z['last_reply_sequence']<z['last_probe_sequence'])
    rootproof=None
    if role=='client':
     rb=self.data(role,'root-pre-'+phase+'.json');ra=self.data(role,'root-fault-'+phase+'.json');rootproof=self.proof(ra,end)
     hid=unique(rb['records'],lambda r:r['target']['device_key']==keys[0]);ser=unique(rb['records'],lambda r:r['target']['device_key']==keys[1]);local=self.data(role,'local-fault-'+phase+'.json');rs=ser['state']['receipt']['session'];rh=hid['state']['receipt']['session'];generation=rs['local_device_generation']
     self.check('kernel','only original HID import retired; original serial capability unchanged '+phase,ra['schema_version']==2 and ra['exports']==[]and ra['records']==[ser]and rootproof['resource_id']==hid['channel_id'])
     self.check('kernel','original local HID Gone and exact sibling generation remains '+phase,local['old_local_exists']is False and local['old_local_bus_id']==rh['local_bus_id']and local['sibling_local_bus_id']==rs['local_bus_id']and local['sibling_generation']==f"{generation['filesystem_device']}:{generation['inode']}")
     rows=self.vhci('vhci-fault-'+phase+'.txt',1);old=unique(rows,lambda x:int(x[1])==rh['port']);same=unique(rows,lambda x:int(x[1])==rs['port']);self.check('kernel','exact NULL old port and USED original serial mapping '+phase,int(old[2])==4 and int(same[2])==6 and same[6]==rs['local_bus_id']and int(same[4],16)==rs['remote_device_id']);self.vhci('vhci-pre-'+phase+'.txt',2)
    elif backend=='native':
     rb=self.data(role,'root-pre-'+phase+'.json');ra=self.data(role,'root-fault-'+phase+'.json');rootproof=self.proof(ra,end);old={r['registration']['device_key']:r for r in rb['exports']};new={r['registration']['device_key']:r for r in ra['exports']}
     self.check('native','only target stub retired; original export and sibling unchanged '+phase,rb['records']==ra['records']==[]and set(old)==set(new)==set(keys)and old[keys[1]]==new[keys[1]]and new[keys[0]]['stub']is None and{k:v for k,v in old[keys[0]].items()if k!='stub'}=={k:v for k,v in new[keys[0]].items()if k!='stub'}and rootproof['resource_id']==old[keys[0]]['stub']['channel_id'])
    else:
     self.check('rust_engine','incoming has no helper attempt/proof '+phase,end['attempts']==retained['attempts']==[]and self.raw[role]['native-pre-'+phase+'.status']==self.raw[role]['native-fault-'+phase+'.status']==b'absent\n')
     if backend=='usbfs':self.check('usbfs','full original two grants unchanged '+phase,self.data(role,'root-pre-'+phase+'.json')==self.data(role,'root-fault-'+phase+'.json')==grants and grants['schema_version']==3 and len(grants['records'])==2 and grants['terminals']==[])
     else:self.check('virtual','no physical journal/proof '+phase,self.raw[role]['physical-pre-'+phase+'.status']==self.raw[role]['physical-fault-'+phase+'.status']==b'absent\n')
    phasefact['endpoints'].append({'role':role,'session_id':target['id'],'instance_id':target['instance_id'],'connection':a['identity'],'last_prearm_reply_sequence':a['last_reply_sequence'],'last_prearm_round_trip_micros':a['last_round_trip_micros'],'end':z['end'],'outstanding_millis':elapsed,'operation_error':end['operation_error'],'cleanup':end['cleanup'],'root_terminal':rootproof})
   client=pair['client']['tcp'];server=pair['server']['tcp'];host,port=socket_address(client['local']);peer,serviceport=socket_address(client['peer']);flow=[ipaddress.ip_address(host).version,host,port,peer,serviceport]
   self.check('tuple','both TLS observations match actual armed 5tuple '+phase,client['local']==server['peer']and client['peer']==server['local']and flow==self.arms[phase]['flow'])
   self.check('deadline','at least one actual same data probe timeout '+phase,any(e['end']=='probe_timeout'for e in phasefact['endpoints']))
   catalog=self.data('client','catalog-during-'+phase+'.json');self.check('catalog','new authenticated catalog completed during target fault '+phase,catalog['schema_version']==1 and catalog['journal_healthy']and catalog['response']['result']=='catalog')
   if backend!='virtual':
    for stage in ['pre','fault']:
     current=self.data('server','host-'+stage+'-'+phase+'.json')
     for row in baseline:
      new=unique(current,lambda x:x['bus']==row['bus']);restored=stage=='fault'and row['vendor']=='0627'
      if backend=='native':
       dynamic={'interfaces','device_driver','usbip_status'};expected_interfaces=[dict(x,driver='')for x in row['interfaces']];expected_status=1 if restored else 2
       self.check('sysfs','Native actual generation/config/interfaces/stub phase '+phase+stage+row['bus'],{k:v for k,v in row.items()if k not in dynamic}=={k:v for k,v in new.items()if k not in dynamic}and new['interfaces']==expected_interfaces and new['device_driver']=='usbip-host'and new['usbip_status']==expected_status)
      else:self.check('sysfs','USBFS actual generation/config/interfaces/driver phase '+phase+stage+row['bus'],{k:v for k,v in row.items()if k!='interfaces'}=={k:v for k,v in new.items()if k!='interfaces'}and new['interfaces']==(row['interfaces']if restored else[dict(x,driver='usbfs')for x in row['interfaces']]))
   facts.append(phasefact)
  # Seven actual serial rounds; keep original full client serial receipt across every new HID attempt.
  serial_receipt=None;hid_resources=set();hid_generations=set();txall=b''
  for n in range(1,8):
   tx=f'RUSTUSB-TX-{n}-0001:fedcba9876543210\n'.encode();rx=f'RUSTUSB-RX-{n}-0001:0123456789abcdef\n'.encode();txall+=tx
   self.check('actual_io','actual serial write '+str(n),self.raw['client'][f'tx-{n}.bin']==tx and len(tx)==35)
   if backend=='virtual':self.check('actual_io','CDC actual same-byte echo '+str(n),self.raw['client'][f'echo-{n}.bin']==tx and f'input-{n}.bin'not in self.raw['client'])
   else:self.check('actual_io','FTDI independently injected actual receive '+str(n),self.raw['client'][f'rx-{n}.bin']==rx and(self.base/f'ftdi-host-injected-{n}.bin').read_bytes()==rx)
   if n%2:
    records=self.data('client',f'client-receipts-{n}.json')['records'];hid=unique(records,lambda r:r['target']['device_key']==keys[0]);serial=unique(records,lambda r:r['target']['device_key']==keys[1])
    if serial_receipt is not None:self.check('reuse','original serial root capability through round'+str(n),serial_receipt==serial)
    serial_receipt=serial;hid_resources.add(hid['channel_id']);hid_generations.add(json.dumps(hid['state']['receipt']['session']['local_device_generation'],sort_keys=True));local=self.data('client',f'local-identities-{n}.json')
    for name,r in [('hid',hid),('serial',serial)]:
     receipt=r['state']['receipt'];g=receipt['session']['local_device_generation'];self.check('local_enumeration','real same receipt/sysfs '+str(n)+name,r['owner']['uid']==2000 and r['correlation']is not None and receipt['session']['status']==6 and receipt['kernel_socket_inode']>0 and receipt['profile']['configuration']==1 and local[name]==receipt['session']['local_bus_id']and local[name+'_generation']==f"{g['filesystem_device']}:{g['inode']}")
    if backend!='virtual':
     events=self.raw['client'][f'input-{n}.bin'];values=[struct.unpack('<qqHHi',events[i:i+24])[2:]for i in range(0,len(events),24)];self.check('actual_io','actual HID key down/up '+str(n),(1,n+1,1)in values and(1,n+1,0)in values)
  self.check('reuse','four actual fresh HID kernel resources/generations',len(hid_resources)==len(hid_generations)==4)
  if backend!='virtual':self.check('actual_io','host received all 245 ordered FTDI bytes',(self.base/'ftdi-host-rx.bin').read_bytes()==txall)
  final_errors={}
  for role,final in finals.items():
   expected=5 if role=='client'else 7
   self.check('final','all original actual sessions retired '+role,len(final['sessions'])==expected and all(x['terminal']and x['cleanup']=='verified'and x['state']=='Disconnected'for x in final['sessions'])and all(j['state']=='finished'for j in final['jobs']))
   held=self.state(role,'agent-final-held.json');kind='import'if role=='client'else'incoming';last=unique(held['sessions'],lambda x:x['role']==kind and x['device_key']==keys[0]and not x['terminal']);targets[role].append(last)
   self.check('reuse','four distinct actual incoming/import attempts '+role,len({x['id']for x in targets[role]})==4 and len({x['instance_id']for x in targets[role]})==1 and all(any(z['id']==x['id']for z in final['sessions'])for x in targets[role]))
   if backend=='native'or role=='client':
    root=self.data(role,'root-final.json');self.check('final','actual no remaining Native capabilities '+role,root['schema_version']==2 and root['records']==root['exports']==[]and len(root['terminals'])==expected)
    for s in final['sessions']:self.proof(root,s)
   elif backend=='usbfs':
    root=self.data(role,'root-final.json');self.check('final','actual two physical Finish terminals and empty live grants',root['schema_version']==3 and root['records']==[]and len(root['terminals'])==2 and self.data(role,'root-final-held.json')==grants)
    for s in final['sessions']:
     if s['role']=='export':
      self.proof(root,s,True);r=unique(grants['records'],lambda r:r['lease_id']==s['attempts'][0]['resource_id']);b=r['baseline'];original=unique(baseline,lambda x:x['bus']==b['bus_id']);g=b['receipt'];self.check('baseline','original grant full baseline/correlation '+s['id'],r['owner']['uid']==2000 and r['recovery']=='manual_only'and r['correlation']==s['attempts'][0]['correlation']and original['generation']==f"{g['sysfs_device']}:{g['sysfs_inode']}"and int(original['configuration'])==b['configuration']and b['interfaces']==[{'number':int(x['number'],16),'alternate':int(x['alt']),'driver':x['driver']or None}for x in original['interfaces']])
     else:self.check('rust_engine','final incoming has no root claim '+s['id'],s['attempts']==[])
   else:self.check('virtual','actual no Native/physical server journal and no root attempts',self.raw[role]['native-final.status']==self.raw[role]['physical-final.status']==b'absent\n'and all(s['attempts']==[]for s in final['sessions'])and self.data(role,'devices.json')==self.data(role,'devices-final.json')==[])
   final_errors[role]=[]
   for s in final['sessions']:
    h=unique(final['link_history'],lambda x:x['session_id']==s['id'])['link']
    self.check('final_health','actual ended data history or no export link '+s['id'],h is None if s['role']=='export'else h['phase']=='ended'and h['end']is not None and h['last_reply_sequence']is not None)
    final_errors[role].append({'id':s['id'],'role':s['role'],'device_key':s['device_key'],'operation_error':s['operation_error'],'cleanup':s['cleanup'],'link_end':None if h is None else h['end']})
   denied=self.data(role,'agent-after-denied.json');self.check('acl','unauthorized caller/profile creates no resources '+role,denied['jobs']==denied['sessions']==denied['requests']==[]and self.raw[role]['root-before-denied.json']==self.raw[role]['root-after-denied.json']and all(self.data(role,'denied-'+reason+'.json')['response']=={'result':'error','code':'permission'}for reason in ['uid','profile']))
  self.vhci('vhci-final.txt',0)
  self.facts.update(backend=backend,family=self.s['family'],fault_phases=facts,serial_io_rounds=7,serial_each_direction_bytes=245,serial_mode='CDC self-echo'if backend=='virtual'else'host-injected FTDI duplex',hid_key_codes=[]if backend=='virtual'else[2,4,6,8],fresh_hid_generations=4,final_operation_and_cleanup=final_errors)
 def run(self):
  result='FAIL';error=None
  try:self.artifacts();self.network();self.lifecycle();result='PASS'
  except Exception as e:error=type(e).__name__+': '+str(e);trace=traceback.format_exc()
  report=dict(case=self.name,result=result,error=error,predicate_count=len(self.checks),categories=dict(collections.Counter(x['category']for x in self.checks)),checks=self.checks,facts=self.facts,summary=meta(self.base/'summary.json'))
  if error:report['traceback']=trace
  return report
if __name__=='__main__':
 target=OUT/'health';target.mkdir(exist_ok=True);reports=[]
 for backend in ['native','usbfs','virtual']:
  for family in ['ipv4','ipv6']:
   name='health-'+backend+'-'+family;p=target/(name+'.json')
   if p.exists():raise FileExistsError('preserve prior review: '+str(p))
   report=Review(name).run();p.write_text(json.dumps(report,indent=2)+'\n');reports.append(report)
   print(json.dumps({k:report[k]for k in ['case','result','error','predicate_count']}),flush=True)
 summary={'result':'PASS'if all(x['result']=='PASS'for x in reports)else'FAIL','head':HEAD,'reviewer_authorship':'Reviewer authored guest-rust-fragments.sh and verify_rust.py and related service code. This independently implemented standard-library parser imports no harness verifier; this is method independence, not independent author identity.','source':meta(Path(__file__)),'groups':[{'case':r['case'],'result':r['result'],'predicate_count':r['predicate_count'],'report':meta(target/(r['case']+'.json'))}for r in reports],'boundaries':['Predicate/file counts are diagnostic comparisons, not USB cases.','MatchedReply/RTT are actual same-data driver observations qualified by session/epoch/TCP identity; encrypted PING bytes are not visible in PCAP. RTT is enqueue-to-library-Pong and does not measure USB latency.','Seven virtual CDC rounds are self-echo; virtual HID evidence is real enumeration/idle fault only, without injected KEY.','Target fault is idle/after-I/O; no pending URB or kernel internal active window is proven.','Rust incoming cleanup is actual engine completion evidence without Native helper proof; USBFS whole-device grant Final Finish is independently rooted.','Final transport errors and verified cleanup are recorded separately. No blanket no-error claim.','Source/ELF attribution is not hardware/complete Phase3 or a new software execution.','All earlier FAIL and unexecuted scopes remain unchanged.']}
 (target/'report.json').write_text(json.dumps(summary,indent=2)+'\n')
 raise SystemExit(0 if summary['result']=='PASS'else 1)
