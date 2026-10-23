#!/usr/bin/env python3
"""Independent offline legacy negative review. Does not import project runners."""
from pathlib import Path
import collections,datetime,hashlib,json,os,re,struct,subprocess
REPO=Path('/home/ubuntu/github/usbip-client')
BASE=Path('/tmp/rustusb-autorestore-negative-4565f7a-20261023')
RUN=BASE/'legacy-schema1'
OUT=Path('/tmp/rustusb-autorestore-legacy-independent-review-20261023.json')
HEAD='4565f7aaddb5a9377c15261a288ca9c3a57eebe5'
OLD='933e720531fd96b7a23616ed687d59b816e0ce09'
checks=[]
def sha(b):return hashlib.sha256(b).hexdigest()
def read(p):return json.loads(p.read_text())
def metadata(p):
 b=p.read_bytes();return {'bytes':len(b),'sha256':sha(b)}
def check(k,b,detail=None):
 checks.append({'id':k,'result':'PASS' if b else 'FAIL','detail':detail})
 if not b:print('FAIL',k,detail)
def rows(lines,prefix):return [json.loads(x[len(prefix):]) for x in lines if x.startswith(prefix)]
def rawblock(lines,begin,end):
 if lines.count(begin)!=1 or lines.count(end)!=1:raise ValueError('nonunique block: '+begin)
 a,b=lines.index(begin),lines.index(end)
 if a>=b:raise ValueError('unordered block: '+begin)
 return '\n'.join(lines[a+1:b])
def block(lines,name):return [json.loads(x) for x in rawblock(lines,'RUSTUSB_JOURNAL_'+name+'_BEGIN','RUSTUSB_JOURNAL_'+name+'_END').splitlines()]
def strip(x):return re.sub(r'\x1b\[[0-9;]*m','',x)
def index(lines,prefix):
 a=[i for i,l in enumerate(lines) if l.startswith(prefix)]
 if len(a)!=1:raise ValueError('nonunique index '+prefix)
 return a[0]
def zero(s):return s['buffer_bytes_reserved']==0 and len(s['devices'])==2 and all(d['state']=='available' and d['cleanup_verified'] and not d['leased'] and d['pending_urbs']==d['queued_urbs']==d['usbfs_inflight']==0 for d in s['devices'])
def identity(v,orig):return v['pid']>1 and v['starttime']>0 and v['uids']==[1000]*4 and v['exe']=='/usr/bin/rustusb' and all(v[x]==0 for x in ['cap_effective','cap_permitted','cap_ambient']) and len(v['fds'])==2 and len({f['fd'] for f in v['fds']})==2 and {f['node']:f['identity'] for f in v['fds']}=={d['node']:d['node_identity'] for d in orig}
freeze=read(BASE/'freeze.json');summary=read(RUN/'summary.json');sequence=read(BASE/'sequence.json')
seq=[r for r in sequence['runs'] if r['name']=='legacy-schema1']
check('completed_selected_sequence',len(seq)==1 and seq[0]['result']=='PASS' and seq[0]['exit_code']==0 and seq[0]['head']==HEAD and all(seq[0][k] for k in ['source_guard','head_guard','worktree_clean','legacy_guard']))
old=read(RUN/'legacy/manifest.json');old_elf=Path(old['binary']['path'])
paths=[BASE/'freeze.json',old_elf]+[p for p in sorted(RUN.rglob('*')) if p.is_file()]
before={str(p):metadata(p) for p in paths}
check('exact_clean_frozen_head',freeze['head']==HEAD and not freeze['working_tree_dirty'] and len(freeze['files'])==155)
blobs={}
for p,w in freeze['files'].items():
 b=subprocess.check_output(['git','show',HEAD+':'+p],cwd=REPO);blobs[p]=b
 check('current_git:'+p,len(b)==w['bytes'] and sha(b)==w['sha256'])
check('actual_legacy_fixed_manifest',old==freeze['legacy_manifest'] and old['git_commit']==OLD and old['result']=='PASS' and old['exit_code']==0 and old['source_guard_passed'] and old['head_guard_passed'] and not old['working_tree_dirty_before'] and not old['working_tree_dirty_after'] and len(old['files'])==109)
legacy_blobs={}
for p,w in old['files'].items():
 b=subprocess.check_output(['git','show',OLD+':'+p],cwd=REPO);legacy_blobs[p]=b
 check('legacy_git_and_copied_source:'+p,b==(RUN/'legacy/source'/p).read_bytes() and len(b)==w['bytes'] and sha(b)==w['sha256'])
check('existing_actual_old_ELF',old_elf.read_bytes()[:4]==b'\x7fELF' and metadata(old_elf)=={k:old['binary'][k] for k in ['bytes','sha256']})
check('legacy_build_isolated_locked',old['command'][:5]==['cargo','build','-p','rustusb-daemon','--all-features'] and all(x in old['command'] for x in ['--release','--locked','--target-dir']) and old['worktree']!=str(REPO) and str(REPO/'target') not in old['command'])
# Recreate exact nested fingerprint from immutable bytes; no project function calls.
native=['Cargo.toml','Cargo.lock','scripts/qemu/guest-init.sh','scripts/qemu/run-native-qemu.py']+[p for p in blobs if p.startswith('crates/') and (p.endswith('.rs') or Path(p).name=='Cargo.toml')]
h=hashlib.sha256()
for p in sorted(native,key=lambda p:Path(p).parts):
 n=p.encode();b=blobs[p];h.update(len(n).to_bytes(8,'big'));h.update(n);h.update(len(b).to_bytes(8,'big'));h.update(b)
h=hashlib.sha256(h.digest())
for n in ['run-physical-qemu.py','guest-physical-init.sh','physical-wire-probe.rs']:
 b=blobs['scripts/qemu/'+n];h.update(n.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
for p in sorted((p for p in blobs if p.startswith('vendor/') and (p.endswith('.rs') or Path(p).name in ['Cargo.toml','Cargo.lock'])),key=lambda p:Path(p).parts):
 b=blobs[p];h.update(p.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
for names in [['run-journal-qemu.py','guest-journal-init.sh'],['run-autorestore-qemu.py','guest-autorestore-fragments.sh','run-autorestore-negative-qemu.py','guest-autorestore-negative-fragments.sh','usbfs-external-claim-probe.c']]:
 h=hashlib.sha256(h.digest())
 for n in names:
  b=blobs['scripts/qemu/'+n];h.update(n.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
h.update((RUN/'legacy/manifest.json').read_bytes());h.update(old_elf.read_bytes())
for p in sorted(legacy_blobs):h.update(legacy_blobs[p])
fingerprint=h.hexdigest()
check('independent_nested_fingerprint',summary['source_fingerprint_sha256']==summary['source_fingerprint_after_sha256']==fingerprint and summary['source_guard_passed'],fingerprint)
check('summary_complete_and_clean',summary['result']=='PASS' and summary['evidence_collection_complete'] and summary['git_commit']==summary['git_commit_after']==HEAD and not summary['working_tree_dirty'] and not summary['working_tree_dirty_after'] and summary['working_tree_guard_passed'])
check('two_actual_guest_exit_zero',summary['guest_exit_codes']=={'server':0,'client':0})
check('current_recorded_binary_chain',summary['binary_sha256']==summary['guest_binary_sha256']==summary['binary_after_sha256'] and summary['binary_guard_passed'])
check('ordinary_host_kernel_kvm',summary['host_uid']==1000 and summary['host_architecture']=='x86_64' and summary['kernel']=='6.8.0-106-generic' and summary['acceleration']=='kvm')
check('mode_and_verdict_boundary',summary['scenario']=='legacy-schema1' and summary['tail_automatic_recovery'] is False and summary['original_journal_verifier_used'] is False and summary['positive_verifier_used'] is False)
for n,w in summary['script_sha256'].items():
 b=(RUN/'source'/n).read_bytes();check('frozen_script:'+n,sha(b)==w and b==blobs['scripts/qemu/'+n])
comp=read(RUN/'negative-composition.json');generated=(RUN/'source/guest-journal-init.sh').read_text()
for group in ['positive','negative']:
 for i,s in enumerate(comp['composition'][group]):
  check('unique_anchor_'+group+'_'+str(i),s['matches']==1 and generated.count(s['before'])==1)
  generated=generated.replace(s['before'],s['after'],1)
check('independent_generated_init',generated.encode()==(RUN/'source/generated-negative-init.sh').read_bytes() and sha(generated.encode())==comp['generated_init_sha256'])
check('legacy_manifest_payload_metadata',comp['legacy_manifest_sha256']==sha((RUN/'legacy/manifest.json').read_bytes()) and comp['binaries']['external_claim_probe'] is None and comp['binaries']['legacy_commit']==OLD)
frag=(RUN/'source/guest-autorestore-negative-fragments.sh').read_text()
readonly=frag.split('negative_readonly() {',1)[1].split('\n}\n',1)[0]
check('pure_observation_source',all(x not in readonly for x in ['physical-pending','physical-reconcile','/usr/bin/rustusb','/run/rustusb/control.sock']))
check('manual_bind_and_readonly_reconcile_source',frag.count(' > /sys/bus/usb/drivers/')==2 and frag.index('negative_stop\n        printf')<frag.index(' > /sys/bus/usb/drivers/usbhid/bind')<frag.index('negative_watch manual-restored-still-pending 500 original')<frag.index('--json physical-reconcile'))
check('one_kill_wait_and_root_control',frag.count('wait "$KILL_PID"')==1 and frag.index('negative_stop\n    server_identity')<frag.index('kill -KILL "$KILL_PID"') and '[ "$neg_code" = 137 ] && [ ! -e "/proc/$KILL_PID" ]' in frag)
logs={r:(RUN/(r+'-evidence.log')).read_text() for r in ['server','client']};S=logs['server'].splitlines();C=logs['client'].splitlines()
for role,lines in [('server',S),('client',C)]:
 kernel=(RUN/(role+'-serial.log')).read_text()
 check(role+'_completion_and_channel',lines.count(f'RUSTUSB_JOURNAL_COLLECTION_COMPLETE role={role}')==1 and lines.count(f'RUSTUSB_JOURNAL_RESULT: PASS role={role}')==1 and lines.count(f'RUSTUSB_JOURNAL_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0')==1 and 'RUSTUSB_JOURNAL_' not in kernel and 'RUSTUSB_AUTONEG_' not in kernel)
 check(role+'_no_failure',(RUN/(role+'-qemu.stderr')).read_bytes()==b'' and all(x not in logs[role]+kernel for x in ['RUSTUSB_JOURNAL_RESULT: FAIL','Kernel panic','BUG:','Oops:','general protection fault']))
records=block(S,'RECORDS');obs=block(S,'OBSERVATIONS');waits=block(S,'WAITS');admin=block(S,'ADMIN');clients=block(C,'CLIENT')
check('immediate_records_and_waits_preserved',rows(S,'RUSTUSB_JOURNAL_RECORD ')==records and rows(S,'RUSTUSB_JOURNAL_WAIT ')==waits)
expected=['initial','normal-ready','normal-pending','normal-closed','reopened-ready','reopened-closed','killed-ready','killed-pending','negative-observed','negative-new-grant-refused','manual-restored','tail-retired','recovered-ready','recovered-pending','recovered-closed']
check('complete_ordered_lifecycle',[r['label'] for r in records]==expected)
r={x['label']:x for x in records};o={x['label']:x for x in obs};orig=r['initial']['sysfs'];byid={d['device_id']:d for d in orig}
check('complete_original_fixtures',len(orig)==2 and [d['bus_id'] for d in orig]==['1-1','1-2'] and [d['interfaces'] for d in orig]==[[{'number':0,'alternate':0,'driver':'usbhid'}],[{'number':0,'alternate':0,'driver':'ftdi_sio'}]] and all(d['configuration']==1 and d['node_owner_mode']=='0:600' for d in orig))
null=json.loads(json.dumps(orig));claimed=json.loads(json.dumps(orig))
for d in null:d['interfaces'][0]['driver']=None
for d in claimed:d['interfaces'][0]['driver']='usbfs'
meta=rows(S,'RUSTUSB_AUTONEG_METADATA ');check('one_metadata',len(meta)==1);meta=meta[0]
check('payload_metadata_and_no_external_actor',meta['binaries']==comp['binaries'] and not meta['tail_automatic_recovery'] and 'RUSTUSB_EXTERNAL_C ' not in logs['server']+logs['client'])
diskraw={}
for row in records:
 label=row['label'];raw=rawblock(S,f'RUSTUSB_JOURNAL_DISK_BEGIN label={label}',f'RUSTUSB_JOURNAL_DISK_END label={label}')+'\n';diskraw[label]=raw
 p=json.loads(rawblock(S,f'RUSTUSB_JOURNAL_PENDING_BEGIN label={label}',f'RUSTUSB_JOURNAL_PENDING_END label={label}'))
 check(label+'_actual_disk_bytes_api',json.loads(raw)==row['journal'] and sha(raw.encode())==row['journal_sha256'] and p==row['pending'])
 check(label+'_protected',row['journal_directory_owner_mode']=='0:700' and row['journal_file_owner_mode']=='0:600')
 entries=row['journal']['records'];version=2 if label.startswith('recovered-') else 1
 check(label+'_storage_version_and_old_IPC',row['journal']['schema_version']==version and p['schema_version']==1 and p['next_after'] is None and p['records']==[{k:v for k,v in e.items() if k!='recovery'} for e in entries])
 if version==1:check(label+'_no_silent_auth_promotion',all(set(e)=={'lease_id','owner','baseline'} for e in entries) and set(p)=={'schema_version','records','next_after'})
 else:check(label+'_explicit_new_auth',all(e['recovery']=='connect_original_drivers' for e in entries) and p['storage_schema_version']==2 and p['recovery_authorizations']==[{'lease_id':e['lease_id'],'recovery':e['recovery']} for e in entries])
for label in ['initial','normal-closed','reopened-closed','tail-retired','recovered-closed']:
 check(label+'_full_original_and_empty',r[label]['sysfs']==orig and r[label]['journal']['records']==[])
leases={}
for epoch in ['normal','reopened','killed','recovered']:
 entries=r[epoch+'-ready']['journal']['records'];owner=o[epoch+'-ready']['identity'];leases[epoch]=[e['lease_id'] for e in entries]
 check(epoch+'_real_ordinary_owner',identity(owner,orig) and len(entries)==2 and zero(o[epoch+'-ready']['snapshot']) and o[epoch+'-ready']['sysfs']==orig)
 for e in entries:
  d=byid[e['baseline']['device_id']];ni=d['node_identity'].split(':');g=list(map(int,d['generation'].split(':')))
  baseline={'device_id':d['device_id'],'bus_id':d['bus_id'],'receipt':{'sysfs_device':g[0],'sysfs_inode':g[1],'node_device':os.makedev(int(ni[2],16),int(ni[3],16)),'node_inode':int(ni[1]),'usb_device_number':d['devnum'],'bus_number':1},'configuration':1,'interfaces':d['interfaces']}
  check(epoch+'_saved_complete_baseline_'+d['bus_id'],e['baseline']==baseline and e['owner']=={'uid':1000,'pid':owner['pid'],'start_time':owner['starttime'],'boot_id':meta['boot_id']})
 if epoch!='reopened':
  pending=o[epoch+'-pending'];s=pending['snapshot']
  check(epoch+'_immutable_at_real_pending',r[epoch+'-ready']['journal']==r[epoch+'-pending']['journal'] and pending['identity']==owner and pending['sysfs']==claimed and s['buffer_bytes_reserved']>0 and len(s['devices'])==2 and all(d['state']=='leased' and d['leased'] and d['pending_urbs']>0 and d['usbfs_inflight']>0 for d in s['devices']))
check('eight_unique_grants',len(set(sum(leases.values(),[])))==8)
helpers=rows(S,'RUSTUSB_AUTONEG_HELPER_START ');policies=rows(S,'RUSTUSB_AUTONEG_POLICY ');helperlogs=rows(S,'RUSTUSB_AUTONEG_HELPER_LOG ')
check('three_distinct_actual_helpers',[h['epoch'] for h in helpers]==[1,2,3] and len({h['pid'] for h in helpers})==3 and all(h['uids']==[0]*4 and h['starttime']>0 for h in helpers))
check('old_then_current_actual_exe_SHA',[h['exe'] for h in helpers]==['/usr/libexec/rustusb-helper-legacy','/usr/bin/rustusb-helper','/usr/bin/rustusb-helper'] and [h['sha256'] for h in helpers]==[old['binary']['sha256']]+[summary['binary_sha256']['rustusb-helper']]*2)
check('three_policy_epochs',[p['helper_epoch'] for p in policies]==[1,2,3])
for i,p in enumerate(policies):
 expected_policy={'allowed_uids':[1000],'export_device_ids':[],'physical_device_ids':list(byid),'peers':[],'trusted_network':True}
 if i:expected_policy['physical_connect_restore_device_ids']=list(byid)
 check('policy_'+str(i+1)+'_actual_bytes_scope',sha(p['raw'].encode())==p['sha256'] and json.loads(p['raw'])==p['policy']==expected_policy)
for i,h in enumerate(helperlogs):
 check('helper_checkpoint_'+str(i)+'_no_automatic_operation',h['helper']==helpers[h['helper']['epoch']-1] and h['operations']==[] and 'physical_auto_restore' not in strip(h['log']))
check('complete_helper_checkpoints',[(h['helper']['epoch'],h['label']) for h in helperlogs]==[(1,'before-stop'),(2,'rejected'),(2,'after-refused-grant'),(2,'before-stop'),(3,'manual-restored-still-pending'),(3,'tail-manual-retired'),(3,'final')])
sw=[w for w in waits if w['event']=='server_wait'];hw=[w for w in waits if w['event']=='negative_helper_wait'];wb={w['label']:w for w in sw}
check('all_real_waits',len(waits)==7 and [w['label'] for w in sw]==['normal','reopened','killed','negative-new-grant','recovered'] and len(hw)==2 and all(w['pid']==helpers[i]['pid'] and w['starttime']==helpers[i]['starttime'] and w['epoch']==i+1 and w['signal']==15 and w['wait_code']==0 for i,w in enumerate(hw)))
events={e:[json.loads(x) for x in rawblock(S,f'RUSTUSB_JOURNAL_EVENTS_BEGIN epoch={e}',f'RUSTUSB_JOURNAL_EVENTS_END epoch={e}').splitlines()] for e in wb}
for e in ['normal','reopened','recovered']:check(e+'_actual_clean_exit',wb[e]['wait_code']==0 and events[e][-1]['event']=='stopped' and sum(x['event']=='stopped' for x in events[e])==1 and zero(events[e][-1]['snapshot']))
kill=wb['killed'];ki=o['killed-pending']['identity']
check('actual_unique_wait137_no_fake_terminal',kill=={'label':'killed','event':'server_wait','wait_code':137,'signal':9,'signal_count':1,'sender_uid':1000,'pid':ki['pid'],'starttime':ki['starttime'],'final_backend_snapshot':None,'final_backend_snapshot_observable':False} and not any(e['event']=='stopped' for e in events['killed']))
check('helper_stopped_before_exact_KILL',index(S,'RUSTUSB_JOURNAL_WAIT {"event":"negative_helper_wait","epoch":1')<index(S,'RUSTUSB_JOURNAL_SIGNAL:') and [x for x in S if x.startswith('RUSTUSB_JOURNAL_SIGNAL:')]==[f"RUSTUSB_JOURNAL_SIGNAL: PASS signal=9 count=1 sender_uid=1000 pid={ki['pid']} starttime={ki['starttime']}"])
refusal=rawblock(S,'RUSTUSB_JOURNAL_STDERR_BEGIN epoch=negative-new-grant','RUSTUSB_JOURNAL_STDERR_END epoch=negative-new-grant')
check('new_grant_actual_pending_guard',wb['negative-new-grant']['wait_code']==1 and not any(e['event']=='ready' for e in events['negative-new-grant']) and 'unresolved physical grant blocks new authorization; inspect physical-pending and reconcile the original baseline' in refusal)
pure=rows(S,'RUSTUSB_AUTONEG_READONLY ');windows=rows(S,'RUSTUSB_AUTONEG_WINDOW ');primary=rows(S,'RUSTUSB_AUTONEG_REJECTION_PRIMARY ')
check('all_pure_observation_order',[p['sequence'] for p in pure]==list(range(1,len(pure)+1)) and all(a['tick']<=b['tick'] for a,b in zip(pure,pure[1:])))
for p in pure:
 expected_sysfs=orig if p['label']=='manual-restored-still-pending' else null
 expected_helper=None if p['label']=='killed-with-helper-stopped' else helpers[2] if p['label']=='manual-restored-still-pending' else helpers[1]
 check('pure_'+str(p['sequence'])+'_same_original_record_bytes',p['journal_raw']==diskraw['killed-pending'] and json.loads(p['journal_raw'])==p['journal']==r['killed-pending']['journal'] and sha(p['journal_raw'].encode())==p['journal_sha256'])
 check('pure_'+str(p['sequence'])+'_full_state_and_identity',p['sysfs']==expected_sysfs and p['helper']==expected_helper and p['external'] is None and not p['pending_api_called'] and p['journal_directory_owner_mode']=='0:700' and p['journal_file_owner_mode']=='0:600')
check('bounded_windows_exact_labels',[w['label'] for w in windows]==['rejected','manual-restored-still-pending'])
for w in windows:
 values=[p for p in pure if p['label']==w['label']];minimum=2000 if w['label']=='rejected' else 500
 check(w['label']+'_complete_elapsed_window',len(values)>1 and w['minimum_centiseconds']==minimum and w['end_tick']-w['start_tick']>=minimum and values[-1]['tick']-w['start_tick']>=minimum and values[0]['tick']>=w['start_tick'])
rejected=[p for p in pure if p['label']=='rejected']
check('pure_rejection_primary_before_pending_and_new_grant',len(primary)==1 and primary[0]==rejected[-1] and index(S,'RUSTUSB_AUTONEG_REJECTION_PRIMARY ')<index(S,'RUSTUSB_JOURNAL_PENDING_BEGIN label=negative-observed')<index(S,'RUSTUSB_JOURNAL_STDERR_BEGIN epoch=negative-new-grant'))
for label in ['negative-observed','negative-new-grant-refused','manual-restored']:check(label+'_old_disk_bytes_unchanged',diskraw[label]==diskraw['killed-pending'])
check('manual_baseline_restored_still_unretired',r['manual-restored']['sysfs']==orig and len(r['manual-restored']['journal']['records'])==2)
check('exact_two_root_readonly_reconciliations',len(admin)==2 and {a['lease_id'] for a in admin}==set(leases['killed']) and all(a['phase']=='legacy-manual-retired' and a['uid']==0 and a['exit_code']==0 and json.loads(a['stdout'])=={'lease_id':a['lease_id'],'observation':'restored','status':'retired'} for a in admin))
check('no_automatic_primary_or_C_actor',rows(S,'RUSTUSB_AUTONEG_RECOVERY_PRIMARY ')==[] and rows(S,'RUSTUSB_EXTERNAL_C ')==[])
check('three_raw_io_cycles',[c['cycle'] for c in clients]==[1,2,3]);frames=[];TX=b''
for c in clients:
 n=c['cycle'];rx=f'RUSTUSB-RX-{n}-0001:0123456789abcdef\n'.encode();tx=f'RUSTUSB-TX-{n}-0001:fedcba9876543210\n'.encode();TX+=tx
 check('cycle'+str(n)+'_exact_bidirectional_FTDI',bytes.fromhex(c['rx_hex'])==rx and bytes.fromhex(c['tx_hex'])==tx and len(rx)==len(tx)==35)
 ev=bytes.fromhex(c['input_event_hex']);check('cycle'+str(n)+'_raw_evdev_size',bool(ev) and len(ev)%24==0)
 f=list(struct.iter_unpack('<qqHHi',ev));frames.append(f);keys=[x for x in f if x[2:4]==(1,n+1)]
 check('cycle'+str(n)+'_actual_key_down_up',len(keys)==2 and [x[4] for x in keys]==[1,0] and keys[0][:2]<keys[1][:2])
 check('cycle'+str(n)+'_disconnect_before_detach',c['spontaneous_disconnect'] and not c['detach_before_observation'] and c['sessions']==[])
 ports=[x.split() for x in c['vhci_status'].splitlines() if x.startswith(('hs ','ss '))]
 check('cycle'+str(n)+'_all16_VHCI_NULL',len(ports)==16 and len({x[1] for x in ports})==16 and all(len(x)==7 and int(x[2])==4 and x[-1]=='0-0' for x in ports))
 rr=c['receipts'];check('cycle'+str(n)+'_actual_device_receipts',len(rr)==2 and len({x['port'] for x in rr})==2 and [(x['vendor_id'],x['product_id']) for x in rr]==[(0x627,1),(0x403,0x6001)] and all(x['status']==6 and x['local_device_address']>0 and x['local_device_generation']['inode']>0 for x in rr))
check('actual_host_TX_sink105',TX==(RUN/'ftdi-host-tx.bin').read_bytes() and len(TX)==105)
check('distinct_client_generation_each_cycle',all(len({c['receipts'][i]['local_device_generation']['inode'] for c in clients})==3 for i in [0,1]))
after={str(p):metadata(p) for p in paths};check('completed_raw_and_old_ELF_unchanged',before==after)
result={'schema_version':1,'result':'PASS' if all(c['result']=='PASS' for c in checks) else 'FAIL','at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head':HEAD,'legacy_head':OLD,'checks_count':len(checks),'checks':checks,'method':'Independent stdlib parser of immutable completed legacy-schema1 raw streams, exact Git and copied sources, retained old ELF, disk bytes, proc identity records, helper logs, wait records, evdev frames and actual host FTDI sink. No project runner imported/called.','facts':{'scenario':'legacy-schema1','guest_count':2,'client_io_cycles':3,'FTDI_each_direction_bytes':105,'recorded_host_assertions':len(summary['verification']['checks']),'pure_samples':len(pure),'sample_labels':dict(collections.Counter(p['label'] for p in pure)),'windows':windows,'helper_epochs':helpers,'actual_waits':waits,'old_persisted_records':r['killed-pending']['journal'],'new_opt_in_records':r['recovered-ready']['journal'],'old_baseline':orig,'admin_reconcile':admin,'evdev_frames':frames,'no_auto_events_in_all_helper_checkpoints':True,'source_fingerprint':fingerprint,'generated_init_sha256':comp['generated_init_sha256'],'current_binary_recorded_sha256':summary['binary_sha256'],'legacy_ELF':old['binary']},'selected_completed_sequence':seq[0],'immutable_artifacts':before,'parser':{'path':str(Path(__file__)),'sha256':sha(Path(__file__).read_bytes())},'authorship':'Reviewer authored earlier journal storage slice, not recovery executor or negative QEMU harness. This is independent raw-evidence reconstruction, not an independent audit of the authored storage implementation.','source_vs_observation':{'direct':['Actual old/current proc exe path/hash/UID/PID/start snapshots, exact original durable grants, real waits, same old disk bytes through both bounded windows, no auto event in seven complete-line helper checkpoint logs, explicit guard stderr, two successful root reconcile responses, three actual HID/FTDI cycles and105B host sink.'],'source_constraints':['The two sysfs bind writes are attributed to frozen legacy-only guest tail, followed by observed full driver restoration; no syscall trace independently identifies the writer.','Pure interval performs disk/sysfs/proc reads; startup doctor/Status occurs earlier. Primary precedes later pending/new-grant, not every helper API since startup.','Schema1 maps to ManualOnly in current frozen product code; disk1 intentionally has no recovery field or authorization summary.']},'limits':['One negative legacy-producer upgrade scenario with manual recovery tail; neither its20s rejection nor5s restored-pending observation is automatic restoration success.','81 polling samples and review assertions are evidence counts, not USB cases; three actual client I/O cycles and two guests only.','Only new current opt-in grants after explicit manual retirement upgrade disk1 to disk2; mixed old/manual plus simultaneously new opt-in disk2 migration is not exercised.','Retained legacy ELF was independently rehashed. Current ELF/initramfs not retained here; current identity relies on recorded build/payload/after chain plus guest helper proc-exe hash, not rehash of absent current executable.','No real hardware, cross-configuration/alternate, arbitrary FD revocation or power-loss durability conclusion; P/C separate runs outside this report.','Killed epoch has no final backend resource snapshot and none is fabricated.'],'boundaries':{'repo_writes':False,'Cargo_build_QEMU':False,'project_verifier_called':False,'original_artifacts_modified':False}}
OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'report':str(OUT),'result':result['result'],'checks':len(checks),'failed':[c['id'] for c in checks if c['result']=='FAIL'],'sha256':sha(OUT.read_bytes())}))
