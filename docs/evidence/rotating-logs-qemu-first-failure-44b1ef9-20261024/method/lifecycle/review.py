#!/usr/bin/env python3
"""Independent-method raw review by the original harness author, not an independent implementation.

No imports from the repository/harness, no product execution, no USB or mutation.
An original FAIL is always retained; check results describe original consistency.
"""
import argparse,base64,hashlib,json,os,re,struct,subprocess
from decimal import Decimal
from pathlib import Path


def sha(data):return hashlib.sha256(data).hexdigest()
def inventory(root):
 return {str(p.relative_to(root)):{'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(root.rglob('*')) if p.is_file()}
def read_json(path):return json.loads(path.read_bytes())
def uptime(value):return Decimal((value.decode() if isinstance(value,bytes) else value).split()[0])
def proc(value):
 text=value.decode();tail=text[text.rfind(') ')+2:].split()
 return {'pid':int(text.split(' ',1)[0]),'comm':text[text.find('(')+1:text.rfind(')')],'state':tail[0],'ppid':int(tail[1]),'starttime':int(tail[19])}
def statuses(value):return dict(line.split(':',1) for line in value.decode().splitlines() if ':' in line)
def projection(s):return {k:s[k] for k in ['id','instance_id','job_id','profile','role','device_key','principal'] } | {'attempts':[{k:a[k] for k in ['kind','correlation','resource_id']} for a in s['attempts']]}


def review(evidence,out,repo):
 summary=read_json(evidence/'summary.json')
 if not summary.get('completed_utc'):raise SystemExit('No completed_utc: refuse to review an unfinished run')
 out.mkdir(exist_ok=False)
 before=inventory(evidence);(out/'originals-before.json').write_text(json.dumps(before,indent=2)+'\n')
 checks=[];facts={};errors=[]
 def check(test,description):
  checks.append({'check':description,'pass':bool(test)})
  if not test:raise ValueError(description)
 def raw(role,name):return (evidence/f'{role}-artifacts'/name).read_bytes()
 def data(role,name):return json.loads(raw(role,name))
 def group(name,fn):
  try:facts[name]=fn()
  except Exception as e:errors.append({'group':name,'error':f'{type(e).__name__}: {e}'})
 def matches(rows,predicate):
  selected=[r for r in rows if predicate(r)]
  if len(selected)!=1:raise ValueError('expected one matching original, got '+str(len(selected)))
  return selected[0]
 def manifest():
  declared=summary.get('artifact_manifest',{})
  check(bool(declared),'runner persisted original artifact inventory')
  mismatches=[k for k,v in declared.items() if before.get(k)!=v]
  check(not mismatches,'declared original byte lengths and SHA256 all match')
  counts={}
  for role in ['server','client']:
   lines=(evidence/f'{role}-evidence.log').read_bytes().splitlines();decoded={};i=0
   while i<len(lines):
    m=re.fullmatch(rb'SECURE_ARTIFACT_BEGIN ([A-Za-z0-9_.-]+) ([0-9]+)',lines[i])
    if not m:i+=1;continue
    name=m[1].decode();size=int(m[2]);i+=1;chunks=[]
    while i<len(lines) and lines[i]!=b'SECURE_ARTIFACT_END '+m[1]:chunks.append(lines[i]);i+=1
    if i==len(lines):raise ValueError('unclosed original base64 block '+name)
    if name in decoded:raise ValueError('duplicate original block '+name)
    body=base64.b64decode(b''.join(chunks),validate=True)
    if len(body)!=size or body!=raw(role,name):raise ValueError('raw block differs from extracted artifact '+name)
    decoded[name]={'bytes':len(body),'sha256':sha(body)};i+=1
   actual={p.name for p in (evidence/f'{role}-artifacts').iterdir() if p.is_file()}
   check(bool(decoded) and set(decoded)==actual,role+' exact complete original base64-to-artifact set')
   counts[role]=len(decoded)
   (out/f'{role}-decoded-originals.json').write_text(json.dumps(decoded,indent=2)+'\n')
  return {'declared_artifacts':len(declared),'entire_original_tree':len(before),'base64_blocks':counts}
 def provenance():
  bad=[]
  for name,v in summary['source_inputs'].items():
   p=Path(name)
   if p.is_absolute() or '..' in p.parts:raise ValueError('unsafe source path')
   copied=(evidence/'source'/p).read_bytes()
   blob=subprocess.check_output(['git','show',summary['head']+':'+name],cwd=repo)
   if len(copied)!=v['bytes'] or sha(copied)!=v['sha256'] or copied!=blob:bad.append(name)
  check(not bad,'all frozen source originals equal exact recorded Git blobs')
  check(all(summary.get(k) is True for k in ['source_guard','head_guard','clean_guard','binary_guard']),'four original runtime guards true')
  check(summary['guest_exit_codes']=={'server':0,'client':0},'both actual QEMU exits zero, distinct from overall verdict')
  processes={}
  for role in ['server','client']:
   status=statuses(raw(role,'identity-helper.txt'))
   check(status['Uid'].split()==['0']*4,role+' actual helper UID0')
   check(raw(role,'executable-helper.txt').split()[0].decode()==summary['binary_sha256']['rustusb-helper'],role+' actual helper ELF receipt')
   processes[role]={}
   for e in [1,2,3]:
    status=statuses(raw(role,f'identity-agent-{e}.txt'));p=proc(raw(role,f'proc-agent-{e}.stat'))
    check(status['Uid'].split()==['2000']*4 and all(int(status[k],16)==0 for k in ['CapInh','CapPrm','CapEff','CapAmb']),f'{role} epoch{e} broker UID/capabilities')
    check(int(status['Pid'])==p['pid'] and p['starttime']>0,f'{role} epoch{e} same saved process generation')
    check(raw(role,f'executable-agent-{e}.txt').split()[0].decode()==summary['binary_sha256']['rustusb'],f'{role} epoch{e} actual broker ELF receipt')
    processes[role][e]=p
  return {'source_count':len(summary['source_inputs']),'head':summary['head'],'binaries':summary['binary_sha256'],'processes':processes}
 def terminal(role,held,current,root,expected_count):
  check(len(current['sessions'])==expected_count and len(root['terminals'])==expected_count,role+' exact retained original session/proof cardinality')
  check(root['schema_version']==2 and root['records']==root['exports']==[],role+' original root owners retired')
  check(all(row['server_id']==('qemu-server' if role=='client' else None) for row in root['terminals']),role+' exact root proof logical server scope')
  orig={s['id']:s for s in held['sessions']};now={s['id']:s for s in current['sessions']}
  check(set(orig)==set(now),role+' no held session history lost')
  results=[]
  for sid,s in orig.items():
   c=now[sid];check(projection(c)==projection(s),role+' original immutable capability '+sid)
   check(c['state']=='Disconnected' and c['terminal'] is True and c['cleanup']=='verified',role+' actual final cleanup fields '+sid)
   for a in s['attempts']:
    expected={'resource_id':a['resource_id'],'kind':a['kind'],'device_key':s['device_key'],'owner_uid':2000,'correlation':a['correlation'],'proof':{'native_import':'native_import_released','native_export':'native_export_restored','native_stub':'native_stub_available'}[a['kind']]}
    check(sum(row['terminal']==expected for row in root['terminals'])==1,role+' exact original root proof '+a['resource_id'])
    results.append({'session_id':sid,'resource_id':a['resource_id'],'kind':a['kind'],'proof':expected['proof'],'operation_error':c['operation_error'],'state':c['state'],'cleanup':c['cleanup'],'evidence':c['evidence']})
  return results
 def vhci(name,expected):
  rows=[r.split() for r in raw('client',name).decode().splitlines() if r.startswith(('hs ','ss '))]
  check(bool(rows) and all(int(r[2]) in [4,6] for r in rows) and sum(int(r[2])==6 for r in rows)==expected,'actual saved VHCI '+name)
  return rows
 def usb():
  baseline=data('server','host-before.json');keys=data('server','keys.json')
  check(len(baseline)==2 and {i['driver'] for d in baseline for i in d['interfaces']}=={'usbhid','ftdi_sio'},'actual original two physical driver baselines')
  rounds=[];ids=[];gens=[]
  for e in [1,2,3]:
   endpoints={}
   for role in ['server','client']:
    held=data(role,f'agent-held-{e}.json');root=data(role,f'root-held-{e}.json');stopped=data(role,f'agent-stopped-{e}.json');after=data(role,f'root-stopped-{e}.json')
    active=[s for s in held['sessions'] if not s['terminal']];expect=4 if role=='server' else 2
    check(len(active)==expect and len(root['terminals'])==(e-1)*expect,f'{role} epoch{e} current owners versus retained prior proof')
    check(all(s['device_key'] in keys and len(s['attempts'])==1 for s in active),f'{role} epoch{e} complete actual capability identities')
    actual_owner=proc(raw(role,f'proc-agent-{e}.stat'))
    for s in active:
     a=s['attempts'][0]
     if role=='client':
      r=matches(root['records'],lambda x:x['channel_id']==a['resource_id']);owner=r['owner']
      check(r['target']['server_id']=='qemu-server' and r['target']['device_key']==s['device_key'],f'client epoch{e} exact approved target')
     else:
      export=matches(root['exports'],lambda x:x['registration']['device_key']==s['device_key'])
      r=export if s['role']=='export' else export['stub'];owner=export['owner']
      check(r['lease_id' if s['role']=='export' else 'channel_id']==a['resource_id'],f'server epoch{e} exact received capability')
     check(r['correlation']==a['correlation'],f'{role} epoch{e} application/root exact attempt binding')
     check(owner['uid']==2000 and owner['pid']==actual_owner['pid'] and owner['starttime']==actual_owner['starttime'],f'{role} epoch{e} actual received root owner PID/starttime')
    endpoints[role]=terminal(role,held,stopped,after,e*expect)
   current=data('server',f'host-held-{e}.json');restored=data('server',f'host-restored-{e}.json')
   dynamic={'configuration','num_interfaces','configuration_bytes','count_bytes','interfaces','device_driver','usbip_status'}
   check([{k:v for k,v in d.items() if k not in dynamic} for d in current]==[{k:v for k,v in d.items() if k not in dynamic} for d in baseline],f'epoch{e} actual original physical generation remains')
   check(all(d['device_driver']=='usbip-host' and d['usbip_status']==2 for d in current),f'epoch{e} actual host USED state, separate from stored receipt')
   check(restored==baseline,f'epoch{e} exact whole original host baseline restored')
   for original,observed in zip(baseline,current,strict=True):
    check(observed['configuration']==original['configuration'] and observed['num_interfaces']==original['num_interfaces'] and observed['interfaces']==[dict(i,driver='') for i in original['interfaces']],f'epoch{e} complete actual configuration/IF alternates')
   local=data('client',f'local-identities-{e}.json');records=data('client',f'client-receipts-{e}.json')['records'];rows=vhci(f'vhci-held-{e}.txt',2)
   for index,key in enumerate(keys):
    r=matches(records,lambda x:x['target']['device_key']==key);v=r['state']['receipt']['session'];kind=['hid','serial'][index]
    check(r['state']['phase']=='observed' and v['status']==6 and v['local_bus_id']==local[kind],f'epoch{e} actual local enumeration {kind}')
    g=v['local_device_generation'];check(local[kind+'_generation']==f"{g['filesystem_device']}:{g['inode']}",f'epoch{e} saved actual generation {kind}')
    check(any(int(row[1])==v['port'] and row[6]==v['local_bus_id'] and int(row[4],16)==v['remote_device_id'] for row in rows),f'epoch{e} actual port/devid/local mapping {kind}')
    ids.append(r['channel_id']);gens.append(json.dumps(g,sort_keys=True))
   vhci(f'vhci-stopped-{e}.txt',0);gone=data('client',f'local-gone-{e}.json')
   check(gone['hid']==local['hid'] and gone['serial']==local['serial'] and gone['hid_exists'] is gone['serial_exists'] is False,f'epoch{e} recorded actual original local generation disappearance')
   rx=f'RUSTUSB-RX-{e}-0001:0123456789abcdef\n'.encode();tx=f'RUSTUSB-TX-{e}-0001:fedcba9876543210\n'.encode()
   check(raw('client',f'rx-{e}.bin')==rx and raw('client',f'tx-{e}.bin')==tx and (evidence/f'ftdi-host-injected-{e}.bin').read_bytes()==rx,f'epoch{e} actual35B per direction')
   b=raw('client',f'input-{e}.bin');check(len(b)>0 and len(b)%24==0,f'epoch{e} complete actual evdev ABI records')
   events=[struct.unpack('<qqHHi',b[n:n+24])[2:] for n in range(0,len(b),24)]
   check((1,e+1,1) in events and (1,e+1,0) in events,f'epoch{e} actual KEY{e} down/up')
   rounds.append({'epoch':e,'key_code':e+1,'serial_rx_bytes':len(rx),'serial_tx_bytes':len(tx),'terminal_proofs':endpoints})
  check(len(set(ids))==len(set(gens))==6,'fresh actual imported capabilities and generations across three epochs')
  check((evidence/'ftdi-host-rx.bin').read_bytes()==b''.join(f'RUSTUSB-TX-{e}-0001:fedcba9876543210\n'.encode() for e in [1,2,3]),'independent host receives exact ordered105FTDIbytes')
  return rounds
 def gate():
  stages=['before','before-stop','after-clean','before-release'];gates=[];agents=[]
  for stage in stages:
   d=data('server',f'gate-{stage}.json');p=proc(raw('server',f'proc-gate-{stage}.stat'));a=proc(raw('server',f'proc-agent-{stage}.stat'))
   check(statuses(raw('server',f'proc-gate-{stage}.status'))['Uid'].split()==['0']*4,'actual root flock owner '+stage)
   check(statuses(raw('server',f'proc-agent-{stage}.status'))['Uid'].split()==['2000']*4,'actual broker remains alive '+stage)
   check(p['pid']==d['pid'] and a['pid']!=p['pid'] and p['state']!='Z' and a['state']!='Z','distinct live process generations '+stage)
   check(raw('server',f'proc-gate-{stage}.exe.sha256').split()[0].decode()==summary['payload_sha256']['server']['usr/bin/flock'],'actual selected flock ELF '+stage)
   check(raw('server',f'proc-gate-{stage}.cmdline').split(b'\0')[:-1]==[b'/usr/bin/flock',b'-x',b'/var/log/rustusb/agent/access.lock',b'/bin/sh',b'/logging-gate.sh',d['nonce'].encode()],'actual independent gate command and nonce '+stage)
   lock=d['lock'].split(':');dev,ino=int(lock[0]),int(lock[1]);check(lock[2:]==['2000','600','1'],'actual protected access inode '+stage)
   fds=[json.loads(l) for l in raw('server',f'proc-gate-{stage}-fds.jsonl').splitlines()];fd=matches(fds,lambda r:r['fd']==d['fd'])
   check(fd['target']=='/var/log/rustusb/agent/access.lock' and fd['stat'].split(':')[:4]==lock[:4],'actual independent flock FD maps same inode '+stage)
   locklines=[line.split() for line in raw('server',f'gate-{stage}-proc-locks.txt').decode().splitlines()]
   direct=[r for r in locklines if len(r)==8 and r[1:5]==['FLOCK','ADVISORY','WRITE',str(p['pid'])]]
   found=[r for r in direct if tuple(int(v,16) for v in r[5].split(':')[:2])==(os.major(dev),os.minor(dev)) and int(r[5].split(':')[2])==ino and r[6:]==['0','EOF']]
   check(len(found)==1,'kernel reports gate EX on exact inode '+stage)
   gates.append({'receipt':d,'process':p});agents.append(a)
  first=gates[0]['receipt']
  check(all(all(g['receipt'][k]==first[k] for k in ['nonce','pid','fd','lock','segment']) for g in gates),'same nonce/FD/lock/header identities through hold')
  check(len({(g['process']['pid'],g['process']['starttime']) for g in gates})==len({(a['pid'],a['starttime']) for a in agents})==1,'same actual gate and broker generations across cleanup')
  a0=proc(raw('server','proc-agent-2.stat'));check((a0['pid'],a0['starttime'])==(agents[0]['pid'],agents[0]['starttime']),'gate blocks original epoch2 broker, not replacement')
  check(all(raw('server',f'gate-{stage}-segment.ndjson')==raw('server','gate-before-segment.ndjson') for stage in stages),'actual log segment bytes cannot advance while gated')
  threads=[json.loads(l) for l in raw('server','threads-busy.jsonl').splitlines()];blocked=[]
  for r in threads:
   c=r['syscall'].split()
   if len(c)>2 and c[0]=='73':
    check(str(int(c[1],16))==r['fd'] and r['target']=='/var/log/rustusb/agent/access.lock' and r['stat']==first['lock'],'actual syscall FD and file identity')
    blocked.append({'tid':int(r['tid']),'comm':r['comm'],'operation':int(c[2],16),'wchan':r['wchan']})
  check(sum(r['operation']==2 and r['comm']=='rustusb-log-wri' for r in blocked)==1 and sum(r['operation']==1 and r['comm']=='rustusb-log-rea' for r in blocked)==2,'actual separate one writerEX and two readerSH waiting on gate')
  before_stop=data('server','agent-held-2.json');clean=data('server','agent-clean-held.json');root=data('server','root-clean-held.json')
  proofs=terminal('server',before_stop,clean,root,8)
  check(data('server','host-clean-held.json')==data('server','host-before.json'),'exact original host baseline before release')
  check(all(j['state']=='finished' for j in clean['jobs']),'all business jobs actually finished before release')
  control=data('server','agent-control-after-clean.json');check(control['control_absent'] is True and control['pid']==a0['pid'],'actual removed service endpoint while original agent lives')
  maincall=raw('server','agent-main-after-clean.syscall').decode();check(maincall.split()[0]=='202','observed main-thread futex, not a uniquely identified join handle')
  client=data('client','agent-eof-held.json');clientroot=data('client','root-eof-held.json')
  clientproofs=terminal('client',data('client','agent-held-2.json'),client,clientroot,4);vhci('vhci-eof-held.txt',0)
  gone=data('client','local-gone-eof-held.json');local=data('client','local-identities-2.json');check(gone['hid']==local['hid'] and gone['serial']==local['serial'] and gone['hid_exists'] is gone['serial_exists'] is False,'client recorded original generations Gone before gate release')
  times={k:uptime(raw('server',f'{k}.uptime')) for k in ['gate-actual-held','term-sent','gate-release-request','gate-actual-released','gate-reaped']}
  check(times['gate-actual-held']<=uptime(gates[0]['receipt']['uptime'])<=uptime(gates[1]['receipt']['uptime'])<=times['term-sent']<=uptime(control['uptime'])<=uptime(gates[2]['receipt']['uptime'])<=uptime(gates[3]['receipt']['uptime'])<=times['gate-release-request']<=times['gate-actual-released']<=times['gate-reaped'],'server monotonic held/TERM/cleanup/release/reap order')
  check(raw('server','gate-process.stdout').decode().splitlines()==['LOG_GATE_HELD nonce='+first['nonce'],'LOG_GATE_RELEASED nonce='+first['nonce']] and raw('server','wait-gate.txt')==b'0\n','exact nonce handshake and actual gate reap0')
  controls=[json.loads(l) for l in (evidence/'host-control.jsonl').read_bytes().splitlines()];marks=[r['mark'] for r in controls]
  check(marks.index('fault2')<marks.index('check-closed2')<marks.index('release-gate')<marks.index('start-3'),'host control ordering ties independent guest observations without comparing their clocks')
  for role in ['server','client']:
   for e in [1,2,3]:check(raw(role,f'wait-agent-{e}.txt')==(b'1\n' if role=='server' and e==2 else b'0\n'),f'actual wait {role} epoch{e}')
   check(raw(role,'wait-helper.txt')==b'0\n',role+' actual helper reap0')
  return {'gate_nonce':first['nonce'],'gate_pid':first['pid'],'gate_starttime':gates[0]['process']['starttime'],'broker_pid':a0['pid'],'broker_starttime':a0['starttime'],'inode':first['lock'],'blocked_syscalls':blocked,'server_uptime':{k:str(v) for k,v in times.items()},'server_original_proofs_before_release':proofs,'client_original_proofs_before_release':clientproofs,'agent2_final_stderr':raw('server','agent-2.log').decode(),'join_scope':'Direct observations: removed service endpoint, original process alive/main futex, blocked actual logger syscalls. Outer logger join attribution uses fixed source ordering; futex202 does not independently identify a Rust join handle.'}
 def original_failure():
  current=[]
  for role in ['server','client']:
   for e in [1,2,3]:
    held=data(role,f'agent-held-{e}.json');active={s['id'] for s in held['sessions'] if not s['terminal']};stopped=data(role,f'agent-stopped-{e}.json')
    current += [{'role':role,'epoch':e,'session_id':s['id'],'session_role':s['role'],'operation_error':s['operation_error'],'cleanup':s['cleanup'],'state':s['state'],'evidence':s['evidence'],'attempts':s['attempts']} for s in stopped['sessions'] if s['id'] in active]
  return {'original_result':summary['result'],'original_error':summary.get('error'),'all_current_session_outcomes':current,'interpretation':'No rewrite of the original verdict. Operation error and actual cleanup proof are separate recorded dimensions. Source-based explanation belongs to a separate review; this report does not manufacture a new QEMU PASS.'}
 for name,fn in [('original_integrity',manifest),('source_and_process_provenance',provenance),('three_epoch_usb',usb),('removable_gate_ordering',gate),('original_failure_retained',original_failure)]:group(name,fn)
 after=inventory(evidence);(out/'originals-after.json').write_text(json.dumps(after,indent=2)+'\n');checks.append({'check':'all original bytes unchanged by review','pass':before==after})
 report={'schema_version':1,'method':'New standalone stdlib parser reading original byte streams/state/OS receipts; original harness author, independent method but not independent implementation or independent author. No original runner/verifier imports or calls.','evidence':str(evidence),'original_run_result':summary['result'],'original_run_error':summary.get('error'),'completed_utc':summary['completed_utc'],'diagnostic_review_result':'CONSISTENT_ORIGINALS' if not errors and all(r['pass'] for r in checks) else 'REVIEW_DISCREPANCY','originals_unchanged':before==after,'groups':facts,'errors':errors,'checks':checks,'check_count':len(checks),'scope':'USB and removable logger gate ordering only. N separately reviews typed logging, permissions, pages, audit projection and rotation. Diagnostic checks are not USB case counts. Original FAIL remains FAIL; no Cargo/QEMU/CLI/verifier execution, source edits, root coordination or device mutation.'}
 (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 manifest_out={str(p.relative_to(out)):{'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in out.rglob('*') if p.is_file()}
 (out/'review-manifest.json').write_text(json.dumps(manifest_out,indent=2)+'\n')
 print(json.dumps({'report':str(out/'report.json'),'original_result':summary['result'],'review_result':report['diagnostic_review_result'],'errors':errors,'unchanged':before==after,'checks':len(checks)}))
 return 0 if report['diagnostic_review_result']=='CONSISTENT_ORIGINALS' else 1

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--evidence',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--repo',type=Path,default=Path('/home/ubuntu/github/usbip-client'));a=p.parse_args();raise SystemExit(review(a.evidence.resolve(),a.output.resolve(),a.repo.resolve()))
