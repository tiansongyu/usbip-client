#!/usr/bin/env python3
"""Independent-method reparse of frozen evidence; author also wrote the harness.
No imports of project runners/auditors and no USB, network, Cargo or QEMU actions.
"""
import argparse,base64,collections,hashlib,ipaddress,json,pathlib,re,stat,struct,subprocess,sys,traceback
HEAD='531e0c32a4b0e7252ca771ab4f10042c71c8c195'
BIN={'rustusb':'bb6fb276c0cbfbb73c2cdb8af0f73d87045ac7235b4943cf096c0643d570814a','rustusb-helper':'b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5'}
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
 for marker in (b'RUSTUSB-TX-',b'RUSTUSB-RX-',b'virtual:hid',b'native:usb-',b'usbfs:usb-'):
  check('wire_no_fixture_plaintext',marker not in wire,'no fixed fixture plaintext')
 return {'wire_bytes':len(wire),'wire_sha256':sha(wire),'records':len(records),'partial_tail_bytes':tail,'hello':hellos,'syn':initial}

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
   check('valve_decision',e['action']==('drop' if drop else 'queue') and e['rule_id']==(active['rule_id'] if drop else None),'exact tuple+direction decision')
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
 keys=js(d/'keys.json');phasefacts=[];sibling_identity=None;targetflows={tuple(p['arm']['flow']):p['arm']['index'] for p in phases.values()}
 for phase in ('c2s','s2c','both'):
  p=phases[phase];arm=p['arm'];disarm=p['disarm'];flow=tuple(arm['flow']);bindings={};states={};hs={}
  for r in ROLES:
   path=d/'bindings'/f'{phase}-{r}-health.json';health=js(path);check('health_raw',sha(read(path))==arm['binding'][r+'_health_sha256'] and read(path)==read(d/(r+'-artifacts')/f'health-{phase}-{r}-pre.json'),'prearm raw binding');check('health_schema',health['schema_version']==1 and health['journal_healthy'] and health['response']['result']=='session_health' and health['response']['next_after'] is None,'actual health IPC');states[r]=js(d/(r+'-artifacts')/f'agent-pre-{phase}.json');check('health_state',states[r]['schema_version']==2,'actual private state2');h=next(x for x in health['response']['sessions'] if x['session_id']==arm['binding'][r+'_session']);ss=next(x for x in states[r]['sessions'] if x['id']==h['session_id']);link=h['link'];expectedrole='import' if r=='client' else 'incoming'
   check('health_identity',h['instance_id']==ss['instance_id']==arm['binding'][r+'_instance'] and h['role']==ss['role']==expectedrole and ss['device_key']==keys[0] and ss['state']=='Connected' and not ss['terminal'],'exact target session/instance/role/key');check('health_identity',h['availability']=='live' and link['identity']['connection']==arm['binding'][r+'_connection'] and link['protocol']=='framed_v2' and link['phase']=='open' and link['end'] is None and link['last_reply_sequence']>=1 and link['last_round_trip_micros'] is not None,'live matched transport reply');check('health_identity',flow_identity(link)==(flow if r=='client' else reverse(flow)),'health actual TCP tuple matches selected raw flow');hs[r]=health;bindings[r]=h
   ended=js(d/(r+'-artifacts')/f'health-{phase}-{r}-terminal.json');ht=next(x for x in ended['response']['sessions'] if x['session_id']==h['session_id']);check('health_terminal',ht['instance_id']==h['instance_id'] and ht['link']['identity']==link['identity'] and ht['availability']=='owner_ended' and ht['link']['phase']=='ended','same ended connection snapshot')
   end=ht['link'];check('health_terminal',end['end'] in ('probe_timeout','peer_eof','io') and end['elapsed_millis']>=link['elapsed_millis'],'terminal transport fact');bindings[r+'_end']=end
  check('health_timeout',any(bindings[r+'_end']['end']=='probe_timeout' for r in ROLES),'actual same-data timeout observed')
  for r in ROLES:
   x=bindings[r+'_end']
   if x['end']=='probe_timeout':check('health_timeout',x['probe']=='timed_out' and x['last_probe_sequence']>x['last_reply_sequence'] and x['elapsed_millis']-x['last_probe_enqueued_millis']>=3000,'local monotonic timeout age')
  check('valve_epoch',arm['syn_sequences']==[next(x['facts']['sequence'] for x in streams[f] if x['facts']['flags']&2) for f in (flow,reverse(flow))],'actual two SYN epochs pinned')
  for f in (flow,reverse(flow)):check('valve_epoch',any(x['payload'] and x['entry']['index']<arm['index'] for x in streams[f]),'real prearm bidirectional TLS data')
  sibling=next(x for x in states['server']['sessions'] if x['role']=='incoming' and x['device_key']==keys[1] and not x['terminal']);h=next(x for x in hs['server']['response']['sessions'] if x['session_id']==sibling['id']);sf=reverse(flow_identity(h['link']));identity=(sibling['id'],sibling['instance_id'],h['link']['identity'])
  if sibling_identity is None:sibling_identity=identity
  check('sibling',identity==sibling_identity and sf!=flow and h['availability']=='live','original sibling connection retained');terminal=js(d/'server-artifacts'/f'health-{phase}-server-terminal.json');ht=next(x for x in terminal['response']['sessions'] if x['session_id']==sibling['id']);check('sibling',ht['availability']=='live' and ht['link']['identity']==h['link']['identity'] and ht['link']['phase']=='open','same sibling still live')
  for f in (sf,reverse(sf)):
   rows=streams[f];check('sibling',all(x['entry']['action']=='queue' for x in rows),'sibling never dropped');check('sibling',any(x['payload'] and arm['index']<x['entry']['index']<disarm['index'] for x in rows),'sibling actual TLS payload continues during fault')
  catalog=js(d/'client-artifacts'/f'catalog-during-{phase}.json');check('catalog',catalog['schema_version']==1 and catalog['journal_healthy'] and catalog['response']['result']=='catalog','real new Catalog RPC succeeds');fresh=p['new_syns']-{flow,sf};check('catalog',len(fresh)==1,'exactly one new non-target/non-sibling TLS flow during phase');cf=next(iter(fresh))
  for f in (cf,reverse(cf)):
   check('catalog',f in streams and any(x['payload'] and arm['index']<x['entry']['index']<disarm['index'] for x in streams[f]) and all(x['entry']['action']=='queue' for x in streams[f]),'actual correlated new flow both directions never dropped')
  expected={'client':phase in ('c2s','both'),'server':phase in ('s2c','both')};check('valve_drops',all((p['drops'][r]>0)==expected[r] for r in ROLES),'strict directional actual drops')
  phasefacts.append({'phase':phase,'target':flow,'target_sessions':{r:bindings[r]['session_id'] for r in ROLES},'target_connections':{r:bindings[r]['link']['identity']['connection'] for r in ROLES},'target_ends':{r:bindings[r+'_end']['end'] for r in ROLES},'drop_packets':dict(p['drops']),'arm_host_ns':arm['monotonic_ns'],'disarm_host_ns':disarm['monotonic_ns'],'sibling':sf,'catalog_correlated_flow':cf})
 tls=[]
 for f,rows in sorted(streams.items()):
  if 3241 not in (f[2],f[4]):continue
  cf=f if f[4]==3241 else reverse(f);allow=cf in targetflows and rows[-1]['entry']['index']>targetflows[cf];fact=tls_stream(rows,allow);client=f[4]==3241;check('tls_direction',all(('client_offer' in x)==client for x in fact['hello']),'correct-direction hello');tls.append({'flow':f,**fact})
 group.update({'pcap_packets':{r:len(cap[r]) for r in ROLES},'ingress_packets':dict(ptr),'forwarded_packets':{r:len(forwarded[r]) for r in ROLES},'drops':sum(sum(p['drops'].values()) for p in phases.values()),'socket_endpoint_fds':fds,'readiness':readyfacts,'start_host_ns':start['monotonic_ns'],'negative_probes':refused,'phases':phasefacts,'tls_directions':tls,'raw_base64_artifacts':{r:len(rawblocks[r]) for r in ROLES}});return group

def provenance(base):
 before=js(base/'inputs-before.json');after=js(base/'inputs-after.json');check('global_source',len(before)==213 and before==after,'213 exact before/after');paths=[x['path'] for x in before];check('global_source',len(set(paths))==213,'unique global inputs')
 for e in before:
  b=blob(e['path']);check('global_source',meta(b)=={k:e[k] for k in ('bytes','sha256')},'Gitblob531 '+e['path']);check('global_current',read(ARGS.repo/e['path'])==b,'current frozen input '+e['path'])
 start=js(base/'start.json');result=js(base/'result.json');check('matrix',start['head']==HEAD and start['clean'] and result['result']=='PASS' and result['head']==HEAD and result['input_count']==213 and result['inputs_unchanged'] and len(result['runs'])==15,'matrix exact source and completed result')
 summary=[]
 for rr in result['runs']:
  d=base/rr['case'];s=js(d/'summary.json');check('matrix',rr['exit_code']==0 and rr['unchanged'] and rr['summary_result']=='PASS' and s['result']=='PASS' and s['guest_exit_codes']=={'server':0,'client':0},rr['case']+' actual exits');check('binary',s['binary_sha256']==BIN,'exact final actual build hashes')
  if rr['case']=='sigterm':
   check('legacy_source',s['git_commit']==s['git_commit_after']==HEAD and not s['working_tree_dirty'] and not s['working_tree_dirty_after'] and s['source_guard_passed'] and s['binary_guard_passed'],'legacy guards');check('binary',s['guest_binary_sha256']==BIN and s['binary_after_sha256']==BIN,'legacy guest/after binaries')
   names=[n for n in paths if n in ('Cargo.toml','Cargo.lock') or (n.startswith('crates/') and (n.endswith('.rs') or n.endswith('/Cargo.toml')))]+['scripts/qemu/guest-init.sh','scripts/qemu/run-native-qemu.py'];h=hashlib.sha256()
   for n in sorted(names,key=lambda x:pathlib.PurePosixPath(x).parts):b=blob(n);nb=n.encode();h.update(len(nb).to_bytes(8,'big'));h.update(nb);h.update(len(b).to_bytes(8,'big'));h.update(b)
   h=hashlib.sha256(h.digest())
   for name in ['run-physical-qemu.py','guest-physical-init.sh','physical-wire-probe.rs']:
    b=blob('scripts/qemu/'+name);h.update(name.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
   for n in sorted((n for n in paths if n.startswith('vendor/') and (n.endswith('.rs') or n.endswith('/Cargo.toml') or n.endswith('/Cargo.lock'))),key=lambda x:pathlib.PurePosixPath(x).parts):b=blob(n);h.update(n.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
   h=hashlib.sha256(h.digest())
   for name in ['run-sigterm-qemu.py','guest-sigterm-init.sh']:
    b=blob('scripts/qemu/'+name);h.update(name.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
   check('legacy_source',h.hexdigest()==s['source_fingerprint_sha256']==s['source_fingerprint_after_sha256'],'independently recomputed layered legacy fingerprint')
   for n,digest in s['script_sha256'].items():check('legacy_source',sha(read(d/'source'/n))==digest==sha(blob('scripts/qemu/'+n)),'legacy source copy '+n)
   summary.append({'case':rr['case'],'source_style':'legacy layered fingerprint + 7 script copies; global 213 guards','source_count':len(s['script_sha256']),'guest_exits':s['guest_exit_codes']});continue
  check('run_source',s['head']==s['head_after']==HEAD and s['git_status_before']==s['git_status_after']=='' and all(s[k] is True for k in ('source_guard','binary_guard','head_guard','clean_guard')) and bool(s['completed_utc']),'clean actual source guards')
  for name,expected in s['source_inputs'].items():b=read(d/'source'/name);check('run_source',meta(b)==expected and b==blob(name),'each run source/Gitblob '+name)
  for name,expected in s['artifact_manifest'].items():check('archived_artifact',meta(read(d/name))==expected,'archived bytes '+name)
  for name in s['actual_broker_executable_receipts']:check('binary',read(d/name).split()[0].decode()==BIN['rustusb'],'executed broker proc exe')
  for name in s['actual_helper_executable_receipts']:check('binary',read(d/name).split()[0].decode()==BIN['rustusb-helper'],'executed root helper proc exe')
  for role,files in s['payload_sha256'].items():
   for name,digest in BIN.items():check('binary',files['usr/bin/'+name]==digest,'role payload exact ELF')
  summary.append({'case':rr['case'],'source_style':'per-file source copies and actual proc executable receipts','source_count':len(s['source_inputs']),'guest_exits':s['guest_exit_codes'],'artifacts':len(s['artifact_manifest'])})
 return {'global_inputs':213,'runs':summary}

def main():
 global ARGS
 ap=argparse.ArgumentParser();ap.add_argument('--matrix',type=pathlib.Path,default=pathlib.Path('/tmp/rustusb-data-link-ready-531e0c3-20261024'));ap.add_argument('--repo',type=pathlib.Path,default=pathlib.Path('/home/ubuntu/github/usbip-client'));ap.add_argument('--output',type=pathlib.Path,required=True);ARGS=ap.parse_args();ARGS.output.mkdir(parents=True,exist_ok=False)
 report={'schema_version':1,'source_head':HEAD,'method':'New standalone standard-library PCAP/TCP/TLS and ordered ledger reparse; no project verifier/auditor imported or called.','reviewer_independence':'Same agent authored the harness/auditor. This is an independent method, not an independent author/implementation claim.','scope':['Six health groups: raw network facts and roles only.','All 15 groups: source and ELF provenance and actual recorded guest exits; old nine are compatibility runs, not new heartbeat cases.'],'limits':['No decrypted server ALPN or h2 PING contents inferred from ciphertext. ClientHello offer and plaintext ServerHello TLS1.3 selection are parsed.','Health matched replies and timeout are application observations bound to actual TCP tuple; they do not prove USB completion.','Host ACK/START timestamps are send completion/log observations, not guest receive or nc syscall timestamps. Guest ordering and actual TCP refusal are separate evidence.','Catalog ciphertext is not decoded: unique new TLS flow is correlated with successful phase RPC and executed guest sequence.','Socket FD/dev/ino is valve-owned endpoint retention, not proof of broker kernel FD ownership. Broker UID/executable and reported real TCP identity are separately checked.','Network results do not replace Root/helper cleanup or USB lifecycle acceptance. No hardware or ARM runtime result is claimed.','Historical e152 Catalog FAIL and 934 virtual readiness FAIL remain unchanged; final result is a new clean run.'],'historical_failures':['/tmp/rustusb-data-link-native-ipv4-e1529ea-20261024','/tmp/rustusb-data-link-final-93428ce-20261024/health-virtual-ipv4']}
 try:
  report['provenance']=provenance(ARGS.matrix)
  for backend in ('native','usbfs','virtual'):
   for family in ('ipv4','ipv6'):
    name='health-'+backend+'-'+family;print('reviewing '+name,flush=True);GROUPS.append(network(ARGS.matrix/name))
  for p,expected in list(RAW.items()):check('final_original_stability',meta(pathlib.Path(p).read_bytes())==expected,p)
  report['result']='PASS'
 except Exception as e:
  report['result']='FAIL';report['error']=str(e);report['traceback']=traceback.format_exc()
 report['groups']=GROUPS;report['checks_by_category']=dict(sorted(COUNTS.items()));report['diagnostic_check_count']=sum(COUNTS.values());report['new_usb_case_count']=0;report['current_git_status_note']=subprocess.check_output(['git','-C',str(ARGS.repo),'status','--short']).decode();report['script']=meta(pathlib.Path(__file__).read_bytes());report['raw_originals_count']=len(RAW)
 (ARGS.output/'raw-originals-manifest.json').write_text(json.dumps({'schema_version':1,'files':[{'path':p,**v} for p,v in sorted(RAW.items())]},indent=2)+'\n');(ARGS.output/'packet-crosschecks.jsonl').write_text(''.join(json.dumps(x,sort_keys=True)+'\n' for x in PACKET_AUDIT));report['outputs']={p.name:meta(p.read_bytes()) for p in ARGS.output.iterdir() if p.is_file()};(ARGS.output/'report.json').write_text(json.dumps(report,indent=2)+'\n');(ARGS.output/'reviewer.py').write_bytes(pathlib.Path(__file__).read_bytes());print(json.dumps({'result':report['result'],'checks':report['diagnostic_check_count'],'groups':len(GROUPS),'originals':len(RAW),'error':report.get('error')}));return 0 if report['result']=='PASS' else 1
if __name__=='__main__':sys.exit(main())
