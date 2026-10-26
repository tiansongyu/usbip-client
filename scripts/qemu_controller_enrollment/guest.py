#!/usr/bin/env python3
"""Private guest: actual product controller policies, independent kernel observations."""
import hashlib,importlib.util,json,mmap,os,signal,stat,subprocess,sys,time,uuid
from pathlib import Path
HERE=Path('/var/tmp/rustusb-installed-fixture');OUT=Path('/var/tmp/rustusb-controller-enrollment')
BIN=Path('/opt/rustusb-e68');SOCKET='/run/rustusb-e68/control.sock';STATE=Path('/var/lib/rustusb-e68/controllers')
PCI='0000:00:06.0';SERIAL='RUSTUSB-E68-STORAGE';POLICY='10000000-0000-0000-0000-000000000001'
spec=importlib.util.spec_from_file_location('arrival',HERE/'arrival_guest.py');base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
base.OUT=OUT;base.SERIAL=SERIAL
read=base.read;run=base.run;save=base.save

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def boot():return read('/proc/sys/kernel/random/boot_id')
def roots():
    rs={n:r for n,r in base.roots().items() if Path(r['parent']).name==PCI};assert len(rs)==2,rs;return rs

def process(pid):
    p=Path('/proc')/str(pid)
    return {'pid':pid,'stat':read(p/'stat'),'status':read(p/'status'),'exe_sha256':sha(p/'exe'),
            'cmdline':(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(),
            'fds':{f.name:os.readlink(f) for f in (p/'fd').iterdir()},'boot_id':boot()}
def original(info):
    current=process(info['pid']);assert current['boot_id']==info['boot_id'] and current['exe_sha256']==info['exe_sha256']
    assert current['stat'].split(') ')[1].split()[19]==info['stat'].split(') ')[1].split()[19];return current

def setup(role):
    for m in (['usbip_host','usb_storage','uas'] if role=='server' else ['vhci_hcd','usb_storage','sg']):run(['modprobe',m])
    mac='52:54:00:59:00:'+('21' if role=='server' else '22')
    names=[p.name for p in Path('/sys/class/net').iterdir() if read(p/'address')==mac];assert len(names)==1
    run(['ip','link','set',names[0],'up']);run(['ip','addr','replace','192.0.2.'+('1' if role=='server' else '2')+'/24','dev',names[0]])
    if role=='server':
        BIN.mkdir(mode=0o755,exist_ok=True)
        for name in ['rustusb','rustusb-helper']:run(['install','-m','0755',HERE/name,BIN/name])
        conf=Path('/etc/rustusb-e68');conf.mkdir(mode=0o755,exist_ok=True)
        p=conf/'policy.json';p.write_text(json.dumps({'allowed_uids':[65534],'export_device_ids':[],'peers':[],'trusted_network':False}));p.chmod(0o644)
        p=Path('/etc/udev/rules.d/10-rustusb-e68.rules');p.write_text('SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", KERNELS=="'+PCI+'", OWNER:="root", GROUP:="root", MODE:="0600"\n');p.chmod(0o644)
        p=Path('/etc/udev/rules.d/72-rustusb-e68.rules');p.write_text('SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", KERNELS=="'+PCI+'", TAG-="uaccess"\n');p.chmod(0o644)
        run(['udevadm','control','--reload'])
    tools=[Path('/usr/lib/linux-tools')/read('/proc/sys/kernel/osrelease')/n for n in ['usbip','usbipd']]
    result={'role':role,'boot_id':boot(),'kernel':run(['uname','-a']),'cmdline':read('/proc/cmdline'),'global_default':read('/sys/module/usbcore/parameters/authorized_default'),
            'tools':{str(p):sha(p) for p in tools},'product':{n:sha(BIN/n) for n in ['rustusb','rustusb-helper']} if role=='server' else {}}
    save('setup-'+role+'-'+boot()+'.json',result);return result

def serve(kind,label):
    log=(OUT/(label+'.log')).open('wb')
    args=([str(BIN/'rustusb-helper'),'--config','/etc/rustusb-e68/policy.json','--socket',SOCKET,'--physical-state-dir','/var/lib/rustusb-e68/physical','--native-state-dir','/var/lib/rustusb-e68/native','--controller-state-dir',str(STATE)] if kind=='helper' else ['usbipd','-4'])
    proc=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT);info=None
    try:
        for _ in range(200):
            assert proc.poll() is None,(kind,proc.returncode)
            ready=Path(SOCKET).is_socket() if kind=='helper' else bool(run(['ss','-Hltn','sport','=',':3240']).strip())
            if ready:break
            time.sleep(.05)
        else:raise RuntimeError('original child did not become ready')
        info=process(proc.pid);save(label+'-ready.json',info)
        code=proc.wait();receipt={'identity':info,'exit':code};save(label+'-exit.json',receipt)
        assert code in ([0] if kind=='helper' else [0,-signal.SIGTERM]),receipt
        return receipt
    finally:
        if proc.poll() is None:
            proc.terminate();code=proc.wait();save(label+'-exception-exit.json',{'exit':code,'identity':info})
        log.close()

def ready(label):
    for _ in range(200):
        p=OUT/(label+'-ready.json')
        if p.exists():return original(json.loads(p.read_text()))
        time.sleep(.05)
    raise RuntimeError('original child ready receipt absent')
def stop(label):
    info=ready(label);os.kill(info['pid'],signal.SIGTERM);return {'signal':signal.SIGTERM,'identity':info}

def cli(arguments,ok=True,uid=0):
    args=([str(BIN/'rustusb'),'--socket',SOCKET,'--json','controller',*arguments])
    if uid:args=['runuser','-u','nobody','--',*args]
    proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True);stdout,stderr=proc.communicate()
    row={'argv':args,'pid':proc.pid,'exit':proc.returncode,'stdout':stdout,'stderr':stderr,'executable_file_sha256':sha(BIN/'rustusb'),'live_exe_sampled':False}
    base.commands.append(row);save('commands-'+str(os.getpid())+'.json',base.commands)
    assert proc.returncode==(0 if ok else 1),row
    return json.loads(stdout) if ok else row

def inspect():return cli(['inspect',PCI])
def listing():return cli(['list'])
def change(action,label,revision,selection=None,expected_boot=None,ok=True,request=None):
    args=[action,'--policy-id',POLICY,'--request-id',request or str(uuid.uuid4()),'--expected-revision',str(revision),'--expected-boot',expected_boot or boot()]
    if selection is not None:args+=['--selector',json.dumps(selection,separators=(',',':'))]
    save(label+'-request.json',{'arguments':args});return cli(args,ok=ok)

def no_local():
    s=base.snapshot();assert not s['blocks'];assert all(i['authorized']=='0' and i['driver'] is None for i in s['interfaces']);return s

def boot_negative():
    s=inspect();assert not s['boot_default_deny'];assert listing()['historical']['revision']==0
    result=change('enroll','boot-negative',0,s['selector'],ok=False);assert listing()['historical']['revision']==0
    assert all(r['device_default']!=0 for r in inspect()['roots']);return {'inspection':s,'rejection':result}

def install_boot():
    p=Path('/etc/default/grub.d/99-rustusb-e68.cfg');assert not p.exists()
    p.write_text('GRUB_CMDLINE_LINUX="${GRUB_CMDLINE_LINUX} usbcore.authorized_default=0"\n');p.chmod(0o644);run(['update-grub'])
    lines=[s.strip() for s in Path('/boot/grub/grub.cfg').read_text().splitlines() if s.strip().startswith(('linux ','linux\t'))]
    assert lines and all('usbcore.authorized_default=0' in s.split() for s in lines)
    return {'fragment':p.read_text(),'linux_lines':lines,'old_boot':boot()}

def enroll():
    s=inspect();before=no_local();assert before['authorized']=='0' and not before['interfaces'];assert all(r['device_default']==0 and r['interface_default']==1 for r in s['roots'])
    cli(['list'],ok=False,uid=65534);assert listing()['historical']['revision']==0
    wrong=dict(s['selector']);wrong['vendor_id']^=1
    change('enroll','selector-negative',0,wrong,ok=False);change('enroll','cas-negative',99,s['selector'],ok=False)
    assert listing()['historical']['revision']==0
    result=change('enroll','first-enroll',0,s['selector']);assert not result['historical'] and result['record']['outcome']['state']=='armed'
    args=json.loads((OUT/'first-enroll-request.json').read_text())['arguments'];replay=cli(args);assert replay['historical'] and replay['record']==result['record']
    bad=args.copy();bad[bad.index('--expected-revision')+1]='1';cli(bad,ok=False)
    request=args[args.index('--request-id')+1];query=cli(['query','--policy-id',POLICY,'--request-id',request]);assert query['retained_historical_result']==result['record']
    change('retire','nonempty-retire',1,ok=False);assert listing()['historical']['revision']==1
    after=no_local();assert before['generation']==after['generation'] and after['authorized']=='0';assert all(r['authorized_default']==r['interface_authorized_default']=='0' for r in roots().values())
    return {'inspection':s,'before':before,'result':result,'after':after,'query':query,'view':listing()}

def reconcile(label,revision,previous_boot=None):
    before=listing();assert before['historical']['revision']==revision
    s=inspect();after=change('reconcile',label,revision);assert after['record']['revision']==revision+1
    assert after['record']['outcome']['verification']['boot_id']==boot()
    assert all(r['device_default']==r['interface_default']==0 for r in inspect()['roots'])
    return {'before':before,'inspection':s,'result':after,'after':listing(),'device':no_local()}

def restart_state():
    state=listing();assert state['verified_in_this_helper']==[];assert state['historical']['records'][0]['outcome']['state']=='armed';return state

def recreate():
    before=no_local();old=roots();driver=(Path('/sys/bus/pci/devices')/PCI/'driver').resolve();assert driver.name=='xhci_hcd'
    (driver/'unbind').write_text(PCI)
    for _ in range(200):
        if not any(Path(r['parent']).name==PCI for r in base.roots().values()):break
        time.sleep(.05)
    else:raise RuntimeError('old root hubs remain')
    (driver/'bind').write_text(PCI)
    for _ in range(200):
        try:
            new=roots();current=no_local()
            if all((Path(r['path'])/(n[3:]+'-0:1.0')/'driver').exists() for n,r in new.items()):break
        except (AssertionError,FileNotFoundError):pass
        time.sleep(.05)
    else:raise RuntimeError('new root hub interfaces absent')
    assert before['generation']!=current['generation'];assert {tuple(r['identity']) for r in old.values()}.isdisjoint({tuple(r['identity']) for r in new.values()})
    assert all(r['authorized_default']=='0' and r['interface_authorized_default']=='1' for r in new.values())
    return {'before':before,'old_roots':old,'after':current,'new_roots':new}

def stock_bind():
    before=no_local();assert before['authorized']=='0';assert all(r['authorized_default']==r['interface_authorized_default']=='0' for r in roots().values())
    p=base.target();run(['usbip','bind','-b',p.name]);bound=no_local();assert bound['driver']=='usbip-host';(p/'authorized').write_text('1');after=no_local();assert after['driver']=='usbip-host' and after['authorized']=='1';return {'before':before,'bound':bound,'after':after}

def stock_io(bus):
    run(['usbip','attach','-r','192.0.2.1','-b',bus])
    for _ in range(200):
        try:
            s=base.snapshot()
            if len(s['blocks'])==1 and len(s['blocks'][0]['sg'])==1:break
        except (AssertionError,FileNotFoundError):pass
        time.sleep(.05)
    else:raise RuntimeError('original stock storage did not enumerate')
    row=s['blocks'][0];assert row['size']=='131072' and row['ro']=='1' and not row['partition'];node=Path('/dev')/row['name'];sg=Path('/dev')/row['sg'][0]
    data=base.direct_read(node,1024*1024,1024*1024);assert data==bytes(1024*1024);run(['sg_sync',str(sg)])
    return {'state':s,'read_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'sg_sync_exit':0,'vhci':base.vhci()}

def stock_detach():
    rows=[line.split() for v in base.vhci().values() for line in v.splitlines() if line.startswith(('hs ','ss ')) and int(line.split()[2])==6];assert len(rows)==1
    run(['usbip','detach','-p',str(int(rows[0][1]))])
    for _ in range(200):
        if not any((p/'serial').is_file() and read(p/'serial')==SERIAL for p in Path('/sys/bus/usb/devices').iterdir()):break
        time.sleep(.05)
    else:raise RuntimeError('original imported USB remained')
    return {'vhci':base.vhci(),'target_gone':True}

def absent():
    for _ in range(200):
        if not any((p/'serial').is_file() and read(p/'serial')==SERIAL for p in Path('/sys/bus/usb/devices').iterdir()):return {'target_gone':True}
        time.sleep(.05)
    raise RuntimeError('synthetic source USB remained')

def retire():
    absent();before=roots();result=change('retire','empty-retire',4);view=listing();assert result['record']['retired'] and result['record']['outcome']['state']=='retired'
    assert view['historical']['revision']==5 and view['verified_in_this_helper']==[];assert roots()==before
    return {'result':result,'view':view,'roots':before}

def collect():
    files={p.name:{'sha256':sha(p),'mode':stat.S_IMODE(p.stat().st_mode),'uid':p.stat().st_uid,'nlink':p.stat().st_nlink} for p in STATE.iterdir()} if STATE.exists() else {}
    if STATE.exists():save('actual-controller-history.json',json.loads((STATE/'controllers.json').read_text()))
    return {'state_files':files,'boot_id':boot()}

def main():
    assert os.geteuid()==0;OUT.mkdir(mode=0o700,exist_ok=True);op=sys.argv[1];a=sys.argv[2:]
    operations={'setup':lambda:setup(a[0]),'serve':lambda:serve(a[0],a[1]),'ready':lambda:ready(a[0]),'stop':lambda:stop(a[0]),'boot-negative':boot_negative,'install-boot':install_boot,'enroll':enroll,'restart-state':restart_state,'reconcile':lambda:reconcile(a[0],int(a[1])),'recreate':recreate,'stock-bind':stock_bind,'stock-io':lambda:stock_io(a[0]),'stock-detach':stock_detach,'absent':absent,'retire':retire,'collect':collect,'snapshot':no_local}
    result=operations[op]();save('action-'+boot()+'-'+op+'-'+str(os.getpid())+'.json',result);print(json.dumps(result),flush=True)
if __name__=='__main__':main()
