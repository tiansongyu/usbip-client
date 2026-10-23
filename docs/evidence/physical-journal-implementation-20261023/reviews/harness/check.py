from pathlib import Path
import ast, hashlib, json, subprocess, tempfile

repo=Path('/home/ubuntu/github/usbip-client')
base=Path('/tmp/rustusb-journal-harness-review-20261023')
checks=[]
def check(name, passed):
    checks.append({'name':name,'result':'PASS' if passed else 'FAIL'})

def admin_loop(path):
    tree=ast.parse(path.read_text(), filename=str(path))
    verify=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='verify')
    loop=next(node for node in verify.body if isinstance(node,ast.For) and isinstance(node.iter,ast.Name) and node.iter.id=='admin')
    return compile(ast.fix_missing_locations(ast.Module(body=[loop],type_ignores=[])),str(path),'exec')

old=admin_loop(base/'run-journal-qemu.py.before')
new=admin_loop(repo/'scripts/qemu/run-journal-qemu.py')
def host_accept(code,row):
    results=[]
    scope={'json':json,'admin':[row],'check':lambda name,condition:results.append(bool(condition))}
    exec(code,scope)
    return results==[True]

reasons={
    ('pending-denied',1000):'physical pending records require root',
    ('reconcile-denied',1000):'physical reconciliation requires root',
    ('pending-denied',1001):'caller UID is not approved by the administrator',
    ('reconcile-denied',1001):'caller UID is not approved by the administrator',
    ('unrestored-refused',0):'physical original configuration, full interface/alternate/driver set, or generation is not restored; unresolved grant retained',
}
positive=[]
for (phase,uid),reason in reasons.items():
    row={'phase':phase,'uid':uid,'exit_code':1,'stdout':json.dumps({'ok':False,'error':'state: '+reason}),'stderr':'trace error'}
    positive.append((row,reason))
    check('host_accept_specific_'+phase+'_'+str(uid),host_accept(new,row))
    wrong={**row,'stdout':json.dumps({'ok':False,'error':'unrecognized subcommand'})}
    check('old_accepts_wrong_error_'+phase+'_'+str(uid),host_accept(old,wrong))
    check('new_rejects_wrong_error_'+phase+'_'+str(uid),not host_accept(new,wrong))
base_row,reason=positive[0]
variants={
    'exit_zero':{**base_row,'exit_code':0},
    'not_json':{**base_row,'stdout':'clap parse error'},
    'json_array':{**base_row,'stdout':'[]'},
    'json_boolean':{**base_row,'stdout':'false'},
    'ok_true':{**base_row,'stdout':json.dumps({'ok':True,'error':reason})},
    'missing_ok':{**base_row,'stdout':json.dumps({'error':reason})},
    'nonstring_error':{**base_row,'stdout':json.dumps({'ok':False,'error':[reason]})},
    'unknown_phase':{**base_row,'phase':'other'},
    'wrong_uid':{**base_row,'uid':0},
    'stderr_only':{**base_row,'stdout':'','stderr':reason},
    'multiple_json':{**base_row,'stdout':base_row['stdout']+'\n'+base_row['stdout']},
}
for name,row in variants.items(): check('host_rejects_'+name,not host_accept(new,row))

shell=(repo/'scripts/qemu/guest-journal-init.sh').read_text()
start=shell.index('admin_refusal() {')
end=shell.index('\n}\n',start)+3
helper=shell[start:end]
with tempfile.TemporaryDirectory(dir=base,prefix='fixture-') as temporary:
    root=Path(temporary); payload=root/'api.json'
    script=root/'helper.sh'
    script.write_text('set -eu\n'+helper.replace('/tmp/api.json',str(payload))+'\nadmin_refusal "$1" "$2"\n')
    for row,reason in positive:
        payload.write_text(row['stdout'])
        result=subprocess.run(['sh',str(script),str(row['exit_code']),reason],capture_output=True)
        check('guest_accept_specific_'+row['phase']+'_'+str(row['uid']),result.returncode==0)
    for name,row in variants.items():
        # UID/phase dispatch is in the guest call sites, not this helper.
        if name in ['unknown_phase','wrong_uid']: continue
        payload.write_text(row['stdout'])
        result=subprocess.run(['sh',str(script),str(row['exit_code']),positive[0][1]],capture_output=True)
        check('guest_rejects_'+name,result.returncode!=0)
    payload.write_text(json.dumps({'ok':False,'error':'unrecognized subcommand'}))
    result=subprocess.run(['sh',str(script),'1',positive[0][1]],capture_output=True)
    check('guest_rejects_wrong_error',result.returncode!=0)

for name in ['run-journal-qemu.py','run-physical-qemu.py','run-native-qemu.py']:
    ast.parse((repo/'scripts/qemu'/name).read_text());check(name+'_ast',True)
for shell_name in ['sh','bash']:
    result=subprocess.run([shell_name,'-n',str(repo/'scripts/qemu/guest-journal-init.sh')],capture_output=True)
    check(shell_name+'_syntax',result.returncode==0)
sc=subprocess.run(['shellcheck','--severity=warning',str(repo/'scripts/qemu/guest-journal-init.sh')],capture_output=True,text=True)
(base/'shellcheck.txt').write_text(sc.stdout+sc.stderr)
files=[]
for name in ['run-journal-qemu.py','guest-journal-init.sh']:
    path=repo/'scripts/qemu'/name;data=path.read_bytes()
    files.append({'path':str(path.relative_to(repo)),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mode':oct(path.stat().st_mode&0o777)})
report={'scope':'Static source review and extracted administrative refusal check fixtures only; no Cargo build, QEMU or USB operations.',
        'initial_files':json.loads((base/'before.json').read_text()),'final_files':files,
        'mustfix_corrected':'Nonzero arbitrary CLI failures previously counted as authorization or unrestored-baseline refusals. Both guest and host now require actual JSON ok=false and the phase/UID-specific error.',
        'checks':checks,'passed':sum(row['result']=='PASS' for row in checks),'total':len(checks),
        'shellcheck':{'exit_code':sc.returncode,'warning':'SC2034 unused loop attempt (existing bounded polling counter; no control-flow failure)','log':str(base/'shellcheck.txt')},
        'limitations':['Only simulated HID/FTDI with config1/alternate0; no ECM/nonzero-alt journal runtime acceptance.', 'Post-administrator-reconcile epoch verifies fresh ready/finish without IMPORT, not post-reconcile actual USB I/O.', 'No power-loss, OS-reboot, helper hard-kill, changed-generation or arbitrary forwarded-FD recovery proof.', 'The product never auto-restores the killed epoch; the guest administrator explicitly writes the two fixed test driver bind paths.']}
(base/'review.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'result':'PASS' if all(row['result']=='PASS' for row in checks) else 'FAIL','passed':report['passed'],'total':report['total'],'report':str(base/'review.json'),'files':files},indent=2))
raise SystemExit(0 if all(row['result']=='PASS' for row in checks) else 1)
