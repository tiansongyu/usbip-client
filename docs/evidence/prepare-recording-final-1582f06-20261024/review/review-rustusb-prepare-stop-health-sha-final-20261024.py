#!/usr/bin/env python3
"""Expand the independent parser's successful final SHA equality into explicit pairs."""
import hashlib,json
from pathlib import Path
base=Path('/tmp/rustusb-prepare-stop-health-independent-review-final-20261024')
original=json.loads((base/'raw-sha256.json').read_text())
rows=[]
for row in original['files']:
 data=Path(row['path']).read_bytes();after={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()};before={k:row[k] for k in ('bytes','sha256')}
 rows.append({'path':row['path'],'before':before,'after':after,'unchanged':before==after})
output=base/'originals-before-after.json'
output.write_text(json.dumps({'result':'PASS' if all(r['unchanged'] for r in rows) else 'FAIL','count':len(rows),'scope':'Explicit pairs expand the original independent parser read/final-equality result; appendable parent results.json has its own separately observed before/after hashes in report and is not called a frozen child original.','files':rows},indent=2)+'\n')
print(json.dumps({'result':'PASS' if all(r['unchanged'] for r in rows) else 'FAIL','count':len(rows),'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}))
raise SystemExit(0 if all(r['unchanged'] for r in rows) else 1)
