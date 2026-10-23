#!/usr/bin/env python3
"""Independent offline raw review; does not import or invoke a QEMU verifier."""
from pathlib import Path
import collections, datetime, hashlib, json, struct, subprocess, socket
BASE=Path('/tmp/rustusb-journal-final-933e720-20261023')
REPO=Path('/home/ubuntu/github/usbip-client')
OUT=Path('/tmp/rustusb-journal-regression-independent-review-20261023.json')
HEAD='933e720531fd96b7a23616ed687d59b816e0ce09'
OLD='a484d116b7d6acbc80ec7d84ab76b25707838115'
checks=[]; artifacts=[]; facts={}
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
check('sequence_terminal',sequence['head']==HEAD and sequence['result']=='PASS' and len(sequence['runs'])==7)
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
physical_hash=h.digest(); fingerprints={'physical-normal':h.hexdigest(),'physical-hot':h.hexdigest()}
h=hashlib.sha256(native_hash)
for p in sorted(vendor+['scripts/qemu/'+name for name in ['run-composite-qemu.py','guest-composite-init.sh','composite-wire-probe.rs']],key=Path):
    data=inputs[p];h.update(p.encode());h.update(len(data).to_bytes(8,'big'));h.update(data)
fingerprints['composite']=h.hexdigest();h=hashlib.sha256(physical_hash)
for name in ['run-sigterm-qemu.py','guest-sigterm-init.sh']:
    data=inputs['scripts/qemu/'+name];h.update(name.encode());h.update(len(data).to_bytes(8,'big'));h.update(data)
fingerprints['sigterm']=h.hexdigest()
summaries={};logs={};events={}
for name,prefix in [('physical-normal','PHYSICAL'),('physical-hot','PHYSICAL'),('composite','COMPOSITE'),('sigterm','SIGTERM')]:
    s=js(name+'/summary.json');summaries[name]=s
    row=next(row for row in sequence['runs'] if row['name']==name)
    check(name+':actual_sequence_exit_and_guards',row['exit_code']==0 and row['sources_unchanged'] and row['head_unchanged'] and row['worktree_clean'])
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
        program=kernel if name=='composite' else lines(f'{name}/{role}-evidence.log')
        roles[role]=program
        check(name+':'+role+':qemu_stderr_empty',read(f'{name}/{role}-qemu.stderr')==b'')
        check(name+':'+role+':kernel_poweroff',any('Power down' in line for line in kernel))
        check(name+':'+role+':no_integrity_failure',not any(term in line for line in kernel+program for term in ['Kernel panic','BUG:','was already given back','seqnum max',f'RUSTUSB_{prefix}_RESULT: FAIL']))
        marker(name+':'+role+':actual_guest_pass',program,f'RUSTUSB_{prefix}_RESULT: PASS role={role}')
        argv=s['qemu_commands'][role]
        check(name+':'+role+':private_qemu_network',not any(arg.startswith(('user,','tap,','bridge,')) for arg in argv) and any(arg.startswith('socket,') and 'fd=' in arg for arg in argv) and not any('usb-host' in arg for arg in argv))
        if name!='composite': marker(name+':'+role+':separate_evidence_UART',program,f'RUSTUSB_{prefix}_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0')
    logs[name]=roles
    ev=block(roles['server'],f'RUSTUSB_{prefix}_EVENTS');events[name]=ev
    reported=s['verification']['server_events'] if name=='sigterm' else s['server_events']
    check(name+':actual_events_match_summary',ev==reported)
    check(name+':event_schema_no_errors',all(e['schema_version']==1 and e['event']!='error' for e in ev))
    check(name+':unique_ready_stopped_and_final',sum(e['event']=='ready' for e in ev)==1 and sum(e['event']=='stopped' for e in ev)==1 and ev[-1]['event']=='stopped')
    check(name+':known_finish_path_present',b'let snapshot = backend.close().await?;' in inputs['crates/rustusb-cli/src/physical_server.rs'] and b'finish_leases(socket, lease_ids).await?;' in inputs['crates/rustusb-cli/src/physical_server.rs'])
    check(name+':final_resource_state',clean(ev[-1]['snapshot'],('available','gone') if name=='physical-hot' else ('available',)))
    facts[name]={'runtime_result':s['result'],'guest_exit_codes':s['guest_exit_codes'],'source_fingerprint_sha256':fingerprints[name],'ELF':s['binary_sha256'],'final_snapshot':ev[-1]['snapshot'],'probe_cases':[]}
check('same_ELF_all_four_regressions',len({json.dumps(s['binary_sha256'],sort_keys=True) for s in summaries.values()})==1)
for name in ['physical-normal','physical-hot','composite']:
    s=summaries[name]; raw=logs[name]; ev=events[name];snaps={r['id']:r['snapshot']for r in ev if r['event']=='snapshot'}
    probe_kind='composite_probe_case' if name=='composite' else 'physical_probe_case'
    probes=[json.loads(line) for line in raw['client'] if line.startswith('{"event":"'+probe_kind+'"')]
    expected_count=19 if name=='composite' else 15
    check(name+':exact_raw_probe_receipts',probes==s['protocol_probe_cases'] and len(probes)==expected_count and all(p['result']=='PASS' for p in probes))
    facts[name]['probe_cases']=probes
    check(name+':baseline_idle',clean(snaps['baseline']))
    for cycle in [1,2]:
        held=snaps[f'held-{cycle}']; io=snaps[f'io-{cycle}']; released=snaps[f'released-{cycle}']
        check(name+f':cycle{cycle}:actual_import_claims',held['buffer_bytes_reserved']>0 and all(d['state']=='leased' and d['leased'] and d['usbfs_inflight']>0 and all(x['driver']=='usbfs' for x in d['current_drivers']) for d in held['devices']))
        check(name+f':cycle{cycle}:released_original',clean(released))
        for before,after,end in zip(held['devices'],io['devices'],released['devices']):
            check(name+f':cycle{cycle}:io_then_reap:'+before['bus_id'],after['usbfs_reaped']>before['usbfs_reaped'] and end['usbfs_reaped']>=after['usbfs_reaped'] and after['counters']['bytes_in']>before['counters']['bytes_in'])
        if name.startswith('physical'):
            marker(name+f':cycle{cycle}:HID',raw['client'],f'RUSTUSB_PHYSICAL_HID: PASS cycle={cycle} key_code={cycle+1} down=true up=true same_receipt=true')
            marker(name+f':cycle{cycle}:FTDI',raw['client'],f'RUSTUSB_PHYSICAL_FTDI: PASS cycle={cycle} in=35 out=35 exact_bytes=true tty_echo=false same_receipt=true')
            marker(name+f':cycle{cycle}:client_released',raw['client'],f'RUSTUSB_PHYSICAL_CLIENT_RELEASED: PASS cycle={cycle} kernel_sessions=0 both_devices_removed=true')
        else:
            marker(name+f':cycle{cycle}:external_baseline_restored',raw['server'],f'RUSTUSB_COMPOSITE_RELEASED: PASS cycle={cycle} config=1 alt0=0 alt1=1 drivers=cdc_ether inflight=0 budget=0')
            marker(name+f':cycle{cycle}:actual_udp',raw['client'],f'RUSTUSB_COMPOSITE_IO: PASS cycle={cycle} exact_udp=true same_receipt=true')
    if name.startswith('physical'):
        cycles=range(1,5) if name=='physical-hot' else range(1,3)
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
hot=logs['physical-hot'];hev=events['physical-hot'];hs={r['id']:r['snapshot']for r in hev if r['event']=='snapshot'}
for label in ['unplug-gone','unplug-io','unplug-released','replug-stale']:
    devs=hs[label]['devices'];check('hot:'+label+':old_slot_stays_gone',len(devs)==2 and devs[0]['state']=='gone' and devs[0]['leased'] is False and devs[0]['usbfs_inflight']==0)
check('hot:remaining_FTDI_actual_transfer_after_HID_gone',hs['unplug-io']['devices'][1]['counters']['bytes_out']>hs['unplug-gone']['devices'][1]['counters']['bytes_out'])
for role,text in [('server','RUSTUSB_PHYSICAL_REINSERT_STALE: PASS new_generation=true old_slot_gone=true nodes=root0600'),('server','RUSTUSB_PHYSICAL_STOPPED: PASS exit=0 ftdi_driver=ftdi_sio gone_hid=true inflight=0'),('client','RUSTUSB_PHYSICAL_FTDI_CONTINUES: PASS before=35 after=35 exact_bytes=true same_receipt=true')]: marker('hot:marker:'+text,hot[role],text)
check('hot:actual_QMP_removed_kbd',any(row['event']=='DEVICE_DELETED' and row['data']['device']=='physical-kbd'for row in summaries['physical-hot']['qmp_events']))
# Decode both private Ethernet captures directly, with IPv4 and UDP checksum checks.
def folded(data):
    if len(data)%2:data+=b'\0'
    total=sum(struct.unpack('!%dH'%(len(data)//2),data))
    while total>>16:total=(total&65535)+(total>>16)
    return total
packets={}
for cap in ['data-usb.pcap','data-peer.pcap']:
    data=read('composite/'+cap);meta=summaries['composite']['private_data_captures'][cap]
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
facts['composite']['independent_pcap']=packets
# TERM raw outcomes and independent same-generation sysfs checks.
term=logs['sigterm'];s=summaries['sigterm'];tev=events['sigterm']
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
facts['sigterm'].update({'startup_wait_code':startup['wait_code'],'main_wait_code':server['wait_code'],'server_observation_centiseconds':server['observation_centiseconds'],'client_observation_centiseconds':client['observation_centiseconds'],'raw_outcomes':{'startup':startup,'server':server,'client':client}})
report={'schema_version':1,'reviewer':'/root/journal_integration_review','created_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head':HEAD,'base':str(BASE),'result':'PASS'if all(c['result']=='PASS'for c in checks)else'FAIL','checks_passed':sum(c['result']=='PASS'for c in checks),'checks_total':len(checks),'checks':checks,'facts':facts,'artifacts':artifacts,'boundaries':['Offline independent parsing only: no runner.verify, imports of test runners, Cargo build or QEMU execution.','Four regression runtimes were terminal in sequence.json before review. This report does not validate the separate new journal scenario or the two local-only runs.','Original normal/hot/ECM/TERM guest/probe scripts are unchanged from a484d11. Normal wait0/stopped evidence exercises close-and-Finish in frozen product source; these older regression harnesses do not independently archive per-grant journal records, so record retirement in these four runs is inferred through that guarded success path, not direct disk evidence.','Physical normal/hot and TERM do not archive raw host FTDI sink payload files. Exact byte observations rely on preserved collector digests plus frozen guest cmp/assertions; this report does not invent absent raw bytes. TERM evdev and ECM private Ethernet PCAP bytes were independently decoded.','All four runs are all-features Linux x86_64/QEMU simulated USB devices using the recorded 6.8.0-106-generic guest kernel. No host USB, real hardware G1/G2, Windows support, automatic SIGKILL recovery, arbitrary FD revocation or power-loss durability is established.','Existing configuration-barrier probe receipts are retained; they do not establish the separate Linux-internal blocked-syscall window, successful clear-halt on arbitrary hardware or general ISO support.']}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'path':str(OUT),'result':report['result'],'passed':report['checks_passed'],'total':len(checks),'bytes':OUT.stat().st_size,'sha256':digest(OUT.read_bytes()),'failures':[c for c in checks if c['result']=='FAIL']},ensure_ascii=False))
