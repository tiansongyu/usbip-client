#!/usr/bin/env python3
"""Read-only raw-artifact audit; imports no project/test runner verifier."""
import hashlib, ipaddress, json, pathlib, subprocess

BASE=pathlib.Path('/tmp/rustusb-secure-foreground-tests-46SBwP')
REPO=pathlib.Path('/home/ubuntu/github/usbip-client')
OUT=pathlib.Path('/tmp/rustusb-secure-foreground-third-runtime-independent-review-20261024.json')
CASES=('ready-int','ready-term','blocked-int','blocked-term','blocked-deadline')
SOURCES=('crates/rustusb-cli/src/secure_foreground_tests.rs','crates/rustusb-cli/src/foreground.rs','crates/rustusb-service/src/foreground.rs','crates/rustusb-service/src/jobs/native.rs','crates/rustusb-service/src/jobs/client.rs')
checks=[]
def check(name, condition, evidence, kind='direct_raw'):
    checks.append({'id':name,'result':'PASS' if condition else 'FAIL','kind':kind,'evidence':evidence})
def digest(path):
    data=path.read_bytes()
    return {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
def inventory():
    return {str(p.relative_to(BASE)):digest(p) for p in sorted(BASE.rglob('*')) if p.is_file()}
def src_inventory():
    return {p:digest(REPO/p) for p in SOURCES}
def state(snapshot, active):
    ds=snapshot.get('devices')
    if snapshot.get('schema_version') != 1 or not isinstance(ds,list) or len(ds)!=2:return False
    actual={(x.get('bus_id'),x.get('profile')) for x in ds}
    if actual!={('1-1','hid_keyboard'),('1-2','cdc_acm')}:return False
    return all(x.get('leased') is active and x.get('configuration')==int(active) and x.get('pending_urbs')==int(active) and x.get('hid_queued_reports')==0 and x.get('cdc_queued_bytes')==0 and x.get('counters',{}).get('sessions_started')==1 and x.get('counters',{}).get('control')==1 for x in ds)
def stdout_events(path):
    data=path.read_bytes()
    if not data.endswith(b'\n'):raise ValueError(f'{path}: unterminated captured stdout')
    found=[]
    for line in data.decode('utf-8').splitlines():
        if line in ('','running 1 test'):continue
        value=json.loads(line)
        if not isinstance(value,dict) or value.get('schema_version')!=1:raise ValueError('unexpected event schema')
        found.append(value)
    return found

raw_before=inventory();src_before=src_inventory()
report={'schema_version':1,'review_kind':'Independent raw parsing plus separately labelled source-linked runtime assertions','source_root':str(BASE),'head_at_review':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),'worktree_scope':'dirty candidate; five runtime cases terminal; separate third software gate reported complete by parent, not parsed here','raw_artifacts_before':raw_before,'sources_before':src_before,'cases':[],'checks':checks}
try:
    top=json.loads((BASE/'summary.json').read_text())
    check('top_exact_five_case_identity',top.get('result')=='PASS' and [c.get('case') for c in top.get('cases',[])]==list(CASES),'summary.json')
    check('artifact_inventory_exact',set(raw_before)=={'summary.json'}|{f'{c}/{f}' for c in CASES for f in ('summary.json','before.json','events-consumed.json','stdout-consumed.log','stderr.log')},'26 exact expected artifacts; no key/certificate/config evidence files')
    for name in CASES:
        folder=BASE/name
        summary=json.loads((folder/'summary.json').read_text())
        before=json.loads((folder/'before.json').read_text())
        saved=json.loads((folder/'events-consumed.json').read_text())
        observed=stdout_events(folder/'stdout-consumed.log')
        stopped=[e for e in observed if e.get('event')=='stopped']
        snapshots=[e for e in observed if e.get('event')=='snapshot']
        check(name+'.summary_index_identity',summary==next(c for c in top['cases'] if c['case']==name) and summary.get('result')=='PASS',f'{name}/summary.json vs top index')
        check(name+'.raw_stdout_equals_event_artifact',observed==saved,f'{name}/stdout-consumed.log and events-consumed.json parsed independently')
        ready=observed[0]
        address=ready['address'];host,port=address.rsplit(':',1)
        check(name+'.actual_ready_loopback',ready.get('event')=='ready' and ready.get('transport')=='mutual_tls' and ipaddress.ip_address(host).is_loopback and 0<int(port)<65536 and len(ready.get('devices',[]))==2,f'{name}/stdout-consumed.log ready')
        check(name+'.actual_nonempty_pending_snapshot',before in snapshots and before.get('id')=='before-0' and state(before.get('snapshot',{}),True),f'{name}/before.json + raw snapshot: exact profiles, sessions_started/control1, leased/config1/pending1 each, queues0')
        check(name+'.summary_tls_pending_in_and_closure',summary.get('mutual_tls') is True and summary.get('authenticated_imports')==2 and summary.get('pending_in_per_import')==1 and summary.get('data_connection_closure')==['transport_closed:UnexpectedEof']*2,f'{name}/summary.json; socket observations recorded by test parent','runtime_observation_record')
        deadline=name=='blocked-deadline';blocked=name.startswith('blocked-')
        elapsed=summary.get('elapsed_seconds',-1)
        check(name+'.actual_wait_and_deadline',summary.get('exit')==(1 if deadline else 0) and (4<=elapsed<=7 if deadline else 0<=elapsed<=8),f'{name}/summary.json actual wait status/elapsed','runtime_observation_record')
        if blocked:
            b=summary.get('blocked',{})
            check(name+'.full_pipe_and_writer_record',b.get('capacity')==4096 and b.get('synthetic_fill')==4096 and b.get('stdout_drained_after_fill') is False and str(b.get('blocked_writer_task','')).isdigit() and int(b['blocked_writer_task'])>0,f'{name}/summary.json; source enforces EAGAIN/FIONREAD/pipe_write and independent OFD flags before building record','source_linked_runtime_assertion')
            check(name+'.no_drain_no_cleanup_snapshot',len(observed)==2 and not stopped and summary.get('stopped_events')==[] and summary.get('backend_cleanup_snapshot_observed') is False,f'{name}/raw stdout stops before fill; Output.frozen source rejects subsequent reads; summary does not claim backend restoration')
        else:
            check(name+'.actual_stopped_resources',len(observed)==3 and len(stopped)==1 and state(stopped[0].get('snapshot',{}),False) and summary.get('stopped_events')==stopped and summary.get('backend_cleanup_snapshot_observed') is True,f'{name}/raw stopped: same2 profiles/sessions_started1/control1, no leases/pending/config/queues')
        stderr=(folder/'stderr.log').read_text()
        check(name+'.exact_stderr',stderr==('operation timed out: server stdout consumer is blocked\n' if deadline else ''),f'{name}/stderr.log')
        report['cases'].append({'case':name,'result':summary['result'],'actual_exit':summary['exit'],'elapsed_seconds':elapsed,'pending_profiles':2,'raw_event_order':[e['event'] for e in observed],'tls_closures':summary['data_connection_closure'],'actual_cleanup_snapshot_observed':not blocked,'blocked_writer_task':summary.get('blocked',{}).get('blocked_writer_task') if blocked else None})
    source=(REPO/SOURCES[0]).read_text()
    check('source.actual_adapter_and_test_only_marker','super::foreground::run(' in source and '#![cfg(all(test, target_os = "linux", feature = "rust-backend"))]' in source and 'LoadedJob::VirtualServer' in source,'secure_foreground_tests.rs exact reviewed source','source')
    check('source.retained_no_drain_and_real_wchan','!self.frozen' in source and 'wait.trim() == "pipe_write"' in source and 'synthetic_fill' in source and 'ioctl_fionread' in source and 'drop(writer)' in source,'test source uses independent OFD; does not set O_NONBLOCK on inherited writer','source')
    check('source.real_pending_read_assertion','Duration::from_millis(40), peer.read' in source and 'pending IN unexpectedly completed or disconnected' in source,'real TLS IN noncompletion asserted by parent; no independent per-URB trace retained','source')
    check('source.no_retry_and_no_secret_evidence','forced_cleanup_not_success' in source and 'return Err(format!' in source and '.mode(0o600)' in source and 'credentials.directory.path()' in source,'failure terminates matrix, temporary private DER not copied to retained directory','source')
    native=(REPO/SOURCES[3]).read_text()
    check('source.native_root_fixture_revision',src_before[SOURCES[3]]['sha256']=='6e193185bb10b2811bdc3f063c16df066157e9e5d02de9cb437b4d4d7e3aa7b5' and native.count('DeviceKey::native(&format!("usb-{}", "a".repeat(64)))?')==2,'root-only correction of two author test-key constructors; no new author source write','source')
except Exception as error:
    report['parser_error']={'type':type(error).__name__,'message':str(error)}
    check('parser_complete',False,'independent reviewer failure retained; no raw edits')
raw_after=inventory();src_after=src_inventory()
check('raw_bytes_unchanged',raw_before==raw_after,'all26 artifacts rehashed after parsing')
check('source_bytes_unchanged',src_before==src_after,'five source files rehashed; reviewer made no source writes')
report['raw_artifacts_after']=raw_after;report['sources_after']=src_after
report['counts']={'lifecycle_scenarios':5,'diagnostic_assertions':len(checks),'passed':sum(c['result']=='PASS' for c in checks),'failed':sum(c['result']=='FAIL' for c in checks),'usb_hardware_cases':0}
report['result']='PASS_WITH_SCOPE_LIMITATIONS' if report['counts']['failed']==0 else 'FAIL'
report['scope_limitations']=[
'Memory-DER cfg(test) child calls actual CLI adapter/shared virtual owner. It bypasses trusted root-owned configuration file loading, helper authorization, Native/USBFS kernel paths, and all real USB/hardware/QEMU.',
'Ready cleanup is directly visible in original stopped snapshots. Blocked cases have no stopped snapshot and independently retain that limitation: process wait/TLS socket closure alone is not a backend or physical restoration proof.',
'Full-pipe counts, writer TID, wait statuses/timings and TLS closures are parent runtime observation records backed by source assertions. No separate raw /proc wchan/fdinfo/FIONREAD dump, wire transcript, packet capture or per-child ELF/UID/caps inventory was retained; none is reconstructed retroactively.',
'Pending config1/lease1/pending1 per profile is directly present in original stdout; 40ms real IN noncompletion is enforced by test source and PASS outcome but no raw URB completion trace is retained.',
'Each process is launched as current_exe exact cfg(test) child in the reviewed source. Exact child ELF/source linkage belongs to root fullgate workflow evidence, not a per-child guard in this directory. Separate fullgate counts/results are not parsed or asserted by this runtime-only review.',
'Five lifecycle scenarios and reviewer diagnostic assertions are not USB case counts or additional unit-test executions. Timeout elapsed includes bounded pipe setup; these five observations are not a benchmark.',
'Private keys remain only in automatically dropped private temporary credentials directory; no secret PKI JSON or private key bytes/hashes are copied into this review.'
]
report['reviewer_actions']={'repository_writes':False,'cargo_build_cli_or_subprocess_test_runs':False,'qemu_usb_modules_network_mutation':False,'only_tmp_report_and_parser_written':True}
report['parser']={'path':__file__,**digest(pathlib.Path(__file__))}
OUT.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'path':str(OUT),**digest(OUT),'result':report['result'],'counts':report['counts']}))
