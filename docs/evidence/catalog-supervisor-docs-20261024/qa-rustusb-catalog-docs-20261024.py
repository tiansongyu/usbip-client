import hashlib, importlib.util, json, pathlib, re, shutil, subprocess, urllib.parse
repo=pathlib.Path('/home/ubuntu/github/usbip-client')
out=pathlib.Path('/tmp/rustusb-catalog-docs-qa-20261024');out.mkdir(exist_ok=False)
files=['README.md','AGENTS.md','.agents/skills/rustusb-development/SKILL.md','docs/acceptance.md','docs/application-service-guide.md','docs/architecture.md','docs/roadmap.md','docs/security.md','docs/testing.md','docs/work-log.md','docs/research/catalog-supervisor-design.md','docs/research/catalog-supervisor-validation.md']
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(name,data):(out/name).write_text(json.dumps(data,indent=2)+'\n')
spec=importlib.util.spec_from_file_location('site_link_qa',repo/'scripts/check-site.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
checked=[];external=[]
for name in files:
    src=repo/name;dst=out/'source'/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
    for target in re.findall(r'\]\(([^)\s]+)\)',src.read_text()):
        parsed=urllib.parse.urlsplit(urllib.parse.unquote(target))
        prefix='https://github.com/tiansongyu/usbip-client/blob/main/'
        if target.startswith(prefix):parsed=urllib.parse.urlsplit(target[len(prefix):]);origin=repo/'README.md'
        else:origin=src
        if parsed.scheme or parsed.netloc:external.append({'source':name,'target':target});continue
        path=(origin.parent/parsed.path).resolve() if parsed.path else origin
        if not path.is_relative_to(repo) or not path.exists():raise RuntimeError('missing link '+name+': '+target)
        if parsed.fragment and parsed.fragment not in module.markdown_ids(path):raise RuntimeError('missing anchor '+name+': '+target)
        checked.append({'source':name,'target':target})
steps=[]
for label,argv in [('site',['python3','scripts/check-site.py']),('javascript',['node','--check','website/app.js']),('diff',['git','diff','--check'])]:
    run=subprocess.run(argv,cwd=repo,capture_output=True)
    (out/(label+'.stdout.txt')).write_bytes(run.stdout);(out/(label+'.stderr.txt')).write_bytes(run.stderr)
    steps.append({'name':label,'argv':argv,'actual_exit_code':run.returncode})
    if run.returncode:raise RuntimeError('failed '+label)
software=json.loads((repo/'docs/evidence/catalog-supervisor-b675626-20261024/software-summary.json').read_text())
assert all((repo/name).stat().st_size==item['bytes'] and sha(repo/name)==item['sha256'] for name,item in software['inputs_before'].items())
for family in ['ipv4','ipv6']:
    q=json.loads((repo/('docs/evidence/catalog-supervisor-b675626-20261024/agent-'+family+'-summary.json')).read_text())
    assert all(sha(repo/name)==item['sha256'] for name,item in q['source_inputs'].items())
site={}
for path in sorted((repo/'website').rglob('*')):
    if not path.is_file():continue
    name=str(path.relative_to(repo));original=subprocess.check_output(['git','show','30fb57a7:'+name],cwd=repo)
    assert hashlib.sha256(original).hexdigest()==sha(path)
    site[name]={'bytes':path.stat().st_size,'sha256':sha(path)}
skill=pathlib.Path('/home/ubuntu/.agents/skills/readme-site')
assert pathlib.Path('/home/ubuntu/.codex/skills/readme-site').is_symlink() and pathlib.Path('/home/ubuntu/.codex/skills/readme-site').resolve()==skill
global_files={}
for path in sorted(skill.rglob('*')):
    if not path.is_file():continue
    name=str(path.relative_to(skill));dst=out/'global-skill'/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,dst)
    global_files[name]={'bytes':path.stat().st_size,'sha256':sha(path)}
assert len(global_files)==4
save('summary.json',{'result':'PASS','scope':'Documentation/static source checks, no new browser or USB execution. External links listed, not all fetched.','local_links':len(checked),'external_links':len(external),'source_files':len(files),'commands':steps,'software_inputs_unchanged':len(software['inputs_before']),'qemu_inputs_unchanged_each':len(q['source_inputs']),'website_unchanged_since':'30fb57a7','site_files':site,'global_skill':global_files,'skill_quick_validation':{'receipt_kind':'transcription of actual earlier tool result in this turn, not a new subprocess run','actual_exit_code':0,'stdout':'Skill is valid!'},'codex_symlink_correct':True})
save('links.json',{'checked':checked,'external_not_all_fetched':external})
print((out/'summary.json').read_text())
