from pathlib import Path
import subprocess, json, hashlib, datetime, sys
repo=Path('/home/ubuntu/github/usbip-client')
def git(*args): return subprocess.check_output(['git',*args],cwd=repo,text=True).strip()
head=git('rev-parse','HEAD'); assert not git('status','--porcelain')
base=Path('/tmp')/f'rustusb-autorestore-{head[:7]}-20261023';base.mkdir(exist_ok=False)
paths=[p for p in git('ls-files').splitlines() if p in ['Cargo.toml','Cargo.lock'] or p.startswith(('crates/','vendor/','scripts/','.github/workflows/'))]
def fingerprint():return {p:{'bytes':(repo/p).stat().st_size,'sha256':hashlib.sha256((repo/p).read_bytes()).hexdigest()} for p in paths}
freeze=fingerprint();(base/'freeze.json').write_text(json.dumps({'head':head,'working_tree_dirty':False,'files':freeze},indent=2)+'\n')
commands=[
 ('owner-death',['python3','scripts/qemu/run-autorestore-qemu.py','--scenario','owner-death','--evidence',str(base/'owner-death')]),
 ('helper-restart',['python3','scripts/qemu/run-autorestore-qemu.py','--scenario','helper-restart','--evidence',str(base/'helper-restart')]),
 ('journal-default',['python3','scripts/qemu/run-journal-qemu.py','--evidence',str(base/'journal-default')]),
 ('physical-normal',['python3','scripts/qemu/run-physical-qemu.py','--evidence',str(base/'physical-normal')]),
 ('physical-hot-unplug',['python3','scripts/qemu/run-physical-qemu.py','--hot-unplug','--evidence',str(base/'physical-hot-unplug')]),
 ('physical-composite',['python3','scripts/qemu/run-composite-qemu.py','--evidence',str(base/'physical-composite')]),
 ('physical-sigterm',['python3','scripts/qemu/run-sigterm-qemu.py','--evidence',str(base/'physical-sigterm')]),
 ('local-signals',['python3','scripts/test-server-signals.py','--evidence',str(base/'local-signals')]),
 ('local-backpressure',['python3','scripts/test-virtual-backpressure.py']),
]
rows=[]
print(json.dumps({'state':'STARTED','head':head,'base':str(base),'frozen_inputs':len(freeze)}),flush=True)
for name,command in commands:
 assert fingerprint()==freeze and git('rev-parse','HEAD')==head and not git('status','--porcelain')
 started=datetime.datetime.now(datetime.timezone.utc).isoformat()
 print(json.dumps({'state':'RUNNING','name':name,'command':command}),flush=True)
 with (base/f'{name}-runner.log').open('wb') as log: result=subprocess.run(command,cwd=repo,stdout=log,stderr=subprocess.STDOUT)
 row={'name':name,'head':head,'command':command,'started_at':started,'finished_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'exit_code':result.returncode,'source_guard':fingerprint()==freeze,'head_guard':git('rev-parse','HEAD')==head,'worktree_clean':not git('status','--porcelain')}
 row['result']='PASS' if result.returncode==0 and all(row[k] for k in ['source_guard','head_guard','worktree_clean']) else 'FAIL';rows.append(row)
 (base/'sequence.json').write_text(json.dumps({'head':head,'runs':rows},indent=2)+'\n'); print(json.dumps(row),flush=True)
 if row['result']!='PASS':sys.exit(1)
print(json.dumps({'state':'COMPLETE','result':'PASS','runs':len(rows),'base':str(base)}),flush=True)
