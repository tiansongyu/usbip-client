#!/usr/bin/env python3
"""Build native preview Debian packages from previously verified release ELFs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def run(args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy(source, root, target, mode=0o644):
    dest = root / target
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    dest.chmod(mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--version', required=True)
    parser.add_argument('--cli', type=Path, default=ROOT/'target/release/rustusb')
    parser.add_argument('--helper', type=Path, default=ROOT/'target/release/rustusb-helper')
    parser.add_argument('--gui', type=Path, help='Optional separately verified native GUI ELF')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9][A-Za-z0-9.+~:-]{0,100}', args.version):
        parser.error('invalid Debian package version')
    subprocess.run(['dpkg', '--validate-version', args.version], check=True)
    arch = run(['dpkg', '--print-architecture'])
    if arch not in ('amd64', 'arm64'):
        parser.error('only native amd64/arm64 packages are supported')
    binaries = {'cli': args.cli.resolve(), 'helper': args.helper.resolve()}
    if args.gui:
        binaries['gui'] = args.gui.resolve()
    expected = {'amd64': 'Advanced Micro Devices X86-64', 'arm64': 'AArch64'}[arch]
    for name, path in binaries.items():
        header = run(['readelf', '-h', str(path)])
        if not any(line.strip() == 'Machine:                           '+expected for line in header.splitlines()):
            raise ValueError(f'{name}: ELF architecture mismatch')
    # Catch a default CLI lacking the actual application-service/TUI features.
    cli_help = run([str(binaries['cli']), '--help'])
    if not all(word in cli_help for word in ('agent', 'tui')):
        raise ValueError('base package requires secure-transport and TUI CLI features')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    provenance = {'schema_version': 1, 'git_head': run(['git', 'rev-parse', 'HEAD'], cwd=ROOT),
                  'git_status': run(['git', 'status', '--porcelain'], cwd=ROOT),
                  'architecture': arch, 'version': args.version, 'binaries': {}, 'packages': []}
    for name, path in binaries.items():
        provenance['binaries'][name] = {'sha256': digest(path), 'bytes': path.stat().st_size}
    env = dict(os.environ, SOURCE_DATE_EPOCH=run(['git', 'show', '-s', '--format=%ct', 'HEAD'], cwd=ROOT))
    for name, inputs in [('rustusb', ('cli', 'helper'))] + ([('rustusb-gui', ('gui',))] if args.gui else []):
        with tempfile.TemporaryDirectory(prefix='rustusb-deb-') as temp:
            work = Path(temp)
            stage = work/'stage'
            control = stage/'DEBIAN'
            control.mkdir(parents=True)
            (work/'debian').mkdir()
            (work/'debian/control').write_text('Source: rustusb\nMaintainer: RustUSB contributors <noreply@github.com>\n\nPackage: '+name+'\nArchitecture: any\nDescription: RustUSB preview\n')
            if name == 'rustusb':
                copy(binaries['cli'], stage, 'usr/bin/rustusb', 0o755)
                copy(binaries['helper'], stage, 'usr/libexec/rustusb/rustusb-helper', 0o755)
                copy(ROOT/'packaging/debian/package-control', stage, 'usr/libexec/rustusb/package-control', 0o755)
                for unit in ('rustusb-helper.service', 'rustusb-agent.service'):
                    copy(ROOT/'packaging/systemd'/unit, stage, 'lib/systemd/system/'+unit)
                for script in ('preinst', 'postinst', 'prerm', 'postrm'):
                    copy(ROOT/'packaging/debian'/script, stage, 'DEBIAN/'+script, 0o755)
                copy(ROOT/'examples/policy.json', stage, 'usr/share/doc/rustusb/examples/policy.json')
                copy(ROOT/'docs/debian-installation.md', stage, 'usr/share/doc/rustusb/installation.md')
                extra = 'python3 (>= 3.10), adduser, init-system-helpers (>= 1.62), systemd (>= 249), linux-tools-common'
            else:
                copy(binaries['gui'], stage, 'usr/bin/rustusb-gui', 0o755)
                copy(ROOT/'packaging/debian/rustusb-gui.desktop', stage, 'usr/share/applications/rustusb-gui.desktop')
                copy(ROOT/'apps/rustusb-gui/icons/icon.png', stage, 'usr/share/icons/hicolor/64x64/apps/rustusb.png')
                extra = 'rustusb (= '+args.version+')'
            copy(ROOT/'LICENSE', stage, 'usr/share/doc/'+name+'/copyright')
            shlibs = run(['dpkg-shlibdeps', '-O'] + ['-e'+str(binaries[key]) for key in inputs], cwd=work)
            if not shlibs.startswith('shlibs:Depends=') or '\n' in shlibs:
                raise ValueError('unexpected dpkg-shlibdeps output')
            dependencies = extra + ', ' + shlibs.split('=', 1)[1]
            installed_size = sum(p.stat().st_size for p in stage.rglob('*') if p.is_file()) // 1024 + 1
            (control/'control').write_text(f'Package: {name}\nVersion: {args.version}\nSection: utils\nPriority: optional\nArchitecture: {arch}\nMaintainer: RustUSB contributors <noreply@github.com>\nDepends: {dependencies}\nInstalled-Size: {installed_size}\nHomepage: https://github.com/tiansongyu/usbip-client\nDescription: experimental administrator-authorized USB/IP management\n Linux USB/IP management with a restricted privileged helper.\n Services are disabled by default. Administrator provisioning is required.\n')
            package = out/f'{name}_{args.version}_{arch}.deb'
            if package.exists():
                raise FileExistsError(package)
            subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(stage), str(package)], check=True, env=env)
            provenance['packages'].append({'file': package.name, 'sha256': digest(package),
                                           'bytes': package.stat().st_size, 'depends': dependencies})
    receipt = out/('build-'+args.version+'.json')
    if receipt.exists():
        raise FileExistsError(receipt)
    receipt.write_text(json.dumps(provenance, indent=2)+'\n')
    print(json.dumps(provenance, indent=2))


if __name__ == '__main__':
    main()
