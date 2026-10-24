from pathlib import Path
import json,hashlib,datetime
out=Path(__file__).resolve().parent
repo=Path('/home/ubuntu/github/usbip-client')
rows=json.loads((out/'final-inputs-before.json').read_text());after=[]
for row in rows:
 b=(repo/row['path']).read_bytes();actual=dict(path=row['path'],bytes=len(b),sha256=hashlib.sha256(b).hexdigest());after.append(actual)
 assert actual==row,row['path']
 assert (out/'final-source'/row['path']).read_bytes()==b,row['path']
(out/'final-inputs-after.json').write_text(json.dumps(after,indent=2)+'\n')
fixed=json.loads((out/'fixed-tokio-inputs.json').read_text())
for row in fixed['files']:
 b=Path(row['original']).read_bytes();assert len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256']
 assert (out/'fixed-tokio-source'/row['path']).read_bytes()==b
result=dict(result='PASS_source_review_guards',selected_inputs=len(rows),final_selected_source_stable=True,fixed_tokio_inputs_stable=len(fixed['files']),utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),scope='Read-only actual byte guards; source findings are in REVIEW.md, not automatically proved by this script. No tests/Cargo/runtime acceptance.')
(out/'guard-result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
