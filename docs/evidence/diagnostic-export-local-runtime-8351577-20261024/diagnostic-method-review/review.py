#!/usr/bin/env python3
"""Independent read-only parsing. No application/runner imports or execution."""
from pathlib import Path
import hashlib,json,os,re,stat
BASE=Path('/tmp/rustusb-diagnostic-local-fixed-20261024')
OUT=Path(__file__).resolve().parent
CHECKOUT=Path('/tmp/rustusb-diagnostic-local-fixed-checkout-20261024')
INPUTS=Path('/tmp/rustusb-diagnostic-software-second-20261024/inputs-before.json')
HEAD='8351577459d25ae36d7ac8924cfdae7880e5292f'
EXPECTED={'all-features':'9708a50ee836378cd64f8726dae36caded96d4fca39faae215aa30658ef49b48','default':'88c00373baab4e25b6df149dbe74d4009fbe1b2932ffe7039b1fcf1ff55ffdb0'}
checks=[];consulted={}
def digest(b):return hashlib.sha256(b).hexdigest()
def read(path):
 b=path.read_bytes();consulted[str(path)]={'path':str(path),'bytes':len(b),'sha256':digest(b)};return b
def load(path):return json.loads(read(path))
def check(name,value):
 checks.append({'id':name,'ok':bool(value)})
 if not value:raise ValueError(name)
def inventory():return [{'path':str(p),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}for p in sorted(BASE.rglob('*'))if p.is_file()]
def proc(row,directory):
 ident=row.get('identity');pre=row.get('pre_exec_identity')
 if ident:check('uid:'+row['name'],ident['pid']==row['pid'] and ident['starttime']>0 and ident['uids']==[1000]*4)
 if pre:check('preexec_uid:'+row['name'],pre['pid']==row['pid'] and pre['starttime']>0 and pre['uids']==[1000]*4)
 if 'executed_binary' not in row:return 'identity_only_no_elf' if ident else 'wrapper_preexec_only' if pre else 'no_proc_sample'
 image=row['executed_binary'];check('procELF:'+row['name'],image['sha256']==EXPECTED['all-features' if directory.parent.name=='all-features' else 'default'])
 status=read(directory/'proc-status.txt').decode();text=read(directory/'proc-stat.txt').decode();fields=text[text.rfind(')')+2:].split()
 check('rawProcIdentity:'+row['name'],int(text.split()[0])==row['pid'] and int(fields[19])==ident['starttime'])
 uids=[int(x)for x in next(l for l in status.splitlines()if l.startswith('Uid:')).split()[1:]]
 check('rawProcUid:'+row['name'],uids==[1000]*4)
 for cap in ['CapInh','CapPrm','CapEff','CapAmb']:
  check('rawProcCaps:'+row['name']+':'+cap,int(next(l for l in status.splitlines()if l.startswith(cap+':')).split()[1],16)==0)
 return 'sampled_exe_hash_and_raw_proc'
def stdout(scope,name):
 raw=read(scope/name/'stdout.bin');check('stdoutBound:'+scope.name+':'+name,len(raw)<65536 and raw.endswith(b'\n') and len(raw.splitlines())==1)
 return raw,json.loads(raw)
def walkstrings(value):
 if isinstance(value,str):yield value
 elif isinstance(value,dict):
  for v in value.values():yield from walkstrings(v)
 elif isinstance(value,list):
  for v in value:yield from walkstrings(v)
def archives(scope,summary):
 mapping={}
 for metadata_file in sorted(scope.glob('final-*/original-metadata.json')):
  for row in load(metadata_file):
   archived=metadata_file.parent/row['archived'];check('archiveName:'+str(archived),Path(row['archived']).name==row['archived'])
   data=read(archived);check('archiveSHA:'+str(archived),len(data)==row['bytes'] and digest(data)==row['sha256'])
   check('originalPrivateMetadata:'+row['path'],row['uid']==1000 and row['mode']=='0o600' and row['nlink']==1)
   original=Path(row['path']);check('originalScope:'+row['path'],original.is_relative_to(Path(summary['private_root'])))
   # Current original paths are optional: absence cannot invalidate archived historical proof.
   if original.exists():
    st=original.lstat();check('originalCurrentIdentity:'+row['path'],stat.S_ISREG(st.st_mode) and(st.st_dev,st.st_ino)==(row['device'],row['inode']) and stat.S_IMODE(st.st_mode)==0o600 and st.st_uid==1000 and st.st_nlink==1)
    check('originalCurrentBytes:'+row['path'],digest(read(original))==row['sha256'])
   mapping[row['path']]=(row,data)
 return mapping
def document(scope,name,receipt,mapping):
 row=next(c for c in summaries[scope.name]['children']if c['name']==name);final=Path(row['argv'][row['argv'].index('--output')+1]);file=receipt['file']['receipt'];published=file['publication']=='published';original=final if published else final.parent/file['partial_name'];meta,data=mapping[str(original)]
 check('fileReceipt:'+scope.name+':'+name,len(data)==file['written_bytes'] and digest(data)==file['sha256'] and len(data)<=4194304)
 doc=json.loads(data);check('docReceipt:'+scope.name+':'+name,doc['capture']==receipt['capture'] and doc['summary']==receipt['summary'] and doc['schema_version']==1)
 check('docPrivacyFlags:'+scope.name+':'+name,all(doc['privacy'][k] for k in ['identifiers_aliased','paths_and_network_addresses_omitted','credentials_and_payloads_omitted','raw_messages_omitted']) and not doc['privacy']['hardware_test_performed'] and not doc['privacy']['network_security_verified'])
 strings=list(walkstrings(doc));check('noAbsoluteOrSensitiveStrings:'+scope.name+':'+name,not any(s.startswith('/') or 'SENSITIVE_CORRUPT_INPUT' in s or 'DO_NOT_EXPORT' in s or re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',s) for s in strings))
 check('artifactBuild:'+scope.name+':'+name,doc['build']['secure_agent_collection']==(scope.name=='all-features'))
 return meta,doc
raw_before=inventory();result=load(BASE/'result.json');start=load(BASE/'start.json');commands=load(BASE/'results.json')
try:
 check('outerResult',result['result']=='PASS' and result['guards_unchanged'] and not result['remaining_unrun'] and len(result['commands'])==4 and all(c['exit_code']==0 for c in result['commands']))
 check('outerEquality',result['before']==result['after']==start['guards'] and result['commands']==commands)
 check('outerHead',result['before']['head']==HEAD and result['before']['clean'] and result['before']['source_matches'])
 compiled=load(INPUTS);check('outerInputCount',len(compiled)==start['compiled_input_count']==192)
 for r in compiled:check('compiledSource:'+r['path'],len(read(CHECKOUT/r['path']))==r['bytes'] and digest(read(CHECKOUT/r['path']))==r['sha256'])
 for p,h in result['before']['binary_hashes'].items():check('actualSelectedELF:'+p,digest(read(Path(p)))==h and h in EXPECTED.values())
 summaries={};casefacts={};samples=[];runtime_sources={}
 for mode in ['all-features','default']:
  scope=BASE/mode;s=load(scope/'summary.json');summaries[mode]=s
  check('summary:'+mode,s['result']=='PASS' and not s['forced_cleanup'] and not s['collection_errors'] and s['source_unchanged'] and s['binary_unchanged'])
  check('sourceSnapshots:'+mode,s['sources_before']==s['sources_after'] and s['sources_before']['head']==HEAD and s['sources_before']['git_status']=='')
  for r in s['sources_before']['files']:check('runtimeSource:'+mode+':'+r['path'],len(read(CHECKOUT/r['path']))==r['bytes'] and digest(read(CHECKOUT/r['path']))==r['sha256'])
  source_rows=s['sources_before']['files'];check('sourceFingerprint:'+mode,digest(json.dumps(source_rows,sort_keys=True).encode())==s['sources_before']['fingerprint'])
  runner=s['sources_before']['diagnostic_runner'];check('runnerSource:'+mode,digest(read(CHECKOUT/'scripts/test-diagnostic-export.py'))==runner['sha256'])
  check('binaryGuard:'+mode,s['binary_before']==s['binary_after'] and s['binary_before']['sha256']==EXPECTED[mode])
  check('caseCount:'+mode,len(s['cases'])==(9 if mode=='all-features' else 4) and all(c['result']=='PASS' for c in s['cases']))
  check('childCount:'+mode,len(s['children'])==(13 if mode=='all-features' else 6))
  mapping=archives(scope,s);children={c['name']:c for c in s['children']};cs={c['name']:c for c in s['cases']};runtime_sources[mode]=len(source_rows)
  for c in s['cases']:check('caseRaw:'+mode+':'+c['name'],load(scope/(c['name']+'.json'))==c and c['started_monotonic_ns']<=c['completed_monotonic_ns'])
  for c in s['children']:
   p=load(scope/c['name']/'process.json');check('processRow:'+mode+':'+c['name'],p==c and not c['forced_cleanup'] and isinstance(c['returncode'],int) and c['waited_monotonic_ns']>=c['started_monotonic_ns'])
   expected=0 if c['name'] in ['local-capture','create-real-local-log','local-log-capture','accepted-publication-signal']else 1
   check('actualWait:'+mode+':'+c['name'],c['returncode']==expected)
   samples.append({'mode':mode,'name':c['name'],'pid':c['pid'],'actual_wait_returncode':c['returncode'],'proc_scope':proc(c,scope/c['name'])})
  for name in ['existing-destination','symlink-parent']:
   _,r=stdout(scope,name);check('refusedNoCreate:'+mode+':'+name,r['file']['result']=='failed' and r['file']['failure']['publication']=='not_created')
  existing=next((meta,data) for path,(meta,data) in mapping.items()if path.endswith('/refusals/existing.json'))
  check('existingOriginalUnchanged:'+mode,existing[1]==b'PRESERVE_ORIGINAL_DIAGNOSTIC_DESTINATION\n' and existing[0]=={**cs['refusals']['facts']['original'],'archived':'existing.json'})
  _,r=stdout(scope,'local-capture');meta,doc=document(scope,'local-capture',r,mapping);check('localComplete:'+mode,r['capture']=='complete' and r['summary']['complete'] and r['file']['receipt']['publication']=='published')
  for style in ['human','json']:
   name='successful-discovery-console-'+style;raw=read(scope/name/'stdout.bin');stderr=read(scope/name/'stderr.bin');check('successAndConsoleFailure:'+mode+':'+style,raw==b'[]\n' and len(stderr)==16 and children[name]['returncode']==1)
   check('consoleFormatPrefix:'+mode+':'+style,stderr.startswith(b'{"timestamp"') if style=='json' else stderr.startswith(b'2026-10-24T'))
  if mode=='default':
   raw,r=stdout(scope,'agent-feature-unavailable');check('defaultUnavailable',r['file']['failure']['kind']=='unsupported' and r['file']['failure']['publication']=='not_created' and b'SECRET_PROFILE' not in raw and b'DO_NOT_EXPORT_AGENT_PATH' not in raw)
   casefacts[mode]={'groups':list(cs),'children':len(children),'configured_source_rows':len(source_rows)};continue
  _,valid=stdout(scope,'local-log-capture');_,validdoc=document(scope,'local-log-capture',valid,mapping);_,bad=stdout(scope,'corrupt-log-capture');_,baddoc=document(scope,'corrupt-log-capture',bad,mapping)
  check('logsActualRecords',validdoc['logs']['records'] and any(r['event']['kind']=='started'for r in validdoc['logs']['records']))
  check('logsPartialAndLiveScope',valid['capture']=='complete' and validdoc['logs']['pages'][0]['live_status'] is None and bad['capture']=='partial' and not bad['summary']['complete'])
  source_epochs=[]
  for file in (scope/'source-logs-before-corruption').glob('segment-*.ndjson'):
   for line in read(file).splitlines():
    row=json.loads(line)
    if 'epoch'in row:source_epochs.append(row['epoch'])
  docs=json.dumps(validdoc);check('logsRawEpochRedacted',all(epoch not in docs for epoch in source_epochs))
  raw,r=stdout(scope,'write-limit-16');f=r['file']['failure'];prefix_meta,prefix=next((meta,data)for path,(meta,data)in mapping.items()if'/write-failure/'in path)
  check('actualEFBIG16',f['errno']==27 and f['kind']=='io' and f['publication']=='partial' and f['written_bytes']==16 and len(prefix)==16 and digest(prefix)==f['sha256'] and children['write-limit-16']['returncode']==1 and len(read(scope/'write-limit-16/stderr.bin'))==16)
  gates=[]
  for group,name,number,syscall,code in [('sigint','prepare-sigint',2,91,1),('sigterm','prepare-sigterm',15,91,1),('publication-race','accepted-publication-signal',15,74,0)]:
   events=load(scope/name/'gate-events.json');held=load(scope/name/'held.json');c=children[name]
   def one(kind):
    rows=[e for e in events if e['event']==kind];check('oneEvent:'+name+':'+kind,len(rows)==1);return rows[0]
   entry=one('held_syscall_entry');sent=one('signal_sent');joining=one('still_joining_before_release');release=one('release_same_syscall');returned=one('same_syscall_returned');reaped=one('actual_reaped');tracing=one('tracing_started');delivery=[e for e in events if e['event']=='actual_signal_delivery' and e['number']==number];check('actualSignalEvent:'+name,len(delivery)==1);delivery=delivery[0]
   check('heldRawEquivalent:'+name,{k:entry[k]for k in held}==held and held['syscall']==syscall and held['syscall_info_arch']==0xc000003e)
   identity=lambda m:(m['device'],m['inode']);check('heldFDIdentity:'+name,identity(held['file'])==identity(held['namespace'])==identity(joining['file']) and held['file']['mode']=='0o600' and held['file']['uid']==1000 and held['file']['nlink']==1)
   check('originalLeaderIdentity:'+name,tracing['identity']==sent['identity']==c['identity'] and sent['identity_method']=='original retained pidfd')
   known={c['pid']}
   for e in events:
    if e['event']=='actual_clone_event':check('cloneParent:'+name+':'+str(e['new_tid']),e['tid']in known);known.add(e['new_tid'])
   check('heldOwnedThread:'+name,held['tid']in known and delivery['tid']in known)
   check('orderedGate:'+name,entry['monotonic_ns']<sent['monotonic_ns']<=delivery['monotonic_ns']<joining['monotonic_ns']<=release['monotonic_ns']<returned['monotonic_ns']<reaped['monotonic_ns']<=c['waited_monotonic_ns'])
   check('holdAfterActualForward:'+name,release['monotonic_ns']-delivery['monotonic_ns']>=500_000_000)
   check('sameSyscallResult:'+name,release['tid']==returned['tid']==held['tid'] and release['fd']==held['fd'] and release['syscall']==syscall and returned['result']==returned['is_error']==0)
   check('actualKernelWait:'+name,reaped['pid']==c['pid'] and reaped['status']==c['actual_waitpid_status'] and os.WIFEXITED(reaped['status']) and os.waitstatus_to_exitcode(reaped['status'])==code==c['returncode'])
   _,r=stdout(scope,name);meta,doc=document(scope,name,r,mapping);check('finalIsOriginalFD:'+name,identity(meta)==identity(held['file']))
   check('cancelOrPublished:'+name,(r['capture']=='cancelled' and r['file']['receipt']['publication']=='partial' and held['file']['bytes']==0)if syscall==91 else(r['capture']=='complete' and r['file']['receipt']['publication']=='published' and r['cancel_observed'] and held['file']['bytes']>0))
   gates.append({'group':group,'pid':c['pid'],'tid':held['tid'],'fd':held['fd'],'syscall':syscall,'result':returned['result'],'is_error':returned['is_error'],'hold_after_forward_ns':release['monotonic_ns']-delivery['monotonic_ns'],'actual_wait_status':reaped['status'],'returncode':code,'capture':r['capture'],'publication':r['file']['receipt']['publication'],'original_inode':list(identity(meta))})
  bp=cs['backpressure']['facts'];c=children['blocked-receipt'];meta,data=next((m,b)for path,(m,b)in mapping.items()if path.endswith('/blocked-output/final.json'));doc=json.loads(data)
  check('BPActualWaitAndPublished',c['returncode']==1 and c['waited_monotonic_ns']-c['started_monotonic_ns']>=4_500_000_000 and digest(data)==bp['sha256'] and doc['capture']=='complete' and doc['summary']['complete'])
  check('BPReportedBound',bp['pipe_capacity']==4096 and bp['no_drain_until_actual_wait'] and bp['elapsed_seconds']>=4.5)
  runner=read(CHECKOUT/'scripts/test-diagnostic-export.py').decode();bpfn=runner.split('def blocked_output(',1)[1].split('\ndef ',1)[0];check('BPSourceWaitBeforeDrain',bpfn.index('run.wait(child')<bpfn.index('os.read(read_fd'))
  casefacts[mode]={'groups':list(cs),'children':len(children),'configured_source_rows':len(source_rows),'gates':gates,'backpressure':{'actual_child_exit':c['returncode'],'elapsed_seconds':bp['elapsed_seconds'],'final_complete':True,'pipe_claim_scope':'4096 fill/no-drain/exact padding+EOF are successful frozen-source assertions; no raw drained padding bytes were saved for independent replay.'}}
 legacy_path=Path('/tmp/rustusb-diagnostic-local-legacy-cross-review-20261024/report.json')
 legacy=load(legacy_path);check('delegatedLegacyReportHash',consulted[str(legacy_path)]['sha256']=='6756b96b24403efd0e2c97f1bf918297f924c083bcd5e8f68d3b69bd48ab1e9b');check('delegatedLegacyResult',legacy['result']=='PASS' and legacy['head_observed_in_receipts']==HEAD and all(c['pass']for c in legacy['checks']) and legacy['counts']['signals']==4 and legacy['counts']['legacy_backpressure']==2)
 raw_after=inventory();check('rawBeforeAfterSame',raw_after==raw_before)
 for path,old in list(consulted.items()):check('consultedBeforeAfter:'+path,hashlib.sha256(Path(path).read_bytes()).hexdigest()==old['sha256'])
 counts={scope:sum(s['proc_scope']==scope for s in samples)for scope in sorted({r['proc_scope']for r in samples})}
 report={'schema_version':1,'result':'DIAGNOSTIC_RUNTIME_RAW_REVIEW_PASS_WITH_EXPLICIT_SCOPE','source_head':HEAD,'raw_origin':str(BASE),'author_scope':'Reviewer authored output and earlier logging components; independent raw/parser method, not full-implementation independent authorship. No original runner/verifier imported.','diagnostic_runtime':casefacts,'diagnostic_child_proc_scope':counts,'diagnostic_children':samples,'outer_guard_scope':{'compiled_inputs_rehashed':len(compiled),'runtime_inventory_rows_each':runtime_sources,'all_current_fixed_checkout_source_matches':True,'all_selected_external_elf_hashes_match':True,'before_after_saved_head_clean_guards_equal':True,'no_new_git_command_or_gitblob_claim':True},'legacy_scope':'Four signal/two old backpressure checks are separate virtual/plaintext regressions; delegated independent raw parser, no original runner/verifier imports.','legacy_review':{'reviewer':'/root/journal_integration_review','path':str(legacy_path),'sha256':consulted[str(legacy_path)]['sha256'],'result':legacy['result'],'counts':legacy['counts'],'limits':legacy['limits']},'method_assertions':len(checks),'method_assertions_are_not_usb_cases':True,'unrun_or_historical_not_merged':['Old clean2ca FAIL preserved; no verdict rewritten.','Earlier dirty-only correction diagnostic groups/children and old RUST_LOG differential are not merged.','No USB/hardware or installed-helper/kernel recovery acceptance follows from these ordinary-UID observers/controlled loopback/ptrace-entry tests.'],'limits':['Observed local syscall ABI is x86_64 (91/74); this run is not an aarch64 runtime result.','Only5 children have actual executed-ELF+raw proc samples;9 identity-only and5 wrapper-preexec samples are weaker. Selected binary and source guards do not fabricate missing samples.','Historical original permissions/identity are joined with archived metadata and current original checks where still present; archive-copy permissions are not presented as original0600 proof.','BP drain bytes were not saved; source-bound successful assertions cannot become an independently replayed byte observation.','No actual-stderr syscall errno or panic stack is inferred beyond receipts/source; successful exit1 replaces old101 only for this new fixed source.','Controlled stderr-success DEVLIST raw network request/reply bytes were not persisted; stdout[], four actualwait1 and16-byte human/JSON stderr are independent raw observations, while protocol exchange/response completion is a frozen-source successful assertion, not packet replay.'],'raw_files_before_after_equal':True,'raw_artifact_count':len(raw_before),'raw_files':raw_before,'consulted_files':list(consulted.values()),'execution_boundary':{'application_cli_cargo_qemu_ptrace_git_runs':0,'source_or_raw_mutations':0,'python_parser_only':True}}
 (OUT/'assertions.json').write_text(json.dumps(checks,indent=2)+'\n');(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({'result':report['result'],'assertions':len(checks),'raw_artifacts':len(raw_before),'proc_scope':counts,'report_sha256':digest((OUT/'report.json').read_bytes())},indent=2))
except Exception as error:
 (OUT/'parser-failure.json').write_text(json.dumps({'error_type':type(error).__name__,'message':str(error),'assertions_completed':len(checks),'last_assertions':checks[-5:]},indent=2)+'\n');raise
