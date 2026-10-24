"""Synthetic projection/order and retained-owner identity fixtures, never USB evidence."""
import ast,copy,difflib,hashlib,importlib.util,json,pathlib
ROOT=pathlib.Path('/home/ubuntu/github/usbip-client');OUT=pathlib.Path(__file__).parent
path=ROOT/'scripts/qemu_diagnostic_export/verify.py'
def module(name,p):
 s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
V=module('diag_revision2',path)
def encoded(value):return (json.dumps(value,separators=(',',':'))+'\n').encode()
class Fixture:
 def __init__(self,data):self.files=data
 def raw(self,role,name):return self.files[name]
 def data(self,role,name):return V.L.decode(self.raw(role,name))
 def check(self,ok,label):
  if not ok:raise RuntimeError(label)
checks=[]
def record(name,fn,negative=False):
 try: value=fn()
 except RuntimeError as error:
  if not negative:raise
  checks.append({'check':name,'passed':True,'rejection':str(error)})
 else:
  if negative:raise RuntimeError('accepted invalid fixture '+name)
  checks.append({'check':name,'passed':True})

def uuid(number):return f'{number:08x}-0000-4000-8000-000000000001'
epoch=uuid(100);jobid=uuid(200)
job={'id':jobid,'instance_id':epoch,'profile':'server','operator_uid':1000,'state':'running','stop_requested':False,'operation_error':None}
source_sessions=[]
for index in range(1,5):
 role='export' if index%2 else 'incoming';kind='native_export' if role=='export' else 'native_stub'
 sid=uuid(300+index);device='native:private-device-'+str((index-1)//2)
 source_sessions.append({'id':sid,'instance_id':epoch,'job_id':jobid,'profile':'server','role':role,'device_key':device,'principal':None if role=='export' else 'qemu-client','state':'Available' if role=='export' else 'Connected','evidence':'export_ready' if role=='export' else 'import_reply_flushed','stop_requested':False,'terminal':False,'operation_error':None,'cleanup':None,'metrics':{'app_rx_bytes':index,'app_tx_bytes':index*2,'saturated':False,'elapsed_millis':index*10},'attempts':[{'kind':kind,'correlation':{'instance_id':epoch,'session_id':sid,'attempt_id':uuid(400+index)},'resource_id':uuid(500+index),'proof':None}]})
def audit(seq,uid):return {'sequence':seq,'actor_uid':uid,'profile':None,'job_id':None,'session_id':None,'action':'denied','failure':'permission','state':None,'evidence':None,'cleanup':None}
source_audit=[audit(1,1002),audit(2,1003)]+[audit(n,1000) for n in range(3,23)]
committed={'sequence':22,'jobs':[dict(job,id=uuid(999),profile='another-private-profile'),job],'sessions':list(reversed(source_sessions)),'audit':source_audit}
# Alias assignment is independent from the verifier: explicit per-namespace
# fixture projection preserves source references in sorted publication order.
ns_names=['application_epochs','process_epochs','jobs','sessions','attempts','resources','devices','principals','profiles','uids','connections'];maps={name:{} for name in ns_names}
def alias(ns,value):
 if value is None:return None
 if value not in maps[ns]:maps[ns][value]=len(maps[ns])+1
 return maps[ns][value]
profile=alias('profiles','server');caller=alias('uids',1000);ep=alias('application_epochs',epoch)
projected_job={'job':alias('jobs',jobid),'application_epoch':ep,'profile':profile,'operator':caller,'state':'running','stop_requested':False,'operation_error':None}
projected_sessions=[]
for old in source_sessions:
 session_alias=alias('sessions',old['id']);a=old['attempts'][0]
 projected_sessions.append({'session':session_alias,'application_epoch':ep,'job':projected_job['job'],'profile':profile,'role':old['role'],'device':alias('devices',old['device_key']),'principal':alias('principals',old['principal']),**{k:old[k] for k in ('state','evidence','stop_requested','terminal','operation_error','cleanup','metrics')},'attempts':[{'kind':a['kind'],'application_epoch':ep,'session':session_alias,'attempt':alias('attempts',a['correlation']['attempt_id']),'resource':alias('resources',a['kind']+':'+a['resource_id']),'proof':None}]})
def project_audit(old,log=False):return {'application_epoch':ep if log else None,'sequence':old['sequence'],'actor':caller,'profile':None,'job':None,'session':None,**{k:old[k] for k in ('action','failure','state','evidence','cleanup')}}
projected_audit=[project_audit(a) for a in source_audit if a['actor_uid']==1000]
health=[]
for row in projected_sessions:
 link=None
 if row['role']=='incoming':
  link={'protocol':'framed_v2','connection':alias('connections','conn'+str(row['session'])),'sample_sequence':2,'elapsed_millis':20,'phase':'open','probe':'idle','last_probe_sequence':None,'last_probe_enqueued_millis':None,'last_reply_sequence':None,'last_reply_millis':None,'last_round_trip_micros':None,'stats':None,'end':None}
 health.append({'session':row['session'],'application_epoch':ep,'role':row['role'],'availability':'not_data_session' if link is None else 'live','not_in_committed_view':False,'observed_at_millis':25,'link':link})
log_records=[{'process_epoch':alias('process_epochs','log-private-epoch'),'sequence':i,'elapsed_millis':i,'level':'info','event':dict(project_audit(a,True),kind='audit')} for i,a in enumerate(source_audit[2:],1)]
pages=[{'observed_at_millis':26,'source_records':count,'exported_records':count,'privacy_filtered_records':0,'scanned_bytes':2000,'gap':False,'partial_tail':False,'more':i==0,'continuation_observed':True,'oldest_retained_boundary_observed':True,'live_status_availability':'observed','live_status':{'healthy':True,'failure':None,'dropped':0,'oversized':0,'projection_errors':0}} for i,count in enumerate((16,4))]
summary={'complete':True,'sections':[{'stage':s,'outcome':'complete','failure':None,'observed_at_millis':30} for s in ('native','helper','agent','health','logs')]}
bundle={'schema_version':1,'capture':'complete','summary':summary,'program_version':{'major':1,'minor':0,'patch':0},'build':{'secure_agent_collection':True},'privacy':{'identifiers_aliased':True,'independent_section_samples':True,'network_security_verified':False,'hardware_test_performed':False},'limits':{'serialized_bytes':4194304,'aliases':8192,'jobs':512,'sessions':512,'audit':1024,'health':512,'log_records':512,'log_pages':32},'alias_counts':{k:len(v) for k,v in maps.items()},'requested_agent_scope':{'profile':profile,'caller':caller},'native':{'os':'linux','architecture':'x86_64','kernel':'available','kernel_numeric':{'major':6,'minor':8,'patch':0},'usbip_host':{'loaded':'available','sysfs':'available','availability':'available'},'vhci_hcd':{'loaded':'missing','sysfs':'missing','availability':'available'},'usbip':'missing','usbipd':'missing'},'helper':{'status':'available','peer_verified':True,'kernel_available':False,'active_sessions':0,'external_trusted_network_policy_statement':False},'agent':{'application_epoch':ep,'profile':profile,'caller':caller,'backend':'native','committed_sequence':22,'publication_age_millis':1,'audit_oldest_retained_sequence':1,'expires_after_millis':30000,'expected_jobs':1,'expected_sessions':4,'expected_audit':20,'jobs':[projected_job],'sessions':projected_sessions,'audit':projected_audit},'health':{'pages':1,'samples':health},'logs':{'pages':pages,'records':log_records}}
files={'selected-committed.json':encoded(committed),'selected-draft.json':encoded(dict(committed,sequence=23)),'export-held.wait':b'0\n','root-before-refusals.json':b'{}\n','root-after-refusals.json':b'{}\n','resources-before-refusals.json':b'{}\n','resources-after-refusals.json':b'{}\n'}
for uid in (1002,1003):
 files[f'export-denied-{uid}.stdout']=encoded({'capture':'partial','summary':{'sections':[{'stage':s,'outcome':'incomplete','failure':'permission'} for s in ('agent','health','logs')]}})
 files[f'export-denied-{uid}.wait']=b'1\n';files[f'denied-bundle-{uid}.json']=b'{"agent":null}\n'
def projection_files(document):
 result=dict(files);raw=encoded(document);result['diagnostic-bundle.json']=raw
 result['export-held.stdout']=encoded({'schema_version':1,'event':'diagnostic_export','capture':'complete','cancel_observed':False,'summary':document['summary'],'file':{'result':'written','receipt':{'publication':'published','partial_name':None,'written_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}}})
 return result
baseline=projection_files(bundle)
(OUT/'projection-fixture-input.json').write_text(json.dumps({'committed':committed,'artifact':bundle},indent=2)+'\n')
record('full projection accepts reverse-insertion raw sessions and UUID-sorted artifact',lambda:V.check_projection(Fixture(baseline)))
# Preserve a real RED of the old verifier against exactly this synthetic input.
old_path=OUT/'verify-before.py';namespace={'__file__':str(path),'__name__':'old_diag_static'}
exec(compile(old_path.read_text(),str(old_path),'exec'),namespace)
record('old frozen projection rejects same correctly sorted artifact',lambda:namespace['check_projection'](Fixture(baseline)),True)
for label,mutate in [('original metric mismatch',lambda d:d['agent']['sessions'][0]['metrics'].update(app_rx_bytes=999)),('wrong attempt kind',lambda d:d['agent']['sessions'][0]['attempts'][0].update(kind='native_import')),('capability alias collision',lambda d:d['agent']['sessions'][1]['attempts'][0].update(resource=d['agent']['sessions'][0]['attempts'][0]['resource'])),('wrong correlated session alias',lambda d:d['agent']['sessions'][0]['attempts'][0].update(session=2)),('wrong audit sequence',lambda d:d['agent']['audit'][0].update(sequence=999))]:
 d=copy.deepcopy(bundle);mutate(d);record('sorted projection still rejects '+label,lambda d=d:V.check_projection(Fixture(projection_files(d))),True)
# Reuse original synthetic gate setup as input construction only. Do not execute
# its old checks or overwrite any original 80-check report.
text=pathlib.Path('/tmp/rustusb-diagnostic-harness-static-20261024/check.py').read_text()
fragment=text[text.index('class Fixture:'):text.index('V.check_gate(Fixture(files));')]
space={'V':V,'json':json,'struct':__import__('struct')};exec(fragment,space)
gate=space['files'];base=space['base'];candidate=space['candidate'];end=space['end'];sha='ab'*32
candidate['tracer_pid']=43;end.update(tracer_pid=43,nonce='prepare-stop-v1',operation='resource')
gate['gate-candidate-1.json']=encoded(candidate);gate['gate-exit-1.json']=encoded(end)
gate['gate-ready.json']=encoded({'event':'ready','uid':2000,'tracer_pid':43,'tracee_pid':42,'tracee_starttime':100})
gate['gate-armed.json']=encoded({'event':'armed','nonce':'prepare-stop-v1','operation':'resource','tracer_pid':43,'tracee_pid':42,'tracee_starttime':100})
def proc_stat(start=100):return ('42 (rustusb) '+' '.join(['S']+['0']*18+[str(start)])+'\n').encode()
for mark in ('agent-start','gate-before','gate-after'):
 gate['proc-'+mark+'.status']=b'Pid:\t42\nUid:\t2000\t2000\t2000\t2000\n'
 gate['proc-'+mark+'.stat']=proc_stat();gate['proc-'+mark+'.exe.sha256']=(sha+'  /proc/42/exe\n').encode();gate['proc-'+mark+'.uptime']=b'1.50 1.00\n'
 gate.setdefault('proc-'+mark+'-fds.jsonl',b'')
record('focused gate binds ready/candidate/before/after original PID/start/ELF',lambda:V.check_gate(Fixture(gate),sha))
variants={
 'same PID reused with other starttime':('proc-gate-after.stat',proc_stat(101)),
 'different actual ELF':('proc-gate-before.exe.sha256',('cd'*32+'  /proc/42/exe\n').encode()),
 'different candidate tracee':('gate-candidate-1.json',encoded(dict(candidate,tracee_pid=99))),
 'different candidate file':('gate-candidate-1.json',encoded(dict(candidate,file_inode=99))),
 'wrong ready starttime':('gate-ready.json',encoded({'event':'ready','uid':2000,'tracer_pid':43,'tracee_pid':42,'tracee_starttime':99})),
 'changed actual nonce':('gate-exit-1.json',encoded(dict(end,nonce='different'))),
}
for label,(name,value) in variants.items():
 changed=dict(gate);changed[name]=value;record('focused gate rejects '+label,lambda changed=changed:V.check_gate(Fixture(changed),sha),True)
# Only the authorized verifier changed; original frozen sibling bytes stay exact.
first=json.loads(pathlib.Path('/tmp/rustusb-diagnostic-harness-author-20261024/handoff.json').read_text())
for name,row in first['source'].items():
 if name.endswith(('verify.py','run.py')):continue
 record('unchanged frozen sibling '+name,lambda name=name,row=row:None if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==row['sha256'] else (_ for _ in ()).throw(RuntimeError('sibling changed')))
run_path=ROOT/'scripts/qemu_diagnostic_export/run.py';R=module('diagnostic_revision2_runner',run_path)
base_logs={role:f'actual evidence before final marker\nRUSTUSB_DIAGNOSTIC_RESULT: PASS role={role}\n' for role in ('server','client')}
marker_variants=[('two normal final roles',None,None,False),('missing server PASS','server','no final marker\n',True),('double client PASS','client',base_logs['client']*2,True),('wrong-role marker','server',base_logs['client'],True),('extra wrong-role PASS','server',base_logs['server']+base_logs['client'],True),('late guest FAIL','server',base_logs['server']+'RUSTUSB_DIAGNOSTIC_RESULT: FAIL role=server stage=artifact-dump exit=1\n',True),('late artifact error','client',base_logs['client']+'SECURE_ARTIFACT_ERROR sample copy_exit=1\n',True),('late oversized artifact','server',base_logs['server']+'SECURE_ARTIFACT_OVERSIZE sample 2000000\n',True),('late poweroff return','client',base_logs['client']+'SECURE_POWEROFF_RETURNED role=client\n',True)]
for index,(label,role,value,reject) in enumerate(marker_variants):
 directory=OUT/('terminal-marker-fixture-'+str(index));directory.mkdir(exist_ok=True)
 logs=dict(base_logs)
 if role is not None:logs[role]=value
 for name,log in logs.items():(directory/(name+'-evidence.log')).write_text(log)
 record('terminal '+label,lambda directory=directory:R.check_final_guest_logs(directory),reject)
ast.parse(run_path.read_text());record('Python AST final runner',lambda:None)
(OUT/'run-diff.patch').write_text(''.join(difflib.unified_diff((OUT/'run-before.py').read_text().splitlines(True),run_path.read_text().splitlines(True),fromfile='original/run.py',tofile='revision2/run.py')))
ast.parse(path.read_text());record('Python AST final verifier',lambda:None)
(OUT/'verify-diff.patch').write_text(''.join(difflib.unified_diff(old_path.read_text().splitlines(True),path.read_text().splitlines(True),fromfile='original/verify.py',tofile='revision2/verify.py')))
(OUT/'fixtures-report.json').write_text(json.dumps({'passed':True,'scope':'synthetic full projection/order and focused gate identity; not a VM/USB/runtime case','checks':checks,'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (path,run_path)}},indent=2)+'\n')
print(json.dumps({'passed':True,'checks':len(checks)}))
