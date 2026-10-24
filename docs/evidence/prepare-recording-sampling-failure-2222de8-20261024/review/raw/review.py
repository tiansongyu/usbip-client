"""Read original bytes only; authored by the harness author, separate method.

This does not import or execute any production harness/verifier and does not
relabel the terminal 2222de8 run. All writes are confined to the output folder.
"""
import base64
import hashlib
import json
import re
import struct
from pathlib import Path

E = Path('/tmp/rustusb-prepare-stop-final-2222de8-20261024/prepare-native-resource-job')
O = Path('/tmp/rustusb-prepare-stop-222-raw-review-20261024')
checks = []
def digest(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
def check(ok, label):
    checks.append({'check': label, 'passed': bool(ok)})
    if not ok:
        raise RuntimeError(label)
def raw(role, name):
    return (E / (role + '-artifacts') / name).read_bytes()
def obj(role, name):
    return json.loads(raw(role, name))
def server(name):
    return obj('server', name)
def snapshot_manifest():
    return {str(p.relative_to(E)): digest(p.read_bytes()) for p in sorted(E.rglob('*')) if p.is_file()}

before = snapshot_manifest()
summary = json.loads((E / 'summary.json').read_bytes())
check(summary['completed_utc'] and summary['result'] == 'FAIL', 'completed original FAIL retained')
check(summary['head'] == summary['head_after'] == '2222de87439f61e8a2e939e7dc15cd845d38aa4e', 'exact actual source head')
check(summary['guest_exit_codes'] == {'server': 0, 'client': 0}, 'both actual guests exited zero; distinct from test verdict')
check(all(summary[k] for k in ('source_guard', 'binary_guard', 'head_guard', 'clean_guard')), 'four actual guards')
check(summary['error'] == 'RuntimeError: held observation 1 recomputed from raw root/sysfs/FD files', 'exact first verifier error retained')
check(summary['binary_sha256'] == {
    'rustusb':'72adb291645fdcf3cb41c1ff9e9e781fe9eed344730a4444407f983c9b63e5f2',
    'rustusb-helper':'b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5'}, 'exact software-gate ELF pair')
frame_counts = {}
for role in ('server', 'client'):
    text = (E / (role + '-evidence.log')).read_text()
    matches = list(re.finditer(r'^SECURE_ARTIFACT_BEGIN ([a-zA-Z0-9_.-]+) ([0-9]+)\n(.*?)^SECURE_ARTIFACT_END \1\s*$', text, re.M | re.S))
    check(text.count('SECURE_ARTIFACT_BEGIN ') == len(matches), role + ' all base64 frames complete')
    names = set()
    for match in matches:
        name, size, payload = match.groups()
        check(name not in names, role + ' unique frame ' + name)
        names.add(name)
        data = base64.b64decode(''.join(payload.split()), validate=True)
        check(len(data) == int(size) and data == raw(role, name), role + ' exact original frame bytes ' + name)
        check(summary['guest_artifacts'][role][name] == digest(data), role + ' original frame manifest ' + name)
    check(names == set(summary['guest_artifacts'][role]), role + ' complete artifact set')
    frame_counts[role] = len(names)
    kernel = (E / (role + '-serial.log')).read_text(errors='replace')
    check('Kernel panic' not in kernel and 'Oops:' not in kernel, role + ' final S0 no panic/Oops; other warnings retained')
    check('RUSTUSB_PREPARE_RESULT: PASS' in text and 'RUSTUSB_PREPARE_RESULT: FAIL' not in text, role + ' actual guest marker PASS, separate from host FAIL')
    for owner in ['helper', 'agent-1'] + (['agent-2'] if role == 'server' else []):
        identity = dict(line.split(':', 1) for line in raw(role, 'identity-' + owner + '.txt').decode().splitlines() if ':' in line)
        check(identity['Uid'].split() == (['0']*4 if owner == 'helper' else ['2000']*4), role + '/' + owner + ' actual four UID fields')
        binary = 'rustusb-helper' if owner == 'helper' else 'rustusb'
        check(raw(role, 'executable-' + owner + '.txt').decode().split()[0] == summary['binary_sha256'][binary], role + '/' + owner + ' actual proc ELF')
for name, value in summary['source_inputs'].items():
    check(digest((E / 'source' / name).read_bytes()) == value, 'original frozen source bytes ' + name)

selection = server('selection.json')
selected = server('selected-state.json')
previous = server('state-before-ack.json')
sessions = server('selected-sessions.json')
held = server('root-held.json')
baseline = server('host-before.json')
check(selected['sequence'] == previous['sequence'] + 1 and selected['audit'][-1]['action'] == 'resource', 'actual selected original second Resource transaction')
check(len(sessions) == 2 and len(held['exports']) == 2 and held['records'] == held['terminals'] == [], 'two actual accepted tracked exports')
check(sum(a['resource_id'] is not None for s in previous['sessions'] for a in s['attempts']) == 1, 'first Resource already acknowledged, second Resource not yet published')
check({s['job_id'] for s in sessions} == {selection['job']}, 'both exact original sessions same job')
expected = []
for session in sessions:
    attempt = session['attempts'][0]
    records = [r for r in held['exports'] if r['lease_id'] == attempt['resource_id']]
    check(len(records) == 1 and records[0]['correlation'] == attempt['correlation'], 'exact original received resource ' + session['id'])
    record = records[0]
    check(record['bound'] is not None and record['stub'] is None and record['owner']['uid'] == 2000, 'actual export bound before any IMPORT ' + session['id'])
    expected.append({'terminal': {'resource_id': attempt['resource_id'], 'kind': 'native_export', 'device_key': session['device_key'], 'owner_uid': 2000, 'correlation': attempt['correlation'], 'proof': 'native_export_restored'}, 'server_id': None})
def exact_retired(root):
    return root['records'] == root['exports'] == [] and len(root['terminals']) == 2 and all(x in root['terminals'] for x in expected)

observations = [json.loads(x) for x in raw('server', 'held-observations.jsonl').splitlines()]
check([x['observation'] for x in observations] == [1, 2], 'actual two original held observations')
first_root = server('root-observe-1.json')
first_host = server('host-observe-1.json')
check(first_root == held and not exact_retired(first_root), 'first saved root sample still contains both exports and no terminal')
check(observations[0]['exact_root_terminal'] is True, 'first guest boolean contradicts saved root sample; original failure reproduced by raw facts')
check(first_host != baseline and observations[0]['baseline_equal'] is False, 'transitional first sysfs sample not accepted as complete baseline')
check(server('host-observe-2.json') == baseline and exact_retired(server('root-observe-2.json')), 'second saved root and host samples establish actual exact release and complete baseline')
check(all(observations[1][k] is True for k in ('baseline_equal', 'target_fd_closed', 'exact_root_terminal')) and observations[1]['gate_released'] is False, 'second guest booleans match saved evidence')
check(server('held-outcome.json')['failed'] == 0 and server('held-outcome.json')['resource_released_while_gate_held'] is True, 'actual guest latched cleanup while gate held')
check(exact_retired(server('root-before-release.json')) and server('host-before-release.json') == baseline, 'independent before-release original sample retains exact proof and baseline')
candidate = server('gate-candidate-' + str(selection['candidate']) + '.json')
held_before = server('gate-held-before.json')
held_after = server('gate-held-after.json')
ended = server('gate-exit-' + str(selection['candidate']) + '.json')
fields = ('candidate', 'tracee_pid', 'tracee_starttime', 'tid', 'fd', 'request', 'file_device', 'file_inode')
check(all(candidate[k] == held_before[k] == held_after[k] == ended[k] for k in fields), 'same selected actual fsync TID/FD/inode before and after observations and syscall return')
check(held_before['held'] and held_after['held'] and ended['return_value'] == 0 and ended['is_error'] is False, 'real gate still held before/after, then same syscall succeeds')
events = [json.loads(x) for x in raw('server', 'gate-events.jsonl').splitlines()]
released = [x for x in events if x['event'] == 'released']
check(len(released) == 1 and held_before['monotonic_ns'] < held_after['monotonic_ns'] < released[0]['monotonic_ns'] < ended['monotonic_ns'], 'only explicit gate release follows held-after receipt')
check(held_before['monotonic_ns'] / 1e9 < float(observations[1]['uptime']) < held_after['monotonic_ns'] / 1e9, 'second sample uptime lies inside actual held interval')
check(not any(x['event'] == 'signal_delivery' and x.get('signal') == 15 and held_before['monotonic_ns'] < x['monotonic_ns'] < held_after['monotonic_ns'] for x in events), 'original authorized StopJob path distinct from later SIGTERM')
check(raw('server', 'wait-stop.txt') == b'0\n' and raw('server', 'wait-tracer.txt') == b'0\n' and events[-1]['event'] == 'finished' and events[-1]['tracee_exit_code'] == 0, 'actual StopJob response and original traced owner/reaper waits')
check(any(a['action'] == 'stop' and a['actor_uid'] == 1000 and a['job_id'] == selection['job'] for a in server('agent-after-release.json')['audit']), 'authorized operator UID1000 stop audit for exact original job')
check(exact_retired(server('root-after-release.json')) and server('host-after-release.json') == baseline, 'original cleanup remains verified after original ACK returns')
fresh = server('root-reuse-ready.json')['exports']
check(len(fresh) == 2 and {x['lease_id'] for x in fresh}.isdisjoint({x['lease_id'] for x in held['exports']}), 'fresh real grant IDs after original retirement')
check(raw('client', 'tx-1.bin') == (E / 'ftdi-host-rx.bin').read_bytes() == b'RUSTUSB-TX-1-0001:fedcba9876543210\n', 'fresh actual FTDI transmit exact35')
check(raw('client', 'rx-1.bin') == (E / 'ftdi-host-injected-1.bin').read_bytes() == b'RUSTUSB-RX-1-0001:0123456789abcdef\n', 'fresh actual FTDI receive exact35')
evdev = raw('client', 'input-1.bin')
keys = [struct.unpack('<qqHHi', evdev[i:i+24])[2:] for i in range(0, len(evdev), 24)]
check(bool(evdev) and len(evdev) % 24 == 0 and (1,2,1) in keys and (1,2,0) in keys, 'fresh actual evdev KEY1 down/up')
check(server('host-final.json') == baseline, 'final complete original driver/config/IF baseline')
for role in ('server', 'client'):
    root = obj(role, 'root-final.json')
    check(root['records'] == root['exports'] == [], role + ' final actual root active resources empty')
frozen_guest = (E / 'source/scripts/qemu_prepare_recording_shutdown/guest-init.sh').read_text()
proof_body = frozen_guest.split('check_target_proof() {', 1)[1].split('\n}\n', 1)[0]
check(proof_body.count('"$ROOT_FILE"') == 2, 'source of mismatch: both proof branches re-read live journal rather than saved observation')
after = snapshot_manifest()
check(before == after, 'all original file bytes unchanged throughout review')
report = {
    'schema_version': 1, 'review_passed': True, 'runtime_result': 'FAIL',
    'author_role': 'Original harness author; separate raw-byte parsing method, not an independent implementation',
    'runtime_head': summary['head'], 'checks': checks, 'check_count': len(checks),
    'count_scope': 'diagnostic predicates, not new USB cases or software test executions',
    'frame_counts': frame_counts, 'source_count': len(summary['source_inputs']),
    'originals_count': len(before), 'mismatch': {'observation': 1, 'saved_root_exports': 2, 'saved_root_terminals': 0, 'guest_reported_terminal': True},
    'actual_second_sample': {'uptime': observations[1]['uptime'], 'exact_original_terminals': expected, 'host_equals_complete_baseline': True},
    'gate': {'before_ns': held_before['monotonic_ns'], 'after_ns': held_after['monotonic_ns'], 'release_ns': released[0]['monotonic_ns'], 'same_syscall_return': ended['return_value']},
    'conclusion': 'The first observation compares a saved root snapshot with a later live-journal predicate. This is an actual harness evidence mismatch and the original host FAIL stays unchanged. The second saved observation and before-release samples separately establish both exact original Native exports retired and full baselines restored while the same fsync gate remains held. Subsequent ACK, actual owner exit and fresh HID/FTDI reuse completed.',
    'limits': ['Sequential root/sysfs/FD snapshots are not atomic.', 'No attribution of arbitrary anonymous socket FDs to individual Native control channels.', 'No pending-URB, kernel-active, physical-hardware or ARM claim.', 'No conclusions about the 22 unexecuted scenarios.', 'No original verifier imported or invoked; no original evidence/source modified.']}
(O / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
(O / 'originals-manifest.json').write_text(json.dumps(before, indent=2) + '\n')
print(json.dumps({'review_passed': True, 'original_result': 'FAIL', 'checks': len(checks), 'originals': len(before), 'output': str(O)}))
