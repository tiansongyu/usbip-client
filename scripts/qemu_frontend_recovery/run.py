#!/usr/bin/env python3
"""Actual native process SIGKILL/restart and query-only recovery, preserving E56."""
import argparse
import gzip
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True
ROOT=Path(__file__).resolve().parents[2]; HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('recovery_decisions',ROOT/'scripts/qemu_rule_decisions/run.py')
baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
legacy=baseline.baseline; actions=legacy.actions; base=baseline.base
DRIVER=None

def inputs():
    values=baseline.inputs()
    for p in HERE.iterdir():
        if p.is_file()and p.suffix in ['.py','.sh','.md']:values[str(p.relative_to(ROOT))]={'bytes':p.stat().st_size,'sha256':base.digest(p)}
    return dict(sorted(values.items()))

def guest():
    source=baseline.guest();replace=baseline.replace
    anchor='[ "${AGENT_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2\n'
    source=replace(source,anchor,anchor+(HERE/'guest.sh').read_text()+'\n')
    # Preserve the actual final HTTP body without changing its return status.
    source=replace(source,'    /usr/bin/curl --silent --show-error --fail-with-body --max-time 35 -X "$method" -H \'Content-Type: application/json\' --data "$body" "http://127.0.0.1:15888$route" >"$dest"\n','    local recovery_http_exit\n    if /usr/bin/curl --silent --show-error --fail-with-body --max-time 35 -X "$method" -H \'Content-Type: application/json\' --data "$body" "http://127.0.0.1:15888$route" >"$dest"; then recovery_http_exit=0;else recovery_http_exit=$?;fi\n    cp "$dest" "/tmp/gui-$GUI_LABEL-last-http.json"\n    return "$recovery_http_exit"\n')
    # Independent actual WebKit drivers permit a second read-only GUI concurrently.
    source=replace(source,'http://127.0.0.1:15888$route','http://127.0.0.1:${GUI_PORT:-15888}$route')
    source=replace(source,'--port=15888','--port=${GUI_PORT:-15888}')
    source=replace(source,'http://127.0.0.1:15888/status','http://127.0.0.1:${GUI_PORT:-15888}/status')
    source=replace(source,'    gui_open "rules-$AGENT_EPOCH" server\n','    if [ "$AGENT_EPOCH" = 1 ];then gui_open rules-precrash server;else gui_open "rules-$AGENT_EPOCH" server;fi\n')
    source=replace(source,"        gui_wait '.pending==null and .unresolved_rule!=null and (.rules.result|contains(\"Response unknown\"))' /tmp/gui-rule-unknown.json\n", "        gui_wait '.pending==null and .unresolved_rule!=null and (.rules.result|contains(\"Response unknown\"))' /tmp/gui-rule-unknown.json\n        recovery_capture rule-positive-pending\n        recovery_kill rule-positive\n")
    source=replace(source,"        RULE_ID=$(jq -er '.automation.rules[0].id' /tmp/gui-rule-late-commit.json)\n", "        RULE_ID=$(jq -er '.automation.rules[0].id' /tmp/gui-rule-late-commit.json)\n        gui_open rules-middle server\n        gui_wait '.recovery_writable and .unresolved_rule!=null and .pending==null' /tmp/recovery-rule-positive-restored.json\n        recovery_capture rule-positive-restored\n")
    source=replace(source,"    gui_wait '.pending==null and .unresolved_rule!=null and (.rules.result|contains(\"Response unknown\"))' /tmp/decision-unknown.json\n", "    gui_wait '.pending==null and .unresolved_rule!=null and (.rules.result|contains(\"Response unknown\"))' /tmp/decision-unknown.json\n    recovery_capture rule-negative-pending\n    recovery_kill rule-negative\n")
    source=replace(source,'    cp /var/lib/rustusb/agent/state.json /tmp/decision-after-restart.json\n','    cp /var/lib/rustusb/agent/state.json /tmp/decision-after-restart.json\n    gui_open rules-negative-queried server\n    gui_wait \' .recovery_writable and .unresolved_rule!=null and .pending==null\' /tmp/recovery-rule-negative-restored.json\n    recovery_capture rule-negative-restored\n')
    source=replace(source,'    cp /var/lib/rustusb/agent/state.json /tmp/decision-after-cli-query.json\n','    cp /var/lib/rustusb/agent/state.json /tmp/decision-after-cli-query.json\n    recovery_capture rule-negative-decided\n    recovery_kill rule-draft\n    gui_open rules-1 server\n    gui_wait \'.recovery_writable and .unresolved_rule==null and .last_rule_decision.decision.decision=="rejected" and .pending==null\' /tmp/recovery-rule-draft-restored.json\n    recovery_capture rule-draft-restored\n')
    source=replace(source,'    manifest; agent_start 1; denied hid\n','    manifest; agent_start 1; denied hid\n    gui_infra\n')
    source=replace(source,'            start_job "$profile"\n','            recovery_start_job "$profile"\n')
    source=replace(source,'[ "$ROLE" != server ] || gui_infra_stop\n','gui_infra_stop\n')
    return source

def payload(work,kernel,certificates,role):
    archive,provenance=baseline.old_payload(work,kernel,certificates,role)
    if role=='server':return archive,provenance
    root=work/'root';actions.install_native_automation(work,root,provenance,DRIVER)
    shutil.copyfile(root/'init',work/'generated-agent-init.sh')
    names=b'.\0'+b'\0'.join(str(p.relative_to(root)).encode()for p in root.rglob('*'))+b'\0'
    plain,archive=work/'recovery.cpio',work/'recovery.cpio.gz'
    with plain.open('wb')as stream:
        result=subprocess.run(['cpio','--null','-o','--format=newc','--owner=0:0'],input=names,cwd=root,stdout=stream,stderr=subprocess.PIPE,check=True)
    (work/'recovery-cpio.stderr').write_bytes(result.stderr)
    with plain.open('rb')as source,gzip.open(archive,'wb',compresslevel=1)as dest:shutil.copyfileobj(source,dest)
    return archive,provenance

def verify(evidence,args):
    facts=baseline.verify(evidence,args);checks=[]
    def require(value,label):
        if not value:raise RuntimeError('frontend process recovery: '+label)
        checks.append(label)
    def folder(role):return evidence/(role+'-artifacts')
    def get(role,name):return json.loads((folder(role)/name).read_text())
    def projected(value):
        if isinstance(value, bool) or value is None:return value
        if isinstance(value, int):return str(value)
        if isinstance(value, list):return [projected(x)for x in value]
        if isinstance(value, dict):return {k:projected(v)for k,v in value.items()}
        return value
    gui_sha=base.digest(ROOT/'apps/rustusb-gui/target/release/rustusb-gui')
    planned=[('server','rules-precrash','rule-positive'),('server','rules-middle','rule-negative'),('server','rules-negative-queried','rule-draft'),('client','recovery-start-before','start')]
    normal=[('server','rules-no-policy'),('server','rules-1'),('server','rules-2'),('client','recovery-competing'),('client','recovery-bad'),('client','recovery-start-after')]
    for role,label in [(r,l)for r,l,_ in planned]+normal:
        actor=(folder(role)/('gui-'+label+'-actor.txt')).read_text()
        require(bool(re.search(r'^Uid:\s+1000\s+1000\s+1000\s+1000$',actor,re.M))and all(re.search(r'^'+c+r':\s+0000000000000000$',actor,re.M)for c in ['CapInh','CapPrm','CapEff','CapAmb'])and gui_sha in actor and 'TARGET=/dev/bus/usb/'not in actor,label+': actual UID1000/ELF/no capabilities or USB FD')
        driver=(folder(role)/('gui-'+label+'-driver-actor.txt')).read_text();require(base.digest(DRIVER)in driver and bool(re.search(r'^Uid:\s+1000\s+1000\s+1000\s+1000$',driver,re.M)),label+': actual WebKit driver UID/ELF')
        require(get(role,'gui-'+label+'-session.json')['value']['capabilities']['browserName']=='wry',label+': actual native WebKit session')
    for role,label,tag in planned:
        require((folder(role)/('gui-'+label+'.wait')).read_text().strip()=='137'and 'actual_wait=137'in(folder(role)/('recovery-'+tag+'-kill.txt')).read_text(),label+': planned actual native SIGKILL and reap137')
        require('local worker joined'not in(folder(role)/('gui-'+label+'.stderr')).read_text(),label+': crash is not a cooperative join')
    for role,old,new in [('client','recovery-start-before','recovery-start-after'),('server','rules-precrash','rules-middle'),('server','rules-middle','rules-negative-queried'),('server','rules-negative-queried','rules-1')]:
        require((folder(role)/('gui-'+old+'-actor.txt')).read_text().splitlines()[0] != (folder(role)/('gui-'+new+'-actor.txt')).read_text().splitlines()[0],old+': distinct actual replacement GUI PID')
    for role,label in normal:
        require((folder(role)/('gui-'+label+'.wait')).read_text().strip()=='0'and 'local worker joined'in(folder(role)/('gui-'+label+'.stderr')).read_text()and 'protocol=WM_DELETE_WINDOW'in(folder(role)/('gui-'+label+'-close.txt')).read_text(),label+': actual native close0/local join')
    for role in ['client','server']:require('GUI_FORCED_CHILD'not in(evidence/(role+'-evidence.log')).read_text(),role+': no forced teardown marker')
    pairs=[('client','start','unresolved','start'),('server','rule-positive','unresolved_rule','rule'),('server','rule-negative','unresolved_rule','rule')]
    for role,tag,field,slot in pairs:
        pending=get(role,'recovery-start-pending.json'if tag=='start'else'gui-rule-unknown.json'if tag=='rule-positive'else'decision-unknown.json')['value']
        restored=get(role,'recovery-'+tag+'-restored.json')['value'];original=pending[field]
        require(original==restored[field]and restored['recovery_writable']and restored['pending']is None,tag+': new process restores exact original identity/payload/protocol under a new checked writer')
        before=get(role,'recovery-'+tag+'-pending-document.json');after=get(role,'recovery-'+tag+'-restored-document.json')
        require(before==after and projected(before[slot])=={'state':'retained','value':original},tag+': actual on-disk original precedes crash and is unchanged by restart')
        require(set(before)=={'schema_version','operator_uid','broker_uid','endpoint','start','rule','last'}and before['schema_version']==1 and before['operator_uid']==1000 and before['broker_uid']==2000 and bool(re.fullmatch('[0-9a-f]{64}',before['endpoint'])),tag+': strict local version/UID/broker/endpoint context')
        file=(folder(role)/('recovery-'+tag+'-pending-file.txt')).read_text();require('DIRECTORY uid=1000 mode=700'in file and 'FILE uid=1000 mode=600 links=1'in file,tag+': actual private state directory/file owner/mode/link')
    client_pending=get('client','recovery-start-pending.json')['value']['unresolved'];identity=client_pending['request_id']
    before=get('client','recovery-start-agent-before.json');late=get('client','recovery-start-late.json');records=[r for r in late['requests']if r['id']==identity]
    require(before['requests']==[]and len(records)==1 and records[0]['uid']==1000 and records[0]['profile']=='hid', 'actual crashed Start commits exactly its original request once')
    require(get('client','recovery-start-agent-before-query.json')==get('client','recovery-start-agent-after-query.json'),'original native Start query makes no new journal mutation')
    resolved=get('client','recovery-start-resolved.json')['value'];require(resolved['unresolved']is None and resolved['status'].startswith('Resolved original job '),'actual restarted GUI resolves only original Start')
    require(get('client','recovery-start-resolved-document.json')['start']=={'state':'empty'},'only successful local settlement clears stored Start slot')
    for tag in ['recovery-competing','recovery-bad']:
        view=get('client','recovery-'+tag+'-after.json')['value'];require(not view['recovery_writable']and view['unresolved']is None and view['pending']is None,tag+': unavailable store creates no new UUID/accepted command')
        denied=get('client','recovery-'+tag+'-denied.json')['value'];require(set(denied)=={'error'}and 'Local request recovery'in denied['error'],tag+': actual Rust bridge refusal')
        require(get('client','recovery-'+tag+'-agent-before.json')==get('client','recovery-'+tag+'-agent-after.json'),tag+': no service mutation')
    saved=get('server','recovery-rule-negative-decided-document.json');restored=get('server','recovery-rule-draft-restored-document.json');view=get('server','recovery-rule-draft-restored.json')['value']
    require(saved==restored and saved['rule']=={'state':'empty'}and saved['last']['state']=='retained'and projected(saved['last']['value'])==view['last_rule_decision'],'settled local refusal receipt and exact original draft survive another full native process crash')
    require(view['unresolved_rule']is None and view['last_rule_decision']['decision']=={'decision':'rejected','sequence':'1','failure':'state'},'restored local last receipt remains qualified and separate from pending work')
    # Disk stores exact integers; native projection deliberately uses decimal strings.
    require(saved['last']['value']['decision']['sequence']==1,'on-disk refusal sequence exact integer')
    for role,n in [('client','recovery-start-confirm.json'),('client','recovery-start-query-confirm.json')]:require(get(role,n)['value']['focus']=='cancel',n+': actual default Cancel')
    require(identity in get('client','recovery-start-query-confirm.json')['value']['text'],'original Start query confirmation names its exact original UUID')
    facts['frontend_process_recovery']={'checks':checks,'assertion_count':len(checks),'actual_native_processes':len(planned)+len(normal),'planned_native_kill137':len(planned),'cooperative_native_exit0':len(normal),'scope':'Actual whole native process restarts, both original UUID namespaces, original rule protocol/payload/last draft, competing writer/private directory refusal, original queries and all E56/Native I/O/Root proofs. No power-loss, hardware, general USB crash recovery or full phase claim.'}
    return facts

def main():
    global DRIVER
    p=argparse.ArgumentParser(add_help=False);p.add_argument('--driver',type=Path,required=True);own,remaining=p.parse_known_args();DRIVER=own.driver.resolve();baseline.DRIVER=DRIVER;legacy.DRIVER=DRIVER
    assert DRIVER.is_file()and DRIVER.read_bytes()[:4]==b'\x7fELF'
    sys.argv=[sys.argv[0],*remaining];p=argparse.ArgumentParser(add_help=False);p.add_argument('--evidence',type=Path,required=True);args,_=p.parse_known_args(remaining)
    gui_before=base.digest(ROOT/'apps/rustusb-gui/target/release/rustusb-gui');driver_before=base.digest(DRIVER)
    base.inputs,base.guest_source,base.payload,base.verify=inputs,guest,payload,verify
    result=base.main(guest_memory_mib=4096)
    path=args.evidence/'summary.json';value=json.loads(path.read_text());value.update(gui_sha256=gui_before,driver_sha256=driver_before,gui_binary_guard=base.digest(ROOT/'apps/rustusb-gui/target/release/rustusb-gui')==gui_before,driver_guard=base.digest(DRIVER)==driver_before)
    if not value['gui_binary_guard']or not value['driver_guard']:value['result']='FAIL';result=1
    path.write_text(json.dumps(value,indent=2)+'\n');return result
if __name__=='__main__':raise SystemExit(main())
