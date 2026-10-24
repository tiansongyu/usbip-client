#!/usr/bin/env python3
"""Read-only new-run reparse; reviewer participated in product Native/link code and audit.py.
Independent method, not independent product/harness author approval.
No imports of project runners/auditors and no USB, network, Cargo or QEMU actions.
"""
import argparse,base64,collections,hashlib,ipaddress,json,pathlib,re,stat,struct,subprocess,sys,traceback
HEAD='1582f06776f4c1ea50e9b5109a9ad7d0b7cdbe16'
BIN={'rustusb':'72adb291645fdcf3cb41c1ff9e9e781fe9eed344730a4444407f983c9b63e5f2','rustusb-helper':'b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5'}
ROLES=('client','server'); MAC={'client':'52:54:00:00:00:62','server':'52:54:00:00:00:61'}
COUNTS=collections.Counter(); RAW={}; BLOBS={}; GROUPS=[]; PACKET_AUDIT=[]
def sha(b):return hashlib.sha256(b).hexdigest()
def check(cat,ok,why):
 COUNTS[cat]+=1
 if not ok:raise ValueError(cat+': '+why)
def read(p):
 p=pathlib.Path(p);b=p.read_bytes();info={'bytes':len(b),'sha256':sha(b)}
 check('read_stability',str(p) not in RAW or RAW[str(p)]==info,str(p));RAW[str(p)]=info;return b
def js(p):return json.loads(read(p))
def jl(p):return [json.loads(x) for x in read(p).splitlines() if x]
def blob(name):
 if name not in BLOBS:BLOBS[name]=subprocess.check_output(['git','-C',str(ARGS.repo),'show',HEAD+':'+name])
 return BLOBS[name]
def meta(b):return {'bytes':len(b),'sha256':sha(b)}
def reverse(f):return (f[0],f[3],f[4],f[1],f[2])
def u16(b,p=0):return int.from_bytes(b[p:p+2],'big')
def endpoint(s):
 h,p=s.rsplit(':',1);return str(ipaddress.ip_address(h.strip('[]'))),int(p)
def flow_identity(link):
 local=endpoint(link['identity']['tcp']['local']);peer=endpoint(link['identity']['tcp']['peer']);return (ipaddress.ip_address(local[0]).version,local[0],local[1],peer[0],peer[1])
def artifacts_from_log(d,role):
 b=read(d/(role+'-evidence.log')).replace(b'\r\n',b'\n');n=0;seen={}
 pattern=rb'(?m)^(SECURE_ARTIFACT|LINK_RAW)_BEGIN ([^\s]+) ([0-9]+)\n([A-Za-z0-9+/=\n]*)\1_END \2(?:\n|$)'
 for m in re.finditer(pattern,b):
  name=m[2].decode();data=base64.b64decode(m[4],validate=False);check('base64',len(data)==int(m[3]),name+' length')
  path=d/(role+'-artifacts')/name
  check('base64',path.is_file(),name+' archived');check('base64',read(path)==data,name+' raw equality');seen[name]=data;n+=1
 check('base64',n>50,str(d)+' raw evidence blocks');return seen

def decode(frame):
 check('ethernet',14<=len(frame)<=69632,'frame bound');mac=':'.join(f'{x:02x}' for x in frame[6:12]);etype=u16(frame,12);p=14;facts={'source_mac':mac}
 if etype==0x806:
  check('arp',len(frame)>=42 and frame[14:20]==b'\x00\x01\x08\x00\x06\x04' and u16(frame,20) in (1,2),'fixed IPv4 ARP');return {**facts,'kind':'arp'},b''
 if etype==0x800:
  check('ipv4',len(frame)>=34 and frame[p]>>4==4,'header');ihl=(frame[p]&15)*4;total=u16(frame,p+2)
  check('ipv4',20<=ihl<=60 and total>=ihl and p+total<=len(frame) and not (u16(frame,p+6)&0x3fff),'length/fragment');end=p+total;proto=frame[p+9];src=str(ipaddress.ip_address(frame[p+12:p+16]));dst=str(ipaddress.ip_address(frame[p+16:p+20]));p+=ihl;version=4
  check('ipv4',proto==6,'unexpected protocol')
 elif etype==0x86dd:
  check('ipv6',len(frame)>=54 and frame[p]>>4==6,'header');end=p+40+u16(frame,p+4);check('ipv6',end<=len(frame),'payload bound');proto=frame[p+6];src=str(ipaddress.ip_address(frame[p+8:p+24]));dst=str(ipaddress.ip_address(frame[p+24:p+40]));p+=40;version=6
  if proto==0:
   check('ipv6',p+2<=end,'HBH header');size=(frame[p+1]+1)*8;check('ipv6',8<=size<=256 and p+size<=end,'HBH size');proto=frame[p];at=p+2
   while at<p+size:
    kind=frame[at];at+=1
    if kind==0:continue
    check('ipv6',at<p+size,'option length');ln=frame[at];at+=1;v=frame[at:at+ln];at+=ln
    check('ipv6',at<=p+size and ((kind==1 and not any(v)) or (kind==5 and v==b'\x00\x00')),'fixed HBH option')
   p+=size
  if proto==58:
   check('icmpv6',p+4<=end and frame[p+1]==0,'ICMP header');names={133:'icmpv6_rs',135:'icmpv6_ns',136:'icmpv6_na',143:'icmpv6_mldv2'};check('icmpv6',frame[p] in names,'known discovery only');return {**facts,'kind':names[frame[p]]},b''
  check('ipv6',proto==6,'unknown extension/fragment/protocol')
 else:raise ValueError('unexpected Ethernet type '+str(etype))
 check('tcp',p+20<=end,'TCP header');sport,dport,seq,ack=struct.unpack_from('!HHII',frame,p);hlen=(frame[p+12]>>4)*4;check('tcp',20<=hlen<=60 and p+hlen<=end,'TCP offset');payload=frame[p+hlen:end]
 return {**facts,'kind':'tcp','flow':[version,src,sport,dst,dport],'sequence':seq,'acknowledgement':ack,'flags':frame[p+13],'payload_length':len(payload)},payload

def pcap(path):
 b=read(path);check('pcap',24<=len(b)<=64*1024*1024,'file bound');fmt={b'\xd4\xc3\xb2\xa1':'<',b'\xa1\xb2\xc3\xd4':'>'}.get(b[:4]);check('pcap',fmt is not None,'microsecond magic');h=struct.unpack_from(fmt+'HHIIII',b,4);check('pcap',h[:2]==(2,4) and h[-1]==1,'Ethernet classic format');pos=24;out=[]
 while pos<len(b):
  check('pcap',pos+16<=len(b),'record header');sec,usec,cap,wire=struct.unpack_from(fmt+'IIII',b,pos);pos+=16;check('pcap',usec<1000000 and cap==wire and 14<=cap<=69632 and pos+cap<=len(b),'record size');frame=b[pos:pos+cap];pos+=cap;facts,payload=decode(frame);out.append({'raw':frame,'facts':facts,'payload':payload,'timestamp_us':sec*1000000+usec});check('pcap',len(out)<=100000,'packet count bound')
 return out

def tls_records(wire,allow_tail):
 pos=0;records=[];tail=0
 while pos<len(wire):
  if pos+5>len(wire):tail=len(wire)-pos;break
  kind=wire[pos];version=wire[pos+1:pos+3];size=u16(wire,pos+3);check('tls_record',kind in (20,21,22,23) and version in (b'\x03\x01',b'\x03\x03') and 0<size<=18432,'record header')
  if pos+5+size>len(wire):tail=len(wire)-pos;break
  records.append((kind,wire[pos+5:pos+5+size]));pos+=5+size
 check('tls_record',not tail or allow_tail,'only faulted source stream may end with partial record');return records,tail

def hello(payload,client):
 check('tls_hello',len(payload)>=38 and payload[:2]==b'\x03\x03','legacy version');p=34;sid=payload[p];p+=1;check('tls_hello',sid<=32 and p+sid<=len(payload),'session ID');p+=sid
 cipher=None
 if client:
  check('tls_hello',p+2<=len(payload),'cipher size');size=u16(payload,p);p+=2;check('tls_hello',size>0 and size%2==0 and p+size<=len(payload),'ciphers');p+=size
  check('tls_hello',payload[p:p+2]==b'\x01\x00','null compression');p+=2
 else:
  check('tls_hello',p+3<=len(payload),'server selection');cipher=u16(payload,p);check('tls_hello',cipher in (0x1301,0x1302,0x1303) and payload[p+2]==0,'TLS13 cipher/compression');p+=3
 check('tls_hello',p+2<=len(payload) and p+2+u16(payload,p)==len(payload),'extension total');p+=2;ext={}
 while p<len(payload):
  check('tls_hello',p+4<=len(payload),'extension header');kind=u16(payload,p);size=u16(payload,p+2);p+=4;check('tls_hello',kind not in ext and len(ext)<64 and p+size<=len(payload),'unique bounded extension');ext[kind]=payload[p:p+size];p+=size
 if client:
  a=ext.get(16,b'');check('tls_hello',len(a)>=3 and u16(a)==len(a)-2,'ALPN vector');p=2;offers=[]
  while p<len(a):
   ln=a[p];p+=1;check('tls_hello',ln>0 and p+ln<=len(a),'ALPN name');offers.append(a[p:p+ln]);p+=ln
  check('tls_hello',offers==[b'rustusb-usbip/2'],'exact client v2 offer');return {'client_offer':['rustusb-usbip/2']}
 check('tls_hello',ext.get(43)==b'\x03\x04','actual ServerHello TLS1.3');return {'server_version':'TLS1.3','cipher':cipher}

def tls_stream(rows,allow_tail):
 syns={x['facts']['sequence'] for x in rows if x['facts']['flags']&2};check('tcp_reassembly',len(syns)==1,'one actual SYN epoch');initial=next(iter(syns));segments=[];end=0
 for x in rows:
  p=x['payload']
  if not p:continue
  offset=(x['facts']['sequence']-initial-1)&0xffffffff;check('tcp_reassembly',offset+len(p)<=16*1024*1024,'bounded sequence window');segments.append((offset,p));end=max(end,offset+len(p))
 check('tcp_reassembly',end>0,'TLS payload exists');buf=bytearray(end);covered=bytearray(end)
 for at,p in segments:
  for i,v in enumerate(p,at):
   check('tcp_overlap',not covered[i] or buf[i]==v,'matching retransmission');buf[i]=v;covered[i]=1
 check('tcp_reassembly',all(covered),'no source stream gaps');wire=bytes(buf);records,tail=tls_records(wire,allow_tail);h=b''.join(p for k,p in records if k==22);pos=0;hellos=[]
 while pos<len(h):
  check('tls_hello',pos+4<=len(h),'complete handshake header');kind=h[pos];size=int.from_bytes(h[pos+1:pos+4],'big');pos+=4;check('tls_hello',size<=65536 and pos+size<=len(h),'complete bounded handshake');body=h[pos:pos+size];pos+=size
  check('tls_hello',kind in (1,2),'TLS13 plaintext handshake types');hellos.append(hello(body,kind==1))
 check('tls_hello',1<=len(hellos)<=2,'bounded hello count');check('tls_record',any(k==23 for k,p in records),'encrypted records after hello')
 for marker in (b'RUSTUSB-TX-',b'RUSTUSB-RX-',b'virtual:hid',b'native:usb-',b'usbfs:usb-',b'physical:usb-'):
  check('wire_no_fixture_plaintext',marker not in wire,'no fixed fixture plaintext')
 return {'wire_bytes':len(wire),'wire_sha256':sha(wire),'records':len(records),'partial_tail_bytes':tail,'hello':hellos,'syn':initial}

def proc_socket_owner(d,role,phase,flow):
 art=d/(role+'-artifacts');identity=read(art/'agent-identity-1.txt').decode();pid=int(re.search(r'^pid=(\d+)$',identity,re.M)[1]);rawstat=read(art/('owner-stat-'+phase+'.txt')).decode();m=re.fullmatch(r'(\d+) \(rustusb\) (.*)\n?',rawstat);check('proc_owner',m and int(m[1])==pid,'actual phase PID');fields=m[2].split();start=int(fields[19]);check('proc_owner',start>0 and len(fields)>40,'actual process starttime');exe=read(art/'executable-agent-1.txt').decode().split();check('proc_owner',exe==[BIN['rustusb'],f'/proc/{pid}/exe'],'actual PID executable receipt')
 status=read(art/'identity-agent-1.txt').decode();check('proc_owner',re.search(r'^Pid:\s+'+str(pid)+r'$',status,re.M) is not None,'actual UID status belongs to same PID')
 def addr(text):
  host,port=text.split(':');b=bytes.fromhex(host);check('proc_tcp',len(b) in (4,16),'fixed proc address length');b=b''.join(b[i:i+4][::-1] for i in range(0,len(b),4));return (str(ipaddress.ip_address(b)),int(port,16))
 rows=[]
 for line in read(art/('tcp-'+phase+'.txt')).decode().splitlines():
  f=line.split()
  if not f or not f[0].endswith(':') or not f[0][:-1].isdigit():continue
  local=addr(f[1]);peer=addr(f[2]);actual=(ipaddress.ip_address(local[0]).version,local[0],local[1],peer[0],peer[1])
  if actual==flow:rows.append({'state':f[3],'uid':int(f[7]),'inode':int(f[9])})
 check('proc_tcp',len(rows)==1 and rows[0]['state']=='01' and rows[0]['uid']==2000 and rows[0]['inode']>0,'same actual established TCP tuple/UID/inode');inode=rows[0]['inode'];fds=[]
 for line in read(art/('owner-sockets-'+phase+'.txt')).decode().splitlines():
  m=re.fullmatch(r'(\d+) socket:\[(\d+)\]',line)
  if m and int(m[2])==inode:fds.append(int(m[1]))
 check('proc_owner',len(fds)>=1,'same TCP inode owned by actual broker PID');return {'pid':pid,'process_starttime_ticks':start,'socket_inode':inode,'fds':fds,'tcp_state':'01','uid':2000}

def network(d):
 group={'case':d.name};s=js(d/'summary.json');family=4 if s['family']=='ipv4' else 6
 cap={r:pcap(d/(r+'-external.pcap')) for r in ROLES};origin={r:[x for x in cap[r] if x['facts']['source_mac']==MAC[r]] for r in ROLES};destination={r:[x for x in cap[r] if x['facts']['source_mac']==MAC['server' if r=='client' else 'client']] for r in ROLES}
 for r in ROLES:check('pcap_roles',len(origin[r])+len(destination[r])==len(cap[r]),r+' all MACs attributed')
 ledger=jl(d/'valve.jsonl');check('valve',ledger[0]['event']=='start' and ledger[-1]['event']=='close' and ledger[-1]['finished'] and ledger[-1]['failure'] is None,'finished ledger')
 fds=ledger[0]['endpoint_fds'];check('valve',set(fds)==set(ROLES),'two endpoints')
 for r,f in fds.items():check('valve_fd',stat.S_ISSOCK(f['mode']) and f['dev']>0 and f['ino']>0 and f['fd']>=0,r+' live socket identity')
 check('valve_fd',fds['client']['ino']!=fds['server']['ino'],'distinct endpoints')
 ptr=collections.Counter();forwarded={r:[] for r in ROLES};allpack={};streams=collections.defaultdict(list);queue=set();active=None;phases={};lasttime=-1;exitallowed=False
 for number,e in enumerate(ledger,1):
  check('valve_order',e['index']==number and e['monotonic_ns']>=lasttime,'exact ledger index/monotonic');lasttime=e['monotonic_ns'];event=e['event']
  if 'endpoint_fds' in e:check('valve_fd',e['endpoint_fds']==fds,'FD retained at '+event)
  if event=='arm':
   check('valve_arm',active is None and not queue and e['buffers_empty'] and e['rule_id']==e['direction'] and e['direction'] in ('c2s','s2c','both'),'barrier');active=e;phases[e['rule_id']]={'arm':e,'drops':collections.Counter(),'new_syns':set()}
  elif event=='disarm':
   check('valve_disarm',active and e['rule_id']==active['rule_id'] and not queue and e['buffers_empty'],'barrier');p=phases[e['rule_id']];check('valve_disarm',{r:p['drops'][r] for r in ROLES}==e['dropped'],'drop totals');p['disarm']=e;active=None
  elif event=='packet':
   r=e['origin'];check('valve_ingress',r in ROLES and ptr[r]<len(origin[r]),'source packet exists');x=origin[r][ptr[r]];ptr[r]+=1;check('valve_ingress',meta(x['raw'])=={'bytes':e['bytes'],'sha256':e['sha256']} and x['facts']==e['packet'],'raw bytes and independent decode match');pid=e['packet_id'];check('valve_ingress',pid not in allpack and pid==len(allpack)+1,'unique sequential packet');x={**x,'entry':e};allpack[pid]=x
   f=tuple(x['facts'].get('flow',()));target=active and f in (tuple(active['flow']),reverse(active['flow']));drop=bool(target and (active['direction']=='both' or (active['direction']=='c2s' and r=='client') or (active['direction']=='s2c' and r=='server')))
   check('valve_decision',e['action']==('drop' if drop else 'queue') and e['rule_id']==(active['rule_id'] if active else None),'exact tuple+direction decision')
   if drop:phases[active['rule_id']]['drops'][r]+=1
   else:queue.add(pid)
   if f:
    check('tcp_scope',f[2]==3240 or f[4]==3240 or ((f[2]==3241 or f[4]==3241) and f[0]==family),'only fixed TLS or negative probe')
    streams[f].append(x)
    if active and r=='client' and f[4]==3241 and x['facts']['flags']&2:phases[active['rule_id']]['new_syns'].add(f)
   PACKET_AUDIT.append({'case':d.name,'packet_id':pid,'ledger_index':number,'origin':r,'pcap_origin_index':ptr[r]-1,'bytes':len(x['raw']),'sha256':sha(x['raw']),'facts':x['facts'],'action':e['action'],'rule_id':e['rule_id']})
  elif event=='forward':
   pid=e['packet_id'];check('valve_forward',pid in queue,'only queued forwarded once');queue.remove(pid);x=allpack[pid];r=x['entry']['origin'];dest='server' if r=='client' else 'client';check('valve_forward',e['origin']==r and e['destination']==dest,'cross endpoint');forwarded[dest].append(x['raw'])
  elif event=='guest_exit_allowed':check('valve_exit',active is None and not queue,'cleanup before EOF permission');exitallowed=True
  elif event=='guest_eof':check('valve_exit',exitallowed,'no early guest EOF')
  elif event=='finished':check('valve_finished',not queue and active is None and e['packet_count']==len(allpack) and e['rules']==['both','c2s','s2c'],'all packet decisions terminal')
  else:check('valve_event',event in ('start','close'),'known event')
 for r in ROLES:
  check('pcap_complete',ptr[r]==len(origin[r]),r+' all ingress accounted');check('pcap_complete',forwarded[r]==[x['raw'] for x in destination[r]],r+' exact ordered forwarded bytes equal actual peer PCAP')
 check('valve_phases',set(phases)=={'c2s','s2c','both'} and all('disarm' in x for x in phases.values()),'three finished phases')
 check('valve_phases',len({tuple(x['arm']['flow']) for x in phases.values()})==3,'fresh target connection each phase')
 # Real plaintext rejection requires reversed SYN -> RST/ACK, not merely any RST.
 refused=[]
 for f,rows in streams.items():
  if f[4]!=3240:continue
  check('plaintext_3240',all(not x['payload'] for x in rows+streams.get(reverse(f),[])),'no plaintext data');syns=[x for x in rows if x['facts']['flags']&2];check('plaintext_3240',syns and all(x['facts']['flags']&~0xc2==0 for x in syns),'client SYN flags');seqs={x['facts']['sequence'] for x in syns};check('plaintext_3240',len(seqs)==1,'one negative-probe epoch');seq=next(iter(seqs));rs=streams.get(reverse(f),[]);matches=[x for x in rs if x['facts']['flags']==0x14 and x['facts']['sequence']==0 and x['facts']['acknowledgement']==(seq+1)&0xffffffff and x['entry']['index']>syns[0]['entry']['index']]
  check('plaintext_3240',matches and all(x['entry']['action']=='queue' for x in rows+rs),'actual forwarded SYN/RSTACK refusal');refused.append({'flow':f,'syn':seq,'syn_index':syns[0]['entry']['index'],'rst_index':matches[0]['entry']['index']})
 check('plaintext_3240',{x['flow'][0] for x in refused}=={4,6},'both protocol families refused')
 # Ready barrier: clocks are never subtracted across host and guest.
 controls=jl(d/'host-control.jsonl');acks={r:[x for x in controls if x['role']==r and x['command']=='NETWORK_READY_ACK'] for r in ROLES};starts=[x for x in controls if x['command']=='START'];check('ready',len(starts)==1 and starts[0]['role']=='client','single START');start=starts[0];readyfacts={}
 rawblocks={r:artifacts_from_log(d,r) for r in ROLES}
 for r in ROLES:
  art=d/(r+'-artifacts');obs=jl(art/'network-ready-observations.jsonl');last=js(art/'network-ready.json');check('ready',1<=len(obs)<=200 and last==obs[-1],'bounded readiness observation');times=[float(x['guest_uptime']) for x in obs]
  check('ready',times==sorted(times) and times[-1]-times[0]<=10.0 and all(x['schema_version']==1 and x['role']==r and x['stage']=='ready' and x['observation']==i for i,x in enumerate(obs,1)),'guest monotonic bounded polling');check('ready',last['carrier']==1 and last['operstate']=='up' and not any(x['carrier']==1 and x['operstate']=='up' for x in obs[:-1]),'first actual carrier/up result');check('ready',read(art/'network-ready.json')==read(art/'network-ready-observations.jsonl').splitlines(keepends=True)[-1],'actual last raw line')
  text=read(art/'network-ready.txt').decode();check('ready','LOWER_UP' in text and 'NO-CARRIER' not in text and ('192.0.2.'+('2' if r=='client' else '1')+'/24') in text and ('fd42:7275:7374::'+('2' if r=='client' else '1')+'/64') in text,'actual address and carrier state');check('ready',len(acks[r])==1 and acks[r][0]['monotonic_ns']<start['monotonic_ns'],'both host ACK-send completions precede START-send completion');ack=acks[r][0];expected={name:sha(read(art/name)) for name in ['network-ready-observations.jsonl','network-ready.json','network-ready.txt']};check('ready',ack['observed']=={'role':r,'poll_count':len(obs),'guest_uptime':last['guest_uptime'],'carrier':1,'operstate':'up','artifact_sha256':expected},'ACK bound to actual evidence')
  log=read(d/(r+'-evidence.log')).decode();check('ready',log.index('LINK_NETWORK_READY role='+r)<log.index('LINK_NETWORK_READY_ACK role='+r),'guest readiness before guest ACK marker');readyfacts[r]={'observations':len(obs),'uptime':last['guest_uptime'],'host_ack_ns':ack['monotonic_ns']}
  for name in expected:check('ready_raw',name in rawblocks[r] and read(d/'live'/r/name)==read(art/name),'both live and final raw '+name)
  status=read(art/'identity-agent-1.txt').decode();uid=re.search(r'^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)',status,re.M);check('process_roles',uid and uid.groups()==('2000',)*4,'broker actual UID');check('process_roles',all(re.search(r'^'+f+r':\s+0+$',status,re.M) for f in ['CapInh','CapPrm','CapEff','CapAmb']),'broker actual no capabilities')
  status=read(art/'identity-helper.txt').decode();uid=re.search(r'^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)',status,re.M);check('process_roles',uid and uid.groups()==('0',)*4,'helper actual root')
 probe=js(d/'client-artifacts/network-probe-start.json');check('ready',probe['stage']=='negative-probes' and probe['role']=='client' and probe['carrier']==1 and probe['operstate']=='up' and float(probe['guest_uptime'])>=float(readyfacts['client']['uptime']),'actual preprobe re-observation');log=read(d/'client-evidence.log').decode();check('ready',log.index('LINK_NETWORK_READY_ACK role=client')<log.index('LINK_NETWORK_PROBES_START'),'guest ACK observed before probes marker')
 generated=read(d/'client-generated-link-init.sh').decode();check('ready_source',generated.index('    host_expect START\n')<generated.index('    network_sample negative-probes 1 > /tmp/network-probe-start.json')<generated.index('if timeout 5 /usr/bin/nc-openbsd "-$family" -z -v -w 2 "$peer" 3240'),'executed init preserves bounded nc after readiness')
 for version in (4,6):
  txt=read(d/f'client-artifacts/plaintext-{version}.log').decode();check('plaintext_3240','Connection refused' in txt and 'timed out' not in txt,'actual nc rejection '+str(version))
 # Health identities are cross-bound to actual private records, exact device keys, and the raw 5tuple.
 keys=js(d/'keys.json');phasefacts=[];sibling_identity=None;owner_epochs={};sibling_inode=None;targetflows={tuple(p['arm']['flow']):p['arm']['index'] for p in phases.values()}
 for phase in ('c2s','s2c','both'):
  ownership={}
  p=phases[phase];arm=p['arm'];disarm=p['disarm'];flow=tuple(arm['flow']);bindings={};states={};hs={}
  for r in ROLES:
   path=d/'bindings'/f'{phase}-{r}-health.json';health=js(path);check('health_raw',sha(read(path))==arm['binding'][r+'_health_sha256'] and read(path)==read(d/(r+'-artifacts')/f'health-{phase}-{r}-pre.json'),'prearm raw binding');check('health_schema',health['schema_version']==1 and health['journal_healthy'] and health['response']['result']=='session_health' and health['response']['next_after'] is None,'actual health IPC');states[r]=js(d/(r+'-artifacts')/f'agent-pre-{phase}.json');check('health_state',states[r]['schema_version']==2,'actual private state2');h=next(x for x in health['response']['sessions'] if x['session_id']==arm['binding'][r+'_session']);ss=next(x for x in states[r]['sessions'] if x['id']==h['session_id']);link=h['link'];expectedrole='import' if r=='client' else 'incoming'
   check('health_identity',h['instance_id']==ss['instance_id']==arm['binding'][r+'_instance'] and h['role']==ss['role']==expectedrole and ss['device_key']==keys[0] and ss['state']=='Connected' and not ss['terminal'],'exact target session/instance/role/key');check('health_identity',h['availability']=='live' and link['identity']['connection']==arm['binding'][r+'_connection'] and link['protocol']=='framed_v2' and link['phase']=='open' and link['end'] is None and link['last_reply_sequence']>=1 and link['last_round_trip_micros'] is not None,'live matched transport reply');check('health_identity',flow_identity(link)==(flow if r=='client' else reverse(flow)),'health actual TCP tuple matches selected raw flow');hs[r]=health;bindings[r]=h
   ownership[r]=proc_socket_owner(d,r,'pre-'+phase,flow if r=='client' else reverse(flow));epoch=(ownership[r]['pid'],ownership[r]['process_starttime_ticks'])
   if r not in owner_epochs:owner_epochs[r]=epoch
   check('proc_owner',owner_epochs[r]==epoch,'same broker process through all phases')
   ended=js(d/(r+'-artifacts')/f'health-{phase}-{r}-terminal.json');ht=next(x for x in ended['response']['sessions'] if x['session_id']==h['session_id']);check('health_terminal',ht['instance_id']==h['instance_id'] and ht['link']['identity']==link['identity'] and ht['availability']=='owner_ended' and ht['link']['phase']=='ended','same ended connection snapshot')
   end=ht['link'];check('health_terminal',end['end'] in ('probe_timeout','peer_eof','io') and end['elapsed_millis']>=link['elapsed_millis'],'terminal transport fact');bindings[r+'_end']=end
  check('health_timeout',any(bindings[r+'_end']['end']=='probe_timeout' for r in ROLES),'actual same-data timeout observed')
  for r in ROLES:
   x=bindings[r+'_end']
   if x['end']=='probe_timeout':check('health_timeout',x['probe']=='timed_out' and x['last_probe_sequence']>x['last_reply_sequence'] and x['elapsed_millis']-x['last_probe_enqueued_millis']>=3000,'local monotonic timeout age')
  check('valve_epoch',arm['syn_sequences']==[next(x['facts']['sequence'] for x in streams[f] if x['facts']['flags']&2) for f in (flow,reverse(flow))],'actual two SYN epochs pinned')
  for f in (flow,reverse(flow)):check('valve_epoch',any(x['payload'] and x['entry']['index']<arm['index'] for x in streams[f]),'real prearm bidirectional TLS data')
  sibling=next(x for x in states['server']['sessions'] if x['role']=='incoming' and x['device_key']==keys[1] and not x['terminal']);h=next(x for x in hs['server']['response']['sessions'] if x['session_id']==sibling['id']);sf=reverse(flow_identity(h['link']));identity=(sibling['id'],sibling['instance_id'],h['link']['identity'])
  sibling_owner=proc_socket_owner(d,'server','pre-'+phase,reverse(sf))
  if sibling_inode is None:sibling_inode=sibling_owner['socket_inode']
  check('proc_owner',sibling_inode==sibling_owner['socket_inode'],'original sibling TCP inode retained across phases')
  if sibling_identity is None:sibling_identity=identity
  check('sibling',identity==sibling_identity and sf!=flow and h['availability']=='live','original sibling connection retained');terminal=js(d/'server-artifacts'/f'health-{phase}-server-terminal.json');ht=next(x for x in terminal['response']['sessions'] if x['session_id']==sibling['id']);check('sibling',ht['availability']=='live' and ht['link']['identity']==h['link']['identity'] and ht['link']['phase']=='open','same sibling still live')
  for f in (sf,reverse(sf)):
   rows=streams[f];check('sibling',all(x['entry']['action']=='queue' for x in rows),'sibling never dropped');check('sibling',any(x['payload'] and arm['index']<x['entry']['index']<disarm['index'] for x in rows),'sibling actual TLS payload continues during fault')
  catalog=js(d/'client-artifacts'/f'catalog-during-{phase}.json');check('catalog',catalog['schema_version']==1 and catalog['journal_healthy'] and catalog['response']['result']=='catalog','real new Catalog RPC succeeds');fresh=p['new_syns']-{flow,sf};check('catalog',len(fresh)==1,'exactly one new non-target/non-sibling TLS flow during phase');cf=next(iter(fresh))
  for f in (cf,reverse(cf)):
   check('catalog',f in streams and any(x['payload'] and arm['index']<x['entry']['index']<disarm['index'] for x in streams[f]) and all(x['entry']['action']=='queue' for x in streams[f]),'actual correlated new flow both directions never dropped')
  expected={'client':phase in ('c2s','both'),'server':phase in ('s2c','both')};check('valve_drops',all((p['drops'][r]>0)==expected[r] for r in ROLES),'strict directional actual drops')
  phasefacts.append({'phase':phase,'target':flow,'target_sessions':{r:bindings[r]['session_id'] for r in ROLES},'target_connections':{r:bindings[r]['link']['identity']['connection'] for r in ROLES},'target_ends':{r:bindings[r+'_end']['end'] for r in ROLES},'drop_packets':dict(p['drops']),'actual_broker_tcp_ownership':ownership,'actual_sibling_tcp_ownership':sibling_owner,'arm_host_ns':arm['monotonic_ns'],'disarm_host_ns':disarm['monotonic_ns'],'sibling':sf,'catalog_correlated_flow':cf})
 tls=[]
 for f,rows in sorted(streams.items()):
  if 3241 not in (f[2],f[4]):continue
  cf=f if f[4]==3241 else reverse(f);allow=cf in targetflows and rows[-1]['entry']['index']>targetflows[cf];fact=tls_stream(rows,allow);client=f[4]==3241;check('tls_direction',all(('client_offer' in x)==client for x in fact['hello']),'correct-direction hello');tls.append({'flow':f,**fact})
 group.update({'pcap_packets':{r:len(cap[r]) for r in ROLES},'ingress_packets':dict(ptr),'forwarded_packets':{r:len(forwarded[r]) for r in ROLES},'drops':sum(sum(p['drops'].values()) for p in phases.values()),'socket_endpoint_fds':fds,'readiness':readyfacts,'start_host_ns':start['monotonic_ns'],'negative_probes':refused,'phases':phasefacts,'tls_directions':tls,'raw_base64_artifacts':{r:len(rawblocks[r]) for r in ROLES}});return group

PHASES=('c2s','s2c','both')
HEALTH_FIELDS={'protocol','identity','sample_sequence','elapsed_millis','phase','probe','last_probe_sequence','last_probe_enqueued_millis','last_reply_sequence','last_reply_millis','last_round_trip_micros','stats','end'}
STATS_FIELDS={'app_read_bytes','app_write_bytes','last_app_read_millis','last_app_write_millis','receive_pending_bytes','send_capacity_blocked','transport_write_blocked'}
PROOFS={'native_import':'native_import_released','native_stub':'native_stub_available','native_export':'native_export_restored','usbfs_grant':'physical_baseline_restored'}
def only(rows,predicate,label):
 found=[r for r in rows if predicate(r)];check('unique',len(found)==1,label);return found[0]
def health_shape(value):
 check('strict_health',set(value)==HEALTH_FIELDS,'exact explicit fields including nullable options')
 check('strict_health',value['protocol']=='framed_v2' and value['identity'] is not None and set(value['identity'])=={'connection','tcp'} and set(value['identity']['tcp'])=={'local','peer'} and value['identity']['connection']>0,'real framed connection identity')
 stats=value['stats'];check('strict_health',stats is not None and set(stats)==STATS_FIELDS and 0<=stats['receive_pending_bytes']<=65535,'bounded payload accounting and all nullable stats present')
 check('strict_health',all(type(stats[k]) is bool for k in ('send_capacity_blocked','transport_write_blocked')),'boolean block observations')
 for k in ('last_app_read_millis','last_app_write_millis'):
  check('strict_health',stats[k] is None or (type(stats[k]) is int and 0<=stats[k]<=value['elapsed_millis']),'app progress time')
 for k in ('last_probe_sequence','last_probe_enqueued_millis','last_reply_sequence','last_reply_millis','last_round_trip_micros'):
  check('strict_health',value[k] is None or (type(value[k]) is int and value[k]>=0),'explicit nullable '+k)
 if value['last_reply_sequence'] is not None:
  check('strict_health',value['last_probe_sequence'] is not None and value['last_reply_sequence']<=value['last_probe_sequence'] and value['last_round_trip_micros'] is not None,'matched reply sequence cannot exceed enqueue')
def root_terminal(root,session):
 check('root_capability',len(session['attempts'])==1,'one actual attempt')
 a=session['attempts'][0];expected={'resource_id':a['resource_id'],'kind':a['kind'],'device_key':session['device_key'],'owner_uid':2000,'correlation':a['correlation'],'proof':PROOFS[a['kind']]}
 check('root_capability',a['correlation']['session_id']==session['id'] and a['correlation']['instance_id']==session['instance_id'],'exact original application epoch and session')
 ts=[t.get('terminal',t) for t in root['terminals']]
 check('root_capability',ts.count(expected)==1 and a['proof'] in (None,expected['proof']),'exact kind/resource/correlation/key/UID/proof')
 return expected

def lifecycle(d):
 backend=d.name.split('-')[1];data=lambda role,name:js(d/(role+'-artifacts')/name);raw=lambda role,name:read(d/(role+'-artifacts')/name)
 keys=js(d/'keys.json');check('lifecycle',keys==data('server','keys.json')==data('client','keys.json') and len(set(keys))==2,'common fixed target and sibling keys')
 check('lifecycle',keys==['virtual:hid','virtual:cdc-acm'] if backend=='virtual' else all(k.startswith('native:usb-' if backend=='native' else 'physical:usb-') for k in keys),'requested namespace')
 phases=[];all_ids={r:set() for r in ROLES};serial_record=None;hid_ids=set();hid_generations=set()
 baseline=None if backend=='virtual' else data('server','host-before.json')
 for phase in PHASES:
  bundle=js(d/'bindings'/f'{phase}-terminal.json');check('phase_bundle',bundle['schema_version']==1 and bundle['phase']==phase,'original pre-disarm terminal bundle')
  for name,want in bundle['source_artifacts'].items():
   role,file=name.split('/',1);check('phase_bundle',meta(raw(role,file))==want,'original source byte hash '+name)
  ends=[]
  for role in ROLES:
   pre=data(role,f'agent-pre-{phase}.json');post=data(role,f'agent-fault-{phase}.json');wanted='import' if role=='client' else 'incoming'
   for state in (pre,post):check('disk2',set(state)=={'schema_version','sequence','jobs','sessions','audit','requests','link_history'} and state['schema_version']==2,'strict private disk2 top-level raw shape')
   target=only(pre['sessions'],lambda s:s['device_key']==keys[0] and s['role']==wanted and not s['terminal'],'pre target')
   ended=only(post['sessions'],lambda s:s['id']==target['id'],'original terminal target')
   sibling=only(pre['sessions'],lambda s:s['device_key']==keys[1] and s['role']==wanted and not s['terminal'],'pre sibling')
   retained=only(post['sessions'],lambda s:s['id']==sibling['id'],'same sibling')
   check('actual_cleanup',all(ended[k]==target[k] for k in ('id','instance_id','job_id','device_key','profile','role')) and ended['terminal'] and ended['state']=='Disconnected' and ended['cleanup']=='verified','same original target really finished')
   check('sibling_owner',all(retained[k]==sibling[k] for k in ('id','instance_id','job_id','device_key','attempts','terminal','cleanup','state')) and retained['state']=='Connected' and retained['operation_error'] is None,'same connected sibling capability')
   prehealth=data(role,f'health-{phase}-{role}-pre.json')['response']['sessions'];posthealth=data(role,f'health-{phase}-{role}-terminal.json')['response']['sessions']
   ph=only(prehealth,lambda h:h['session_id']==target['id'],'pre health');eh=only(posthealth,lambda h:h['session_id']==target['id'],'ended health')
   for h in (ph,eh):health_shape(h['link'])
   link=eh['link'];check('actual_cleanup',eh['instance_id']==target['instance_id'] and eh['availability']=='owner_ended' and link['identity']==ph['link']['identity'] and link['phase']=='ended' and link['end'] is not None and link['sample_sequence']>=ph['link']['sample_sequence'] and link['elapsed_millis']>=ph['link']['elapsed_millis'],'same connection frozen after real owner ended')
   elapsed=None
   if link['end']=='probe_timeout':
    elapsed=link['elapsed_millis']-link['last_probe_enqueued_millis'];check('timeout',3000<=elapsed<=5000 and link['probe']=='timed_out' and link['last_reply_sequence']<link['last_probe_sequence'] and ended['operation_error']=='timeout','same driver actual timeout within original scheduling allowance')
   proof=None
   if role=='client' or backend=='native':
    rp=data(role,f'root-pre-{phase}.json');rf=data(role,f'root-fault-{phase}.json');proof=root_terminal(rf,ended)
    check('actual_cleanup',rf['schema_version']==2 and len(rf['terminals'])==PHASES.index(phase)+1,'exact retired target total')
    if role=='client':
     sr=only(rp['records'],lambda r:r['target']['device_key']==keys[1],'original sibling import');tr=only(rp['records'],lambda r:r['target']['device_key']==keys[0],'original target import')
     check('actual_cleanup',rf['records']==[sr] and rf['exports']==[] and proof['resource_id']==tr['channel_id'],'only original target import retired')
     loc=data(role,f'local-fault-{phase}.json');ts=tr['state']['receipt']['session'];ss=sr['state']['receipt']['session'];gen=ss['local_device_generation']
     check('actual_cleanup',loc['old_local_exists'] is False and loc['old_local_bus_id']==ts['local_bus_id'] and loc['sibling_local_bus_id']==ss['local_bus_id'] and loc['sibling_generation']==f"{gen['filesystem_device']}:{gen['inode']}",'target old sysfs gone and sibling original generation retained')
     rows=[l.split() for l in raw(role,f'vhci-fault-{phase}.txt').decode().splitlines() if l.startswith(('hs ','ss '))]
     trw=only(rows,lambda r:int(r[1])==ts['port'],'original target VHCI port');srw=only(rows,lambda r:int(r[1])==ss['port'],'original sibling VHCI port')
     check('actual_cleanup',int(trw[2])==4 and int(srw[2])==6 and srw[6]==ss['local_bus_id'] and int(srw[4],16)==ss['remote_device_id'] and sum(int(r[2])==6 for r in rows)==1 and all(int(r[2]) in (4,6) for r in rows),'exact original target NULL and serial USED')
    else:
     prior={x['registration']['device_key']:x for x in rp['exports']};current={x['registration']['device_key']:x for x in rf['exports']}
     check('native_export',rp['records']==rf['records']==[] and set(prior)==set(current)==set(keys) and current[keys[1]]==prior[keys[1]] and current[keys[0]]['stub'] is None and {k:v for k,v in current[keys[0]].items() if k!='stub'}=={k:v for k,v in prior[keys[0]].items() if k!='stub'} and proof['resource_id']==prior[keys[0]]['stub']['channel_id'],'original target stub retires without modifying sibling stub/export')
   else:
    check('rust_incoming',target['attempts']==ended['attempts']==sibling['attempts']==retained['attempts']==[] and raw(role,f'native-pre-{phase}.status')==raw(role,f'native-fault-{phase}.status')==b'absent\n','Rust lease has no invented Root helper terminal')
    if backend=='usbfs':
     rp=data(role,f'root-pre-{phase}.json');rf=data(role,f'root-fault-{phase}.json')
     check('usbfs_grants',rp==rf and rp['schema_version']==3 and len(rp['records'])==2 and not rp['terminals'],'immutable original grants retained until final cooperative Finish')
    else:check('virtual',raw(role,f'physical-pre-{phase}.status')==raw(role,f'physical-fault-{phase}.status')==b'absent\n','no physical journal fabricated')
   recorded=only(bundle['outcomes'],lambda x:x['role']==role,'pre-disarm exact role outcome')
   check('phase_bundle',recorded['session_id']==target['id'] and recorded['connection']==link['identity'] and recorded['end']==link['end'] and recorded['operation_error']==ended['operation_error'] and recorded['cleanup']==ended['cleanup'] and recorded['root_terminal']==proof,'pre-disarm bundle matches raw actual lifecycle facts')
   all_ids[role].update(s['id'] for s in pre['sessions']);ends.append({'role':role,'session_id':target['id'],'connection':link['identity'],'end':link['end'],'timeout_age_millis':elapsed,'root_proof':proof})
  check('timeout',any(x['end']=='probe_timeout' for x in ends),'one actual same-data probe timeout per phase')
  round_id=(PHASES.index(phase)+1)*2;tx=f'RUSTUSB-TX-{round_id}-0001:fedcba9876543210\n'.encode()
  check('sibling_io',raw('client',f'tx-{round_id}.bin')==tx and (raw('client',f'echo-{round_id}.bin')==tx if backend=='virtual' else raw('client',f'rx-{round_id}.bin')==f'RUSTUSB-RX-{round_id}-0001:0123456789abcdef\n'.encode()),'actual35B sibling I/O before each disarm')
  if backend!='virtual':physical_scope(d,backend,phase,baseline)
  phases.append({'phase':phase,'ends':ends,'sibling_io_round':round_id})
 # Seven35B I/O rounds; four distinct post-fault HID generations and one serial.
 txs=[]
 for round_id in range(1,8):
  tx=f'RUSTUSB-TX-{round_id}-0001:fedcba9876543210\n'.encode();txs.append(tx)
  check('io',len(tx)==35 and raw('client',f'tx-{round_id}.bin')==tx,'actual serial TX round'+str(round_id))
  if backend=='virtual':check('io',raw('client',f'echo-{round_id}.bin')==tx,'actual virtual CDC echo')
  else:
   rx=f'RUSTUSB-RX-{round_id}-0001:0123456789abcdef\n'.encode();check('io',raw('client',f'rx-{round_id}.bin')==rx==read(d/f'ftdi-host-injected-{round_id}.bin'),'actual FTDI RX vs host injection')
  if round_id%2:
   if backend!='virtual':
    ev=raw('client',f'input-{round_id}.bin');check('io',len(ev)>0 and len(ev)%24==0,'whole actual evdev events');values=[struct.unpack_from('<qqHHi',ev,k)[2:] for k in range(0,len(ev),24)];check('io',(1,round_id+1,1) in values and (1,round_id+1,0) in values,'actual HID press/release')
   else:check('io',not(d/'client-artifacts'/f'input-{round_id}.bin').exists(),'virtual HID has no invented key event')
   records=data('client',f'client-receipts-{round_id}.json')['records'];check('receipt',len(records)==2,'two actual connected imports')
   hid=only(records,lambda r:r['target']['device_key']==keys[0],'new HID');serial=only(records,lambda r:r['target']['device_key']==keys[1],'same serial')
   if serial_record is not None:check('receipt',serial==serial_record,'same serial capability across faults')
   serial_record=serial;hid_ids.add(hid['channel_id']);hid_generations.add(json.dumps(hid['state']['receipt']['session']['local_device_generation'],sort_keys=True))
   for record in records:
    receipt=record['state']['receipt'];check('receipt',record['owner']['uid']==2000 and record['owner']['starttime']>0 and record['correlation'] is not None and record['state']['phase']=='observed' and receipt['session']['status']==6 and receipt['kernel_socket_inode']>0 and receipt['profile'] is not None and receipt['session']['local_device_generation'] is not None,'strong actual local import receipt')
 check('receipt',len(hid_ids)==len(hid_generations)==4,'four fresh target capabilities and generations')
 if backend!='virtual':check('io',read(d/'ftdi-host-rx.bin')==b''.join(txs) and len(b''.join(txs))==245,'host exact ordered245B FTDI TX sink')
 # Full actual final history and Root distinction, never infer Rootproof for Rust incoming.
 finals={}
 for role in ROLES:
  final=data(role,'agent-final.json');expected=7 if role=='server' else 5
  check('disk2',set(final)=={'schema_version','sequence','jobs','sessions','audit','requests','link_history'} and final['schema_version']==2 and len(final['sessions'])==expected and len({s['id'] for s in final['sessions']})==expected and len(final['jobs'])==(1 if role=='server' else 5),'complete canonical final disk2 history')
  all_ids[role].update(s['id'] for s in data(role,'agent-final-held.json')['sessions']);check('disk2',all_ids[role]=={s['id'] for s in final['sessions']},'every original observed session retained')
  check('actual_cleanup',all(s['terminal'] and s['cleanup']=='verified' and s['state']=='Disconnected' for s in final['sessions']) and all(j['state']=='finished' and j['operator_uid']==1000 for j in final['jobs']),'actual final cleanup and job ownership')
  history=final['link_history'];check('disk2',len(history)==expected and len({x['session_id'] for x in history})==expected,'one health history per real session')
  root=data(role,'root-final.json') if role=='client' or backend!='virtual' else None
  if root is not None:check('actual_cleanup',root['records']==[] and root.get('exports',[])==[] and len(root['terminals'])==(expected if role=='client' or backend=='native' else 2),'actual final helper capabilities empty with exact proof count')
  for ss in final['sessions']:
   check('disk2',any(j['id']==ss['job_id'] and j['instance_id']==ss['instance_id'] for j in final['jobs']),'same actual job/session epoch')
   if role=='client' or backend=='native' or (backend=='usbfs' and ss['role']=='export'):root_terminal(root,ss)
   else:check('rust_incoming',ss['attempts']==[],'no fabricated helper proof')
   h=only(history,lambda h:h['session_id']==ss['id'] and h['instance_id']==ss['instance_id'],'same actual history entry')
   if ss['role']=='export':check('disk2',h['link'] is None,'export not falsely given connection health')
   else:health_shape(h['link']);check('disk2',h['link']['phase']=='ended' and h['link']['end'] is not None and h['link']['last_reply_sequence']>=1,'actual final ended connection including fresh reply')
  check('actual_cleanup',raw(role,'wait-agent-1.txt').strip()==b'0','actual agent TERM/reap0')
  stdout=read(d/f'{role}-evidence.log').decode();kernel=read(d/f'{role}-serial.log').decode(errors='replace');check('guest',stdout.count('RUSTUSB_LINK_RESULT: PASS role='+role)==1 and not any(mark in stdout for mark in ['RUSTUSB_LINK_RESULT: FAIL','SECURE_ARTIFACT_ERROR','SECURE_ARTIFACT_OVERSIZE','SECURE_POWEROFF_RETURNED']) and not any(mark in kernel for mark in ['Kernel panic','Oops:']),'whole guest final evidence and kernel check')
  deny=data(role,'agent-after-denied.json');check('acl',all(deny[k]==[] for k in ['jobs','sessions','requests']) and raw(role,'root-before-denied.json')==raw(role,'root-after-denied.json'),'unauthorized controls have no resource effect')
  for reason in ['uid','profile']:check('acl',data(role,'denied-'+reason+'.json')['response']=={'result':'error','code':'permission'},'actual typed '+reason+' denial')
  finals[role]={'session_count':expected,'root_proof_count':0 if root is None else len(root['terminals']),'disk2_sequence':final['sequence']}
 if baseline is not None:check('actual_cleanup',data('server','host-restored.json')==baseline,'full original cfg/alt/interface/driver and generation baseline restored')
 rows=[l.split() for l in raw('client','vhci-final.txt').decode().splitlines() if l.startswith(('hs ','ss '))];check('actual_cleanup',rows and all(int(r[2])==4 for r in rows),'all client VHCI ports actually NULL')
 return {'backend':backend,'phases':phases,'io_rounds':7,'serial_each_direction_bytes':245,'actual_hid_key_events':backend!='virtual','finals':finals}


def physical_scope(d,backend,phase,baseline):
 art=d/'server-artifacts';initial=js(art/('root-pre-'+phase+'.json'));keys=js(d/'keys.json')
 records=initial['exports'] if backend=='native' else initial['records']
 check('physical_scope',len(records)==len(baseline)==2 and len({r['bus'] for r in baseline})==2,'nonempty original complete physical pair')
 expected_by_bus={r['bus']:r for r in baseline}
 proc=read(art/('owner-stat-pre-'+phase+'.txt')).decode();m=re.fullmatch(r'(\d+) \(rustusb\) (.*)\n?',proc);check('physical_scope',m is not None,'actual original owner proc stat');owner_pid=int(m[1]);owner_start=int(m[2].split()[19])
 exports=[r for r in js(art/('agent-pre-'+phase+'.json'))['sessions'] if r['role']=='export']
 for record in records:
  key=record['registration']['device_key'] if backend=='native' else 'physical:'+record['baseline']['device_id'];session=only(exports,lambda x:x['device_key']==key,'original actual export session')
  check('physical_scope',len(session['attempts'])==1 and session['attempts'][0]['correlation']==record['correlation'] and session['attempts'][0]['resource_id']==record['lease_id'],'received root capability equals exact durable application attempt')
  owner=record['owner'];start=owner['starttime'] if backend=='native' else owner['start_time'];check('physical_scope',owner['uid']==2000 and owner['pid']==owner_pid and start==owner_start,'root owner bound to actual broker PID and starttime')
  if backend=='native':
   reg=record['registration'];g=reg['generation'];row=expected_by_bus[g['bus_id']];profile=reg['original_profile']
   check('physical_scope',row['generation']==f"{g['filesystem_device']}:{g['inode']}" and row['path']==g['sysfs_path'] and int(row['busnum'])==g['bus_number'] and int(row['devnum'])==g['device_number'] and int(row['vendor'],16)==g['vendor_id'] and int(row['product'],16)==g['product_id'],'original Native full hardware generation and address')
   expected_if=[{'number':int(i['number'],16),'alternate':int(i['alt']),'class':int(i['class'],16),'subclass':int(i['subclass'],16),'protocol':int(i['protocol'],16),'driver':i['driver'] or None} for i in row['interfaces']]
   check('physical_scope',profile['configuration']==int(row['configuration']) and profile['interfaces']==expected_if and reg['original_device_driver']==row['device_driver'],'original full configuration alternate classes drivers registered')
  else:
   original=record['baseline'];receipt=original['receipt'];row=expected_by_bus[original['bus_id']]
   check('physical_scope',row['generation']==f"{receipt['sysfs_device']}:{receipt['sysfs_inode']}" and int(row['busnum'])==receipt['bus_number'] and int(row['devnum'])==receipt['usb_device_number'] and receipt['node_device']>0 and receipt['node_inode']>0,'original USBFS receipt generation and address')
   expected_if=[{'number':int(i['number'],16),'alternate':int(i['alt']),'driver':i['driver'] or None} for i in row['interfaces']]
   check('physical_scope',original['configuration']==int(row['configuration']) and original['interfaces']==expected_if and record['recovery']=='manual_only','full original physical configuration alternate driver baseline')
 for stage in ('pre','fault'):
  current=js(art/f'host-{stage}-{phase}.json');check('physical_scope',len(current)==2 and {r['bus'] for r in current}==set(expected_by_bus),'both actual physical observations preserved')
  for row in current:
   original=expected_by_bus[row['bus']];hid=original['vendor']=='0627'
   if backend=='native':
    dynamic={'configuration','num_interfaces','configuration_bytes','count_bytes','interfaces','device_driver','usbip_status'}
    check('physical_scope',{k:v for k,v in row.items() if k not in dynamic}=={k:v for k,v in original.items() if k not in dynamic} and row['device_driver']=='usbip-host' and int(row['configuration'])==1 and int(row['num_interfaces'])==len(original['interfaces']) and row['interfaces']==[dict(i,driver='') for i in original['interfaces']] and row['usbip_status']==(1 if stage=='fault' and hid else 2),'Native same physical generation correct imported/AVAILABLE phase')
   else:
    check('physical_scope',{k:v for k,v in row.items() if k!='interfaces'}=={k:v for k,v in original.items() if k!='interfaces'},'USBFS same generation cfg device state')
    expected_if=original['interfaces'] if stage=='fault' and hid else [dict(i,driver='usbfs') for i in original['interfaces']]
    check('physical_scope',row['interfaces']==expected_if,'only fault target driver restored, original sibling remains claimed')

def configuration(d,backend,family):
 data=lambda role,name:js(d/(role+'-artifacts')/name);raw=lambda role,name:read(d/(role+'-artifacts')/name)
 public={name:read(d/f'{name}-public.der') for name in ('ca','server','client')};pin=sha(public['client']);keys=js(d/'keys.json');facts=[]
 for role in ROLES:
  policy=data(role,'helper-policy.json');check('config',policy==data(role,'actual-config-1-policy.json') and policy['allowed_uids']==[1000] and policy['secure_broker_uid']==2000 and policy['trusted_network'] is False and policy['export_device_ids']==[] and policy['peers']==[],'actual helper role/legacy pool isolation')
  if role=='client':check('config',policy['secure_imports']==[{'server_id':'qemu-server','device_keys':keys}],'logical server and exact remote device pool')
  elif backend=='native':check('config',policy['secure_native_device_ids']==[k.split(':',1)[1] for k in keys],'Native privileged maximum pool')
  elif backend=='usbfs':check('config',policy['secure_physical_device_ids']==[k.split(':',1)[1] for k in keys],'USBFS privileged maximum pool')
  else:check('config',policy['secure_native_device_ids']==[] and policy['secure_physical_device_ids']==[],'virtual no physical pool')
  profiles=[{'id':'server','backend':backend,'config':'/etc/rustusb/server.json'}] if role=='server' else [{'id':name,'backend':'client','config':f'/etc/rustusb/client-{name}.json'} for name in ('hid','serial')]
  manifest={'schema_version':1,'broker_uid':2000,'helper_socket':'/run/rustusb/control.sock','control_directory':'/run/agent','data_directory':'/var/lib/rustusb/agent','profiles':profiles,'operators':[{'uid':1000,'profiles':[p['id'] for p in profiles],'actions':['read','start','stop','discover','audit']}]}
  expected={'agent.json':manifest,'policy.json':policy};linkpolicy={'protocol':'framed_v2','interval_millis':1000,'response_timeout_millis':3000}
  if role=='server':expected['server.json']={'schema_version':2,'data_link':linkpolicy,'broker_uid':2000,'listen':'192.0.2.1:3241' if family=='ipv4' else '[fd42:7275:7374::1]:3241','devices':keys,'principals':[{'id':'qemu-client','pins':[pin],'devices':keys}],'ca_certificates':['/etc/rustusb/ca.der'],'certificate_chain':['/etc/rustusb/server.der'],'private_key':'/etc/rustusb/server-key.der'}
  else:
   for name,key in zip(('hid','serial'),keys):expected[f'client-{name}.json']={'schema_version':2,'data_link':linkpolicy,'broker_uid':2000,'server_id':'qemu-server','endpoint':{'host':'192.0.2.1' if family=='ipv4' else 'fd42:7275:7374::1','port':3241},'server_name':'rustusb-qemu.test','device':key,'ca_certificates':['/etc/rustusb/ca.der'],'certificate_chain':['/etc/rustusb/client.der'],'private_key':'/etc/rustusb/client-key.der'}
  for name,value in expected.items():check('config',data(role,'actual-config-1-'+name)==value,'actual fixed '+role+' '+name)
  check('config',raw(role,'actual-config-1-agent.json')==raw(role,'agent-manifest.json') and raw(role,'actual-config-1-client-pin.txt')==(pin+'\n').encode(),'actual loaded manifest bytes and public DER pin')
  for name,value in public.items():check('config',raw(role,'actual-config-1-'+name+'.der')==value,'actual raw public DER '+name)
  dirs={'/','/etc','/etc/rustusb','/run','/run/agent','/var','/var/lib','/var/lib/rustusb','/var/lib/rustusb/agent'}
  pubpaths={'/etc/rustusb/'+n for n in expected}|{'/etc/rustusb/'+n+'.der' for n in public}|{'/etc/rustusb/client-pin.txt'};private='/etc/rustusb/'+role+'-key.der';metadata=data(role,'config-metadata-1.json');by={x['path']:x for x in metadata}
  check('config_metadata',len(by)==len(metadata) and set(by)==dirs|pubpaths|{private},'exact bounded config/ancestor/key metadata coverage')
  for path,row in by.items():
   mode=int(row['mode'],8);check('config_metadata',row['canonical']==path and row['inode']>0 and row['device']>=0,'actual canonical path '+path)
   if path in dirs:
    broker=path in ('/run/agent','/var/lib/rustusb/agent');check('config_metadata',row['kind']=='directory' and row['captured_file'] is None and row['uid']==row['gid']==(2000 if broker else 0) and ((mode== (0o711 if path=='/run/agent' else 0o700)) if broker else not(mode&0o022)),'protected actual ancestor '+path)
   elif path==private:check('config_metadata',row['kind']=='private_key_metadata_only' and row['uid']==row['gid']==2000 and mode==0o600 and row['links']==1 and row['captured_file'] is None and row['bytes']>0,'private key metadata only, no contents/hash')
   else:
    name='actual-config-1-'+path.rsplit('/',1)[1];check('config_metadata',row['kind']=='public_file' and row['uid']==row['gid']==0 and mode==0o644 and row['links']==1 and row['captured_file']==name and row['bytes']==len(raw(role,name)),'root immutable public config '+path)
  check('config_metadata',not any(p.name.endswith('-key.der') for p in (d/(role+'-artifacts')).iterdir()),'no private key artifact')
  facts.append({'role':role,'metadata_paths':len(metadata),'public_der_sha256':{n:sha(b) for n,b in public.items()},'schema2_policy':linkpolicy,'key_content_captured':False})
 return facts

def provenance(base):
 before=js(base/'inputs-before.json');start=js(base/'start.json');check('matrix',start['head']==HEAD and start['clean'] and start['input_count']==len(before)==222 and start['expected_binaries']==BIN,'actual clean execution start scope')
 for e in before:
  b=blob(e['path']);check('global_source',meta(b)=={k:e[k] for k in ('bytes','sha256')},'Gitblob1582 '+e['path']);check('global_current',read(ARGS.repo/e['path'])==b,'current selected frozen input '+e['path'])
 scope={'global_inputs':len(before),'start':{'head':start['head'],'clean':start['clean']},'runs':[]}
 after=base/'inputs-after.json'
 if after.exists():check('global_source',js(after)==before,'complete parent matrix before/after unchanged');scope['parent_after_available']=True
 else:scope['parent_after_available']=False
 progress=base/'results.json';progress_raw=progress.read_bytes();completed=json.loads(progress_raw)
 scope['parent_progress_observation']={'path':str(progress),'before':meta(progress_raw),'selected_health_results':[r for r in completed if r['case'].startswith('health-')],'scope':'Append-only parent is still allowed to run remaining compatibility jobs; only the six completed child originals are immutable review inputs.'}
 (ARGS.output/'parent-progress-observed.json').write_bytes(progress_raw)
 for backend in ('native','usbfs','virtual'):
  for family in ('ipv4','ipv6'):
   name='health-'+backend+'-'+family;d=base/name;s=js(d/'summary.json');rr=only(completed,lambda r:r['case']==name,'actual root completed health result '+name)
   check('run_terminal',s['result']=='PASS' and bool(s.get('completed_utc')) and rr['exit_code']==0 and rr['summary_result']=='PASS' and s['guest_exit_codes']=={'server':0,'client':0},'actual two guests and terminal run '+name)
   check('run_source',s['head']==s['head_after']==HEAD and s['git_status_before']==s['git_status_after']=='' and all(s[k] is True for k in ('source_guard','binary_guard','head_guard','clean_guard')),'actual clean source guards '+name)
   check('binary',s['binary_sha256']==BIN and rr['binary_sha256']==BIN and rr['binary_matches_software'],'exact actual software ELF pair '+name)
   for n,want in s['source_inputs'].items():check('run_source',meta(read(d/'source'/n))==want and read(d/'source'/n)==blob(n),'actual source copy/Gitblob '+n)
   for n,want in s['artifact_manifest'].items():check('archived_artifact',meta(read(d/n))==want,'actual archived bytes '+n)
   for n in s['actual_broker_executable_receipts']:check('binary',read(d/n).split()[0].decode()==BIN['rustusb'],'actual broker proc exe')
   for n in s['actual_helper_executable_receipts']:check('binary',read(d/n).split()[0].decode()==BIN['rustusb-helper'],'actual helper proc exe')
   for role,files in s['payload_sha256'].items():
    for n,digest in BIN.items():check('binary',files['usr/bin/'+n]==digest,'actual role payload ELF')
   scope['runs'].append({'case':name,'source_count':len(s['source_inputs']),'archived_artifacts':len(s['artifact_manifest']),'guest_exits':s['guest_exit_codes']})
 return scope

def main():
 global ARGS
 ap=argparse.ArgumentParser();ap.add_argument('--matrix',type=pathlib.Path,default=pathlib.Path('/tmp/rustusb-prepare-stop-final-1582f06-20261024'));ap.add_argument('--repo',type=pathlib.Path,default=pathlib.Path('/home/ubuntu/github/usbip-client'));ap.add_argument('--output',type=pathlib.Path,required=True);ARGS=ap.parse_args();ARGS.output.mkdir(parents=True,exist_ok=False)
 report={'schema_version':1,'source_head':HEAD,'method':'Standalone stdlib PCAP/TCP/TLS/ordered-ledger and raw lifecycle relational reparse. No project verifier/auditor imports or calls. Read-only git-show for exact blob bytes.','reviewer_independence':'Reviewer participated in Native/link production and a narrow audit.py component; independent reconciliation method, not wholly independent product/harness implementation author.','scope':'Six completed health groups/18actual directional fault phases only. Other preparation/rawv1 groups belong to other reviewers. No partial run accepted as terminal.','limits':['TLS1.3 ServerHello and exact client ALPN offer are clear handshake observations; no encrypted server ALPN, internal h2 PING/PONG or USB completion inferred from ciphertext.','The actual health observations bind a same-data connection and local enqueue-to-library-pong timing, not a wire-only RTT or USB progress proof.','Sequential root/sysfs/FD snapshots are not atomic. Exact helper capability proofs are separate from transport timeout and app cleanup.','No managed Native/USBFS pending URB proof or kernel-internal hard block; no hardware or ARM QEMU runtime claim. Virtual has actual CDC echo, no invented QMP HID input or server physical proof.','Each source capture, actual ELF, original phase tuple/session and artifact is attributed to this1582 run. Older531/222FAIL evidence remains separate.','Host ACK/START timestamps are send completion observations, not guest receipt or exact nc syscall timestamps.','No Cargo, QEMU, program/helper execution, host USB/module/network mutation or repository edits.']}
 try:
  # This preflight refuses absent/nonterminal groups before parsing any run payloads.
  for backend in ('native','usbfs','virtual'):
   for family in ('ipv4','ipv6'):
    path=ARGS.matrix/('health-'+backend+'-'+family)/'summary.json'
    s=json.loads(path.read_bytes());check('preflight',bool(s.get('completed_utc')),'health group must be terminal')
  report['provenance']=provenance(ARGS.matrix)
  for backend in ('native','usbfs','virtual'):
   for family in ('ipv4','ipv6'):
    name='health-'+backend+'-'+family;print('reviewing '+name,flush=True);d=ARGS.matrix/name;group=network(d);group['lifecycle']=lifecycle(d);group['actual_configuration']=configuration(d,backend,family);GROUPS.append(group)
  for p,want in list(RAW.items()):check('final_original_stability',meta(pathlib.Path(p).read_bytes())==want,p)
  progress=ARGS.matrix/'results.json';now=progress.read_bytes();rows=json.loads(now);selected=report['provenance']['parent_progress_observation']['selected_health_results'];check('parent_progress',all(x in rows for x in selected),'original completed child rows retained while parent appends');report['provenance']['parent_progress_observation']['after']=meta(now)
  report['result']='PASS'
 except Exception as e:
  report['result']='FAIL';report['error']=str(e);report['traceback']=traceback.format_exc()
 report['groups']=GROUPS;report['checks_by_category']=dict(sorted(COUNTS.items()));report['diagnostic_check_count']=sum(COUNTS.values());report['counter_semantics']='Diagnostic predicates include per-byte TCP retransmission equality. They are not test executions/USB cases/independent reviewers.'
 report['totals']={'health_groups':len(GROUPS),'fault_phases':sum(len(g['phases']) for g in GROUPS),'guest_exit0':len(GROUPS)*2,'pcap_records':sum(sum(g['pcap_packets'].values()) for g in GROUPS),'ingress_frames':sum(sum(g['ingress_packets'].values()) for g in GROUPS),'peer_received_forwarded_frames':sum(sum(g['forwarded_packets'].values()) for g in GROUPS),'dropped_frames':sum(g['drops'] for g in GROUPS),'tls_directions':sum(len(g['tls_directions']) for g in GROUPS),'source_originals':len(RAW)}
 (ARGS.output/'raw-sha256.json').write_text(json.dumps({'files':[{'path':p,**v} for p,v in sorted(RAW.items())]},indent=2)+'\n')
 (ARGS.output/'packet-crosschecks.jsonl').write_text(''.join(json.dumps(x,sort_keys=True)+'\n' for x in PACKET_AUDIT));report['script']=meta(pathlib.Path(__file__).read_bytes());report['detail_sha256']={p.name:meta(p.read_bytes()) for p in ARGS.output.iterdir() if p.is_file()};(ARGS.output/'report.json').write_text(json.dumps(report,indent=2)+'\n');(ARGS.output/'reviewer.py').write_bytes(pathlib.Path(__file__).read_bytes());print(json.dumps({'result':report['result'],'groups':len(GROUPS),'diagnostic_checks':report['diagnostic_check_count'],'error':report.get('error')}));return 0 if report['result']=='PASS' else 1
if __name__=='__main__':sys.exit(main())
