from pathlib import Path
import json,hashlib
out=Path('/home/ubuntu/github/usbip-client/docs/evidence/data-link-matrix-93428ce-20261024');out.mkdir(exist_ok=False);records=[]
def copy(source,dest):
 source=Path(source);assert source.is_file() and not source.is_symlink(),source;b=source.read_bytes();p=out/dest;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b);assert p.read_bytes()==b;records.append(dict(path=dest,original=str(source),bytes=len(b),sha256=hashlib.sha256(b).hexdigest()))
root=Path('/tmp/rustusb-data-link-final-93428ce-20261024')
for p in sorted(root.rglob('*')):
 if p.is_file():copy(p,'runs/'+str(p.relative_to(root)))
for name in ['run-rustusb-data-link-final-matrix-20261024.py','rustusb-data-link-backend-dispatch-handoff-20261024.json','rustusb-data-link-virtual-network-first-diagnosis-20261024.json','rustusb-data-link-virtual-network-first-diagnosis-20261024.py','rustusb-data-link-readiness-product-guard-20261024.json']:
 copy(Path('/tmp')/name,'review/'+name)
copy(__file__,'archive.py')
scope=dict(result='FAIL',commit='93428ce0d27ca159003fdd98e0b9e61db7bb9d40',clean=True,passed=['health-native-ipv4','health-native-ipv6','health-usbfs-ipv4','health-usbfs-ipv6'],failed='health-virtual-ipv4',unrun=['health-virtual-ipv6','six existing secure runs','two existing agent runs','one existing SIGTERM run'],new_fault_phases_passed=12,guest_exits_zero=10,notes=['Four complete new health runs pass; matrix as a whole fails at the fifth run and stops.','Virtual preparation fails at plaintext3240 nc-w2, before Catalog/IMPORT/fault arming.','Nine complete socket-forward records are not proof all nine frames arrived at peer PCAP.','No TCP/SYN appears; initial guest snapshots are NO-CARRIER and later LinkUp is observed.','HostSTART is send time, not the exact guest nc syscall start or deadline.','Carrier readiness barrier is a fixture fix; nc/probe policies are not widened.','No pending-URB/hardware/whole Phase3 acceptance is inferred.'])
b=(json.dumps(scope,indent=2)+'\n').encode();(out/'scope.json').write_bytes(b);records.append(dict(path='scope.json',original='generated archive scope',bytes=len(b),sha256=hashlib.sha256(b).hexdigest()));(out/'manifest.json').write_text(json.dumps(dict(schema_version=1,files=records),indent=2)+'\n');print('files',len(records)+1,'bytes',sum(x['bytes'] for x in records))
