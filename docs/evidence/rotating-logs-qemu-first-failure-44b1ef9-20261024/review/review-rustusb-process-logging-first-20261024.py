#!/usr/bin/env python3
"""Offline log/privacy/projection review. Does not import producer or verifier."""
from pathlib import Path
import argparse,base64,hashlib,json,re,subprocess,collections
HEAD='44b1ef96be4524b43e9f914214fb9f3166519365'
UUID=re.compile(r'^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$')
FAILURES={'invalid_input','permission','protocol','io','timeout','state','unsupported','command','unsafe_device','cancelled','persistence'}
CODES={'discovery_completed','operation_started','operation_completed','operation_failed','native_attach','native_detach','native_export','native_unexport','network_ended','resource_released','diagnostic'}
TARGETS={'rustusb','rustusb_cli','rustusb_daemon','rustusb_service','rustusb_native','rustusb_linux','rustusb_protocol','rustusb_core','rustusb_rust_backend','rustusb_transport'}
def strict(pairs):
 d={}
 for k,v in pairs:
  if k in d:raise ValueError('duplicate JSON field')
  d[k]=v
 return d
def decode(raw):return json.loads(raw,object_pairs_hook=strict,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite')))
def digest(p):
 b=p.read_bytes();return {'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def uint(v,maxval=(1<<64)-1):return type(v)is int and 0<=v<=maxval

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--run',type=Path,default=Path('/tmp/rustusb-process-logging-qemu-first-coordinator-20261024/qemu'));ap.add_argument('--repo',type=Path,default=Path('/home/ubuntu/github/usbip-client'));ap.add_argument('--output',type=Path,default=Path('/tmp/rustusb-process-logging-qemu-first-log-review-20261024'));a=ap.parse_args()
 summary=decode((a.run/'summary.json').read_bytes())
 if not summary.get('completed_utc'):raise SystemExit('run not terminal; no verdict made')
 a.output.mkdir(exist_ok=True)
 originals={str(p.relative_to(a.run)):digest(p) for p in sorted(a.run.rglob('*')) if p.is_file()}
 checks=[];facts={};stores={};raw_records=[]
 def ck(name,value,detail=None):checks.append({'assertion':name,'pass':bool(value),**({'detail':detail} if detail is not None else {})})
 def raw(role,name):
  if role not in ['server','client'] or '/' in name or name in ['.','..']:raise ValueError('fixed original path')
  return (a.run/(role+'-artifacts')/name).read_bytes()
 def data(role,name):return decode(raw(role,name))
 def jlines(role,name):return [decode(l) for l in raw(role,name).splitlines() if l]
 def status(s):
  fields={'healthy','closing','queued','accepted','written','dropped','oversized','projection_errors','overflowed','last_written','failure'}
  return set(s)==fields and all(type(s[k])is bool for k in ['healthy','closing','overflowed']) and all(uint(s[k]) for k in ['queued','accepted','written','dropped','oversized','projection_errors']) and s['queued']<=256 and s['written']<=s['accepted'] and (s['failure'] is None or s['failure'] in FAILURES) and (not s['healthy'] or (s['dropped']==s['oversized']==s['projection_errors']==0 and not s['overflowed'] and s['failure'] is None))
 def record(r):
  if set(r)!={'schema_version','epoch','sequence','elapsed_millis','wall_unix_millis','level','event'} or r['schema_version']!=1 or not UUID.fullmatch(r['epoch']) or not uint(r['sequence']) or r['sequence']==0 or not uint(r['elapsed_millis']) or (r['wall_unix_millis'] is not None and not uint(r['wall_unix_millis'])) or r['level'] not in ['trace','debug','info','warn','error']:return False
  e=r['event'];kind=e.get('kind')
  if kind=='started':return set(e)=={'kind','pid','uid'} and uint(e['pid'],(1<<32)-1) and e['pid']>0 and uint(e['uid'],(1<<32)-1)
  if kind=='stopped':return set(e)=={'kind','status'} and status(e['status']) and e['status']['closing'] and e['status']['queued']==0
  if kind=='diagnostic':
   if set(e)!={'kind','code','target','line','failure','errno','fields','omitted_fields'} or e['code'] not in CODES or len(e['target'])>128 or not re.fullmatch('[a-z0-9_:]+',e['target']) or not any(e['target']==t or e['target'].startswith(t+'::') for t in TARGETS) or (e['failure'] is not None and e['failure'] not in FAILURES) or not uint(e['omitted_fields'],(1<<32)-1):return False
   if e['line'] is not None and (not uint(e['line'],(1<<32)-1) or not e['line']):return False
   if e['errno'] is not None and not (type(e['errno'])is int and 1<=e['errno']<=4095):return False
   limits={'count':(1<<32)-1,'device_count':(1<<32)-1,'session_count':(1<<32)-1,'interface':255,'bytes_to_peer':(1<<64)-1,'bytes_from_peer':(1<<64)-1,'elapsed_millis':(1<<64)-1}
   return all((k in ['success','network_closed'] and type(v)is bool) or (k in limits and uint(v,limits[k])) for k,v in e['fields'].items())
  if kind=='audit':
   if set(e)!={'kind','application_instance','audit_sequence','actor_uid','profile','job_id','session_id','action','failure','state','evidence','cleanup'}:return False
   return bool(UUID.fullmatch(e['application_instance'])) and uint(e['audit_sequence']) and e['audit_sequence']>0 and uint(e['actor_uid'],(1<<32)-1) and (e['profile'] is None or bool(re.fullmatch('[A-Za-z0-9_.-]{1,64}',e['profile']))) and all(e[k] is None or UUID.fullmatch(e[k]) for k in ['job_id','session_id']) and e['action'] in ['start','denied','stop','admission','attempt','resource','observe','finish','recovery','discover'] and (e['failure'] is None or e['failure'] in FAILURES) and (e['state'] is None or e['state'] in ['Discovered','Available','Exporting','Connecting','Connected','Disconnecting','Disconnected','Error','Recovering']) and (e['evidence'] is None or e['evidence'] in ['intent','prepared','local_enumeration','export_ready','import_reply_flushed','closing','released','unconfirmed','root_journal_proof']) and (e['cleanup'] is None or e['cleanup'] in ['not_started','verified','unconfirmed'])
  return False
 def segment(b,label):
  ls=b.splitlines(keepends=True);h=decode(ls[0]);rs=[];off=len(ls[0]);offsets={}
  ck(label+' complete header/schema/byte bound',len(b)<=4096 and b.endswith(b'\n') and len(ls[0])<=256 and set(h)=={'schema_version','segment','epoch','ordinal'} and h['schema_version']==1 and bool(UUID.fullmatch(h['segment'])) and bool(UUID.fullmatch(h['epoch'])) and uint(h['ordinal']) and h['ordinal']>0)
  for l in ls[1:]:
   r=decode(l);off+=len(l);rs.append(r);offsets[h['segment']+':'+str(off)]=r
   ck(label+' strict complete payload-free record '+str(r['sequence']),len(l)<=2048 and l.endswith(b'\n') and record(r) and r['epoch']==h['epoch'])
  ck(label+' strictly increasing sequence not elapsed timestamps',all(x['sequence']<y['sequence'] for x,y in zip(rs,rs[1:])))
  return {'header':h,'records':rs,'offsets':offsets}
 ck('actual terminal FAIL retained independently',summary['result']=='FAIL' and bool(summary['completed_utc']))
 ck('exact clean44b1 run guards and two actual guest0',summary['head']==HEAD and summary['head_after']==HEAD and summary['git_status_before']==summary['git_status_after']=='' and all(summary[k] for k in ['source_guard','binary_guard','head_guard','clean_guard']) and summary['guest_exit_codes']=={'server':0,'client':0})
 ck('locked real allfeatures build not skip',summary['build_skipped'] is False and summary['build_features']=='--workspace --all-features --release --locked' and b'Finished `release` profile' in (a.run/'build.log').read_bytes())
 ck('189 actual frozen input files',len(summary['source_inputs'])==189)
 for n,r in summary['source_inputs'].items():
  b=(a.run/'source'/n).read_bytes();gb=subprocess.check_output(['git','show',HEAD+':'+n],cwd=a.repo)
  ck('source-copy and exact Git44b1 '+n,len(b)==r['bytes'] and hashlib.sha256(b).hexdigest()==r['sha256'] and gb==b)
 old=decode(Path('/tmp/rustusb-logging-software-second-20261024/inputs-after.json').read_bytes())
 ck('all177 previous actual723 software inputs embedded unchanged',len(old)==177 and all(summary['source_inputs'].get(x['path'])=={'bytes':x['bytes'],'sha256':x['sha256']} for x in old))
 for n,r in summary['artifact_manifest'].items():ck('host original manifest '+n,digest(a.run/n)==r)
 decoded_counts={}
 for role in ['server','client']:
  text=(a.run/(role+'-evidence.log')).read_text();ls=text.splitlines();i=0;found={}
  while i<len(ls):
   m=re.fullmatch(r'SECURE_ARTIFACT_BEGIN ([A-Za-z0-9_.-]+) ([0-9]+)',ls[i])
   if not m:i+=1;continue
   name,expected=m[1],int(m[2]);j=i+1;parts=[]
   while j<len(ls) and ls[j]!='SECURE_ARTIFACT_END '+name:parts.append(ls[j]);j+=1
   if j==len(ls):raise ValueError('unterminated original base64 block')
   b=base64.b64decode(''.join(parts),validate=True);ck(role+' original base64 exact '+name,name not in found and len(b)==expected and raw(role,name)==b);found[name]=expected;i=j+1
  actual={p.name for p in (a.run/(role+'-artifacts')).iterdir() if p.is_file()};ck(role+' all decoded artifacts genuinely present in serial blocks',actual==set(found));decoded_counts[role]=len(found)
  ck(role+' actual guest collection PASS and no error marker',text.count('RUSTUSB_LOGGING_RESULT: PASS role='+role)==1 and not any(x in text for x in ['RUSTUSB_LOGGING_RESULT: FAIL','SECURE_ARTIFACT_ERROR','SECURE_ARTIFACT_OVERSIZE','SECURE_POWEROFF_RETURNED']))
  ck(role+' final kernel no panic/Oops',not any(x in (a.run/(role+'-serial.log')).read_text(errors='replace') for x in ['Kernel panic','Oops:']))
  init=(a.run/(role+'-generated-logging-init.sh')).read_bytes();ck(role+' exact generatedinit/payload',hashlib.sha256(init).hexdigest()==summary['payload_sha256'][role]['init'])
  ancestors=jlines(role,'logs-ancestors.jsonl');ck(role+' all four rootprotected log ancestors',len(ancestors)==4 and {x['path'] for x in ancestors}=={'/','/var','/var/log','/var/log/rustusb'} and all(x['path']==x['canonical'] and x['uid']==x['gid']==0 and int(x['mode'],8)&0o022==0 for x in ancestors))
  for process,epochs,uid,program in [('helper',['final'],0,'rustusb-helper'),('agent',[1,2,3],2000,'rustusb')]:
   process_ids=[];log_epochs=[]
   for epoch in epochs:
    label=process if process=='helper' else 'agent-'+str(epoch);statusbytes=raw(role,'identity-'+label+'.txt');fields=dict(l.split(':',1) for l in statusbytes.decode().splitlines() if ':' in l);pid=int(fields['Pid']);process_ids.append(pid)
    ck(role+' '+label+' actual OSuid and sampled ELF',fields['Uid'].split()==[str(uid)]*4 and raw(role,'executable-'+label+'.txt').decode().split()[0]==summary['binary_sha256'][program] and summary['payload_sha256'][role]['usr/bin/'+program]==summary['binary_sha256'][program])
    if uid:ck(role+' '+label+' zero capabilities',all(int(fields[x],16)==0 for x in ['CapInh','CapPrm','CapEff','CapAmb']))
    if process=='agent':
     stat=raw(role,'proc-'+label+'.stat').decode();rest=stat[stat.rfind(')')+2:].split();ck(role+' '+label+' independent saved procstat starttime',int(stat.split(' ',1)[0])==pid and int(rest[19])>0)
    prefix='logs-'+process+'-'+str(epoch)+'-';meta=data(role,prefix+'metadata.json');dirname='/var/log/rustusb/'+process;directory=next(x for x in meta if x['path']==dirname)
    ck(role+' '+prefix+' actual directory0700/owner',directory['kind']=='directory' and directory['canonical']==dirname and directory['uid']==directory['gid']==uid and directory['mode']=='700' and not directory['capture'])
    byname={x['path'].rsplit('/',1)[-1]:x for x in meta if x['path']!=dirname};ck(role+' '+prefix+' unique fixed finite names',len(byname)+1==len(meta) and {'writer.lock','access.lock','segment-00.ndjson'}<=set(byname)<={'writer.lock','access.lock'}|{'segment-%02d.ndjson'%i for i in range(4)})
    segments=[]
    for name,m in byname.items():
     ck(role+' '+prefix+name+' actual regular0600/nlink1',m['uid']==m['gid']==uid and m['mode']=='600' and m['links']==1 and m['kind'] in ['regular file','regular empty file'] and m['path']==m['canonical']==dirname+'/'+name)
     if name.endswith('.lock'):ck(role+' '+prefix+name+' zero lock',m['bytes']==0 and not m['capture']);continue
     b=raw(role,m['capture']);ck(role+' '+prefix+name+' captured exact bounded bytes',len(b)==m['bytes'] and len(b)<=4096 and m['capture']==prefix+name);segments.append(segment(b,role+'/'+m['capture']))
    segments.sort(key=lambda x:x['header']['ordinal']);allr=[r for seg in segments for r in seg['records']];raw_records+=allr
    ck(role+' '+prefix+' at most4 segment order/unique IDs',1<=len(segments)<=4 and len({s['header']['segment'] for s in segments})==len(segments) and all(x['header']['ordinal']+1==y['header']['ordinal'] for x,y in zip(segments,segments[1:])))
    ck(role+' '+prefix+' distinct admission identity/order',len({(r['epoch'],r['sequence']) for r in allr})==len(allr) and all(x['epoch']!=y['epoch'] or x['sequence']<y['sequence'] for x,y in zip(allr,allr[1:])))
    startup=segment(raw(role,'logs-helper-started.ndjson' if process=='helper' else prefix+'started.ndjson'),role+'/'+prefix+'startup');starts=[r for r in startup['records'] if r['event']['kind']=='started'];ck(role+' '+prefix+' actual current process startup',len(starts)==1 and starts[0]['sequence']==1 and starts[0]['event']=={'kind':'started','pid':pid,'uid':uid});epochid=starts[0]['epoch'];log_epochs.append(epochid)
    end=[r for r in allr if r['epoch']==epochid and r['event']['kind']=='stopped'];ck(role+' '+prefix+' exactly one actual final Stopped',len(end)==1 and allr[-1]==end[0] and segments[-1]['header']['epoch']==epochid)
    st=end[0]['event']['status'];lost=role=='server' and process=='agent' and epoch==2
    ck(role+' '+prefix+' complete actual accepted/written drain',st['accepted']==st['written'] and st['queued']==0 and st['oversized']==st['projection_errors']==0 and st['failure'] is None and not st['overflowed'])
    ck(role+' '+prefix+' distinct loss/healthy status',(st['dropped']>0 and not st['healthy']) if lost else (st['dropped']==0 and st['healthy']))
    waitname='wait-helper.txt' if process=='helper' else 'wait-agent-'+str(epoch)+'.txt';ck(role+' '+prefix+' actual parentwait expected',(raw(role,waitname)==(b'1\n' if lost else b'0\n')))
    if process=='agent':
     err=raw(role,'agent-'+str(epoch)+'.log');ck(role+' '+prefix+' file-only no console or fixed final LostRecords',err==b'logging failed after the requested operation; inspect its result before retrying: logging lost records; the requested operation may have completed\n' if lost else err==b'');ck(role+' '+prefix+' machine stdout unpolluted',raw(role,'agent-'+str(epoch)+'.stdout')==b'')
    stores[(role,process,epoch)]={'pid':pid,'epoch':epochid,'status':st,'records':allr,'segments':[s['header'] for s in segments],'startup_ordinal':startup['header']['ordinal']}
   if process=='agent':ck(role+' three process+log epochs genuinely distinct',len(set(process_ids))==len(set(log_epochs))==3)
  ck(role+' restarted epoch advances sealed ordinal',all(stores[(role,'agent',e)]['startup_ordinal']==stores[(role,'agent',e-1)]['segments'][-1]['ordinal']+1 for e in [2,3]))
  ck(role+' permission refusals did not change actual root/resource bytes',raw(role,'root-before-log-refusals.json')==raw(role,'root-after-log-refusals.json') and raw(role,'resources-before-log-refusals.json')==raw(role,'resources-after-log-refusals.json'))
  for why,rc in [('uid',1),('profile',1),('path',2)]:ck(role+' actual refused read '+why,raw(role,'logs-denied-'+why+'.wait')==f'{rc}\n'.encode() and raw(role,'logs-denied-'+why+'.stdout')==b'' and bool(raw(role,'logs-denied-'+why+'.stderr')))
  for epoch in [1,2,3]:
   state=data(role,'agent-stopped-'+str(epoch)+'.json');audit=state['audit']
   for r in stores[(role,'agent',epoch)]['records']:
    e=r['event']
    if e['kind']!='audit':continue
    wanted={k:v for k,v in e.items() if k not in ['kind','application_instance','audit_sequence']};wanted['sequence']=e['audit_sequence'];ck(role+' epoch'+str(epoch)+' retained log exact committed audit '+str(e['audit_sequence']),audit.count(wanted)==1)
  ck(role+' helper logs have no fabricated application audit',all(r['event']['kind']!='audit' for r in stores[(role,'helper','final')]['records']))
 # Bind committing application epochs to the corresponding actual log process,
 # including old sealed segments retained by a later process.
 for role in ['server','client']:
  epochmap={}
  for epoch in [1,2,3]:
   live={x['instance_id'] for x in data(role,'agent-held-'+str(epoch)+'.json')['sessions'] if not x['terminal']}
   ck(role+' epoch'+str(epoch)+' one actual current appinstance',len(live)==1)
   epochmap[stores[(role,'agent',epoch)]['epoch']]=next(iter(live))
  for epoch in [1,2,3]:
   for r in stores[(role,'agent',epoch)]['records']:
    if r['event']['kind']=='audit':ck(role+' exact committing instance/log epoch '+str(r['sequence']),r['event']['application_instance']==epochmap[r['epoch']])
  manifest=data(role,'agent-manifest.json')
  ck(role+' actual fixed broker/audit operator profile policy',manifest['broker_uid']==2000 and len(manifest['operators'])==1 and manifest['operators'][0]['uid']==1000 and 'audit' in manifest['operators'][0]['actions'] and set(manifest['operators'][0]['profiles'])==({'server'} if role=='server' else {'hid','serial'}))
  refusals=data(role,'agent-after-log-refusals.json')['audit']
  ck(role+' actual denied foreign UID and own unscoped audit',any(x['actor_uid']==1002 and x['action']=='denied' and x['failure']=='permission' for x in refusals) and any(x['actor_uid']==1000 and x['profile'] is None and x['action']=='denied' for x in refusals))
  for epoch in [1,2,3]:
   prefix='actual-config-'+str(epoch)+'-';actual=data(role,prefix+'agent.json');meta=data(role,'config-metadata-'+str(epoch)+'.json');by={x['path']:x for x in meta};row=by['/etc/rustusb/agent.json'];key=by['/etc/rustusb/'+role+'-key.der']
   ck(role+' epoch'+str(epoch)+' actual unchanged rootcontrolled manifest',actual==manifest and row['canonical']==row['path'] and row['uid']==row['gid']==0 and row['mode']=='644' and row['links']==1 and row['kind']=='public_file' and row['captured_file']==prefix+'agent.json')
   ck(role+' epoch'+str(epoch)+' private key metadata only broker0600',key['uid']==key['gid']==2000 and key['mode']=='600' and key['links']==1 and key['kind']=='private_key_metadata_only' and key['captured_file'] is None and key['bytes']>0)
 page_totals={}
 for role,labels in [('server',{'server-normal':'server','server-rotated':'server'}),('client',{'hid-normal':'hid','serial-normal':'serial'})]:
  state=data(role,'agent-stopped-1.json')
  for label,profile in labels.items():
   paths=sorted((a.run/(role+'-artifacts')).glob('read-'+label+'-*.json'),key=lambda p:int(p.stem.rsplit('-',1)[1]));nums=[int(p.stem.rsplit('-',1)[1]) for p in paths];ck(role+'/'+label+' actual finite contiguous page sequence',bool(paths) and nums==list(range(1,len(nums)+1)) and len(paths)<=80)
   seen=set();hits=0;previous=None
   for number,p in enumerate(paths,1):
    wrapper=decode(p.read_bytes());pg=wrapper['page'];ck(role+'/'+label+' typed bounded page '+str(number),set(wrapper)=={'schema_version','event','page'} and wrapper['schema_version']==1 and wrapper['event']=='logs' and pg['schema_version']==1 and len(pg['records'])<=16 and pg['scanned_bytes']<=262144 and pg['partial_tail'] is False and pg['status'] is not None and status(pg['status']) and pg['status']['healthy'])
    ck(role+'/'+label+' strict forward/final cursor '+str(number),(pg['more'] is True and pg['next_after'] is not None and pg['next_after']!=previous) if number<len(paths) else pg['more'] is False);previous=pg['next_after']
    for r in pg['records']:
     e=r['event'];key=(r['epoch'],r['sequence']);ck(role+'/'+label+' exact authorized audit not broad process record '+str(key),record(r) and e['kind']=='audit' and (e['profile']==profile or (e['profile'] is None and e['action']=='denied' and e['actor_uid']==1000)) and key not in seen);seen.add(key);hits+=int(e['profile']==profile)
     wanted={k:v for k,v in e.items() if k not in ['kind','application_instance','audit_sequence']};wanted['sequence']=e['audit_sequence'];ck(role+'/'+label+' page exact already committed audit '+str(e['audit_sequence']),state['audit'].count(wanted)==1)
   if label.endswith('normal'):ck(role+'/'+label+' positive profile evidence not empty permission test',hits>0)
   page_totals[role+'/'+label]={'pages':len(paths),'records':len(seen),'exact_profile_records':hits}
 ck('normal server actual rotation reaches4 slots',len(stores[('server','agent',1)]['segments'])==4 and stores[('server','agent',1)]['segments'][-1]['ordinal']>stores[('server','agent',1)]['startup_ordinal'])
 for label,count in [('normal',64),('blocked',512)]:
  rows=jlines('server','flood-'+label+'-receipts.jsonl');before=data('server','flood-'+label+'-before.json');after=data('server','flood-'+label+'-after.json')
  def owners(s):return {'jobs':s['jobs'],'requests':s['requests'],'sessions':[{k:v for k,v in r.items() if k!='metrics'} for r in s['sessions']]}
  ck(label+' exact actual committed denial count/resource identity',len(rows)==count and [r['index'] for r in rows]==list(range(1,count+1)) and owners(before)==owners(after))
  previous=before['sequence']
  for r in rows:
   e=r['audit'];ck(label+' actual denied+committed sequence '+str(r['index']),r['wait']==1 and r['response']['journal_healthy'] is True and r['response']['response']=={'result':'error','code':'permission'} and r['sequence']>previous and previous<e['sequence']<=r['sequence'] and e['actor_uid']==1000 and e['action']=='denied' and e['profile'] is None);previous=r['sequence']
 # Privacy proof scope: only typed persisted log records, not raw test collectors.
 sensitive=[b'unknown-profile',b'RUSTUSB-RX-',b'RUSTUSB-TX-',b'-----BEGIN',b'PRIVATE KEY',b'/etc/rustusb/']
 for cert in ['ca','server','client']:sensitive.append(hashlib.sha256((a.run/(cert+'-public.der')).read_bytes()).hexdigest().encode())
 for role in ['server','client']:
  ck(role+' fixed collector contains no private key DER files',not any(p.name.endswith('-key.der') for p in (a.run/(role+'-artifacts')).iterdir()))
  for p in sorted((a.run/(role+'-artifacts')).glob('logs-*.ndjson')):ck(role+' stored typed bytes exclude actual testpayload/certpin/pathsentinels '+p.name,not any(x in p.read_bytes() for x in sensitive))
 failure_id='6833749c-1ecf-4e2a-ad25-1699904b6c66';state=data('server','agent-stopped-1.json');failed=next(s for s in state['sessions'] if s['id']==failure_id);held1=data('server','agent-held-1.json');prior=next(s for s in held1['sessions'] if s['id']==failure_id)
 facts={'original_run_result':summary['result'],'original_primary_failure':summary['error'],'primary_failed_session':{'held':prior,'ended':failed},'page_totals':page_totals,'status_by_owner':{'/'.join(map(str,k)):v['status'] for k,v in stores.items()},'process_log_epochs':{'/'.join(map(str,k)):v['epoch'] for k,v in stores.items()},'actual_base64_artifact_counts':decoded_counts,'source_inputs':len(summary['source_inputs']),'software_source_subset':177,'stored_record_copy_count_not_cases':len(raw_records),'actual_ELF_hashes':summary['binary_sha256'],'guest_exit_codes':summary['guest_exit_codes']}
 ck('primary failure is observed operationState not changed cleanup result',prior['state']=='Connected' and prior['terminal'] is False and failed['operation_error']=='state' and failed['state']=='Disconnected' and failed['terminal'] and failed['cleanup']=='verified' and failed['evidence']=='released')
 after={str(p.relative_to(a.run)):digest(p) for p in sorted(a.run.rglob('*')) if p.is_file()};ck('all746 originals file set+bytes unchanged during parser',originals==after and len(originals)==746)
 result={'schema_version':1,'log_scope_review':'PASS' if all(c['pass'] for c in checks) else 'FAIL','actual_overall_run_result':'FAIL_PRESERVED','run':str(a.run),'completed_utc':summary['completed_utc'],'head':summary['head'],'scope':'Independent offline raw-method checks for logs/permissions/pages/committed projection/ELF provenance. Reviewer authored record/worker, not fully independent implementation review. No producer/verifier import or execution. H separately checks USB/gate lifecycle.','facts':facts,'checks':checks,'limits':['Overall host FAIL remains, irrespective of logging subchecks. No original verdict, source, raw bytes or later helper proof modified.','Original primary aggregator failure required incoming operation_errorNone; actual known session endedState with cleanupVerified. Exact peerclose interpretation belongs to separate source/health review, not assumed away here.','No count here denotes extra USB cases or hardware acceptance; three epochs/actualIO/gate proof are H separate raw method.','Elapsed timestamps need not increase across concurrent producers; only admission sequence/order was checked.','Exact audit metadata agrees with captured committed state; logs remain best-effort projection, never business WAL authority or cleanup witness.','No diagnostic export, key rotation/revocation, G1/G2/hardware, permanent FS cancellation or completePhase3 claim.','Private-key file bytes are not collected by fixed source and no such named files occur; this is bounded collector/log whitelist evidence, not a universal secret-leak proof.','No external API/CLI/build/QEMU/USB action was run by parser.'],'raw_manifest':'raw-artifacts.json'}
 (a.output/'raw-artifacts.json').write_text(json.dumps(originals,indent=2)+'\n');(a.output/'review.json').write_text(json.dumps(result,indent=2)+'\n')
 small={k:v for k,v in result.items() if k not in ['checks','facts']};small['facts']={k:v for k,v in facts.items() if k!='primary_failed_session'};small['primary_observation']={k:failed[k] for k in ['id','role','operation_error','cleanup','state','evidence','terminal']};small['check_count']=len(checks);small['failures']=[c for c in checks if not c['pass']];(a.output/'summary.json').write_text(json.dumps(small,indent=2)+'\n')
 print(json.dumps({'log_scope_review':result['log_scope_review'],'overall_run':'FAIL_PRESERVED','checks':len(checks),'failures':[c for c in checks if not c['pass']],'page_totals':page_totals,'status_summary':{k:{f:v[f] for f in ['healthy','accepted','written','dropped']} for k,v in facts['status_by_owner'].items()},'summary':str(a.output/'summary.json'),'summary_integrity':digest(a.output/'summary.json')},indent=2))
if __name__=='__main__':main()
