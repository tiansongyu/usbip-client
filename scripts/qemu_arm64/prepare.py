#!/usr/bin/env python3
"""Run original product-free OS preparation in actual ARM64 full-system guests."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from arm_vm import ArmVm

parser=argparse.ArgumentParser(description=__doc__,add_help=False)
parser.add_argument('--tool-root',type=Path,required=True)
options,remaining=parser.parse_known_args()
ArmVm.tool_root=options.tool_root
original=Path(__file__).resolve().parents[1]/'qemu_installed_usb/prepare.py'
text=original.read_text();anchor='from vm import Vm,sha'
if text.count(anchor)!=1:raise ValueError('original preparation import anchor changed')
generated=text.replace(anchor,'from arm_vm import ArmVm as Vm,sha',1)
# Keep original CLI, guest preparation, source/clean guards and actual owner join.
if '--output' not in remaining:raise ValueError('original --output argument required')
output=Path(remaining[remaining.index('--output')+1]).resolve()
method=output.with_name(output.name+'.composition.json')
method.write_text(json.dumps({'original_path':str(original),'original_sha256':hashlib.sha256(text.encode()).hexdigest(),'adapter_sha256':hashlib.sha256(Path(__file__).with_name('arm_vm.py').read_bytes()).hexdigest(),'generated_sha256':hashlib.sha256(generated.encode()).hexdigest(),'tool_root':str(options.tool_root.resolve())},indent=2)+'\n')
output.with_name(output.name+'.generated.py').write_text(generated)
sys.argv=[str(original),*remaining]
exec(compile(generated,str(original),'exec'),{'__file__':str(original),'__name__':'__main__'})
