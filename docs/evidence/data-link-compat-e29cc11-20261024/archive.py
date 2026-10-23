from pathlib import Path
import hashlib,json
out=Path('/home/ubuntu/github/usbip-client/docs/evidence/data-link-compat-e29cc11-20261024');out.mkdir(exist_ok=False);records=[]
def copy(source,dest):
 source=Path(source);assert source.is_file() and not source.is_symlink(),source;raw=source.read_bytes();p=out/dest;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);assert p.read_bytes()==raw;records.append(dict(path=dest,original=str(source),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
for label,directory in [('runs','/tmp/rustusb-data-link-compat-e29cc11-20261024'),('independent','/tmp/rustusb-data-link-compat-independent-review-20261024')]:
 root=Path(directory)
 for p in sorted(root.rglob('*')):
  if p.is_file():copy(p,label+'/'+str(p.relative_to(root)))
copy('/tmp/run-rustusb-data-link-compat-matrix-20261024.py','matrix-runner.py');copy(__file__,'archive.py')
scope=dict(scope='Nine existing raw-v1 QEMU compatibility regressions for the new data-link product, with strict private agent disk2 verification. Separate from new v2 heartbeat blackhole, accepted-initializer, kernel-internal active-window and hardware acceptance.',commit='e29cc1163ff9ce3cd73833c15eb24611941bac34',clean=True,runs=9,guest_exits_zero=18,frozen_inputs=204,source_inputs_unchanged=True,cli_sha256='76546440e5f72ea4f1132d68de9a9b474a028052bbb453cdd85936e5dd7ea622',helper_sha256='b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5',independent_predicates=3879,supplemental_batch_predicates=244,decoded_guest_base64_artifacts=1030,notes=['Predicate counts are diagnostic checks, not USB cases.','USBFS startup gate still reports helper acceptance false; old pending SIGTERM scope remains unchanged.','Agent KILL observation is not a pending-URB proof.','Existing USBFS no-intermediate-grant-journal and signal original-sink limitations remain.','Raw-v1 health is explicitly unavailable; no RTT is inferred.'])
raw=(json.dumps(scope,indent=2)+'\n').encode();(out/'scope.json').write_bytes(raw);records.append(dict(path='scope.json',original='generated archive scope',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()));(out/'manifest.json').write_text(json.dumps(dict(schema_version=1,files=records),indent=2)+'\n');print('files',len(records)+1,'bytes',sum(x['bytes'] for x in records))
