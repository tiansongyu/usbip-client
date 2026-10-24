from pathlib import Path
import datetime, hashlib, json, subprocess, sys

root = Path('/home/ubuntu/github/usbip-client')
out = Path('/tmp/rustusb-diagnostic-dev-first-20261024')
out.mkdir(exist_ok=False)

def inputs():
    paths = [root / n for n in ['Cargo.toml', 'Cargo.lock', 'rust-toolchain.toml', 'scripts/check.sh']]
    for base in ['crates', 'vendor/nusb']:
        paths += [p for p in (root/base).rglob('*') if p.is_file() and (p.suffix == '.rs' or p.name == 'Cargo.toml') and 'target' not in p.parts]
    return [dict(path=str(p.relative_to(root)), bytes=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(set(paths))]

def save(name, value):
    (out/name).write_text(json.dumps(value, indent=2) + '\n')

before = inputs()
save('inputs-before.json', before)
for row in before:
    dest = out/'source'/row['path']
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes((root/row['path']).read_bytes())
save('start.json', dict(head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(), status=subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True), utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), scope='First actual development compilation and focused new diagnostic tests. Dirty implementation; not full software gate, CLI subprocess or QEMU acceptance.'))
(out/'runner.py').write_bytes(Path(__file__).read_bytes())
commands = [
    ('diagnostics-default', ['cargo','test','-p','rustusb-diagnostics','--lib','--locked','--offline','--','--nocapture']),
    ('diagnostics-secure', ['cargo','test','-p','rustusb-diagnostics','--all-features','--lib','--locked','--offline','--','--nocapture']),
    ('service', ['cargo','test','-p','rustusb-service','--all-features','--lib','--locked','--offline','diagnostic','--','--nocapture']),
    ('native-reap', ['cargo','test','-p','rustusb-native','--lib','--locked','--offline','actual_reap','--','--nocapture']),
    ('cli-check', ['cargo','check','-p','rustusb-cli','--all-features','--all-targets','--locked','--offline']),
]
results = []
for name, command in commands:
    print(json.dumps(dict(start=name, command=command)), flush=True)
    with (out/(name+'.log')).open('wb') as log:
        process = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT)
    row = dict(name=name, command=command, exit_code=process.returncode, inputs_unchanged=inputs()==before)
    results.append(row)
    save('results.json', results)
    print(json.dumps(row), flush=True)
    if process.returncode or inputs()!=before:
        print('\n'.join((out/(name+'.log')).read_text().splitlines()[-65:]), flush=True)
        save('inputs-after.json', inputs())
        save('result.json', dict(result='FAIL', runs=results, input_count=len(before), inputs_unchanged=inputs()==before, remaining_unrun=[n for n,_ in commands[len(results):]]))
        sys.exit(1)
save('inputs-after.json', inputs())
save('result.json', dict(result='PASS', runs=results, input_count=len(before), inputs_unchanged=inputs()==before))
