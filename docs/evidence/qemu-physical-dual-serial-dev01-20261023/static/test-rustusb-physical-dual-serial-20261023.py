import ast,hashlib,importlib.util,json,os,pathlib,pty,select,subprocess,tempfile,termios
root=pathlib.Path('/home/ubuntu/github/usbip-client')
runner=root/'scripts/qemu/run-physical-qemu.py';guest=root/'scripts/qemu/guest-physical-init.sh'
ns=importlib.util.spec_from_file_location('physical_dual_serial_pure_checks',runner);module=importlib.util.module_from_spec(ns);ns.loader.exec_module(module)
checks=[]
def passed(name,**values): checks.append({'name':name,'result':'PASS',**values})
ast.parse(runner.read_text());passed('python_ast')
for shell in ['/usr/bin/busybox','/bin/bash']:
 command=[shell,'sh','-n',str(guest)] if shell.endswith('busybox') else [shell,'-n',str(guest)]
 r=subprocess.run(command,capture_output=True,timeout=3);assert r.returncode==0
 passed('shell_syntax',command=command,exit_code=r.returncode,stdout_sha256=hashlib.sha256(r.stdout).hexdigest(),stderr_sha256=hashlib.sha256(r.stderr).hexdigest())
program={role:f'RUSTUSB_PHYSICAL_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0\nRUSTUSB_PHYSICAL_RESULT: PASS role={role}\n' for role in module.ROLES}
kernel={role:'synthetic benign kernel message\n' for role in module.ROLES}
assert module.verify_channel_beacons(program)=={role:program[role].splitlines()[0] for role in module.ROLES};passed('exact_two_channel_beacons')
module.check_channel_failures(program,kernel);passed('benign_two_channel_input')
for role in module.ROLES:
 for channel in ['evidence','kernel']:
  for message in module.INTEGRITY_FAILURES+['RUSTUSB_PHYSICAL_RESULT: FAIL']:
   p=dict(program);k=dict(kernel);target=p if channel=='evidence' else k;target[role]+=message+' synthetic sentinel\n'
   try:module.check_channel_failures(p,k)
   except RuntimeError:passed('both_channels_reject_early_or_late_failure',role=role,channel=channel,sentinel=message)
   else:raise AssertionError((role,channel,message))
for role in module.ROLES:
 for kind in ['missing','duplicate','wrong_device']:
  p=dict(program);marker=p[role].splitlines()[0]+'\n'
  p[role]={'missing':p[role].replace(marker,''),'duplicate':marker+p[role],'wrong_device':p[role].replace('device=ttyS1','device=ttyS0')}[kind]
  try:module.verify_channel_beacons(p)
  except RuntimeError:passed('reject_invalid_channel_beacon',role=role,kind=kind)
  else:raise AssertionError((role,kind))
with tempfile.TemporaryDirectory(prefix='rustusb-physical-dual-serial-static-') as directory:
 d=pathlib.Path(directory)
 for role in module.ROLES:
  (d/f'{role}-serial.log').write_text(kernel[role]);(d/f'{role}-evidence.log').write_text(program[role])
 actual_p,actual_k=module.read_channels(d)
 assert actual_p==program and actual_k==kernel;passed('read_program_and_kernel_files_separately')
 # The original interrupted marker remains broken and is never repaired.
 original=root/'docs/evidence/qemu-checked-release-candidate-physical-hot-regression-20261023/server-serial.log'
 before=original.read_bytes();assert b'RUSTUSB_PHYSICAL[' in before and b'_REINSERT_STALE: PASS' in before
 assert b'RUSTUSB_PHYSICAL_REINSERT_STALE: PASS new_generation=true old_slot_gone=true nodes=root0600' not in before
 (d/'server-serial.log').write_bytes(before)
 actual_p,actual_k=module.read_channels(d);assert actual_p==program
 module.check_channel_failures(actual_p,actual_k)
 assert original.read_bytes()==before;passed('raw_candidate_marker_preserved_without_repair',original_sha256=hashlib.sha256(before).hexdigest(),synthetic_program_channel=True)
master,slave=pty.openpty();device=os.ttyname(slave)
try:
 text=guest.read_text();start=text.index('STAGE=evidence-channel\n');stop=text.index('STAGE=boot\n',start)
 setup=text[start:stop].replace('/dev/ttyS1',device)
 expected=f'RUSTUSB_PHYSICAL_EVIDENCE_CHANNEL: PASS role=server device=ttyS1 kernel_console=ttyS0\n'.encode()
 command=['/usr/bin/busybox','sh','-c','set -eu\nROLE=server\n'+setup]
 r=subprocess.run(command,capture_output=True,timeout=3);assert r.returncode==0 and not r.stdout and not r.stderr
 data=b''
 while len(data)<len(expected):
  ready,_,_=select.select([master],[],[],1);assert ready;data+=os.read(master,4096)
 assert data==expected
 flags=termios.tcgetattr(slave)
 assert not flags[3]&(termios.ECHO|termios.ICANON)
 assert flags[2]&termios.CLOCAL and not flags[2]&termios.HUPCL
 assert flags[4]==termios.B115200 and flags[5]==termios.B115200
 passed('actual_busybox_stty_and_redirection_on_disposable_pty',exit_code=r.returncode,output_bytes=len(data),output_sha256=hashlib.sha256(data).hexdigest(),raw_no_echo=True,clocal=True,hupcl=False,baud=115200)
finally:os.close(master);os.close(slave)
report={'schema_version':1,'scope':'Static syntax and pure temporary-file/PTY checks only; no QEMU, Cargo, guest, USB, host UART, modules or network operations','result':'PASS','check_count':len(checks),'checks':checks,'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [runner,guest]},'busybox_sha256':hashlib.sha256(pathlib.Path('/usr/bin/busybox').read_bytes()).hexdigest()}
out=pathlib.Path('/tmp/rustusb-physical-dual-serial-static-20261023.json');out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'result':report['result'],'checks':len(checks),'report':str(out),'source_sha256':report['source_sha256']},indent=2))
