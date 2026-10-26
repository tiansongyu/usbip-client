#!/usr/bin/env python3
"""Storage authorization/USBFS boundaries in a disposable full Ubuntu guest."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'scripts/qemu_installed_usb'))
from vm import Vm,sha
spec=importlib.util.spec_from_file_location('physical',ROOT/'scripts/qemu/run-physical-qemu.py')
physical=importlib.util.module_from_spec(spec);spec.loader.exec_module(physical)

def save(path,obj):path.write_text(json.dumps(obj,indent=2)+'\n')
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
def inputs():
    return {n:sha(ROOT/n) for n in subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0') if n and not n.startswith('docs/evidence/')}

class PolicyVm(Vm):
    cold_storage=False
    def boot_device_args(self):
        if not self.cold_storage:return []
        block={'driver':'raw','node-name':'policy-disk','read-only':True,
               'cache':{'direct':False,'no-flush':False},'file':{'driver':'file','filename':str(self.private/'storage.raw')}}
        return ['-device','qemu-xhci,id=policy-xhci,addr=0x6','-blockdev',json.dumps(block),
                '-device','usb-storage,id=policy-storage,drive=policy-disk,bus=policy-xhci.0,port=1,serial=RUSTUSB-E67-STORAGE']

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prepared',type=Path,required=True)
    ap.add_argument('--release',choices=['jammy','noble'],required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();out=args.output.resolve();out.mkdir(mode=0o700)
    assert not git('status','--porcelain');head=git('rev-parse','HEAD');source=inputs()
    prep=json.loads((args.prepared/args.release/'preparation.json').read_text())
    parent=args.prepared/args.release/'.private/disk.qcow2';assert prep['passed'] and sha(parent)==prep['derived_disk_sha256']
    elfs=[ROOT/'target/release/rustusb',ROOT/'target/release/rustusb-helper',ROOT/'apps/rustusb-gui/target/release/rustusb-gui']
    frozen={str(p):sha(p) for p in [parent,*elfs]}
    save(out/'provenance.json',{'head':head,'inputs':source,'frozen':frozen,'preparation':prep,'scope':'upstream mechanism; no product binary executes'})
    vm=PolicyVm(out/'guest',parent,'policy-'+args.release);backing=vm.private/'storage.raw'
    with backing.open('xb') as f:f.truncate(64*1024*1024)
    copy=out/'fixture-upload';copy.mkdir();shutil.copy(ROOT/'scripts/qemu_storage_arrival/guest.py',copy/'arrival_guest.py')
    result={'passed':False,'head':head,'release':args.release,'qemu_exits':[],'reboot_requested':False}
    observations={};qmp=None;boot=0;serial_starts=[];reaped=set()
    def record_exit(code):
        if vm.process is not None and vm.process.pid not in reaped:
            reaped.add(vm.process.pid);result['qemu_exits'].append(code)
            result.setdefault('actual_process_exits',[]).append({'boot':boot,'pid':vm.process.pid,'exit':code})
    def guest(action,*values):
        command='sudo python3 /var/tmp/rustusb-installed-fixture/guest.py '+shlex.join([action,*values])
        value=json.loads(vm.run(command,action+('-'+'-'.join(values) if values else '')+'-boot'+str(boot)))
        observations[str(boot)+'-'+action+('-'+'-'.join(values) if values else '')]=value
        save(out/'observations.json',observations);return value
    def start():
        nonlocal boot,qmp
        boot+=1;serial=vm.out/'serial.log';serial_starts.append(serial.stat().st_size if serial.exists() else 0)
        vm.boot()
        save(out/f'boot-{boot}-qemu.json',{'pid':vm.process.pid,'exe_sha256':sha(Path('/proc')/str(vm.process.pid)/'exe'),
                                        'command':json.loads((vm.out/'qemu-command.json').read_text()),
                                        'qemu_version':subprocess.check_output(['qemu-system-x86_64','--version'],text=True)})
        qmp=physical.Qmp(vm.private/'qmp.sock',time.monotonic()+15,vm.process)
    calls=[]
    def call(op,arguments=None):
        response=qmp.call(op,arguments);calls.append({'boot':boot,'op':op,'arguments':arguments,'response':response});save(out/'qmp.json',calls);return response
    def add_storage():
        call('device_add',{'driver':'usb-storage','id':'policy-storage','drive':'policy-disk','bus':'policy-xhci.0','port':'1','serial':'RUSTUSB-E67-STORAGE'})
        vm.run('sudo udevadm settle --timeout=30','udev-storage')
    def remove_storage():
        call('device_del',{'id':'policy-storage'});guest('absent')
    try:
        start();vm.upload([HERE/'guest.py',HERE/'fd-probe.c',copy/'arrival_guest.py'])
        vm.run('gcc -std=c11 -O2 -Wall -Wextra -Werror -o /var/tmp/rustusb-installed-fixture/fd-probe /var/tmp/rustusb-installed-fixture/fd-probe.c','compile-probe')
        initial=guest('prepare');assert not any(s.startswith('usbcore.authorized_default=') for s in initial['cmdline'].split())
        assert initial['global_default'] in ('-1','1')
        call('device_add',{'driver':'qemu-xhci','id':'policy-xhci','addr':'0x6'})
        call('blockdev-add',{'driver':'raw','node-name':'policy-disk','read-only':True,
                             'cache':{'direct':False,'no-flush':False},'file':{'driver':'file','filename':str(backing)}})
        add_storage();positive=guest('local');assert any(i['driver']=='usb-storage' for i in positive['interfaces'])
        print(args.release+': real local-storage positive control',flush=True)
        remove_storage();guest('arm');add_storage();guest('descriptors')
        print(args.release+': two actual nonroot descriptor owners completed',flush=True)
        guest('recreate','1');remove_storage();guest('install-boot')
        call('query-status');save(out/'qmp-boot1-events.json',qmp.events)
        qmp.close();qmp=None
        vm.run('sudo systemctl reboot','actual-reboot',expected=(0,255));result['reboot_requested']=True
        while True:
            try:code=vm.process.wait(timeout=45);break
            except subprocess.TimeoutExpired:print(args.release+': original QEMU still live after reboot request',flush=True)
        record_exit(code);save(out/'reboot-exit.json',{'actual_qemu_exit':code,'old_boot_id':initial['boot_id']})
        assert code==0
        for f in vm.handles:f.close()
        vm.handles=[];vm.cold_storage=True
        print(args.release+': original QEMU exited; booting with storage present and boot default deny',flush=True)
        start();second=guest('prepare');assert second['boot_id']!=initial['boot_id']
        guest('cold');guest('recreate','0');guest('arm');guest('authorize');remove_storage()
        result['passed']=True
    except Exception:
        result['failure']=traceback.format_exc();print(result['failure'],flush=True)
    finally:
        if qmp is not None:
            try:call('query-status');save(out/'qmp-final-events.json',qmp.events)
            except Exception:result['qmp_error']=traceback.format_exc();result['passed']=False
            finally:qmp.close()
        if vm.process is not None and vm.process.poll() is None:
            try:
                with (out/'guest-evidence.tar').open('wb') as f:
                    p=subprocess.run(vm.ssh+['sudo tar -C /var/tmp/rustusb-storage-policy -cf - .'],stdout=f,stderr=subprocess.PIPE)
                (out/'collection.stderr').write_bytes(p.stderr);result['collection_exit']=p.returncode
                if p.returncode:result['passed']=False
            except Exception:result['collection_error']=traceback.format_exc();result['passed']=False
        record_exit(vm.close())
        serial=(vm.out/'serial.log').read_bytes() if (vm.out/'serial.log').exists() else b''
        for i,start in enumerate(serial_starts):
            (out/f'boot-{i+1}-serial.log').write_bytes(serial[start:serial_starts[i+1] if i+1<len(serial_starts) else len(serial)])
        result['kernel_integrity']=not any(s.encode() in serial for s in [*physical.INTEGRITY_FAILURES,'blocked for more than'])
        result['guards']={'head':git('rev-parse','HEAD')==head,'clean':not git('status','--porcelain'),
                          'source':inputs()==source,'frozen':all(sha(Path(p))==h for p,h in frozen.items())}
        result['backing_zero_after_actual_exit']=backing.read_bytes()==bytes(64*1024*1024)
        result['passed']=result['passed'] and result['qemu_exits']==[0,0] and result.get('collection_exit')==0 and result['kernel_integrity'] and all(result['guards'].values()) and result['backing_zero_after_actual_exit']
        save(out/'result.json',result)
    print(json.dumps(result),flush=True)
    if not result['passed']:raise SystemExit(1)
if __name__=='__main__':main()
