import datetime,hashlib,json,pathlib,re,subprocess,sys
root=pathlib.Path('/home/ubuntu/github/usbip-client')
out=pathlib.Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False)
def inputs():
    paths=[root/'Cargo.toml',root/'Cargo.lock',root/'rust-toolchain.toml',root/'scripts/check.sh']
    for parent in [root/'crates',root/'vendor/nusb']:
        paths.extend(parent.rglob('*.rs'))
        paths.extend(parent.rglob('Cargo.toml'))
        paths.extend(parent.rglob('Cargo.lock'))
    return [{'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(set(paths)) if p.is_file() and 'target' not in p.parts]
def save(name,value): (out/name).write_text(json.dumps(value,indent=2)+'\n')
before=inputs();save('inputs-before.json',before)
save('start.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'command':['bash','-x','scripts/check.sh'],'selected_input_count':len(before),'worktree_clean':not subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip()})
with (out/'check.log').open('wb') as log: result=subprocess.run(['bash','-x','scripts/check.sh'],cwd=root,stdout=log,stderr=subprocess.STDOUT)
after=inputs();save('inputs-after.json',after)
log=(out/'check.log').read_text();groups={};stage='unknown'
for line in log.splitlines():
    if line.startswith('+ cargo test '): stage='vendor' if '--manifest-path' in line else ('all_features' if '--all-features' in line else 'default')
    match=re.search(r'test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored;',line)
    if match:
        group=groups.setdefault(stage,{'passed':0,'failed':0,'ignored':0,'results':0})
        for key,value in zip(['passed','failed','ignored'],match.groups()[1:]):group[key]+=int(value)
        group['results']+=1
summary={'exit_code':result.returncode,'inputs_unchanged':before==after,'input_count':len(before),'tests':groups,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
save('result.json',summary);print(json.dumps(summary),flush=True)
sys.exit(0 if result.returncode==0 and before==after else 1)
