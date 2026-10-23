from pathlib import Path, PurePosixPath
import hashlib,json,re,subprocess,zipfile
B=Path('/tmp/rustusb-secure-foundation-ci-f4774a6-20261023')
R=Path('/home/ubuntu/github/usbip-client')
HEAD='f4774a63e189a0cfaec9d8515b719b0e399c87ea'
checks=[]
def check(name,ok,detail=None):checks.append({'name':name,'passed':bool(ok),'detail':detail})
def digest(b):return hashlib.sha256(b).hexdigest()
def readj(p):return json.loads(p.read_text())
def save(name,o): (B/name).write_text(json.dumps(o,ensure_ascii=False,indent=2)+'\n')
def clean(text):
 return re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',re.sub(r'^\ufeff?\d{4}-[^ ]+ ','',text,flags=re.M))
def objects(text):
 decoder=json.JSONDecoder();out=[]
 for m in re.finditer(r'^\{',text,re.M):
  try:o,end=decoder.raw_decode(text[m.start():])
  except ValueError:continue
  out.append((o,text[m.start():m.start()+end]+'\n'))
 return out
runs={kind:readj(B/(kind+'-run-initial.json')) for kind in ['software','pages']}
for kind,run in runs.items():
 check(kind+' run exact commit',run['head_sha']==HEAD)
 check(kind+' run completed success',run['status']=='completed' and run['conclusion']=='success')
 check(kind+' push attempt1',run['event']=='push' and run['run_attempt']==1)
alljobs={kind:readj(B/(kind+'-jobs-api.json'))['jobs'] for kind in ['software','pages']}
check('four software matrix jobs',len(alljobs['software'])==4)
check('two Pages jobs',len(alljobs['pages'])==2)
expected={'check (ubuntu-22.04, x86_64)':('22.04','x86_64'),'check (ubuntu-24.04, x86_64)':('24.04','x86_64'),'check (ubuntu-22.04-arm, aarch64)':('22.04','aarch64'),'check (ubuntu-24.04-arm, aarch64)':('24.04','aarch64')}
check('exact matrix names',set(x['name'] for x in alljobs['software'])==set(expected))
for kind,jobs in alljobs.items():
 connector=readj(B/(kind+'-jobs-connector-initial.json'))['structuredContent']['jobs']
 if len(connector)==1 and 'jobs' in connector[0]:connector=connector[0]['jobs']
 check(kind+' connector/API job agreement',[(x['id'],x['status'],x['conclusion']) for x in jobs]==[(x['id'],x['status'],x['conclusion']) for x in connector])
 with zipfile.ZipFile(B/(kind+'-run-logs.zip')) as archive:
  for job in jobs:
   label=job['name'];jid=job['id'];log=(B/f'job-{jid}.log').read_bytes()
   check(label+' job terminal success',job['status']=='completed' and job['conclusion']=='success' and job['head_sha']==HEAD)
   check(label+' non-skipped steps all successful',all(x['conclusion']=='success' for x in job['steps']))
   candidates=[n for n in archive.namelist() if '/' not in n and n.endswith('_'+label+'.txt')]
   check(label+' one full ZIP job log',len(candidates)==1)
   if len(candidates)==1:check(label+' connector decoded bytes equal raw ZIP',archive.read(candidates[0])==log)
   check(label+' connector response matches extracted log',readj(B/f'job-{jid}-connector.json')['structuredContent']['content'].encode()==log)
   check(label+' checkout exact commit',HEAD in clean(log.decode('utf-8-sig')))

# Freeze the actual workflow/check logic independently from the currently edited worktree.
source_logic={}
for name in ['.github/workflows/ci.yml','.github/workflows/pages.yml','scripts/check.sh','scripts/test-server-signals.py','scripts/test-virtual-backpressure.py','crates/rustusb-transport/src/tls/tests.rs']:
 data=subprocess.check_output(['git','show',HEAD+':'+name],cwd=R)
 target=B/'frozen-logic'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
 source_logic[name]={'length':len(data),'sha256':digest(data)}
check('locked check source includes fmt/clippy/test/release/rustdoc and vendor',all(x in (B/'frozen-logic/scripts/check.sh').read_text() for x in ['cargo fmt --all -- --check','cargo clippy --workspace --all-targets --locked -- -D warnings','cargo test --workspace --locked','cargo build --workspace --release --locked','RUSTDOCFLAGS="-D warnings" cargo doc','cargo test --workspace --all-features --locked','cargo build --workspace --all-features --release --locked','--manifest-path vendor/nusb/Cargo.toml']))

jobs_report=[];shared_files=None;shared_source_sha=None
for job in alljobs['software']:
 name=job['name'];jid=job['id'];text=clean((B/f'job-{jid}.log').read_text(encoding='utf-8-sig'));version,arch=expected[name]
 counts=[tuple(map(int,m.groups())) for m in re.finditer(r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;',text)]
 check(name+' 35 Cargo result records',len(counts)==35,len(counts))
 totals=[sum(row[i] for row in counts) for i in range(5)]
 check(name+' 304 passes with zero failures/ignored/filtered',totals==[304,0,0,0,0],totals)
 check(name+' default/allfeatures/vendor breakdown',len(counts)==35 and [sum(x[0] for x in counts[:17]),sum(x[0] for x in counts[17:34]),counts[-1][0]]==[108,176,20])
 tls_blocks=re.findall(r'Running unittests src/lib\.rs \(target/debug/deps/rustusb_transport-[^\n]+\)\n(.*?)test result: ok\. 18 passed;',text,re.S)
 check(name+' two actual transport test executables',len(tls_blocks)==2)
 for i,block in enumerate(tls_blocks):check(name+f' TLS block{i} eighteen named pass lines',len(re.findall(r'^test tls::tests::[^\n]+ \.\.\. ok$',block,re.M))==18)
 check(name+' OS version observed',f'VERSION_ID="{version}"' in text)
 check(name+' native architecture observed',f'host: {arch}-unknown-linux-gnu' in text)
 check(name+' pinned rustc and cargo observed','rustc 1.97.1' in text and 'cargo 1.97.1' in text)
 objs=objects(text)
 bp=[(o,s) for o,s in objs if o.get('scope')=='local CLI output backpressure; no physical USB or kernel changes']
 summaries=[(o,s) for o,s in objs if 'source_before' in o and 'binary_before' in o and 'cases' in o]
 receipts=[o for o,s in objs if 'summary_sha256' in o and 'result' in o]
 check(name+' one actual backpressure result',len(bp)==1)
 check(name+' one actual signal summary and receipt',len(summaries)==1 and len(receipts)==1)
 if not (len(bp)==len(summaries)==len(receipts)==1):continue
 backpressure,bpraw=bp[0];summary,raw=summaries[0]
 (B/f'job-{jid}-backpressure.json').write_text(bpraw)
 (B/f'job-{jid}-signals-summary.json').write_text(raw)
 check(name+' exact printed signal-summary digest',digest(raw.encode())==receipts[0]['summary_sha256'])
 check(name+' two backpressure modes',backpressure['result']=='PASS' and {c['mode'] for c in backpressure['checks']}=={'sigint','deadline'} and len(backpressure['checks'])==2)
 for case in backpressure['checks']:
  check(name+' backpressure '+case['mode'],case['exit']==(0 if case['mode']=='sigint' else 1) and case['stdout_capacity']==4096 and case['stdout_drained_after_fill'] is False and case['lease_socket_closed'] is True)
 check(name+' signal root summary success and ordinary UID',summary['result']=='PASS' and summary['host_uid']>0 and summary['host'][-1]==arch)
 before,after=summary['source_before'],summary['source_after']
 check(name+' source guards and exact head',summary['source_unchanged'] is True and before==after and before['head']==HEAD and before['dirty'] is False and before['git_status']=='')
 files=before['files'];fingerprint=hashlib.sha256()
 for rec in files:fingerprint.update(rec['path'].encode()+b'\0'+str(rec['length']).encode()+b'\0'+rec['sha256'].encode()+b'\n')
 check(name+' recorded source fingerprint independently rebuilt',fingerprint.hexdigest()==before['source_sha256'])
 check(name+' source paths unique sorted',len({r['path'] for r in files})==len(files) and files==sorted(files,key=lambda r:r['path']))
 if shared_files is None:
  shared_files=files;shared_source_sha=before['source_sha256']
  for rec in files:
   path=PurePosixPath(rec['path']);safe=not path.is_absolute() and '..' not in path.parts
   data=subprocess.check_output(['git','show',HEAD+':'+str(path)],cwd=R) if safe else b''
   check('frozen source Git blob '+str(path),safe and len(data)==rec['length'] and digest(data)==rec['sha256'])
  save('frozen-source-records.json',{'head':HEAD,'source_sha256':shared_source_sha,'files':files})
 else:check(name+' same exact frozen source records',files==shared_files and before['source_sha256']==shared_source_sha)
 binary=summary['binary_before']
 check(name+' binary unchanged',summary['binary_unchanged'] is True and binary['sha256']==summary['binary_after_sha256'])
 check(name+' all-features release built before smoke','cargo build --workspace --all-features --release --locked' in (B/'frozen-logic/scripts/check.sh').read_text() and 'Unprivileged virtual-server lifecycle smoke (all-features binary)' in [x['name'] for x in job['steps']])
 cases=summary['cases'];check(name+' four actual signal cases',len(cases)==4 and {(c['mode'],c['signal']) for c in cases}=={('ready','sigint'),('ready','sigterm'),('blocked','sigint'),('blocked','sigterm')})
 case_records=[]
 for c in cases:
  label=name+' '+c['mode']+'-'+c['signal'];a=c['after'];execbin=c['executed_binary']
  check(label+' actual executable receipt',all(execbin[k]==binary[k] for k in ['device','inode','sha256']) and execbin['resolved']==binary['path'] and execbin['proc_exe']==f"/proc/{c['child']['pid']}/exe")
  check(label+' real child identity at signal',c['sent']['identity']==c['child'] and c['child']['uids']==[summary['host_uid']]*4 and c['sent']['signal']==(2 if c['signal']=='sigint' else 15))
  check(label+' both leases pending before signal',len(c['before']['snapshot']['devices'])==2 and all(d['leased'] and d['pending_urbs']==1 and d['configuration']==1 for d in c['before']['snapshot']['devices']))
  check(label+' cooperative wait0 no forced cleanup',c['result']=='PASS' and c['final_returncode']==0 and c['forced_cleanup'] is False and a['returncode']==0 and a['wait_error'] is None and a['signal_exit_seconds']<8)
  check(label+' both actual data sockets EOF',set(a['data_sockets'])=={'1-1','1-2'} and all(v['eof'] is True and v['unexpected_hex']=='' for v in a['data_sockets'].values()))
  check(label+' harness assertions all true',all(x['passed'] for x in c['assertions']))
  if c['mode']=='ready':
   stopped=a['stopped_events'];check(label+' ready snapshot proves cleanup',a['backend_cleanup_observed'] is True and a['stdout_eof'] is True and len(stopped)==1 and all(not d['leased'] and d['pending_urbs']==0 and d['configuration']==0 and d['hid_queued_reports']==0 and d['cdc_queued_bytes']==0 for d in stopped[0]['snapshot']['devices']))
  else:
   pipe=c['pipe'];check(label+' blocked actual pipe evidence',pipe['capacity']==4096 and pipe['bytes_after_fill']==4096 and pipe['independent_writer_eagain'] is True and pipe['blocked_writer']['wchan']=='pipe_write' and pipe['blocked_writer']['monotonic_ns']<=c['sent']['monotonic_ns'])
   check(label+' blocked no stdout drain or invented cleanup snapshot',c['stdout_drained_after_fill'] is False and a['stdout_read_after_fill'] is False and a['stdout_unread_bytes']==4096 and a['backend_cleanup_observed'] is False and a['stopped_events']==[])
  case_records.append({'mode':c['mode'],'signal':c['signal'],'exit':a['returncode'],'seconds':a['signal_exit_seconds'],'backend_cleanup_observed':a['backend_cleanup_observed'],'source_assertions':len(c['assertions']),'executable_sha256':execbin['sha256']})
 jobs_report.append({'id':jid,'name':name,'url':job['html_url'],'os':version,'architecture':arch,'cargo_test_executions':totals[0],'cargo_result_records':len(counts),'test_breakdown':[108,176,20],'tls_test_executions':36,'source_count':len(files),'source_sha256':before['source_sha256'],'binary':binary,'signal_cases':case_records,'backpressure':backpressure,'summary_sha256':digest(raw.encode())})

pages_report={}
for job in alljobs['pages']:
 text=clean((B/f"job-{job['id']}.log").read_text(encoding='utf-8-sig'))
 if job['name']=='check':
  numbers=re.findall(r'PASS: (\d+) local asset/link/SVG checks;',text);check('Pages actual 119 checks',numbers==['119'],numbers)
  check('Pages JavaScript syntax step success',any(x['name']=='Check JavaScript syntax' and x['conclusion']=='success' for x in job['steps']))
  pages_report['site_checks']=119
 if job['name']=='deploy':
  check('Pages deployment exact commit logged',f'Created deployment for {HEAD}' in text)
  pages_report['artifact_sha256']=re.findall(r'SHA256 digest of uploaded artifact zip is ([0-9a-f]{64})',text)
 pages_report[job['name']]={'id':job['id'],'status':job['status'],'conclusion':job['conclusion'],'url':job['html_url']}
report={'result':'PASS' if all(c['passed'] for c in checks) else 'FAIL','assertions':len(checks),'passed':sum(c['passed'] for c in checks),'head':HEAD,'software_run':{k:runs['software'][k] for k in ['id','run_attempt','html_url','status','conclusion','head_sha','created_at','updated_at']},'pages_run':{k:runs['pages'][k] for k in ['id','run_attempt','html_url','status','conclusion','head_sha','created_at','updated_at']},'software_jobs':jobs_report,'pages':pages_report,'frozen_logic':source_logic,'checks':checks,'boundaries':['Software CI only: no QEMU/physical USB/driver recovery/secure USB end-to-end claim. Separate kernel-socketpair QEMU failure is not a failure of this exact software CI.','304 is test executions per matrix cell (108 default +176 all-features +20 vendor), not unique cases. Eighteen transport tests run twice per cell.','292 is the harness-selected frozen file set; it can include evidence source files and is not the number of compiled source files. Exact Git blob hashes were independently checked.','Raw job logs and run ZIPs are archived and cross-checked byte-for-byte. Individual signal artifact payloads and runner ELF binaries were not uploaded by this workflow; their receipts and hashes are observed in the printed summary, not independently downloaded bytes.','Blocked stdout cases prove wait0 and data EOF without draining the blocked pipe; they do not provide a stopped resource snapshot.','The release binary provenance is tied to exact check.sh build order, API step success and per-case /proc executable device/inode/hash receipts. No assertion that the standalone transport crate is linked into the CLI.','Pages status/check logs are verified for exact commit. Artifact byte contents and current public site were not fetched in this task.','No repository changes, workflow reruns, builds or QEMU were performed by reviewer. GitHub connector used for jobs/logs; read-only gh REST supplemented full run/job metadata and original ZIP logs because wrappers omit those fields/formats.']}
save('review.json',report)
manifest=[]
for p in sorted(B.rglob('*')):
 if p.is_file() and p.name!='manifest.json':manifest.append({'path':str(p.relative_to(B)),'length':p.stat().st_size,'sha256':digest(p.read_bytes())})
save('manifest.json',{'head':HEAD,'files':manifest})
print(json.dumps({'result':report['result'],'checks':len(checks),'passed':report['passed'],'source_count':len(shared_files or []),'jobs':len(jobs_report),'files':len(manifest),'failed':[c for c in checks if not c['passed']],'report_sha256':digest((B/'review.json').read_bytes())},ensure_ascii=False))
