from pathlib import Path
import json,subprocess,urllib.request,hashlib,datetime
root=Path('/home/ubuntu/github/usbip-client');head='1ed00832943789564bdac5b472391a4226f29876';out=Path('/tmp/rustusb-automatic-rust-publication-1ed00832-20261025')
def git(*args):return subprocess.check_output(['git',*args],cwd=root)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
assert git('rev-parse','HEAD').decode().strip()==head and not git('status','--porcelain').strip()
remote=git('ls-remote','origin','refs/heads/main');(out/'remote-main.txt').write_bytes(remote);assert remote.decode().split()[0]==head
resources=[]
for n in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg','assets/gui-qemu.png']:
 url='https://tiansongyu.github.io/usbip-client/'+(''if n=='index.html'else n)
 with urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache','Accept-Encoding':'identity'}),timeout=30)as response:body=response.read();status=response.status;headers=dict(response.headers)
 target=out/'http'/n;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(body);target.with_name(target.name+'.headers.json').write_text(json.dumps(headers,indent=2)+'\n')
 row={'url':url,'status':status,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),'exact_git_bytes':body==git('show',head+':website/'+n)};resources.append(row);(out/'http-progress.json').write_text(json.dumps(resources,indent=2)+'\n');assert status==200 and row['exact_git_bytes'],row
base=root/'docs/evidence/automatic-sharing-rust-qemu-20261025';qa=json.loads((base/'qa.json').read_text());software=json.loads((base/'summary.json').read_text())
for n,expected in qa['site_files'].items():assert sha(root/n)==expected
for n,info in qa['global_skill_unchanged'].items():assert sha(Path('/home/ubuntu/.agents/skills/readme-site')/n)==info['sha256']
link=Path('/home/ubuntu/.codex/skills/readme-site');assert link.is_symlink()and link.resolve()==Path('/home/ubuntu/.agents/skills/readme-site')
for n,info in software['inputs'].items():assert sha(root/n)==info['sha256'],n
for n,info in software['binaries'].items():assert sha(root/('apps/rustusb-gui/target/release/rustusb-gui'if n=='rustusb-gui'else'target/release/'+n))==info['sha256']
for member in json.loads((base/'manifest.json').read_text())['files']:
 p=base/member['path'];assert p.stat().st_size==member['bytes']and sha(p)==member['sha256'];assert git('ls-files','--error-unmatch',str(p.relative_to(root)))
result={'result':'PASS','content_head':head,'local_gate_head':software['head'],'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'http':resources,'frozen_inputs_preserved':len(software['inputs']),'site_files':len(qa['site_files']),'global_skill_files':len(qa['global_skill_unchanged']),'binaries':software['binaries'],'scope':'Canonical HTTPS/Git bodies and source/release/global skill integrity. CI state saved separately; no CI logs/ELFs/per-platform test recount or new hardware proof.'}
(out/'http-and-source.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
