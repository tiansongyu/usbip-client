#!/usr/bin/env python3
"""Narrow independent-method review of the original b1 detached/stub phase."""
import base64,hashlib,json
from pathlib import Path
BASE=Path('/tmp/rustusb-prepare-stop-local-resource-b1de034-20261024')
OUT=Path(__file__).resolve().parent
checks=[]
def sha(b):return hashlib.sha256(b).hexdigest()
def j(p):return json.loads(p.read_text())
def check(ok,label):checks.append({'pass':bool(ok),'label':label})
def inv():return {str(p.relative_to(BASE)):{'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())}for p in sorted(BASE.rglob('*'))if p.is_file()}
def dump(n,v):(OUT/n).write_text(json.dumps(v,indent=2,sort_keys=True)+'\n')
before=inv();dump('raw-sha-before.json',before)
s=j(BASE/'summary.json');a=BASE/'server-artifacts';c=BASE/'client-artifacts'
target=j(a/'local-target.json');survivor=j(a/'local-survivor.json');held=j(a/'root-held.json');during=j(a/'root-local-survivor-io.json');detached=j(a/'root-local-after-detach.json');empty=j(a/'root-local-empty.json');client=j(c/'root-local-detached.json')
original=next(x for x in held['exports'] if x['lease_id']==survivor['attempts'][0]['resource_id']);stub=during['exports'][0]['stub']
check(s['result']=='FAIL' and s['guest_exit_codes']=={'server':0,'client':0},'Original FAIL remains despite completed guest collection')
check(s['head']=='b1de03488474af74a4155232cda73a00e1e0f142' and s['head_after']==s['head'],'original exact b1 HEAD')
check(all(s[x] for x in ['source_guard','binary_guard','head_guard','clean_guard']) and s['git_status_before']==s['git_status_after']=='','original four guard/clean receipts')
for n,m in s['artifact_manifest'].items():check(before[n]==m,'Original archive bytes '+n)
needed={'server':['local-target.json','local-survivor.json','root-held.json','root-local-survivor-io.json','root-local-after-detach.json','root-local-empty.json','host-local-after-detach.json','local-stop-survivor.json','gate-events.jsonl'],'client':['root-local-detached.json','local-detached-sessions.json','local-stop-import.json','local-detached-vhci.txt']}
for role,names in needed.items():
 lines=(BASE/(role+'-evidence.log')).read_text().splitlines();decoded={};i=0
 while i<len(lines):
  if lines[i].startswith('SECURE_ARTIFACT_BEGIN '):
   _,name,size=lines[i].split();i+=1;parts=[]
   while i<len(lines)and not lines[i].startswith('SECURE_ARTIFACT_END '):parts.append(lines[i]);i+=1
   if name in names:
    b=base64.b64decode(''.join(parts),validate=True);check(lines[i]=='SECURE_ARTIFACT_END '+name and len(b)==int(size) and b==(BASE/(role+'-artifacts')/name).read_bytes(),role+' selected decoded original '+name);decoded[name]=sha(b)
  i+=1
 check(set(decoded)==set(names),role+' all narrow-review originals decoded')
check(detached==during,'server original stub root record unchanged between I/O and after-detach samples')
check(len(detached['exports'])==1 and {k:v for k,v in detached['exports'][0].items()if k!='stub'}=={k:v for k,v in original.items()if k!='stub'},'same original HID export remains registered')
check(stub['state']['phase']=='observed' and stub['state']['receipt']['usbip_status']==2,'stored prior observed stub receipt reports USED; not current sysfs')
term=lambda r:[x['terminal']for x in r['terminals']]
check(len(term(detached))==1 and term(detached)[0]['resource_id']==target['attempts'][0]['resource_id'] and term(detached)[0]['proof']=='native_export_restored','after-detach only serial root terminal, no original HID stub terminal')
host=j(a/'host-local-after-detach.json');hid=next(x for x in host if x['bus']==original['registration']['generation']['bus_id'])
check(hid['usbip_status']==1 and hid['device_driver']=='usbip-host','current independent HID sysfs already AVAILABLE despite pending root stub record')
check(client['records']==client['exports']==[] and len(term(client))==1 and term(client)[0]['kind']=='native_import' and term(client)[0]['proof']=='native_import_released','client own channel cleanup proof complete')
check(all(x['terminal']and x['cleanup']=='verified' for x in j(c/'local-detached-sessions.json')['response']['sessions']),'client application cleanup confirmed separately')
ports=[x.split()for x in(c/'local-detached-vhci.txt').read_text().splitlines()if x.startswith(('hs ','ss '))]
check(bool(ports)and all(x[2]=='004'for x in ports),'client actual VHCI NULL')
check(j(a/'local-stop-survivor.json')['response']=={'result':'stop_accepted','owner_present':True},'separate later HID export StopSession accepted')
check(empty['records']==empty['exports']==[] and len(term(empty))==3,'only later sample after last export stop has three root terminals')
expected={'resource_id':stub['channel_id'],'kind':'native_stub','device_key':survivor['device_key'],'owner_uid':2000,'correlation':stub['correlation'],'proof':'native_stub_available'}
check(expected in term(empty)and expected not in term(detached),'same exact stub proof appears later, cannot backfill after-detach')
source=(BASE/'server-generated-init.sh').read_text();anchor='    host_expect SURVIVOR_DETACHED\n    root_snapshot local-after-detach\n    agent stop-session "$SURVIVOR_SESSION"'
check(anchor in source,'frozen old flow performs one server sample then immediately requests last export stop')
controls=[json.loads(x)for x in(BASE/'host-control.jsonl').read_text().splitlines()];marks=[x['mark']for x in controls]
check(marks.index('detach-survivor')<marks.index('survivor-detached')<marks.index('empty-catalog-request'),'actual host barrier order matches frozen source, not extra server completion ack')
after=inv();dump('raw-sha-after.json',after);check(before==after,'all originals unchanged through narrow review')
report={'review_result':'PASS'if all(x['pass']for x in checks)else'FAIL','original_product_result':'FAIL; no reinterpretation','head':s['head'],'selected_sources':len(s['source_inputs']),'binary_sha256':s['binary_sha256'],'original_verifier_metadata_traceability_only':{'count':s['facts']['check_count'],'failures':s['facts']['failures']},'raw_file_count':len(before),'raw_unchanged':before==after,'diagnostic_predicates_not_usb_cases':len(checks),'checks_failed':[x for x in checks if not x['pass']],'original_hid_export_id':original['lease_id'],'original_stub_id':stub['channel_id'],'stored_stub_socket_inode':stub['state']['receipt']['kernel_socket_inode'],'actual_after_detach_sysfs_status':hid['usbip_status'],'actual_after_detach_devnum':hid['devnum'],'original_hid_devnum':str(original['registration']['generation']['device_number']),'finding':'Client native_import_released/NULL does not prove server stub retirement. Server after-detach root snapshot still retains the exact original stub without a terminal; same-phase actual sysfs is already AVAILABLE but devnum is 0 rather than original 2, so this is not a complete stable device identity. Original harness has no server retirement barrier before requesting last export stop. Subsequent exact stub proof cannot be used as earlier evidence.','causation_limit':'The originals establish the missing completion barrier and sampled state only. They do not prove a particular scheduler race, eventual independent retirement without export stop, or root cause of delayed metadata.','reviewer_disclosure':'Method cross-review: participated in production service and earlier harness work. No original verifier imported or invoked. No Cargo/QEMU/Git or source mutation.'}
dump('raw-report.json',report);dump('raw-checks.json',checks)
print(json.dumps({k:report[k]for k in ['review_result','original_product_result','raw_file_count','diagnostic_predicates_not_usb_cases','actual_after_detach_sysfs_status']}))
assert report['review_result']=='PASS'
