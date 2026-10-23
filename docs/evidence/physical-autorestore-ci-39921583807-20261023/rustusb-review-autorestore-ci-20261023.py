import base64,datetime,hashlib,json,os,pathlib,re
D=pathlib.Path('/tmp/rustusb-autorestore-ci-39921583807-20261023');OUT=pathlib.Path('/tmp/rustusb-autorestore-ci-independent-review-20261023.json');HEAD='2ab997673a5894099f8df770707a060da3735154';RUN=39921583807
EXPECTED={120307841130:('22.04','aarch64'),120307841300:('22.04','x86_64'),120307841438:('24.04','x86_64'),120307842454:('24.04','aarch64')}
checks=[]
def sha(b):return hashlib.sha256(b).hexdigest()
def ck(name,test,detail=None):checks.append({'name':name,'passed':bool(test),'detail':detail})
def load(p):return json.loads(p.read_bytes())
localpath=pathlib.Path('/tmp/rustusb-autorestore-ci-source-contract-20261023.json');local=load(localpath)['expected_clean_tracked_source_state']
files=sorted(p for p in D.rglob('*')if p.is_file())+[localpath];before={str(p):{'length':len(p.read_bytes()),'sha256':sha(p.read_bytes())}for p in files}
run=load(D/'run-final.json');jobsapi=load(D/'jobs-final.json');jobs={v['id']:v for v in jobsapi['jobs']}
ck('actual run terminal success/expected2ab9976/attempt1',run['id']==RUN and run['head_sha']==HEAD and run['status']=='completed' and run['conclusion']=='success' and run['run_attempt']==1)
ck('four exact actual job IDs',jobsapi['total_count']==4 and set(jobs)==set(EXPECTED))
for item in load(D/'fetch-provenance.json')['responses']:
 p=D/item['path'];ck('exact successful gh GET '+item['path'],item['returncode']==0 and len(p.read_bytes())==item['length']and sha(p.read_bytes())==item['sha256'] and item['stderr_length']==0 and (D/(item['path']+'.stderr')).read_bytes()==b'')
for item in load(D/'source-fetch-provenance.json'):
 api=load(D/item['api_path']);b=base64.b64decode(api['content']);ck('pinned GH source bytes/Gitblob '+item['decoded_source'],b==(D/item['decoded_source']).read_bytes() and len(b)==item['length']and sha(b)==item['source_sha256']and hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()==api['sha']==item['git_blob']and '?ref='+HEAD in item['command'][-1])
workflow=(D/'workflow.source').read_text();buildsource=(D/'check-script.source').read_text();signalssource=(D/'signals-script.source').read_bytes()
ck('CI step uses existing allfeature build then explicit skip-build signals',workflow.index('run: scripts/check.sh')<workflow.index('Unprivileged virtual-server lifecycle smoke')<workflow.index('Unprivileged server signal lifecycle (four cases)') and 'python3 scripts/test-server-signals.py --skip-build --evidence "$RUNNER_TEMP/server-signals"' in workflow and 'cargo build --workspace --all-features --release --locked' in buildsource)
ck('same frozen locally-audited signal harness bytes',sha(signalssource)=='bd8d3c40f174aea0a1dee208a81c0234f01c161e66500766c21b11b328b8fce9')
records=[]
for jobid,(ubuntu,arch)in EXPECTED.items():
 job=jobs[jobid];prefix=str(jobid)+' ';raw=(D/f'job-{jobid}.log').read_bytes();log=raw.decode('utf-8-sig');clean=[re.sub(r'^\d{4}-\d\d-\d\dT[^ ]+Z ','',line)for line in log.splitlines()]
 ck(prefix+'actual checkout full commit in raw log',any(line.endswith('git log -1 --format=%H') and index+1<len(clean) and clean[index+1]==HEAD for index,line in enumerate(clean)))
 ck(prefix+'actual job and all steps success expected SHA',job['run_id']==RUN and job['head_sha']==HEAD and job['status']=='completed' and job['conclusion']=='success' and all(v['status']=='completed'and v['conclusion']=='success'for v in job['steps']))
 rows=re.findall(r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;',log);counts=[int(v[0])for v in rows]
 ck(prefix+'31 exact result rows 90default158allfeatures20vendor=268',len(counts)==31 and all(all(int(t)==0 for t in v[1:])for v in rows)and[sum(counts[:15]),sum(counts[15:30]),sum(counts[30:])]==[90,158,20])
 ck(prefix+'Ubuntu release/rust native host exact',re.findall(r'VERSION_ID="([^"]+)"',log)==[ubuntu]and re.findall(r'host: ([^\n\r]+)',log)==[f'{arch}-unknown-linux-gnu'])
 unames=[line for line in clean if line.startswith('Linux ')and line.endswith(' GNU/Linux')]
 rust=[line for line in clean if re.match(r'^rustc 1\.97\.1 ',line)];cargo=[line for line in clean if re.match(r'^cargo 1\.97\.1 ',line)]
 ck(prefix+'native uname and pinned toolchain observed',len(unames)==1 and unames[0].endswith(f'{arch} {arch} {arch} GNU/Linux')and len(rust)==len(cargo)==1)
 decoded=[];decoder=json.JSONDecoder()
 for offset,line in enumerate(clean):
  if line!='{':continue
  text='\n'.join(clean[offset:])
  try:value,end=decoder.raw_decode(text)
  except json.JSONDecodeError:continue
  if isinstance(value,dict)and'scope'in value:decoded.append((value,text[:end]+'\n'))
 legacy=[v for v,t in decoded if v.get('scope')=='local CLI output backpressure; no physical USB or kernel changes'];new=[(v,t)for v,t in decoded if v.get('scope')=='ordinary-user loopback virtual profiles only; no physical USB, modules, ptrace, or network configuration']
 ck(prefix+'one exact legacy2 and new4 full JSON summary',len(legacy)==len(new)==1)
 legacy=legacy[0];summary,jsontext=new[0]
 ck(prefix+'legacy2 actual sigint0/deadline1 within original bounds',legacy['result']=='PASS'and legacy['host_uid']>0 and [v['mode']for v in legacy['checks']]==['sigint','deadline']and [v['exit']for v in legacy['checks']]==[0,1]and all(v['stdout_capacity']==4096 and v['stdout_drained_after_fill']is False and v['lease_socket_closed']is True for v in legacy['checks'])and legacy['checks'][0]['elapsed_seconds']<2 and 4<=legacy['checks'][1]['elapsed_seconds']<=7)
 compact=[]
 for line in clean:
  if not(line.startswith('{')and line.endswith('}')):continue
  try:value=json.loads(line)
  except json.JSONDecodeError:continue
  if isinstance(value,dict)and'summary_sha256'in value:compact.append(value)
 ck(prefix+'printed full summary reconstructs exact harness file SHA',len(compact)==1 and compact[0]['summary_sha256']==sha(jsontext.encode())and compact[0]['result']==summary['result']=='PASS')
 ck(prefix+'skip-build provenance remains external/not inferred',summary['build']=={'command':None,'features':'external prebuilt; not inferred','skipped':True})
 sb=summary['source_before'];sa=summary['source_after'];h=hashlib.sha256()
 for f in sb['files']:h.update(os.fsencode(f['path'])+b'\0'+str(f['length']).encode()+b'\0'+f['sha256'].encode()+b'\n')
 ck(prefix+'183 clean source inputs match independently reconstructed fixed Gitblob set',sb==sa and sb['head']==HEAD and sb['git_status']=='' and sb['dirty']is False and sb['files']==local['files']and len(sb['files'])==183 and h.hexdigest()==sb['source_sha256']==local['source_sha256']and summary['source_unchanged']is True)
 ck(prefix+'native summary host confirms actual matrix architecture',summary['host'][0]=='Linux'and summary['host'][-1]==arch and summary['host_uid']==legacy['host_uid']>0)
 bb=summary['binary_before'];ck(prefix+'same executable sha before/after all cases',bb['sha256']==summary['binary_after_sha256']and summary['binary_unchanged']is True)
 ck(prefix+'four exact combination results',len(summary['cases'])==4 and {(v['mode'],v['signal'])for v in summary['cases']}=={(m,t)for m in ['ready','blocked']for t in ['sigint','sigterm']})
 casefacts=[]
 for case in summary['cases']:
  mode=case['mode'];sig=case['signal'];name=prefix+mode+'/'+sig+' ';pre=case['before']['snapshot'];post=case['after'];dv={v['bus_id']:v for v in pre['devices']};child=case['child'];sent=case['sent']
  ck(name+'pre two actual exclusive/configured/pending leases',pre['schema_version']==1 and set(dv)=={'1-1','1-2'}and all(v['leased']is True and v['configuration']==v['pending_urbs']==1 and v['hid_queued_reports']==v['cdc_queued_bytes']==0 and v['counters']['control']==v['counters']['sessions_started']==1 for v in dv.values()))
  ck(name+'owned child signal identity and expected INT/TERM',sent['identity']==child and sent['pid']==child['pid']and sent['signal']=={'sigint':2,'sigterm':15}[sig]and child['uids']==[summary['host_uid']]*4 and child['starttime']>0)
  ck(name+'actual exit0 without emergency/harness error',case['result']=='PASS'and case['final_returncode']==post['returncode']==0 and case['forced_cleanup']is False and 'error'not in case and post['wait_error']is None and 0<post['signal_exit_seconds']<8)
  ex=case['executed_binary'];ck(name+'actual executed ELF inode/SHA guards',ex['device']==bb['device']and ex['inode']==bb['inode']and ex['sha256']==bb['sha256']and ex['proc_exe']==f"/proc/{child['pid']}/exe")
  ck(name+'both actual data socket EOF observations',set(post['data_sockets'])=={'1-1','1-2'}and all(v=={'eof':True,'unexpected_hex':''}for v in post['data_sockets'].values()))
  if mode=='ready':
   stopped=post['stopped_events'];ck(name+'one actual stopped snapshot and stdoutEOF',post['backend_cleanup_observed']is True and post['stdout_eof']is True and post['stdout_unread_bytes']==0 and len(stopped)==1 and stopped[0]['event']=='stopped'and stopped[0]['schema_version']==stopped[0]['snapshot']['schema_version']==1)
   released=stopped[0]['snapshot']['devices'];ck(name+'stopped two virtual resources0',len(released)==2 and {v['bus_id']for v in released}=={'1-1','1-2'}and all(v['leased']is False and v['configuration']==v['pending_urbs']==v['hid_queued_reports']==v['cdc_queued_bytes']==0 for v in released))
  else:
   pp=case['pipe'];w=pp['blocked_writer'];ck(name+'actual4096 pipe occupancy/EAGAIN/no drain',pp['capacity']==pp['bytes_after_fill']==pp['synthetic_padding_bytes']==post['stdout_unread_bytes']==4096 and pp['bytes_before']==0 and pp['independent_writer_eagain']is True and case['stdout_drained_after_fill']is False and post['stdout_read_after_fill']is False)
   ck(name+'independent OFD does not pollute child blocking flags',pp['independent_writer_flags']&os.O_NONBLOCK!=0 and all(pp[k]&os.O_NONBLOCK==0 for k in ['original_writer_flags_before','original_writer_flags_after','child_stdout_flags_before','child_stdout_flags_after']))
   ck(name+'actual child writer blocked before signal',w['wchan'].endswith('pipe_write')and w['comm'].startswith('virtual-test-')and w['monotonic_ns']<sent['monotonic_ns'])
   ck(name+'no false backend resource claim from EOF',post['backend_cleanup_observed']is False and post['stdout_eof']is False and post['stopped_events']==[])
  casefacts.append({'mode':mode,'signal':sig,'exit':post['returncode'],'elapsed_seconds':post['signal_exit_seconds'],'backend_resources0_visible':mode=='ready','full4096_undrained_writer_blocked':mode=='blocked'})
 records.append({'id':jobid,'name':job['name'],'url':job['html_url'],'status':job['status'],'conclusion':job['conclusion'],'native_environment':{'ubuntu_version':ubuntu,'arch':arch,'uname':unames[0],'rustc':rust[0],'cargo':cargo[0],'host_uid':summary['host_uid']},'unit_test_executions':268,'test_result_rows':31,'ordered_passed_counts':counts,'failed_ignored_measured_filtered':0,'default_passed':90,'allfeatures_passed':158,'vendor_passed':20,'legacy_backpressure':legacy,'signals_cases':casefacts,'signals_summary_reconstructed_sha256':sha(jsontext.encode()),'source_fingerprint_sha256':sb['source_sha256'],'binary_sha256':bb['sha256'],'binary_provenance':'summary explicitly skip-build/external; actual same binary inode/hash linked to earlier successful check.sh all-features release build in this job, not inferred from feature fields','raw_log':before[str(D/f'job-{jobid}.log')]})
changed=[str(p)for p in files if before[str(p)]!={'length':len(p.read_bytes()),'sha256':sha(p.read_bytes())}];ck('all raw API/log/source inputs unchanged across review',not changed,{'count':len(files),'changed':changed})
result={'schema_version':1,'result':'PASS'if all(c['passed']for c in checks)else'FAIL','scope':'Independent parsing of actual four-platform GitHub CI logs/API; local harness authored by reviewer, not independent harness implementation validation. No CI run/rerun/build/test execution triggered.','reviewed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head_sha':HEAD,'run':{k:run[k]for k in ['id','html_url','head_sha','status','conclusion','run_attempt','updated_at']},'checks':checks,'check_count':len(checks),'failures':[c for c in checks if not c['passed']],'jobs':records,'totals':{'jobs':4,'unit_test_executions':1072,'legacy_backpressure_cases':8,'new_signal_cases':16},'input_artifacts_sha256_before_and_after':before,'limitations':['Native x86_64/aarch64 Ubuntu22.04/24.04 software CI does not establish physical USB or aarch64 kernel redirection interoperability.','The new harness was invoked --skip-build; its feature metadata explicitly says external prebuilt/not inferred. Provenance follows the prior successful same-job allfeatures build, fixed workflow/check.sh source, actual proc-exe inode/SHA and before/after binary guards.','Logs retain full printed summary before/after snapshots and pipe proofs, plus hashes of per-case protocol/stdout artifacts. CI did not upload those separate raw files; this review does not pretend to reconstruct unavailable byte captures.','Ready stopped snapshots prove virtual cleanup; blocked case exit/socket EOF is not proof of an unread stopped snapshot or physical restoration.','The 183 source entries were independently reconstructed from exact2ab9976 Git blobs, not a possibly changed working tree.','Only timestamp prefixes were removed in memory to decode logged JSON. Original GitHub log response bytes were preserved and rehashed unchanged.','These are actual run39921583807 attempt1 logs bound to2ab9976; no rerun/trigger/cancel/write action or repository modification was performed.']}
OUT.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');b=OUT.read_bytes();print(json.dumps({'path':str(OUT),'length':len(b),'sha256':sha(b),'checks':len(checks),'result':result['result'],'failures':result['failures']}))
