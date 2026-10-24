"""Static composition and actual shell-function fixture; no QEMU/USB/build."""
import ast
import difflib
import hashlib
import importlib.util
import json
import shlex
import shutil
import subprocess
from pathlib import Path

REPO = Path('/home/ubuntu/github/usbip-client')
OWN = REPO / 'scripts/qemu_prepare_recording_shutdown'
OUT = Path('/tmp/rustusb-prepare-saved-proof-binding-20261024')
CHECKS = []
def fact(p):
    b = p.read_bytes()
    return {'bytes':len(b), 'sha256':hashlib.sha256(b).hexdigest()}
def check(ok, label):
    CHECKS.append({'check':label, 'passed':bool(ok)})
    if not ok:
        raise RuntimeError(label)
def function(text):
    return 'check_target_proof() {\n' + text.split('check_target_proof() {\n',1)[1].split('\n}\n',1)[0] + '\n}\n'

before = json.loads((OUT / 'before-source-manifest.json').read_text())
source = {name: fact(OWN/name) for name in before}
for name in ['verify.py','verify_local.py','run.py','local.py','prepare-gate.c']:
    check(source[name] == before[name], 'strict verifier, dispatcher, composition and C unchanged: '+name)
old = (OUT/'before-guest-init.sh').read_text()
new = (OWN/'guest-init.sh').read_text()
old_local = (OUT/'before-guest-local-fragments.sh').read_text()
new_local = (OWN/'guest-local-fragments.sh').read_text()
expected = old.replace('check_target_proof() {\n','check_target_proof() {\n    # Judge the saved observation, never a later read of the live journal.\n    [ "$#" = 1 ] || return 2\n',1)
old_body = function(expected)
expected = expected.replace(old_body,old_body.replace('"$ROOT_FILE"','"$1"'),1)
expected = expected.replace('if check_target_proof; then proof=true; fi','if check_target_proof "/tmp/root-observe-$observation.json"; then proof=true; fi',1)
expected = expected.replace('    check_target_proof\n','    check_target_proof /tmp/root-after-release.json\n',1)
check(new == expected, 'global delta is only explicit saved-path binding and one-argument guard')
expected_local = old_local.replace('    check_target_proof\n','    check_target_proof /tmp/root-local-empty.json\n',1).replace('    check_target_proof\n','    check_target_proof /tmp/root-after-release.json\n',1)
check(new_local == expected_local, 'local delta is only two explicit saved-path arguments')
for name in ['guest-init.sh','guest-local-fragments.sh']:
    (OUT/(name+'.diff')).write_text(''.join(difflib.unified_diff((OUT/('before-'+name)).read_text().splitlines(keepends=True),(OWN/name).read_text().splitlines(keepends=True),fromfile='before/'+name,tofile='after/'+name)))
    shutil.copyfile(OWN/name,OUT/('after-'+name))
for p in OWN.glob('*.py'):
    ast.parse(p.read_text())
    check(True,'Python AST '+p.name)
spec = importlib.util.spec_from_file_location('prepare_saved_binding_static',OWN/'run.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
modes = [(c,t) for c in ['native-resource','usbfs-resource','usbfs-initializer'] for t in ['job','term']] + [(c,'session') for c in ['native-resource','native-available']]
for case,stop in modes:
    text = module.guest_source(case,stop)
    p = OUT/(case+'-'+stop+'.sh')
    p.write_text(text)
    for args in [['dash','-n',str(p)],['shellcheck','-S','warning','-e','SC2034',str(p)]]:
        result = subprocess.run(args,capture_output=True,text=True)
        (OUT/(case+'-'+stop+'-'+args[0]+'.log')).write_text(result.stdout+result.stderr)
        check(result.returncode==0,case+'/'+stop+' '+args[0])
    calls = [line.strip() for line in text.splitlines() if 'check_target_proof' in line and '()' not in line]
    wanted = ['check_target_proof /tmp/root-local-empty.json','check_target_proof /tmp/root-after-release.json'] if stop=='session' else ['if check_target_proof "/tmp/root-observe-$observation.json"; then proof=true; fi','check_target_proof /tmp/root-after-release.json']
    check(calls==wanted,case+'/'+stop+' all exact saved observation call paths')
    check('"$ROOT_FILE"' not in function(text),case+'/'+stop+' proof function cannot re-read live journal')

originals = {}
fixture_results = []
for backend,evidence,saved_names in [
    ('native',Path('/tmp/rustusb-prepare-stop-final-2222de8-20261024/prepare-native-resource-job'),('root-observe-1.json','root-observe-2.json')),
    ('usbfs',Path('/tmp/rustusb-prepare-stop-usbfs-resource-job-68cc3ad-20261024'),('root-held.json','root-after-release.json')),
]:
    d=OUT/('fixture-'+backend)
    d.mkdir(exist_ok=False)
    src=evidence/'server-artifacts'
    for original,name in [('selected-sessions.json','sessions.json'),(saved_names[0],'active.json'),(saved_names[1],'retired.json')]:
        originals[str(src/original)]=fact(src/original)
        shutil.copyfile(src/original,d/name)
    # Only the selected-sessions fixture path is adapted. Both function bodies
    # execute as actual shell code; saved/live states are distinct copied files.
    for version,body in [('old',function(old)),('new',function(new))]:
        body=body.replace('/tmp/selected-sessions.json',str(d/'sessions.json'))
        script='#!/bin/sh\nset -eu\nBACKEND='+shlex.quote(backend)+'\nROOT_FILE='+shlex.quote(str(d/'live.json'))+'\n'+body+'check_target_proof "$@"\n'
        (d/(version+'.sh')).write_text(script)
    for label,saved,live,old_rc,new_rc in [
        ('saved-active-live-retired','active.json','retired.json',0,1),
        ('saved-retired-live-active','retired.json','active.json',1,0),
        ('saved-retired-live-missing','retired.json',None,2,0),
        ('saved-missing-live-retired','absent.json','retired.json',0,2),
        ('missing-explicit-argument',None,'retired.json',0,2),
    ]:
        if live is None:
            (d/'live.json').unlink(missing_ok=True)
        else:
            shutil.copyfile(d/live,d/'live.json')
        row={'backend':backend,'fixture':label,'source':'actual saved original bytes; ROOT_FILE association deliberately diverged in isolated copies'}
        for version,wanted in [('old',old_rc),('new',new_rc)]:
            args=['dash',str(d/(version+'.sh'))]+([] if saved is None else [str(d/saved)])
            result=subprocess.run(args,capture_output=True,text=True)
            row[version]={'command':args,'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr,'expected_exit_code':wanted}
        (d/(label+'.json')).write_text(json.dumps(row,indent=2)+'\n')
        check(row['old']['exit_code']==old_rc and row['new']['exit_code']==new_rc,backend+' actual function '+label)
        fixture_results.append(row)
for name,expected in originals.items():
    check(fact(Path(name))==expected,'original evidence unchanged '+name)
check(all(fact(OWN/name)==value for name,value in source.items()),'all selected sources unchanged through static execution')
report={'schema_version':1,'passed':True,'checks':CHECKS,'check_count':len(CHECKS),
 'source':source,'original_fixture_inputs':originals,
 'scope':'Actual shell functions with copied saved/live divergence inputs, generated init syntax/ShellCheck, and exact source delta; not USB or runtime acceptance.',
 'runtime_failure_preserved':'2222de8 NativeResource/job remains FAIL and 22 other matrix scenarios unexecuted.',
 'fixture_results':fixture_results,'not_executed':['Cargo','compiler','QEMU','host USB','Git']}
(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'passed':True,'checks':len(CHECKS),'source':source,'output':str(OUT)}))
