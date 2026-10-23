#!/usr/bin/env python3
"""Small read-only linkage for the completed shared-owner matrix security reviews."""
import hashlib,json,pathlib,subprocess
ROOT=pathlib.Path('/home/ubuntu/github/usbip-client')
BASE=pathlib.Path('/tmp/rustusb-shared-owner-qemu-fixed-20261024')
HEAD='b45c2e8c67a06396360fee0af6c0a092a950a6ff'
OUT=pathlib.Path('/tmp/rustusb-shared-owner-security-review-index-20261024.json')
seen={};checks=[]
def read(p):
 p=pathlib.Path(p);v=p.read_bytes();m={'path':str(p),'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()}
 if str(p) in seen and seen[str(p)]!=m:raise ValueError('input changed: '+str(p))
 seen[str(p)]=m;return v
def load(p):return json.loads(read(p))
def check(n,v,detail):checks.append({'id':n,'passed':bool(v),'detail':detail})
secure=load('/tmp/rustusb-shared-owner-secure-six-security-independent-review-20261024.json')
agent=load('/tmp/rustusb-shared-owner-agent-security-independent-review-20261024.json')
for p in ('/tmp/rustusb-shared-owner-secure-security-independent-review-20261024.py','/tmp/rustusb-shared-owner-agent-security-independent-review-20261024.py','/tmp/rustusb-shared-owner-packet-decoder-20261024.py'):read(p)
start=load(BASE/'start.json');result=load(BASE/'result.json');runs=load(BASE/'results.json')
before=load(BASE/'inputs-before.json');after=load(BASE/'inputs-after.json')
check('outer_nine_terminal',start['head']==result['head']==HEAD and start['clean'] is True and result['result']=='PASS' and result['runs']==runs and len(runs)==9 and all(v['exit_code']==0 and v['summary_result']=='PASS' and v['unchanged'] is True for v in runs),'Outer wrapper only; detailed TLS audits cover8, plaintextSIGTERM lifecycle belongs to H/root.')
check('outer_input_manifests_equal',before==after and len(before)==len({v['path'] for v in before})==192 and result['input_count']==start['input_count']==192 and result['inputs_unchanged'] is True,'192 before/after entries, not conflated with per-run154/156 source fingerprints.')
bad=[]
for v in before:
 raw=subprocess.check_output(['git','show',HEAD+':'+v['path']],cwd=ROOT)
 if len(raw)!=v['bytes'] or hashlib.sha256(raw).hexdigest()!=v['sha256']:bad.append(v['path'])
check('outer192_match_exact_git',not bad,{'count':len(before),'mismatches':bad})
check('six_report_complete',secure['result']=='PASS' and secure['head']==HEAD and secure['counts']['runs']==6 and secure['counts']['guest_exit0']==12 and secure['counts']['failed']==0,'350 independently parsed source/artifact/security and lifecycle spotchecks; not350USBcases.')
check('agent_report_complete',agent['result']=='PASS' and agent['head']==HEAD and agent['counts']['runs']==2 and agent['counts']['guest_exit0']==4 and agent['counts']['failed']==0,'101 independently parsed source/artifact/security checks; both config epochs included.')
expected={'rustusb':'ee66f1c9af551f603ad0cd5cbaf3b7e25ce13329819728802defe2632528df01','rustusb-helper':'cc6525a4aa746fcfea5e05ee6a6cbc5975cf24bd82d13928da6e50a5fccb3c2a'}
allruns=secure['runs']+agent['runs']
check('actual_frozen_elf_scope',all(v['binary_sha256']==expected for v in allruns),'All8 summary guarded hashes equal; each guest /proc/PID/exe receipt independently crosschecked in detailed reports, no downloaded ELF inference.')
check('per_run_source_scope',all(v['source_inputs']==154 for v in secure['runs']) and all(v['source_inputs']==156 for v in agent['runs']),'Actual new run source counts observed, not inherited old125/154 count assertions.')
check('agent_actual_config_epochs',all(len(v['actual_configuration'])==4 and all(o['capture_before_first_job_start'] and not o['private_key_bytes_captured'] for o in v['actual_configuration']) for v in agent['runs']),'Each run server/client epoch1/2 actual JSON/pin/publicDER and metadata; private keys metadata only.')
captures=[]
for v in allruns:
 for role,cap in v['captures'].items():
  captures.append({'run':v.get('name','agent-native-'+v['family']),'role':role,'packets':cap['packets'],'directional_tls_streams':len(cap['TLS_directional_flows']),'encrypted_record_count':sum(f['record_types'].get('23',0) for f in cap['TLS_directional_flows']),'all_other_payload_accounted':cap['all_other_payload_accounted'],'plain3240_probe_families':sorted({r['family'] for r in cap['plain_3240_rst']})})
check('all16_captures_accounted',len(captures)==16 and all(c['all_other_payload_accounted'] and c['plain3240_probe_families']==[4,6] for c in captures),'Counts computed from current reassembly; each full TCP flow matches capture at opposite end.')
check('all_linked_originals_unchanged',all(pathlib.Path(p).stat().st_size==v['bytes'] and hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()==v['sha256'] for p,v in seen.items()),'Outer inputs/results and detailed parser/reports rehashed.')
read(pathlib.Path(__file__))
report={'schema_version':1,'result':'PASS' if all(v['passed'] for v in checks) else 'FAIL','head':HEAD,'scope':'Completed8TLSrun independent review linkage; plaintextSIGTERM is a separate ninth wrapper result, notTLS. No runner verifier calls/build/product/QEMU/repository writes.','counts':{'tls_runs':8,'tls_guest_exit0':16,'secure_diagnostic_checks':secure['counts']['checks'],'agent_diagnostic_checks':agent['counts']['checks'],'combined_diagnostic_checks':secure['counts']['checks']+agent['counts']['checks'],'outer_checks':len(checks),'outer192_source_entries':192,'plaintext_sigterm_runs_separate':1},'checks':checks,'captures':captures,'linked_artifacts':list(seen.values()),'limits':['Six foreground runs retain actual startup JSON/policy/publicDER and OS process/capability receipts but no independent full ancestor/file stat inventory; trusted file mode checks are source/startup enforcement there. Agent2 runs separately retain actual two-epoch metadata. No metadata is copied across evidence scopes.','No TLS decryption or keylog: plaintextClientHello ALPN/SNI offer and ServerHelloTLS1.3 selection are visible; encrypted negotiatedALPN/certificate handshake are not packet-only proofs. PublicDER validation is offline and linked to source/config/actualI/O.','Single client principal is granted both devices in these fixtures. LocalUID denials and busy hiding are real; no new valid-but-ungranted remote cert QEMUnegative claimed.','Whole-link absence of specified clear management/URB/fixture signatures is a bounded leak check; not a universal side-channel or hardwareG3 proof.','Old4d first-run FAIL remains separate and unchanged. Earlier eaa/cb/b83 reports retain their own source and collection boundaries.','Current docs-only dirty worktree after completed runs would not invalidate recorded clean-run guards; immutable Git blobs anchor exact source.','No real hardwareG1/G2, kernel-internal accepted-initializer window, ISO, universal crash/FDrevocation, heartbeat/RTT, credential rotation, or entirePhase3 completion claim.']}
OUT.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'path':str(OUT),'bytes':OUT.stat().st_size,'sha256':hashlib.sha256(OUT.read_bytes()).hexdigest(),'result':report['result'],'counts':report['counts']}))
