from pathlib import Path
import hashlib, importlib.util, json, subprocess, sys

ROOT = Path('/home/ubuntu/github/usbip-client')
OUT = Path('/tmp/rustusb-diagnostic-gate-template-check-20261024')
OUT.mkdir(exist_ok=False)
path = ROOT/'scripts/qemu_diagnostic_export/run.py'
spec = importlib.util.spec_from_file_location('diagnostic_gate_template_actual', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
generated, provenance = module.diagnostic_gate_source()
original = (ROOT/provenance['original_path']).read_bytes().decode('utf-8')
(OUT/'original-gate.c').write_bytes(original.encode('utf-8'))
(OUT/'generated-diagnostic-gate.c').write_bytes(generated.encode('utf-8'))
(OUT/'run.py').write_bytes(path.read_bytes())
(OUT/'gate-source-provenance.json').write_text(json.dumps(provenance, indent=2)+'\n')
(OUT/'checker.py').write_bytes(Path(__file__).read_bytes())
results = []

def command(name, argv, expected=0):
    with (OUT/(name+'.log')).open('wb') as log:
        process = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT)
    row = dict(name=name, command=argv, exit_code=process.returncode, expected=expected)
    results.append(row)
    (OUT/'commands.json').write_text(json.dumps(results, indent=2)+'\n')
    if process.returncode != expected:
        raise RuntimeError(name+' returned '+str(process.returncode)+', expected '+str(expected))

def predicate(source, argc, name):
    # Exact actual guard and actual node_path, with all device/ptrace/bootstrap
    # code omitted. The compiled host fixture performs only string validation.
    node = source[source.index('static bool node_path('):source.index('\nint main(int argc')]
    start = source.index('    if (argc != '+str(argc)+' ||')
    end = source.index(') {\n        const char *message = "prepare-gate: invalid fixed test invocation', start)+1
    guard = source[start:end]
    text = '#include <stdbool.h>\n#include <stddef.h>\n#include <string.h>\n#define DIRECTORY "/run/prepare-gate"\n'+node+'\nint main(int argc, char **argv) {\n'+guard+' return 1;\nreturn 0;\n}\n'
    path = OUT/(name+'.c')
    path.write_text(text)
    executable = OUT/name
    command('compile-'+name, ['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(path),'-o',str(executable)])
    return executable

try:
    # Compile the complete generated tool, but never execute that privileged
    # guest-only program on the host. Only the isolated predicates run below.
    command('compile-complete-generated', ['cc','-std=c11','-O2','-Wall','-Wextra','-Werror',str(OUT/'generated-diagnostic-gate.c'),'-o',str(OUT/'guest-gate-not-executed')])
    old = predicate(original, 15, 'original-argv-only')
    new = predicate(generated, 21, 'diagnostic-argv-only')
    prefix = ['--directory','/run/prepare-gate','--usb-node','/dev/bus/usb/001/002','--','/usr/bin/setpriv','--reuid=2000','--regid=2000','--clear-groups','/usr/bin/rustusb']
    tail = ['agent','--config','/etc/rustusb/agent.json','run']
    logging = ['--log-directory','/var/log/rustusb/agent','--log-max-bytes','262144','--log-retain','4']
    legacy = prefix+tail
    diagnostic = prefix+logging+tail
    command('original-valid15', [str(old),*legacy])
    command('original-rejects-actual-first-run21', [str(old),*diagnostic], 1)
    command('diagnostic-valid-fixed21', [str(new),*diagnostic])
    command('diagnostic-rejects-old15', [str(new),*legacy], 1)
    for index in range(len(diagnostic)):
        altered = list(diagnostic)
        altered[index] += '-INVALID'
        command('reject-mutated-argv-'+str(index+1), [str(new),*altered], 1)
    command('reject-extra-argument', [str(new),*diagnostic,'--extra'], 1)
    command('reject-missing-argument', [str(new),*diagnostic[:-1]], 1)
    report = dict(result='PASS', commands=len(results), source=provenance,
                  scope='Full generated C compiled only; actual isolated C string allowlist predicates executed, no guest/product CLI/ptrace/device operations. Does not prove QEMU acceptance.',
                  generated_sha256=hashlib.sha256(generated.encode('utf-8')).hexdigest())
except Exception as error:
    report = dict(result='FAIL', commands=len(results), error=str(error), source=provenance)
    raise
finally:
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
