from pathlib import Path
import base64,hashlib,ipaddress,json,re,struct,subprocess
root=Path('/home/ubuntu/github/usbip-client');out=Path('/tmp/rustusb-dual-stack-raw-review-67d0bb0-20261024');out.mkdir(exist_ok=False)
head='67d0bb002d910d75bcb77a51abb85ade2c5d51c5'
software=Path('/tmp/rustusb-dual-stack-software-first-20261024')
inputs={r['path']:{'bytes':r['bytes'],'sha256':r['sha256']} for r in json.loads((software/'inputs-before.json').read_text())}
binaries={Path(r['path']).name:r['sha256'] for r in json.loads((software/'result.json').read_text())['artifacts']}
directories=[Path('/tmp/rustusb-dual-stack-native-ipv4-multi-on-first-20261024')]
rows0=json.loads(Path('/tmp/rustusb-dual-stack-remaining-67d0bb0-20261024/progress.json').read_text());assert len(rows0)==13 and all(r['result']=='PASS' and r['exit_code']==0 for r in rows0)
directories += [Path(r['evidence']) for r in rows0]

def digest(p):
 b=p.read_bytes();return dict(bytes=len(b),sha256=hashlib.sha256(b).hexdigest())
def inventory():return {str(p):digest(p) for d in directories for p in sorted(d.rglob('*')) if p.is_file()}
def projection(directory,role,family):
 raw=directory/f'{role}-external.pcap';data=raw.read_bytes();saved=json.loads((directory/f'{role}-external-projection.json').read_text());derived=(directory/f'{role}-external-healthy-projection.pcap').read_bytes()
 assert data[:4] in [b'\xd4\xc3\xb2\xa1',b'\xa1\xb2\xc3\xd4'];endian='<' if data[:4]==b'\xd4\xc3\xb2\xa1' else '>'
 healthy_family=4 if family=='ipv4' else 6
 bad='fd42:7275:7374::99' if healthy_family==4 else '192.0.2.99';bad_client='fd42:7275:7374::2' if healthy_family==4 else '192.0.2.2'
 good_server='192.0.2.1' if healthy_family==4 else 'fd42:7275:7374::1';good_client='192.0.2.2' if healthy_family==4 else 'fd42:7275:7374::2'
 offset=24;packet=0;kept=bytearray(data[:24]);excluded=[];stale={};healthy={}
 while offset<len(data):
  start=offset;sec,usec,n,original=struct.unpack(endian+'IIII',data[offset:offset+16]);offset+=16;frame=data[offset:offset+n];offset+=n;packet+=1
  assert n==original and len(frame)==n and usec<1_000_000
  eth=int.from_bytes(frame[12:14],'big');ip=frame[14:];tcp=None
  if eth==0x0800 and len(ip)>=20 and ip[9]==6:
   version=4;src=str(ipaddress.ip_address(ip[12:16]));dst=str(ipaddress.ip_address(ip[16:20]));tcp=ip[(ip[0]&15)*4:int.from_bytes(ip[2:4],'big')]
  elif eth==0x86dd and len(ip)>=40 and ip[6]==6:
   version=6;src=str(ipaddress.ip_address(ip[8:24]));dst=str(ipaddress.ip_address(ip[24:40]));tcp=ip[40:40+int.from_bytes(ip[4:6],'big')]
  remove=False
  if tcp is not None:
   assert len(tcp)>=20;sport,dport,seq,ack=struct.unpack('!HHII',tcp[:12]);flags=tcp[13];th=(tcp[12]>>4)*4;stamp=sec*1_000_000+usec;key=(version,src,sport,dst,dport,seq)
   if src==bad or dst==bad:
    assert version!=healthy_family and src==bad_client and dst==bad and frame[:12]==bytes.fromhex('525400000099525400000062')
    assert dport==3241 and sport>0 and flags in [2,0xc2] and ack==0 and len(tcp)==th
    remove=True;stale.setdefault(key,stamp);excluded.append(dict(packet=packet,sha256=hashlib.sha256(data[start:offset]).hexdigest(),timestamp_micros=stamp,flow=list(key)))
   elif version==healthy_family and dport==3241 and flags&2 and not flags&16:
    assert src==good_client and dst==good_server and frame[:12]==bytes.fromhex('525400000061525400000062');healthy.setdefault(key,stamp)
  if not remove:kept.extend(data[start:offset])
 assert bytes(kept)==derived and excluded==saved['excluded_empty_syn_packets']
 assert hashlib.sha256(data).hexdigest()==saved['raw_sha256'] and hashlib.sha256(derived).hexdigest()==saved['derived_sha256']
 assert packet==saved['original_packets'] and sorted(stale.values())==saved['stale_first_syn_micros'] and sorted(healthy.values())==saved['healthy_first_syn_micros']
 if role=='client':assert len(stale)==len(healthy)>=7 and all(a<b and b-a<10_000_000 for a,b in zip(sorted(stale.values()),sorted(healthy.values())))
 return dict(role=role,original_packets=packet,exact_empty_syn_exclusions=len(excluded),unique_stale=len(stale),unique_healthy=len(healthy))

originals_before=inventory();rows=[];decoded=0
for d in directories:
 s=json.loads((d/'summary.json').read_text());assert s['result']=='PASS' and s['guest_exit_codes']=={'client':0,'server':0}
 assert s['head']==s['head_after']==head and s['git_status_before']==s['git_status_after']=='' and all(s[k] for k in ['source_guard','binary_guard','head_guard','clean_guard'])
 assert s['binary_sha256']==binaries and s['build_skipped'] is False
 missing=set(inputs)-set(s['source_inputs']);assert missing==({'rust-toolchain.toml','scripts/check.sh'} if 'default-' in d.name else set())
 for name,expected in inputs.items():
  if name not in missing:assert s['source_inputs'][name]==expected
 for name,meta in s['source_inputs'].items():
  original=subprocess.check_output(['git','show',head+':'+name],cwd=root);assert original==(d/'source'/name).read_bytes();assert dict(bytes=len(original),sha256=hashlib.sha256(original).hexdigest())==meta
 for name,meta in s['artifact_manifest'].items():assert digest(d/name)==meta
 for role in ['server','client']:
  text=(d/f'{role}-evidence.log').read_text();blocks=re.findall(r'^SECURE_ARTIFACT_BEGIN (\S+) ([0-9]+)\n(.*?)^SECURE_ARTIFACT_END \1\s*$',text,re.M|re.S)
  assert len(blocks)==text.count('SECURE_ARTIFACT_BEGIN ')==len(s['guest_artifacts'][role]);names=set()
  for name,size,encoded in blocks:
   assert name not in names;names.add(name);raw=base64.b64decode(''.join(encoded.split()),validate=True);assert len(raw)==int(size) and raw==(d/f'{role}-artifacts'/name).read_bytes();assert digest(d/f'{role}-artifacts'/name)==s['guest_artifacts'][role][name];decoded+=1
 row=dict(directory=str(d),backend=s['backend'],family=s['family'],inputs=len(s['source_inputs']),software_input_subset=len(inputs)-len(missing),software_inputs_not_selected_by_original_runner=sorted(missing),guest_exits=s['guest_exit_codes'])
 if 'default-' not in d.name and 'v2-diagnostic' not in d.name:
  p=d/'client-artifacts';probe=(p/'dual-resolver.txt').read_text().splitlines();build=json.loads((d/'resolver-probe-build.json').read_text());assert probe[0].startswith('RESOLVER_ACTUAL_EXE '+build['executable_sha256']+' ')
  assert 'RESOLVER_ACTUAL_UID 2000 2000 2000 2000' in probe and all('RESOLVER_ENV '+key+' absent' in probe for key in ['RESOLV_MULTI','RESOLV_HOST_CONF'])
  bad='[fd42:7275:7374::99]:3241' if s['family']=='ipv4' else '192.0.2.99:3241';good='192.0.2.1:3241' if s['family']=='ipv4' else '[fd42:7275:7374::1]:3241'
  assert [x.split(' ',2)[2] for x in probe if x.startswith('RESOLVER_CANDIDATE ')]==[bad,good]
  assert (p/'dual-host-conf.txt').read_text()=='multi on\n'
  row['projection_checks']=[projection(d,role,s['family']) for role in ['server','client']]
  row['protocol']=s['facts']['dual_stack']['protocol']
 rows.append(row)
assert originals_before==inventory()
(out/'originals-before.json').write_text(json.dumps(originals_before,indent=2)+'\n');(out/'originals-after.json').write_text(json.dumps(inventory(),indent=2)+'\n')
result=dict(result='PASS',scope='Same-author read-only byte/source/base64/probe/projection method, no original runner/parser/verifier import. Not another execution, independent security audit, per-process packet attribution or independent TLS classifier.',head=head,original_files_unchanged=len(originals_before),base64_artifacts_redecoded=decoded,software_inputs_mapped=198,binary_sha256=binaries,runs=rows)
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print({k:v for k,v in result.items() if k not in ['runs','binary_sha256']})
