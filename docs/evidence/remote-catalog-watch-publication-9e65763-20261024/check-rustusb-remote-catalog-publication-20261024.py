import datetime,hashlib,json,pathlib,subprocess,urllib.request
repo=pathlib.Path('/home/ubuntu/github/usbip-client')
out=pathlib.Path('/tmp/rustusb-remote-catalog-publication-9e65763-20261024')
head='9e65763705aa48f4a8085ecb6f8b938fea4b7305'
def git(*args):return subprocess.check_output(['git',*args],cwd=repo)
def sha(b):return hashlib.sha256(b).hexdigest()
assert git('rev-parse','HEAD').decode().strip()==head
assert not git('status','--porcelain').strip()
records=[]
for name in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg']:
 url='https://tiansongyu.github.io/usbip-client/'+('' if name=='index.html' else name)
 request=urllib.request.Request(url,headers={'Cache-Control':'no-cache','Accept-Encoding':'identity'})
 with urllib.request.urlopen(request,timeout=30) as response:
  body=response.read();headers=dict(response.headers);status=response.status;final_url=response.url
 target=out/'http'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body)
 (out/'http'/(name+'.headers.json')).write_text(json.dumps(headers,indent=2)+'\n')
 expected=git('show',head+':website/'+name)
 row={'url':url,'final_url':final_url,'http_status':status,'git_path':'website/'+name,'bytes':len(body),'sha256':sha(body),'exact_git_bytes':body==expected}
 records.append(row)
 (out/'http-progress.json').write_text(json.dumps(records,indent=2)+'\n')
 assert status==200 and row['exact_git_bytes'],row
software=json.loads((repo/'docs/evidence/remote-catalog-watch-633b2a3-20261024/software-summary.json').read_text())
for name,item in software['inputs_before'].items():assert sha((repo/name).read_bytes())==item['sha256'] and len((repo/name).read_bytes())==item['bytes'],name
qemu={}
for family in ['v4','v6']:
 data=json.loads((repo/('docs/evidence/remote-catalog-watch-633b2a3-20261024/qemu-'+family+'-final-summary.json')).read_text())
 for name,item in data['source_inputs'].items():assert sha((repo/name).read_bytes())==item['sha256'],name
 qemu[family]=len(data['source_inputs'])
skill=pathlib.Path('/home/ubuntu/.agents/skills/readme-site');expected=json.loads((repo/'docs/evidence/remote-catalog-watch-docs-20261024/final/summary.json').read_text())['global_skill']
for name,item in expected.items():assert sha((skill/name).read_bytes())==item['sha256'],name
link=pathlib.Path('/home/ubuntu/.codex/skills/readme-site');assert link.is_symlink() and link.resolve()==skill
summary={'result':'PASS','head':head,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'http_resources':records,'unchanged_inputs':{'software':len(software['inputs_before']),'qemu_each':qemu,'global_skill':len(expected)},'scope':'Exact canonical HTTPS resource and tested-source byte checks; not browser or new USB execution. CI primary API recorded separately.'}
(out/'http-and-source.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
