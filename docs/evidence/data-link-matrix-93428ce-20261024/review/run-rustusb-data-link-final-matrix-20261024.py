import datetime,hashlib,json,pathlib,subprocess,sys
root=pathlib.Path('/tmp/rustusb-data-link-final-checkout-20261024');out=pathlib.Path(sys.argv[1]);expected='93428ce0d27ca159003fdd98e0b9e61db7bb9d40';out.mkdir(parents=True,exist_ok=False)
def git(*args):return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
def inputs():
 paths=[root/'Cargo.toml',root/'Cargo.lock',root/'rust-toolchain.toml',root/'scripts/check.sh']
 for parent in [root/'crates',root/'vendor/nusb',root/'scripts/qemu',root/'scripts/qemu_data_link_health']:
  paths.extend(p for p in parent.rglob('*') if p.is_file() and (p.suffix in ['.rs','.c','.py','.sh'] or p.name in ['Cargo.toml','Cargo.lock']) and 'target' not in p.parts and '__pycache__' not in p.parts)
 return [{'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(set(paths))]
def save(name,value):(out/name).write_text(json.dumps(value,indent=2)+'\n')
assert git('rev-parse','HEAD')==expected and not git('status','--porcelain')
before=inputs();save('inputs-before.json',before);save('start.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head':expected,'clean':True,'input_count':len(before),'scope':'six new framed-v2 health fault runs (each three directional phases), followed by nine existing raw-v1 regressions; not hardware/ISO/kernel-internal-active acceptance'})
cases=[(f'health-{b}-{f}',['python3','-B','scripts/qemu_data_link_health/run.py','--backend',b,'--family',f]) for b in ['native','usbfs','virtual'] for f in ['ipv4','ipv6']]
cases += [(f'secure-{b}-{f}',['python3','-B','scripts/qemu/run-secure-qemu.py','--backend',b,'--family',f]) for b in ['virtual','usbfs','native'] for f in ['ipv4','ipv6']]
cases += [(f'agent-native-{f}',['python3','-B','scripts/qemu/run-agent-qemu.py','--family',f]) for f in ['ipv4','ipv6']]
cases += [('sigterm',['python3','-B','scripts/qemu/run-sigterm-qemu.py'])]
results=[]
for name,command in cases:
 assert git('rev-parse','HEAD')==expected and not git('status','--porcelain') and inputs()==before
 directory=out/name;log=out/(name+'.runner.log');command+=['--kernel','6.8.0-106-generic','--evidence',str(directory)]
 print(json.dumps({'start':name,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}),flush=True)
 with log.open('wb') as output:result=subprocess.run(command,cwd=root,stdout=output,stderr=subprocess.STDOUT)
 summary=json.loads((directory/'summary.json').read_text()) if (directory/'summary.json').is_file() else None
 unchanged=inputs()==before and git('rev-parse','HEAD')==expected and not git('status','--porcelain')
 record={'case':name,'command':command,'exit_code':result.returncode,'unchanged':unchanged,'directory':str(directory),'summary_result':summary.get('result') if summary else None};results.append(record);save('results.json',results);print(json.dumps(record),flush=True)
 if result.returncode or not unchanged:
  save('inputs-after.json',inputs());save('result.json',{'result':'FAIL','runs':results,'head':expected,'remaining_unrun':[n for n,_ in cases[len(results):]]});sys.exit(1)
save('inputs-after.json',inputs());save('result.json',{'result':'PASS','runs':results,'head':expected,'input_count':len(before),'inputs_unchanged':inputs()==before,'new_health_runs':6,'existing_compat_runs':9})
