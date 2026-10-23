import ast,hashlib,importlib.util,json,pathlib,shlex,subprocess,sys
sys.dont_write_bytecode=True
root=pathlib.Path('/home/ubuntu/github/usbip-client')
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
n=load('n',root/'scripts/qemu/run-autorestore-negative-qemu.py');p=load('p',root/'scripts/qemu/run-autorestore-qemu.py')
checks=[]
def check(name,yes,detail=None):
 checks.append({'name':name,'passed':bool(yes),'detail':detail})
 if not yes:raise AssertionError((name,detail))
ast.parse((root/'scripts/qemu/run-autorestore-negative-qemu.py').read_text());check('python_ast',True)
texts=[('negative-fragment',(root/'scripts/qemu/guest-autorestore-negative-fragments.sh').read_text()),('positive-fragment',(root/'scripts/qemu/guest-autorestore-fragments.sh').read_text())]
for mode in n.SCENARIOS:
 data,ledger=n.generate_init(p,(root/'scripts/qemu/guest-journal-init.sh').read_bytes(),mode)
 target=pathlib.Path('/tmp/rustusb-negative-'+mode+'-init.sh');target.write_bytes(data)
 r=subprocess.run(['sh','-n',str(target)],capture_output=True,text=True);check(mode+'_sh_n',r.returncode==0,r.stderr)
 texts.append((mode,data.decode()))
r=subprocess.run(['sh','-n',str(root/'scripts/qemu/guest-autorestore-negative-fragments.sh')],capture_output=True,text=True);check('negative_fragment_sh_n',r.returncode==0,r.stderr)
fixture=pathlib.Path('/tmp/negative-jq-compile-fixture.json');fixture.write_text('{}\n')
programs={}
for label,text in texts:
 for lineno,line in enumerate(text.replace('\\\n',' ').splitlines(),1):
  if 'jq ' not in line:continue
  tokens=shlex.split(line,comments=True)
  if 'jq' not in tokens:continue
  i=tokens.index('jq')+1;args=[]
  while i<len(tokens):
   t=tokens[i]
   if t in ['--arg','--argjson','--slurpfile','--rawfile']:
    args.extend([t,tokens[i+1],'fixture' if t=='--arg' else '0' if t=='--argjson' else str(fixture)]);i+=3
   elif t.startswith('-'):i+=1
   else:break
  programs.setdefault((tokens[i],tuple(args)),[]).append(f'{label}:{lineno}')
for i,((program,args),origins) in enumerate(programs.items(),1):
 r=subprocess.run(['jq','-n',*args,'if false then ('+program+') else null end'],capture_output=True,text=True)
 check('jq_compile_'+str(i),r.returncode==0,{'origins':origins,'stderr':r.stderr})
legacy=n.validate_legacy(pathlib.Path('/tmp/rustusb-legacy-producer-build-933e720-20261023/target/release/rustusb-helper'),pathlib.Path('/tmp/rustusb-legacy-producer-build-933e720-20261023/manifest.json'))
check('legacy_real_manifest_109_gitblobs_ELF',len(legacy['files'])==109)
# Execute the actual jq event parser with real ANSI-delimited tracing fields.
expr=next(prog for prog,args in programs if 'capture(' in prog and 'physical_auto_restore' in prog)
tracing='\x1b[3moperation\x1b[0m\x1b[2m=\x1b[0m"physical_auto_restore" \x1b[3mlease_id\x1b[0m\x1b[2m=\x1b[0m"aaaa-bbbb" \x1b[3mphase\x1b[0m\x1b[2m=\x1b[0m"failed" reason="physical device is unsafe, already exported, claimed, or incompletely described"\n'
r=subprocess.run(['jq','-Rsc',expr],input=tracing,capture_output=True,text=True);data=json.loads(r.stdout)
check('actual_jq_ANSI_failed_parser',r.returncode==0 and len(data)==1 and data[0]['phase']=='failed' and data[0]['lease_id']=='aaaa-bbbb')
report={'result':'PASS','scope':'Static syntax/actual jq compilation and ANSI fixture plus read-only manifest verification. No Cargo, C compilation, QEMU or helper/device execution by this checker.','checks':checks,'jq_unique_programs':len(programs),'source_sha256':{f:hashlib.sha256((root/'scripts/qemu'/f).read_bytes()).hexdigest() for f in n.OWN}}
out=pathlib.Path('/tmp/rustusb-autorestore-negative-static-20261023.json');out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'result':'PASS','checks':len(checks),'jq_unique':len(programs),'report':str(out)}))
