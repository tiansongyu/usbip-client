#!/usr/bin/env python3
"""Native Tauri/WebKit IPC and real agent refusal in a disposable, NIC-free QEMU guest."""
import argparse,base64,datetime,gzip,hashlib,importlib.util,json,os,re,shutil,subprocess,sys,tempfile,time
from pathlib import Path
sys.dont_write_bytecode=True
REPO=Path(__file__).resolve().parents[2];OWN=Path(__file__).resolve().parent

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
secure=module('gui_secure_payload',REPO/'scripts/qemu/run-secure-qemu.py');native=secure.native
physical=module('gui_qmp',REPO/'scripts/qemu/run-physical-qemu.py')
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def git(*args):return subprocess.check_output(['git',*args],cwd=REPO,text=True).strip()
def inputs():
    names=set(secure.inputs())
    for base in [REPO/'apps/rustusb-gui',OWN]:
        for path in base.rglob('*'):
            if path.is_file() and not {'target','gen','__pycache__'}.intersection(path.relative_to(base).parts):names.add(str(path.relative_to(REPO)))
    names.add('scripts/check-gui.sh')
    return {name:{'bytes':(REPO/name).stat().st_size,'sha256':digest(REPO/name)}for name in sorted(names)}
def command(argv,path):
    with path.open('wb')as stream:subprocess.run(argv,cwd=REPO,stdout=stream,stderr=subprocess.STDOUT,check=True)
def copy_tree(source,root):
    source=Path(source)
    for path in source.rglob('*'):
        out=root/str(path).lstrip('/')
        if path.is_dir():out.mkdir(parents=True,exist_ok=True)
        elif path.is_file():
            out.parent.mkdir(parents=True,exist_ok=True)
            if path.read_bytes()[:4]==b'\x7fELF':native.copy_binary(path,'/'+str(out.relative_to(root)),root)
            else:shutil.copy2(path,out,follow_symlinks=True)
def payload(work,kernel,driver,evidence):
    credentials=secure.credentials(work,evidence);secure.payload(work,kernel,credentials,'server');root=work/'root'
    for source,target in [(REPO/'apps/rustusb-gui/target/release/rustusb-gui','/usr/bin/rustusb-gui'),(driver,'/usr/bin/WebKitWebDriver')]:native.copy_binary(source,target,root)
    for name in ['Xvfb','xkbcomp','curl','bwrap','xdg-dbus-proxy','dbus-daemon','xauth','xdotool']:
        source=shutil.which(name)
        if not source:raise RuntimeError('missing runtime utility: '+name)
        native.copy_binary(source,'/usr/bin/'+name,root)
    for tree in ['/usr/lib/x86_64-linux-gnu/webkit2gtk-4.1','/usr/lib/x86_64-linux-gnu/gio/modules','/usr/share/fonts/truetype/dejavu','/etc/fonts','/usr/share/glib-2.0/schemas','/usr/share/X11/xkb','/etc/dbus-1','/usr/share/dbus-1/session.conf']:
        path=Path(tree)
        if path.is_dir():copy_tree(path,root)
        elif path.is_file():
            target=root/tree.lstrip('/');target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    # EGL vendors/software DRI are dlopen dependencies, absent from ordinary ldd output.
    copy_tree('/usr/share/glvnd/egl_vendor.d',root)
    for library in ['/usr/lib/x86_64-linux-gnu/libEGL_mesa.so.0','/usr/lib/x86_64-linux-gnu/libGLX_mesa.so.0','/usr/lib/x86_64-linux-gnu/dri/swrast_dri.so','/usr/lib/x86_64-linux-gnu/libEGL.so.1','/usr/lib/x86_64-linux-gnu/libGLESv2.so.2','/usr/lib/x86_64-linux-gnu/libGL.so.1','/usr/lib/x86_64-linux-gnu/libglapi.so.0']:
        native.copy_binary(library,library,root)
    # Fontconfig's relative includes need their dereferenced conf.avail targets.
    copy_tree('/usr/share/fontconfig',root)
    (root/'etc/passwd').write_text('root:x:0:0:root:/:/bin/sh\noperator:x:1000:1000:operator:/home/operator:/bin/sh\nbroker:x:2000:2000:broker:/:/sbin/nologin\n')
    (root/'init').write_bytes((OWN/'guest-init.sh').read_bytes());(root/'init').chmod(0o755)
    (root/'usr/bin/gui-launcher').write_text('''#!/bin/sh
/usr/bin/rustusb-gui "$@" >/tmp/gui-app.stdout 2>/tmp/gui-app.stderr &
pid=$!; echo "$pid" >/tmp/gui-pid.txt
wait "$pid"; result=$?; echo "$result" >/tmp/gui-wait.txt
exit "$result"
''');(root/'usr/bin/gui-launcher').chmod(0o755)
    subprocess.run(['dash','-n',str(root/'init')],check=True)
    shutil.copyfile(root/'init',evidence/'generated-init.sh')
    files={str(p.relative_to(root)):{'bytes':p.stat().st_size,'sha256':digest(p)}for p in root.rglob('*')if p.is_file()and not p.is_symlink()and p.read_bytes()[:4]==b'\x7fELF'}
    (evidence/'runtime-elfs.json').write_text(json.dumps(files,indent=2)+'\n')
    names=b'.\0'+b'\0'.join(str(p.relative_to(root)).encode()for p in root.rglob('*'))+b'\0'
    archive=work/'gui-initramfs.cpio.gz'
    with (work/'gui-initramfs.cpio').open('wb')as output:
        packed=subprocess.run(['cpio','--null','-o','--format=newc','--owner=0:0'],input=names,cwd=root,stdout=output,stderr=subprocess.PIPE,check=True)
    (evidence/'cpio.stderr').write_bytes(packed.stderr)
    with (work/'gui-initramfs.cpio').open('rb')as source,gzip.open(archive,'wb',compresslevel=1)as output:shutil.copyfileobj(source,output)
    return archive

def verify(evidence,summary):
    log=(evidence/'guest-evidence.log').read_text();assert 'RUSTUSB_GUI_RESULT: PASS'in log and 'RUSTUSB_GUI_FORCED_CHILD'not in log
    out=evidence/'guest-artifacts';out.mkdir();records=[];total=0
    for match in re.finditer(r'RUSTUSB_GUI_FILE_BEGIN name=([A-Za-z0-9_.-]+) bytes=(\d+) sha256=([0-9a-f]{64})\n(.*?)RUSTUSB_GUI_FILE_END name=\1\n',log,re.S):
        name,size,sha,encoded=match.groups();data=base64.b64decode(''.join(encoded.split()),validate=True);total+=len(data)
        assert len(records)<100 and len(data)==int(size)<=2097152 and total<=16*1024*1024 and digest_bytes(data)==sha
        assert not (out/name).exists();(out/name).write_bytes(data);records.append({'name':name,'bytes':len(data),'sha256':sha})
    read=lambda name:json.loads((out/('gui-'+name+'.json')).read_text())['value']
    assert read('initial-dom')['bridge']and read('initial-dom')['url'].startswith('tauri://localhost/')
    assert read('strict-input')['rejected']
    before,after=read('added')['local']['devices'][0],read('readded')['local']['devices'][0]
    assert before['key']!=after['key'] and before['address']!=after['address']
    assert read('removed')['local']['devices']==[]
    assert 'No devices match this filter'in read('search-empty')
    assert 'Select an observed device'in read('detail-removed')
    assert 'device_address'in read('detail-before') and 'device_address'in read('detail-after')
    original=read('denied')['unresolved'];assert original==read('after-renderer-reload')['unresolved']==read('retried')['unresolved']
    state=json.loads((out/'gui-agent-after.json').read_text());assert state['jobs']==state['sessions']==state['requests']==[]
    assert len(state['audit'])==2 and all(r['actor_uid']==1000 and r['action']=='denied'and r['failure']=='permission'for r in state['audit'])
    assert read('confirm-default')=={'open':True,'focus':'cancel'}
    assert (out/'gui-preferences-stat.txt').read_text().strip()=='1000:600:1'
    prefs=json.loads((out/'gui-saved-preferences.json').read_text());assert prefs['options']['refresh_seconds']==3 and not prefs['options']['auto_refresh']
    assert (out/'gui-wait.txt').read_text().strip()=='1'and (out/'gui-agent-wait.txt').read_text().strip()=='0'
    for phase in ['initial','before-stop']:
        actor=(out/f'gui-actor-{phase}.txt').read_text();assert re.search(r'^Uid:\s+1000\s+1000\s+1000\s+1000$',actor,re.M)
        assert all(re.search(r'^'+cap+r':\s+0000000000000000$',actor,re.M)for cap in ['CapInh','CapPrm','CapEff','CapAmb'])
        assert summary['binaries']['rustusb-gui']in actor and 'TARGET=/dev/bus/usb/'not in actor
    for name in ['initial','added','denied','narrow']:
        data=base64.b64decode(read(name+'-screenshot'));assert data.startswith(b'\x89PNG\r\n\x1a\n');(evidence/(name+'.png')).write_bytes(data)
    (evidence/'decoded-files.json').write_text(json.dumps(records,indent=2)+'\n')
    return {'files':len(records),'bytes':total,'scope':'actual native WebView, read/audit-only real agent, no accepted USB job or data-redirection proof'}
def digest_bytes(data):return hashlib.sha256(data).hexdigest()
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--driver',type=Path,required=True);parser.add_argument('--evidence',type=Path,required=True);parser.add_argument('--kernel',default=os.uname().release);parser.add_argument('--timeout',type=int,default=300);args=parser.parse_args()
    assert os.geteuid()!=0 and os.uname().machine=='x86_64'and re.fullmatch(r'[A-Za-z0-9._+-]{1,128}',args.kernel)and 60<=args.timeout<=900
    evidence=args.evidence.resolve();evidence.mkdir(exist_ok=False,parents=True)
    binaries={'rustusb':REPO/'target/release/rustusb','rustusb-helper':REPO/'target/release/rustusb-helper','rustusb-gui':REPO/'apps/rustusb-gui/target/release/rustusb-gui'}
    summary={'result':'FAIL','head':git('rev-parse','HEAD'),'status':git('status','--porcelain'),'source_inputs':inputs(),'binaries':{n:digest(p)for n,p in binaries.items()},'host_uid':os.getuid(),'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'forced_termination':False};process=None;qmp=None
    try:
        assert summary['status']=='','clean committed source required'
        for name in summary['source_inputs']:
            target=evidence/'source'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(REPO/name,target)
        command(['qemu-system-x86_64','--version'],evidence/'qemu-version.txt')
        with tempfile.TemporaryDirectory(prefix='rustusb-native-gui-')as directory:
            work=Path(directory);kernel=work/'vmlinuz'
            try:shutil.copyfile(Path('/boot')/('vmlinuz-'+args.kernel),kernel)
            except PermissionError:
                subprocess.run(['sudo','-n','cp','/boot/vmlinuz-'+args.kernel,str(kernel)],check=True);subprocess.run(['sudo','-n','chmod','644',str(kernel)],check=True)
            archive=payload(work,args.kernel,args.driver.resolve(),evidence)
            summary.update(kernel_sha256=digest(kernel),initramfs_sha256=digest(archive),driver_sha256=digest(args.driver))
            acceleration=['-enable-kvm','-cpu','host']if os.access('/dev/kvm',os.R_OK|os.W_OK)else['-cpu','max']
            argv=['qemu-system-x86_64','-m','4096','-smp','2',*acceleration,'-kernel',str(kernel),'-initrd',str(archive),'-append','console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.gui=native','-display','none','-serial',f'file:{evidence/"guest-kernel.log"}','-serial',f'file:{evidence/"guest-evidence.log"}','-monitor','none','-no-reboot','-nic','none','-device','qemu-xhci,id=usb','-qmp',f'unix:{work/"qmp.sock"},server=on,wait=off'];summary['qemu_command']=argv
            with (evidence/'qemu.stderr').open('wb')as stream:
                process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=stream);summary['qemu_pid']=process.pid;deadline=time.monotonic()+args.timeout;qmp=physical.Qmp(work/'qmp.sock',deadline,process);sent=set();commands=[]
                while time.monotonic()<deadline:
                    log=(evidence/'guest-evidence.log').read_text(errors='replace')if(evidence/'guest-evidence.log').exists()else''
                    if 'RUSTUSB_GUI_RESULT: FAIL'in log:raise RuntimeError('actual guest failure, originals retained')
                    for stage,cmd,arguments in [('add','device_add',{'driver':'usb-kbd','id':'gui-kbd','bus':'usb.0','port':'1'}),('remove','device_del',{'id':'gui-kbd'}),('readd','device_add',{'driver':'usb-kbd','id':'gui-kbd','bus':'usb.0','port':'1'})]:
                        if f'RUSTUSB_GUI_REQUEST: {stage}\n'in log and stage not in sent:
                            if stage=='readd':
                                until=time.monotonic()+5
                                while not any(r.get('event')=='DEVICE_DELETED'and r.get('data',{}).get('device')=='gui-kbd'for r in qmp.events):
                                    assert time.monotonic()<until;qmp.read(.05)
                            commands.append({'stage':stage,'command':cmd,'arguments':arguments,'return':qmp.call(cmd,arguments)});sent.add(stage)
                    if process.poll()is not None:break
                    qmp.read(0);time.sleep(.05)
                else:raise TimeoutError('owned guest deadline expired')
                summary['qemu_wait']=process.wait(timeout=5);assert summary['qemu_wait']==0 and len(sent)==3
                (evidence/'qmp-commands.json').write_text(json.dumps(commands,indent=2)+'\n');(evidence/'qmp-events.json').write_text(json.dumps(qmp.events,indent=2)+'\n')
                summary['verification']=verify(evidence,summary);summary['result']='PASS'
    except BaseException as error:summary.update(error_type=type(error).__name__,error=str(error));raise
    finally:
        if qmp:qmp.close()
        if process and process.poll()is None:
            summary['forced_termination']=True;process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            summary['qemu_wait']=process.returncode
        if process:summary['qemu_wait']=process.wait()
        summary['guards']={'sources':inputs()==summary['source_inputs'],'head':git('rev-parse','HEAD')==summary['head'],'status':git('status','--porcelain')==summary['status'],'binaries':all(digest(p)==summary['binaries'][n]for n,p in binaries.items())}
        if not all(summary['guards'].values()):summary['result']='FAIL'
        (evidence/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    assert summary['result']=='PASS';print('PASS: native GUI/real-agent denial, actual child joins, no USB ownership')
if __name__=='__main__':main()
