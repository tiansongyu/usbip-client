"""Separate raw audit of the terminal 4d700c2 failure; never invokes a runner."""
import base64,hashlib,json,re,subprocess
from pathlib import Path
ROOT=Path('/tmp/rustusb-shared-owner-qemu-final-20261024');R=ROOT/'secure-virtual-ipv4'
REPO=Path('/home/ubuntu/github/usbip-client');HEAD='4d700c22754fc5e4efb37fee391e00ff340d5829'
OUT=Path('/tmp/rustusb-shared-owner-first-failure-raw-review-20261024.json')
def sha(b):return hashlib.sha256(b).hexdigest()
checks=[]
def check(name,value):
 checks.append({'name':name,'passed':bool(value)})
 if not value:raise ValueError(name)
def obj(role,name):return json.loads((R/f'{role}-artifacts'/name).read_bytes())
summary_bytes=(R/'summary.json').read_bytes();s=json.loads(summary_bytes)
report={'schema_version':1,'runtime_verdict':'FAIL','audit_status':'INCOMPLETE','head':HEAD,'scope':'Failure boundary only; not a full lifecycle acceptance. Raw parser author also reviewed/authored related harnesses; independent method, not independent reviewer identity.','limits':['Only one virtual/IPv4 run executed; other eight not executed.','Two connected/configured Native receipts are not completion of payload I/O or final cleanup.','No runner.verify/pcap_review, Cargo/QEMU/USB/device process invocation.','Actual guest0 indicates orderly collection/poweroff, not product/harness PASS.','No claim of unchanged contender kernel snapshot: native-after-contender artifact was not reached.']}
try:
 check('terminal_FAIL_exact_source_and_all_guards',s['completed_utc'] and s['result']=='FAIL' and s['head']==s['head_after']==HEAD and all(s[k] is True for k in ['source_guard','head_guard','binary_guard','clean_guard']) and s['guest_exit_codes']=={'server':0,'client':0})
 check('batch_first_failure_stopped_remaining',len(json.loads((ROOT/'results.json').read_bytes()))==1 and json.loads((ROOT/'results.json').read_bytes())[0]['exit_code']==1)
 for p,e in s['source_inputs'].items():
  b=(R/'source'/p).read_bytes();check('source:'+p,len(b)==e['bytes'] and sha(b)==e['sha256'] and b==subprocess.check_output(['git','show',HEAD+':'+p],cwd=REPO))
 for p,e in s['artifact_manifest'].items():
  b=(R/p).read_bytes();check('artifact:'+p,len(b)==e['bytes'] and sha(b)==e['sha256'])
 captures={};warnings={}
 for role in ['server','client']:
  raw=(R/f'{role}-evidence.log').read_bytes();found={}
  for m in re.finditer(rb'^SECURE_ARTIFACT_BEGIN ([a-zA-Z0-9_.-]+) ([0-9]+)\n(.*?)^SECURE_ARTIFACT_END \1\s*$',raw,re.M|re.S):
   name=m[1].decode();b=base64.b64decode(b''.join(m[3].split()),validate=True);check('base64:'+role+'/'+name,name not in found and len(b)==int(m[2]) and b==(R/f'{role}-artifacts'/name).read_bytes() and sha(b)==s['guest_artifacts'][role][name]['sha256']);found[name]=b
  check(role+':all_original_frames',len(found)==raw.count(b'SECURE_ARTIFACT_BEGIN ')==len(s['guest_artifacts'][role]))
  check(role+':explicit_guest_FAIL',raw.count(('RUSTUSB_SECURE_RESULT: FAIL role='+role+' stage=').encode())==1 and b'RUSTUSB_SECURE_RESULT: PASS' not in raw)
  kernel=(R/f'{role}-serial.log').read_text();check(role+':no_kernel_panic',all(t not in kernel for t in ['Kernel panic','Oops:']))
  warnings[role]=[{'line':i+1,'text':line} for i,line in enumerate(kernel.splitlines()) if re.search(r'warning|\bWARN\b|\bERROR\b|vhci_device speed not set|urb->status -|usbip.*(?:fail|error|cannot)|usb .*error',line,re.I)]
  for name,b in found.items():
   if name.startswith('executable-'):
    binary='rustusb-helper' if name=='executable-helper.txt' else 'rustusb';check(role+':actualELF:'+name,b.decode().split()[0]==s['binary_sha256'][binary]==s['payload_sha256'][role]['usr/bin/'+binary])
  captures[role]=len(found)
 catalog=obj('client','catalog-initial.json');held=obj('client','channels-held-1.json');failure=obj('client','failure-native-channels.json')
 check('two_observed_legacy_schema1_records',held['schema_version']==1 and held['exports']==[] and len(held['records'])==2 and all(x['state']['phase']=='observed' for x in held['records']) and failure==held)
 receipts=[]
 for profile in ['hid','serial']:
  rows=[json.loads(l) for l in (R/'client-artifacts'/f'attach-{profile}-1.events').read_text().splitlines()];check(profile+':one_connected_receipt_only',len(rows)==1 and rows[0]['event']=='connected' and rows[0]['transport']=='mtls');r=rows[0]['receipt'];receipts.append(r)
  item=next(x for x in held['records'] if x['channel_id']==r['channel_id']);check(profile+':actual_full_journal_receipt',item['state']['receipt']==r['kernel'] and item['target']==r['target'] and r['kernel']['session']['status']==6 and r['kernel']['profile']['configuration']==1 and item['owner']['uid']==2000)
 check('exact_mismatch_preserved',b'configured remote device unavailable or unauthorized' in (R/'client-artifacts/contender-1.log').read_bytes() and b'configured remote device is unavailable or unauthorized' not in (R/'client-artifacts/contender-1.log').read_bytes())
 guest=(R/'source/scripts/qemu/guest-secure-init.sh').read_text();product=(R/'source/crates/rustusb-service/src/jobs/client.rs').read_text();check('source_confirms_original_text_contract',"grep -F 'configured remote device is unavailable or unauthorized'" in guest and 'Error::Permission("configured remote device unavailable or unauthorized".into())' in product)
 check('after_contender_and_io_not_captured',all(not (R/'client-artifacts'/p).exists() for p in ['native-after-contender-1.txt','input-1.bin','rx-1.bin','tx-1.bin']))
 check('original_summary_unchanged',summary_bytes==(R/'summary.json').read_bytes())
 report.update(audit_status='CONFIRMED_FAILURE_BOUNDARY',finding='Foreground catalog-unavailable refusal text lost the word is; retained original fixed-string harness assertion aborts before native-after-contender/I/O. Product refusal was observed; lifecycle acceptance was not reached.',source_input_count=len(s['source_inputs']),artifact_count=len(s['artifact_manifest']),decoded_guest_artifacts=captures,binary_sha256=s['binary_sha256'],connected_receipts=receipts,preserved_kernel_warning_lines=warnings,summary_sha256=sha(summary_bytes))
except Exception as e:report['audit_error']=type(e).__name__+': '+str(e)
report['checks']=checks;report['checks_total']=len(checks);report['checks_passed']=sum(x['passed'] for x in checks);report['parser_sha256']=sha(Path(__file__).read_bytes());OUT.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'status':report['audit_status'],'runtime':'FAIL','checks':len(checks),'error':report.get('audit_error'),'output':str(OUT),'sha256':sha(OUT.read_bytes())}));raise SystemExit(int('audit_error' in report))
