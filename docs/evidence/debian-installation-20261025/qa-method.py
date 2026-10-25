from pathlib import Path
import hashlib,json,re,subprocess,urllib.parse
from html.parser import HTMLParser
root=Path('/home/ubuntu/github/usbip-client');out=root/'docs/evidence/debian-installation-20261025'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def git(*args):return subprocess.check_output(['git',*args],cwd=root)
soft=json.loads(Path('/tmp/rustusb-debian-software-final-20261025/summary.json').read_text())
changed=[]
for name,info in soft['inputs'].items():
 b=git('show',soft['head']+':'+name);assert len(b)==info['bytes'] and hashlib.sha256(b).hexdigest()==info['sha256']
 if sha(root/name)!=info['sha256']:changed.append(name)
assert set(changed)=={'docs/debian-installation.md','docs/third-party.md'},changed
assert not git('diff','22d3c485','--','packaging')
assert not git('diff','412b40ff','--','crates','apps','vendor','Cargo.toml','Cargo.lock')
for name,info in soft['binaries'].items():
 p=root/('apps/rustusb-gui/target/release/rustusb-gui' if name=='rustusb-gui' else 'target/release/'+name)
 assert p.stat().st_size==info['bytes'] and sha(p)==info['sha256']
skill=Path('/home/ubuntu/.agents/skills/readme-site')
baseline=json.loads((root/'docs/evidence/tui-interactions-docs-20261025/summary.json').read_text())['global_skill_unchanged']
for name,info in baseline.items():assert (skill/name).stat().st_size==info['bytes'] and sha(skill/name)==info['sha256']
symlink=Path('/home/ubuntu/.codex/skills/readme-site');assert symlink.is_symlink() and symlink.resolve()==skill.resolve()
docs=[str(p.relative_to(root)) for p in root.glob('docs/research/*installation*.md')]
docs+=['README.md','README.zh-CN.md','AGENTS.md','.agents/skills/rustusb-development/SKILL.md','docs/debian-installation.md','docs/research/debian-lifecycle-research.md','docs/research/systemd-lifecycle-research.md','docs/architecture.md','docs/gui.md','docs/acceptance.md','docs/compatibility.md','docs/testing.md','docs/roadmap.md','docs/third-party.md','docs/application-service-guide.md','docs/native-guide.md','scripts/qemu_package/README.md']
targets=set()
for name in set(docs):
 for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)',(root/name).read_text()):
  if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:|^#',target):continue
  path=urllib.parse.unquote(target.split('#',1)[0]);p=(root/name).parent/path
  if path:assert p.exists(),(name,target);targets.add(str(p.resolve()))
class HTML(HTMLParser):
 def __init__(self):super().__init__();self.ids=[];self.refs=[]
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if 'id' in a:self.ids.append(a['id'])
  for k in ['href','src']:
   if k in a:self.refs.append(a[k])
h=HTML();h.feed((root/'website/index.html').read_text());assert len(h.ids)==len(set(h.ids))
for target in h.refs:
 u=urllib.parse.urlparse(target)
 if not u.scheme:
  assert (root/'website'/u.path).exists(),target
  if u.fragment and not u.path:assert u.fragment in h.ids,target
 elif target.startswith('https://github.com/tiansongyu/usbip-client/blob/main/'):
  assert (root/urllib.parse.unquote(u.path.split('/blob/main/',1)[1])).exists(),target
site={str(p.relative_to(root)):sha(p) for p in (root/'website').rglob('*') if p.is_file()}
site_changed=[n for n in site if (root/n).read_bytes()!=git('show','412b40ff:'+n)]
assert site_changed==['website/index.html'],site_changed
for label,cmd,needle in [('cli',['target/release/rustusb','--help'],b'agent'),('gui',['apps/rustusb-gui/target/release/rustusb-gui','--help'],b'--manifest'),('agent',['target/release/rustusb','agent','--config','/etc/rustusb/agent.json','run','--help'],b'--automation-decisions-policy')]:
 r=subprocess.run(cmd,cwd=root,capture_output=True,check=True);assert needle in r.stdout;(out/(label+'-help.txt')).write_bytes(r.stdout)
subprocess.run(['node','--check','website/app.js'],cwd=root,check=True)
(out/'qa-method.py').write_bytes(Path(__file__).read_bytes())
result=dict(passed=True,source_head=soft['head'],frozen_inputs_checked=len(soft['inputs']),changed_inputs_since_gate=changed,site_files=site,site_changed=site_changed,global_skill_unchanged=baseline,global_skill_symlink=str(symlink.resolve()),documentation_count=len(set(docs)),local_targets=len(targets),site_refs=len(h.refs),same_three_gate_elfs=True,package_code_unchanged_from='22d3c485',rust_source_unchanged_from='412b40ff',scope='Static source/docs/assets/help integrity. Browser and CI/publication are separate observations.')
(out/'qa.json').write_text(json.dumps(result,indent=2)+'\n')
manifest={p.name:{'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(out.iterdir()) if p.is_file() and p.name!='manifest.json'}
(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ['passed','frozen_inputs_checked','documentation_count','local_targets','site_refs','changed_inputs_since_gate']}))
