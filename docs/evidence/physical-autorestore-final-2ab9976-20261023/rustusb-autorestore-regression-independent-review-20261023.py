#!/usr/bin/env python3
"""Independent offline raw review; does not import or invoke a QEMU verifier."""
from pathlib import Path
import collections, datetime, hashlib, json, struct, subprocess, socket
BASE=Path('/tmp/rustusb-autorestore-2ab9976-20261023')
REPO=Path('/home/ubuntu/github/usbip-client')
OUT=Path('/tmp/rustusb-autorestore-regression-independent-review-20261023.json')
HEAD='2ab997673a5894099f8df770707a060da3735154'
OLD='a484d116b7d6acbc80ec7d84ab76b25707838115'
checks=[]; artifacts=[]; facts={}; diagnostics={}
def digest(data): return hashlib.sha256(data).hexdigest()
def check(name, value, detail=None):
    row={'name':name,'result':'PASS' if value else 'FAIL'}
    if detail is not None: row['detail']=detail
    checks.append(row)
def read(rel):
    path=BASE/rel; data=path.read_bytes()
    artifacts.append({'path':str(path),'bytes':len(data),'sha256':digest(data)})
    return data
def js(rel): return json.loads(read(rel))
def blob(path,rev=HEAD): return subprocess.check_output(['git','show',f'{rev}:{path}'],cwd=REPO)
def lines(rel): return read(rel).decode('utf-8').splitlines()
def block(raw,prefix,suffix=None):
    start=prefix+'_BEGIN'; end=(suffix or prefix)+'_END'
    check('one_block:'+prefix,raw.count(start)==raw.count(end)==1 and raw.index(start)<raw.index(end))
    return [json.loads(line) for line in raw[raw.index(start)+1:raw.index(end)] if line]
def marker(name,raw,text): check(name,raw.count(text)==1)
def clean(snap,allowed=('available',)):
    return snap['schema_version']==1 and snap['buffer_bytes_reserved']==0 and bool(snap['devices']) and all(d['state'] in allowed and d['leased'] is False and d['cleanup_verified'] is True and all(d[k]==0 for k in ['pending_urbs','queued_urbs','usbfs_inflight']) and (d['state']=='gone' or d['current_drivers']==d['original_drivers']) for d in snap['devices'])
freeze=js('freeze.json'); sequence=js('sequence.json')
check('freeze_clean_exact_head',freeze['head']==HEAD and freeze['working_tree_dirty'] is False)
check('sequence_terminal',sequence['head']==HEAD and len(sequence['runs'])==9 and all(r['result']=='PASS' and r['exit_code']==0 and r.get('finished_at') for r in sequence['runs']))
inputs={}
for path,meta in freeze['files'].items():
    data=blob(path); inputs[path]=data
    check('frozen_git_input:'+path,len(data)==meta['bytes'] and digest(data)==meta['sha256'])
    check('current_input_unchanged:'+path,(REPO/path).read_bytes()==data)
# Independent reconstruction of the native/physical/composite/TERM layered fingerprints.
native_paths=['Cargo.toml','Cargo.lock','scripts/qemu/guest-init.sh','scripts/qemu/run-native-qemu.py']+[p for p in inputs if p.startswith('crates/') and (p.endswith('.rs') or Path(p).name=='Cargo.toml')]
h=hashlib.sha256()
for p in sorted(native_paths,key=Path):
    name=p.encode(); data=inputs[p]; h.update(len(name).to_bytes(8,'big'));h.update(name);h.update(len(data).to_bytes(8,'big'));h.update(data)
native_hash=h.digest()
vendor=[p for p in inputs if p.startswith('vendor/') and (p.endswith('.rs') or Path(p).name in ['Cargo.toml','Cargo.lock'])]
h=hashlib.sha256(native_hash)
for name in ['run-physical-qemu.py','guest-physical-init.sh','physical-wire-probe.rs']:
    data=inputs['scripts/qemu/'+name]; h.update(name.encode());h.update(len(data).to_bytes(8,'big'));h.update(data)
for p in sorted(vendor,key=Path):
    data=inputs[p];h.update(p.encode());h.update(len(data).to_bytes(8,'big'));h.update(data)
physical_hash=h.digest(); fingerprints={'physical-normal':h.hexdigest(),'physical-hot-unplug':h.hexdigest()}
h=hashlib.sha256(native_hash)
for p in sorted(vendor+['scripts/qemu/'+name for name in ['run-composite-qemu.py','guest-composite-init.sh','composite-wire-probe.rs']],key=Path):
    data=inputs[p];h.update(p.encode());h.update(len(data).to_bytes(8,'big'));h.update(data)
fingerprints['physical-composite']=h.hexdigest();h=hashlib.sha256(physical_hash)
for name in ['run-sigterm-qemu.py','guest-sigterm-init.sh']:
    data=inputs['scripts/qemu/'+name];h.update(name.encode());h.update(len(data).to_bytes(8,'big'));h.update(data)
fingerprints['physical-sigterm']=h.hexdigest()
summaries={};logs={};events={}
for name,prefix in [('physical-normal','PHYSICAL'),('physical-hot-unplug','PHYSICAL'),('physical-composite','COMPOSITE'),('physical-sigterm','SIGTERM')]:
    s=js(name+'/summary.json');summaries[name]=s
    row=next(row for row in sequence['runs'] if row['name']==name)
    check(name+':actual_sequence_exit_and_guards',row['exit_code']==0 and row['source_guard'] and row['head_guard'] and row['worktree_clean'])
    check(name+':reported_runtime',s['result']=='PASS' and s['git_commit']==HEAD and not s['working_tree_dirty'] and s['guest_exit_codes']=={'server':0,'client':0})
    check(name+':exact_source_fingerprint',s['source_fingerprint_sha256']==fingerprints[name])
    check(name+':all_features_locked_build',s['build_performed'] and s['build_command']==['cargo','build','--workspace','--release','--locked','--all-features'])
    check(name+':build_finished',b'Finished `release` profile' in read(name+'/build.log'))
    check(name+':guest_ELF_copy_matches_build',s['binary_sha256']==s['guest_binary_sha256'])
    check(name+':host_scope',s['host_uid']==1000 and s['host_architecture']=='x86_64' and s['kernel']=='6.8.0-106-generic' and s['acceleration']=='kvm')
    for script,sha in s['script_sha256'].items():
        data=inputs['scripts/qemu/'+script]
        check(name+':script_hash:'+script,digest(data)==sha)
        check(name+':historical_script_unchanged:'+script,blob('scripts/qemu/'+script,OLD)==data)
    roles={}
    for role in ['server','client']:
        kernel=lines(f'{name}/{role}-serial.log')
        program=kernel if name=='physical-composite' else lines(f'{name}/{role}-evidence.log')
        roles[role]=program
        stderr=read(f'{name}/{role}-qemu.stderr')
        if name=='physical-composite' and role=='server':
            stderr_lines=stderr.decode('utf-8').splitlines()
            expected_negative=[
                'usbnet: failed control transaction: request 0xc0ff value 0x0 index 0x0 length 0x1',
                'usbnet: failed data transaction: pid 0x69 ep 0x1 len 0x1',
                'usbnet: failed control transaction: request 0x201 value 0x0 index 0x82 length 0x0',
            ]
            check(name+':'+role+':negative_probe_diagnostics_observed',all(stderr_lines.count(line)==1 for line in expected_negative))
            diagnostics[name+':'+role]={'line_count':len(stderr_lines),'line_counts':dict(collections.Counter(stderr_lines)),'expected_negative_probe_lines':expected_negative,'source':'scripts/qemu/composite-wire-probe.rs:509-532','interpretation':'The preserved probe deliberately requests the three listed failing transactions and separately asserts STALL/continued connection. Remaining packet-filter/descriptor diagnostics are preserved observations, not individually source-attributed or declared harmless by this review. Empty QEMU stderr is not a requirement of this regression; completion, byte traffic, original-state restoration and process status are independently checked.'}
        else:
            check(name+':'+role+':qemu_stderr_empty',stderr==b'')
        check(name+':'+role+':kernel_poweroff',any('Power down' in line for line in kernel))
        check(name+':'+role+':no_integrity_failure',not any(term in line for line in kernel+program for term in ['Kernel panic','BUG:','was already given back','seqnum max',f'RUSTUSB_{prefix}_RESULT: FAIL']))
        marker(name+':'+role+':actual_guest_pass',program,f'RUSTUSB_{prefix}_RESULT: PASS role={role}')
        argv=s['qemu_commands'][role]
        check(name+':'+role+':private_qemu_network',not any(arg.startswith(('user,','tap,','bridge,')) for arg in argv) and any(arg.startswith('socket,') and 'fd=' in arg for arg in argv) and not any('usb-host' in arg for arg in argv))
        if name!='physical-composite': marker(name+':'+role+':separate_evidence_UART',program,f'RUSTUSB_{prefix}_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0')
    logs[name]=roles
    ev=block(roles['server'],f'RUSTUSB_{prefix}_EVENTS');events[name]=ev
    reported=s['verification']['server_events'] if name=='physical-sigterm' else s['server_events']
    check(name+':actual_events_match_summary',ev==reported)
    check(name+':event_schema_no_errors',all(e['schema_version']==1 and e['event']!='error' for e in ev))
    check(name+':unique_ready_stopped_and_final',sum(e['event']=='ready' for e in ev)==1 and sum(e['event']=='stopped' for e in ev)==1 and ev[-1]['event']=='stopped')
    check(name+':known_finish_path_present',b'let snapshot = backend.close().await?;' in inputs['crates/rustusb-cli/src/physical_server.rs'] and b'finish_leases(socket, lease_ids).await?;' in inputs['crates/rustusb-cli/src/physical_server.rs'])
    check(name+':final_resource_state',clean(ev[-1]['snapshot'],('available','gone') if name=='physical-hot-unplug' else ('available',)))
    facts[name]={'runtime_result':s['result'],'guest_exit_codes':s['guest_exit_codes'],'source_fingerprint_sha256':fingerprints[name],'ELF':s['binary_sha256'],'final_snapshot':ev[-1]['snapshot'],'probe_cases':[]}
check('same_ELF_all_four_regressions',len({json.dumps(s['binary_sha256'],sort_keys=True) for s in summaries.values()})==1)
for name in ['physical-normal','physical-hot-unplug','physical-composite']:
    s=summaries[name]; raw=logs[name]; ev=events[name];snaps={r['id']:r['snapshot']for r in ev if r['event']=='snapshot'}
    probe_kind='composite_probe_case' if name=='physical-composite' else 'physical_probe_case'
    probes=[json.loads(line) for line in raw['client'] if line.startswith('{"event":"'+probe_kind+'"')]
    expected_count=19 if name=='physical-composite' else 15
    check(name+':exact_raw_probe_receipts',probes==s['protocol_probe_cases'] and len(probes)==expected_count and all(p['result']=='PASS' for p in probes))
    facts[name]['probe_cases']=probes
    check(name+':baseline_idle',clean(snaps['baseline']))
    for cycle in [1,2]:
        held=snaps[f'held-{cycle}']; io=snaps[f'io-{cycle}']; released=snaps[f'released-{cycle}']
        check(name+f':cycle{cycle}:actual_import_claims',held['buffer_bytes_reserved']>0 and all(d['state']=='leased' and d['leased'] and d['usbfs_inflight']>0 and all(x['driver']=='usbfs' for x in d['current_drivers']) for d in held['devices']))
        check(name+f':cycle{cycle}:released_original',clean(released))
        for before,after,end in zip(held['devices'],io['devices'],released['devices']):
            check(name+f':cycle{cycle}:io_then_reap:'+before['bus_id'],after['usbfs_reaped']>before['usbfs_reaped'] and end['usbfs_reaped']>=after['usbfs_reaped'] and after['counters']['bytes_in']>before['counters']['bytes_in'])
        if name in ['physical-normal','physical-hot-unplug']:
            marker(name+f':cycle{cycle}:HID',raw['client'],f'RUSTUSB_PHYSICAL_HID: PASS cycle={cycle} key_code={cycle+1} down=true up=true same_receipt=true')
            marker(name+f':cycle{cycle}:FTDI',raw['client'],f'RUSTUSB_PHYSICAL_FTDI: PASS cycle={cycle} in=35 out=35 exact_bytes=true tty_echo=false same_receipt=true')
            marker(name+f':cycle{cycle}:client_released',raw['client'],f'RUSTUSB_PHYSICAL_CLIENT_RELEASED: PASS cycle={cycle} kernel_sessions=0 both_devices_removed=true')
        else:
            marker(name+f':cycle{cycle}:external_baseline_restored',raw['server'],f'RUSTUSB_COMPOSITE_RELEASED: PASS cycle={cycle} config=1 alt0=0 alt1=1 drivers=cdc_ether inflight=0 budget=0')
            marker(name+f':cycle{cycle}:actual_udp',raw['client'],f'RUSTUSB_COMPOSITE_IO: PASS cycle={cycle} exact_udp=true same_receipt=true')
    if name in ['physical-normal','physical-hot-unplug']:
        cycles=range(1,5) if name=='physical-hot-unplug' else range(1,3)
        expected=b''.join(f'RUSTUSB-TX-{c}-0001:fedcba9876543210\n'.encode() for c in cycles)
        capture=s['serial_output']
        check(name+':host_tx_exact_summary_and_expected_digest',capture['exact_match'] and capture['expected_bytes']==capture['received_bytes']==len(expected) and capture['sha256']==digest(expected) and s['serial_received_sha256']==digest(expected))
        facts[name]['FTDI_host_capture_summary']={'bytes':len(expected),'expected_sha256':digest(expected),'raw_sink_payload_archived':False}
        check(name+':guest_wait0_is_guarded',b'    wait "$SERVER_PID"\n' in inputs['scripts/qemu/guest-physical-init.sh'] and b'set -eu' in inputs['scripts/qemu/guest-physical-init.sh'])
    else:
        marker(name+':original_composite_alt1',raw['server'],'RUSTUSB_COMPOSITE_ORIGINAL: PASS config=1 interfaces=2 driver0=cdc_ether driver1=cdc_ether alt0=0 alt1=1 speed=12 node=root0600')
        marker(name+':official_direct_cycle2',raw['client'],'RUSTUSB_COMPOSITE_OFFICIAL_ATTACH command=usbip_attach')
        marker(name+':official_ownership_not_adopted',raw['client'],'RUSTUSB_COMPOSITE_OFFICIAL_OWNERSHIP: PASS managed=false helper_detach=denied')
        marker(name+':normal_wait0_final_restored',raw['server'],'RUSTUSB_COMPOSITE_STOPPED: PASS exit=0 restored=true pending=0 inflight=0 budget=0')
        check(name+':full_driver_set',all(d['original_drivers']==[{'driver':'cdc_ether','interface':0},{'driver':'cdc_ether','interface':1}] for d in ev[-1]['snapshot']['devices']))
        check(name+':guest_wait0_is_guarded',b'    wait "$SERVER_PID"\n' in inputs['scripts/qemu/guest-composite-init.sh'] and b'set -eu' in inputs['scripts/qemu/guest-composite-init.sh'])
hot=logs['physical-hot-unplug'];hev=events['physical-hot-unplug'];hs={r['id']:r['snapshot']for r in hev if r['event']=='snapshot'}
for label in ['unplug-gone','unplug-io','unplug-released','replug-stale']:
    devs=hs[label]['devices'];check('hot:'+label+':old_slot_stays_gone',len(devs)==2 and devs[0]['state']=='gone' and devs[0]['leased'] is False and devs[0]['usbfs_inflight']==0)
check('hot:remaining_FTDI_actual_transfer_after_HID_gone',hs['unplug-io']['devices'][1]['counters']['bytes_out']>hs['unplug-gone']['devices'][1]['counters']['bytes_out'])
for role,text in [('server','RUSTUSB_PHYSICAL_REINSERT_STALE: PASS new_generation=true old_slot_gone=true nodes=root0600'),('server','RUSTUSB_PHYSICAL_STOPPED: PASS exit=0 ftdi_driver=ftdi_sio gone_hid=true inflight=0'),('client','RUSTUSB_PHYSICAL_FTDI_CONTINUES: PASS before=35 after=35 exact_bytes=true same_receipt=true')]: marker('hot:marker:'+text,hot[role],text)
check('hot:actual_QMP_removed_kbd',any(row['event']=='DEVICE_DELETED' and row['data']['device']=='physical-kbd'for row in summaries['physical-hot-unplug']['qmp_events']))
# Decode both private Ethernet captures directly, with IPv4 and UDP checksum checks.
def folded(data):
    if len(data)%2:data+=b'\0'
    total=sum(struct.unpack('!%dH'%(len(data)//2),data))
    while total>>16:total=(total&65535)+(total>>16)
    return total
packets={}
for cap in ['data-usb.pcap','data-peer.pcap']:
    data=read('physical-composite/'+cap);meta=summaries['physical-composite']['private_data_captures'][cap]
    check('pcap:'+cap+':artifact',len(data)==meta['bytes'] and digest(data)==meta['sha256'])
    endian='<' if data[:4]==b'\xd4\xc3\xb2\xa1' else '>'
    magic,major,minor,zone,sigfigs,snaplen,linktype=struct.unpack(endian+'IHHIIII',data[:24]);check('pcap:'+cap+':Ethernet_v2_4',(major,minor,linktype)==(2,4,1))
    pos=24; payloads=[];frame_count=0
    while pos<len(data):
        sec,usec,incl,orig=struct.unpack(endian+'IIII',data[pos:pos+16]);pos+=16;frame=data[pos:pos+incl];pos+=incl;frame_count+=1
        check(f'pcap:{cap}:frame{frame_count}:complete',incl==orig and len(frame)==incl)
        if frame[12:14]!=b'\x08\x00':continue
        ip=frame[14:];hlen=(ip[0]&15)*4;total=int.from_bytes(ip[2:4],'big');check(f'pcap:{cap}:frame{frame_count}:IPv4_checksum',ip[0]>>4==4 and folded(ip[:hlen])==65535 and total<=len(ip))
        if ip[9]!=17:continue
        udp=ip[hlen:total];sport,dport,length,checksum=struct.unpack('!HHHH',udp[:8]);
        if {sport,dport}!={48001,48002}:continue
        check(f'pcap:{cap}:frame{frame_count}:UDP_checksum',len(udp)==length and checksum!=0 and folded(ip[12:20]+bytes([0,17])+struct.pack('!H',length)+udp)==65535)
        payloads.append({'source':socket.inet_ntoa(ip[12:16]),'destination':socket.inet_ntoa(ip[16:20]),'source_port':sport,'destination_port':dport,'payload_hex':udp[8:].hex()})
    expected=[]
    for cycle in [1,2]:
        expected.extend([{'source':'198.18.0.2','destination':'198.18.0.1','source_port':48002,'destination_port':48001,'payload_hex':f'RUSTUSB-ECM-REQUEST-{cycle}:0123456789abcdef'.encode().hex()},{'source':'198.18.0.1','destination':'198.18.0.2','source_port':48001,'destination_port':48002,'payload_hex':f'RUSTUSB-ECM-RESPONSE-{cycle}:fedcba9876543210'.encode().hex()}])
    check('pcap:'+cap+':exact_two_roundtrips',payloads==expected)
    packets[cap]={'frames':frame_count,'actual_udp':payloads}
facts['physical-composite']['independent_pcap']=packets
# TERM raw outcomes and independent same-generation sysfs checks.
term=logs['physical-sigterm'];s=summaries['physical-sigterm'];tev=events['physical-sigterm']
def tagged(raw,tag):
    rows=[json.loads(line[len(tag):])for line in raw if line.startswith(tag)];check('one_tag:'+tag,len(rows)==1);return rows[0]
startup=tagged(term['server'],'RUSTUSB_SIGTERM_STARTUP_OUTCOME ');server=tagged(term['server'],'RUSTUSB_SIGTERM_OUTCOME ');client=tagged(term['client'],'RUSTUSB_SIGTERM_OUTCOME ')
obs=block(term['server'],'RUSTUSB_SIGTERM_OBSERVATIONS');bylabel={row['label']:row for row in obs}
check('term:raw_observations_match_summary',obs==s['verification']['server_observations'])
check('term:startup_cancel_before_listener',startup['signal']==15 and startup['signal_count']==1 and startup['uid']==startup['sender_uid']==1000 and startup['wait_code']==0 and startup['ready_before_signal'] is False and startup['listener_before_signal'] is False and startup['helper_stopped_before_signal'] and startup['helper_continued'] and startup['sysfs_unchanged'] and startup['observation_centiseconds']<=300)
check('term:startup_real_helper_backlog',startup['connected_unix_inode'] in startup['unix_ss_observation'] and '/run/rustusb/control.sock' in startup['unix_ss_observation'] and f'pid={startup["pid"]}' in startup['unix_ss_observation'])
check('term:main_one_owned_signal_wait0',server['signal']==15 and server['signal_count']==1 and server['sender_uid']==1000 and server['wait_code']==0 and server['pid_absent'] and server['fd_table_absent'] and server['process_exit_observed'] and server['stdin_kept_open'] and server['shutdown_control_sent'] is False)
check('term:external_original_state_restored',bylabel['post-term']['sysfs']==bylabel['baseline']['sysfs']==startup['sysfs'] and server['restored_sysfs'] and server['within_observation_window'])
for label in ['held','term-pending']:
    observation=bylabel[label];identity=observation['identity'];snap=observation['snapshot']
    check('term:'+label+':original_owned_process',identity['pid']==server['pid'] and identity['starttime']==server['starttime'] and identity['uids']==[1000]*4 and all(identity[k]==0 for k in ['cap_effective','cap_permitted','cap_ambient']))
    check('term:'+label+':claims_and_real_pending',snap['buffer_bytes_reserved']>0 and all(d['leased'] and d['state']=='leased' and d['pending_urbs']>0 and d['usbfs_inflight']>0 for d in snap['devices']))
    for before,now in zip(bylabel['baseline']['sysfs'],observation['sysfs']):
        check('term:'+label+':generation_and_claim:'+before['bus_id'],now=={**before,'interfaces':[{'number':0,'alternate':0,'driver':'usbfs'}]} and any(fd['node']==now['node'] and fd['identity']==now['node_identity']for fd in observation['fds']))
for before,after in zip(bylabel['term-pending']['snapshot']['devices'],tev[-1]['snapshot']['devices']):check('term:reap_after_real_pending:'+before['bus_id'],after['usbfs_reaped']>before['usbfs_reaped'] and after['cancelled']>=before['cancelled'])
check('term:client_spontaneous_before_detach',client['spontaneous_disconnect'] and not client['detach_before_observation'] and client['hid_generation_gone'] and client['ftdi_generation_gone'] and client['sessions']==[])
status=[line.split()for line in client['vhci_status'].splitlines() if line.split() and line.split()[0] in ['hs','ss']]
check('term:actual_16_VHCI_NULL',len(status)==16 and all(int(row[2])==4 and row[6]=='0-0' for row in status))
rawinput=bytes.fromhex(client['input_event_hex']);decoded=list(struct.iter_unpack('<qqHHi',rawinput))
check('term:actual_evdev_key1_down_up',any(row[2:]==(1,2,1)for row in decoded) and any(row[2:]==(1,2,0)for row in decoded))
check('term:serial_expected_digest',client['serial_in_bytes']==client['serial_out_bytes']==35 and client['rx_sha256']==digest(b'RUSTUSB-RX-1-0001:0123456789abcdef\n') and client['tx_sha256']==digest(b'RUSTUSB-TX-1-0001:fedcba9876543210\n') and s['serial_output']['exact_match'])
facts['physical-sigterm'].update({'startup_wait_code':startup['wait_code'],'main_wait_code':server['wait_code'],'server_observation_centiseconds':server['observation_centiseconds'],'client_observation_centiseconds':client['observation_centiseconds'],'raw_outcomes':{'startup':startup,'server':server,'client':client}})
facts['qemu_stderr_observations']=diagnostics
# Default-off journal: independently parse original schema1 records, no new
# runner verifier is imported. This run is not the opt-in recovery positive.
jname='journal-default';j=js(jname+'/summary.json');jr=lines(jname+'/server-evidence.log');jc=lines(jname+'/client-evidence.log')
seq=next(r for r in sequence['runs'] if r['name']==jname)
check('journal:terminal_source_guards',seq['exit_code']==0 and seq['source_guard'] and seq['head_guard'] and seq['worktree_clean'])
check('journal:clean_head_and_two_guest0',j['result']=='PASS' and j['git_commit']==j['git_commit_after']==HEAD and not j['working_tree_dirty'] and not j['working_tree_dirty_after'] and j['guest_exit_codes']=={'server':0,'client':0})
check('journal:source_ELF_guards',j['source_guard_passed'] and j['binary_guard_passed'] and j['source_fingerprint_sha256']==j['source_fingerprint_after_sha256'] and j['binary_sha256']==j['guest_binary_sha256']==j['binary_after_sha256']==summaries['physical-normal']['binary_sha256'])
h=hashlib.sha256(physical_hash)
for script in ['run-journal-qemu.py','guest-journal-init.sh']:
 data=inputs['scripts/qemu/'+script];h.update(script.encode());h.update(len(data).to_bytes(8,'big'));h.update(data)
 check('journal:old_harness_unchanged:'+script,data==blob('scripts/qemu/'+script,'933e720531fd96b7a23616ed687d59b816e0ce09'))
check('journal:independent_fingerprint',j['source_fingerprint_sha256']==h.hexdigest())
check('journal:legacy_policy_omits_optin',b'physical_connect_restore_device_ids' not in inputs['scripts/qemu/guest-journal-init.sh'])
check('journal:locked_all_features_build',j['build_command']==['cargo','build','--workspace','--release','--locked','--all-features'] and b'Finished `release` profile' in read(jname+'/build.log'))
for role,raw in [('server',jr),('client',jc)]:
 kr=lines(jname+'/'+role+'-serial.log')
 marker('journal:'+role+':UART',raw,f'RUSTUSB_JOURNAL_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0')
 marker('journal:'+role+':complete',raw,f'RUSTUSB_JOURNAL_COLLECTION_COMPLETE role={role}')
 marker('journal:'+role+':pass',raw,f'RUSTUSB_JOURNAL_RESULT: PASS role={role}')
 check('journal:'+role+':kernel_poweroff_and_no_errors',any('Power down' in x for x in kr) and not any(t in x for x in kr+raw for t in ['Kernel panic','BUG:','was already given back','RUSTUSB_JOURNAL_RESULT: FAIL']))
 check('journal:'+role+':no_program_marker_in_S0',not any(x.startswith('RUSTUSB_JOURNAL_') for x in kr))
 check('journal:'+role+':qemu_stderr',read(jname+'/'+role+'-qemu.stderr')==b'')
 argv=j['qemu_commands'][role];check('journal:'+role+':private_synthetic_only',any(x.startswith('socket,') and 'fd=' in x for x in argv) and not any(x.startswith(('user,','tap,','bridge,')) or 'usb-host' in x for x in argv))
jrecords=block(jr,'RUSTUSB_JOURNAL_RECORDS');jwaits=block(jr,'RUSTUSB_JOURNAL_WAITS');jobs=block(jr,'RUSTUSB_JOURNAL_OBSERVATIONS');jadmin=block(jr,'RUSTUSB_JOURNAL_ADMIN');jclients=block(jc,'RUSTUSB_JOURNAL_CLIENT')
jlabels=['initial','normal-ready','normal-pending','normal-closed','reopened-ready','reopened-closed','killed-ready','killed-pending','killed-post','denied-before-helper-restart-refused','helper-reloaded','denied-after-helper-restart-refused','unrestored-refused','guest-admin-restored','admin-retired','reconciled-ready','reconciled-pending','reconciled-closed']
check('journal:18_ordered_observations',[r['label'] for r in jrecords]==jlabels)
jby={r['label']:r for r in jrecords};jobby={r['label']:r for r in jobs};joriginal=jby['initial']['sysfs'];jid={d['device_id']:d for d in joriginal}
check('journal:original_protected_two_drivers',[d['interfaces'] for d in joriginal]==[[{'number':0,'alternate':0,'driver':'usbhid'}],[{'number':0,'alternate':0,'driver':'ftdi_sio'}]] and all(d['configuration']==1 and d['node_owner_mode']=='0:600' for d in joriginal))
check('journal:immediate_primary_records',[json.loads(x[len('RUSTUSB_JOURNAL_RECORD '):]) for x in jr if x.startswith('RUSTUSB_JOURNAL_RECORD ')]==jrecords)
check('journal:immediate_actual_waits',[json.loads(x[len('RUSTUSB_JOURNAL_WAIT '):]) for x in jr if x.startswith('RUSTUSB_JOURNAL_WAIT ')]==jwaits)
for row in jrecords:
 label=row['label'];pending=row['pending'];disk=row['journal']
 check('journal:'+label+':disk1_IPC1_manual_unchanged',disk['schema_version']==pending['schema_version']==1 and pending['next_after'] is None and disk['records']==pending['records'] and all('recovery' not in r for r in disk['records']) and 'storage_schema_version' not in pending and 'recovery_authorizations' not in pending)
 check('journal:'+label+':root_protected',row['journal_directory_owner_mode']=='0:700' and row['journal_file_owner_mode']=='0:600')
 for kind,value in [('PENDING',pending),('DISK',disk)]:
  begin=f'RUSTUSB_JOURNAL_{kind}_BEGIN label={label}';end=f'RUSTUSB_JOURNAL_{kind}_END label={label}'
  check('journal:'+label+':one_raw_'+kind,jr.count(begin)==jr.count(end)==1 and jr.index(begin)<jr.index(end))
  text='\n'.join(jr[jr.index(begin)+1:jr.index(end)])+'\n'
  check('journal:'+label+':raw_value_'+kind,json.loads(text)==value)
  if kind=='DISK':check('journal:'+label+':actual_disk_bytes_sha',digest(text.encode())==row['journal_sha256'])
for label in ['initial','normal-closed','reopened-closed','admin-retired','reconciled-closed']:
 check('journal:'+label+':empty_original',jby[label]['pending']['records']==[] and jby[label]['sysfs']==joriginal)
killrecords=jby['killed-pending']['pending']['records'];nullstate=[{**d,'interfaces':[{'number':0,'alternate':0,'driver':None}]} for d in joriginal]
for label in ['killed-pending','killed-post','denied-before-helper-restart-refused','helper-reloaded','denied-after-helper-restart-refused','unrestored-refused','guest-admin-restored']:
 check('journal:'+label+':old_grants_not_retired_or_rebased',jby[label]['pending']['records']==killrecords)
for label in ['killed-post','denied-before-helper-restart-refused','helper-reloaded','denied-after-helper-restart-refused','unrestored-refused']:
 check('journal:'+label+':drivers_remain_null',jby[label]['sysfs']==nullstate)
check('journal:only_manual_restoration_then_retire',jby['guest-admin-restored']['sysfs']==joriginal and jby['guest-admin-restored']['pending']['records']==killrecords and jby['admin-retired']['pending']['records']==[])
leases=[]
for epoch in ['normal','reopened','killed','reconciled']:
 records=jby[epoch+'-ready']['pending']['records'];identity=jobby[epoch+'-ready']['identity'];leases += [r['lease_id'] for r in records]
 check('journal:'+epoch+':two_new_grants',len(records)==2 and {r['baseline']['device_id'] for r in records}==set(jid))
 check('journal:'+epoch+':ordinary_identity',identity['uids']==[1000]*4 and identity['exe']=='/usr/bin/rustusb' and all(identity[k]==0 for k in ['cap_effective','cap_permitted','cap_ambient']))
 for record in records:
  baseline=record['baseline'];device=jid[baseline['device_id']];receipt=baseline['receipt'];nd=device['node_identity'].split(':');sg=list(map(int,device['generation'].split(':')))
  check('journal:'+epoch+':original_receipt:'+device['bus_id'],baseline['configuration']==device['configuration'] and baseline['interfaces']==device['interfaces'] and baseline['bus_id']==device['bus_id'] and [receipt['sysfs_device'],receipt['sysfs_inode']]==sg and receipt['node_inode']==int(nd[1]) and receipt['usb_device_number']==device['devnum'] and receipt['bus_number']==int(device['bus_id'].split('-')[0]))
  check('journal:'+epoch+':owner_tuple:'+device['bus_id'],record['owner']['pid']==identity['pid'] and record['owner']['start_time']==identity['starttime'] and record['owner']['uid']==1000 and bool(record['owner']['boot_id']))
check('journal:eight_distinct_grants',len(leases)==len(set(leases))==8)
jwaitby={w['label']:w for w in jwaits if w['event']=='server_wait'}
check('journal:six_server_waits',len(jwaitby)==6)
for epoch in ['normal','reopened','killed','denied-before-helper-restart','denied-after-helper-restart','reconciled']:
 start=f'RUSTUSB_JOURNAL_EVENTS_BEGIN epoch={epoch}';end=f'RUSTUSB_JOURNAL_EVENTS_END epoch={epoch}'
 check('journal:'+epoch+':unique_raw_events',jr.count(start)==jr.count(end)==1)
 ev=[json.loads(x) for x in jr[jr.index(start)+1:jr.index(end)] if x]
 if epoch in ['normal','reopened','reconciled']:
  stopped=[e for e in ev if e['event']=='stopped'];check('journal:'+epoch+':wait0_stopped0',jwaitby[epoch]['wait_code']==0 and len(stopped)==1 and ev[-1]==stopped[0] and clean(stopped[0]['snapshot']))
 elif epoch=='killed':
  w=jwaitby[epoch];check('journal:actual_KILL137_no_invented_terminal',w['wait_code']==137 and w['signal']==9 and w['signal_count']==1 and w['sender_uid']==1000 and w['final_backend_snapshot'] is None and w['final_backend_snapshot_observable'] is False and not any(e['event']=='stopped' for e in ev))
 else:check('journal:'+epoch+':refusal_not_ready',jwaitby[epoch]['wait_code']!=0 and not any(e['event']=='ready' for e in ev))
for epoch in ['normal','killed','reconciled']:
 o=jobby[epoch+'-pending'];snap=o['snapshot']
 check('journal:'+epoch+':real_pending_claims_budget',snap['buffer_bytes_reserved']>0 and len(snap['devices'])==2 and all(d['leased'] and d['pending_urbs']>=1 and d['usbfs_inflight']>=1 for d in snap['devices']) and o['sysfs']==[{**d,'interfaces':[{'number':0,'alternate':0,'driver':'usbfs'}]} for d in joriginal])
helper=[w for w in jwaits if w['event']=='helper_restart'];check('journal:real_helper_restart',len(helper)==1 and helper[0]['old_wait_code']==0 and helper[0]['old_pid']!=helper[0]['new_pid'] and helper[0]['state_directory_unchanged'])
check('journal:admin12_actual_responses',len(jadmin)==12)
for i,row in enumerate(jadmin):
 response=json.loads(row['stdout']);phase=row['phase']
 if phase=='restored-retired':check('journal:root_retire:'+str(i),row['uid']==0 and row['exit_code']==0 and response=={'lease_id':row['lease_id'],'observation':'restored','status':'retired'})
 else:
  expected='caller UID is not approved by the administrator' if row['uid']==1001 else 'physical pending records require root' if phase=='pending-denied' else 'physical reconciliation requires root' if phase=='reconcile-denied' else 'physical original configuration, full interface/alternate/driver set, or generation is not restored; unresolved grant retained'
  check('journal:precise_admin_refusal:'+str(i),row['exit_code']!=0 and response.get('ok') is False and expected in response.get('error',''))
check('journal:three_client_cycles',[c['cycle'] for c in jclients]==[1,2,3])
for c in jclients:
 cycle=c['cycle'];data=bytes.fromhex(c['input_event_hex']);ev=list(struct.iter_unpack('<qqHHi',data))
 check(f'journal:cycle{cycle}:evdev_actual_down_up',any(e[2:]==(1,cycle+1,1) for e in ev) and any(e[2:]==(1,cycle+1,0) for e in ev))
 check(f'journal:cycle{cycle}:actual_serial_bytes',bytes.fromhex(c['rx_hex'])==f'RUSTUSB-RX-{cycle}-0001:0123456789abcdef\n'.encode() and bytes.fromhex(c['tx_hex'])==f'RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n'.encode())
 check(f'journal:cycle{cycle}:spontaneous_before_detach',c['spontaneous_disconnect'] and c['detach_before_observation'] is False and c['sessions']==[])
 status=[x.split() for x in c['vhci_status'].splitlines() if x.split() and x.split()[0] in ['hs','ss']]
 check(f'journal:cycle{cycle}:VHCI_NULL',len(status)==16 and all(int(x[2])==4 and x[6]=='0-0' for x in status))
 check(f'journal:cycle{cycle}:actual_client_receipts',len(c['receipts'])==2 and all(r['status']==6 and r['local_device_address']>0 and r['local_device_generation']['inode']>0 for r in c['receipts']))
expected=b''.join(f'RUSTUSB-TX-{c}-0001:fedcba9876543210\n'.encode() for c in [1,2,3]);captured=read(jname+'/ftdi-host-tx.bin')
check('journal:actual_host_TX_105',captured==expected and len(captured)==105 and j['serial_output']['sha256']==digest(captured) and j['serial_output']['exact_match'])
check('journal:184_original_runner_checks_reported',len(j['verification']['checks'])==184 and all(x['result']=='PASS' for x in j['verification']['checks']))
facts[jname]={'scope':'default off: manual schema1 records, KILL does not recover, explicit guest admin restoration/read-only retirement then third real I/O','actual_host_TX_bytes':len(captured),'observations':len(jrecords),'raw_waits':jwaits,'helper_restart':helper,'manual_admin_responses':jadmin,'original_184_reported_pass':True}
# Four local signal runs: inspect recorded original wire bytes, proc identity,
# actual pipe observations, consumed stdout and per-case artifacts.
local=js('local-signals/summary.json');before=js('local-signals/source-before.json');after=js('local-signals/source-after.json')
check('local:clean_same_source_HEAD',before==after and before['head']==HEAD and not before['dirty'] and local['source_unchanged'])
for item in before['files']:
 data=blob(item['path']);check('local:source_blob:'+item['path'],len(data)==item['length'] and digest(data)==item['sha256'])
check('local:all_features_same_QEMU_binary',local['build']['command']==['cargo','build','--workspace','--release','--locked','--all-features'] and local['build']['returncode']==0 and local['binary_before']['sha256']==local['binary_after_sha256']==j['binary_sha256']['rustusb'] and local['binary_unchanged'])
for artifact in local['artifacts']:
 data=read('local-signals/'+artifact['path']);check('local:artifact:'+artifact['path'],len(data)==artifact['length'] and digest(data)==artifact['sha256'])
localfacts=[]
for name in ['ready-sigint','ready-sigterm','blocked-sigint','blocked-sigterm']:
 path='local-signals/'+name+'/';r=js(path+'result.json');p=js(path+'protocol.json');bef=js(path+'before.json');aft=js(path+'after.json');out=js(path+'stdout-events.json');raw=read(path+'stdout-consumed.bin')
 check('local:'+name+':reported_raw_objects',r['before']==bef and r['after']==aft and r['result']=='PASS' and r['final_returncode']==0 and r['forced_cleanup'] is False)
 check('local:'+name+':consumed_stdout_actual_JSON',[json.loads(x) for x in raw.splitlines()]==out and len(raw)==r['stdout_consumed_bytes'])
 check('local:'+name+':own_process_signal',r['child']==r['sent']['identity'] and r['child']['uids']==[1000]*4 and r['child']['starttime']>0 and r['sent']['pid']==r['child']['pid'] and r['sent']['signal']==(2 if name.endswith('sigint') else 15) and r['executed_binary']['sha256']==local['binary_before']['sha256'])
 check('local:'+name+':two_pending_leases',bef['schema_version']==1 and len(bef['snapshot']['devices'])==2 and all(d['leased'] and d['pending_urbs']==1 and d['configuration']==1 for d in bef['snapshot']['devices']))
 check('local:'+name+':wait0_and_two_real_EOF',aft['returncode']==0 and aft['wait_error'] is None and set(aft['data_sockets'])=={'1-1','1-2'} and all(v=={'eof':True,'unexpected_hex':''} for v in aft['data_sockets'].values()))
 check('local:'+name+':eight_wire_receipts',len(p)==8 and [x['operation'] for x in p]==['IMPORT','SUBMIT','RET_SUBMIT','SUBMIT']*2)
 for bus in ['1-1','1-2']:
  rows=[x for x in p if x['bus_id']==bus];imp,control,ack,pending=rows
  header=bytes.fromhex(imp['header_hex']);req=bytes.fromhex(imp['request_hex']);dev=bytes.fromhex(imp['device_hex']);submit=bytes.fromhex(control['frame_hex']);ret=bytes.fromhex(ack['frame_hex']);pendingraw=bytes.fromhex(pending['frame_hex'])
  check('local:'+name+':IMPORT_bytes:'+bus,struct.unpack('!HHI',header)==(0x111,3,0) and struct.unpack('!HHI',req[:8])==(0x111,0x8003,0) and req[8:].rstrip(b'\0')==bus.encode() and len(dev)==312 and dev[256:288].rstrip(b'\0')==bus.encode())
  check('local:'+name+':config_ACK_bytes:'+bus,len(submit)==len(ret)==48 and struct.unpack('!I',submit[:4])[0]==1 and submit[40:]==bytes.fromhex('0009010000000000') and struct.unpack('!II',ret[:8])==(3,1) and struct.unpack('!iI',ret[20:28])==(0,0))
  check('local:'+name+':pending_IN_bytes:'+bus,len(pendingraw)==48 and struct.unpack('!IIIII',pendingraw[:20])==(1,2,imp['devid'],1,1 if bus=='1-1' else 2) and int.from_bytes(pendingraw[24:28],'big')==(8 if bus=='1-1' else 64))
 if name.startswith('ready'):
  stopped=[e for e in out if e['event']=='stopped'];check('local:'+name+':visible_backend_cleanup',len(stopped)==1 and aft['stopped_events']==stopped and aft['backend_cleanup_observed'] and aft['stdout_eof'] and all(not d['leased'] and d['pending_urbs']==0 and d['configuration']==0 for d in stopped[0]['snapshot']['devices']))
 else:
  pipe=r['pipe'];padding=read(path+'synthetic-padding.bin')
  check('local:'+name+':actual_pipe_full_then_signal',pipe['bytes_after_fill']==pipe['capacity']==pipe['synthetic_padding_bytes']==len(padding)==4096 and pipe['independent_writer_eagain'] and pipe['blocked_writer']['wchan']=='pipe_write' and pipe['blocked_writer']['monotonic_ns']<r['sent']['monotonic_ns'] and pipe['child_stdout_flags_before']==pipe['child_stdout_flags_after']==pipe['original_writer_flags_before']==pipe['original_writer_flags_after']==1)
  check('local:'+name+':unread_output_no_fake_cleanup',r['stdout_drained_after_fill'] is False and aft['stdout_read_after_fill'] is False and aft['stdout_unread_bytes']==4096 and aft['backend_cleanup_observed'] is False and aft['stopped_events']==[] and not any(e['event']=='stopped' for e in out))
 localfacts.append({'case':name,'signal':r['sent']['signal'],'wait':aft['returncode'],'signal_exit_seconds':aft['signal_exit_seconds'],'two_socket_EOF':True,'terminal_backend_snapshot_observed':aft['backend_cleanup_observed']})
facts['local-signals']=localfacts
back=js('local-backpressure-runner.log');backseq=next(r for r in sequence['runs'] if r['name']=='local-backpressure')
check('backpressure:actual_sequence_exit_guards',backseq['exit_code']==0 and backseq['source_guard'] and backseq['head_guard'] and backseq['worktree_clean'])
check('backpressure:two_reported_modes',back['result']=='PASS' and back['host_uid']==1000 and [c['mode'] for c in back['checks']]==['sigint','deadline'])
check('backpressure:bounded_exit_without_stdout_drain',all(c['stdout_capacity']==4096 and c['stdout_drained_after_fill'] is False and c['lease_socket_closed'] for c in back['checks']) and back['checks'][0]['exit']==0 and back['checks'][1]['exit']==1 and back['checks'][0]['elapsed_seconds']<5 and back['checks'][1]['elapsed_seconds']<8)
facts['local-backpressure']={'raw_collector_report':back,'boundary':'Only collector JSON and frozen source/sequence are archived; no independent syscall or pipe-byte raw recording for these two older cases.'}
# Parent sequence is the independent source/binary mutation guard for older
# runners lacking their own final source/hash fields.
check('regressions_all_terminal_before_this_read',all(next(r for r in sequence['runs'] if r['name']==n)['finished_at'] for n in ['journal-default','physical-normal','physical-hot-unplug','physical-composite','physical-sigterm','local-signals','local-backpressure']))
facts['review_end_worktree_status']=subprocess.check_output(['git','status','--porcelain'],cwd=REPO,text=True).splitlines()
check('review_end_runtime_head_and_frozen_inputs_unchanged',subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()==HEAD and all((REPO/path).read_bytes()==data for path,data in inputs.items()))
facts['reviewer_guard_correction']={'initial_report':str(OUT.with_name(OUT.stem+'-initial.json')),'initial_result':'1115/1116 PASS; only reviewer final global-clean assumption failed while parent authored post-run docs','correction':'The runtime sequence proves clean before/after each finished run. This offline review now requires unchanged runtime HEAD and all152 frozen inputs, while separately recording current post-run document edits. No runtime source, assertion or artifact changed.'}
report={'schema_version':1,'reviewer':'/root/journal_harness_review','created_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head':HEAD,'base':str(BASE),'result':'PASS' if all(c['result']=='PASS' for c in checks) else 'FAIL','checks_passed':sum(c['result']=='PASS' for c in checks),'checks_total':len(checks),'checks':checks,'facts':facts,'artifacts':artifacts,'mustfix':[c for c in checks if c['result']=='FAIL'],'boundaries':['Independent offline raw parsing, no import/call of runner.verify, no Cargo/build/QEMU or repository mutation. Four physical regression sections reuse the previously reviewed independent parser with only source/run-name changes; original empty-ECM-stderr assumption was already corrected in that parser.','Reviewed five regression QEMU runs / ten guest exits, four local signal cases and two older local backpressure cases. The two new opt-in autorestore positives are reviewed separately by another agent; their PASS is not established by this report.','The default journal uses actual disk1 and IPC1, retaining null drivers and old immutable grants through helper restart; guest administrator restores, root read-only reconcile retires, third real I/O follows. No default-off automatic recovery or schema promotion occurred.','Normal/hot and TERM older collectors archive expected FTDI digest/byte assertions, not raw host FTDI sink files. Journal host105 raw bytes, journal/TERM evdev bytes, and both ECM PCAPs are independently decoded. No absent raw artifact is invented.','Four older physical regression harnesses do not independently archive per-grant disk journal records; normal wait0/stopped exercises the guarded product close-and-Finish path, but record retirement there is inferred from that path rather than directly read.','Local blocked signal cases prove process exit and two data socket EOFs with stdout still unread; terminal backend cleanup snapshot remains unobserved. The two older backpressure cases provide collector JSON, not an independently recorded syscall trace.','Only recorded Linux/QEMU synthetic devices and local virtual tests: no real hardware G1/G2, Windows support, kernel-internal active-syscall window, all-FD capability revocation or power-loss durability established.']}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'path':str(OUT),'result':report['result'],'passed':report['checks_passed'],'total':len(checks),'bytes':OUT.stat().st_size,'sha256':digest(OUT.read_bytes()),'failures':report['mustfix']},ensure_ascii=False))
