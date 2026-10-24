from pathlib import Path
import urllib.request,json,hashlib
root=Path('/home/ubuntu/github/usbip-client/website');out=Path('/tmp/rustusb-hotplug-docs-site-20261024');base='http://127.0.0.1:41995/usbip-client/'
rows=[]
for name in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg']:
 url=base+('' if name=='index.html' else name)
 with urllib.request.urlopen(url,timeout=10) as response:b=response.read();headers=dict(response.headers);status=response.status
 p=out/'http'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
 (out/'http'/(name.replace('/','_')+'.headers.json')).write_text(json.dumps(headers,indent=2)+'\n')
 assert status==200 and b==(root/name).read_bytes();rows.append({'url':url,'status':status,'path':name,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'equal_source':True})
with urllib.request.urlopen('http://127.0.0.1:41995/usbip-client-nojs/',timeout=10) as response:
 b=response.read();headers=dict(response.headers);assert response.status==200 and b==(root/'index.html').read_bytes() and headers.get('Content-Security-Policy')=="script-src 'none'"
(out/'http/nojs.headers.json').write_text(json.dumps(headers,indent=2)+'\n');(out/'http-review.json').write_text(json.dumps({'result':'PASS','resources':rows,'nojs_same_html_and_CSP':True},indent=2)+'\n');print('PASS six project-subpath HTTP resources equal source bytes; nojs same HTML and explicit CSP')
