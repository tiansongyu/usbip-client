import pathlib,subprocess,json,hashlib,importlib.util,re,datetime
root=pathlib.Path('/home/ubuntu/github/usbip-client');out=pathlib.Path('/tmp/rustusb-frontend-recovery-software-20261025');out.mkdir(exist_ok=False)
spec=importlib.util.spec_from_file_location('action_inputs',root/'scripts/qemu_gui_link/run.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def git(*a):return subprocess.check_output(['git',*a],cwd=root,text=True).strip()
def frozen():
 result=m.inputs()
 for p in sorted((root/'scripts/qemu_automatic_rust').iterdir()):
  if p.is_file()and p.suffix in ['.py','.sh']:result[str(p.relative_to(root))]={'bytes':p.stat().st_size,'sha256':m.link.sha(p)}
 return dict(sorted(result.items()))
original_frozen=frozen
def frozen():
 result=original_frozen()
 for p in sorted((root/'scripts/qemu_gui_rules').iterdir()):
  if p.is_file() and p.suffix in ['.py','.sh','.md']:result[str(p.relative_to(root))]={'bytes':p.stat().st_size,'sha256':m.link.sha(p)}
 return dict(sorted(result.items()))
before_decisions=frozen
def frozen():
 result=before_decisions()
 for p in sorted((root/'scripts/qemu_rule_decisions').iterdir()):
  if p.is_file() and p.suffix in ['.py','.sh','.md']:result[str(p.relative_to(root))]={'bytes':p.stat().st_size,'sha256':m.link.sha(p)}
 return dict(sorted(result.items()))
before_recovery=frozen
def frozen():
 result=before_recovery()
 for directory in ['qemu_frontend_recovery','qemu_tui','qemu_tui_actions','qemu_tui_failures']:
  for p in sorted((root/'scripts'/directory).iterdir()):
   if p.is_file() and p.suffix in ['.py','.sh','.md','.c','.txt']:result[str(p.relative_to(root))]={'bytes':p.stat().st_size,'sha256':m.link.sha(p)}
 return dict(sorted(result.items()))
inputs=frozen();head=git('rev-parse','HEAD');status=git('status','--porcelain');assert not status
summary={'head':head,'inputs':inputs,'result':'FAIL','commands':[],'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
try:
 with (out/'preflight.json').open('wb')as stream:preflight=subprocess.run(['python3','scripts/qemu_automatic_rust/preflight.py'],cwd=root,stdout=stream,stderr=subprocess.STDOUT)
 assert preflight.returncode==0
 summary['preflight']=json.loads((out/'preflight.json').read_text());assert summary['preflight']['schema_case_count']==50
 spec2=importlib.util.spec_from_file_location('decision_preflight',root/'scripts/qemu_rule_decisions/run.py');module2=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(module2)
 generated=module2.guest();(out/'generated-decision-init.sh').write_text(generated)
 syntax=subprocess.run(['dash','-n',str(out/'generated-decision-init.sh')],capture_output=True);assert syntax.returncode==0
 (out/'decision-preflight.json').write_text(json.dumps({'generated_bytes':len(generated.encode()),'sha256':hashlib.sha256(generated.encode()).hexdigest(),'dash_exit':syntax.returncode,'new_fixture_inputs':len(module2.inputs())},indent=2)+'\n')
 spec3=importlib.util.spec_from_file_location('recovery_preflight',root/'scripts/qemu_frontend_recovery/run.py');module3=importlib.util.module_from_spec(spec3);spec3.loader.exec_module(module3)
 generated=module3.guest();(out/'generated-recovery-init.sh').write_text(generated)
 syntax=subprocess.run(['dash','-n',str(out/'generated-recovery-init.sh')],capture_output=True);assert syntax.returncode==0
 for n in ['qemu_tui','qemu_tui_actions','qemu_tui_failures']:
  subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(root/'scripts'/n/'pty-driver.c'),'-lutil','-o',str(out/(n+'-preflight'))],check=True)
 (out/'recovery-preflight.json').write_text(json.dumps({'generated_bytes':len(generated.encode()),'sha256':hashlib.sha256(generated.encode()).hexdigest(),'dash_exit':syntax.returncode,'new_fixture_inputs':len(module3.inputs()),'compiled_original_pty_drivers':3},indent=2)+'\n')
 for name,cmd in [('core',['bash','scripts/check.sh']),('gui',['bash','scripts/check-gui.sh'])]:
  with (out/(name+'.log')).open('wb')as stream:r=subprocess.run(cmd,cwd=root,stdout=stream,stderr=subprocess.STDOUT)
  values=[tuple(map(int,x))for x in re.findall(r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored;', (out/(name+'.log')).read_text())]
  summary['commands'].append({'name':name,'argv':cmd,'exit':r.returncode,'passed':sum(x[0]for x in values),'failed':sum(x[1]for x in values),'ignored':sum(x[2]for x in values)});assert r.returncode==0
 summary['binaries']={n:{'bytes':p.stat().st_size,'sha256':m.link.sha(p)}for n,p in {'rustusb':root/'target/release/rustusb','rustusb-helper':root/'target/release/rustusb-helper','rustusb-gui':root/'apps/rustusb-gui/target/release/rustusb-gui'}.items()};summary['result']='PASS'
finally:
 summary['guards']={'inputs':frozen()==inputs,'head':git('rev-parse','HEAD')==head,'clean':git('status','--porcelain')==status}
 if not all(summary['guards'].values()):summary['result']='FAIL'
 (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
assert summary['result']=='PASS'
