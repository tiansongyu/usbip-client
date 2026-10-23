from pathlib import Path
import subprocess,json,hashlib,datetime,os
root=Path('/tmp/rustusb-prepare-stop-local-red-checkout-20261024')
out=Path('/tmp/rustusb-prepare-reserved-local-red-20261024');out.mkdir(exist_ok=False)
names=[r['path'] for r in json.loads(Path('/tmp/rustusb-prepare-stop-software-first-20261024/inputs-before.json').read_text())]
def inputs():
    return [dict(path=n,bytes=(root/n).stat().st_size,sha256=hashlib.sha256((root/n).read_bytes()).hexdigest()) for n in names]
before=inputs();(out/'inputs-before.json').write_text(json.dumps(before,indent=2)+'\n')
for n in names:
    p=out/'source'/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((root/n).read_bytes())
base=['cargo','test','-p','rustusb-service','--all-features','--locked']
commands=[base+['jobs::native::prepare_tests::reserved_unsent_local_stop_retains_uncertainty_without_stopping_sibling','--','--exact','--nocapture']]
(out/'start.json').write_text(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),status=subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True),commands=commands,scope='The actual655 candidate product source plus five new assertions, this run executes only reserved-unsent local cancellation. Private production stages and owned Unix/real record oneshots; no root/helper/kernel/QEMU proof.'),indent=2)+'\n')
results=[]
for i,cmd in enumerate(commands):
    log=out/f'{i:02d}.log'
    with log.open('wb') as f:r=subprocess.run(cmd,cwd=root,env=dict(os.environ,CARGO_TARGET_DIR='/home/ubuntu/github/usbip-client/target'),stdout=f,stderr=subprocess.STDOUT)
    results.append(dict(command=cmd,exit_code=r.returncode,log=log.name));print(i,'exit',r.returncode,flush=True)
    print('\n'.join(log.read_text().splitlines()[-26:]),flush=True)
    if 'error: could not compile' in log.read_text():break
after=inputs();(out/'inputs-after.json').write_text(json.dumps(after,indent=2)+'\n')
(out/'result.json').write_text(json.dumps(dict(commands=results,inputs_stable=before==after,selected_inputs=len(after)),indent=2)+'\n')
print('stable',before==after,'selected',len(after),flush=True)
