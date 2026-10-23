from pathlib import Path
import subprocess,json,hashlib,datetime,sys
repo=Path('/home/ubuntu/github/usbip-client'); freeze=json.loads(Path('/tmp/rustusb-sigterm-fixed-freeze-934358f-20261023.json').read_text())
base=Path('/tmp/rustusb-sigterm-fixed-qemu-934358f-20261023');base.mkdir()
checks=[('sigterm',['scripts/qemu/run-sigterm-qemu.py']),('virtual',['scripts/qemu/run-userspace-qemu.py']),('physical-normal',['scripts/qemu/run-physical-qemu.py']),('physical-hot',['scripts/qemu/run-physical-qemu.py','--hot-unplug'])]
result={'schema_version':1,'result':'FAIL','git_commit':freeze['git_commit'],'working_tree_clean':True,'runs':[],'remaining':[x[0] for x in checks]}
def guard():
 assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()==freeze['git_commit']
 assert subprocess.check_output(['git','status','--porcelain'],cwd=repo)==b''
 for row in freeze['files']:assert hashlib.sha256((repo/row['path']).read_bytes()).hexdigest()==row['sha256'],row['path']
def persist():(base/'sequence.json').write_text(json.dumps(result,indent=2)+'\n')
persist()
try:
 for name,args in checks:
  guard();evidence=base/name;command=['python3',*args,'--evidence',str(evidence)];started=datetime.datetime.now(datetime.timezone.utc).isoformat()
  print('START',name,started,flush=True)
  with (base/(name+'-runner.log')).open('wb') as log:code=subprocess.run(command,cwd=repo,stdout=log,stderr=subprocess.STDOUT).returncode
  guard();path=evidence/'summary.json';summary=json.loads(path.read_text()) if path.exists() else {}
  result['runs'].append({'name':name,'command':command,'started_utc':started,'exit_code':code,'result':summary.get('result'),'summary_sha256':hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None});result['remaining'].remove(name);persist()
  print('END',name,code,summary.get('result'),flush=True)
  if code or summary.get('result')!='PASS':raise RuntimeError('original failed run retained: '+name)
 result['result']='PASS';result['source_after_guard_passed']=True
except Exception as error:result['error']=str(error)
finally:persist()
sys.exit(0 if result['result']=='PASS' else 1)
