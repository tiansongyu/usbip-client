#!/usr/bin/env python3
"""Independent-method raw review of old-product Local gates; no project imports."""
import base64,copy,hashlib,json,re,struct,subprocess,sys
from decimal import Decimal
from pathlib import Path
OUT=Path(__file__).resolve().parent
REPO=Path('/home/ubuntu/github/usbip-client')
HEAD='b529f9ec5c83f3c1ef71151684aff324b6de00d8'
CLI='bb6fb276c0cbfbb73c2cdb8af0f73d87045ac7235b4943cf096c0643d570814a'
HELPER='b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5'
BASES={x:Path('/tmp/rustusb-prepare-stop-local-'+x+'-b529f9e-20261024')for x in ['resource','available']}
BASES['b1']=Path('/tmp/rustusb-prepare-stop-local-resource-b1de034-20261024')
checks=[];cache={}
def sha(b):return hashlib.sha256(b).hexdigest()
def j(p):return json.loads(p.read_text())
def jl(p):return [json.loads(x)for x in p.read_text().splitlines()if x]
def ck(ok,label):checks.append({'pass':bool(ok),'label':label})
def eq(a,b,label):ck(a==b,label)
def dump(n,v):(OUT/n).write_text(json.dumps(v,sort_keys=True,indent=2)+'\n')
def inventory():return {k:{str(p.relative_to(b)):{'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())}for p in sorted(b.rglob('*'))if p.is_file()}for k,b in BASES.items()}
def blob(head,name):
 key=(head,name)
 if key not in cache:cache[key]=subprocess.check_output(['git','cat-file','blob',head+':'+name],cwd=REPO)
 return cache[key]
def terms(root):return [r['terminal']for r in root['terminals']]
def proof(session,attempt):return {'resource_id':attempt['resource_id'],'kind':attempt['kind'],'device_key':session['device_key'],'owner_uid':2000,'correlation':attempt['correlation'],'proof':{'native_export':'native_export_restored','native_stub':'native_stub_available','native_import':'native_import_released'}[attempt['kind']]}
def parse_stat(text):return int(text.split(' ',1)[0]),int(text.rsplit(')',1)[1].split()[19])
def decode(base,role,declared):
 lines=(base/(role+'-evidence.log')).read_text().splitlines();found={};i=0
 while i<len(lines):
  if lines[i].startswith('SECURE_ARTIFACT_BEGIN '):
   _,name,size=lines[i].split();i+=1;parts=[]
   while i<len(lines)and not lines[i].startswith('SECURE_ARTIFACT_END '):parts.append(lines[i]);i+=1
   eq(lines[i],'SECURE_ARTIFACT_END '+name,role+' exact artifact end '+name)
   b=base64.b64decode(''.join(parts),validate=True);ck(name not in found,role+' unique artifact '+name)
   eq(len(b),int(size),role+' decoded declared size '+name);eq(b,(base/(role+'-artifacts')/name).read_bytes(),role+' decoded original bytes '+name)
   eq({'bytes':len(b),'sha256':sha(b)},declared[name],role+' original guest manifest '+name);found[name]=sha(b)
  i+=1
 eq(set(found),set(declared),role+' complete original artifact set')
 ck(not any(x.startswith(('SECURE_ARTIFACT_ERROR','SECURE_ARTIFACT_OVERSIZE'))for x in lines),role+' no artifact collection failure')
 return len(found)
def key_events(data,label):
 ck(len(data)>0 and len(data)%24==0,label+' aligned actual evdev records')
 entries=list(struct.iter_unpack('<qqHHi',data));keys=[x[2:]for x in entries if x[2]==1]
 eq(keys,[(1,2,1),(1,2,0)],label+' exact KEY1 down/up');return len(entries)

def review(case):
 b=BASES[case];a=b/'server-artifacts';c=b/'client-artifacts';s=j(b/'summary.json');first=len(checks)
 sj=lambda n:j(a/n);cj=lambda n:j(c/n)
 eq((s['head'],s['head_after']),(HEAD,HEAD),case+' original exact head')
 eq((s['git_status_before'],s['git_status_after']),('',''),case+' clean original before/after')
 for k in ['source_guard','binary_guard','head_guard','clean_guard']:ck(s[k]is True,case+' '+k)
 eq((s['result'],s['case'],s['stop']),('FAIL','native-'+case,'session'),case+' original FAIL retained')
 eq(s['guest_exit_codes'],{'server':0,'client':0},case+' actual collection completed')
 eq(s['binary_sha256'],{'rustusb':CLI,'rustusb-helper':HELPER},case+' old original ELF declarations')
 for name,meta in s['artifact_manifest'].items():eq(before[case][name],meta,case+' archive original '+name)
 eq(set(s['artifact_manifest']),set(before[case])-{'summary.json'},case+' original complete archive excluding self')
 for name,meta in s['source_inputs'].items():
  data=(b/'source'/name).read_bytes();eq({'bytes':len(data),'sha256':sha(data)},meta,case+' saved source '+name);eq(data,blob(HEAD,name),case+' exact committed blob '+name)
 decoded={r:decode(b,r,s['guest_artifacts'][r])for r in ['server','client']}
 for role in ['server','client']:
  ar=b/(role+'-artifacts');log=(b/(role+'-evidence.log')).read_text()
  for label,expected in [('helper',HELPER),('agent-1',CLI)]+([('agent-2',CLI),('gate',s['probe_sha256'])]if role=='server'else[]):
   rec=(ar/('executable-'+label+'.txt')).read_text().split();eq(rec[0],expected,case+' '+role+' actual /proc ELF '+label);ck(bool(re.fullmatch('/proc/[0-9]+/exe',rec[1])),case+' actual live executable path '+label)
   fields=dict(x.split(':',1)for x in(ar/('identity-'+label+'.txt')).read_text().splitlines()if ':'in x)
   uid='0'if label in ['helper','gate']else'2000';eq(fields['Uid'].split(),[uid]*4,case+' '+role+' actual UID '+label)
   if label.startswith('agent'):ck(all(int(fields[x],16)==0 for x in ['CapInh','CapPrm','CapEff','CapAmb']),case+' '+role+' agent no capabilities '+label)
  eq(sha((b/(role+'-generated-init.sh')).read_bytes()),s['payload_sha256'][role]['init'],case+' '+role+' generated payload init')
  eq(s['payload_sha256'][role]['usr/bin/rustusb'],CLI,case+' '+role+' payload CLI');eq(s['payload_sha256'][role]['usr/bin/rustusb-helper'],HELPER,case+' '+role+' payload helper')
  ck('SECURE_CREDENTIAL_ISOLATION role='+role+' own_key=true opposite_key=false ca_key=false'in log,case+' '+role+' actual role key isolation')
  ck('Kernel panic'not in(b/(role+'-serial.log')).read_text()and'Oops:'not in(b/(role+'-serial.log')).read_text(),case+' '+role+' no kernel fault')
 eq(sha((b/'kernel.config').read_bytes()),s['kernel_config_sha256'],case+' exact kernel config')
 target= sj('local-target.json');survivor=sj('local-survivor.json');selected=sj('selected-state.json');committed=sj('state-before-ack.json');selection=sj('selection.json');ready=sj('gate-ready.json');n=selection['candidate'];gate=sj(f'gate-candidate-{n}.json');pre=sj('gate-held-before.json');post=sj('gate-held-after.json');done=sj(f'gate-exit-{n}.json');events=jl(a/'gate-events.jsonl')
 eq(selected,sj(f'draft-{n}.json'),case+' exact selected actual WAL draft')
 eq(selected['sequence'],committed['sequence']+1,case+' next original durable sequence')
 eq(selected['audit'][:-1],committed['audit'],case+' all prior audit unchanged')
 eq(selected['audit'][-1]['session_id'],target['id'],case+' held exact serial target transition')
 eq((target['device_key'],survivor['device_key']),(sj('keys.json')[1],sj('keys.json')[0]),case+' serial target/HID survivor exact keys')
 eq({x['id']for x in sj('selected-sessions.json')},{target['id'],survivor['id']},case+' exact original two sessions')
 eq((target['job_id'],survivor['job_id']),(selection['job'],selection['job']),case+' same original job')
 if case=='resource':
  eq(selected['audit'][-1]['action'],'resource',case+' actual second Resource entry')
  eq(sum(x['resource_id']is not None for ss in committed['sessions']for x in ss['attempts']),1,case+' original first Resource durable only')
 else:
  eq((selected['audit'][-1]['action'],selected['audit'][-1]['state'],selected['audit'][-1]['evidence']),('observe','Available','export_ready'),case+' actual Available entry')
  ck(all(x['attempts'][0]['resource_id']for x in committed['sessions']),case+' both resources already durable')
  ck(any(x['id']==survivor['id']and x['state']=='Available'for x in committed['sessions'])and any(x['id']==target['id']and x['state']!='Available'for x in committed['sessions']),case+' only HID Available ACK precedes target Available')
 for name,x in [('before',pre),('after',post),('exit',done)]:
  for field in ['candidate','tracee_pid','tracee_starttime','tid','fd','request','file_device','file_inode']:eq(x[field],gate[field],case+' same held '+field+' '+name)
 eq((gate['nonce'],gate['operation'],gate['request']),('prepare-stop-v1','resource',74),case+' actual fsync entry nonce and number')
 eq((gate['tracee_pid'],gate['tracee_starttime']),(ready['tracee_pid'],ready['tracee_starttime']),case+' same owner generation')
 ck(pre['held']and post['held']and gate['monotonic_ns']<pre['monotonic_ns']<post['monotonic_ns']<done['monotonic_ns'],case+' actual same held interval')
 ck(post['monotonic_ns']-pre['monotonic_ns']>=15_000_000_000,case+' actual held window at least15s')
 eq((done['return_value'],done['is_error']),(0,False),case+' same original fsync returns0 after explicit release')
 releases=[x for x in events if x['event']=='released'];eq(len(releases),1,case+' exactly one explicit release');ck(post['monotonic_ns']<releases[0]['monotonic_ns']<done['monotonic_ns'],case+' after-held before original exit')
 ck(not any(x['event']=='signal_delivery'and pre['monotonic_ns']<x['monotonic_ns']<post['monotonic_ns']for x in events),case+' no signal substitutes local RPC while gate held')
 eq((a/'wait-stop.txt').read_text(),'1\n',case+' actual stop-session caller timeout exit1 retained');ck('operation timed out: agent local request'in(a/'stop-target.log').read_text(),case+' durable response timeout preserved')
 fd=next(x for x in sj('fds-held.json')if int(x['fd'])==gate['fd']);eq((fd['target'],int(fd['device']),int(fd['inode'])),('/var/lib/rustusb/agent/state.next',gate['file_device'],gate['file_inode']),case+' actual protected WAL fd identity')
 rootheld=sj('root-held.json');eq((rootheld['schema_version'],rootheld['records'],rootheld['terminals']),(2,[],[]),case+' initial exact privileged state')
 eq(len(rootheld['exports']),2,case+' two actual bound exports')
 for session in [target,survivor]:
  attempt=session['attempts'][0];record=next(x for x in rootheld['exports']if x['lease_id']==attempt['resource_id']);eq(record['correlation'],attempt['correlation'],case+' exact original root correlation '+session['id']);eq(record['registration']['device_key'],session['device_key'],case+' exact original key '+session['id']);eq((record['owner']['uid'],record['owner']['pid'],record['owner']['starttime']),(2000,gate['tracee_pid'],gate['tracee_starttime']),case+' original helper owner '+session['id']);ck(record['bound']is not None and record['stub']is None,case+' bound preimport root resource '+session['id'])
 original=next(x for x in rootheld['exports']if x['lease_id']==survivor['attempts'][0]['resource_id']);baseline=sj('host-before.json');heldhost=sj('host-held.json');obs=jl(a/'local-held-observations.jsonl')
 for x in obs:
  i=x['observation'];eq(sj(f'root-observe-{i}.json'),rootheld,case+f' held sample{i} original two resources still present');eq(sj(f'host-observe-{i}.json'),heldhost,case+f' held sample{i} both bound profiles unchanged');eq(sj(f'agent-observe-{i}.json'),committed,case+f' held sample{i} original ACK not falsely committed');eq(parse_stat((a/f'owner-stat-observe-{i}.txt').read_text()),(gate['tracee_pid'],gate['tracee_starttime']),case+f' held sample{i} owner still same generation');eq((x['target_exact_terminal_and_original_sibling'],x['target_restored_and_sibling_bound'],x['gate_released']),(False,False,False),case+f' held sample{i} independent failed condition')
 outcome=sj('local-held-outcome.json');eq((outcome['failed'],outcome['local_cleanup_while_held']),(1,False),case+' genuine expected held FAIL latched')
 eq(sj('root-before-release.json'),rootheld,case+' final before-release root still pending')
 listenerrows=[x.split()for x in(a/'listeners-held.txt').read_text().splitlines()if len(x.split())>3];bound=any(x[3]=='0A'and x[1].endswith(':0CA9')for x in listenerrows);eq(bound,case=='available',case+' held listener actual boundary');eq((c/'held-connect.wait').read_text(),'0\n'if bound else'1\n',case+' actual held TCP result')
 if not bound:ck('Connection refused'in(c/'held-connect.stderr').read_text(),case+' actual prelistener refusal')
 afterack=sj('root-local-after-ack.json');eq(afterack['exports'],[original],case+' same HID complete export after original ACK');eq(terms(afterack),[proof(target,target['attempts'][0])],case+' serial exact root proof only after release')
 for name in ['local-jobs.json','local-empty-jobs.json']:
  jobs=sj(name)['response']['jobs'];ck(len(jobs)==1 and jobs[0]['id']==selection['job']and jobs[0]['instance_id']==survivor['instance_id']and jobs[0]['state']=='running',case+' same original job Running '+name)
 eq(cj('local-catalog.json')['response'],{'result':'catalog','devices':[{'device_key':survivor['device_key'],'device':original['registration']['published_device']}]},case+' actual authenticated catalog original HID only')
 denied=cj('local-denied-sessions.json')['response']['sessions'];ck(len(denied)==1 and denied[0]['device_key']==target['device_key']and denied[0]['terminal']and denied[0]['operation_error']=='permission'and denied[0]['cleanup']=='not_started'and denied[0]['attempts']==[],case+' actual Catalog selection denied no helper attempt')
 eq((c/'local-before-denied-root.json').read_bytes(),(c/'local-after-denied-root.json').read_bytes(),case+' denial leaves root bytes unchanged')
 during=sj('root-local-survivor-io.json');eq({k:v for k,v in during['exports'][0].items()if k!='stub'},{k:v for k,v in original.items()if k!='stub'},case+' original HID root capability survives actual I/O')
 stub=during['exports'][0]['stub'];eq(sj('local-detach-stub.json'),stub,case+' exact actual live stub pinned for barrier');eq(stub['state']['phase'],'observed',case+' stub actual observed receipt');ck(stub['state']['receipt']['kernel_socket_inode']>0,case+' real kernel stub socket inode')
 local_evdev=key_events((c/'local-survivor-input.bin').read_bytes(),case+' original surviving HID')
 client_import=cj('local-survivor-sessions.json')['response']['sessions'][0];client_detached=cj('root-local-detached.json');eq(client_detached['records'],[],case+' client original import released');eq(terms(client_detached),[proof(client_import,client_import['attempts'][0])],case+' client own exact proof distinct from server stub')
 server_stubproof={'resource_id':stub['channel_id'],'kind':'native_stub','device_key':survivor['device_key'],'owner_uid':2000,'correlation':stub['correlation'],'proof':'native_stub_available'}
 expectedterms=sorted([proof(target,target['attempts'][0]),server_stubproof],key=lambda x:x['resource_id'])
 expectedhost=copy.deepcopy(baseline);hidbus=original['registration']['generation']['bus_id']
 for h in expectedhost:
  if h['bus']==hidbus:
   h['device_driver']='usbip-host';h['usbip_status']=1
   for interface in h['interfaces']:interface['driver']=''
 def complete(r):return r['schema_version']==2 and r['records']==[] and r['exports']==[original]and sorted(terms(r),key=lambda x:x['resource_id'])==expectedterms
 barrier=sj('local-detach-barrier.json');samples=jl(a/'local-detach-observations.jsonl');eq((barrier['limit_seconds'],barrier['maximum_observations'],barrier['retired'],barrier['read_only'],barrier['last_export_stop_sent']),(15,500,True,True,False),case+' actual bounded read-only barrier')
 eq([x['observation']for x in samples],list(range(1,len(samples)+1)),case+' complete contiguous poll samples');eq(len(samples),barrier['observations'],case+' barrier count');ck(len(samples)<=500,case+' bounded poll allocation')
 start=Decimal(barrier['started_uptime']);last=start;pollfacts=[]
 for row in samples:
  i=row['observation'];r=sj(f'root-local-detach-poll-{i}.json');h=sj(f'host-local-detach-poll-{i}.json');app=sj(f'agent-local-detach-poll-{i}.json');now=Decimal(row['uptime']);rootok=complete(r);hostok=h==expectedhost;within=start<=now<=start+15
  eq((row['root_retired_exact'],row['host_available_original'],row['within_bound']),(rootok,hostok,within),case+f' poll{i} recomputed original root+host+time');ck(row['read_only']and not row['last_export_stop_sent']and now>=last,case+f' poll{i} read-only monotonic');ck(not any(e['action']=='stop'and e['session_id']==survivor['id']for e in app['audit']),case+f' poll{i} no last-export stop audit');last=now
  hid=next(x for x in h if x['bus']==hidbus);pollfacts.append({'sample':i,'uptime':str(now),'root_terminal_count':len(r['terminals']),'stub_still_in_root':r['exports'][0]['stub']is not None,'sysfs_status':hid['usbip_status'],'devnum':hid['devnum'],'root_complete':rootok,'host_complete':hostok})
 last_i=samples[-1]['observation'];ck(samples[-1]['root_retired_exact']and samples[-1]['host_available_original']and samples[-1]['within_bound'],case+' actual accepted sample complete')
 eq(Decimal(barrier['completed_uptime']),last,case+' accepted original uptime');eq((a/'root-local-after-detach.json').read_bytes(),(a/f'root-local-detach-poll-{last_i}.json').read_bytes(),case+' exact accepted root bytes not later proof');eq((a/'host-local-after-detach.json').read_bytes(),(a/f'host-local-detach-poll-{last_i}.json').read_bytes(),case+' exact accepted host bytes not later proof')
 ck(Decimal((a/'local-last-export-stop-uptime.txt').read_text())>=last,case+' last-exportstop actually after accepted sample uptime')
 slog=(b/'server-evidence.log').read_text();clog=(b/'client-evidence.log').read_text();mark1='PREPARE_LOCAL_STUB_RETIRED\n';mark2='PREPARE_LOCAL_LAST_EXPORT_STOP_REQUEST\n';ck(slog.count(mark1)==slog.count(mark2)==1 and slog.index(mark1)<slog.index(mark2),case+' actual accepted marker before last-exportstop request')
 frozen=(b/'server-generated-init.sh').read_text();ck('[ "$STUB_RETIRED" = true ]'in frozen and frozen.index('[ "$STUB_RETIRED" = true ]')<frozen.index('agent stop-session "$SURVIVOR_SESSION"'),case+' frozen guard forbids last-exportstop before success')
 eq(sj('local-stop-survivor.json')['response'],{'result':'stop_accepted','owner_present':True},case+' actual later authorized local export stop')
 empty=sj('root-local-empty.json');eq(empty['exports'],[],case+' only later empty export pool');eq(sorted(terms(empty),key=lambda x:x['resource_id']),sorted(expectedterms+[proof(survivor,survivor['attempts'][0])],key=lambda x:x['resource_id']),case+' exact original two export+stub terminals')
 eq(sj('host-local-empty.json'),baseline,case+' last local export stop complete original baseline')
 eq(cj('local-empty-catalog.json')['response'],{'result':'catalog','devices':[]},case+' actual authenticated empty catalog before global TERM')
 emptylisteners=[x.split()for x in(a/'local-empty-listeners.txt').read_text().splitlines()if len(x.split())>3];ck(any(x[3]=='0A'and x[1].endswith(':0CA9')for x in emptylisteners),case+' same configured listener remains bound when pool empty')
 marks=[x['mark']for x in jl(b/'host-control.jsonl')];ordered=['survivor-io','detach-survivor','survivor-detached','empty-catalog-request','empty-catalog-confirmed','local-job-closed'];ck(all(marks.count(x)==1 for x in ordered)and [marks.index(x)for x in ordered]==sorted(marks.index(x)for x in ordered),case+' original controlled ordering through emptyCatalog/globalstop')
 actual_stops=[x for x in sj('agent-after-release.json')['audit']if x['action']=='stop'];eq({(x['actor_uid'],x['session_id'])for x in actual_stops},{(1000,target['id']),(1000,survivor['id'])},case+' actual two local UID1000 stop audits')
 fresh=sj('root-reuse-ready.json')['exports'];ck(len(fresh)==2 and {x['lease_id']for x in fresh}.isdisjoint({x['lease_id']for x in rootheld['exports']})and all(x['correlation']['instance_id']!=survivor['instance_id']for x in fresh),case+' fresh epoch new two actual capabilities')
 fresh_evdev=key_events((c/'input-1.bin').read_bytes(),case+' fresh HID')
 eq((c/'rx-1.bin').read_bytes(),(b/'ftdi-host-injected-1.bin').read_bytes(),case+' fresh actual exact35B host->client');eq((c/'tx-1.bin').read_bytes(),(b/'ftdi-host-rx.bin').read_bytes(),case+' fresh actual exact35B client->host');eq(((c/'rx-1.bin').stat().st_size,(c/'tx-1.bin').stat().st_size),(35,35),case+' actual serial byte lengths')
 for role,expectedjobs,expectedsessions,expectedattempts in [('server',2,7,7),('client',4,4,3)]:
  ar=b/(role+'-artifacts');state=j(ar/'agent-final.json');root=j(ar/'root-final.json');eq((root['records'],root['exports']),([],[]),case+' '+role+' final actual root empty');eq((len(state['jobs']),len(state['sessions']),len(root['terminals'])),(expectedjobs,expectedsessions,expectedattempts),case+' '+role+' exact retained histories')
  ck(all(x['state']=='finished'for x in state['jobs'])and all(x['terminal']for x in state['sessions']),case+' '+role+' actual jobs/sessions terminal')
  actualattempts=[]
  for session in state['sessions']:
   for attempt in session['attempts']:
    actualattempts.append(attempt);eq(terms(root).count(proof(session,attempt)),1,case+' '+role+' exact root final proof '+session['id']);eq(session['cleanup'],'verified',case+' '+role+' application cleanup '+session['id'])
  eq(len(actualattempts),expectedattempts,case+' '+role+' exact real attempt count')
  if role=='client':eq([x for x in state['sessions']if not x['attempts']],denied,case+' exact original denied zero-attempt history retained')
 eq(sj('host-final.json'),baseline,case+' final full original driver/config/alt/generation baseline')
 for n in ['local-detached-vhci.txt','local-vhci-empty.txt','vhci-final.txt']:
  ports=[x.split()for x in(c/n).read_text().splitlines()if x.startswith(('hs ','ss '))];ck(bool(ports)and all(x[2]=='004'for x in ports),case+' actual all ports NULL '+n)
 ck('RUSTUSB_PREPARE_RESULT: FAIL role=server stage=actual-held-cleanup-missing exit=1'in slog and'RUSTUSB_PREPARE_RESULT: PASS role=server'not in slog,case+' initial held FAIL remains latched through all later success')
 ck('RUSTUSB_PREPARE_RESULT: PASS role=client'in clog,case+' client actual terminal collection pass')
 return {'case':case,'original_result':'FAIL','head':s['head'],'source_count':len(s['source_inputs']),'decoded_artifacts':decoded,'held_candidate':n,'held_observation_count':len(obs),'held_target_cleanup':False,'barrier_samples':pollfacts,'accepted_time':str(last),'last_export_stop_time':(a/'local-last-export-stop-uptime.txt').read_text().strip(),'original_hid_export':original['lease_id'],'original_stub':stub['channel_id'],'stub_kernel_socket_inode':stub['state']['receipt']['kernel_socket_inode'],'original_hid_evdev_records':local_evdev,'fresh_hid_evdev_records':fresh_evdev,'fresh_serial_bytes_each_direction':35,'same_job':selection['job'],'diagnostic_predicates':len(checks)-first,'original_verifier_metadata_not_used_as_verdict':{'count':s['facts']['check_count'],'failures':s['facts']['failures']}}

before=inventory();dump('raw-sha-before.json',before)
try:
 runs=[review(x)for x in ['resource','available']]
 # Compare the prior b1 observation without a counterfactual rerun or backfill.
 b=BASES['b1'];s=j(b/'summary.json');a=b/'server-artifacts';old=j(a/'root-local-after-detach.json');later=j(a/'root-local-empty.json');host=j(a/'host-local-after-detach.json');h=next(x for x in host if x['vendor']=='0627')
 eq(s['result'],'FAIL','b1 original four-failure run remains FAIL');eq(s['binary_sha256'],{'rustusb':CLI,'rustusb-helper':HELPER},'b1 same old product ELF')
 for name,meta in s['artifact_manifest'].items():eq(before['b1'][name],meta,'b1 preserved original archive '+name)
 ck(len(old['exports'])==1 and old['exports'][0]['stub']is not None and len(old['terminals'])==1,'b1 original after-detach root has unretired stub and only serial terminal')
 eq((h['usbip_status'],h['devnum']),(1,'0'),'b1 actual sysfs AVAILABLE/address0 not stable identity')
 ck(later['exports']==[]and len(later['terminals'])==3,'b1 only later export-stop phase contains all3 terminals')
 eq(len(s['facts']['failures']),4,'b1 original extra failure not erased')
 current=j(BASES['resource']/'summary.json');differences=[]
 for name,meta in s['source_inputs'].items():
  if name in current['source_inputs']and meta!=current['source_inputs'][name]:differences.append(name)
 production=[x for x in current['source_inputs']if x in ['Cargo.toml','Cargo.lock']or x.startswith(('crates/','vendor/'))]
 ck(all(s['source_inputs'][x]==current['source_inputs'][x]for x in production),'all selected product source bytes identical b1 versus b529')
 eq(current['source_inputs'],j(BASES['available']/'summary.json')['source_inputs'],'both b529 cases share same frozen full selected input set')
 comparison={'b1_head':s['head'],'b1_result':'FAIL with original4 failures','b1_after_detach_root_stub_present':True,'b1_after_detach_terminals':1,'b1_after_detach_actual_sysfs_status':h['usbip_status'],'b1_after_detach_actual_devnum':h['devnum'],'b1_after_last_export_stop_terminals':len(later['terminals']),'product_source_entries_compared':len(production),'selected_source_differences':differences,'conclusion':'b529 independently waits until an actual complete server retirement sample before last export stop. This observed success in different runs does not prove why b1 was sampled early or what b1 would have done without its export stop.'}
except Exception as e:
 dump('parser-error.json',{'type':type(e).__name__,'message':str(e),'completed_predicates':len(checks)});dump('checks-partial.json',checks);raise
finally:
 after=inventory();dump('raw-sha-after.json',after)
eq(before,after,'all three original directories unchanged throughout review')
failed=[x for x in checks if not x['pass']]
report={'review_result':'PASS'if not failed else'FAIL','scope':'Independent-method raw lifecycle review of two actual old-product Local QEMU failures, with separate preserved b1 comparison. Not an independent implementation.','product_results':'b529 Resource FAIL; b529 Available FAIL; b1 original FAIL unchanged','author_disclosure':'Reviewer contributed service implementation and earlier harness portions; did not import or call the original verifier, did not run Cargo/QEMU, did not mutate Git/source/original evidence.','runs':runs,'b1_comparison':comparison,'diagnostic_predicates_not_usb_cases':len(checks),'failed_predicates':failed,'raw_file_count':sum(map(len,before.values())),'raw_unchanged':before==after,'unique_git_blobs_read':len(cache),'limitations':['Old-product source/CLI is not new 667 software or final23 matrix evidence.','Original host/global/clean guards are contemporaneous execution receipts corroborated with saved committed blobs and actual guest ELF digests; no binaries were rebuilt or rerun.','Client detach is only client cleanup evidence; accepted server samples are independently checked against original exact root/stub and full sysfs identity.','Whole raw root/host/app sampling groups are sequential reads, not an atomic cross-filesystem snapshot.','Local serial denial is Catalog-selection Permission/NotStarted/zerohelperattempt, not a new raw stale-IMPORT QEMU negative.','No physical hardware G1/G2, ARM execution, in-kernel-active stall or pre-listener sibling I/O claim.','VHCI NULL is directly sampled; old-generation Gone is supported by exact root native_import_released and frozen helper implementation, not an extra final sysfs-stat dump.','PCAP bytes are manifest checked only; no new packet-level TLS audit in this lifecycle review.','Original verifier metadata counts are recorded for traceability only; independent predicates derive from raw artifacts.']}
dump('report.json',report);dump('checks.json',checks)
dump('handoff.json',{'report_sha256':sha((OUT/'report.json').read_bytes()),'parser_sha256':sha(Path(__file__).read_bytes()),'raw_sha_before_sha256':sha((OUT/'raw-sha-before.json').read_bytes()),'raw_sha_after_sha256':sha((OUT/'raw-sha-after.json').read_bytes()),'review_result':report['review_result'],'product_results':report['product_results']})
print(json.dumps({'result':report['review_result'],'failures':failed[:5],'predicates':len(checks),'raw_files':report['raw_file_count'],'runs':[{'case':r['case'],'samples':r['barrier_samples'],'accepted':r['accepted_time'],'laststop':r['last_export_stop_time']}for r in runs]}))
sys.exit(bool(failed))
