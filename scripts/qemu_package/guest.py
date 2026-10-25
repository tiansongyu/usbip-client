#!/usr/bin/env python3
"""Disposable full-Ubuntu package acceptance, invoked as guest root only."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import subprocess
import sys
import time

FIXTURE = Path('/var/tmp/rustusb-package-fixture')
OUT = Path('/var/tmp/rustusb-package-evidence')
OUT.mkdir(mode=0o700, exist_ok=True)
EVENTS = OUT/'checks.jsonl'
INDEX = len(list(OUT.glob('command-*.json')))


def check(condition, label):
    with EVENTS.open('a') as stream:
        stream.write(json.dumps({'label': label, 'passed': bool(condition)})+'\n')
    if not condition:
        raise AssertionError(label)


def run(args, expected=(0,)):
    global INDEX
    INDEX += 1
    base = OUT/f'command-{INDEX:04d}'
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    base.with_suffix('.stdout').write_bytes(result.stdout)
    base.with_suffix('.stderr').write_bytes(result.stderr)
    base.with_suffix('.json').write_text(json.dumps({'args': [str(x) for x in args], 'exit': result.returncode})+'\n')
    if result.returncode not in expected:
        raise RuntimeError(f'command {INDEX} returned {result.returncode}: {args}')
    return result


def text(args, expected=(0,)):
    return run(args, expected).stdout.decode().strip()


def service(name, key):
    return text(['systemctl', 'show', name, '-p', key, '--value'])


def agent(*args):
    return json.loads(text(['runuser', '-u', 'ubuntu', '--', '/usr/bin/rustusb', '--json',
                           'agent', '--config', '/etc/rustusb/agent.json', *args]))


def ready():
    for _ in range(100):
        result = run(['runuser', '-u', 'ubuntu', '--', '/usr/bin/rustusb', '--json',
                      'agent', '--config', '/etc/rustusb/agent.json', 'profiles'], (0, 1))
        if result.returncode == 0:
            data = json.loads(result.stdout)
            check(data['journal_healthy'] and data['response']['result'] == 'profiles', 'actual ordinary-user profile readiness')
            return
        time.sleep(.1)
    raise RuntimeError('agent readiness observation expired; not a completed stop')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def provision():
    entry = pwd.getpwnam('rustusb')
    directory = Path('/etc/rustusb')
    for name in ('ca.der', 'server.der', 'client.der', 'server-key.der'):
        dest = directory/name
        dest.write_bytes((FIXTURE/name).read_bytes())
        if name.endswith('-key.der'):
            os.chown(dest, entry.pw_uid, entry.pw_gid)
            dest.chmod(0o600)
        else:
            dest.chmod(0o644)
    pin = sha(directory/'client.der')
    policy = dict(allowed_uids=[], export_device_ids=[], peers=[], trusted_network=False,
                  secure_broker_uid=entry.pw_uid)
    server = dict(schema_version=1, broker_uid=entry.pw_uid, listen='127.0.0.1:3249',
                  devices=['virtual:hid'], principals=[dict(id='package-client', pins=[pin], devices=['virtual:hid'])],
                  ca_certificates=['/etc/rustusb/ca.der'], certificate_chain=['/etc/rustusb/server.der'],
                  private_key='/etc/rustusb/server-key.der')
    manifest = dict(schema_version=1, broker_uid=entry.pw_uid, helper_socket='/run/rustusb/control.sock',
                    control_directory='/run/rustusb-agent', data_directory='/var/lib/rustusb-agent',
                    profiles=[dict(id='package-virtual', backend='virtual', config='/etc/rustusb/server.json')],
                    operators=[dict(uid=pwd.getpwnam('ubuntu').pw_uid, profiles=['package-virtual'], actions=['read', 'start', 'stop', 'discover', 'audit'])])
    for name, value in [('policy.json', policy), ('server.json', server), ('agent.json', manifest)]:
        (directory/name).write_text(json.dumps(value)+'\n')
        (directory/name).chmod(0o644)
    (OUT/'uid.json').write_text(json.dumps({'uid': entry.pw_uid, 'gid': entry.pw_gid})+'\n')
    (OUT/'config-hashes.json').write_text(json.dumps({p.name: sha(p) for p in directory.iterdir() if p.is_file()})+'\n')


def desktop():
    children = []
    logs = []
    try:
        for name, args in [('xvfb', ['Xvfb', ':99', '-screen', '0', '1180x760x24', '-nolisten', 'tcp', '-ac']),
                           ('openbox', ['runuser', '-u', 'ubuntu', '--', 'env', 'DISPLAY=:99', 'openbox'])]:
            log = (OUT/(name+'.log')).open('wb'); logs.append(log)
            child = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT); children.append(child)
            time.sleep(1)
            check(child.poll() is None, name+' actual process running')
        log = (OUT/'gui.log').open('wb'); logs.append(log)
        gui = subprocess.Popen(['runuser', '-u', 'ubuntu', '--', 'env', 'DISPLAY=:99', 'LIBGL_ALWAYS_SOFTWARE=1',
                                'WEBKIT_DISABLE_DMABUF_RENDERER=1', '/usr/bin/rustusb-gui', '--manifest', '/etc/rustusb/agent.json'], stdout=log, stderr=subprocess.STDOUT)
        children.append(gui)
        window = None
        for _ in range(100):
            rows = text(['env', 'DISPLAY=:99', 'wmctrl', '-lp'])
            matches = [line.split()[0] for line in rows.splitlines() if 'RustUSB' in line]
            if matches:
                window = matches[0]; break
            check(gui.poll() is None, 'packaged GUI remains alive before native window')
            time.sleep(.2)
        check(window is not None, 'packaged ordinary-user native GUI window')
        time.sleep(2)
        run(['env', 'DISPLAY=:99', 'wmctrl', '-ic', window])
        code = gui.wait(timeout=60)
        (OUT/'gui-exit.json').write_text(json.dumps({'exit': code, 'wm_delete_window': True})+'\n')
        check(code == 0, 'packaged GUI actual cooperative WM close0')
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
            code = child.wait()
            with (OUT/'desktop-processes.jsonl').open('a') as stream:
                stream.write(json.dumps({'pid': child.pid, 'exit': code})+'\n')
        for log in logs: log.close()


def install():
    run(['dpkg-query', '-W', 'systemd', 'dpkg', 'init-system-helpers'])
    run(['systemctl', '--version']); run(['uname', '-a'])
    run(['apt-get', 'update'])
    packages = json.loads((FIXTURE/'packages.json').read_text())
    first = [str(FIXTURE/name) for name in packages['first']]
    second = [str(FIXTURE/name) for name in packages['second']]
    run(['env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', 'install', '-y', *first, 'xvfb', 'openbox', 'wmctrl'])
    for unit in ('rustusb-agent.service', 'rustusb-helper.service'):
        check(text(['systemctl', 'is-enabled', unit], (1,)) == 'disabled', 'fresh '+unit+' disabled')
        check(service(unit, 'ActiveState') == 'inactive', 'fresh '+unit+' inactive')
    check(not Path('/etc/rustusb/policy.json').exists() and not Path('/etc/rustusb/agent.json').exists(), 'no live policy/manifest installed')
    check(not text(['dpkg-query', '-W', '-f=${Conffiles}', 'rustusb']), 'no payload conffiles')
    run(['systemd-analyze', 'verify', '/lib/systemd/system/rustusb-helper.service', '/lib/systemd/system/rustusb-agent.service'])
    provision()
    # Refuse wrong mode without rewriting a retained tree.
    data = Path('/var/lib/rustusb-agent')
    data.mkdir(mode=0o755)
    entry = pwd.getpwnam('rustusb'); os.chown(data, entry.pw_uid, entry.pw_gid)
    (data/'retained-sentinel').write_text('preserve-package-acceptance\n')
    before = sha(data/'retained-sentinel')
    result = run(['/usr/bin/python3', '-I', '/usr/libexec/rustusb/package-control', 'prepare-agent'], (1,))
    check(b'unexpected existing directory' in result.stderr and (data.stat().st_mode & 0o777) == 0o755 and sha(data/'retained-sentinel') == before, 'wrong mode refused without repair/data loss')
    data.chmod(0o700)  # Explicit test administrator correction, not product repair.
    run(['systemctl', 'start', 'rustusb-helper.service', 'rustusb-agent.service']); ready()
    desktop()
    started = agent('start', 'package-virtual')
    check(started['response']['result'] == 'started' and not started['response']['replayed'], 'ordinary operator actually starts virtual job')
    (OUT/'started.json').write_text(json.dumps(started)+'\n')
    for _ in range(100):
        sessions = agent('sessions', 'package-virtual')
        (OUT/'sessions-before-upgrade.json').write_text(json.dumps(sessions)+'\n')
        if any(row['state'] == 'Available' and row['job_id'] == started['response']['job']['id']
               for row in sessions['response']['sessions']): break
        time.sleep(.1)
    else: raise RuntimeError('virtual job not available')
    # Invalid unit override: both old prerm and new failed-upgrade must refuse.
    override = Path('/etc/systemd/system/rustusb-agent.service.d')
    override.mkdir()
    (override/'unsafe-test.conf').write_text('[Service]\nSendSIGKILL=yes\n')
    run(['systemctl', 'daemon-reload'])
    version = text(['dpkg-query', '-W', '-f=${Version}', 'rustusb'])
    binary = sha('/usr/bin/rustusb')
    result = run(['dpkg', '-i', second[0]], (1,))
    check(b'failed-upgrade' in result.stderr and text(['dpkg-query', '-W', '-f=${Version}', 'rustusb']) == version and sha('/usr/bin/rustusb') == binary, 'failed prerm and failed-upgrade preserve old package')
    (override/'unsafe-test.conf').unlink(); override.rmdir()
    run(['systemctl', 'daemon-reload'])
    # A stopped actual owner keeps dpkg pending; do not treat a deadline as exit.
    pid = int(service('rustusb-agent.service', 'MainPID')); os.kill(pid, signal.SIGSTOP)
    log = (OUT/'blocked-upgrade.log').open('wb')
    upgrade = subprocess.Popen(['dpkg', '-i', *second], stdout=log, stderr=subprocess.STDOUT)
    try:
        time.sleep(3)
        check(upgrade.poll() is None and service('rustusb-agent.service', 'ActiveState') == 'deactivating', 'actual upgrade waits for stopped original broker')
        check(service('rustusb-helper.service', 'ActiveState') == 'active' and sha('/usr/bin/rustusb') == binary, 'helper remains during broker cleanup before replacement')
    finally:
        os.kill(pid, signal.SIGCONT)
        code = upgrade.wait(); log.close()
        (OUT/'upgrade-exit.json').write_text(json.dumps({'exit': code})+'\n')
    check(code == 0, 'upgrade actually exits0 after original broker continues')
    for unit in ('rustusb-agent.service', 'rustusb-helper.service'):
        check(service(unit, 'ActiveState') == 'inactive' and service(unit, 'Result') == 'success', 'upgrade leaves '+unit+' cooperatively stopped')
    state = json.loads((data/'state.json').read_text())
    (OUT/'state-after-upgrade.json').write_text(json.dumps(state)+'\n')
    check(any(job['id'] == started['response']['job']['id'] for job in state['jobs']), 'original application job journal retained')
    # Explicit administrator boot authorization; no job resubmission.
    run(['systemctl', 'enable', 'rustusb-helper.service', 'rustusb-agent.service'])
    (OUT/'boot-before.txt').write_text(Path('/proc/sys/kernel/random/boot_id').read_text())
    (OUT/'stage-install-complete').write_text('ok\n')


def after_reboot():
    check(Path('/proc/sys/kernel/random/boot_id').read_text() != (OUT/'boot-before.txt').read_text(), 'actual full-system reboot occurred')
    ready()
    for unit in ('rustusb-agent.service', 'rustusb-helper.service'):
        check(service(unit, 'ActiveState') == 'active', 'explicitly enabled '+unit+' starts after reboot')
    started = json.loads((OUT/'started.json').read_text())
    state = json.loads(Path('/var/lib/rustusb-agent/state.json').read_text())
    check(any(job['id'] == started['response']['job']['id'] for job in state['jobs']), 'old job identity retained across boot')
    sentinel = sha('/var/lib/rustusb-agent/retained-sentinel')
    hashes = json.loads((OUT/'config-hashes.json').read_text())
    uid = json.loads((OUT/'uid.json').read_text())
    # Ordinary admin mask is independent from package enable bookkeeping.
    run(['systemctl', 'stop', 'rustusb-agent.service', 'rustusb-helper.service'])
    run(['systemctl', 'mask', 'rustusb-agent.service'])
    run(['dpkg', '--remove', 'rustusb-gui', 'rustusb'])
    run(['dpkg', '--purge', 'rustusb-gui', 'rustusb'])
    check(not Path('/usr/bin/rustusb').exists() and not Path('/usr/bin/rustusb-gui').exists(), 'package binaries actually removed')
    check(pwd.getpwnam('rustusb').pw_uid == uid['uid'], 'broker UID retained after purge')
    check(all(sha(Path('/etc/rustusb')/name) == value for name, value in hashes.items()), 'all administrator config/credential bytes retained')
    check(sha('/var/lib/rustusb-agent/retained-sentinel') == sentinel and Path('/var/lib/rustusb-agent/state.json').exists(), 'private data and old journal retained after purge')
    check(os.readlink('/etc/systemd/system/rustusb-agent.service') == '/dev/null', 'administrator mask retained')
    for unit in ('rustusb-agent.service', 'rustusb-helper.service'):
        check(not os.path.lexists('/etc/systemd/system/multi-user.target.wants/'+unit), 'package enable link removed '+unit)
    run(['journalctl', '-b', '--no-pager', '-u', 'rustusb-helper.service', '-u', 'rustusb-agent.service'])
    (OUT/'stage-after-reboot-complete').write_text('ok\n')


if __name__ == '__main__':
    check(os.geteuid() == 0 and Path('/proc/1/comm').read_text().strip() == 'systemd', 'guest root with real systemd PID1')
    {'install': install, 'after-reboot': after_reboot}[sys.argv[1]]()
