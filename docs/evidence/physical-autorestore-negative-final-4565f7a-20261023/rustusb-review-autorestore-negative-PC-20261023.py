"""Independent raw P/C review. Imports no runner, generator or verifier."""
from pathlib import Path
import datetime, hashlib, json, os, re, struct, subprocess

ROOT = Path('/home/ubuntu/github/usbip-client')
BASE = Path('/tmp/rustusb-autorestore-negative-4565f7a-20261023')
HEAD = '4565f7aaddb5a9377c15261a288ca9c3a57eebe5'
OUT = Path('/tmp/rustusb-autorestore-negative-PC-independent-review-20261023.json')
sha = lambda b: hashlib.sha256(b).hexdigest()
checks = []
def ck(name, value, detail=None):
    checks.append({'name': name, 'passed': bool(value), 'detail': detail})
def load(p): return json.loads(p.read_bytes())
def digest(p):
    b=p.read_bytes(); return {'bytes':len(b),'sha256':sha(b)}
def pref(log, marker):
    return [json.loads(line[len(marker):]) for line in log.splitlines() if line.startswith(marker)]
def block(log, begin, end):
    lines=log.splitlines()
    if lines.count(begin)!=1 or lines.count(end)!=1 or lines.index(begin)>=lines.index(end):
        raise ValueError('one ordered raw block required: '+begin)
    return '\n'.join(lines[lines.index(begin)+1:lines.index(end)])+'\n'
def rows(log, tag):
    return [json.loads(line) for line in block(log,'RUSTUSB_JOURNAL_'+tag+'_BEGIN','RUSTUSB_JOURNAL_'+tag+'_END').splitlines() if line.strip()]
def epochs(log, epoch):
    return [json.loads(line) for line in block(log,'RUSTUSB_JOURNAL_EVENTS_BEGIN epoch='+epoch,'RUSTUSB_JOURNAL_EVENTS_END epoch='+epoch).splitlines() if line.strip()]
def ops(log):
    out=[]
    for line in re.sub(r'\x1b\[[0-9;]*m','',log).splitlines():
        if 'physical_auto_restore' not in line: continue
        lease=re.search(r'\blease_id="([0-9a-f-]{36})"',line)
        phase=re.search(r'\bphase="(attempt|failed|retired)"',line)
        if not lease or not phase: raise ValueError('malformed recovery operation log')
        out.append({'lease_id':lease[1],'phase':phase[1],'raw':line})
    return out
def released(snapshot):
    return (snapshot.get('schema_version')==1 and snapshot.get('buffer_bytes_reserved')==0
            and len(snapshot.get('devices',[]))==2 and all(d.get('state')=='available'
            and d.get('leased') is False and d.get('cleanup_verified') is True
            and all(d.get(k)==0 for k in ['pending_urbs','queued_urbs','usbfs_inflight'])
            and d.get('current_drivers')==d.get('original_drivers') for d in snapshot['devices']))
def identity(v,original):
    return (v['uids']==[1000]*4 and v['exe']=='/usr/bin/rustusb' and v['pid']>1 and v['starttime']>0
            and all(v[k]==0 for k in ['cap_effective','cap_permitted','cap_ambient'])
            and all(any(f['node']==d['node'] and f['identity']==d['node_identity'] for f in v['fds']) for d in original))
def changed(original,driver):
    value=json.loads(json.dumps(original))
    for d in value:
        for i in d['interfaces']:i['driver']=driver
    return value

freeze=load(BASE/'freeze.json');sequence=load(BASE/'sequence.json')
inputs=[BASE/'freeze.json',BASE/'sequence.json']
for name in ['policy-revoked','external-claim']:
    inputs += [p for p in (BASE/name).rglob('*') if p.is_file()]
    inputs += [BASE/(name+'-runner.log')]
before={str(p):digest(p) for p in inputs}
ck('sequence3 actual terminal, same freeze head',sequence['head']==freeze['head']==HEAD
   and [r['name'] for r in sequence['runs']]==['legacy-schema1','policy-revoked','external-claim']
   and all(r['exit_code']==0 and r['result']=='PASS' and all(r[k] is True for k in ['source_guard','head_guard','worktree_clean','legacy_guard']) for r in sequence['runs']))
ck('actual freeze starts clean with155 files',freeze['working_tree_dirty'] is False and len(freeze['files'])==155)
git={}
for name,expected in freeze['files'].items():
    b=subprocess.check_output(['git','show',HEAD+':'+name],cwd=ROOT);git[name]=b
    ck('freeze Gitblob '+name,len(b)==expected['bytes'] and sha(b)==expected['sha256'])

# Match source order using pathlib components, not lexical flat strings.
native=[n for n in git if n in ['Cargo.toml','Cargo.lock'] or (n.startswith('crates/') and (n.endswith('.rs') or Path(n).name=='Cargo.toml'))]
native+=['scripts/qemu/guest-init.sh','scripts/qemu/run-native-qemu.py']
h=hashlib.sha256()
for n in sorted(native,key=Path):
    b=git[n];h.update(len(n.encode()).to_bytes(8,'big'));h.update(n.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
h=hashlib.sha256(h.digest())
for n in ['run-physical-qemu.py','guest-physical-init.sh','physical-wire-probe.rs']:
    b=git['scripts/qemu/'+n];h.update(n.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
for n in sorted([n for n in git if n.startswith('vendor/') and (n.endswith('.rs') or Path(n).name in ['Cargo.toml','Cargo.lock'])],key=Path):
    b=git[n];h.update(n.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
h=hashlib.sha256(h.digest())
for n in ['run-journal-qemu.py','guest-journal-init.sh']:
    b=git['scripts/qemu/'+n];h.update(n.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
h=hashlib.sha256(h.digest())
for n in ['run-autorestore-qemu.py','guest-autorestore-fragments.sh','run-autorestore-negative-qemu.py','guest-autorestore-negative-fragments.sh','usbfs-external-claim-probe.c']:
    b=git['scripts/qemu/'+n];h.update(n.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
fingerprint=h.hexdigest()
facts=[]
for scenario in ['policy-revoked','external-claim']:
    D=BASE/scenario;S=load(D/'summary.json');log=(D/'server-evidence.log').read_text();client=(D/'client-evidence.log').read_text();tag=scenario+' '
    ck(tag+'actual completed PASS/2guest0/clean source ELF guards',S['result']=='PASS' and S['evidence_collection_complete'] is True
       and S['guest_exit_codes']=={'server':0,'client':0} and S['git_commit']==S['git_commit_after']==HEAD
       and not S['working_tree_dirty'] and not S['working_tree_dirty_after']
       and S['source_guard_passed'] and S['binary_guard_passed'] and S['working_tree_guard_passed']
       and S['source_fingerprint_sha256']==S['source_fingerprint_after_sha256']==fingerprint
       and S['binary_sha256']==S['guest_binary_sha256']==S['binary_after_sha256'])
    ck(tag+'actual default locked allfeature build',S['build_performed'] is True and S['build_command']==['cargo','build','--workspace','--release','--locked','--all-features'],S['build_command'])
    for role,program in [('server',log),('client',client)]:
        kernel=(D/(role+'-serial.log')).read_text();lines=program.splitlines();q=S['qemu_commands'][role]
        ck(tag+role+' UART/pass/collection/kernel integrity',lines.count('RUSTUSB_JOURNAL_EVIDENCE_CHANNEL: PASS role='+role+' device=ttyS1 kernel_console=ttyS0')==1
           and lines.count('RUSTUSB_JOURNAL_COLLECTION_COMPLETE role='+role)==1 and lines.count('RUSTUSB_JOURNAL_RESULT: PASS role='+role)==1
           and not any(x in program+kernel for x in ['RUSTUSB_JOURNAL_RESULT: FAIL','Kernel panic','BUG:','was already given back','seqnum max'])
           and not any(line.startswith(('RUSTUSB_JOURNAL_','RUSTUSB_AUTONEG_','RUSTUSB_EXTERNAL_C ')) for line in kernel.splitlines()))
        ck(tag+role+' QEMU private socketpair/no passthrough/dualserial',q.count('-serial')==2 and '-nic' in q and q[q.index('-nic')+1]=='none'
           and any(x.startswith('socket,id=private,fd=') for x in q) and 'usb-host' not in ' '.join(q)
           and lines.count('RUSTUSB_JOURNAL_ISOLATION: PASS role='+role+' carrier=1 ip_forward=0')==1)
    for p in (D/'source').iterdir():
        if p.is_file() and p.name!='generated-negative-init.sh':
            ck(tag+'archived source '+p.name,p.read_bytes()==git['scripts/qemu/'+p.name])
    composition=load(D/'negative-composition.json');generated=(D/'source/generated-negative-init.sh').read_bytes()
    rebuilt=git['scripts/qemu/guest-journal-init.sh'].decode()
    for group in ['positive','negative']:
        for index,row in enumerate(composition['composition'][group]):
            ck(tag+'one composition anchor '+group+str(index),rebuilt.count(row['before'])==row['matches']==1)
            rebuilt=rebuilt.replace(row['before'],row['after'],1)
    ck(tag+'generated bytes reconstruct from frozen original',generated==rebuilt.encode() and sha(generated)==composition['generated_init_sha256'])
    ck(tag+'P/C have no administrator restore calls or test CONNECT',all(x not in generated.decode() for x in [' > /sys/bus/usb/drivers/','--json physical-reconcile','usbfs-guardian-probe'])
       and all(x not in git['scripts/qemu/usbfs-external-claim-probe.c'].decode() for x in ['USBDEVFS_CONNECT','USBDEVFS_DISCONNECT','USBDEVFS_RESET','USBDEVFS_SETCONFIGURATION','USBDEVFS_SETINTERFACE']))
    records=rows(log,'RECORDS');obs=rows(log,'OBSERVATIONS');waits=rows(log,'WAITS');admin=rows(log,'ADMIN');clients=rows(client,'CLIENT')
    by={r['label']:r for r in records};ob={r['label']:r for r in obs};original=by['initial']['sysfs'];null=changed(original,None);claimed=changed(original,'usbfs')
    metadata=pref(log,'RUSTUSB_AUTONEG_METADATA ');helpers=pref(log,'RUSTUSB_AUTONEG_HELPER_START ');policies=pref(log,'RUSTUSB_AUTONEG_POLICY ')
    logs=pref(log,'RUSTUSB_AUTONEG_HELPER_LOG ');pure=pref(log,'RUSTUSB_AUTONEG_READONLY ');windows=pref(log,'RUSTUSB_AUTONEG_WINDOW ')
    ck(tag+'raw immediate records/waits exactly match complete blocks',pref(log,'RUSTUSB_JOURNAL_RECORD ')==records and pref(log,'RUSTUSB_JOURNAL_WAIT ')==waits)
    ck(tag+'complete original HID/FTDI samegeneration cfg1 alt0 root0600',len(original)==2 and [d['bus_id'] for d in original]==['1-1','1-2']
       and [d['interfaces'] for d in original]==[[{'number':0,'alternate':0,'driver':'usbhid'}],[{'number':0,'alternate':0,'driver':'ftdi_sio'}]]
       and all(d['configuration']==1 and d['node_owner_mode']=='0:600' and d['generation'] and d['node_identity'] for d in original))
    ck(tag+'no admin reconcile or fake killed resources',admin==[] and len(metadata)==1 and metadata[0]['scenario']==scenario and metadata[0]['tail_automatic_recovery'] is True)
    ck(tag+'three distinct actual root helper epochs/payload ELF',len(helpers)==3 and [x['epoch'] for x in helpers]==[1,2,3] and len({x['pid'] for x in helpers})==3
       and all(x['exe']=='/usr/bin/rustusb-helper' and x['uids']==[0]*4 and x['starttime']>0 and x['sha256']==S['guest_binary_sha256']['rustusb-helper'] for x in helpers)
       and metadata[0]['binaries']==composition['binaries'])
    expected_policy={'allowed_uids':[1000],'export_device_ids':[],'physical_device_ids':[d['device_id'] for d in original],'peers':[],'trusted_network':True}
    ck(tag+'actual startup policy raw/hash and scopes',len(policies)==3 and all(p['helper_epoch']==i+1 and sha(p['raw'].encode())==p['sha256'] and json.loads(p['raw'])==p['policy']
       and p['policy']==dict(expected_policy,physical_connect_restore_device_ids=[] if scenario=='policy-revoked' and i==1 else expected_policy['physical_device_ids']) for i,p in enumerate(policies)))
    ck(tag+'policy saved before each actual helper startup',all(log.index('RUSTUSB_AUTONEG_POLICY '+json.dumps(p,separators=(',',':')))<log.index('RUSTUSB_AUTONEG_HELPER_START '+json.dumps(helpers[i],separators=(',',':'))) for i,p in enumerate(policies)))
    expected_labels=['initial','normal-ready','normal-pending','normal-closed','reopened-ready','reopened-closed','killed-ready','killed-pending','negative-observed','negative-new-grant-refused','tail-retired','recovered-ready','recovered-pending','recovered-closed']
    ck(tag+'exact lifecycle/empty original restored checkpoints',[r['label'] for r in records]==expected_labels and all(by[k]['journal']['records']==[] and by[k]['sysfs']==original for k in ['initial','normal-closed','reopened-closed','tail-retired','recovered-closed']))
    for row in records:
        label=row['label'];disk=row['journal'];pending=row['pending']
        ck(tag+label+' raw disk/pending and private disk2/IPC1 distinction',json.loads(block(log,'RUSTUSB_JOURNAL_DISK_BEGIN label='+label,'RUSTUSB_JOURNAL_DISK_END label='+label))==disk
           and sha(block(log,'RUSTUSB_JOURNAL_DISK_BEGIN label='+label,'RUSTUSB_JOURNAL_DISK_END label='+label).encode())==row['journal_sha256']
           and json.loads(block(log,'RUSTUSB_JOURNAL_PENDING_BEGIN label='+label,'RUSTUSB_JOURNAL_PENDING_END label='+label))==pending
           and pending['schema_version']==1 and pending['next_after'] is None and pending['records']==[{k:v for k,v in e.items() if k!='recovery'} for e in disk['records']]
           and row['journal_directory_owner_mode']=='0:700' and row['journal_file_owner_mode']=='0:600'
           and (disk['schema_version']==1 and pending['records']==[] if label=='initial' else disk['schema_version']==pending['storage_schema_version']==2
                and pending['recovery_authorizations']==[{'lease_id':e['lease_id'],'recovery':'connect_original_drivers'} for e in disk['records']]
                and all(e['recovery']=='connect_original_drivers' for e in disk['records'])))
    killed=by['killed-pending']['journal'];leases={e['lease_id'] for e in killed['records']}
    ck(tag+'old immutable optin retained through refusal',len(leases)==2 and all(by[k]['journal']==killed for k in ['negative-observed','negative-new-grant-refused']))
    all_leases=[]
    for epoch in ['normal','reopened','killed','recovered']:
        entries=by[epoch+'-ready']['journal']['records'];who=ob[epoch+'-ready']['identity'];all_leases.extend(e['lease_id'] for e in entries)
        ck(tag+epoch+' ordinary owner and ready original/zero',len(entries)==2 and identity(who,original) and released(ob[epoch+'-ready']['snapshot']) and ob[epoch+'-ready']['sysfs']==original)
        for e in entries:
            d=next(d for d in original if d['device_id']==e['baseline']['device_id']);b=e['baseline'];rc=b['receipt'];nd=d['node_identity'].split(':')
            ck(tag+epoch+' full receipt '+d['bus_id'],b['bus_id']==d['bus_id'] and b['configuration']==d['configuration'] and b['interfaces']==d['interfaces']
               and [rc['sysfs_device'],rc['sysfs_inode']]==list(map(int,d['generation'].split(':')))
               and rc['node_inode']==int(nd[1]) and rc['node_device']==os.makedev(int(nd[2],16),int(nd[3],16)) and rc['usb_device_number']==d['devnum'] and rc['bus_number']==1
               and e['owner']=={'uid':1000,'pid':who['pid'],'start_time':who['starttime'],'boot_id':metadata[0]['boot_id']})
    ck(tag+'eight fresh epoch lease ids',len(all_leases)==len(set(all_leases))==8)
    for epoch in ['normal','killed','recovered']:
        snap=ob[epoch+'-pending']['snapshot'];who=ob[epoch+'-pending']['identity']
        ck(tag+epoch+' actual two leased USBFS INpending',identity(who,original) and who==ob[epoch+'-ready']['identity'] and ob[epoch+'-pending']['sysfs']==claimed
           and by[epoch+'-pending']['journal']==by[epoch+'-ready']['journal'] and snap['buffer_bytes_reserved']>0 and len(snap['devices'])==2
           and all(d['leased'] is True and d['state']=='leased' and d['pending_urbs']>=1 and d['usbfs_inflight']>=1 for d in snap['devices']))
    waits_server={w['label']:w for w in waits if w['event']=='server_wait'};hw=[w for w in waits if w['event']=='negative_helper_wait']
    ck(tag+'two helpers truly stopped/wait0 before replaced',len(hw)==2 and all(w['epoch']==i+1 and w['pid']==helpers[i]['pid'] and w['starttime']==helpers[i]['starttime'] and w['signal']==15 and w['wait_code']==0 for i,w in enumerate(hw)))
    ki=ob['killed-pending']['identity'];kw=waits_server['killed'];ev={e:epochs(log,e) for e in ['normal','reopened','killed','negative-new-grant','recovered']}
    ck(tag+'one actual owned KILL137 after helper wait0, no terminal0',kw['wait_code']==137 and kw['signal']==9 and kw['signal_count']==1 and kw['sender_uid']==1000 and kw['pid']==ki['pid'] and kw['starttime']==ki['starttime']
       and kw['final_backend_snapshot'] is None and kw['final_backend_snapshot_observable'] is False and not any(e['event']=='stopped' for e in ev['killed'])
       and log.index('RUSTUSB_JOURNAL_WAIT '+json.dumps(hw[0],separators=(',',':')))<log.index('RUSTUSB_JOURNAL_SIGNAL:'))
    for epoch in ['normal','reopened','recovered']:
        stopped=[e for e in ev[epoch] if e['event']=='stopped']
        ck(tag+epoch+' real stopped snapshot resources0 and wait0',waits_server[epoch]['wait_code']==0 and len(stopped)==1 and ev[epoch][-1]==stopped[0] and released(stopped[0]['snapshot']))
    refusal=block(log,'RUSTUSB_JOURNAL_STDERR_BEGIN epoch=negative-new-grant','RUSTUSB_JOURNAL_STDERR_END epoch=negative-new-grant')
    ck(tag+'new grant rejected by exact pending guard',waits_server['negative-new-grant']['wait_code']==1 and not ev['negative-new-grant']
       and 'unresolved physical grant blocks new authorization; inspect physical-pending and reconcile the original baseline' in refusal)
    ck(tag+'read-only pure observations ordered and protected',pure and [x['sequence'] for x in pure]==list(range(1,len(pure)+1)) and all(a['tick']<=b['tick'] for a,b in zip(pure,pure[1:])))
    for x in pure:
        ck(tag+'pure observation '+str(x['sequence']),x['pending_api_called'] is False and x['journal_directory_owner_mode']=='0:700' and x['journal_file_owner_mode']=='0:600'
           and json.loads(x['journal_raw'])==x['journal'] and sha(x['journal_raw'].encode())==x['journal_sha256']
           and (x['helper'] is None or x['helper']==helpers[x['helper']['epoch']-1]))
    killed_pure=[x for x in pure if x['label']=='killed-with-helper-stopped']
    ck(tag+'independent pre-restart null baseline old records',len(killed_pure)==1 and killed_pure[0]['helper'] is None and killed_pure[0]['sysfs']==null and killed_pure[0]['journal']==killed)
    for label,minimum,drivers in [('rejected',2000,claimed if scenario=='external-claim' else null)]+([('released-no-retry',500,null)] if scenario=='external-claim' else []):
        win=next(w for w in windows if w['label']==label);samples=[x for x in pure if x['label']==label]
        ck(tag+label+' full fixed observation window/immutable authority',win['minimum_centiseconds']==minimum and win['end_tick']-win['start_tick']>=minimum and len(samples)>1
           and win['start_tick']<=samples[0]['tick'] and samples[-1]['tick']>=win['start_tick']+minimum and samples[-1]['tick']<=win['end_tick']
           and all(x['sysfs']==drivers and x['journal']==killed and x['helper']==helpers[1] for x in samples),{'start':win['start_tick'],'end':win['end_tick'],'samples':len(samples)})
    for x in logs:
        actual=ops(x['log']);ck(tag+'raw helper log '+x['label']+' epoch'+str(x['helper']['epoch']),actual==x['operations'] and x['helper']==helpers[x['helper']['epoch']-1])
        if x['helper']['epoch']==1:ck(tag+'epoch1 no endedowner recovery '+x['label'],actual==[])
        if x['helper']['epoch']==2:
            ck(tag+'epoch2 exact authority/attempts '+x['label'],actual==[] if scenario=='policy-revoked' else len(actual)==4 and all([o['phase'] for o in actual if o['lease_id']==lease]==['attempt','failed'] for lease in leases)
               and all('physical device is unsafe, already exported, claimed, or incompletely described' in o['raw'] for o in actual if o['phase']=='failed'))
    tail=next(x for x in logs if x['label']=='final');actual=ops(tail['log'])
    ck(tag+'only helper3 two attempts/two retired original ids',tail['helper']==helpers[2] and len(actual)==4 and all([o['phase'] for o in actual if o['lease_id']==lease]==['attempt','retired'] for lease in leases))
    primary=pref(log,'RUSTUSB_AUTONEG_RECOVERY_PRIMARY ');last=[x for x in pure if x['label']=='tail-automatic-recovery'];tw=next(w for w in windows if w['label']=='tail-automatic-recovery')
    ck(tag+'product tail pure restoration before API/new grant',len(primary)==1 and primary[0]==last[-1] and primary[0]['sysfs']==original and primary[0]['journal']=={'schema_version':2,'records':[]}
       and primary[0]['helper']==helpers[2] and 0<=tw['end_tick']-tw['start_tick']<2000 and tw['start_tick']<=primary[0]['tick']<=tw['end_tick']
       and log.index('RUSTUSB_AUTONEG_RECOVERY_PRIMARY ')<log.index('RUSTUSB_JOURNAL_PENDING_BEGIN label=tail-retired')<log.index('RUSTUSB_JOURNAL_PENDING_BEGIN label=recovered-ready'))
    ck(tag+'three actual cycles', [x['cycle'] for x in clients]==[1,2,3])
    for row in clients:
        cycle=row['cycle'];data=bytes.fromhex(row['input_event_hex']);frames=list(struct.iter_unpack('<qqHHi',data)) if data and len(data)%24==0 else []
        ck(tag+'cycle'+str(cycle)+' exact35B each/HID downup and independent NULL',bytes.fromhex(row['rx_hex'])==f'RUSTUSB-RX-{cycle}-0001:0123456789abcdef\n'.encode()
           and bytes.fromhex(row['tx_hex'])==f'RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n'.encode() and any(f[2:]==(1,cycle+1,1) for f in frames) and any(f[2:]==(1,cycle+1,0) for f in frames)
           and row['spontaneous_disconnect'] is True and row['detach_before_observation'] is False and row['sessions']==[])
        vh=[l.split() for l in row['vhci_status'].splitlines() if l.startswith(('hs ','ss '))]
        ck(tag+'cycle'+str(cycle)+' raw VHCI allNULL old receipt ports',vh and all(len(v)==7 and int(v[2])==4 and v[6]=='0-0' for v in vh)
           and len(row['receipts'])==2 and all(str(r['port']).zfill(4) in {v[1].zfill(4) for v in vh} and r['local_device_generation']['inode']>0 and r['local_device_address']>0 for r in row['receipts']))
    expected=b''.join(f'RUSTUSB-TX-{i}-0001:fedcba9876543210\n'.encode() for i in [1,2,3]);sink=(D/'ftdi-host-tx.bin').read_bytes()
    ck(tag+'actual host sink exactly105B not only collector JSON',sink==expected and len(sink)==105 and S['serial_output']['exact_match'] is True and S['serial_output']['sha256']==sha(sink))
    c=pref(log,'RUSTUSB_EXTERNAL_C ');cfacts=None
    if scenario=='policy-revoked':ck(tag+'no external actor payload',not c and metadata[0]['binaries']['external_claim_probe'] is None)
    else:
        io=[x for x in c if x['event']=='ioctl'];closed=[x for x in c if x['event']=='close'];ready=[x for x in c if x['event']=='ready'];states=[x for x in c if x['event']=='state'];cw=[w for w in waits if w['event']=='external_c_wait'];held=[x for x in pure if x['label'] in ['c-claimed-before-helper','rejected']]
        ck(tag+'C exact20 events/4successful realioctls/2closedEBADF/wait0',len(c)==20 and len(ready)==len(cw)==1 and len(io)==4 and [(x['device'],x['op']) for x in io]==[(0,'claim'),(1,'claim'),(0,'release'),(1,'release')]
           and all(x['rc']==x['errno']==x['interface']==0 for x in io) and len(closed)==2 and all(x['close_rc']==0 and x['getfd_rc']==-1 and x['getfd_errno']==9 for x in closed)
           and cw[0]['wait_code']==0 and cw[0]['pid']==ready[0]['pid'] and cw[0]['starttime']==ready[0]['starttime'] and c[-1]=={'event':'done'})
        for index,d in enumerate(original):
            ds=[x for x in states if x['device']==index];nd=d['node_identity'].split(':');fd=next(x['fd'] for x in ds if x['phase']=='independently-opened')
            ck(tag+'C device'+str(index)+' newopen fullreceipt/claim/release scope',[x['phase'] for x in ds]==['before-open','independently-opened','claimed','before-release','released','closed']
               and ds[0]['fd']==ds[-1]['fd']==-1 and all(x['fd']==fd for x in ds[1:-1])
               and all(x['pid']==ready[0]['pid'] and x['starttime']==ready[0]['starttime'] and x['node_dev']==int(nd[0]) and x['node_inode']==int(nd[1]) and x['node_rdev']==os.makedev(int(nd[2],16),int(nd[3],16))
                       and [x['sysfs_dev'],x['sysfs_inode']]==list(map(int,d['generation'].split(':'))) and x['configuration']==x['bus_number']==1 and x['device_number']==d['devnum'] for x in ds)
               and [x['interfaces'][0]['driver'] for x in ds]==[None,None,'usbfs','usbfs',None,None])
        ck(tag+'independent root proc C identity/fds stable allheld samples',held and all(x['external']['pid']==ready[0]['pid'] and x['external']['starttime']==ready[0]['starttime'] and x['external']['uids']==[0]*4
           and x['external']['exe']=='/usr/bin/usbfs-external-claim-probe' and x['external']['sha256']==metadata[0]['binaries']['external_claim_probe']['sha256'] and len(x['external']['fds'])==2
           and all(any(f['node']==d['node'] and f['identity']==d['node_identity'] for f in x['external']['fds']) for d in original) for x in held))
        ck(tag+'C ready before helper2/observed failures before C release/helper2 lives through noRetry',log.index('RUSTUSB_EXTERNAL_C '+json.dumps(ready[0],separators=(',',':')))<log.index('RUSTUSB_AUTONEG_HELPER_START '+json.dumps(helpers[1],separators=(',',':')))
           and log.index('RUSTUSB_AUTONEG_HELPER_LOG '+json.dumps(next(x for x in logs if x['label']=='after-refused-grant'),separators=(',',':')))<log.index('RUSTUSB_EXTERNAL_C '+json.dumps(next(x for x in c if x['event']=='ioctl' and x['op']=='release'),separators=(',',':')))
           and all(x['external'] is None for x in pure if x['label']=='released-no-retry'))
        nw=next(w for w in windows if w['label']=='released-no-retry')
        ck(tag+'actual C wait0 then noRetry window then helper2 stop/new helper3',ready[0]['pid']!=ki['pid']
           and log.index('RUSTUSB_JOURNAL_WAIT '+json.dumps(cw[0],separators=(',',':')))
               <log.index('RUSTUSB_AUTONEG_WINDOW '+json.dumps(nw,separators=(',',':')))
               <log.index('RUSTUSB_JOURNAL_WAIT '+json.dumps(hw[1],separators=(',',':')))
               <log.index('RUSTUSB_AUTONEG_HELPER_START '+json.dumps(helpers[2],separators=(',',':')))
               <log.index('RUSTUSB_AUTONEG_RECOVERY_PRIMARY '))
        cfacts={'pid':ready[0]['pid'],'starttime':ready[0]['starttime'],'ioctls':io,'close_checks':closed,'wait':cw[0],'independent_open_proof':'frozen source opens new descriptors and independent root proc/receipt observations; no kcmp/syscall tracer assertion'}
    facts.append({'scenario':scenario,'runner_result':S['result'],'guest_exit_codes':S['guest_exit_codes'],'helpers':helpers,'windows':windows,'readonly_samples':len(pure),'record_count':len(records),'actual_KILL137':kw,'tail_recovery_primary_tick':primary[0]['tick'],'tail_operations':actual,'actual_tx_sink_bytes':len(sink),'actual_tx_sink_sha256':sha(sink),'three_rx35B_and_HID':True,'external_actor':cfacts,'source_fingerprint':fingerprint,'binary_sha256':S['binary_sha256']})

ck('all original raw/source/API bytes unchanged during independent review',all(digest(Path(p))==v for p,v in before.items()),{'files':len(before)})
report={'schema_version':1,'result':'PASS' if all(x['passed'] for x in checks) else 'FAIL','scope':'Independent raw P/C review only; no runner/verifier imported or called. L handled by another reviewer.','head':HEAD,'reviewed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'checks':checks,'check_count':len(checks),'failures':[x for x in checks if not x['passed']],'scenarios':facts,'input_artifacts_before_and_after':before,'limitations':['This is QEMU synthetic HID/FTDI on recorded guest kernels, not physical hardware or wholePhase2 acceptance.','P proves startup-policy withdrawal and restoration only after explicit helper restart, not immediate revocation of an existing FD or an un-restarted policy edit.','C proves one separate root process independently opened/claimed both devices; this is not arbitrary external OFD revocation, and no CONNECT syscall/errno tracer was used.','Helper operation logs plus actual sysfs/disk observations and source control flow establish the stated defer/no-retry contract; do not call that per-ioctl tracing.','Killed epoch has no terminal resource snapshot. Later original-state restoration, journal retirement and new-epoch cleanup do not fabricate it.','Actual ELF files were not retained by these run directories; provenance is the recorded build→guest→after hash chain plus observedproc helper/probe identity.','All before/after raw bytes are rehashed. Current post-run documentation dirtiness, if any, is distinct from the recorded clean execution source guards.']}
initial=Path('/tmp/rustusb-autorestore-negative-PC-independent-review-initial-20261023.json')
report['reviewer_parser_correction']={'initial_report':str(initial),'initial_report_sha256':sha(initial.read_bytes()),
    'reason':'Initial review hard-coded an equivalent Cargo flag order incorrectly. Actual command already matched the frozen runner; only the independent predicate was corrected. The two initial FAIL assertions were review-tool mistakes, not QEMU or product failures. Raw bytes and runtime verdicts were unchanged.'}
OUT.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'path':str(OUT),'result':report['result'],'checks':len(checks),'bytes':OUT.stat().st_size,'sha256':sha(OUT.read_bytes()),'failures':report['failures']},indent=2))
