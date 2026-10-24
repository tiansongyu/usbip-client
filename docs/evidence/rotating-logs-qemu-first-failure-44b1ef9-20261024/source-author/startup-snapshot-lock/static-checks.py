"""Author source/fixture checks; no guest/product executable or QEMU execution."""
import ast,copy,hashlib,importlib.util,json,pathlib,re,subprocess
ROOT=pathlib.Path('/home/ubuntu/github/usbip-client')
OUT=pathlib.Path('/tmp/rustusb-process-logging-qemu-static-revision2-20261024');OUT.mkdir(exist_ok=True)
checks=[]
def record(name,ok,detail=None):checks.append({'name':name,'pass':bool(ok),'detail':detail})
def command(name,args):
 r=subprocess.run(args,capture_output=True,text=True,cwd=ROOT);record(name,r.returncode==0,{'argv':args,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
p=ROOT/'scripts/qemu_process_logging/run.py';ast.parse(p.read_text());record('run AST',True)
spec=importlib.util.spec_from_file_location('logging_static',p);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
generated=module.guest_source();script=OUT/'generated-init.sh';script.write_text(generated)
command('generated shell syntax',['dash','-n',str(script)])
command('generated ShellCheck',['shellcheck','-s','sh',str(script)])
command('gate shell syntax',['dash','-n','scripts/qemu_process_logging/gate.sh'])
command('gate ShellCheck',['shellcheck','-s','sh','scripts/qemu_process_logging/gate.sh'])
command('runner help',['python3','-B','scripts/qemu_process_logging/run.py','--help'])
command('owned diff whitespace',['git','diff','--check','--','scripts/qemu_process_logging','docs/research/rotating-logs-design.md'])
joined='\n'.join(line for line in generated.splitlines() if not line.lstrip().startswith('#')).replace('\\\n',' ')
expressions=[]
for m in re.finditer(r"'([^']*)'",joined):
 prefix=joined[joined.rfind('\n',0,m.start())+1:m.start()]
 if not prefix.rstrip().endswith('sed') and ('jq ' in prefix or 'wait_sessions ' in prefix) and ('{' in m[1] or '.' in m[1] or 'length' in m[1]):expressions.append(m[1])
for n,expr in enumerate(expressions):
 args=['jq','-n']
 for var in sorted(set(re.findall(r'\$([A-Za-z_]\w*)',expr))):args+=['--argjson',var,'null']
 args+=['if false then ('+expr+') else true end']
 r=subprocess.run(args,capture_output=True,text=True);record(f'jq compile {n+1}',r.returncode==0,{'expression':expr,'stderr':r.stderr})
release=[e for e in expressions if 'def identity:' in e]
record('unique same-saved release predicate',len(release)==1)
raw=pathlib.Path('/tmp/rustusb-prepare-stop-final-1582f06-20261024/agent-native-ipv4/server-artifacts')
if len(release)==1:
 app=json.loads((raw/'agent-stopped-2.json').read_text());held=json.loads((raw/'agent-held-3.json').read_text());root=json.loads((raw/'root-stopped-2.json').read_text())
 cases=[('real historical two-epoch complete release',app,root,True)]
 for damage in ['missing_terminal','wrong_proof','changed_correlation','missing_session','wrong_resource','unfinished_job']:
  a,b=copy.deepcopy(app),copy.deepcopy(root)
  if damage=='missing_terminal':b['terminals'].pop()
  if damage=='wrong_proof':b['terminals'][0]['terminal']['proof']='native_export_gone'
  if damage=='changed_correlation':b['terminals'][0]['terminal']['correlation']['attempt_id']='00000000-0000-0000-0000-000000000000'
  if damage=='missing_session':a['sessions'].pop()
  if damage=='wrong_resource':b['terminals'][0]['terminal']['resource_id']='00000000-0000-0000-0000-000000000000'
  if damage=='unfinished_job':a['jobs'][-1]['state']='running'
  cases.append((damage,a,b,False))
 for name,a,b,expected in cases:
  for field,value in [('app',a),('held',held),('root',b)]: (OUT/(field+'.json')).write_text(json.dumps(value))
  r=subprocess.run(['jq','-e','--slurpfile','app',str(OUT/'app.json'),'--slurpfile','held',str(OUT/'held.json'),release[0],str(OUT/'root.json')],capture_output=True,text=True)
  record('saved release fixture '+name,(r.returncode==0)==expected,{'exit':r.returncode,'expected':expected,'stderr':r.stderr})
report={'scope':'Source syntax and historical/altered JSON predicates only; no CLI, Cargo, guest init, USB, QEMU or gate process executed','checks':checks,'check_count':len(checks),'jq_count':len(expressions),'all_pass':all(c['pass'] for c in checks),'files':{str(p.relative_to(ROOT)):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in (ROOT/'scripts/qemu_process_logging').iterdir() if p.is_file()},'generated_init_sha256':hashlib.sha256(generated.encode()).hexdigest()}
(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'all_pass':report['all_pass'],'count':len(checks),'jq_count':len(expressions),'failed':[c for c in checks if not c['pass']],'report':str(OUT/'report.json')},indent=2))
