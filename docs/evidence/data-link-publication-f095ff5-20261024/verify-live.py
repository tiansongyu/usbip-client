from pathlib import Path
import urllib.request,hashlib,json,subprocess,datetime

root=Path('/home/ubuntu/github/usbip-client')
out=Path('/tmp/rustusb-data-link-publication-f095ff5-20261024/live')
out.mkdir(exist_ok=False)
head='f095ff51b35134c3209d49d21b77ce695c9554b0'
rows=[]
for name in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg']:
    url='https://tiansongyu.github.io/usbip-client/'+name
    with urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache'}),timeout=30) as response:
        raw=response.read();status=response.status;headers=dict(response.headers.items())
    p=out/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    expected=subprocess.check_output(['git','show',head+':website/'+name],cwd=root)
    rows.append(dict(path=name,url=url,http_status=status,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),matches_git_blob=raw==expected,headers=headers))
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),result='PASS' if all(r['http_status']==200 and r['matches_git_blob'] for r in rows) else 'FAIL',head=head,resources=rows,scope='Six live HTTPS resources equal exact content Git blobs. Browser DOM is verified separately; no screenshot/pixel claim.')
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(dict(result=report['result'],resources=len(rows),head=head)))
assert report['result']=='PASS'
