from pathlib import Path
import subprocess,json,hashlib,datetime,sys
repo=Path('/home/ubuntu/github/usbip-client')
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
base=Path('/tmp')/f'rustusb-journal-final-{head[:7]}-20261023'
base.mkdir(exist_ok=False)
paths=[p for p in subprocess.check_output(['git','ls-files'],cwd=repo,text=True).splitlines() if p in ['Cargo.toml','Cargo.lock'] or p.startswith(('crates/','vendor/','scripts/','.github/workflows/'))]
def fingerprint():return {p:{'sha256':hashlib.sha256((repo/p).read_bytes()).hexdigest(),'bytes':(repo/p).stat().st_size} for p in paths}
freeze=fingerprint()
assert not subprocess.check_output(['git','status','--porcelain'],cwd=repo)
(base/'freeze.json').write_text(json.dumps({'head':head,'working_tree_dirty':False,'files':freeze},indent=2)+'\n')
commands=[('journal',['python3','scripts/qemu/run-journal-qemu.py','--evidence',str(base/'journal')]),('physical-normal',['python3','scripts/qemu/run-physical-qemu.py','--evidence',str(base/'physical-normal')]),('physical-hot',['python3','scripts/qemu/run-physical-qemu.py','--hot-unplug','--evidence',str(base/'physical-hot')]),('composite',['python3','scripts/qemu/run-composite-qemu.py','--evidence',str(base/'composite')]),('sigterm',['python3','scripts/qemu/run-sigterm-qemu.py','--evidence',str(base/'sigterm')]),('local-signals',['python3','scripts/test-server-signals.py','--evidence',str(base/'local-signals')]),('backpressure',['python3','scripts/test-virtual-backpressure.py'])]
sequence={'head':head,'base':str(base),'result':'RUNNING','runs':[]}
print(json.dumps(sequence),flush=True)
for name,command in commands:
 assert fingerprint()==freeze
 assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()==head
 assert not subprocess.check_output(['git','status','--porcelain'],cwd=repo)
 started=datetime.datetime.now(datetime.timezone.utc).isoformat()
 with (base/f'{name}-runner.log').open('wb') as log:result=subprocess.run(command,cwd=repo,stdout=log,stderr=subprocess.STDOUT)
 row={'name':name,'command':command,'started_at':started,'finished_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'exit_code':result.returncode,'sources_unchanged':fingerprint()==freeze,'head_unchanged':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()==head,'worktree_clean':not subprocess.check_output(['git','status','--porcelain'],cwd=repo)}
 sequence['runs'].append(row)
 (base/'sequence.json').write_text(json.dumps(sequence,indent=2)+'\n')
 print(json.dumps(row),flush=True)
 if result.returncode or not all(row[k] for k in ['sources_unchanged','head_unchanged','worktree_clean']):sequence['result']='FAIL';break
else:sequence['result']='PASS'
(base/'sequence.json').write_text(json.dumps(sequence,indent=2)+'\n')
print(json.dumps({'result':sequence['result'],'base':str(base),'completed_runs':len(sequence['runs'])}),flush=True)
sys.exit(0 if sequence['result']=='PASS' else 1)
