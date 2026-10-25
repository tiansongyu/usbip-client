import pathlib,json,subprocess,urllib.request,hashlib,datetime
root=pathlib.Path('/home/ubuntu/github/usbip-client');head='8fd0094b31ace722d15a32841200df47036bcb45';out=pathlib.Path('/tmp/rustusb-gui-actions-publication-20261025')
def git(*args):return subprocess.check_output(['git',*args],cwd=root)
def sha(b):return hashlib.sha256(b).hexdigest()
assert git('rev-parse','HEAD').decode().strip()==head and not git('status','--porcelain').strip()
remote=git('ls-remote','origin','refs/heads/main');(out/'remote-main.txt').write_bytes(remote);assert remote.decode().split()[0]==head
resources=[]
for name in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg','assets/gui-qemu.png']:
 url='https://tiansongyu.github.io/usbip-client/'+(''if name=='index.html'else name)
 with urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache','Accept-Encoding':'identity'}),timeout=30)as response:body=response.read();status=response.status;headers=dict(response.headers)
 target=out/'http'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body);target.with_name(target.name+'.headers.json').write_text(json.dumps(headers,indent=2)+'\n')
 row={'url':url,'status':status,'bytes':len(body),'sha256':sha(body),'exact_git_bytes':body==git('show',head+':website/'+name)};resources.append(row);(out/'http-progress.json').write_text(json.dumps(resources,indent=2)+'\n');assert status==200 and row['exact_git_bytes'],row
qa=json.loads((root/'docs/evidence/gui-native-actions-docs-20261025/summary.json').read_text())
for name,info in qa['site_files'].items():assert sha((root/'website'/name).read_bytes())==info['sha256']
for name,info in qa['global_skill_unchanged'].items():assert sha((pathlib.Path('/home/ubuntu/.agents/skills/readme-site')/name).read_bytes())==info['sha256']
link=pathlib.Path('/home/ubuntu/.codex/skills/readme-site');assert link.is_symlink()and link.resolve()==pathlib.Path('/home/ubuntu/.agents/skills/readme-site')
software=json.loads((root/'docs/evidence/gui-native-actions-20261025/software-summary.json').read_text());count=0
for n,i in software['inputs'].items():
 if n=='apps/rustusb-gui/README.md':continue
 assert sha((root/n).read_bytes())==i['sha256'],n;count+=1
for n,info in software['binaries'].items():assert sha((root/('apps/rustusb-gui/target/release/rustusb-gui'if n=='rustusb-gui'else'target/release/'+n)).read_bytes())==info['sha256']
result={'result':'PASS','content_head':head,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'http':resources,'frozen_non_readme_inputs_preserved':count,'site_files':len(qa['site_files']),'global_skill_files':len(qa['global_skill_unchanged']),'binaries':software['binaries'],'scope':'Exact canonical HTTPS/Git bytes and source/release/global skill integrity. CI state saved separately. Not a new browser pixel/native USB/hardware test.'}
(out/'http-and-source.json').write_text(json.dumps(result,indent=2)+'\n');print('PASS: seven canonical HTTPS bodies match Git; source/ELF/global skill preserved')
