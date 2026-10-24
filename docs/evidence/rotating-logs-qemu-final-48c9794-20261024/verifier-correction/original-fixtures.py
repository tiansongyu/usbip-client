"""Constructed verifier predicates only. No guest, subprocess, build or USB work."""
import ast,copy,hashlib,json,types
from pathlib import Path
ROOT=Path('/home/ubuntu/github/usbip-client')
SOURCE=ROOT/'scripts/qemu_process_logging/verify.py'
OUT=Path('/tmp/rustusb-process-logging-verifier-peer-close-revision-20261024')
text=SOURCE.read_text();ast.parse(text)
v=types.ModuleType('logging_verifier_fixture');exec(compile(text,str(SOURCE),'exec'),v.__dict__)
checks=[]
def check(name,action,valid=True):
    failure=None
    try: action()
    except Exception as error: failure=type(error).__name__+': '+str(error)
    good=(failure is None)==valid
    checks.append({'name':name,'expected_accept':valid,'passed':good,'actual_error':failure})
    if not good: raise AssertionError(name+': '+str(failure))
U=lambda n:f'00000000-0000-0000-0000-{n:012x}'
status={'healthy':True,'closing':True,'queued':0,'accepted':3,'written':3,'dropped':0,'oversized':0,'projection_errors':0,'overflowed':False,'last_written':U(2)+':200','failure':None}
record={'schema_version':1,'epoch':U(1),'sequence':2,'elapsed_millis':1,'wall_unix_millis':None,'level':'info','event':{'kind':'audit','application_instance':U(20),'audit_sequence':1,'actor_uid':1000,'profile':'server','job_id':U(21),'session_id':None,'action':'start','failure':None,'state':None,'evidence':None,'cleanup':None}}
header={'schema_version':1,'segment':U(2),'epoch':U(1),'ordinal':1}
encode=lambda x:json.dumps(x,separators=(',',':')).encode()+b'\n'
check('valid bounded complete typed log',lambda:v.parse_segment(encode(header)+encode(record)))
check('valid completed actual loss status',lambda:v.validate_status(dict(status,healthy=False,dropped=9),True))
for name,mutate in [
 ('unknown record field',lambda x:x.update(secret='sentinel')),
 ('missing nullable field',lambda x:x.pop('wall_unix_millis')),
 ('bool schema is not integer1',lambda x:x.update(schema_version=True)),
 ('zero record sequence',lambda x:x.update(sequence=0)),
 ('unapproved raw message',lambda x:x['event'].update(message='SECRET')),
 ('unknown audit action',lambda x:x['event'].update(action='arbitrary')),
 ('bad profile text',lambda x:x['event'].update(profile='server\nsecret')),
 ('missing nullable audit field',lambda x:x['event'].pop('failure')),
]:
 x=copy.deepcopy(record);mutate(x);check(name,lambda x=x:v.parse_segment(encode(header)+encode(x)),False)
check('duplicate raw field rejected before parse loss',lambda:v.parse_segment(encode(header)+encode(record).replace(b'"sequence":2',b'"sequence":2,"sequence":3')),False)
check('incomplete original tail rejected for successful epoch',lambda:v.parse_segment(encode(header)+encode(record)[:-1]),False)
check('oversize complete record refused',lambda:v.parse_segment(encode(header)+b' '*2048+encode(record)),False)
check('duplicate sequence refused',lambda:v.parse_segment(encode(header)+encode(record)+encode(record)),False)
check('header epoch mismatch refused',lambda:v.parse_segment(encode(dict(header,epoch=U(9)))+encode(record)),False)
for name,changes in [('loss cannot claim healthy',{'dropped':1}),('written cannot exceed admitted',{'written':4}),('pending cannot be Stopped',{'queued':1}),('Stopped requires closing',{'closing':False})]:
 check(name,lambda changes=changes:v.validate_status(dict(status,**changes),True),False)
diag=copy.deepcopy(record);diag['event']={'kind':'diagnostic','code':'operation_failed','target':'rustusb_service::runtime','line':10,'failure':'io','errno':27,'fields':{'count':1,'success':True},'omitted_fields':2}
check('approved numeric fixed diagnostic accepted',lambda:v.validate_record(diag))
for name,mutate in [('prefix spoof target',lambda x:x['event'].update(target='rustusb_service_fake')),('raw string scalar',lambda x:x['event']['fields'].update(count='SECRET')),('bool numeric scalar',lambda x:x['event']['fields'].update(count=True)),('wrong bool scalar',lambda x:x['event']['fields'].update(success=1)),('negative scalar',lambda x:x['event']['fields'].update(count=-1))]:
 x=copy.deepcopy(diag);mutate(x);check(name,lambda x=x:v.validate_record(x),False)
page={'schema_version':1,'records':[],'next_after':U(2)+':1024','oldest_available':U(2)+':140','gap':False,'partial_tail':False,'scanned_bytes':2048,'more':True,'status':dict(status,closing=False)}
check('zero-hit finite window still has progress cursor',lambda:v.validate_page(page))
x=copy.deepcopy(page);x.pop('status');check('missing nullable page status refused',lambda:v.validate_page(x),False)
x=copy.deepcopy(page);x['next_after']=U(2)+':01';check('noncanonical cursor refused',lambda:v.validate_page(x),False)
x=copy.deepcopy(page);x['scanned_bytes']=262145;check('overbudget scan refused',lambda:v.validate_page(x),False)
class Checks:
 def check(self,ok,label):
  if not ok:raise RuntimeError(label)
sessions=[];terminals=[]
for n in range(4):
 kind='native_export' if n<2 else 'native_stub';key='native:usb-'+str(n%2)*64
 correlation={'instance_id':U(30),'session_id':U(40+n),'attempt_id':U(50+n)}
 attempt={'kind':kind,'resource_id':U(60+n),'correlation':correlation,'proof':None}
 session={'id':U(40+n),'instance_id':U(30),'job_id':U(31),'profile':'server','role':'export' if n<2 else 'incoming','device_key':key,'principal':None,'terminal':True,'state':'Disconnected','evidence':'released','cleanup':'verified','operation_error':None,'attempts':[attempt]}
 sessions.append(session)
 terminals.append({'server_id':None,'terminal':{'resource_id':U(60+n),'kind':kind,'device_key':key,'owner_uid':2000,'correlation':copy.deepcopy(correlation),'proof':'native_export_restored' if n<2 else 'native_stub_available'}})
state={'schema_version':2,'sessions':sessions,'jobs':[{'id':U(31),'instance_id':U(30),'operator_uid':1000,'state':'finished'}]}
root={'schema_version':2,'records':[],'exports':[],'terminals':terminals};held=copy.deepcopy(state)
check('exact four original Native retirements accepted',lambda:v.check_root_history(Checks(),'server',1,state,root,held))
for name,mutate in [('wrong root UID',lambda d:d['terminals'][0]['terminal'].update(owner_uid=0)),('later other capability is not original proof',lambda d:d['terminals'][0]['terminal'].update(resource_id=U(99))),('wrong correlation is not original proof',lambda d:d['terminals'][0]['terminal']['correlation'].update(attempt_id=U(99))),('wrong logical server scope',lambda d:d['terminals'][0].update(server_id='other')),('missing terminal is not empty-success',lambda d:d['terminals'].pop())]:
 x=copy.deepcopy(root);mutate(x);check(name,lambda x=x:v.check_root_history(Checks(),'server',1,state,x,held),False)
x=copy.deepcopy(state);x['sessions'][0]['cleanup']='unconfirmed';check('unconfirmed never verified',lambda:v.check_root_history(Checks(),'server',1,x,root,held),False)
x=copy.deepcopy(state);x['jobs'][0]['state']='running';check('unfinished server owner not complete',lambda:v.check_root_history(Checks(),'server',1,x,root,held),False)
x=copy.deepcopy(state);x['sessions'][0]['principal']='another-principal';check('changed original principal refused',lambda:v.check_root_history(Checks(),'server',1,x,root,held),False)
x=copy.deepcopy(state);x['sessions'][0]['attempts'][0]['proof']='native_import_released';check('wrong proof echo refused',lambda:v.check_root_history(Checks(),'server',1,x,root,held),False)
x=copy.deepcopy(state);xr=copy.deepcopy(root);x['sessions'][0]['attempts'][0]['resource_id']=U(98);xr['terminals'][0]['terminal']['resource_id']=U(98)
check('new current proof cannot replace original held capability',lambda:v.check_root_history(Checks(),'server',1,x,xr,held),False)
client=copy.deepcopy(state);client['jobs']=[];clientroot=copy.deepcopy(root)
for n,s in enumerate(client['sessions']):
 s['role']='import';s['job_id']=U(100+n);s['attempts'][0]['kind']='native_import'
 client['jobs'].append({'id':U(100+n),'instance_id':U(30),'operator_uid':1000,'state':'finished'})
 clientroot['terminals'][n]['server_id']='qemu-server';clientroot['terminals'][n]['terminal']['kind']='native_import';clientroot['terminals'][n]['terminal']['proof']='native_import_released'
clientheld=copy.deepcopy(client);cohort={client['sessions'][2]['id'],client['sessions'][3]['id']}
client['sessions'][2]['operation_error']='io';client['sessions'][3]['operation_error']='state'
check('only exact two peer-close IDs preserve actual Io/State with verified proof',lambda:v.check_root_history(Checks(),'client',2,client,clientroot,clientheld,peer_close_ids=cohort))
x=copy.deepcopy(client);x['sessions'][0]['operation_error']='io';check('peer-close allowance cannot escape original two IDs',lambda:v.check_root_history(Checks(),'client',2,x,clientroot,clientheld,peer_close_ids=cohort),False)
x=copy.deepcopy(client);x['sessions'][2]['operation_error']='timeout';check('peer-close does not admit timeout or arbitrary failure',lambda:v.check_root_history(Checks(),'client',2,x,clientroot,clientheld,peer_close_ids=cohort),False)
x=copy.deepcopy(client);x['sessions'][2]['state']='Error';check('verified peer-close must still be Disconnected',lambda:v.check_root_history(Checks(),'client',2,x,clientroot,clientheld,peer_close_ids=cohort),False)
report={'schema_version':1,'scope':'constructed standalone predicate checks; no real QEMU/process/helper/USB/build acceptance','source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'checks':checks,'count':len(checks),'passed':all(x['passed'] for x in checks)}
(OUT/'fixtures-report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'passed':report['passed'],'count':len(checks),'source_sha256':report['source_sha256']}))
