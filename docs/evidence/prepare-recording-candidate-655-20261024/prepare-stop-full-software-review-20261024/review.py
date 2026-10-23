#!/usr/bin/env python3
"""Independent read-only parsing of completed software/local-runtime originals.
No imports from project/harness; no Cargo/Git/QEMU or product invocation.
"""
import argparse,hashlib,json,re,struct
from pathlib import Path
SOFT=Path('/tmp/rustusb-prepare-stop-software-first-20261024')
LOCAL=Path('/tmp/rustusb-prepare-stop-local-runtime-20261024')
CLI='b7d610e002317d73c3603ebfb82866e0447f97dd5696236b81978b5998881554'
HELPER='b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5'
def sha(data):return hashlib.sha256(data).hexdigest()
def check(v,m):
 if not v:raise AssertionError(m)
def read(p):return json.loads(p.read_bytes())
def main(out):
 out.mkdir(parents=True,exist_ok=True)
 raw=[{'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for base in [SOFT,LOCAL] for p in sorted(base.rglob('*')) if p.is_file()]
 start=read(SOFT/'start.json');result=read(SOFT/'result.json');before=read(SOFT/'inputs-before.json');after=read(SOFT/'inputs-after.json')
 check(start['head']=='21a96fa5a0ef21293b431f180752feaf75e7d15e' and bool(start['status'].strip()),'actual dirty worktree attribution')
 check(start['command']==['bash','-x','scripts/check.sh'],'actual shell invocation')
 check(result['result']=='PASS' and all(c['exit_code']==0 for c in result['commands']),'actual command terminal exits')
 check(before==after and len(before)==167 and result['inputs_stable'] and result['selected_inputs']==167,'167 stable selected inputs')
 imap={x['path']:x for x in before};check(len(imap)==167,'unique source paths')
 snapshots=list((SOFT/'source').rglob('*'));snapshots=[p for p in snapshots if p.is_file()]
 check(len(snapshots)==167,'exact complete selected source snapshot')
 for item in before:
  p=SOFT/'source'/item['path'];b=p.read_bytes();check(len(b)==item['bytes'] and sha(b)==item['sha256'],'copied source '+item['path'])
 log=(SOFT/'full-gate.log').read_text();commands=re.findall(r'^\+ (cargo .*)$',log,re.M)
 expected=['cargo fmt --all -- --check','cargo clippy --workspace --all-targets --locked -- -D warnings','cargo test --workspace --locked','cargo build --workspace --release --locked','cargo doc --workspace --no-deps --locked','cargo clippy --workspace --all-targets --all-features --locked -- -D warnings','cargo test --workspace --all-features --locked','cargo build --workspace --all-features --release --locked','cargo doc --workspace --all-features --no-deps --locked','cargo test --manifest-path vendor/nusb/Cargo.toml --lib --features tokio --locked --target-dir target/nusb-tests']
 check(commands==expected,'10 actual Cargo xtrace commands')
 check(len(re.findall(r"^\+ RUSTDOCFLAGS='-D warnings'$",log,re.M))==2,'two rustdoc -Dwarnings environments')
 check("+ RUSTDOCFLAGS='-D warnings'\n+ cargo doc --workspace --no-deps --locked" in log and "+ RUSTDOCFLAGS='-D warnings'\n+ cargo doc --workspace --all-features --no-deps --locked" in log,'rustdoc env directly precedes both docs')
 check(not re.search(r'^error(?:\[|:)|^warning:',log,re.M),'full gate compiler/lint diagnostics')
 phase=None;target=None;executions=[];summaries=[]
 for line in log.splitlines():
  if line=='+ cargo test --workspace --locked':phase='default'
  elif line=='+ cargo test --workspace --all-features --locked':phase='all_features'
  elif line.startswith('+ cargo test --manifest-path'):phase='vendor'
  elif line.startswith('+ cargo '):phase=None
  m=re.match(r'\s+Running unittests (.*?) \((.*?)\)',line)
  if m:
   binary=Path(m[2]).name;crate=re.sub(r'-[0-9a-f]+$','',binary);target=crate+':'+m[1]
  m=re.match(r'\s+Doc-tests (\S+)',line)
  if m:target=m[1]+':doctest'
  m=re.match(r'^test (\S+) \.\.\. (ok|FAILED|ignored)$',line)
  if m:
   check(phase is not None and target is not None,'named execution missing phase/target')
   executions.append({'phase':phase,'target':target,'name':m[1],'result':m[2]})
  m=re.match(r'^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;',line)
  if m:
   check(phase is not None,'result missing phase')
   nums=list(map(int,m.groups()[1:]));check(m[1]=='ok' and nums[1:]==[0,0,0,0],'failed/ignored/measured/filtered final gate')
   summaries.append({'phase':phase,'target':target,'passed':nums[0],'failed':nums[1],'ignored':nums[2]})
 counts={phase:sum(x['passed'] for x in summaries if x['phase']==phase) for phase in ['default','all_features','vendor']}
 check(counts=={'default':274,'all_features':361,'vendor':20},'actual test totals')
 check(len(summaries)==39 and len(executions)==655 and all(x['result']=='ok' for x in executions),'39 summaries/655 actual named executions')
 check(len({(x['phase'],x['target'],x['name']) for x in executions})==655,'qualified execution uniqueness per feature run')
 qualified=len({(x['target'],x['name']) for x in executions})
 for required in ['received_identity_failure_survives_verified_close_in_session_finish','shutdown_poison_still_fans_out_and_returns_an_explicit_error','stopped_import_attempt_closes_actual_tls_before_late_ack_and_never_calls_helper','received_collection_stops_two_exports_during_resource_ack','held_available_ack_releases_actual_listener_and_lease_before_original_record']:
  check(any(x['name'].endswith(required) for x in executions),'final new test missing '+required)
 native_tree=(SOFT/'native-only-tree.log').read_text();pkgs=set(re.findall(r'(?:^|[─ ])([A-Za-z0-9_-]+) v[0-9]',native_tree,re.M))
 check({'rustusb-service','rustusb-transport','h2'}<=pkgs,'Native-only service+h2 absent')
 check(not({'rustusb-rust-backend','nusb','rusb'}&pkgs),'Native-only engine/USBFS dependency leaked')
 check('Finished `dev` profile' in (SOFT/'native-only-check.log').read_text(),'Native-only actual check')
 check('rustc 1.97.1' in (SOFT/'rustc.log').read_text() and 'cargo 1.97.1' in (SOFT/'cargo.log').read_text(),'actual tool versions')
 check('secure-server' in (SOFT/'cli-help.log').read_text() and 'agent' in (SOFT/'cli-help.log').read_text(),'actual CLI help receipt')
 artifacts=[]
 for expected,item in zip([CLI,HELPER],result['artifacts']):
  check(item['sha256']==expected,'recorded actual release digest')
  p=Path(item['path']);b=p.read_bytes();s=p.stat()
  check(b.startswith(b'\x7fELF') and sha(b)==expected and len(b)==item['bytes'],'current bytes match actual release receipt')
  check((s.st_dev,s.st_ino)==(item['dev'],item['inode']),'current ELF identity matches actual gate receipt')
  artifacts.append(item)
 local=read(LOCAL/'result.json');check(local['result']=='PASS' and all(c['exit_code']==0 for c in local['commands']),'local runtime terminal state')
 check(local['cli_sha256']==CLI and local['inputs_stable'] and local['binary_stable'] and local['selected_inputs']==167,'same 167/full-gate CLI local runtime guard')
 bp=read(LOCAL/'backpressure.log');check(bp['result']=='PASS' and bp['host_uid']==1000,'actual ordinary-user BP')
 check([x['mode'] for x in bp['checks']]==['sigint','deadline'],'two BP scenarios')
 for x in bp['checks']:
  check(x['stdout_capacity']==4096 and x['stdout_drained_after_fill'] is False and x['lease_socket_closed'] is True,'BP fullpipe/no-drain/socketEOF')
  check(x['exit']==(0 if x['mode']=='sigint' else 1),'BP actual exit semantics')
 sp=LOCAL/'signals';summary=read(sp/'summary.json');check(summary['result']=='PASS' and summary['binary_unchanged'] and summary['source_unchanged'],'signals success guards')
 check(summary['binary_before']['sha256']==CLI==summary['binary_after_sha256'],'signals binary both guards')
 check(summary['binary_before']['device']==artifacts[0]['dev'] and summary['binary_before']['inode']==artifacts[0]['inode'],'signal exact release inode')
 sa=read(sp/'source-before.json');sb=read(sp/'source-after.json');check(sa==sb and summary['source_before']==sa and summary['source_after']==sb,'signal raw source guard equality')
 signal_inputs={x['path']:x for x in sa['files']};check(len(signal_inputs)==len(sa['files']),'signal source unique')
 missing_signal_inputs=sorted(set(imap)-set(signal_inputs));check(missing_signal_inputs==['scripts/check.sh'],'exact signal inventory selection difference')
 for p,x in imap.items():
  if p in missing_signal_inputs:continue
  y=signal_inputs[p];check(y['sha256']==x['sha256'] and y['length']==x['bytes'],'signal/gate selected input '+p)
 for a in summary['artifacts']:
  b=(sp/a['path']).read_bytes();check(len(b)==a['length'] and sha(b)==a['sha256'],'signal raw artifact '+a['path'])
 signal_cases=[]
 for name in ['ready-sigint','ready-sigterm','blocked-sigint','blocked-sigterm']:
  p=sp/name;c=read(p/'result.json');before_case=read(p/'before.json');after_case=read(p/'after.json');protocol=read(p/'protocol.json');events=read(p/'stdout-events.json')
  check(c['before']==before_case and c['after']==after_case,'signal raw snapshots '+name)
  check(c['result']=='PASS' and c['final_returncode']==0 and not c['forced_cleanup'],'signal actual exit '+name)
  check(c['child']==c['sent']['identity'] and c['child']['pid']==c['sent']['pid'] and c['child']['uids']==[1000]*4,'signal exact process UID/starttime '+name)
  check(c['executed_binary']['sha256']==CLI and c['executed_binary']['inode']==artifacts[0]['inode'] and c['executed_binary']['device']==artifacts[0]['dev'],'signal child actual ELF '+name)
  check(c['sent']['signal']==(2 if name.endswith('sigint') else 15),'actual signal '+name)
  check(c['pipe']['capacity']==4096 and c['stdout_drained_after_fill'] is False,'signal stdout constraints '+name)
  check(after_case['returncode']==0 and after_case['wait_error'] is None and 0<=after_case['signal_exit_seconds']<8,'actual signal bounded exit '+name)
  check(set(after_case['data_sockets'])=={'1-1','1-2'} and all(x=={'eof':True,'unexpected_hex':''} for x in after_case['data_sockets'].values()),'actual two data EOF '+name)
  devices=before_case['snapshot']['devices'];check(len(devices)==2 and all(x['leased'] and x['configuration']==1 and x['pending_urbs']==1 for x in devices),'actual pre-stop two pending virtual URBs '+name)
  check(len(protocol)==8,'raw protocol records '+name)
  for bus,devid,ep,length in [('1-1',65537,1,8),('1-2',65538,2,64)]:
   rows=[x for x in protocol if x['bus_id']==bus];check([x['operation'] for x in rows]==['IMPORT','SUBMIT','RET_SUBMIT','SUBMIT'],'raw protocol order '+name+bus)
   check(bytes.fromhex(rows[0]['header_hex'])==bytes.fromhex('0111000300000000') and len(bytes.fromhex(rows[0]['device_hex']))==312,'actual success IMPORT '+name+bus)
   check(rows[0]['devid']==devid,'actual device ID '+name+bus)
   frame=bytes.fromhex(rows[3]['frame_hex']);check(len(frame)==48 and struct.unpack('>IIIIIIII',frame[:32])==(1,2,devid,1,ep,0,length,0),'actual pending IN frame '+name+bus)
   response=bytes.fromhex(rows[2]['frame_hex']);check(len(response)==48 and struct.unpack('>I',response[:4])[0]==3 and rows[2]['status']==0 and rows[2]['actual_length']==0,'actual config response '+name+bus)
  if name.startswith('ready'):
   check(after_case['backend_cleanup_observed'] is True and len(after_case['stopped_events'])==1,'ready cleanup snapshot '+name)
   check(all(not x['leased'] and x['pending_urbs']==0 and x['configuration']==0 for x in after_case['stopped_events'][0]['snapshot']['devices']),'ready actual resource cleanup '+name)
  else:
   pipe=c['pipe'];check(pipe['bytes_after_fill']==4096 and pipe['synthetic_padding_bytes']==4096 and pipe['independent_writer_eagain'] and pipe['blocked_writer']['wchan']=='pipe_write','actual blocked writer '+name)
   check(pipe['child_stdout_flags_before']==pipe['child_stdout_flags_after']==pipe['original_writer_flags_before']==pipe['original_writer_flags_after']==1,'child flags unchanged '+name)
   check(pipe['blocked_writer']['comm']=='virtual-test-st' and pipe['blocked_writer']['monotonic_ns']<=c['sent']['monotonic_ns'],'actual writer before signal '+name)
   check(after_case['backend_cleanup_observed'] is False and not after_case['stopped_events'] and after_case['stdout_unread_bytes']==4096 and not after_case['stdout_read_after_fill'],'blocked output scope no fake snapshot '+name)
  raw_events=[json.loads(line) for line in (p/'stdout-consumed.bin').read_bytes().splitlines() if line.strip()];check(raw_events==events,'actual consumed stdout matches events '+name)
  signal_cases.append({'case':name,'signal':c['sent']['signal'],'pid':c['child']['pid'],'elapsed_seconds':after_case['signal_exit_seconds'],'executed_binary':c['executed_binary'],'actual_eofs':2,'backend_snapshot':after_case['backend_cleanup_observed']})
 for x in raw:
  b=Path(x['path']).read_bytes();check(len(b)==x['bytes'] and sha(b)==x['sha256'],'original mutated during review '+x['path'])
 report={'status':'PASS read-only independent-method evidence review','actual_start':start,'test_counts':counts,'test_execution_total':655,'test_result_rows':39,'unique_qualified_target_and_test_names':qualified,'actual_cargo_steps':commands,'rustdoc_warnings_as_errors':2,'source_inputs':167,'source_copies_all_match':True,'input_before_after_equal':True,'artifacts':artifacts,'native_only_normal_dependencies':sorted(pkgs),'native_only_has_service_h2':True,'native_only_has_rustengine_nusb_rusb':False,'local_runtime':{'terminal':True,'signal_cases':signal_cases,'backpressure':bp,'source_inventory_entries':len(sa['files']),'selected_input_overlap_matches':166,'signal_inventory_omits':['scripts/check.sh'],'omitted_script_verified_in_full_gate_source_snapshot':True},'raw_originals':len(raw),'limits':['Actual software run was dirty21a96fa; no future commit or clean label inferred.','655 are executions across default/all-features/vendor; 39 result rows include zero-test/doc targets. Unique qualified names are not USB cases.','Software native/USBFS fixtures use controlled ordinary-user observations; no kernel/QEMU/hardware acceptance inferred.','Original local four signal/two backpressure tests exercise plaintext virtual foreground, not TLS/network blackhole or physical USB.','Four signal cases have actual child /proc executable SHA+inode and process identity receipts. Two old BP cases have wrapper same-binary guard and their existing JSON, not equivalent per-child raw artifacts.','Ready signal cases have stopped backend snapshots; blocked cases prove process exit/two sockets EOF with stdout unread, not a missing stopped snapshot.','Reviewer authored part of Rust/client/recording implementation: method cross-review, not independent implementation authorship.','This review does not invoke any original harness verifier or product executable.'],'execution':{'cargo':False,'qemu':False,'git':False,'repository_edits':False}}
 (out/'qualified-executions.json').write_text(json.dumps(executions,indent=2)+'\n');(out/'test-results.json').write_text(json.dumps(summaries,indent=2)+'\n');(out/'raw-originals-manifest.json').write_text(json.dumps({'files':raw,'unchanged_after_review':True},indent=2)+'\n');(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({'status':'PASS','execution_counts':counts,'result_rows':39,'qualified_unique':qualified,'raw_originals':len(raw),'report_sha256':sha((out/'report.json').read_bytes())}))
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,default=Path('/tmp/rustusb-prepare-stop-full-software-review-20261024'));main(ap.parse_args().output)
