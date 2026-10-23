#!/usr/bin/env python3
"""Nine-group immutable raw review driver. Never imports or invokes a runner."""
from pathlib import Path
import argparse, datetime, hashlib, json, subprocess, sys
HERE=Path(__file__).resolve().parent
BASE=Path('/tmp/rustusb-data-link-ready-531e0c3-20261024')
REPO=Path('/home/ubuntu/github/usbip-client')
HEAD='531e0c32a4b0e7252ca771ab4f10042c71c8c195'
NAMES=[f'secure-{backend}-{family}' for backend in ['virtual','usbfs','native'] for family in ['ipv4','ipv6']]+['agent-native-ipv4','agent-native-ipv6','sigterm']
def load(p):return json.loads(p.read_bytes())
def sha(b):return hashlib.sha256(b).hexdigest()
def filemeta(p):
 b=p.read_bytes();return {'path':str(p),'bytes':len(b),'sha256':sha(b)}
def ready():
 terminal=BASE/'result.json'
 if not terminal.exists():return {'status':'WAITING_FOR_TERMINAL_BATCH','reason':'final result.json absent','completed_receipts':len(load(BASE/'results.json'))}
 result=load(terminal);runs=[r for r in load(BASE/'results.json') if not r['case'].startswith('health-')]
 if len(runs)!=9 or [x['case'] for x in runs]!=NAMES:return {'status':'WAITING_FOR_TERMINAL_BATCH','reason':'nine ordered receipts absent','completed_receipts':len(runs)}
 return {'status':'READY_FOR_RAW_REVIEW','runtime_result':result.get('result'),'completed_receipts':9}
def main():
 p=argparse.ArgumentParser();p.add_argument('--run',action='store_true');args=p.parse_args()
 status=ready();print(json.dumps(status))
 if not args.run or status['status']!='READY_FOR_RAW_REVIEW':return 2 if args.run else 0
 report_path=HERE/'report.json'
 if report_path.exists():raise FileExistsError('preserve previous review result: '+str(report_path))
 checks=[];groups=[]
 def check(name,condition):
  checks.append({'name':name,'passed':bool(condition)})
  if not condition:raise ValueError(name)
 report={'schema_version':1,'result':'FAIL','source_head':HEAD,'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'method':'Copied and disclosed historical independent raw parsers plus strict private disk2 checks. No runner import/verify/pcap_review, no Cargo, product process, QEMU or USB execution. Current reviewer contributed related product/harness code; independent method is not an independent author identity.','scope':'Nine old raw-v1 compatibility groups only. New framed-v2 heartbeat matrix is separate.','groups':groups,'batch_checks':checks,'boundaries':['Comparisons are predicates, not USB cases or new test executions.','ELFs and immutable Git blobs are checked against each guest receipt and frozen source, not inferred solely from a PASS summary.','Native helper tracked disk2 and private application disk2 are distinct; untracked foreground helper journals remain disk1.','Raw-v1 health is unavailable; this batch cannot prove heartbeat/RTT or timeout behavior.','USBFS foreground collector has final journal but no intermediate immutable grant journal snapshots.','Legacy SIGTERM lacks raw host FTDI sink and intermediate journals; its prior limited observation remains explicit.','Agent KILL occurs after tty closure; no claim a specific URB/ioctl was pending.','Historical FAIL runs/results are untouched. Full Phase3, hardware and accepted hardblocked initialization remain outside scope.','PCAPs are inventoried; this lifecycle audit does not independently classify their TLS records.']}
 try:
  start=load(BASE/'start.json');end=load(BASE/'result.json');runs=[r for r in load(BASE/'results.json') if not r['case'].startswith('health-')]
  before=load(BASE/'inputs-before.json');after=load(BASE/'inputs-after.json')
  check('exact_clean_start',start['head']==HEAD and start['clean'] is True and start['input_count']==213)
  check('completed_batch_guards',end['result']=='PASS' and end['head']==HEAD and end['inputs_unchanged'] is True and end['input_count']==213)
  check('nine_ordered_actual_process_receipts',[r['case'] for r in runs]==NAMES and all(r['exit_code']==0 and r['unchanged'] is True and r['summary_result']=='PASS' for r in runs))
  check('exact_before_after_inputs',before==after and len(before)==213)
  for row in before:
   b=subprocess.check_output(['git','show',HEAD+':'+row['path']],cwd=REPO)
   check('gitblob:'+row['path'],len(b)==row['bytes'] and sha(b)==row['sha256'])
  elf=load(HERE/'expected-binaries.json');summary_hashes={}
  for name in NAMES:
   directory=BASE/name;summary=load(directory/'summary.json');summary_hashes[name]=sha((directory/'summary.json').read_bytes())
   check(name+':actual_two_guests_and_binaries',summary['result']=='PASS' and summary['guest_exit_codes']=={'server':0,'client':0} and summary['binary_sha256']==elf)
   if name.startswith('secure-'):
    _,backend,family=name.split('-');cmd=[sys.executable,str(HERE/'secure.py'),'--evidence',str(directory),'--backend',backend,'--family',family]
   else:cmd=[sys.executable,str(HERE/('agent.py' if name.startswith('agent-') else 'sigterm.py')),'--evidence',str(directory)]
   output=HERE/(name+'.json');cmd+=['--expected-binaries',str(HERE/'expected-binaries.json'),'--output',str(output)]
   result=subprocess.run(cmd,capture_output=True,text=True)
   (HERE/(name+'.parser.stdout')).write_text(result.stdout);(HERE/(name+'.parser.stderr')).write_text(result.stderr)
   check(name+':independent_parser_exit',result.returncode==0)
   parsed=load(output);check(name+':independent_raw_result',parsed['result']=='PASS')
   check(name+':original_summary_unchanged',summary_hashes[name]==sha((directory/'summary.json').read_bytes()))
   groups.append({'case':name,'summary':filemeta(directory/'summary.json'),'review':filemeta(output),'result':parsed['result'],'comparisons':parsed.get('check_count',parsed.get('group',{}).get('assertion_count')),'guest_exit_codes':summary['guest_exit_codes'],'source_inputs':len(summary.get('source_inputs',{})) or 'layered fingerprint','decoded_guest_artifacts':parsed.get('group',{}).get('redecoded_guest_artifacts',parsed.get('facts',{}).get('decoded_guest_files'))})
  originals=[]
  for path in sorted(BASE.rglob('*')):
   if path.is_file():
    if path.name.endswith('-key.der'):raise ValueError('unexpected private-key artifact, do not inventory: '+str(path))
    originals.append(filemeta(path))
  (HERE/'raw-inputs.json').write_text(json.dumps({'source_head':HEAD,'file_count':len(originals),'files':originals},indent=2)+'\n')
  report.update(result='PASS',binary_sha256=elf,global_source_inputs=213,completed_groups=9,guest_exit_zero=18,recorded_group_comparisons=sum(x['comparisons'] for x in groups),raw_inventory=filemeta(HERE/'raw-inputs.json'))
 except Exception as error:
  report['error']=type(error).__name__+': '+str(error)
 report['parser_provenance']=filemeta(HERE/'preparation.json');report['driver']=filemeta(Path(__file__))
 report_path.write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({'result':report['result'],'report':str(report_path),'error':report.get('error'),'completed_reviews':len(groups)}))
 return int(report['result']!='PASS')
if __name__=='__main__':raise SystemExit(main())
