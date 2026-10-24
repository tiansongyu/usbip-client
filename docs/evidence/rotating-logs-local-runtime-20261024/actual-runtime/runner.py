from pathlib import Path
import json,subprocess,hashlib,datetime,sys
root=Path('/tmp/rustusb-logging-runtime-checkout-20261024');out=Path('/tmp/rustusb-logging-local-runtime-first-20261024');out.mkdir(exist_ok=False)
head='f3cf8d4f13077cbcbb48c9e3e27918b87ca31a0d';cli=Path('/home/ubuntu/github/usbip-client/target/release/rustusb');expected='6cc828f3769d1ebd4c2e7b566707b7788f107151bc21155843c315a818b8b474'
def git(*args):return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
selected=json.loads(Path('/tmp/rustusb-logging-software-second-20261024/inputs-after.json').read_text())
def inputs():return [dict(path=r['path'],bytes=(root/r['path']).stat().st_size,sha256=hashlib.sha256((root/r['path']).read_bytes()).hexdigest()) for r in selected]
def save(n,d):(out/n).write_text(json.dumps(d,indent=2)+'\n')
assert git('rev-parse','HEAD')==head and not git('status','--porcelain') and inputs()==selected
assert hashlib.sha256(cli.read_bytes()).hexdigest()==expected
save('inputs-before.json',inputs());save('start.json',dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),head=head,clean=True,scope='Clean productcheckpoint uses actual external all-features releaseCLI from dirty48a4 complete723 run; no rebuild. New6 logging cases and original4signal/2backpressure separate scopes.'))
(out/'runner.py').write_bytes(Path(__file__).read_bytes())
commands=[('process-logging',['python3','-B','scripts/test-process-logging.py','--binary',str(cli),'--evidence',str(out/'logging')]),('signals',['python3','-B','scripts/test-server-signals.py','--binary',str(cli),'--skip-build','--evidence',str(out/'signals')]),('backpressure',['python3','-B','scripts/test-virtual-backpressure.py','--binary',str(cli)])]
results=[]
for name,cmd in commands:
 print(json.dumps(dict(start=name)),flush=True)
 with (out/(name+'.log')).open('wb') as f:p=subprocess.run(cmd,cwd=root,stdout=f,stderr=subprocess.STDOUT)
 row=dict(name=name,command=cmd,exit_code=p.returncode,inputs_stable=inputs()==selected,binary_stable=hashlib.sha256(cli.read_bytes()).hexdigest()==expected,head_guard=git('rev-parse','HEAD')==head,clean_guard=not git('status','--porcelain'))
 results.append(row);save('results.json',results);print(json.dumps(row),flush=True)
 if p.returncode or not all(row[k] for k in ['inputs_stable','binary_stable','head_guard','clean_guard']):break
save('inputs-after.json',inputs());passed=len(results)==len(commands) and all(r['exit_code']==0 and all(r[k] for k in ['inputs_stable','binary_stable','head_guard','clean_guard']) for r in results)
save('result.json',dict(result='PASS' if passed else 'FAIL',head=head,cli_sha256=expected,commands=results,remaining_unrun=[n for n,_ in commands[len(results):]],inputs_stable=inputs()==selected))
sys.exit(0 if passed else 1)
