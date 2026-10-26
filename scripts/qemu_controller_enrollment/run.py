#!/usr/bin/env python3
"""Product enrollment/restart/reboot checks plus separately labeled stock Native I/O."""
import argparse,concurrent.futures,importlib.util,json,shlex,shutil,socket,subprocess,sys,time,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'scripts/qemu_installed_usb'))
from vm import Vm,sha
spec=importlib.util.spec_from_file_location('physical',ROOT/'scripts/qemu/run-physical-qemu.py');physical=importlib.util.module_from_spec(spec);spec.loader.exec_module(physical)
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
def inputs():return {n:sha(ROOT/n) for n in subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0') if n and not n.startswith('docs/evidence/')}
class ControllerVm(Vm):
    cold_storage=False
    def boot_device_args(self):
        args=['-device','qemu-xhci,id=controller-xhci,addr=0x6']
        if self.cold_storage:
            block={'driver':'raw','node-name':'controller-disk','read-only':True,'cache':{'direct':False,'no-flush':False},'file':{'driver':'file','filename':str(self.private/'storage.raw')}}
            args+=['-blockdev',json.dumps(block),'-device','usb-storage,id=controller-storage,drive=controller-disk,bus=controller-xhci.0,port=1,serial=RUSTUSB-E68-STORAGE']
        return args

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prepared',type=Path,required=True);ap.add_argument('--server-release',choices=['jammy','noble'],required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    out=a.output.resolve();out.mkdir(mode=0o700);assert not git('status','--porcelain');head=git('rev-parse','HEAD');source=inputs()
    releases={'server':a.server_release,'client':'noble' if a.server_release=='jammy' else 'jammy'}
    parents={role:a.prepared/release/'.private/disk.qcow2' for role,release in releases.items()}
    prep={role:json.loads((a.prepared/release/'preparation.json').read_text()) for role,release in releases.items()}
    for role,p in parents.items():assert prep[role]['passed'] and sha(p)==prep[role]['derived_disk_sha256']
    elfs=[ROOT/'target/release/rustusb',ROOT/'target/release/rustusb-helper',ROOT/'apps/rustusb-gui/target/release/rustusb-gui'];frozen={str(p):sha(p) for p in [*parents.values(),*elfs]}
    save(out/'provenance.json',{'head':head,'inputs':source,'frozen':frozen,'parents':prep,'scope':'actual product controller defaults; stock Native data I/O is not product storage admission'})
    left,right=socket.socketpair();server=ControllerVm(out/'server',parents['server'],'server',link=left);client=Vm(out/'client',parents['client'],'client',link=right);vms={'server':server,'client':client}
    backing=server.private/'storage.raw'
    with backing.open('xb') as f:f.truncate(64*1024*1024)
    pool=concurrent.futures.ThreadPoolExecutor(max_workers=4);children={};observations={};qmp=None;boot=0;calls=[];exits=[];reaped=set();serial_starts=[]
    result={'passed':False,'head':head,'releases':releases}
    upload=out/'fixture-upload';upload.mkdir();shutil.copy(ROOT/'scripts/qemu_storage_arrival/guest.py',upload/'arrival_guest.py')
    def guest(role,op,*args,label=None):
        label=label or '-'.join([op,*map(str,args)])+'-boot'+str(boot)
        raw=vms[role].run('sudo python3 /var/tmp/rustusb-installed-fixture/guest.py '+shlex.join([op,*map(str,args)]),label)
        value=json.loads(raw);observations[role+'-'+label]=value;save(out/'observations.json',observations);return value
    def child_start(kind,label):
        children[label]=(kind,pool.submit(guest,'server','serve',kind,label,label=label+'-owner'))
        return guest('server','ready',label)
    def child_stop(label):
        guest('server','stop',label);kind,future=children.pop(label);value=future.result();assert value['exit'] in ([0] if kind=='helper' else [0,-15]);return value
    def record_exit(role):
        vm=vms[role]
        if vm.process and vm.process.pid not in reaped:
            assert vm.process.poll() is not None;reaped.add(vm.process.pid);exits.append({'role':role,'boot':boot if role=='server' else 1,'pid':vm.process.pid,'exit':vm.process.returncode});save(out/'actual-qemu-exits.json',exits)
    def start_server():
        nonlocal boot,qmp
        boot+=1;serial=server.out/'serial.log';serial_starts.append(serial.stat().st_size if serial.exists() else 0);server.boot()
        save(out/f'server-boot-{boot}.json',{'pid':server.process.pid,'exe_sha256':sha(Path('/proc')/str(server.process.pid)/'exe'),'command':json.loads((server.out/'qemu-command.json').read_text())})
        qmp=physical.Qmp(server.private/'qmp.sock',time.monotonic()+15,server.process)
    def call(op,args=None):
        value=qmp.call(op,args);calls.append({'boot':boot,'op':op,'arguments':args,'response':value});save(out/'qmp.json',calls);return value
    def reboot():
        nonlocal qmp
        call('query-status');save(out/f'qmp-boot-{boot}-events.json',qmp.events);qmp.close();qmp=None
        server.run('sudo systemctl reboot','reboot-'+str(boot),expected=(0,255))
        while True:
            try:code=server.process.wait(timeout=45);break
            except subprocess.TimeoutExpired:print('original server QEMU remains live during reboot',flush=True)
        record_exit('server');assert code==0
        for h in server.handles:h.close()
        server.handles=[];server.cold_storage=True;start_server();guest('server','setup','server')
    try:
        start_server();server.upload([HERE/'guest.py',upload/'arrival_guest.py',*elfs[:2]]);guest('server','setup','server')
        first=child_start('helper','helper-1');guest('server','boot-negative');guest('server','install-boot');child_stop('helper-1')
        print(a.server_release+': boot provenance refusal passed; real reboot',flush=True)
        reboot();second=child_start('helper','helper-2');assert second['boot_id']!=first['boot_id'];enrollment=guest('server','enroll');held=guest('server','ready','helper-2');child_stop('helper-2')
        child_start('helper','helper-3');restart=guest('server','restart-state');assert restart['helper_epoch']!=enrollment['view']['helper_epoch']
        guest('server','reconcile','same-boot',1);replacement=guest('server','recreate');reconciled=guest('server','reconcile','new-hcd',2)
        assert [r['identity'] for r in reconciled['result']['record']['outcome']['verification']['roots']]!=[r['identity'] for r in enrollment['result']['record']['outcome']['verification']['roots']]
        child_stop('helper-3');old_boot=reconciled['inspection']['boot_id'];print(a.server_release+': helper restart and new HCD generation passed; second real reboot',flush=True)
        reboot();child_start('helper','helper-4');history=guest('server','restart-state');assert history['historical']['records'][0]['outcome']['verification']['boot_id']==old_boot
        current=guest('server','reconcile','new-boot',3);assert current['inspection']['boot_id']!=old_boot
        # A separate stock path uses the defaults established by the product.
        client.boot();save(out/'client-qemu.json',{'pid':client.process.pid,'exe_sha256':sha(Path('/proc')/str(client.process.pid)/'exe'),'command':json.loads((client.out/'qemu-command.json').read_text())})
        client.upload([HERE/'guest.py',upload/'arrival_guest.py']);guest('client','setup','client');bound=guest('server','stock-bind');child_start('stock','stock-daemon')
        io=guest('client','stock-io',bound['after']['bus']);assert io['read_bytes']==1024*1024 and io['sg_sync_exit']==0;guest('server','snapshot');guest('client','stock-detach');child_stop('stock-daemon')
        call('device_del',{'id':'controller-storage'});guest('server','absent');guest('server','retire');guest('server','collect');child_stop('helper-4')
        result['passed']=True;print(a.server_release+': actual read-only stock I/O and empty policy retirement passed',flush=True)
    except Exception:
        result['failure']=traceback.format_exc();print(result['failure'],flush=True)
    finally:
        for label in list(children):
            try:child_stop(label)
            except Exception:result.setdefault('child_cleanup_errors',[]).append(traceback.format_exc());result['passed']=False
        pool.shutdown(wait=True)
        if qmp:
            try:call('query-status');save(out/'qmp-final-events.json',qmp.events)
            except Exception:result['qmp_error']=traceback.format_exc();result['passed']=False
            finally:qmp.close()
        for role,vm in vms.items():
            if vm.process and vm.process.poll() is None:
                try:
                    with (out/(role+'-evidence.tar')).open('wb') as f:p=subprocess.run(vm.ssh+['sudo tar -C /var/tmp/rustusb-controller-enrollment -cf - .'],stdout=f,stderr=subprocess.PIPE)
                    (out/(role+'-collection.stderr')).write_bytes(p.stderr);result[role+'_collection_exit']=p.returncode
                    if p.returncode:result['passed']=False
                except Exception:result[role+'_collection_error']=traceback.format_exc();result['passed']=False
                vm.close()
            record_exit(role)
        left.close();right.close()
        serial=(server.out/'serial.log').read_bytes() if (server.out/'serial.log').exists() else b''
        for i,start in enumerate(serial_starts):(out/f'server-boot-{i+1}-serial.log').write_bytes(serial[start:serial_starts[i+1] if i+1<len(serial_starts) else len(serial)])
        combined=serial+((client.out/'serial.log').read_bytes() if (client.out/'serial.log').exists() else b'')
        result['actual_qemu_exits']=exits;result['kernel_integrity']=not any(s.encode() in combined for s in [*physical.INTEGRITY_FAILURES,'blocked for more than'])
        result['guards']={'head':git('rev-parse','HEAD')==head,'clean':not git('status','--porcelain'),'inputs':inputs()==source,'frozen':all(sha(Path(p))==h for p,h in frozen.items())}
        result['readonly_backing_unchanged']=backing.read_bytes()==bytes(64*1024*1024)
        result['passed']=result['passed'] and len(exits)==4 and all(r['exit']==0 for r in exits) and result['kernel_integrity'] and all(result['guards'].values()) and result['readonly_backing_unchanged']
        save(out/'result.json',result)
    print(json.dumps(result),flush=True)
    if not result['passed']:raise SystemExit(1)
if __name__=='__main__':main()
