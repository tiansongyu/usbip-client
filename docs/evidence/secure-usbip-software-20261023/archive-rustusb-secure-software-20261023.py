from pathlib import Path
import hashlib,json,shutil
root=Path.cwd(); dest=root/'docs/evidence/secure-usbip-software-20261023';dest.mkdir(exist_ok=False)
prefixes=['rustusb-secure-core-protocol-first-','rustusb-secure-config-first-','rustusb-secure-config-author-handoff-','rustusb-secure-config-interface-review-','rustusb-secure-engine-author-handoff-','rustusb-secure-engine-config-first-','rustusb-secure-engine-fixed-','rustusb-secure-engine-fixture-fix-handoff-','rustusb-secure-engine-integration-design-','rustusb-secure-server-author-handoff-','rustusb-secure-shared-','rustusb-secure-guide-independent-review-','rustusb-secure-software-','rustusb-secure-workspace-','rustusb-secure-native-only-','rustusb-secure-local-','rustusb-native-client-author-handoff','rustusb-native-secure-client-independent-source-review-','rustusb-native-secure-server-author-handoff-','rustusb-native-secure-server-helper-independent-review-','rustusb-native-server-author-handoff']
files=[p for p in Path('/tmp').glob('*20261023*') if p.is_file() and any(p.name.startswith(s) for s in prefixes)]
files.append(Path('/tmp/archive-rustusb-secure-software-20261023.py'))
entries=[]
def copy(source,rel):
 target=dest/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
 b=source.read_bytes();assert b==target.read_bytes();entries.append({'path':rel.as_posix(),'original':str(source),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
for p in sorted(files):copy(p,Path(p.name))
for p in sorted(Path('/tmp/rustusb-secure-local-signals-20261023').rglob('*')):
 if p.is_file():copy(p,Path('signals')/p.relative_to('/tmp/rustusb-secure-local-signals-20261023'))
manifest={'scope':'Development and full software validation of the dirty candidate based on 056341c; no new secure USB/IP QEMU result. Preserve original failures and source-specific handoffs. Native-only six tests and local signal/backpressure cases are separate from396 executions.','files':entries}
(dest/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps({'files':len(entries),'bytes':sum(f['bytes'] for f in entries)}))
