from pathlib import Path
import argparse,datetime,hashlib,json,subprocess,sys

parser=argparse.ArgumentParser()
parser.add_argument('--checkout',type=Path,required=True)
parser.add_argument('--head',required=True)
parser.add_argument('--evidence',type=Path,required=True)
args=parser.parse_args();root=args.checkout;out=args.evidence;out.mkdir(parents=True,exist_ok=False)
def git(*argv):return subprocess.check_output(['git',*argv],cwd=root,text=True).strip()
def inputs():
    paths=[root/'Cargo.toml',root/'Cargo.lock',root/'rust-toolchain.toml',root/'scripts/check.sh']
    for parent in ['crates','vendor/nusb','scripts/qemu','scripts/qemu_data_link_health','scripts/qemu_prepare_recording_shutdown']:
        paths.extend(p for p in (root/parent).rglob('*') if p.is_file() and (p.suffix in ['.rs','.c','.py','.sh'] or p.name in ['Cargo.toml','Cargo.lock']) and 'target' not in p.parts and '__pycache__' not in p.parts)
    return [dict(path=str(p.relative_to(root)),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(set(paths))]
def save(name,data):(out/name).write_text(json.dumps(data,indent=2)+'\n')
assert git('rev-parse','HEAD')==args.head and not git('status','--porcelain')
expected_binaries={'rustusb':'72adb291645fdcf3cb41c1ff9e9e781fe9eed344730a4444407f983c9b63e5f2','rustusb-helper':'b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5'}
before=inputs();save('inputs-before.json',before)
save('start.json',dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),head=args.head,clean=True,input_count=len(before),expected_binaries=expected_binaries,scope='Eight actual prepare/recording-stop QEMU scenarios plus six existing v2 directional-fault runs and nine raw-v1 regressions. No hardware or kernel-internal hard-block acceptance.'))
(out/'runner.py').write_bytes(Path(__file__).read_bytes())
cases=[]
for case in ['native-resource','usbfs-resource','usbfs-initializer']:
    for stop in ['job','term']:
        cases.append((f'prepare-{case}-{stop}',['python3','-B','scripts/qemu_prepare_recording_shutdown/run.py','--case',case,'--stop',stop,'--family','ipv4']))
for case in ['native-resource','native-available']:
    cases.append((f'prepare-{case}-session',['python3','-B','scripts/qemu_prepare_recording_shutdown/run.py','--case',case,'--stop','session','--family','ipv4']))
cases += [(f'health-{backend}-{family}',['python3','-B','scripts/qemu_data_link_health/run.py','--backend',backend,'--family',family]) for backend in ['native','usbfs','virtual'] for family in ['ipv4','ipv6']]
cases += [(f'secure-{backend}-{family}',['python3','-B','scripts/qemu/run-secure-qemu.py','--backend',backend,'--family',family]) for backend in ['virtual','usbfs','native'] for family in ['ipv4','ipv6']]
cases += [(f'agent-native-{family}',['python3','-B','scripts/qemu/run-agent-qemu.py','--family',family]) for family in ['ipv4','ipv6']]
cases += [('sigterm',['python3','-B','scripts/qemu/run-sigterm-qemu.py'])]
records=[]
for name,argv in cases:
    assert git('rev-parse','HEAD')==args.head and not git('status','--porcelain') and inputs()==before
    command=argv+['--kernel','6.8.0-106-generic','--evidence',str(out/name)]
    print(json.dumps(dict(start=name,utc=datetime.datetime.now(datetime.timezone.utc).isoformat())),flush=True)
    with (out/(name+'.runner.log')).open('wb') as log:result=subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT)
    summary_path=out/name/'summary.json';summary=json.loads(summary_path.read_text()) if summary_path.is_file() else None
    actual={name:hashlib.sha256((root/'target/release'/name).read_bytes()).hexdigest() for name in expected_binaries}
    unchanged=inputs()==before and git('rev-parse','HEAD')==args.head and not git('status','--porcelain')
    row=dict(case=name,command=command,exit_code=result.returncode,unchanged=unchanged,binary_matches_software=actual==expected_binaries,binary_sha256=actual,directory=str(out/name),summary_result=summary.get('result') if summary else None,guest_exit_codes=summary.get('guest_exit_codes') if summary else None)
    records.append(row);save('results.json',records);print(json.dumps(row),flush=True)
    if result.returncode or not unchanged or actual!=expected_binaries or row['summary_result']!='PASS':
        save('inputs-after.json',inputs());save('result.json',dict(result='FAIL',head=args.head,runs=records,remaining_unrun=[n for n,_ in cases[len(records):]],inputs_unchanged=inputs()==before));sys.exit(1)
save('inputs-after.json',inputs());save('result.json',dict(result='PASS',head=args.head,input_count=len(before),runs=records,inputs_unchanged=inputs()==before,prepare_runs=8,health_runs=6,legacy_runs=9,notes=['Twenty-three runs, not diagnostic predicate counts.','Guest exits and exact cleanup/attempt proof remain independently checked per runner.']))
