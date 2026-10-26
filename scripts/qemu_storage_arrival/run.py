#!/usr/bin/env python3
"""Native pre-arrival isolation in two private full-Ubuntu QEMU guests."""
import argparse
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'scripts/qemu_installed_usb'))
from vm import Vm,sha
spec=importlib.util.spec_from_file_location('physical',ROOT/'scripts/qemu/run-physical-qemu.py')
physical=importlib.util.module_from_spec(spec);spec.loader.exec_module(physical)


def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
def save(path,data):path.write_text(json.dumps(data,indent=2)+'\n')
def inputs():
    names=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    return {n:sha(ROOT/n) for n in names if n and not n.startswith('docs/evidence/')}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--prepared',type=Path,required=True)
    parser.add_argument('--server-release',choices=['jammy','noble'],required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(mode=0o700)
    assert not git('status','--porcelain')
    head=git('rev-parse','HEAD');source=inputs()
    releases={'server':args.server_release,'client':'noble' if args.server_release=='jammy' else 'jammy'}
    parents={role:args.prepared/release/'.private/disk.qcow2' for role,release in releases.items()}
    prep={role:json.loads((args.prepared/release/'preparation.json').read_text()) for role,release in releases.items()}
    for role,p in parents.items():assert prep[role]['passed'] and sha(p)==prep[role]['derived_disk_sha256']
    elfs=[ROOT/'target/release/rustusb',ROOT/'target/release/rustusb-helper',ROOT/'apps/rustusb-gui/target/release/rustusb-gui']
    frozen={str(p):sha(p) for p in [*parents.values(),*elfs]}
    save(out/'provenance.json',{'head':head,'inputs':source,'frozen':frozen,'prepared':prep,
                               'scope':'upstream mechanism; no RustUSB product executable runs'})
    links=socket.socketpair()
    vms={role:Vm(out/role,parents[role],role,links[i]) for i,role in enumerate(['server','client'])}
    result={'passed':False,'head':head,'releases':releases,'qemu_exits':{},'collection_exits':{}}
    qmp_records=[];daemon_future=None;pool=concurrent.futures.ThreadPoolExecutor(max_workers=2)
    observations={}
    def guest(role,action,*args):
        label=action+('-'+'-'.join(map(str,args)) if args else '')
        command='sudo python3 /var/tmp/rustusb-installed-fixture/guest.py '+shlex.join([action,*map(str,args)])
        value=json.loads(vms[role].run(command,label))
        observations[role+'-'+label]=value;save(out/'observations.json',observations)
        return value
    try:
        futures=[pool.submit(vm.boot) for vm in vms.values()]
        for future in futures:future.result()
        for role,vm in vms.items():
            vm.run('set -e\nsudo systemctl stop apt-daily.timer apt-daily-upgrade.timer\nsudo env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 install -y gcc sg3-utils','dependencies')
            vm.upload([HERE/'guest.py',HERE/'claim-denied.c'])
            vm.run('gcc -O2 -Wall -Wextra -Werror -o /var/tmp/rustusb-installed-fixture/claim-denied /var/tmp/rustusb-installed-fixture/claim-denied.c','compile-claim-probe')
            guest(role,'prepare',role)
        server=vms['server'];client=vms['client']
        qmp=physical.Qmp(server.private/'qmp.sock',time.monotonic()+15,server.process)
        def call(op,arguments):
            response=qmp.call(op,arguments)
            qmp_records.append({'op':op,'arguments':arguments,'response':response})
            save(out/'qmp.json',qmp_records)
            return response
        try:
            call('device_add',{'driver':'qemu-xhci','id':'arrival-xhci'})
            guest('server','fence')
            call('device_add',{'driver':'usb-bot','id':'arrival-bot','bus':'arrival-xhci.0','port':'1','serial':'RUSTUSB-E66-BOT'})
            for lun in [0,1]:
                backing=server.private/f'lun{lun}.raw'
                with backing.open('xb') as f:f.truncate(64*1024*1024)
                call('blockdev-add',{'driver':'raw','node-name':f'arrival-lun{lun}','read-only':lun==0,
                                    'file':{'driver':'file','filename':str(backing),
                                            'cache':{'direct':False,'no-flush':False}}})
                call('device_add',{'driver':'scsi-hd','id':f'arrival-scsi{lun}','bus':'arrival-bot.0',
                                   'scsi-id':0,'lun':lun,'drive':f'arrival-lun{lun}'})
            call('qom-set',{'path':'/machine/peripheral/arrival-bot','property':'attached','value':True})
            call('query-block',{})
        finally:qmp.close()
        server.run('sudo udevadm settle --timeout=30','arrival-settle')
        bound=guest('server','bind');bus=bound['bus']
        daemon_future=pool.submit(server.run,'sudo python3 /var/tmp/rustusb-installed-fixture/guest.py serve','serve')
        server.run('for i in $(seq 1 200); do test ! -f /var/tmp/rustusb-storage-arrival/daemon-ready.json || exit 0; sleep 0.1; done; exit 1','daemon-ready')
        print('round 1: actual two-LUN enumeration and checked client cache commands',flush=True)
        guest('client','attach',bus);guest('server','snapshot','imported-1');guest('server','claim')
        guest('client','io',1);guest('client','detach');guest('server','snapshot','normal-detach')
        print('round 2: actual daemon death differs from kernel data-owner release',flush=True)
        guest('client','attach',bus);guest('client','io',2)
        guest('server','kill-daemon')
        observations['server-serve']=json.loads(daemon_future.result());daemon_future=None
        save(out/'observations.json',observations)
        guest('client','io',3);guest('server','snapshot','daemon-dead-io-live')
        guest('server','reset-link');guest('client','empty')
        final=guest('server','snapshot','fault-quarantine')
        assert final['driver']=='usbip-host' and final['authorized']=='1'
        result['passed']=True
    except Exception:
        result['failure']=traceback.format_exc();print(result['failure'],flush=True)
    finally:
        if daemon_future is not None:
            try:
                if not daemon_future.done():guest('server','kill-daemon')
                daemon_future.result()
            except Exception:result['daemon_cleanup_error']=traceback.format_exc();result['passed']=False
        pool.shutdown(wait=True)
        for role,vm in vms.items():
            if vm.process is not None and vm.process.poll() is None:
                try:
                    with (out/(role+'-evidence.tar')).open('wb') as f:
                        p=subprocess.run(vm.ssh+['sudo tar -C /var/tmp/rustusb-storage-arrival -cf - .'],stdout=f,stderr=subprocess.PIPE)
                    (out/(role+'-collection.stderr')).write_bytes(p.stderr)
                    result['collection_exits'][role]=p.returncode
                    if p.returncode:result['passed']=False
                except Exception:result[role+'-collection-error']=traceback.format_exc();result['passed']=False
            result['qemu_exits'][role]=vm.close()
            if result['qemu_exits'][role]!=0:result['passed']=False
            serial=(vm.out/'serial.log').read_text(errors='replace')
            result[role+'-kernel-integrity']=not any(text in serial for text in physical.INTEGRITY_FAILURES)
            if not result[role+'-kernel-integrity']:result['passed']=False
        for link in links:link.close()
        result['guards']={'head':git('rev-parse','HEAD')==head,'clean':not git('status','--porcelain'),
                          'source':inputs()==source,'frozen':all(sha(Path(p))==h for p,h in frozen.items())}
        if not all(result['guards'].values()):result['passed']=False
        if result['passed']:
            backing=vms['server'].private/'lun1.raw'
            with backing.open('rb') as f:
                for cycle in [1,2,3]:
                    row=observations['client-io-'+str(cycle)];f.seek(row['offset']);data=f.read(row['bytes'])
                    import hashlib
                    if hashlib.sha256(data).hexdigest()!=row['sha256']:result['passed']=False
            result['backing_readback_after_actual_qemu_exit']=result['passed']
            result['backing_scope']='file-byte readback, not physical-media power-loss durability'
        save(out/'result.json',result)
    print(json.dumps(result),flush=True)
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
