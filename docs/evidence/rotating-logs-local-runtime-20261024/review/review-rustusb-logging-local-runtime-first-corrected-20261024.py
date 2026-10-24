#!/usr/bin/env python3
"""Independent offline artifact parser: no imports of the producer/verifiers."""
from pathlib import Path
import argparse,collections,hashlib,json,re,struct,subprocess
SHA='6cc828f3769d1ebd4c2e7b566707b7788f107151bc21155843c315a818b8b474'
HEAD='f3cf8d4f13077cbcbb48c9e3e27918b87ca31a0d'
UUID=re.compile(r'^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$')
def strict(pairs):
 d={}
 for k,v in pairs:
  if k in d:raise ValueError('duplicate JSON key')
  d[k]=v
 return d
def load(p):return json.loads(p.read_bytes(),object_pairs_hook=strict)
def sha(p):
 b=p.read_bytes();return {'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def devices(value):return value.get('snapshot',value).get('devices',[])
def held(value):
 d=devices(value)
 return len(d)==2 and {x['profile'] for x in d}=={'hid_keyboard','cdc_acm'} and all(x['leased'] and x['configuration']==1 and x['pending_urbs']==1 for x in d)
def released(value):
 d=devices(value)
 return len(d)==2 and all(not x['leased'] and x['configuration']==0 and x['pending_urbs']==0 and x['hid_queued_reports']==0 and x['cdc_queued_bytes']==0 for x in d)
def ndjson(p):return [json.loads(x,object_pairs_hook=strict) for x in p.read_bytes().splitlines() if x]
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--input',type=Path,default=Path('/tmp/rustusb-logging-local-runtime-first-20261024'));ap.add_argument('--repo',type=Path,default=Path('/home/ubuntu/github/usbip-client'));ap.add_argument('--output',type=Path,default=Path('/tmp/rustusb-logging-local-runtime-independent-review-corrected-20261024'));a=ap.parse_args();a.output.mkdir(exist_ok=True)
 raw={str(p.relative_to(a.input)):sha(p) for p in sorted(a.input.rglob('*')) if p.is_file()};checks=[];facts={};details={}
 def ck(name,value,detail=None):checks.append({'assertion':name,'pass':bool(value),**({'detail':detail} if detail is not None else {})})
 def gitbytes(path):return subprocess.check_output(['git','show',HEAD+':'+path],cwd=a.repo)
 start=load(a.input/'start.json');result=load(a.input/'result.json');wrapper=load(a.input/'results.json');logdir=a.input/'logging';log=load(logdir/'summary.json')
 ck('actual wrapper terminal PASS and exact clean product head',result['result']=='PASS' and start['clean'] and start['head']==result['head']==HEAD)
 ck('actual three commands terminal0 and all source head clean binary guards',len(wrapper)==3 and all(x['exit_code']==0 and all(x[k] for k in ['inputs_stable','binary_stable','head_guard','clean_guard']) for x in wrapper))
 ck('same external723 CLI selected without build',all('--binary' in x['command'] for x in wrapper) and log['build']=='external prebuilt executable; this runner never builds' and result['cli_sha256']==SHA)
 inp=load(a.input/'inputs-before.json');out=load(a.input/'inputs-after.json')
 ck('actual177 before-after exact bytes',(a.input/'inputs-before.json').read_bytes()==(a.input/'inputs-after.json').read_bytes() and len(inp)==len(out)==177)
 mapped=[]
 for r in inp:
  b=gitbytes(r['path']);ok=len(b)==r['bytes'] and hashlib.sha256(b).hexdigest()==r['sha256'];mapped.append({'path':r['path'],'pass':ok});ck('original177 matches product blob '+r['path'],ok)
 ck('new six completed with no collection/forced-cleanup failure',len(log['cases'])==6 and log['completed'] and log['result']=='PASS' and not log['collection_errors'] and not log['forced_cleanup'])
 for r in log['artifacts']:
  ck('logging original indexed bytes '+r['path'],sha(logdir/r['path'])=={'bytes':r['bytes'],'sha256':r['sha256']})
 ck('new 190 input inventory unchanged',log['sources_before']==log['sources_after'] and len(log['sources_before']['files'])==190)
 newsource=log['sources_before'];ck('newsource recorded clean f3cf8 and canonical fingerprint',newsource['head']==HEAD and not newsource['git_status'] and hashlib.sha256(json.dumps(newsource['files'],sort_keys=True).encode()).hexdigest()==newsource['fingerprint'])
 for r in newsource['files']:
  b=gitbytes(r['path']);ck('new runtime source exact Git '+r['path'],len(b)==r['bytes'] and hashlib.sha256(b).hexdigest()==r['sha256'])
 ck('new source includes actual process harness and reused wire helper',{'scripts/test-process-logging.py','scripts/test-server-signals.py'}<={x['path'] for x in newsource['files']})
 ck('new actual external binary before/after hashes',log['binary_before']['sha256']==log['binary_after']['sha256']==SHA and log['binary_before']==log['binary_after'])
 actual_exe=Path(log['binary_before']['path']);actual_stat=actual_exe.stat();actual_bytes=actual_exe.read_bytes();facts['current_external_ELF']={'path':str(actual_exe),'bytes':len(actual_bytes),'sha256':hashlib.sha256(actual_bytes).hexdigest(),'device':actual_stat.st_dev,'inode':actual_stat.st_ino}
 ck('current external ELF exact previous723 bytes and observed child inode',hashlib.sha256(actual_bytes).hexdigest()==SHA and actual_bytes[:4]==b'\x7fELF' and (actual_stat.st_dev,actual_stat.st_ino)==(log['binary_before']['device'],log['binary_before']['inode']))
 ck('ordinary UID1000 and safe sampled parent ownership',log['uid']==1000 and all(x['uid']==1000 and (int(x['mode'],8)&0o022)==0 for x in log['parent_metadata']))
 child_rows=[load(p) for p in sorted(logdir.glob('*/process.json'))];sampled=[];fast=[]
 for r in child_rows:
  name=r['name'];d=logdir/name
  ck('child actual wait and no forced cleanup '+name,r['returncode'] is not None and not r['forced_cleanup'] and r['waited_monotonic_ns']>=r['started_monotonic_ns'])
  ck('child saved UID/PID/start identity '+name,r['identity']['pid']==r['pid'] and r['identity']['starttime']>0 and r['identity']['uids']==[1000]*4)
  if 'executed_binary' in r:
   sampled.append(name);exe=r['executed_binary'];ck('sampled actual child ELF '+name,exe['sha256']==SHA and exe['inode']==log['binary_before']['inode'] and exe['device']==log['binary_before']['device'])
   status=(d/'proc-status.txt').read_text();stat=(d/'proc-stat.txt').read_text();rest=stat[stat.rfind(')')+2:].split()
   uids=[int(x) for x in re.search(r'^Uid:\s+(.+)$',status,re.M)[1].split()]
   ck('raw proc identity and capability zero '+name,int(stat.split(' ',1)[0])==r['pid'] and int(rest[19])==r['identity']['starttime'] and uids==[1000]*4 and int(re.search(r'^CapEff:\s+([0-9a-f]+)',status,re.M)[1],16)==0)
  else:
   fast.append(name);ck('fast child explicitly lacks proc ELF sample '+name,'fast_exit_proc_scope' in r and not (d/'proc-stat.txt').exists())
  for s in r.get('signals',[]):ck('signal only selected child exact retained identity '+name,s['pid']==r['pid'] and s['identity']==r['identity'] and r['started_monotonic_ns']<=s['monotonic_ns']<=r['waited_monotonic_ns'])
 ck('new actual child observations 38 with11 sampled/27 fast',len(child_rows)==38 and len(sampled)==11 and len(fast)==27)
 details['new_child_identity']={'sampled_proc_exe':sampled,'fast_proc_exe_unavailable':fast}
 startup=['flags-below-size','flags-above-retain','flags-missing-directory','flags-console-conflict','flags-reader-writer','startup-bad-mode','startup-symlink']
 for i,name in enumerate(startup):
  r=load(logdir/name/'process.json');ck('startup refusal original wait/stdout/stderr '+name,r['returncode']==(2 if i<4 else 1) and (logdir/name/'stdout.bin').read_bytes()==b'' and (logdir/name/'stderr.bin').stat().st_size>0)
 ck('startup no log state created in inspected valid/writable stores',load(logdir/'final-store-startup-valid/original-metadata.json')==[] and load(logdir/'final-store-startup-writable/original-metadata.json')==[])
 stores={};all_records=[]
 for directory in sorted(logdir.iterdir()):
  if not directory.is_dir() or not (directory/'original-metadata.json').exists():continue
  entries=load(directory/'original-metadata.json');segments=[]
  for m in entries:
   ck('original log metadata private/singlelink/hash '+directory.name+'/'+m['archived'],m['uid']==1000 and m['mode']=='0o600' and m['nlink']==1 and sha(directory/m['archived'])=={'bytes':m['bytes'],'sha256':m['sha256']})
   if not m['archived'].endswith('.ndjson'):continue
   b=(directory/m['archived']).read_bytes();parts=b.splitlines(keepends=True);header=json.loads(parts[0],object_pairs_hook=strict);records=[];offset=len(parts[0]);tail=b''
   ck('actual segment header UUID/bounds '+directory.name+'/'+m['archived'],header['schema_version']==1 and bool(UUID.fullmatch(header['segment'])) and bool(UUID.fullmatch(header['epoch'])) and header['ordinal']>0 and len(b)<=4096)
   for line in parts[1:]:
    if not line.endswith(b'\n'):tail=line;continue
    rec=json.loads(line,object_pairs_hook=strict);offset+=len(line);records.append({'record':rec,'cursor':header['segment']+':'+str(offset)})
    ev=rec['event'];ck('record strict wholeline and process epoch '+directory.name+'/'+str(rec['sequence']),len(line)<=2048 and rec['schema_version']==1 and rec['epoch']==header['epoch'] and rec['sequence']>0 and set(rec)=={'schema_version','epoch','sequence','elapsed_millis','wall_unix_millis','level','event'})
    if ev['kind']=='diagnostic':
     ck('persisted diagnostic closed payload-free fields '+directory.name+'/'+str(rec['sequence']),set(ev)=={'kind','code','target','line','failure','errno','fields','omitted_fields'} and set(ev['fields'])<={'count','device_count','session_count','interface','success','network_closed','bytes_to_peer','bytes_from_peer','elapsed_millis'} and all(isinstance(v,(int,bool)) for v in ev['fields'].values()) and re.fullmatch(r'rustusb(?:_[a-z]+)*(?:::[a-z_]+)*',ev['target']))
    all_records.append(rec)
   segments.append({'header':header,'records':records,'tail_hex':tail.hex(),'bytes':len(b),'header_bytes':len(parts[0])})
  segments.sort(key=lambda x:x['header']['ordinal']);flatten=[x for seg in segments for x in seg['records']]
  ck('finite retention at most2 actual segments '+directory.name,len(segments)<=2)
  per_epoch=collections.defaultdict(list)
  for r in flatten:per_epoch[r['record']['epoch']].append(r['record']['sequence'])
  ck('retained process sequences strictly ordered '+directory.name,all(v==sorted(set(v)) for v in per_epoch.values()))
  stores[directory.name]={'segments':segments,'flatten':flatten}
 def protocol(path,expect_eof=True):
  data=load(path);res={}
  for bus in ['1-1','1-2']:
   rows=[x for x in data if x['bus_id']==bus];imports=[x for x in rows if x['operation']=='IMPORT'];submits=[x for x in rows if x['operation']=='SUBMIT'];returns=[x for x in rows if x['operation']=='RET_SUBMIT']
   if len(imports)!=1 or len(submits)!=2 or len(returns)!=1:return False
   imp=imports[0];req=bytes.fromhex(imp['request_hex']);reply=bytes.fromhex(imp['header_hex']);dev=bytes.fromhex(imp['device_hex'])
   if struct.unpack('>HHI',req[:8])!=(0x111,0x8003,0) or struct.unpack('>HHI',reply)!=(0x111,3,0) or req[8:].rstrip(b'\0').decode()!=bus or dev[256:288].rstrip(b'\0').decode()!=bus:return False
   bn,dn=struct.unpack_from('>II',dev,288);devid=bn<<16|dn
   parsed=[struct.unpack('>10I8s',bytes.fromhex(x['frame_hex'])) for x in submits]
   c,p=parsed
   if c[:5]!=(1,1,devid,0,0) or c[6]!=0 or c[10][1]!=9 or int.from_bytes(c[10][2:4],'little')!=1:return False
   if p[:5]!=(1,2,devid,1,1 if bus=='1-1' else 2) or p[6]!=(8 if bus=='1-1' else 64):return False
   ret=bytes.fromhex(returns[0]['frame_hex'])
   if len(ret)!=48 or struct.unpack_from('>II',ret)!=(3,1) or struct.unpack_from('>iI',ret,20)!=(0,0):return False
   if expect_eof and not any(x['operation']=='actual_data_eof' and x['bytes_hex']=='' for x in rows):return False
  return True
 for name in ['rotation-owner','follow-owner-sigint','follow-owner-sigterm','bp-owner','fsize-owner']:
  d=logdir/name;events=ndjson(d/'stdout.bin');ck('raw ready/held/stopped with genuine two leases '+name,events[0]['event']=='ready' and held(load(d/'held.json')) and released(load(d/'stopped.json')) and sum(e.get('event')=='stopped' for e in events)==1)
  ck('raw USBIP import/config1/pendingIN/controlreturn and finalEOF '+name,protocol(d/'protocol.json'))
  ck('raw stdout parsed events match collector JSON '+name,events==load(d/'stdout-events.json'))
  ck('file mode no console tee '+name,(d/'stderr.bin').read_bytes()==b'' if name!='fsize-owner' else (d/'stderr.bin').stat().st_size>0)
 initial=stores['rotation-initial-files'];expected=[r['record'] for r in initial['flatten']];pages=[ndjson(logdir/('cursor-'+str(i))/'stdout.bin')[0]['page'] for i in range(18)]
 ck('actual stable18 cursor pages reconstruct exact retained typed NDJSON',[x for p in pages for x in p['records']]==expected and len(expected)==18 and all(len(p['records'])==1 and p['status'] is None and not p['gap'] and not p['partial_tail'] for p in pages))
 ck('each actual cursor is exact raw complete-line offset',[p['next_after'] for p in pages]==[r['cursor'] for r in initial['flatten']] and all(p['more'] for p in pages[:-1]) and not pages[-1]['more'])
 epochs=[]
 for name,store in [('rotation-owner','rotation-initial-files'),('restart-0','restart-0-files'),('restart-1','restart-1-files'),('restart-2','restart-2-files')]:
  child=load(logdir/name/'process.json');st=stores[store];stopped=[x['record'] for x in st['flatten'] if x['record']['event']['kind']=='stopped'][-1];epochs.append(stopped['epoch'])
  ck('actual owner restart new epoch and healthy drain '+name,child['returncode']==0 and stopped['event']['status']['healthy'] and stopped['event']['status']['queued']==0 and released(load(logdir/name/'stopped.json')))
 ck('four distinct actual process epochs',len(set(epochs))==4)
 expired=ndjson(logdir/'expired-cursor/stdout.bin')[0]['page'];oldcursor=load(logdir/'rotation-restart-cursors.json')['facts']['expired_cursor'];ck('expired old cursor absent from final retained segments yields actual gap',expired['gap'] and oldcursor.split(':')[0] not in {s['header']['segment'] for s in stores['final-store-rotation']['segments']} and expired['records']==[r['record'] for r in stores['final-store-rotation']['flatten']])
 ck('human rendering actual stdout safe static message and cursor',(logdir/'human-page/stdout.bin').read_text().startswith('[INFO] epoch=') and 'Next cursor:' in (logdir/'human-page/stdout.bin').read_text() and b'\x1b' not in (logdir/'human-page/stdout.bin').read_bytes())
 for sig,num in [('sigint',2),('sigterm',15)]:
  owner=load(logdir/('follow-owner-'+sig)/'process.json');reader=load(logdir/('follow-reader-'+sig)/'process.json');pending=load(logdir/('follow-reader-'+sig)/'owner-still-held.json');outpages=ndjson(logdir/('follow-reader-'+sig)/'stdout.bin')
  ck('actual follow signal only reader and owner remains genuinely held '+sig,reader['returncode']==0 and reader['signals'][0]['signal']==num and reader['signals'][0]['pid']!=owner['pid'] and held(pending) and bool(outpages) and all(p['event']=='logs' for p in outpages) and pending['id']=='after-observer-exit')
 bp=load(logdir/'observer-output-backpressure.json')['facts'];br=load(logdir/'bp-reader/process.json');obs=load(logdir/'bp-reader/blocked-writer-observations.json')
 ck('actual4096 filled unread observer with real pipe writer and exit1',bp['capacity']==bp['synthetic_padding_bytes']==4096 and not bp['stdout_read_after_fill'] and br['returncode']==1 and bp['observed_wait'] in obs and bp['observed_wait']['wchan'] in ['pipe_write','anon_pipe_write'] and bp['observed_wait']['tid']>0 and 4<=bp['elapsed_seconds']<=8)
 ck('blocked observer deadline leaves actual sibling owner held',any(e.get('id')=='after-reader-deadline' and held(e) for e in ndjson(logdir/'bp-owner/stdout.bin')) and b'stdout consumer is blocked' in (logdir/'bp-reader/stderr.bin').read_bytes())
 fault=load(logdir/'fsize-owner/rlimit-fault.json');fp=load(logdir/'fsize-owner/process.json');before=fault['file_before'];after=fault['file_after'];fstore=stores['fsize-failed-originals'];seg=fstore['segments'][0];status=(logdir/'fsize-owner/proc-status.txt').read_text();sigign=int(re.search(r'^SigIgn:\s+([0-9a-f]+)',status,re.M)[1],16)
 ck('parent prlimit actual16-byte extension with same original active inode',before['bytes']==329 and after['bytes']==345 and fault['limit_after']==[345,-1] and fault['limit_before']==[-1,-1] and (before['device'],before['inode'])==(after['device'],after['inode']) and fault['set_monotonic_ns']<fp['signals'][0]['monotonic_ns'])
 ck('actual open loggerFD matches sampled path inode and offset',bool(fault['actual_log_fds']) and all(x['actual_fd_device']==before['device'] and x['actual_fd_inode']==before['inode'] and x['actual_fd_size']==329 and 'pos:\t329' in x['fdinfo'] for x in fault['actual_log_fds']))
 ck('actual CLI retained SIGXFSZ ignored and failure remained active before cooperative stop',bool(sigign & (1<<(25-1))) and fp['ignore_sigxfsz_exec_wrapper'] and held(fault['held_after_actual_partial_write']))
 ck('actual partial16 bytes preserved and no fabricated diskStopped',len(bytes.fromhex(seg['tail_hex']))==16 and not any(r['record']['event']['kind']=='stopped' for r in fstore['flatten']) and (logdir/'fsize-failed-originals/segment-00.ndjson').read_bytes()==(logdir/'final-store-fsize-failure/segment-00.ndjson').read_bytes())
 ck('actual backend stopped resource0 and actual wait1 EFBIG fixed final error',released(load(logdir/'fsize-owner/stopped.json')) and fp['returncode']==1 and b'logging I/O:' in (logdir/'fsize-owner/stderr.bin').read_bytes() and b'os error 27' in (logdir/'fsize-owner/stderr.bin').read_bytes())
 facts['fsize_timing']='Source Virtual.stop receives stdout until EOF, actual wait1, then records data EOF; actual data EOF sampling is after wait. Backend cleanup-before-logger is source reasoning plus stopped resource snapshot, not an invented earlier socket timestamp.'
 sigroot=a.input/'signals';ss=load(sigroot/'summary.json');sb=load(sigroot/'source-before.json');sa=load(sigroot/'source-after.json');fingerprint=hashlib.sha256()
 for r in sb['files']:fingerprint.update(r['path'].encode()+b'\0'+str(r['length']).encode()+b'\0'+r['sha256'].encode()+b'\n')
 ck('legacy signal source inventory equality and actual fingerprint',sb==sa and sb['source_sha256']==fingerprint.hexdigest() and sb['head']==HEAD and not sb['dirty'])
 ck('legacy signal external binary association not inferred build features',ss['build']['skipped'] and ss['build']['features']=='external prebuilt; not inferred' and ss['binary_before']['sha256']==ss['binary_after_sha256']==SHA)
 for mode in ['ready','blocked']:
  for sig,num in [('sigint',2),('sigterm',15)]:
   name=mode+'-'+sig;d=sigroot/name;r=load(d/'result.json');post=load(d/'after.json');pre=load(d/'before.json');ev=ndjson(d/'stdout-consumed.bin')
   ck('legacy4 actual pending protocol identity signal exit0 '+name,held(pre) and protocol(d/'protocol.json',False) and r['final_returncode']==0 and not r['forced_cleanup'] and r['executed_binary']['sha256']==SHA and r['sent']['identity']==r['child'] and r['sent']['signal']==num and r['child']['uids']==[1000]*4 and post['wait_error'] is None and post['signal_exit_seconds']<=8)
   ck('legacy4 actual two socket EOF observed '+name,all(v['eof'] and v['unexpected_hex']=='' for v in post['data_sockets'].values()) and len(post['data_sockets'])==2)
   if mode=='ready':ck('legacy ready actual stopped snapshot resources0 '+name,post['backend_cleanup_observed'] and len(post['stopped_events'])==1 and released(post['stopped_events'][0]) and any(e.get('event')=='stopped' for e in ev))
   else:
    pipe=r['pipe'];ck('legacy blocked exact4096 no-drain actual writer originalOFD still blocking '+name,pipe['capacity']==pipe['bytes_after_fill']==pipe['synthetic_padding_bytes']==4096 and pipe['independent_writer_eagain'] and all(pipe[k]&0o4000==0 for k in ['child_stdout_flags_before','child_stdout_flags_after','original_writer_flags_before','original_writer_flags_after']) and pipe['blocked_writer']['wchan'] in ['pipe_write','anon_pipe_write'] and not r['stdout_drained_after_fill'] and not post['stdout_read_after_fill'] and post['stdout_unread_bytes']==4096 and not post['backend_cleanup_observed'] and not post['stopped_events'])
 bpold=json.loads((a.input/'backpressure.log').read_text());ck('legacy2 old printed actual scope and outcomes separate',bpold['result']=='PASS' and bpold['host_uid']==1000 and len(bpold['checks'])==2 and [(x['mode'],x['exit']) for x in bpold['checks']]==[('sigint',0),('deadline',1)] and all(x['stdout_capacity']==4096 and not x['stdout_drained_after_fill'] and x['lease_socket_closed'] for x in bpold['checks']))
 facts.update({'new_cases':6,'startup_refusal_steps_within_one_case':7,'new_child_processes':len(child_rows),'new_proc_ELF_samples':len(sampled),'fast_child_proc_ELF_unavailable':len(fast),'new_source_inventory':190,'selected_product_inputs':177,'legacy_signal_manifest_entries':len(sb['files']),'legacy_signal_historical_docs_entries':sum(r['path'].startswith('docs/') for r in sb['files']),'legacy_signal_cases':4,'legacy_backpressure_cases':2,'actual_four_epochs':epochs,'actual_cursor_pages':len(pages),'original_stored_copied_records_not_new_testcases':len(all_records)})
 after_raw={str(p.relative_to(a.input)):sha(p) for p in sorted(a.input.rglob('*')) if p.is_file()};ck('all original raw bytes and file set unchanged during parser',raw==after_raw)
 report={'schema_version':1,'result':'PASS' if all(c['pass'] for c in checks) else 'FAIL','run':str(a.input),'head':HEAD,'CLI_sha256':SHA,'scope':'Offline parser/raw correlation, reviewer authored record/worker implementation; not fully independent implementation audit. No producer/verifier imported.','facts':facts,'parser_correction':{'initial_report':'/tmp/rustusb-logging-local-runtime-independent-review-20261024/review.json','initial_script':'/tmp/review-rustusb-logging-local-runtime-first-20261024.py','reason':'Initial parser assumed filename-derived snapshot id owner-still-held; raw exact id is after-observer-exit. Original2 parser assertions FAIL remain; no run/source/raw changed.'},'assertions':checks,'limitations':['Ordinary-user loopback virtual memory profiles only, no root helper execution, host USB, modules, kernel USB enumeration, hardware or QEMU claims.','Backend stopped resources0 is observed in new owner and legacy ready stdout. Legacy blocked output remains unread and has no backend resource snapshot, so socket EOF/exit alone is not promoted to backend cleanup.','New BP observed capacity4096/writer pipe_write/no-drain and executed source checks original blocking OFD; numeric F_GETFL values are not archived for that new case. Old signal blocked cases do archive those actual values.','27 fast new children have no actual /proc executable sample; selected argv, initial identity and global binary guards only.','Legacy2 backpressure runner has printed outcome/socket observations and wrapper ELF continuity only, no per-child ELF/proc or separate raw protocol artifacts.','New fsize data EOF is read after actual wait1; do not move its sampling before wait.','Actual fsize disk retains16-byte incomplete tail, no successful loggerStopped record. No new actual reader-on-partial-tail or partial-tail restart scenario is inferred from it.','177 product input list,190 new harness inventory and14149 legacy signal inventory have different selection semantics. Historical docs snapshots are not14149 live product inputs.','No new audit/Rootproof/physical safety acceptance inferred; typed audit authorization tested separately in software gate.','Actual wrapper external CLI is linked to previous723 allfeatures build by byte SHA, not an inferred build from skip-build. Prior dirty48a4 software run is not relabelled clean.'],'raw_manifest':'raw-artifacts.json','child_detail':'child-identity.json','source_mapping':'selected-product-map.json'}
 (a.output/'raw-artifacts.json').write_text(json.dumps(raw,indent=2)+'\n');(a.output/'selected-product-map.json').write_text(json.dumps(mapped,indent=2)+'\n');(a.output/'child-identity.json').write_text(json.dumps(details,indent=2)+'\n');(a.output/'review.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({'result':report['result'],'assertions':len(checks),'failures':[c for c in checks if not c['pass']],'facts':facts,'review':str(a.output/'review.json'),'integrity':sha(a.output/'review.json')},indent=2))
if __name__=='__main__':main()
