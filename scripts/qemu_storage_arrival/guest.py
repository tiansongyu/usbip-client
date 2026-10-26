#!/usr/bin/env python3
"""Guest-only upstream Native storage mechanism; never a product admission path."""
import errno
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

HERE = Path('/var/tmp/rustusb-installed-fixture')
OUT = Path('/var/tmp/rustusb-storage-arrival')
SERIAL = 'RUSTUSB-E66-BOT'
commands = []


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + '\n')


def read(path):
    return Path(path).read_text().strip()


def run(args, expected=(0,)):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    commands.append({'argv':list(map(str,args)), 'exit':result.returncode,
                     'stdout':result.stdout, 'stderr':result.stderr})
    save('commands-' + str(os.getpid()) + '.json', commands)
    assert result.returncode in expected, commands[-1]
    return result.stdout


def roots():
    return {p.name:{'path':str(p.resolve()), 'parent':str(p.resolve().parent),
                    'identity':[p.stat().st_dev,p.stat().st_ino],
                    'parent_identity':[p.resolve().parent.stat().st_dev,p.resolve().parent.stat().st_ino],
                    'authorized_default':read(p/'authorized_default'),
                    'interface_authorized_default':read(p/'interface_authorized_default')}
            for p in sorted(Path('/sys/bus/usb/devices').glob('usb*'))}


def target():
    items = [p for p in Path('/sys/bus/usb/devices').iterdir()
             if (p/'serial').is_file() and read(p/'serial') == SERIAL]
    assert len(items) == 1, [p.name for p in items]
    return items[0]


def generation(p):
    stat = p.stat()
    return {'path':str(p.resolve()), 'dev':stat.st_dev, 'ino':stat.st_ino,
            'busnum':read(p/'busnum'), 'devnum':read(p/'devnum')}


def disk_rows(p):
    rows = []
    for block in sorted(Path('/sys/class/block').iterdir()):
        if block.resolve().is_relative_to(p.resolve()):
            rows.append({'name':block.name, 'path':str(block.resolve()),
                         'partition':(block/'partition').exists(), 'size':read(block/'size'),
                         'ro':read(block/'ro'), 'scsi':(block/'device').resolve().name,
                         'sg':[p.name for p in Path('/sys/class/scsi_generic').glob('sg*')
                               if (p/'device').resolve()==(block/'device').resolve()]})
    return rows


def snapshot():
    p = target()
    interfaces = []
    for interface in sorted(Path('/sys/bus/usb/devices').glob(p.name+':*')):
        interfaces.append({'name':interface.name, 'authorized':read(interface/'authorized'),
                           'number':int(read(interface/'bInterfaceNumber'),16),
                           'driver':(interface/'driver').resolve().name if (interface/'driver').exists() else None})
    return {'generation':generation(p), 'bus':p.name, 'authorized':read(p/'authorized'),
            'configuration':read(p/'bConfigurationValue'),
            'driver':(p/'driver').resolve().name if (p/'driver').exists() else None,
            'interfaces':interfaces, 'blocks':disk_rows(p), 'roots':roots(),
            'usbip_status':read(p/'usbip_status') if (p/'usbip_status').exists() else None}


def source_check(label):
    state = snapshot()
    baseline = json.loads((OUT/'source-generation.json').read_text())
    assert state['generation'] == baseline
    fence = json.loads((OUT/'fence.json').read_text())
    for name in fence['selected']:
        assert state['roots'][name]['authorized_default'] == '0'
        assert state['roots'][name]['interface_authorized_default'] == '0'
        assert state['roots'][name]['path'] == fence['after'][name]['path']
        assert state['roots'][name]['identity'] == fence['after'][name]['identity']
        assert state['roots'][name]['parent_identity'] == fence['after'][name]['parent_identity']
    for name, row in fence['before'].items():
        assert state['roots'][name] == row
    assert not state['blocks'], state
    assert all(i['authorized']=='0' and i['driver'] is None for i in state['interfaces']), state
    sockets=run(['ss','-Htne','state','established','src','192.0.2.1','and','sport','=',':3240','and','dst','192.0.2.2'])
    state['tcp_raw']=sockets;state['tcp_connections']=[]
    for line in sockets.splitlines():
        fields=line.split();match=re.search(r'\bino:(\d+)\b',line)
        assert len(fields)>=4 and match is not None,line
        state['tcp_connections'].append({'local':fields[2],'peer':fields[3],'inode':int(match.group(1))})
    save('source-'+label+'.json',state)
    return state


def prepare(role):
    assert role in ('server','client')
    mac = '52:54:00:59:00:' + ('21' if role=='server' else '22')
    interfaces = [p.name for p in Path('/sys/class/net').iterdir() if read(p/'address')==mac]
    assert len(interfaces)==1
    interface = interfaces[0]
    run(['ip','link','set',interface,'up'])
    run(['ip','addr','add','192.0.2.'+('1' if role=='server' else '2')+'/24','dev',interface])
    for module in (['usbip_host','usb_storage','uas'] if role=='server' else ['vhci_hcd','usb_storage','sg']):
        run(['modprobe',module])
    result = {'role':role,'roots':roots(),'kernel':run(['uname','-a']),
              'packages':run(['dpkg-query','-W','sg3-utils','linux-tools-'+read('/proc/sys/kernel/osrelease')]),
              'usbip':run(['usbip','version']), 'route':run(['ip','-j','route','get','192.0.2.'+('2' if role=='server' else '1')])}
    files=[Path('/usr/lib/linux-tools')/read('/proc/sys/kernel/osrelease')/name for name in ['usbip','usbipd']]
    files += [Path('/usr/bin/sg_sync'),HERE/'claim-denied',HERE/'claim-denied.c']
    result['tool_files']={str(p):{'resolved':str(p.resolve()),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                                'bytes':p.stat().st_size} for p in files}
    result['tool_identity_scope']='file bytes; only the long usbipd additionally records a live /proc/PID/exe hash'
    result['boot_id']=read('/proc/sys/kernel/random/boot_id')
    save('prepared.json',result)
    return result


def fence():
    before=json.loads((OUT/'prepared.json').read_text())['roots']
    for _ in range(200):
        current=roots();selected=sorted(set(current)-set(before))
        if len(selected)==2:break
        time.sleep(.05)
    assert len(selected)==2 and len({current[n]['parent'] for n in selected})==1
    assert not any((p/'serial').exists() for n in selected for p in Path(current[n]['path']).iterdir())
    for name in selected:
        base=Path('/sys/bus/usb/devices')/name
        (base/'authorized_default').write_text('0')
        (base/'interface_authorized_default').write_text('0')
    after=roots()
    assert all(after[n]['authorized_default']=='0' and after[n]['interface_authorized_default']=='0' for n in selected)
    assert all(after[n]==v for n,v in before.items())
    result={'before':before,'selected':selected,'after':after};save('fence.json',result)
    return result


def bind():
    for _ in range(200):
        try:p=target();break
        except AssertionError:time.sleep(.05)
    else:raise RuntimeError('unauthorized synthetic USB arrival not observed')
    f=json.loads((OUT/'fence.json').read_text())
    assert any(p.resolve().is_relative_to(Path(f['after'][n]['path'])) for n in f['selected'])
    save('source-generation.json',generation(p))
    initial=source_check('unauthorized-arrival')
    assert initial['authorized']=='0' and not initial['interfaces']
    run(['usbip','bind','-b',p.name])
    bound=source_check('bound-unauthorized');assert bound['driver']=='usbip-host' and bound['authorized']=='0'
    assert generation(p)==initial['generation']
    (p/'authorized').write_text('1')
    authorized=source_check('bound-authorized')
    assert authorized['authorized']=='1' and authorized['driver']=='usbip-host'
    return authorized


def serve():
    log=(OUT/'usbipd.log').open('wb')
    proc=subprocess.Popen(['usbipd','-4'],stdout=log,stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            assert proc.poll() is None
            if run(['ss','-Hltn','sport','=',':3240']).strip():break
            time.sleep(.05)
        else:raise RuntimeError('usbipd listener not ready')
        identity={'pid':proc.pid,'exe_sha256':hashlib.sha256(Path('/proc/'+str(proc.pid)+'/exe').read_bytes()).hexdigest(),
                  'stat':read('/proc/'+str(proc.pid)+'/stat')}
        save('daemon-ready.json',identity)
        while proc.poll() is None:time.sleep(.1)
        code=proc.wait();save('daemon-exit.json',{'identity':identity,'exit':code})
        assert code==-signal.SIGKILL
        return {'actual_usbipd_exit':code}
    finally:
        if proc.poll() is None:proc.terminate();proc.wait()
        log.close()


def kill_daemon():
    info=json.loads((OUT/'daemon-ready.json').read_text())
    assert read('/proc/'+str(info['pid'])+'/stat').split(') ')[1].split()[19]==info['stat'].split(') ')[1].split()[19]
    assert hashlib.sha256(Path('/proc/'+str(info['pid'])+'/exe').read_bytes()).hexdigest()==info['exe_sha256']
    os.kill(info['pid'],signal.SIGKILL)
    return {'signal_sent':signal.SIGKILL,'pid':info['pid']}


def await_daemon():
    for _ in range(200):
        if (OUT/'daemon-ready.json').exists():break
        time.sleep(.05)
    else:raise RuntimeError('root observer did not see daemon readiness')
    info=json.loads((OUT/'daemon-ready.json').read_text())
    assert hashlib.sha256(Path('/proc/'+str(info['pid'])+'/exe').read_bytes()).hexdigest()==info['exe_sha256']
    assert run(['ss','-Hltn','sport','=',':3240']).strip()
    return info


def vhci():
    return {str(p):p.read_text() for p in sorted(Path('/sys/devices/platform').glob('vhci_hcd.*/status*'))}


def attach(bus):
    assert bus and all(c in '0123456789-.' for c in bus)
    run(['usbip','attach','-r','192.0.2.1','-b',bus])
    for _ in range(200):
        try:
            p=target();rows=disk_rows(p)
            if len(rows)==2 and all(len(r['sg'])==1 and (Path('/dev')/r['name']).exists()
                                    and (Path('/dev')/r['sg'][0]).exists() for r in rows):break
        except AssertionError:pass
        time.sleep(.05)
    else:raise RuntimeError('two real LUNs did not enumerate')
    state=snapshot();state['vhci']=vhci()
    assert sorted(int(r['scsi'].split(':')[-1]) for r in rows)==[0,1]
    assert all(r['size']=='131072' and not r['partition'] for r in rows)
    assert sorted(r['ro'] for r in rows)==['0','1']
    save('client-held.json',state)
    return state


def direct_read(node,offset,length):
    fd=os.open(node,os.O_RDONLY|os.O_DIRECT|os.O_CLOEXEC)
    buffer=mmap.mmap(-1,length)
    try:
        assert os.preadv(fd,[buffer],offset)==length
        return buffer[:]
    finally:buffer.close();os.close(fd)


def io(cycle):
    state=snapshot();saved=json.loads((OUT/'client-held.json').read_text())
    assert state['generation']==saved['generation'] and state['blocks']==saved['blocks']
    rows={int(r['scsi'].split(':')[-1]):r for r in state['blocks']}
    assert set(rows)=={0,1} and rows[0]['ro']=='1' and rows[1]['ro']=='0'
    assert all(r['size']=='131072' and not r['partition'] for r in rows.values())
    payload=(('RUSTUSB-E66-LUN1-CYCLE-'+str(cycle)+'\n').encode()*200)[:4096]
    assert len(payload)==4096
    offset=(cycle+1)*1024*1024
    node=Path('/dev')/rows[1]['name'];fd=os.open(node,os.O_RDWR|os.O_CLOEXEC)
    try:
        assert os.pwrite(fd,payload,offset)==len(payload)
        os.fsync(fd)
    finally:os.close(fd)
    assert direct_read(node,offset,len(payload))==payload
    ro_data=direct_read(Path('/dev')/rows[0]['name'],offset,len(payload))
    assert ro_data==bytes(len(payload))
    refusals=[]
    try:
        fd=os.open(Path('/dev')/rows[0]['name'],os.O_RDWR|os.O_CLOEXEC)
        try:os.pwrite(fd,payload,offset)
        finally:os.close(fd)
    except OSError as error:refusals.append(error.errno)
    assert refusals==[errno.EROFS],refusals
    for lun,row in rows.items():
        assert len(row['sg'])==1
        run(['sg_sync','--verbose','/dev/'+(row['sg'][0] if lun==0 else row['name'])])
    result={'cycle':cycle,'offset':offset,'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),
            'readback':'fresh O_DIRECT FD and aligned mmap buffer',
            'ro_read_sha256':hashlib.sha256(ro_data).hexdigest(),
            'ro_write_errno':refusals[0],'state':state,'sync_luns':sorted(rows)}
    save('io-'+str(cycle)+'.json',result)
    return result


def empty():
    old=json.loads((OUT/'client-held.json').read_text())
    for _ in range(200):
        rows=[line.split() for value in vhci().values() for line in value.splitlines() if line.startswith(('hs ','ss '))]
        if rows and all(int(row[2])==4 for row in rows) and not Path(old['generation']['path']).exists():break
        time.sleep(.05)
    else:raise RuntimeError('original VHCI not NULL and gone')
    assert not any(p.resolve().is_relative_to(Path(old['generation']['path'])) for p in Path('/sys/class/block').iterdir())
    return {'vhci':vhci(),'old_generation_gone':True,'no_target_blocks':True}


def flush_error():
    state=snapshot();saved=json.loads((OUT/'client-held.json').read_text())
    assert state['generation']==saved['generation'] and state['blocks']==saved['blocks']
    rows={int(r['scsi'].split(':')[-1]):r for r in state['blocks']}
    assert set(rows)=={0,1} and rows[0]['ro']=='1' and rows[1]['ro']=='0'
    assert all(r['size']=='131072' and not r['partition'] for r in rows.values())
    row=rows[1];node=Path('/dev')/row['name']
    write_cache=read(Path('/sys/class/block')/row['name']/'queue/write_cache')
    assert write_cache=='write back',write_cache
    fd=os.open(node,os.O_RDWR|os.O_DIRECT|os.O_CLOEXEC)
    buffer=mmap.mmap(-1,4096);writes=[]
    try:
        for control in [False,True]:
            offset=(8+int(control))*1024*1024
            payload=(('RUSTUSB-E66-FLUSH-'+str(control)+'\n').encode()*200)[:4096]
            assert len(payload)==4096
            buffer[:]=payload
            assert os.pwritev(fd,[buffer],offset)==len(payload)
            # No fsync, FUA, O_SYNC or close before this explicit SCSI command.
            run(['sg_sync','--verbose','--verbose',node],expected=(0,) if control else (11,))
            writes.append({'offset':offset,'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),
                           'control':control,'sync_command':commands[-1]})
            if not control:save('first-flush-failure.json',writes[-1])
            assert direct_read(node,offset,len(payload))==payload
    finally:buffer.close();os.close(fd)
    result={'writes':writes,'write_cache':write_cache,'generation':state['generation'],
            'first_sync_exit':11,'explicit_second_control_exit':0,'clean_release_approved':False}
    save('flush-error.json',result)
    return result


def detach():
    rows=[line.split() for value in vhci().values() for line in value.splitlines() if line.startswith(('hs ','ss ')) and int(line.split()[2])==6]
    assert len(rows)==1
    run(['usbip','detach','-p',str(int(rows[0][1]))])
    return empty()


def reset_link():
    query=['ss','-Htn','state','established','src','192.0.2.1','and','sport','=',':3240','and','dst','192.0.2.2']
    before=run(query).strip().splitlines();assert len(before)==1,before
    peer=before[0].split()[-1];port=int(peer.rsplit(':',1)[1]);assert 0<port<65536
    killed=run(['ss','-K',*query[1:],'and','dport','=',':'+str(port)])
    for _ in range(100):
        after=run(query)
        if not after.strip():break
        time.sleep(.05)
    else:raise RuntimeError('exact original TCP remained')
    assert not run(['ss','-Hltn','sport','=',':3240']).strip()
    return {'before':before,'peer_port':port,'kill_output':killed,'after':after}


def claim():
    state=source_check('claim-probe');assert state['interfaces'] and state['configuration'] not in ('','0')
    p=target();node='/dev/bus/usb/'+read(p/'busnum').zfill(3)+'/'+read(p/'devnum').zfill(3)
    result=json.loads(run([HERE/'claim-denied',node,str(state['interfaces'][0]['number'])]))
    assert result['claim_rc']==-1 and result['errno']==errno.ENODEV
    after=source_check('claim-probe-after')
    assert all(after[k]==state[k] for k in ['generation','configuration','interfaces'])
    return result


def main():
    assert Path(__file__).resolve()==HERE/'guest.py' and os.geteuid()==0
    OUT.mkdir(mode=0o700,exist_ok=True)
    action=sys.argv[1];args=sys.argv[2:]
    operations={'prepare':lambda:prepare(args[0]),'fence':fence,'bind':bind,'serve':serve,
                'snapshot':lambda:source_check(args[0]),'attach':lambda:attach(args[0]),
                'io':lambda:io(int(args[0])),'detach':detach,'empty':empty,'claim':claim,
                'kill-daemon':kill_daemon,'reset-link':reset_link,'await-daemon':await_daemon,
                'flush-error':flush_error}
    result=operations[action]();save('action-'+action+'-'+str(os.getpid())+'.json',result)
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
