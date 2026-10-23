from pathlib import Path
import subprocess,json,hashlib,datetime,sys
repo=Path('/home/ubuntu/github/usbip-client')
legacy_manifest=Path('/tmp/rustusb-legacy-producer-build-933e720-20261023/manifest.json')
legacy=json.loads(legacy_manifest.read_text());legacy_repo=Path(legacy['worktree']);legacy_binary=Path(legacy['binary']['path'])
def git(root,*args):return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
head=git(repo,'rev-parse','HEAD');assert not git(repo,'status','--porcelain')
base=Path('/tmp')/f'rustusb-autorestore-negative-{head[:7]}-20261023';base.mkdir(exist_ok=False)
paths=[p for p in git(repo,'ls-files').splitlines() if p in ['Cargo.toml','Cargo.lock'] or p.startswith(('crates/','vendor/','scripts/','.github/workflows/'))]
def fp(root,paths):return {p:{'bytes':(root/p).stat().st_size,'sha256':hashlib.sha256((root/p).read_bytes()).hexdigest()} for p in paths}
def legacy_guard():
 data=legacy_binary.read_bytes()
 return git(legacy_repo,'rev-parse','HEAD')==legacy['git_commit'] and not git(legacy_repo,'status','--porcelain') and fp(legacy_repo,legacy['files'])==legacy['files'] and len(data)==legacy['binary']['bytes'] and hashlib.sha256(data).hexdigest()==legacy['binary']['sha256']
assert legacy_guard()
freeze=fp(repo,paths);(base/'freeze.json').write_text(json.dumps({'head':head,'working_tree_dirty':False,'files':freeze,'legacy_manifest':legacy},indent=2)+'\n')
commands=[]
for scenario in ['legacy-schema1','policy-revoked','external-claim']:
 command=['python3','scripts/qemu/run-autorestore-negative-qemu.py','--scenario',scenario,'--evidence',str(base/scenario)]
 if scenario=='legacy-schema1':command+=['--legacy-helper',str(legacy_binary),'--legacy-manifest',str(legacy_manifest)]
 commands.append((scenario,command))
rows=[];print(json.dumps({'state':'STARTED','head':head,'base':str(base),'frozen_inputs':len(freeze),'legacy_binary_sha256':legacy['binary']['sha256']}),flush=True)
for name,command in commands:
 assert fp(repo,paths)==freeze and git(repo,'rev-parse','HEAD')==head and not git(repo,'status','--porcelain') and legacy_guard()
 started=datetime.datetime.now(datetime.timezone.utc).isoformat();print(json.dumps({'state':'RUNNING','name':name,'command':command}),flush=True)
 with (base/f'{name}-runner.log').open('wb') as log:result=subprocess.run(command,cwd=repo,stdout=log,stderr=subprocess.STDOUT)
 row={'name':name,'head':head,'command':command,'started_at_utc':started,'finished_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'exit_code':result.returncode,'source_guard':fp(repo,paths)==freeze,'head_guard':git(repo,'rev-parse','HEAD')==head,'worktree_clean':not git(repo,'status','--porcelain'),'legacy_guard':legacy_guard()}
 row['result']='PASS' if result.returncode==0 and all(row[k] for k in ['source_guard','head_guard','worktree_clean','legacy_guard']) else 'FAIL';rows.append(row)
 (base/'sequence.json').write_text(json.dumps({'head':head,'runs':rows},indent=2)+'\n');print(json.dumps(row),flush=True)
 if row['result']!='PASS':sys.exit(1)
print(json.dumps({'state':'COMPLETE','result':'PASS','runs':len(rows),'base':str(base)}),flush=True)
