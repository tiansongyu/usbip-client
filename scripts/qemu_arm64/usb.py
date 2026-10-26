#!/usr/bin/env python3
"""Original installed USB assertions on actual full-system ARM64 guests."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from arm_vm import ArmVm,sha

ROOT=Path(__file__).resolve().parents[2]
original=ROOT/'scripts/qemu_installed_usb/run.py'
parser=argparse.ArgumentParser(description=__doc__,add_help=False)
parser.add_argument('--tool-root',type=Path)
options,remaining=parser.parse_known_args()
source=original.read_text();anchor='from vm import Vm, sha'
if source.count(anchor)!=1:raise ValueError('original VM import anchor changed')
generated=source.replace(anchor,'from arm_vm import ArmVm as Vm, sha',1)
compile(generated,str(original),'exec')
if '--help' in remaining or '-h' in remaining:
 print('ARM64 adapter: add --tool-root EXTRACTED_ROOT to the original options below.')
 sys.argv=[str(original),'--help']
 exec(compile(generated,str(original),'exec'),{'__file__':str(original),'__name__':'__main__'})
if options.tool_root is None:parser.error('--tool-root is required')
def argument(name):
 if name not in remaining:raise ValueError('original '+name+' required')
 return Path(remaining[remaining.index(name)+1]).resolve()
tools=options.tool_root.resolve();ArmVm.tool_root=tools
output=argument('--output');prepared=argument('--prepared')
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip():raise ValueError('clean source required before ARM USB owners')
paths=[Path(__file__),Path(__file__).with_name('arm_vm.py'),original,original.with_name('guest.py'),original.with_name('vm.py'),tools/'usr/bin/qemu-system-aarch64',tools/'usr/share/AAVMF/AAVMF_CODE.fd',tools/'usr/share/AAVMF/AAVMF_VARS.fd',argument('--base'),argument('--upgrade')]
for release in ['jammy','noble']:
 receipt=json.loads((prepared/release/'preparation.json').read_text())
 if not receipt['passed'] or receipt['official_image']['architecture']!='arm64':raise ValueError('verified closed ARM64 preparation required')
 paths.append(prepared/release/'.private/disk.qcow2')
frozen={str(p):sha(p) for p in paths}
method={'original_path':str(original),'original_sha256':hashlib.sha256(source.encode()).hexdigest(),'adapter_sha256':sha(Path(__file__)),'generated_sha256':hashlib.sha256(generated.encode()).hexdigest(),'changes':['single original VM import only'],'head':head,'frozen':frozen}
output.with_name(output.name+'.composition.json').write_text(json.dumps(method,indent=2)+'\n')
output.with_name(output.name+'.generated.py').write_text(generated)
sys.argv=[str(original),*remaining]
try:
 exec(compile(generated,str(original),'exec'),{'__file__':str(original),'__name__':'__main__'})
finally:
 if output.exists():
  guards={'head_guard':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==head,'clean_guard':not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip(),'source_tools_parents_packages_guard':all(sha(Path(p))==h for p,h in frozen.items())}
  inherited=output/'summary.json';body=json.loads(inherited.read_text()) if inherited.exists() else None
  verdict={'head':head,'guards':guards,'inherited_summary_sha256':sha(inherited) if body else None,'inherited_passed':bool(body and body['passed']),'passed':bool(body and body['passed']) and all(guards.values()),'inherited_summary_not_rewritten':True}
  (output/'arm-verdict.json').write_text(json.dumps(verdict,indent=2)+'\n')
  if not all(guards.values()):raise RuntimeError('ARM outer freeze guard failed; original verdict retained')
