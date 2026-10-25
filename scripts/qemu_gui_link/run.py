#!/usr/bin/env python3
"""Real native GUI RTT/fault presentation, preserving the original v2 USB proofs."""
import argparse
import gzip
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'scripts/qemu_data_link_health'))
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value);return value
link=module('gui_link_original',ROOT/'scripts/qemu_data_link_health/run.py')
actions=module('gui_link_actions',ROOT/'scripts/qemu_gui_actions/run.py')
old_inputs,old_guest,old_payload=link.source_inputs,link.guest_source,link.payload
original_checker=link.lifecycle('native');DRIVER=None
def inputs():
    result=old_inputs();result.update(actions.inputs())
    for p in HERE.iterdir():
        if p.is_file()and p.suffix in ['.py','.sh','.md']:result[str(p.relative_to(ROOT))]={'bytes':p.stat().st_size,'sha256':link.sha(p)}
    return dict(sorted(result.items()))
def replace(text,old,new):
    if text.count(old)!=1:raise RuntimeError('unique GUI v2 anchor absent: '+repr(old))
    return text.replace(old,new,1)
def guest(backend='native'):
    if backend!='native':raise RuntimeError('GUI v2 fixture supports Native only')
    text=old_guest(backend)
    anchor='[ "${LINK_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2\n'
    text=replace(text,anchor,anchor+(ROOT/'scripts/qemu_gui_actions/guest-functions.sh').read_text()+'\n'+(HERE/'guest-functions.sh').read_text()+'\n')
    text=replace(text,'    manifest; agent_start 1; denied hid\n','    manifest; agent_start 1; denied hid\n    gui_infra\n')
    anchor='        phase_snapshot "pre-$phase"\n        publish "/tmp/health-$phase-client-pre.json"'
    text=replace(text,anchor,'        gui_link_pre "$phase" "$HID_SESSION"\n'+anchor)
    anchor='        echo "LINK_FAULT_CLOSED role=client phase=$phase"'
    text=replace(text,anchor,'        gui_link_terminal "$phase" "$HID_SESSION"\n'+anchor)
    text=replace(text,'ip link set eth0 down\n','[ "$ROLE" != client ] || gui_infra_stop\nip link set eth0 down\n')
    return text
def payload(work,kernel,certificates,role,backend='native'):
    archive,provenance=old_payload(work,kernel,certificates,role,backend)
    if role!='client':return archive,provenance
    root=work/'root';actions.install_native_automation(work,root,provenance,DRIVER)
    names=b'.\0'+b'\0'.join(str(p.relative_to(root)).encode()for p in root.rglob('*'))+b'\0'
    plain=work/'gui-link.cpio';archive=work/'gui-link.cpio.gz'
    with plain.open('wb')as stream:r=subprocess.run(['cpio','--null','-o','--format=newc','--owner=0:0'],input=names,cwd=root,stdout=stream,stderr=subprocess.PIPE,check=True)
    (work/'gui-cpio.stderr').write_bytes(r.stderr)
    with plain.open('rb')as source,gzip.open(archive,'wb',compresslevel=1)as dest:shutil.copyfileobj(source,dest)
    return archive,provenance
def verify(evidence,args):
    facts=original_checker.verify(evidence,args);folder=evidence/'client-artifacts'
    read=lambda n:json.loads((folder/n).read_text());gui_sha=link.sha(ROOT/'apps/rustusb-gui/target/release/rustusb-gui');checks=[]
    for phase in ['c2s','s2c','both']:
        prefix='gui-link-'+phase;actor=(folder/(prefix+'-actor.txt')).read_text()
        assert re.search(r'^Uid:\s+1000\s+1000\s+1000\s+1000$',actor,re.M)
        assert all(re.search(r'^'+c+r':\s+0000000000000000$',actor,re.M)for c in ['CapInh','CapPrm','CapEff','CapAmb'])
        assert gui_sha in actor and 'TARGET=/dev/bus/usb/'not in actor
        driver=(folder/(prefix+'-driver-actor.txt')).read_text();assert link.sha(DRIVER)in driver and re.search(r'^Uid:\s+1000\s+1000\s+1000\s+1000$',driver,re.M)
        assert read(prefix+'-session.json')['value']['capabilities']['browserName']=='wry'
        assert (folder/(prefix+'.wait')).read_text().strip()=='0'and 'local worker joined'in(folder/(prefix+'.stderr')).read_text()
        assert 'protocol=WM_DELETE_WINDOW'in(folder/(prefix+'-close.txt')).read_text()and 'sent=1'in(folder/(prefix+'-close.txt')).read_text()
        assert re.search(r'timestamp=[1-9][0-9]*',(folder/(prefix+'-close.txt')).read_text())
        assert (folder/(prefix+'-pre-stat.txt')).read_text().split()[21]==(folder/(prefix+'-terminal-stat.txt')).read_text().split()[21]
        expanded=read(prefix+'-expanded.json')['value'];before=read(prefix+'-refreshed.json')['value'];after=read(prefix+'-terminal-dom.json')['value']
        assert all(v['open']and v['focused']for v in [expanded,before,after])
        assert all(v['metadataScrollTop']==40 for v in [expanded,before,after])
        assert int(before['health']['link']['last_reply_sequence'])>int(expanded['health']['link']['last_reply_sequence'])
        original=read(f'health-{phase}-client-pre.json')['response']['sessions'];target=before['health'];same=[s for s in original if s['session_id']==target['session_id']];assert len(same)==1
        expected_identity={**same[0]['link']['identity'],'connection':str(same[0]['link']['identity']['connection'])}
        assert target['instance_id']==same[0]['instance_id']and target['link']['identity']==expected_identity
        assert target['availability']=='live'and target['link']['phase']=='open'and target['link']['end']is None
        assert before['rtt']=='Last enqueue-to-reply RTT: '+target['link']['last_round_trip_micros']+' µs'
        ended=after['health'];assert ended['session_id']==target['session_id']and ended['instance_id']==target['instance_id']and ended['link']['identity']==target['link']['identity']
        assert ended['availability']=='owner_ended'and ended['link']['phase']=='ended'and ended['link']['end']is not None
        snapshot=read(prefix+'-terminal-snapshot.json')['value'];session=[s for s in snapshot['sessions']if s['id']==target['session_id']];assert len(session)==1 and session[0]['terminal']and session[0]['cleanup']=='verified'
        assert ended['link']['end']in after['summary']and 'history'in after['summary']and 'ended'in after['summary']
        assert after['rtt'].startswith('Last recorded enqueue-to-reply RTT: ')and '(not a current responsiveness result)'in after['rtt']
        checks.append({'phase':phase,'session':target['session_id'],'connection':target['link']['identity'],'end':ended['link']['end'],'operation_error':session[0]['operation_error'],'cleanup':session[0]['cleanup']})
    for profile in ['serial','hid']:
        switch=read('gui-link-profile-'+profile+'.json')['value'];assert switch['profile']==profile and switch['details']and all(not d['open']for d in switch['details'])
        expected={s['id']for s in read('agent-pre-c2s.json')['sessions']if s['profile']==profile and not s['terminal']};assert {d['health']['session_id']for d in switch['details']}==expected
    assert 'GUI_FORCED_CHILD'not in(evidence/'client-evidence.log').read_text()
    assert (folder/'gui-operator-key-unreadable.txt').read_text().strip()=='0'
    facts['native_gui']={'native_processes':3,'checks':checks,'scope':'Actual same-data RTT and three direction faults; stable expansion/focus, real WM close/wait0. Original v2 network/USB proof unchanged; no installation/hardware/fullPhase5 claim.'}
    return facts
def main():
    global DRIVER
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--driver',type=Path,required=True);p.add_argument('--family',choices=['ipv4','ipv6'],required=True);p.add_argument('--kernel',default=os.uname().release);p.add_argument('--timeout',type=int,default=600);p.add_argument('--evidence',type=Path,required=True);args=p.parse_args();args.backend='native';args.skip_build=False
    DRIVER=args.driver.resolve();assert DRIVER.is_file()and DRIVER.read_bytes()[:4]==b'\x7fELF'
    gui_before=link.sha(ROOT/'apps/rustusb-gui/target/release/rustusb-gui');driver_before=link.sha(DRIVER)
    link.source_inputs=inputs;link.guest_source=guest;link.payload=payload;link.lifecycle=lambda backend:SimpleNamespace(phase_proof=original_checker.phase_proof,verify=verify)if backend=='native'else None
    result=link.run_experiment(args,guest_memory_mib=4096)
    path=args.evidence/'summary.json';summary=json.loads(path.read_text());summary.update(gui_sha256=gui_before,driver_sha256=driver_before,gui_binary_guard=link.sha(ROOT/'apps/rustusb-gui/target/release/rustusb-gui')==gui_before,driver_guard=link.sha(DRIVER)==driver_before)
    if not summary['gui_binary_guard']or not summary['driver_guard']:summary['result']='FAIL';result=1
    path.write_text(json.dumps(summary,indent=2)+'\n');return result
if __name__=='__main__':raise SystemExit(main())
