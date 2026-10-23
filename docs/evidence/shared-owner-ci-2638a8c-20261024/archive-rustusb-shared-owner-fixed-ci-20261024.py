import pathlib,json,hashlib,shutil
root=pathlib.Path('/home/ubuntu/github/usbip-client');tmp=pathlib.Path('/tmp');dest=root/'docs/evidence/shared-owner-ci-2638a8c-20261024';dest.mkdir(parents=True,exist_ok=False);entries=[]
def archive(p,rel):
 p=pathlib.Path(p);q=dest/rel;q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q);raw=q.read_bytes();assert raw==p.read_bytes();entries.append({'path':rel,'original':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
for p in sorted(tmp.glob('rustusb-shared-owner-ci-testfix-*20261024.connector.json')):archive(p,'official/'+p.name)
for name in ['review-rustusb-shared-owner-fixed-ci-20261024.py','rustusb-shared-owner-ci-testfix-independent-summary-20261024.json','archive-rustusb-shared-owner-fixed-ci-20261024.py']:archive(tmp/name,name)
for p in sorted(tmp.glob('rustusb-shared-owner-fixed-ci-independent-review-20261024.*')):archive(p,'independent/'+p.name)
summary=json.loads((tmp/'rustusb-shared-owner-ci-testfix-independent-summary-20261024.json').read_text());assert summary['result']=='PASS' and len(summary['jobs'])==4
scope={'run_id':39999926362,'head':'2638a8c4700d79233d283de1a168f574be5f8ee4','result':'four actual native jobs success','tests_per_job':'530 pass /39 result /0 failed ignored; five secure mTLS fixture cases; original two backpressure and four signals','observed_kernel_names':'Ubuntu24 6.17: three anon_pipe_write observations per architecture; Ubuntu22 6.8: three pipe_write per architecture','source_inputs':152,'scope':'primary connector-decoded official logs and API status; not a separate ZIP-byte comparison, not raw missing runner temporary files; old failed39997678019 scope retained separately, missing old values not reconstructed; no QEMU/hardware/performance claim'}
p=tmp/'rustusb-shared-owner-ci-fixed-scope-20261024.json';p.write_text(json.dumps(scope,indent=2)+'\n');archive(p,'scope.json')
(dest/'manifest.json').write_text(json.dumps({'schema_version':1,'entries':entries},indent=2)+'\n');print(json.dumps({'files':len(entries),'bytes':sum(i['bytes'] for i in entries),'manifest':str(dest/'manifest.json')}))
