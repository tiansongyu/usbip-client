#!/usr/bin/env python3
"""Read-only independent artifact/alias/authorization parser; no producer imports."""
from pathlib import Path
from collections import defaultdict
import base64,hashlib,json,re,struct
BASE=Path('/tmp/rustusb-diagnostic-qemu-second-d9ebe2e-20261024');OUT=Path(__file__).resolve().parent
HEAD='d9ebe2eadf8490081035199c91d17bbba41bc57e';CLI='9708a50ee836378cd64f8726dae36caded96d4fca39faae215aa30658ef49b48';HELPER='7c690c62dbc02b7e08b224e2f3cfe773c77bebc6a8e6c10ebd716b2696806322'
checks=[];readset={};counts=defaultdict(int);aliases=defaultdict(dict);reverse=defaultdict(dict)
def sha(b):return hashlib.sha256(b).hexdigest()
def read(p):
 b=p.read_bytes();readset[str(p)]={'path':str(p),'bytes':len(b),'sha256':sha(b)};return b
def strict(pairs):
 d={}
 for k,v in pairs:
  if k in d:raise ValueError('duplicate JSON key '+k)
  d[k]=v
 return d
def loads(b):return json.loads(b,object_pairs_hook=strict)
def load(p):return loads(read(p))
def check(name,ok):
 checks.append({'id':name,'ok':bool(ok)})
 if not ok:raise ValueError(name)
def keys(obj,names,label):check('closed:'+label,isinstance(obj,dict) and set(obj)==set(names.split()))
def bind(ns,raw,value):
 if raw is None:check('nullAlias:'+ns,value is None);return
 check('aliasRange:'+ns,type(value)is int and 1<=value<=8192)
 if raw in aliases[ns]:check('aliasStable:'+ns,aliases[ns][raw]==value)
 if value in reverse[ns]:check('aliasInjective:'+ns,reverse[ns][value]==raw)
 aliases[ns][raw]=value;reverse[ns][value]=raw

def inv():return [{'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())}for p in sorted(BASE.rglob('*'))if p.is_file()]
before=inv()
try:
 summary=load(BASE/'summary.json');check('terminalRun',summary['result']=='PASS' and summary['completed_utc'] and summary['guest_exit_codes']=={'server':0,'client':0})
 check('savedSourceGuards',summary['head']==summary['head_after']==HEAD and summary['git_status_before']==summary['git_status_after']=='' and all(summary[k]for k in ['source_guard','binary_guard','head_guard','clean_guard']))
 check('selectedELF',summary['binary_sha256']=={'rustusb':CLI,'rustusb-helper':HELPER})
 for rel,row in summary['source_inputs'].items():
  p=BASE/'source'/rel;check('source:'+rel,len(read(p))==row['bytes'] and sha(read(p))==row['sha256'])
 check('selectedSource209',len(summary['source_inputs'])==209)
 compiled=load(Path('/tmp/rustusb-diagnostic-software-second-20261024/inputs-before.json'));check('compiled192',len(compiled)==192)
 for row in compiled:check('compiledMap:'+row['path'],row['path']in summary['source_inputs'] and summary['source_inputs'][row['path']]=={'bytes':row['bytes'],'sha256':row['sha256']})
 for role in ['server','client']:
  lines=read(BASE/(role+'-evidence.log')).decode().splitlines();decoded={};i=0
  while i<len(lines):
   m=re.fullmatch(r'SECURE_ARTIFACT_BEGIN (\S+) (\d+)',lines[i])
   if not m:i+=1;continue
   name,size=m.group(1),int(m.group(2));check('safeArtifactName:'+role+':'+name,Path(name).name==name and name not in decoded)
   j=i+1;pieces=[]
   while j<len(lines) and lines[j]!='SECURE_ARTIFACT_END '+name:pieces.append(lines[j].strip());j+=1
   check('artifactEnd:'+role+':'+name,j<len(lines));data=base64.b64decode(''.join(pieces),validate=True)
   check('artifactBytes:'+role+':'+name,len(data)==size and data==read(BASE/(role+'-artifacts')/name));decoded[name]=data;i=j+1
  check('roleTerminalMarker:'+role,lines.count('RUSTUSB_DIAGNOSTIC_RESULT: PASS role='+role)==1 and not any('RUSTUSB_DIAGNOSTIC_RESULT: FAIL'in l for l in lines));counts['decoded_'+role]=len(decoded)
 S=BASE/'server-artifacts';raw=read(S/'diagnostic-bundle.json');doc=loads(raw);receipt=load(S/'export-held.stdout')
 check('bundleActualBytes',len(raw)==26173 and sha(raw)=='f433247df9d17cf9f6653efa5b3b5f2c54b37709f9f945e59d9c389904e05013' and raw.endswith(b'\n'))
 check('bundleReceipt',receipt['file']['result']=='written' and receipt['file']['receipt']=={'publication':'published','partial_name':None,'written_bytes':len(raw),'sha256':sha(raw)} and receipt['capture']==doc['capture']=='complete' and receipt['summary']==doc['summary'] and doc['summary']['complete'] and read(S/'export-held.wait').strip()==b'0')
 keys(doc,'schema_version capture summary program_version build privacy limits alias_counts requested_agent_scope native helper agent health logs','document')
 keys(doc['summary'],'complete sections','summary')
 for section in doc['summary']['sections']:keys(section,'stage outcome failure observed_at_millis','section');check('completeSection:'+section['stage'],section['outcome']=='complete' and section['failure']is None)
 keys(doc['privacy'],'policy_version identifiers_aliased paths_and_network_addresses_omitted credentials_and_payloads_omitted raw_messages_omitted independent_section_samples network_security_verified hardware_test_performed','privacy')
 check('schemaVersionOne',doc['schema_version']==1)
 check('privacyPolicy',doc['privacy']['policy_version']==1 and not doc['privacy']['network_security_verified'] and not doc['privacy']['hardware_test_performed'] and all(doc['privacy'][k]for k in ['identifiers_aliased','paths_and_network_addresses_omitted','credentials_and_payloads_omitted','raw_messages_omitted','independent_section_samples']))
 keys(doc['limits'],'serialized_bytes aliases jobs sessions audit health log_records log_pages','limits');check('boundedFile',len(raw)<=doc['limits']['serialized_bytes']==4194304 and doc['limits']['aliases']==8192)
 keys(doc['requested_agent_scope'],'profile caller','requestedScope');keys(doc['build'],'secure_agent_collection','build');check('secureBuild',doc['build']['secure_agent_collection'])
 keys(doc['program_version'],'major minor patch','programVersion');keys(doc['native'],'os architecture kernel kernel_numeric usbip_host vhci_hcd usbip usbipd','native');keys(doc['native']['kernel_numeric'],'major minor patch','kernelNumeric')
 for m in ['usbip_host','vhci_hcd']:keys(doc['native'][m],'loaded sysfs availability',m)
 keys(doc['helper'],'status peer_verified kernel_available active_sessions external_trusted_network_policy_statement','helper')
 old=load(S/'selected-committed.json');draft=load(S/'selected-draft.json');agent=doc['agent'];profile='server';uid=1000
 check('oldStateBound',old['schema_version']==2 and old['sequence']==46 and old==load(S/'agent-gate-before.json')==load(S/'agent-gate-after.json')==load(S/'committed-1.json'))
 check('withheldDifference',draft['sequence']==47 and [k for k in old if old[k]!=draft[k]]==['sequence','audit'] and draft['audit'][:-1]==old['audit'] and draft['audit'][-1]['sequence']==47 and draft['audit'][-1]['action']=='denied')
 keys(agent,'application_epoch profile caller backend committed_sequence publication_age_millis audit_oldest_retained_sequence expires_after_millis expected_jobs expected_sessions expected_audit jobs sessions audit','agent')
 check('committedHeader',agent['committed_sequence']==46 and agent['backend']=='native' and agent['expires_after_millis']==30000 and(agent['expected_jobs'],agent['expected_sessions'],agent['expected_audit'])==(1,4,44))
 bind('profiles',profile,doc['requested_agent_scope']['profile']);bind('uids',uid,doc['requested_agent_scope']['caller']);bind('profiles',profile,agent['profile']);bind('uids',uid,agent['caller'])
 jobs=sorted([j for j in old['jobs']if j['profile']==profile],key=lambda r:r['id']);sessions=sorted([r for r in old['sessions']if r['profile']==profile],key=lambda r:r['id']);audit=[e for e in old['audit']if e['profile']==profile or(e['profile']is None and e['action']=='denied'and e['actor_uid']==uid)]
 check('allAuthorizedRows',len(jobs)==len(agent['jobs'])==1 and len(sessions)==len(agent['sessions'])==4 and len(audit)==len(agent['audit'])==44)
 epoch=jobs[0]['instance_id'];bind('application_epochs',epoch,agent['application_epoch'])
 for a,b in zip(jobs,agent['jobs']):
  keys(b,'job application_epoch profile operator state stop_requested operation_error','job');bind('jobs',a['id'],b['job']);bind('application_epochs',a['instance_id'],b['application_epoch']);bind('profiles',a['profile'],b['profile']);bind('uids',a['operator_uid'],b['operator'])
  for k in ['state','stop_requested','operation_error']:check('jobScalar:'+k,a[k]==b[k])
 for a,b in zip(sessions,agent['sessions']):
  keys(b,'session application_epoch job profile role device principal state evidence stop_requested terminal operation_error cleanup metrics attempts','session')
  for ns,source,target in [('sessions','id','session'),('application_epochs','instance_id','application_epoch'),('jobs','job_id','job'),('profiles','profile','profile'),('devices','device_key','device'),('principals','principal','principal')]:bind(ns,a[source],b[target])
  for k in ['role','state','evidence','stop_requested','terminal','operation_error','cleanup','metrics']:check('sessionScalar:'+k,a[k]==b[k])
  keys(b['metrics'],'app_rx_bytes app_tx_bytes saturated elapsed_millis','metrics');check('attemptCount',len(a['attempts'])==len(b['attempts']))
  for x,y in zip(a['attempts'],b['attempts']):
   keys(y,'kind application_epoch session attempt resource proof','attempt');check('attemptScalars',x['kind']==y['kind'] and x['proof']==y['proof']);corr=x['correlation'];bind('application_epochs',corr['instance_id'],y['application_epoch']);bind('sessions',corr['session_id'],y['session']);bind('attempts',corr['attempt_id'],y['attempt']);bind('resources',(x['kind'],x['resource_id']),y['resource'])
 def auditrow(a,b,application_epoch):
  keys(b,'application_epoch sequence actor profile job session action failure state evidence cleanup','audit');bind('application_epochs',application_epoch,b['application_epoch']);bind('uids',a['actor_uid'],b['actor']);bind('profiles',a['profile'],b['profile']);bind('jobs',a['job_id'],b['job']);bind('sessions',a['session_id'],b['session'])
  for k in ['sequence','action','failure','state','evidence','cleanup']:check('auditScalar:'+str(a['sequence'])+':'+k,a[k]==b[k])
 for a,b in zip(audit,agent['audit']):auditrow(a,b,None)
 check('noWithheldAudit47',all(e['sequence']<=46 for e in agent['audit']) and {e['sequence']for e in agent['audit']}=={e['sequence']for e in audit})
 health=load(S/'health-during.json')['response']['sessions'];keys(doc['health'],'pages samples','health');check('independentHealthCount',doc['health']['pages']==1 and len(health)==len(doc['health']['samples'])==4)
 for a,b in zip(health,doc['health']['samples']):
  keys(b,'session application_epoch role availability not_in_committed_view observed_at_millis link','healthSample');bind('sessions',a['session_id'],b['session']);bind('application_epochs',a['instance_id'],b['application_epoch']);check('healthRoleAvailability',a['role']==b['role'] and a['availability']==b['availability'] and b['not_in_committed_view']is False)
  if a['link']is None:check('noInventedExportHealth',b['link']is None);continue
  x,y=a['link'],b['link'];keys(y,'protocol connection sample_sequence elapsed_millis phase probe last_probe_sequence last_probe_enqueued_millis last_reply_sequence last_reply_millis last_round_trip_micros stats end','link');bind('connections',(a['instance_id'],x['identity']['connection']),y['connection'])
  for k in ['protocol','phase','probe','end']:check('healthLinkCategory:'+k,x[k]==y[k])
  check('healthMonotonicSample',y['sample_sequence']<=x['sample_sequence'] and y['elapsed_millis']<=x['elapsed_millis'] and y['protocol']=='framed_v2' and y['probe']=='reply_observed')
  keys(y['stats'],'app_read_bytes app_write_bytes last_app_read_millis last_app_write_millis receive_pending_bytes send_capacity_blocked transport_write_blocked','linkStats')
  for k in ['app_read_bytes','app_write_bytes']:check('healthCounterNoFuture:'+k,y['stats'][k]<=x['stats'][k])
 loglines=[loads(line)for line in read(S/'logs-agent-1-segment-00.ndjson').splitlines()];source_logs=[r for r in loglines[1:]if r['event']['kind']=='audit'and r['event']['audit_sequence']<=46 and(r['event']['profile']==profile or(r['event']['profile']is None and r['event']['action']=='denied'and r['event']['actor_uid']==uid))]
 keys(doc['logs'],'pages records','logs');check('threeActualPages44',len(doc['logs']['pages'])==3 and len(doc['logs']['records'])==len(source_logs)==44 and [p['source_records']for p in doc['logs']['pages']]==[16,16,12] and [p['more']for p in doc['logs']['pages']]==[True,True,False])
 for p in doc['logs']['pages']:
  keys(p,'observed_at_millis source_records exported_records privacy_filtered_records scanned_bytes gap partial_tail more continuation_observed oldest_retained_boundary_observed live_status_availability live_status','logPage');check('cleanLogPage',not p['gap'] and not p['partial_tail'] and p['privacy_filtered_records']==0 and p['source_records']==p['exported_records'] and p['live_status_availability']=='observed')
  st=p['live_status'];keys(st,'healthy closing queued accepted written dropped oversized projection_errors overflowed synced_boundary_observed failure','logStatus');check('healthySeparateLogStatus',st['healthy'] and not st['closing'] and st['dropped']==st['oversized']==st['projection_errors']==0 and not st['overflowed'] and st['failure']is None and st['written']<=st['accepted'])
 for a,b in zip(source_logs,doc['logs']['records']):
  keys(b,'process_epoch sequence elapsed_millis level event','logRecord');bind('process_epochs',a['epoch'],b['process_epoch']);check('logEnvelopeOriginal',all(a[k]==b[k]for k in ['sequence','elapsed_millis','level']));ev=b['event'];keys(ev,'kind application_epoch sequence actor profile job session action failure state evidence cleanup','logAudit');check('auditLogOnly',ev['kind']=='audit');event={k:v for k,v in ev.items()if k!='kind'};original=a['event'];auditrow({**original,'sequence':original['audit_sequence']},event,original['application_instance'])
 check('noWithheldLog47',all(r['event']['sequence']<=46 for r in doc['logs']['records']))
 expected_counts={ns:len(mapping)for ns,mapping in aliases.items()};check('aliasCounts',doc['alias_counts']==expected_counts)
 for ns,mapping in aliases.items():check('aliasContiguousInjective:'+ns,len(mapping)==len(reverse[ns]) and set(mapping.values())==set(range(1,len(mapping)+1)))
 def strings(v):
  if isinstance(v,str):yield v
  elif isinstance(v,dict):
   for x in v.values():yield from strings(x)
  elif isinstance(v,list):
   for x in v:yield from strings(x)
 allowed_strings=set('Available Connected Connecting Exporting admission agent attempt audit available complete denied export export_ready framed_v2 health helper import_reply_flushed incoming info intent linux live logs missing native native_export native_stub not_data_session not_loaded observe observed open permission prepared reply_observed resource running start unknown warn x86_64'.split())
 check('allStringLeavesClosedForActualCase',set(strings(doc))<=allowed_strings)
 rawtext=raw.decode();secrets=set()
 for state in [old,draft]:
  for j in state['jobs']:secrets.update([j['id'],j['instance_id'],j['profile']])
  for a in state['sessions']:
   secrets.update([a['id'],a['instance_id'],a['job_id'],a['device_key']]);secrets.update([a['principal']]if a['principal']else[])
   for at in a['attempts']:secrets.update(at['correlation'].values());secrets.add(at['resource_id'])
 secrets.update([r['epoch']for r in loglines if'epoch'in r]);secrets.add(loglines[0]['segment']);secrets.add('diagnostic-private-canary')
 for a in health:
  if a['link']:secrets.update(a['link']['identity']['tcp'].values())
 check('allOriginalStringsOmitted',all(v not in rawtext for v in secrets if v))
 check('noRawIdentityForms',not re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|native:usb-|/etc/|/sys/|/var/|192\.0\.2\.',rawtext))
 manifest=load(S/'actual-config-1-agent.json');roles={r['uid']:r for r in manifest['operators']};check('actualReadAuditRole',set(roles[1002]['actions'])=={'read'} and set(roles[1003]['actions'])=={'audit'} and {'read','audit'}<=set(roles[1000]['actions']))
 for user in [1002,1003]:
  denied=load(S/('denied-bundle-'+str(user)+'.json'));r=load(S/('export-denied-'+str(user)+'.stdout'));check('deniedWait:'+str(user),read(S/('export-denied-'+str(user)+'.wait')).strip()==b'1')
  check('permissionLimitedArtifact:'+str(user),denied['capture']=='partial' and not denied['summary']['complete'] and denied['agent']is None and denied['health']=={'pages':0,'samples':[]} and denied['logs']=={'pages':[],'records':[]} and all(v['failure']=='permission'for v in denied['summary']['sections']if v['stage']in['agent','health','logs']))
  check('deniedFileReceipt:'+str(user),r['summary']==denied['summary'] and r['file']['receipt']['sha256']==sha(read(S/('denied-bundle-'+str(user)+'.json'))) and r['file']['receipt']['publication']=='published')
  check('realKernelActorDenied:'+str(user),any(e['actor_uid']==user and e['profile']is None and e['action']=='denied'and e['failure']=='permission'for e in old['audit']))
  parent=load(S/('file-parent-'+str(user)+'.json'));check('privateRoleParent:'+str(user),parent['uid']==user and int(parent['mode'],8)==0o700 and parent['kind']=='directory')
 check('denialResourcesUnchanged',read(S/'root-before-refusals.json')==read(S/'root-after-refusals.json') and read(S/'resources-before-refusals.json')==read(S/'resources-after-refusals.json'))
 metadata=load(S/'file-held.json');existing=load(S/'file-existing.json');parent=load(S/'file-parent-1000.json')
 check('privateArtifactMetadata',metadata==existing and metadata['uid']==metadata['gid']==1000 and metadata['mode']=='600' and metadata['links']==1 and metadata['kind']=='regular file'and metadata['bytes']==len(raw)and metadata['path']==metadata['canonical'] and parent['uid']==1000 and parent['mode']=='700')
 refusal=load(S/'export-existing.stdout');check('existingBeforePrepareRefused',refusal['file']['failure']['publication']=='not_created' and refusal['file']['failure']['kind']=='already_exists' and refusal['file']['failure']['written_bytes']==0 and read(S/'export-existing.wait').strip()==b'1')
 status=read(S/'proc-export-held.status').decode();ps=read(S/'proc-export-held.stat').decode();pid=int(ps.split()[0]);fields=ps[ps.rfind(')')+2:].split();uids=[int(x)for x in next(l for l in status.splitlines()if l.startswith('Uid:')).split()[1:]];check('exporterUID',uids==[1000]*4 and pid==int(next(l for l in status.splitlines()if l.startswith('Pid:')).split()[1]))
 for name in ['CapInh','CapPrm','CapEff','CapAmb']:check('exporterCaps:'+name,int(next(l for l in status.splitlines()if l.startswith(name+':')).split()[1],16)==0)
 check('exporterRealELF',read(S/'proc-export-held.exe.sha256').decode().split()[0]==CLI)
 cmd=read(S/'proc-export-held.cmdline');check('exporterCommand',b'diagnostics'in cmd and b'/var/lib/rustusb/export-1000/bundle.json'in cmd)
 def uptime(name):return float(read(S/name).split()[0])
 held_before=load(S/'gate-held-before.json');held_after=load(S/'gate-held-after.json');exitgate=load(S/'gate-exit-1.json')
 check('exportWhileOriginalGateHeld',held_before['held']and held_after['held']and held_before['file_inode']==held_after['file_inode']and held_before['monotonic_ns']/1e9<=uptime('export-held-before.uptime')<=uptime('export-held-after.uptime')<=held_after['monotonic_ns']/1e9<exitgate['monotonic_ns']/1e9)
 check('exportProcWithinWindow',uptime('export-held-before.uptime')<=uptime('proc-export-held.uptime')<=uptime('export-held-after.uptime'))
 for operation in ['begin','end']:
  encoded=read(S/('rpc-post-export-'+operation+'.response.bin'));length=struct.unpack('!I',encoded[:4])[0];parsed=loads(encoded[4:]);check('freshRPCFrame:'+operation,length==len(encoded)-4 and parsed==load(S/('rpc-post-export-'+operation+'.response.json')))
 begin=load(S/'rpc-post-export-begin.response.json');end=load(S/'rpc-post-export-end.response.json');endrequest=load(S/'rpc-post-export-end.request.json')
 check('freshSameUIDMetadataCycle',begin['response']['header']['snapshot']['number']==2 and begin['response']['header']['committed_sequence']==46 and begin['response']['header']['counts']=={'jobs':1,'sessions':4,'audit':44} and endrequest['request']['snapshot']==begin['response']['header']['snapshot']and end['response']=={'result':'diagnostic_ended'})
 check('freshCycleStillHeld',uptime('export-held-after.uptime')<=uptime('rpc-post-export-begin.uptime')<=uptime('rpc-post-export-end.uptime')<=held_after['monotonic_ns']/1e9)
 for path,row in summary['artifact_manifest'].items():check('hostArtifactManifest:'+path,len(read(BASE/path))==row['bytes'] and sha(read(BASE/path))==row['sha256'])
 check('allRawUnchanged',before==inv())
 for p,record in list(readset.items()):check('consultedUnchanged:'+p,sha(Path(p).read_bytes())==record['sha256'])
 report={'schema_version':1,'result':'PASS_WITH_EVIDENCE_BOUNDARIES','head':HEAD,'scope':'Independent read-only artifact/alias/authorization parser; reviewer authored output/logging components, not an independent implementation reviewer. No producer/verifier imported or application/Git/Cargo/QEMU execution.','observed':{'bundle_bytes':len(raw),'bundle_sha256':sha(raw),'selected_sources':209,'compiled_inputs_mapped':192,'guest_exits':summary['guest_exit_codes'],'decoded_artifacts':dict(counts),'old_commit':46,'withheld_draft':47,'exported_jobs':len(jobs),'exported_sessions':len(sessions),'authorized_audit':len(audit),'source_audit_total':len(old['audit']),'log_pages':len(doc['logs']['pages']),'log_records':len(source_logs),'health_samples':len(health),'alias_counts':expected_counts,'exporter_pid':pid,'exporter_starttime':int(fields[19]),'exporter_uid':1000,'exporter_effective_permitted_inheritable_ambient_caps':0,'file_inode':[metadata['device'],metadata['inode']],'same_uid_fresh_begin_number':2},'boundaries':['Original collector token1/End request and response were not independently packet-traced: code path plus full capture receipt and sameUID fresh Begin2/End response are the exact evidence.','1002/1003 have actual policy/denied receipts/waits and committed kernel actorUID denial records; no independent per-child proc/cap receipt exists for them.','Existing-target rejection is before prepare. No concurrent rename syscall was traced; sameinode/size plus successful guestcmp is not a saved independent after-content capture.','Exporter FD listing is empty/racy; it is not evidence that no descriptor or USB capability existed. Actual no-cap UID plus original root-record/resources equality are distinct facts.','Health/logs are independent samples, not atomic with committed46. Monotonic correspondence and original identities are checked without pretending exact simultaneous state.','Private file metadata records original guest0600/0700 and namespace identity; copied host evidence permissions are not substituted.','Host1300 predicate count and this method assertion count are not USB cases. H independently reviews WAL actualgate/USB IO/Rootproof; this report does not duplicate those claims.','Firstbde FAIL, earlier dirty3child and old2ca localFAIL remain separate; no reclassification.','This run does not establish physical hardware G1/G2, general kernel hardblock cancellation, process FD revocation or complete Phase3 acceptance.'],'method_predicates_not_cases':len(checks),'raw_before_after_equal':True,'raw_file_count':len(before),'raw_files':before,'consulted_files':list(readset.values())}
 (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');(OUT/'assertions.json').write_text(json.dumps(checks,indent=2)+'\n')
 print(json.dumps({'result':report['result'],'predicates':len(checks),'raw_files':len(before),'aliases':expected_counts,'sha256':sha((OUT/'report.json').read_bytes())},indent=2))
except Exception as error:
 (OUT/'parser-failure.json').write_text(json.dumps({'error_type':type(error).__name__,'message':str(error),'completed_predicates':len(checks),'last':checks[-5:]},indent=2)+'\n');raise
