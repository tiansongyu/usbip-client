import hashlib,importlib.util,json,pathlib,re,shutil,subprocess,urllib.parse
repo=pathlib.Path('/home/ubuntu/github/usbip-client');out=pathlib.Path('/tmp/rustusb-offline-crl-docs-qa-20261024');out.mkdir(exist_ok=False)
files=['README.md','AGENTS.md','.agents/skills/rustusb-development/SKILL.md','docs/acceptance.md','docs/architecture.md','docs/application-service-guide.md','docs/compatibility.md','docs/offline-crl.md','docs/research/offline-crl-design.md','docs/research/offline-crl-validation.md','docs/roadmap.md','docs/security.md','docs/secure-guide.md','docs/testing.md','docs/third-party.md','docs/website.md','docs/work-log.md']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(n,v):(out/n).write_text(json.dumps(v,indent=2)+'\n')
spec=importlib.util.spec_from_file_location('crl_links',repo/'scripts/check-site.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
links=[];external=[]
for name in files:
 src=repo/name;dst=out/'source'/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
 for target in re.findall(r'\]\(([^)\s]+)\)',src.read_text()):
  parsed=urllib.parse.urlsplit(urllib.parse.unquote(target));prefix='https://github.com/tiansongyu/usbip-client/blob/main/'
  if target.startswith(prefix):parsed=urllib.parse.urlsplit(target[len(prefix):]);origin=repo/'README.md'
  else:origin=src
  if parsed.scheme or parsed.netloc:external.append({'source':name,'target':target});continue
  path=(origin.parent/parsed.path).resolve() if parsed.path else origin
  assert path.is_relative_to(repo) and path.exists(),(name,target)
  if parsed.fragment:assert parsed.fragment in mod.markdown_ids(path),(name,target)
  links.append({'source':name,'target':target})
commands=[]
for name,args in [('site',['python3','scripts/check-site.py']),('javascript',['node','--check','website/app.js']),('diff',['git','diff','--check'])]:
 p=subprocess.run(args,cwd=repo,capture_output=True);(out/(name+'.stdout.txt')).write_bytes(p.stdout);(out/(name+'.stderr.txt')).write_bytes(p.stderr);commands.append({'name':name,'argv':args,'actual_exit_code':p.returncode});assert p.returncode==0
base=repo/'docs/evidence/offline-crl-e441bb8-20261024';s=json.loads((base/'software-summary.json').read_text())
for name,item in s['inputs_before'].items():assert sha(repo/name)==item['sha256'] and (repo/name).stat().st_size==item['bytes'],name
qcounts={}
for p in base.glob('*-summary.json'):
 if p.name.startswith(('software','prior')):continue
 d=json.loads(p.read_text())
 for name,item in d['source_inputs'].items():assert sha(repo/name)==item['sha256'],name
 qcounts[p.name]=len(d['source_inputs'])
assert len(qcounts)==13
site={}
for p in sorted((repo/'website').rglob('*')):
 if p.is_file():
  name=str(p.relative_to(repo));assert hashlib.sha256(subprocess.check_output(['git','show','e6177063:'+name],cwd=repo)).hexdigest()==sha(p),name
  site[name]={'bytes':p.stat().st_size,'sha256':sha(p)}
skill=pathlib.Path('/home/ubuntu/.agents/skills/readme-site');expected=json.loads((repo/'docs/evidence/remote-catalog-watch-docs-20261024/final/summary.json').read_text())['global_skill']
for name,item in expected.items():assert sha(skill/name)==item['sha256'],name
link=pathlib.Path('/home/ubuntu/.codex/skills/readme-site');assert link.is_symlink() and link.resolve()==skill
save('links.json',{'local_checked':links,'external_not_all_refetched':external})
save('summary.json',{'result':'PASS','scope':'Documentation/static and byte comparison only; no new browser/pixel or USB execution. Snapshots precede later receipts.','source_files':len(files),'local_links':len(links),'external_links':len(external),'commands':commands,'software_inputs_unchanged':len(s['inputs_before']),'qemu_inputs_unchanged':qcounts,'site_unchanged_since':'e6177063','site_files':site,'global_skill_unchanged':expected,'codex_symlink_correct':True,'global_quick_validation':'Not repeated; four files unchanged from previous validated installation.'})
print((out/'summary.json').read_text())
