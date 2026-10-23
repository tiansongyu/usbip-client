#!/usr/bin/env python3
"""Offline independent parse of official connector responses, no root parser imports."""
from pathlib import Path
import hashlib,json,re,subprocess
ROOT=Path('/home/ubuntu/github/usbip-client');TMP=Path('/tmp')
HEAD='531e0c32a4b0e7252ca771ab4f10042c71c8c195';RUN=40029976284
JOBS=[120675534150,120675534276,120675534288,120675534313]
BASE=TMP/'rustusb-data-link-ci-531e0c3-20261024'
OUT=TMP/'rustusb-data-link-ci-531e0c3-independent-review-20261024.json'
seen={};checks=[];records=[]
def read(p):
 p=Path(p);b=p.read_bytes();v={'path':str(p),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
 if str(p) in seen and seen[str(p)]!=v:raise ValueError('changed input '+str(p))
 seen[str(p)]=v;return b
def load(p):return json.loads(read(p))
def ck(n,v,detail=None):
 d={'id':n,'passed':bool(v)}
 if detail is not None:d['detail']=detail
 checks.append(d)
def payload(v):
 s=v.get('structuredContent',v)
 return json.loads(s['content']) if isinstance(s,dict) and isinstance(s.get('content'),str) else s

def state(s,active):
 ds=s.get('devices',[])
 return s.get('schema_version')==1 and len(ds)==2 and {(d['bus_id'],d['profile']) for d in ds}=={('1-1','hid_keyboard'),('1-2','cdc_acm')} and all(d['leased'] is active and d['configuration']==int(active) and d['pending_urbs']==int(active) and d['hid_queued_reports']==d['cdc_queued_bytes']==0 for d in ds)
def fingerprint(files):
 h=hashlib.sha256()
 for f in files:h.update(f['path'].encode()+b'\0'+str(f['length']).encode()+b'\0'+f['sha256'].encode()+b'\n')
 return h.hexdigest()
def sources_against_git(files):
 # Stream immutable Git blobs, retaining at most one source file; no builds/runs.
 process=subprocess.Popen(['git','cat-file','--batch'],cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE)
 bad=[]
 try:
  for f in files:
   process.stdin.write((HEAD+':'+f['path']+'\n').encode());process.stdin.flush()
   header=process.stdout.readline().split()
   if len(header)!=3 or header[1]!=b'blob':raise ValueError('missing immutable source '+f['path'])
   n=int(header[2]);value=process.stdout.read(n);newline=process.stdout.read(1)
   if len(value)!=n or newline!=b'\n':raise ValueError('short Git blob '+f['path'])
   if n!=f['length'] or hashlib.sha256(value).hexdigest()!=f['sha256']:bad.append(f['path'])
 finally:
  process.stdin.close();process.stdout.close();process.wait()
 tree=subprocess.check_output(['git','ls-tree','-rz','--name-only',HEAD],cwd=ROOT).split(b'\0')
 expected=sorted(x.decode() for x in tree if x and (x==b'Cargo.lock' or x.endswith((b'.rs',b'.toml')) or x.startswith(b'vendor/') or x==b'scripts/test-server-signals.py'))
 if expected!=[f['path'] for f in files]:bad.append('<inventory completeness differs from frozen Git selection>')
 return bad

api=payload(load(BASE/'run-final.connector.json'))
jobs=payload(load(BASE/'jobs-final.connector.json'))['jobs']
ck('run_exact_terminal_success',api['id']==RUN and api['head_sha']==HEAD and api['status']=='completed' and api['conclusion']=='success')
artifacts=payload(load(BASE/'artifacts-final.connector.json'))
ck('no_uploaded_workflow_artifacts_available',artifacts.get('artifacts')==[])
ck('four_exact_terminal_jobs',{v['id'] for v in jobs}==set(JOBS) and all(v['run_id']==RUN and v['status']=='completed' and v['conclusion']=='success' for v in jobs))
common_files=None;common_source_sha=None
try:
 for jobid in JOBS:
  meta=next(v for v in jobs if v['id']==jobid);label=meta['name']
  match=re.fullmatch(r'check \((ubuntu-(22|24)\.04(?:-arm)?), (x86_64|aarch64)\)',label)
  if not match:raise ValueError('unexpected platform '+label)
  os_name,version,arch=match.group(1),match.group(2),match.group(3)
  response=load(BASE/f'job-{jobid}.connector.json')
  text=response['structuredContent']['content']
  raw_path=BASE/f'job-{jobid}.log'
  ck(label+'.decoded_complete_log_matches_connector',read(raw_path)==text.encode())
  lines=[re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',re.sub(r'^\ufeff?\d{4}-\d\d-\d\dT\S+Z ?','',x)) for x in text.splitlines()]
  plain='\n'.join(lines);decoder=json.JSONDecoder();objects=[]
  for m in re.finditer(r'(?m)^\{',plain):
   try:o,n=decoder.raw_decode(plain[m.start():])
   except json.JSONDecodeError:continue
   objects.append((o,plain[m.start():m.start()+n]))
  values=[v for v,_ in objects]
  required={'Formatting, lint, tests, release and documentation','Record secure foreground lifecycle diagnostics','Read-only CLI smoke checks','Unprivileged virtual-server lifecycle smoke (all-features binary)','Unprivileged server signal lifecycle (four cases)'}
  ck(label+'.actual_steps_success',required<={s['name'] for s in meta['steps']} and all(s['status']=='completed' and s['conclusion']=='success' for s in meta['steps']))
  uname=next(x for x in lines if x.startswith('Linux '));rusthost=next(x.removeprefix('host: ') for x in lines if x.startswith('host: '))
  ck(label+'.actual_checkout_platform_toolchain',HEAD in lines and f'+{HEAD}:refs/remotes/origin/main' in plain and 'VERSION_ID="'+version+'.04"' in lines and arch in uname and rusthost==arch+'-unknown-linux-gnu' and 'rustc 1.97.1 (8bab26f4f 2026-07-14)' in lines and 'cargo 1.97.1 (c980f4866 2026-06-30)' in lines)
  group=None;cli=0;rows=[]
  for line in lines:
   if 'Running unittests src/main.rs (target/debug/deps/rustusb-' in line:
    cli+=1;group='default' if cli==1 else 'all_features'
   if 'Running unittests ' in line and 'target/nusb-tests/' in line:group='vendor'
   m=re.fullmatch(r'test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out; finished in ([0-9.]+)s',line)
   if m:rows.append({'group':group,'status':m[1],'passed':int(m[2]),'failed':int(m[3]),'ignored':int(m[4]),'measured':int(m[5]),'filtered':int(m[6])})
  totals={g:sum(v['passed'] for v in rows if v['group']==g) for g in ('default','all_features','vendor')}
  ck(label+'.626_passed_39_rows_no_failure_ignored',len(rows)==39 and totals=={'default':260,'all_features':346,'vendor':20} and all(v['status']=='ok' and v['failed']==v['ignored']==v['measured']==v['filtered']==0 for v in rows),totals)
  
  required_tests=['framed_mtls_catalog_completes_full_reply_and_both_half_closes_without_claims','managed_framed_catalog_and_acl_refusal_keep_fin_eof_without_device_admission','framed_busy_reply_is_complete_and_clean_while_original_import_stays_owned','real_ping_continues_at_full_window_but_credit_waits_for_actual_reads','buffered_one_byte_batch_hits_real_auto_metadata_budget','empty_nonfinal_flood_has_a_lifetime_bound_despite_zero_byte_credit','actual_trailer_headers_are_rejected_instead_of_clean_application_eof','poisoned_shutdown_metadata_cannot_claim_fin_or_healthy_terminal','explicit_v2_never_downgrades_to_raw_peer']
  ck(label+'.actual_new_health_and_management_regressions',all(any(t in x and x.startswith('test ') and x.endswith(' ... ok') for x in lines) for t in required_tests),required_tests)
  ck(label+'.release_docs_completed_after_check_script',sum('Finished `release` profile' in x for x in lines)==2 and sum('Generated ' in x for x in lines)>=2 and '##[error]' not in plain)
  tls=next(v for v in values if 'failed_case' in v and 'cases' in v)
  tls_cases=tls['cases'];expected=['ready-int','ready-term','blocked-int','blocked-term','blocked-deadline']
  ck(label+'.five_actual_tls_cases',tls['result']=='PASS' and tls['failed_case'] is None and tls['error'] is None and [c['case'] for c in tls_cases]==expected and all(c['result']=='PASS' and c['mutual_tls'] is True and c['authenticated_imports']==2 and c['pending_in_per_import']==1 and len(c['data_connection_closure'])==2 and all(x in ('eof','transport_closed:UnexpectedEof','transport_closed:ConnectionReset','transport_closed:BrokenPipe','transport_closed:ConnectionAborted') for x in c['data_connection_closure']) for c in tls_cases))
  observations={v['case']:v['blocked_writer_observation'] for v in values if 'blocked_writer_observation' in v}
  ck(label+'.exact_three_writer_observations',set(observations)=={'blocked-int','blocked-term','blocked-deadline'})
  waits=[]
  for c in tls_cases:
   deadline=c['case']=='blocked-deadline';blocked=c['case'].startswith('blocked-')
   ck(label+'.'+c['case']+'.status_time_scope',c['exit']==(1 if deadline else 0) and (4<=c['elapsed_seconds']<=7 if deadline else 0<=c['elapsed_seconds']<=8) and c['backend_cleanup_snapshot_observed'] is (not blocked))
   if blocked:
    b=c['blocked'];o=observations[c['case']];a=o['matched'];waits.append(a['wchan'].strip())
    ck(label+'.'+c['case']+'.actual_raw_writer_and_fullpipe',o['pid']>0 and o['error'] is None and a in o['last_observed_tasks'] and a['tid']==b['blocked_writer_task'] and a['comm']==b['blocked_writer_comm'] and a['wchan']==b['blocked_writer_wchan'] and a['wchan'].strip() in ('anon_pipe_write','pipe_write') and a['comm_read_error'] is a['wchan_read_error'] is None and b['capacity']==b['synthetic_fill']==4096 and b['stdout_drained_after_fill'] is False,{'pid':o['pid'],'tid':a['tid'],'comm':a['comm'].strip(),'wchan':a['wchan']})
  bp=next(v for v in values if str(v.get('scope','')).startswith('local CLI output backpressure'))
  ck(label+'.legacy_two_backpressure',bp['result']=='PASS' and bp['host_uid']>0 and [v['mode'] for v in bp['checks']]==['sigint','deadline'] and all(v['stdout_capacity']==4096 and v['stdout_drained_after_fill'] is False and v['lease_socket_closed'] is True and v['exit']==(0 if v['mode']=='sigint' else 1) and (0<=v['elapsed_seconds']<=2 if v['mode']=='sigint' else 4<=v['elapsed_seconds']<=7) for v in bp['checks']))
  signals,signal_raw=next((v,r) for v,r in objects if 'binary_before' in v)
  ck(label+'.legacy_four_signal_matrix',signals['result']=='PASS' and signals['host_uid']>0 and len(signals['cases'])==4 and {(c['mode'],c['signal']) for c in signals['cases']}=={('ready','sigint'),('ready','sigterm'),('blocked','sigint'),('blocked','sigterm')} and all(c['result']=='PASS' and c['final_returncode']==0 and c['forced_cleanup'] is False and all(a['passed'] for a in c['assertions']) for c in signals['cases']))
  source=signals['source_before'];after=signals['source_after'];files=source['files']
  if common_files is None:
   common_files=files;common_source_sha=source['source_sha256'];bad=sources_against_git(files)
   archive=[f for f in files if f['path'].startswith('docs/evidence/') and f['path'].endswith('.rs')]
   ck('archived_rs_in_inventory_are_checked_not_compiled_cases',len(archive)>0,{'count':len(archive),'scope':'Same immutable Git blob check as all entries; source archive inclusion does not mean these files are compiled.'})
   ck('logged_source_inventory_exact_git',not bad,{'count':len(files),'mismatches':bad})
  ck(label+'.actual_source_guard',source==after and source['head']==HEAD and source['dirty'] is False and source['git_status']=='' and files==common_files and [f['path'] for f in files]==sorted({f['path'] for f in files}) and fingerprint(files)==source['source_sha256']==common_source_sha and signals['source_unchanged'] is True,{'count':len(files),'source_sha256':source['source_sha256']})
  checkline=next(v for v in values if 'summary_sha256' in v)
  ck(label+'.printed_signal_summary_matches_runtime_file_digest',hashlib.sha256((signal_raw+'\n').encode()).hexdigest()==checkline['summary_sha256'] and checkline['result']=='PASS')
  binary=signals['binary_before'];hash_value=binary['sha256']
  ck(label+'.actual_signal_elf_guard',signals['binary_unchanged'] is True and signals['binary_after_sha256']==hash_value and all(c['executed_binary']['sha256']==hash_value and c['executed_binary']['device']==binary['device'] and c['executed_binary']['inode']==binary['inode'] for c in signals['cases']))
  ck(label+'.external_build_honest',signals['build']=={'command':None,'features':'external prebuilt; not inferred','skipped':True},'Feature attribution only linked to preceding successful fixed check.sh allfeatures release build; harness reports external honestly.')
  for c in signals['cases']:
   n=label+'.plain_'+c['mode']+'_'+c['signal'];a=c['after'];child=c['child'];sent=c['sent']
   ck(n+'.pending_identity_close',state(c['before']['snapshot'],True) and child==sent['identity'] and sent['pid']==child['pid'] and child['starttime']>0 and child['uids']==[signals['host_uid']]*4 and sent['signal']==(2 if c['signal']=='sigint' else 15) and a['returncode']==0 and a['wait_error'] is None and 0<=a['signal_exit_seconds']<=8 and set(a['data_sockets'])=={'1-1','1-2'} and all(v=={'eof':True,'unexpected_hex':''} for v in a['data_sockets'].values()))
   if c['mode']=='ready':
    ck(n+'.actual_stopped_snapshot',a['backend_cleanup_observed'] is True and a['stdout_eof'] is True and len(a['stopped_events'])==1 and state(a['stopped_events'][0]['snapshot'],False))
   else:
    b=c['pipe'];ck(n+'.fullpipe_no_drain_only_exit',a['backend_cleanup_observed'] is False and a['stopped_events']==[] and a['stdout_read_after_fill'] is False and a['stdout_unread_bytes']==4096 and c['stdout_drained_after_fill'] is False and b['capacity']==b['bytes_after_fill']==b['synthetic_padding_bytes']==4096 and b['independent_writer_eagain'] is True and b['independent_writer_flags']&2048 and not any(b[k]&2048 for k in ('child_stdout_flags_before','child_stdout_flags_after','original_writer_flags_before','original_writer_flags_after')))
  records.append({'job_id':jobid,'platform':label,'conclusion':meta['conclusion'],'uname':uname,'rust_target':rusthost,'unit_result_rows':len(rows),'result_rows':rows,'actual_test_binaries':[x.strip() for x in lines if 'Running unittests ' in x or 'Doc-tests ' in x],'test_execution_groups':totals,'total_passed_executions':sum(totals.values()),'tls_cases':[{'case':c['case'],'exit':c['exit'],'elapsed_seconds':c['elapsed_seconds'],'cleanup_snapshot_observed':c['backend_cleanup_snapshot_observed'],'actual_wchan':c.get('blocked',{}).get('blocked_writer_wchan') if c.get('blocked') else None} for c in tls_cases],'actual_new_writer_wchans':waits,'legacy_backpressure_cases':len(bp['checks']),'legacy_signal_cases':len(signals['cases']),'source_archived_rs_entries':sum(f['path'].startswith('docs/evidence/') and f['path'].endswith('.rs') for f in files),'source_entries':len(files),'source_sha256':source['source_sha256'],'signal_release_elf_sha256':hash_value,'signal_summary_sha256':checkline['summary_sha256'],'legacy_backpressure_elf_guard':'Not present in old summary; linked preceding fixed workflow/target path only.','log_text_sha256':hashlib.sha256(text.encode()).hexdigest(),'original_runner_artifacts':'Only printed summaries/observations and artifact hashes available. Runner/tmp files and binaries not downloaded or fabricated.'})
except Exception as error:
 ck('independent_parser_complete',False,type(error).__name__+': '+str(error))
ck('all_four_records_completed',len(records)==4)
ck('all_original_connector_bytes_unchanged',all(Path(p).stat().st_size==v['bytes'] and hashlib.sha256(Path(p).read_bytes()).hexdigest()==v['sha256'] for p,v in seen.items()))
workflow=subprocess.check_output(['git','show',HEAD+':.github/workflows/ci.yml'],cwd=ROOT)
script=subprocess.check_output(['git','show',HEAD+':scripts/check.sh'],cwd=ROOT)
report={'schema_version':1,'result':'PASS_WITH_SCOPE_LIMITATIONS' if all(v['passed'] for v in checks) else 'FAIL','run_id':RUN,'head':HEAD,'counts':{'jobs':len(records),'unit_executions_each':626,'result_rows_each':39,'new_tls_lifecycle_cases_each':5,'actual_new_writer_observations_each':3,'legacy_backpressure_cases_each':2,'legacy_signal_cases_each':4,'diagnostic_checks':len(checks),'passed':sum(v['passed'] for v in checks),'failed':sum(not v['passed'] for v in checks)},'jobs':records,'checks':checks,'official_connector_artifacts':list(seen.values()),'uploaded_workflow_artifacts':artifacts['artifacts'],'workflow_sources':{'ci_yaml_sha256':hashlib.sha256(workflow).hexdigest(),'check_script_sha256':hashlib.sha256(script).hexdigest()},'scope':'Offline independent official API/log parse; imports no root verifier, executes no CI/Cargo/product/QEMU, writes only/tmp parser/report.','limits':['626 is sum of actual passing libtest execution rows (default260/allfeatures346/vendor20), not626 unique functions or USB/hardware cases. Five TLS scenarios/two backpressure/four signal cases are separate lifecycle reporting.','New secure5cases are memory-DER cfg(test) actual-adapter tests; safe CI summary prints precondition counts/cleanup-observed flags, not their full before/stopped snapshots. Successful exact source assertions and unit test link them; no absent runner/tmp snapshots are invented. No per-child test-ELF digest is printed for these five cases; actual executable inode/digest proof is available only in the separate four-signal summary.','Three raw safe PID/TID/comm/wchan observations are actually printed in each new CI log. They now prove current Ubuntu24 anon_pipe_write and Ubuntu22 pipe_write. Earlier39997678019 failures lacked these observations and are not retroactively filled.','Legacy4signals prints actual before/after facts, child PID/starttime/UID and executedELF guard plus a dynamically counted before/after source inventory; all logged source bytes metadata match immutableGit. Its indexed runner artifacts are not uploaded/downloaded, so their contents cannot be independently rehashed here beyond data printed in full summary.','Legacy2backpressure prints result/status/capacity/no-drain/socketEOF only; no per-child ELF guard. It is linked by fixed preceding workflow build/source/target path with no rebuild, never upgraded to stronger source/ELF proof.','Four-signal build says external prebuilt; not inferred. Allfeatures attribution is from successful fixed scripts/check.sh execution and two recorded release completions, not invented from --skip-build.','The successful immutable check.sh has ten sequential set-e commands, but CI does not use bash-x tracing: fmt/Clippy command arguments are linked to that script and successful named step, not claimed as separately printed expanded commands. Release/doc completions and each actual unit result row are printed.','No trusted-root loader, helper/realUSB, QEMU/hardware, fullG3 or universal leakage claim from these loopback tests. Current QEMU matrix and source/ELF proofs are separate evidence, not supplied by CI logs; CI does not validate website edits pending publication.']}
report['parser']={'path':__file__,'sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
OUT.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'path':str(OUT),'bytes':OUT.stat().st_size,'sha256':hashlib.sha256(OUT.read_bytes()).hexdigest(),'result':report['result'],'counts':report['counts']}))
for c in checks:
 if not c['passed']:print('FAIL',c)
