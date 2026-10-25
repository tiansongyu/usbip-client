#!/usr/bin/env python3
"""Actual packaged systemd Native/USBFS I/O and cooperative upgrade in two guests."""
import argparse
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import shlex
import socket
import subprocess
import time
import traceback
from vm import Vm, sha

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('installed_secure',ROOT/'scripts/qemu/run-secure-qemu.py')
secure=importlib.util.module_from_spec(spec);spec.loader.exec_module(secure)

def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def require(ok,message):
    if not ok:raise RuntimeError(message)

def check_terminal(snap):
    app=snap['app'];uid=snap['uid'];count=0
    require(app is not None,'actual application state exists')
    require(all(j['state']=='finished' and j['operator_uid']==1000 for j in app['jobs']),'all original jobs finished')
    for session in app['sessions']:
        require(session['terminal'] and session['cleanup']=='verified','original session actual cleanup verified')
        for attempt in session['attempts']:
            journal=snap['physical'] if attempt['kind']=='usbfs_grant' else snap['native']
            terminals=journal['terminals'] if attempt['kind']=='usbfs_grant' else [x['terminal'] for x in journal['terminals']]
            matches=[t for t in terminals if t['kind']==attempt['kind'] and t['correlation']==attempt['correlation']]
            require(len(matches)==1,'exact unique original Root correlation')
            t=matches[0]
            require(t['owner_uid']==uid and t['device_key']==session['device_key'] and t['resource_id']==attempt['resource_id'],'Root UID/key/resource equality')
            expected={'native_import':'native_import_released','native_export':'native_export_restored','native_stub':'native_stub_available','usbfs_grant':'physical_baseline_restored'}[attempt['kind']]
            require(t['proof']==expected and attempt['proof'] in (None,expected),'exact Root release criterion; optional app echo must agree')
            count+=1
    for journal in [snap['native'],snap['physical']]:
        if journal:
            require(journal['records']==[] and journal.get('exports',[])==[],'no remaining original Root capabilities')
            require(len(journal['terminals'])==sum(1 for s in app['sessions'] for a in s['attempts'] if (a['kind']=='usbfs_grant')==(journal is snap['physical'])),'no unmatched or fabricated Root terminals')
    return count

def check_empty_vhci(snap,old):
    rows=[line.split() for item in snap['vhci'] for line in item['text'].splitlines() if line.startswith(('hs ','ss '))]
    require(rows and all(int(x[2])==4 for x in rows),'all actual original VHCI ports NULL')
    require(not snap['locals'],'no current original local receipts')
    # Kernel generation absence is sampled separately from the journal.
    return [x['bus'] for x in old['locals']]

def check_held(snap,keys):
    app=snap['app'];uid=snap['uid'];rows=snap['native']['records']
    require(len(rows)==2 and len(snap['locals'])==2,'two real kernel imports')
    require({s['driver'] for s in snap['locals']}=={'usbhid','ftdi_sio'},'actual downstream native drivers')
    require({r['target']['device_key'] for r in rows}==set(keys),'exact imported device keys')
    for r in rows:
        receipt=r['state']['receipt']
        require(r['owner']['uid']==uid and r['state']['phase']=='observed' and receipt['session']['status']==6 and receipt['kernel_socket_inode']>0,'strong actual helper receipt')
        matches=[(s,a) for s in app['sessions'] for a in s['attempts'] if a['resource_id']==r['channel_id']]
        require(len(matches)==1 and matches[0][1]['correlation']==r['correlation'] and matches[0][0]['device_key']==r['target']['device_key'],'live exact application/helper identity')

def one(args):
    out=args.output.resolve();out.mkdir(mode=0o700)
    private=out/'.private';private.mkdir(mode=0o700)
    initial=git('rev-parse','HEAD');require(not git('status','--porcelain'),'clean source before live owners')
    elfs=[ROOT/'target/release/rustusb',ROOT/'target/release/rustusb-helper',ROOT/'apps/rustusb-gui/target/release/rustusb-gui']
    frozen={str(p):sha(p) for p in [*elfs,args.base,args.upgrade]}
    save(out/'provenance.json',{'head':initial,'frozen_files':frozen,'backend':args.backend,'family':args.family,
        'prepared_os':[json.loads((args.prepared/r/'preparation.json').read_text()) for r in ['jammy','noble']]})
    certs=secure.credentials(private,out)
    prep={r:args.prepared/r/'.private/disk.qcow2' for r in ['jammy','noble']}
    server_release,client_release=('jammy','noble') if args.family==4 else ('noble','jammy')
    for r in [server_release,client_release]:
        receipt=json.loads((args.prepared/r/'preparation.json').read_text())
        require(receipt['passed'] and sha(prep[r])==receipt['derived_disk_sha256'],'prepared OS exact closed image')
    links=socket.socketpair();server=Vm(out/'server',prep[server_release],'server',links[0],True);client=Vm(out/'client',prep[client_release],'client',links[1])
    vms={'server':server,'client':client};result={'passed':False,'head':initial,'qemu_exits':{},'backend':args.backend,'family':args.family}
    data={}
    def guest(role,action,*values):
        label=action+('-'+'-'.join(str(x) for x in values) if values else '')
        command='sudo python3 /var/tmp/rustusb-installed-fixture/guest.py '+shlex.join([action,*map(str,values)])
        value=json.loads(vms[role].run(command,label))
        data.setdefault(role,{}).setdefault(action,[]).append(value)
        save(out/(role+'-'+label+'-'+str(len(data[role][action]))+'.json'),value)
        return value
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(vm.boot) for vm in vms.values()]
            for f in futures:f.result()
        for role,vm in vms.items():
            print(role+': installing actual base package',flush=True)
            vm.upload([HERE/'guest.py',args.base,args.upgrade,*(certs/n for n in ['ca.der','server.der','client.der',role+'-key.der'])])
            vm.run('sudo dpkg -i '+shlex.quote('/var/tmp/rustusb-installed-fixture/'+args.base.name),'install-base')
        server_setup=guest('server','setup','server',args.backend,args.family)
        keys=server_setup['device_keys'];keyfile=private/'device-keys.json';save(keyfile,keys);client.upload([keyfile])
        client_setup=guest('client','setup','client',args.backend,args.family)
        for role in vms:
            guest(role,'start-services')
            guest(role,'denied','server' if role=='server' else 'hid')
        guest('server','start-jobs','server');guest('server','ready','server')
        guest('client','plaintext-refused')
        for cycle in [1,2,3]:
            print('actual installed USB I/O round '+str(cycle),flush=True)
            if cycle==3:
                # Stop stale client jobs after their actual remote teardown, preserving operation errors.
                guest('client','stop-services');check_terminal(data['client']['stop-services'][-1])
                guest('server','start-services');guest('server','start-jobs','server');guest('server','ready','server')
                guest('client','start-services')
            catalog=guest('client','discover')
            require(catalog['journal_healthy'],'authenticated discovery is healthy')
            require(catalog['response']['result']=='catalog' and {d['device_key'] for d in catalog['response']['devices']}==set(keys),'exact authenticated approved catalog')
            guest('client','start-jobs','hid','serial')
            held=guest('client','ready','client')['snapshot'];check_held(held,keys)
            guest('server','snapshot')
            # Private FTDI chardev and QMP drive only the emulated server peripherals.
            server.run('true','io-host-barrier')
            client.run('rm -f /var/tmp/rustusb-installed-fixture/probe-ready.json','reset-probe-marker')
            ftdi=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);ftdi.settimeout(25);ftdi.connect(str(server.private/'ftdi.sock'))
            qmp=secure.physical.Qmp(server.private/'qmp.sock',time.monotonic()+10,server.process)
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future=pool.submit(guest,'client','probe',cycle)
                    client.run("while test ! -f /var/tmp/rustusb-installed-fixture/probe-ready.json; do sleep 0.1; done",'await-probe')
                    qmp.call('send-key',{'keys':[{'type':'qcode','data':str(cycle)}],'hold-time':100})
                    ftdi.sendall(f'RUSTUSB-RX-{cycle}-0001:0123456789abcdef\n'.encode())
                    expected=f'RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n'.encode();actual=b''
                    while len(actual)<len(expected):actual+=ftdi.recv(len(expected)-len(actual))
                    save(out/f'ftdi-host-{cycle}.json',{'actual_hex':actual.hex(),'expected_hex':expected.hex(),'equal':actual==expected})
                    require(actual==expected,'actual guest to host FTDI bytes')
                    future.result()
            finally:qmp.close();ftdi.close()
            if cycle in [1,2]:
                role='client' if cycle==1 else 'server'
                print(role+': upgrade with actual live USB owners',flush=True)
                upgraded=guest(role,'upgrade',args.upgrade.name);check_terminal(upgraded['after'])
                before=upgraded['before'];after=upgraded['after']['units']
                stopped_events={}
                for name in before:
                    require(int(before[name]['MainPID'])>0 and before[name]['ActiveState']=='active','actual original unit owner')
                    require(not after[name]['InvocationID'] or after[name]['InvocationID']==before[name]['InvocationID'],'any retained invocation must match original')
                    events=[json.loads(line) for line in upgraded['journal'].splitlines()]
                    stopped_events[name]=[e for e in events if e.get('_PID')=='1' and e.get('UNIT')==name and e.get('INVOCATION_ID')==before[name]['InvocationID'] and e.get('MESSAGE_ID')=='9d1aaa27d60140bd96365438aad20286' and e.get('JOB_TYPE')=='stop' and e.get('JOB_RESULT')=='done']
                    require(len(stopped_events[name])==1,'original invocation exact successful stop event')
                    deactivated=[e for e in events if e.get('_PID')=='1' and e.get('UNIT')==name and e.get('INVOCATION_ID')==before[name]['InvocationID'] and e.get('MESSAGE_ID')=='7ad2d189f7e94e70a38c781354912448']
                    require(len(deactivated)==1,'original invocation exact successful deactivation')
                require(int(stopped_events['rustusb-agent.service'][0]['__MONOTONIC_TIMESTAMP'])<int(stopped_events['rustusb-helper.service'][0]['__MONOTONIC_TIMESTAMP']),'actual agent stop precedes helper stop')
                if role=='client':
                    removed=check_empty_vhci(upgraded['after'],held)
                    client.run('test ! -e '+shlex.quote('/sys/bus/usb/devices/'+removed[0])+' && test ! -e '+shlex.quote('/sys/bus/usb/devices/'+removed[1]),'old-local-generations-absent')
                    guest('server','ready','server')
                    guest('client','start-services')
                else:
                    require(upgraded['after']['host']==server_setup['baseline'],'original server driver/config/interface/node/generation baseline restored')
                    # Wait for the real client kernel transition, not just peer PID disappearance.
                    client.run("for x in $(seq 1 300); do if awk '$1==\"hs\" || $1==\"ss\" {n++; if($3!=4)bad=1} END {exit !(n>0&&!bad)}' /sys/devices/platform/vhci_hcd.0/status; then exit 0; fi; sleep 0.1; done; exit 1",'remote-vhci-null')
        for role in ['client','server']:
            final=guest(role,'stop-services');result[role+'_root_proofs']=check_terminal(final)
            if role=='server':require(final['host']==server_setup['baseline'],'final original server baseline')
        result['passed']=True
    except BaseException:
        result['failure']=traceback.format_exc()
        (out/'failure.txt').write_text(result['failure'])
    finally:
        for role,vm in vms.items():
            if vm.process and vm.process.poll() is None:
                try:guest(role,'stop-services')
                except BaseException:(out/(role+'-cleanup-failure.txt')).write_text(traceback.format_exc())
                try:
                    vm.run('sudo journalctl -b -u rustusb-agent -u rustusb-helper --no-pager -o json','final-service-journal')
                    guest(role,'snapshot')
                except BaseException:(out/(role+'-collection-failure.txt')).write_text(traceback.format_exc())
            result['qemu_exits'][role]=vm.close()
        for link in links:link.close()
        result['head_guard']=git('rev-parse','HEAD')==initial
        result['clean_guard']=not git('status','--porcelain')
        result['frozen_file_guard']=all(sha(Path(p))==digest for p,digest in frozen.items())
        if result['passed']:
            try:
                devices=data['client']['discover'][0]['response']['devices']
                identifiers={(x['device']['bus_number']<<16)|x['device']['device_number'] for x in devices}
                for role,vm in vms.items():
                    review=secure.pcap_review(vm.out/'data.pcap','ipv'+str(args.family),identifiers,expected_alpn=b'rustusb-usbip/2')
                    save(out/(role+'-pcap-review.json'),review)
                result['pcap_review_passed']=True
            except BaseException:
                result['passed']=False;result['pcap_failure']=traceback.format_exc()
        result['passed']=result['passed'] and all(x==0 for x in result['qemu_exits'].values()) and all(result[k] for k in ['head_guard','clean_guard','frozen_file_guard'])
        save(out/'summary.json',result);print(json.dumps(result),flush=True)
    return 0 if result['passed'] else 1

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--base',type=Path,required=True);parser.add_argument('--upgrade',type=Path,required=True)
    parser.add_argument('--backend',choices=['native','usbfs'],required=True);parser.add_argument('--family',type=int,choices=[4,6],required=True)
    raise SystemExit(one(parser.parse_args()))

