import datetime,hashlib,json,pathlib,subprocess,urllib.request
repo=pathlib.Path('/home/ubuntu/github/usbip-client');out=pathlib.Path('/tmp/rustusb-crl-publication-91ecc44-20261024');head='91ecc44fe458de40ff806b80d7a73b7e24440a64'
def git(*args):return subprocess.check_output(['git',*args],cwd=repo)
def sha(b):return hashlib.sha256(b).hexdigest()
assert git('rev-parse','HEAD').decode().strip()==head and not git('status','--porcelain').strip()
rows=[]
for name in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg']:
 url='https://tiansongyu.github.io/usbip-client/'+('' if name=='index.html' else name)
 with urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache','Accept-Encoding':'identity'}),timeout=30) as response:body=response.read();headers=dict(response.headers);status=response.status;final_url=response.url
 target=out/'http'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body);(out/'http'/(name+'.headers.json')).write_text(json.dumps(headers,indent=2)+'\n')
 row={'url':url,'final_url':final_url,'http_status':status,'git_path':'website/'+name,'bytes':len(body),'sha256':sha(body),'exact_git_bytes':body==git('show',head+':website/'+name)};rows.append(row);(out/'http-progress.json').write_text(json.dumps(rows,indent=2)+'\n');assert status==200 and row['exact_git_bytes'],row
base=repo/'docs/evidence/offline-crl-e441bb8-20261024';software=json.loads((base/'software-summary.json').read_text())
for n,i in software['inputs_before'].items():assert sha((repo/n).read_bytes())==i['sha256'] and (repo/n).stat().st_size==i['bytes'],n
qemus={}
for p in base.glob('*-summary.json'):
 if p.name.startswith(('software','prior')):continue
 data=json.loads(p.read_text())
 for n,i in data['source_inputs'].items():assert sha((repo/n).read_bytes())==i['sha256'],n
 qemus[p.name]=len(data['source_inputs'])
assert len(qemus)==13
skill=pathlib.Path('/home/ubuntu/.agents/skills/readme-site');qa=json.loads((repo/'docs/evidence/offline-crl-docs-20261024/summary.json').read_text());expected=qa['global_skill_unchanged']
for n,i in expected.items():assert sha((skill/n).read_bytes())==i['sha256'],n
link=pathlib.Path('/home/ubuntu/.codex/skills/readme-site');assert link.is_symlink() and link.resolve()==skill
for n,i in qa['site_files'].items():assert sha((repo/n).read_bytes())==i['sha256'],n
summary={'result':'PASS','content_head':head,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'http_resources':rows,'unchanged_inputs':{'software':len(software['inputs_before']),'qemu':qemus,'site':len(qa['site_files']),'global_skill':len(expected)},'scope':'Exact canonical HTTP bodies and unchanged tested source. No new browser/pixel/USB execution; CI primary responses separate.'}
(out/'http-and-source.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
