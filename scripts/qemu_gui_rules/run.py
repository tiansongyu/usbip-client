#!/usr/bin/env python3
"""Native GUI rule CRUD and real lost ACK, preserving original Native USB proofs."""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

auto = module('gui_rules_auto', ROOT / 'scripts/qemu_automatic_rules/run.py')
actions = module('gui_rules_actions', ROOT / 'scripts/qemu_gui_actions/run.py')
base = auto.agent
old_inputs, old_guest, old_payload = base.inputs, base.guest_source, base.payload
DRIVER = None

def inputs():
    values = old_inputs()
    values.update(actions.inputs())
    for p in HERE.iterdir():
        if p.is_file() and p.suffix in ['.py', '.sh', '.md']:
            values[str(p.relative_to(ROOT))] = {'bytes': p.stat().st_size, 'sha256': base.digest(p)}
    return dict(sorted(values.items()))

def guest():
    source = old_guest()
    functions = (ROOT / 'scripts/qemu_gui_actions/guest-functions.sh').read_text()
    # A fixture failure while the real agent is stopped must still resume and join it.
    functions = auto.replace_once(functions, '    local original=$?\n', '    local original=$?\n    [ -z "${AGENT_PID:-}" ] || kill -CONT "$AGENT_PID" 2>/dev/null || true\n')
    anchor = '[ "${AGENT_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2\n'
    source = auto.replace_once(source, anchor, anchor + functions + '\n' + (HERE / 'guest.sh').read_text() + '\n')
    source = auto.replace_once(source, '    automatic_policy\n', '    gui_infra\n    gui_rules_no_policy\n    automatic_policy\n')
    source = auto.replace_once(source, '        automatic_start_job server\n', '        gui_rules_start\n')
    return auto.replace_once(source, 'STAGE=complete\necho "RUSTUSB_AGENT_RESULT: PASS role=$ROLE"', '[ "$ROLE" != server ] || gui_infra_stop\nSTAGE=complete\necho "RUSTUSB_AGENT_RESULT: PASS role=$ROLE"')

def payload(work, kernel, certificates, role):
    archive, provenance = old_payload(work, kernel, certificates, role)
    if role != 'server':
        return archive, provenance
    root = work / 'root'
    actions.install_native_automation(work, root, provenance, DRIVER)
    shutil.copyfile(root / 'init', work / 'generated-agent-init.sh')
    names = b'.\0' + b'\0'.join(str(p.relative_to(root)).encode() for p in root.rglob('*')) + b'\0'
    plain, archive = work / 'gui-rules.cpio', work / 'gui-rules.cpio.gz'
    with plain.open('wb') as stream:
        result = subprocess.run(['cpio', '--null', '-o', '--format=newc', '--owner=0:0'], input=names, cwd=root, stdout=stream, stderr=subprocess.PIPE, check=True)
    (work / 'gui-cpio.stderr').write_bytes(result.stderr)
    with plain.open('rb') as source, gzip.open(archive, 'wb', compresslevel=1) as dest:
        shutil.copyfileobj(source, dest)
    return archive, provenance

def verify(evidence, args):
    # Original 264 assertions/kernel/Root proofs/three HID+FTDI rounds stay intact.
    facts = auto.original_verify(evidence, args, {'server final': 3, 'server held0': 3, 'server held1': 3})
    folder = evidence / 'server-artifacts'
    get = lambda n: json.loads((folder / n).read_text())
    checks = []
    def require(condition, label):
        if not condition:
            raise RuntimeError('GUI rule check: ' + label)
        checks.append(label)
    gui_sha = base.digest(ROOT / 'apps/rustusb-gui/target/release/rustusb-gui')
    for label in ['rules-no-policy', 'rules-1', 'rules-2']:
        prefix = 'gui-' + label
        actor = (folder / (prefix + '-actor.txt')).read_text()
        require(bool(re.search(r'^Uid:\s+1000\s+1000\s+1000\s+1000$', actor, re.M)) and all(re.search(r'^' + c + r':\s+0000000000000000$', actor, re.M) for c in ['CapInh','CapPrm','CapEff','CapAmb']) and gui_sha in actor and 'TARGET=/dev/bus/usb/' not in actor, label + ': actual GUI UID1000/ELF/no caps/no USB FD')
        driver_actor = (folder / (prefix + '-driver-actor.txt')).read_text()
        require(base.digest(DRIVER) in driver_actor and bool(re.search(r'^Uid:\s+1000\s+1000\s+1000\s+1000$', driver_actor, re.M)), label + ': actual driver UID/ELF')
        close = (folder / (prefix + '-close.txt')).read_text()
        require('protocol=WM_DELETE_WINDOW' in close and 'sent=1' in close and bool(re.search(r'timestamp=[1-9][0-9]*', close)) and (folder/(prefix+'.wait')).read_text().strip() == '0' and 'local worker joined' in (folder/(prefix+'.stderr')).read_text(), label + ': native close/actual wait0/local join')
        require(get(prefix+'-session.json')['value']['capabilities']['browserName'] == 'wry', label + ': native WebKit session')
    require('GUI_FORCED_CHILD' not in (evidence/'server-evidence.log').read_text(), 'no forced GUI child')
    require(get('gui-no-policy.json')['value'] == {'policy': 'Policy disabled by administrator', 'disabled': True}, 'actual no-policy disabled UI')
    require(get('gui-no-policy-rejected.json')['value']['error'] == 'No automation grant for this profile', 'closed bridge refuses ungranted Create')
    require(get('gui-no-policy-before.json') == get('gui-no-policy-after.json'), 'no-policy bridge denial has no agent mutation')
    require(get('gui-rule-invalid.json')['value']['invalid'] and get('gui-rule-invalid.json')['value']['focused'], 'UTF-8 byte validation/focusable error summary')
    require(get('gui-rule-draft.json')['value'] == {'name':'QEMU GUI fixed pool','focus':'rule-name','start':2,'end':5}, 'draft/focus/caret retained across actual advancing refresh')
    pending = get('gui-rule-pending.json')['value']; reloaded = get('gui-rule-reloaded.json')['value']; unknown = get('gui-rule-unknown.json')['value']
    require(pending['pending'] is not None and reloaded['pending'] == pending['pending'] and pending['unresolved_rule'] == reloaded['unresolved_rule'] == unknown['unresolved_rule'], 'original typed payload/UUID and accepted slot survive real renderer reload')
    require(unknown['pending'] is None and 'Response unknown' in unknown['rules']['result'], 'actual five-second IPC timeout, original rule UUID retained')
    require(bool(re.search(r'^State:\s+T', (folder/'gui-rule-stopped-agent.txt').read_text(), re.M)), 'actual agent stopped during unknown response')
    request = unknown['unresolved_rule']['request_id']; before = get('gui-rule-before.json'); committed = get('gui-rule-late-commit.json'); resolved = get('gui-rule-resolved.json')['value']
    require(before['jobs'] == before['sessions'] == before['requests'] == [] and len(committed['automation']['rules']) == len(committed['automation']['mutations']) == 1 and committed['automation']['mutations'][0]['request_id'] == request, 'late original commits exactly one disabled Create with no Start')
    require(resolved['unresolved_rule'] is None and resolved['rules']['result'].startswith('Resolved original rule '), 'real RetryRuleMutation resolves original committed result')
    require(get('gui-rule-before-resolve.json')['automation'] == get('gui-rule-after-resolve.json')['automation'], 'original query never reapplies edit')
    require('revision changed' in get('gui-rule-stale-draft.json')['value']['text'] and get('gui-rule-stale-draft.json')['value']['name'] == 'Preserved stale draft' and get('gui-rule-stale-before.json')['automation'] == get('gui-rule-stale-after.json')['automation'], 'concurrent revision preserves draft and blocks stale edit before IPC')
    final = get('agent-stopped-2.json'); journal = final['automation']; rule = journal['rules'][0]
    require(set(journal) == {'sequence','rules','mutations','origins','events'} and len(journal['rules']) == 1 and len(journal['origins']) == len(final['jobs']) == 2, 'strict disk3/one rule/two actual auto jobs')
    require(rule['revision'] == 9 and rule['deleted'] and not rule['enabled'] and [o['revision'] for o in journal['origins']] == [5,7], 'real Create/Replace/Enable/rearm/Disable/Delete and original firing revisions')
    require([e['kind'] for e in journal['events']] == ['created','replaced','disabled','replaced','enabled','fired','paused_by_stop','enabled','fired','disabled','deleted'], 'exact committed independent rule event chain')
    for epoch, revision in [(1,5),(2,7)]:
        state = get(f'agent-auto-admitted-{epoch}.json'); origin = next(o for o in state['automation']['origins'] if o['revision'] == revision)
        job = next(j for j in state['jobs'] if j['id'] == origin['job_id']); start = next(r for r in state['requests'] if r['id'] == origin['request_id'])
        require(origin['operator_uid'] == job['operator_uid'] == start['uid'] == 1000 and origin['rule_id'] == rule['id'] and start['job_id'] == job['id'] and origin['profile'] == job['profile'] == start['profile'] == 'server', f'epoch{epoch} actual original origin/job/Start binding')
        require(get(f'start-server-{epoch}.json')['response']['replayed'] is True and any(j['id']==job['id'] and j['state']=='running' and not j['stop_requested'] for j in get(f'gui-rules-after-close-{epoch}.json')['response']['jobs']), f'epoch{epoch} actual RetryStart and job continues after native close')
    paused = get('agent-paused-restart.json')
    require(len(paused['jobs']) == len(paused['automation']['origins']) == 1 and paused['automation']['rules'][0]['paused_by_user'] and paused['automation']['rules'][0]['revision'] == 6, 'manual stop pause survives actual agent restart and settle')
    for name in ['disabled','deleted']:
        view = get(f'gui-rule-{name}-active.json')['value']; row = view['rules']['rules'][0]
        require(not row['enabled'] and row['last_job']['state'] == 'running' and not row['last_job']['stop_requested'] and (name != 'deleted' or row['deleted']), name + ': future intent separate from actual active job')
    for p in folder.glob('gui-rule-confirm-*.json'):
        require(json.loads(p.read_text())['value']['focus'] == 'cancel', p.name + ': default Cancel')
    facts['gui_rules'] = {'checks': checks, 'assertion_count':len(checks), 'native_processes':3, 'gui_sha256':gui_sha, 'scope':'Actual Native GUI, real lost IPC ACK/renderer reload/query, fixed-pool CRUD and original I/O; no settled-negative recovery, dynamic pools, installation or hardware.'}
    return facts

def main():
    global DRIVER
    parser = argparse.ArgumentParser(add_help=False);parser.add_argument('--driver', type=Path, required=True)
    own, remaining = parser.parse_known_args();DRIVER = own.driver.resolve()
    assert DRIVER.is_file() and DRIVER.read_bytes()[:4] == b'\x7fELF'
    sys.argv = [sys.argv[0], *remaining]
    base.inputs, base.guest_source, base.payload, base.verify = inputs, guest, payload, verify
    parser = argparse.ArgumentParser(add_help=False);parser.add_argument('--evidence', type=Path, required=True);args, _ = parser.parse_known_args(remaining)
    gui_before, driver_before = base.digest(ROOT/'apps/rustusb-gui/target/release/rustusb-gui'), base.digest(DRIVER)
    result = base.main(guest_memory_mib=4096)
    path = args.evidence/'summary.json';summary = json.loads(path.read_text())
    summary.update(gui_sha256=gui_before, driver_sha256=driver_before, gui_binary_guard=base.digest(ROOT/'apps/rustusb-gui/target/release/rustusb-gui')==gui_before, driver_guard=base.digest(DRIVER)==driver_before)
    if not summary['gui_binary_guard'] or not summary['driver_guard']:
        summary['result']='FAIL';result=1
    path.write_text(json.dumps(summary,indent=2)+'\n');return result
if __name__ == '__main__':
    raise SystemExit(main())
