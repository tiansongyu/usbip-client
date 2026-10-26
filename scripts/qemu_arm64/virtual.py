#!/usr/bin/env python3
"""Two actual ARM64 Ubuntu guests; packaged virtual HID/CDC over framed mTLS."""
import argparse
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import tarfile
import time
import traceback

from arm_vm import ArmVm, sha

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('virtual_secure', ROOT / 'scripts/qemu/run-secure-qemu.py')
secure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(secure)


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


class Controller:
    def __init__(self, vm):
        self.vm = vm
        self.index = 0
        self.stderr = (vm.out / 'controller.stderr').open('wb')
        self.command = vm.ssh + ['sudo python3 /var/tmp/rustusb-installed-fixture/virtual_guest.py']
        save(vm.out / 'controller-command.json', self.command)
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.stderr, text=True)

    def call(self, action, *args):
        self.index += 1
        name = f'{self.index:03d}-{action}'
        request = {'action': action, 'args': args}
        save(self.vm.out / (name + '-request.json'), request)
        self.process.stdin.write(json.dumps(request) + '\n')
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        (self.vm.out / (name + '-response.json')).write_text(line)
        require(bool(line), 'actual guest controller EOF: ' + self.vm.role)
        response = json.loads(line)
        require(response['ok'], response.get('error', 'guest fixture failure'))
        return response['data']

    def close(self):
        self.process.stdin.close()
        # EOF triggers guest-side original-child TERM/wait before controller exit.
        self.process.stdout.read()
        code = self.process.wait()
        self.stderr.close()
        save(self.vm.out / 'controller-wait.json', {'exit': code})
        return code


def verify_held(held, uid, binary):
    rows = held['kernel']['journal']['records']
    require(len(rows) == 2 and held['kernel']['journal']['exports'] == [], 'two exact helper imports')
    vhci = [line.split() for item in held['kernel']['vhci'] for line in item['text'].splitlines()
            if line.startswith(('hs ', 'ss '))]
    used = [row for row in vhci if int(row[2]) == 6]
    require(len(used) == 2 and all(int(row[2]) in (4, 6) for row in vhci), 'two actual USED VHCI ports')
    for profile in ['hid', 'serial']:
        item = held[profile]
        receipt = item['event']['receipt']
        matches = [r for r in rows if r['channel_id'] == receipt['channel_id']]
        require(len(matches) == 1, 'unique original channel')
        row = matches[0]
        require(row['state']['phase'] == 'observed' and row['state']['receipt'] == receipt['kernel']
                and row['target'] == receipt['target'], 'exact helper target and kernel receipt')
        require(row['owner']['uid'] == uid and row['owner']['pid'] == item['identity']['pid']
                and row['owner']['starttime'] == item['identity']['starttime'], 'actual owner process identity')
        require(item['identity']['exe_sha256'] == binary and item['identity']['uids'] == [uid] * 4,
                'live native ARM executable and broker UID')
        require(receipt['kernel']['kernel_socket_inode'] > 0, 'actual kernel socket receipt')
        session = receipt['kernel']['session']
        require(sum(int(r[1]) == session['port'] and int(r[4], 16) == session['remote_device_id']
                    and r[6] == session['local_bus_id'] for r in used) == 1, 'independent live VHCI row')


def verify_snapshot(snapshot, cycle, held):
    devices = snapshot['snapshot']['devices']
    require(len(devices) == 2 and {d['profile'] for d in devices} == {'hid_keyboard', 'cdc_acm'},
            'exact original virtual profiles')
    require(all(d['leased'] == held and d['configuration'] == (1 if held else 0) for d in devices),
            'original leases and configuration')
    if not held:
        require(all(d['pending_urbs'] == 0 and d['hid_queued_reports'] == 0 and d['cdc_queued_bytes'] == 0
                    for d in devices), 'no residual virtual work')
    hid = next(d for d in devices if d['profile'] == 'hid_keyboard')
    cdc = next(d for d in devices if d['profile'] == 'cdc_acm')
    require(hid['counters']['hid_taps'] == cycle and cdc['counters']['cdc_out_bytes'] == 35 * cycle
            and cdc['counters']['cdc_in_bytes'] == 70 * cycle
            and cdc['counters']['cdc_injected_bytes'] == 35 * cycle, 'exact virtual I/O counters')


def one(args):
    out = args.output.resolve()
    out.mkdir(mode=0o700)
    private = out / '.private'
    private.mkdir(mode=0o700)
    head = git('rev-parse', 'HEAD')
    require(not git('status', '--porcelain'), 'clean source before owners')
    inputs = {n: sha(ROOT / n) for n in git('ls-files').splitlines()
              if not n.startswith('docs/evidence/') and (ROOT / n).is_file()}
    build = json.loads(args.build_receipt.read_text())
    require(build['git_head'] == 'ed1e8fef84c096feaa337484a4b75f145d6db278'
            and build['architecture'] == 'arm64' and build['git_status'] == '', 'original native ARM build receipt')
    package = next(p for p in build['packages'] if p['file'] == args.package.name)
    require(sha(args.package) == package['sha256'] and args.package.stat().st_size == package['bytes'],
            'exact previously built package')
    # Rust and build inputs must still be the executed product revision.
    product_names = git('ls-files', 'Cargo.toml', 'Cargo.lock', 'crates', 'vendor').splitlines()
    product_names = [n for n in product_names if n.endswith(('.rs', 'Cargo.toml', 'Cargo.lock'))]
    for name in product_names:
        require(subprocess.check_output(['git', 'show', build['git_head'] + ':' + name], cwd=ROOT)
                == (ROOT / name).read_bytes(), 'unchanged executed product source: ' + name)
    shutil.copyfile(args.build_receipt, out / 'native-build-receipt.json')
    parents = {r: args.prepared / r / '.private/disk.qcow2' for r in ['jammy', 'noble']}
    for release, path in parents.items():
        receipt = json.loads((args.prepared / release / 'preparation.json').read_text())
        require(receipt['passed'] and sha(path) == receipt['derived_disk_sha256'], 'exact closed prepared image')
        save(out / (release + '-preparation.json'), receipt)
    tools = args.tool_root.resolve()
    frozen_paths = [args.package, args.build_receipt, *parents.values(),
                    tools / 'usr/bin/qemu-system-aarch64', tools / 'usr/share/AAVMF/AAVMF_CODE.fd',
                    tools / 'usr/share/AAVMF/AAVMF_VARS.fd', ROOT / 'target/release/rustusb',
                    ROOT / 'target/release/rustusb-helper', ROOT / 'apps/rustusb-gui/target/release/rustusb-gui']
    frozen = {str(p): sha(p) for p in frozen_paths}
    save(out / 'provenance.json', {'fixture_head': head, 'product_head': build['git_head'], 'inputs': inputs,
                                 'product_input_names': product_names, 'frozen': frozen, 'family': args.family})
    certs = secure.credentials(private, out)
    ArmVm.tool_root = tools
    releases = ('jammy', 'noble') if args.family == 4 else ('noble', 'jammy')
    links = socket.socketpair()
    vms = {role: ArmVm(out / role, parents[release], role, link, False)
           for role, release, link in zip(['server', 'client'], releases, links)}
    controllers = {}
    result = {'passed': False, 'fixture_head': head, 'product_head': build['git_head'], 'family': args.family,
              'qemu_exits': {}, 'controller_exits': {}, 'cycles': [], 'errors': []}
    catalog = None
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(vm.boot) for vm in vms.values()]
            for future in futures:
                future.result()
        setup = {}
        for role, vm in vms.items():
            print(role + ': actual ARM package installation', flush=True)
            vm.upload([HERE / 'virtual_guest.py', ROOT / 'scripts/qemu_installed_usb/guest.py', args.package,
                       *(certs / n for n in ['ca.der', 'server.der', 'client.der', role + '-key.der'])])
            vm.run('sudo dpkg -i ' + shlex.quote('/var/tmp/rustusb-installed-fixture/' + args.package.name), 'install')
            controller = Controller(vm)
            controllers[role] = controller
            setup[role] = controller.call('setup', role, args.family)
            for name, key in [('/usr/bin/rustusb', 'cli'), ('/usr/libexec/rustusb/rustusb-helper', 'helper')]:
                require(setup[role]['elfs'][name] == build['binaries'][key], 'installed package ELF matches native build')
            if role == 'client':
                helper = setup[role]['helper']['process']
                require(helper['exe_sha256'] == build['binaries']['helper']['sha256'], 'actual running native helper')
        for vm in vms.values():
            qmp = secure.physical.Qmp(vm.private / 'qmp.sock', time.monotonic() + 10, vm.process)
            try:
                qmp.call('object-del', {'id': 'capture'})
                qmp.call('object-add', {'qom-type': 'filter-dump', 'id': 'capture', 'netdev': 'data',
                                      'file': str(vm.out / 'data.pcap')})
            finally:
                qmp.close()
        save(out / 'capture-window.json', {'start': 'after both DAD/route checks; before product server/import start',
                                          'end': 'actual QEMU exit', 'boot_retained': True, 'filtering': False})
        server, client = controllers['server'], controllers['client']
        original = server.call('server-start')['identity']
        require(original['exe_sha256'] == build['binaries']['cli']['sha256'], 'actual server native ELF')
        client.call('plaintext-refused')
        catalog = client.call('catalog')
        channels = []
        for cycle in [1, 2]:
            print('ARM64 virtual actual HID/CDC cycle ' + str(cycle), flush=True)
            held = client.call('attach', cycle)
            verify_held(held, setup['client']['uid'], build['binaries']['cli']['sha256'])
            channels += [held[p]['event']['receipt']['channel_id'] for p in ['hid', 'serial']]
            client.call('contender')
            client.call('open-probe', cycle)
            server.call('inject', cycle)
            io = client.call('io', cycle)
            verify_snapshot(server.call('snapshot'), cycle, True)
            released = client.call('detach', cycle)
            # Product stop has completed before sampling released virtual state.
            verify_snapshot(server.call('snapshot'), cycle, False)
            require(client.call('catalog') == catalog, 'same authenticated catalog after actual release')
            identity = server.call('identity')['server']
            require(all(identity[k] == original[k] for k in ['pid', 'starttime', 'exe', 'exe_sha256', 'uids']),
                    'same original server process throughout both cycles')
            result['cycles'].append({'io': io, 'released': released})
        require(len(set(channels)) == 4, 'four distinct original channel identities')
        stopped = server.call('server-stop')
        finals = [e for e in stopped['events'] if e.get('event') == 'stopped']
        require(len(finals) == 1 and all(e.get('event') != 'error' for e in stopped['events']), 'one successful server stop')
        verify_snapshot(finals[0], 2, False)
        result['data_checks_passed'] = True
    except Exception:
        result['errors'].append(traceback.format_exc())
    finally:
        for role in ['client', 'server']:
            if role in controllers:
                try:
                    result['controller_exits'][role] = controllers[role].close()
                except Exception:
                    result['errors'].append(traceback.format_exc())
        for role, vm in vms.items():
            try:
                if role in controllers:
                    vm.run('sudo tar -C /var/tmp/rustusb-installed-fixture/public -czf - .', 'public-artifacts')
                    archive = vm.out / f'{vm.index:04d}-public-artifacts.stdout'
                    destination = vm.out / 'artifacts'
                    destination.mkdir()
                    with tarfile.open(archive, 'r:gz') as tar:
                        for member in tar.getmembers():
                            if member.isdir() and member.name == '.':
                                continue
                            name = member.name.removeprefix('./')
                            require(member.isfile() and '/' not in name and name not in ('', '.', '..'),
                                    'fixed public artifact regular file')
                            (destination / name).write_bytes(tar.extractfile(member).read())
                    vm.run('sudo journalctl -u rustusb-helper -b --no-pager -o json', 'helper-journal')
                    vm.run('sudo dmesg', 'dmesg')
                result['qemu_exits'][role] = vm.close()
            except Exception:
                result['errors'].append(traceback.format_exc())
                # Cleanup errors cannot bypass the actual VM owner.
                if vm.process is not None and vm.process.poll() is None:
                    result['qemu_exits'][role] = vm.close()
        for link in links:
            link.close()
        result['guards'] = {'head': git('rev-parse', 'HEAD') == head, 'clean': not git('status', '--porcelain'),
                            'inputs': all(sha(ROOT / n) == h for n, h in inputs.items()),
                            'packages_tools_parents_host_elfs': all(sha(p) == h for p, h in frozen.items())}
    if result.get('data_checks_passed') and not result['errors']:
        try:
            device_ids = {(d['device']['bus_number'] << 16) | d['device']['device_number'] for d in catalog}
            reviews = {r: secure.pcap_review(vm.out / 'data.pcap', 'ipv' + str(args.family), device_ids,
                                            b'rustusb-usbip/2') for r, vm in vms.items()}
            save(out / 'packet-review.json', reviews)
            result['packet_checks_passed'] = True
        except Exception:
            result['errors'].append(traceback.format_exc())
    result['passed'] = (result.get('data_checks_passed', False) and result.get('packet_checks_passed', False)
                        and not result['errors'] and all(result['guards'].values())
                        and result['qemu_exits'] == {'server': 0, 'client': 0}
                        and result['controller_exits'] == {'client': 0, 'server': 0})
    save(out / 'summary.json', result)
    print(json.dumps({'passed': result['passed'], 'errors': result['errors'],
                      'qemu_exits': result['qemu_exits'], 'guards': result['guards']}), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--tool-root', type=Path, required=True)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--build-receipt', type=Path, required=True)
    parser.add_argument('--family', type=int, choices=[4, 6], required=True)
    raise SystemExit(one(parser.parse_args()))
