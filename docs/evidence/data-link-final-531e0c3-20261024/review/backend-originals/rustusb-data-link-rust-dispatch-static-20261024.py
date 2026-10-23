import ast, hashlib, json, re, subprocess, sys
from pathlib import Path
ROOT = Path('/home/ubuntu/github/usbip-client')
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'scripts/qemu_data_link_health'))
import run
OUT = Path('/tmp/rustusb-data-link-rust-dispatch-static-20261024')
OUT.mkdir(exist_ok=False)
checks = []
def record(name, passed, detail=None):
    checks.append({'name': name, 'passed': bool(passed), 'detail': detail})
def command(name, argv):
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    record(name, result.returncode == 0, {'argv': argv, 'exit': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
def digest(path):
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
try:
    sources = sorted((ROOT / 'scripts/qemu_data_link_health').iterdir())
    for path in sources:
        if path.suffix == '.py':
            ast.parse(path.read_text())
            record('AST ' + path.name, True)
    old = json.loads(Path('/tmp/rustusb-data-link-native-handoff-20261024.json').read_text())['generated_init']['sha256']
    for backend in ('native', 'usbfs', 'virtual'):
        text = run.guest_source(backend)
        generated = OUT / (backend + '-init.sh')
        generated.write_text(text)
        command(backend + ' generated dash syntax', ['dash', '-n', str(generated)])
        command(backend + ' generated ShellCheck', ['shellcheck', '-s', 'sh', str(generated)])
        if backend == 'native':
            record('Native generated init remains byte-identical to e1529ea first-run source', hashlib.sha256(text.encode()).hexdigest() == old)
        joined = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#')).replace('\\\n', ' ')
        expressions = []
        for match in re.finditer(r"'([^']*)'", joined):
            prefix = joined[joined.rfind('\n', 0, match.start()) + 1:match.start()]
            if not prefix.rstrip().endswith('sed') and ('jq ' in prefix or 'wait_sessions ' in prefix) and ('{' in match[1] or '.' in match[1] or 'length' in match[1]):
                expressions.append(match[1])
        for number, expression in enumerate(expressions, 1):
            argv = ['jq', '-n']
            for var in sorted(set(re.findall(r'\$([A-Za-z_]\w*)', expression))):
                argv += ['--argjson', var, 'null']
            argv += ['if false then (' + expression + ') else true end']
            result = subprocess.run(argv, capture_output=True, text=True)
            record(backend + ' jq compile ' + str(number), result.returncode == 0, {'expression': expression, 'stderr': result.stderr})
        (OUT / (backend + '-jq.json')).write_text(json.dumps(expressions, indent=2) + '\n')
    command('three backend CLI help', ['python3', '-B', 'scripts/qemu_data_link_health/run.py', '--help'])
    command('tracked diff whitespace', ['git', 'diff', '--check'])
except Exception as error:
    record('static checker exception', False, repr(error))
report = {'scope': 'Static Python/generated shell/jq checks only; no Cargo/QEMU/USB and no runtime verdict.',
          'all_pass': all(item['passed'] for item in checks), 'checks': checks,
          'sources': [digest(p) for p in sorted((ROOT / 'scripts/qemu_data_link_health').iterdir()) if p.is_file()],
          'generated': [digest(p) for p in sorted(OUT.glob('*-init.sh'))]}
(OUT / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'path': str(OUT / 'report.json'), 'all_pass': report['all_pass'], 'checks': len(checks), 'failures': [x for x in checks if not x['passed']]}))
raise SystemExit(0 if report['all_pass'] else 1)
