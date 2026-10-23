import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import py_compile
import re
import shlex
import struct
import subprocess
import sys

sys.dont_write_bytecode = True
repo = Path('/home/ubuntu/github/usbip-client')
runner = repo / 'scripts/qemu/run-sigterm-qemu.py'
guest = repo / 'scripts/qemu/guest-sigterm-init.sh'
checks = []
raw = []

def check(name, condition):
    checks.append({'name': name, 'result': 'PASS' if condition else 'FAIL'})

def command(argv):
    p = subprocess.run(argv, capture_output=True)
    raw.append({'command': argv, 'exit_code': p.returncode, 'stdout': p.stdout.decode(errors='replace'), 'stderr': p.stderr.decode(errors='replace')})
    return p.returncode

py_compile.compile(str(runner), cfile='/tmp/rustusb-sigterm-runner-20261023.pyc', doraise=True)
check('python_compile', True)
check('posix_shell_syntax', command(['sh', '-n', str(guest)]) == 0)
check('actual_busybox_shell_syntax', command(['/usr/bin/busybox', 'sh', '-n', str(guest)]) == 0)
joined = guest.read_text().replace('\\\n', ' ')
filters = []
for line in joined.splitlines():
    found = re.search(r'\bjq\s', line)
    if not found:
        continue
    lexer = shlex.shlex(line[found.start():], posix=True)
    lexer.whitespace_split = True
    assert lexer.get_token() == 'jq'
    while True:
        token = lexer.get_token()
        if token in ['--arg', '--argjson', '--slurpfile', '--rawfile']:
            lexer.get_token()
            lexer.get_token()
        elif token.startswith('-'):
            continue
        else:
            filters.append(token)
            break
for index, expression in enumerate(filters):
    bindings = []
    for variable in sorted(set(re.findall(r'\$([A-Za-z_][A-Za-z_0-9]*)', expression))):
        bindings += ['--argjson', variable, 'null']
    check(f'actual_jq_filter_{index}', command(['/usr/bin/jq', '-n', *bindings, 'empty | (' + expression + ')']) == 0)

spec = importlib.util.spec_from_file_location('sigterm_qemu', runner)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ss = 'u_str LISTEN 1 128 /run/rustusb/control.sock 500 * 0 users:(("rustusb-helper",pid=22,fd=5)) <-> ino:100 dev:0/0 peers: 42\n'
check('backlog_named_helper_peer', module.backlog_contains(ss, 22, '42'))
check('signal_pipe_not_helper_queue', not module.backlog_contains('u_str ESTAB 0 0 * 42 * 43 users:(("rustusb",pid=12,fd=4))\n', 22, '42'))
check('wrong_helper_rejected', not module.backlog_contains(ss, 2, '42'))
check('wrong_peer_rejected', not module.backlog_contains(ss, 22, '4'))
check('different_path_rejected', not module.backlog_contains(ss.replace('/run/rustusb/control.sock', '/other.sock'), 22, '42'))

original = []
devices = []
for index, driver in enumerate(['usbhid', 'ftdi_sio']):
    original.append({'device_id': f'usb-{index}', 'bus_id': f'1-{index+1}', 'node': f'/dev/bus/usb/001/00{index+2}', 'generation': f'1:{index+50}', 'node_identity': f'2:{index+60}:bd:{index+1}', 'node_owner_mode': '0:600', 'devnum': index+2, 'configuration': 1, 'interfaces': [{'number': 0, 'alternate': 0, 'driver': driver}]})
    devices.append({'device_id': f'usb-{index}', 'state': 'available', 'leased': False, 'pending_urbs': 0, 'queued_urbs': 0, 'usbfs_inflight': 0, 'cleanup_verified': True, 'original_drivers': [{'interface': 0, 'driver': driver}], 'current_drivers': [{'interface': 0, 'driver': driver}], 'usbfs_reaped': 10, 'cancelled': 0, 'counters': {'controls': 5, 'interrupt_in': 2 if index == 0 else 0, 'bulk_in': 2 if index == 1 else 0, 'bulk_out': 2 if index == 1 else 0}})
baseline = {'schema_version': 1, 'buffer_bytes_reserved': 0, 'devices': devices}
pending = copy.deepcopy(baseline)
pending['buffer_bytes_reserved'] = 512
for device in pending['devices']:
    device.update(state='leased', leased=True, pending_urbs=1, usbfs_inflight=1, cleanup_verified=False, current_drivers=[{'interface': 0, 'driver': 'usbfs'}])
stopped = copy.deepcopy(baseline)
for device in stopped['devices']:
    device['usbfs_reaped'] += 1
events = [{'schema_version': 1, 'event': 'ready', 'address': '192.0.2.1:3240', 'snapshot': baseline}]
events += [{'schema_version': 1, 'event': 'snapshot', 'id': name, 'snapshot': snap} for name, snap in [('baseline', baseline), ('held', pending), ('pending-1', pending)]]
events.append({'schema_version': 1, 'event': 'stopped', 'snapshot': stopped})
observations = []
fds = [{'node': device['node'], 'fd': index+7, 'identity': device['node_identity']} for index, device in enumerate(original)]
identity = {'pid': 13, 'starttime': 2, 'exe': '/usr/bin/rustusb', 'uids': [1000]*4, 'cap_effective': 0, 'cap_permitted': 0, 'cap_ambient': 0, 'fds': fds}
for label in ['baseline', 'held', 'term-pending', 'post-term']:
    observed = copy.deepcopy(original)
    if label in ['held', 'term-pending']:
        for device in observed:
            device['interfaces'][0]['driver'] = 'usbfs'
    observations.append({'label': label, 'sysfs': observed, 'snapshot': baseline if label == 'baseline' else pending, 'fds': fds, 'identity': identity})
server = {'schema_version': 1, 'event': 'sigterm_outcome', 'role': 'server', 'helper_alive': True, 'observation_limit_seconds': 20, 'signal': 15, 'signal_count': 1, 'sender_uid': 1000, 'pid': 13, 'starttime': 2, 'stdin_kept_open': True, 'shutdown_control_sent': False, 'wait_code': 0, 'process_exit_observed': True, 'pid_absent': True, 'fd_table_absent': True, 'pre_snapshot_id': 'pending-1', 'restored_sysfs': True, 'within_observation_window': True}
startup = {'schema_version': 1, 'event': 'startup_term_outcome', 'uid': 1000, 'sender_uid': 1000, 'signal': 15, 'signal_count': 1, 'pid': 12, 'starttime': 1, 'helper_pid': 22, 'connected_unix_inode': '42', 'unix_ss_observation': ss, 'unix_observation': '00000000: 00000003 00000000 00000000 0001 03 42\n', 'tcp_observation': '', 'tcp6_observation': '', 'helper_stopped_before_signal': True, 'ready_before_signal': False, 'listener_before_signal': False, 'helper_acceptance_observed': False, 'wait_code': 0, 'process_exit_observed': True, 'observation_limit_seconds': 3, 'observation_centiseconds': 20, 'helper_continued': True, 'sysfs_unchanged': True, 'sysfs': original}
receipts = [{'port': index, 'local_bus_id': f'9-{index+1}', 'status': 6, 'local_device_address': index+2, 'local_device_generation': {'inode': index+30, 'filesystem_device': 1}} for index in range(2)]
client = {'schema_version': 1, 'event': 'sigterm_outcome', 'role': 'client', 'helper_alive': True, 'observation_limit_seconds': 20, 'spontaneous_disconnect': True, 'detach_before_observation': False, 'hid_generation_gone': True, 'ftdi_generation_gone': True, 'sessions': [], 'receipts': receipts, 'vhci_status': 'hub port sta spd dev sockfd local_busid\nhs 0000 004 000 00000000 0 0-0\nhs 0001 004 000 00000000 0 0-0\n', 'serial_in_bytes': 35, 'serial_out_bytes': 35, 'rx_sha256': hashlib.sha256(module.RX).hexdigest(), 'tx_sha256': hashlib.sha256(module.TX).hexdigest(), 'input_event_hex': (struct.pack('<qqHHi', 1, 0, 1, 2, 1) + struct.pack('<qqHHi', 2, 0, 1, 2, 0)).hex(), 'input_event_format': 'linux_x86_64_24'}

def logs(server_value, startup_value, events_value, observations_value, client_value):
    output = {}
    for role, outcome in [('server', server_value), ('client', client_value)]:
        output[role] = '\n'.join([f'RUSTUSB_SIGTERM_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0', f'RUSTUSB_SIGTERM_COLLECTION_COMPLETE role={role}', f'RUSTUSB_SIGTERM_RESULT: PASS role={role}', f'RUSTUSB_SIGTERM_ISOLATION: PASS role={role} carrier=1 ip_forward=0', 'RUSTUSB_SIGTERM_OUTCOME ' + json.dumps(outcome)]) + '\n'
    output['server'] += 'RUSTUSB_SIGTERM_STARTUP_OUTCOME ' + json.dumps(startup_value) + '\n'
    output['server'] += 'RUSTUSB_SIGTERM_FD_GATE: PASS direct_uid1000=denied direct_uid1001=denied helper_uid1001=denied nodes=root0600\nRUSTUSB_SIGTERM_SERVER_READY: PASS uid=1000 app_root=false capabilities=0 usbip_host=false vhci_hcd=false\n'
    for tag, entries in [('EVENTS', events_value), ('OBSERVATIONS', observations_value)]:
        output['server'] += f'RUSTUSB_SIGTERM_{tag}_BEGIN\n' + '\n'.join(json.dumps(entry) for entry in entries) + f'\nRUSTUSB_SIGTERM_{tag}_END\n'
    output['client'] += '{"event":"physical_probe_case","case":"both_real_devices_exclusive_busy","result":"PASS"}\n'
    return output

kernel = {'server': '', 'client': ''}
positive = logs(server, startup, events, observations, client)
verified = module.verify(positive, kernel)
check('synthetic_complete_contract', all(item['result'] == 'PASS' for item in verified['checks']))
old_server, old_startup, old_observations = copy.deepcopy(server), copy.deepcopy(startup), copy.deepcopy(observations)
old_server['wait_code'] = old_startup['wait_code'] = 143
old_server['restored_sysfs'] = False
for device in old_observations[-1]['sysfs']:
    device['interfaces'][0]['driver'] = None
old = module.verify(logs(old_server, old_startup, events[:-1], old_observations, client), kernel)
check('synthetic_old143_keeps_both_outcomes', old['outcomes']['server']['wait_code'] == old['startup_outcome']['wait_code'] == 143)
check('synthetic_old143_records_missing_stopped', any(item['name'] == 'one_final_stopped' and item['result'] == 'FAIL' for item in old['checks']))
check('synthetic_old143_keeps_post_sysfs_no_fake_zero', len(old['server_observations']) == 4 and old['server_observations'][-1]['sysfs'][0]['interfaces'][0]['driver'] is None)
check('synthetic_completion_wins_not_forced_cancel_count', all(item['result'] == 'PASS' for item in verified['checks'] if item['name'] == 'real_pending_reaped_cancellation_counts_preserved'))
broken = copy.deepcopy(client)
broken['rx_sha256'] = '0'*64
bad_serial = module.verify(logs(server, startup, events, observations, broken), kernel)
check('synthetic_wrong_rx_rejected', any(item['name'] == 'exact_serial_content_digests' and item['result'] == 'FAIL' for item in bad_serial['checks']))
try:
    module.json_block(positive['server'].replace('RUSTUSB_SIGTERM_EVENTS_BEGIN\n', 'RUSTUSB_SIGTERM_EVENTS_BEGIN\n\x1b[32m'), 'EVENTS')
except ValueError:
    check('ansi_not_stripped', True)
else:
    check('ansi_not_stripped', False)
result = {'result': 'PASS' if all(item['result'] == 'PASS' for item in checks) else 'FAIL', 'scope': 'static syntax and explicitly synthetic parser contracts; no Cargo, QEMU, signals or host networking', 'source_sha256': {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest() for path in [runner, guest]}, 'checks': checks, 'commands': raw, 'synthetic_positive_verification_checks': len(verified['checks']), 'synthetic_old143_failed_checks': [item['name'] for item in old['checks'] if item['result'] == 'FAIL']}
Path('/tmp/rustusb-sigterm-harness-static-20261023.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps({'result': result['result'], 'checks': len(checks), 'failed': [item for item in checks if item['result'] == 'FAIL']}, indent=2))
sys.exit(0 if result['result'] == 'PASS' else 1)
