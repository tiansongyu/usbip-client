from pathlib import Path
import hashlib,json,shutil
root=Path.cwd();dest=root/'docs/evidence/secure-usbip-integration-preparation-20261023';dest.mkdir(exist_ok=False)
prefixes=['rustusb-secure-harness-','rustusb-secure-native-phase-static-','rustusb-secure-role-payload-static-','rustusb-secure-payload-dependencies-','rustusb-secure-journal-','rustusb-secure-software-fourth-','rustusb-secure-software-environment-','rustusb-secure-software-independent-review-','rustusb-secure-ci-independent-review-','rustusb-native-used-before-device-add-audit-','check-rustusb-secure-harness-root-']
files={p for p in Path('/tmp').glob('*20261023*') if p.is_file() and any(p.name.startswith(s) for s in prefixes)}
files.add(Path('/tmp/archive-rustusb-secure-preparation-20261023.py'));entries=[]
def copy(source,rel):
 target=dest/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);b=source.read_bytes();assert b==target.read_bytes();entries.append({'path':rel.as_posix(),'original':str(source),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
for p in sorted(files):copy(p,Path(p.name))
for dirname,relname in [('rustusb-secure-ci-39950528568-20261023','ci41e2c67'),('rustusb-secure-payload-dependencies-preflight-20261023','payload-initial'),('rustusb-secure-payload-dependencies-final-preflight-20261023','payload-final')]:
 source=Path('/tmp')/dirname
 for p in sorted(source.rglob('*')):
  if p.is_file():copy(p,Path(relname)/p.relative_to(source))
manifest={'scope':'Preflight, independent software/CI review and later diagnostic preparation. Original early static reviews missed the absent base64 and do not imply QEMU success. First three actual QEMU runs remain FAIL in separate archives. CI exact41e2c67 has396 per platform; fourth software gate396 belongs to changed CLI error handling. Payload preflight is host-only and no USB execution. Native USED source audit establishes timing, not exact first failing syscall.','files':entries}
(dest/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print({'files':len(entries),'bytes':sum(f['bytes'] for f in entries)})
