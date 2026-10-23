#!/usr/bin/env python3
"""Offline independent reconstruction; never imports a repository runner."""
import collections
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess

REPO = Path('/home/ubuntu/github/usbip-client')
BASE = Path('/tmp/rustusb-autorestore-2ab9976-20261023')
OUT = Path('/tmp/rustusb-autorestore-positive-independent-review-20261023.json')
HEAD = '2ab997673a5894099f8df770707a060da3735154'
NAMES = ['owner-death', 'helper-restart']
checks = []
def check(label, condition, detail=None):
    checks.append({'id': label, 'result': 'PASS' if condition else 'FAIL', 'detail': detail})
    if not condition:
        print('FAIL', label, detail)
def sha(data):
    return hashlib.sha256(data).hexdigest()
def metadata(path):
    data = path.read_bytes()
    return {'bytes': len(data), 'sha256': sha(data)}
def load(path):
    return json.loads(path.read_text())
def entries(lines, prefix):
    return [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
def block_text(lines, begin, end):
    if lines.count(begin) != 1 or lines.count(end) != 1:
        raise ValueError('nonunique raw block ' + begin)
    lo, hi = lines.index(begin), lines.index(end)
    if lo >= hi:
        raise ValueError('unordered raw block ' + begin)
    return '\n'.join(lines[lo + 1:hi])
def block(lines, tag):
    text = block_text(lines, 'RUSTUSB_JOURNAL_' + tag + '_BEGIN', 'RUSTUSB_JOURNAL_' + tag + '_END')
    return [json.loads(line) for line in text.splitlines()]
def line_of(lines, prefix):
    indices = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    if len(indices) != 1:
        raise ValueError('expected unique prefix ' + prefix)
    return indices[0]
def clean_log(value):
    return re.sub(r'\x1b\[[0-9;]*m', '', value)
def operations(value):
    result = []
    for line in clean_log(value).splitlines():
        if 'physical_auto_restore' in line:
            fields = dict(re.findall(r'\b(operation|lease_id|phase)="([^"]+)"', line))
            if fields.get('operation') != 'physical_auto_restore' or set(fields) != {'operation', 'lease_id', 'phase'}:
                raise ValueError('unknown automatic operation format')
            result.append({**fields, 'time': line.split()[0], 'raw': line})
    return result
def zero(snapshot):
    return snapshot['schema_version'] == 1 and snapshot['buffer_bytes_reserved'] == 0 and len(snapshot['devices']) == 2 and all(
        d['state'] == 'available' and d['leased'] is False and d['cleanup_verified'] is True and
        d['pending_urbs'] == d['queued_urbs'] == d['usbfs_inflight'] == 0 for d in snapshot['devices'])
def identity_valid(value, original):
    by_node = {d['node']: d['node_identity'] for d in original}
    return (value['pid'] > 1 and value['starttime'] > 0 and value['uids'] == [1000]*4 and value['exe'] == '/usr/bin/rustusb'
        and value['cap_effective'] == value['cap_permitted'] == value['cap_ambient'] == 0
        and len(value['fds']) == 2 and len({f['fd'] for f in value['fds']}) == 2
        and {f['node']:f['identity'] for f in value['fds']} == by_node)
def helper_valid(value):
    return value['pid'] > 1 and value['starttime'] > 0 and value['uids'] == [0]*4 and value['exe'] == '/usr/bin/rustusb-helper'
def strip_drivers(devices):
    result = json.loads(json.dumps(devices))
    for d in result:
        for i in d['interfaces']:
            del i['driver']
    return result

freeze = load(BASE/'freeze.json')
sequence = load(BASE/'sequence.json')
selected_sequence = {r['name']:r for r in sequence['runs'] if r['name'] in NAMES}
check('freeze_exact_clean_head', freeze['head'] == HEAD and freeze['working_tree_dirty'] is False)
blobs = {}
for path, wanted in freeze['files'].items():
    content = subprocess.check_output(['git', 'show', HEAD+':'+path], cwd=REPO)
    blobs[path] = content
    check('frozen_git_input:'+path, len(content) == wanted['bytes'] and sha(content) == wanted['sha256'])
# Reconstruct the nested byte fingerprint using immutable Git blobs, no runner code execution.
native_paths = ['Cargo.toml','Cargo.lock','scripts/qemu/guest-init.sh','scripts/qemu/run-native-qemu.py']
native_paths += [p for p in blobs if p.startswith('crates/') and (p.endswith('.rs') or p.endswith('/Cargo.toml'))]
h = hashlib.sha256()
for p in sorted(native_paths, key=lambda p: Path(p).parts):
    name = p.encode(); data = blobs[p]
    h.update(len(name).to_bytes(8,'big'));h.update(name);h.update(len(data).to_bytes(8,'big'));h.update(data)
h = hashlib.sha256(h.digest())
for name in ['run-physical-qemu.py','guest-physical-init.sh','physical-wire-probe.rs']:
    data=blobs['scripts/qemu/'+name];h.update(name.encode());h.update(len(data).to_bytes(8,'big'));h.update(data)
for p in sorted((p for p in blobs if p.startswith('vendor/') and (p.endswith('.rs') or Path(p).name in ['Cargo.toml','Cargo.lock'])), key=lambda p: Path(p).parts):
    data=blobs[p];h.update(p.encode());h.update(len(data).to_bytes(8,'big'));h.update(data)
for names in [['run-journal-qemu.py','guest-journal-init.sh'],['run-autorestore-qemu.py','guest-autorestore-fragments.sh']]:
    h=hashlib.sha256(h.digest())
    for name in names:
        data=blobs['scripts/qemu/'+name];h.update(name.encode());h.update(len(data).to_bytes(8,'big'));h.update(data)
fingerprint=h.hexdigest()
immutable_paths = [BASE/'freeze.json'] + [p for name in NAMES for p in sorted((BASE/name).rglob('*')) if p.is_file()]
raw_before = {str(p):metadata(p) for p in immutable_paths}
results = {}
for name in NAMES:
    path=BASE/name;summary=load(path/'summary.json');seq=selected_sequence[name];prefix=name+':'
    check(prefix+'sequence_terminal',seq['result']=='PASS' and seq['exit_code']==0 and seq['head']==HEAD and all(seq[k] for k in ['source_guard','head_guard','worktree_clean']))
    check(prefix+'summary_complete_clean', summary['result']=='PASS' and summary['evidence_collection_complete'] and summary['git_commit']==summary['git_commit_after']==HEAD and not summary['working_tree_dirty'] and not summary['working_tree_dirty_after'])
    check(prefix+'recorded_binary_chain',summary['binary_sha256']==summary['guest_binary_sha256']==summary['binary_after_sha256'] and summary['binary_guard_passed'])
    check(prefix+'independent_source_fingerprint',summary['source_fingerprint_sha256']==summary['source_fingerprint_after_sha256']==fingerprint and summary['source_guard_passed'])
    check(prefix+'two_actual_guest_exit_zero',summary['guest_exit_codes']=={'server':0,'client':0})
    check(prefix+'ordinary_host_and_exact_build',summary['host_uid']==1000 and summary['host_architecture']=='x86_64' and summary['build_command']==['cargo','build','--workspace','--release','--locked','--all-features'])
    check(prefix+'scenario_metadata',summary['scenario']==name and summary['product_automatic_recovery_test'] and summary['original_journal_verifier_used'] is False)
    check(prefix+'kernel_and_acceleration',summary['kernel']=='6.8.0-106-generic' and summary['acceleration']=='kvm')
    for filename,digest in summary['script_sha256'].items():
        data=(path/'source'/filename).read_bytes()
        check(prefix+'frozen_script:'+filename,sha(data)==digest and data==blobs['scripts/qemu/'+filename])
    composition=load(path/'autorestore-composition.json')
    generated=(path/'source/guest-journal-init.sh').read_text()
    for i,step in enumerate(composition['composition']):
        check(prefix+f'unique_composition_anchor_{i}',step['matches']==1 and generated.count(step['before'])==1)
        generated=generated.replace(step['before'],step['after'],1)
    actual=(path/'source/generated-autorestore-init.sh').read_bytes()
    check(prefix+'generated_init_exact',actual==generated.encode() and sha(actual)==composition['generated_init_sha256'])
    fragments=(path/'source/guest-autorestore-fragments.sh').read_text()
    check(prefix+'fragment_exact',sha(fragments.encode())==composition['fragment_sha256'])
    check(prefix+'no_test_or_admin_recovery_actor',all(x not in generated+fragments for x in [' > /sys/bus/usb/drivers/','--json physical-reconcile','usbfs-guardian-probe','guardian_restore','USBDEVFS_CONNECT']))
    readonly_body=fragments.split('autorestore_readonly() {',1)[1].split('\n}\n',1)[0]
    check(prefix+'readonly_source_no_helper_cli',all(x not in readonly_body for x in ['/usr/bin/rustusb','physical-pending','physical-reconcile','/run/rustusb/control.sock']))
    flow=fragments.split('autorestore_killed_epoch() {',1)[1]
    check(prefix+'source_primary_before_api',flow.index("printf 'RUSTUSB_AUTORESTORE_PRIMARY '")<flow.index('record automatically-restored') and flow.count('wait "$KILL_PID"')==1)
    check(prefix+'no_host_usb_or_tcp_listener',summary['topology']=={'private_socketpair':['server:192.0.2.1','client:192.0.2.2'],'host_network_listener':False,'ip_forward':False} and all('usb-host' not in ' '.join(cmd) and 'hostfwd' not in ' '.join(cmd) for cmd in summary['qemu_commands'].values()))
    logs={role:(path/(role+'-evidence.log')).read_text() for role in ['server','client']}
    for role in ['server','client']:
        lines=logs[role].splitlines();kernel=(path/(role+'-serial.log')).read_text()
        check(prefix+role+'_collection_and_channels',lines.count(f'RUSTUSB_JOURNAL_COLLECTION_COMPLETE role={role}')==1 and lines.count(f'RUSTUSB_JOURNAL_RESULT: PASS role={role}')==1 and lines.count(f'RUSTUSB_JOURNAL_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0')==1 and 'RUSTUSB_JOURNAL_' not in kernel and 'RUSTUSB_AUTORESTORE_' not in kernel)
        check(prefix+role+'_no_failure_or_integrity_markers',not any(word in logs[role]+kernel for word in ['RUSTUSB_JOURNAL_RESULT: FAIL','Kernel panic','BUG:','Oops:','general protection fault','AddressSanitizer','thread \'main\' panicked']))
        check(prefix+role+'_qemu_stderr_empty',(path/(role+'-qemu.stderr')).read_bytes()==b'')
    lines=logs['server'].splitlines();client_lines=logs['client'].splitlines()
    records=block(lines,'RECORDS');waits=block(lines,'WAITS');obs=block(lines,'OBSERVATIONS');clients=block(client_lines,'CLIENT')
    check(prefix+'immediate_records_equal_final',entries(lines,'RUSTUSB_JOURNAL_RECORD ')==records)
    check(prefix+'immediate_waits_equal_final',entries(lines,'RUSTUSB_JOURNAL_WAIT ')==waits)
    check(prefix+'no_admin_api_recovery',block(lines,'ADMIN')==[])
    expected=['initial','normal-ready','normal-pending','normal-closed','reopened-ready','reopened-closed','killed-ready']
    if name=='helper-restart':expected+=['killed-attached','helper-reloaded','live-held']
    expected+=['killed-pending','automatically-restored','recovered-ready','recovered-pending','recovered-closed']
    check(prefix+'ordered_record_lifecycle',[r['label'] for r in records]==expected)
    rec={r['label']:r for r in records};ob={r['label']:r for r in obs};original=rec['initial']['sysfs'];by_id={d['device_id']:d for d in original}
    check(prefix+'original_full_fixture',len(original)==2 and [d['bus_id'] for d in original]==['1-1','1-2'] and [d['interfaces'] for d in original]==[[{'number':0,'alternate':0,'driver':'usbhid'}],[{'number':0,'alternate':0,'driver':'ftdi_sio'}]] and all(d['configuration']==1 and d['node_owner_mode']=='0:600' for d in original))
    meta=entries(lines,'RUSTUSB_AUTORESTORE_METADATA ');check(prefix+'one_metadata',len(meta)==1);meta=meta[0]
    check(prefix+'explicit_policy',meta['scenario']==name and meta['policy']=={'allowed_uids':[1000],'export_device_ids':[],'physical_device_ids':list(by_id),'physical_connect_restore_device_ids':list(by_id),'peers':[],'trusted_network':True} and not meta['usbfs_test_probe'] and not meta['administrator_reconcile'])
    for r in records:
        label=r['label'];disk=r['journal'];pending=r['pending']
        raw=block_text(lines,f'RUSTUSB_JOURNAL_DISK_BEGIN label={label}',f'RUSTUSB_JOURNAL_DISK_END label={label}')+'\n'
        raw_pending=block_text(lines,f'RUSTUSB_JOURNAL_PENDING_BEGIN label={label}',f'RUSTUSB_JOURNAL_PENDING_END label={label}')
        check(prefix+label+'_disk_bytes_and_api',json.loads(raw)==disk and sha(raw.encode())==r['journal_sha256'] and json.loads(raw_pending)==pending)
        check(prefix+label+'_protected',r['journal_directory_owner_mode']=='0:700' and r['journal_file_owner_mode']=='0:600')
        check(prefix+label+'_legacy_ipc_projection',pending['schema_version']==1 and pending['next_after'] is None and pending['records']==[{k:v for k,v in e.items() if k!='recovery'} for e in disk['records']])
        if label=='initial':
            check(prefix+'initial_legacy_empty',disk=={'schema_version':1,'records':[]} and pending=={'schema_version':1,'records':[],'next_after':None})
        else:
            check(prefix+label+'_sticky_opt_in_disk',disk['schema_version']==2 and pending['storage_schema_version']==2 and pending['recovery_authorizations']==[{'lease_id':e['lease_id'],'recovery':e['recovery']} for e in disk['records']] and all(e['recovery']=='connect_original_drivers' for e in disk['records']))
        if label in ['initial','normal-closed','reopened-closed','automatically-restored','recovered-closed']:
            check(prefix+label+'_original_and_retired',r['sysfs']==original and disk['records']==[])
    leases={}
    for epoch in ['normal','reopened','killed','recovered']:
        ready=rec[epoch+'-ready']['journal']['records'];identity=ob[epoch+'-ready']['identity'];leases[epoch]=[e['lease_id'] for e in ready]
        check(prefix+epoch+'_real_owner_and_two_grants',identity_valid(identity,original) and len(ready)==2 and len(set(leases[epoch]))==2)
        check(prefix+epoch+'_ready_snapshot',zero(ob[epoch+'-ready']['snapshot']) and ob[epoch+'-ready']['sysfs']==original)
        for e in ready:
            d=by_id[e['baseline']['device_id']];node=d['node_identity'].split(':');gen=list(map(int,d['generation'].split(':')))
            expected_baseline={'device_id':d['device_id'],'bus_id':d['bus_id'],'receipt':{'sysfs_device':gen[0],'sysfs_inode':gen[1],'node_device':os.makedev(int(node[2],16),int(node[3],16)),'node_inode':int(node[1]),'usb_device_number':d['devnum'],'bus_number':int(d['bus_id'].split('-')[0])},'configuration':d['configuration'],'interfaces':d['interfaces']}
            check(prefix+epoch+'_complete_persisted_baseline_'+d['bus_id'],e['baseline']==expected_baseline and e['owner']=={'uid':1000,'pid':identity['pid'],'start_time':identity['starttime'],'boot_id':meta['boot_id']})
        if epoch!='reopened':
            o=ob[epoch+'-pending'];s=o['snapshot'];claimed=json.loads(json.dumps(original))
            for d in claimed:
                for i in d['interfaces']:i['driver']='usbfs'
            check(prefix+epoch+'_actual_pending_and_claims',s['buffer_bytes_reserved']>0 and len(s['devices'])==2 and all(d['leased'] and d['state']=='leased' and d['pending_urbs']>0 and d['usbfs_inflight']>0 for d in s['devices']) and o['sysfs']==claimed and o['identity']==identity and rec[epoch+'-pending']['journal']['records']==ready)
    check(prefix+'eight_distinct_grants',len(set(sum(leases.values(),[])))==8)
    epoch_events={e:[json.loads(x) for x in block_text(lines,f'RUSTUSB_JOURNAL_EVENTS_BEGIN epoch={e}',f'RUSTUSB_JOURNAL_EVENTS_END epoch={e}').splitlines()] for e in leases}
    server_waits=[w for w in waits if w.get('event')=='server_wait'];wait_by={w['label']:w for w in server_waits}
    check(prefix+'four_server_waits',[w['label'] for w in server_waits]==list(leases))
    for epoch in ['normal','reopened','recovered']:
        ev=epoch_events[epoch]
        check(prefix+epoch+'_normal_terminal',wait_by[epoch]['wait_code']==0 and sum(x.get('event')=='ready' for x in ev)==1 and sum(x.get('event')=='stopped' for x in ev)==1 and ev[-1]['event']=='stopped' and zero(ev[-1]['snapshot']))
    killed=wait_by['killed'];killed_identity=ob['killed-pending']['identity'];killed_disk=rec['killed-pending']['journal']['records'];killed_by={r['lease_id']:r for r in killed_disk}
    check(prefix+'actual_owned_kill_unknown_final',killed['wait_code']==137 and killed['signal']==9 and killed['signal_count']==1 and killed['sender_uid']==1000 and killed['pid']==killed_identity['pid'] and killed['starttime']==killed_identity['starttime'] and killed['final_backend_snapshot'] is None and killed['final_backend_snapshot_observable'] is False and not any(e.get('event')=='stopped' for e in epoch_events['killed']))
    check(prefix+'one_signal_marker',[l for l in lines if l.startswith('RUSTUSB_JOURNAL_SIGNAL:')]==[f"RUSTUSB_JOURNAL_SIGNAL: PASS signal=9 count=1 sender_uid=1000 pid={killed['pid']} starttime={killed['starttime']}"])
    samples=entries(lines,'RUSTUSB_AUTORESTORE_READONLY ');primary=entries(lines,'RUSTUSB_AUTORESTORE_PRIMARY ');window=entries(lines,'RUSTUSB_AUTORESTORE_RECOVERY_WINDOW ')
    check(prefix+'complete_sample_order',[r['sequence'] for r in samples]==list(range(1,len(samples)+1)) and all(a['tick']<=b['tick'] for a,b in zip(samples,samples[1:])))
    for r in samples:
        check(prefix+f"sample_{r['sequence']}_raw_disk",json.loads(r['journal_raw'])==r['journal'] and sha(r['journal_raw'].encode())==r['journal_sha256'] and r['journal']['schema_version']==2 and r['pending_api_called'] is False and r['journal_directory_owner_mode']=='0:700' and r['journal_file_owner_mode']=='0:600' and helper_valid(r['helper']))
        check(prefix+f"sample_{r['sequence']}_original_generation_and_unmodified_remaining_records",strip_drivers(r['sysfs'])==strip_drivers(original) and all(e==killed_by.get(e['lease_id']) for e in r['journal']['records']))
    after=[r for r in samples if r['phase']=='after-owner-reaped'];live=[r for r in samples if r['phase']=='live-owner']
    check(prefix+'primary_pure_full_baseline_retirement',len(primary)==1 and primary[0]==after[-1] and primary[0]['sysfs']==original and primary[0]['journal']=={'schema_version':2,'records':[]} and primary[0]['owner'] is None)
    check(prefix+'recovery_window_and_no_pid',len(window)==1 and window[0]['pending_api_before_primary'] is False and 0<=primary[0]['tick']-window[0]['start_tick']<=2000 and all(r['owner'] is None for r in after))
    waitline=next(i for i,l in enumerate(lines) if l.startswith('RUSTUSB_JOURNAL_WAIT ') and json.loads(l.split(' ',1)[1]).get('label')=='killed')
    pindex=line_of(lines,'RUSTUSB_AUTORESTORE_PRIMARY ');apiindex=line_of(lines,'RUSTUSB_JOURNAL_PENDING_BEGIN label=automatically-restored')
    newindex=next(i for i,l in enumerate(lines) if l.startswith('RUSTUSB_JOURNAL_RECORD ') and json.loads(l.split(' ',1)[1])['label']=='recovered-ready')
    check(prefix+'primary_precedes_postkill_API_and_new_grant',waitline<pindex<apiindex<newindex and not any(l.startswith('RUSTUSB_JOURNAL_PENDING_BEGIN ') for l in lines[waitline:pindex]))
    hlogs=entries(lines,'RUSTUSB_AUTORESTORE_HELPER ');hby={r['label']:r for r in hlogs};final_ops=operations(hby['final']['log'])
    check(prefix+'helper_identity_each_log',all(helper_valid(r['helper']) for r in hlogs))
    check(prefix+'no_early_auto_operations',all(operations(r['log'])==[] for r in hlogs if r['label'] not in ['restored','final']))
    check(prefix+'exact_attempt_retired_old_leases',len(final_ops)==4 and {r['lease_id'] for r in final_ops}==set(leases['killed']) and all([r['phase'] for r in final_ops if r['lease_id']==lease]==['attempt','retired'] for lease in leases['killed']))
    check(prefix+'operations_stable_after_restoration',operations(hby['restored']['log'])==final_ops and clean_log(hby['final']['log']).startswith(clean_log(hby['restored']['log'])))
    suffix=clean_log(hby['restored']['log'])[len(clean_log(hby['before-kill']['log'])):]
    op_lines=suffix.splitlines();automatic_indices=[i for i,l in enumerate(op_lines) if 'operation="physical_auto_restore"' in l];pending_indices=[i for i,l in enumerate(op_lines) if 'operation="physical_pending"' in l]
    check(prefix+'helper_auto_before_postkill_pending',len(automatic_indices)==4 and pending_indices and max(automatic_indices)<min(pending_indices) and all('operation="open_physical"' not in l and 'operation="finish_physical"' not in l and 'operation="reconcile_physical"' not in l for l in op_lines[:max(automatic_indices)+1]))
    check(prefix+'primary_and_logs_same_recovery_helper',primary[0]['helper']==hby['before-kill']['helper']==hby['restored']['helper']==hby['final']['helper'])
    restart=[w for w in waits if w.get('event')=='helper_restart'];live_windows=entries(lines,'RUSTUSB_AUTORESTORE_LIVE_WINDOW ')
    if name=='helper-restart':
        check(prefix+'actual_helper_restart',len(restart)==1 and restart[0]['old_wait_code']==0 and restart[0]['old_pid']!=restart[0]['new_pid'] and restart[0]['state_directory_unchanged'] and len([w for w in waits if w.get('event')=='helper_stop' and w['old_wait_code']==0])==1)
        check(prefix+'new_helper_identity',hby['live-before-restart']['helper']['pid']==restart[0]['old_pid'] and hby['live-after-restart']['helper']['pid']==restart[0]['new_pid'] and hby['live-after-restart']['helper']==primary[0]['helper'])
        check(prefix+'live_window_five_seconds',len(live_windows)==1 and live_windows[0]['end_tick']-live_windows[0]['start_tick']>=500 and len(live)>=2 and live[-1]['tick']-live[0]['tick']>=500)
        for r in live:
            check(prefix+f"live_sample_{r['sequence']}_no_takeover",r['owner']==killed_identity and r['journal']['records']==killed_disk and r['sysfs']==ob['killed-pending']['sysfs'])
        check(prefix+'owner_and_journal_survive_restart',all(ob[l]['identity']==killed_identity and rec[l]['journal']['records']==killed_disk for l in ['killed-attached','helper-reloaded','live-held']))
    else:
        check(prefix+'no_restart_in_owner_death',restart==[] and live==[] and live_windows==[] and hby['initial']['helper']==primary[0]['helper'])
    check(prefix+'three_client_io_cycles',[c['cycle'] for c in clients]==[1,2,3])
    input_frames=[];all_tx=b''
    for c in clients:
        cycle=c['cycle'];rx=f'RUSTUSB-RX-{cycle}-0001:0123456789abcdef\n'.encode();tx=f'RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n'.encode();all_tx+=tx
        check(prefix+f'cycle{cycle}_exact_bidi_bytes',bytes.fromhex(c['rx_hex'])==rx and bytes.fromhex(c['tx_hex'])==tx and len(rx)==len(tx)==35)
        ev=bytes.fromhex(c['input_event_hex']);check(prefix+f'cycle{cycle}_evdev_complete',len(ev)>0 and len(ev)%24==0)
        frames=list(struct.iter_unpack('<qqHHi',ev));keys=[f for f in frames if f[2:4]==(1,cycle+1)]
        check(prefix+f'cycle{cycle}_actual_key_down_up',len(keys)==2 and [f[4] for f in keys]==[1,0] and keys[0][:2]<keys[1][:2]);input_frames.append(frames)
        check(prefix+f'cycle{cycle}_disconnect_before_detach',c['spontaneous_disconnect'] is True and c['detach_before_observation'] is False and c['sessions']==[])
        ports=[l.split() for l in c['vhci_status'].splitlines() if l.startswith(('hs ','ss '))]
        check(prefix+f'cycle{cycle}_independent_vhci_null',len(ports)==16 and len({r[1] for r in ports})==16 and all(len(r)==7 and int(r[2])==4 and r[-1]=='0-0' for r in ports))
        receipts=c['receipts'];check(prefix+f'cycle{cycle}_two_real_receipts',len(receipts)==2 and len({r['port'] for r in receipts})==2 and [(r['vendor_id'],r['product_id']) for r in receipts]==[(0x627,1),(0x403,0x6001)] and all(r['status']==6 and r['local_device_address']>0 and r['local_device_generation']['inode']>0 for r in receipts))
    check(prefix+'actual_host_TX_sink_105_bytes',(path/'ftdi-host-tx.bin').read_bytes()==all_tx and len(all_tx)==105)
    check(prefix+'new_client_generations_each_cycle',all(len({c['receipts'][i]['local_device_generation']['inode'] for c in clients})==3 for i in [0,1]))
    results[name]={'scenario_result':'PASS' if all(c['result']=='PASS' for c in checks if c['id'].startswith(prefix)) else 'FAIL','recorded_host_checks':len(summary['verification']['checks']),'records':len(records),'readonly_samples':len(samples),'live_owner_samples':len(live),'recovery_samples':len(after),'raw_poll_samples_are_not_USB_cases':True,'killed_wait':killed,'recovery_helper':primary[0]['helper'],'primary_readonly_tick':primary[0]['tick'],'recovery_window':window[0],'primary_observed_centiseconds_after_window_start':primary[0]['tick']-window[0]['start_tick'],'live_window':live_windows[0] if live_windows else None,'helper_restart':restart[0] if restart else None,'helper_auto_operations':final_ops,'original_baseline':original,'killed_persisted_records':killed_disk,'all_epoch_leases':leases,'client_cycle_count':3,'FTDI_rx_bytes':105,'FTDI_tx_sink_bytes':105,'evdev_frames':input_frames,'binary_recorded_sha256':summary['binary_sha256'],'generated_init_sha256':sha(actual),'snapshot_no_fake_killed_terminal_resources':True}
check('both_scenarios_same_product_binary',results[NAMES[0]]['binary_recorded_sha256']==results[NAMES[1]]['binary_recorded_sha256'])
raw_after={str(p):metadata(p) for p in immutable_paths}
check('all_completed_run_raw_artifacts_unchanged_during_review',raw_after==raw_before)
report={'schema_version':1,'reviewed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'review_result':'PASS' if all(c['result']=='PASS' for c in checks) else 'FAIL','head':HEAD,'method':'Independent offline parser of original program/kernel streams, inline disk bytes, API blocks, OS/FD observations, helper logs, evdev bytes and FTDI sink. Fixed Git sources and generated init rebuilt from saved replacement ledger. No repository runner imported or verifier called.','authorship':'Reviewer authored journal.rs storage slice, not product recovery executor or QEMU harness. This is independent raw evidence reconstruction, not an independent audit of the authored journal implementation.','checks_count':len(checks),'checks':checks,'cases':results,'frozen_input_count':len(freeze['files']),'source_fingerprint_reconstructed':fingerprint,'selected_sequence_records':selected_sequence,'immutable_artifacts':raw_before,'parser':{'path':str(Path(__file__)),'sha256':sha(Path(__file__).read_bytes())},'observed_vs_source':{'direct_observations':['Real PID/start/UID/FD snapshots and wait137, complete old persisted opt-in baselines, product attempt/retired log events, primary full sysfs plus atomically copied disk image with empty records before later pending API, all three HID/FTDI cycles and exact host sink.','Helper restart case observes same live owner/claims/records for at least5seconds under new helper, then recovery and new I/O after actual owner death.'],'source_constraints':['The interval before primary makes only sysfs/proc/disk reads; absence of helper API is proven by frozen flow plus helper operation ordering, not system-wide syscall tracing.','Generated flow has no guest administrator bind/reconcile and no USBFS recovery test probe; attribution to helper CONNECT relies on frozen product implementation plus its runtime attempt/retired records and external restoration, not per-ioctl tracing.']},'limits':['Two positive product QEMU scenarios, six total client I/O cycles, four guests. Check and polling counts are evidence assertions/samples, not USB cases.','ELF/initramfs originals were not retained; binary identities are recorded build/payload/after comparisons. This review does not rehash absent executable bytes or claim new software test execution.','Twenty-second recovery window begins after wait137 and coordination delivery, not at SIGKILL. Sample elapsed times are not a restoration latency SLA.','Same configuration1, one interface0/alt0 per HID/FTDI fixture only. No arbitrary cross-config/alt/composite/storage/hardware recovery, all-FD revocation, power-loss durability or external-root isolation conclusion.','Killed epoch has no final stopped/resource snapshot; no old zero-resource result is fabricated.','Other regression runs and CI are outside this report.'],'boundaries':{'repository_writes':False,'build_Cargo_QEMU_USB':False,'runner_verify_called':False,'original_artifacts_modified':False}}
report['review_process'] = {
    'initial_report': {'path': '/tmp/rustusb-autorestore-positive-independent-review-20261023-initial.json', **metadata(Path('/tmp/rustusb-autorestore-positive-independent-review-20261023-initial.json'))},
    'initial_parser': {'path': '/tmp/rustusb-review-autorestore-positive-20261023-initial.py', **metadata(Path('/tmp/rustusb-review-autorestore-positive-20261023-initial.py'))},
    'initial_result': '557/559 PASS; two fingerprint assertions failed because this independent parser originally sorted relative strings instead of pathlib path components.',
    'correction': 'Changed only independent parser native/vendor fingerprint ordering to Path.parts ordering; no harness, product, original artifact, or runtime result changed. Frozen path sets and bytes were already exact.',
    'resolved': fingerprint == '1f3bec4e1ea3352fa1a98b230e414efe871d9e82a53073277793377f51deb950',
}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'report':str(OUT),'result':report['review_result'],'checks':len(checks),'failures':[c['id'] for c in checks if c['result']!='PASS'],'sha256':sha(OUT.read_bytes())}))
