import pathlib,json,subprocess,urllib.request,hashlib,datetime
repo=pathlib.Path('/home/ubuntu/github/usbip-client');out=pathlib.Path('/tmp/rustusb-tui-publication-5866f9a-20261024');head='5866f9a4a593b70bc321b50ef6a68abdb5aac847'
def git(*a):return subprocess.check_output(['git',*a],cwd=repo)
def sha(b):return hashlib.sha256(b).hexdigest()
assert git('rev-parse','HEAD').decode().strip()==head and not git('status','--porcelain').strip()
http=[]
for name in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg']:
 url='https://tiansongyu.github.io/usbip-client/'+(''if name=='index.html'else name)
 with urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache','Accept-Encoding':'identity'}),timeout=30)as response:body=response.read();status=response.status;headers=dict(response.headers)
 target=out/'http'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body);(out/'http'/(name+'.headers.json')).write_text(json.dumps(headers,indent=2)+'\n')
 row={'url':url,'git_path':'website/'+name,'status':status,'bytes':len(body),'sha256':sha(body),'exact_git_bytes':body==git('show',head+':website/'+name)};http.append(row);(out/'http-progress.json').write_text(json.dumps(http,indent=2)+'\n');assert status==200 and row['exact_git_bytes'],row
base=repo/'docs/evidence/tui-d181665-20261024';s=json.loads((base/'software-summary.json').read_text())
for n,i in s['inputs_before'].items():assert sha((repo/n).read_bytes())==i['sha256']
qs={}
for p in base.glob('*second*-summary.json'):
 q=json.loads(p.read_text());assert q['result']=='PASS'
 for n,i in q['source_inputs'].items():assert sha((repo/n).read_bytes())==i['sha256']
 qs[p.name]=len(q['source_inputs'])
qa=json.loads((repo/'docs/evidence/tui-docs-20261024/summary.json').read_text())
for n,i in qa['site_files'].items():assert sha((repo/n).read_bytes())==i['sha256']
skill=pathlib.Path('/home/ubuntu/.agents/skills/readme-site')
for n,i in qa['global_skill_unchanged'].items():assert sha((skill/n).read_bytes())==i['sha256']
link=pathlib.Path('/home/ubuntu/.codex/skills/readme-site');assert link.is_symlink()and link.resolve()==skill
binary=next(c['binaries']for c in s['commands']if c['name']=='release-all')
for n,i in binary.items():assert sha((repo/'target/release'/n).read_bytes())==i['sha256']
summary={'result':'PASS','head':head,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'http_resources':http,'unchanged_inputs':{'software':len(s['inputs_before']),'qemu':qs,'site':len(qa['site_files']),'global_skill':len(qa['global_skill_unchanged'])},'scope':'Actual canonical HTTPS bodies against exact published content and unchanged source/ELF/global skill. Browser DOM/geometry separately recorded; screenshot unavailable. No new USB, pixel or security audit.'}
(out/'http-and-source.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))
