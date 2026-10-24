#!/usr/bin/env python3
"""Read-only evidence parser. No product/runner imports or subprocess execution."""
from pathlib import Path
import hashlib,json,struct
B=Path('/tmp/rustusb-diagnostic-local-fixed-20261024')
S=B/'server-signals'
C=Path('/tmp/rustusb-diagnostic-local-fixed-checkout-20261024')
O=Path('/tmp/rustusb-diagnostic-local-legacy-cross-review-20261024')
HEAD='8351577459d25ae36d7ac8924cfdae7880e5292f'
def obj(p):return json.loads(p.read_text())
def sha(b):return hashlib.sha256(b).hexdigest()
def fact(p):
 b=p.read_bytes();return {'bytes':len(b),'sha256':sha(b)}
def save(n,o):(O/n).write_text(json.dumps(o,indent=2)+'\n')
paths=sorted(p for p in S.rglob('*')if p.is_file())+[B/n for n in ('server-backpressure.log','result.json','results.json','runner.py','start.json','server-signals.log')]+[C/'scripts'/n for n in ('test-server-signals.py','test-virtual-backpressure.py')]
before={str(p):fact(p) for p in paths};save('raw-before.json',before)
checks=[]
def ck(name,test,detail=None):checks.append({'predicate':name,'pass':bool(test),**({'facts':detail}if detail is not None else {})})
r=obj(B/'result.json');summary=obj(S/'summary.json');sb=obj(S/'source-before.json');sa=obj(S/'source-after.json')
ck('wrapper recorded clean835 same chosen external ELFs before/after',r['before']==r['after'] and r['guards_unchanged'] and r['before']['head']==HEAD and r['before']['clean'] and r['before']['source_matches'])
ck('signals actual summary frozen ordinary UID1000 and source inventory',summary['host_uid']==1000 and sb==sa==summary['source_before']==summary['source_after'] and sb['head']==HEAD and not sb['dirty'] and not sb['git_status'] and summary['binary_before']['sha256']==summary['binary_after_sha256']==r['before']['binary_hashes'][summary['binary_before']['path']])
source_signal=next(x for x in sb['files']if x['path']=='scripts/test-server-signals.py');ck('read frozen signals script matches actual source receipt',fact(C/source_signal['path'])=={'bytes':source_signal['length'],'sha256':source_signal['sha256']})
commands={x['name']:x for x in r['commands']}
ck('wrapper actually completed requested two legacy commands at selected binary',all(commands[k]['exit_code']==0 and commands[k]['command'][-1 if k=='server-backpressure' else -3]==summary['binary_before']['path'] for k in ('server-signals','server-backpressure')))
ck('all signal raw inventory artifacts match exact bytes',all(fact(S/a['path'])=={'bytes':a['length'],'sha256':a['sha256']}for a in summary['artifacts']))
facts=[]
for mode in ('ready','blocked'):
 for signal,num in (('sigint',2),('sigterm',15)):
  tag=mode+'-'+signal;p=S/tag;x=obj(p/'result.json');pre=obj(p/'before.json');post=obj(p/'after.json');events=obj(p/'stdout-events.json');wire=obj(p/'protocol.json');body=p.joinpath('stdout-consumed.bin').read_bytes();raw_events=[json.loads(l)for l in body.splitlines()]
  ck(tag+' summary and case copies exactly correspond',x in summary['cases'] and x['before']==pre and x['after']==post and raw_events==events)
  ck(tag+' signal targeted same actual PID/start/UID and selected proc ELF',x['child']==x['sent']['identity'] and x['child']['pid']==x['sent']['pid'] and x['child']['uids']==[1000]*4 and x['child']['starttime']>0 and x['sent']['signal']==num and x['executed_binary']['proc_exe']==f"/proc/{x['child']['pid']}/exe" and all(x['executed_binary'][k]==summary['binary_before'][k]for k in ('device','inode','sha256')) and x['executed_binary']['resolved']==summary['binary_before']['path'])
  devices=pre['snapshot']['devices'];ck(tag+' both actual configured profiles each held one pending IN',len(devices)==2 and {d['profile']for d in devices}=={'hid_keyboard','cdc_acm'} and all(d['leased'] and d['pending_urbs']==1 and d['configuration']==1 and d['counters']['sessions_started']==1 for d in devices))
  good=len(wire)==8
  for i,(bus,devid,endpoint,length)in enumerate((('1-1',65537,1,8),('1-2',65538,2,64))):
   a,b,c,d=wire[i*4:i*4+4];dev=bytes.fromhex(a['device_hex']);req=bytes.fromhex(a['request_hex']);cfg=bytes.fromhex(b['frame_hex']);ack=bytes.fromhex(c['frame_hex']);pending=bytes.fromhex(d['frame_hex'])
   good=good and a['bus_id']==bus and a['devid']==devid and bytes.fromhex(a['header_hex'])==bytes.fromhex('0111000300000000') and req[:8]==bytes.fromhex('0111800300000000') and req[8:].rstrip(b'\0').decode()==bus and len(dev)==312 and dev[256:288].rstrip(b'\0').decode()==bus
   good=good and len(cfg)==len(ack)==len(pending)==48 and struct.unpack('!5I',cfg[:20])==(1,1,devid,0,0) and cfg[40:]==bytes.fromhex('0009010000000000') and struct.unpack('!2I',ack[:8])==(3,1) and struct.unpack('!ii',ack[20:28])==(0,0) and struct.unpack('!5I',pending[:20])==(1,2,devid,1,endpoint) and struct.unpack('!i',pending[24:28])[0]==length
  ck(tag+' independent standard USBIP frame decode proves IMPORT configured and pending IN requests',good)
  ck(tag+' exact actual exit0/no forced and two postwait data TCP EOFs',x['final_returncode']==post['returncode']==0 and not x['forced_cleanup'] and post['wait_error']is None and 0<=post['signal_exit_seconds']<8 and set(post['data_sockets'])=={'1-1','1-2'} and all(v=={'eof':True,'unexpected_hex':''}for v in post['data_sockets'].values()))
  if mode=='ready':
   stopped=[e for e in raw_events if e.get('event')=='stopped'];ds=stopped[0]['snapshot']['devices'] if len(stopped)==1 else []
   ck(tag+' consumed stopped event full two-profile zero resources',len(stopped)==1 and stopped==post['stopped_events'] and len(ds)==2 and {d['profile']for d in ds}=={'hid_keyboard','cdc_acm'} and all(not d['leased'] and all(d[k]==0 for k in ('pending_urbs','configuration','hid_queued_reports','cdc_queued_bytes'))for d in ds) and post['backend_cleanup_observed'] and post['stdout_eof'])
  else:
   q=x['pipe'];w=q['blocked_writer']
   ck(tag+' independent OFD full4096 and real blocked writer before signal',q['capacity']==q['bytes_after_fill']==q['synthetic_padding_bytes']==post['stdout_unread_bytes']==4096 and len((p/'synthetic-padding.bin').read_bytes())==4096 and q['independent_writer_eagain'] and w['wchan']=='pipe_write' and w['comm']=='virtual-test-st' and w['tid']>0 and w['monotonic_ns']<x['sent']['monotonic_ns'] and q['child_stdout_flags_before']==q['child_stdout_flags_after']==q['original_writer_flags_before']==q['original_writer_flags_after']==1)
   ck(tag+' no drain and no invented stopped/backend snapshot',not x['stdout_drained_after_fill'] and not post['stdout_read_after_fill'] and not post['stdout_eof'] and not post['backend_cleanup_observed'] and post['stopped_events']==[] and 'no stopped snapshot' in x['cleanup_scope'])
  ck(tag+' percase raw artifact hashes match',all(fact(p/a['path'])=={'bytes':a['length'],'sha256':a['sha256']}for a in x['artifacts']))
  facts.append({'case':tag,'child':x['child'],'sent':x['sent'],'proc_ELF':x['executed_binary'],'exit':x['final_returncode'],'elapsed_seconds':post['signal_exit_seconds'],'pipe':x['pipe'],'cleanup_scope':x.get('cleanup_scope','actual stopped snapshot plus postwait two TCP EOFs')})
bp=obj(B/'server-backpressure.log');ck('legacy BP exact2 actual log outcomes',bp['result']=='PASS' and bp['host_uid']==1000 and [(x['mode'],x['exit'])for x in bp['checks']]==[('sigint',0),('deadline',1)] and all(x['stdout_capacity']==4096 and not x['stdout_drained_after_fill'] and x['lease_socket_closed'] for x in bp['checks']) and 0<=bp['checks'][0]['elapsed_seconds']<=2 and 4<=bp['checks'][1]['elapsed_seconds']<=7)
signal_source=(C/'scripts/test-server-signals.py').read_text();bp_source=(C/'scripts/test-virtual-backpressure.py').read_text();part=signal_source[signal_source.index('            deadline = time.monotonic() + EXIT_SECONDS'):]
ck('fixed source both paths sample data EOF after wait',part.index('process.wait(')<part.index('stream.recv(1)') and bp_source.index('process.wait(timeout=8)')<bp_source.index('stream.recv(1)'))
after={str(p):fact(p)for p in paths};save('raw-after.json',after);ck('all original reviewed bytes unchanged',before==after)
report={'result':'PASS'if all(x['pass']for x in checks)else'FAIL','method':'Separate read-only raw/byte/protocol parser; no runner imports or execution, no API/Git/Cargo/CLI/QEMU. Reviewer participated in service/product implementations, so method cross-review not independent implementation.','head_observed_in_receipts':HEAD,'counts':{'signals':4,'legacy_backpressure':2,'diagnostic_predicates':len(checks),'original_files':len(before),'signal_source_inventory_not_compiled_count':len(sb['files'])},'checks':checks,'signals':facts,'backpressure':bp,'limits':['Signals actual /proc PID/UID/start/ELF metadata are captured receipts, not retained full raw proc filesystem files.','Ready two cases have real stopped resource snapshots; blocked two have no stopped output or backend resource proof while stdout stays unread, only actual exit and two TCP EOF observations.','Both scripts sample data EOF after process wait; do not claim EOF timestamp before exit.','Old BP two cases expose only stdout summary and exact wrapper command/binary guard, not perchild PID/proc ELF or raw protocol frames. Do not borrow signal perchild evidence for BP.','Pure ordinary-user virtual loopback, no physical/kernel USB, hardware or QEMU.','This parser does not independently remap Git or rebuild the binaries; source receipt and read-only frozen script hashes are checked. Diagnostic 9+4 cases are separately reviewed by N.']}
save('report.json',report);save('manifest.json',{'files':{p.name:fact(p)for p in sorted(O.iterdir())if p.is_file()and p.name!='manifest.json'}})
print(json.dumps({'result':report['result'],'predicates':len(checks),'failures':[x for x in checks if not x['pass']],'report':str(O/'report.json'),'sha256':fact(O/'report.json')['sha256']},indent=2))
