#!/usr/bin/env python3
"""Read existing artifacts only; never executes Cargo, product or runner code."""
import hashlib
import json
import os
from pathlib import Path
import re
import struct

ROOT = Path('/home/ubuntu/github/usbip-client')
BASE = Path('/tmp/rustusb-secure-local-signals-20261023')
OUT = Path('/tmp/rustusb-secure-software-independent-review-20261023.json')
checks = []
observed = {}


def data(path):
    path = Path(path)
    value = path.read_bytes()
    observed[str(path)] = {'path': str(path), 'length': len(value), 'sha256': hashlib.sha256(value).hexdigest()}
    return value


def load(path):
    return json.loads(data(path))


def check(name, good, detail=None):
    item = {'name': name, 'passed': bool(good)}
    if detail is not None:
        item['detail'] = detail
    checks.append(item)


before = load('/tmp/rustusb-secure-software-third-inputs-20261023.json')
after = load('/tmp/rustusb-secure-software-third-after-20261023.json')
result = load('/tmp/rustusb-secure-software-third-result-20261023.json')
status = load('/tmp/rustusb-secure-software-third-status-20261023.json')
log = data('/tmp/rustusb-secure-software-third-20261023.log').decode()
script = data(ROOT / 'scripts/check.sh').decode()
old = {i['path']: i for i in before['files']}
new = {i['path']: i for i in after['files']}
check('121 selected inputs, same exact records before and after', len(old) == 121 and old == new)
for name, item in old.items():
    content = (ROOT / name).read_bytes()
    check('selected input still matches: ' + name,
          len(content) == item['length'] and hashlib.sha256(content).hexdigest() == item['sha256'])
check('overall actual recorded exit status zero', status['exit_code'] == result['exit_code'] == 0)
rows = [tuple(map(int, row)) for row in re.findall(
    r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;', log)]
named_tests = re.findall(r'^test (.+?) \.\.\. ok$', log, re.M)
groups = [sum(row[0] for row in rows[:17]), sum(row[0] for row in rows[17:34]), sum(row[0] for row in rows[34:])]
check('35 actual result rows and 396 named executions', len(rows) == 35 and len(named_tests) == 396)
check('151 default / 225 all-features / 20 vendored executions', groups == [151, 225, 20], groups)
check('all actual result fields failure/ignore/measure/filter zero', all(all(n == 0 for n in row[1:]) for row in rows))
check('recomputed results equal parent result, without using it as parser',
      sum(row[0] for row in rows) == result['test_executions'] and len(rows) == result['test_result_records']
      and [row[0] for row in rows] == result['groups'])
check('raw log has two release completions and two rustdoc generation completions',
      log.count('Finished `release` profile') == 2 and log.count('Generated /home/ubuntu/github/usbip-client/target/doc/') == 2)
check('no warning/error compilation diagnostics in successful full log',
      not re.search(r'^(?:error(?:\[|:)|warning:)', log, re.M))
commands = [line for line in script.splitlines() if line.startswith('cargo ') or line.startswith('RUSTDOCFLAGS=')]
check('checked sequential script includes fmt / both Clippy / release / rustdoc / vendor tests',
      len(commands) == 10 and commands[0] == 'cargo fmt --all -- --check'
      and sum('clippy ' in c and '-D warnings' in c for c in commands) == 2
      and sum('cargo build ' in c and '--release' in c for c in commands) == 2
      and sum('cargo doc ' in c and '-D warnings' in c for c in commands) == 2
      and 'set -euo pipefail' in script)
for name, item in result['release_binaries'].items():
    content = (ROOT / name).read_bytes()
    check('release ELF current bytes match recorded result: ' + name,
          content.startswith(b'\x7fELF') and len(content) == item['length']
          and hashlib.sha256(content).hexdigest() == item['sha256'])

native_log = data('/tmp/rustusb-secure-native-only-tests-20261023.log').decode()
native_status = load('/tmp/rustusb-secure-native-only-status-20261023.json')
native_tree = data('/tmp/rustusb-secure-native-only-tree-20261023.txt').decode()
check('Native-only actual six tests pass with no ignored or failed',
      len(re.findall(r'^test .+ \.\.\. ok$', native_log, re.M)) == 6
      and 'test result: ok. 6 passed; 0 failed; 0 ignored;' in native_log and native_status['exit_code'] == 0)
check('Native-only dependency tree includes transport/native/daemon but no Rust USB engine',
      all(name in native_tree for name in ['rustusb-transport', 'rustusb-native', 'rustusb-daemon'])
      and 'rustusb-rust-backend' not in native_tree and native_status['rust_engine_in_feature_tree'] is False)

summary = load(BASE / 'summary.json')
signals_log = data('/tmp/rustusb-secure-local-signals-20261023.log').decode()
check('four distinct local ready/blocked INT/TERM cases',
      {(c['mode'], c['signal']) for c in summary['cases']} == {(m,s) for m in ['ready','blocked'] for s in ['sigint','sigterm']}
      and len(summary['cases']) == 4)
check('signals skipped build recorded honestly as external prebuilt features not inferred',
      summary['build'] == {'command': None, 'features': 'external prebuilt; not inferred', 'skipped': True})
release_sha = result['release_binaries']['target/release/rustusb']['sha256']
check('actual selected signal binary agrees with full-gate all-features release ELF',
      summary['binary_before']['sha256'] == summary['binary_after_sha256'] == release_sha
      and summary['binary_before']['length'] == result['release_binaries']['target/release/rustusb']['length'])
for indexed in summary['artifacts']:
    value = data(BASE / indexed['path'])
    check('local indexed raw length and SHA: ' + indexed['path'], len(value) == indexed['length'] and hashlib.sha256(value).hexdigest() == indexed['sha256'])
check('all 32 preexisting signal raw artifacts indexed',
      len(summary['artifacts']) == 32 and {i['path'] for i in summary['artifacts']}
      == {str(p.relative_to(BASE)) for p in BASE.rglob('*') if p.is_file() and p.name != 'summary.json'})
source_before = load(BASE / 'source-before.json')
source_after = load(BASE / 'source-after.json')
check('local source artifacts match embedded exact snapshots', source_before == summary['source_before'] and source_after == summary['source_after'])
check('local snapshots same source files/HEAD and explicitly dirty candidate',
      source_before['files'] == source_after['files'] and source_before['head'] == source_after['head'] == before['head']
      and source_before['dirty'] is True and source_after['dirty'] is True)
for source in [source_before, source_after]:
    digest = hashlib.sha256()
    for item in source['files']:
        digest.update(os.fsencode(item['path']) + b'\0' + str(item['length']).encode() + b'\0' + item['sha256'].encode() + b'\n')
    check('recomputed local source fingerprint ' + ('before' if source is source_before else 'after'), digest.hexdigest() == source['source_sha256'])
check('all 313 local captured source current bytes match', all(
    len((ROOT / i['path']).read_bytes()) == i['length'] and hashlib.sha256((ROOT / i['path']).read_bytes()).hexdigest() == i['sha256']
    for i in source_before['files']), len(source_before['files']))

case_results = []
for brief in summary['cases']:
    name = brief['mode'] + '-' + brief['signal']
    base = BASE / name
    case = load(base / 'result.json')
    pre = load(base / 'before.json')
    post = load(base / 'after.json')
    protocol = load(base / 'protocol.json')
    raw_stdout = data(base / 'stdout-consumed.bin')
    events = load(base / 'stdout-events.json')
    check(name + ': case actual artifacts equal summary', case == brief and case['before'] == pre and case['after'] == post)
    check(name + ': stdout parsed from raw byte prefix matches exact retained events', [json.loads(line) for line in raw_stdout.splitlines()] == events)
    devices = pre['snapshot']['devices']
    check(name + ': actual two profiles configured and held with one pending each',
          {(d['bus_id'],d['profile']) for d in devices} == {('1-1','hid_keyboard'),('1-2','cdc_acm')}
          and all(d['configuration'] == 1 and d['leased'] is True and d['pending_urbs'] == 1 for d in devices))
    for bus, endpoint, length in [('1-1',1,8),('1-2',2,64)]:
        wire = [p for p in protocol if p['bus_id'] == bus]
        imported, configured, complete, pending = wire
        request = bytes.fromhex(imported['request_hex'])
        response = bytes.fromhex(imported['header_hex'])
        setup = bytes.fromhex(configured['frame_hex'])
        finished = bytes.fromhex(complete['frame_hex'])
        waiting = bytes.fromhex(pending['frame_hex'])
        check(name + ': ' + bus + ' independent IMPORT/config/pending binary decode',
              struct.unpack('>HHI', request[:8]) == (0x111,0x8003,0)
              and request[8:].rstrip(b'\0').decode() == bus
              and struct.unpack('>HHI', response) == (0x111,3,0)
              and len(bytes.fromhex(imported['device_hex'])) == 312
              and struct.unpack('>II', setup[:8]) == (1,1) and setup[40:] == bytes.fromhex('0009010000000000')
              and struct.unpack('>II', finished[:8]) == (3,1)
              and struct.unpack('>iI', finished[20:28]) == (0,0)
              and struct.unpack('>II', waiting[:8]) == (1,2)
              and struct.unpack('>III', waiting[12:24]) == (1,endpoint,0)
              and struct.unpack('>I', waiting[24:28])[0] == length)
    check(name + ': signal sent only to original ordinary child identity',
          case['sent']['pid'] == case['child']['pid'] == case['sent']['identity']['pid']
          and case['sent']['identity'] == case['child'] and case['child']['uids'] == [1000]*4
          and case['sent']['signal'] == (2 if case['signal'] == 'sigint' else 15))
    check(name + ': actual /proc child ELF SHA and inode match selected release',
          case['executed_binary']['sha256'] == release_sha
          and case['executed_binary']['device'] == summary['binary_before']['device']
          and case['executed_binary']['inode'] == summary['binary_before']['inode'])
    check(name + ': actual cooperative exit0 bounded8s without force or unexpected data',
          case['forced_cleanup'] is False and case['final_returncode'] == post['returncode'] == 0
          and post['signal_exit_seconds'] < 8 and post['wait_error'] is None
          and set(post['data_sockets']) == {'1-1','1-2'}
          and all(v['eof'] is True and v['unexpected_hex'] == '' for v in post['data_sockets'].values()))
    if case['mode'] == 'ready':
        stopped = post['stopped_events']
        check(name + ': retained stopped resource snapshot proves virtual backend cleanup, beyond socket EOF',
              len(stopped) == 1 and post['backend_cleanup_observed'] is True and stopped[0] in events
              and all(d['leased'] is False and d['pending_urbs'] == d['configuration'] == d['hid_queued_reports'] == d['cdc_queued_bytes'] == 0 for d in stopped[0]['snapshot']['devices']))
    else:
        pipe = case['pipe']
        check(name + ': actual pipe4096 full, separate writer EAGAIN and child writer blocked before signal',
              pipe['capacity'] == pipe['bytes_after_fill'] == pipe['synthetic_padding_bytes'] == 4096
              and pipe['independent_writer_eagain'] is True
              and pipe['blocked_writer']['wchan'] == 'pipe_write'
              and pipe['blocked_writer']['monotonic_ns'] < case['sent']['monotonic_ns']
              and all(pipe[k] & os.O_NONBLOCK == 0 for k in ['original_writer_flags_before','original_writer_flags_after','child_stdout_flags_before','child_stdout_flags_after'])
              and pipe['independent_writer_flags'] & os.O_NONBLOCK != 0)
        check(name + ': no drain or fake backend stopped receipt while output remains blocked',
              case['stdout_drained_after_fill'] is False and post['stdout_read_after_fill'] is False
              and post['stdout_unread_bytes'] == 4096 and post['stopped_events'] == [] and post['backend_cleanup_observed'] is False
              and len(data(base / 'synthetic-padding.bin')) == 4096)
    case_results.append({'mode':case['mode'],'signal':case['signal'],'actual_exit':post['returncode'],'elapsed_seconds':post['signal_exit_seconds'],'backend_cleanup_snapshot_observed':post['backend_cleanup_observed']})

backpressure = json.loads(data('/tmp/rustusb-secure-local-backpressure-20261023.log'))
legacy = data(ROOT / 'scripts/test-virtual-backpressure.py').decode()
check('legacy two recorded backpressure scenarios meet original exits/time/socket assertions',
      backpressure['host_uid'] == 1000 and len(backpressure['checks']) == 2
      and backpressure['checks'][0]['mode'] == 'sigint' and backpressure['checks'][0]['exit'] == 0 and backpressure['checks'][0]['elapsed_seconds'] < 2
      and backpressure['checks'][1]['mode'] == 'deadline' and backpressure['checks'][1]['exit'] == 1 and 4 <= backpressure['checks'][1]['elapsed_seconds'] <= 7
      and all(c['stdout_capacity'] == 4096 and c['stdout_drained_after_fill'] is False and c['lease_socket_closed'] is True for c in backpressure['checks']))
check('legacy script default binary path points at selected release, no invented per-child ELF guard',
      'default=Path("target/release/rustusb")' in legacy and 'proc/' not in legacy and 'sha256' not in legacy)

raw_unchanged = all(len(Path(p).read_bytes()) == info['length'] and hashlib.sha256(Path(p).read_bytes()).hexdigest() == info['sha256'] for p,info in observed.items())
check('all inspected raw/source artifacts unchanged across this review', raw_unchanged)
report = {
    'schema_version':1, 'result':'PASS' if all(c['passed'] for c in checks) else 'FAIL',
    'scope':'Independent read-only existing log/JSON/raw protocol/source parser; no runner verification imports and no Cargo, product, test, QEMU or USB execution.',
    'attribution':{'head':before['head'],'source_scope':before['scope'],'candidate_dirty':True,
                   'default_test_executions':groups[0],'all_features_test_executions':groups[1],'vendored_test_executions':groups[2],
                   'full_gate_total':sum(groups),'result_rows':len(rows),'native_only_cli_tests_separate':6,
                   'release_binaries':result['release_binaries'],
                   'local_signal_build':'--skip-build explicitly external prebuilt; actual per-child ELF and workflow-linked preceding all-features release SHA agree; features are not inferred merely from --skip-build.',
                   'commands':commands,
                   'command_evidence_limit':'scripts/check.sh uses set -e and recorded overall exit0; compiler/test/release/rustdoc completions observed. Silent fmt and exact flags are linked to fixed checked script and root execution record, not separately printed shell xtrace.'},
    'local_signal_cases':case_results,
    'limits':['Dirty candidate software validation is not a clean-commit QEMU run, hardware G1/G2, encrypted physical end-to-end acceptance or entire Phase3 completion.',
              'Native-only six tests are separate from 396 full-gate executions and do not identify the all-features release binary.',
              'Local signal scenarios exercise ordinary-user loopback virtual profiles, not TLS or physical USB/kernel cleanup.',
              'Ready mode has actual virtual stopped snapshot; blocked mode intentionally has none and socket EOF alone is not claimed as backend cleanup proof.',
              'Legacy two-case backpressure runner records no per-child /proc ELF or source guards; its release SHA association is workflow provenance only (root states no intervening release rebuild), not independent process identity proof.',
              'Neither local runner provides real physical input/USB forwarding evidence.'],
    'checks':checks,'counts':{'checks':len(checks),'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks)},
    'artifacts':[observed[p] for p in sorted(observed)]
}
OUT.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'report':str(OUT),'length':OUT.stat().st_size,'sha256':hashlib.sha256(OUT.read_bytes()).hexdigest(),'counts':report['counts'],'result':report['result']}))
for item in checks:
    if not item['passed']:
        print('FAILED', item)
