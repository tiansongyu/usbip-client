from pathlib import Path
import json,hashlib,subprocess,re,importlib.util,urllib.parse
from html.parser import HTMLParser
root=Path('/home/ubuntu/github/usbip-client');out=root/'docs/evidence/frontend-intent-recovery-20261025'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def git(*args):return subprocess.check_output(['git',*args],cwd=root)
s=json.loads((out/'summary.json').read_text())
for name,info in s['inputs'].items():
 b=git('show',s['head']+':'+name);assert len(b)==info['bytes'] and __import__('hashlib').sha256(b).hexdigest()==info['sha256']
changed_inputs=[name for name,info in s['inputs'].items()if sha(root/name)!=info['sha256']]
assert set(changed_inputs)=={'scripts/qemu_frontend_recovery/run.py','scripts/qemu_frontend_recovery/guest.sh'},changed_inputs
qemu_head=json.loads((out/'validation.json').read_text())['qemu_head']
for n in changed_inputs:assert (root/n).read_bytes()==git('show',qemu_head+':'+n)
assert not git('diff',s['head'],'--','crates','apps','Cargo.toml','Cargo.lock')
for name,meta in s['binaries'].items():
 p=root/('apps/rustusb-gui/target/release/rustusb-gui'if name=='rustusb-gui'else'target/release/'+name);assert p.stat().st_size==meta['bytes']and sha(p)==meta['sha256']
site={};changed=[]
for p in sorted((root/'website').rglob('*')):
 if p.is_file():
  n=str(p.relative_to(root));site[n]=sha(p)
  if p.read_bytes()!=git('show',s['head']+':'+n):changed.append(n)
assert changed==['website/index.html']
baseline=json.loads((root/'docs/evidence/tui-interactions-docs-20261025/summary.json').read_text())['global_skill_unchanged'];skill=Path('/home/ubuntu/.agents/skills/readme-site')
for n,meta in baseline.items():assert (skill/n).stat().st_size==meta['bytes']and sha(skill/n)==meta['sha256']
link=Path('/home/ubuntu/.codex/skills/readme-site');assert link.is_symlink()and link.resolve()==skill.resolve()
docs=['docs/frontend-recovery.md','docs/research/frontend-intent-recovery-design.md','docs/research/frontend-intent-recovery-validation.md','scripts/qemu_frontend_recovery/README.md','docs/research/settled-rule-decisions-design.md','docs/research/settled-rule-decisions-validation.md','scripts/qemu_rule_decisions/README.md','docs/research/gui-automatic-rules-design.md','docs/research/gui-automatic-rules-validation.md','scripts/qemu_gui_rules/README.md','docs/research/automatic-sharing-rust-qemu-design.md','docs/research/automatic-sharing-rust-qemu-validation.md','README.md','README.zh-CN.md','docs/automatic-sharing.md','docs/research/automatic-sharing-runtime-validation.md','docs/research/automatic-sharing-design.md','docs/research/automatic-sharing-foundation-validation.md','docs/architecture.md','docs/acceptance.md','docs/roadmap.md','docs/work-log.md','AGENTS.md','.agents/skills/rustusb-development/SKILL.md','docs/gui.md','docs/application-service-guide.md','docs/testing.md','docs/third-party.md']
targets=set()
for n in docs:
 for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)',(root/n).read_text()):
  if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:|^#',target):continue
  path=urllib.parse.unquote(target.split('#',1)[0]);p=(root/n).parent/path
  if not path:continue
  assert p.exists(),(n,target);targets.add(str(p.resolve()))
class HTML(HTMLParser):
 def __init__(self):super().__init__();self.ids=[];self.refs=[]
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if 'id'in a:self.ids.append(a['id'])
  for k in ['href','src']:
   if k in a:self.refs.append(a[k])
h=HTML();h.feed((root/'website/index.html').read_text());assert len(h.ids)==len(set(h.ids));count=0
for target in h.refs:
 u=urllib.parse.urlparse(target)
 if not u.scheme:
  assert (root/'website'/u.path).exists(),target
  if u.fragment and not u.path:assert u.fragment in h.ids,target
 elif target.startswith('https://github.com/tiansongyu/usbip-client/blob/main/'):
  assert (root/urllib.parse.unquote(u.path.split('/blob/main/',1)[1])).exists(),target
 count+=1
for name in ['summary.json','core.log','gui.log','preflight.json']:assert (out/name).read_bytes()==(Path('/tmp/rustusb-frontend-recovery-software-20261025')/name).read_bytes()
for tail,name,needle in [(['agent','--config','/etc/rustusb/agent.json','run','--help'],'agent-run-help.txt',b'--automation-decisions-policy'),(['agent','--config','/etc/rustusb/agent.json','rule','lab-server','--help'],'agent-rule-help.txt',b'--settled')]:
 r=subprocess.run([str(root/'target/release/rustusb'),*tail],capture_output=True,check=True);assert needle in r.stdout;(out/name).write_bytes(r.stdout)
for name,argv,needle in [('tui-help.txt',['target/release/rustusb','tui','--help'],b'--no-preferences'),('gui-help.txt',['apps/rustusb-gui/target/release/rustusb-gui','--help'],b'--settings-directory')]:
 r=subprocess.run(argv,cwd=root,capture_output=True,check=True);assert needle in r.stdout;(out/name).write_bytes(r.stdout)
subprocess.run(['node','--check','website/app.js'],cwd=root,check=True)
(out/'qa-method.py').write_bytes(Path(__file__).read_bytes())
qa={'result':'PASS','source_head':s['head'],'frozen_inputs_checked':len(s['inputs']),'same_gate_binaries':s['binaries'],'site_files':site,'site_changed':changed,'global_skill_unchanged':baseline,'global_skill_symlink':str(link.resolve()),'documentation_files_checked':len(docs),'local_link_targets_checked':len(targets),'site_link_asset_checks':count,'original_gate_files_exact':True,'final_qemu_runs':16,'actual_guest_exit0':31,'original_failed_runs':2,'qemu_source_head':qemu_head,'changed_inputs_since_gate':changed_inputs,'new_hardware_runs':0,'scope':'Static docs/HTML/assets, actual CLI help, source and ELF integrity. Current browser checks saved separately; actual native GUI evidence is separately archived; no new hardware claim.'}
(out/'qa.json').write_text(json.dumps(qa,indent=2)+'\n')
manifest={'files':[]}
for p in sorted(out.rglob('*')):
 if p.is_file()and p.name!='manifest.json':manifest['files'].append({'path':str(p.relative_to(out)),'bytes':p.stat().st_size,'sha256':sha(p)})
(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({k:qa[k]for k in ['result','frozen_inputs_checked','documentation_files_checked','local_link_targets_checked','site_link_asset_checks']}))
