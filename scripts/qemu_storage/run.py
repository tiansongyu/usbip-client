#!/usr/bin/env python3
"""Inspect synthetic storage in one disposable full Ubuntu guest; no host device access."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[2]
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
    parser=argparse.ArgumentParser();parser.add_argument('--prepared',type=Path,required=True);parser.add_argument('--release',choices=['jammy','noble'],required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(mode=0o700)
    assert not git('status','--porcelain')
    head=git('rev-parse','HEAD');source=inputs()
    image=args.prepared/args.release/'.private/disk.qcow2'
    prep=json.loads((args.prepared/args.release/'preparation.json').read_text());assert prep['passed'] and sha(image)==prep['derived_disk_sha256']
    elfs=[ROOT/'target/release/rustusb',ROOT/'target/release/rustusb-helper']
    frozen={str(p):sha(p) for p in [image,*elfs]}
    save(out/'provenance.json',{'head':head,'inputs':source,'frozen':frozen,'prepared':prep})
    vm=Vm(out/'guest',image,'storage')
    result={'passed':False,'head':head,'release':args.release}
    qmp_records=[]
    try:
        vm.boot()
        # Dependencies are installed only in this disposable guest, before product execution.
        vm.run('set -e\nsudo systemctl stop apt-daily.timer apt-daily-upgrade.timer\nsudo env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 install -y gcc dmsetup','guest-dependencies')
        qmp=physical.Qmp(vm.private/'qmp.sock',time.monotonic()+10,vm.process)
        def call(op,arguments):
            response=qmp.call(op,arguments);qmp_records.append({'op':op,'arguments':arguments,'response':response});save(out/'qmp.json',qmp_records);return response
        try:
            call('device_add',{'driver':'qemu-xhci','id':'storage-xhci'})
            call('device_add',{'driver':'usb-bot','id':'storage-bot','bus':'storage-xhci.0','serial':'RUSTUSB-E65-BOT'})
            for lun in [0,1]:
                image_lun=vm.private/f'lun{lun}.raw'
                with image_lun.open('xb') as f:f.truncate(64*1024*1024)
                call('blockdev-add',{'driver':'raw','node-name':f'lun{lun}','read-only':lun==0,'file':{'driver':'file','filename':str(image_lun)}})
                call('device_add',{'driver':'scsi-hd','id':f'scsi-lun{lun}','bus':'storage-bot.0','scsi-id':0,'lun':lun,'drive':f'lun{lun}'})
            call('qom-set',{'path':'/machine/peripheral/storage-bot','property':'attached','value':True})
        finally:qmp.close()
        vm.upload([*elfs,ROOT/'scripts/qemu_storage/guest.py',ROOT/'scripts/qemu_storage/hold.c'])
        vm.run('set -e\nchmod 755 /var/tmp/rustusb-installed-fixture/rustusb /var/tmp/rustusb-installed-fixture/rustusb-helper\nsudo python3 /var/tmp/rustusb-installed-fixture/guest.py','inspection')
        result['guest_result']=json.loads(vm.run('sudo cat /var/tmp/rustusb-storage-evidence/result.json','result'))
        assert result['guest_result']['passed']
        result['passed']=True
    except Exception:
        result['failure']=traceback.format_exc();print(result['failure'],flush=True)
    finally:
        if vm.process is not None and vm.process.poll() is None:
            try:
                # Only the explicit synthetic evidence directory is collected, never private images/keys.
                with (out/'guest-evidence.tar').open('wb') as f:
                    p=subprocess.run(vm.ssh+['sudo tar -C /var/tmp/rustusb-storage-evidence -cf - .'],stdout=f,stderr=subprocess.PIPE)
                result['collection_exit']=p.returncode
                if p.returncode!=0:result['passed']=False
            except Exception:result['collection_error']=traceback.format_exc();result['passed']=False
        result['qemu_exit']=vm.close()
        if result['qemu_exit']!=0:result['passed']=False
        result['guards']={'head':git('rev-parse','HEAD')==head,'clean':not git('status','--porcelain'),'source':inputs()==source,'frozen':all(sha(Path(p))==h for p,h in frozen.items())}
        if not all(result['guards'].values()):result['passed']=False
        serial=(vm.out/'serial.log').read_text(errors='replace')
        result['kernel_integrity']=not any(text in serial for text in physical.INTEGRITY_FAILURES)
        if not result['kernel_integrity']:result['passed']=False
        save(out/'result.json',result)
    print(json.dumps(result),flush=True)
    if not result['passed']:raise SystemExit(1)
if __name__=='__main__':main()
