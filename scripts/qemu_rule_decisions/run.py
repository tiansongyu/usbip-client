#!/usr/bin/env python3
"""Real native GUI settled CAS refusal, lost reply and cooperative agent restart."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('decision_gui_baseline',ROOT/'scripts/qemu_gui_rules/run.py')
baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
base=baseline.base;old_inputs,old_guest,old_payload=baseline.inputs,baseline.guest,baseline.payload
DRIVER=None

def inputs():
    values=old_inputs()
    for p in HERE.iterdir():
        if p.is_file()and p.suffix in ['.py','.sh','.md']:values[str(p.relative_to(ROOT))]={'bytes':p.stat().st_size,'sha256':base.digest(p)}
    return dict(sorted(values.items()))
def replace(text,old,new):
    if text.count(old)!=1:raise RuntimeError('unique settled fixture anchor missing: '+repr(old))
    return text.replace(old,new,1)
def guest():
    source=old_guest();anchor='[ "${AGENT_COMMON:-}" = 1 ] && [ "$BACKEND" = native ] || exit 2\n'
    source=replace(source,anchor,anchor+(HERE/'guest.sh').read_text()+'\n')
    source=replace(source,'--automation-policy /etc/rustusb/automation.json','--automation-decisions-policy /etc/rustusb/automation.json')
    source=replace(source,"'{schema_version:1,broker_uid:2000,grants:[{operator_uid:1000,profile:\"server\",devices:$keys[0],triggers:[\"local_profile_ready\"],rearm:[\"once\"],max_rules:4}]}'", "'{schema_version:2,settled_rule_results:true,broker_uid:2000,grants:[{operator_uid:1000,profile:\"server\",devices:$keys[0],triggers:[\"local_profile_ready\"],rearm:[\"once\"],max_rules:4}]}'")
    source=replace(source,"'.schema_version==3 and .jobs==[] and .automation.rules==[]'", "'.schema_version==4 and .jobs==[] and .automation.rules==[] and .rule_rejections=={sequence:0,entries:[]}'")
    source=replace(source,"        agent rule server disable \"$RULE_ID\" --expected-revision 2 > /tmp/gui-rule-concurrent-disable.jsonl\n", "        settled_refresh false\n        agent rule server disable \"$RULE_ID\" --expected-revision 2 > /tmp/gui-rule-concurrent-disable.jsonl\n        settled_refusal\n")
    return source

def verify(evidence,args):
    # All original Native264, E55 positive/unknown/CRUD, I/O and exact Root proofs.
    facts=baseline.verify(evidence,args,{'server final':4,'server held0':4,'server held1':4})
    folder=evidence/'server-artifacts';get=lambda n:json.loads((folder/n).read_text());checks=[]
    def require(value,label):
        if not value:raise RuntimeError('settled rule decision: '+label)
        checks.append(label)
    policy=get('automation-policy.json')
    require(policy=={'schema_version':2,'settled_rule_results':True,'broker_uid':2000,'grants':[{'operator_uid':1000,'profile':'server','devices':get('keys.json'),'triggers':['local_profile_ready'],'rearm':['once'],'max_rules':4}]},'actual explicit root2 complete-pool delegation')
    before=get('decision-before.json');late=get('decision-late.json');unknown=get('decision-unknown.json')['value'];pending=get('decision-pending.json')['value'];reloaded=get('decision-reloaded.json')['value']
    intent=unknown['unresolved_rule'];identity=intent['request_id']
    require(intent['settled_protocol']and intent['profile']=='server'and intent['mutation']['change']=='replace'and intent['mutation']['expected_revision']=='2','original settled protocol/profile/typed stale CAS retained')
    require(pending['pending']is not None and pending['pending']==reloaded['pending']and pending['unresolved_rule']==reloaded['unresolved_rule']==intent,'same admitted slot/UUID/payload through real renderer reload')
    require(unknown['pending']is None and 'Response unknown'in unknown['rules']['result'],'actual IPC deadline gives uncertainty, not fabricated refusal')
    require(before['automation']['rules'][0]['revision']==3 and pending['rules']['rules'][0]['revision']=='2'and not pending['options']['auto_refresh'],'actual service revision3 versus paused observed frontend2')
    require(before['automation']==late['automation']and before['jobs']==late['jobs']==[]and before['sessions']==late['sessions']==[]and before['requests']==late['requests']==[],'late rejected request does not edit a rule or admit a USB job')
    require(bool(re.search(r'^State:\s+T',(folder/'decision-stopped-agent.txt').read_text(),re.M)),'actual stopped broker during the negative request deadline')
    require(before['rule_rejections']=={'sequence':0,'entries':[]},'no pre-existing refusal before the original failed edit')
    journal=late['rule_rejections'];require(set(journal)=={'sequence','entries'}and journal['sequence']==1 and len(journal['entries'])==1,'strict single durable rejection, independent sequence')
    record=journal['entries'][0]
    require(set(record)=={'sequence','request_id','uid','profile','mutation','failure'}and record['request_id']==identity and record['uid']==1000 and record['profile']=='server'and record['failure']=='state'and record['sequence']==1,'actual UID1000/original UUID fixed rejection binding')
    require(record['mutation']['rule_id']==intent['mutation']['rule_id']and record['mutation']['expected_revision']==2 and record['mutation']['spec']=={'name':'Preserved stale draft','trigger':'local_profile_ready','settle_seconds':2,'rearm':'once'},'exact original failed payload, no silent latest-revision substitution')
    require(get('decision-after-restart.json')['rule_rejections']==journal and get('decision-after-restart.json')['automation']==late['automation'],'durable refusal survives cooperative real agent restart')
    for prefix in ['decision-agent-before','decision-agent-after']:
        actor=(folder/(prefix+'.txt')).read_text();require(bool(re.search(r'^Uid:\s+2000\s+2000\s+2000\s+2000$',actor,re.M))and base.digest(ROOT/'target/release/rustusb')in actor,prefix+': actual broker UID/executed ELF')
    before_actor=(folder/'decision-agent-before.txt').read_text().splitlines()[0];after_actor=(folder/'decision-agent-after.txt').read_text().splitlines()[0]
    require(before_actor!=after_actor and(folder/'wait-agent-decision-restart.txt').read_text().strip()=='0','actual old broker wait0 and distinct new process')
    queried=get('decision-resolved.json')['value'];receipt=queried['last_rule_decision']
    require(queried['unresolved_rule']is None and receipt['original']==intent and receipt['replayed']and receipt['decision']=={'decision':'rejected','sequence':'1','failure':'state'},'actual original query resolves precise retained negative receipt')
    require(get('decision-before-query.json')==get('decision-after-query.json'),'negative query leaves entire application journal unchanged')
    frames=[json.loads(line)for line in(folder/'decision-cli-query.jsonl').read_text().splitlines()]
    require(len(frames)==2 and frames[0]=={'schema_version':1,'event':'rule_decision_query','protocol':'settled','request_id':identity} and frames[1]['response']=={'result':'rule_decided','request_id':identity,'decision':{'decision':'rejected','sequence':1,'failure':'state'},'replayed':True} and(folder/'decision-cli-query.wait').read_text().strip()=='1','actual settled CLI original query/two frames/expected rejection exit1')
    require(get('decision-after-cli-query.json')==get('decision-after-query.json'),'CLI rejection query does not apply or record another edit')
    draft=get('decision-restored-draft.json')['value']
    require(draft=={'name':'Preserved stale draft','originalRevision':'2','profile':'server','settled':True},'explicit native draft restoration keeps original revision and profile')
    for path in ['decision-confirm-refusal.json','decision-confirm-query.json','decision-confirm-restore.json']:
        require(get(path)['value']['focus']=='cancel',path+': actual default Cancel')
    require(identity in get('decision-confirm-query.json')['value']['text'] and identity in get('decision-confirm-restore.json')['value']['text'],'query and restore confirmations identify the exact original UUID')
    final=get('agent-stopped-2.json')
    require(final['schema_version']==4 and final['rule_rejections']==journal,'exact same original refusal retained after later actual CRUD/I/O/cleanup')
    require([m['outcome']['revision']for m in final['automation']['mutations']]==[1,2,3,4,5,7,8,9],'only eight intended positive edits; no replay of rejected revision2')
    require(get('gui-rule-stale-draft.json')['value']['name']=='Preserved stale draft','restored stale definition is still reviewed before explicit latest edit')
    facts['settled_rule_decisions']={'checks':checks,'assertion_count':len(checks),'rejected_requests':1,'native_rejection_queries':1,'cli_rejection_queries':1,'agent_restarts_before_query':1,'scope':'Actual native service-CAS refusal, real lost IPC reply/renderer reload, durable query across cooperative restart and explicit draft review; original positive rules/Native I/O/Root proof retained. No process-crash intent file/hardware/fullPhase5 claim.'}
    return facts

def main():
    global DRIVER
    p=argparse.ArgumentParser(add_help=False);p.add_argument('--driver',type=Path,required=True);own,remaining=p.parse_known_args();DRIVER=own.driver.resolve();baseline.DRIVER=DRIVER
    assert DRIVER.is_file()and DRIVER.read_bytes()[:4]==b'\x7fELF'
    sys.argv=[sys.argv[0],*remaining];p=argparse.ArgumentParser(add_help=False);p.add_argument('--evidence',type=Path,required=True);args,_=p.parse_known_args(remaining)
    gui_before=base.digest(ROOT/'apps/rustusb-gui/target/release/rustusb-gui');driver_before=base.digest(DRIVER)
    base.inputs,base.guest_source,base.payload,base.verify=inputs,guest,old_payload,verify
    result=base.main(guest_memory_mib=4096)
    path=args.evidence/'summary.json';value=json.loads(path.read_text());value.update(gui_sha256=gui_before,driver_sha256=driver_before,gui_binary_guard=base.digest(ROOT/'apps/rustusb-gui/target/release/rustusb-gui')==gui_before,driver_guard=base.digest(DRIVER)==driver_before)
    if not value['gui_binary_guard']or not value['driver_guard']:value['result']='FAIL';result=1
    path.write_text(json.dumps(value,indent=2)+'\n');return result
if __name__=='__main__':raise SystemExit(main())
