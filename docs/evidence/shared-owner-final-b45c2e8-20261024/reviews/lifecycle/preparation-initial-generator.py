from pathlib import Path
import ast,hashlib,json
HEAD='4d700c22754fc5e4efb37fee391e00ff340d5829'
LOCK='/tmp/rustusb-shared-owner-review-source-lock-20261024.json'
OUT=Path('/tmp/rustusb-shared-owner-review-parsers-20261024');OUT.mkdir(exist_ok=True)
def replace(s,a,b):
 if s.count(a)!=1:raise RuntimeError(('anchor',a[:100],s.count(a)))
 return s.replace(a,b)
old=Path('/tmp/rustusb-foreground-six-lifecycle-raw-review-eaa1936-20261023.py');s=old.read_text()
s=s[:s.index('\nresults=[]')]
s=replace(s,'import base64,hashlib,json,re,struct,subprocess','import argparse,base64,hashlib,json,re,struct,subprocess')
s=replace(s,"HEAD='eaa19366486b8b99412efbfce1f13d49f73fe0cc'",f"HEAD='{HEAD}'\nLOCK=json.loads(Path('{LOCK}').read_text())\nEXPECTED_BINARIES=None")
s=replace(s,"OUT=Path('/tmp/rustusb-foreground-six-lifecycle-raw-review-eaa1936-20261023.json')\n",'')
s=replace(s,'def audit(backend,family):\n root=Path(f\'/tmp/rustusb-qemu-foreground-{backend}-{family}-eaa1936-20261023\')','def audit(root,backend,family):')
s=replace(s,"s['binary_sha256']=={'rustusb':'7a7f07caa8d65d880a01b873a64e1c9b4fa267da97e90586f182066db143b79f','rustusb-helper':'cc6525a4aa746fcfea5e05ee6a6cbc5975cf24bd82d13928da6e50a5fccb3c2a'}","s['binary_sha256']==EXPECTED_BINARIES")
s=replace(s,"len(s['source_inputs'])==152","set(s['source_inputs'])==set(LOCK['secure_inputs']) and s['source_inputs']=={p:LOCK['source_hashes'][p] for p in LOCK['secure_inputs']}")
s=replace(s,"sha((root/'source/crates/rustusb-native/src/channel.rs').read_bytes())=='3d77ff555b39ac8a9e2098a22dfb0b8e219dba9355197cbf458f038dfbea9b95'","sha((root/'source/crates/rustusb-native/src/channel.rs').read_bytes())==LOCK['source_hashes']['crates/rustusb-native/src/channel.rs']['sha256']")
s=replace(s,"'cycles':cycles,'assertion_count'","'cycles':cycles,'preserved_kernel_warning_lines':{role:[{'line':i+1,'text':line} for i,line in enumerate((root/f'{role}-serial.log').read_text().splitlines()) if re.search(r'warning|\\bWARN\\b|\\bERROR\\b|vhci_device speed not set|urb->status -|usbip.*(?:fail|error|cannot)|usb .*error',line,re.I)] for role in ('server','client')},'assertion_count'")
s+='''
def main():
 global EXPECTED_BINARIES
 p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);p.add_argument('--backend',choices=['virtual','usbfs','native'],required=True);p.add_argument('--family',choices=['ipv4','ipv6'],required=True);p.add_argument('--expected-binaries',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 EXPECTED_BINARIES=json.loads(a.expected_binaries.read_text())
 if set(EXPECTED_BINARIES)!={'rustusb','rustusb-helper'} or not all(re.fullmatch('[0-9a-f]{64}',v) for v in EXPECTED_BINARIES.values()):raise ValueError('exact root-reported build ELF map required')
 if a.output.exists():raise FileExistsError('preserve earlier reviewer result')
 report={'kind':'separate raw lifecycle audit','source_head':HEAD,'evidence':str(a.evidence),'result':'FAIL','reviewer_provenance':{'identity':'/root/journal_harness_review; historical independent parser author; independent method does not imply different reviewer identity','derived_from':'/tmp/rustusb-foreground-six-lifecycle-raw-review-eaa1936-20261023.py','original_sha256':'5e2fa15807016a810aa2eae1a8c07265cabef98f206924522697a657c0bef239','changes':'New run/source/ELF attribution, single-group CLI, preserved raw kernel warning list; lifecycle assertions retained. No runner imports or verifier/pcap calls.'},'limits':['Audit comparisons are not new USB cases.','Only this completed pair is reviewed; no Cargo/QEMU/USB/production executable run.','Historical runtime verdicts/source ownership stay unchanged.','No independent PCAP protocol/decryption review here.','No claim of kernel-hardblocked initialization acceptance, hardware G1/G2 or complete Phase3.','Legacy USBFS intermediate grant journal is not captured; only actual retained final journal is checked.']}
 try:
  report['group']=audit(a.evidence.resolve(),a.backend,a.family);report['result']=report['group']['result']
 except Exception as e:report['error']=type(e).__name__+': '+str(e)
 report['parser_sha256']=sha(Path(__file__).read_bytes());report['source_lock_sha256']=sha(Path('''+repr(LOCK)+''').read_bytes());report['expected_binaries_file_sha256']=sha(a.expected_binaries.read_bytes())
 a.output.write_text(json.dumps(report,indent=2)+'\\n');print(json.dumps({'result':report['result'],'output':str(a.output),'error':report.get('error'),'checks':report.get('group',{}).get('assertion_count')}));return int(report['result']!='PASS')
if __name__=='__main__':raise SystemExit(main())
'''
(OUT/'secure.py').write_text(s)
old=Path('/tmp/rustusb-agent-config-independent-raw-audit-20261024.py');s=old.read_text()
s=replace(s,"EXPECTED='cb08672366561281d3fe45453a37b2985284d8ca'\nSOFTWARE_BASE='eaa19366486b8b99412efbfce1f13d49f73fe0cc'",f"EXPECTED='{HEAD}'\nLOCK=json.loads(pathlib.Path('{LOCK}').read_text())\nEXPECTED_BINARIES=None")
s=replace(s,"self.check('provenance','154 source inputs include actual new service/core',source_count==154 and 'crates/rustusb-service/src/jobs/native.rs' in summary['source_inputs'] and 'crates/rustusb-core/src/application.rs' in summary['source_inputs'])","self.check('provenance','exact current frozen input set includes service/core',summary['source_inputs']=={p:LOCK['source_hashes'][p] for p in LOCK['agent_inputs']})")
start=s.index("   software_manifest_path='docs/evidence/application-service-software-20261023/second/inputs-before.json'");end=s.index("   for name,entry in summary['artifact_manifest'].items():",start)
s=s[:start]+'''   software_manifest_path=LOCK['software_manifest_git_path']
   software_manifest_bytes=subprocess.check_output(['git','show',EXPECTED+':'+software_manifest_path],cwd=REPO);software_manifest=json.loads(software_manifest_bytes)
   self.check('software_attribution','actual completed shared-owner source manifest',sha(software_manifest_bytes)==LOCK['software_manifest_sha256'] and len(software_manifest)==LOCK['software_input_count'])
   for entry in software_manifest:
    name=entry['path'];current=subprocess.check_output(['git','show',EXPECTED+':'+name],cwd=REPO)
    self.check('software_attribution',name,len(current)==entry['bytes'] and sha(current)==entry['sha256'])
   self.check('software_attribution','actual two ELFs match root locked-build attribution',summary['binary_sha256']==EXPECTED_BINARIES)
   self.facts['software_attribution']={'head':EXPECTED,'software_manifest_sha256':sha(software_manifest_bytes),'selected_input_count':len(software_manifest),'expected_elf':EXPECTED_BINARIES,'scope':'Read-only exact Gitblob/ELF attribution; no execution or relabeling of software tests.'}
'''+s[end:]
s=s.replace('These cb08672 runs close the old actual TLS-config/public-pin/metadata capture gap. The eaa1936 parser/reports and their missing-evidence boundary are retained unchanged. Private key bytes remain deliberately uncollected.','Reuses the cb08672 actual public configuration/pin/metadata parser; all historical scripts/reports remain unchanged. Private key bytes remain deliberately uncollected.')
start=s.index('def main():')
s=s[:start]+'''def main():
 global EXPECTED_BINARIES
 a=argparse.ArgumentParser();a.add_argument('--evidence',type=pathlib.Path,required=True);a.add_argument('--output',type=pathlib.Path,required=True);a.add_argument('--expected-binaries',type=pathlib.Path,required=True);args=a.parse_args()
 EXPECTED_BINARIES=json.loads(args.expected_binaries.read_text())
 if set(EXPECTED_BINARIES)!={'rustusb','rustusb-helper'} or not all(re.fullmatch('[0-9a-f]{64}',v) for v in EXPECTED_BINARIES.values()):raise ValueError('exact root-reported ELF map required')
 if args.output.exists():raise FileExistsError('preserve earlier reviewer result')
 r=Audit(args.evidence.resolve()).report();r['parser_sha256']=sha(pathlib.Path(__file__).read_bytes());r['source_lock_sha256']=sha(pathlib.Path('''+repr(LOCK)+''').read_bytes());r['reuse']={'original':'/tmp/rustusb-agent-config-independent-raw-audit-20261024.py','sha256':'6d60e2dd0c035c6bcb12f97f1fec0d206c62ceec786da1ac679f6f6b7236b706','changes':'Only source/current software and ELF attribution plus output preservation; actual lifecycle/configuration assertions retained.'};args.output.write_text(json.dumps(r,indent=2)+'\\n');print(json.dumps({'result':r['result'],'checks':r['check_count'],'categories':r['category_counts'],'error':r['error'],'output':str(args.output)}));return int(r['result']!='PASS')
if __name__=='__main__':raise SystemExit(main())
'''
(OUT/'agent.py').write_text(s)
for p in OUT.glob('*.py'):
 ast.parse(p.read_bytes());print(json.dumps({'path':str(p),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'AST':'PASS','executed':False}))
