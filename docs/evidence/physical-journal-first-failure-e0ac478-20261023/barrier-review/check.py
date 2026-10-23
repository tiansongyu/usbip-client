from pathlib import Path
import ast, difflib, hashlib, json, subprocess, tempfile, types

repo=Path('/home/ubuntu/github/usbip-client');base=Path('/tmp/rustusb-journal-barrier-review-20261023')
host=repo/'scripts/qemu/run-journal-qemu.py';guest=repo/'scripts/qemu/guest-journal-init.sh'
oldh=(base/'run-journal-qemu.py.before').read_text();oldg=(base/'guest-journal-init.sh.before').read_text()
h=host.read_text();g=guest.read_text();checks=[]
def check(name,ok):checks.append({'name':name,'result':'PASS' if ok else 'FAIL'})
for name,source in [('host',h),('physical',(repo/'scripts/qemu/run-physical-qemu.py').read_text()),('native',(repo/'scripts/qemu/run-native-qemu.py').read_text())]:
 ast.parse(source);check(name+'_ast',True)
for name in ['sh','bash']:
 result=subprocess.run([name,'-n',str(guest)],capture_output=True);check(name+'_syntax',result.returncode==0)
check('send_control_deadlines_unchanged',g[g.index('send_control() {'):g.index('start_helper() {')]==oldg[oldg.index('send_control() {'):oldg.index('start_helper() {')])
def without_immediate_diagnostic(source):
 return '\n'.join(line for line in source.splitlines() if "printf 'RUSTUSB_JOURNAL_WAIT '" not in line)
check('20_second_kill_observation_unchanged',without_immediate_diagnostic(g[g.index('    until_tick=$(( $(ticks) + 2000 ))',g.index('    KILL_PID=')):g.index('    dump_epoch\n    record killed-post')])==without_immediate_diagnostic(oldg[oldg.index('    until_tick=$(( $(ticks) + 2000 ))',oldg.index('    KILL_PID=')):oldg.index('    dump_epoch\n    record killed-post')]))
check('server_save_before_barrier_before_restart',g.index('    record killed-post') < g.index('    send_control 192.0.2.2 cycle-2-server-observed') < g.index('    receive_control cycle-2-observed') < g.index('    refused_server denied-before-helper-restart'))
client=g[g.index('    for cycle in 1 2 3; do'):]
check('client_observation_before_barrier_before_detach',client.index('>> /tmp/client-outcomes.jsonl') < client.index('if [ "$cycle" = 2 ]; then receive_control cycle-2-server-observed; fi') < client.index('unpriv --json detach') < client.index('send_control 192.0.2.1 "cycle-$cycle-observed"'))
check('only_cycle2_has_new_barrier',g.count('send_control 192.0.2.2 cycle-2-server-observed')==1 and g.count('if [ "$cycle" = 2 ]; then receive_control cycle-2-server-observed; fi')==1)
for text in ['time.monotonic() - failure_started >= 30','deadline = time.monotonic() + args.timeout','process.wait(timeout=10)','default=300']:
 check('host_deadline_unchanged_'+text,text in h and text in oldh)
def fn(source,name): return next(node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name==name)
expanded=oldh
for before,after in {
 '"reconciled-ready", "reconciled-closed"}':'"reconciled-ready", "reconciled-pending", "reconciled-closed"}',
 '        if epoch != "normal":':'        if epoch == "reopened":',
 '    for epoch in ["normal", "killed"]:':'    for epoch in ["normal", "killed", "reconciled"]:',
 'check("client_two_actual_io_and_disconnect_records", [row["cycle"] for row in clients] == [1, 2])':'check("client_three_actual_io_and_disconnect_records", [row["cycle"] for row in clients] == [1, 2, 3])',
 'check("two_cycles_both_devices_busy", busy == [{"event": "physical_probe_case", "case": "both_real_devices_exclusive_busy", "result": "PASS"}] * 2)':'check("three_cycles_both_devices_busy", busy == [{"event": "physical_probe_case", "case": "both_real_devices_exclusive_busy", "result": "PASS"}] * 3)',
}.items():
 assert expanded.count(before)==1
 expanded=expanded.replace(before,after)
check('original_verifier_preserved_with_only_approved_third_cycle_expansion',ast.dump(fn(h,'verify'))==ast.dump(fn(expanded,'verify')))
check('guest_admin_refusal_unchanged',g[g.index('admin_refusal() {'):g.index('if [ "$ROLE" = server ]; then\n    STAGE=select-simulated-devices')]==oldg[oldg.index('admin_refusal() {'):oldg.index('if [ "$ROLE" = server ]; then\n    STAGE=select-simulated-devices')])
# Execute the actual first-failure if statement, with deterministic in-memory
# logs and a private artifact directory; no process, socket or QEMU is started.
condition=next(node for node in ast.walk(fn(h,'main')) if isinstance(node,ast.If) and ast.unparse(node.test)=='failed and failure_started is None')
compiled=compile(ast.fix_missing_locations(ast.Module(body=[condition],type_ignores=[])),str(host),'exec')
with tempfile.TemporaryDirectory(dir=base,prefix='marker-fixture-') as temporary:
 scope={'failed':True,'failure_started':None,'time':types.SimpleNamespace(monotonic=lambda:12.0),'summary':{},'ROLES':['server','client'],
        'program':{'server':'ready\n','client':'control delivery failed: cycle-2-observed\nRUSTUSB_JOURNAL_RESULT: FAIL role=client stage=client-cycle-2-disconnect exit=1\n'},
        'kernel':{'server':'BUG: example fixture\n','client':''},'physical':types.SimpleNamespace(INTEGRITY_FAILURES=['Kernel panic','BUG:','was already given back','seqnum max']),
        'evidence':Path(temporary),'json':json}
 exec(compiled,scope)
 expected=[{'role':'server','channel':'kernel','line':'BUG: example fixture'},{'role':'client','channel':'program','line':'RUSTUSB_JOURNAL_RESULT: FAIL role=client stage=client-cycle-2-disconnect exit=1'}]
 check('first_failure_preserves_role_channel_line',scope['summary']['observed_failure_markers']==expected)
 check('first_failure_persisted_immediately',json.loads((Path(temporary)/'first-observed-failure.json').read_text())==expected)
 scope['program']['client']='RUSTUSB_JOURNAL_RESULT: FAIL later\n';scope['summary']['error']='later process.wait timeout'
 exec(compiled,scope)
 check('later_failure_does_not_replace_first',scope['summary']['observed_failure_markers']==expected and json.loads((Path(temporary)/'first-observed-failure.json').read_text())==expected)
 check('later_terminal_error_can_coexist',scope['summary']['error']=='later process.wait timeout')
# Deterministic handshake schedule model of the extracted order: connect refusal
# is immediate, send retry budget 10 s, listen budget 60 s. It is not QEMU evidence.
def model(barrier,client_observation,cleanup):
 server=[('sleep',20),*([('send','server-observed')] if barrier else []),('recv','client-observed')]
 client=[('sleep',client_observation),*([('recv','server-observed')] if barrier else []),('sleep',cleanup),('send','client-observed')]
 tasks=[{'ops':server,'index':0,'start':0.0},{'ops':client,'index':0,'start':0.0}]
 for step in range(1501):
  now=step/10
  for task in tasks:
   while task['index']<len(task['ops']):
    op,arg=task['ops'][task['index']]
    if op!='sleep' or now-task['start']+1e-9<arg:break
    task['index']+=1;task['start']=now
  if all(task['index']==len(task['ops']) for task in tasks):return True
  if all(task['index']<len(task['ops']) for task in tasks):
   left,right=[task['ops'][task['index']] for task in tasks]
   if {left[0],right[0]}=={'send','recv'} and left[1]==right[1]:
    for task in tasks:task['index']+=1;task['start']=now
    continue
  for task in tasks:
   if task['index']<len(task['ops']):
    op,_=task['ops'][task['index']]
    if op in ['send','recv'] and now-task['start']>=({'send':10,'recv':60}[op]):return False
 return False
check('old_fast_client_schedule_times_out_before_server_listens',not model(False,.2,.2))
for observation,cleanup in [(.2,.2),(.2,30),(19.5,.2),(20.9,.2)]:check(f'new_barrier_schedule_{observation}_{cleanup}',model(True,observation,cleanup))
# Each state/wait record reaches the evidence UART immediately, independent of
# the eventual aggregate block, which may never be reached on a guest failure.
lines=g.splitlines()
record_sites=[index for index,line in enumerate(lines) if line.rstrip().endswith('>> /tmp/journal-observations.jsonl')]
wait_sites=[index for index,line in enumerate(lines) if line.rstrip().endswith('>> /tmp/waits.jsonl')]
check('each_journal_record_immediately_printed',len(record_sites)==1 and all(lines[index+1].strip()=="printf 'RUSTUSB_JOURNAL_RECORD '; tail -n 1 /tmp/journal-observations.jsonl" for index in record_sites))
check('all_four_wait_sites_immediately_printed',len(wait_sites)==4 and all(lines[index+1].strip()=="printf 'RUSTUSB_JOURNAL_WAIT '; tail -n 1 /tmp/waits.jsonl" for index in wait_sites))
reconciled=g[g.index('    STAGE=authorized-after-reconcile'):g.index("    echo 'RUSTUSB_JOURNAL_OBSERVATIONS_BEGIN'")]
ordered=['start_ready reconciled','send_control 192.0.2.2 cycle-3-ready','receive_control cycle-3-io','actual_pending','normal_stop','record reconciled-closed','records_count 0','send_control 192.0.2.2 cycle-3-stopped','receive_control cycle-3-observed']
check('third_cycle_reimports_and_observes_pending_before_stop_and_barrier',all(reconciled.index(left)<reconciled.index(right) for left,right in zip(ordered,ordered[1:])))
check('only_cycle2_uses_kill_other_cycles_use_normal_stop','if [ "$cycle" = 2 ]; then receive_control cycle-2-killed; else receive_control "cycle-$cycle-stopped"; fi' in client)
check('host_requires_all_six_actual_injections','injections != {"key-1", "serial-1", "key-2", "serial-2", "key-3", "serial-3"}' in h and 'for cycle in [1, 2, 3]:' in h)
tree=ast.parse(h);payload_nodes=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in ['rx','tx'] or isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='TX' for target in node.targets)]
payload={};exec(compile(ast.fix_missing_locations(ast.Module(body=payload_nodes,type_ignores=[])),str(host),'exec'),payload)
check('host_expected_sink_is_exact_three_35_byte_payloads',len(payload['TX'])==105 and payload['TX']==b''.join(f'RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n'.encode() for cycle in [1,2,3]))
check('three_client_receipts_io_disconnect_and_busy_required','[row["cycle"] for row in clients] == [1, 2, 3]' in h and 'for epoch in ["normal", "killed", "reconciled"]:' in h and '"result": "PASS"}] * 3)' in h)
check('no_import_assertion_kept_only_for_explicit_reopened_epoch','if epoch == "reopened":' in h and 'if epoch != "normal":' not in h)
files=[]
for path in [host,guest]:
 data=path.read_bytes();files.append({'path':str(path.relative_to(repo)),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mode':oct(path.stat().st_mode&0o777)})
(base/'change.patch').write_text(''.join(difflib.unified_diff(oldg.splitlines(True),g.splitlines(True),fromfile='a/scripts/qemu/guest-journal-init.sh',tofile='b/scripts/qemu/guest-journal-init.sh'))+''.join(difflib.unified_diff(oldh.splitlines(True),h.splitlines(True),fromfile='a/scripts/qemu/run-journal-qemu.py',tofile='b/scripts/qemu/run-journal-qemu.py')))
report={'scope':'Fixture barrier/first-failure evidence repair and administrator-requested post-reconciliation actual third I/O cycle, static/extracted checks only. No Cargo build/QEMU/USB operations.',
 'root_run_preserved':'/tmp/rustusb-journal-e0ac478-20261023; original run FAIL unchanged',
 'cause':'Client observes spontaneous disconnect promptly and exhausts immediate connect-refusal retries (~10 s) while server is still collecting its 20 s post-KILL observation.',
 'change':'After persisting its post-KILL primary state, server sends server-observed; client waits after saving its own primary observation and before detach, then replies observed. Existing deadlines and prior normal/KILL assertions unchanged; verifier additionally requires an actual third HID/FTDI cycle after administrator reconciliation. Host persists first failure marker rows immediately and retains them with later terminal errors. Every journal/sysfs and wait row is also emitted immediately to the evidence UART, without depending on final aggregate blocks.',
 'checks':checks,'passed':sum(row['result']=='PASS' for row in checks),'total':len(checks),'files':files,
 'limits':['Logical schedule model is derived validation, not a QEMU result.','The old full run remains FAIL; no driver autorecovery claim.']}
(base/'review.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'result':'PASS' if report['passed']==report['total'] else 'FAIL','passed':report['passed'],'total':report['total'],'files':files,'report':str(base/'review.json')},indent=2))
raise SystemExit(0 if report['passed']==report['total'] else 1)
