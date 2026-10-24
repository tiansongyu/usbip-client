#!/usr/bin/env python3
"""Read only preserved evidence; no checker/runner imports or subprocesses."""
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import struct

p = argparse.ArgumentParser()
p.add_argument('--root', type=Path, default=Path('/home/ubuntu/github/usbip-client'))
p.add_argument('--gate', type=Path, default=Path('/tmp/rustusb-prepare-stop-software-ab-first-20261024'))
p.add_argument('--local', type=Path, default=Path('/tmp/rustusb-prepare-stop-local-runtime-ab-20261024'))
p.add_argument('--service', type=Path, default=Path('/tmp/rustusb-prepare-stop-service-ab-first-20261024'))
p.add_argument('--prior', type=Path, default=Path('/tmp/rustusb-prepare-stop-software-first-20261024'))
p.add_argument('--out', type=Path, default=Path('/tmp/rustusb-prepare-stop-software-ab-review-20261024'))
a = p.parse_args()
a.out.mkdir(exist_ok=True)
checks = []
def check(name, value, evidence=None):
    checks.append({'name': name, 'passed': bool(value), 'evidence': evidence})
def sha(b): return hashlib.sha256(b).hexdigest()
def meta(path):
    b=path.read_bytes()
    return {'path': str(path), 'bytes': len(b), 'sha256': sha(b)}
def load(path): return json.loads(path.read_text())
def dump(name, value):
    path=a.out/name
    path.write_text(json.dumps(value, indent=2)+'\n')
    return meta(path)

raw_paths = sorted({p for d in (a.gate,a.local,a.service) for p in d.rglob('*') if p.is_file()} | {a.prior/'full-gate.log', a.prior/'inputs-before.json'})
raw_before = {str(p):meta(p) for p in raw_paths}
start=load(a.gate/'start.json'); result=load(a.gate/'result.json')
inputs=load(a.gate/'inputs-before.json'); after=load(a.gate/'inputs-after.json')
check('actual gate terminal exit0', result['result']=='PASS' and result['commands'][0]['exit_code']==0)
check('actual start records dirty candidate, not clean/future commit', bool(start['status']) and 'dirty' in start['scope'], {'head':start['head'],'status':start['status'],'scope':start['scope']})
check('167 selected before/after manifests identical', len(inputs)==167 and inputs==after)
check('all selected paths unique and relative', len({r['path'] for r in inputs})==len(inputs) and all(not Path(r['path']).is_absolute() and '..' not in Path(r['path']).parts for r in inputs))
input_map=[]
for r in inputs:
    copied=a.gate/'source'/r['path']; current=a.root/r['path']
    cp=meta(copied); cu=meta(current)
    want=(r['bytes'],r['sha256'])
    input_map.append({'path':r['path'],'bytes':r['bytes'],'sha256':r['sha256'], 'copy_match':(cp['bytes'],cp['sha256'])==want,'current_match':(cu['bytes'],cu['sha256'])==want})
check('all167 preserved source copies match before manifest', all(r['copy_match'] for r in input_map))
check('all167 current selected inputs match preserved source', all(r['current_match'] for r in input_map))
check('snapshot has exactly selected167 source paths', sorted(str(p.relative_to(a.gate/'source')) for p in (a.gate/'source').rglob('*') if p.is_file())==sorted(r['path'] for r in inputs))
check('new recording and Native private fixture included', {'crates/rustusb-service/src/jobs/recording.rs','crates/rustusb-service/src/jobs/native_prepare_tests.rs'} <= {r['path'] for r in inputs})

EXPECTED=[
'cargo fmt --all -- --check',
'cargo clippy --workspace --all-targets --locked -- -D warnings',
'cargo test --workspace --locked',
'cargo build --workspace --release --locked',
'cargo doc --workspace --no-deps --locked',
'cargo clippy --workspace --all-targets --all-features --locked -- -D warnings',
'cargo test --workspace --all-features --locked',
'cargo build --workspace --all-features --release --locked',
'cargo doc --workspace --all-features --no-deps --locked',
'cargo test --manifest-path vendor/nusb/Cargo.toml --lib --features tokio --locked --target-dir target/nusb-tests']

def parse_gate(log):
    phase=None; current=None; group=None; rows=[]; tests=[]; commands=[]; began=None; group_tests=[]; declared=None
    for number,line in enumerate(log.splitlines(),1):
        if line.startswith('+ cargo '):
            command=line[2:]; commands.append({'line':number,'command':command})
            if command=='cargo test --workspace --locked': phase='default'
            elif command=='cargo test --workspace --all-features --locked': phase='all-features'
            elif command.startswith('cargo test --manifest-path'): phase='vendor'
        m=re.search(r'Running (unittests \S+|tests/\S+) \((\S+)\)',line)
        if m:
            current=re.sub(r'-[0-9a-f]{16}$','',Path(m[2]).name)
            group={'kind':'integration' if m[1].startswith('tests/') else 'unit','target':current,'executable':m[2],'announcement_line':number}
            began=None; group_tests=[]; declared=None
        m=re.search(r'Doc-tests (\S+)',line)
        if m:
            current=m[1];group={'kind':'doc','target':current,'announcement_line':number};began=None;group_tests=[];declared=None
        m=re.fullmatch(r'running (\d+) tests?',line)
        if m: declared=int(m[1]);began=number
        m=re.fullmatch(r'test (.+) \.\.\. (ok|FAILED|ignored)',line)
        if m:
            t={'phase':phase,'target':current,'name':m[1],'status':m[2],'line':number}
            tests.append(t);group_tests.append(t)
        m=re.fullmatch(r'test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out; finished in (.+)',line)
        if m:
            if group is None: raise ValueError('result without announced target')
            row=dict(group,phase=phase,line=number,declared=declared,observed_names=len(group_tests),result=m[1],passed=int(m[2]),failed=int(m[3]),ignored=int(m[4]),measured=int(m[5]),filtered=int(m[6]),elapsed=m[7]);rows.append(row)
    return commands, rows, tests

log=(a.gate/'full-gate.log').read_text()
commands,rows,tests=parse_gate(log)
check('ten exact xtrace Cargo commands in expected order', [x['command'] for x in commands]==EXPECTED,commands)
check('both rustdoc commands actually carry deny warnings', log.count("+ RUSTDOCFLAGS='-D warnings'\n+ cargo doc ")==2)
# The set-e script and terminal exit0 corroborate empty-output fmt success;
# all subsequent stages have actual Finished output/result rows.
for i,c in enumerate(commands):
    end=commands[i+1]['line']-1 if i+1<len(commands) else len(log.splitlines())
    text='\n'.join(log.splitlines()[c['line']:end])
    completed = True if i==0 else (('test result: ok.' in text) if c['command'].startswith('cargo test ') else ('Finished `' in text))
    check('xtrace step %02d actual completion'%(i+1),completed,{'command':c['command'],'trace_line':c['line'],'section_last_line':end})
check('39 result rows all ok with0fail/ignored/measured/filtered', len(rows)==39 and all(x['result']=='ok' and not any(x[k] for k in ('failed','ignored','measured','filtered')) for x in rows))
check('every per-target declared count equals individual successful names and result', all(x['declared']==x['observed_names']==x['passed'] for x in rows))
counts={phase:sum(x['passed'] for x in rows if x['phase']==phase) for phase in ('default','all-features','vendor')}
check('default280 allfeatures367 vendor20 =667 actual executions', counts=={'default':280,'all-features':367,'vendor':20} and len(tests)==667,counts)
check('every individually printed test successful', all(x['status']=='ok' for x in tests))
qualified={(x['target'],x['name']) for x in tests}
check('387 distinct target-qualified names, repetition explicit',len(qualified)==387,{'distinct_names':len(qualified),'executions':len(tests),'repeated_executions':len(tests)-len(qualified)})
check('default/allfeatures service62/71 actual result counts',[(x['phase'],x['passed']) for x in rows if x['target']=='rustusb_service' and x['kind']=='unit']==[('default',62),('all-features',71)])
required_native=[
'known_identity_failure_is_not_erased_by_simultaneous_stop',
'local_resource_stop_keeps_the_safe_sibling_owned_and_available',
'local_available_stop_keeps_the_safe_sibling_owned_and_available',
'local_stop_before_helper_is_a_local_skip_without_new_attempt',
'reserved_unsent_local_stop_retains_uncertainty_without_stopping_sibling',
'native_tls_local_stop_refuses_import_keeps_busy_and_empty_catalog_listener']
for name in required_native:
    found=[(x['phase'],x['status']) for x in tests if x['name']=='jobs::native::prepare_tests::'+name]
    check('new Native regression '+name,found==[('default','ok'),('all-features','ok')],found)
old_commands,old_rows,old_tests=parse_gate((a.prior/'full-gate.log').read_text())
old_qualified={(x['target'],x['name']) for x in old_tests}
old_inputs={x['path']:x for x in load(a.prior/'inputs-before.json')}
delta={'prior_executions':sum(x['passed'] for x in old_rows),'prior_source_scope':'separate historical dirty655 candidate; not relabelled', 'new_qualified':[{'target':x[0],'name':x[1]} for x in sorted(qualified-old_qualified)],'removed_qualified':[{'target':x[0],'name':x[1]} for x in sorted(old_qualified-qualified)],'selected_input_deltas':[x['path'] for x in inputs if old_inputs.get(x['path'])!=x]}
check('historical655 remains separate; exactly6 new names and no removals',delta['prior_executions']==655 and len(delta['new_qualified'])==6 and not delta['removed_qualified'],delta)

# Additional independently completed service gate, not added to667 again.
service_result=load(a.service/'result.json')
check('separate service five commands exited0', service_result['result']=='PASS' and len(service_result['commands'])==5 and all(x['exit_code']==0 for x in service_result['commands']))
for fname,expected in [('01.log',62),('02.log',71)]:
    text=(a.service/fname).read_text();matches=re.findall(r'test result: ok\. (\d+) passed; 0 failed; 0 ignored; 0 measured; 0 filtered out;',text)
    check('separate service '+fname+' actual test count',matches==[str(expected),'0'])
for fname in ('03.log','04.log'):
    check('separate service '+fname+' Clippy completion','Finished `dev` profile' in (a.service/fname).read_text())
check('separate service all167 same candidate',load(a.service/'inputs-before.json')==inputs==load(a.service/'inputs-after.json'))

native_check=(a.gate/'native-only-check.log').read_text();tree=(a.gate/'native-only-tree.log').read_text()
check('Native-only exact check command completed', result['commands'][1]['command']==['cargo','check','-p','rustusb-cli','--no-default-features','--features','secure-transport','--locked'] and result['commands'][1]['exit_code']==0 and 'Finished `dev` profile' in native_check)
check('Native-only normal dependency tree excludes optional Rust engine/nusb/libusb',result['commands'][2]['exit_code']==0 and not re.search(r'\b(?:rustusb-rust-backend|nusb|rusb|libusb1-sys) v',tree))
check('Native-only tree actually includes service/native/transport',all(k+' v' in tree for k in ('rustusb-service','rustusb-native','rustusb-transport')))

artifacts=[]
for r in result['artifacts']:
    path=Path(r['path']);b=path.read_bytes();st=path.stat(); machine=struct.unpack_from('<H',b,18)[0] if b[:4]==b'\x7fELF' else None
    info=dict(r,current_sha256=sha(b),current_bytes=len(b),current_device=st.st_dev,current_inode=st.st_ino,elf_class=b[4] if machine is not None else None,elf_machine=machine)
    artifacts.append(info)
    check('actual release '+path.name+' bytes and identity', sha(b)==r['sha256'] and len(b)==r['bytes'] and st.st_dev==r['dev'] and st.st_ino==r['inode'] and b[:5]==b'\x7fELF\x02' and machine==62)
check('actual release CLI expected candidate fingerprint',artifacts[0]['sha256']=='72adb291645fdcf3cb41c1ff9e9e781fe9eed344730a4444407f983c9b63e5f2')
tools={'rustc':(a.gate/'rustc.log').read_text().strip(),'cargo':(a.gate/'cargo.log').read_text().strip()}
check('recorded tools actual host/version', 'rustc 1.97.1' in tools['rustc'] and 'x86_64-unknown-linux-gnu' in tools['rustc'] and 'cargo 1.97.1' in tools['cargo'])

# Ordinary-user plaintext local cases, deriving protocol/pipe/cleanup fields,
# not accepting the harness assertion list as a replacement for this review.
local_result=load(a.local/'result.json'); signal_base=a.local/'signals'; signal_summary=load(signal_base/'summary.json')
check('local wrapper terminal actual both commands0',local_result['result']=='PASS' and len(local_result['commands'])==2 and all(x['exit_code']==0 for x in local_result['commands']))
check('local selected167 match exact software candidate',load(a.local/'inputs-before.json')==inputs==load(a.local/'inputs-after.json'))
check('signals source before/after unchanged', signal_summary['source_before']==signal_summary['source_after'] and signal_summary['source_unchanged'])
signal_sources={x['path']:x for x in signal_summary['source_before']['files']}
signal_selected=[x for x in inputs if x['path']!='scripts/check.sh']
check('signals own filtered source index matches selected166 Rust/TOML/lock paths',len(signal_selected)==166 and all(x['path'] in signal_sources and signal_sources[x['path']]['length']==x['bytes'] and signal_sources[x['path']]['sha256']==x['sha256'] for x in signal_selected),{'full_index_count':len(signal_sources),'selected_in_signal_index':len(signal_selected),'not_in_signal_index':'scripts/check.sh; independently covered by wrapper167 snapshot'})
fingerprint=hashlib.sha256()
for x in signal_summary['source_before']['files']:
    fingerprint.update(os.fsencode(x['path'])+b'\0'+str(x['length']).encode()+b'\0'+x['sha256'].encode()+b'\n')
check('signals9712 source index fingerprint independently recomputed',fingerprint.hexdigest()==signal_summary['source_before']['source_sha256'] and signal_summary['source_before']==load(signal_base/'source-before.json') and signal_summary['source_after']==load(signal_base/'source-after.json'))
for artifact in signal_summary['artifacts']:
    found=meta(signal_base/artifact['path']);check('signals summary indexed raw '+artifact['path'],found['bytes']==artifact['length'] and found['sha256']==artifact['sha256'])
check('signals actual host ordinary UID1000/kernel6.8',signal_summary['host_uid']==1000 and signal_summary['host'][2]=='6.8.0-106-generic')
check('signals external build accurately not inferred',signal_summary['build']['skipped'] and signal_summary['build']['features']=='external prebuilt; not inferred')
check('signals all4 cases before/after ELF matches actual allfeatures release',len(signal_summary['cases'])==4 and signal_summary['binary_before']['sha256']==signal_summary['binary_after_sha256']==artifacts[0]['sha256'] and signal_summary['binary_unchanged'])
local_cases=[]
for mode in ('ready','blocked'):
    for sig in ('sigint','sigterm'):
        prefix=mode+'-'+sig;d=signal_base/prefix
        r=load(d/'result.json');before=load(d/'before.json');aft=load(d/'after.json');protocol=load(d/'protocol.json');events=load(d/'stdout-events.json');raw=(d/'stdout-consumed.bin').read_bytes()
        check(prefix+' exact raw JSON copies',r['before']==before and r['after']==aft and [json.loads(x) for x in raw.splitlines()]==events)
        check(prefix+' signal only identity-matched owned ordinary child',r['child']==r['sent']['identity'] and r['child']['pid']==r['sent']['pid'] and r['child']['uids']==[1000]*4 and r['child']['starttime']>0 and r['sent']['signal']==(2 if sig=='sigint' else 15))
        check(prefix+' child actual ELF equals release identity',r['executed_binary']['sha256']==artifacts[0]['sha256'] and r['executed_binary']['inode']==artifacts[0]['inode'] and r['executed_binary']['device']==artifacts[0]['dev'])
        devices=before['snapshot']['devices']
        check(prefix+' nonvacuous configured two leased pending profiles',len(devices)==2 and {x['bus_id'] for x in devices}=={'1-1','1-2'} and {x['profile'] for x in devices}=={'hid_keyboard','cdc_acm'} and all(x['leased'] and x['configuration']==1 and x['pending_urbs']==1 and x['counters']['sessions_started']==1 for x in devices))
        for bus,endpoint,size in [('1-1',1,8),('1-2',2,64)]:
            ops=[x for x in protocol if x['bus_id']==bus]
            check(prefix+' '+bus+' actual IMPORT header success',len(ops)==4 and ops[0]['operation']=='IMPORT' and bytes.fromhex(ops[0]['header_hex'])==bytes.fromhex('0111000300000000') and len(bytes.fromhex(ops[0]['device_hex']))==312)
            config=bytes.fromhex(ops[1]['frame_hex']);response=bytes.fromhex(ops[2]['frame_hex']);pending=bytes.fromhex(ops[3]['frame_hex'])
            check(prefix+' '+bus+' real SET_CONFIG1/IN wire frames',len(config)==len(response)==len(pending)==48 and struct.unpack('>5I',config[:20])==(1,1,ops[0]['devid'],0,0) and config[40:]==bytes.fromhex('0009010000000000') and struct.unpack('>I',response[:4])[0]==3 and struct.unpack('>iI',response[20:28])==(0,0) and struct.unpack('>7I',pending[:28])==(1,2,ops[0]['devid'],1,endpoint,0,size))
        check(prefix+' cooperative exit0 within original8s, no force',r['final_returncode']==aft['returncode']==0 and not r['forced_cleanup'] and aft['wait_error'] is None and 0<=aft['signal_exit_seconds']<signal_summary['limits_seconds']['signal_exit'])
        check(prefix+' actual two data sockets EOF without bytes',set(aft['data_sockets'])=={'1-1','1-2'} and all(x=={'eof':True,'unexpected_hex':''} for x in aft['data_sockets'].values()))
        check(prefix+' stdout raw length equals consumed',len(raw)==r['stdout_consumed_bytes'])
        if mode=='ready':
            stopped=aft['stopped_events'];check(prefix+' actual stopped event/EOF and all resources0',aft['backend_cleanup_observed'] and aft['stdout_eof'] and len(stopped)==1 and stopped[0] in events and len(stopped[0]['snapshot']['devices'])==2 and all(not x['leased'] and x['pending_urbs']==x['hid_queued_reports']==x['cdc_queued_bytes']==x['configuration']==0 for x in stopped[0]['snapshot']['devices']))
        else:
            pipe=r['pipe'];writer=pipe['blocked_writer']
            check(prefix+' exact4096 full pipe EAGAIN with no drain',pipe['capacity']==pipe['bytes_after_fill']==pipe['synthetic_padding_bytes']==aft['stdout_unread_bytes']==4096 and pipe['bytes_before']==0 and pipe['independent_writer_eagain'] and not r['stdout_drained_after_fill'] and not aft['stdout_read_after_fill'] and len((d/'synthetic-padding.bin').read_bytes())==4096)
            check(prefix+' only separate OFD nonblocking',all(not(pipe[k]&os.O_NONBLOCK) for k in ('child_stdout_flags_before','child_stdout_flags_after','original_writer_flags_before','original_writer_flags_after')) and bool(pipe['independent_writer_flags']&os.O_NONBLOCK))
            check(prefix+' actual child writer blocked before signal',writer['tid']>0 and writer['comm']=='virtual-test-st' and writer['wchan']=='pipe_write' and writer['monotonic_ns']<=r['sent']['monotonic_ns'])
            check(prefix+' blocked scope no fabricated backend stopped proof',not aft['backend_cleanup_observed'] and not aft['stopped_events'] and not any(x.get('event')=='stopped' for x in events))
        for artifact in r['artifacts']:
            found=meta(d/artifact['path']);check(prefix+' artifact bytes '+artifact['path'],found['bytes']==artifact['length'] and found['sha256']==artifact['sha256'])
        local_cases.append({'mode':mode,'signal':sig,'pid':r['child']['pid'],'exit':aft['returncode'],'elapsed_seconds':aft['signal_exit_seconds'],'blocked_writer':r['pipe'].get('blocked_writer'),'cleanup_scope':'actual stopped virtual resources0' if mode=='ready' else 'exit+socketEOF only; no stopped/backend proof'})

bp=json.loads((a.local/'backpressure.log').read_text())
check('legacy two backpressure checks terminal and exact modes',bp['result']=='PASS' and bp['host_uid']==1000 and [x['mode'] for x in bp['checks']]==['sigint','deadline'])
for r in bp['checks']:
    check('legacy '+r['mode']+' unchanged output/EOF/deadline contract',r['stdout_capacity']==4096 and not r['stdout_drained_after_fill'] and r['lease_socket_closed'] and ((r['exit']==0 and r['elapsed_seconds']<=2) if r['mode']=='sigint' else (r['exit']!=0 and 4<=r['elapsed_seconds']<=7)))

raw_after={str(p):meta(p) for p in raw_paths}
check('all reviewed preserved raw/source artifacts unchanged through this review',raw_before==raw_after,{'artifact_count':len(raw_paths)})
check('review did not find compiled source drift',all(meta(a.root/r['path'])['sha256']==r['sha256'] for r in inputs))
inputs_file=dump('input-reconciliation.json',input_map)
rows_file=dump('test-executions.json',{'commands':commands,'rows':rows,'tests':tests,'distinct_qualified':[{'target':t,'name':n} for t,n in sorted(qualified)],'prior_delta':delta})
raw_file=dump('raw-sha256.json',{'before':list(raw_before.values()),'after_unchanged':raw_before==raw_after})
report={
 'schema_version':1,'result':'PASS' if all(x['passed'] for x in checks) else 'FAIL',
 'scope':'Independent raw-statistics/source/ELF/local-evidence reconciliation by Native implementation participant, not independent approval of own production code. No commands or runners were executed/imported.',
 'actual_start':start,'counts':{'default_executions':counts['default'],'all_features_executions':counts['all-features'],'vendor_executions':counts['vendor'],'total_executions':len(tests),'result_rows':len(rows),'distinct_target_qualified_names':len(qualified),'failed':sum(x['failed'] for x in rows),'ignored':sum(x['ignored'] for x in rows),'selected_inputs':len(inputs),'local_signal_cases':len(local_cases),'legacy_backpressure_cases':len(bp['checks'])},
 'commands':commands,'tools':tools,'release_artifacts':artifacts,'native_only':{'commands':result['commands'][1:3],'normal_tree_excludes':['rustusb-rust-backend','nusb','rusb','libusb1-sys']},
 'local_signal_scope':signal_summary['scope'],'local_cases':local_cases,'legacy_backpressure':bp,
 'limits':[
 'Actual dirty b1de034 candidate; no future commit mapping and no clean-run claim. Prior655 and five RED failures remain their original evidence.',
 '667 counts executions across default/allfeatures/vendor;387 distinct target-qualified names. Separate62/71 developer tests and local runtime scenarios are not added to667.',
 'Gate allfeatures release command plus recorded ELF linked to each signal child actual /proc/exe fingerprint. Signals --skip-build itself correctly states features external/not inferred.',
 'Legacy two-backpressure script has no per-child /proc ELF/source guard; attribution is the wrapper selected path, prior allfeatures build and stable release/source workflow. No missing child originals fabricated.',
 'The success-enclosing secure_foreground test is in allfeatures CLI test output. This audit does not assign unrelated temporary TLS5 artifacts to it without an explicit preserved path/receipt.',
 'Local ordinary-user plaintext virtual profiles only. Blocked stdout cases prove0exit+twoEOF with actual fullpipe/writer observation; no stopped/backend/kernel/root cleanup proof claimed while unread.',
 'No new hardware, trusted TLS-loader, helper, USBFS, QEMU or phase completion evidence.',
 'First review-parser output retained separately: its only failed check incorrectly expected scripts/check.sh in the signal runner filtered Rust/TOML/vendor manifest. The software/local wrapper167 guards cover that script; signal own166 selected paths and actual9712 fingerprint are checked separately. Original runs/JSON/logs unchanged.',
 'No original checker/runner imports, Cargo, binaries, Git, QEMU or host USB/module/network mutation.'
 ],
 'detail_files':[inputs_file,rows_file,raw_file], 'checks':checks,
 'passed_checks':sum(x['passed'] for x in checks),'failed_checks':sum(not x['passed'] for x in checks)
}
report_path=dump('review.json',report)
print(json.dumps({'result':report['result'],'passed_checks':report['passed_checks'],'failed_checks':report['failed_checks'],'report':report_path,'failed':[x for x in checks if not x['passed']]}))
raise SystemExit(0 if report['result']=='PASS' else 1)
