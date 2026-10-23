import datetime, hashlib, json, pathlib, subprocess

root=pathlib.Path('/home/ubuntu/github/usbip-client')
out=pathlib.Path('/tmp/rustusb-secure-foundation-software-20261023')
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def inputs():
    paths=[root/'Cargo.toml',root/'Cargo.lock',root/'scripts/check.sh']
    for directory in ['crates','vendor']:
        paths += [p for p in (root/directory).rglob('*') if p.is_file() and (p.suffix=='.rs' or p.name in ['Cargo.toml','Cargo.lock'])]
    return [{'path':str(p.relative_to(root)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(set(paths))]
before=inputs()
(out/'inputs-before.json').write_text(json.dumps(before,indent=2)+'\n')
commands=[
 ('probe-build',['rustc','--edition=2024','-O','-D','warnings','scripts/qemu/kernel-socketpair-probe.rs','-o',str(out/'kernel-socketpair-probe')]),
 ('probe-host-guard',[str(out/'kernel-socketpair-probe')]),
 ('check',['bash','scripts/check.sh']),
]
results=[]
for label,command in commands:
    started=now()
    with (out/(label+'.log')).open('wb') as log:
        code=subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT).returncode
    passed=(code==1 and json.loads((out/(label+'.log')).read_text())['error']=='guest root required') if label=='probe-host-guard' else code==0
    results.append({'name':label,'command':command,'started_at_utc':started,'finished_at_utc':now(),'exit_code':code,'passed':passed})
    print(label,code,'PASS' if passed else 'FAIL',flush=True)
    if not passed: break
after=inputs()
result={'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'results':results,'source_guard_passed':before==after,'input_count':len(before),'passed':len(results)==3 and all(r['passed'] for r in results) and before==after}
(out/'inputs-after.json').write_text(json.dumps(after,indent=2)+'\n')
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result),flush=True)
raise SystemExit(0 if result['passed'] else 1)
