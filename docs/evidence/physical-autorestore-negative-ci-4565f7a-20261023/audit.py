import base64,datetime,hashlib,json,os,pathlib,re
D=pathlib.Path(__file__).resolve().parent;OUT=D/'review.json';HEAD='4565f7aaddb5a9377c15261a288ca9c3a57eebe5';RUN=39928383971
EXPECTED={120330295748:('22.04','aarch64'),120330295794:('24.04','aarch64'),120330295966:('22.04','x86_64'),120330296009:('24.04','x86_64')}
COUNTS=[1,2,20,0,0,8,31,4,24,0,0,0,0,0,0,4,2,64,0,0,8,31,4,45,0,0,0,0,0,0,20]
checks=[]
def sha(b):return hashlib.sha256(b).hexdigest()
def ck(name,test,detail=None):checks.append({'name':name,'passed':bool(test),'detail':detail})
def load(p):return json.loads(p.read_bytes())
localpath=D/'pinned-source-inputs.json';local=load(localpath)
files=sorted(p for p in D.rglob('*')if p.is_file() and p.name not in {'review.json','manifest.json'});before={str(p):{'length':len(p.read_bytes()),'sha256':sha(p.read_bytes())}for p in files}
run=load(D/'software-run-final.json');jobsapi=load(D/'software-jobs-final.json');jobs={v['id']:v for v in jobsapi['jobs']}
ck('actual run terminal success/expected4565f7a/attempt1',run['id']==RUN and run['head_sha']==HEAD and run['status']=='completed' and run['conclusion']=='success' and run['run_attempt']==1)
ck('four exact actual job IDs',jobsapi['total_count']==4 and set(jobs)==set(EXPECTED))
for item in load(D/'fetch-provenance.json')['responses']:
 p=D/item['path'];ck('exact successful gh GET '+item['path'],item['returncode']==0 and len(p.read_bytes())==item['length']and sha(p.read_bytes())==item['sha256'] and item['stderr_length']==0 and (D/(item['path']+'.stderr')).read_bytes()==b'')
for stem,path in [('workflow','.github/workflows/ci.yml'),('pages-workflow','.github/workflows/pages.yml'),('check','scripts/check.sh'),('signals','scripts/test-server-signals.py'),('backpressure','scripts/test-virtual-backpressure.py')]:
 api=load(D/(stem+'-source-api.json'));data=base64.b64decode(api['content']);blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
 import subprocess
 pinned=subprocess.check_output(['git','show',HEAD+':'+path],cwd='/home/ubuntu/github/usbip-client')
 ck('pinned official GH source API/Gitblob '+path,data==(D/(stem+'.source')).read_bytes()==pinned and len(data)==api['size'] and blob==api['sha'])
repo='/home/ubuntu/github/usbip-client'
names=subprocess.check_output(['git','ls-tree','-rz','--name-only',HEAD],cwd=repo).decode().split('\0')
selected=sorted(n for n in names if n and (n=='Cargo.lock' or pathlib.Path(n).suffix in {'.rs','.toml'} or n.startswith('vendor/') or n=='scripts/test-server-signals.py'))
expected_source=[];source_fingerprint=hashlib.sha256()
for n in selected:
 data=subprocess.check_output(['git','show',HEAD+':'+n],cwd=repo);h=sha(data);expected_source.append({'path':n,'length':len(data),'sha256':h});source_fingerprint.update(n.encode()+b'\0'+str(len(data)).encode()+b'\0'+h.encode()+b'\n')
ck('all187 source inputs reconstructed from exact committed Git objects',len(selected)==187 and expected_source==local['files'] and source_fingerprint.hexdigest()==local['source_sha256'] and local['head']==HEAD)
for jobid in [*EXPECTED,120330295684,120330353686]:
 name='pages-check-log-connector.json' if jobid==120330295684 else f'job-{jobid}-connector.json';wrapper=load(D/name)
 result=wrapper['value'] if jobid==120330295684 else wrapper['value']['result']
 ck('connector decoded complete log preserved '+str(jobid),wrapper['status']=='fulfilled' and result['isError'] is False and result['structuredContent']['content'].encode()==(D/f'job-{jobid}.log').read_bytes())
workflow=(D/'workflow.source').read_text();buildsource=(D/'check.source').read_text();signalssource=(D/'signals.source').read_bytes()
ck('CI step uses existing allfeature build then explicit skip-build signals',workflow.index('run: scripts/check.sh')<workflow.index('Unprivileged virtual-server lifecycle smoke')<workflow.index('Unprivileged server signal lifecycle (four cases)') and 'python3 scripts/test-server-signals.py --skip-build --evidence "$RUNNER_TEMP/server-signals"' in workflow and 'cargo build --workspace --all-features --release --locked' in buildsource)
ck('signal harness bytes match pinned CI source set',next(v for v in local['files'] if v['path']=='scripts/test-server-signals.py')['sha256']==sha(signalssource))
records=[]
for jobid,(ubuntu,arch)in EXPECTED.items():
 job=jobs[jobid];prefix=str(jobid)+' ';raw=(D/f'job-{jobid}.log').read_bytes();log=raw.decode('utf-8-sig');clean=[re.sub(r'^\d{4}-\d\d-\d\dT[^ ]+Z ','',line)for line in log.splitlines()]
 ck(prefix+'exact checkout after git log command',any(line.endswith('git log -1 --format=%H') and clean[i+1]==HEAD for i,line in enumerate(clean[:-1])))
 ck(prefix+'actual job and all steps success expected SHA',job['run_id']==RUN and job['head_sha']==HEAD and job['status']=='completed' and job['conclusion']=='success' and job['run_attempt']==1 and all(v['status']=='completed'and v['conclusion']=='success'for v in job['steps']))
 ck(prefix+'no failed Rust result rows','test result: FAILED' not in log)
 rows=re.findall(r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;',log);counts=[int(v[0])for v in rows]
 ck(prefix+'31 exact result rows 90default158allfeatures20vendor=268',counts==COUNTS and all(all(int(t)==0 for t in v[1:])for v in rows)and[sum(counts[:15]),sum(counts[15:30]),sum(counts[30:])]==[90,158,20])
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
 ck(prefix+'187 clean source inputs match independently reconstructed exact4565f7a Git set',sb==sa and sb['head']==HEAD and sb['git_status']=='' and sb['dirty']is False and sb['files']==local['files']and len(sb['files'])==187 and h.hexdigest()==sb['source_sha256']==local['source_sha256']and summary['source_unchanged']is True)
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
 records.append({'source_inputs':len(sb['files']),'id':jobid,'name':job['name'],'url':job['html_url'],'status':job['status'],'conclusion':job['conclusion'],'native_environment':{'ubuntu_version':ubuntu,'arch':arch,'uname':unames[0],'rustc':rust[0],'cargo':cargo[0],'host_uid':summary['host_uid']},'unit_test_executions':268,'test_result_rows':31,'default_passed':90,'allfeatures_passed':158,'vendor_passed':20,'legacy_backpressure':legacy,'signals_cases':casefacts,'signals_summary_reconstructed_sha256':sha(jsontext.encode()),'source_fingerprint_sha256':sb['source_sha256'],'binary_sha256':bb['sha256'],'binary_provenance':'summary explicitly skip-build/external; actual same binary inode/hash linked to earlier successful check.sh all-features release build in this job, not inferred from feature fields','raw_log':before[str(D/f'job-{jobid}.log')]})
pagesrun=load(D/'pages-run-final.json');pagesjobs=load(D/'pages-jobs-final.json');pagesworkflow=(D/'pages-workflow.source').read_text()
ck('Pages exact run commit attempt1 terminal success',pagesrun['id']==39928383815 and pagesrun['head_sha']==HEAD and pagesrun['status']=='completed' and pagesrun['conclusion']=='success' and pagesrun['run_attempt']==1 and pagesrun['event']=='push')
ck('Pages actual check and deploy both terminal success',pagesjobs['total_count']==2 and {j['id'] for j in pagesjobs['jobs']}=={120330295684,120330353686} and all(j['head_sha']==HEAD and j['run_id']==pagesrun['id'] and j['run_attempt']==1 and j['status']=='completed' and j['conclusion']=='success' and all(st['status']=='completed' and st['conclusion']=='success' for st in j['steps']) for j in pagesjobs['jobs']))
pagelogs={j['name']:[re.sub(r'^\d{4}-\d\d-\d\dT[^ ]+Z ','',line)for line in (D/f"job-{j['id']}.log").read_text(encoding='utf-8-sig').splitlines()] for j in pagesjobs['jobs']}
for name,lines in pagelogs.items():ck('Pages '+name+' actual checkout SHA',any(line.endswith('git log -1 --format=%H') and lines[i+1]==HEAD for i,line in enumerate(lines[:-1])))
ck('Pages checker115 and JavaScript syntax successful job steps','PASS: 115 local asset/link/SVG checks; unique HTML IDs and image metadata' in pagelogs['check'] and any('node --check website/app.js' in line for line in pagelogs['check']))
uses=re.findall(r'uses: ([^ \n]+)',pagesworkflow)
ck('Pages direct action references full pinned SHA',len(uses)==5 and all(re.fullmatch(r'actions/[^@]+@[0-9a-f]{40}',value) for value in uses))
ck('Pages PR gated, main gated, check dependency, website-only upload',"if: github.event_name != 'pull_request' && github.ref == 'refs/heads/main'" in pagesworkflow and 'needs: check' in pagesworkflow and 'path: website' in pagesworkflow)
archived=[line[2:] for line in pagelogs['deploy'] if line.startswith('./') and not line.endswith('/')]
website_paths=subprocess.check_output(['git','ls-tree','-rz','--name-only',HEAD,'website/'],cwd=repo).decode().split('\0');expected_site=sorted(path.removeprefix('website/') for path in website_paths if path and not any(part.startswith('.') for part in pathlib.PurePosixPath(path.removeprefix('website/')).parts))
ck('Pages actual archive contains only six committed nonhidden website files',sorted(archived)==expected_site and len(expected_site)==6 and '  INPUT_PATH: website' in pagelogs['deploy'] and '  path: website' in pagelogs['deploy'] and any('--exclude=".[^/]*"' in line for line in pagelogs['deploy']),{'actual':archived,'action_excluded_hidden_file':'website/.nojekyll'})
ck('Pages actual uploaded artifact and deployment version success',any('Artifact ID is 12489134583' in line for line in pagelogs['deploy']) and any('"pages_build_version": "'+HEAD+'"' in line for line in pagelogs['deploy']) and any('Created deployment for '+HEAD in line for line in pagelogs['deploy']) and 'Reported success!' in pagelogs['deploy'] and 'Evaluated environment url: https://tiansongyu.github.io/usbip-client/' in pagelogs['deploy'])
pages={'run':{key:pagesrun[key] for key in ['id','html_url','head_sha','status','conclusion','run_attempt']},'jobs':[{'id':j['id'],'name':j['name'],'conclusion':j['conclusion']}for j in pagesjobs['jobs']],'site_checks':115,'artifact_id':12489134583,'archive_members':archived,'excluded_hidden_file':'website/.nojekyll; upload-pages-artifact tar explicitly excludes hidden files; this is an Actions artifact deployment, not Jekyll source branch publishing','deployment_url':'https://tiansongyu.github.io/usbip-client/','scope':'Successful static website check/archive/deployment only; neither Phase5 GUI nor USB or platform runtime acceptance. No live-page HTTP audit performed.','nonblocking_log_warnings':['configure/deploy Pages and nested upload-artifact target Node20 but GitHub forced Node24; deployment completed successfully.','Node punycode deprecation warning from action dependencies; no job failure.']}
changed=[str(p)for p in files if before[str(p)]!={'length':len(p.read_bytes()),'sha256':sha(p.read_bytes())}];ck('all raw API/log/source inputs unchanged across review',not changed,{'count':len(files),'changed':changed})
result={'schema_version':1,'result':'PASS'if all(c['passed']for c in checks)else'FAIL','scope':'Independent parsing of actual four-platform GitHub CI and Pages logs/API; no runner.verify, CI run/rerun, build or product test execution triggered. This audit parses logged observations, not separate raw case files unavailable from CI.','reviewed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head_sha':HEAD,'run':{k:run[k]for k in ['id','html_url','head_sha','status','conclusion','run_attempt','updated_at']},'checks':checks,'check_count':len(checks),'failures':[c for c in checks if not c['passed']],'jobs':records,'pages':pages,'totals':{'jobs':4,'unit_test_executions':1072,'legacy_backpressure_cases':8,'new_signal_cases':16,'source_inputs_per_job':187,'pages_local_site_checks':115},'mustfix':[] if all(c['passed']for c in checks) else [c['name']for c in checks if not c['passed']],'review_corrections':[{'kind':'audit expectation correction, not runtime failure','initial_report':'review-initial.json','initial_parser':'audit-initial.py','reason':'Initial archive comparison incorrectly included .nojekyll despite upload-pages-artifact logged tar --exclude hidden files. Final comparison retains six observed members and checks the explicit exclusion; no CI/QEMU/build rerun.'}],'collection_notes':['GitHub connector was used first for run/jobs and all decoded logs. Its jobs wrapper omits fields needed for exact source/attempt checking, so full official gh GET JSON and pinned source contents were also saved.','A local display-only JSON parse accidentally treated the first Pages text log as JSON; it was already stored and preserved. The deploy log response from that batch was not retained, so the completed deploy log was retrieved once more. No CI action was rerun.'],'input_artifacts_sha256_before_and_after':before,'limitations':['Native x86_64/aarch64 Ubuntu22.04/24.04 software CI does not establish physical USB or aarch64 kernel redirection interoperability.','The new harness was invoked --skip-build; its feature metadata explicitly says external prebuilt/not inferred. Provenance follows the prior successful same-job allfeatures build, fixed workflow/check.sh source, actual proc-exe inode/SHA and before/after binary guards.','Logs retain full printed summary before/after snapshots and pipe proofs, plus hashes of per-case protocol/stdout artifacts. CI did not upload those separate raw files; this review does not pretend to reconstruct unavailable byte captures.','Ready stopped snapshots prove virtual cleanup; blocked case exit/socket EOF is not proof of an unread stopped snapshot or physical restoration.','Only timestamp prefixes were removed in memory to decode logged JSON. Connector-decoded UTF-8 GitHub logs, including BOM/newlines, were preserved and rehashed unchanged. HTTP transfer encoding bytes are not claimed.','These are actual run39928383971 attempt1 logs bound to4565f7a; no rerun/trigger/cancel/write action or repository modification was performed.']}
OUT.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');b=OUT.read_bytes();print(json.dumps({'path':str(OUT),'length':len(b),'sha256':sha(b),'checks':len(checks),'result':result['result'],'failures':result['failures']}))
