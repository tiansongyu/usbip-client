#!/usr/bin/env python3
"""Offline official log review. Only subprocesses are read-only git object access.
No product/harness import, API, build, or guest execution. Never overwrites a run directory.
"""
from pathlib import Path
import argparse, hashlib, json, re, subprocess, traceback
BASE=Path('/tmp/rustusb-diagnostic-ci-d9ebe2e-root-20261024')
REPO=Path('/home/ubuntu/github/usbip-client')
RUN=40067501829
HEAD='d9ebe2eadf8490081035199c91d17bbba41bc57e'
IDS={120793716138:('22.04','aarch64'),120793716286:('22.04','x86_64'),120793716316:('24.04','aarch64'),120793716335:('24.04','x86_64')}
EXPECTED_LOG_SHA={120793716138:'277e4386f277bcf204e474dcc5b0f13bbdcedc628d93734044b1f5c5d63f7750',120793716286:'669f0267f69547e0fb45ca37c1cf513ad331dbb09805b42527dfd513149d4bf3',120793716316:'c171d6972072daeabfd07604a59ba5e40498c42208e8b0456266d26817a155ce',120793716335:'7a38d643764642dab20b5e50bc247f85755b6f3eb986ee60ed56700fb88582c7'}

def sha(b):return hashlib.sha256(b).hexdigest()
def meta(p):
 b=p.read_bytes();return {'bytes':len(b),'sha256':sha(b)}
def write(p,v):p.write_text(json.dumps(v,indent=2,ensure_ascii=False)+'\n')
def git(*args):return subprocess.check_output(['git','-C',str(REPO),*args])
def documents(lines):
 text='\n'.join(lines);offset=previous=0;result=[];decoder=json.JSONDecoder()
 for line in lines:
  pos=offset+len(line)-len(line.lstrip());offset+=len(line)+1
  if pos<previous or not line.lstrip().startswith('{'):continue
  try:obj,end=decoder.raw_decode(text,pos)
  except ValueError:continue
  if isinstance(obj,dict):result.append(obj);previous=end
 return result

def main(out):
 out.mkdir(parents=True,exist_ok=False)
 before={p.name:meta(p) for p in sorted(BASE.iterdir()) if p.is_file()};write(out/'originals-before.json',before)
 additional_roots=[Path('/tmp/rustusb-diagnostic-software-second-20261024')]
 additional_before={str(p):meta(p) for root in additional_roots for p in sorted(root.rglob('*')) if p.is_file()};write(out/'additional-originals-before.json',additional_before)
 checks=[]
 def ck(name,condition,facts=None):
  checks.append({'predicate':name,'pass':bool(condition),**({'facts':facts}if facts is not None else {})})
 report={'schema_version':1,'run_id':RUN,'review_result':'FAIL','parser_revision':1,'based_on_preserved_method':'dff36a3 final-revision2; new exact d9 metadata and diagnostic-group validation','qualification_method':'Ordered Cargo stderr executable labels paired with ordered libtest stdout running/result blocks; actual log cross-stream interleaving retained. Previous dff method context-reset failure remains in its original directory; no such result is invented for this run.','scope':'Offline independent-method cross-review of official CI responses. Reviewer authored logging storage and a separate QEMU verifier; not independent implementation review. Parsing does not execute API, product, Cargo or QEMU. Official connector acquisition is a separately preserved step. Read-only Git blob access maps the exact committed inputs; no Git mutation.','checks':checks}
 try:
  head=HEAD;report['head']=head;ck('exact committed object exists',git('rev-parse',head+'^{commit}').decode().strip()==head)
  jobs=json.loads((BASE/'jobs.response.json').read_text())['structuredContent']['jobs'];ck('exact four official terminal success jobs',len(jobs)==4 and {j['id']for j in jobs}==set(IDS) and all(j['status']=='completed'and j['conclusion']=='success'for j in jobs))
  run=json.loads(json.loads((BASE/'final-run.response.json').read_text())['structuredContent']['content']);ck('exact final push run head and success',run['id']==RUN and run['head_sha']==head and run['event']=='push' and run['status']=='completed' and run['conclusion']=='success');report['run_metadata']=run
  initial=json.loads(json.loads((BASE/'initial-runs.response.json').read_text())['structuredContent']['content'])['workflow_runs'];pages=next(x for x in initial if x['id']==40067501862);initial_run=next(x for x in initial if x['id']==RUN)
  ck('initial progress state preserved separately and Pages status only',initial_run['head_sha']==HEAD and initial_run['status']=='in_progress' and initial_run['conclusion']is None and pages['head_sha']==HEAD and pages['status']=='completed' and pages['conclusion']=='success');report['initial_run_metadata']=initial_run;report['pages_metadata']=pages
  source_ref=None;logging_ref=None;diagnostic_ref=None;job_reports=[]
  for job in jobs:
   ident=job['id'];tag=str(ident);version,arch=IDS[ident];p=BASE/f'{ident}.log';log_bytes=p.read_bytes();raw=log_bytes.decode('utf-8');receipt={'path':str(p),'bytes':len(log_bytes),'sha256':sha(log_bytes),'acquisition':'Root official connector structuredContent.content exact UTF8 chunks; expected SHA delivered before review'};ck(tag+' exact Root-acquired official UTF8 SHA without newline normalization',receipt['sha256']==EXPECTED_LOG_SHA[ident] and raw.encode('utf-8')==log_bytes);report.setdefault('log_receipts',[]).append(receipt)
   lines=[re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',re.sub(r'^\ufeff?\d{4}-\d{2}-\d{2}T[^ ]+Z ','',l))for l in raw.splitlines()];docs=documents(lines)
   def one(prefix):
    ds=[d for d in docs if d.get('scope','').startswith(prefix)];ck(tag+' one summary '+prefix,len(ds)==1);return ds[0]
   lg=one('actual logging subprocesses');sg=one('ordinary-user loopback');bp=one('local CLI output backpressure');tls=one('five real mTLS');dg=one('actual ordinary-UID private diagnostic subprocesses')
   write(out/f'job-{ident}-logging.json',lg);write(out/f'job-{ident}-signals.json',sg);write(out/f'job-{ident}-diagnostics.json',dg)
   rows=[]
   for line in lines:
    m=re.match(r'test result: (\S+)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;',line)
    if m:rows.append(dict(zip(['result','passed','failed','ignored','measured','filtered'],[m[1],*map(int,m.groups()[1:])])) )
   totals={'default':sum(r['passed']for r in rows[:23]),'all_features':sum(r['passed']for r in rows[23:46]),'vendor':sum(r['passed']for r in rows[46:])}
   names=[l for l in lines if re.fullmatch(r'test .+ \.\.\. ok',l)]
   # Cargo emits labels on stderr and libtest emits blocks on stdout. The
   # official merged log can place the next stderr label before the preceding
   # stdout result. Preserve order within each stream; do not clear/overwrite
   # a pending label based on cross-stream interleaving.
   contexts=[{'line':i+1,'context':line.strip()} for i,line in enumerate(lines) if line.strip().startswith(('Running ','Doc-tests '))]
   starts=[{'line':i+1,'count':int(m[1])} for i,line in enumerate(lines) if (m:=re.fullmatch(r'running (\d+) tests?',line.strip()))]
   qualified=[];executed=[]
   for line_number,line in enumerate(lines,1):
    clean=line.strip();named=re.fullmatch(r'test (.+) \.\.\. ok',clean)
    if named:executed.append(named[1])
    if clean.startswith('test result: '):
     index=len(qualified)
     qualified.append({'context':contexts[index]['context'] if index<len(contexts) else None,'context_line':contexts[index]['line'] if index<len(contexts) else None,'start':starts[index] if index<len(starts) else None,'result_line':line_number,'section':'default' if index<23 else 'all-features' if index<46 else 'vendor','executed_names':executed});executed=[]
   ck(tag+' exact qualified execution names for each libtest result',len(contexts)==len(starts)==len(qualified)==len(rows) and all(q['context'] and q['start']['count']==len(q['executed_names'])==r['passed'] for q,r in zip(qualified,rows)))
   write(out/f'job-{ident}-test-rows.json',{'rows':rows,'qualified':qualified,'executed_test_lines':names})
   ck(tag+' actual 797 executions and47 result rows, zero failure ignored filtered',len(rows)==47 and totals=={'default':343,'all_features':434,'vendor':20}and len(names)==797 and all(r['result']=='ok'and all(r[k]==0 for k in ['failed','ignored','measured','filtered'])for r in rows))
   ck(tag+' all substantive official steps success and no Actions error',all(s['conclusion']in ['success','skipped']for s in job['steps'])and not any(l.startswith('##[error]')for l in lines))
   ck(tag+' actual native uname/os-release/compiler host',f'VERSION_ID="{version}"'in lines and f'host: {arch}-unknown-linux-gnu'in lines and any(l.startswith('Linux ')and arch in l for l in lines)and lg['host'][0]=='Linux'and lg['host'][-1]==arch and version in job['name'])
   ck(tag+' actual ordinary uid1001',lg['uid']==sg['host_uid']==bp['host_uid']==1001)
   ck(tag+' legacy2 backpressure',bp['result']=='PASS'and [(x['mode'],x['exit'])for x in bp['checks']]==[('sigint',0),('deadline',1)]and all(x['stdout_capacity']==4096 and not x['stdout_drained_after_fill']and x['lease_socket_closed']for x in bp['checks']))
   ck(tag+' actual four signal cases',sg['result']=='PASS'and {(c['mode'],c['signal'])for c in sg['cases']}=={(m,s)for m in ['ready','blocked']for s in ['sigint','sigterm']}and all(c['result']=='PASS'and c['final_returncode']==0 and not c['forced_cleanup']and c['after']['wait_error']is None and all(v['eof']and not v['unexpected_hex']for v in c['after']['data_sockets'].values())and c['before']['snapshot']['devices']and all(d['leased']and d['pending_urbs']==1 and d['configuration']==1 for d in c['before']['snapshot']['devices'])for c in sg['cases']))
   ck(tag+' five actual TLS adapter cases are already inside libtest total',tls['result']=='PASS'and len(tls['cases'])==5 and all(c['result']=='PASS'and c['mutual_tls']for c in tls['cases']))
   ck(tag+' signal inventory and ELF unchanged at exact clean head',sg['source_unchanged']and sg['source_before']==sg['source_after']and sg['source_before']['head']==head and not sg['source_before']['dirty']and not sg['source_before']['git_status']and sg['binary_unchanged']and sg['binary_before']['sha256']==sg['binary_after_sha256'])
   ck(tag+' logging inventory and ELF unchanged exact clean head',lg['source_unchanged']and lg['sources_before']==lg['sources_after']and lg['sources_before']['head']==head and not lg['sources_before']['git_status']and lg['binary_unchanged']and lg['binary_before']==lg['binary_after']and lg['binary_before']['sha256']==sg['binary_before']['sha256'])
   if source_ref is None:source_ref=sg['source_before']['files'];logging_ref=lg['sources_before']['files']
   ck(tag+' inventories byte/hash identities equal all platforms',source_ref==sg['source_before']['files']and logging_ref==lg['sources_before']['files'])
   cases={c['name']:c for c in lg['cases']};expected=['startup-refusals','rotation-restart-cursors','follow-sigint','follow-sigterm','observer-output-backpressure','runtime-file-size-io-failure']
   ck(tag+' six logging actual cases complete with no forced/error collection',list(cases)==expected and lg['result']=='PASS'and lg['completed']and not lg.get('error')and not lg['collection_errors']and not lg['forced_cleanup']and all(c['result']=='PASS'and c['started_monotonic_ns']<c['completed_monotonic_ns']for c in lg['cases']))
   children=lg['children'];actual=[c for c in children if 'executed_binary'in c];fast=[c for c in children if 'fast_exit_proc_scope'in c];ck(tag+'38children=11actualproc+27fast explicitly separated',len(children)==38 and len(actual)==11 and len(fast)==27 and all(('executed_binary'in c)!=('fast_exit_proc_scope'in c)for c in children))
   ck(tag+' each actual child waited and UID/generation noforced',len({c['name']for c in children})==38 and all(c['pid']==c['identity']['pid']and c['identity']['uids']==[lg['uid']]*4 and c['identity']['starttime']>0 and c['started_monotonic_ns']<c['waited_monotonic_ns']and isinstance(c['returncode'],int)and not c['forced_cleanup']for c in children))
   ck(tag+'11actualproc inode and executable bytes match selected release',all(c['executed_binary']['sha256']==lg['binary_before']['sha256']and c['executed_binary']['device']==lg['binary_before']['device']and c['executed_binary']['inode']==lg['binary_before']['inode']and c['executed_binary']['resolved']==lg['binary_before']['path']for c in actual))
   ck(tag+' actual signals target retained process generation',all(s['identity']==c['identity']and s['pid']==c['pid']and c['started_monotonic_ns']<s['monotonic_ns']<c['waited_monotonic_ns']for c in children for s in c.get('signals',[])))
   names_by_child={c['name']:c for c in children};startup=cases['startup-refusals']['facts'];ck(tag+' seven startup refusals exact exit classes',startup['cases']==7 and [names_by_child[n]['returncode']for n in startup['child_waits']]==[2,2,2,2,1,1,1])
   rotate=cases['rotation-restart-cursors']['facts'];ck(tag+' rotation pagination matches raw and4distinct epochs retained2',rotate['actual_reader_matches_raw_records']and len(rotate['epochs'])==len(set(rotate['epochs']))==4 and rotate['retained_segments']==2 and ':'in rotate['expired_cursor'])
   ck(tag+' follow INT TERM only observer exits owner unchanged',all(cases['follow-'+sig]['facts']=={'observer_exit':0,'owner_unchanged_after_observer':True,'signal':num}for sig,num in [('sigint',2),('sigterm',15)]))
   bpf=cases['observer-output-backpressure']['facts'];ck(tag+' actual4096 blocked output no drain deadlineexit1',bpf['capacity']==bpf['synthetic_padding_bytes']==4096 and bpf['stdout_read_after_fill']is False and bpf['exit']==1 and 4.5<=bpf['elapsed_seconds']<=10 and bpf['observed_wait']['wchan']in ['pipe_write','anon_pipe_write']and bpf['observed_wait']['tid']>0)
   fs=cases['runtime-file-size-io-failure']['facts'];fchild=names_by_child['fsize-owner'];ck(tag+' actual file-size failure partial16 errno27 preserved originals',fs['actual_partial_log_bytes']==16 and fs['errno']==27 and fs['exit']==fchild['returncode']==1 and fs['log_originals_preserved']and fchild['ignore_sigxfsz_exec_wrapper']and fchild['identity']==fchild['pre_exec_identity'])
   stopped=fs['actual_virtual_stopped'];ck(tag+' actual stopped full virtualcleanup after loggingfault',stopped['event']=='stopped'and {d['profile']for d in stopped['snapshot']['devices']}=={'hid_keyboard','cdc_acm'}and all(not d['leased']and d['pending_urbs']==d['configuration']==d['hid_queued_reports']==d['cdc_queued_bytes']==0 and d['counters']['sessions_started']==1 for d in stopped['snapshot']['devices']))
   artifact={a['path']:a for a in lg['artifacts']};required=['fsize-owner/rlimit-fault.json','fsize-owner/held.json','fsize-owner/stopped.json','fsize-owner/protocol.json','fsize-owner/proc-status.txt','fsize-owner/stderr.bin','fsize-failed-originals/segment-00.ndjson','final-store-fsize-failure/segment-00.ndjson','bp-reader/blocked-writer-observations.json']
   ck(tag+' receipt inventory includes fault actual files and preserves final partial',len(artifact)==len(lg['artifacts'])and all(n in artifact and artifact[n]['bytes']>0 for n in required)and artifact[required[-3]]['sha256']==artifact[required[-2]]['sha256'])
   ck(tag+' private cache parents actual secured',all(x['uid']==lg['uid']and int(x['mode'],8)&0o022==0 for x in lg['parent_metadata'])and lg['parent_metadata'][-2]['mode']==lg['parent_metadata'][-1]['mode']=='0o700')
   dc={c['name']:c for c in dg['cases']};dexpected=['refusals','local','logs','write-failure','stderr-success','sigint','sigterm','publication-race','backpressure']
   ck(tag+' exactly9 actual diagnostic groups with no default-only group invented',list(dc)==dexpected and dg['result']=='PASS' and not dg['collection_errors'] and not dg['forced_cleanup'] and all(c['result']=='PASS' and c['started_monotonic_ns']<c['completed_monotonic_ns']for c in dg['cases']))
   ck(tag+' diagnostics actual UID/native platform/source and selected ELF stable',dg['uid']==1001 and dg['host'][-1]==arch and dg['source_unchanged'] and dg['sources_before']==dg['sources_after'] and dg['sources_before']['head']==head and not dg['sources_before']['git_status'] and dg['binary_unchanged'] and dg['binary_before']==dg['binary_after'] and dg['binary_before']['sha256']==lg['binary_before']['sha256'])
   if diagnostic_ref is None:diagnostic_ref=dg['sources_before']['files']
   ck(tag+' diagnostic source inventory matches across native jobs',diagnostic_ref==dg['sources_before']['files'])
   children_d=dg['children'];proc_d=[c for c in children_d if 'executed_binary'in c];fast_d=[c for c in children_d if 'fast_exit_proc_scope'in c];pre_d=[c for c in children_d if 'pre_exec_identity'in c]
   ck(tag+' actual13 diagnosticchildren=4proc+6identityonly+3preexec',len(children_d)==13 and len(proc_d)==4 and len(fast_d)==6 and len(pre_d)==3 and len({c['name']for c in children_d})==13 and all(sum(k in c for k in ('executed_binary','fast_exit_proc_scope','pre_exec_identity'))==1 for c in children_d))
   ck(tag+' each diagnostic child actual waited and known ordinary generation',all(not c['forced_cleanup'] and c['started_monotonic_ns']<c['waited_monotonic_ns'] and isinstance(c['returncode'],int) and (c.get('identity')or c.get('pre_exec_identity'))['pid']==c['pid'] and (c.get('identity')or c.get('pre_exec_identity'))['uids']==[1001]*4 and (c.get('identity')or c.get('pre_exec_identity'))['starttime']>0 for c in children_d))
   ck(tag+' only4 diagnosticproc receipts prove selected executable inode/bytes',all(all(c['executed_binary'][k]==dg['binary_before'][k]for k in ('device','inode','sha256')) and c['executed_binary']['resolved']==dg['binary_before']['path']for c in proc_d))
   by_d={c['name']:c for c in children_d};expected_exits={'existing-destination':1,'symlink-parent':1,'local-capture':0,'create-real-local-log':0,'local-log-capture':0,'corrupt-log-capture':1,'write-limit-16':1,'successful-discovery-console-human':1,'successful-discovery-console-json':1,'prepare-sigint':1,'prepare-sigterm':1,'accepted-publication-signal':0,'blocked-receipt':1}
   ck(tag+' exact13 diagnostic actual exit outcomes',set(by_d)==set(expected_exits) and all(by_d[k]['returncode']==v for k,v in expected_exits.items()))
   def private_file(v):return v['metadata']['mode']=='0o600' and v['metadata']['uid']==1001 and v['metadata']['nlink']==1 and 0<v['metadata']['bytes']<=4194304
   def receipt_matches(v,receipt):return receipt['file']['result']=='written' and receipt['file']['receipt']['sha256']==v['sha256'] and receipt['file']['receipt']['written_bytes']==v['metadata']['bytes']
   refusal=dc['refusals']['facts'];ck(tag+' destination and symlink refusals do not claim publication',refusal['existing']['file']['failure']['kind']=='already_exists' and refusal['existing']['file']['failure']['publication']=='not_created' and refusal['symlink']['file']['failure']['publication']=='not_created' and refusal['symlink']['file']['failure']['kind']=='io' and refusal['original']['bytes']>0)
   local=dc['local']['facts'];doc=local['document'];ck(tag+' actual local document exported privately complete with truthful absent agent/helper',private_file(local) and receipt_matches(local,local['receipt']) and doc['capture']=='complete' and doc['summary']['complete'] and doc['build']['secure_agent_collection']is True and doc['native']['architecture']==arch and doc['requested_agent_scope']is None and doc['agent']is None and doc['health']=={'pages':0,'samples':[]} and doc['helper']['status']=='missing' and doc['helper']['active_sessions']is None and not doc['helper']['peer_verified'] and not doc['privacy']['hardware_test_performed'] and not doc['privacy']['network_security_verified'])
   logfacts=dc['logs']['facts'];valid=logfacts['valid_file'];corrupt=logfacts['corrupt_file'];records=valid['document']['logs']['records'];ck(tag+' actual selected safe local logs complete and corrupt log explicitly partial',private_file(valid) and private_file(corrupt) and receipt_matches(valid,logfacts['valid_receipt']) and receipt_matches(corrupt,logfacts['corrupt_receipt']) and valid['document']['capture']=='complete' and [q['event']['kind']for q in records]==['started','stopped'] and [q['sequence']for q in records]==[1,2] and all(q['process_epoch']==1 for q in records) and valid['document']['alias_counts']['process_epochs']==valid['document']['alias_counts']['uids']==1 and corrupt['document']['capture']=='partial' and corrupt['document']['logs']['records']==[] and any(s['stage']=='logs' and s['failure']=='corrupt' and s['outcome']=='incomplete'for s in corrupt['document']['summary']['sections']))
   wf=dc['write-failure']['facts'];failure=wf['receipt']['file']['failure'];ck(tag+' diagnostic actual partial16 errno27 is file failure not failed capture',wf['receipt']['capture']=='complete' and wf['receipt']['file']['result']=='failed' and failure['errno']==27 and failure['publication']=='partial' and failure['written_bytes']==wf['partial']['bytes']==16 and failure['sha256']==wf['sha256'] and wf['partial']['mode']=='0o600' and by_d['write-limit-16']['wrapper']=='rlimit_fsize_16_sigxfsz_ignored')
   stderr=dc['stderr-success']['facts'];ck(tag+' human and JSON successful DEVLIST still actual exit1 on stderr EFBIG',len(stderr['cases'])==2 and {x['json_console']for x in stderr['cases']}=={False,True} and all(x['actual_operation']=='successful_empty_DEVLIST' and x['exit_code']==1 and x['stderr']['bytes']==16 for x in stderr['cases']) and all(by_d[name]['wrapper']=='rlimit_fsize_16_sigxfsz_ignored'for name in ('successful-discovery-console-human','successful-discovery-console-json')))
   for label,child,number,publish in [('sigint','prepare-sigint',2,False),('sigterm','prepare-sigterm',15,False),('publication-race','accepted-publication-signal',15,True)]:
    df=dc[label]['facts'];held=df['held'];outf=df['file'];rr=df['receipt'];ch=by_d[child];expected_syscall=(74 if publish else 91)if arch=='x86_64'else(82 if publish else 52);expected_arch=0xc000003e if arch=='x86_64'else 0xc00000b7
    ck(tag+' '+label+' same real inode entry gate and successful actual syscall exit',held['syscall']==expected_syscall and held['syscall_info_arch']==expected_arch and df['actual_syscall_return']==df['actual_syscall_is_error']==0 and df['signal']==number and all(held['file'][k]==held['namespace'][k]==outf['metadata'][k]for k in ('device','inode','uid','mode','nlink')) and held['file']['descriptor']==f"/proc/{held['tid']}/fd/{held['fd']}" and held['tid']>0 and ch['wait_method']=='actual ptrace waitpid(__WALL) reaped original child' and ch['actual_waitpid_status']==ch['returncode']<<8 and 'before kernel execution'in df['boundary'])
    ck(tag+' '+label+' actual final receipt retains cancellation/publication distinction',private_file(outf) and receipt_matches(outf,rr) and rr['cancel_observed'] and rr['capture']==('complete'if publish else'cancelled') and rr['file']['receipt']['publication']==('published'if publish else'partial') and outf['document']['capture']==rr['capture'])
   dbp=dc['backpressure']['facts'];ck(tag+' diagnostic4096 no drain untilwait with private complete publication',dbp['pipe_capacity']==4096 and dbp['no_drain_until_actual_wait'] and 4<=dbp['elapsed_seconds']<=10 and dbp['capture']=='complete' and dbp['file']['mode']=='0o600' and dbp['file']['nlink']==1 and dbp['file']['uid']==1001 and dbp['file']['bytes']>0 and len(dbp['sha256'])==64)
   job_reports.append({'id':ident,'name':job['name'],'actual_native_host':lg['host'],'uid':lg['uid'],'software':totals,'total':sum(totals.values()),'result_rows':47,'qualified_test_lines':len(names),'signals':4,'legacy_backpressure':2,'tls_adapter_cases_already_in_tests':5,'logging_cases':6,'logging_children':{'total':38,'actual_proc':len(actual),'fast_argv_only':len(fast)},'logging_wchan':bpf['observed_wait'],'logging_fsize_facts':fs,'logged_ELF_receipt_not_local_binary':lg['binary_before'],'diagnostics':{'groups':9,'children':13,'proc_ELF_receipts':len(proc_d),'identity_only':len(fast_d),'preexec_only':len(pre_d),'source_inventory':len(dg['sources_before']['files']),'actual_exit_by_child':{k:c['returncode']for k,c in by_d.items()},'facts':{k:v['facts']for k,v in dc.items()}},'logging_source_inventory':len(lg['sources_before']['files']),'signal_inventory_including_archived_sources':len(sg['source_before']['files']),'runtime_artifact_hash_inventory_only':len(artifact),'raw_log':{**meta(p),'utf8_bom':log_bytes.startswith(b'\xef\xbb\xbf'),'linefeed_count':log_bytes.count(b'\n'),'crlf_count':log_bytes.count(b'\r\n')},'steps':[{'name':s['name'],'conclusion':s['conclusion']}for s in job['steps']]})
  allfiles={}
  for entry in [*source_ref,*logging_ref,*diagnostic_ref]:
   value={'bytes':entry.get('bytes',entry.get('length')),'sha256':entry['sha256']};name=entry['path']
   if name in allfiles:ck('overlapping inventories agree '+name,allfiles[name]==value)
   allfiles[name]=value
  tree={}
  for item in git('ls-tree','-rz',head).split(b'\0'):
   if not item:continue
   header,path=item.split(b'\t',1);mode,kind,oid=header.decode().split();tree[path.decode()]=(kind,oid)
  mapped=[];blobs={};objects=sorted({tree[n][1]for n in allfiles if n in tree and tree[n][0]=='blob'})
  # One read-only Git batch; no working tree checkout or file mutation.
  batch=subprocess.Popen(['git','-C',str(REPO),'cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
  for oid in objects:
   batch.stdin.write((oid+'\n').encode());batch.stdin.flush();hdr=batch.stdout.readline().decode().split();size=int(hdr[2]);body=batch.stdout.read(size);end=batch.stdout.read(1)
   if hdr[:2]!=[oid,'blob']or len(body)!=size or end!=b'\n':raise RuntimeError('invalid git batch blob')
   blobs[oid]={'bytes':size,'sha256':sha(body)}
  batch.stdin.close();ck('Git blob reader completed',batch.wait()==0)
  for name,want in allfiles.items():
   kind,oid=tree.get(name,(None,None));got=blobs.get(oid);mapped.append({'path':name,'oid':oid,'expected':want,'actual':got,'match':got==want})
  ck('all unique source inventory entries exact Gitd9ebe2e bytes',all(x['match']for x in mapped),{'unique_paths':len(mapped),'unique_blobs':len(blobs),'mismatches':[x for x in mapped if not x['match']]});write(out/'git-source-map.json',mapped)
  full=Path('/tmp/rustusb-diagnostic-software-second-20261024');full_before=json.loads((full/'inputs-before.json').read_text());full_after=json.loads((full/'inputs-after.json').read_text());ck('Root first full gate actual192 source stable',len(full_before)==192 and full_before==full_after)
  selected=[]
  for row in full_before:
   body=git('show',head+':'+row['path']);actual={'bytes':len(body),'sha256':sha(body)};selected.append({'path':row['path'],'expected':{'bytes':row['bytes'],'sha256':row['sha256']},'git_commit':head,'actual':actual,'matches':actual=={'bytes':row['bytes'],'sha256':row['sha256']}})
  ck('Root dirty2ca5f5b full-gate192 actual bytes map exactly to committed d9ebe2e',all(row['matches'] for row in selected));write(out/'full-gate192-map.json',selected);report['full_gate_scope']={'actual_start':json.loads((full/'start.json').read_text()),'source_count':192,'map':'full-gate192-map.json','not_clean_relabelled':True}
  harness=git('show',head+':scripts/test-process-logging.py').decode();(out/'reviewed-test-process-logging.py').write_text(harness)
  stop=harness[harness.index('    def stop(self, expected=0):'):harness.index('\ndef private_store')];fault=harness[harness.index('def runtime_file_failure('):harness.index('\ndef main():')]
  ck('fixed harness source waits process then validates stopped then samples data EOF',stop.index('self.run.wait(')<stop.index('all_released(')<stop.index('stream.recv(1)')and 'data == b""'in stop)
  ck('fixed source actual runtime fault targets acquired owner with SIGXFSZ ignore and matched FD',fault.index('Virtual(run, "fsize-owner"')<fault.index('resource.prlimit(')and 'signal.SIGXFSZ - 1'in fault and 'actual.st_dev, actual.st_ino'in fault and 'before["bytes"] + 16'in fault and 'held-after-log-io-failure'in fault and 'os error 27'in fault and 'forged successful drained shutdown'in fault)
  report.update({'jobs':job_reports,'source_map':{'path':'git-source-map.json','unique_paths':len(mapped),'unique_git_blobs':len(blobs)},'evidence_layers':{'official_direct':['terminal job metadata','797 qualified test execution lines and47 libtest rows per job','six case outcomes and fsize stopped device snapshot','38 logging child receipts per job,11 with /proc-derived executable metadata','9 diagnostic groups /13 children:4 full proc ELF,6 process identity only,3 preexec only; actual document projections and file/stat/hash receipts','source and artifact hash inventories; actual UID/uname/wchan'], 'fixed_source_contract':['Runtime RLIMIT_FSIZE only after ready/import/configuration/pending virtual INs','SIGXFSZ ignored, inode/FD matched, limit=active_size+16, partial preserved','same actual owner cleanup before process exit1 is checked via stopped event; data socket EOF sampled after process wait'], 'not_present':['Ephemeral runner /tmp artifact bodies are NOT uploaded here: protocol.json, rlimit-fault.json, partial NDJSON/proc bodies and stderr are hash receipts only','No locally available CI ELF bytes or reconstructed runtime originals','Diagnostic private JSON/partial files, protocol bytes, ptrace trace and child stdout/stderr bodies are not uploaded; their summary projections and hash/stat/exit receipts are observed','No timestamp proving data EOF happened before process exit; source explicitly samples EOF after wait']},'limits':['Diagnostic predicate counts are not new USB cases. TLS five adapter cases are nested in existing Rust test totals, not added to797.','Signal inventory includes archived evidence source files, not compilation inputs. Logging inventory is its actual selected source/runner set, distinct from the192 full-gate software-source count.','The9 diagnostic groups are actual CI executions; the separate default-only4 local groups were NOT executed in this CI and are not inferred. Earlier dff793 and its parser/API failures keep their original evidence. No QEMU acceptance follows from this CI.','No host/root-helper/physical USB/kernel mutation, hardware result, QEMU result, permanent filesystem stall cancellation or fullPhase3 completion follows from these ordinary-user loopback cases.','All original official log bytes and receipts preserved; Root acquired through the official connector. This parser makes no API call or CI rerun.']})
 except Exception as exc:
  report['parser_error']={'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()}
 after={p.name:meta(p)for p in sorted(BASE.iterdir())if p.is_file()};write(out/'originals-after.json',after);ck('all official originals unchanged during review',before==after)
 additional_after={str(p):meta(p) for root in additional_roots for p in sorted(root.rglob('*')) if p.is_file()};write(out/'additional-originals-after.json',additional_after);ck('full-gate and prior initial official originals unchanged',additional_before==additional_after)
 report['review_result']='PASS'if not report.get('parser_error')and all(c['pass']for c in checks)else'FAIL';report['predicates']=len(checks);write(out/'report.json',report)
 print(json.dumps({'review_result':report['review_result'],'predicates':len(checks),'failed':[c for c in checks if not c['pass']],'parser_error':report.get('parser_error'),'report':str(out/'report.json'),'sha256':meta(out/'report.json')['sha256']},indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args();main(a.output)
