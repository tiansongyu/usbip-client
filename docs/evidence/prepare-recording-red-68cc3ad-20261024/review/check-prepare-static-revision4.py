import ast, hashlib, importlib.util, json, re, subprocess
from pathlib import Path
out=Path('/tmp/rustusb-prepare-harness-static-revision4-20261024');out.mkdir(exist_ok=False)
repo=Path('/home/ubuntu/github/usbip-client');folder=repo/'scripts/qemu_prepare_recording_shutdown'
report={'scope':'static construction only; no guest, C execution, Cargo or QEMU','checks':[],'files':{}}
def check(ok,label):
 report['checks'].append({'check':label,'passed':bool(ok)})
 if not ok:raise RuntimeError(label)
for p in folder.glob('*.py'):ast.parse(p.read_text());check(True,'AST '+p.name)
spec=importlib.util.spec_from_file_location('prepare_static',folder/'run.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
for case in ['native-resource','usbfs-resource','usbfs-initializer']:
 for stop in ['job','term']:
  generated=out/f'{case}-{stop}.sh';generated.write_text(m.guest_source(case,stop))
  for argv in [['dash','-n',str(generated)],['shellcheck','-S','warning','-e','SC2034',str(generated)]]:
   p=subprocess.run(argv,capture_output=True,text=True);(out/(case+'-'+stop+'-'+argv[0]+'.log')).write_text(p.stdout+p.stderr);check(p.returncode==0,' '.join(argv[:2])+' '+case+'/'+stop)
# Compile every quoted jq expression in the new fragment with real jq.
text=(folder/'guest-init.sh').read_text()
expressions=[]
for match in re.finditer(r"(?m)^\s*(?:if )?jq\b([\s\S]*?)(?=\n(?:[^ \t]|    [^ \t]|        [^ \t])|\Z)",text):
 exprs=re.findall(r"'([^']*)'",match[0],re.S)
 for expr in exprs:
  if expr not in expressions:expressions.append(expr)
# Simpler statement scan finds all single-quoted literals following jq, bounded by newline shell command continuation; explicit known multiline functions included above.
for line in text.splitlines():
 if 'jq ' in line:
  for expr in re.findall(r"'([^']*)'",line):
   if expr not in expressions:expressions.append(expr)
for i,expr in enumerate(expressions):
 if expr.startswith(('/', '%')):continue
 vars=set(re.findall(r'\$([A-Za-z_][A-Za-z0-9_]*)',expr))
 argv=['jq','-n']
 for var in sorted(vars):argv+=['--argjson',var,'null']
 argv+=['def fixture: ('+expr+'); null']
 p=subprocess.run(argv,capture_output=True,text=True)
 (out/f'jq-{i}.txt').write_text(expr+'\n'+p.stdout+p.stderr)
 check(p.returncode==0,f'jq expression compile {i}')
# Exercise the actual second Resource selection predicate with meaningful draft transitions.
select=re.search(r"if jq -e '([^']*audit\[-1\][^']*)'",text)[1]
base={'schema_version':2,'audit':[{'action':'resource','profile':'pool'}], 'sessions':[{'profile':'pool','role':'export','terminal':False,'attempts':[{'resource_id':'r1'}]},{'profile':'pool','role':'export','terminal':False,'attempts':[{'resource_id':'r2'}]}]}
for name,mutate,expected in [('second',lambda j:None,0),('first',lambda j:j['sessions'][1]['attempts'][0].update(resource_id=None),1),('empty',lambda j:j.update(sessions=[]),1),('wrong-action',lambda j:j['audit'][-1].update(action='begin'),1),('wrong-profile',lambda j:j['audit'][-1].update(profile='other'),1)]:
 j=json.loads(json.dumps(base));mutate(j);p=subprocess.run(['jq','-e',select],input=json.dumps(j),text=True,capture_output=True);(out/f'select-{name}.json').write_text(json.dumps({'fixture':j,'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr},indent=2));check(p.returncode==expected,'second Resource select '+name)
# Exact proof predicate must reject empty/missing/changed terminals.
proof=re.findall(r"jq -e --slurpfile sessions /tmp/selected-sessions.json '([\s\S]*?)'",text)
for mode,expr in zip(['native','usbfs'],proof):
 kind='native_export' if mode=='native' else 'usbfs_grant';prf='native_export_restored' if mode=='native' else 'physical_baseline_restored'
 ses=[{'device_key':str(i),'attempts':[{'resource_id':str(i),'correlation':{'attempt_id':str(i)}}]} for i in [1,2]]
 terminal=[{'resource_id':str(i),'kind':kind,'owner_uid':2000,'correlation':{'attempt_id':str(i)},'device_key':str(i),'proof':prf} for i in [1,2]]
 root={'exports':[],'records':[],'terminals':[{'terminal':t} for t in terminal] if mode=='native' else terminal}
 for name,mutate,expected in [('both',lambda j:None,0),('one',lambda j:j['terminals'].pop(),1),('empty',lambda j:j.update(terminals=[]),1),('live',lambda j:j['exports' if mode=='native' else 'records'].append({'lease_id':'1'}),1)]:
  j=json.loads(json.dumps(root));mutate(j);p=subprocess.run(['jq','-e','--argjson','sessions',json.dumps([ses]),expr],input=json.dumps(j),text=True,capture_output=True);(out/f'proof-{mode}-{name}.json').write_text(json.dumps({'fixture':j,'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr},indent=2));check(p.returncode==expected,'two original root proofs '+mode+'/'+name)
for p in sorted(folder.iterdir()):
 if p.is_file():report['files'][str(p.relative_to(repo))]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size}
report['check_count']=len(report['checks']);report['passed']=all(c['passed'] for c in report['checks']);(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':report['passed'],'checks':report['check_count'],'output':str(out)}))
