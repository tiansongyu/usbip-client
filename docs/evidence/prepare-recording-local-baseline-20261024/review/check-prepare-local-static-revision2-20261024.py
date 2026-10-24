import ast,copy,hashlib,importlib.util,json,re,subprocess
from pathlib import Path
repo=Path('/home/ubuntu/github/usbip-client');folder=repo/'scripts/qemu_prepare_recording_shutdown';out=Path('/tmp/rustusb-prepare-local-static-revision2-20261024');out.mkdir(exist_ok=False)
report={'scope':'source composition/static/jq fixtures only, no Cargo/QEMU/USB; historical real bytes seed constructed fixtures, never runtime evidence','checks':[]}
def check(ok,label,details=None):
 report['checks'].append({'check':label,'passed':bool(ok),'details':details})
 if not ok:raise RuntimeError(label)
for p in folder.glob('*.py'):ast.parse(p.read_text());check(True,'AST '+p.name)
s=importlib.util.spec_from_file_location('prepare_local_static',folder/'run.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
for case,stop in [(c,t) for c in ['native-resource','usbfs-resource','usbfs-initializer'] for t in ['job','term']]+[(c,'session') for c in ['native-resource','native-available']]:
 text=m.guest_source(case,stop);p=out/(case+'-'+stop+'.sh');p.write_text(text)
 for cmd in [['dash','-n',str(p)],['shellcheck','-S','warning','-e','SC2034',str(p)]]:
  r=subprocess.run(cmd,capture_output=True,text=True);(out/(case+'-'+stop+'-'+cmd[0]+'.log')).write_text(r.stdout+r.stderr);check(r.returncode==0,' '.join(cmd[:2])+' '+case+'/'+stop)
 if stop!='session':check(text==(Path('/tmp/rustusb-prepare-harness-static-revision4-20261024')/(case+'-'+stop+'.sh')).read_text(),'prior six generated guest bytes unchanged '+case+'/'+stop)
for case,stop in [('usbfs-resource','session'),('usbfs-initializer','session'),('native-available','job'),('native-available','term')]:
 try:m.guest_source(case,stop)
 except ValueError:check(True,'invalid mode rejected '+case+'/'+stop)
 else:check(False,'invalid mode rejected '+case+'/'+stop)
fragment=(folder/'guest-local-fragments.sh').read_text()
# Compile fixed quoted jq expressions; ignore separate awk/stat shell literals.
expressions=[]
for match in re.finditer(r'jq\b[^\n]*',fragment):
 line=match[0]
 for expression in re.findall("'([^']*)'",line):
  if expression not in expressions:expressions.append(expression)
for label in ['local_root_proof','local_host_proof']:
 block=fragment.split(label+'() {\n',1)[1].split('\n}\n',1)[0];expressions.extend(re.findall("'([^']*)'",block,re.S))
for i,expr in enumerate(expressions):
 if expr.startswith('%'):continue
 args=['jq','-n']
 for var in sorted(set(re.findall(r'\$([A-Za-z_][A-Za-z0-9_]*)',expr))):args+=['--argjson',var,'null']
 r=subprocess.run(args+['def fixture: ('+expr+'); null'],capture_output=True,text=True);(out/f'jq-{i}.txt').write_text(expr+'\n'+r.stdout+r.stderr);check(r.returncode==0,f'actual jq compile {i}')
base=Path('/tmp/rustusb-prepare-stop-native-resource-job-68cc3ad-20261024/server-artifacts')
selected=json.loads((base/'selected-state.json').read_text());keys=json.loads((base/'keys.json').read_text())
selector=re.findall(r"jq -e --arg key \"\$SERIAL_KEY\" '([^']*)'",fragment)
for mode,expr in zip(['resource','available'],selector):
 fixture=copy.deepcopy(selected)
 if mode=='available':
  for session in fixture['sessions']:session.update(state='Available',evidence='export_ready')
  fixture['audit'][-1].update(action='observe',state='Available',evidence='export_ready')
 for name,mutate,expected in [('valid',lambda d:None,0),('wrong-target',lambda d:d['audit'][-1].update(session_id=next(s['id'] for s in d['sessions'] if s['device_key']==keys[0])),1),('one-owner',lambda d:d['sessions'].pop(),1),('missing-resource',lambda d:d['sessions'][0]['attempts'][0].update(resource_id=None),1),('wrong-transition',lambda d:d['audit'][-1].update(action='start'),1)]:
  d=copy.deepcopy(fixture);mutate(d);r=subprocess.run(['jq','-e','--arg','key',keys[1],expr],input=json.dumps(d),capture_output=True,text=True);(out/f'select-{mode}-{name}.json').write_text(json.dumps({'fixture':d,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr},indent=2));check(r.returncode==expected,'exact local selector '+mode+'/'+name)
held=json.loads((base/'root-held.json').read_text());closed=json.loads((base/'root-after-release.json').read_text());target=next(s for s in selected['sessions'] if s['device_key']==keys[1]);survivor=next(s for s in selected['sessions'] if s['device_key']==keys[0]);wanted={'schema_version':2,'records':[],'exports':[r for r in held['exports'] if r['lease_id']==survivor['attempts'][0]['resource_id']],'terminals':[r for r in closed['terminals'] if r['terminal']['resource_id']==target['attempts'][0]['resource_id']]}
root_expr=re.search(r"local_root_proof\(\) \{[\s\S]*?'([\s\S]*?)'",fragment)[1]
for name,mutate,expected in [('valid',lambda d:None,0),('both-closed',lambda d:d.update(exports=[]),1),('no-terminal',lambda d:d.update(terminals=[]),1),('sibling-new-lease',lambda d:d['exports'][0].update(lease_id='different'),1),('sibling-owner',lambda d:d['exports'][0]['owner'].update(pid=99999),1),('target-wrong-proof',lambda d:d['terminals'][0]['terminal'].update(proof='native_export_gone'),1)]:
 d=copy.deepcopy(wanted);mutate(d);args=['jq','-e']
 for key,value in [('target',[target]),('survivor',[survivor]),('original',[held])]:args+=['--argjson',key,json.dumps(value)]
 r=subprocess.run(args+[root_expr],input=json.dumps(d),capture_output=True,text=True);(out/f'local-proof-{name}.json').write_text(json.dumps({'fixture':d,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr},indent=2));check(r.returncode==expected,'local target-only proof '+name)
host=json.loads((base/'host-before.json').read_text());bound=json.loads((base/'host-held.json').read_text());bus=next(r['bus'] for r in host if r['vendor']=='0403');mixed=sorted([r for r in host if r['bus']==bus]+[r for r in bound if r['bus']!=bus],key=lambda r:r['bus']);host_expr=re.search(r"local_host_proof\(\) \{[\s\S]*?'([\s\S]*?)'",fragment)[1]
for name,fixture,expected in [('valid',mixed,0),('both-restored',host,1),('both-bound',bound,1),('empty',[],1)]:
 r=subprocess.run(['jq','-e','--arg','target',bus,'--argjson','before',json.dumps([host]),'--argjson','held',json.dumps([bound]),host_expr],input=json.dumps(fixture),capture_output=True,text=True);(out/f'local-host-{name}.json').write_text(json.dumps({'fixture':fixture,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr},indent=2));check(r.returncode==expected,'actual mixed baseline predicate '+name)
report['seed_runtime_source']='read-only 68cc3ad original RED; mutated copies are explicitly synthetic fixtures';report['source_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()};report['check_count']=len(report['checks']);report['passed']=all(c['passed'] for c in report['checks']);(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':report['passed'],'checks':report['check_count'],'output':str(out)}))
