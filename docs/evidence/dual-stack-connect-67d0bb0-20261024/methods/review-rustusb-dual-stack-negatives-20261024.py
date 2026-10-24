from pathlib import Path
import hashlib,json,re,struct,subprocess
root=Path('/home/ubuntu/github/usbip-client');out=Path('/tmp/rustusb-dual-stack-negative-review-20261024');out.mkdir(exist_ok=False)
names=['old-native-ipv4-first-5b26bba','old-native-ipv4-second','native-ipv4-first','native-ipv4-framed-first','native-ipv4-probe-first','old-valid-counterfactual-0d116f0']
rows=[]
for name in names:
 p=Path('/tmp/rustusb-dual-stack-'+name+'-20261024');s=json.loads((p/'summary.json').read_text());assert s['result']=='FAIL' and s['guest_exit_codes']=={'server':0,'client':0} and all(s[x] for x in ['source_guard','binary_guard','head_guard','clean_guard'])
 lines=(p/'client-evidence.log').read_text().splitlines();failure=[x for x in lines if 'RUSTUSB_SECURE_RESULT: FAIL' in x or 'operation failed' in x or "ip: invalid argument" in x]
 row=dict(directory=str(p),actual_head=s['head'],result=s['result'],guest_exits=s['guest_exit_codes'],guards=True,client_failure_lines=failure,scope='Original host FAIL retained. Guest exits do not reclassify it.')
 probe=p/'client-artifacts/dual-resolver.txt'
 if probe.exists():row['actual_probe_lines']=probe.read_text().splitlines()
 rows.append(row)
old=Path(rows[-1]['directory']);s=json.loads((old/'summary.json').read_text())
probe=(old/'client-artifacts/dual-resolver.txt').read_text().splitlines()
assert 'RESOLVER_ACTUAL_UID 2000 2000 2000 2000' in probe
assert [x for x in probe if x.startswith('RESOLVER_CANDIDATE ')]==['RESOLVER_CANDIDATE 0 [fd42:7275:7374::99]:3241','RESOLVER_CANDIDATE 1 192.0.2.1:3241']
build=json.loads((old/'resolver-probe-build.json').read_text());assert probe[0].startswith('RESOLVER_ACTUAL_EXE '+build['executable_sha256']+' ')
assert (old/'client-artifacts/dual-host-conf.txt').read_text()=='multi on\n'
# Separate direct raw-PCAP observation; imports no original parser/verifier.
data=(old/'client-external.pcap').read_bytes();assert data[:4]==b'\xd4\xc3\xb2\xa1';offset=24;stale=[];healthy=[]
while offset<len(data):
 sec,usec,n,original=struct.unpack('<IIII',data[offset:offset+16]);offset+=16;frame=data[offset:offset+n];offset+=n;assert n==original and len(frame)==n
 kind=int.from_bytes(frame[12:14],'big');ip=frame[14:]
 if kind==0x86dd and len(ip)>=60 and ip[6]==6:
  tcp=ip[40:40+int.from_bytes(ip[4:6],'big')]
  if int.from_bytes(tcp[2:4],'big')==3241 and tcp[13]&2 and not tcp[13]&16:
   assert frame[:6]==bytes.fromhex('525400000099');stale.append((sec,usec))
 elif kind==0x0800 and len(ip)>=40 and ip[9]==6:
  tcp=ip[(ip[0]&15)*4:int.from_bytes(ip[2:4],'big')]
  if int.from_bytes(tcp[2:4],'big')==3241 and tcp[13]&2 and not tcp[13]&16:healthy.append((sec,usec))
assert stale and not healthy
rows[-1]['valid_counterfactual']=dict(actual_resolver_candidates=2,actual_broker_uid=2000,stale_syn_packets=len(stale),healthy_syn_connections=0,interpretation='Corrected fixture reproduces sequential first-address timeout; no installed USB PASS or new software execution.')
old_inputs=json.loads(Path('/tmp/rustusb-authentication-admission-software-first-47fcb75-20261024/inputs-before.json').read_text())
for r in old_inputs:
 b=(old/'source'/r['path']).read_bytes();assert len(b)==r['bytes'] and hashlib.sha256(b).hexdigest()==r['sha256']
rows[-1]['selected_product_inputs_identical_to_clean47f']=len(old_inputs)
prepare=Path('/tmp/rustusb-dual-stack-old-product-preparation-20261024')
r=subprocess.run(['git','bundle','verify',str(prepare/'old-product.bundle')],cwd=root,capture_output=True);assert r.returncode==0
(out/'bundle-verify.stdout').write_bytes(r.stdout);(out/'bundle-verify.stderr').write_bytes(r.stderr)
result=dict(result='PASS_observation_review_not_QEMU_PASS',preserved_host_failures=6,runs=rows,scope='Same-author read-only scalar/source/probe/PCAP/bundle observations. First five runs do not establish a valid two-candidate counterfactual. Only the final corrected old-source run does; every original FAIL remains FAIL.')
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print('Retained6 host FAILs; validold two-candidate counterfactual:',rows[-1]['valid_counterfactual'])
