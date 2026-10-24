#!/usr/bin/env python3
"""Read-only official CI parsing adapted from prior shared-owner review.
Original secure-test/harness author; independent method, not independent implementation.
No CI rerun, build, product execution, or runner/tmp artifact reconstruction.
"""
from pathlib import Path
import argparse,hashlib,json,re,subprocess
ROOT=Path('/home/ubuntu/github/usbip-client');TMP=Path('/tmp')
HEAD='5ffdc6cfcc0e3c2de386121d0277d7daf0ed4e9f';RUN=40044605684
JOBS=[120723319604,120723319703,120723319780,120723319787]
ap=argparse.ArgumentParser();ap.add_argument('--inputs',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
CONFIG=json.loads(args.inputs.read_text());OUT=args.output
if OUT.exists():raise RuntimeError('Refusing to overwrite previous parser result')
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
 # One read-only batch; do not run CI/Cargo/any product or copy runner artifacts.
 inputs=''.join(HEAD+':'+f['path']+'\n' for f in files).encode()
 blob=subprocess.check_output(['git','cat-file','--batch'],input=inputs,cwd=ROOT)
 offset=0;bad=[]
 for f in files:
  end=blob.index(b'\n',offset);header=blob[offset:end].split();offset=end+1
  if len(header)!=3 or header[1]!=b'blob':raise ValueError('missing immutable source '+f['path'])
  n=int(header[2]);value=blob[offset:offset+n];offset+=n+1
  if len(value)!=f['length'] or hashlib.sha256(value).hexdigest()!=f['sha256']:bad.append(f['path'])
 if offset!=len(blob):raise ValueError('unconsumed git batch bytes')
 return bad
api=payload(load(CONFIG['run']))
jobs=payload(load(CONFIG['jobs']))['jobs']
ck('run_exact_terminal_success',api['id']==RUN and api['head_sha']==HEAD and api['status']=='completed' and api['conclusion']=='success')
ck('four_exact_terminal_jobs',{v['id'] for v in jobs}==set(JOBS) and all(v['run_id']==RUN and v['status']=='completed' and v['conclusion']=='success' for v in jobs))
common_files=None;common_source_sha=None
try:
 for jobid in JOBS:
  meta=next(v for v in jobs if v['id']==jobid);label=meta['name']
  match=re.fullmatch(r'check \((ubuntu-(22|24)\.04(?:-arm)?), (x86_64|aarch64)\)',label)
  if not match:raise ValueError('unexpected platform '+label)
  os_name,version,arch=match.group(1),match.group(2),match.group(3)
  response=load(CONFIG['logs'][str(jobid)]['response'])
  text=response['structuredContent']['content']
  if CONFIG['logs'][str(jobid)].get('raw'):
   ck(label+'.official_decoded_raw_bytes',text.encode()==read(CONFIG['logs'][str(jobid)]['raw']))
  lines=[re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',re.sub(r'^\ufeff?\d{4}-\d\d-\d\dT\S+Z ?','',x)) for x in text.splitlines()]
  plain='\n'.join(lines);decoder=json.JSONDecoder();objects=[]
  for m in re.finditer(r'(?m)^\{',plain):
   try:o,n=decoder.raw_decode(plain[m.start():])
   except json.JSONDecodeError:continue
   objects.append((o,plain[m.start():m.start()+n]))
  values=[v for v,_ in objects]
  required={'Formatting, lint, tests, release and documentation','Record secure foreground lifecycle diagnostics','Read-only CLI smoke checks','Unprivileged virtual-server lifecycle smoke (all-features binary)','Unprivileged server signal lifecycle (four cases)'}
  ck(label+'.actual_steps_success',required<={s['name'] for s in meta['steps']} and all(s['status']=='completed' and s['conclusion']=='success' for s in meta['steps'] if s['name'] in required))
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
  ck(label+'.667_passed_39_rows_no_failure_ignored',len(rows)==39 and totals=={'default':280,'all_features':367,'vendor':20} and all(v['status']=='ok' and v['failed']==v['ignored']==v['measured']==v['filtered']==0 for v in rows),totals)
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
    ck(label+'.'+c['case']+'.actual_raw_writer_and_fullpipe',o['pid']>0 and o['error'] is None and a in o['last_observed_tasks'] and a['tid']==b['blocked_writer_task'] and a['comm']==b['blocked_writer_comm'] and a['wchan']==b['blocked_writer_wchan'] and a['wchan'].strip() in {'pipe_write','anon_pipe_write'} and a['comm_read_error'] is a['wchan_read_error'] is None and b['capacity']==b['synthetic_fill']==4096 and b['stdout_drained_after_fill'] is False,{'pid':o['pid'],'tid':a['tid'],'comm':a['comm'].strip(),'wchan':a['wchan']})
  bp=next(v for v in values if str(v.get('scope','')).startswith('local CLI output backpressure'))
  ck(label+'.legacy_two_backpressure',bp['result']=='PASS' and bp['host_uid']>0 and [v['mode'] for v in bp['checks']]==['sigint','deadline'] and all(v['stdout_capacity']==4096 and v['stdout_drained_after_fill'] is False and v['lease_socket_closed'] is True and v['exit']==(0 if v['mode']=='sigint' else 1) and (0<=v['elapsed_seconds']<=2 if v['mode']=='sigint' else 4<=v['elapsed_seconds']<=7) for v in bp['checks']))
  signals,signal_raw=next((v,r) for v,r in objects if 'binary_before' in v)
  ck(label+'.legacy_four_signal_matrix',signals['result']=='PASS' and signals['host_uid']>0 and len(signals['cases'])==4 and {(c['mode'],c['signal']) for c in signals['cases']}=={('ready','sigint'),('ready','sigterm'),('blocked','sigint'),('blocked','sigterm')} and all(c['result']=='PASS' and c['final_returncode']==0 and c['forced_cleanup'] is False and all(a['passed'] for a in c['assertions']) for c in signals['cases']))
  source=signals['source_before'];after=signals['source_after'];files=source['files']
  if common_files is None:
   common_files=files;common_source_sha=source['source_sha256'];bad=sources_against_git(files)
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
  records.append({'job_id':jobid,'platform':label,'conclusion':meta['conclusion'],'uname':uname,'rust_target':rusthost,'unit_result_rows':len(rows),'test_execution_groups':totals,'total_passed_executions':sum(totals.values()),'tls_cases':[{'case':c['case'],'exit':c['exit'],'elapsed_seconds':c['elapsed_seconds'],'cleanup_snapshot_observed':c['backend_cleanup_snapshot_observed'],'actual_wchan':c.get('blocked',{}).get('blocked_writer_wchan') if c.get('blocked') else None} for c in tls_cases],'actual_new_writer_wchans':waits,'legacy_backpressure_cases':len(bp['checks']),'legacy_signal_cases':len(signals['cases']),'source_entries':len(files),'source_sha256':source['source_sha256'],'signal_release_elf_sha256':hash_value,'signal_summary_sha256':checkline['summary_sha256'],'legacy_backpressure_elf_guard':'Not present in old summary; linked preceding fixed workflow/target path only.','log_text_sha256':hashlib.sha256(text.encode()).hexdigest(),'original_runner_artifacts':'Only printed summaries/observations and artifact hashes available. Runner/tmp files and binaries not downloaded or fabricated.'})
except Exception as error:
 ck('independent_parser_complete',False,type(error).__name__+': '+str(error))
ck('all_four_records_completed',len(records)==4)
ck('all_original_connector_bytes_unchanged',all(Path(p).stat().st_size==v['bytes'] and hashlib.sha256(Path(p).read_bytes()).hexdigest()==v['sha256'] for p,v in seen.items()))
workflow=subprocess.check_output(['git','show',HEAD+':.github/workflows/ci.yml'],cwd=ROOT)
script=subprocess.check_output(['git','show',HEAD+':scripts/check.sh'],cwd=ROOT)
report={'schema_version':1,'result':'PASS_WITH_SCOPE_LIMITATIONS' if all(v['passed'] for v in checks) else 'FAIL','run_id':RUN,'head':HEAD,'counts':{'jobs':len(records),'unit_executions_each':667,'result_rows_each':39,'new_tls_lifecycle_cases_each':5,'actual_new_writer_observations_each':3,'legacy_backpressure_cases_each':2,'legacy_signal_cases_each':4,'diagnostic_checks':len(checks),'passed':sum(v['passed'] for v in checks),'failed':sum(not v['passed'] for v in checks)},'jobs':records,'checks':checks,'official_connector_artifacts':list(seen.values()),'workflow_sources':{'ci_yaml_sha256':hashlib.sha256(workflow).hexdigest(),'check_script_sha256':hashlib.sha256(script).hexdigest()},'scope':'Original secure lifecycle/harness author, separate official API/log parsing method, not an independent implementation. Imports no root verifier, executes no CI/Cargo/product/QEMU, writes only/tmp parser/report.','limits':['667 is the sum of actual passing libtest execution rows (default280/allfeatures367/vendor20), not667 unique functions or USB/hardware cases. Five TLS scenarios/two backpressure/four signal cases are separately reported lifecycle observations; the TLS parent test is already counted in libtest.',
'New secure5cases use memory-DER cfg(test) actual-adapter tests. CI safe output prints precondition counts/closure/cleanup-observed flags, not full before/stopped snapshots. Do not reconstruct unavailable runner/tmp bytes or claim trusted-root loader coverage.',
'Three actual PID/TID/comm/wchan observations are printed. Exact accepted names are pipe_write and anon_pipe_write; actual kernel and observed names are recorded, not inferred from the Ubuntu label. Earlier CI failures are not retroactively filled.',
'Legacy4signals prints before/after facts, child PID/starttime/UID, actual proc-ELF receipts and full source inventories. All logged source metadata must match exact immutableGit blobs. Indexed runner/tmp artifacts and binaries were not downloaded; their contents are not reconstructed.',
'Legacy2backpressure prints result/status/capacity/no-drain/socketEOF only, no per-child ELF guard. Linkage is fixed preceding workflow build/source/target path with no rebuild, not upgraded to a stronger actual ELF receipt.',
'Four-signal build metadata remains external prebuilt; not inferred. Allfeatures attribution is from successful exact scripts/check.sh order and both release completions.',
'No helper, actual USB, QEMU/hardware, ARM kernel redirection or complete Phase3 claim from ordinary-user native-platform loopback CI. Ready virtual cleanup snapshots and blocked socketEOF/exit have different scopes.',
'No repository changes, workflow reruns or GitHub writes. Read-only git cat-file/show is used only for exact historical source verification.']}

report['parser']={'path':__file__,'sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
OUT.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'path':str(OUT),'bytes':OUT.stat().st_size,'sha256':hashlib.sha256(OUT.read_bytes()).hexdigest(),'result':report['result'],'counts':report['counts']}))
for c in checks:
 if not c['passed']:print('FAIL',c)
