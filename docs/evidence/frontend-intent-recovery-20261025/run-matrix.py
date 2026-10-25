import concurrent.futures,datetime,hashlib,json,os,pathlib,subprocess,threading
root=pathlib.Path('/home/ubuntu/github/usbip-client');out=pathlib.Path('/tmp/rustusb-frontend-recovery-matrix-20261025');out.mkdir(exist_ok=False)
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip();assert not subprocess.check_output(['git','status','--porcelain'],cwd=root).strip()
software=json.loads(pathlib.Path('/tmp/rustusb-frontend-recovery-software-20261025/summary.json').read_text());assert software['result']=='PASS'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
inputs={n:{'bytes':(root/n).stat().st_size,'sha256':sha(root/n)}for n in software['inputs']}
changed=[n for n,m in inputs.items()if m!=software['inputs'][n]];assert set(changed)=={'scripts/qemu_frontend_recovery/run.py','scripts/qemu_frontend_recovery/guest.sh'},changed
binaries={n:root/('apps/rustusb-gui/target/release/rustusb-gui'if n=='rustusb-gui'else'target/release/'+n)for n in software['binaries']}
def guard():
 assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()==head
 assert not subprocess.check_output(['git','status','--porcelain'],cwd=root).strip()
 for n,m in inputs.items():assert (root/n).stat().st_size==m['bytes']and sha(root/n)==m['sha256'],n
 for n,p in binaries.items():assert p.stat().st_size==software['binaries'][n]['bytes']and sha(p)==software['binaries'][n]['sha256'],n
prior=pathlib.Path('/tmp/rustusb-frontend-recovery-new-ipv4-03-20261025');first=json.loads((prior/'summary.json').read_text());assert first['result']=='PASS'and first['head']==head
runs=[{'name':'new-ipv4','evidence':str(prior),'existing_actual_invocation':True,'exit':0,'started_utc':first['started_utc'],'ended_utc':first['completed_utc']}]
lock=threading.RLock()
meta={'head':head,'software_head':software['head'],'source_inputs':inputs,'changed_fixture_inputs_since_gate':changed,'binaries':software['binaries'],'maximum_concurrent_invocations':2,'test_decoder':{'PYTHONPATH':'/tmp/rustusb-tui-qa-pyte','pyte':'0.8.2','wcwidth':'0.2.13'},'runs':runs}
def save(result):
 with lock:(out/'summary.json').write_text(json.dumps({'result':result,**meta},indent=2)+'\n')
driver='/tmp/rustusb-gui-native-tools-20261025/extracted/usr/bin/WebKitWebDriver'
cases=[('new-ipv6','qemu_frontend_recovery/run.py','ipv6',True),('settled-rules-ipv4','qemu_rule_decisions/run.py','ipv4',True),('settled-rules-ipv6','qemu_rule_decisions/run.py','ipv6',True),('legacy-rules-ipv4','qemu_gui_rules/run.py','ipv4',True),('legacy-rules-ipv6','qemu_gui_rules/run.py','ipv6',True),('gui-actions-ipv4','qemu_gui_actions/run.py','ipv4',True),('gui-actions-ipv6','qemu_gui_actions/run.py','ipv6',True),('gui-link-ipv4','qemu_gui_link/run.py','ipv4',True),('gui-single','qemu_gui/run.py',None,True),('tui-actions-ipv4','qemu_tui_actions/run.py','ipv4',False),('tui-actions-ipv6','qemu_tui_actions/run.py','ipv6',False),('tui-failures-ipv4','qemu_tui_failures/run.py','ipv4',False),('tui-failures-ipv6','qemu_tui_failures/run.py','ipv6',False),('tui-original-ipv4','qemu_tui/run.py','ipv4',False),('tui-original-ipv6','qemu_tui/run.py','ipv6',False)]
def execute(row):
 env=dict(os.environ);env['PYTHONPATH']='/tmp/rustusb-tui-qa-pyte';env['PYTHONDONTWRITEBYTECODE']='1'
 with (out/(row['name']+'.log')).open('wb')as stream:
  process=subprocess.Popen(row['argv'],cwd=root,stdout=stream,stderr=subprocess.STDOUT,env=env)
  with lock:row['actual_child_pid']=process.pid;save('RUNNING')
  code=process.wait()
 with lock:row['exit']=code;row['ended_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat();save('RUNNING')
 print(json.dumps({'name':row['name'],'exit':code}),flush=True)
 return code
save('RUNNING')
for offset in range(0,len(cases),2):
 guard();batch=[]
 for name,script,family,gui in cases[offset:offset+2]:
  evidence=pathlib.Path('/tmp/rustusb-frontend-recovery-'+name+'-20261025');argv=['python3','scripts/'+script,'--evidence',str(evidence),'--timeout','600']
  if gui:argv+=['--driver',driver]
  if family:argv+=['--family',family]
  row={'name':name,'argv':argv,'evidence':str(evidence),'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()};runs.append(row);batch.append(row)
 save('RUNNING')
 with concurrent.futures.ThreadPoolExecutor(max_workers=2)as pool:codes=list(pool.map(execute,batch))
 # Every actual running child has returned before stopping or admitting another batch.
 guard()
 if any(codes):save('FAIL');raise SystemExit(1)
guard();meta['guards']={'head':True,'clean':True,'inputs':True,'binaries':True};save('PASS')
print(json.dumps({'result':'PASS','invocations':len(runs),'head':head}),flush=True)
