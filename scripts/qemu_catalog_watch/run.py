#!/usr/bin/env python3
"""Actual installed catalog watchers plus every original managed Native proof."""
import gzip
import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
base=module('catalog_watch_agent_base',ROOT/'scripts/qemu/run-agent-qemu.py')
checker=module('catalog_watch_verifier',HERE/'verify.py')
original_inputs=base.inputs
original_guest=base.guest_source
original_payload=base.payload
original_verify=base.verify

def inputs():
    result=original_inputs()
    for path in sorted(HERE.glob('*')):
        if path.is_file() and path.suffix in ['.py','.sh','.jq','.md']:
            result[str(path.relative_to(ROOT))]={'bytes':path.stat().st_size,'sha256':base.digest(path)}
    return dict(sorted(result.items()))

def replace_once(text,old,new):
    if text.count(old)!=1:raise RuntimeError('unique catalog guest anchor absent: '+repr(old))
    return text.replace(old,new,1)

def guest_source():
    text=original_guest()
    anchor='[ "${AGENT_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2\n'
    text=replace_once(text,anchor,anchor+(HERE/'guest-functions.sh').read_text()+'\n')
    text=replace_once(text,'    for epoch in 1 2; do\n        STAGE="client-epoch-$epoch"',
        '    catalog_cli_cases\n    catalog_watch_start\n    for epoch in 1 2; do\n        STAGE="client-epoch-$epoch"')
    text=replace_once(text,'            kill -KILL "$AGENT_PID"\n','            catalog_pause_before_kill\n            kill -KILL "$AGENT_PID"\n')
    text=replace_once(text,'            agent_start 2\n            wait_sessions serial',
        '            catalog_local_outage\n            agent_start 2\n            wait_sessions serial')
    text=replace_once(text,'            echo AGENT_CLIENT_RECOVERED\n            host_expect START2',
        '            catalog_recovered\n            echo AGENT_CLIENT_RECOVERED\n            catalog_remote_empty_wait\n            host_expect START2')
    text=replace_once(text,'        else\n            host_expect HELD3\n',
        '            catalog_stop_export "$HID_KEY" hid\n            echo CATALOG_SERVER_ONE\n            host_expect CATALOG_ONE\n'
        '            catalog_stop_export "$SERIAL_KEY" serial\n            echo CATALOG_SERVER_EMPTY\n            host_expect CATALOG_EMPTY_STOPPED\n'
        '        else\n            host_expect HELD3\n')
    return text

def payload(work,kernel,certificates,role):
    _,provenance=original_payload(work,kernel,certificates,role)
    root=work/'root';point=root/'etc/rustusb/catalog-point.jq'
    point.write_bytes((HERE/'point.jq').read_bytes());point.chmod(0o644)
    provenance['etc/rustusb/catalog-point.jq']=base.digest(point)
    names=b'.\0'+b'\0'.join(str(p.relative_to(root)).encode() for p in root.rglob('*'))+b'\0'
    packed=subprocess.run(['cpio','--null','-o','--format=newc','--owner=0:0'],input=names,cwd=root,capture_output=True,check=True)
    archive=work/'catalog-watch-initramfs.cpio.gz';archive.write_bytes(gzip.compress(packed.stdout))
    return archive,provenance

def extra_controls(logs,send_once):
    if 'CATALOG_CLIENT_ONE\n' in logs['client']:send_once('catalog-one','server','CATALOG_ONE')
    if 'CATALOG_CLIENT_EMPTY_STOPPED\n' in logs['client']:send_once('catalog-empty-stopped','server','CATALOG_EMPTY_STOPPED')

def verify(evidence,args):
    facts=original_verify(evidence,args)
    facts['catalog_watch']=checker.verify(evidence,args)
    return facts

def main():
    base.inputs=inputs;base.guest_source=guest_source;base.payload=payload;base.verify=verify;base.extra_controls=extra_controls
    return base.main()
if __name__=='__main__':sys.exit(main())
