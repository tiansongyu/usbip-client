import ast, hashlib, importlib.util, json, pathlib, shlex, subprocess, sys
sys.dont_write_bytecode=True
root=pathlib.Path('/home/ubuntu/github/usbip-client')
spec=importlib.util.spec_from_file_location('auto',root/'scripts/qemu/run-autorestore-qemu.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
results=[]
def check(name,ok,detail=None):
 results.append({'name':name,'passed':bool(ok),'detail':detail})
 if not ok: raise AssertionError((name,detail))
source=root/'scripts/qemu/run-autorestore-qemu.py';ast.parse(source.read_text());check('python_ast',True)
texts=[('fragments',(root/'scripts/qemu/guest-autorestore-fragments.sh').read_text())]
for mode in a.SCENARIOS:
 generated,ledger=a.generate_init((root/'scripts/qemu/guest-journal-init.sh').read_bytes(),mode)
 p=pathlib.Path('/tmp/rustusb-autorestore-'+mode+'-init.sh');p.write_bytes(generated)
 r=subprocess.run(['sh','-n',str(p)],capture_output=True,text=True);check(mode+'_sh_n',r.returncode==0,r.stderr)
 check(mode+'_11_unique_anchors',len(ledger)==11 and all(x['matches']==1 for x in ledger))
 texts.append((mode,generated.decode()))
r=subprocess.run(['sh','-n',str(root/'scripts/qemu/guest-autorestore-fragments.sh')],capture_output=True,text=True);check('fragments_sh_n',r.returncode==0,r.stderr)
fixture=pathlib.Path('/tmp/autorestore-jq-compile-fixture.json');fixture.write_text('{}\n')
programs={}
for label,text in texts:
 text=text.replace('\\\n',' ')
 for line_number,line in enumerate(text.splitlines(),1):
  if 'jq ' not in line: continue
  tokens=shlex.split(line,comments=True)
  if 'jq' not in tokens: continue
  i=tokens.index('jq')+1;declarations=[]
  while i<len(tokens):
   tok=tokens[i]
   if tok in ['--arg','--argjson','--slurpfile','--rawfile']:
    key=tokens[i+1]
    if tok=='--arg': declarations += [tok,key,'fixture']
    elif tok=='--argjson': declarations += [tok,key,'0']
    else: declarations += [tok,key,str(fixture)]
    i+=3
   elif tok.startswith('-'): i+=1
   else: break
  program=tokens[i]; identity=(program,tuple(declarations))
  programs.setdefault(identity,[]).append(f'{label}:{line_number}')
for n,((program,declarations),origins) in enumerate(programs.items(),1):
 r=subprocess.run(['jq','-n',*declarations,'if false then ('+program+') else null end'],capture_output=True,text=True)
 check('jq_compile_'+str(n),r.returncode==0,{'origins':origins,'stderr':r.stderr})
# Execute real predicates with known positive/negative inputs; no device actor.
orig=[{'configuration':1,'interfaces':[{'number':0,'alternate':0,'driver':'usbhid'}]}]
p=pathlib.Path('/tmp/autorestore-jq-original.json');p.write_text(json.dumps(orig))
pred='.sysfs==$original[0] and .journal.schema_version==2 and .journal.records==[]'
for name,data,ok in [('primary_good',{'sysfs':orig,'journal':{'schema_version':2,'records':[]}},True),('primary_pending',{'sysfs':orig,'journal':{'schema_version':2,'records':[{}]}},False),('primary_null',{'sysfs':[{'configuration':1,'interfaces':[{'number':0,'alternate':0,'driver':None}]}],'journal':{'schema_version':2,'records':[]}},False),('primary_wrong_version',{'sysfs':orig,'journal':{'schema_version':1,'records':[]}},False)]:
 r=subprocess.run(['jq','-e','--slurpfile','original',str(p),pred],input=json.dumps(data),capture_output=True,text=True)
 check(name,(r.returncode==0)==ok,r.stderr)
check('tracing_plain_attempt',a.helper_operations('2026 INFO helper: text operation="physical_auto_restore" lease_id="aaa-bbb" phase="attempt"')==[{'lease_id':'aaa-bbb','phase':'attempt','raw':'2026 INFO helper: text operation="physical_auto_restore" lease_id="aaa-bbb" phase="attempt"'}])
check('tracing_ansi_retired',a.helper_operations('\x1b[32mINFO\x1b[0m x operation="physical_auto_restore" lease_id=aaa-bbb phase="retired"')[0]['phase']=='retired')
try: a.helper_operations('operation="physical_auto_restore" malformed')
except ValueError: check('malformed_product_event_rejected',True)
else: check('malformed_product_event_rejected',False)
report={'scope':'static syntax, actual jq compilation and small fixture predicates only; no Cargo/compile/QEMU/device execution','result':'PASS','checks':results,'jq_unique_program_count':len(programs),'source_sha256':{name:hashlib.sha256((root/'scripts/qemu'/name).read_bytes()).hexdigest() for name in a.OWN_SOURCES},'base_sha256':a.BASE_HASHES}
path=pathlib.Path('/tmp/rustusb-autorestore-static-checks-20261023.json');path.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'result':'PASS','checks':len(results),'jq_unique_programs':len(programs),'report':str(path)}))
