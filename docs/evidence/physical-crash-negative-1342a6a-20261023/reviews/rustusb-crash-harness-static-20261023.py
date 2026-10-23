import copy, hashlib, importlib.util, json, pathlib, py_compile, shlex, struct, subprocess, sys
sys.dont_write_bytecode=True
repo=pathlib.Path('/home/ubuntu/github/usbip-client')
paths=[repo/'scripts/qemu/run-crash-qemu.py',repo/'scripts/qemu/guest-crash-init.sh']
checks=[]
def record(name,ok,detail=None):
 checks.append(dict(name=name,result='PASS' if ok else 'FAIL',detail=detail))
py_compile.compile(str(paths[0]),cfile='/tmp/rustusb-run-crash-qemu-20261023.pyc',doraise=True)
record('python_compile_to_tmp',True)
for binary in [['sh','-n'],['/bin/busybox','sh','-n']]:
 p=subprocess.run(binary+[str(paths[1])],capture_output=True,text=True)
 record(' '.join(binary),p.returncode==0,dict(exit_code=p.returncode,stdout=p.stdout,stderr=p.stderr))
spec=importlib.util.spec_from_file_location('crash_qemu_static',paths[0]); module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
source=paths[1].read_text().replace('\\\n',' ')
filters=0
for line in source.splitlines():
 if not line.strip().startswith(('jq ','if jq ','if ! jq ')):
  continue
 try: args=shlex.split(line)
 except ValueError: continue
 j=args.index('jq')+1; bound=[]
 while j<len(args) and args[j].startswith('-'):
  flag=args[j]; j+=1
  if flag in ['--arg','--argjson','--slurpfile','--rawfile']:
   bound+=['--argjson',args[j],'null'];j+=2
 if j>=len(args): continue
 p=subprocess.run(['jq','-n',*bound,'try ('+args[j]+') catch null'],capture_output=True,text=True)
 filters+=1
 record('jq_filter_'+str(filters),'compile error' not in p.stderr,dict(filter=args[j],exit_code=p.returncode,stderr=p.stderr))
# This fixture is explicitly reconstructed from an earlier TERM verification
# shape, then mutated for parser checks. It is not a crash execution transcript.
prior=pathlib.Path('/tmp/rustusb-sigterm-fixed-qemu-934358f-20261023/sigterm/summary.json')
v=json.loads(prior.read_bytes())['verification']
original=copy.deepcopy(v['server_observations'][0]['sysfs'])
base=copy.deepcopy(v['server_observations'][0]['snapshot'])
before=v['outcomes']['server']['pre_snapshot_id']
server=copy.deepcopy(v['outcomes']['server']);server.update(event='crash_outcome',signal=9,wait_code=137,epoch='initial',shutdown_before_crash=False,final_backend_snapshot=None,final_backend_snapshot_observable=False);server.pop('shutdown_control_sent',None)
client=copy.deepcopy(v['outcomes']['client']);client.update(event='crash_outcome',new_imports_after_crash=False,rx_hex=module.RX.hex())
initial_events=[copy.deepcopy(e) for e in v['server_events'] if e['event']!='stopped']
obs=[]
for old,label in zip(v['server_observations'][:3],['baseline','held','pre-crash']):
 e=copy.deepcopy(old);e['label']=label;e['epoch']='initial';e['identity']['fds']=e.pop('fds');obs.append(e)
obs.append(dict(label='post-crash',epoch='initial',sysfs=copy.deepcopy(original),snapshot=None,snapshot_observable=False))
identity=copy.deepcopy(obs[0]['identity']);identity['pid']=999;identity['starttime']=888
obs.append(dict(label='restart-ready',epoch='restart',sysfs=copy.deepcopy(original),snapshot=copy.deepcopy(base),identity=identity))
obs.append(dict(label='post-restart',epoch='restart',sysfs=copy.deepcopy(original),snapshot=None))
restart=dict(schema_version=1,event='restart_outcome',role='server',epoch='restart',attempted=True,attempt_count=1,pid=999,starttime=888,ready_observed=True,identity_verified=True,shutdown_control_attempted=True,shutdown_ack_observed=True,process_exit_observed=True,wait_code=0,restored_initial_sysfs=True,helper_alive=True,new_imports_sent=False,startup_limit_seconds=10,shutdown_limit_seconds=20)
restart_events=[dict(schema_version=1,event='ready',address='192.0.2.1:3240',snapshot=copy.deepcopy(base)),dict(schema_version=1,event='ack',id='restart-stop',ok=True),dict(schema_version=1,event='stopped',snapshot=copy.deepcopy(base))]
def logs(server,client,obs,restart,restart_events):
 def block(tag,data):return '\n'.join(['RUSTUSB_CRASH_'+tag+'_BEGIN',*[json.dumps(e) for e in data],'RUSTUSB_CRASH_'+tag+'_END'])
 p={}
 for role in module.ROLES:
  p[role]='\n'.join([f'RUSTUSB_CRASH_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0',f'RUSTUSB_CRASH_ISOLATION: PASS role={role} carrier=1 ip_forward=0',f'RUSTUSB_CRASH_PRIMARY_EVIDENCE_COMPLETE role={role}',f'RUSTUSB_CRASH_COLLECTION_COMPLETE role={role}',f'RUSTUSB_CRASH_RESULT: PASS role={role}','RUSTUSB_CRASH_OUTCOME '+json.dumps(server if role=='server' else client)])+'\n'
 p['server']+='\n'.join([block('INITIAL_EVENTS',initial_events),block('RESTART_EVENTS',restart_events),block('OBSERVATIONS',obs),'RUSTUSB_CRASH_RESTART_OUTCOME '+json.dumps(restart),'RUSTUSB_CRASH_FD_GATE: PASS direct_uid1000=denied direct_uid1001=denied helper_uid1001=denied nodes=root0600','RUSTUSB_CRASH_SERVER_READY: PASS uid=1000 app_root=false capabilities=0 usbip_host=false vhci_hcd=false'])
 p['client']+='{"event":"physical_probe_case","case":"both_real_devices_exclusive_busy","result":"PASS"}\n'
 return p
kernel={role:'' for role in module.ROLES}
p=logs(server,client,obs,restart,restart_events)
out=module.verify(p,kernel)
record('synthetic_restored_parser_shape_all_checks',all(e['result']=='PASS' for e in out['checks']),[e for e in out['checks'] if e['result']!='PASS'])
negative=copy.deepcopy(obs)
for label in ['post-crash','restart-ready','post-restart']:
 for d in next(e for e in negative if e['label']==label)['sysfs']: d['interfaces'][0]['driver']=None
rs=copy.deepcopy(restart_events)
for event in [rs[0],rs[-1]]:
 for d in event['snapshot']['devices']:
  d['original_drivers'][0]['driver']=None;d['current_drivers'][0]['driver']=None
s=copy.deepcopy(server);s.update(restored_sysfs=False,within_observation_window=False,observation_centiseconds=2004)
r=copy.deepcopy(restart);r['restored_initial_sysfs']=False
p=logs(s,client,negative,r,rs);p['server']=p['server'].replace('RUSTUSB_CRASH_RESULT: PASS role=server','RUSTUSB_CRASH_RESULT: FAIL role=server stage=recovery-contract evidence_collection_complete=true')
out=module.verify(p,kernel)
record('synthetic_negative_collection_retained',out['evidence_collection_complete'])
record('synthetic_negative_facts_still_pass',all(e['result']=='PASS' for e in out['checks'] if e['category'] in ['fixture','fact']),[e for e in out['checks'] if e['category'] in ['fixture','fact'] and e['result']!='PASS'])
record('synthetic_negative_recovery_is_fail',any(e['result']=='FAIL' for e in out['checks'] if e['category']=='recovery'))
record('synthetic_missing_killed_snapshot_not_zero',out['killed_epoch_terminal_resources'] is None and out['killed_epoch_terminal_resources_observed'] is False)
refused=copy.deepcopy(negative);refused[4]=dict(label='restart-rejected',epoch='restart',sysfs=copy.deepcopy(negative[4]['sysfs']),snapshot=None,identity=None)
r=copy.deepcopy(restart);r.update(ready_observed=False,identity_verified=False,shutdown_control_attempted=False,shutdown_ack_observed=False,wait_code=1)
out=module.verify(logs(s,client,refused,r,[]),kernel)
record('synthetic_restart_refusal_retains_collection',out['evidence_collection_complete'] and out['restart_events']==[] and any(e['name']=='restart_refusal_preserved_without_fake_ready' and e['result']=='PASS' for e in out['checks']))
try: module.one_record('RUSTUSB_CRASH_OUTCOME \x1b[32m{}','RUSTUSB_CRASH_OUTCOME ')
except json.JSONDecodeError: record('ansi_json_rejected',True)
else:record('ansi_json_rejected',False)
report=dict(schema_version=1,result='PASS' if all(c['result']=='PASS' for c in checks) else 'FAIL',scope='static syntax/literal jq and explicitly reconstructed parser fixtures only; no Cargo/QEMU/network/USB access',sources={p.name:dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths},synthetic_shape_source=dict(path=str(prior),sha256=hashlib.sha256(prior.read_bytes()).hexdigest()),checks=checks)
path=pathlib.Path('/tmp/rustusb-crash-harness-static-20261023.json');path.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(dict(result=report['result'],checks=len(checks),failed=[c['name'] for c in checks if c['result']!='PASS'],report=str(path))))
sys.exit(0 if report['result']=='PASS' else 1)
