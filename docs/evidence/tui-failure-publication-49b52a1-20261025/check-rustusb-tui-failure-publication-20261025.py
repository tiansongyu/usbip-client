import pathlib,json,subprocess,urllib.request,hashlib,datetime,sys,re
repo=pathlib.Path('/home/ubuntu/github/usbip-client');head=sys.argv[1];out=pathlib.Path(sys.argv[2]);assert re.fullmatch('[0-9a-f]{40}',head)
def git(*a):return subprocess.check_output(['git',*a],cwd=repo)
def sha(b):return hashlib.sha256(b).hexdigest()
assert git('rev-parse','HEAD').decode().strip()==head and not git('status','--porcelain').strip()
http=[]
for name in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg']:
 url='https://tiansongyu.github.io/usbip-client/'+(''if name=='index.html'else name)
 with urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache','Accept-Encoding':'identity'}),timeout=30)as response:body=response.read();status=response.status;headers=dict(response.headers)
 target=out/'http'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body);(out/'http'/(name+'.headers.json')).write_text(json.dumps(headers,indent=2)+'\n')
 row={'url':url,'git_path':'website/'+name,'status':status,'bytes':len(body),'sha256':sha(body),'exact_git_bytes':body==git('show',head+':website/'+name)};http.append(row);(out/'http-progress.json').write_text(json.dumps(http,indent=2)+'\n');assert status==200 and row['exact_git_bytes'],row
base=repo/'docs/evidence/tui-failure-retry-4ee6be3-20261025';s=json.loads((base/'software-summary.json').read_text())
for n,i in s['inputs_before'].items():assert sha((repo/n).read_bytes())==i['sha256']
qs={}
for p in base.glob('*-summary.json'):
 if p.name=='software-summary.json'or '-red-'in p.name:continue
 q=json.loads(p.read_text());assert q['result']=='PASS'
 for n,i in q['source_inputs'].items():assert sha((repo/n).read_bytes())==i['sha256']
 qs[p.name]=len(q['source_inputs'])
assert len(qs)==4
qa=json.loads((repo/'docs/evidence/tui-failure-docs-20261025/summary.json').read_text())
for n,i in qa['site_files'].items():assert sha((repo/n).read_bytes())==i['sha256']
skill=pathlib.Path('/home/ubuntu/.agents/skills/readme-site')
for n,i in qa['global_skill_unchanged'].items():assert sha((skill/n).read_bytes())==i['sha256']
link=pathlib.Path('/home/ubuntu/.codex/skills/readme-site');assert link.is_symlink()and link.resolve()==skill
binary=next(c['binaries']for c in s['commands']if c['name']=='release-all')
for n,i in binary.items():assert sha((repo/'target/release'/n).read_bytes())==i['sha256']
summary={'result':'PASS','head':head,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'http_resources':http,'unchanged_inputs':{'software':len(s['inputs_before']),'qemu':qs,'site':len(qa['site_files']),'global_skill':len(qa['global_skill_unchanged'])},'scope':'Actual canonical HTTPS bodies against exact published content and unchanged source/ELF/global skill. Website/skill unchanged; no new browser/USB/pixel/security audit.'}
(out/'http-and-source.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
