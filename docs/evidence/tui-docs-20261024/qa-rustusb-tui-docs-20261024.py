import pathlib,json,hashlib,re,subprocess,importlib.util,urllib.parse,shutil,os,signal
repo=pathlib.Path('/home/ubuntu/github/usbip-client');out=pathlib.Path('/tmp/rustusb-tui-docs-qa-20261024');out.mkdir(exist_ok=False)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
files=['README.md','AGENTS.md','docs/tui.md','docs/research/tui-design.md','docs/research/tui-validation.md','docs/architecture.md','docs/acceptance.md','docs/roadmap.md','docs/testing.md','docs/compatibility.md','docs/security.md','docs/third-party.md','docs/work-log.md','scripts/qemu_tui/README.md']
spec=importlib.util.spec_from_file_location('site_checks',repo/'scripts/check-site.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
links=[];external=[]
for name in files:
 src=repo/name;dest=out/'source'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dest)
 for target in re.findall(r'\]\(([^)\s]+)\)',src.read_text()):
  parsed=urllib.parse.urlsplit(urllib.parse.unquote(target));prefix='https://github.com/tiansongyu/usbip-client/blob/main/'
  if target.startswith(prefix):parsed=urllib.parse.urlsplit(target[len(prefix):]);origin=repo/'README.md'
  else:origin=src
  if parsed.scheme or parsed.netloc:external.append({'source':name,'target':target});continue
  path=(origin.parent/parsed.path).resolve()if parsed.path else origin
  assert path.is_relative_to(repo)and path.exists(),(name,target)
  if parsed.fragment:assert parsed.fragment in mod.markdown_ids(path),(name,target)
  links.append({'source':name,'target':target})
commands=[]
for label,args in [('site',['python3','scripts/check-site.py']),('javascript',['node','--check','website/app.js']),('diff',['git','diff','--check'])]:
 p=subprocess.run(args,cwd=repo,capture_output=True);(out/(label+'.stdout.txt')).write_bytes(p.stdout);(out/(label+'.stderr.txt')).write_bytes(p.stderr);assert p.returncode==0;commands.append({'name':label,'argv':args,'exit':p.returncode})
base=repo/'docs/evidence/tui-d181665-20261024'
s=json.loads((base/'software-summary.json').read_text())
for n,i in s['inputs_before'].items():assert sha(repo/n)==i['sha256']
qcounts={}
for p in base.glob('*second*-summary.json'):
 q=json.loads(p.read_text());assert q['result']=='PASS'
 for n,i in q['source_inputs'].items():assert sha(repo/n)==i['sha256']
 qcounts[p.name]=len(q['source_inputs'])
prior=json.loads((repo/'docs/evidence/offline-crl-docs-20261024/summary.json').read_text())
skill=pathlib.Path('/home/ubuntu/.agents/skills/readme-site');expected=prior['global_skill_unchanged']
for n,i in expected.items():assert sha(skill/n)==i['sha256']
link=pathlib.Path('/home/ubuntu/.codex/skills/readme-site');assert link.is_symlink()and link.resolve()==skill
site={str(p.relative_to(repo)):{'bytes':p.stat().st_size,'sha256':sha(p)}for p in (repo/'website').rglob('*')if p.is_file()}
changes=[n for n,i in site.items()if n not in prior['site_files']or i['sha256']!=prior['site_files'][n]['sha256']];assert changes==['website/index.html']
# The content change is exactly one fact/link in initial HTML. No CSS/JS behavior changes.
old=subprocess.check_output(['git','show','d181665b:website/index.html'],cwd=repo).decode()
assert (repo/'website/index.html').read_text()==old.replace('Storage sharing, ISO, TUI, GUI and Windows USB support remain planned or unvalidated.','An optional <a href="https://github.com/tiansongyu/usbip-client/blob/main/docs/tui.md">terminal preview</a> is available. Storage sharing, ISO, complete TUI acceptance, GUI and Windows USB support remain planned or unvalidated.')
browser=pathlib.Path('/tmp/rustusb-tui-browser-qa-20261024')
(browser/'scope.txt').write_text('CUA IAB tab27: initial root/subpath accessibility tree and terminal preview href observed. Status anchor clicked. Direct screenshot API and getScreenshot both returned Unable to capture screenshot. Viewport temporarily390x844; DOM geometry document375 <=viewport390; correct link visible in DOM. Reset viewport and closed owned tab. No pixel, no-JS or reduced-motion runtime claim. Terminal PNG separately generated from actual PTY and view_image inspected.\n')
shutil.copytree(browser,out/'browser')
summary={'result':'PASS','documents':len(files),'local_links':len(links),'external_links_not_all_fetched':external,'commands':commands,'software_inputs':len(s['inputs_before']),'qemu_inputs':qcounts,'site_files':site,'site_changed':changes,'global_skill_unchanged':expected,'codex_link':str(link),'browser_scope':(browser/'scope.txt').read_text()}
(out/'links.json').write_text(json.dumps(links,indent=2)+'\n');(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
target=repo/'docs/evidence/tui-docs-20261024';shutil.copytree(out,target);shutil.copyfile(__file__,target/pathlib.Path(__file__).name)
entries=[{'path':str(p.relative_to(target)),'bytes':p.stat().st_size,'sha256':sha(p)}for p in sorted(target.rglob('*'))if p.is_file()]
(target/'manifest.json').write_text(json.dumps({'files':entries,'scope':summary['browser_scope']},indent=2)+'\n')
print(json.dumps({k:summary[k]for k in ['result','documents','local_links','software_inputs','qemu_inputs','site_changed']}))
# Stop only this task's exact loopback preview command, after closing its owned tab.
for line in subprocess.check_output(['ps','-eo','pid=,args='],text=True).splitlines():
 pid,cmd=line.strip().split(None,1)
 if cmd=='python3 -m http.server 4971 --bind 127.0.0.1 --directory /tmp/rustusb-tui-site-preview-20261024':os.kill(int(pid),signal.SIGTERM)
