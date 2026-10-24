from pathlib import Path
import urllib.request,json,hashlib,datetime,shutil,subprocess
out=Path('/tmp/rustusb-hotplug-netlink-research-20261024');out.mkdir(exist_ok=False)
urls={
 'linux-5.15-kobject_uevent.c':'https://raw.githubusercontent.com/torvalds/linux/v5.15/lib/kobject_uevent.c',
 'linux-6.8-kobject_uevent.c':'https://raw.githubusercontent.com/torvalds/linux/v6.8/lib/kobject_uevent.c',
 'linux-6.8-af_netlink.c':'https://raw.githubusercontent.com/torvalds/linux/v6.8/net/netlink/af_netlink.c',
 'linux-6.8-usb.c':'https://raw.githubusercontent.com/torvalds/linux/v6.8/drivers/usb/core/usb.c',
 'linux-6.8-message.c':'https://raw.githubusercontent.com/torvalds/linux/v6.8/drivers/usb/core/message.c',
 'systemd-249-device-monitor.c':'https://raw.githubusercontent.com/systemd/systemd/v249/src/libsystemd/sd-device/device-monitor.c',
 'systemd-255-device-monitor.c':'https://raw.githubusercontent.com/systemd/systemd/v255/src/libsystemd/sd-device/device-monitor.c',
 'systemd-249-monitor-private.h':'https://raw.githubusercontent.com/systemd/systemd/v249/src/libsystemd/sd-device/device-monitor-private.h',
 'kobject-uevent-metadata.json':'https://crates.io/api/v1/crates/kobject-uevent',
 'kobject-uevent-0.2.0.crate':'https://static.crates.io/crates/kobject-uevent/kobject-uevent-0.2.0.crate',
 'uevent-metadata.json':'https://crates.io/api/v1/crates/uevent',
 'netlink-sys-metadata.json':'https://crates.io/api/v1/crates/netlink-sys',
 'nlink-metadata.json':'https://crates.io/api/v1/crates/nlink',
 'rustix-metadata.json':'https://crates.io/api/v1/crates/rustix',
 'rustix-issue-1698.json':'https://api.github.com/repos/bytecodealliance/rustix/issues/1698',
 'netlink.7.html':'https://man7.org/linux/man-pages/man7/netlink.7.html',
}
rows=[]
for n,url in urls.items():
 row={'path':n,'url':url}
 try:
  with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'RustUSB-component-research'}),timeout=40) as response:
   b=response.read();row.update(status=response.status,effective_url=response.url,headers=dict(response.headers),bytes=len(b),sha256=hashlib.sha256(b).hexdigest());(out/n).write_bytes(b)
 except Exception as e:row['error']=str(e)
 rows.append(row)
 (out/'fetch.json').write_text(json.dumps({'observed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'requests':rows},indent=2)+'\n')
 print(n,row.get('status'),row.get('error',''))
source=Path('/home/ubuntu/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/rustix-1.1.5')
for n in ['Cargo.toml','Cargo.toml.orig','.cargo_vcs_info.json','LICENSE-MIT','LICENSE-APACHE','COPYRIGHT','src/net/types.rs','src/net/send_recv/mod.rs','src/net/send_recv/msg.rs','src/net/socket.rs','src/net/sockopt.rs','src/backend/linux_raw/net/syscalls.rs','src/backend/libc/net/syscalls.rs']:
 p=out/'rustix-1.1.5-source'/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/n,p)
repo=Path('/home/ubuntu/github/usbip-client');selected=['vendor/nusb/src/platform/linux_usbfs/hotplug.rs','vendor/nusb/src/hotplug.rs','vendor/nusb/src/platform/linux_usbfs/events.rs','vendor/nusb/Cargo.toml','vendor/nusb/LICENSE-APACHE','vendor/nusb/LICENSE-MIT']
for n in selected:
 p=out/'existing-nusb'/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(repo/n,p)
(out/'method-source.py').write_bytes(Path(__file__).read_bytes())
