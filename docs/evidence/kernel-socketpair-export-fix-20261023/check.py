import ast, copy, hashlib, json, pathlib, re, struct, subprocess
ROOT=pathlib.Path('/home/ubuntu/github/usbip-client'); OUT=pathlib.Path(__file__).parent
scripts=ROOT/'scripts/qemu'; checks=[]
def check(name,passed,**extra):
 checks.append(dict(name=name,passed=bool(passed),**extra))
 if not passed: raise RuntimeError(name)
shell=(scripts/'guest-kernel-socketpair-init.sh').read_text(); tree=ast.parse((scripts/'run-kernel-socketpair-qemu.py').read_text())
check('Python AST',True)
r=subprocess.run(['sh','-n',str(scripts/'guest-kernel-socketpair-init.sh')],capture_output=True,text=True);check('sh -n',r.returncode==0,stderr=r.stderr)
r=subprocess.run(['git','diff','--check'],cwd=ROOT,capture_output=True,text=True);check('git diff --check',r.returncode==0,stderr=r.stderr)
expr=re.search(r"'(def unsigned:.*?)' >> /tmp/physical.jsonl",shell,re.S).group(1)
fixtures=[('exact empty','', '',0,True,None),('configured','1\n',' 1\n',1,True,1),('empty cfg only','',' 1\n',1,False,None),('empty count only','1\n','',0,False,None),('blank cfg',' \n','',0,False,None),('blank both','\n',' \n',0,False,None),('empty with IF','','',1,False,None),('count mismatch','1\n',' 2\n',1,False,None),('zero cfg','0\n','1\n',1,False,None),('signed cfg','+1\n','1\n',1,False,None),('embedded whitespace','1 2','1\n',1,False,None),('malformed count','1\n','x',1,False,None),('out of range','256','1',1,False,None)]
for name,cfg,count,n,ok,value in fixtures:
 cmd=['jq','-Mcn']
 for key,val in {'bus':'1-1','generation':'23:100','path':'/sys/devices/pci0000:00/example/1-1','vendor':'0627','product':'0001','speed':'480','driver':'usbip-host','cfg':cfg,'count':count}.items():cmd+=['--arg',key,val]
 for key,val in {'address':2,'busnum':1,'status':1,'interfaces':[{}]*n}.items():cmd+=['--argjson',key,json.dumps(val)]
 r=subprocess.run(cmd+[expr],capture_output=True,text=True)
 check('actual jq '+name,(r.returncode==0)==ok and (not ok or json.loads(r.stdout)['configuration']==value),returncode=r.returncode,stdout=r.stdout,stderr=r.stderr)
# Execute only AST-extracted pure functions against a synthetic fixture. Never run main or read a guest result.
env={'re':re,'json':json,'struct':struct,'RX':b'RUSTUSB-RX-1-0001:0123456789abcdef\n','TX':b'RUSTUSB-TX-1-0001:fedcba9876543210\n','PASS':'RUSTUSB_PAIR_RESULT: PASS single_guest=true tls=false remote_security=false'}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ['records','status_rows','verify']],type_ignores=[]),'<AST pure functions>','exec'),env)
for text in ['000000','000017','ffffa123abcde000','0000000000000000']:
 check('opaque parser '+text,env['status_rows']('hub port sta spd dev sockfd local_busid\nhs 0 6 3 00010002 '+text+' 3-1')[0]['socket_display']==text)
for text in ['z123','-1','0123456789abcdef0']:
 try:env['status_rows']('hub port sta spd dev sockfd local_busid\nhs 0 6 3 00010002 '+text+' 3-1');accepted=True
 except ValueError:accepted=False
 check('reject socket display '+text,not accepted)
profiles=[('1-1','0627','0001',2,3,1,1,'480',3,'usbhid'),('1-2','0403','6001',3,255,255,255,'12',2,'ftdi_sio')]
prepared=[];attached=[];baseline=[];sockets=[]
for idx,(bus,vendor,product,address,cls,sub,proto,speed,kspeed,driver) in enumerate(profiles):
 interface=dict(name=bus+':1.0',number=0,alternate=0,**{'class':cls},subclass=sub,protocol=proto,driver=None)
 original=dict(bus_id=bus,sysfs_path='/sys/devices/pci0000:00/example/'+bus,filesystem_device=23,inode=100+idx,bus_number=1,device_address=address,vendor=vendor,product=product,configuration=None,speed=speed,interfaces=[])
 local={**original,'bus_id':'3-'+str(idx+1),'sysfs_path':'/sys/devices/platform/vhci_hcd.0/usb3/3-'+str(idx+1),'inode':200+idx,'bus_number':3,'configuration':1,'interfaces':[{**interface,'name':'3-'+str(idx+1)+':1.0','driver':driver}]}
 pair=[]
 for j in range(2):
  fd=10+idx*2+j; pair.append(dict(fd=fd,inode=1000+fd,filesystem_device=9,target=f'socket:[{1000+fd}]',anonymous=True,family='AF_UNIX',type='SOCK_STREAM'))
 sockets+=pair
 prepared.append(dict(original=original,port=idx,stub=pair[0],vhci=pair[1],attribute_inode=300+idx))
 attached.append(dict(original={**original,'configuration':1,'interfaces':[interface]},local=local,port=idx,remote_devid=65536+address,stub=pair[0],vhci=pair[1]))
 baseline.append(dict(bus_id=bus,generation=f'23:{100+idx}',address=address,sysfs_path=original['sysfs_path'],bus_number=1,vendor=vendor,product=product,speed=speed,configuration=1,device_driver='usb',usbip_status=None,interfaces=[dict(name=bus+':1.0',number_hex='00',alternate=0,class_hex=f'{cls:02x}',subclass_hex=f'{sub:02x}',protocol_hex=f'{proto:02x}',driver=driver)]))
def status(used):
 return 'hub port sta spd dev sockfd local_busid\n'+''.join(f'hs {i} {6 if used else 4} {profiles[i][8] if used else 0} {65536+profiles[i][3] if used else 0:08x} {10+i if used else 0:06} {attached[i]["local"]["bus_id"] if used else "0-0"}\n' for i in range(2))
snapshots=[]
for stage in ['original','exported','attached','before-stop','disconnected','restored']:
 devices=copy.deepcopy(baseline)
 for d in devices:
  if stage not in ['original','restored']:
   d['device_driver']='usbip-host';d['usbip_status']=2 if stage in ['attached','before-stop'] else 1
   if stage=='exported':d['configuration']=None;d['interfaces']=[]
   else:d['interfaces'][0]['driver']=None
 snapshots.append(dict(stage=stage,physical=devices,vhci_status=status(stage in ['attached','before-stop']),network_interfaces=['lo'],tcp='header\n',tcp6='header\n'))
events=[]
def event(name,**body):events.append(dict(event=name,schema_version=1,**body))
event('guard',pid=50,starttime=123);event('initial_status',status=status(False))
for p in prepared:event('prepared',**p)
for i,p in enumerate(prepared):
 for side in ['stub','vhci']:
  value=f'{p["stub"]["fd"]}\n' if side=='stub' else f'{p["port"]} {p["vhci"]["fd"]} {65536+profiles[i][3]} {profiles[i][8]}\n'
  event('write_begin',bus_id=p['original']['bus_id'],side=side,value=value,inode=p['attribute_inode']);event('write_complete',bus_id=p['original']['bus_id'],side=side,bytes=len(value))
for a in attached:event('attached',**a)
event('ready');event('stop_received')
for p in prepared:
 for side in ['stub','vhci']:event('shutdown',bus_id=p['original']['bus_id'],side=side,fd=p[side]['fd'],how='SHUT_RDWR',ok=True,error=None)
event('disconnected',ports_null=True,stub_available=True,old_local_generations_absent=True,fds_still_open=True,retained_sockets=sockets,originals=[a['original'] for a in attached],status=status(False));event('complete')
summary=dict(probe_binary_sha256='synthetic-probe',guest_binaries={'synthetic':True},binary_sha256={'rustusb-helper':'synthetic-helper'})
extras=[('RUSTUSB_PAIR_IDENTITY ',dict(pid=50,starttime=123,uids=[0]*4,control_fifo='0:600:fifo',executed_sha256='synthetic-probe',fds='\n'.join(f'{s["fd"]} {s["target"]}' for s in sockets))),('RUSTUSB_PAIR_WAIT ',dict(pid=50,starttime=123,wait_code=0)),('RUSTUSB_PAIR_GUARD_REFUSAL ',dict(event='error',error='guest root required')),('RUSTUSB_PAIR_BINARIES ',dict(installed={'synthetic':True},helper_executed_sha256='synthetic-helper')),('RUSTUSB_PAIR_SESSIONS ',[dict(managed=False,session={'status':6})]*2),('RUSTUSB_PAIR_IO ',dict(input_event_hex=(struct.pack('<qqHHi',0,0,1,2,1)+struct.pack('<qqHHi',0,0,1,2,0)).hex(),hid_local='3-1',ftdi_local='3-2',rx_hex=env['RX'].hex(),tx_hex=env['TX'].hex()))]
def fixture_log(ss,ee):
 return '\n'.join([env['PASS'],'RUSTUSB_PAIR_EVIDENCE_CHANNEL: PASS device=ttyS1 kernel_console=ttyS0','RUSTUSB_PAIR_EXPORT: PASS uid=1000 devices=2 via=existing_helper','RUSTUSB_PAIR_UNEXPORT: PASS uid=1000 devices=2 original_drivers=true']+['RUSTUSB_PAIR_PROBE '+json.dumps(e) for e in ee]+['RUSTUSB_PAIR_SNAPSHOT '+json.dumps(s) for s in ss]+[p+json.dumps(e) for p,e in extras])
fixture=fixture_log(snapshots,events);result=copy.deepcopy(summary);env['verify'](fixture,result,env['TX']);check('synthetic complete phase fixture only',result['check_count']==50,synthetic_verifier_assertions=result['check_count'])
(OUT/'synthetic-fixture-only.json').write_text(json.dumps({'scope':'SYNTHETIC ONLY; not guest evidence','snapshots':snapshots,'events':events,'pure_verifier_assertions':result['checks']},indent=2)+'\n')
mutations=[('export falsely configured',lambda s,e:s[1]['physical'][0].update(configuration=1)),('unknown config null during attached',lambda s,e:s[2]['physical'][0].update(configuration=None)),('new server generation',lambda s,e:s[2]['physical'][0].update(generation='23:999')),('wrong HID speed',lambda s,e:s[2]['physical'][0].update(speed='12')),('wrong current server driver',lambda s,e:s[2]['physical'][0]['interfaces'][0].update(driver='usbhid')),('missing attached IF',lambda s,e:s[2]['physical'][0].update(interfaces=[])),('disconnected cfg dropped',lambda s,e:s[4]['physical'][0].update(configuration=None)),('alternate changed',lambda s,e:s[3]['physical'][1]['interfaces'][0].update(alternate=1)),('wrong local full profile',lambda s,e:next(x for x in e if x['event']=='attached')['local']['interfaces'][0].update(protocol=9)),('wrong kernel speed',lambda s,e:s[2].update(vhci_status=s[2]['vhci_status'].replace('hs 0 6 3 ','hs 0 6 2 '))),('probe server generation changed',lambda s,e:next(x for x in e if x['event']=='attached')['original'].update(inode=999))]
for name,mutate in mutations:
 ss,ee=copy.deepcopy(snapshots),copy.deepcopy(events);mutate(ss,ee)
 try:env['verify'](fixture_log(ss,ee),copy.deepcopy(summary),env['TX']);rejected=False;error=None
 except (RuntimeError,ValueError,KeyError) as ex:rejected=True;error=str(ex)
 check('synthetic reject '+name,rejected,error=error)
probe=(scripts/'kernel-socketpair-probe.rs').read_text()
check('forbid unsafe/unwrap/expect/panic', '#![forbid(unsafe_code)]' in probe and re.search(r'\b(?:unwrap|expect|panic)\s*[!(]',probe) is None)
check('single File::write no automatic retry', 'self.file.write(text.as_bytes())?' in probe and '.write_all(' not in probe)
check('exact empty pair source boundary','if config.is_empty() && count.is_empty()' in probe and 'require(interfaces == 0' in probe)
check('existing timeouts unchanged',all(fragment in probe for fragment in ['Duration::from_secs(15)','Duration::from_secs(60)']) and 'not 60 <= args.timeout <= 1800' in (scripts/'run-kernel-socketpair-qemu.py').read_text())
files=[]
for name in ['run-kernel-socketpair-qemu.py','guest-kernel-socketpair-init.sh','kernel-socketpair-probe.rs']:
 p=scripts/name;data=p.read_bytes();old=(OUT/'original'/name).read_bytes();files.append(dict(path=str(p.relative_to(ROOT)),length=len(data),sha256=hashlib.sha256(data).hexdigest(),original_sha256=hashlib.sha256(old).hexdigest()))
report=dict(schema_version=1,result='PASS',scope='Author static syntax, actual jq and synthetic pure host-verifier fixtures ONLY. No Rust compile/probe/Cargo/QEMU executed.',check_count=len(checks),checks=checks,files=files,original_runtime='f4774a6 first QEMU remains FAIL; no reinterpretation',limitations=['Corrected sources still require strict root Rust compilation and a new clean QEMU run.','Single guest anonymous socketpair mechanism only; no remote/TLS/authentication acceptance.'])
(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'result':report['result'],'checks':len(checks),'files':files}))
