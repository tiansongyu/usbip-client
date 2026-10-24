from pathlib import Path
import subprocess,json,hashlib,datetime
root=Path('/home/ubuntu/github/usbip-client')
out=Path('/tmp/rustusb-prepare-stop-software-ab-first-20261024');out.mkdir(exist_ok=False)
old=json.loads(Path('/tmp/rustusb-data-link-management-software-first-20261024/inputs-before.json').read_text())
selected={r['path'] for r in old}
selected.update(str(p.relative_to(root)) for p in (root/'crates').rglob('*.rs'))
selected=sorted(selected)
def inputs():
    return [dict(path=n,bytes=(root/n).stat().st_size,sha256=hashlib.sha256((root/n).read_bytes()).hexdigest()) for n in selected]
before=inputs();(out/'inputs-before.json').write_text(json.dumps(before,indent=2)+'\n')
for n in selected:
    p=out/'source'/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((root/n).read_bytes())
(out/'start.json').write_text(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),status=subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True),command=['bash','-x','scripts/check.sh'],scope='Actual dirty primary Native A/B revision; later individual Git input mapping must not relabel this clean. Previous655 candidate remains separately attributed.'),indent=2)+'\n')
commands=[('full-gate.log',['bash','-x','scripts/check.sh']),('native-only-check.log',['cargo','check','-p','rustusb-cli','--no-default-features','--features','secure-transport','--locked']),('native-only-tree.log',['cargo','tree','-p','rustusb-cli','--no-default-features','--features','secure-transport','--edges','normal','--locked']),('rustc.log',['rustc','--version','--verbose']),('cargo.log',['cargo','--version','--verbose']),('cli-help.log',['target/release/rustusb','--help']),('helper-help.log',['target/release/rustusb-helper','--help'])]
results=[]
for log,cmd in commands:
    with (out/log).open('wb') as f:r=subprocess.run(cmd,cwd=root,stdout=f,stderr=subprocess.STDOUT)
    results.append(dict(command=cmd,exit_code=r.returncode,log=log));print(log,'exit',r.returncode,flush=True)
    if r.returncode:
        print('\n'.join((out/log).read_text().splitlines()[-45:]),flush=True);break
after=inputs();(out/'inputs-after.json').write_text(json.dumps(after,indent=2)+'\n')
artifacts=[]
if len(results)==len(commands) and all(r['exit_code']==0 for r in results):
    for n in ['rustusb','rustusb-helper']:
        p=root/'target/release'/n;b=p.read_bytes();s=p.stat();artifacts.append(dict(path=str(p),bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),dev=s.st_dev,inode=s.st_ino,mtime_ns=s.st_mtime_ns))
(out/'result.json').write_text(json.dumps(dict(result='PASS' if artifacts else 'FAIL',commands=results,inputs_stable=before==after,selected_inputs=len(after),artifacts=artifacts),indent=2)+'\n')
print('selected',len(after),'stable',before==after,flush=True)
