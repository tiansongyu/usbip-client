import ast, pathlib, subprocess, re, json, hashlib
root=pathlib.Path('/home/ubuntu/github/usbip-client'); path=root/'scripts/qemu/run-kernel-socketpair-qemu.py'
tree=ast.parse(path.read_text(),filename=str(path)); checks=[{'name':'runner ast syntax','passed':True}]
r=subprocess.run(['sh','-n',str(root/'scripts/qemu/guest-kernel-socketpair-init.sh')],capture_output=True,text=True)
checks.append({'name':'guest sh -n','passed':r.returncode==0,'stderr':r.stderr})
fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='status_rows'); env={'re':re}
exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),env)
for opaque in ['000000','000017','ffffa123abcde000','0000000000000000']:
 row=env['status_rows']('hub port sta spd dev sockfd local_busid\nhs 0000 006 002 00010002 '+opaque+' 3-1\n')[0]
 checks.append({'name':'opaque-display fixture '+opaque,'passed':row['socket_display']==opaque and row['remote']==65538})
for opaque in ['z123','-1','0123456789abcdef0']:
 try: env['status_rows']('hub port sta spd dev sockfd local_busid\nhs 0 6 2 00010002 '+opaque+' 3-1\n'); accepted=True
 except ValueError: accepted=False
 checks.append({'name':'invalid-display fixture '+opaque,'passed':not accepted})
files=[]
for name in ['run-kernel-socketpair-qemu.py','guest-kernel-socketpair-init.sh','kernel-socketpair-probe.rs']:
 p=root/'scripts/qemu'/name; data=p.read_bytes(); files.append({'path':str(p.relative_to(root)),'length':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mode':oct(p.stat().st_mode&0o777)})
probe=(root/'scripts/qemu/kernel-socketpair-probe.rs').read_text()
checks.append({'name':'forbid unsafe and no unwrap/expect/panic calls','passed':'#![forbid(unsafe_code)]' in probe and re.search(r'\b(?:unwrap|expect|panic)\s*[!(]',probe) is None})
checks.append({'name':'one-write sysfs and no stub negative command','passed':'self.file.write(text.as_bytes())?' in probe and '.write_all(' not in probe and 'write("-1' not in probe})
report={'schema_version':1,'scope':'Author static Python/shell checks and pure Python parser fixtures only; no Rust compile/probe execution/Cargo/QEMU/product test','result':'PASS' if all(c['passed'] for c in checks) else 'FAIL','checks':checks,'check_count':len(checks),'files':files,'preflight_correction':'Root initially suggested v6.8 sockfd was %pK. Fixed v6.8 source actually prints %06u historical sockfd, matching current Native parser. This was a review correction, not a product or runtime failure. Probe preserves bounded opaque display without treating it as identity; hex fixtures are parser compatibility only.','review_fixes':['Create evdev capture file before background reader.','Reject extra status files; fixed experiment requires one complete controller status.','Only ENOENT proves old local sysfs disappearance.','Attempt all four owned socket shutdowns before any fallible diagnostic write.'],'limitations':['Rust compilation and guest execution remain pending root orchestration.','One guest, one KEY1 cycle and35B serial each direction only; no remote TCP/TLS/authentication/DEVLIST/IMPORT handshake.','The direct kernel imports are explicitly unmanaged by current helper; ordinary helper export/unexport remain existing product operations.']}
p=pathlib.Path('/tmp/rustusb-kernel-socketpair-author-static-20261023.json'); p.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'path':str(p),'result':report['result'],'checks':len(checks),'files':files}))
