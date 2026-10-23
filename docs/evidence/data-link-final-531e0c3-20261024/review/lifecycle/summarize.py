#!/usr/bin/env python3
"""Summarize finished independent raw reviews; no product or harness execution."""
from pathlib import Path
import ast,collections,hashlib,json,subprocess
HERE=Path(__file__).resolve().parent
BASE=Path('/tmp/rustusb-data-link-ready-531e0c3-20261024')
REPO=Path('/home/ubuntu/github/usbip-client')
HEAD='531e0c32a4b0e7252ca771ab4f10042c71c8c195'
def load(p):return json.loads(p.read_bytes())
def meta(p):
 b=p.read_bytes();return {'path':str(p),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def unique(xs,fn):
 result=[x for x in xs if fn(x)]
 if len(result)!=1:raise AssertionError('unique original record')
 return result[0]
checks=[]
def check(label,value):
 checks.append({'label':label,'passed':bool(value)})
 if not value:raise AssertionError(label)
compat=load(HERE/'compat/report.json');health=load(HERE/'health/report.json');result=load(BASE/'result.json')
check('both method reports complete',compat['result']==health['result']=='PASS'and len(compat['groups'])==9 and len(health['groups'])==6)
check('actual fifteen terminal process receipts and frozen inputs',result['result']=='PASS'and result['head']==HEAD and result['inputs_unchanged']and result['input_count']==213 and len(result['runs'])==15 and all(r['exit_code']==0 and r['unchanged']and r['summary_result']=='PASS'for r in result['runs']))
new=[];timeout=[];decoded=0;live=0;pcapframes=0;supplement=[]
for row in health['groups']:
 report=load(Path(row['report']['path']));name=row['case'];base=BASE/name;facts=report['facts'];new.append(report)
 decoded+=sum(facts['decoded_guest_files'].values());live+=sum(facts['decoded_live_files'].values());pcapframes+=sum(facts['wire_ownership']['pcap_frames'].values())
 timeout.extend(e['outstanding_millis']for p in facts['fault_phases']for e in p['endpoints']if e['outstanding_millis']is not None)
 for role,wanted in [('client','import'),('server','incoming')]:
  get=lambda n:load(base/f'{role}-artifacts'/n)
  keys=get('keys.json');before=get('agent-pre-c2s.json');final=get('agent-final.json');held=get('agent-final-held.json')
  serial=unique(before['sessions'],lambda s:s['role']==wanted and s['device_key']==keys[1]and not s['terminal'])
  finalserial=unique(final['sessions'],lambda s:s['id']==serial['id']);heldserial=unique(held['sessions'],lambda s:s['id']==serial['id'])
  firstlink=unique(before['link_history'],lambda h:h['session_id']==serial['id'])['link'];lastlink=unique(final['link_history'],lambda h:h['session_id']==serial['id'])['link'];heldlink=unique(held['link_history'],lambda h:h['session_id']==serial['id'])['link']
  check(name+role+' original serial retained through final live connection and terminal',all(serial[k]==finalserial[k]==heldserial[k]for k in ['id','instance_id','job_id','attempts','device_key'])and firstlink['identity']==heldlink['identity']==lastlink['identity']and heldserial['state']=='Connected'and not heldserial['terminal'])
  hid=[s for s in final['sessions']if s['role']==wanted and s['device_key']==keys[0]]
  if role=='client'or facts['backend']=='native':check(name+role+' four distinct real HID correlations',len(hid)==4 and len({s['attempts'][0]['correlation']['attempt_id']for s in hid})==4)
  stat=(base/f'{role}-artifacts/owner-stat-pre-c2s.txt').read_text();pid=int(stat.split(' ',1)[0]);parts=stat.rsplit(') ',1)[1].split();starttime=int(parts[19])
  root=None if(role=='server'and facts['backend']=='virtual')else get('root-pre-c2s.json')
  if root:
   owners=[r['owner']for r in(root['records']if role=='client'or facts['backend']=='usbfs'else root['exports'])]
   check(name+role+' root grant owners match actual proc generation',len(owners)==2 and all(o['uid']==2000 and o['pid']==pid and o.get('start_time',o.get('starttime'))==starttime for o in owners))
  supplement.append({'case':name,'role':role,'original_serial_session':serial['id'],'same_connection':firstlink['identity'],'final_link_end':lastlink['end'],'final_operation_error':finalserial['operation_error'],'final_cleanup':finalserial['cleanup'],'actual_agent_pid':pid,'actual_agent_starttime':starttime})
olddecoded=0
for group in compat['groups']:
 values=group.get('decoded_guest_artifacts')
 if isinstance(values,dict):olddecoded+=sum(values.values())
 elif isinstance(values,int):olddecoded+=values
# The compatibility audit captured all raw files before the new review; every
# one is rehashed now, providing a before/after read-only check on originals.
prior=load(HERE/'compat/raw-inputs.json')['files'];originals=[meta(p)for p in sorted(BASE.rglob('*'))if p.is_file()]
check('all raw originals unchanged during the independent review',prior==originals)
check('no private-key bytes inventoried',not any(Path(x['path']).name.endswith('-key.der')for x in originals))
raw={'scope':'All final531 raw input files, including all15run originals. SHA256 metadata only; no private keys.','head':HEAD,'file_count':len(originals),'files':originals};(HERE/'raw-originals-manifest.json').write_text(json.dumps(raw,indent=2)+'\n')
software=Path('/tmp/rustusb-data-link-management-software-first-20261024');swresult=load(software/'result.json');counts=load(software/'test-counts.json');check('referenced software gate complete without reexecution',swresult['exit']==0 and swresult['rust_inputs_unchanged']and swresult['input_count']==165 and counts['executions']==626 and counts['failed']==counts['ignored']==0)
# Source evidence contains one MD fixture readme beyond the batch py/sh/Rust set.
selected=set().union(*(set(load(BASE/x['case']/'summary.json')['source_inputs'])for x in health['groups']));globalset={r['path']for r in load(BASE/'inputs-before.json')};extra=sorted(selected-globalset)
for path in extra:
 blob=subprocess.check_output(['git','show',HEAD+':'+path],cwd=REPO)
 check('extra selected immutable Gitblob '+path,all(meta(BASE/x['case']/'source'/path)['sha256']==hashlib.sha256(blob).hexdigest()for x in health['groups']))
revision={'scope':'Reviewer/parser defects only. No original run summary, source, expected USB outcome, or raw file was modified. Old9 inherited lifecycle assertions were not removed or relaxed.','entries':[{'file':'health_review-initial.py','issue':'Initial standalone parser had missing whitespace in Python conditional tokens; parse failed before opening raw data.'},{'file':'health_review-revision1.py','reports':'health-first','issue':'First parser expected a features list; actual frozen summary uses exact --workspace --all-features --release --locked string. Replaced with strict actual documented representation.'},{'file':'health_review-revision2.py','reports':'health-second','issue':'Assumed new selected README belonged to global213 py/sh/Rust inputs. Added direct immutable Gitblob check for the additional selected README; did not omit source verification.'},{'file':'health_review-revision3.py','reports':'health-third','issue':'Incorrectly normalized all decoded TCP directions. Raw valve packet flow preserves source-to-destination; decoder now preserves direction and compares each drop against correct directional armed tuple, including SYN/SYN-ACK.'}]}
(HERE/'parser-revision-history.json').write_text(json.dumps(revision,indent=2)+'\n')
report={'schema_version':1,'result':'PASS','head':HEAD,'method':'Independent standard-library raw decoder plus disclosed adaptation of earlier independent old9 lifecycle parsers. No harness verifier, packet_audit, runner import, Cargo, QEMU, or product executable invoked.','reviewer_authorship':'Current reviewer authored new Rust guest-rust-fragments.sh/verify_rust.py and related service implementation. New Native harness is not this reviewer’s work. Method independence is not independent author identity.','source_attribution':{'global_inputs':213,'software_inputs_individually_mapped_to_git':165,'new_health_selected_inputs':178,'old_secure_selected_inputs':167,'old_agent_selected_inputs':169,'extra_new_selected_gitblobs_outside_global':extra,'same_release_elf':load(HERE/'compat/expected-binaries.json'),'software_scope':'Reference only:260default+346allfeatures+20vendor=626 actual earlier gate; inputs mapped to final531. Not a new execution or claim software itself ran at clean531.'},'scopes':{'new_health':{'groups':6,'guest_exit_zero':12,'directional_fault_windows':18,'io_rounds':42,'serial_each_direction_bytes':1470,'virtual_self_echo_each_direction_bytes':490,'hardware_emulated_ftdi_independent_each_direction_bytes':980,'actual_endpoint_probe_timeout_samples':len(timeout),'timeout_enqueue_elapsed_millis_range':[min(timeout),max(timeout)],'decoded_guest_files':decoded,'decoded_live_files':live,'pcap_frames_accounted':pcapframes,'predicates':sum(x['predicate_count']for x in health['groups'])},'old_raw_v1_compat':{'groups':9,'guest_exit_zero':18,'decoded_guest_files':olddecoded,'predicates':compat['recorded_group_comparisons'],'scope':'Six foreground+two Native agent+SIGTERM, old raw-v1 health explicitly unavailable; separate from new fault windows.'},'total':{'groups':15,'guest_exit_zero':30,'decoded_guest_files':decoded+olddecoded,'raw_original_files':len(originals)}},'supplemental_predicates':checks,'supplemental_predicate_count':len(checks),'predicate_counts':{'new_health':sum(x['predicate_count']for x in health['groups']),'old_compat_groups':compat['recorded_group_comparisons'],'old_compat_batch':len(compat['batch_checks']),'supplemental':len(checks),'total':sum(x['predicate_count']for x in health['groups'])+compat['recorded_group_comparisons']+len(compat['batch_checks'])+len(checks),'meaning':'Diagnostic comparisons including hashes/packets; not product-test or USB-case counts.'},'actual_serial_owner_and_final_classification':supplement,'new_report':meta(HERE/'health/report.json'),'old_report':meta(HERE/'compat/report.json'),'raw_manifest':meta(HERE/'raw-originals-manifest.json'),'revision_history':meta(HERE/'parser-revision-history.json'),'historical_scope_preserved':['e1529ea first Native-v4 Catalog QEMU FAIL remains FAIL; no fault arm or IMPORT was reached.','93428ce matrix remains four PASS + first virtual-v4 readiness FAIL + ten unrun, never merged into a passing fifteen.','Root first public-engine ConnectionAborted software regression remains an original FAIL.','Prior e29 raw-v1 nine-group results remain in their original review directory, not overwritten.'],'limits':health['boundaries']+['Final client Gone is supported by exact retained root release proof, NULL ports, kernel logs and frozen successful guest absence assertions; not a new independent sysfs-tree dump.','USBFS old foreground lacks intermediate raw grant journal, while new managed USBFS captures and preserves full schema3 grant originals.','Public PKI/ACL policy is not expanded into a new guessed-key/unmapped-certificate QEMU negative.','Device cleanup and application/transport failure categories remain distinct, including final server protocol ends after client TERM.']}
(HERE/'report.json').write_text(json.dumps(report,indent=2)+'\n')
# Freeze all review artifacts, including retained initial parser failures.
files=[meta(p)for p in sorted(HERE.rglob('*'))if p.is_file()and p.name not in('manifest.json','handoff.json')and '__pycache__'not in str(p)]
(HERE/'manifest.json').write_text(json.dumps({'file_count':len(files),'files':files},indent=2)+'\n')
print(json.dumps({'result':report['result'],'report':str(HERE/'report.json'),'scopes':report['scopes'],'supplemental_predicates':len(checks),'review_files':len(files)},indent=2))
