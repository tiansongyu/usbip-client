import pathlib,json,hashlib,shutil,datetime,subprocess
root=pathlib.Path('/home/ubuntu/github/usbip-client');tmp=pathlib.Path('/tmp');dest=root/'docs/evidence/shared-owner-final-software-20261024';dest.mkdir(parents=True,exist_ok=False);entries=[]
def archive(p,rel):
 p=pathlib.Path(p);q=dest/rel;q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q);raw=q.read_bytes();assert raw==p.read_bytes();entries.append({'path':rel,'original':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
run=tmp/'rustusb-shared-owner-software-third-20261024';result=json.loads((run/'result.json').read_text());assert result['exit_code']==0 and result['inputs_unchanged']
for p in sorted(run.iterdir()):archive(p,'gate/'+p.name)
inputs=json.loads((run/'inputs-after.json').read_text());old=json.loads((root/'docs/evidence/shared-owner-software-20261024/second/inputs-after.json').read_text());delta=[a['path'] for a,b in zip(inputs,old) if a!=b];assert len(inputs)==len(old)==152 and delta==['crates/rustusb-service/src/jobs/client.rs'],delta
for i in inputs:assert hashlib.sha256((root/i['path']).read_bytes()).hexdigest()==i['sha256']
start=datetime.datetime.fromisoformat(json.loads((run/'start.json').read_text())['utc']).timestamp();runtime=[p for p in tmp.glob('rustusb-secure-foreground-tests-*') if p.is_dir() and p.stat().st_mtime>=start];assert len(runtime)==1
r=json.loads((runtime[0]/'summary.json').read_text());assert r['result']=='PASS' and len(r['cases'])==5
for p in sorted(runtime[0].rglob('*')):
 if p.is_file():archive(p,'secure-foreground-runtime/'+str(p.relative_to(runtime[0])))
local=tmp/'rustusb-shared-owner-final-local-signals-20261024';l=json.loads((local/'summary.json').read_text());assert l['result']=='PASS' and l['source_unchanged'] and l['binary_unchanged']
for p in sorted(local.rglob('*')):
 if p.is_file():archive(p,'local-signals/'+str(p.relative_to(local)))
b=json.loads((tmp/'rustusb-shared-owner-final-local-backpressure-20261024.log').read_text());assert b['result']=='PASS' and len(b['checks'])==2
for p in sorted(tmp.glob('rustusb-shared-owner-final-*20261024.log')):archive(p,'integration/'+p.name)
archive(tmp/'rustusb-shared-owner-final-native-only-tree-20261024.txt','integration/native-only-tree.txt')
for p in sorted(tmp.glob('rustusb-secure-foreground-runtime-independent-review-20261024.*')):archive(p,'independent-original-local/'+p.name)
for p in [tmp/'archive-rustusb-shared-owner-final-software-20261024.py',tmp/'archive-rustusb-shared-owner-compat-failure-20261024.py']:archive(p,p.name)
scope={'gate':result,'source_delta_from_second_pass':delta,'scope':'only foreground contender denial text restored; original managed message and original QEMU assertion retained','runtime_scope':r['scope'],'runtime_original':str(runtime[0]),'local_four_signals':l['result'],'local_two_backpressure':b['result'],'previous_independent_local_review':'reviewer parsed original second-gate raw runtime; retained source revision, not relabelled as third run','qemu':'original clean4d first run FAIL archived separately; fixed matrix not yet executed','remaining':'hardware; initializer gate/kernel hardblock; arbitrary WAL stall bounded cleanup; full Phase3 and later stages'}
p=tmp/'rustusb-shared-owner-final-software-scope-20261024.json';p.write_text(json.dumps(scope,indent=2)+'\n');archive(p,'scope.json')
(dest/'manifest.json').write_text(json.dumps({'schema_version':1,'entries':entries},indent=2)+'\n');print(json.dumps({'files':len(entries),'bytes':sum(i['bytes'] for i in entries),'runtime':str(runtime[0]),'manifest':str(dest/'manifest.json')}))
