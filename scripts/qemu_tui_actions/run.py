#!/usr/bin/env python3
"""Actual UID1000 TUI Start/session-stop/job-stop/settings with original USB proofs."""
import gzip
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
base=module('tui_actions_agent_base',ROOT/'scripts/qemu/run-agent-qemu.py')
old_inputs,old_guest,old_payload,old_verify=base.inputs,base.guest_source,base.payload,base.verify
decoder=module('tui_actions_decoder',ROOT/'scripts/qemu_tui/decode.py')

def inputs():
    result=old_inputs()
    paths=[p for p in HERE.iterdir()if p.is_file()and p.suffix in ['.c','.sh','.py','.md']]
    paths +=[ROOT/'scripts/qemu_tui'/name for name in ['decode.py','requirements.txt']]
    for p in paths:result[str(p.relative_to(ROOT))]={'bytes':p.stat().st_size,'sha256':base.digest(p)}
    return dict(sorted(result.items()))
def replace(text,old,new):
    if text.count(old)!=1:raise RuntimeError('unique confirmed-action anchor absent: '+repr(old))
    return text.replace(old,new,1)
def guest():
    text=old_guest()
    anchor='[ "${AGENT_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2\n'
    text=replace(text,anchor,anchor+(HERE/'guest-functions.sh').read_text()+'\n')
    start='''    agent start "$1" > "/tmp/start-$1-$AGENT_EPOCH.json" 2> "/tmp/start-$1-$AGENT_EPOCH.log"
    jq -e '.schema_version==1 and .journal_healthy==true and .response.result=="started" and .response.replayed==false and .response.job.operator_uid==1000' "/tmp/start-$1-$AGENT_EPOCH.json" > /dev/null
    # The requesting CLI has actually returned before any I/O below.
    echo "AGENT_START_RETURNED role=$ROLE profile=$1 epoch=$AGENT_EPOCH wait=0"'''
    text=replace(text,start,'    tui_start_job "$1"')
    text=replace(text,'manifest; agent_start 1; denied server','manifest; agent_start 1; denied server\n    tui_preferences')
    text=replace(text,'manifest; agent_start 1; denied hid','manifest; agent_start 1; denied hid\n    tui_preferences')
    stop='''            agent stop-session "$STOP_ID" > /tmp/stop-hid.json
            jq -e '.response=={result:"stop_accepted",owner_present:true}' /tmp/stop-hid.json > /dev/null'''
    text=replace(text,stop,'            tui_run stop-session stop-session-hid "$STOP_ID"')
    text=replace(text,'        kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" "agent-$epoch"','        tui_stop_job server\n        kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" "agent-$epoch"')
    text=replace(text,'            kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-2','            tui_stop_job hid\n            tui_stop_job serial\n            kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-2')
    return text

def payload(work,kernel,certificates,role):
    _,provenance=old_payload(work,kernel,certificates,role)
    root=work/'root';binary=work/'tui-action-pty'
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(HERE/'pty-driver.c'),'-lutil','-o',str(binary)],check=True)
    base.secure.native.copy_binary(binary,'/usr/bin/tui-action-pty',root)
    provenance['usr/bin/tui-action-pty']=base.digest(root/'usr/bin/tui-action-pty')
    names=b'.\0'+b'\0'.join(str(p.relative_to(root)).encode()for p in root.rglob('*'))+b'\0'
    packed=subprocess.run(['cpio','--null','-o','--format=newc','--owner=0:0'],input=names,cwd=root,capture_output=True,check=True)
    archive=work/'tui-action-initramfs.cpio.gz';archive.write_bytes(gzip.compress(packed.stdout));return archive,provenance

def verify(evidence,args):
    facts=old_verify(evidence,args);checks=[]
    for role in ['server','client']:
        folder=evidence/f'{role}-artifacts'
        read=lambda name:json.loads((folder/name).read_text())
        labels=['settings-save','settings-load','settings-override','settings-error']
        profiles=['server']if role=='server'else['hid','serial']
        labels +=[f'start-{profile}-{epoch}'for epoch in [1,2]for profile in profiles]
        labels +=['stop-session-hid','stop-job-server-1','stop-job-server-2']if role=='server'else['stop-job-hid-2','stop-job-serial-2']
        rendered={}
        for label in labels:
            fields=dict(line.split('=',1)for line in (folder/f'tui-{label}.result').read_text().splitlines())
            if any(fields[key]!='1'for key in ['waited','raw_observed','resized','restored','entered','left'])or fields['exit']!='0'or fields['uid']!='1000':raise RuntimeError(f'actual TUI lifecycle {role}/{label}')
            terminal=dict(line.split('=',1)for line in (folder/f'tui-{label}.termios').read_text().splitlines())
            if any(terminal['before_'+key]!=terminal['after_'+key]for key in ['flags','speeds','cc']):raise RuntimeError('saved termios mismatch')
            status=(folder/f'tui-{label}.status').read_text()
            if not re.search(r'^Uid:\s+1000\s+1000\s+1000\s+1000$',status,re.M):raise RuntimeError('actual UI UID')
            if any(not re.search(rf'^{cap}:\s+0000000000000000$',status,re.M)for cap in ['CapInh','CapPrm','CapEff','CapAmb']):raise RuntimeError('UI capabilities')
            if(folder/f'tui-{label}.exe-sha256').read_text().split()[0]!=base.digest(ROOT/'target/release/rustusb'):raise RuntimeError('actual UI ELF')
            rendered[label]=decoder.frames((folder/f'tui-{label}.raw').read_bytes())
            (evidence/f'tui-{role}-{label}-frames.txt').write_text('\n\n--- FRAME ---\n\n'.join(rendered[label]))
            checks.append(f'{role}/{label}: actual UID1000/ELF/no-caps/wait0/termios/resize')
        saved={'schema_version':1,'options':{'refresh_seconds':3,'auto_refresh':False,'monochrome':True}}
        if read('tui-saved-preferences.json')!=saved or read('tui-final-preferences.json')!=saved:raise RuntimeError('actual preference bytes/state')
        for label,interval,auto,mono in [('settings-load',3,'false','true'),('settings-override',4,'true','false')]:
            if not any(f'Refresh interval: {interval} seconds'in f and f'Auto refresh: {auto}'in f and f'Monochrome: {mono}'in f for f in rendered[label]):raise RuntimeError('reloaded/override options not visible in same actual frame')
        if not any('Save failed:'in f for f in rendered['settings-error']):raise RuntimeError('actual save error not displayed')
        stat=(folder/'tui-preferences-stat.txt').read_text().splitlines()
        if len(stat)!=3 or not stat[0].startswith('1000:700:')or stat[1:]!=['1000:600:1','1000:600:1']:raise RuntimeError('actual preferences permissions')
        if(folder/'tui-preferences-error-stat.txt').read_text().strip()!='1000:644:1':raise RuntimeError('actual failure mode injection')
        if(folder/'tui-operator-key-unreadable.txt').read_text().strip()!='0':raise RuntimeError('operator private-key isolation')
        for profile in profiles:
            for epoch in [1,2]:
                label=f'start-{profile}-{epoch}'
                before=read(f'tui-{label}-before.json');after=read(f'tui-{label}-after.json');retry=read(f'tui-{label}-after-retry.json');receipt=read(f'start-{profile}-{epoch}.json')
                old={r['id']for r in before['requests']};new=[r for r in after['requests']if r['id']not in old]
                if len(new)!=1 or new[0]['uid']!=1000 or new[0]['profile']!=profile or after['requests']!=retry['requests']:raise RuntimeError('one exact actual terminal admission and safe retry')
                job=receipt['response']['job']
                if not receipt['response']['replayed']or receipt['response']['result']!='started'or job['id']!=new[0]['job_id']:raise RuntimeError('real replayed metadata receipt does not identify original UI job')
                if not any('Admitted job'in f for f in rendered[label]):raise RuntimeError('normal original Start ACK not shown in actual UI')
        checks.append(f'{role}: actual preferences save/reload/override/failure and exact UI admission identities')
    facts['tui_actions']={'checks':checks,'pty_children':19,'scope':'Distinct UI-driven managed scenario; original 264 kernel/identity/proof/I/O assertions retained. Actual replayed RetryStart receipts only, no fabricated JSON.'}
    return facts

def main():
    base.inputs=inputs;base.guest_source=guest;base.payload=payload;base.verify=verify
    return base.main()
if __name__=='__main__':sys.exit(main())
