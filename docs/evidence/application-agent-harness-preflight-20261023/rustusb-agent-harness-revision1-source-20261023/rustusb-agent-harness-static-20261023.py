import ast,hashlib,importlib.util,json,pathlib,re,subprocess
ROOT=pathlib.Path('/home/ubuntu/github/usbip-client');OUT=pathlib.Path('/tmp/rustusb-agent-harness-static-20261023.json')
checks=[]
def record(name,ok,detail=None):
 checks.append({'name':name,'pass':bool(ok),'detail':detail})
def command(name,args):
 r=subprocess.run(args,capture_output=True,text=True,cwd=ROOT);record(name,r.returncode==0,{'argv':args,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
p=ROOT/'scripts/qemu/run-agent-qemu.py';ast.parse(p.read_text());record('Python AST',True)
spec=importlib.util.spec_from_file_location('agent_static',p);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
generated=module.guest_source();script=pathlib.Path('/tmp/rustusb-agent-generated-init.sh');script.write_text(generated)
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
files=[]
for relative in ['scripts/qemu/run-agent-qemu.py','scripts/qemu/guest-agent-init.sh','scripts/qemu/run-secure-qemu.py','scripts/qemu/guest-secure-init.sh']:
 p=ROOT/relative;b=p.read_bytes();files.append({'path':relative,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
r={'scope':'author static checks only; no product executable, Cargo, QEMU, USB or generated PID1 executed','checks':checks,'check_count':len(checks),'jq_compile_count':len(expressions),'all_pass':all(c['pass'] for c in checks),'files':files,'generated_init':{'path':str(script),'bytes':len(generated.encode()),'sha256':hashlib.sha256(generated.encode()).hexdigest()}}
OUT.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps({'report':str(OUT),'all_pass':r['all_pass'],'checks':len(checks),'jq':len(expressions),'failed':[c for c in checks if not c['pass']]},indent=2))
