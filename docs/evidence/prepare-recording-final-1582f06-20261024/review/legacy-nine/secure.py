#!/usr/bin/env python3
"""Independent immutable-evidence audit; no runner import, verifier or PCAP parser."""
import argparse,base64,hashlib,json,re,struct,subprocess
from pathlib import Path
REPO=Path('/home/ubuntu/github/usbip-client')
HEAD='1582f06776f4c1ea50e9b5109a9ad7d0b7cdbe16'
LOCK=json.loads(Path('/tmp/rustusb-prepare-stop-legacy-nine-review-20261024/source-lock.json').read_text())
EXPECTED_BINARIES=None
BLOBS={}
def sha(b):return hashlib.sha256(b).hexdigest()
def readj(p):return json.loads(p.read_text())
def linesj(p):return [json.loads(l) for l in p.read_text().splitlines()]
def blob(path):
 if path not in BLOBS:BLOBS[path]=subprocess.check_output(['git','show',HEAD+':'+path],cwd=REPO)
 return BLOBS[path]
def props(b):return {l.split(':',1)[0]:l.split(':',1)[1].strip() for l in b.decode().splitlines() if ':' in l}
def rows(b):
 out=[]
 for l in b.decode().splitlines():
  f=l.split()
  if not f or f[0] not in ('hs','ss'):continue
  assert len(f)==7
  out.append(dict(hub=f[0],port=int(f[1]),state=int(f[2]),speed=int(f[3]),devid=int(f[4],16),historical_sockfd=int(f[5]),local=f[6]))
 return out
def null(rs):return len(rs)==16 and {r['port'] for r in rs}==set(range(16)) and all(r['state']==4 and r['speed']==0 and r['devid']==0 and r['historical_sockfd']==0 and r['local']=='0-0' for r in rs)
def normalized_if(h,drivers=True):
 return [dict(number=int(i['number'],16),alternate=int(i['alt']),**{k:int(i[k],16) for k in ('class','subclass','protocol')},**({'driver':i['driver'] or None} if drivers else {})) for i in h['interfaces']]
HARD=('bus','generation','devnum','path','busnum','vendor','product','speed','release')
def native_phase(base,actual,phase):
 if len(base)!=2 or len(actual)!=2:return False
 for b,a in zip(base,actual):
  if any(b[k]!=a[k] for k in HARD) or a['device_driver']!='usbip-host':return False
  if a['usbip_status']!=(2 if phase in ('held','io') else 1):return False
  if phase=='exported':
   if not(a['configuration']==a['num_interfaces']=='' and a['configuration_bytes']==a['count_bytes']==0 and a['interfaces']==[]):return False
  elif not(a['configuration']==b['configuration'] and a['num_interfaces']==b['num_interfaces'] and a['configuration_bytes']==b['configuration_bytes'] and a['count_bytes']==b['count_bytes'] and normalized_if(a,False)==normalized_if(b,False) and all(i['driver']=='' for i in a['interfaces'])):return False
 return True

def audit(root,backend,family):
 summary_bytes=(root/'summary.json').read_bytes();s=json.loads(summary_bytes)
 if not s.get('completed_utc'):raise RuntimeError('refuse nonterminal evidence')
 checks=[]
 def check(group,name,condition,detail=None):checks.append({'group':group,'name':name,'passed':bool(condition),**({'detail':detail} if detail is not None else {})})
 def data(role,name):return (root/f'{role}-artifacts'/name).read_bytes()
 def obj(role,name):return json.loads(data(role,name))
 def ev(role,name):return [json.loads(l) for l in data(role,name).splitlines()]
 check('provenance','terminal source/build/guest guards',s['head']==s['head_after']==HEAD and s['result']=='PASS' and s['backend']==backend and s['family']==family and s['guest_exit_codes']=={'server':0,'client':0} and all(s[k] for k in ('source_guard','binary_guard','head_guard','clean_guard')) and s['git_status_before']==s['git_status_after']=='' and s['build_skipped'] is False and b'Finished' in (root/'build.log').read_bytes())
 check('provenance','fixed actual foreground regression ELFs',s['binary_sha256']==EXPECTED_BINARIES)
 for p,m in s['source_inputs'].items():
  b=(root/'source'/p).read_bytes()
  assert len(b)==m['bytes'] and sha(b)==m['sha256'] and b==blob(p),p
 check('provenance','all frozen inputs exact commit Git blobs',set(s['source_inputs'])==set(LOCK['secure_inputs']) and s['source_inputs']=={p:LOCK['source_hashes'][p] for p in LOCK['secure_inputs']},{'count':len(s['source_inputs'])})
 check('provenance','reviewed initialization fix frozen',sha((root/'source/crates/rustusb-native/src/channel.rs').read_bytes())==LOCK['source_hashes']['crates/rustusb-native/src/channel.rs']['sha256'])
 for p,m in s['artifact_manifest'].items():
  b=(root/p).read_bytes();assert len(b)==m['bytes'] and sha(b)==m['sha256'],p
 check('provenance','all artifact manifest originals match',True,{'count':len(s['artifact_manifest'])})
 decoded=0;texts={}
 for role in ('server','client'):
  raw=(root/f'{role}-evidence.log').read_text().splitlines();texts[role]='\n'.join(raw);seen=set();i=0
  while i<len(raw):
   if not raw[i].startswith('SECURE_ARTIFACT_BEGIN '):i+=1;continue
   _,name,n=raw[i].split();i+=1;enc=[]
   while i<len(raw) and raw[i]!='SECURE_ARTIFACT_END '+name:enc.append(raw[i]);i+=1
   assert i<len(raw) and name not in seen
   b=base64.b64decode(''.join(enc),validate=True);m=s['guest_artifacts'][role][name]
   assert len(b)==int(n)==m['bytes'] and sha(b)==m['sha256'] and data(role,name)==b,name
   seen.add(name);i+=1;decoded+=1
  check('provenance',role+' raw serial artifact extraction exact',seen==set(s['guest_artifacts'][role]),{'count':len(seen)})
  kernel=(root/f'{role}-serial.log').read_text()
  check('guest_completion',role+' real boot and orderly PASS',texts[role].count('RUSTUSB_SECURE_RESULT: PASS role='+role)==1 and 'RUSTUSB_SECURE_RESULT: FAIL' not in texts[role] and f'SECURE_BOOT role={role} backend={backend} family={family} kernel=6.8.0-106-generic' in texts[role] and 'Power down' in kernel and all(x not in kernel for x in ('Kernel panic','Oops:')) and (root/f'{role}-qemu.stderr').read_bytes()==b'')
  names=s['credential_payloads'][role];other='client' if role=='server' else 'server'
  check('isolation',role+' role keys CRNG RTC',f'{role}-key.der' in names and f'{other}-key.der' not in names and not any(n.startswith('ca-key') for n in names) and f'SECURE_CREDENTIAL_ISOLATION role={role} own_key=true opposite_key=false ca_key=false' in texts[role] and 'SECURE_RNG_RTC_READY crng=true ' in texts[role])
  for p in (root/f'{role}-artifacts').glob('executable-*.txt'):
   exe='rustusb-helper' if p.name=='executable-helper.txt' else 'rustusb'
   check('actual_elf',role+'/'+p.name,p.read_text().split()[0]==s['binary_sha256'][exe]==s['payload_sha256'][role]['usr/bin/'+exe])
  for p in (root/f'{role}-artifacts').glob('identity-*.txt'):
   identity=props(p.read_bytes());uid='0' if p.name=='identity-helper.txt' else '2000'
   check('credentials',role+'/'+p.name,identity['Uid'].split()==[uid]*4 and (uid=='0' or all(identity[k]=='0000000000000000' for k in ('CapInh','CapPrm','CapEff','CapAmb'))))
  cmd=s['qemu_commands'][role];append=cmd[cmd.index('-append')+1]
  check('topology',role+' isolated separate guest and fixed role',cmd[0]=='qemu-system-x86_64' and '-nic' in cmd and cmd[cmd.index('-nic')+1]=='none' and any(x.startswith('socket,id=external,fd=') for x in cmd) and f'rustusb.role={role}' in append and f'rustusb.backend={backend}' in append and f'rustusb.family={family}' in append and 'RUSTUSB_QEMU_GUEST=1 rustusb.secure=1' in append)
 catalog=obj('client','catalog-initial.json');bykey={r['device_key']:r['device'] for r in catalog};keys=obj('client','keys.json')
 hidkey=next(k for k,d in bykey.items() if d['interfaces'][0]['class']==3);serkey=next(k for k in bykey if k!=hidkey)
 check('catalog','exact unique configured key/device mapping',len(catalog)==len(bykey)==2 and set(keys)==set(bykey) and (set(keys)=={'virtual:hid','virtual:cdc-acm'} if backend=='virtual' else all(k.startswith(('physical' if backend=='usbfs' else 'native')+':usb-') for k in keys)) and len({d['bus_id'] for d in bykey.values()})==2)
 check('catalog','full actual descriptor profiles',bykey[hidkey]['interfaces']==[{'class':3,'subclass':1,'protocol':1}] and bykey[serkey]['interfaces']==([{'class':2,'subclass':2,'protocol':0},{'class':10,'subclass':0,'protocol':0}] if backend=='virtual' else [{'class':255,'subclass':255,'protocol':255}]) and all(d['configuration']==d['num_configurations']==1 for d in bykey.values()) and [bykey[k]['speed'] for k in (hidkey,serkey)]==([2,2] if backend=='virtual' else [3,2]))
 policy=obj('client','helper-policy.json')
 check('isolation','client broker policy exact and no fake trusted network',policy['trusted_network'] is False and policy['allowed_uids']==[1000] and policy['secure_broker_uid']==2000 and policy['peers']==policy['export_device_ids']==[] and policy['secure_imports']==[{'server_id':'qemu-server','device_keys':keys}])
 denied=obj('client','denied-response.json')
 check('isolation','UID1000 denial no client kernel mutation',denied['ok'] is False and denied['data'] is None and 'not the configured secure broker' in denied['error'] and data('client','native-before-denied.txt')==data('client','native-after-denied.txt') and null(rows(data('client','native-after-denied.txt'))))
 check('network_refusal','both actual IPv4 IPv6 plaintext refusals',all(b'Connection refused' in data('client',f'plaintext-{f}.log') for f in (4,6)))
 allreceipts=[];cycles=[]
 for c in (1,2):
  group=f'cycle_{c}';held=obj('client',f'channels-held-{c}.json');rs=rows(data('client',f'native-held-{c}.txt'));used=[r for r in rs if r['state']==6]
  check(group,'two USED ports and complete observed durable records',len(rs)==16 and len(used)==2 and {r['devid'] for r in used}=={d['bus_number']<<16|d['device_number'] for d in bykey.values()} and held['schema_version']==1 and held['exports']==[] and len(held['records'])==2 and all(r['state']['phase']=='observed' and r['owner']['uid']==2000 and r['owner']['starttime']>0 and len(r['owner']['boot'])==36 for r in held['records']))
  receipts=[]
  for profile,key in [('hid',hidkey),('serial',serkey)]:
   events=ev('client',f'attach-{profile}-{c}.events');connected=[e for e in events if e['event']=='connected'];disconnected=[e for e in events if e['event']=='disconnected']
   check(group,profile+' exact lifecycle events',len(events)==2 and len(connected)==len(disconnected)==1 and connected[0]['transport']=='mtls' and disconnected[0]['cleanup_verified'] is True)
   rec=connected[0]['receipt'];k=rec['kernel'];local=k['session'];remote=bykey[key];allreceipts.append(rec);receipts.append(rec)
   record=next(r for r in held['records'] if r['channel_id']==rec['channel_id']);actual=next(r for r in used if r['port']==local['port']);identity=props(data('client',f'identity-{profile}-{c}.txt'))
   check(group,profile+' catalog target journal owner and full kernel receipt',rec['target']=={'server_id':'qemu-server','device_key':key,'remote':remote} and connected[0]['device_key']==key and record['owner']['pid']==int(identity['Pid']) and record['target']==rec['target'] and record['state']['receipt']==k and actual['local']==local['local_bus_id'] and actual['devid']==local['remote_device_id']==(remote['bus_number']<<16|remote['device_number']) and actual['speed']==remote['speed'] and actual['hub']=='hs' and local['status']==6 and local['vendor_id']==remote['vendor_id'] and local['product_id']==remote['product_id'] and 1<=local['local_device_address']<=127 and min(local['local_device_generation'].values())>0 and k['kernel_socket_inode']>0 and k['intent']['port']==local['port'])
   check(group,profile+' complete actual configured profile',k['profile']['configuration']==1 and [{k:i[k] for k in ('class','subclass','protocol')} for i in k['profile']['interfaces']]==remote['interfaces'] and all(i['alternate']==0 for i in k['profile']['interfaces']) and [i['number'] for i in k['profile']['interfaces']]==list(range(len(remote['interfaces']))))
   check(group,profile+' actual wait0 no recurrence of NotFound',data('client',f'wait-{profile}-{c}.txt').strip()==b'0' and data('client',f'attach-{profile}-{c}.log')==b'')
  inp=data('client',f'input-{c}.bin');ie=[struct.unpack_from('<qqHHi',inp,n)[2:] for n in range(0,len(inp),24)]
  check(group,'actual evdev key down and up',len(inp)>0 and len(inp)%24==0 and (1,c+1,1) in ie and (1,c+1,0) in ie)
  rx=f'RUSTUSB-RX-{c}-0001:0123456789abcdef\n'.encode();tx=f'RUSTUSB-TX-{c}-0001:fedcba9876543210\n'.encode()
  check(group,'exact real 35B RX and TX',len(rx)==len(tx)==35 and data('client',f'rx-{c}.bin')==rx and data('client',f'tx-{c}.bin')==tx and ((data('server',f'injected-{c}.bin')==rx and data('client',f'echo-{c}.bin')==tx) if backend=='virtual' else (root/f'ftdi-host-injected-{c}.bin').read_bytes()==rx))
  check(group,'SIGTERM completed allNULL durable empty and localGone',null(rows(data('client',f'native-empty-{c}.txt'))) and obj('client',f'channels-empty-{c}.json')=={'schema_version':1,'records':[],'exports':[]} and f'SECURE_CLIENT_RELEASED cycle={c} signal=SIGTERM wait=0 kernel_null=true local_gone=true' in texts['client'])
  check(group,'unchanged contender kernel and catalog reuse',data('client',f'native-held-{c}.txt')==data('client',f'native-after-contender-{c}.txt') and b'configured remote device is unavailable or unauthorized' in data('client',f'contender-{c}.log') and obj('client',f'catalog-reuse-{c}.json')==catalog)
  cycles.append({'cycle':c,'evdev_key_code':c+1,'serial_bytes_each_direction':35,'channel_ids':[r['channel_id'] for r in receipts],'local_generations':[r['kernel']['session']['local_device_generation'] for r in receipts]})
 check('reuse','four fresh channel IDs and local generations',len({r['channel_id'] for r in allreceipts})==4 and len({tuple(sorted(r['kernel']['session']['local_device_generation'].items())) for r in allreceipts})==4)
 kernel=(root/'client-serial.log').read_text()
 check('kernel_enumeration','actual HID serial enumeration twice and exact address disconnection',all(len(re.findall(f"New USB device found, idVendor={d['vendor_id']:04x}, idProduct={d['product_id']:04x}",kernel))==2 for d in bykey.values()) and all(f"USB disconnect, device number {r['kernel']['session']['local_device_address']}" in kernel for r in allreceipts) and kernel.count('USB ACM device' if backend=='virtual' else 'FTDI USB Serial Device converter now attached')==2)
 helper=data('client','helper.log')
 check('initialization','Native import no recurrent NotFound or helper IO warning',b'NotFound' not in helper and b'error_category\x1b[0m\x1b[2m=\x1b[0m"io"' not in helper)
 serverevents=ev('server','server.events');ready=[e for e in serverevents if e['event']=='ready'];stopped=[e for e in serverevents if e['event']=='stopped']
 address='192.0.2.1:3241' if family=='ipv4' else '[fd42:7275:7374::1]:3241'
 check('server_stop','one real ready and SIGTERM orderly stopped',len(ready)==len(stopped)==1 and ready[0]['transport']=='mutual_tls' and ready[0]['address']==address and data('server','wait-server.txt').strip()==b'0' and 'SECURE_SERVER_STOPPED signal=SIGTERM wait=0' in texts['server'])
 control=linesj(root/'host-control.jsonl');expected=['keys','start']+[f'{m}-{c}' for c in (1,2) for m in ('inject','io','close','released','clean')]+['stop','finish']
 check('ordering','actual host controls sequence keeps IO before cleanup and reuse', [e['mark'] for e in control]==expected and all(a['monotonic']<b['monotonic'] for a,b in zip(control,control[1:])))
 if backend=='virtual':
  for c in (1,2):
   io=obj('server',f'io-{c}.json');ds={d['profile']:d for d in io['devices']};cdc=ds['cdc_acm'];released=obj('server',f'released-{c}.json')
   tx=b''.join(f'RUSTUSB-TX-{i}-0001:fedcba9876543210\n'.encode() for i in range(1,c+1));rxecho=b''.join(f'RUSTUSB-RX-{i}-0001:0123456789abcdef\nRUSTUSB-TX-{i}-0001:fedcba9876543210\n'.encode() for i in range(1,c+1))
   check(f'cycle_{c}','virtual actual counters and exact cumulative byte hashes',all(d['leased'] and d['configuration']==1 for d in ds.values()) and ds['hid_keyboard']['counters']['hid_taps']==c and cdc['counters']['cdc_out_bytes']==35*c and cdc['counters']['cdc_in_bytes']==70*c and cdc['counters']['cdc_injected_bytes']==35*c and cdc['cdc_out_sha256']==sha(tx) and cdc['cdc_in_sha256']==sha(rxecho))
   check(f'cycle_{c}','virtual independent resources all zero',all(d['leased'] is False and d['pending_urbs']==0 and d['configuration']==0 and d['hid_queued_reports']==0 and d['cdc_queued_bytes']==0 for d in released['devices']))
  check('server_stop','virtual final resources clean',all(not d['leased'] and d['pending_urbs']==0 for d in stopped[0]['snapshot']['devices']))
 else:
  baseline=obj('server','host-before.json');bymap={d['bus']:d for d in baseline};policy=obj('server','helper-policy.json');denied=obj('server','denied-response.json');ids=[k.split(':',1)[1] for k in keys]
  check('physical_baseline','real two fixed synthetic original devices and drivers',len(baseline)==2 and [d['vendor']+':'+d['product'] for d in baseline]==['0627:0001','0403:6001'] and [d['speed'] for d in baseline]==['480','12'] and all(d['device_driver']=='usb' and d['configuration']=='1' and len(d['interfaces'])==1 and int(d['interfaces'][0]['alt'])==0 for d in baseline) and [d['interfaces'][0]['driver'] for d in baseline]==['usbhid','ftdi_sio'])
  check('isolation','server broker isolated and denied without baseline mutation',policy['allowed_uids']==[1000] and policy['secure_broker_uid']==2000 and policy['trusted_network'] is False and policy['export_device_ids']==policy['peers']==[] and policy['secure_'+('physical' if backend=='usbfs' else 'native')+'_device_ids']==ids and denied['ok'] is False and 'not the configured secure broker' in denied['error'] and obj('server','host-after-denied.json')==baseline and b'Permission denied' in data('server','direct-usbfs-denied.log'))
  qmp=linesj(root/'qmp-input.jsonl');check('physical_io','two actual QMP key commands accepted',len(qmp)==2 and all(q['command']=='send-key' and q['return']=={} and q['arguments']['keys']==[{'type':'qcode','data':str(c)}] for c,q in enumerate(qmp,1)))
  expectedtx=b''.join(f'RUSTUSB-TX-{i}-0001:fedcba9876543210\n'.encode() for i in (1,2))
  check('physical_io','host char backend received exact cumulative 70B', (root/'ftdi-host-rx.bin').read_bytes()==expectedtx)
  check('physical_baseline','final original full configuration IF alternates drivers generation restored',obj('server','host-final.json')==baseline)
  if backend=='usbfs':
   for c in (1,2):
    held=obj('server',f'held-{c}.json');io=obj('server',f'io-{c}.json');released=obj('server',f'released-{c}.json')
    check(f'cycle_{c}','USBFS genuinely held active pending URBs and claims',held['buffer_bytes_reserved']>0 and len(held['devices'])==2 and all(d['leased'] and d['configuration']==1 and d['pending_urbs']>0 and d['usbfs_inflight']>0 and d['current_drivers']==[{'interface':0,'driver':'usbfs'}] for d in held['devices']))
    check(f'cycle_{c}','USBFS real endpoint work visible',io['devices'][0]['counters']['interrupt_in']>=2 and io['devices'][1]['counters']['bulk_out']>=c and io['devices'][1]['counters']['bytes_out']==35*c and all(d['usbfs_reaped']>0 for d in io['devices']))
    check(f'cycle_{c}','USBFS release verified zero resources and exact original baseline',released['buffer_bytes_reserved']==0 and all(not d['leased'] and d['cleanup_verified'] and d['state']=='available' and d['pending_urbs']==d['queued_urbs']==d['usbfs_inflight']==0 and d['current_drivers']==d['original_drivers'] for d in released['devices']) and obj('server',f'host-released-{c}.json')==baseline)
   check('journal','USBFS final durable journal retired',obj('server','physical-final.json')=={'schema_version':1,'records':[]})
  else:
   exported=obj('server','server-native-exported.json');exps={e['registration']['device_key']:e for e in exported['exports']};owner=props(data('server','identity-server.txt'))
   check('native_export','preimport actual configuration is empty not published cfg1',native_phase(baseline,obj('server','host-exported.json'),'exported') and exported['schema_version']==1 and exported['records']==[] and len(exported['exports'])==2 and set(exps)==set(bykey))
   for key,e in exps.items():
    reg=e['registration'];g=reg['generation'];b=bymap[g['bus_id']];fd,ino=map(int,b['generation'].split(':'));bound=e['bound'];pub=reg['published_device']
    check('native_export',key+' strong original registration bound identity and owner',reg['device_key']==key and pub==bykey[key] and reg['original_device_driver']=='usb' and reg['original_profile']=={'configuration':1,'interfaces':normalized_if(b)} and g['filesystem_device']==fd and g['inode']==ino and g['sysfs_path']==b['path'] and g['bus_number']==int(b['busnum']) and g['device_number']==int(b['devnum']) and g['vendor_id']==int(b['vendor'],16) and g['product_id']==int(b['product'],16) and g['device_version']==int(b['release'],16) and g['speed']==pub['speed'] and bound['generation']==g and bound['attribute_device']==fd and bound['attribute_inode']>0 and bound['bound_profile']=={'configuration':None,'interfaces':[]} and e['stub'] is None and e['owner']['uid']==2000 and e['owner']['pid']==int(owner['Pid']) and e['owner']['starttime']>0 and len(e['owner']['boot'])==36)
   stubids=[]
   for c in (1,2):
    for phase in ('held','io','released'):
     check(f'cycle_{c}','Native '+phase+' real sysfs lifecycle',native_phase(baseline,obj('server',f'host-{phase}-{c}.json'),phase))
    heldj=obj('server',f'server-native-held-{c}.json');releasej=obj('server',f'server-native-released-{c}.json')
    check(f'cycle_{c}','Native server journal remains exactly two exports',heldj['schema_version']==releasej['schema_version']==1 and heldj['records']==releasej['records']==[] and len(heldj['exports'])==len(releasej['exports'])==2)
    for e in heldj['exports']:
     key=e['registration']['device_key'];orig=exps[key];stub=e['stub'];r=stub['state']['receipt'];stubids.append(stub['channel_id'])
     check(f'cycle_{c}',key+' observed stub and unchanged immutable export',all(e[k]==orig[k] for k in ('lease_id','owner','registration','bound')) and stub['state']['phase']=='observed' and r['export']==orig['bound'] and r['kernel_socket_inode']>0 and r['usbip_status']==2 and r['current_profile'] in ({'configuration':None,'interfaces':[]},{'configuration':1,'interfaces':[{**i,'driver':None} for i in orig['registration']['original_profile']['interfaces']]}))
    check(f'cycle_{c}','Native close retires stub only preserving complete export',all(e==exps[e['registration']['device_key']] for e in releasej['exports']))
    for name,accept in [('held',False),('io',False),('released',True)]:
     snap=obj('server',f'{name}-{c}.json')
     check(f'cycle_{c}','Native '+name+' published metadata distinct from live state',snap['metadata_scope']=='published_registration_not_live_configuration' and {d['device_key']:d['published_device'] for d in snap['devices']}==bykey and all(d['accepting_imports'] is accept for d in snap['devices']))
   check('native_export','four fresh stub channels across two reuse cycles',len(set(stubids))==4)
   check('server_stop','Native all exports closed verified and durable state empty',stopped[0]['export_cleanup_verified'] is True and stopped[0]['exports_closed']==2 and obj('server','server-native-final.json')=={'schema_version':1,'records':[],'exports':[]})
 legacy_journals=[p for role in ('server','client') for p in (root/f'{role}-artifacts').glob('*.json') if p.name.startswith(('channels-','server-native-')) or p.name=='physical-final.json']
 check('legacy_compatibility','raw root journals remain schema1 without tracking fields',bool(legacy_journals) and all(readj(p)['schema_version']==1 and b'correlation' not in p.read_bytes() and b'terminals' not in p.read_bytes() for p in legacy_journals),{'journals':len(legacy_journals)})
 check('immutability','original summary unchanged by review',(root/'summary.json').read_bytes()==summary_bytes)
 return {'group':backend+'/'+family,'result':'PASS' if all(c['passed'] for c in checks) else 'FAIL','evidence_root':str(root),'summary_sha256':sha(summary_bytes),'head':s['head'],'completed_utc':s['completed_utc'],'source_inputs':len(s['source_inputs']),'artifact_entries':len(s['artifact_manifest']),'redecoded_guest_artifacts':decoded,'binary_sha256':s['binary_sha256'],'cycles':cycles,'preserved_kernel_warning_lines':{role:[{'line':i+1,'text':line} for i,line in enumerate((root/f'{role}-serial.log').read_text().splitlines()) if re.search(r'warning|\bWARN\b|\bERROR\b|vhci_device speed not set|urb->status -|usbip.*(?:fail|error|cannot)|usb .*error',line,re.I)] for role in ('server','client')},'assertion_count':len(checks),'checks':checks}

def main():
 global EXPECTED_BINARIES
 p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);p.add_argument('--backend',choices=['virtual','usbfs','native'],required=True);p.add_argument('--family',choices=['ipv4','ipv6'],required=True);p.add_argument('--expected-binaries',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 EXPECTED_BINARIES=json.loads(a.expected_binaries.read_text())
 if set(EXPECTED_BINARIES)!={'rustusb','rustusb-helper'} or not all(re.fullmatch('[0-9a-f]{64}',v) for v in EXPECTED_BINARIES.values()):raise ValueError('exact completed-build locked build ELF map required')
 if a.output.exists():raise FileExistsError('preserve earlier reviewer result')
 report={'kind':'separate raw lifecycle audit','source_head':HEAD,'evidence':str(a.evidence),'result':'FAIL','reviewer_provenance':{'identity':'/root/journal_harness_review; historical independent parser author; independent method does not imply different reviewer identity','derived_from':'/tmp/rustusb-foreground-six-lifecycle-raw-review-eaa1936-20261023.py','original_sha256':'5e2fa15807016a810aa2eae1a8c07265cabef98f206924522697a657c0bef239','changes':'New run/source/ELF attribution, single-group CLI, preserved raw kernel warning list; lifecycle assertions retained. No runner imports or verifier/pcap calls.'},'limits':['Audit comparisons are not new USB cases.','Only this completed pair is reviewed; no Cargo/QEMU/USB/production executable run.','Historical runtime verdicts/source ownership stay unchanged.','No independent PCAP protocol/decryption review here.','No claim of kernel-hardblocked initialization acceptance, hardware G1/G2 or complete Phase3.','Legacy USBFS intermediate grant journal is not captured; only actual retained final journal is checked.']}
 try:
  report['group']=audit(a.evidence.resolve(),a.backend,a.family);report['result']=report['group']['result']
 except Exception as e:report['error']=type(e).__name__+': '+str(e)
 report['parser_sha256']=sha(Path(__file__).read_bytes());report['source_lock_sha256']=sha(Path('/tmp/rustusb-prepare-stop-legacy-nine-review-20261024/source-lock.json').read_bytes());report['expected_binaries_file_sha256']=sha(a.expected_binaries.read_bytes())
 a.output.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'result':report['result'],'output':str(a.output),'error':report.get('error'),'checks':report.get('group',{}).get('assertion_count')}));return int(report['result']!='PASS')
if __name__=='__main__':raise SystemExit(main())
