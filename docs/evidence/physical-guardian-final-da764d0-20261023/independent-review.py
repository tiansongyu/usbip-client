#!/usr/bin/env python3
"""Offline independent raw review. No runner imports, builds or device access."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=Path('/tmp/rustusb-guardian-da764d0-20261023'))
    parser.add_argument('--repo', type=Path, default=Path('/home/ubuntu/github/usbip-client'))
    parser.add_argument('--output', type=Path, default=Path('/tmp/rustusb-guardian-second-independent-review-20261023.json'))
    args = parser.parse_args()
    run = args.base / 'run'
    files = [args.base / 'freeze.json', args.base / 'runner.log']
    files += sorted(path for path in run.rglob('*') if path.is_file())
    raw = {str(path.relative_to(args.base)): path.read_bytes() for path in files}
    freeze = json.loads(raw['freeze.json'])
    summary = json.loads(raw['run/summary.json'])
    head = freeze['head']
    source = {path: subprocess.check_output(['git', 'show', head + ':' + path], cwd=args.repo)
              for path in freeze['files']}
    program = {role: raw[f'run/{role}-evidence.log'].decode() for role in ['server', 'client']}
    kernel = {role: raw[f'run/{role}-serial.log'].decode() for role in ['server', 'client']}
    checks = []

    def check(name, condition, detail=None):
        checks.append({'id': name, 'result': 'PASS' if condition else 'FAIL', 'detail': detail})
        if not condition:
            raise AssertionError(name)

    def block(text, tag):
        lines = text.splitlines()
        begin = f'RUSTUSB_JOURNAL_{tag}_BEGIN'
        end = f'RUSTUSB_JOURNAL_{tag}_END'
        check('unique_ordered_block:' + tag, lines.count(begin) == lines.count(end) == 1 and lines.index(begin) < lines.index(end))
        return [json.loads(line) for line in lines[lines.index(begin) + 1:lines.index(end)]]

    def named_block(text, kind, value):
        lines = text.splitlines()
        begin = f'RUSTUSB_JOURNAL_{kind}_BEGIN {value}'
        end = f'RUSTUSB_JOURNAL_{kind}_END {value}'
        check('unique_ordered_block:' + kind + ':' + value, lines.count(begin) == lines.count(end) == 1 and lines.index(begin) < lines.index(end))
        return '\n'.join(lines[lines.index(begin) + 1:lines.index(end)])

    check('terminal_PASS_complete', summary['result'] == 'PASS' and summary['evidence_collection_complete'] is True)
    check('clean_run_HEAD_before_after', summary['git_commit'] == summary['git_commit_after'] == head == 'da764d0b7fb156e76844918698e8edc14d7e9b79' and not freeze['working_tree_dirty'] and not summary['working_tree_dirty'] and not summary['working_tree_dirty_after'])
    check('ordinary_host_x86_64', summary['host_uid'] == 1000 and summary['host_architecture'] == 'x86_64')
    for path, data in source.items():
        expected = freeze['files'][path]
        check('frozen_Git_blob:' + path, len(data) == expected['bytes'] and digest(data) == expected['sha256'])
    for name, sha in summary['script_sha256'].items():
        check('actual_copied_script:' + name, raw['run/source/' + name] == source['scripts/qemu/' + name] and digest(raw['run/source/' + name]) == sha)
    # Reconstruct framing directly from fixed bytes; no runner functions called.
    native_paths = ['Cargo.toml', 'Cargo.lock']
    native_paths += [path for path in source if path.startswith('crates/') and (path.endswith('.rs') or PurePosixPath(path).name == 'Cargo.toml')]
    native_paths += ['scripts/qemu/guest-init.sh', 'scripts/qemu/run-native-qemu.py']
    hasher = hashlib.sha256()
    for path in sorted(native_paths, key=PurePosixPath):
        name, data = path.encode(), source[path]
        hasher.update(len(name).to_bytes(8, 'big'))
        hasher.update(name)
        hasher.update(len(data).to_bytes(8, 'big'))
        hasher.update(data)
    hasher = hashlib.sha256(hasher.digest())
    for name in ['run-physical-qemu.py', 'guest-physical-init.sh', 'physical-wire-probe.rs']:
        data = source['scripts/qemu/' + name]
        hasher.update(name.encode())
        hasher.update(len(data).to_bytes(8, 'big'))
        hasher.update(data)
    for path in sorted([p for p in source if p.startswith('vendor/') and (p.endswith('.rs') or PurePosixPath(p).name in ['Cargo.toml', 'Cargo.lock'])], key=PurePosixPath):
        data = source[path]
        hasher.update(path.encode())
        hasher.update(len(data).to_bytes(8, 'big'))
        hasher.update(data)
    hasher = hashlib.sha256(hasher.digest())
    for name in ['run-journal-qemu.py', 'guest-journal-init.sh']:
        data = source['scripts/qemu/' + name]
        hasher.update(name.encode())
        hasher.update(len(data).to_bytes(8, 'big'))
        hasher.update(data)
    hasher = hashlib.sha256(hasher.digest())
    for name in ['run-guardian-qemu.py', 'guest-guardian-fragments.sh', 'usbfs-guardian-probe.c']:
        data = source['scripts/qemu/' + name]
        hasher.update(name.encode())
        hasher.update(len(data).to_bytes(8, 'big'))
        hasher.update(data)
    check('independently_reconstructed_source_fingerprint', hasher.hexdigest() == summary['source_fingerprint_sha256'] == summary['source_fingerprint_after_sha256'], hasher.hexdigest())
    check('source_guard_and_Cargo_lock', summary['source_guard_passed'] and digest(source['Cargo.lock']) == summary['cargo_lock_sha256'])
    check('allfeatures_locked_build', summary['build_performed'] and summary['build_command'] == ['cargo', 'build', '--workspace', '--release', '--locked', '--all-features'])
    check('recorded_ELF_identities_and_guard', summary['binary_guard_passed'] and summary['binary_sha256'] == summary['binary_after_sha256'] == summary['guest_binary_sha256'], {'sha256': summary['binary_sha256'], 'scope': 'Stored before/payload/after hash metadata, not rehash of absent ELF/initramfs originals.'})
    for role in ['server', 'client']:
        lines = program[role].splitlines()
        check(role + '_dual_UART_beacon', lines.count(f'RUSTUSB_JOURNAL_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0') == 1)
        check(role + '_actual_PASS_complete', lines.count(f'RUSTUSB_JOURNAL_RESULT: PASS role={role}') == 1 and lines.count(f'RUSTUSB_JOURNAL_COLLECTION_COMPLETE role={role}') == 1)
        check(role + '_no_failure_integrity_markers', not any(marker in text for text in [program[role], kernel[role]] for marker in ['RUSTUSB_JOURNAL_RESULT: FAIL', 'Kernel panic', 'BUG:', 'was already given back', 'seqnum max']))
        command = summary['qemu_commands'][role]
        check(role + '_private_net_dual_uart', command.count('-serial') == 2 and '-nic' in command and 'none' in command and any(x.startswith('socket,id=private,fd=') for x in command) and not any(x.startswith(('tap,', 'user,')) for x in command))
        check(role + '_normal_QEMU_and_kernel_shutdown', summary['guest_exit_codes'][role] == 0 and raw[f'run/{role}-qemu.stderr'] == b'' and 'reboot: Power down' in kernel[role])
    records_list = block(program['server'], 'RECORDS')
    observations_list = block(program['server'], 'OBSERVATIONS')
    waits_list = block(program['server'], 'WAITS')
    admin = block(program['server'], 'ADMIN')
    clients = block(program['client'], 'CLIENT')
    records = {row['label']: row for row in records_list}
    observations = {row['label']: row for row in observations_list}
    waits = {row['label']: row for row in waits_list if 'label' in row}
    check('unique_record_observation_labels', len(records) == len(records_list) == 18 and len(observations) == len(observations_list) == 7)
    check('instant_record_copies_equal_final_block', [json.loads(line.removeprefix('RUSTUSB_JOURNAL_RECORD ')) for line in program['server'].splitlines() if line.startswith('RUSTUSB_JOURNAL_RECORD ')] == records_list)
    check('instant_wait_copies_equal_final_block', [json.loads(line.removeprefix('RUSTUSB_JOURNAL_WAIT ')) for line in program['server'].splitlines() if line.startswith('RUSTUSB_JOURNAL_WAIT ')] == waits_list)
    original = records['initial']['sysfs']
    check('actual_original_full_sysfs', len(original) == 2 and [device['bus_id'] for device in original] == ['1-1', '1-2'] and [device['interfaces'] for device in original] == [[{'number': 0, 'alternate': 0, 'driver': 'usbhid'}], [{'number': 0, 'alternate': 0, 'driver': 'ftdi_sio'}]] and all(d['configuration'] == 1 and d['node_owner_mode'] == '0:600' for d in original))
    expected_baselines = {}
    for device in original:
        dev, ino = map(int, device['generation'].split(':'))
        _, node_ino, major, minor = device['node_identity'].split(':')
        expected_baselines[device['device_id']] = {'device_id': device['device_id'], 'bus_id': device['bus_id'], 'configuration': device['configuration'], 'interfaces': device['interfaces'], 'receipt': {'sysfs_device': dev, 'sysfs_inode': ino, 'node_device': os.makedev(int(major, 16), int(minor, 16)), 'node_inode': int(node_ino), 'usb_device_number': device['devnum'], 'bus_number': 1}}
    for label, record in records.items():
        pending = json.loads(named_block(program['server'], 'PENDING', 'label=' + label))
        disk_bytes = (named_block(program['server'], 'DISK', 'label=' + label) + '\n').encode()
        disk = json.loads(disk_bytes)
        check('journal_API_disk_triplicate:' + label, record['pending'] == pending and record['journal'] == disk and pending['records'] == disk['records'] and pending['schema_version'] == disk['schema_version'] == 1 and pending['next_after'] is None)
        check('journal_actual_sha_permissions:' + label, record['journal_directory_owner_mode'] == '0:700' and record['journal_file_owner_mode'] == '0:600' and digest(disk_bytes) == record['journal_sha256'])
        check('same_physical_generation_config_alt:' + label, [{**d, 'interfaces': original[i]['interfaces']} for i, d in enumerate(record['sysfs'])] == original)
        for entry in pending['records']:
            check('complete_original_baseline:' + label + ':' + entry['baseline']['bus_id'], entry['baseline'] == expected_baselines[entry['baseline']['device_id']])
            check('canonical_lease_boot_owner:' + label + ':' + entry['baseline']['bus_id'], bool(re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', entry['lease_id'])) and bool(re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', entry['owner']['boot_id'])) and entry['owner']['uid'] == 1000 and entry['owner']['pid'] > 0 and entry['owner']['start_time'] > 0)
    for label in ['initial', 'normal-closed', 'reopened-closed', 'admin-retired', 'reconciled-closed']:
        check('retired_zero_records:' + label, records[label]['pending']['records'] == [])
    for epoch in ['normal', 'reopened', 'killed', 'reconciled']:
        obs = observations[epoch + '-ready']
        identity = obs['identity']
        snapshot = obs['snapshot']
        check(epoch + '_real_ready_process_identity', obs['epoch'] == epoch and identity['uids'] == [1000] * 4 and identity['exe'] == '/usr/bin/rustusb' and all(identity[k] == 0 for k in ['cap_effective', 'cap_permitted', 'cap_ambient']))
        check(epoch + '_ready_full_original_sysfs', obs['sysfs'] == original)
        check(epoch + '_ready_unleased_zero', snapshot['buffer_bytes_reserved'] == 0 and all(not d['leased'] and d['pending_urbs'] == d['queued_urbs'] == d['usbfs_inflight'] == 0 and d['current_drivers'] == d['original_drivers'] for d in snapshot['devices']))
        recs = records[epoch + '-ready']['pending']['records']
        check(epoch + '_OS_owner_matches_persisted_grant', len(recs) == 2 and all(entry['owner']['pid'] == identity['pid'] and entry['owner']['start_time'] == identity['starttime'] and entry['owner']['uid'] == identity['uids'][0] for entry in recs))
        for device in obs['sysfs']:
            fds = [fd for fd in identity['fds'] if fd['node'] == device['node']]
            check(epoch + '_delegated_FD_generation:' + device['bus_id'], len(fds) == 1 and fds[0]['identity'] == device['node_identity'] and fds[0]['fd'] >= 3)
    check('separate_epoch_owner_and_lease_ids', len({observations[e + '-ready']['identity']['pid'] for e in ['normal', 'reopened', 'killed', 'reconciled']}) == 4 and len({r['lease_id'] for e in ['normal', 'reopened', 'killed', 'reconciled'] for r in records[e + '-ready']['pending']['records']}) == 8)
    event_epochs = ['normal', 'reopened', 'killed', 'denied-before-helper-restart', 'denied-after-helper-restart', 'reconciled']
    events = {epoch: [json.loads(line) for line in named_block(program['server'], 'EVENTS', 'epoch=' + epoch).splitlines()] for epoch in event_epochs}
    for epoch in ['normal', 'reopened', 'reconciled']:
        stop = [entry for entry in events[epoch] if entry['event'] == 'stopped']
        check(epoch + '_normal_stop_wait0', waits[epoch]['wait_code'] == 0 and waits[epoch]['signal'] is None and len(stop) == 1 and events[epoch][-1]['event'] == 'stopped')
        snapshot = stop[0]['snapshot']
        check(epoch + '_terminal_resources_and_drivers', snapshot['buffer_bytes_reserved'] == 0 and len(snapshot['devices']) == 2 and all(device['state'] == 'available' and not device['leased'] and device['cleanup_verified'] and device['pending_urbs'] == device['queued_urbs'] == device['usbfs_inflight'] == 0 and device['current_drivers'] == device['original_drivers'] for device in snapshot['devices']) and records[epoch + '-closed']['sysfs'] == original)
    check('reopened_no_IMPORT', all(all(value == 0 for value in device['counters'].values()) and device['usbfs_reaped'] == 0 for device in events['reopened'][-1]['snapshot']['devices']))
    for epoch in ['normal', 'killed', 'reconciled']:
        obs = observations[epoch + '-pending']
        snapshot = obs['snapshot']
        check(epoch + '_real_active_USBFS', snapshot['buffer_bytes_reserved'] > 0 and len(snapshot['devices']) == 2 and all(device['leased'] and device['state'] == 'leased' and device['pending_urbs'] >= 1 and device['usbfs_inflight'] >= 1 for device in snapshot['devices']) and all(device['interfaces'] == [{'number': 0, 'alternate': 0, 'driver': 'usbfs'}] for device in obs['sysfs']))
        check(epoch + '_actual_HID_and_bulk_io_counters', snapshot['devices'][0]['counters']['interrupt_in'] >= 2 and snapshot['devices'][1]['counters']['bulk_out'] >= 1 and snapshot['devices'][1]['counters']['bytes_out'] == 35)
        check(epoch + '_pending_same_FD_identity', obs['identity'] == observations[epoch + '-ready']['identity'])
    killed = waits['killed']
    owner = records['killed-ready']['pending']['records'][0]['owner']
    check('actual_saved_wait137_same_signal_target', killed['wait_code'] == 137 and killed['signal'] == 9 and killed['signal_count'] == 1 and killed['sender_uid'] == 1000 and killed['pid'] == owner['pid'] and killed['starttime'] == owner['start_time'])
    check('no_fabricated_killed_terminal', killed['final_backend_snapshot'] is None and killed['final_backend_snapshot_observable'] is False and not any(event['event'] == 'stopped' for event in events['killed']))
    null_labels = ['killed-post', 'denied-before-helper-restart-refused', 'helper-reloaded', 'denied-after-helper-restart-refused', 'unrestored-refused']
    for label in null_labels:
        check('actual_unrestored_driver_null:' + label, all(device['interfaces'] == [{'number': 0, 'alternate': 0, 'driver': None}] for device in records[label]['sysfs']))
    retained_labels = ['killed-ready', 'killed-pending'] + null_labels + ['guest-admin-restored']
    for label in retained_labels:
        check('exact_old_record_retained:' + label, records[label]['pending']['records'] == records['killed-ready']['pending']['records'])
    restart = [row for row in waits_list if row['event'] == 'helper_restart']
    check('actual_helper_restart_without_state_deletion', len(restart) == 1 and restart[0]['old_pid'] != restart[0]['new_pid'] and restart[0]['old_starttime'] != restart[0]['new_starttime'] and restart[0]['old_wait_code'] == 0 and restart[0]['state_directory_unchanged'] is True)
    check('journal_hash_same_across_helper_restart', records['killed-pending']['journal_sha256'] == records['helper-reloaded']['journal_sha256'] == records['denied-after-helper-restart-refused']['journal_sha256'])
    for epoch in ['denied-before-helper-restart', 'denied-after-helper-restart']:
        stderr = named_block(program['server'], 'STDERR', 'epoch=' + epoch)
        check(epoch + '_actual_denial_exit1_no_ready', waits[epoch]['wait_code'] == 1 and not any(event['event'] == 'ready' for event in events[epoch]) and 'unresolved physical grant blocks new authorization; inspect physical-pending and reconcile the original baseline' in stderr)
    old_ids = {r['lease_id'] for r in records['killed-ready']['pending']['records']}
    check('twelve_admin_results', len(admin) == 12)
    for phase, uid, expected, count in [('pending-denied', 1000, 'physical pending records require root', 2), ('reconcile-denied', 1000, 'physical reconciliation requires root', 2), ('pending-denied', 1001, 'caller UID is not approved by the administrator', 2), ('reconcile-denied', 1001, 'caller UID is not approved by the administrator', 2), ('unrestored-refused', 0, 'physical original configuration, full interface/alternate/driver set, or generation is not restored; unresolved grant retained', 2)]:
        rows = [row for row in admin if row['phase'] == phase and row['uid'] == uid]
        check(f'strict_admin_refusal:{phase}:uid{uid}', len(rows) == count and all(row['exit_code'] == 1 and json.loads(row['stdout'])['ok'] is False and expected in json.loads(row['stdout'])['error'] for row in rows))
    check('root_reconciliation_refused_each_old_lease', {row['lease_id'] for row in admin if row['phase'] == 'unrestored-refused'} == old_ids)
    check('actual_admin_original_state_restored_before_retirement', records['guest-admin-restored']['sysfs'] == original and len(records['guest-admin-restored']['pending']['records']) == 2 and records['admin-retired']['sysfs'] == original)
    retired = [row for row in admin if row['phase'] == 'restored-retired']
    check('root_only_each_lease_verified_retired', len(retired) == 2 and {row['lease_id'] for row in retired} == old_ids and all(row['uid'] == 0 and row['exit_code'] == 0 and json.loads(row['stdout']) == {'lease_id': row['lease_id'], 'observation': 'restored', 'status': 'retired'} for row in retired))
    check('three_actual_client_cycles', [row['cycle'] for row in clients] == [1, 2, 3])
    for outcome in clients:
        cycle = outcome['cycle']
        data = bytes.fromhex(outcome['input_event_hex'])
        events_in = [struct.unpack('<qqHHi', data[n:n+24]) for n in range(0, len(data), 24)]
        check(f'cycle{cycle}_actual_key_down_up', len(data) > 0 and len(data) % 24 == 0 and all(any(t == 1 and c == cycle + 1 and value == state for _, _, t, c, value in events_in) for state in [1, 0]))
        check(f'cycle{cycle}_exact35B_both_directions', bytes.fromhex(outcome['rx_hex']) == f'RUSTUSB-RX-{cycle}-0001:0123456789abcdef\n'.encode() and bytes.fromhex(outcome['tx_hex']) == f'RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n'.encode())
        rows = [line.split() for line in outcome['vhci_status'].splitlines() if line.startswith(('hs ', 'ss '))]
        check(f'cycle{cycle}_autonomous_NULL_before_detach', outcome['spontaneous_disconnect'] is True and outcome['sessions'] == [] and outcome['detach_before_observation'] is False and len(rows) == 16 and all(row[2] == '004' for row in rows) and len(outcome['receipts']) == 2)
    check('client_receipts_new_generation_each_cycle', len({receipt['local_device_generation']['inode'] for outcome in clients for receipt in outcome['receipts']}) == 6)
    tx = b''.join(f'RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n'.encode() for cycle in [1, 2, 3])
    check('raw_FTDI_host_sink_exact105', raw['run/ftdi-host-tx.bin'] == tx and len(tx) == 105 and summary['serial_output']['received_bytes'] == summary['serial_output']['expected_bytes'] == 105 and summary['serial_output']['exact_match'] and summary['serial_output']['sha256'] == digest(tx))
    check('three_RX105_and_TX105', sum(len(bytes.fromhex(row['rx_hex'])) for row in clients) == 105 and sum(len(bytes.fromhex(row['tx_hex'])) for row in clients) == 105)
    check('guest_root_ordinary_boundaries', 'RUSTUSB_JOURNAL_FD_GATE: PASS direct_uid1000=denied direct_uid1001=denied helper_uid1001=denied nodes=root0600' in program['server'].splitlines() and 'RUSTUSB_JOURNAL_DISK_GATE: PASS ordinary_uid1000=denied directory=root0700 file=root0600' in program['server'].splitlines() and 'RUSTUSB_JOURNAL_SERVER_READY: PASS uid=1000 app_root=false capabilities=0 usbip_host=false vhci_hcd=false' in program['server'].splitlines())
    guest_source = source['scripts/qemu/guest-journal-init.sh'].decode()
    check('base_source_preserves_original_admin_recovery_anchors', 'usbhid/bind' in guest_source and 'ftdi_sio/bind' in guest_source)
    # Preserve the earlier failure rather than replacing it with this successful run.
    old_summary = Path('/tmp/rustusb-guardian-43d6d43-20261023/run/summary.json')
    old = old_summary.read_bytes()
    check('original_first_FAIL_preserved', json.loads(old)['result'] == 'FAIL' and json.loads(old)['evidence_collection_complete'] is False)
    sequence_raw = (args.base / 'sequence.json').read_bytes()
    raw['sequence.json'] = sequence_raw
    sequence = json.loads(sequence_raw)
    check('root_sequence_PASS_clean_frozen_source', sequence['result'] == 'PASS' and sequence['exit_code'] == 0 and sequence['head'] == head and sequence['source_guard'] and sequence['head_guard'] and sequence['worktree_clean'])
    composition = json.loads(raw['run/guardian-composition.json'])
    generated = source['scripts/qemu/guest-journal-init.sh']
    for index, row in enumerate(composition['composition']):
        before, after = row['before'].encode(), row['after'].encode()
        check('guardian_generated_unique_anchor:' + str(index), generated.count(before) == row['matches'] == 1)
        generated = generated.replace(before, after, 1)
    check('guardian_generated_init_exact_bytes', generated == raw['run/source/generated-guardian-init.sh'] and digest(generated) == composition['generated_init_sha256'])
    check('guardian_probe_compiled_payload_hash', composition['probe_sha256'] == composition['copied_probe_sha256'] and raw['run/guardian-build.log'] == b'')
    prefix = 'RUSTUSB_GUARDIAN_PROBE '
    gevents = [json.loads(line[len(prefix):]) for line in program['server'].splitlines() if line.startswith(prefix)]
    gobservations = [json.loads(line.removeprefix('RUSTUSB_GUARDIAN_RECORD ')) for line in program['server'].splitlines() if line.startswith('RUSTUSB_GUARDIAN_RECORD ')]
    gwaits = [json.loads(line.removeprefix('RUSTUSB_GUARDIAN_WAIT ')) for line in program['server'].splitlines() if line.startswith('RUSTUSB_GUARDIAN_WAIT ')]
    check('guardian_exact100_scalar_events', len(gevents) == 100 and all(row['event'] in {'state', 'ready', 'ofd_compare', 'done', 'ioctl', 'close', 'external_ofd'} for row in gevents))
    labels = ['b-opened-before-import', 'dup-armed-live', 'dup-retained-after-wait137', 'dup-refusal-preserved', 'duplicate-closed-b-retained', 'independent-b-claimed', 'independent-b-released', 'external-c-claimed', 'external-c-preserved', 'external-c-closed', 'before-b-connect', 'b-restored-original']
    check('guardian_exact12_observations', [row['label'] for row in gobservations] == labels)
    gby = {row['label']: row for row in gobservations}
    ready = [row for row in gevents if row['event'] == 'ready']
    check('guardian_single_root_probe', len(ready) == 1 and ready[0]['uid'] == 0 and ready[0]['seq'] == 0)
    gp, gs = ready[0]['pid'], ready[0]['starttime']
    old_records = records['killed-pending']['pending']['records']
    count_by_label = dict(zip(labels, [2, 4, 4, 4, 2, 2, 2, 4, 4, 2, 2, 2]))
    seq_by_label = dict(zip(labels, [0, 1, 1, 5, 6, 8, 10, 13, 17, 20, 20, 22]))
    driver_by_label = dict(zip(labels, [['usbhid', 'ftdi_sio'], ['usbfs'] * 2, ['usbfs'] * 2, ['usbfs'] * 2, [None] * 2, ['usbfs'] * 2, [None] * 2, ['usbfs'] * 2, ['usbfs'] * 2, [None] * 2, [None] * 2, ['usbhid', 'ftdi_sio']]))
    for label in labels:
        row = gby[label]
        expected = json.loads(json.dumps(original))
        for device, driver in zip(expected, driver_by_label[label]):
            device['interfaces'] = [{'number': 0, 'alternate': 0, 'driver': driver}]
        check('guardian_complete_sysfs_scope:' + label, row['seq'] == seq_by_label[label] and row['sysfs'] == expected and row['original'] == original and row['full_interfaces'] == [{'bus_id': d['bus_id'], 'interfaces': d['interfaces']} for d in expected])
        owner = row['guardian']
        check('guardian_actual_process_FD_count:' + label, owner['pid'] == gp and owner['starttime'] == gs and owner['uids'] == [0] * 4 and len(owner['fds']) == count_by_label[label])
        check('guardian_every_open_FD_identity:' + label, all(fd['node'] in [d['node'] for d in original] and fd['identity'] == next(d['node_identity'] for d in original if d['node'] == fd['node']) for fd in owner['fds']) and len({fd['fd'] for fd in owner['fds']}) == count_by_label[label])
        expected_records = [] if label == labels[0] else old_records
        check('guardian_old_journal_untouched:' + label, row['pending']['records'] == row['journal']['records'] == expected_records and all(entry['owner']['boot_id'] == row['boot_id'] for entry in expected_records))
    check('guardian_observations_monotonic', all(a['tick'] <= b['tick'] for a, b in zip(gobservations, gobservations[1:])))
    check('guardian_saved_wait137_and_probe_wait0', gwaits == [{'pid': killed['pid'], 'starttime': killed['starttime'], 'wait_code': 137, 'signal': 9, 'signal_count': 1, 'terminal_resources': None}, {'pid': gp, 'starttime': gs, 'wait_code': 0, 'role': 'guardian'}])
    check('guardian_reap_precedes_retained_dup_observation', program['server'].index('RUSTUSB_GUARDIAN_WAIT ') < program['server'].index('"label":"dup-retained-after-wait137"'))
    pairs = [row for row in gevents if row['event'] == 'ofd_compare']
    check('actual_KCMP_A_equals_D_B_differs', [row['device'] for row in pairs] == [0, 1] and all(row['seq'] == 1 and row['owner_pid'] == killed['pid'] and row['owner_starttime'] == killed['starttime'] and row['a_d'] == 0 and row['a_b'] in [1, 2, 3] and row['b_d'] in [1, 2, 3] for row in pairs))
    external = [row for row in gevents if row['event'] == 'external_ofd']
    check('actual_KCMP_C_differs_from_B_same_probe_process', [row['device'] for row in external] == [0, 1] and all(row['seq'] == 11 and row['pid'] == gp and row['b_c'] in [1, 2, 3] for row in external))
    operations = [(2, 'claim', 'B', 0, -1, 16), (3, 'connect', 'B', 0, -1, 16), (4, 'claim', 'B', 1, -1, 16), (5, 'connect', 'B', 1, -1, 16), (7, 'claim', 'B', 0, 0, 0), (8, 'claim', 'B', 1, 0, 0), (9, 'release', 'B', 0, 0, 0), (10, 'release', 'B', 1, 0, 0), (12, 'claim', 'C', 0, 0, 0), (13, 'claim', 'C', 1, 0, 0), (14, 'claim', 'B', 0, -1, 16), (15, 'connect', 'B', 0, -1, 16), (16, 'claim', 'B', 1, -1, 16), (17, 'connect', 'B', 1, -1, 16), (18, 'release', 'C', 0, 0, 0), (19, 'release', 'C', 1, 0, 0), (21, 'connect', 'B', 0, 1, 0), (22, 'connect', 'B', 1, 1, 0)]
    ioctls = [row for row in gevents if row['event'] == 'ioctl']
    check('actual18_ioctl_exact_order_rc_errno', [(r['seq'], r['op'], r['handle'], r['device'], r['rc'], r['errno']) for r in ioctls] == operations and all(r['interface'] == 0 for r in ioctls))
    check('eight_EBUSY_eight_claim_release0_two_CONNECT1', sum(r['errno'] == 16 and r['rc'] == -1 for r in ioctls) == 8 and sum(r['op'] in ['claim', 'release'] and r['rc'] == r['errno'] == 0 for r in ioctls) == 8 and sum(r['op'] == 'connect' and r['rc'] == 1 and r['errno'] == 0 for r in ioctls) == 2)
    close_events = [row for row in gevents if row['event'] == 'close']
    check('all_D_C_B_explicit_closes_EBADF', [(r['seq'], r['handle'], r['device']) for r in close_events] == [(seqno, h, device) for seqno, h in [(6, 'D'), (20, 'C'), (23, 'B')] for device in [0, 1]] and all(r['close_rc'] == 0 and r['getfd_rc'] == -1 and r['getfd_errno'] == 9 for r in close_events))
    check('all23_bounded_commands_done', [r['seq'] for r in gevents if r['event'] == 'done'] == list(range(1, 24)))
    states = [row for row in gevents if row['event'] == 'state']
    check('all48_exact_state_snapshots', [(r['seq'], r['device']) for r in states] == [(seqno, device) for seqno in range(24) for device in [0, 1]])
    for seqno in range(24):
        for device in [0, 1]:
            row = states[seqno * 2 + device]
            original_device = original[device]
            receipt = expected_baselines[original_device['device_id']]['receipt']
            if seqno == 0 or seqno >= 22:
                expected_driver = original_device['interfaces'][0]['driver']
            elif 1 <= seqno <= 5 or seqno in [8, 13, 14, 15, 16, 17]:
                expected_driver = 'usbfs'
            elif seqno in [7, 12]:
                expected_driver = 'usbfs' if device == 0 else None
            elif seqno in [9, 18]:
                expected_driver = 'usbfs' if device == 1 else None
            elif seqno == 21:
                expected_driver = 'usbhid' if device == 0 else None
            else:
                expected_driver = None
            expected_b_owned = (seqno == 7 and device == 0) or seqno == 8 or (seqno == 9 and device == 1)
            expected_c_owned = (seqno == 12 and device == 0) or 13 <= seqno <= 17 or (seqno == 18 and device == 1)
            check(f'guardian_step{seqno}_device{device}_kernel_claim_transition', row['interfaces'] == [{'number': 0, 'alternate': 0, 'driver': expected_driver}] and bool(row['b_claimed']) == expected_b_owned and bool(row['c_claimed']) == expected_c_owned)
            check(f'guardian_step{seqno}_device{device}_same_generation_B', row['pid'] == gp and row['starttime'] == gs and row['node_rdev'] == receipt['node_device'] and row['node_inode'] == receipt['node_inode'] and row['sysfs_dev'] == receipt['sysfs_device'] and row['sysfs_inode'] == receipt['sysfs_inode'] and row['configuration'] == 1 and row['b_fd'] == states[device]['b_fd'])
    check('B_open_precedes_killed_epoch_IMPORT', program['server'].index('"label":"b-opened-before-import"') < program['server'].index('RUSTUSB_JOURNAL_RECORD {"label":"killed-ready"'))
    check('B_restoration_closure_precedes_reconcile_and_third_IO', program['server'].index('"label":"b-restored-original"') < program['server'].index('"role":"guardian"') < program['server'].index('RUSTUSB_JOURNAL_RECORD {"label":"guest-admin-restored"') < program['server'].index('RUSTUSB_JOURNAL_RECORD {"label":"admin-retired"') < program['server'].index('RUSTUSB_JOURNAL_RECORD {"label":"reconciled-pending"'))
    check('guardian_collection_complete_only_server', program['server'].splitlines().count('RUSTUSB_GUARDIAN_COLLECTION_COMPLETE role=server') == 1 and not any('RUSTUSB_GUARDIAN_' in text for text in [program['client'], *kernel.values()]))
    emitted = summary['verification']['checks']
    check('emitted184_base_plus53_guardian_all_PASS', len(emitted) == 237 and all(row['result'] == 'PASS' for row in emitted) and summary['verification']['guardian']['base_journal_check_count'] == 184 and len(summary['verification']['guardian']['checks']) == 53, 'Metadata counts are corroboration; actual original/journal/guardian facts were independently rebuilt above.')
    mechanism = {'event_counts': {name: sum(row['event'] == name for row in gevents) for name in ['state', 'ready', 'ofd_compare', 'done', 'ioctl', 'close', 'external_ofd']}, 'guardian_pid': gp, 'guardian_starttime': gs, 'waits': gwaits, 'OFD_comparisons': pairs, 'C_comparisons': external, 'actual_ioctls': ioctls, 'observation_labels': labels, 'C_is_another_process': False, 'C_has_independent_OFD': True, 'C_data_IO_tested': False, 'automatic_product_recovery': False, 'arbitrary_FD_revocation_proved': False, 'killed_epoch_terminal_resources': None, 'emitted_assertions': {'base': 184, 'guardian': 53}}
    check('raw_artifacts_unchanged_during_review', all((args.base / path).read_bytes() == data for path, data in raw.items()))
    artifact_list = [{'path': path, 'bytes': len(data), 'sha256': digest(data)} for path, data in raw.items()]
    report = {
        'schema_version': 1,
        'reviewed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'review_result': 'PASS',
        'run_result': 'PASS',
        'evidence_collection_complete': True,
        'run_directory': str(run),
        'run_head': head,
        'method': 'Independent parser of saved program/kernel streams, disk/API/instant/final JSON, USB input bytes, FTDI sink and fixed Git blobs. No runner.verify or runtime experiment.',
        'authorship': 'Reviewer authored initial Store, not this QEMU harness. This is independent offline raw reconstruction, not an independent implementation audit of initial journal code.',
        'checks_count': len(checks),
        'checks': checks,
        'artifacts_count': len(artifact_list),
        'artifacts': artifact_list,
        'frozen_source_count': len(source),
        'frozen_source_manifest_sha256': digest(raw['freeze.json']),
        'parser': {'path': str(Path(__file__).resolve()), 'bytes': len(Path(__file__).read_bytes()), 'sha256': digest(Path(__file__).read_bytes())},
        'guardian_mechanism': mechanism,
        'key_facts': {
            'record_observations': len(records), 'OS_observations': len(observations),
            'wait_observations': len(waits_list), 'admin_results': len(admin),
            'client_cycles': 3, 'guest_exit_codes': summary['guest_exit_codes'],
            'FTDI_RX_bytes': 105, 'FTDI_TX_actual_sink_bytes': 105,
            'killed_wait': killed, 'helper_restart': restart[0],
            'record_continuity': retained_labels,
            'restoration': 'Explicit disposable-guest root probe uses independent B CONNECT for original usbhid/ftdi_sio, exact full sysfs match, closes all known probe descriptors and exits0, then each old lease is retired by read-only helper reconciliation.',
            'new_epoch_after_reconcile': 'Third KEY3 and exact35B bidirectional actual I/O, followed by normal stopped and zero run/resources.'
        },
        'original_failure': {'path': str(old_summary), 'bytes': len(old), 'sha256': digest(old), 'result': 'FAIL', 'evidence_collection_complete': False},
        'limits': [
            'Guest-root B CONNECT mechanism plus existing journal guard/reconciliation only. Not production automatic recovery, selective original-driver binding or FD revocation. C is an independent OFD inside the same probe process; its traffic was not tested.',
            'PID death, socket EOF and saved state do not prove all external dup/SCM_RIGHTS references retired; no FD revocation claim.',
            'Killed old epoch has no terminal backend snapshot; no fabricated zero pending/inflight/reap final state.',
            'Full baselines here are the actual single-interface HID and FTDI devices; this review alone does not validate arbitrary composite/configuration/alternate devices.',
            'ELF and initramfs originals were not persisted. Binary guard checks compare actual recorded build/payload/after identities, not a rehash of absent executables.',
            'Helper process restart in a running guest is not power-loss durability or OS reboot validation.',
            'Only this completed guardian mechanism run was reviewed; historical software/USB regressions remain attributed to their original revisions.',
            'QEMU guest-only operations do not establish real hardware G1/G2, WAN/VPN security, kernel-inside-active windows or host USB behavior.'
        ],
        'boundaries': {'repository_writes': False, 'build_fmt_Cargo': False, 'QEMU_USB_ptrace_runs': False, 'runner_verify_called': False, 'raw_modified': False},
        'parser_development': 'Reused the previously corrected independent journal parser and added raw guardian OFD/FD/step-transition checks. No runner.verify call. The original failed experiment remains FAIL.',
        'raw_unchanged_before_after': True
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    data = args.output.read_bytes()
    print(json.dumps({'path': str(args.output), 'bytes': len(data), 'sha256': digest(data), 'checks': len(checks), 'artifacts': len(artifact_list), 'result': report['review_result'], 'parser_sha256': report['parser']['sha256']}))


if __name__ == '__main__':
    main()
