"""Read-only independent pcap/fixture diagnosis; no harness verifier invocation."""
import base64, hashlib, json, re, struct
from pathlib import Path
ROOT=Path('/tmp/rustusb-data-link-final-93428ce-20261024/health-virtual-ipv4')
OUT=Path('/tmp/rustusb-data-link-virtual-network-first-diagnosis-20261024.json')
originals={};checks=[]
def raw(name):
 value=(ROOT/name).read_bytes(); originals[name]={'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest()};return value
def require(name,ok):
 checks.append({'name':name,'passed':bool(ok)})
 if not ok: raise RuntimeError(name)
def pcap(name):
 value=raw(name)
 order={b'\xd4\xc3\xb2\xa1':'<',b'\xa1\xb2\xc3\xd4':'>'}[value[:4]]
 major,minor,_,_,snaplen,link=struct.unpack(order+'HHIIII',value[4:24])
 require(name+' bounded Ethernet pcap header',(major,minor)==(2,4) and link==1 and 0<snaplen<=1048576)
 pos=24;result=[]
 while pos<len(value):
  sec,micro,caplen,wirelen=struct.unpack(order+'IIII',value[pos:pos+16]);pos+=16
  require(name+' complete packet',caplen==wirelen and 14<=caplen<=snaplen and pos+caplen<=len(value) and micro<1000000)
  frame=value[pos:pos+caplen];pos+=caplen
  fact={'timestamp_unix_micros':sec*1000000+micro,'bytes':caplen,'sha256':hashlib.sha256(frame).hexdigest(),'src_mac':frame[6:12].hex(':'),'dst_mac':frame[:6].hex(':'),'ethertype':hex(int.from_bytes(frame[12:14],'big'))}
  if frame[12:14]==b'\x08\x06':
   htype,ptype,hlen,plen,opcode=struct.unpack('!HHBBH',frame[14:22]);require('exact ARP IPv4 shape',(htype,ptype,hlen,plen)==(1,0x800,6,4))
   fact['arp']={'opcode':opcode,'sender_ip':'.'.join(map(str,frame[28:32])),'target_ip':'.'.join(map(str,frame[38:42]))}
  result.append(fact)
 return result
summary=json.loads(raw('summary.json'))
require('original first virtual overall FAIL retained',summary['result']=='FAIL' and summary['head']=='93428ce0d27ca159003fdd98e0b9e61db7bb9d40')
require('reported source guards true and actual two guest0 not a PASS',all(summary[k] is True for k in ('source_guard','binary_guard','head_guard','clean_guard')) and summary['guest_exit_codes']=={'server':0,'client':0})
require('actual IPv4 plaintext connect timeout',raw('client-artifacts/plaintext-4.log')==b'nc-openbsd: connect to 192.0.2.1 port 3240 (tcp) timed out: Operation now in progress\n')
require('Catalog/IMPORT not reached',not (ROOT/'client-artifacts/catalog-initial.json').exists() and not list((ROOT/'client-artifacts').glob('start-*.json')))
packets={role:pcap(role+'-external.pcap') for role in ('server','client')}
valve=[json.loads(line) for line in raw('valve.jsonl').splitlines()]
base=valve[0]['monotonic_ns'];observed=[row for row in valve if row['event']=='packet'];forwarded={row['packet_id']:row for row in valve if row['event']=='forward'}
require('no deliberately armed fault or drop',not any(r['event']=='arm' or r.get('action')=='drop' for r in valve))
require('all nine observed frames actually forwarded',len(observed)==len(forwarded)==9)
latencies=[]
for row in observed:
 fwd=forwarded[row['packet_id']]
 require('exact ingress-forward tuple',fwd['origin']==row['origin'] and fwd['destination']!=row['origin'])
 require('original ingress present in source capture',any(p['sha256']==row['sha256'] for p in packets[row['origin']]))
 latencies.append({'packet_id':row['packet_id'],'kind':row['packet']['kind'],'origin':row['origin'],'ingress_since_valve_start_ms':(row['monotonic_ns']-base)/1000000,'forward_delay_ms':(fwd['monotonic_ns']-row['monotonic_ns'])/1000000})
require('no TCP was even observed before failure',all(row['packet']['kind']!='tcp' for row in observed))
for role,values in packets.items():
 require(role+' capture contains only ARP/IPv6 boot traffic',all(p['ethertype'] in ('0x806','0x86dd') for p in values))
 require(role+' all actual ingress source frames captured',sum(p['src_mac']==('52:54:00:00:00:61' if role=='server' else '52:54:00:00:00:62') for p in values)==sum(r['origin']==role for r in observed))
controls=[json.loads(line) for line in raw('host-control.jsonl').splitlines()]
require('host only sent keys and START before failure',[r['mark'] for r in controls]==['keys','start'])
net=[]
for role in ('server','client'):
 network=raw(role+'-artifacts/network.txt').decode()
 require(role+' actual initial NO-CARRIER with expected static addresses','NO-CARRIER' in network and ('192.0.2.1/24' if role=='server' else '192.0.2.2/24') in network and ('fd42:7275:7374::1/64' if role=='server' else 'fd42:7275:7374::2/64') in network)
 dmesg=raw(role+'-artifacts/failure-dmesg.log').decode()
 match=re.search(r'^\[\s*([0-9.]+)\] e1000: eth0 NIC Link is Up ([^\n]+)',dmesg,re.M)
 require(role+' actual later carrier-up kernel event',match is not None)
 net.append({'role':role,'initial_state':'NO-CARRIER','kernel_link_up_uptime_seconds':float(match[1]),'kernel_line':match[0]})
 init=raw(role+'-generated-link-init.sh').decode()
 require(role+' generated init lacks carrier/operstate readiness barrier','/sys/class/net/eth0/carrier' not in init and '/sys/class/net/eth0/operstate' not in init)
 source=raw('source/scripts/qemu_data_link_health/run.py').decode()
 require('actual virtual drive continuously polls same valve','while time.monotonic() < deadline:\n        private.valve.poll(0.01)' in source and 'while ftdi is not None and select.select([ftdi], [], [], 0)[0]:' in source)
selected={name:line for name,line in [(f'generated:{i}',line) for i,line in enumerate(init.splitlines(),1)] if any(key in line for key in ('ip link set eth0 up','ip addr show dev eth0','nc-openbsd "$family"','"-$family" -z'))}
report={'result':'READ_ONLY_NETWORK_DIAGNOSIS_ORIGINAL_FAIL','scope':'Author of harness; independent stdlib raw pcap and source diagnosis, no runner verifier/packet auditor/Cargo/QEMU or source edit.',
 'finding':'The new guest configures eth0/addressing without waiting for carrier. Both original snapshots are NO-CARRIER, later e1000 link-up is around guest uptime2.768s, and the early fixed two-second nc refusal test gets only a timeout with no TCP/SYN captured. The virtual path has no physical device enumeration delay. This supports a missing guest network readiness precondition, not an intentional valve drop or evidence of product TLS/USB failure.',
 'pump':'The recorded packet receive-to-forward latency for the first seven frames is below0.5ms. All nine ingress frames match source PCAPs and have completed socket-forward records. Receiving PCAPs are incomplete during failure teardown, so this is not a full receiving-side packet audit; final two delays (~50ms) are collector pacing after first failure. Actual drive calls valve.poll every iteration independently of the optional FTDI path.',
 'timing_boundary':'Host START is a send timestamp, not a logged guest nc start. Guest uptime and host monotonic clocks are distinct; raw evidence does not assign an exact syscall timestamp. No catalog/IMPORT/heartbeat fault occurred.',
 'host_control_relative_ms':[{'mark':r['mark'],'role':r['role'],'since_valve_start_ms':(r['monotonic_ns']-base)/1000000} for r in controls],
 'network':net,'forward_latencies':latencies,'raw_pcaps':packets,'source_lines':selected,'analyzer_history':'First diagnostic parser incorrectly required all receiving PCAP records even during failed teardown. Original parser and assertion failure are retained; current audit verifies source ingress and reports incomplete receiving evidence explicitly.',
 'recommended_minimal_change':'Only new harness generated common network prefix: preserve initial snapshot, bounded read-only carrier==1 and operstate==up readiness barrier in both guests before workload, record observed values/uptime and final network snapshot. Keep original timeout5/nc-w2/Connection refused checks and all valve/TLS/lifecycle bounds unchanged. No sleeps-only or TCP retries masking failure.',
 'check_count':len(checks),'checks':checks,'originals':originals}
require('all originals unchanged after read',all((ROOT/name).stat().st_size==meta['bytes'] and hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==meta['sha256'] for name,meta in originals.items()))
report['check_count']=len(checks)
OUT.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
print(json.dumps({'report':str(OUT),'sha256':hashlib.sha256(OUT.read_bytes()).hexdigest(),'checks':len(checks)}))
