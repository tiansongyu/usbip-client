#!/usr/bin/env python3
"""Disposable guest controller-policy experiments; no product authorization."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

HERE=Path('/var/tmp/rustusb-installed-fixture')
OUT=Path('/var/tmp/rustusb-storage-policy')
PCI='0000:00:06.0'
SERIAL='RUSTUSB-E67-STORAGE'
spec=importlib.util.spec_from_file_location('arrival',HERE/'arrival_guest.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
base.OUT=OUT;base.SERIAL=SERIAL
read=base.read;run=base.run

def save(name,value):base.save(name,value)
def selected_roots():
    roots={n:r for n,r in base.roots().items() if Path(r['parent']).name==PCI}
    assert len(roots)==2,roots
    return roots

def node(p):return Path('/dev/bus/usb')/read(p/'busnum').zfill(3)/read(p/'devnum').zfill(3)

def snapshot(label):
    value=base.snapshot();p=base.target();n=node(p);st=n.stat()
    assert stat.S_ISCHR(st.st_mode) and os.major(st.st_rdev)==189
    value.update({'boot_id':read('/proc/sys/kernel/random/boot_id'),'node':str(n),
                  'node_stat':{'dev':st.st_dev,'ino':st.st_ino,'rdev':st.st_rdev,'uid':st.st_uid,
                               'gid':st.st_gid,'mode':stat.S_IMODE(st.st_mode)},
                  'acl':{k:os.getxattr(n,k).hex() for k in os.listxattr(n) if 'posix_acl' in k}})
    save(label+'.json',value);return value

def await_state(label,authorized,local):
    for _ in range(200):
        try:
            s=snapshot(label)
            if s['authorized']==str(authorized) and bool(s['blocks'])==local:
                if local and not all(r['size']=='131072' and r['ro']=='1' and not r['partition'] for r in s['blocks']):
                    raise RuntimeError('unexpected synthetic backing geometry')
                return s
        except (FileNotFoundError,AssertionError):pass
        time.sleep(.05)
    raise RuntimeError('expected actual storage state not reached: '+label)

def prepare():
    for m in ['usbip_host','usb_storage','uas']:run(['modprobe',m])
    rules=Path('/etc/udev/rules.d')
    first=rules/'10-rustusb-e67-usbfs.rules';last=rules/'72-rustusb-e67-usbfs.rules'
    prefix='SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", KERNELS=="'+PCI+'", '
    contents={first:prefix+'OWNER:="root", GROUP:="root", MODE:="0600"\n',last:prefix+'TAG-="uaccess"\n'}
    for p,text in contents.items():
        if p.exists():assert p.read_text()==text
        else:p.write_text(text);p.chmod(0o644)
    run(['udevadm','control','--reload'])
    data={'kernel':run(['uname','-a']),'boot_id':read('/proc/sys/kernel/random/boot_id'),
          'cmdline':read('/proc/cmdline'),'global_default':read('/sys/module/usbcore/parameters/authorized_default'),
          'udev_version':run(['udevadm','--version']),'packages':run(['dpkg-query','-W','udev','gcc','linux-tools-'+read('/proc/sys/kernel/osrelease')]),
          'fixture_rules':{str(p):p.read_text() for p in contents},
          'stock_rules':{str(p):p.read_text() for p in [Path('/usr/lib/udev/rules.d/50-udev-default.rules'),Path('/usr/lib/udev/rules.d/73-seat-late.rules')]},
          'tool_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [HERE/'fd-probe',HERE/'fd-probe.c',HERE/'guest.py',HERE/'arrival_guest.py']}}
    save('prepare-'+data['boot_id']+'.json',data);return data

def arm():
    roots=selected_roots()
    for n in roots:
        p=Path('/sys/bus/usb/devices')/n
        (p/'authorized_default').write_text('0');(p/'interface_authorized_default').write_text('0')
    after=selected_roots();assert all(r['authorized_default']==r['interface_authorized_default']=='0' for r in after.values())
    result={'before':roots,'after':after};save('armed-'+read('/proc/sys/kernel/random/boot_id')+'.json',result);return result

def absent():
    for _ in range(200):
        matches=[p for p in Path('/sys/bus/usb/devices').iterdir() if (p/'serial').exists() and read(p/'serial')==SERIAL]
        if not matches:return {'target_absent':True}
        time.sleep(.05)
    raise RuntimeError('original synthetic USB remained')

def no_local(s):
    assert not s['blocks'] and all(i['authorized']=='0' and i['driver'] is None for i in s['interfaces']),s

def fd_cohort(name,stages):
    initial=snapshot('cohort-'+name+'-initial');n=Path(initial['node']);p=base.target()
    assert initial['node_stat']['uid']==0 and initial['node_stat']['gid']==0 and initial['node_stat']['mode']==0o600
    identity=base.generation(p);rows=[]
    log=(OUT/('probe-'+name+'.stderr')).open('wb')
    proc=subprocess.Popen([str(HERE/'fd-probe'),str(n)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True)
    exit_info={}
    try:
        ready=json.loads(proc.stdout.readline());assert ready['phase']=='ready' and ready['pid']==proc.pid
        assert ready['uid']==ready['gid']==65534 and ready['groups']==ready['cap_eff']==0
        st=read('/proc/'+str(proc.pid)+'/stat');exe=Path('/proc')/str(proc.pid)/'exe'
        receipt={'ready':ready,'stat':st,'status':read('/proc/'+str(proc.pid)+'/status'),
                 'exe_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'generation':identity}
        assert receipt['exe_sha256']==hashlib.sha256((HERE/'fd-probe').read_bytes()).hexdigest()
        assert [ready[k] for k in ['node_dev','node_ino','node_rdev']]==[initial['node_stat'][k] for k in ['dev','ino','rdev']]
        save('probe-'+name+'-identity.json',receipt)
        for label,mutation,expected_rw,expected_fresh in stages:
            mutation()
            state=snapshot('cohort-'+name+'-'+label);assert state['generation']==identity;no_local(state)
            assert read('/proc/'+str(proc.pid)+'/stat').split(') ')[1].split()[19]==st.split(') ')[1].split()[19]
            proc.stdin.write('probe\n');proc.stdin.flush();row=json.loads(proc.stdout.readline())
            row.update({'label':label,'state':state});rows.append(row);save('probe-'+name+'-observations.json',rows)
            assert row['pid']==proc.pid and (row['fresh_open_rc'],row['fresh_open_errno'])==expected_fresh,row
            assert (row['held_ro']['rc'],row['held_ro']['errno'])==(-1,1),row
            if expected_rw==18:
                assert row['held_rw']['rc']==18 and row['held_rw']['errno']==0,row
                assert row['held_rw']['length']==18 and row['held_rw']['type']==1
                assert row['held_rw']['vid']==int(read(p/'idVendor'),16) and row['held_rw']['pid']==int(read(p/'idProduct'),16)
            else:assert (row['held_rw']['rc'],row['held_rw']['errno'])==(-1,expected_rw),row
        return {'identity':receipt,'observations':rows}
    finally:
        try:
            proc.stdin.write('quit\n');proc.stdin.flush();proc.stdin.close()
            exit_info['remaining_stdout']=proc.stdout.read()
        except BrokenPipeError:exit_info['pipe_closed']=True
        exit_info['actual_exit']=proc.wait();log.close();proc.stdout.close();save('probe-'+name+'-exit.json',exit_info)
        assert exit_info['actual_exit']==0,exit_info

def descriptors():
    initial=await_state('fd-initial',0,False);assert initial['driver']=='usb'
    assert all(r['authorized_default']==r['interface_authorized_default']=='0' for r in selected_roots().values())
    n=Path(initial['node']);p=base.target();bus=p.name
    a=fd_cohort('pre-bind',[
        ('delegated-open',lambda:n.chmod(0o666),18,(0,0)),
        ('chmod-closed',lambda:n.chmod(0o600),18,(-1,13)),
        ('native-bound',lambda:run(['usbip','bind','-b',bus]),19,(-1,13))])
    assert snapshot('post-bind')['driver']=='usbip-host'
    b=fd_cohort('post-bind',[
        ('fresh-while-bound',lambda:None,18,(-1,13)),
        ('device-authorized',lambda:(p/'authorized').write_text('1'),18,(-1,13)),
        ('device-deauthorized',lambda:(p/'authorized').write_text('0'),18,(-1,13)),
        ('native-unbound',lambda:run(['usbip','unbind','-b',bus]),18,(-1,13))])
    final=snapshot('fd-final');assert final['driver']=='usb' and final['authorized']=='0';no_local(final)
    return {'pre_bind':a,'post_bind':b,'final':final}

def recreate(expected):
    before=snapshot('before-hcd-'+expected);roots=selected_roots();pcipath=Path('/sys/bus/pci/devices')/PCI
    st=pcipath.stat();driver=(pcipath/'driver').resolve();assert driver.name=='xhci_hcd'
    (driver/'unbind').write_text(PCI)
    absent()
    assert not any(Path(r['parent']).name==PCI for r in base.roots().values())
    (driver/'bind').write_text(PCI)
    after=await_state('after-hcd-'+expected,int(expected),expected=='1');new=selected_roots()
    assert (pcipath.stat().st_dev,pcipath.stat().st_ino)==(st.st_dev,st.st_ino)
    assert before['generation']!=after['generation']
    assert {tuple(r['identity']) for r in roots.values()}.isdisjoint({tuple(r['identity']) for r in new.values()})
    assert all(r['authorized_default']==expected and r['interface_authorized_default']=='1' for r in new.values())
    return {'before':before,'old_roots':roots,'after':after,'new_roots':new,'old_receipt_invalid':True}

def install_boot_policy():
    assert not any(t.startswith('usbcore.authorized_default=') for t in read('/proc/cmdline').split())
    p=Path('/etc/default/grub.d/99-rustusb-e67-storage.cfg');assert not p.exists()
    p.write_text('GRUB_CMDLINE_LINUX="${GRUB_CMDLINE_LINUX} usbcore.authorized_default=0"\n');p.chmod(0o644)
    run(['update-grub'])
    lines=[s.strip() for s in Path('/boot/grub/grub.cfg').read_text().splitlines() if s.strip().startswith(('linux ','linux\t'))]
    assert lines and all('usbcore.authorized_default=0' in line.split() for line in lines),lines
    return {'fragment':p.read_text(),'generated_linux_lines':lines,'old_boot_id':read('/proc/sys/kernel/random/boot_id')}

def cold():
    tokens=read('/proc/cmdline').split();assert [t for t in tokens if t.startswith('usbcore.authorized_default=')]==['usbcore.authorized_default=0']
    assert read('/sys/module/usbcore/parameters/authorized_default')=='0'
    s=await_state('cold-arrival',0,False);assert not s['interfaces']
    assert all(r['authorized_default']=='0' and r['interface_authorized_default']=='1' for r in selected_roots().values())
    assert s['node_stat']['mode']==0o600 and s['node_stat']['uid']==0 and not s['acl']
    return s

def authorize():
    before=snapshot('before-explicit-authorization');assert before['authorized']=='0';no_local(before)
    assert all(r['authorized_default']==r['interface_authorized_default']=='0' for r in selected_roots().values())
    p=base.target();(p/'authorized').write_text('1')
    after=await_state('after-explicit-authorization',1,False);assert before['generation']==after['generation']
    assert after['interfaces'];no_local(after)
    return {'before':before,'after':after}

def main():
    assert Path(__file__).resolve()==HERE/'guest.py' and os.geteuid()==0
    OUT.mkdir(mode=0o700,exist_ok=True);action=sys.argv[1]
    ops={'prepare':prepare,'arm':arm,'absent':absent,'descriptors':descriptors,'install-boot':install_boot_policy,
         'cold':cold,'authorize':authorize,'local':lambda:await_state('local-positive',1,True),
         'recreate':lambda:recreate(sys.argv[2])}
    result=ops[action]();save('action-'+action+'-'+str(os.getpid())+'.json',result);print(json.dumps(result),flush=True)
if __name__=='__main__':main()
