#!/usr/bin/env python3
"""Full Ubuntu cloud-disk QEMU package lifecycle (not the initramfs USB suite)."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024*1024): h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, type=Path)
    parser.add_argument('--image-receipt', required=True, type=Path)
    parser.add_argument('--first', required=True, type=Path, help='Directory with one base+GUI pair')
    parser.add_argument('--second', required=True, type=Path, help='Higher-version base+GUI pair')
    parser.add_argument('--evidence', required=True, type=Path)
    args = parser.parse_args()
    out = args.evidence.resolve(); out.mkdir(mode=0o700)
    private = out/'.private'; private.mkdir(mode=0o700)
    receipt = json.loads(args.image_receipt.read_text())
    if not receipt['signature_verified'] or sha(args.image) != receipt['sha256']:
        raise ValueError('verified image receipt does not match actual image')
    (out/'image-provenance.json').write_text(json.dumps(receipt, indent=2)+'\n')
    files = {}
    for directory in (ROOT/'packaging', HERE):
        for path in sorted(directory.rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                files[str(path.relative_to(ROOT))] = sha(path)
    inputs = {'git_head': subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
              'git_status': subprocess.check_output(['git','status','--porcelain'], cwd=ROOT, text=True).strip(),
              'source': files, 'binaries': {str(path.relative_to(ROOT)): sha(path) for path in
              [ROOT/'target/release/rustusb', ROOT/'target/release/rustusb-helper', ROOT/'apps/rustusb-gui/target/release/rustusb-gui']},
              'packages': {}}
    fixture = private/'fixture'; fixture.mkdir()
    groups = {}
    for key, directory in [('first', args.first), ('second', args.second)]:
        packages = sorted(directory.glob('*.deb'), key=lambda p: p.name.startswith('rustusb-gui_'))
        if len(packages) != 2: raise ValueError('expected one base+GUI package pair')
        groups[key] = []
        for path in packages:
            shutil.copyfile(path, fixture/path.name)
            inputs['packages'][path.name] = sha(path)
            groups[key].append(path.name)
    (fixture/'packages.json').write_text(json.dumps(groups)+'\n')
    (out/'inputs-before.json').write_text(json.dumps(inputs, indent=2)+'\n')
    spec = importlib.util.spec_from_file_location('package_secure_support', ROOT/'scripts/qemu/run-secure-qemu.py')
    support = importlib.util.module_from_spec(spec); spec.loader.exec_module(support)
    credentials = support.credentials(private, out)
    for name in ('ca.der', 'server.der', 'client.der', 'server-key.der'):
        shutil.copyfile(credentials/name, fixture/name)
    shutil.copyfile(HERE/'guest.py', fixture/'guest.py')
    key = private/'ssh-key'
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
    seed = private/'seed'; seed.mkdir()
    (seed/'meta-data').write_text('instance-id: rustusb-package-test\nlocal-hostname: rustusb-package-test\n')
    (seed/'user-data').write_text('#cloud-config\nssh_pwauth: false\nusers:\n  - name: ubuntu\n    groups: [sudo]\n    sudo: ALL=(ALL) NOPASSWD:ALL\n    shell: /bin/bash\n    lock_passwd: true\n    ssh_authorized_keys:\n      - '+key.with_suffix('.pub').read_text().strip()+'\n')
    subprocess.run(['genisoimage', '-quiet', '-output', str(private/'seed.iso'), '-volid', 'cidata', '-joliet', '-rock', str(seed/'user-data'), str(seed/'meta-data')], check=True)
    disk = private/'disk.qcow2'
    subprocess.run(['qemu-img', 'create', '-q', '-f', 'qcow2', '-F', 'qcow2', '-b', str(args.image.resolve()), str(disk), '16G'], check=True)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0)); port = reservation.getsockname()[1]
    ssh_options = ['-i', str(key), '-p', str(port), '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile='+str(private/'known_hosts'), '-o', 'ConnectTimeout=3', '-o', 'BatchMode=yes']
    ssh = ['ssh', *ssh_options, 'ubuntu@127.0.0.1']
    console = (out/'serial.log').open('wb'); errors = (out/'qemu.stderr').open('wb')
    qemu = subprocess.Popen(['qemu-system-x86_64', '-enable-kvm', '-cpu', 'host', '-m', '4096', '-smp', '4', '-display', 'none', '-serial', 'stdio', '-monitor', 'none', '-drive', 'file='+str(disk)+',if=virtio,format=qcow2', '-drive', 'file='+str(private/'seed.iso')+',media=cdrom,readonly=on', '-netdev', f'user,id=net0,hostfwd=tcp:127.0.0.1:{port}-:22', '-device', 'virtio-net-pci,netdev=net0', '-no-reboot'], stdout=console, stderr=errors)
    summary = {'passed': False, 'qemu_exit': None, 'forced': False, 'stages': []}
    def remote(command, filename, expected=(0,)):
        result = subprocess.run(ssh+[command], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (out/(filename+'.stdout')).write_bytes(result.stdout); (out/(filename+'.stderr')).write_bytes(result.stderr)
        (out/(filename+'.json')).write_text(json.dumps({'command': command, 'exit': result.returncode})+'\n')
        if result.returncode not in expected: raise RuntimeError(filename+' exit '+str(result.returncode))
        return result
    def wait_ssh():
        for _ in range(240):
            result = subprocess.run(ssh+['true'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode == 0: return
            if qemu.poll() is not None: raise RuntimeError('QEMU exited before SSH')
            time.sleep(1)
        raise RuntimeError('SSH readiness observation expired')
    try:
        wait_ssh(); remote('sudo cloud-init status --wait', 'cloud-init')
        remote('mkdir -m 700 /var/tmp/rustusb-package-fixture', 'create-fixture')
        scp_args = ['scp', '-q', '-i', str(key), '-P', str(port), '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile='+str(private/'known_hosts')]
        with (out/'scp.log').open('wb') as log:
            subprocess.run(scp_args+[str(p) for p in fixture.iterdir()]+['ubuntu@127.0.0.1:/var/tmp/rustusb-package-fixture/'], stdout=log, stderr=subprocess.STDOUT, check=True)
        remote('sudo python3 /var/tmp/rustusb-package-fixture/guest.py install', 'install')
        summary['stages'].append('install-upgrade')
        # Full guest reboot, observed as this QEMU process actually exits0.
        remote('sudo systemctl reboot', 'request-reboot', (0,255))
        summary['first_qemu_exit'] = qemu.wait(timeout=120)
        if summary['first_qemu_exit'] != 0: raise RuntimeError('first QEMU exit nonzero')
        summary['stages'].append('reboot')
        command = qemu.args[:]  # Same disk/NoCloud ID and hardware; guest boot ID must change.
        qemu = subprocess.Popen(command, stdout=console, stderr=errors)
        wait_ssh()
        remote('sudo python3 /var/tmp/rustusb-package-fixture/guest.py after-reboot', 'after-reboot')
        summary['stages'].append('remove-purge')
        summary['passed'] = True
    finally:
        if qemu.poll() is None:
            try:
                with (out/'guest-evidence.tar').open('wb') as stream:
                    result = subprocess.run(ssh+['sudo tar -C /var/tmp/rustusb-package-evidence -cf - .'], stdout=stream, stderr=subprocess.PIPE)
                    (out/'collect.stderr').write_bytes(result.stderr)
                    summary['collect_exit'] = result.returncode
                remote('sudo systemctl poweroff', 'poweroff', (0,255))
                summary['qemu_exit'] = qemu.wait(timeout=120)
            except Exception as error:
                summary['cleanup_error'] = str(error); summary['passed'] = False
                # Failed development environments are not successful lifecycle tests.
                qemu.terminate(); summary['forced'] = True
                summary['qemu_exit'] = qemu.wait()
        else: summary['qemu_exit'] = qemu.returncode
        console.close(); errors.close()
        summary['source_guard'] = all(sha(ROOT/name) == value for name,value in files.items())
        summary['binary_guard'] = all(sha(ROOT/name) == value for name,value in inputs['binaries'].items())
        summary['head_guard'] = subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip() == inputs['git_head']
        summary['passed'] = summary['passed'] and summary['qemu_exit'] == 0 and summary.get('collect_exit') == 0 and not summary['forced'] and all(summary[k] for k in ('source_guard','binary_guard','head_guard'))
        (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
        print(json.dumps(summary), flush=True)
    if not summary['passed']: raise SystemExit(1)


if __name__ == '__main__': main()
