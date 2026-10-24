"""Constructed cleanup/collector fault fixture; no CLI or subprocess is executed."""
import hashlib,importlib.util,json
from pathlib import Path
from types import SimpleNamespace
BASE=Path('/tmp/rustusb-process-logging-runner-author-revision2-20261024')
SOURCE=Path('/home/ubuntu/github/usbip-client/scripts/test-process-logging.py')
spec=importlib.util.spec_from_file_location('process_logging_collector_fixture',SOURCE)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
evidence=BASE/'fixture';evidence.mkdir(exist_ok=False)
visited=[];checks=[]
def check(name,condition):
 checks.append({'name':name,'passed':bool(condition)})
 if not condition:raise AssertionError(name)
class Close:
 def __init__(self,name,fail=False):self.name=name;self.fail=fail
 def close(self):
  visited.append(self.name)
  if self.fail:raise OSError('injected close: '+self.name)
class Process:
 def __init__(self,name):self.stdin=Close(name+' stdin',name=='first');self.stdout=Close(name+' stdout')
 def poll(self):return 0
run=m.Run(Path('/unused/no-child-executed'),evidence,None)
original_save=m.save
def save(path,value):
 if path==evidence/'first'/'stdout-events.json':raise OSError('injected event artifact failure')
 return original_save(path,value)
m.save=save
for name in ['first','second']:
 directory=evidence/name;directory.mkdir()
 run.children.append({'process':Process(name),'row':{'name':name},'directory':directory,
  'stderr':Close(name+' stderr'),'output':SimpleNamespace(raw=Close(name+' raw',name=='first'),events=[]),'protocol':[]})
run.client_sockets=[Close('first socket',True),Close('second socket')]
summary={'result':'FAIL','error':{'type':'RuntimeError','message':'primary-case-failure'},'collection_errors':run.collection_errors}
run.primary_checkpoint(summary)
forced=run.finalize()
check('no fake process was signalled',forced==[])
check('later child and all closes attempted',all(x in visited for x in ['first stderr','second raw','second stdin','second stdout','second stderr','first socket','second socket']))
check('later child receipt persisted',(evidence/'second'/'process.json').is_file())
check('four independent failures retained',len(run.collection_errors)==4)
check('primary checkpoint survives later errors',json.loads((evidence/'primary-failure.json').read_text())['error']['message']=='primary-case-failure')
store=evidence/'store';store.mkdir();(store/'a').write_bytes(b'a');(store/'b').write_bytes(b'b')
original_metadata=m.metadata
def metadata(path):
 if path==store/'a':raise OSError('injected first original metadata failure')
 return original_metadata(path)
m.metadata=metadata
run.archive_final(store,evidence/'archive')
check('second original archived despite first metadata failure',(evidence/'archive'/'b').read_bytes()==b'b')
check('per-original error retained',run.collection_errors[-1]['operation'].endswith('a:archive-original'))
def final_save(path,value):
 if path==evidence/'summary.json':raise OSError('injected final summary filesystem failure')
 return original_save(path,value)
m.save=final_save
path,sha=run.final_summary(summary)
check('fallback report actually persisted',path is not None and path!=evidence/'summary.json' and path.is_file())
actual=json.loads(path.read_text())
check('fallback preserves original primary failure',actual['error']['message']=='primary-case-failure')
check('fallback records all six collection failures',len(actual['collection_errors'])==6)
check('fallback actual bytes match receipt',hashlib.sha256(path.read_bytes()).hexdigest()==sha)
report={'scope':'constructed collector/close errors only; zero subprocesses/CLI/Cargo/QEMU/USB','source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'checks':checks,'passed':all(c['passed'] for c in checks),'visited':visited,'fallback_original':str(path),'fallback_sha256':sha}
(BASE/'cleanup-fixture-report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'passed':report['passed'],'diagnostic_checks':len(checks),'fallback':str(path)}))
