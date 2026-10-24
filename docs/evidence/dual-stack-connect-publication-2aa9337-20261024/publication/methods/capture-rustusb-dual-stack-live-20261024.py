from pathlib import Path
import urllib.request,json,subprocess,hashlib,datetime
out=Path('/tmp/rustusb-dual-stack-publication-2aa9337-20261024');root=Path('/home/ubuntu/github/usbip-client');commit='2aa9337411ed67f4626c04425703fe09a5bcc5c7';rows=[]
for n in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg']:
 url='https://tiansongyu.github.io/usbip-client/'+('' if n=='index.html' else n)
 with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'RustUSB-publication-byte-check'}),timeout=40) as response:
  b=response.read();status=response.status;headers=dict(response.headers);effective=response.url
 p=out/'live'/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b);(out/'live'/(n.replace('/','_')+'.headers.json')).write_text(json.dumps(headers,indent=2)+'\n')
 expected=subprocess.check_output(['git','show',commit+':website/'+n],cwd=root);row={'url':url,'effective_url':effective,'status':status,'path':n,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'git_commit':commit,'equal_git_blob':b==expected};rows.append(row)
 (out/'live-review.json').write_text(json.dumps({'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'resources':rows},indent=2)+'\n');assert status==200 and b==expected,row
print('PASS six canonical HTTPS resources exactly match',commit)
