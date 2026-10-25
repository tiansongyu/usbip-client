#!/usr/bin/env python3
"""Actual native GUI Start/Stop/Remote/window-close with unchanged Native USB proofs."""
import argparse,gzip,hashlib,importlib.util,json,os,re,shutil,subprocess,sys
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
base=module('gui_actions_agent',ROOT/'scripts/qemu/run-agent-qemu.py')
gui=module('gui_actions_runtime',ROOT/'scripts/qemu_gui/run.py')
old_inputs,old_guest,old_payload,old_verify=base.inputs,base.guest_source,base.payload,base.verify
DRIVER=None

def inputs():
    result=old_inputs();result.update(gui.inputs())
    for p in HERE.iterdir():
        if p.is_file()and p.suffix in ['.c','.sh','.py','.md']:result[str(p.relative_to(ROOT))]={'bytes':p.stat().st_size,'sha256':base.digest(p)}
    return dict(sorted(result.items()))
def replace(text,old,new):
    if text.count(old)!=1:raise RuntimeError('unique GUI action anchor absent: '+repr(old))
    return text.replace(old,new,1)
def guest():
    text=old_guest();anchor='[ "${AGENT_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2\n'
    text=replace(text,anchor,anchor+(HERE/'guest-functions.sh').read_text()+'\n')
    start='''    agent start "$1" > "/tmp/start-$1-$AGENT_EPOCH.json" 2> "/tmp/start-$1-$AGENT_EPOCH.log"
    jq -e '.schema_version==1 and .journal_healthy==true and .response.result=="started" and .response.replayed==false and .response.job.operator_uid==1000' "/tmp/start-$1-$AGENT_EPOCH.json" > /dev/null
    # The requesting CLI has actually returned before any I/O below.
    echo "AGENT_START_RETURNED role=$ROLE profile=$1 epoch=$AGENT_EPOCH wait=0"'''
    text=replace(text,start,'    gui_start_job "$1"')
    for profile in ['server','hid']:text=replace(text,'manifest; agent_start 1; denied '+profile,'manifest; agent_start 1; denied '+profile+'\n    gui_infra')
    stop='''            agent stop-session "$STOP_ID" > /tmp/stop-hid.json
            jq -e '.response=={result:"stop_accepted",owner_present:true}' /tmp/stop-hid.json > /dev/null'''
    text=replace(text,stop,'            gui_stop server sessions "$STOP_ID" stop-session-hid')
    text=replace(text,'        kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" "agent-$epoch"','        gui_stop_job server\n        kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" "agent-$epoch"')
    text=replace(text,'            kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-2','            gui_stop_job hid\n            gui_stop_job serial\n            kill -TERM "$AGENT_PID"; wait_exit "$AGENT_PID" agent-2')
    text=replace(text,'    agent discover hid > /tmp/catalog-initial.json','    gui_remote\n    agent discover hid > /tmp/catalog-initial.json')
    text=replace(text,'STAGE=complete\necho "RUSTUSB_AGENT_RESULT: PASS role=$ROLE"','gui_infra_stop\nSTAGE=complete\necho "RUSTUSB_AGENT_RESULT: PASS role=$ROLE"')
    return text

def install_native_automation(work,root,provenance,driver):
    """Install the same guest-only native launcher/close/runtime for GUI fixtures."""
    gui.install_runtime(root,driver)
    binary=work/'gui-native-close'
    base.command(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(HERE/'native-close.c'),'-lX11','-o',str(binary)],work/'gui-close-build.log')
    base.secure.native.copy_binary(binary,'/usr/bin/gui-native-close',root)
    launcher=root/'usr/bin/gui-launcher'
    launcher.write_text('''#!/bin/sh
label=$1;shift
case "$label" in ''|*[!a-z0-9-]*)exit 2;;esac
/usr/bin/rustusb-gui "$@" >"/tmp/gui-$label.stdout" 2>"/tmp/gui-$label.stderr" &
pid=$!;echo "$pid" >"/tmp/gui-$label.pid"
wait "$pid";result=$?;echo "$result" >"/tmp/gui-$label.wait";exit "$result"
''');launcher.chmod(0o755)
    for name in ['rustusb-gui','gui-native-close','WebKitWebDriver','gui-launcher']:provenance['usr/bin/'+name]=base.digest(root/'usr/bin'/name)
    elfs={str(p.relative_to(root)):{'bytes':p.stat().st_size,'sha256':base.digest(p)}for p in root.rglob('*')if p.is_file()and not p.is_symlink()and p.read_bytes()[:4]==b'\x7fELF'}
    (root/'etc/rustusb/gui-runtime-elfs.json').write_text(json.dumps(elfs,indent=2)+'\n')

def payload(work,kernel,certificates,role):
    _,provenance=old_payload(work,kernel,certificates,role);root=work/'root'
    install_native_automation(work,root,provenance,DRIVER)
    shutil.copyfile(root/'init',work/'generated-agent-init.sh')
    names=b'.\0'+b'\0'.join(str(p.relative_to(root)).encode()for p in root.rglob('*'))+b'\0'
    plain=work/'gui-agent.cpio';archive=work/'gui-agent.cpio.gz'
    with plain.open('wb')as stream:r=subprocess.run(['cpio','--null','-o','--format=newc','--owner=0:0'],input=names,cwd=root,stdout=stream,stderr=subprocess.PIPE,check=True)
    (work/'gui-cpio.stderr').write_bytes(r.stderr)
    with plain.open('rb')as source,gzip.open(archive,'wb',compresslevel=1)as dest:shutil.copyfileobj(source,dest)
    return archive,provenance

def verify(evidence,args):
    facts=old_verify(evidence,args);checks=[]
    gui_sha=base.digest(ROOT/'apps/rustusb-gui/target/release/rustusb-gui')
    for role in ['server','client']:
        folder=evidence/f'{role}-artifacts';read=lambda n:json.loads((folder/n).read_text())
        profiles=['server']if role=='server'else['hid','serial']
        labels=[f'start-{p}-{e}'for e in [1,2]for p in profiles]
        labels+=['stop-session-hid','stop-job-server-1','stop-job-server-2']if role=='server'else['remote-catalog','stop-job-hid-2','stop-job-serial-2']
        for label in labels:
            prefix='gui-'+label
            actor=(folder/(prefix+'-actor.txt')).read_text()
            assert re.search(r'^Uid:\s+1000\s+1000\s+1000\s+1000$',actor,re.M)
            assert all(re.search(r'^'+c+r':\s+0000000000000000$',actor,re.M)for c in ['CapInh','CapPrm','CapEff','CapAmb'])
            assert gui_sha in actor and 'TARGET=/dev/bus/usb/'not in actor
            assert (folder/(prefix+'.wait')).read_text().strip()=='0'
            assert 'local worker joined'in(folder/(prefix+'.stderr')).read_text()
            close=(folder/(prefix+'-close.txt')).read_text();assert 'protocol=WM_DELETE_WINDOW'in close and 'sent=1'in close
            assert re.search(r'timestamp=[1-9][0-9]*',close)
            assert 'GUI_FORCED_CHILD'not in(evidence/f'{role}-evidence.log').read_text()
            driver_actor=(folder/(prefix+'-driver-actor.txt')).read_text();assert re.search(r'^Uid:\s+1000\s+1000\s+1000\s+1000$',driver_actor,re.M)
            assert base.digest(DRIVER)in driver_actor
            session=read(prefix+'-session.json')['value'];assert session['capabilities']['browserName']=='wry'
            if label!='remote-catalog':assert read(prefix+'-confirm.json')['value']['focus']=='cancel'
            if label.startswith('stop-job-'):
                profile=label[len('stop-job-'):].rsplit('-',1)[0];epoch=label.rsplit('-',1)[1]
                expected=read(f'start-{profile}-{epoch}.json')['response']['job']['id']
                assert expected in read(prefix+'-confirm.json')['value']['text']
                assert 'Stop accepted (owner present: true)'in read(prefix+'-accepted.json')['value']['status']
            if label=='stop-session-hid':
                candidates=[s['id']for s in read('server-held-1.json')['response']['sessions']if s['role']=='incoming'and s['device_key']==json.loads((folder/'keys.json').read_text())[0]and not s['terminal']]
                assert len(candidates)==1 and candidates[0]in read(prefix+'-confirm.json')['value']['text']
                assert 'Stop accepted (owner present: true)'in read(prefix+'-accepted.json')['value']['status']
            checks.append(f'{role}/{label}: actual native UID1000/ELF/no-caps/WM_DELETE_WINDOW/wait0/join')
        for p in profiles:
            for epoch in [1,2]:
                prefix=f'gui-start-{p}-{epoch}';before=read(prefix+'-before.json');after=read(prefix+'-after.json');retry=read(prefix+'-after-retry.json')
                new=[r for r in after['requests']if r['id']not in {x['id']for x in before['requests']}]
                assert len(new)==1 and new[0]['uid']==1000 and new[0]['profile']==p and after['requests']==retry['requests']
                receipt=read(f'start-{p}-{epoch}.json')['response'];job=receipt['job'];assert receipt['replayed']and job['id']==new[0]['job_id']
                assert 'Admitted job '+job['id']in read(prefix+'-admitted.json')['value']['status']
                assert 'Admitted job '+job['id']in read(prefix+'-dom.json')['value']
                alive=read(prefix+'-after-close.json')['response']['jobs'];assert any(j['id']==job['id']and j['state']=='running'and not j['stop_requested']for j in alive)
        assert(folder/'gui-operator-key-unreadable.txt').read_text().strip()=='0'
    client=evidence/'client-artifacts';catalog=json.loads((client/'gui-remote-catalog.json').read_text())['value']['remote']
    assert catalog['state']=='current'and len(catalog['devices'])==2
    assert {d['device_key']for d in catalog['devices']}==set(json.loads((client/'keys.json').read_text()))
    assert 'No devices match'in json.loads((client/'gui-remote-search-empty.json').read_text())['value']
    detail=json.loads((client/'gui-remote-detail.json').read_text())['value'];assert detail['filterHidden']and 'device_number'in detail['text']
    health=json.loads((client/'gui-raw-health.json').read_text())['value']['health'];assert any(h['link']and h['link']['protocol']=='raw_v1'and h['link']['last_round_trip_micros']is None for h in health)
    assert 'RTT unavailable'in json.loads((client/'gui-raw-health-dom.json').read_text())['value']
    facts['gui_actions']={'checks':checks,'native_processes':12,'start_requests':6,'targeted_session_stops':1,'job_stops':4,'gui_sha256':gui_sha,'scope':'Real native actions/Remote/WM close and continued original kernel I/O; raw-v1 RTT unavailable, not installation/hardware/full Phase5.'}
    return facts

def main():
    global DRIVER
    p=argparse.ArgumentParser(add_help=False);p.add_argument('--driver',type=Path,required=True);own,remaining=p.parse_known_args();DRIVER=own.driver.resolve()
    assert DRIVER.is_file()and DRIVER.read_bytes()[:4]==b'\x7fELF'
    sys.argv=[sys.argv[0],*remaining]
    base.inputs=inputs;base.guest_source=guest;base.payload=payload;base.verify=verify
    location=argparse.ArgumentParser(add_help=False);location.add_argument('--evidence',type=Path,required=True);target,_=location.parse_known_args(remaining)
    gui_before=base.digest(ROOT/'apps/rustusb-gui/target/release/rustusb-gui');driver_before=base.digest(DRIVER)
    result=base.main(guest_memory_mib=4096)
    summary_path=target.evidence/'summary.json';summary=json.loads(summary_path.read_text())
    summary['gui_binary_sha256']=gui_before;summary['driver_sha256']=driver_before
    summary['gui_binary_guard']=base.digest(ROOT/'apps/rustusb-gui/target/release/rustusb-gui')==gui_before
    summary['driver_guard']=base.digest(DRIVER)==driver_before
    if not summary['gui_binary_guard']or not summary['driver_guard']:summary['result']='FAIL';result=1
    summary_path.write_text(json.dumps(summary,indent=2)+'\n')
    return result
if __name__=='__main__':raise SystemExit(main())
