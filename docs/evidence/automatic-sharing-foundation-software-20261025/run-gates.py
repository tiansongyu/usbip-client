import pathlib,subprocess,json,hashlib,importlib.util,re,datetime
root=pathlib.Path('/home/ubuntu/github/usbip-client');out=pathlib.Path('/tmp/rustusb-automation-foundation-software-20261025');out.mkdir(exist_ok=False)
spec=importlib.util.spec_from_file_location('action_inputs',root/'scripts/qemu_gui_link/run.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def git(*a):return subprocess.check_output(['git',*a],cwd=root,text=True).strip()
inputs=m.inputs();head=git('rev-parse','HEAD');status=git('status','--porcelain');assert not status
summary={'head':head,'inputs':inputs,'result':'FAIL','commands':[],'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
try:
 for name,cmd in [('core',['bash','scripts/check.sh']),('gui',['bash','scripts/check-gui.sh'])]:
  with (out/(name+'.log')).open('wb')as stream:r=subprocess.run(cmd,cwd=root,stdout=stream,stderr=subprocess.STDOUT)
  values=[tuple(map(int,x))for x in re.findall(r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored;', (out/(name+'.log')).read_text())]
  summary['commands'].append({'name':name,'argv':cmd,'exit':r.returncode,'passed':sum(x[0]for x in values),'failed':sum(x[1]for x in values),'ignored':sum(x[2]for x in values)});assert r.returncode==0
 summary['binaries']={n:{'bytes':p.stat().st_size,'sha256':m.link.sha(p)}for n,p in {'rustusb':root/'target/release/rustusb','rustusb-helper':root/'target/release/rustusb-helper','rustusb-gui':root/'apps/rustusb-gui/target/release/rustusb-gui'}.items()};summary['result']='PASS'
finally:
 summary['guards']={'inputs':m.inputs()==inputs,'head':git('rev-parse','HEAD')==head,'clean':git('status','--porcelain')==status}
 if not all(summary['guards'].values()):summary['result']='FAIL'
 (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
assert summary['result']=='PASS'
