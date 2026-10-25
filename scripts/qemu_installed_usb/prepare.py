import argparse,concurrent.futures,json,pathlib,subprocess,sys
from vm import Vm,sha
parser=argparse.ArgumentParser(description='Prepare product-free Ubuntu test bases')
parser.add_argument('--output',type=pathlib.Path,required=True)
parser.add_argument('--images',type=pathlib.Path,required=True)
args=parser.parse_args()
OUT=args.output.resolve();OUT.mkdir(mode=0o700)
ROOT=pathlib.Path(__file__).resolve().parents[2]
HEAD=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
GUEST="""set -eu
test "$(cat /proc/1/comm)" = systemd
test ! -e /usr/bin/rustusb
uname -a
dpkg-query -W systemd dpkg
apt-get -o Acquire::Languages=none -o Acquire::http::Timeout=30 -o Acquire::Retries=2 update
kernel=$(uname -r)
DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::http::Timeout=30 -o Acquire::Retries=2 install --no-install-recommends -y linux-tools-common "linux-tools-$kernel" jq iproute2 kmod usbutils
if ! modinfo vhci_hcd >/dev/null 2>&1 || ! modinfo usbip_host >/dev/null 2>&1 || ! modinfo ftdi_sio >/dev/null 2>&1; then
 DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::http::Timeout=30 -o Acquire::Retries=2 install --no-install-recommends -y "linux-modules-extra-$kernel"
fi
for module in usbip_host vhci_hcd usbhid ftdi_sio xhci_pci; do modinfo "$module"; modprobe "$module"; done
/usr/bin/usbip version
dpkg-query -W "linux-tools-$kernel" "linux-modules-$kernel" "linux-modules-extra-$kernel" || true
test ! -e /usr/bin/rustusb
test ! -e /etc/rustusb/policy.json
"""
def one(release):
 folder=args.images.resolve()/release
 receipt=json.loads((folder/'receipt.json').read_text());image=folder/receipt['filename']
 assert receipt['signature_verified'] and sha(image)==receipt['sha256']
 out=OUT/release;vm=Vm(out,image,'prepare-'+release);result={'release':release,'passed':False,'source_head':HEAD,'official_image':receipt}
 script=vm.private/'prepare.sh';script.write_text(GUEST)
 try:
  vm.boot();vm.upload([script]);vm.run('sudo sh /var/tmp/rustusb-installed-fixture/prepare.sh','prepare-os')
  result['passed']=True
 finally:
  result['qemu_exit']=vm.close();result['head_guard']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==HEAD
  result['clean_guard']=not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip()
  result['derived_disk_sha256']=sha(vm.disk);result['private_derived_disk']=str(vm.disk)
  result['passed']=result['passed'] and result['qemu_exit']==0 and result['head_guard'] and result['clean_guard']
  (out/'preparation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:result[k] for k in ['release','passed','qemu_exit','head_guard','clean_guard','derived_disk_sha256']}),flush=True)
 return result
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(one,['jammy','noble']))
(OUT/'summary.json').write_text(json.dumps(results,indent=2)+'\n')
if not all(x['passed'] for x in results):sys.exit(1)
