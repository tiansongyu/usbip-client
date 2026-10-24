from pathlib import Path
import datetime,hashlib,json,subprocess,sys
root=Path('/home/ubuntu/github/usbip-client');out=Path('/tmp/rustusb-logging-dev-first-20261024');out.mkdir(exist_ok=False)
def inputs():
 paths=[root/'Cargo.toml',root/'Cargo.lock',root/'rust-toolchain.toml',root/'scripts/check.sh']
 for base in ['crates','vendor/nusb']:
  paths += [p for p in (root/base).rglob('*') if p.is_file() and (p.suffix=='.rs' or p.name=='Cargo.toml') and 'target' not in p.parts]
 return [dict(path=str(p.relative_to(root)),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(set(paths))]
def save(n,v):(out/n).write_text(json.dumps(v,indent=2)+'\n')
before=inputs();save('inputs-before.json',before)
for r in before:
 p=out/'source'/r['path'];p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((root/r['path']).read_bytes())
save('start.json',dict(head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),status=subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True),utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),scope='First development logging/service subset plus CLI compile; not full acceptance or USB QEMU.'))
(out/'runner.py').write_bytes(Path(__file__).read_bytes())
commands=[('logging',['cargo','test','-p','rustusb-logging','--lib','--offline','--','--nocapture']),('service',['cargo','test','-p','rustusb-service','--all-features','--lib','--offline','logging','--','--nocapture']),('cli-check',['cargo','check','-p','rustusb-cli','--all-features','--all-targets','--offline'])]
results=[]
for name,cmd in commands:
 print(json.dumps(dict(start=name,command=cmd)),flush=True)
 with (out/(name+'.log')).open('wb') as f:p=subprocess.run(cmd,cwd=root,stdout=f,stderr=subprocess.STDOUT)
 row=dict(name=name,command=cmd,exit_code=p.returncode,inputs_unchanged=inputs()==before);results.append(row);save('results.json',results);print(json.dumps(row),flush=True)
 if p.returncode or inputs()!=before:
  save('inputs-after.json',inputs());save('result.json',dict(result='FAIL',runs=results,input_count=len(before),inputs_unchanged=inputs()==before,remaining_unrun=[n for n,_ in commands[len(results):]]));sys.exit(1)
save('inputs-after.json',inputs());save('result.json',dict(result='PASS',runs=results,input_count=len(before),inputs_unchanged=inputs()==before))
