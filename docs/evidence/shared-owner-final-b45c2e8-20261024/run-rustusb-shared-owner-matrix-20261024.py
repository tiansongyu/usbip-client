import datetime,hashlib,json,pathlib,subprocess,sys
root=pathlib.Path('/home/ubuntu/github/usbip-client');out=pathlib.Path(sys.argv[1]);expected=sys.argv[2]
out.mkdir(parents=True,exist_ok=False)
def git(*args):return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
def inputs():
 paths=[root/'Cargo.toml',root/'Cargo.lock',root/'rust-toolchain.toml',root/'scripts/check.sh']
 for parent in [root/'crates',root/'vendor/nusb',root/'scripts/qemu']:
  paths.extend(p for p in parent.rglob('*') if p.is_file() and (p.suffix in ['.rs','.c','.py','.sh'] or p.name in ['Cargo.toml','Cargo.lock']) and 'target' not in p.parts)
 return [{'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(set(paths))]
def save(name,value):(out/name).write_text(json.dumps(value,indent=2)+'\n')
assert git('rev-parse','HEAD')==expected
assert not git('status','--porcelain'), 'requires clean tree'
before=inputs();save('inputs-before.json',before)
save('start.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head':expected,'clean':True,'input_count':len(before),'scope':'nine existing QEMU regressions; not an accepted-initializer gate or hardware acceptance'})
results=[]
cases=[(f'secure-{b}-{f}',['python3','scripts/qemu/run-secure-qemu.py','--backend',b,'--family',f]) for b in ['virtual','usbfs','native'] for f in ['ipv4','ipv6']]
cases += [(f'agent-native-{f}',['python3','scripts/qemu/run-agent-qemu.py','--family',f]) for f in ['ipv4','ipv6']]
cases += [('sigterm',['python3','scripts/qemu/run-sigterm-qemu.py'])]
for name,command in cases:
 assert git('rev-parse','HEAD')==expected and not git('status','--porcelain') and inputs()==before
 directory=out/name;log=out/(name+'.runner.log')
 command += ['--kernel','6.8.0-106-generic','--evidence',str(directory)]
 print(json.dumps({'start':name,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}),flush=True)
 with log.open('wb') as output:result=subprocess.run(command,cwd=root,stdout=output,stderr=subprocess.STDOUT)
 summary=json.loads((directory/'summary.json').read_text()) if (directory/'summary.json').is_file() else None
 unchanged=inputs()==before and git('rev-parse','HEAD')==expected and not git('status','--porcelain')
 record={'case':name,'command':command,'exit_code':result.returncode,'unchanged':unchanged,'directory':str(directory),'summary_result':summary.get('result') if summary else None}
 results.append(record);save('results.json',results);print(json.dumps(record),flush=True)
 if result.returncode or not unchanged:
  save('inputs-after.json',inputs());save('result.json',{'result':'FAIL','runs':results,'head':expected});sys.exit(1)
save('inputs-after.json',inputs());save('result.json',{'result':'PASS','runs':results,'head':expected,'input_count':len(before),'inputs_unchanged':inputs()==before})
