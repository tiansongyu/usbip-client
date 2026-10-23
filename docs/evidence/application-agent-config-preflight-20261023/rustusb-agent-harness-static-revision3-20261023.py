import ast,copy,hashlib,importlib.util,json,pathlib,re,subprocess
ROOT=pathlib.Path('/home/ubuntu/github/usbip-client');OUT=pathlib.Path('/tmp/rustusb-agent-harness-static-revision3-20261023.json')
checks=[]
def record(name,ok,detail=None):
 checks.append({'name':name,'pass':bool(ok),'detail':detail})
def command(name,args):
 r=subprocess.run(args,capture_output=True,text=True,cwd=ROOT);record(name,r.returncode==0,{'argv':args,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
p=ROOT/'scripts/qemu/run-agent-qemu.py';ast.parse(p.read_text());record('Python AST',True)
spec=importlib.util.spec_from_file_location('agent_static',p);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
generated=module.guest_source();script=pathlib.Path('/tmp/rustusb-agent-generated-init-revision3.sh');script.write_text(generated)
command('generated PID1 shell syntax',['dash','-n',str(script)])
command('generated PID1 ShellCheck',['shellcheck','-s','sh',str(script)])
command('fragment shell syntax',['dash','-n','scripts/qemu/guest-agent-init.sh'])
command('runner help',['python3','scripts/qemu/run-agent-qemu.py','--help'])
command('owned diff whitespace',['git','diff','--check','--','scripts/qemu/run-agent-qemu.py','scripts/qemu/guest-agent-init.sh'])
joined='\n'.join(line for line in generated.splitlines() if not line.lstrip().startswith('#')).replace('\\\n',' ')
expressions=[]
for m in re.finditer(r"'([^']*)'",joined):
 prefix=joined[joined.rfind('\n',0,m.start())+1:m.start()]
 if not prefix.rstrip().endswith('sed') and ('jq ' in prefix or 'wait_sessions ' in prefix) and ('{' in m[1] or '.' in m[1] or 'length' in m[1]):
  expressions.append(m[1])
for n,expr in enumerate(expressions):
 args=['jq','-n']
 for var in sorted(set(re.findall(r'\$([A-Za-z_]\w*)',expr))): args+=['--argjson',var,'null']
 args+=['if false then ('+expr+') else true end']
 r=subprocess.run(args,capture_output=True,text=True)
 record(f'actual jq compile {n+1}',r.returncode==0,{'expression':expr,'stderr':r.stderr})
# Actual expressions are evaluated against role/phase fixtures; these are parser
# and assertion checks only, never fake USB or QEMU evidence.
cases=[
 ('connected import', '.response.sessions|map(select(.state=="Connected" and .evidence=="local_enumeration" and .terminal==false))|length==1', [{'state':'Connected','evidence':'local_enumeration','terminal':False}],True),
 ('server proof cannot replace local enumeration','.response.sessions|map(select(.state=="Connected" and .evidence=="local_enumeration" and .terminal==false))|length==1',[{'state':'Connected','evidence':'import_reply_flushed','terminal':False}],False),
 ('Root proof positive','.response.sessions|length==1 and all(.[]; .state=="Disconnected" and .terminal and .cleanup=="verified" and .evidence=="root_journal_proof" and (.attempts|length)>0 and all(.attempts[]; .proof!=null))',[{'state':'Disconnected','terminal':True,'cleanup':'verified','evidence':'root_journal_proof','attempts':[{'proof':'native_import_released'}]}],True),
 ('missing attempt proof rejected','.response.sessions|length==1 and all(.[]; .state=="Disconnected" and .terminal and .cleanup=="verified" and .evidence=="root_journal_proof" and (.attempts|length)>0 and all(.attempts[]; .proof!=null))',[{'state':'Disconnected','terminal':True,'cleanup':'verified','evidence':'root_journal_proof','attempts':[{'proof':None}]}],False),
]
for name,expr,rows,expected in cases:
 r=subprocess.run(['jq','-e',expr],input=json.dumps({'response':{'sessions':rows}}),capture_output=True,text=True)
 record('fixture '+name,(r.returncode==0)==expected,{'exit':r.returncode,'expected_match':expected,'stderr':r.stderr})
# Exercise the actual final-history verifier with complete observations and
# deliberate loss; these fabricated records never become runtime evidence.
def history_fixture(role):
 starts=[]; held=[]; final={"jobs":[],"sessions":[]}
 for epoch in [1,2]:
  profiles=["server"] if role=="server" else ["hid","serial"]
  for profile in profiles:
   job={"id":f"job-{epoch}-{profile}","instance_id":f"epoch-{epoch}","profile":profile,"operator_uid":1000}
   starts.append({"response":{"job":copy.deepcopy(job)}});final["jobs"].append(job)
   roles=["export","export","incoming","incoming"] if role=="server" else ["import"]
   for n,kind in enumerate(roles):
    sid=f"session-{epoch}-{profile}-{n}"
    final["sessions"].append({"id":sid,"instance_id":job["instance_id"],"job_id":job["id"],"profile":profile,"role":kind,"device_key":f"native:fixture-{n%2}","principal":None,"attempts":[{"kind":{"export":"native_export","incoming":"native_stub","import":"native_import"}[kind],"correlation":{"attempt_id":sid},"resource_id":sid,"proof":None}]})
   held.append(copy.deepcopy(final))
 return final,starts,held
for role in ["server","client"]:
 state,starts,held=history_fixture(role)
 record(role+' actual history verifier accepts full two-epoch fixture',module.history_identity(state,starts,held,role)==(8 if role=='server' else 4))
 for loss in ['empty','drop_old_session','drop_old_job']:
  damaged=copy.deepcopy(state)
  if loss=='empty': damaged['sessions']=[]
  elif loss=='drop_old_session': damaged['sessions'].pop(0)
  else: damaged['jobs'].pop(0)
  try: module.history_identity(damaged,starts,held,role)
  except RuntimeError: rejected=True
  else: rejected=False
  record(role+' actual history verifier rejects '+loss,rejected)
files=[]
for relative in ['scripts/qemu/run-agent-qemu.py','scripts/qemu/guest-agent-init.sh','scripts/qemu/run-secure-qemu.py','scripts/qemu/guest-secure-init.sh']:
 p=ROOT/relative;b=p.read_bytes();files.append({'path':relative,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
r={'scope':'author static checks only; no product executable, Cargo, QEMU, USB or generated PID1 executed','checks':checks,'check_count':len(checks),'jq_compile_count':len(expressions),'all_pass':all(c['pass'] for c in checks),'files':files,'generated_init':{'path':str(script),'bytes':len(generated.encode()),'sha256':hashlib.sha256(generated.encode()).hexdigest()}}
OUT.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps({'report':str(OUT),'all_pass':r['all_pass'],'checks':len(checks),'jq':len(expressions),'failed':[c for c in checks if not c['pass']]},indent=2))
