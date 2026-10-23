from pathlib import Path
import subprocess, json, hashlib, datetime, sys

repo = Path('/home/ubuntu/github/usbip-client')
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo)
base = Path('/tmp') / f'rustusb-guardian-{head[:7]}-20261023'
base.mkdir(exist_ok=False)
paths = [p for p in subprocess.check_output(['git', 'ls-files'], cwd=repo, text=True).splitlines()
         if p in ['Cargo.toml', 'Cargo.lock'] or p.startswith(('crates/', 'vendor/', 'scripts/', '.github/workflows/'))]
def fingerprint():
    return {p: {'bytes': (repo / p).stat().st_size, 'sha256': hashlib.sha256((repo / p).read_bytes()).hexdigest()} for p in paths}
freeze = fingerprint()
(base / 'freeze.json').write_text(json.dumps({'head': head, 'working_tree_dirty': False, 'files': freeze}, indent=2) + '\n')
command = ['python3', 'scripts/qemu/run-guardian-qemu.py', '--evidence', str(base / 'run')]
started = datetime.datetime.now(datetime.timezone.utc).isoformat()
print(json.dumps({'state': 'STARTED', 'head': head, 'base': str(base), 'frozen_inputs': len(freeze), 'command': command}), flush=True)
with (base / 'runner.log').open('wb') as log:
    result = subprocess.run(command, cwd=repo, stdout=log, stderr=subprocess.STDOUT)
row = {'head': head, 'command': command, 'started_at': started, 'finished_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
       'exit_code': result.returncode, 'source_guard': fingerprint() == freeze,
       'head_guard': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip() == head,
       'worktree_clean': not subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo)}
row['result'] = 'PASS' if result.returncode == 0 and all(row[k] for k in ['source_guard', 'head_guard', 'worktree_clean']) else 'FAIL'
(base / 'sequence.json').write_text(json.dumps(row, indent=2) + '\n')
print(json.dumps(row), flush=True)
sys.exit(0 if row['result'] == 'PASS' else 1)
