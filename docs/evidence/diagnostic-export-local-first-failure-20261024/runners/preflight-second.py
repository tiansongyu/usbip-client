from pathlib import Path
import datetime, hashlib, json, subprocess, sys

repo = Path('/tmp/rustusb-diagnostic-local-checkout-20261024')
out = Path('/tmp/rustusb-diagnostic-local-second-20261024')
out.mkdir(exist_ok=False)
full = Path('/home/ubuntu/github/usbip-client/target/release/rustusb')
default = Path('/tmp/rustusb-diagnostic-default-build-20261024/target/release/rustusb')
expected = {str(full): '67fff5700d3282f711659c8b3fae363d5ee4f80d9a8bfdd42476dba388dfc419', str(default): 'a6d12305506c97fda617be0f11f7fceb28d7d3469ac8e33c498a97ca5807a99f'}
rows = json.loads(Path('/tmp/rustusb-diagnostic-software-first-20261024/inputs-before.json').read_text())

def git(*args):
    return subprocess.check_output(['git', *args], cwd=repo, text=True).strip()

def save(name, value):
    (out/name).write_text(json.dumps(value, indent=2) + '\n')

def guards():
    return dict(head=git('rev-parse', 'HEAD'), clean=git('status', '--porcelain') == '',
                source_matches=all(hashlib.sha256((repo/r['path']).read_bytes()).hexdigest() == r['sha256'] for r in rows),
                binary_hashes={name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in expected})

before = guards()
assert before['clean'] and before['source_matches'] and before['binary_hashes'] == expected, before
save('start.json', dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), scope='Actual clean2ca5f5b process harness with externally selected same191-source all/default release ELFs; no build or host USB.', guards=before, compiled_input_count=len(rows)))
(out/'runner.py').write_bytes(Path(__file__).read_bytes())
commands = [
    ('diagnostic-all', ['python3', '-B', 'scripts/test-diagnostic-export.py', '--binary', str(full), '--evidence', str(out/'all-features')]),
    ('diagnostic-default', ['python3', '-B', 'scripts/test-diagnostic-export.py', '--binary', str(default), '--evidence', str(out/'default'), '--cases', 'refusals', 'local', 'agent-unavailable']),
    ('server-signals', ['python3', '-B', 'scripts/test-server-signals.py', '--skip-build', '--binary', str(full), '--evidence', str(out/'server-signals')]),
    ('server-backpressure', ['python3', '-B', 'scripts/test-virtual-backpressure.py', '--binary', str(full)]),
]
results = []
for name, command in commands:
    print(json.dumps(dict(start=name, command=command)), flush=True)
    with (out/(name+'.log')).open('wb') as log:
        process = subprocess.run(command, cwd=repo, stdout=log, stderr=subprocess.STDOUT)
    row = dict(name=name, command=command, exit_code=process.returncode)
    results.append(row)
    save('results.json', results)
    print(json.dumps(row), flush=True)
    if process.returncode:
        print('\n'.join((out/(name+'.log')).read_text().splitlines()[-30:]), flush=True)
        break
after = guards()
save('result.json', dict(result='PASS' if len(results)==len(commands) and all(r['exit_code']==0 for r in results) and after==before else 'FAIL', before=before, after=after, guards_unchanged=after==before, commands=results, remaining_unrun=[n for n,_ in commands[len(results):]]))
raise SystemExit(0 if len(results)==len(commands) and all(r['exit_code']==0 for r in results) and after==before else 1)
