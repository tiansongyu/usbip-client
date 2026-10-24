#!/usr/bin/env python3
"""Real UID1000 terminal Start uncertainty and retained-job outage in Native QEMU."""
import gzip
import json
from pathlib import Path
import re
import subprocess
import sys
import importlib.util
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('tui_failures_actions',ROOT/'scripts/qemu_tui_actions/run.py')
actions=importlib.util.module_from_spec(spec);spec.loader.exec_module(actions)
base=actions.base

def inputs():
    result=actions.inputs()
    for p in HERE.iterdir():
        if p.is_file() and p.suffix in ['.c','.sh','.py','.md']:
            result[str(p.relative_to(ROOT))]={'bytes':p.stat().st_size,'sha256':base.digest(p)}
    return dict(sorted(result.items()))

def guest():
    text=actions.guest()
    anchor='[ "${AGENT_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2\n'
    text=actions.replace(text,anchor,anchor+(HERE/'guest-functions.sh').read_text()+'\n')
    return actions.replace(text,'    tui_run "start-$TUI_PROFILE" "$TUI_LABEL"',
        '''    if [ "$AGENT_EPOCH" = 1 ] && [ "$TUI_PROFILE" != serial ]; then
        tui_unknown_start "$TUI_PROFILE" "$TUI_LABEL"
    else
        tui_run "start-$TUI_PROFILE" "$TUI_LABEL"
    fi''')

def payload(work,kernel,certificates,role):
    _,provenance=actions.payload(work,kernel,certificates,role)
    root=work/'root';binary=work/'tui-failure-pty'
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(HERE/'pty-driver.c'),'-lutil','-o',str(binary)],check=True)
    base.secure.native.copy_binary(binary,'/usr/bin/tui-failure-pty',root)
    provenance['usr/bin/tui-failure-pty']=base.digest(root/'usr/bin/tui-failure-pty')
    names=b'.\0'+b'\0'.join(str(p.relative_to(root)).encode()for p in root.rglob('*'))+b'\0'
    packed=subprocess.run(['cpio','--null','-o','--format=newc','--owner=0:0'],input=names,cwd=root,capture_output=True,check=True)
    archive=work/'tui-failure-initramfs.cpio.gz';archive.write_bytes(gzip.compress(packed.stdout));return archive,provenance

def verify(evidence,args):
    selected={('server','start-server-1'),('client','start-hid-1')}
    facts=actions.verify(evidence,args,unknown_starts=selected)
    checks=[]
    for role,label in sorted(selected):
        folder=evidence/f'{role}-artifacts';prefix=f'tui-{label}'
        read=lambda name:json.loads((folder/(prefix+name)).read_text())
        before=read('-before-issued.json');timeout=read('-after-timeout.json');admitted=read('-after-admit.json')
        if before!=timeout:raise RuntimeError('store changed while original agent stopped')
        old={r['id']for r in before['requests']};new=[r for r in admitted['requests']if r['id']not in old]
        if len(new)!=1 or len(admitted['jobs'])!=len(before['jobs'])+1:raise RuntimeError('queued actual Start not exactly once')
        if read('-before-read.json')!=read('-after-read-timeout.json'):raise RuntimeError('store changed while read agent stopped')
        final=read('-after-retry.json')
        if final['requests']!=admitted['requests'] or {j['id']for j in final['jobs']}!={j['id']for j in admitted['jobs']}:raise RuntimeError('UI original retry duplicated request/job')
        identities=[]
        for mark in ['before','paused','timeout','resumed','read-paused','read-timeout','read-resumed']:
            status=(folder/f'{prefix}-agent-{mark}.status').read_text()
            if not re.search(r'^Uid:\s+2000\s+2000\s+2000\s+2000$',status,re.M):raise RuntimeError('paused actual broker UID')
            if mark in ['paused','timeout','read-paused','read-timeout'] and not re.search(r'^State:\s+T',status,re.M):raise RuntimeError('actual broker not stopped')
            stat=(folder/f'{prefix}-agent-{mark}.stat').read_text();tail=stat[stat.rfind(')')+2:].split()
            identities.append((stat.split(' ',1)[0],tail[19]))
        if len(set(identities))!=1:raise RuntimeError('test replaced original broker process')
        def ms(suffix):
            value=float((folder/f'{prefix}.{suffix}').read_text().split()[0])
            return value*1000 if suffix in ['retry','read-resumed'] else value
        if not(ms('ready')<ms('issued')<ms('timeout')<ms('retry')<ms('retry-issued')<ms('read-ready')<ms('read-issued')<ms('read-timeout')<ms('read-resumed')<ms('quit')):raise RuntimeError('actual PTY phase ordering')
        if ms('timeout')-ms('issued')<6500 or ms('read-timeout')-ms('read-issued')<6500:raise RuntimeError('deadline observation too short')
        rendered=actions.decoder.frames((folder/f'{prefix}.raw').read_bytes());job=new[0]['job_id']
        # All assertions use complete decoded real frames, never ANSI byte substrings.
        current=[i for i,f in enumerate(rendered)if job in f and 'Current page' in f]
        stale=[i for i,f in enumerate(rendered)if job in f and 'Stale: agent unavailable' in f]
        if not any(a<b<c for a in current for b in stale for c in current):raise RuntimeError('retained exact job did not visibly transition current/stale/current')
        checks.append(f'{role}: same real broker stopped twice, actual timeout/original UI retry, one durable job/request, retained exact job current/stale/current')
    facts['tui_failures']={'checks':checks,'unknown_start_ptys':2,'scope':'SIGSTOP before Start admission and before metadata refresh, not wire-loss or permanent OS-blocking evidence'}
    return facts

def main():
    base.inputs=inputs;base.guest_source=guest;base.payload=payload;base.verify=verify
    return base.main()
if __name__=='__main__':sys.exit(main())
