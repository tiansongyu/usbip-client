from pathlib import Path
import hashlib,json,subprocess,urllib.request,datetime
out=Path('/tmp/rustusb-authentication-admission-publication-5845b14-20261024/live-attempt-1');out.mkdir(exist_ok=False)
head='5845b14ba41b6d112cf264845e5beecbb7533ef3';rows=[]
for n in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg']:
 u='https://tiansongyu.github.io/usbip-client/'+('' if n=='index.html' else n)
 req=urllib.request.Request(u,headers={'Cache-Control':'no-cache'})
 with urllib.request.urlopen(req,timeout=20) as r:b=r.read();headers=dict(r.headers);status=r.status;url=r.url
 p=out/'bodies'/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
 expected=subprocess.check_output(['git','show',head+':website/'+n])
 rows.append(dict(path=n,url=url,status=status,headers=headers,bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),expected_sha256=hashlib.sha256(expected).hexdigest(),exact_git_bytes=b==expected))
result=dict(head=head,utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),resources=rows,result='PASS' if all(x['status']==200 and x['exact_git_bytes'] for x in rows) else 'FAIL')
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(result['result'],[(x['path'],x['status'],x['exact_git_bytes']) for x in rows]);raise SystemExit(0 if result['result']=='PASS' else 1)
