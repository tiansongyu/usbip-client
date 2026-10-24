import pathlib,json,hashlib,re,subprocess,importlib.util,urllib.parse,shutil
repo=pathlib.Path('/home/ubuntu/github/usbip-client');out=pathlib.Path('/tmp/rustusb-tui-interactions-docs-qa-20261025');out.mkdir(exist_ok=False)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
files=['README.md','AGENTS.md','docs/tui.md','docs/research/tui-design.md','docs/research/tui-validation.md','docs/research/tui-settings-actions-design.md','docs/research/tui-settings-actions-validation.md','docs/architecture.md','docs/acceptance.md','docs/roadmap.md','docs/testing.md','docs/compatibility.md','docs/security.md','docs/third-party.md','docs/work-log.md','docs/research/tui-failure-retry-design.md','docs/research/tui-failure-retry-validation.md','docs/research/tui-hotplug-permission-design.md','docs/research/tui-hotplug-permission-validation.md']
spec=importlib.util.spec_from_file_location('tui_interactions_links',repo/'scripts/check-site.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
links=[];external=[]
for name in files:
 src=repo/name;dest=out/'source'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dest)
 for target in re.findall(r'\]\(([^)\s]+)\)',src.read_text()):
  parsed=urllib.parse.urlsplit(urllib.parse.unquote(target));prefix='https://github.com/tiansongyu/usbip-client/blob/main/'
  if target.startswith(prefix):parsed=urllib.parse.urlsplit(target[len(prefix):]);origin=repo/'README.md'
  else:origin=src
  if parsed.scheme or parsed.netloc:external.append({'source':name,'target':target});continue
  path=(origin.parent/parsed.path).resolve()if parsed.path else origin;assert path.is_relative_to(repo)and path.exists(),(name,target)
  if parsed.fragment:assert parsed.fragment in mod.markdown_ids(path),(name,target)
  links.append({'source':name,'target':target})
commands=[]
for label,args in [('site',['python3','scripts/check-site.py']),('javascript',['node','--check','website/app.js']),('diff',['git','diff','--check']),('native-cli-tree',['cargo','tree','-p','rustusb-cli','-e','normal','--locked']),('tui-help',['target/release/rustusb','tui','--help'])]:
 p=subprocess.run(args,cwd=repo,capture_output=True);(out/(label+'.stdout.txt')).write_bytes(p.stdout);(out/(label+'.stderr.txt')).write_bytes(p.stderr);assert p.returncode==0;commands.append({'name':label,'argv':args,'exit':p.returncode})
 if label=='native-cli-tree':assert not any(s in p.stdout for s in [b'rustusb-tui',b'ratatui',b'crossterm',b'rustusb-transport'])
base=repo/'docs/evidence/tui-hotplug-permission-2dc888c-20261025';s=json.loads((base/'software-summary.json').read_text())
for n,i in s['inputs_before'].items():assert sha(repo/n)==i['sha256']
qs={}
for p in base.glob('*-summary.json'):
 if p.name in ['software-summary.json','build-selection-summary.json'] or '-red-' in p.name:continue
 q=json.loads(p.read_text());assert q['result']=='PASS'
 for n,i in q['source_inputs'].items():assert sha(repo/n)==i['sha256']
 qs[p.name]=len(q['source_inputs'])
assert len(qs)==5
prior=json.loads((repo/'docs/evidence/tui-failure-docs-20261025/summary.json').read_text());skill=pathlib.Path('/home/ubuntu/.agents/skills/readme-site')
for n,i in prior['global_skill_unchanged'].items():assert sha(skill/n)==i['sha256']
link=pathlib.Path('/home/ubuntu/.codex/skills/readme-site');assert link.is_symlink()and link.resolve()==skill
for n,i in prior['site_files'].items():assert sha(repo/n)==i['sha256']
for n,i in next(c['binaries']for c in s['commands']if c['name']=='release-all').items():assert sha(repo/'target/release'/n)==i['sha256']
summary={'result':'PASS','documents':len(files),'local_links':len(links),'external_not_all_fetched':external,'commands':commands,'software_inputs':len(s['inputs_before']),'qemu_inputs':qs,'site_files':prior['site_files'],'global_skill_unchanged':prior['global_skill_unchanged'],'scope':'Local/source checks only. Seven site/four global skill files and discovery symlink unchanged. No new browser/pixel/no-JS/reduced-motion check; E45 screenshot limitation retained. Actual CLI help captured with exit0. Sources and all release ELF unchanged.'}
(out/'links.json').write_text(json.dumps(links,indent=2)+'\n');(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
target=repo/'docs/evidence/tui-interactions-docs-20261025';shutil.copytree(out,target);shutil.copyfile(__file__,target/pathlib.Path(__file__).name)
entries=[{'path':str(p.relative_to(target)),'bytes':p.stat().st_size,'sha256':sha(p)}for p in sorted(target.rglob('*'))if p.is_file()]
(target/'manifest.json').write_text(json.dumps({'files':entries,'scope':summary['scope']},indent=2)+'\n')
print(json.dumps({k:summary[k]for k in ['result','documents','local_links','software_inputs','qemu_inputs']}))
