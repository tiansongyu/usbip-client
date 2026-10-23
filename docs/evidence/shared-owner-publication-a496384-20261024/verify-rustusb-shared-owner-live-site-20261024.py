import pathlib,urllib.request,hashlib,json,subprocess,datetime
root=pathlib.Path('/home/ubuntu/github/usbip-client');out=pathlib.Path('/tmp/rustusb-shared-owner-live-a496384-20261024');out.mkdir(exist_ok=False);head='a496384f9bdeb38e46ccb1c6fca4458b49e42eb8';rows=[]
for name in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg']:
 url='https://tiansongyu.github.io/usbip-client/'+name
 with urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache'}),timeout=30) as response:raw=response.read();status=response.status;headers=dict(response.headers.items())
 p=out/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);expected=subprocess.check_output(['git','show',head+':website/'+name],cwd=root);row={'path':name,'url':url,'http_status':status,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'matches_git_blob':raw==expected,'etag':headers.get('ETag')};rows.append(row)
 assert status==200 and raw==expected,row
report={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'result':'PASS','head':head,'resources':rows,'scope':'six live HTTPS resource bytes equal exact content Git blobs; browser DOM verification is separate, not a pixel screenshot'};(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'result':'PASS','resources':len(rows),'head':head}))
