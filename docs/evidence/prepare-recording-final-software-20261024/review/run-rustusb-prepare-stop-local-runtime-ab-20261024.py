from pathlib import Path
import subprocess,json,hashlib,datetime
root=Path('/home/ubuntu/github/usbip-client')
out=Path('/tmp/rustusb-prepare-stop-local-runtime-ab-20261024');out.mkdir(exist_ok=False)
selected=json.loads(Path('/tmp/rustusb-prepare-stop-software-ab-first-20261024/inputs-after.json').read_text())
def inputs():
    return [dict(path=r['path'],bytes=(root/r['path']).stat().st_size,sha256=hashlib.sha256((root/r['path']).read_bytes()).hexdigest()) for r in selected]
before=inputs();(out/'inputs-before.json').write_text(json.dumps(before,indent=2)+'\n')
cli=root/'target/release/rustusb';sha=hashlib.sha256(cli.read_bytes()).hexdigest();assert sha=='72adb291645fdcf3cb41c1ff9e9e781fe9eed344730a4444407f983c9b63e5f2'
commands=[('signals.log',['python3','scripts/test-server-signals.py','--binary',str(cli),'--skip-build','--evidence',str(out/'signals')]),('backpressure.log',['python3','scripts/test-virtual-backpressure.py','--binary',str(cli)])]
results=[]
for log,cmd in commands:
    with (out/log).open('wb') as f:r=subprocess.run(cmd,cwd=root,stdout=f,stderr=subprocess.STDOUT)
    results.append(dict(command=cmd,exit_code=r.returncode,log=log))
    if r.returncode:break
after=inputs();(out/'inputs-after.json').write_text(json.dumps(after,indent=2)+'\n')
result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),result='PASS' if len(results)==2 and all(r['exit_code']==0 for r in results) else 'FAIL',commands=results,cli_sha256=sha,binary_stable=hashlib.sha256(cli.read_bytes()).hexdigest()==sha,inputs_stable=before==after,selected_inputs=len(after),scope='Actual all-features release CLI from the complete667-execution run. Ordinary-user original four signals/two stdout-backpressure; no host USB/kernel mutation.')
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
