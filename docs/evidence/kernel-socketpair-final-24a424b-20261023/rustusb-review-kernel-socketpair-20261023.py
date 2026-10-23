#!/usr/bin/env python3
"""Independent read-only raw review. Never imports or calls the QEMU runner."""
import datetime, hashlib, json, pathlib, struct, subprocess

BASE = pathlib.Path('/tmp/rustusb-kernel-socketpair-24a424b-20261023')
REPO = pathlib.Path('/home/ubuntu/github/usbip-client')
HEAD = '24a424b117ecdca32a93fcbfa8a09d867110e2a8'
OUT = pathlib.Path('/tmp/rustusb-kernel-socketpair-independent-review-20261023.json')
checks = []
def digest(data): return hashlib.sha256(data).hexdigest()
def readjson(path): return json.loads(path.read_text())
def check(name, passed, detail=None):
    checks.append({'name':name,'passed':bool(passed),'detail':detail})
def git(*args): return subprocess.check_output(['git',*args],cwd=REPO)
def record(path):
    data=path.read_bytes()
    return {'path':str(path),'length':len(data),'sha256':digest(data)}
summary=readjson(BASE/'summary.json')
manifest=readjson(BASE/'artifact-sha256.json')
raw=(BASE/'guest-evidence.log').read_text()
console=(BASE/'guest-serial.log').read_text()
lines=raw.splitlines()
def tagged(tag):
    prefix='RUSTUSB_PAIR_'+tag+' '
    return [(n,json.loads(line[len(prefix):])) for n,line in enumerate(lines,1) if line.startswith(prefix)]
def single(tag):
    found=tagged(tag)
    if len(found)!=1: raise ValueError('nonunique '+tag)
    return found[0][1]
def vhci(text):
    rows=[]
    for line in text.splitlines()[1:]:
        a=line.split()
        if len(a)!=7: raise ValueError('VHCI row shape')
        rows.append({'hub':a[0],'port':int(a[1]),'state':int(a[2]),'speed':int(a[3]),'devid':int(a[4],16),'socket':int(a[5],16),'local':a[6]})
    return rows
def allnull(text):
    rows=vhci(text)
    return len(rows)==16 and len({x['port'] for x in rows})==16 and all(x['state']==4 and x['devid']==0 and x['socket']==0 and x['local']=='0-0' for x in rows)

# Exact immutable provenance; the runner's check list is deliberately not read.
check('all archived artifact bytes match manifest', all((BASE/x['path']).stat().st_size==x['length'] and digest((BASE/x['path']).read_bytes())==x['sha256'] for x in manifest), {'files':len(manifest)})
before,after=summary['source_before'],summary['source_after']
check('exact run source before and after and clean run',before==after and before['head']==HEAD and before['git_status']=='')
source_rows=before['files']; source_diffs=[]
for row in source_rows:
    blob=git('show',HEAD+':'+row['path'])
    now=(REPO/row['path']).read_bytes()
    if len(blob)!=row['length'] or digest(blob)!=row['sha256'] or now!=blob: source_diffs.append(row['path'])
check('all run-selected sources match exact Git blobs and current bytes',len(source_rows)==109 and not source_diffs,{'files':len(source_rows),'mismatches':source_diffs})
check('source manifest fingerprint recomputed independently',digest(json.dumps(source_rows,sort_keys=True).encode())==before['source_sha256'])
frozen=[]
for p in sorted((BASE/'source').iterdir()):
    data=p.read_bytes(); blob=git('show',HEAD+':scripts/qemu/'+p.name)
    frozen.append(record(p))
    if data!=blob:source_diffs.append(p.name)
check('six frozen harness sources match exact commit',len(frozen)==6 and not source_diffs)
current_state={'head':git('rev-parse','HEAD').decode().strip(),'status':git('status','--porcelain=v1').decode()}
check('terminal success and exact outer run result',summary['result']=='PASS' and summary['guest_exit_code']==0 and not summary.get('emergency_qemu_cleanup') and json.loads(pathlib.Path(str(BASE)+'.log').read_text())['result']=='PASS')
check('no guest failure or kernel integrity marker',not any(x in raw+console for x in ['RUSTUSB_PAIR_RESULT: FAIL','Kernel panic','BUG:','was already given back','seqnum max']) and (BASE/'guest-qemu.stderr').stat().st_size==0)
check('real build mode and command retained',summary['build_performed'] and summary['build_features']=='default' and summary['build_command']==['cargo','build','--workspace','--release','--locked'])

binary=single('BINARIES'); identity=single('IDENTITY'); events=[x for _,x in tagged('PROBE')]
check('installed binaries and actual helper/probe executed digest chain',binary['installed']==summary['guest_binaries'] and binary['helper_executed_sha256']==summary['binary_sha256']['rustusb-helper'] and identity['executed_sha256']==summary['probe_binary_sha256']==summary['guest_binaries']['kernel-socketpair-probe']['sha256'])
elf=[]
for name in ['rustusb','rustusb-helper']:
    path=REPO/'target/release'/name; data=path.read_bytes(); row=record(path)
    row['elf64_little_endian']=data[:6]==b'\x7fELF\x02\x01';row['elf_machine']=struct.unpack_from('<H',data,18)[0]
    elf.append(row)
check('available product ELF bytes equal installed actual run artifacts',all(x['sha256']==summary['binary_sha256'][pathlib.Path(x['path']).name] and x['elf64_little_endian'] and x['elf_machine']==62 for x in elf))
kernel=summary['kernel']; config_path=pathlib.Path('/boot/config-'+kernel); config= config_path.read_text()
kernel_hash=subprocess.check_output(['sudo','-n','sha256sum','/boot/vmlinuz-'+kernel],text=True).split()[0]
check('kernel image and config hashes independently re-read',kernel_hash==summary['kernel_image_sha256'] and digest(config_path.read_bytes())==summary['guest_modules']['config_sha256'])
module_observations=[]
for name,expected in summary['guest_modules']['copied'].items():
    found=list(pathlib.Path('/lib/modules',kernel).rglob(name))
    row={'module':name,'paths':[str(x) for x in found],'sha256':digest(found[0].read_bytes()) if len(found)==1 else None,'expected_sha256':expected}
    module_observations.append(row)
check('all eleven copied module files independently match',len(module_observations)==11 and all(x['sha256']==x['expected_sha256'] for x in module_observations))
options={'usb-common':'USB_COMMON','usbcore':'USB','xhci-hcd':'USB_XHCI_HCD','xhci-pci':'USB_XHCI_PCI','evdev':'INPUT_EVDEV','hid':'HID','hid-generic':'HID_GENERIC','usbhid':'USB_HID','usbserial':'USB_SERIAL','ftdi_sio':'USB_SERIAL_FTDI_SIO','usbip-core':'USBIP_CORE','usbip-host':'USBIP_HOST','vhci-hcd':'USBIP_VHCI_HCD'}
check('all required kernel features are built-in or exact copied modules', all('CONFIG_'+options[name]+'='+row['mode']+'\n' in config and (row['mode']=='y' and row['sha256'] is None or row['mode']=='m' and row['sha256']==summary['guest_modules']['copied'][name+'.ko']) for name,row in summary['guest_modules']['required'].items()))
check('actual guest boot identifies matching kernel and markers','Linux version '+kernel in console and 'RUSTUSB_PAIR_BOOT kernel='+kernel in raw and 'RUSTUSB_QEMU_GUEST=1 rustusb.kernel_socketpair_test=1' in console)

snapshots=[x for _,x in tagged('SNAPSHOT')]; stage={x['stage']:x for x in snapshots}
check('six ordered complete snapshots',list(stage)==['original','exported','attached','before-stop','disconnected','restored'] and len(snapshots)==6)
check('guest has no NIC and no sampled IPv4 or IPv6 TCP socket',summary['topology']=={'guests':1,'qemu_nics':0,'usb_models':['usb-kbd','usb-serial'],'tls':False} and summary['qemu_command'][summary['qemu_command'].index('-nic')+1]=='none' and all(x['network_interfaces']==['lo'] and len(x['tcp'].splitlines())==1 and len(x['tcp6'].splitlines())==1 for x in snapshots))
expected_event_names=['guard','initial_status','prepared','prepared','write_begin','write_complete','write_begin','write_complete','write_begin','write_complete','write_begin','write_complete','attached','attached','ready','stop_received','shutdown','shutdown','shutdown','shutdown','disconnected','complete']
check('probe lifecycle is complete in exact internal order', [x['event'] for x in events]==expected_event_names and all(x['schema_version']==1 for x in events))
guard=events[0];wait=single('WAIT')
check('same live probe identity and actual successful wait',guard['pid']==identity['pid']==wait['pid'] and guard['starttime']==identity['starttime']==wait['starttime'] and guard['uids']==identity['uids']==[0,0,0,0] and identity['control_fifo']=='0:600:fifo' and wait['wait_code']==0 and wait['command']=='stop')
check('non-root probe denied guest-root guard',single('GUARD_REFUSAL')=={'event':'error','schema_version':1,'result':'FAIL','error':'guest root required'})
prepared=[x for x in events if x['event']=='prepared'];attached=[x for x in events if x['event']=='attached'];disconnected=[x for x in events if x['event']=='disconnected'][0]
sockets=[row[side] for row in prepared for side in ['stub','vhci']]
fdmap={int(x.split(' ',1)[0]):x.split(' ',1)[1] for x in identity['fds'].splitlines()}
check('four distinct actual open anonymous stream socket receipts',len(sockets)==4 and len({x['inode'] for x in sockets})==4 and len({x['fd'] for x in sockets})==4 and all(x['family']=='AF_UNIX' and x['type']=='SOCK_STREAM' and x['anonymous'] and x['target']=='socket:['+str(x['inode'])+']' and fdmap[x['fd']]==x['target'] for x in sockets))
unix_matches=[]
for stage_name in ['attached','before-stop']:
    rows=[x.split() for x in stage[stage_name]['unix'].splitlines()[1:]]
    for sock in sockets:
        hits=[x for x in rows if x[6]==str(sock['inode'])]
        unix_matches.append(len(hits)==1 and len(hits[0])==7 and hits[0][4:6]==['0001','03'])
check('proc net unix independently corroborates anonymous connected STREAM inodes',all(unix_matches))
writes=[x for x in events if x['event']=='write_begin'];completed=[x for x in events if x['event']=='write_complete']
expected_writes=[('1-1','stub',3,'5\n'),('1-1','vhci',4,'0 6 65538 3\n'),('1-2','stub',7,'9\n'),('1-2','vhci',8,'1 10 65539 2\n')]
check('exact four sysfs writes have matching full successful return sizes',[(x['bus_id'],x['side'],x['attribute_fd'],x['value']) for x in writes]==expected_writes and all(c['bus_id']==w['bus_id'] and c['side']==w['side'] and c['bytes']==len(w['value'].encode()) and fdmap[w['attribute_fd']]==w['path'] for w,c in zip(writes,completed)))
check('writes use original stub attribute inode and shared VHCI attach inode',writes[0]['inode']==prepared[0]['attribute_inode'] and writes[2]['inode']==prepared[1]['attribute_inode'] and writes[1]['inode']==writes[3]['inode'] and all(x['filesystem_device']==23 for x in writes))
original=stage['original']['physical']
check('original complete baseline is fixed HID and FTDI cfg1 alt0 original driver',[(x['bus_id'],x['vendor'],x['product'],x['speed'],x['configuration'],x['device_driver'],x['interfaces'][0]['alternate'],x['interfaces'][0]['driver']) for x in original]==[('1-1','0627','0001','480',1,'usb',0,'usbhid'),('1-2','0403','6001','12',1,'usb',0,'ftdi_sio')] and all(len(x['interfaces'])==1 and x['usbip_status'] is None for x in original))
generation_fields=['bus_id','generation','address','sysfs_path','bus_number','vendor','product','speed']
check('same complete physical generation in all snapshots',all(len(x['physical'])==2 and all(all(x['physical'][i][k]==original[i][k] for k in generation_fields) for i in range(2)) for x in snapshots))
check('exported devices are genuinely unconfigured and AVAILABLE',all(x['configuration'] is None and x['interfaces']==[] and x['device_driver']=='usbip-host' and x['usbip_status']==1 for x in stage['exported']['physical']) and all(x['original']['configuration'] is None and x['original']['interfaces']==[] for x in prepared))
profile=[]
for x in original:
    row=dict(x['interfaces'][0]);row['driver']=None;profile.append([row])
check('attach and before-stop same profile cfg1 with stub USED',all(x['configuration']==1 and x['interfaces']==profile[i] and x['device_driver']=='usbip-host' and x['usbip_status']==2 for st in ['attached','before-stop'] for i,x in enumerate(stage[st]['physical'])))
check('two actual VHCI generations bind correct matching interface drivers',[(x['local']['bus_id'],x['port'],x['remote_devid'],x['local']['vendor'],x['local']['product'],x['local']['configuration'],x['local']['interfaces'][0]['driver']) for x in attached]==[('3-1',0,65538,'0627','0001',1,'usbhid'),('3-2',1,65539,'0403','6001',1,'ftdi_sio')] and all(x['local']['sysfs_path'].startswith('/sys/devices/platform/vhci_hcd.0/') and len(x['local']['interfaces'])==1 and x['local']['interfaces'][0]['alternate']==0 for x in attached))
active_rows=[x for x in vhci(stage['attached']['vhci_status']) if x['state']!=4]
check('raw VHCI status matches both active local routes',[(x['port'],x['state'],x['speed'],x['devid'],x['local']) for x in active_rows]==[(0,6,3,65538,'3-1'),(1,6,2,65539,'3-2')])
sessions=single('SESSIONS')
check('helper reports same actual unmanaged imports',len(sessions)==2 and all(not x['managed'] and x['session']['port']==a['port'] and x['session']['local_bus_id']==a['local']['bus_id'] and x['session']['local_device_generation']=={'filesystem_device':a['local']['filesystem_device'],'inode':a['local']['inode']} and x['session']['status']==6 for x,a in zip(sessions,attached)))
io=single('IO');event_bytes=bytes.fromhex(io['input_event_hex'])
input_events=[{'seconds':a,'microseconds':b,'type':c,'code':d,'value':e} for a,b,c,d,e in struct.iter_unpack('<qqHHi',event_bytes)]
key_events=[x['value'] for x in input_events if (x['type'],x['code'])==(1,2)]
check('real evdev KEY_1 press then release from VHCI local device',len(event_bytes)==144 and len(input_events)==6 and key_events==[1,0] and io['hid_local']==attached[0]['local']['bus_id'])
rx=b'RUSTUSB-RX-1-0001:0123456789abcdef\n';tx=b'RUSTUSB-TX-1-0001:fedcba9876543210\n'
check('real exact 35 bytes each direction through VHCI FTDI',io['ftdi_local']==attached[1]['local']['bus_id'] and len(rx)==len(tx)==35 and bytes.fromhex(io['rx_hex'])==(BASE/'serial-injected.bin').read_bytes()==rx and bytes.fromhex(io['tx_hex'])==(BASE/'serial-received.bin').read_bytes()==tx)
check('QMP key and chardev injection metadata corroborates raw I/O',[(x['kind'],x.get('key'),x.get('bytes')) for x in summary['injections']]==[('key','1',None),('serial',None,35)] and summary['injections'][1]['sha256']==digest(rx) and summary['serial_output']['sha256']==digest(tx) and summary['actual_io']==io)
shutdown=[x for x in events if x['event']=='shutdown']
check('all four exact owned socket shutdown calls succeed',[(x['bus_id'],x['side'],x['fd']) for x in shutdown]==[('1-1','stub',5),('1-1','vhci',6),('1-2','stub',9),('1-2','vhci',10)] and all(x['how']=='SHUT_RDWR' and x['ok'] and x['error'] is None for x in shutdown))
check('same four sockets retained until both kernel ends cleared',disconnected['fds_still_open'] and disconnected['ports_null'] and disconnected['old_local_generations_absent'] and disconnected['stub_available'] and disconnected['retained_sockets']==[{k:s[k] for k in ['fd','inode','target']} for s in sockets] and allnull(disconnected['status']))
check('all initial and final VHCI ports NULL with zero route',all(allnull(stage[x]['vhci_status']) for x in ['original','exported','disconnected','restored']))
check('disconnected originals remain same cfg1 unclaimed profile with stub AVAILABLE',all(x['configuration']==1 and x['interfaces']==profile[i] and x['device_driver']=='usbip-host' and x['usbip_status']==1 for i,x in enumerate(stage['disconnected']['physical'])))
check('complete reported before retained socket Drop and ordinary unexport restores full baseline',events[-1]['result']=='PASS' and events[-1]['cleanup_before_fd_drop'] and stage['restored']['physical']==original and 'RUSTUSB_PAIR_UNEXPORT: PASS uid=1000 devices=2 original_drivers=true' in lines and 'RUSTUSB_PAIR_RESULT: PASS single_guest=true tls=false remote_security=false' in lines)
check('kernel independently logs both real enumerations and disconnections',all(x in console for x in ['usb 3-1: New USB device found, idVendor=0627, idProduct=0001','usb 3-2: New USB device found, idVendor=0403, idProduct=6001','usb 3-1: USB disconnect, device number 2','usb 3-2: USB disconnect, device number 3','reboot: Power down']))

first=BASE.parent/'rustusb-kernel-socketpair-f4774a6-20261023';first_summary=readjson(first/'summary.json')
check('first f477 actual failure is retained without reinterpretation',first_summary['source_before']['head']=='f4774a63e189a0cfaec9d8515b719b0e399c87ea' and first_summary['result']=='FAIL' and first_summary['first_observed_failure']=='RUSTUSB_PAIR_RESULT: FAIL stage=helper-export exit=1' and 'RUSTUSB_PAIR_PROBE ' not in (first/'guest-evidence.log').read_text())

report={
 'schema_version':1,'reviewer':'independent journal_integration_review agent','created_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'result':'PASS' if all(x['passed'] for x in checks) else 'FAIL','must_fix':[],
 'scope':'One x86_64 KVM disposable QEMU guest; two emulated USB devices; kernel usbip_host via two anonymous AF_UNIX stream pairs into kernel VHCI. This is a socket-family/cleanup mechanism experiment, not a secure product USB path.',
 'method':'Direct JSON-line, proc/sysfs snapshot, Linux input_event byte, host serial byte, kernel-console and frozen-source review. No runner verifier import/call; no build/Cargo/QEMU/test re-execution; no repository writes. The parser performs read-only Git/file/hash checks and writes only this /tmp report.',
 'checks':checks,'failed_checks':[x['name'] for x in checks if not x['passed']],
 'assertion_count_note':'Grouped review predicates are bookkeeping, not a USB-device, USB-transfer, or regression-case count. Runner PASS50 is retained as metadata only.',
 'provenance':{'head':HEAD,'run_started_at_utc':summary['started_at_utc'],'run_finished_at_utc':summary['finished_at_utc'],'run_source_files':source_rows,'source_fingerprint':before['source_sha256'],'frozen_sources':frozen,'artifact_manifest':record(BASE/'artifact-sha256.json'),'summary':record(BASE/'summary.json'),'current_review_worktree':current_state,'current_dirty_scope_note':'Parent delivery documentation/archive writes occur after the run. The before/after run source receipts are clean and all 109 executable/build inputs independently match commit 24a424b. Review-time documentation dirtiness is not assigned retroactively to the run.','binaries':summary['guest_binaries'],'available_elf_recheck':elf,'kernel':kernel,'kernel_image_sha256_rechecked':kernel_hash,'config_sha256_rechecked':digest(config_path.read_bytes()),'copied_modules_rechecked':module_observations,'initramfs_sha256_recorded':summary['initramfs_sha256'],'probe_compile_command':summary['probe_build_command'],'build_log':record(BASE/'build.log')},
 'observed':{'evidence_lines':{tag:[n for n,_ in tagged(tag)] for tag in ['BINARIES','GUARD_REFUSAL','PROBE','IDENTITY','SNAPSHOT','SESSIONS','IO','WAIT']},'probe_identity':identity,'anonymous_sockets':sockets,'sysfs_writes':writes,'sysfs_write_returns':completed,'stage_snapshots':snapshots,'input_event_decode':input_events,'actual_io':io,'host_serial_files':[record(BASE/'serial-injected.bin'),record(BASE/'serial-received.bin')],'shutdown':shutdown,'disconnected':disconnected,'wait':wait,'kernel_nonfatal_observations':['vhci_device speed not set appears twice during FTDI enumeration','FTDI endpoint 0 is stalled appears three times before successful enumeration','URB status -104 accompanies recorded unlink activity; actual payload and final cleanup subsequently succeeded'],'nonfatal_note':'These raw console messages are retained. This review does not claim a warning-free kernel log or infer all USB request types from successful HID/FTDI I/O.'},
 'source_constraints':[
 {'file':'source/kernel-socketpair-probe.rs','lines':[66,105,186,195,233,242,268,277,337,393],'fact':'Reviewed guard precedes USB access; Option configuration only accepts both zero-byte cfg/count and no interfaces; Attribute retains and validates opened sysfs inode; UnixStream::pair creates each typed anonymous stream; successful write_complete follows a full actual File::write; disconnect shuts down all four before fallible diagnostic emission, then polls actual NULL/local NotFound/AVAILABLE and captures retained FD receipts before Channel Drop.'},
 {'file':'source/guest-kernel-socketpair-init.sh','lines':[38,88,126,140,158,174,208,209,212,222,223,231],'fact':'Reviewed UID1000 CLI wrapper and root helper export/unexport; UID1001 denial exit checked; persistent FIFO only opened by child; live UIDs/exe verified before literal receipt; input and tty selected below actual VHCI local sysfs path; one real probe wait, then drain; ordinary helper unexport and whole original physical manifest comparison.'},
 {'file':'source/run-kernel-socketpair-qemu.py','lines':[47,59,253,277,288,303,333,337,345],'fact':'Reviewed source freeze/build/payload provenance, -nic none, separate evidence and kernel serial channels, actual QMP send-key and serial sendall/recv, terminal process wait and immutable source/binary checks. Its verify function was neither imported nor called by this review.'}
 ],
 'evidence_boundaries':[
 'Temporary probe ELF, copied boot image and initramfs payload have been deleted by runner TemporaryDirectory cleanup. Kernel image is independently rehashed at original /boot path; product ELFs and all eleven module files are available and independently rehashed. Probe compiled/installed/actual /proc/exe digest chain agrees, but this review cannot independently inspect the deleted probe ELF or rehash the deleted initramfs. No reconstruction or rebuild substitutes for those original bytes.',
 'Snapshots expose configuration:null rather than the two raw zero-byte files; the frozen collector only emits null when both cfg/count are exactly empty and interface set is empty. Missing/unreadable/malformed/whitespace numeric data fails. This is a reviewed collector constraint corroborated by the probe, not a second separately archived raw attribute-byte file.',
 'AF_UNIX SOCK_STREAM follows the reviewed Rust UnixStream::pair constructor, corroborated by unnamed socket receipts and proc/net/unix inode/type/state. There is no independent syscall trace or getsockopt family trace.',
 'Four sysfs write results and four shutdown results are source-emitted actual call receipts and kernel-state corroboration, not a syscall trace. local Gone booleans follow reviewed NotFound checks, corroborated by kernel disconnect messages and NULL status.',
 'Raw parent WAIT is printed before buffered probe stop/shutdown/complete lines are drained; do not treat global log print order as syscall order. Probe internal event order plus frozen wait/drain code establishes the lifecycle.',
 'Success logs retain the normal UID1000 unexport marker and complete before/after baseline; per-unexport success JSON and a syscall trace are not separately archived. The source invokes the ordinary helper CLI, not direct sysfs rebinding.',
 'No guest NIC and all six TCP/TCP6 snapshots empty corroborate this experiment; finite snapshots do not prove a general lifetime network audit. Frozen guest/probe source contains no usbipd start or TCP transport path.',
 'This single-guest root probe does not test TLS, remote identity/ACL, DEVLIST/IMPORT authorization, SCM_RIGHTS handoff, dedicated broker isolation, service restart/crash, stale attr FD removal/replug G1/G2, physical hardware, Windows, multi-host delivery, or complete Phase 3. Existing standalone transport TLS/software evidence remains separate.',
 'This review does not execute a new guest or fabricate a replacement result; local ELF/source rehashes are supplemental read-only provenance checks, not another USB case.'
 ],
 'first_failure_preserved':{'directory':str(first),'summary':record(first/'summary.json'),'artifact_manifest':record(first/'artifact-sha256.json'),'head':first_summary['source_before']['head'],'result':first_summary['result'],'first_observed_failure':first_summary['first_observed_failure'],'guest_exit_code':first_summary['guest_exit_code'],'note':'f477 remains actual FAIL before probe startup or USB transfer I/O; guest exit 0 did not turn it into success. This final 24a run is distinct evidence.'},
 'parser':record(pathlib.Path(__file__))
}
OUT.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'result':report['result'],'report':str(OUT),'failed_checks':report['failed_checks'],'sha256':digest(OUT.read_bytes()),'grouped_predicates':len(checks)}))
