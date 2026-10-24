from pathlib import Path
import urllib.request,subprocess,json,hashlib,datetime,concurrent.futures
repo=Path('/home/ubuntu/github/usbip-client')
head='7640efbbe2f57c2a791b38da339695976e80205d'
out=Path('/tmp/rustusb-logging-publication-7640efb-20261024/live-first');out.mkdir(exist_ok=False)
files=[('','website/index.html','index.html'),('styles.css','website/styles.css','styles.css'),('app.js','website/app.js','app.js'),('assets/favicon.svg','website/assets/favicon.svg','assets/favicon.svg'),('assets/usbip-flow.svg','website/assets/usbip-flow.svg','assets/usbip-flow.svg'),('assets/usbip-flow-mobile.svg','website/assets/usbip-flow-mobile.svg','assets/usbip-flow-mobile.svg')]
def check(row):
 relative,source,destination=row
 url='https://tiansongyu.github.io/usbip-client/'+relative
 request=urllib.request.Request(url,headers={'Cache-Control':'no-cache'})
 with urllib.request.urlopen(request,timeout=30) as r:
  body=r.read();receipt=dict(url=url,final_url=r.url,status=r.status,headers=dict(r.headers),fetched_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
 q=out/destination;q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(body)
 expected=subprocess.check_output(['git','show',head+':'+source],cwd=repo)
 return dict(**receipt,path=destination,git_path=source,bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),git_sha256=hashlib.sha256(expected).hexdigest(),equal_git_blob=body==expected)
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:rows=list(pool.map(check,files))
report={'head':head,'result':'PASS' if all(r['status']==200 and r['equal_git_blob'] for r in rows) else 'FAIL','resources':rows,'scope':'Actual canonical HTTPS resources, exact response bytes compared with content Git blobs. Not browser/pixel/USB tests.'}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'result':report['result'],'resources':[{'path':r['path'],'status':r['status'],'bytes':r['bytes'],'equal_git_blob':r['equal_git_blob']} for r in rows]}))

