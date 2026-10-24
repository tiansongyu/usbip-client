#!/usr/bin/env python3
"""Installed ordinary-user TUI PTYs plus every unchanged Native managed proof."""
import gzip
import importlib.util
from pathlib import Path
import subprocess
import re
import sys
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('tui_agent_base',ROOT/'scripts/qemu/run-agent-qemu.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
old_inputs,old_guest,old_payload,old_verify=base.inputs,base.guest_source,base.payload,base.verify

def inputs():
    result=old_inputs()
    for path in sorted(HERE.glob('*')):
        if path.is_file() and path.suffix in ['.c','.py','.md']:
            result[str(path.relative_to(ROOT))]={'bytes':path.stat().st_size,'sha256':base.digest(path)}
    return dict(sorted(result.items()))

def replace(text,old,new):
    if text.count(old)!=1:raise RuntimeError('unique TUI composition anchor missing: '+repr(old))
    return text.replace(old,new,1)

def guest():
    text=old_guest()
    functions='''
tui_cases() {
    for tui_mode in quit ctrlc int term; do
        /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/tui-pty "$tui_mode" "/tmp/tui-$tui_mode"
    done
    agent jobs "$1" > /tmp/tui-jobs-after.json
    sessions "$1" /tmp/tui-sessions-after.json
    jq -e '.response.jobs==[]' /tmp/tui-jobs-after.json > /dev/null
    jq -e '.response.sessions==[]' /tmp/tui-sessions-after.json > /dev/null
    echo "TUI_PTYS_PASS role=$ROLE waits=4"
}
'''
    text=replace(text,'[ "${AGENT_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2\n','[ "${AGENT_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2\n'+functions)
    text=replace(text,'manifest; agent_start 1; denied server','manifest; agent_start 1; denied server\n    tui_cases server')
    text=replace(text,'    for epoch in 1 2; do\n        STAGE="client-epoch-$epoch"','    tui_cases hid\n    for epoch in 1 2; do\n        STAGE="client-epoch-$epoch"')
    text=replace(text,'            native_host_phase held-1 held; agent_snapshot held-1','            native_host_phase held-1 held; agent_snapshot held-1\n            /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/tui-pty term /tmp/tui-held')
    text=replace(text,'        actual_io "$round" yes','        if [ "$epoch" = 1 ]; then /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups /usr/bin/tui-pty term /tmp/tui-held; fi\n        actual_io "$round" yes')
    return text

def payload(work,kernel,certificates,role):
    _,provenance=old_payload(work,kernel,certificates,role)
    root=work/'root'
    binary=work/'tui-pty'
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(HERE/'pty-driver.c'),'-lutil','-o',str(binary)],check=True)
    base.secure.native.copy_binary(binary,'/usr/bin/tui-pty',root)
    provenance['usr/bin/tui-pty']=base.digest(root/'usr/bin/tui-pty')
    names=b'.\0'+b'\0'.join(str(p.relative_to(root)).encode() for p in root.rglob('*'))+b'\0'
    packed=subprocess.run(['cpio','--null','-o','--format=newc','--owner=0:0'],input=names,cwd=root,capture_output=True,check=True)
    archive=work/'tui-initramfs.cpio.gz';archive.write_bytes(gzip.compress(packed.stdout))
    return archive,provenance

def verify(evidence,args):
    facts=old_verify(evidence,args);checks=[]
    for role in ['server','client']:
        artifacts=evidence/f'{role}-artifacts'
        for mode in ['quit','ctrlc','int','term','held']:
            fields=dict(line.split('=',1) for line in (artifacts/f'tui-{mode}.result').read_text().splitlines())
            for name in ['waited','raw_observed','resized','restored','entered','left']:
                if fields[name]!='1':raise RuntimeError(f'TUI {role} {mode} {name}')
            if fields['exit']!='0' or fields['uid']!='1000':raise RuntimeError('actual ordinary-user exit')
            terminal=dict(line.split('=',1) for line in (artifacts/f'tui-{mode}.termios').read_text().splitlines())
            if any(terminal['before_'+key]!=terminal['after_'+key] for key in ['flags','speeds','cc']):raise RuntimeError('actual saved termios mismatch')
            status=(artifacts/f'tui-{mode}.status').read_text()
            if not re.search(r'^Uid:\s+1000\s+1000\s+1000\s+1000$',status,re.M):raise RuntimeError('actual child UID')
            for cap in ['CapInh','CapPrm','CapEff','CapAmb']:
                if not re.search(rf'^{cap}:\s+0000000000000000$',status,re.M):raise RuntimeError('actual child capabilities')
            actual=(artifacts/f'tui-{mode}.exe-sha256').read_text().split()[0]
            expected=base.digest(ROOT/'target/release/rustusb')
            if actual!=expected:raise RuntimeError('actual executed TUI ELF differs')
            raw=(artifacts/f'tui-{mode}.raw').read_bytes()
            if mode=='held' and b'Connected' not in raw:raise RuntimeError('actual connected session display absent')
            if b'RustUSB' not in raw:raise RuntimeError('actual product display missing')
            checks.append(f'{role}/{mode}: actual UID/ELF/wait0/raw/resize/exact-termios/alternate-screen')
    facts['tui']={'checks':checks,'pty_children':10,'mutations':'default-cancel only; original CLI retains all USB start/stop operations','scope':'product PTY metadata/navigation/signals, not installed confirmed-action coverage'}
    return facts

def main():
    base.inputs=inputs;base.guest_source=guest;base.payload=payload;base.verify=verify
    return base.main()
if __name__=='__main__':sys.exit(main())
