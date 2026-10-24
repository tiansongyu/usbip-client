#!/usr/bin/env python3
"""Recheck saved source bytes against their recorded Git commit; no product execution."""
from pathlib import Path
import hashlib,json,subprocess,datetime
out=Path(__file__).resolve().parent
repo=Path('/home/ubuntu/github/usbip-client')
saved=json.loads((out/'sources.json').read_text())
rows=[]
for row in saved['sources']:
    current=(repo/row['path']).read_bytes()
    original=(out/row['copy']).read_bytes()
    blob=subprocess.check_output(['git','show',saved['head']+':'+row['path']],cwd=repo)
    rows.append({'path':row['path'],'saved_sha_matches':hashlib.sha256(original).hexdigest()==row['sha256'],'current_matches_saved':current==original,'saved_matches_recorded_git_blob':original==blob})
result={'schema_version':1,'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head_recorded':saved['head'],'head_now':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),'checks':rows,'all_source_checks_pass':all(all(v for k,v in r.items() if k!='path') for r in rows),'status_now':subprocess.check_output(['git','status','--porcelain'],cwd=repo,text=True),'scope':'Only source-byte/Git comparisons; no product or test execution.'}
(out/'source-after.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='checks'},indent=2))
