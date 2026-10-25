"""Private full-Ubuntu QEMU utility for installed-service acceptance."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import time
import uuid

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while chunk := f.read(1024*1024): h.update(chunk)
    return h.hexdigest()

class Vm:
    def __init__(self, out, image, role, link=None, usb=False):
        self.out=Path(out);self.out.mkdir(parents=True)
        self.private=self.out/'.private';self.private.mkdir(mode=0o700)
        self.role=role;self.link=link;self.usb=usb;self.process=None;self.index=0;self.handles=[]
        self.key=self.private/'ssh-key'
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(self.key)],check=True)
        seed=self.private/'seed';seed.mkdir()
        (seed/'meta-data').write_text('instance-id: '+str(uuid.uuid4())+'\nlocal-hostname: rustusb-installed-'+role+'\n')
        (seed/'user-data').write_text('#cloud-config\nssh_pwauth: false\nusers:\n  - name: ubuntu\n    groups: [sudo]\n    sudo: ALL=(ALL) NOPASSWD:ALL\n    shell: /bin/bash\n    lock_passwd: true\n    ssh_authorized_keys:\n      - '+self.key.with_suffix('.pub').read_text().strip()+'\n')
        subprocess.run(['genisoimage','-quiet','-output',str(self.private/'seed.iso'),'-volid','cidata','-joliet','-rock',str(seed/'user-data'),str(seed/'meta-data')],check=True)
        self.disk=self.private/'disk.qcow2'
        subprocess.run(['qemu-img','create','-q','-f','qcow2','-F','qcow2','-b',str(Path(image).resolve()),str(self.disk),'20G'],check=True)
        with socket.socket() as reserve:reserve.bind(('127.0.0.1',0));self.port=reserve.getsockname()[1]
        self.options=['-i',str(self.key),'-p',str(self.port),'-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile='+str(self.private/'known_hosts'),'-o','ConnectTimeout=3','-o','BatchMode=yes']
        self.ssh=['ssh',*self.options,'ubuntu@127.0.0.1']

    def boot(self):
        console=(self.out/'serial.log').open('ab');errors=(self.out/'qemu.stderr').open('ab');self.handles.extend([console,errors])
        args=['qemu-system-x86_64','-enable-kvm','-cpu','host','-m','2048','-smp','2','-display','none','-serial','stdio','-monitor','none','-drive','file='+str(self.disk)+',if=virtio,format=qcow2','-drive','file='+str(self.private/'seed.iso')+',media=cdrom,readonly=on','-netdev',f'user,id=management,hostfwd=tcp:127.0.0.1:{self.port}-:22','-device','virtio-net-pci,netdev=management,mac=52:54:00:59:00:10','-no-reboot']
        if self.link is not None:args+=['-netdev',f'socket,id=data,fd={self.link.fileno()}','-device','e1000,netdev=data,mac=52:54:00:59:00:'+('21' if self.role=='server' else '22'),'-object','filter-dump,id=capture,netdev=data,file='+str(self.out/'boot-data.pcap')]
        args+=['-qmp','unix:'+str(self.private/'qmp.sock')+',server=on,wait=off']
        if self.usb:args+=['-device','qemu-xhci,id=usb','-device','usb-kbd,id=installed-kbd,bus=usb.0,port=1','-chardev','socket,id=ftdi,path='+str(self.private/'ftdi.sock')+',server=on,wait=off','-device','usb-serial,id=installed-ftdi,bus=usb.0,port=2,chardev=ftdi,always-plugged=on']
        (self.out/'qemu-command.json').write_text(json.dumps(args,indent=2)+'\n')
        self.process=subprocess.Popen(args,pass_fds=(() if self.link is None else (self.link.fileno(),)),stdin=subprocess.DEVNULL,stdout=console,stderr=errors)
        self.wait_ssh();self.run('sudo cloud-init status --wait','cloud-init')

    def wait_ssh(self):
        polls=0
        while True:
            result=subprocess.run(self.ssh+['true'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if result.returncode==0:return
            if self.process.poll() is not None:raise RuntimeError('actual QEMU exited before SSH: '+str(self.process.returncode))
            polls+=1
            if polls%30==0:
                with (self.out/'ssh-observations.jsonl').open('a') as f:f.write(json.dumps({'polls':polls,'qemu_pid':self.process.pid,'qemu_still_live':self.process.poll() is None})+'\n')
                print(self.role+': original QEMU remains live; waiting for SSH',flush=True)
            time.sleep(1)

    def run(self, command, label, expected=(0,)):
        self.index+=1;base=self.out/f'{self.index:04d}-{label}'
        base.with_suffix('.started.json').write_text(json.dumps({'command':command,'monotonic':time.monotonic()})+'\n')
        with base.with_suffix('.stdout').open('wb') as stdout,base.with_suffix('.stderr').open('wb') as stderr:result=subprocess.run(self.ssh+[command],stdout=stdout,stderr=stderr)
        base.with_suffix('.json').write_text(json.dumps({'command':command,'exit':result.returncode})+'\n')
        if result.returncode not in expected:raise RuntimeError(f'{self.role} {label} exit {result.returncode}')
        return base.with_suffix('.stdout').read_bytes()

    def upload(self, paths):
        self.run('mkdir -p -m 700 /var/tmp/rustusb-installed-fixture','fixture-directory')
        args=['scp','-q','-i',str(self.key),'-P',str(self.port),'-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile='+str(self.private/'known_hosts'),*[str(p) for p in paths],'ubuntu@127.0.0.1:/var/tmp/rustusb-installed-fixture/']
        with (self.out/'scp.log').open('ab') as log:subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,check=True)

    def close(self):
        if self.process is None:return None
        if self.process.poll() is None:self.run('sudo systemctl poweroff','poweroff',expected=(0,255))
        while True:
            try:code=self.process.wait(timeout=45);break
            except subprocess.TimeoutExpired:print(self.role+': original QEMU still live during cooperative poweroff',flush=True)
        for handle in self.handles:handle.close()
        return code
