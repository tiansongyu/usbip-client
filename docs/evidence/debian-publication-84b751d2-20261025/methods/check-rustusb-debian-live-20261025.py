from pathlib import Path
import argparse,datetime,hashlib,json,subprocess,urllib.request
p=argparse.ArgumentParser();p.add_argument('--head',required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
root=Path('/home/ubuntu/github/usbip-client');out=args.output;out.mkdir(parents=True,exist_ok=True)
def git(*a):return subprocess.check_output(['git',*a],cwd=root)
remote=git('ls-remote','origin','refs/heads/main');(out/'remote-main.txt').write_bytes(remote);assert remote.decode().split()[0]==args.head
rows=[]
for n in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg','assets/gui-qemu.png']:
 url='https://tiansongyu.github.io/usbip-client/'+('' if n=='index.html' else n)
 with urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache','Accept-Encoding':'identity'}),timeout=30) as response:
  body=response.read();status=response.status;headers=dict(response.headers)
 target=out/'http'/n;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body)
 target.with_name(target.name+'.headers.json').write_text(json.dumps(headers,indent=2)+'\n')
 row={'url':url,'status':status,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),'exact_git_bytes':body==git('show',args.head+':website/'+n)};rows.append(row)
 (out/'http-progress.json').write_text(json.dumps(rows,indent=2)+'\n');assert status==200 and row['exact_git_bytes'],row
result={'passed':True,'content_head':args.head,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'http':rows,'scope':'Canonical deployed HTTPS and exact Git asset bytes; no new product/hardware execution.'}
(out/'http-summary.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'passed':True,'head':args.head,'exact_assets':len(rows)}))
