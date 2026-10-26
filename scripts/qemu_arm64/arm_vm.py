"""ARM64 platform adapter; reuse actual owners and all original guest methods."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

BASE=Path(__file__).resolve().parents[1]/'qemu_installed_usb'
sys.path.insert(0,str(BASE))
from vm import Vm,sha

class ArmVm(Vm):
    """Disposable virt-6.2/TCG guest using explicitly selected extracted tools."""
    tool_root=None

    def boot(self):
        if self.tool_root is None:raise ValueError('explicit ARM tool root required')
        tools=Path(self.tool_root).resolve()
        executable=tools/'usr/bin/qemu-system-aarch64'
        code=tools/'usr/share/AAVMF/AAVMF_CODE.fd'
        template=tools/'usr/share/AAVMF/AAVMF_VARS.fd'
        variables=self.private/'AAVMF_VARS.fd'
        if not variables.exists():shutil.copyfile(template,variables)
        inputs={str(p):{'sha256':sha(p),'bytes':p.stat().st_size} for p in [executable,code,template]}
        (self.out/'arm-platform.json').write_text(json.dumps({'architecture':'aarch64','accelerator':'tcg','machine':'virt-6.2','cpu':'cortex-a72','inputs':inputs},indent=2)+'\n')
        console=(self.out/'serial.log').open('ab');errors=(self.out/'qemu.stderr').open('ab');self.handles.extend([console,errors])
        args=[str(executable),'-machine','virt-6.2,gic-version=3','-accel','tcg,thread=multi','-cpu','cortex-a72','-m','2048','-smp','2','-display','none','-serial','stdio','-monitor','none',
              '-drive','if=pflash,format=raw,readonly=on,file='+str(code),'-drive','if=pflash,format=raw,file='+str(variables),
              '-drive','file='+str(self.disk)+',if=none,id=os,format=qcow2','-device','virtio-blk-pci,drive=os',
              '-drive','file='+str(self.private/'seed.iso')+',if=none,id=seed,format=raw,readonly=on','-device','virtio-blk-pci,drive=seed',
              '-netdev',f'user,id=management,hostfwd=tcp:127.0.0.1:{self.port}-:22','-device','virtio-net-pci,netdev=management,mac=52:54:00:61:00:10','-no-reboot']
        if self.link is not None:args+=['-netdev',f'socket,id=data,fd={self.link.fileno()}','-device','e1000,netdev=data,mac=52:54:00:59:00:'+('21' if self.role=='server' else '22'),'-object','filter-dump,id=capture,netdev=data,file='+str(self.out/'boot-data.pcap')]
        args+=['-qmp','unix:'+str(self.private/'qmp.sock')+',server=on,wait=off']
        if self.usb:args+=['-device','qemu-xhci,id=usb','-device','usb-kbd,id=installed-kbd,bus=usb.0,port=1','-chardev','socket,id=ftdi,path='+str(self.private/'ftdi.sock')+',server=on,wait=off','-device','usb-serial,id=installed-ftdi,bus=usb.0,port=2,chardev=ftdi,always-plugged=on']
        (self.out/'qemu-command.json').write_text(json.dumps(args,indent=2)+'\n')
        self.process=subprocess.Popen(args,pass_fds=(() if self.link is None else (self.link.fileno(),)),stdin=subprocess.DEVNULL,stdout=console,stderr=errors)
        self.wait_ssh()
        self.run('test "$(uname -m)" = aarch64 && test "$(dpkg --print-architecture)" = arm64 && test "$(cat /proc/1/comm)" = systemd && uname -a && dpkg-query -W dpkg systemd','actual-arm-identity')
        self.run('sudo cloud-init status --wait','cloud-init')
