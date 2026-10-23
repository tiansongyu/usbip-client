from pathlib import Path
import subprocess,json,hashlib,os,datetime
root=Path('/home/ubuntu/github/usbip-client')
out=Path('/tmp/rustusb-prepare-stop-service-first-20261024');out.mkdir(exist_ok=False)
original=json.loads(Path('/tmp/rustusb-data-link-management-software-first-20261024/inputs-before.json').read_text())
selected={r['path'] for r in original}
selected.update(str(p.relative_to(root)) for p in (root/'crates').rglob('*.rs'))
selected=sorted(selected)
def inputs():
    return [dict(path=n,bytes=(root/n).stat().st_size,sha256=hashlib.sha256((root/n).read_bytes()).hexdigest()) for n in selected]
before=inputs();(out/'inputs-before.json').write_text(json.dumps(before,indent=2)+'\n')
for n in selected:
    if n.startswith('crates/rustusb-service/'):
        p=out/'source'/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((root/n).read_bytes())
commands=[['cargo','fmt','--all','--','--check'],['cargo','test','-p','rustusb-service','--locked'],['cargo','test','-p','rustusb-service','--all-features','--locked'],['cargo','clippy','-p','rustusb-service','--all-targets','--locked','--','-D','warnings'],['cargo','clippy','-p','rustusb-service','--all-targets','--all-features','--locked','--','-D','warnings']]
(out/'start.json').write_text(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),status=subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True),commands=commands,scope='First complete service default/all-features development gate; not whole workspace/release/QEMU acceptance.'),indent=2)+'\n')
results=[]
for i,cmd in enumerate(commands):
    log=f'{i:02d}.log'
    with (out/log).open('wb') as f:r=subprocess.run(cmd,cwd=root,stdout=f,stderr=subprocess.STDOUT)
    results.append(dict(command=cmd,exit_code=r.returncode,log=log));print(i,'exit',r.returncode,flush=True)
    if r.returncode:
        print('\n'.join((out/log).read_text().splitlines()[-40:]),flush=True);break
after=inputs();(out/'inputs-after.json').write_text(json.dumps(after,indent=2)+'\n')
(out/'result.json').write_text(json.dumps(dict(result='PASS' if len(results)==len(commands) and all(r['exit_code']==0 for r in results) else 'FAIL',commands=results,inputs_stable=before==after,selected_inputs=len(after)),indent=2)+'\n')
print('selected',len(after),'stable',before==after,flush=True)
