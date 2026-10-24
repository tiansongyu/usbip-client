from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import sys, json, hashlib, subprocess, urllib.request, urllib.error

head='52627d8a915ee300c4d0e2fefdf286fc464a79b9'
root=Path('/tmp/rustusb-authentication-publication-52627d8-20261024')
out=root/sys.argv[1]
out.mkdir(parents=True,exist_ok=False)
files=['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg']
def fetch(name):
    url='https://tiansongyu.github.io/usbip-client/'+('' if name=='index.html' else name)
    expected=subprocess.check_output(['git','show',head+':website/'+name])
    result={'url':url,'source':'website/'+name,'head':head,'expected_bytes':len(expected),'expected_sha256':hashlib.sha256(expected).hexdigest()}
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache','User-Agent':'RustUSB-publication-verification'}),timeout=20) as response:
            body=response.read(4*1024*1024+1)
            if len(body)>4*1024*1024:raise ValueError('unexpected oversized public response')
            result.update(status=response.status,final_url=response.url,headers=dict(response.headers),bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),matches_commit=body==expected)
            p=out/'live'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(body)
            p=out/'expected'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(expected)
    except Exception as e:
        result.update(error_type=type(e).__name__,error=str(e),matches_commit=False)
    return result
with ThreadPoolExecutor(max_workers=4) as pool: rows=list(pool.map(fetch,files))
ok=all(x.get('status')==200 and x['matches_commit'] for x in rows)
report={'result':'PASS' if ok else 'NOT_YET_MATCHING','scope':'Actual canonical HTTPS bytes compared with exact content-commit Git blobs; no browser/pixel or USB claim. Prior attempts retained separately.','head':head,'resources':rows}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'path':str(out),'result':report['result'],'resources':[{'source':x['source'],'status':x.get('status'),'matches_commit':x['matches_commit']} for x in rows]}))
