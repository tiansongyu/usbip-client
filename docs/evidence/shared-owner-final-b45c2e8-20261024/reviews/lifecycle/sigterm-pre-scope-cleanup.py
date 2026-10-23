"""Harness-author raw reparse; no runner import, verify call, build or QEMU."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

REPO = Path('/home/ubuntu/github/usbip-client')
HEAD='b45c2e8c67a06396360fee0af6c0a092a950a6ff'
EXPECTED_BINARIES=None
PATHS={}
OUTPUT=None
RX = b'RUSTUSB-RX-1-0001:0123456789abcdef\n'
TX = b'RUSTUSB-TX-1-0001:fedcba9876543210\n'
checks, runs, artifacts = [], {}, []

def test(name, value, detail=None):
    item = {'name': name, 'result': 'PASS' if value else 'FAIL'}
    if detail is not None:
        item['observed'] = detail
    checks.append(item)

def one_json(lines, prefix):
    values = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
    if len(values) != 1:
        raise ValueError(f'one exact {prefix} record required, got {len(values)}')
    return values[0]

def block(lines, name):
    start, end = f'RUSTUSB_SIGTERM_{name}_BEGIN', f'RUSTUSB_SIGTERM_{name}_END'
    if lines.count(start) != 1 or lines.count(end) != 1:
        raise ValueError(f'{name}: exact block boundaries required')
    left, right = lines.index(start), lines.index(end)
    if left >= right:
        raise ValueError('reversed block boundaries')
    return [json.loads(line) for line in lines[left+1:right]]

def verify_provenance(directory):
 summary=json.loads((directory/'summary.json').read_bytes())
 batch=json.loads((directory.parent/'results.json').read_bytes())
 terminal=[r for r in batch if r['case']=='sigterm']
 test('terminal_wrapper_receipt',len(terminal)==1 and terminal[0]['exit_code']==0 and terminal[0]['unchanged'] is True and terminal[0]['summary_result']=='PASS')
 test('root_expected_ELFs',summary['binary_sha256']==EXPECTED_BINARIES)
 names=subprocess.check_output(['git','ls-tree','-r','--name-only',HEAD],cwd=REPO,text=True).splitlines()
 def data(p):return subprocess.check_output(['git','show',HEAD+':'+p],cwd=REPO)
 native=['Cargo.toml','Cargo.lock','scripts/qemu/guest-init.sh','scripts/qemu/run-native-qemu.py']+[p for p in names if p.startswith('crates/') and (p.endswith('.rs') or Path(p).name=='Cargo.toml')]
 h=hashlib.sha256()
 for p in sorted(native,key=Path):
  n=p.encode();b=data(p);h.update(len(n).to_bytes(8,'big'));h.update(n);h.update(len(b).to_bytes(8,'big'));h.update(b)
 h=hashlib.sha256(h.digest())
 for n in ['run-physical-qemu.py','guest-physical-init.sh','physical-wire-probe.rs']:
  b=data('scripts/qemu/'+n);h.update(n.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
 vendor=[p for p in names if p.startswith('vendor/') and (p.endswith('.rs') or Path(p).name in ['Cargo.toml','Cargo.lock'])]
 for p in sorted(vendor,key=Path):
  b=data(p);h.update(p.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
 h=hashlib.sha256(h.digest())
 for n in ['run-sigterm-qemu.py','guest-sigterm-init.sh']:
  b=data('scripts/qemu/'+n);h.update(n.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
 test('independent_complete_layered_source_fingerprint',summary['source_fingerprint_sha256']==h.hexdigest())
 test('actual_locked_release_finished',b'Finished `release` profile' in (directory/'build.log').read_bytes())
 return {'git':HEAD,'fingerprint_sha256':h.hexdigest(),'native_source_files':len(native),'vendor_source_files':len(vendor)}

def execute():
 for label, directory in PATHS.items():
     for path in sorted(directory.rglob('*')):
         if path.is_file():
             data = path.read_bytes()
             artifacts.append({'run': label, 'path': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'provenance': 'unchanged original runtime artifact'})
     summary = json.loads((directory / 'summary.json').read_bytes())
     program = {role: (directory / f'{role}-evidence.log').read_bytes().decode('utf-8').splitlines() for role in ['server', 'client']}
     kernel = {role: (directory / f'{role}-serial.log').read_bytes().decode('utf-8') for role in ['server', 'client']}
     server = one_json(program['server'], 'RUSTUSB_SIGTERM_OUTCOME ')
     client = one_json(program['client'], 'RUSTUSB_SIGTERM_OUTCOME ')
     startup = one_json(program['server'], 'RUSTUSB_SIGTERM_STARTUP_OUTCOME ')
     events = block(program['server'], 'EVENTS')
     observations = block(program['server'], 'OBSERVATIONS')
     observed = {item['label']: item for item in observations}
     snaps = {event['id']: event['snapshot'] for event in events if event.get('event') == 'snapshot'}
     before = snaps[server['pre_snapshot_id']]
     stopped = [event for event in events if event.get('event') == 'stopped']
     original = observed['baseline']['sysfs']
     after = observed['post-term']['sysfs']
     wanted_wait = 143 if label == 'old' else 0
     test(f'{label}:runtime_result_preserved', summary['result'] == ('FAIL' if label == 'old' else 'PASS'))
     test(f'{label}:both_guest_exit_zero', summary['guest_exit_codes'] == {'server': 0, 'client': 0})
     test(f'{label}:default_locked_build', summary['build_performed'] is True and summary['build_command'] == ['cargo', 'build', '--workspace', '--release', '--locked', '--all-features'])
     test(f'{label}:source_guard_exact', summary['source_guard_passed'] is True and summary['source_fingerprint_sha256'] == summary['source_fingerprint_after_sha256'])
     test(f'{label}:binary_guard_exact', summary['binary_guard_passed'] is True and summary['binary_sha256'] == summary['binary_after_sha256'] == summary['guest_binary_sha256'])
     test(f'{label}:head_unchanged', summary['git_commit'] == summary['git_commit_after'])
     if label == 'fixed':
         test('fixed:clean_exact_commit', summary['git_commit'] == HEAD and summary['working_tree_dirty'] is False and summary['working_tree_dirty_after'] is False)
     else:
         test('old:dirty_development_not_reclassified', summary['working_tree_dirty'] is True and summary['working_tree_dirty_after'] is True)
     for name, digest in summary['script_sha256'].items():
         data = (directory / 'source' / name).read_bytes()
         test(f'{label}:exact_script_snapshot:{name}', hashlib.sha256(data).hexdigest() == digest)
         if label == 'fixed':
             blob = subprocess.check_output(['git', 'show', summary['git_commit'] + ':scripts/qemu/' + name], cwd=REPO)
             test(f'fixed:script_git_blob:{name}', blob == data)
     for role in ['server', 'client']:
         lines = program[role]
         beacon = f'RUSTUSB_SIGTERM_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0'
         test(f'{label}:{role}:exact_channel_beacon', [line for line in lines if line.startswith('RUSTUSB_SIGTERM_EVIDENCE_CHANNEL:')] == [beacon])
         complete = f'RUSTUSB_SIGTERM_COLLECTION_COMPLETE role={role}'
         test(f'{label}:{role}:complete_capture', lines.count(complete) == 1)
         result = f'RUSTUSB_SIGTERM_RESULT: {"FAIL" if label == "old" and role == "server" else "PASS"} role={role}'
         matches = [index for index, line in enumerate(lines) if line.startswith(result)]
         test(f'{label}:{role}:final_result_after_collection', len(matches) == 1 and matches[0] > lines.index(complete))
         test(f'{label}:{role}:no_integrity_failure_both_channels', not any(text in log for text in ['Kernel panic', 'BUG:', 'was already given back', 'seqnum max'] for log in [kernel[role], '\n'.join(lines)]))
         test(f'{label}:{role}:private_network_marker', f'RUSTUSB_SIGTERM_ISOLATION: PASS role={role} carrier=1 ip_forward=0' in lines)
     test(f'{label}:raw_schema', all(item.get('schema_version') == 1 for item in [server, client, startup, *events]))
     test(f'{label}:startup_actual_wait', startup['wait_code'] == wanted_wait, startup['wait_code'])
     test(f'{label}:startup_ordinary_owned_pid', startup['pid'] > 1 and startup['pid'] != server['pid'] and startup['starttime'] > 0 and startup['uid'] == startup['sender_uid'] == 1000)
     test(f'{label}:startup_one_term', startup['signal'] == 15 and startup['signal_count'] == 1)
     test(f'{label}:startup_exited_under_three_second_observation', startup['process_exit_observed'] is True and startup['observation_limit_seconds'] == 3 and 0 <= startup['observation_centiseconds'] <= 300)
     test(f'{label}:startup_helper_stop_cont_and_no_acceptance_claim', startup['helper_stopped_before_signal'] is True and startup['helper_continued'] is True and startup['helper_acceptance_observed'] is False)
     test(f'{label}:startup_no_ready_before_term', startup['ready_before_signal'] is False and block(program['server'], 'STARTUP_EVENTS') == [])
     inode = startup['connected_unix_inode']
     rows = [line.split() for line in startup['unix_ss_observation'].splitlines()]
     listener_rows = [fields for fields in rows if len(fields) >= 8 and fields[0] == 'u_str' and fields[1] == 'LISTEN' and fields[4] == '/run/rustusb/control.sock' and f'pid={startup["helper_pid"]},' in ' '.join(fields)]
     test(f'{label}:one_owned_roothelper_listener', len(listener_rows) == 1)
     listener = listener_rows[0]
     test(f'{label}:helper_backlog_contains_exact_client_inode', 'peers:' in listener and inode in listener[listener.index('peers:')+1:], {'listener_inode': listener[5], 'client_inode': inode})
     client_rows = [fields for fields in rows if len(fields) >= 8 and fields[1] == 'ESTAB' and fields[5] == inode and f'pid={startup["pid"]},fd={startup["connected_unix_fd"]}' in ' '.join(fields)]
     test(f'{label}:client_pid_fd_inode_correlated', len(client_rows) == 1)
     proc_rows = [line.split() for line in startup['unix_observation'].splitlines()]
     test(f'{label}:proc_unix_established_client', any(len(fields) >= 7 and fields[4] == '0001' and fields[5] == '03' and fields[6] == inode for fields in proc_rows))
     test(f'{label}:proc_unix_named_listener', any(len(fields) >= 8 and fields[3] == '00010000' and fields[5] == '01' and fields[6] == listener[5] and fields[7] == '/run/rustusb/control.sock' for fields in proc_rows))
     tcp = [line.split() for text in [startup['tcp_observation'], startup['tcp6_observation']] for line in text.splitlines()]
     test(f'{label}:startup_no_3240_listener', startup['listener_before_signal'] is False and not any(len(fields) > 3 and fields[1].endswith(':0CA8') and fields[3] == '0A' for fields in tcp))
     test(f'{label}:startup_original_sysfs_unchanged', startup['sysfs_unchanged'] is True and startup['sysfs'] == original)
     test(f'{label}:main_actual_wait', server['wait_code'] == wanted_wait, server['wait_code'])
     test(f'{label}:main_one_term_no_shutdown_or_stdin_eof', server['signal'] == 15 and server['signal_count'] == 1 and server['sender_uid'] == 1000 and server['stdin_kept_open'] is True and server['shutdown_control_sent'] is False)
     test(f'{label}:main_process_and_fd_table_gone', server['process_exit_observed'] is True and server['pid_absent'] is True and server['fd_table_absent'] is True)
     test(f'{label}:both_helpers_alive', server['helper_alive'] is True and client['helper_alive'] is True)
     test(f'{label}:all_four_independent_observations', len(observations) == 4 and set(observed) == {'baseline', 'held', 'term-pending', 'post-term'})
     test(f'{label}:real_two_device_pending_budget', len(before['devices']) == 2 and before['buffer_bytes_reserved'] > 0)
     for index, original_driver in enumerate(['usbhid', 'ftdi_sio']):
         base = original[index]
         dev_id = base['device_id']
         pre = next(item for item in before['devices'] if item['device_id'] == dev_id)
         test(f'{label}:{original_driver}:original_root0600_cfg1_alt0', base['node_owner_mode'] == '0:600' and base['configuration'] == 1 and base['interfaces'] == [{'number': 0, 'alternate': 0, 'driver': original_driver}] and base['generation'] and base['node_identity'])
         test(f'{label}:{original_driver}:real_leased_pending_inflight', pre['state'] == 'leased' and pre['leased'] is True and pre['pending_urbs'] >= 1 and pre['usbfs_inflight'] >= 1)
         for phase in ['held', 'term-pending']:
             obs = observed[phase]
             test(f'{label}:{original_driver}:{phase}:same_generation_claim', obs['sysfs'][index] == {**base, 'interfaces': [{'number': 0, 'alternate': 0, 'driver': 'usbfs'}]})
             identity = obs['identity']
             test(f'{label}:{original_driver}:{phase}:ordinary_owned_pid', identity['pid'] == server['pid'] and identity['starttime'] == server['starttime'] and identity['uids'] == [1000]*4 and identity['exe'] == '/usr/bin/rustusb' and all(identity[name] == 0 for name in ['cap_effective', 'cap_permitted', 'cap_ambient']))
             test(f'{label}:{original_driver}:{phase}:actual_authorized_fd', any(fd['node'] == base['node'] and fd['identity'] == base['node_identity'] for fd in obs['fds']))
         expected_after = base if label == 'fixed' else {**base, 'interfaces': [{'number': 0, 'alternate': 0, 'driver': None}]}
         test(f'{label}:{original_driver}:independent_post_term_sysfs', after[index] == expected_after)
         test(f'{label}:{original_driver}:actual_controls', pre['counters']['controls'] > 0)
         test(f'{label}:{original_driver}:actual_data_completion', pre['counters']['interrupt_in'] >= 2 if index == 0 else pre['counters']['bulk_in'] > 0 and pre['counters']['bulk_out'] > 0 and pre['counters']['bytes_out'] == 35)
     test(f'{label}:server_restore_result_not_reclassified', server['restored_sysfs'] is (label == 'fixed') and server['within_observation_window'] is (label == 'fixed'))
     test(f'{label}:observed_window', server['observation_limit_seconds'] == 20 and (server['observation_centiseconds'] >= 2000 if label == 'old' else 0 <= server['observation_centiseconds'] < 2000), server['observation_centiseconds'])
     test(f'{label}:events_no_error', all(event['event'] != 'error' for event in events))
     test(f'{label}:one_first_ready', events[0]['event'] == 'ready' and sum(event['event'] == 'ready' for event in events) == 1)
     test(f'{label}:unique_snapshots', len(snaps) == sum(event['event'] == 'snapshot' for event in events))
     test(f'{label}:stopped_count_preserved', len(stopped) == (0 if label == 'old' else 1))
     reap_changes = []
     if label == 'fixed':
         final = stopped[0]['snapshot']
         test('fixed:stopped_is_final_event', events[-1] == stopped[0])
         test('fixed:stopped_global_budget_zero', final['schema_version'] == 1 and final['buffer_bytes_reserved'] == 0 and len(final['devices']) == 2)
         for current in final['devices']:
             pre = next(item for item in before['devices'] if item['device_id'] == current['device_id'])
             test(f'fixed:{current["bus_id"]}:stopped_resources_zero_available', current['state'] == 'available' and current['leased'] is False and current['cleanup_verified'] is True and current['pending_urbs'] == current['queued_urbs'] == current['usbfs_inflight'] == 0)
             test(f'fixed:{current["bus_id"]}:stopped_original_drivers', current['current_drivers'] == current['original_drivers'] == pre['original_drivers'])
             test(f'fixed:{current["bus_id"]}:actual_reap_growth_monotonic_cancel', current['usbfs_reaped'] > pre['usbfs_reaped'] and current['cancelled'] >= pre['cancelled'])
             reap_changes.append({'bus_id': current['bus_id'], 'reaped_before': pre['usbfs_reaped'], 'reaped_after': current['usbfs_reaped'], 'cancelled_before': pre['cancelled'], 'cancelled_after': current['cancelled']})
     else:
         test('old:no_fabricated_post_term_backend_zero', observed['post-term']['snapshot'] is None)
     test(f'{label}:client_spontaneous_disconnect_no_prior_detach', client['spontaneous_disconnect'] is True and client['detach_before_observation'] is False and client['sessions'] == [] and client['hid_generation_gone'] is True and client['ftdi_generation_gone'] is True)
     receipts = client['receipts']
     test(f'{label}:two_complete_distinct_receipts', len(receipts) == 2 and receipts[0]['port'] != receipts[1]['port'] and receipts[0]['local_bus_id'] != receipts[1]['local_bus_id'] and all(item['status'] == 6 and item['local_device_address'] > 0 and item['local_device_generation']['filesystem_device'] > 0 and item['local_device_generation']['inode'] > 0 for item in receipts))
     vhci = [line.split() for line in client['vhci_status'].splitlines() if line.split() and line.split()[0] in ['hs', 'ss']]
     test(f'{label}:raw_all_vhci_null', len(vhci) == 16 and len({int(row[1]) for row in vhci}) == 16 and all(len(row) == 7 and int(row[2]) == 4 and row[6] == '0-0' for row in vhci))
     test(f'{label}:both_receipt_ports_explicitly_null', all(any(int(row[1]) == receipt['port'] and int(row[2]) == 4 for row in vhci) for receipt in receipts))
     key_bytes = bytes.fromhex(client['input_event_hex'])
     key_events = list(struct.iter_unpack('<qqHHi', key_bytes))
     down = [index for index, item in enumerate(key_events) if item[2:] == (1, 2, 1)]
     up = [index for index, item in enumerate(key_events) if item[2:] == (1, 2, 0)]
     test(f'{label}:actual_key_one_press_release_in_order', client['input_event_format'] == 'linux_x86_64_24' and len(down) == len(up) == 1 and down[0] < up[0])
     test(f'{label}:exact_serial_client_rx35_sha', client['serial_in_bytes'] == len(RX) and client['rx_sha256'] == hashlib.sha256(RX).hexdigest())
     test(f'{label}:exact_serial_client_tx35_sha', client['serial_out_bytes'] == len(TX) and client['tx_sha256'] == hashlib.sha256(TX).hexdigest())
     sink = summary['serial_output']
     test(f'{label}:host_sink_runtime_exact35_observation', sink['expected_bytes'] == sink['received_bytes'] == summary['serial_received_bytes'] == 35 and sink['sha256'] == summary['serial_received_sha256'] == hashlib.sha256(TX).hexdigest() and sink['exact_match'] is True)
     test(f'{label}:exact_required_injections', summary['injections'] == ['key-1', 'serial-1'])
     busy = [json.loads(line) for line in program['client'] if line.startswith('{"event":"physical_probe_case"')]
     test(f'{label}:both_devices_busy_real_probe', busy == [{'event': 'physical_probe_case', 'case': 'both_real_devices_exclusive_busy', 'result': 'PASS'}])
     runs[label] = {'path': str(directory), 'runtime_result': summary['result'], 'git_commit': summary['git_commit'], 'working_tree_dirty': summary['working_tree_dirty'], 'startup_wait_code': startup['wait_code'], 'main_wait_code': server['wait_code'], 'main_observation_centiseconds': server['observation_centiseconds'], 'startup_client_inode': inode, 'startup_child_pid': startup['pid'], 'helper_pid': startup['helper_pid'], 'main_pid': server['pid'], 'pre_budget_bytes': before['buffer_bytes_reserved'], 'pre_devices': [{'bus_id': item['bus_id'], 'pending_urbs': item['pending_urbs'], 'queued_urbs': item['queued_urbs'], 'usbfs_inflight': item['usbfs_inflight']} for item in before['devices']], 'post_sysfs': after, 'stopped_count': len(stopped), 'reap_changes': reap_changes, 'raw_client_vhci_rows': len(vhci), 'key_input_events': len(key_events), 'host_sink': sink, 'script_sha256': summary['script_sha256'], 'binary_sha256': summary['binary_sha256'], 'guest_exit_codes': summary['guest_exit_codes']}
 
 
 report = {'schema_version': 1, 'result': 'PASS' if all(item['result'] == 'PASS' for item in checks) else 'FAIL',
           'reviewer_role': 'author of run-sigterm-qemu.py and guest-sigterm-init.sh',
           'review_kind': 'separate raw reparse by harness author; not independent implementation review',
           'method': 'Reuse historical separate parser lifecycle assertions; standard-library raw text/input_event/SHA256, exact source fingerprint and Git blobs; no runner import or runner.verify' ,
           'scope': 'shared-owner completed legacy SIGTERM group only; historical old/fixed results are not rerun or reattributed',
           'limits': ['helper backlog proves queued connection, not application accept/authorization', 'OS exit/FD close alone is not backend cleanup', 'old missing stopped leaves final adapter resources/reap unobservable, not proof of a permanent kernel leak', '20.04s old capture is bounded observation, not proof drivers can never restore', 'centisecond elapsed fields are observations, not benchmarks', 'host FTDI sink bytes are not a separate persisted binary artifact; this review correlates the runtime exact-byte/digest observation with client digest, injection count and adapter OUT35', 'SIGKILL, kernel-internal active ioctl, real USB hardware and repeated-signal cleanup remain outside this comparison'],
           'check_count': len(checks), 'passed': sum(item['result'] == 'PASS' for item in checks), 'checks': checks, 'runs': runs,
           'input_artifact_count': len(artifacts), 'input_artifacts': artifacts}
 report['review_script'] = {'path': str(Path(__file__)), 'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
 report['reuse']={'parser':'/tmp/rustusb-sigterm-fixed-qemu-author-raw-review-20261023.py','sha256':'a100c3410b7eacd9d0777f755e213689bfaad3deb02768dc4fdba19a5e3a87cf','changes':'Single new dataset and exact source/ELF provenance; old negative comparison omitted, fixed lifecycle assertions unchanged.'}
 OUTPUT.write_text(json.dumps(report, indent=2) + '\n')
 print(json.dumps({'result': report['result'], 'checks': len(checks), 'failed': [item['name'] for item in checks if item['result'] == 'FAIL'], 'bytes': OUTPUT.stat().st_size, 'sha256': hashlib.sha256(OUTPUT.read_bytes()).hexdigest(), 'path': str(OUTPUT)}, indent=2))

def main():
 global OUTPUT,PATHS,EXPECTED_BINARIES
 p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--expected-binaries',type=Path,required=True);a=p.parse_args()
 if a.output.exists():raise FileExistsError('preserve previous review result')
 EXPECTED_BINARIES=json.loads(a.expected_binaries.read_text())
 if set(EXPECTED_BINARIES)!={'rustusb','rustusb-helper'} or not all(re.fullmatch('[0-9a-f]{64}',v) for v in EXPECTED_BINARIES.values()):raise ValueError('exact completed-build locked ELF map required')
 OUTPUT=a.output;PATHS={'fixed':a.evidence.resolve()}
 try:
  verify_provenance(a.evidence.resolve());execute()
 except Exception as e:
  OUTPUT.write_text(json.dumps({'result':'FAIL','review_kind':'separate raw parser; not new USB execution','error':type(e).__name__+': '+str(e),'checks':checks,'input_artifacts':artifacts,'parser_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},indent=2)+'\n')
 report=json.loads(OUTPUT.read_bytes());return int(report['result']!='PASS')
if __name__=='__main__':raise SystemExit(main())
