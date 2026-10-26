#!/usr/bin/env python3
"""Disposable guest-root test controller; product children use the broker UID."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import select
import signal
import socket
import struct
import subprocess
import sys
import termios
import time
import traceback

import guest as common

HERE = common.HERE
PUBLIC = HERE / 'public'
CONFIG = common.CONFIG
KEYS = ['virtual:hid', 'virtual:cdc-acm']


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def save(name, value):
    (PUBLIC / name).write_text(json.dumps(value, indent=2) + '\n')
    return value


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def process_identity(pid):
    p = Path('/proc') / str(pid)
    data = {'pid': pid, 'status': (p / 'status').read_text(),
            'stat': (p / 'stat').read_text(), 'exe': str((p / 'exe').resolve()),
            'exe_sha256': sha(p / 'exe')}
    data['starttime'] = int(data['stat'].rsplit(') ', 1)[1].split()[19])
    data['uids'] = [int(x) for x in next(x for x in data['status'].splitlines()
                                       if x.startswith('Uid:')).split()[1:]]
    return data


class Controller:
    def __init__(self):
        PUBLIC.mkdir(mode=0o700)
        self.children = {}
        self.probe = None
        self.count = 0
        self.uid = pwd.getpwnam('rustusb').pw_uid
        self.gid = pwd.getpwnam('rustusb').pw_gid
        self.prefix = ['setpriv', '--reuid=' + str(self.uid),
                       '--regid=' + str(self.gid), '--clear-groups', '/usr/bin/rustusb']

    def setup(self, role, family):
        self.role = role
        mac = '52:54:00:59:00:' + ('21' if role == 'server' else '22')
        interface = next(p.name for p in Path('/sys/class/net').iterdir()
                         if (p / 'address').read_text().strip() == mac)
        common.run(['ip', 'link', 'set', interface, 'up'])
        suffix = '1' if role == 'server' else '2'
        for address in ['192.0.2.' + suffix + '/24', 'fd42:7275:7374::' + suffix + '/64']:
            common.run(['ip', 'addr', 'add', address, 'dev', interface])
        own6 = 'fd42:7275:7374::' + suffix
        for _ in range(200):
            addresses = json.loads(common.run(['ip', '-j', 'addr', 'show', 'dev', interface]))
            usable = [a for row in addresses for a in row['addr_info']
                      if a['local'] == own6 and not a.get('tentative') and not a.get('dadfailed')
                      and not {'tentative', 'dadfailed'}.intersection(a.get('flags', []))]
            if usable and (Path('/sys/class/net') / interface / 'carrier').read_text().strip() == '1':
                break
            time.sleep(.05)
        else:
            raise RuntimeError('IPv6 DAD/carrier not ready')
        peer6 = 'fd42:7275:7374::' + ('2' if role == 'server' else '1')
        route = json.loads(common.run(['ip', '-j', '-6', 'route', 'get', peer6]))
        require(len(route) == 1 and route[0]['dev'] == interface and route[0]['prefsrc'] == own6,
                'exact IPv6 source route')
        for name in ['ca.der', 'server.der', 'client.der', role + '-key.der']:
            target = CONFIG / name
            target.write_bytes((HERE / name).read_bytes())
            target.chmod(0o600 if 'key' in name else 0o644)
            os.chown(target, self.uid if 'key' in name else 0, self.gid if 'key' in name else 0)
        require(not (HERE / ('client-key.der' if role == 'server' else 'server-key.der')).exists()
                and not (HERE / 'ca-key.der').exists(), 'role-specific private credentials')
        link = {'protocol': 'framed_v2', 'interval_millis': 1000, 'response_timeout_millis': 3000}
        base = {'schema_version': 2, 'data_link': link, 'broker_uid': self.uid,
                'ca_certificates': ['/etc/rustusb/ca.der'],
                'certificate_chain': ['/etc/rustusb/' + role + '.der'],
                'private_key': '/etc/rustusb/' + role + '-key.der'}
        if role == 'server':
            config = dict(base, listen='192.0.2.1:3241' if family == 4 else '[fd42:7275:7374::1]:3241',
                          devices=KEYS, principals=[{'id': 'qemu-client',
                          'pins': [sha(CONFIG / 'client.der')], 'devices': KEYS}])
            common.write(CONFIG / 'server.json', config)
        else:
            for module in ['vhci_hcd', 'usbhid', 'cdc_acm']:
                common.run(['modprobe', module])
            common.write(CONFIG / 'policy.json', {'allowed_uids': [], 'export_device_ids': [],
                         'peers': [], 'trusted_network': False, 'secure_broker_uid': self.uid,
                         'secure_imports': [{'server_id': 'qemu-server', 'device_keys': KEYS}]})
            for profile, key in zip(['hid', 'serial'], KEYS):
                config = dict(base, server_id='qemu-server', server_name='rustusb-qemu.test',
                              endpoint={'host': '192.0.2.1' if family == 4 else 'fd42:7275:7374::1',
                                        'port': 3241}, device=key)
                common.write(CONFIG / ('client-' + profile + '.json'), config)
            common.run(['systemctl', 'start', 'rustusb-helper.service'])
            for _ in range(200):
                if Path('/run/rustusb/control.sock').is_socket():
                    break
                time.sleep(.05)
            else:
                raise RuntimeError('actual helper socket not ready')
        elfs = {}
        for name in ['/usr/bin/rustusb', '/usr/libexec/rustusb/rustusb-helper']:
            data = Path(name).read_bytes()
            require(data[:6] == b'\x7fELF\x02\x01' and int.from_bytes(data[18:20], 'little') == 183,
                    'installed actual AArch64 ELF')
            elfs[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        return {'role': role, 'family': family, 'uid': self.uid, 'gid': self.gid,
                'addresses': addresses, 'route': route, 'elfs': elfs,
                'identity': common.run(['uname', '-a']) + common.run(['dpkg', '--print-architecture'])
                            + common.run(['dpkg-query', '-W', 'rustusb', 'systemd', 'dpkg']),
                'public_config': {p.name: common.read(p) for p in CONFIG.glob('*.json')},
                'config_metadata': {p.name: common.stat_info(p) for p in CONFIG.glob('*') if p.is_file()},
                'helper': common.unit_status()['rustusb-helper.service'] if role == 'client' else None}

    def events(self, name):
        # A still-running producer may have an incomplete final line.
        data = (PUBLIC / (name + '.events')).read_bytes()
        return [json.loads(line) for line in data.splitlines(keepends=True) if line.endswith(b'\n')]

    def await_event(self, name, predicate):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            matches = [e for e in self.events(name) if predicate(e)]
            if matches:
                require(len(matches) == 1, 'unique product event')
                return matches[0]
            require(self.children[name]['process'].poll() is None, 'product child exited before event: ' + name)
            time.sleep(.05)
        raise RuntimeError('product event observation limit: ' + name)

    def spawn(self, name, argv, control=False):
        require(name not in self.children, 'child identity never reused')
        stdout = (PUBLIC / (name + '.events')).open('wb')
        stderr = (PUBLIC / (name + '.stderr')).open('wb')
        p = subprocess.Popen(self.prefix + argv, stdin=subprocess.PIPE if control else subprocess.DEVNULL,
                             stdout=stdout, stderr=stderr)
        self.children[name] = {'process': p, 'files': [stdout, stderr]}
        save(name + '-command.json', self.prefix + argv)
        return p

    def start_server(self):
        p = self.spawn('server', ['secure-server', '--backend', 'virtual', '--config',
                                '/etc/rustusb/server.json', '--control-stdin'], True)
        event = self.await_event('server', lambda e: e.get('event') == 'ready')
        require(event['transport'] == 'mutual_tls', 'mutual TLS server ready')
        identity = process_identity(p.pid)
        require(identity['uids'] == [self.uid] * 4, 'unprivileged original server')
        return {'event': event, 'identity': identity}

    def control(self, op, **values):
        self.count += 1
        identity = 'fixture-' + str(self.count)
        command = dict(values, id=identity, op=op)
        save(identity + '-control.json', command)
        p = self.children['server']['process']
        p.stdin.write((json.dumps(command) + '\n').encode())
        p.stdin.flush()
        event = self.await_event('server', lambda e: e.get('id') == identity)
        require(event.get('event') == 'snapshot' or (event.get('event') == 'ack' and event.get('ok') is True),
                'successful exact control acknowledgement')
        return event

    def catalog(self):
        result = json.loads(common.run(self.prefix + ['--json', 'secure-discover', '--config',
                                                      '/etc/rustusb/client-hid.json']))
        require(len(result) == 2 and {d['device_key'] for d in result} == set(KEYS), 'exact approved catalog')
        return result

    def snapshot(self):
        return {'journal': common.read('/var/lib/rustusb/native/channels.json'), 'vhci': common.vhci()}

    def attach(self, cycle):
        result = {}
        for profile in ['hid', 'serial']:
            name = f'{profile}-{cycle}'
            p = self.spawn(name, ['secure-attach', '--config', '/etc/rustusb/client-' + profile + '.json'])
            event = self.await_event(name, lambda e: e.get('event') == 'connected')
            identity = process_identity(p.pid)
            require(identity['uids'] == [self.uid] * 4, 'unprivileged original import')
            require(event['transport'] == 'mtls' and event['receipt']['kernel']['session']['status'] == 6,
                    'actual USED import receipt')
            result[profile] = {'event': event, 'identity': identity}
        result['kernel'] = self.snapshot()
        require(len(result['kernel']['journal']['records']) == 2, 'two actual helper records')
        return result

    def contender(self):
        before = self.snapshot()
        p = subprocess.run(self.prefix + ['secure-attach', '--config', '/etc/rustusb/client-hid.json'],
                           capture_output=True, text=True, timeout=15)
        result = {'exit': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr,
                  'before': before, 'after': self.snapshot()}
        save('contender-' + str(time.monotonic_ns()) + '.json', result)
        require(p.returncode == 1 and 'configured remote device is unavailable or unauthorized' in p.stderr,
                'strict competing import refusal')
        require(before == result['after'], 'contender left original kernel and owner records unchanged')
        return result

    def open_probe(self, cycle):
        require(self.probe is None, 'no prior fixture device FD')
        locals_ = {}
        for profile, product in [('hid', '0001'), ('serial', '0002')]:
            event = next(e for e in self.events(f'{profile}-{cycle}') if e.get('event') == 'connected')
            receipt = event['receipt']['kernel']['session']
            p = Path('/sys/bus/usb/devices') / receipt['local_bus_id']
            generation = common.stat_info(p)
            require(generation['device'] == receipt['local_device_generation']['filesystem_device']
                    and generation['inode'] == receipt['local_device_generation']['inode'], 'exact imported generation')
            require((p / 'idVendor').read_text().strip() == '1234'
                    and (p / 'idProduct').read_text().strip() == product, 'virtual VID/PID')
            interfaces = sorted(p.glob(p.name + ':*'))
            expected_count = 1 if profile == 'hid' else 2
            require(len(interfaces) == expected_count and int((p / 'bNumInterfaces').read_text()) == expected_count,
                    'actual composite interface count')
            drivers = [str((i / 'driver').resolve().name) for i in interfaces]
            require(drivers == (['usbhid'] if profile == 'hid' else ['cdc_acm', 'cdc_acm']), 'actual native drivers')
            pattern = 'event*' if profile == 'hid' else 'ttyACM*'
            for _ in range(150):
                nodes = sorted({n.name for n in p.resolve().rglob(pattern)})
                if nodes:
                    break
                time.sleep(.05)
            require(len(nodes) == 1, 'unique actual event/ACM node')
            node = Path('/dev/input' if profile == 'hid' else '/dev') / nodes[0]
            locals_[profile] = {'path': str(p), 'generation': generation, 'interfaces': [i.name for i in interfaces],
                                'drivers': drivers, 'node': str(node), 'node_metadata': common.stat_info(node),
                                'power': {n: (p / 'power' / n).read_text().strip()
                                          for n in ['control', 'autosuspend_delay_ms', 'runtime_status']}}
            require(locals_[profile]['power']['autosuspend_delay_ms'] == '-1'
                    and locals_[profile]['power']['runtime_status'] == 'active', 'import runtime power receipt')
        serial = os.open(locals_['serial']['node'], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.probe = {'serial': serial, 'event': None, 'cycle': cycle, 'locals': locals_}
        attrs = termios.tcgetattr(serial)
        attrs[:4] = [0, 0, termios.CS8 | termios.CLOCAL | termios.CREAD, 0]
        attrs[4:6] = [termios.B57600, termios.B57600]
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(serial, termios.TCSANOW, attrs)
        self.probe['event'] = os.open(locals_['hid']['node'], os.O_RDONLY | os.O_NONBLOCK)
        return locals_

    def io(self, cycle):
        require(self.probe is not None and self.probe['cycle'] == cycle, 'probe belongs to this cycle')
        serial, event = self.probe['serial'], self.probe['event']
        rx, raw, echo = b'', b'', b''
        tx = f'RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n'.encode()
        deadline = time.monotonic() + 20
        try:
            while time.monotonic() < deadline:
                readable, _, _ = select.select([serial, event], [], [], .2)
                if serial in readable:
                    rx += os.read(serial, 4096)
                if event in readable:
                    raw += os.read(event, 4096)
                require(len(raw) % 24 == 0, 'complete AArch64 input_event records')
                transitions = [list(struct.unpack('<qqHHi', raw[i:i+24])[2:]) for i in range(0, len(raw), 24)]
                if len(rx) >= 35 and [1, cycle + 1, 1] in transitions and [1, cycle + 1, 0] in transitions:
                    break
            require(rx == f'RUSTUSB-RX-{cycle}-0001:0123456789abcdef\n'.encode(), 'exact injected CDC bytes')
            require([1, cycle + 1, 1] in transitions and [1, cycle + 1, 0] in transitions, 'exact HID down and up')
            require(os.write(serial, tx) == 35, 'complete client CDC write')
            deadline = time.monotonic() + 15
            while len(echo) < 35 and time.monotonic() < deadline:
                if select.select([serial], [], [], .2)[0]:
                    echo += os.read(serial, 4096)
            require(echo == tx, 'exact virtual CDC echo')
            return {'cycle': cycle, 'rx_hex': rx.hex(), 'tx_hex': tx.hex(), 'echo_hex': echo.hex(),
                    'transitions': transitions, 'locals': self.probe['locals']}
        finally:
            for label, data in [('rx', rx), ('tx', tx), ('echo', echo), ('input', raw)]:
                (PUBLIC / f'{label}-{cycle}.bin').write_bytes(data)
            self.close_probe()

    def close_probe(self):
        if self.probe is not None:
            for name in ['event', 'serial']:
                if self.probe[name] is not None:
                    os.close(self.probe[name])
            self.probe = None

    def stop(self, name):
        child = self.children[name]
        p = child['process']
        if p.poll() is None:
            p.send_signal(signal.SIGTERM)
        # A timeout is a failed observation, never a successful or cancelled owner.
        timeout = False
        try:
            code = p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            timeout = True
            save(name + '-wait-timeout.json', {'pid': p.pid, 'still_owned': True})
            code = p.wait()
        if p.stdin is not None:
            p.stdin.close()
        for handle in child['files']:
            handle.close()
        result = save(name + '-wait.json', {'pid': p.pid, 'exit': code, 'observation_timeout': timeout,
                                           'events': self.events(name)})
        require(code == 0 and not timeout, 'actual original child successful wait')
        return result

    def detach(self, cycle):
        require(self.probe is None, 'test node FDs released before product cleanup')
        buses = [next(e for e in self.events(f'{p}-{cycle}') if e.get('event') == 'connected')
                 ['receipt']['kernel']['session']['local_bus_id'] for p in ['hid', 'serial']]
        waits = [self.stop(f'{p}-{cycle}') for p in ['hid', 'serial']]
        for value in waits:
            require(any(e.get('event') == 'disconnected' and e.get('cleanup_verified') is True
                        for e in value['events']), 'original cleanup event')
        snap = self.snapshot()
        require(snap['journal']['records'] == [] and snap['journal']['exports'] == [], 'no remaining import capabilities')
        rows = [s.split() for f in snap['vhci'] for s in f['text'].splitlines() if s.startswith(('hs ', 'ss '))]
        require(rows and all(int(r[2]) == 4 for r in rows), 'all VHCI ports NULL')
        gone = {bus: not (Path('/sys/bus/usb/devices') / bus).exists() for bus in buses}
        require(all(gone.values()), 'both original local generations absent')
        return {'waits': waits, 'snapshot': snap, 'gone': gone}

    def dispatch(self, action, args):
        if action == 'setup':
            return self.setup(*args)
        if action == 'server-start':
            return self.start_server()
        if action == 'snapshot':
            return self.control('snapshot')
        if action == 'inject':
            cycle = args[0]
            return [self.control('hid_key', key=29 + cycle), self.control('cdc_emit',
                    hex=f'RUSTUSB-RX-{cycle}-0001:0123456789abcdef\n'.encode().hex())]
        if action == 'catalog':
            return self.catalog()
        if action == 'attach':
            return self.attach(*args)
        if action == 'contender':
            return self.contender()
        if action == 'open-probe':
            return self.open_probe(*args)
        if action == 'io':
            return self.io(*args)
        if action == 'detach':
            return self.detach(*args)
        if action == 'server-stop':
            return self.stop('server')
        if action == 'plaintext-refused':
            result = []
            for family, address in [(socket.AF_INET, '192.0.2.1'), (socket.AF_INET6, 'fd42:7275:7374::1')]:
                with socket.socket(family, socket.SOCK_STREAM) as s:
                    s.settimeout(3)
                    code = s.connect_ex((address, 3240))
                    require(code == 111, 'plaintext connection refused')
                    result.append({'family': int(family), 'address': address, 'errno': code})
            return result
        if action == 'identity':
            return {n: process_identity(c['process'].pid) for n, c in self.children.items()
                    if c['process'].poll() is None}
        raise RuntimeError('unknown fixed fixture action')

    def cleanup(self):
        self.close_probe()
        result = {}
        for name, child in self.children.items():
            if not all(f.closed for f in child['files']):
                try:
                    result[name] = self.stop(name)
                except Exception:
                    result[name] = {'error': traceback.format_exc()}
        if getattr(self, 'role', None) == 'client':
            common.run(['systemctl', 'stop', 'rustusb-helper.service'])
            result['helper_final'] = common.unit_status()['rustusb-helper.service']
            require(result['helper_final']['MainPID'] == '0'
                    and result['helper_final']['ActiveState'] == 'inactive', 'actual helper stopped')
        return save('controller-cleanup.json', result)


def main():
    controller = Controller()
    try:
        for index, line in enumerate(sys.stdin, 1):
            request = json.loads(line)
            save(f'{index:03d}-request.json', request)
            try:
                result = {'ok': True, 'data': controller.dispatch(request['action'], request.get('args', []))}
            except Exception:
                result = {'ok': False, 'error': traceback.format_exc()}
            save(f'{index:03d}-response.json', result)
            print(json.dumps(result), flush=True)
    finally:
        controller.cleanup()


if __name__ == '__main__':
    main()
