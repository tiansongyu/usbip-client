"""Author static checks; synthetic gates are not an executed VM or USB case."""
import ast,copy,hashlib,importlib.util,json,os,pathlib,re,shlex,struct,subprocess
ROOT=pathlib.Path('/home/ubuntu/github/usbip-client');OUT=pathlib.Path(__file__).parent
checks=[]
def check(label,passed):
 checks.append({'check':label,'passed':bool(passed)})
 if not passed: raise RuntimeError(label)
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
run=load('diag_static_run',ROOT/'scripts/qemu_diagnostic_export/run.py');V=run.verification
for p in (ROOT/'scripts/qemu_diagnostic_export').glob('*.py'):
 ast.parse(p.read_text());check('Python AST '+p.name,True)
source=run.guest_source();(OUT/'generated-init-final.sh').write_text(source)
for cmd in [['dash','-n',str(OUT/'generated-init-final.sh')],['shellcheck','-s','sh','-S','warning',str(OUT/'generated-init-final.sh')]]:
 r=subprocess.run(cmd,capture_output=True,text=True);(OUT/(cmd[0]+'-final.log')).write_text(r.stdout+r.stderr);check(cmd[0]+' generated init',r.returncode==0)
# Actual jq compile against fixture variables; runtime null/type errors are not
# promoted to positive predicate results. Exit3 is compilation failure.
(OUT/'jq-value.json').write_text('{}\n')
logical=source.replace('\\\n',' ');programs=[]
for line in logical.splitlines():
 m=re.search(r'\bjq\s+',line)
 if not m or line.lstrip().startswith('#'):continue
 try: tokens=shlex.split(line[m.end():],comments=True)
 except ValueError:continue
 args=[];idx=0
 while idx<len(tokens):
  t=tokens[idx]
  if t in ('--arg','--argjson','--slurpfile','--rawfile'):
   args += [t,tokens[idx+1],str(OUT/'jq-value.json') if t in ('--slurpfile','--rawfile') else ('0' if t=='--argjson' else 'fixture')];idx+=3
  elif t.startswith('-'):idx+=1
  else:break
 if idx==len(tokens):continue
 expr=tokens[idx]
 if expr=='$3':continue # runtime wait_sessions parameter, compile each literal caller below
 r=subprocess.run(['jq','-n',*args,expr],capture_output=True,text=True)
 programs.append({'expression':expr,'exit':r.returncode,'diagnostic':r.stderr})

 if r.returncode not in (0,1,4,5):
  (OUT/'jq-first-parser-error.json').write_text(json.dumps({'line':line,'tokens':tokens,'args':args,'expression':expr,'exit':r.returncode,'diagnostic':r.stderr},indent=2)+'\n')
 check('jq compile '+str(len(programs)),r.returncode in (0,1,4,5))
for line in logical.splitlines():
 if 'wait_sessions ' not in line or line.lstrip().startswith('#'):continue
 tokens=shlex.split(line[line.index('wait_sessions '):],comments=True)
 if len(tokens)>3:
  expr=tokens[3];r=subprocess.run(['jq','-n',expr],capture_output=True,text=True);programs.append({'expression':expr,'exit':r.returncode,'diagnostic':r.stderr});check('jq caller compile '+str(len(programs)),r.returncode in (0,1,4,5))
(OUT/'jq-results.json').write_text(json.dumps(programs,indent=2)+'\n')
# Actual POSIX shell octal header construction preserves unsigned high byte.
for length in (1,127,128,255):
 r=subprocess.run(['dash','-c',"octet=$(printf '%03o' \"$1\"); printf '\\000\\000\\000'; printf '%b' \"\\\\$octet\"",'fixture',str(length)],capture_output=True)
 check('actual shell BE frame length '+str(length),r.returncode==0 and r.stdout==struct.pack('>I',length))
# Gate fixture deliberately only covers the check_gate subpredicate. The full
# verifier separately requires nonempty real owners/I/O/config/ELF/baselines.
class Fixture:
 def __init__(self,data):self.files=data;self.checks=[]
 def raw(self,role,name):return self.files[name]
 def data(self,role,name):return V.L.decode(self.raw(role,name))
 def check(self,condition,label):
  if not condition:raise RuntimeError(label)
def encoded(obj):return (json.dumps(obj)+'\n').encode()
base={'candidate':1,'tracee_pid':42,'tracee_starttime':100,'tid':44,'fd':9,'request':74,'file_device':3,'file_inode':10}
candidate=dict(base,event='held',operation='resource',nonce='prepare-stop-v1',monotonic_ns=1_000_000_000)
held1=dict(base,held=True,monotonic_ns=1_100_000_000);held2=dict(base,held=True,monotonic_ns=1_800_000_000)
end=dict(base,event='exit',return_value=0,is_error=False,monotonic_ns=1_900_000_000)
state={'sequence':20,'jobs':[],'sessions':[],'requests':[],'audit':[]}
after=dict(state,sequence=21,audit=[{'sequence':21,'actor_uid':1000,'action':'denied'}])
header={'result':'diagnostic_started','header':{'snapshot':{'instance_id':'fixture','number':2},'committed_sequence':20}}
responses={'post-export-begin':{'response':header},'post-export-end':{'response':{'result':'diagnostic_ended'}}}
files={'selected-candidate.txt':b'1\n','gate-candidate-1.json':encoded(candidate),'gate-held-before.json':encoded(held1),'gate-held-after.json':encoded(held2),'gate-exit-1.json':encoded(end),'wait-tracer.txt':b'0\n','selected-committed.json':encoded(state),'agent-gate-before.json':encoded(state),'agent-gate-after.json':encoded(state),'root-gate-before.json':b'{}\n','root-gate-after.json':b'{}\n','agent-after-release.json':encoded(after)}
events=[dict(base,event='syscall_entry',syscall_nr=74),dict(event='released',monotonic_ns=1_850_000_000),dict(event='tracee_exit',exit_code=0,signal=0),dict(event='finished')]
files['gate-events.jsonl']=b''.join(encoded(e) for e in events)
for mark in ('before','after'):
 files['proc-gate-'+mark+'-fds.jsonl']=encoded({'fd':'9','target':'/var/lib/rustusb/agent/state.next','stat':'3:10:2000:600:regular file'})
 files['proc-gate-'+mark+'.status']=b'Uid:\t2000\t2000\t2000\t2000\n'
for mark,response in responses.items():
 body=encoded(response);files['rpc-'+mark+'.response.json']=body;files['rpc-'+mark+'.response.bin']=struct.pack('>I',len(body))+body
files['rpc-post-export-end.request.json']=encoded({'request':{'profile':'server','snapshot':header['header']['snapshot']}})
for name in ('export-held-before.uptime','export-held-after.uptime','rpc-post-export-begin.uptime','rpc-post-export-end.uptime'):files[name]=b'1.50 1.00\n'
V.check_gate(Fixture(files));check('synthetic coherent held gate accepted by focused predicate only',True)
variants={
 'saved commit differs':('agent-gate-after.json',encoded(after)),
 'held inode replaced':('gate-held-after.json',encoded(dict(held2,file_inode=99))),
 'actual syscall failed':('gate-exit-1.json',encoded(dict(end,return_value=-5,is_error=True))),
 'wrong actual owner':('proc-gate-before.status',b'Uid:\t0\t0\t0\t0\n'),
 'framed reply length mismatch':('rpc-post-export-end.response.bin',b'\0\0\0\1{}'),
 'late exporter receipt':('export-held-after.uptime',b'2.50 1.00\n'),
 'owner capability changed':('root-gate-after.json',b'{"extra":1}\n')}
for label,(name,value) in variants.items():
 changed=dict(files);changed[name]=value
 try:V.check_gate(Fixture(changed))
 except RuntimeError:check('synthetic rejection '+label,True)
 else:check('synthetic rejection '+label,False)
for name,mutate in [('two raw identities collide',lambda b:b.bind('jobs','second',1)),('one identity changes alias',lambda b:b.bind('jobs','first',2)),('bool alias',lambda b:b.bind('jobs','bool',True))]:
 binder=V.AliasBinding(lambda value,label:(_ for _ in ()).throw(RuntimeError(label)) if not value else None,{'jobs':2});binder.bind('jobs','first',1)
 try:mutate(binder)
 except RuntimeError:check('alias privacy rejection '+name,True)
 else:check('alias privacy rejection '+name,False)
source_inventory={str(p.relative_to(ROOT)):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in (ROOT/'scripts/qemu_diagnostic_export').iterdir() if p.is_file()}
(OUT/'source-inputs.json').write_text(json.dumps(run.inputs(),indent=2)+'\n')
(OUT/'report.json').write_text(json.dumps({'scope':'AST, shell syntax/lint, jq compilation, shell framing, synthetic gate/alias rejection only; no Cargo/CLI/QEMU/USB execution','checks':checks,'passed':all(c['passed'] for c in checks),'jq_programs':len(programs),'source':source_inventory},indent=2)+'\n')
print(json.dumps({'passed':True,'checks':len(checks),'jq_programs':len(programs)}))
