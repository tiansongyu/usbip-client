#!/usr/bin/env python3
"""Fixed, guest-root test fixture. Never installed as a product interface."""
import argparse
import glob
import hashlib
import json
import os
from pathlib import Path
import pwd
import select
import stat
import struct
import subprocess
import termios
import time

HERE=Path('/var/tmp/rustusb-installed-fixture')
CONFIG=Path('/etc/rustusb')
UNITS=['rustusb-agent.service','rustusb-helper.service']

def run(args, expected=(0,)):
    p=subprocess.run(args,capture_output=True,text=True)
    if p.returncode not in expected:
        raise RuntimeError(json.dumps({'argv':args,'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr}))
    return p.stdout

def read(path):
    p=Path(path)
    return json.loads(p.read_text()) if p.exists() else None

def write(path,value,mode=0o644,uid=0,gid=0):
    p=Path(path);p.write_text(json.dumps(value,indent=2)+'\n');p.chmod(mode);os.chown(p,uid,gid)

def agent(*args,uid=1000,expected=(0,)):
    return json.loads(run(['setpriv','--reuid='+str(uid),'--regid='+str(uid),'--clear-groups',
        '/usr/bin/rustusb','agent','--config','/etc/rustusb/agent.json',*args],expected))

def wait_agent():
    for _ in range(200):
        try:
            data=agent('profiles')
            assert data['journal_healthy'] and data['response']['result']=='profiles'
            return data
        except (RuntimeError,AssertionError):
            time.sleep(.05)
    raise RuntimeError('authenticated agent IPC not ready')

def stat_info(path):
    p=Path(path);s=p.stat()
    return {'device':s.st_dev,'inode':s.st_ino,'uid':s.st_uid,'gid':s.st_gid,
            'mode':stat.S_IMODE(s.st_mode),'rdev':s.st_rdev}

def host_baseline(keys):
    result=[]
    for key in keys:
        bus=key['bus_id'];p=Path('/sys/bus/usb/devices')/bus
        node=Path('/dev/bus/usb')/f"{int((p/'busnum').read_text()):03d}"/f"{int((p/'devnum').read_text()):03d}"
        interfaces=[]
        for interface in sorted(p.glob(bus+':*')):
            interfaces.append({'name':interface.name,'alternate':(interface/'bAlternateSetting').read_text().strip(),
                'driver':(interface/'driver').resolve().name if (interface/'driver').exists() else None})
        result.append({'bus':bus,'generation':stat_info(p),'configuration':(p/'bConfigurationValue').read_text().strip(),
            'interfaces':interfaces,'node':str(node),'node_metadata':stat_info(node)})
    return result

def vhci():
    rows=[]
    for p in sorted(Path('/sys/devices/platform').glob('vhci_hcd.*/status*')):
        rows.append({'path':str(p),'text':p.read_text()})
    return rows

def unit_status():
    result={}
    for name in UNITS:
        fields=['MainPID','ControlPID','ActiveState','SubState','Result','ExecMainCode','ExecMainStatus',
            'InvocationID','ControlGroup','ExecMainExitTimestampMonotonic','InactiveEnterTimestampMonotonic',
            'KillMode','KillSignal','SendSIGHUP','SendSIGKILL','TimeoutStopUSec','Restart']
        data=dict(line.split('=',1) for line in run(['systemctl','show',name,'--property='+','.join(fields)]).splitlines())
        cgroup=Path('/sys/fs/cgroup'+data['ControlGroup']) if data['ControlGroup'] else None
        data['cgroup_processes']=[int(x) for p in (cgroup.rglob('cgroup.procs') if cgroup and cgroup.exists() else []) for x in p.read_text().split()]
        result[name]=data
    return result

def snapshot():
    keys=read(HERE/'keys.json') or []
    locals=[]
    for r in (read('/var/lib/rustusb/native/channels.json') or {}).get('records',[]):
        receipt=r.get('state',{}).get('receipt')
        if receipt:
            local=receipt['session']['local_bus_id'];p=Path('/sys/bus/usb/devices')/local
            locals.append({'bus':local,'generation':stat_info(p) if p.exists() else None,
                           'driver':(p/(local+':1.0')/'driver').resolve().name if (p/(local+':1.0')/'driver').exists() else None,
                           'events':[x.name for x in p.rglob('event*') if x.name[5:].isdigit()],
                           'ttys':[x.name for x in p.rglob('ttyUSB*') if x.name[6:].isdigit()]})
    return {'uid':pwd.getpwnam('rustusb').pw_uid,'units':unit_status(),
        'app':read('/var/lib/rustusb-agent/state.json'),
        'native':read('/var/lib/rustusb/native/channels.json'),
        'physical':read('/var/lib/rustusb/physical/journal.json'),
        'host':host_baseline(keys) if keys else None,'vhci':vhci(),'locals':locals}

def setup(role,backend,family):
    uid=pwd.getpwnam('rustusb').pw_uid;gid=pwd.getpwnam('rustusb').pw_gid
    mac='52:54:00:59:00:'+('21' if role=='server' else '22')
    interface=next(p.name for p in Path('/sys/class/net').iterdir() if (p/'address').read_text().strip()==mac)
    run(['ip','link','set',interface,'up'])
    for address in ['192.0.2.'+('1' if role=='server' else '2')+'/24','fd42:7275:7374::'+('1' if role=='server' else '2')+'/64']:
        run(['ip','addr','add',address,'dev',interface])
    for mod in ['usbip_host','vhci_hcd','usbhid','ftdi_sio']:run(['modprobe',mod])
    for name in ['ca.der','server.der','client.der',role+'-key.der']:
        target=CONFIG/name
        target.write_bytes((HERE/name).read_bytes())
        target.chmod(0o600 if 'key' in name else 0o644);os.chown(target,uid if 'key' in name else 0,gid if 'key' in name else 0)
    if role=='server':
        devices=json.loads(run(['/usr/bin/rustusb','--json','devices']))
        keys=[]
        for vid,pid in [(1575,1),(1027,24577)]:
            matching=[d for d in devices if d['vendor_id']==vid and d['product_id']==pid]
            assert len(matching)==1 and matching[0]['export_blockers']==[]
            keys.append(matching[0])
        write(HERE/'keys.json',keys)
        device_keys=[('native:' if backend=='native' else 'physical:')+d['id'] for d in keys]
        policy={'allowed_uids':[],'export_device_ids':[],'peers':[],'trusted_network':False,
            'secure_broker_uid':uid,('secure_native_device_ids' if backend=='native' else 'secure_physical_device_ids'):[d['id'] for d in keys]}
        listen=('192.0.2.1:3241' if family==4 else '[fd42:7275:7374::1]:3241')
        config={'listen':listen,'devices':device_keys,'principals':[{'id':'qemu-client',
            'pins':[hashlib.sha256((CONFIG/'client.der').read_bytes()).hexdigest()],'devices':device_keys}]}
        profiles=[{'id':'server','backend':backend,'config':'/etc/rustusb/server.json'}]
        config.update({'schema_version':2,'data_link':{'protocol':'framed_v2','interval_millis':1000,'response_timeout_millis':3000},
            'broker_uid':uid,'ca_certificates':['/etc/rustusb/ca.der'],'certificate_chain':['/etc/rustusb/server.der'],'private_key':'/etc/rustusb/server-key.der'})
        write(CONFIG/'server.json',config)
        write(HERE/'device-keys.json',device_keys)
    else:
        device_keys=read(HERE/'device-keys.json')
        policy={'allowed_uids':[],'export_device_ids':[],'peers':[],'trusted_network':False,
            'secure_broker_uid':uid,'secure_imports':[{'server_id':'qemu-server','device_keys':device_keys}]}
        profiles=[]
        for name,key in zip(['hid','serial'],device_keys):
            config={'schema_version':2,'data_link':{'protocol':'framed_v2','interval_millis':1000,'response_timeout_millis':3000},
                'broker_uid':uid,'server_id':'qemu-server','endpoint':{'host':'192.0.2.1' if family==4 else 'fd42:7275:7374::1','port':3241},
                'server_name':'rustusb-qemu.test','device':key,'ca_certificates':['/etc/rustusb/ca.der'],
                'certificate_chain':['/etc/rustusb/client.der'],'private_key':'/etc/rustusb/client-key.der'}
            write(CONFIG/('client-'+name+'.json'),config)
            profiles.append({'id':name,'backend':'client','config':'/etc/rustusb/client-'+name+'.json'})
    write(CONFIG/'policy.json',policy)
    write(CONFIG/'agent.json',{'schema_version':1,'broker_uid':uid,'helper_socket':'/run/rustusb/control.sock',
        'control_directory':'/run/rustusb-agent','data_directory':'/var/lib/rustusb-agent','profiles':profiles,
        'operators':[{'uid':1000,'profiles':[p['id'] for p in profiles],'actions':['read','start','stop','discover','audit']}]})
    return {'role':role,'backend':backend,'family':family,'uid':uid,'device_keys':device_keys,
        'baseline':host_baseline(keys) if role=='server' else None,'vhci':vhci(),
        'versions':run(['dpkg-query','-W','systemd','dpkg','rustusb'])+run(['uname','-a'])+run(['usbip','version']),
        'units':{name:run(['systemctl','cat',name]) for name in UNITS},
        'public_config':{p.name:read(p) for p in CONFIG.glob('*.json')},
        'elfs':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path('/usr/bin/rustusb'),Path('/usr/libexec/rustusb/rustusb-helper')]}}

def start():
    run(['systemctl','start','rustusb-agent.service'])
    return {'profiles':wait_agent(),'snapshot':snapshot()}

def jobs(action,profiles):
    responses=[agent(action,p) for p in profiles]
    for response in responses:
        assert response['journal_healthy'] and response['response']['result']==('started' if action=='start' else 'stop_accepted')
    return responses

def ready(role):
    for _ in range(300):
        all_sessions=[s for p in (['server'] if role=='server' else ['hid','serial']) for s in agent('sessions',p)['response']['sessions']]
        live=[s for s in all_sessions if not s['terminal']]
        desired=[s for s in live if s['role']=='export' and s['state']=='Available'] if role=='server' else [s for s in live if s['state']=='Connected' and s['evidence']=='local_enumeration']
        if len(desired)==2:return {'sessions':all_sessions,'snapshot':snapshot()}
        time.sleep(.1)
    raise RuntimeError('actual session readiness observation limit; owners still require cleanup')

def probe(cycle):
    snap=snapshot();locals=snap['locals']
    assert len(locals)==2
    hid=next(x for x in locals if x['driver']=='usbhid')
    serial=next(x for x in locals if x['driver']=='ftdi_sio')
    event='/dev/input/'+next(iter(set(hid['events'])))
    tty='/dev/'+next(iter(set(serial['ttys'])))
    fd=os.open(tty,os.O_RDWR|os.O_NOCTTY|os.O_NONBLOCK)
    attrs=termios.tcgetattr(fd);attrs[0]=0;attrs[1]=0;attrs[2]=termios.CS8|termios.CLOCAL|termios.CREAD;attrs[3]=0
    attrs[4]=termios.B57600;attrs[5]=termios.B57600;attrs[6][termios.VMIN]=0;attrs[6][termios.VTIME]=0
    termios.tcsetattr(fd,termios.TCSANOW,attrs)
    eventfd=os.open(event,os.O_RDONLY|os.O_NONBLOCK)
    write(HERE/'probe-ready.json',{'cycle':cycle,'event':event,'tty':tty})
    deadline=time.monotonic()+20;rx=b'';events=[]
    try:
        while time.monotonic()<deadline:
            r,_,_=select.select([fd,eventfd],[],[],.2)
            if fd in r:rx+=os.read(fd,4096)
            if eventfd in r:
                data=os.read(eventfd,4096)
                assert len(data)%24==0
                events.extend([list(struct.unpack('llHHI',data[i:i+24]))[2:] for i in range(0,len(data),24)])
            if len(rx)>=35 and [1,cycle+1,1] in events and [1,cycle+1,0] in events:break
        assert rx==f'RUSTUSB-RX-{cycle}-0001:0123456789abcdef\n'.encode(),repr(rx)
        assert [1,cycle+1,1] in events and [1,cycle+1,0] in events,events
        tx=f'RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n'.encode()
        assert os.write(fd,tx)==35
        return {'cycle':cycle,'rx_bytes':len(rx),'tx_bytes':len(tx),'events':events,'locals':locals}
    finally:os.close(fd);os.close(eventfd)

def stopped():
    snap=snapshot()
    for name,s in snap['units'].items():
        assert s['MainPID']=='0' and s['ControlPID']=='0' and s['ActiveState']=='inactive' and s['SubState']=='dead'
        assert s['Result']=='success' and s['ExecMainCode'] in ['0','1'] and s['ExecMainStatus']=='0' and s['cgroup_processes']==[]
    return snap

def main():
    p=argparse.ArgumentParser();p.add_argument('action');p.add_argument('args',nargs='*');a=p.parse_args()
    if a.action=='setup':result=setup(a.args[0],a.args[1],int(a.args[2]))
    elif a.action=='start-services':result=start()
    elif a.action=='start-jobs':result=jobs('start',a.args)
    elif a.action=='ready':result=ready(a.args[0])
    elif a.action=='snapshot':result=snapshot()
    elif a.action=='probe':result=probe(int(a.args[0]))
    elif a.action=='denied':
        before=snapshot();result=agent('start',a.args[0],uid=1002,expected=(1,));after=snapshot()
        assert result['response']=={'result':'error','code':'permission'}
        assert before['native']==after['native'] and before['physical']==after['physical'] and before['host']==after['host'] and before['vhci']==after['vhci']
    elif a.action=='stop-services':
        run(['systemctl','stop','rustusb-agent.service','rustusb-helper.service']);result=stopped()
    elif a.action=='upgrade':
        before=unit_status()
        output=run(['dpkg','-i',str(HERE/a.args[0])])
        result={'before':before,'dpkg_stdout':output,'after':stopped(),'version':run(['dpkg-query','-W','rustusb']),
            'journal':run(['journalctl','-u','rustusb-agent','-u','rustusb-helper','-b','--no-pager','-o','json'])}
    elif a.action=='discover':result=agent('discover','hid')
    else:raise RuntimeError('unknown fixture action')
    print(json.dumps(result))
if __name__=='__main__':main()

