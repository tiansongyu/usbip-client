"""Constructed completion predicates; no source mutation or actual USB operations."""
import copy,hashlib,importlib.util,json,re,subprocess
from pathlib import Path
repo=Path('/home/ubuntu/github/usbip-client');folder=repo/'scripts/qemu_prepare_recording_shutdown';base=Path('/tmp/rustusb-prepare-stop-local-resource-b1de034-20261024/server-artifacts');out=Path('/tmp/rustusb-prepare-local-detach-fixtures-20261024');out.mkdir(exist_ok=False)
report={'scope':'real jq and pure Python predicate fixtures; b1 samples retained, constructed successful state is not runtime evidence','checks':[]}
def obj(name):return json.loads((base/name).read_text())
def record(ok,label,details):
 report['checks'].append({'check':label,'passed':bool(ok)})
 (out/(label+'.json')).write_text(json.dumps(details,indent=2)+'\n')
 if not ok:raise RuntimeError(label)
spec=importlib.util.spec_from_file_location('local_detach_fixture',folder/'verify_local.py');v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
fragment=(folder/'guest-local-fragments.sh').read_text();expr=re.search(r"local_stub_retired\(\) \{[\s\S]*?'([\s\S]*?)'",fragment)[1];host_expr=re.search(r"local_stub_available_host\(\) \{[\s\S]*?'([\s\S]*?)'",fragment)[1]
held=obj('root-held.json');during=obj('root-local-survivor-io.json');first=obj('root-local-after-detach.json');late=obj('root-local-empty.json');survivor=obj('local-survivor.json');original=next(r for r in held['exports'] if r['lease_id']==survivor['attempts'][0]['resource_id']);stub=during['exports'][0]['stub'];target_proof=during['terminals'][0]['terminal'];baseline=obj('host-before.json');hid=original['registration']['generation']['bus_id']
# This completion view is assembled solely for testing exact identity rejection.
complete={'schema_version':2,'records':[],'exports':[copy.deepcopy(original)],'terminals':[copy.deepcopy(during['terminals'][0]),next(copy.deepcopy(t) for t in late['terminals'] if t['terminal']['resource_id']==stub['channel_id'])]}
variants={
'constructed-complete':(complete,lambda d:None,True),
'actual-b1-first-read':(first,lambda d:None,False),
'actual-b1-after-last-export-stop':(late,lambda d:None,False),
'missing-stub-terminal':(complete,lambda d:d['terminals'].pop(),False),
'wrong-stub-resource':(complete,lambda d:d['terminals'][1]['terminal'].update(resource_id='different'),False),
'wrong-stub-correlation':(complete,lambda d:d['terminals'][1]['terminal']['correlation'].update(attempt_id='different'),False),
'wrong-stub-owner':(complete,lambda d:d['terminals'][1]['terminal'].update(owner_uid=0),False),
'wrong-stub-key':(complete,lambda d:d['terminals'][1]['terminal'].update(device_key='native:other'),False),
'wrong-stub-proof':(complete,lambda d:d['terminals'][1]['terminal'].update(proof='native_stub_gone'),False),
'wrong-serial-terminal':(complete,lambda d:d['terminals'][0]['terminal']['correlation'].update(attempt_id='different'),False),
'replaced-export':(complete,lambda d:d['exports'][0].update(lease_id='different'),False),
'replaced-export-attribute':(complete,lambda d:d['exports'][0]['bound'].update(attribute_inode=999),False),
'root-stub-still-present':(complete,lambda d:d['exports'][0].update(stub=copy.deepcopy(stub)),False),
'export-already-closed':(complete,lambda d:d.update(exports=[]),False),
'extra-terminal':(complete,lambda d:d['terminals'].append(copy.deepcopy(d['terminals'][0])),False),
}
for name,(seed,mutate,expected) in variants.items():
 d=copy.deepcopy(seed);mutate(d);args=['jq','-es']
 for key,value in [('stub',[stub]),('survivor',[survivor]),('original',[held]),('during',[during])]:args+=['--argjson',key,json.dumps(value)]
 r=subprocess.run(args+[expr],input=json.dumps(d),capture_output=True,text=True);python=v.detach_root_complete(d,original,target_proof,stub)
 record((r.returncode==0)==expected and python==expected,'root-'+name,{'fixture':d,'expected':expected,'jq_exit':r.returncode,'jq_stdout':r.stdout,'jq_stderr':r.stderr,'python_result':python})
for name,data in [('empty',''),('duplicate',json.dumps(complete)+'\n'+json.dumps(complete)),('malformed','{')]:
 args=['jq','-es']
 for key,value in [('stub',[stub]),('survivor',[survivor]),('original',[held]),('during',[during])]:args+=['--argjson',key,json.dumps(value)]
 r=subprocess.run(args+[expr],input=data,capture_output=True,text=True);record(r.returncode!=0,'root-input-'+name,{'input':data,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
expected_host=v.detach_host_expected(baseline,hid)
variants={
'constructed-complete':(expected_host,lambda d:None,True),
'actual-b1-address-zero':(obj('host-local-after-detach.json'),lambda d:None,False),
'global-restored-too-early':(baseline,lambda d:None,False),
'wrong-address':(expected_host,lambda d:d[0].update(devnum='0'),False),
'new-directory-generation':(expected_host,lambda d:d[0].update(generation='23:999999'),False),
'wrong-config':(expected_host,lambda d:d[0].update(configuration=''),False),
'wrong-alt':(expected_host,lambda d:d[0]['interfaces'][0].update(alt='1'),False),
'still-used':(expected_host,lambda d:d[0].update(usbip_status=2),False),
'wrong-original-serial':(expected_host,lambda d:d[1]['interfaces'][0].update(driver=''),False),
}
for name,(seed,mutate,expected) in variants.items():
 d=copy.deepcopy(seed);mutate(d);r=subprocess.run(['jq','-es','--arg','hid',hid,'--argjson','before',json.dumps([baseline]),host_expr],input=json.dumps(d),capture_output=True,text=True);record((r.returncode==0)==expected and (d==expected_host)==expected,'host-'+name,{'fixture':d,'expected':expected,'jq_exit':r.returncode,'jq_stdout':r.stdout,'jq_stderr':r.stderr})
for name,data in [('empty',''),('duplicate',json.dumps(expected_host)+'\n'+json.dumps(expected_host))]:
 r=subprocess.run(['jq','-es','--arg','hid',hid,'--argjson','before',json.dumps([baseline]),host_expr],input=data,capture_output=True,text=True);record(r.returncode!=0,'host-input-'+name,{'input':data,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
report['sources']={name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in ['guest-local-fragments.sh','verify_local.py']};report['check_count']=len(report['checks']);report['passed']=all(c['passed'] for c in report['checks']);(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':report['passed'],'checks':report['check_count'],'output':str(out)}))
