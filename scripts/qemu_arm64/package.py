#!/usr/bin/env python3
"""Compose original complete cold-install checks on the ARM64 platform."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(add_help=False)
parser.add_argument('--tool-root',type=Path,required=True)
options,remaining=parser.parse_known_args()
tool=options.tool_root.resolve()
if '--evidence' not in remaining:raise ValueError('original --evidence required')
output=Path(remaining[remaining.index('--evidence')+1]).resolve()
original=ROOT/'scripts/qemu_package/run.py'
source=original.read_text();generated=source
changes=[]
def replace(old,new,label):
 global generated
 if generated.count(old)!=1:raise ValueError('original anchor changed: '+label)
 generated=generated.replace(old,new,1);changes.append(label)
build_start=source.index("    with (out/'fsync-gate-build.log').open('wb') as log:")
build_end=source.index("    shutil.copyfile(HERE/'guest.py'",build_start)
replace(source[build_start:build_end],"    shutil.copyfile(HERE/'fsync-gate.c', fixture/'fsync-gate.c')\n",'defer C fixture compilation into ARM guest')
launch_start=source.index("    qemu = subprocess.Popen(['qemu-system-x86_64'")
launch_end=source.index('\n',launch_start)
replace(source[launch_start:launch_end],"    qemu = arm_launch(disk, private, port, console, errors, out)",'actual ARM platform launch')
wait_start=source.index('    def wait_ssh():')
wait_end=source.index('    try:\n        wait_ssh()',wait_start)
replace(source[wait_start:wait_end],"    def wait_ssh():\n        arm_wait_ssh(qemu, ssh, out)\n",'SSH actual-owner observation loop')
replace("        wait_ssh(); remote('sudo cloud-init status --wait', 'cloud-init')", "        wait_ssh(); remote('test \\\"$(uname -m)\\\" = aarch64 && test \\\"$(dpkg --print-architecture)\\\" = arm64 && test \\\"$(cat /proc/1/comm)\\\" = systemd && uname -a', 'actual-arm-identity')\n        remote('sudo cloud-init status --wait', 'cloud-init')",'explicit actual guest identity')
upload="        remote('sudo python3 /var/tmp/rustusb-package-fixture/guest.py install', 'install')"
compile_guest="""        remote('sudo apt-get -o Acquire::Languages=none -o Acquire::http::Timeout=30 -o Acquire::Retries=2 update && sudo env DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::http::Timeout=30 -o Acquire::Retries=2 install --no-install-recommends -y gcc libc6-dev', 'arm-fixture-compiler')
        native_build = remote('cc -std=c11 -shared -fPIC -O2 -Wall -Wextra -Werror /var/tmp/rustusb-package-fixture/fsync-gate.c -ldl -o /var/tmp/rustusb-package-fixture/fsync-gate.so && readelf -h /var/tmp/rustusb-package-fixture/fsync-gate.so && sha256sum /var/tmp/rustusb-package-fixture/fsync-gate.c /var/tmp/rustusb-package-fixture/fsync-gate.so', 'arm-fixture-build')
        if b'AArch64' not in native_build.stdout: raise RuntimeError('actual interposer is not AArch64')
        native_hashes = {line.split()[1].decode(): line.split()[0].decode() for line in native_build.stdout.splitlines() if len(line.split()) == 2 and len(line.split()[0]) == 64}
        if native_hashes['/var/tmp/rustusb-package-fixture/fsync-gate.c'] != sha(HERE/'fsync-gate.c'): raise RuntimeError('original guest C source differs')
        (out/'fsync-gate-library.json').write_text(json.dumps({'sha256':native_hashes['/var/tmp/rustusb-package-fixture/fsync-gate.so'], 'source_sha256':sha(HERE/'fsync-gate.c'), 'machine':'AArch64', 'compiled_in_actual_ARM_guest':True})+'\\n')
"""
replace(upload,compile_guest+upload,'compile exact original interposer natively in guest')
if generated.count('qemu.wait(timeout=120)')!=2:raise ValueError('original QEMU observation wait anchors changed')
generated=generated.replace('qemu.wait(timeout=120)','arm_wait_qemu(qemu, out)');changes.append('both QEMU observation intervals retain actual owner')

def arm_launch(disk,private,port,console,errors,out):
 executable=tool/'usr/bin/qemu-system-aarch64';code=tool/'usr/share/AAVMF/AAVMF_CODE.fd';template=tool/'usr/share/AAVMF/AAVMF_VARS.fd';variables=private/'AAVMF_VARS.fd';shutil.copyfile(template,variables)
 args=[str(executable),'-machine','virt-6.2,gic-version=3','-accel','tcg,thread=multi','-cpu','cortex-a72','-m','4096','-smp','4','-display','none','-serial','stdio','-monitor','none','-drive','if=pflash,format=raw,readonly=on,file='+str(code),'-drive','if=pflash,format=raw,file='+str(variables),'-drive','file='+str(disk)+',if=none,id=os,format=qcow2','-device','virtio-blk-pci,drive=os','-drive','file='+str(private/'seed.iso')+',if=none,id=seed,format=raw,readonly=on','-device','virtio-blk-pci,drive=seed','-netdev',f'user,id=net0,hostfwd=tcp:127.0.0.1:{port}-:22','-device','virtio-net-pci,netdev=net0','-no-reboot']
 (out/'arm-platform.json').write_text(json.dumps({'machine':'virt-6.2','accelerator':'tcg','cpu':'cortex-a72','inputs':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [executable,code,template]}},indent=2)+'\n')
 (out/'qemu-command.json').write_text(json.dumps(args,indent=2)+'\n')
 return subprocess.Popen(args,stdout=console,stderr=errors)

def arm_wait_ssh(qemu,ssh,out):
 polls=0
 while True:
  result=subprocess.run(ssh+['true'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  if result.returncode==0:return
  if qemu.poll() is not None:raise RuntimeError('actual ARM QEMU ended before SSH: '+str(qemu.returncode))
  polls+=1
  if polls%30==0:
   with (out/'arm-owner-observations.jsonl').open('a') as f:f.write(json.dumps({'phase':'ssh','polls':polls,'pid':qemu.pid,'actual_live':qemu.poll() is None})+'\n')
   print('original ARM QEMU remains live before SSH',flush=True)
  time.sleep(1)

def arm_wait_qemu(qemu,out):
 while True:
  try:return qemu.wait(timeout=45)
  except subprocess.TimeoutExpired:
   with (out/'arm-owner-observations.jsonl').open('a') as f:f.write(json.dumps({'phase':'wait','pid':qemu.pid,'actual_live':qemu.poll() is None})+'\n')
   print('original ARM QEMU still live; retaining actual wait',flush=True)

method={'original_sha256':hashlib.sha256(source.encode()).hexdigest(),'adapter_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'generated_sha256':hashlib.sha256(generated.encode()).hexdigest(),'changes':changes}
output.with_name(output.name+'.composition.json').write_text(json.dumps(method,indent=2)+'\n')
output.with_name(output.name+'.generated.py').write_text(generated)
compile(generated,str(original),'exec')
sys.argv=[str(original),*remaining]
initial_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
real_run='--help' not in remaining and '-h' not in remaining
if real_run and subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip():raise ValueError('clean source required before actual ARM owners')
frozen={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),tool/'usr/bin/qemu-system-aarch64',tool/'usr/share/AAVMF/AAVMF_CODE.fd',tool/'usr/share/AAVMF/AAVMF_VARS.fd']}
for flag in ['--first','--second']:
 if flag not in remaining:raise ValueError('original '+flag+' argument required')
 for p in Path(remaining[remaining.index(flag)+1]).resolve().glob('*.deb'):frozen[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
try:
 exec(compile(generated,str(original),'exec'),{'__file__':str(original),'__name__':'__main__','arm_launch':arm_launch,'arm_wait_ssh':arm_wait_ssh,'arm_wait_qemu':arm_wait_qemu})
finally:
 if real_run and output.exists():
  guards={'initial_head':initial_head,'head_guard':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==initial_head,'clean_guard':not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip(),'adapter_tool_guard':all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in frozen.items()),'frozen_inputs':frozen}
  (output/'arm-freeze-guards.json').write_text(json.dumps(guards,indent=2)+'\n')
  if not all(guards[k] for k in ['head_guard','clean_guard','adapter_tool_guard']):raise RuntimeError('ARM outer source/tool freeze guard failed; retain original verdict separately')
