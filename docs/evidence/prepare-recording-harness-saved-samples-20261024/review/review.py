#!/usr/bin/env python3
"""Independent read-only saved/live evidence binding review; no runner imports."""
from pathlib import Path
import hashlib,json,re,difflib
repo=Path('/home/ubuntu/github/usbip-client')
base=Path('/tmp/rustusb-prepare-stop-final-2222de8-20261024')
run=base/'prepare-native-resource-job'
source=run/'source'
out=Path('/tmp/rustusb-prepare-saved-proof-binding-independent-review-20261024')
out.mkdir(exist_ok=True)
checks=[]
def check(name,value,detail=None): checks.append({'check':name,'passed':bool(value),'detail':detail})
def load(path):return json.loads(path.read_text())
def meta(path):
 b=path.read_bytes();return {'path':str(path),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
names=['guest-init.sh','guest-local-fragments.sh','prepare-gate.c','run.py','verify.py','local.py','verify_local.py']
rel=Path('scripts/qemu_prepare_recording_shutdown')
raw_names=['selected-sessions.json','held-observations.jsonl','root-observe-1.json','root-observe-2.json','host-before.json','host-observe-2.json','gate-held-before.json','gate-held-after.json','selection.json','gate-candidate-9.json','gate-exit-9.json','gate-events.jsonl']
paths=[base/'result.json',base/'results.json',base/'start.json',run/'summary.json']+[run/'server-artifacts'/n for n in raw_names]+[source/rel/n for n in names]
original_before={str(p):meta(p) for p in paths}
current_before={n:meta(repo/rel/n) for n in names}
result=load(base/'result.json');summary=load(run/'summary.json')
check('original matrix FAIL remains with1run and22unrun',result['result']=='FAIL' and len(result['runs'])==1 and result['runs'][0]['exit_code']==1 and len(result['remaining_unrun'])==22,result['remaining_unrun'])
check('original run host FAIL and two guest0 are distinct',summary['result']=='FAIL' and summary['guest_exit_codes']=={'server':0,'client':0})
check('original actual first verifier mismatch retained',summary['error']=='RuntimeError: held observation 1 recomputed from raw root/sysfs/FD files',summary['error'])
check('original source/ELF/head/clean guards all true',all(summary[k] for k in ['source_guard','binary_guard','head_guard','clean_guard']))
art=run/'server-artifacts'
sessions=load(art/'selected-sessions.json')
def exact(root):
 if len(sessions)!=2:return False
 terminals=[x['terminal'] for x in root['terminals']]
 for session in sessions:
  attempt=session['attempts'][0]
  expected={'resource_id':attempt['resource_id'],'kind':'native_export','owner_uid':2000,'correlation':attempt['correlation'],'device_key':session['device_key'],'proof':'native_export_restored'}
  if terminals.count(expected)!=1 or any(x['lease_id']==attempt['resource_id'] for x in root['exports']):return False
 return True
observations=[json.loads(x) for x in (art/'held-observations.jsonl').read_text().splitlines()]
first=load(art/'root-observe-1.json');second=load(art/'root-observe-2.json')
check('saved first snapshot has2exports0terminal and cannot prove target retirement',len(first['exports'])==2 and not first['terminals'] and not exact(first))
check('original observation1 wrongly records true against this saved sample',observations[0]['observation']==1 and observations[0]['exact_root_terminal'] is True and not exact(first),observations[0])
check('separate original observation2 contains exact both root proof',observations[1]['observation']==2 and exact(second) and second['exports']==[])
check('separate original second host snapshot equals complete baseline',load(art/'host-observe-2.json')==load(art/'host-before.json'))
before=load(art/'gate-held-before.json');after=load(art/'gate-held-after.json');candidate=load(art/'gate-candidate-9.json');ended=load(art/'gate-exit-9.json')
events=[json.loads(x) for x in (art/'gate-events.jsonl').read_text().splitlines()];release=[x for x in events if x['event']=='released']
identity=['candidate','tracee_pid','tracee_starttime','tid','fd','request','file_device','file_inode']
check('original saved observations bracket same held syscall before explicit release',before['held'] and after['held'] and all(before[k]==after[k]==candidate[k]==ended[k] for k in identity) and len(release)==1 and candidate['monotonic_ns']<=before['monotonic_ns']<after['monotonic_ns']<=release[0]['monotonic_ns']<ended['monotonic_ns'] and not ended['is_error'] and ended['return_value']==0)
old=(source/rel/'guest-init.sh').read_text();new=(repo/rel/'guest-init.sh').read_text()
old_local=(source/rel/'guest-local-fragments.sh').read_text();new_local=(repo/rel/'guest-local-fragments.sh').read_text()
expected=old.replace('check_target_proof() {\n','check_target_proof() {\n    # Judge the saved observation, never a later read of the live journal.\n    [ "$#" = 1 ] || return 2\n').replace('))\' "$ROOT_FILE" > /dev/null', '))\' "$1" > /dev/null').replace('if check_target_proof; then','if check_target_proof "/tmp/root-observe-$observation.json"; then').replace('    check_target_proof\n','    check_target_proof /tmp/root-after-release.json\n')
expected_local=old_local.replace('    check_target_proof\n    jq -e \'.records==[] and .exports==[] and (.terminals|length)==3\'','    check_target_proof /tmp/root-local-empty.json\n    jq -e \'.records==[] and .exports==[] and (.terminals|length)==3\'').replace('    check_target_proof\n','    check_target_proof /tmp/root-after-release.json\n')
check('main guest exact narrow change only saved argument andarity',new==expected)
check('local guest exact two saved snapshot argument changes only',new_local==expected_local)
check('proof function explicitly demands exactly1argument with no fallback', '[ "$#" = 1 ] || return 2' in new and new.count('))\' "$1" > /dev/null')==2)
calls=[]
for name,text in [('guest-init.sh',new),('guest-local-fragments.sh',new_local)]:
 for number,line in enumerate(text.splitlines(),1):
  if 'check_target_proof' in line and 'check_target_proof()' not in line:
   calls.append({'file':str(rel/name),'line':number,'text':line.strip()})
check('all4call sites explicit saved sample paths',len(calls)==4 and [x['text'] for x in calls]==['if check_target_proof "/tmp/root-observe-$observation.json"; then proof=true; fi','check_target_proof /tmp/root-after-release.json','check_target_proof /tmp/root-local-empty.json','check_target_proof /tmp/root-after-release.json'],calls)
for name in ['prepare-gate.c','run.py','verify.py','local.py','verify_local.py']:
 check(name+' unchanged from actual222source', (source/rel/name).read_bytes()==(repo/rel/name).read_bytes())
check('strictproof expressions and all deadlines unchanged by exactdiff',new==expected and new_local==expected_local)
original_after={str(p):meta(p) for p in paths}
check('all inspected original files unchanged',original_before==original_after)
check('candidate snapshot unchanged throughout review',current_before=={n:meta(repo/rel/n) for n in names})
patch=''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='original222/guest-init.sh',tofile='candidate/guest-init.sh'))+''.join(difflib.unified_diff(old_local.splitlines(True),new_local.splitlines(True),fromfile='original222/guest-local-fragments.sh',tofile='candidate/guest-local-fragments.sh'))
(out/'reviewed-delta.patch').write_text(patch)
report={'schema_version':1,'review_result':'PASS' if all(x['passed'] for x in checks) else 'FAIL','original_runtime_result':'FAIL','original_unrun_count':22,'source_scope':'Independent binding review of harness, Native product author participant; no verifier/runner imported. Current two-file static candidate only, no new runtime result.','original_head':summary['head'],'candidate_sources':list(current_before.values()),'checks':checks,'passed':sum(x['passed'] for x in checks),'failed':sum(not x['passed'] for x in checks),'original_sha_before':list(original_before.values()),'original_sha_after_unchanged':original_before==original_after,'limits':['Original observation1 saved root snapshot cannot support its true exact_root_terminal field. Source shows old helper rereads live journal. This proves saved/live binding mismatch, not precise syscall timing of that later read.','Separate saved observation2 plus held brackets shows actual original receipts and full baselines before release, but never changes original wholeFAIL or says anything about22unrun.','Snapshot root/sysfs/FD reads are sequential, not atomic. Strict verifier intentionally rejects any per-sample mismatch.','No product source, proof kind/identity matching, old verifier, timeout, syscall gate, or pre-existing evidence has been modified by this reviewer.','No Cargo/Git/QEMU/product/helper/host USB execution; no new clean/future commit attribution.']}
p=out/'report.json';p.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'result':report['review_result'],'checks':len(checks),'failed':[x for x in checks if not x['passed']],'report':meta(p)}))
raise SystemExit(0 if report['review_result']=='PASS' else 1)
