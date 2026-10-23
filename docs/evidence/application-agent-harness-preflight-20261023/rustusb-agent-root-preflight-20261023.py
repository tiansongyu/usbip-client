import ast,hashlib,importlib.util,json,pathlib,re,subprocess
root=pathlib.Path('/home/ubuntu/github/usbip-client');script=root/'scripts/qemu/run-agent-qemu.py';fragment=root/'scripts/qemu/guest-agent-init.sh'
ast.parse(script.read_text())
spec=importlib.util.spec_from_file_location('agent_root_preflight',script);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
source=m.guest_source();pathlib.Path('/tmp/rustusb-agent-root-generated-init-20261023.sh').write_text(source)
checks=[]
for cmd in [['dash','-n'],['shellcheck','--shell=sh','-']]:
 p=subprocess.run(cmd,input=source,text=True,capture_output=True);checks.append({'command':cmd,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr});assert p.returncode==0
assert re.search(r'exec 3<\s*"\$EVENT"',source)
inputs=m.inputs();assert len([p for p in inputs if p.startswith('crates/rustusb-service/')])==16
p=subprocess.run(['python3',str(script),'--help'],text=True,capture_output=True);assert p.returncode==0;checks.append({'command':['python3','run-agent-qemu.py','--help'],'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
report={'scope':'root independent static preflight, not a QEMU result','inputs':{str(p.relative_to(root)):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in [script,fragment]},'qemu_selected_inputs':len(inputs),'service_selected_inputs':16,'checks':checks,'generated_init_sha256':hashlib.sha256(source.encode()).hexdigest(),'initial_checker_note':'First root checker asserted a whitespace-sensitive exec3 literal and failed against valid exec 3< SPACE path. Both generated dash/shellcheck exited0; corrected the checker regex only, without changing harness source.'}
pathlib.Path('/tmp/rustusb-agent-root-preflight-20261023.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'checks_passed':len(checks),'qemu_inputs':len(inputs),'guest_bytes':len(source.encode())}))
