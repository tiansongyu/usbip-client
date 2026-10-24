"""Constructed metadata/predicate fixtures only, no guest/product/old verifier run."""
from pathlib import Path
import ast,copy,hashlib,json,struct,tempfile,types
ROOT=Path('/home/ubuntu/github/usbip-client');SRC=ROOT/'scripts/qemu_process_logging/verify.py';OUT=Path(__file__).parent
v=types.ModuleType('new_predicates');ast.parse(SRC.read_text());exec(compile(SRC.read_text(),str(SRC),'exec'),v.__dict__)
U=lambda n:f'00000000-0000-0000-0000-{n:012x}'
checks=[]
def test(name,fn,accept=True):
 error=None
 try:fn()
 except Exception as e:error=f'{type(e).__name__}: {e}'
 ok=(error is None)==accept;checks.append({'name':name,'expected_accept':accept,'passed':ok,'actual_error':error})
 if not ok:raise AssertionError(name+': '+str(error))
def link(n,client=False,ended=False):
 tcp={'local':'192.0.2.1:3241','peer':f'192.0.2.2:{40000+n}'}
 if client:tcp={'local':tcp['peer'],'peer':tcp['local']}
 return {'protocol':'framed_v2','identity':{'connection':n,'tcp':tcp},'phase':'ended'if ended else'open','end':('local_stop'if client else'protocol')if ended else None,'probe':'stopped'if ended else'awaiting_first_reply'}
def session(n,role,terminal=False):
 client=role=='import';kind={'export':'native_export','incoming':'native_stub','import':'native_import'}[role]
 inst=U(20 if client else 10);sid=U(n);corr={'instance_id':inst,'session_id':sid,'attempt_id':U(100+n)}
 return {'id':sid,'instance_id':inst,'job_id':U(200+n if client else 200),'profile':'hid'if client else'server','role':role,'device_key':'native:usb-'+str(n%2)*64,'principal':'server-pin'if client else'qemu-client','terminal':terminal,'state':'Disconnected'if terminal else('Available'if role=='export'else'Connected'),'evidence':'released'if terminal else('export_ready'if role=='export'else'local_enumeration'if client else'import_reply_flushed'),'cleanup':'verified'if terminal else None,'operation_error':'state'if terminal and role=='incoming'else None,'attempts':[{'kind':kind,'correlation':corr,'resource_id':U(300+n),'proof':None}]}
def setup(path,epoch=1):
 sh={'sessions':[session(n,'export'if n<3 else'incoming')for n in (1,2,3,4)],'schema_version':2,'jobs':[{'id':U(200),'instance_id':U(10),'operator_uid':1000,'state':'running'}]}
 ch={'sessions':[session(n,'import')for n in (5,6)],'schema_version':2,'jobs':[{'id':U(200+n),'instance_id':U(20),'operator_uid':1000,'state':'running'}for n in (5,6)]}
 for state in [sh,ch]:
  state['link_history']=[{'session_id':s['id'],'instance_id':s['instance_id'],'link':None if s['role']=='export'else link((3 if s['device_key'].endswith('1')else 4),s['role']=='import')}for s in state['sessions']]
 se=copy.deepcopy(sh);ce=copy.deepcopy(ch)
 for state in [se,ce]:
  for s in state['sessions']:s.update(terminal=True,state='Disconnected',evidence='released',cleanup='verified',operation_error='state'if s['role']=='incoming'else None)
  for j in state['jobs']:j['state']='finished'
  for h in state['link_history']:
   if h['link']:h['link'].update(phase='ended',end='local_stop'if state is ce else'protocol',probe='stopped')
 data={('server',f'agent-held-{epoch}.json'):sh,('client',f'agent-held-{epoch}.json'):ch,('server',f'agent-stopped-{epoch}.json'):se,('client',f'agent-stopped-{epoch}.json'):ce}
 rx=f'RUSTUSB-RX-{epoch}-0001:0123456789abcdef\n'.encode();tx=f'RUSTUSB-TX-{epoch}-0001:fedcba9876543210\n'.encode()
 raws={('client',f'wait-agent-{epoch}.txt'):b'0\n',('client',f'rx-{epoch}.bin'):rx,('client',f'tx-{epoch}.bin'):tx,('client',f'input-{epoch}.bin'):b''.join(struct.pack('<qqHHi',0,0,1,epoch+1,value)for value in [1,0])}
 controls=[{'command':f'CLOSE{epoch}','role':'client','mark':f'close{epoch}','monotonic':1},{'command':f'STOP{epoch}','role':'server','mark':f'stop{epoch}','monotonic':2}]
 (path/'host-control.jsonl').write_text(''.join(json.dumps(r)+'\n'for r in controls));(path/'client-evidence.log').write_text(f'LOG_CLIENT_STOPPED epoch={epoch}\n');(path/f'ftdi-host-injected-{epoch}.bin').write_bytes(rx);(path/'ftdi-host-rx.bin').write_bytes(b'X'*(35*(epoch-1))+tx)
 class Original:
  evidence=path
  def data(self,role,name):return data[(role,name)]
  def raw(self,role,name):return raws[(role,name)]
  def check(self,condition,label):v.require(condition,label)
 root={'schema_version':2,'records':[],'exports':[],'terminals':[{'server_id':None,'terminal':v.expected_proof(s,s['attempts'][0])}for s in se['sessions']]}
 return Original(),data,raws,root,controls
with tempfile.TemporaryDirectory(prefix='logging-peer-fixtures-',dir=OUT)as temp:
 path=Path(temp)
 def action(mutate=None,epoch=1,history=None):
  o,d,r,root,controls=setup(path,epoch)
  if mutate:mutate(d,r,root,controls,path)
  facts=v.client_first_peer_closes(o,epoch)
  if epoch==1:
   if history:history(d,root,facts)
   v.check_root_history(o,'server',1,d[('server','agent-stopped-1.json')],root,d[('server','agent-held-1.json')],server_peer_closes=facts)
  return facts
 test('normal two exact StateProtocol errors accepted with complete original proof',lambda:action())
 test('fresh epoch3 metadata uses original connection not any earlier ID',lambda:action(epoch=3))
 test('none outcome remains distinct and allowed',lambda:action(lambda d,*_:d[('server','agent-stopped-1.json')]['sessions'][2].update(operation_error=None)))
 test('fault epoch2 cannot get this allowance',lambda:action(epoch=2),False)
 S=lambda d:d[('server','agent-stopped-1.json')]['sessions'][2]
 H=lambda d:d[('server','agent-held-1.json')]['sessions'][2]
 SH=lambda d:d[('server','agent-stopped-1.json')]['link_history'][2]['link']
 CH=lambda d:d[('client','agent-stopped-1.json')]['link_history'][0]['link']
 for name,mutate in [
  ('terminalID replacement',lambda d,*_:S(d).update(id=U(999))),
  ('changed instance',lambda d,*_:S(d).update(instance_id=U(999))),
  ('changed role',lambda d,*_:S(d).update(role='export')),
  ('changed principal',lambda d,*_:S(d).update(principal='other')),
  ('missing actual importflush evidence',lambda d,*_:H(d).update(evidence='prepared')),
  ('preclose already terminal',lambda d,*_:H(d).update(terminal=True)),
  ('new finalconnection',lambda d,*_:SH(d)['identity'].update(connection=99)),
  ('changed finaltuple',lambda d,*_:SH(d)['identity']['tcp'].update(peer='192.0.2.2:9999')),
  ('peer not LocalStop',lambda d,*_:CH(d).update(end='protocol')),
  ('State cannot hide Io end',lambda d,*_:SH(d).update(end='io')),
  ('State cannot hide timeout',lambda d,*_:SH(d).update(end='probe_timeout')),
  ('Protocol end does not permit Io category',lambda d,*_:S(d).update(operation_error='io')),
  ('Protocol end does not permit arbitrarycategory',lambda d,*_:S(d).update(operation_error='persistence')),
  ('client peer must have successful operation',lambda d,*_:d[('client','agent-stopped-1.json')]['sessions'][0].update(operation_error='state')),
  ('original attempt replaced',lambda d,*_:S(d)['attempts'][0].update(resource_id=U(999))),
  ('incorrect original attemptkind',lambda d,*_:H(d)['attempts'][0].update(kind='native_export')),
  ('unknown Rootproof cannot establish cleanup',lambda d,r,root,*_:root['terminals'][2]['terminal'].update(proof='native_export_restored')),
  ('different RootUID',lambda d,r,root,*_:root['terminals'][2]['terminal'].update(owner_uid=0)),
  ('different Rootcorrelation',lambda d,r,root,*_:root['terminals'][2]['terminal']['correlation'].update(attempt_id=U(999))),
  ('missing Rootterminal',lambda d,r,root,*_:root['terminals'].pop()),
  ('unconfirmed stays failure',lambda d,*_:S(d).update(cleanup='unconfirmed')),
  ('clientwaitfailure',lambda d,r,*_:r.update({('client','wait-agent-1.txt'):b'1\n'})),
  ('missing actual serial bytes',lambda d,r,*_:r.update({('client','rx-1.bin'):b''})),
  ('missing actual keyrelease',lambda d,r,*_:r.update({('client','input-1.bin'):struct.pack('<qqHHi',0,0,1,2,1)})),
  ('exports cannot reuse incomingexception',lambda d,*_:d[('server','agent-stopped-1.json')]['sessions'][0].update(operation_error='state')),
 ]:test(name,lambda mutate=mutate:action(mutate),False)
 def wrongorder(d,r,root,c,p):
  c[1]['monotonic']=0;(p/'host-control.jsonl').write_text(''.join(json.dumps(x)+'\n'for x in c))
 test('host serverstop before clientclose refused',lambda:action(wrongorder),False)
 def wrongrole(d,r,root,c,p):
  c[0]['role']='server';(p/'host-control.jsonl').write_text(''.join(json.dumps(x)+'\n'for x in c))
 test('hostclose command wrongrole refused',lambda:action(wrongrole),False)
 def wrongpeer(d,r,root,c,p):
  for name in ['agent-held-1.json','agent-stopped-1.json']:d[('client',name)]['link_history'][0]['link']['identity']['tcp']['local']='192.0.2.2:9999'
 test('internallystable clienttuple still must reverse server',lambda:action(wrongpeer),False)
 test('retained operation error cannot silently disappear',lambda:action(history=lambda d,r,f:S(d).update(operation_error=None)),False)
 def replaceafter(d,r,f):
  # Facts were built from real earlier originals; simulate a later retained copy.
  for key in [('server','agent-held-1.json'),('server','agent-stopped-1.json')]:d[key]=copy.deepcopy(d[key])
  for key in [('server','agent-held-1.json'),('server','agent-stopped-1.json')]:d[key]['sessions'][2]['attempts'][0]['resource_id']=U(999)
  r['terminals'][2]['terminal']['resource_id']=U(999)
 test('later held+root edits cannot substitute original cohort capability',lambda:action(history=replaceafter),False)
 test('wrongcohort epoch cannot qualify',lambda:action(history=lambda d,r,f:f[U(3)].update(epoch=2)),False)
report={'schema_version':1,'scope':'constructed metadata and byte predicates only; no actual guest/USB/CLI acceptance, no original QEMU verifier rerun','source_sha256':hashlib.sha256(SRC.read_bytes()).hexdigest(),'count':len(checks),'passed':all(c['passed']for c in checks),'checks':checks}
(OUT/'peer-close-fixtures-report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':report['passed'],'count':len(checks),'source_sha256':report['source_sha256']}))
