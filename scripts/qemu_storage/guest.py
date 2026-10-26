#!/usr/bin/env python3
"""Disposable root guest only; synthetic USB ancestry checked before every write."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import sys
import time

HERE=Path('/var/tmp/rustusb-installed-fixture')
OUT=Path('/var/tmp/rustusb-storage-evidence')
RUN=Path('/run/rustusb-storage-test')
CLI=HERE/'rustusb'
HELPER=HERE/'rustusb-helper'
records=[]
children=[]

def save(name,data):
    (OUT/name).write_text(json.dumps(data,indent=2)+'\n')

def command(args,expected=(0,),input=None):
    p=subprocess.run([str(a) for a in args],input=input,capture_output=True,text=True)
    records.append({'argv':[str(a) for a in args],'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
    save('commands.json',records)
    if p.returncode not in expected:raise RuntimeError(f'command exit {p.returncode}: {args}')
    return p.stdout

def cli(*args,uid=0,expected=(0,)):
    prefix=[] if uid==0 else ['setpriv','--reuid='+str(uid),'--regid='+str(uid),'--clear-groups']
    return command([*prefix,CLI,'--json','--socket',RUN/'control.sock',*args],expected)

def snapshot(bus):
    p=Path('/sys/bus/usb/devices')/bus;s=p.stat()
    return {'generation':[s.st_dev,s.st_ino],'devnum':(p/'devnum').read_text().strip(),
        'config':(p/'bConfigurationValue').read_text().strip(),
        'driver':(p/'driver').resolve().name,
        'interfaces':{i.name:(i/'driver').resolve().name for i in sorted(p.glob(bus+':*'))}}

def device_number(path):
    s=Path(path).stat();return {'major':os.major(s.st_rdev),'minor':os.minor(s.st_rdev)}

def inspect(label):
    data=json.loads(cli('storage-inspect',target['id']))
    assert data['schema_version']==1 and data['device_id']==target['id']
    assert 'not_an_export_permission' in data['limitations']
    assert 'device_write_protection_unknown' in data['limitations']
    assert len(data['luns'])==2
    assert snapshot(target['bus_id'])==baseline
    assert 'RUSTUSB_PRIVATE' not in json.dumps(data)
    save(label+'.json',data)
    return data

def found(data,kind,node,pid=None,tid=None):
    number=device_number(node)
    return any(u['kind']==kind and u['device']==number and (pid is None or u['pid']==pid) and (tid is None or u['tid']==tid) for u in data['uses'])

def holder(mode,node):
    p=subprocess.Popen([str(HERE/'hold'),mode,str(node)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    children.append(p)
    ready,_,_=select.select([p.stdout],[],[],10)
    if not ready:raise RuntimeError('holder readiness timeout')
    tid=int(p.stdout.readline().strip())
    assert p.poll() is None
    save('holder-'+mode+'-'+str(p.pid)+'.json',{'pid':p.pid,'tid':tid,'mode':mode,'exe_sha256':hashlib.sha256(Path('/proc/'+str(p.pid)+'/exe').read_bytes()).hexdigest()})
    return p,tid

def finish(p):
    p.stdin.close();code=p.wait(timeout=15)
    save('child-exit-'+str(p.pid)+'.json',{'pid':p.pid,'exit':code,'stderr':p.stderr.read()})
    assert code==0
    children.remove(p)

def verify_disk(path):
    name=Path(path).name
    p=Path('/sys/class/block')/name
    assert p.resolve().is_relative_to(usb)
    assert (p/'size').read_text().strip()=='131072'
    assert not (p/'partition').exists()
    assert (p/'ro').read_text().strip()=='0'
    assert name==rw.name

OUT.mkdir(mode=0o700)
RUN.mkdir(mode=0o755)
result={'passed':False}
helper=None
mounted=False
swap=False
mapper=False
try:
    assert os.geteuid()==0
    command(['uname','-a'])
    command(['gcc','-O2','-Wall','-Wextra','-Werror','-pthread','-o',HERE/'hold',HERE/'hold.c'])
    devices=json.loads(cli('devices'))
    candidates=[d for d in devices if d.get('serial')=='RUSTUSB-E65-BOT']
    assert len(candidates)==1
    target=candidates[0];usb=(Path('/sys/bus/usb/devices')/target['bus_id']).resolve()
    assert any('storage' in b for b in target['export_blockers'])
    baseline=snapshot(target['bus_id'])
    save('target.json',target);save('baseline.json',baseline)
    disks=[]
    for _ in range(100):
        disks=sorted(p for p in Path('/sys/class/block').iterdir() if p.resolve().is_relative_to(usb) and not (p/'partition').exists())
        if len(disks)==2:break
        time.sleep(.1)
    assert len(disks)==2
    ro=[p for p in disks if (p/'ro').read_text().strip()=='1'];rwlist=[p for p in disks if (p/'ro').read_text().strip()=='0']
    assert len(ro)==len(rwlist)==1
    rw=Path('/dev')/rwlist[0].name;rodev=Path('/dev')/ro[0].name
    verify_disk(rw)
    # Two bounded MBR partitions on the exact disposable 64 MiB second LUN.
    mbr=bytearray(512)
    for i,(start,count,kind) in enumerate([(2048,49152,0x83),(51200,49152,0x82)]):
        mbr[446+16*i:462+16*i]=struct.pack('<B3sB3sII',0,b'\0\0\0',kind,b'\0\0\0',start,count)
    mbr[510:512]=b'\x55\xaa'
    fd=os.open(rw,os.O_RDWR|os.O_CLOEXEC)
    try:os.pwrite(fd,mbr,0);os.fsync(fd);fcntl.ioctl(fd,0x125f)
    finally:os.close(fd)
    part1=Path(str(rw)+'1');part2=Path(str(rw)+'2')
    for _ in range(100):
        if part1.exists() and part2.exists():break
        time.sleep(.1)
    assert part1.exists() and part2.exists()
    policy={'allowed_uids':[0,1000],'export_device_ids':[target['id']],'peers':[],'trusted_network':True}
    (RUN/'policy.json').write_text(json.dumps(policy));(RUN/'policy.json').chmod(0o600)
    log=(OUT/'helper.log').open('wb')
    helper=subprocess.Popen([str(HELPER),'--config',str(RUN/'policy.json'),'--socket',str(RUN/'control.sock'),'--native-state-dir',str(RUN/'native'),'--physical-state-dir',str(RUN/'physical')],stdout=log,stderr=subprocess.STDOUT)
    for _ in range(200):
        if (RUN/'control.sock').exists():break
        assert helper.poll() is None
        time.sleep(.05)
    save('helper-identity.json',{'pid':helper.pid,'exe_sha256':hashlib.sha256(Path('/proc/'+str(helper.pid)+'/exe').read_bytes()).hexdigest(),'cli_sha256':hashlib.sha256(CLI.read_bytes()).hexdigest()})
    idle=inspect('idle')
    assert len(idle['blocks'])==4
    assert any(b['device']==device_number(rodev) and b['kernel_read_only'] is True for b in idle['blocks'])
    denial=cli('storage-inspect',target['id'],uid=1000,expected=(1,))
    assert 'requires root' in denial
    export_denial=cli('export',target['id'],expected=(1,))
    assert 'unsafe' in export_denial.lower()
    assert snapshot(target['bus_id'])==baseline
    for mode,node in [('fd',part2),('mapping',rw),('thread-fd',part2)]:
        p,tid=holder(mode,node)
        data=inspect(mode)
        assert found(data,'mapping' if mode=='mapping' else 'block_fd',node,p.pid,tid)
        if mode=='thread-fd':assert tid!=p.pid and not found(data,'block_fd',node,p.pid,p.pid)
        if mode=='mapping':
            # C closes the original FD; bare-block VMA remains and maps dev is not rdev.
            fds=[os.stat(f) for f in Path('/proc/'+str(p.pid)+'/fd').iterdir()]
            assert all(s.st_rdev!=rw.stat().st_rdev for s in fds)
            save('raw-mapping-maps.txt.json',{'maps':Path('/proc/'+str(p.pid)+'/maps').read_text()})
        finish(p)
    command(['mkfs.ext4','-F',part1])
    mount=RUN/'mount';mount.mkdir()
    command(['mount',part1,mount]);mounted=True
    secret=mount/'RUSTUSB_PRIVATE_FILENAME';secret.write_bytes(b'RUSTUSB_PRIVATE_CONTENT'+b'\0'*4096)
    p,tid=holder('fd',secret)
    data=inspect('mounted-file-fd');assert found(data,'mount',part1) and found(data,'filesystem_fd',part1,p.pid,tid)
    finish(p)
    p,tid=holder('mapping',secret)
    data=inspect('mounted-file-mapping');assert found(data,'mapping',part1,p.pid,tid)
    finish(p)
    command(['umount',mount]);mounted=False
    # An independently owned mount namespace, with actual readiness and clean exit.
    code="import subprocess,sys;subprocess.run(['mount','--make-rprivate','/'],check=True);subprocess.run(['mount',sys.argv[1],sys.argv[2]],check=True);print('ready',flush=True);sys.stdin.read();subprocess.run(['umount',sys.argv[2]],check=True)"
    p=subprocess.Popen(['unshare','--mount','python3','-c',code,str(part1),str(mount)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);children.append(p)
    ready,_,_=select.select([p.stdout],[],[],10);assert ready and p.stdout.readline().strip()=='ready'
    assert os.stat('/proc/'+str(p.pid)+'/ns/mnt').st_ino!=os.stat('/proc/self/ns/mnt').st_ino
    data=inspect('separate-mount-namespace');assert found(data,'mount',part1,p.pid)
    finish(p)
    command(['mkswap',part2]);command(['swapon',part2]);swap=True
    data=inspect('swap');assert found(data,'swap',part2)
    command(['swapoff',part2]);swap=False
    command(['modprobe','dm_crypt'])
    # Disposable plain dm-crypt mapping. Key goes only to stdin, never the record.
    table='0 49152 crypt aes-xts-plain64 '+os.urandom(32).hex()+' 0 '+str(part1)+' 0\n'
    command(['dmsetup','create','rustusb-e65-crypt','--uuid','CRYPT-PLAIN-RUSTUSB-E65'],input=table);mapper=True
    data=inspect('crypt-holder')
    assert any(b['dm_hint']=='crypt' for b in data['blocks']) and found(data,'holder',part1)
    command(['dmsetup','remove','rustusb-e65-crypt']);mapper=False
    final=inspect('released')
    assert not final['busy_observed']
    assert snapshot(target['bus_id'])==baseline
    helper.send_signal(signal.SIGTERM);result['helper_exit']=helper.wait(timeout=30);assert result['helper_exit']==0
    log.close();assert 'RUSTUSB_PRIVATE' not in (OUT/'helper.log').read_text()
    result.update(passed=True,cases=['idle-multi-lun-partitions-ro','root-denial','storage-export-denial','raw-fd','bare-block-mmap','unshared-thread-fd','mount-file-fd','file-mmap','separate-mount-namespace','swap','crypt-holder','released'],final_issues=final['issues'])
finally:
    for p in list(children):
        try:finish(p)
        except Exception as e:result.setdefault('cleanup_errors',[]).append(str(e))
    for active,args in [(mapper,['dmsetup','remove','rustusb-e65-crypt']),(swap,['swapoff',str(locals().get('part2',''))]),(mounted,['umount',str(RUN/'mount')])]:
        if active:
            try:command(args)
            except Exception as e:result.setdefault('cleanup_errors',[]).append(str(e))
    if helper is not None and helper.poll() is None:
        helper.send_signal(signal.SIGTERM)
        result['helper_exit']=helper.wait(timeout=30)
    save('result.json',result)
    print(json.dumps(result),flush=True)
