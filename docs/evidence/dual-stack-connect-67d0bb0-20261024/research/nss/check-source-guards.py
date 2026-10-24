from pathlib import Path
import json,hashlib
out=Path(__file__).resolve().parent
identity=json.loads((out/'fixed-rust-inputs.json').read_text())
for row in identity['files']:
 b=Path(row['original']).read_bytes()
 assert len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256']
 assert (out/'fixed-rust-source'/row['path']).read_bytes()==b
root=Path('/tmp/rustusb-dual-stack-native-ipv4-probe-first-20261024')
rows=[]
for name in ['client-artifacts/dual-resolver.txt','client-artifacts/dual-host-conf.txt','client-artifacts/dual-hosts.txt','client-artifacts/dual-gai.txt','client-artifacts/dual-nsswitch.txt','resolver-probe-build.json']:
 b=(root/name).read_bytes();assert (out/'root-guest-probe'/name).read_bytes()==b
 rows.append(dict(original=str(root/name),path='root-guest-probe/'+name,bytes=len(b),sha256=hashlib.sha256(b).hexdigest()))
(out/'original-probe-inputs.json').write_text(json.dumps(rows,indent=2)+'\n')
print('PASS fixedRust source2 and selected original root-probe receipts6 stable; no new execution')
