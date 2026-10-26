#!/usr/bin/env python3
"""Prepare product-free tool-only parents; no USB fixture or host device changes."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import traceback

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/qemu_installed_usb'))
from vm import Vm,sha


def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--prepared',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(mode=0o700)
    assert not git('status','--porcelain')
    head=git('rev-parse','HEAD')
    names=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    source={n:sha(ROOT/n) for n in names if n and not n.startswith('docs/evidence/')}
    def one(release):
        parent=args.prepared/release/'.private/disk.qcow2'
        upstream=json.loads((args.prepared/release/'preparation.json').read_text())
        assert upstream['passed'] and sha(parent)==upstream['derived_disk_sha256']
        vm=Vm(out/release,parent,'arrival-prepare-'+release)
        result={'passed':False,'head':head,'release':release,'upstream_preparation':upstream,
                'parent_sha256':upstream['derived_disk_sha256'],'inputs':source}
        try:
            vm.boot()
            vm.run('set -e\ntest ! -e /usr/bin/rustusb\nsudo systemctl stop apt-daily.timer apt-daily-upgrade.timer\nsudo env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=120 install -y gcc sg3-utils\ndpkg-query -W gcc sg3-utils\ntest ! -e /usr/bin/rustusb\ntest ! -e /etc/rustusb/policy.json','tool-only-preparation')
            result['passed']=True
        except Exception:result['failure']=traceback.format_exc();print(result['failure'],flush=True)
        finally:
            result['qemu_exit']=vm.close()
            result['derived_disk_sha256']=sha(vm.disk)
            result['guards']={'head':git('rev-parse','HEAD')==head,'clean':not git('status','--porcelain'),
                              'source':all(sha(ROOT/n)==h for n,h in source.items()),
                              'parent':sha(parent)==upstream['derived_disk_sha256']}
            result['passed']=result['passed'] and result['qemu_exit']==0 and all(result['guards'].values())
            (vm.out/'preparation.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({k:result[k] for k in ['passed','head','release','qemu_exit','derived_disk_sha256','guards']}),flush=True)
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(one,['jammy','noble']))
    (out/'summary.json').write_text(json.dumps(results,indent=2)+'\n')
    if not all(r['passed'] for r in results):raise SystemExit(1)


if __name__=='__main__':main()
