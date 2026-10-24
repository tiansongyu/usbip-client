"""Read-only cross-run source binding and initializer entry detail supplement."""
import hashlib
import json
from pathlib import Path

E=Path('/tmp/rustusb-prepare-stop-final-1582f06-20261024')
O=Path('/tmp/rustusb-prepare-final-158-raw-review-20261024')
checks=[]
def fact(p):
 b=p.read_bytes();return {'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def check(value,name):
 checks.append({'check':name,'passed':bool(value)})
 if not value:raise RuntimeError(name)
report=json.loads((O/'all-eight-first/report.json').read_text())
declared=json.loads((E/'inputs-before.json').read_text());global_inputs={x['path']:{'bytes':x['bytes'],'sha256':x['sha256']} for x in declared}
summary=[json.loads((Path(c['directory'])/'summary.json').read_text()) for c in report['cases']]
check(len(summary)==8 and all(x['source_inputs']==summary[0]['source_inputs'] for x in summary),'all eight completed runs use identical source input mapping')
check(all(x['completed_utc'] and x['head']==report['runtime_head'] and x['result']=='PASS' for x in summary),'all eight exact source results are terminal')
for path,value in summary[0]['source_inputs'].items():check(global_inputs.get(path)==value,'eight-run source is matrix frozen input '+path)
entries=[];ipc=[]
for case in report['cases']:
 d=Path(case['directory']);a=d/'server-artifacts';sel=json.loads((a/'selection.json').read_text());candidate=json.loads((a/f'gate-candidate-{sel["candidate"]}.json').read_text());events=[json.loads(x) for x in (a/'gate-events.jsonl').read_text().splitlines()]
 if case['case']=='usbfs-initializer':
  entry=[x for x in events if x['event']=='syscall_entry' and x.get('kind')=='initializer']
  check(len(entry)==1 and entry[0]['syscall_nr']==16 and entry[0]['request']==candidate['request']==3222820096,'actual x86_64 ioctl/USBDEVFS_CONTROL entry '+case['stop'])
  check(all(entry[0][k]==candidate[k] for k in ('tracee_pid','tracee_starttime','tid','fd','node_device','node_inode','node_rdev')) and entry[0]['monotonic_ns']<candidate['monotonic_ns'],'same exact received node and original initializer entry '+case['stop'])
  p=d/'source/scripts/qemu_prepare_recording_shutdown/prepare-gate.c';src=p.read_text()
  check('static const unsigned char expected[12] = {0x80, 0x08, 0, 0, 0, 0, 1, 0, 50, 0, 0, 0};' in src and 'if (!configuration_header(thread->tid, info.entry.args[2])) return -1;' in src,'frozen guest probe admits only exact GET_CONFIGURATION header '+case['stop'])
  entries.append({'case':d.name,'actual_entry':entry[0],'classification_source':{'path':str(p),'facts':fact(p)},'limit':'Control header is classified by the source-locked probe; original log contains ioctl/request/classification, not a separately dumped USB control payload.'})
 if case['stop']!='term':
  status=(a/'wait-stop.txt').read_text().strip();stderr=(a/'stop-target.log').read_text();response=json.loads((a/'stop-target.json').read_text())
  check(status=='0' and stderr=='' and response['response']=={'result':'stop_accepted','owner_present':True},'actual completed operator stop response '+d.name)
  state=json.loads((a/'agent-final.json').read_text());targets=[x for x in state['audit'] if x['action']=='stop' and x['actor_uid']==1000 and x['job_id']==sel['job']]
  if case['stop']=='session':
   sid=json.loads((a/'local-target.json').read_text())['id'];check(any(x['session_id']==sid for x in targets),'exact authorized target StopSession persisted '+d.name)
  else:check(any(x['session_id'] is None for x in targets),'exact authorized StopJob persisted '+d.name)
  ipc.append({'case':d.name,'wait':int(status),'stderr':stderr,'response':response,'scope':'Public operator wrapper UID1000 plus actual durable actor_uid audit; there is no separate transient CLI /proc identity sample.'})
original_manifest=json.loads((O/'all-eight-first/originals-manifest.json').read_text())
check(all(fact(E/case/name)==value for case,files in original_manifest.items() for name,value in files.items()),'all 3420 original files still match completed raw review manifest')
result={'schema_version':1,'passed':True,'checks':checks,'diagnostic_count':len(checks),'scope':'Supplementary source/entry/IPC facts, not additional USB executions','same_source_count':len(summary[0]['source_inputs']),'matrix_initial_input_count':len(global_inputs),'initializer_entries':entries,'actual_operator_stop_responses':ipc,'matrix_scope':'Matrix may still run remaining health/legacy scenarios. No whole-23 terminal claim is made here.'}
(O/'source-entry-supplement.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'passed':True,'diagnostics':len(checks),'same_sources':result['same_source_count'],'matrix_initial_inputs':len(global_inputs)}))
