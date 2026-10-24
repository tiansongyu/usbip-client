from pathlib import Path
import json,hashlib
out=Path('/home/ubuntu/github/usbip-client/docs/evidence/rotating-logs-publication-7640efb-20261024')
manifest=out/'manifest.json';v=json.loads(manifest.read_text())
existing={r['path'] for r in v['files']}
items=[
 (Path('/tmp/rustusb-logging-publication-7640efb-20261024/doc-links-publication-final.json'),'post-reference-qa/doc-links-publication-final.json'),
 (Path('/tmp/rustusb-rotating-logs-doc-links-publication-final-20261024.py'),'post-reference-qa/doc-link-check.py'),
 (Path(__file__),'post-reference-qa/append-receipts.py')]
for source,name in items:
 assert name not in existing
 b=source.read_bytes();destination=out/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(b)
 v['files'].append(dict(path=name,original=str(source),bytes=len(b),sha256=hashlib.sha256(b).hexdigest()))
manifest.write_text(json.dumps(v,indent=2)+'\n')
print('publication archive complete files',len(v['files'])+1,'bytes',sum(r['bytes'] for r in v['files']))

