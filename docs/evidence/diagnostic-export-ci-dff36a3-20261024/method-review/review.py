#!/usr/bin/env python3
"""Offline official log review. Only subprocesses are read-only git object access.
No product/harness import, API, build, or guest execution. Never overwrites a run directory.
"""
from pathlib import Path
import argparse, hashlib, json, re, subprocess, traceback
BASE=Path('/tmp/rustusb-diagnostic-ci-dff36a3-20261024')
REPO=Path('/home/ubuntu/github/usbip-client')
RUN=40061061065
HEAD='dff36a32f352ec37b778af6690e7939edf655dc7'
IDS={120774457784:('22.04','x86_64'),120774457960:('24.04','x86_64'),120774457965:('22.04','aarch64'),120774457956:('24.04','aarch64')}
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
 checks=[]
 def ck(name,condition,facts=None):
  checks.append({'predicate':name,'pass':bool(condition),**({'facts':facts}if facts is not None else {})})
 report={'schema_version':1,'run_id':RUN,'review_result':'FAIL','scope':'Offline independent-method cross-review of official CI responses. Reviewer authored logging storage and a separate QEMU verifier; not independent implementation review. Parsing does not execute API, product, Cargo or QEMU. Official connector acquisition is a separately preserved step. Read-only Git blob access maps the exact committed inputs; no Git mutation.','checks':checks}
 try:
  head=HEAD;report['head']=head;ck('exact committed object exists',git('rev-parse',head+'^{commit}').decode().strip()==head)
  jobs=json.loads((BASE/'jobs.response.json').read_text())['structuredContent']['jobs'];ck('exact four official terminal success jobs',len(jobs)==4 and {j['id']for j in jobs}==set(IDS) and all(j['status']=='completed'and j['conclusion']=='success'for j in jobs))
  run=json.loads(json.loads((BASE/'run.response.json').read_text())['structuredContent']['content']);ck('exact final push run head and success',run['id']==RUN and run['head_sha']==head and run['event']=='push' and run['status']=='completed' and run['conclusion']=='success');report['run_metadata']=run
  pages=json.loads(json.loads((BASE/'pages.response.json').read_text())['structuredContent']['content']);ck('Pages status separately observed for prior content',pages['id']==40061061057 and pages['status']=='completed' and pages['conclusion']=='success');report['pages_metadata']=pages
  source_ref=None;logging_ref=None;job_reports=[]
  for job in jobs:
   ident=job['id'];tag=str(ident);version,arch=IDS[ident];p=BASE/f'job-{ident}.response.json';response=json.loads(p.read_text());raw=response['structuredContent']['content'];log_bytes=(BASE/f'job-{ident}.log').read_bytes();ck(tag+' exact structuredContent UTF8 bytes without line normalization',log_bytes==raw.encode('utf-8'));report.setdefault('log_receipts',[]).append({'id':ident,'bytes':len(log_bytes),'sha256':sha(log_bytes)})
   lines=[re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',re.sub(r'^\ufeff?\d{4}-\d{2}-\d{2}T[^ ]+Z ','',l))for l in raw.splitlines()];docs=documents(lines)
   def one(prefix):
    ds=[d for d in docs if d.get('scope','').startswith(prefix)];ck(tag+' one summary '+prefix,len(ds)==1);return ds[0]
   lg=one('actual logging subprocesses');sg=one('ordinary-user loopback');bp=one('local CLI output backpressure');tls=one('five real mTLS')
   write(out/f'job-{ident}-logging.json',lg);write(out/f'job-{ident}-signals.json',sg)
   rows=[]
   for line in lines:
    m=re.match(r'test result: (\S+)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;',line)
    if m:rows.append(dict(zip(['result','passed','failed','ignored','measured','filtered'],[m[1],*map(int,m.groups()[1:])])) )
   totals={'default':sum(r['passed']for r in rows[:23]),'all_features':sum(r['passed']for r in rows[23:46]),'vendor':sum(r['passed']for r in rows[46:])}
   names=[l for l in lines if re.fullmatch(r'test .+ \.\.\. ok',l)];write(out/f'job-{ident}-test-rows.json',{'rows':rows,'executed_test_lines':names})
   ck(tag+' actual 793 executions and47 result rows, zero failure ignored filtered',len(rows)==47 and totals=={'default':341,'all_features':432,'vendor':20}and len(names)==793 and all(r['result']=='ok'and all(r[k]==0 for k in ['failed','ignored','measured','filtered'])for r in rows))
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
   job_reports.append({'id':ident,'name':job['name'],'actual_native_host':lg['host'],'uid':lg['uid'],'software':totals,'total':sum(totals.values()),'result_rows':47,'qualified_test_lines':len(names),'signals':4,'legacy_backpressure':2,'tls_adapter_cases_already_in_tests':5,'logging_cases':6,'logging_children':{'total':38,'actual_proc':len(actual),'fast_argv_only':len(fast)},'logging_wchan':bpf['observed_wait'],'logging_fsize_facts':fs,'logged_ELF_receipt_not_local_binary':lg['binary_before'],'logging_source_inventory':len(lg['sources_before']['files']),'signal_inventory_including_archived_sources':len(sg['source_before']['files']),'runtime_artifact_hash_inventory_only':len(artifact),'response':meta(p),'steps':[{'name':s['name'],'conclusion':s['conclusion']}for s in job['steps']]})
  allfiles={}
  for entry in [*source_ref,*logging_ref]:
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
  ck('all unique source inventory entries exact Gitdff36a3 bytes',all(x['match']for x in mapped),{'unique_paths':len(mapped),'unique_blobs':len(blobs),'mismatches':[x for x in mapped if not x['match']]});write(out/'git-source-map.json',mapped)
  full=Path('/tmp/rustusb-diagnostic-software-first-20261024');full_before=json.loads((full/'inputs-before.json').read_text());full_after=json.loads((full/'inputs-after.json').read_text());ck('Root first full gate actual191 source stable',len(full_before)==191 and full_before==full_after)
  selected=[]
  for row in full_before:
   body=git('show',head+':'+row['path']);actual={'bytes':len(body),'sha256':sha(body)};selected.append({'path':row['path'],'expected':{'bytes':row['bytes'],'sha256':row['sha256']},'git_commit':head,'actual':actual,'matches':actual=={'bytes':row['bytes'],'sha256':row['sha256']}})
  ck('Root dirty88179 full-gate191 actual bytes map exactly to committed dff36a3',all(row['matches'] for row in selected));write(out/'full-gate191-map.json',selected);report['full_gate_scope']={'actual_start':json.loads((full/'start.json').read_text()),'source_count':191,'map':'full-gate191-map.json','not_clean_relabelled':True}
  harness=git('show',head+':scripts/test-process-logging.py').decode();(out/'reviewed-test-process-logging.py').write_text(harness)
  stop=harness[harness.index('    def stop(self, expected=0):'):harness.index('\ndef private_store')];fault=harness[harness.index('def runtime_file_failure('):harness.index('\ndef main():')]
  ck('fixed harness source waits process then validates stopped then samples data EOF',stop.index('self.run.wait(')<stop.index('all_released(')<stop.index('stream.recv(1)')and 'data == b""'in stop)
  ck('fixed source actual runtime fault targets acquired owner with SIGXFSZ ignore and matched FD',fault.index('Virtual(run, "fsize-owner"')<fault.index('resource.prlimit(')and 'signal.SIGXFSZ - 1'in fault and 'actual.st_dev, actual.st_ino'in fault and 'before["bytes"] + 16'in fault and 'held-after-log-io-failure'in fault and 'os error 27'in fault and 'forged successful drained shutdown'in fault)
  report.update({'jobs':job_reports,'source_map':{'path':'git-source-map.json','unique_paths':len(mapped),'unique_git_blobs':len(blobs)},'evidence_layers':{'official_direct':['terminal job metadata','793 qualified test execution lines and47 libtest rows per job','six case outcomes and fsize stopped device snapshot','38 actual child receipts per job,11 with /proc-derived executable metadata','source and artifact hash inventories; actual UID/uname/wchan'], 'fixed_source_contract':['Runtime RLIMIT_FSIZE only after ready/import/configuration/pending virtual INs','SIGXFSZ ignored, inode/FD matched, limit=active_size+16, partial preserved','same actual owner cleanup before process exit1 is checked via stopped event; data socket EOF sampled after process wait'], 'not_present':['Ephemeral runner /tmp artifact bodies are NOT uploaded here: protocol.json, rlimit-fault.json, partial NDJSON/proc bodies and stderr are hash receipts only','No locally available CI ELF bytes or reconstructed runtime originals','No timestamp proving data EOF happened before process exit; source explicitly samples EOF after wait']},'limits':['Diagnostic predicate counts are not new USB cases. TLS five adapter cases are nested in existing Rust test totals, not added to793.','Signal inventory includes archived evidence source files, not compilation inputs. Logging inventory is its actual selected source/runner set, distinct from the191 full-gate software-source count.','Earlier failures and later stderr fixes keep their actual revisions. This dff36a3 CI does not validate later edited source or diagnostics subprocess/QEMU acceptance.','No host/root-helper/physical USB/kernel mutation, hardware result, QEMU result, permanent filesystem stall cancellation or fullPhase3 completion follows from these ordinary-user loopback cases.','All original official responses preserved; no API call or CI rerun.']})
 except Exception as exc:
  report['parser_error']={'type':type(exc).__name__,'message':str(exc),'traceback':traceback.format_exc()}
 after={p.name:meta(p)for p in sorted(BASE.iterdir())if p.is_file()};write(out/'originals-after.json',after);ck('all official originals unchanged during review',before==after)
 report['review_result']='PASS'if not report.get('parser_error')and all(c['pass']for c in checks)else'FAIL';report['predicates']=len(checks);write(out/'report.json',report)
 print(json.dumps({'review_result':report['review_result'],'predicates':len(checks),'failed':[c for c in checks if not c['pass']],'parser_error':report.get('parser_error'),'report':str(out/'report.json'),'sha256':meta(out/'report.json')['sha256']},indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args();main(a.output)
