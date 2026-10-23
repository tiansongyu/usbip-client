#!/usr/bin/env python3
"""Read-only, standalone raw-evidence review; no project imports or QEMU/Cargo execution."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys

OUT = Path(__file__).resolve().parent
REPO = Path('/home/ubuntu/github/usbip-client')
HEAD = '68cc3adf87abf43fcedd2b227c6ad20a5bae4370'
CLI = 'bb6fb276c0cbfbb73c2cdb8af0f73d87045ac7235b4943cf096c0643d570814a'
HELPER = 'b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5'
BASES = {k: Path('/tmp/rustusb-prepare-stop-'+k+'-resource-job-68cc3ad-20261024') for k in ['native', 'usbfs']}
checks = []
blob_cache = {}
def sha(b): return hashlib.sha256(b).hexdigest()
def readj(p): return json.loads(p.read_text())
def linesj(p): return [json.loads(x) for x in p.read_text().splitlines() if x]
def ck(ok, name, detail=None):
    checks.append({'name': name, 'pass': bool(ok), **({'detail': detail} if detail is not None else {})})
def exact(a,b,name): ck(a==b,name)
def inventory():
    return {k: {str(p.relative_to(base)): {'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())}
                for p in sorted(base.rglob('*')) if p.is_file()} for k,base in BASES.items()}
def dump(name,value):
    (OUT/name).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')
def git_blob(path):
    if path not in blob_cache:
        blob_cache[path]=subprocess.check_output(['git','cat-file','blob',HEAD+':'+path],cwd=REPO)
    return blob_cache[path]
def terminals(root): return [x.get('terminal',x) for x in root['terminals']]
def receipt_rows(root,kind): return root['exports'] if kind=='native' else root['records']
def stat_identity(text):
    pid=int(text.split(' ',1)[0]); tail=text.rsplit(')',1)[1].split()
    return pid,int(tail[19])
def normalize_profile(row):
    return {'configuration':int(row['configuration']), 'interfaces':[
        {'number':int(x['number'],16),'alternate':int(x['alt']), 'driver':x['driver']}
        for x in row['interfaces']]}
def base64_review(base,role,expected):
    raw=(base/(role+'-evidence.log')).read_text().splitlines(); found={}; index=0
    while index<len(raw):
        line=raw[index]
        if line.startswith('SECURE_ARTIFACT_BEGIN '):
            _,name,size=line.split(); index+=1; parts=[]
            while index<len(raw) and not raw[index].startswith('SECURE_ARTIFACT_END '):
                parts.append(raw[index]);index+=1
            ck(index<len(raw),role+' complete base64 '+name)
            exact(raw[index],'SECURE_ARTIFACT_END '+name,role+' matching base64 end '+name)
            ck(name not in found,role+' no duplicate artifact '+name)
            b=base64.b64decode(''.join(parts),validate=True)
            exact(len(b),int(size),role+' base64 declared size '+name)
            exact(b,(base/(role+'-artifacts')/name).read_bytes(),role+' decoded original '+name)
            exact({'bytes':len(b),'sha256':sha(b)},expected[name],role+' guest manifest '+name)
            found[name]=sha(b)
        index+=1
    exact(set(found),set(expected),role+' complete declared guest artifact set')
    ck(not any(x.startswith(('SECURE_ARTIFACT_ERROR','SECURE_ARTIFACT_OVERSIZE')) for x in raw),role+' no artifact error marker')
    return len(found)

def review(kind,base):
    start=len(checks); s=readj(base/'summary.json'); a=base/'server-artifacts'; c=base/'client-artifacts'
    def j(n): return readj(a/n)
    def cj(n): return readj(c/n)
    exact(s['head'],HEAD,kind+' actual original HEAD'); exact(s['head_after'],HEAD,kind+' final HEAD')
    exact(s['git_status_before'],'',kind+' recorded clean before');exact(s['git_status_after'],'',kind+' recorded clean after')
    for name in ['source_guard','binary_guard','head_guard','clean_guard']: ck(s[name] is True,kind+' '+name)
    exact(s['result'],'FAIL',kind+' original overall FAIL retained');exact(s['guest_exit_codes'],{'server':0,'client':0},kind+' collection guest exits')
    exact(s['backend'],kind,kind+' backend');exact(s['case'],kind+'-resource',kind+' case');exact(s['stop'],'job',kind+' stop route')
    exact(s['binary_sha256'],{'rustusb':CLI,'rustusb-helper':HELPER},kind+' original product ELF declarations')
    exact(len(s['source_inputs']),174,kind+' selected original source count')
    for name,meta in s['source_inputs'].items():
        original=(base/'source'/name).read_bytes(); exact({'sha256':sha(original),'bytes':len(original)},meta,kind+' frozen source '+name)
        exact(original,git_blob(name),kind+' original commit blob '+name)
    for name,meta in s['artifact_manifest'].items():
        exact(before[kind][name],meta,kind+' archive original '+name)
    exact(set(s['artifact_manifest']),set(before[kind])-{'summary.json'},kind+' declared complete archive except self')
    decoded={role:base64_review(base,role,s['guest_artifacts'][role]) for role in ['server','client']}
    for role in ['server','client']:
        ar=base/(role+'-artifacts'); text=(base/(role+'-evidence.log')).read_text()
        exact(sha((base/(role+'-generated-init.sh')).read_bytes()),s['payload_sha256'][role]['init'],kind+' '+role+' generated init')
        for label,digest in [('agent-1',CLI),('helper',HELPER)]+([('agent-2',CLI),('gate',s['probe_sha256'])] if role=='server' else []):
            receipt=(ar/('executable-'+label+'.txt')).read_text().split()
            exact(receipt[0],digest,kind+' '+role+' actual /proc executable '+label)
            ck(re.fullmatch(r'/proc/[0-9]+/exe',receipt[1]) is not None,kind+' '+role+' live exe path '+label)
        for product,digest in [('rustusb',CLI),('rustusb-helper',HELPER)]:
            exact(s['payload_sha256'][role]['usr/bin/'+product],digest,kind+' '+role+' payload '+product)
        ck('SECURE_CREDENTIAL_ISOLATION role='+role+' own_key=true opposite_key=false ca_key=false' in text,kind+' '+role+' role private key isolation')
        ck('SECURE_RNG_RTC_READY crng=true' in text,kind+' '+role+' actual CRNG ready')
        ck('Kernel panic' not in text,kind+' '+role+' no panic')
    exact(sha((base/'kernel.config').read_bytes()),s['kernel_config_sha256'],kind+' archived kernel config hash')
    sel=j('selection.json'); candidate=sel['candidate']; ready=j('gate-ready.json'); armed=j('gate-armed.json')
    held=j(f'gate-candidate-{candidate}.json'); exited=j(f'gate-exit-{candidate}.json'); pre=j('gate-held-before.json'); post=j('gate-held-after.json')
    events=linesj(a/'gate-events.jsonl')
    exact(sel['case'],kind+'-resource',kind+' selected case');exact(sel['stop'],'job',kind+' selected stop mode')
    exact((ready['uid'],ready['gid'],ready['cap_effective'],ready['cap_permitted'],ready['cap_ambient'],ready['cap_inheritable']),(2000,2000,0,0,0,0),kind+' actual tracee role and no caps')
    for e in [armed,held,exited]:
        exact(e['nonce'],'prepare-stop-v1',kind+' gate nonce '+e['event']);exact(e['operation'],'resource',kind+' gate operation '+e['event'])
        exact((e['tracee_pid'],e['tracee_starttime'],e['tracer_pid']),(ready['tracee_pid'],ready['tracee_starttime'],ready['tracer_pid']),kind+' same process generation '+e['event'])
        exact(e['request'],74,kind+' actual x86_64 fsync syscall '+e['event'])
        ck(all(v==0 for v in e['mutation_counts'].values()),kind+' gate has no USB mutation syscall '+e['event'])
    for name,e in [('before',pre),('after',post),('exit',exited)]:
        for field in ['candidate','tracee_pid','tracee_starttime','tid','fd','file_device','file_inode']:
            exact(e[field],held[field],kind+' same held '+field+' '+name)
    ck(pre['held'] and post['held'],kind+' both independent observations are held')
    ck(held['monotonic_ns']<pre['monotonic_ns']<post['monotonic_ns']<exited['monotonic_ns'],kind+' gate chronological order')
    ck(post['monotonic_ns']-pre['monotonic_ns']>=15_000_000_000,kind+' at least 15 seconds actually held')
    exact((exited['return_value'],exited['is_error']),(0,False),kind+' same fsync completes normally after release')
    exact((a/f'gate-decision-{candidate}').read_text(),'select\n',kind+' actual candidate selected control')
    exact((a/f'gate-release-{candidate}').read_text(),'release\n',kind+' actual original gate release control')
    selected_events=[e for e in events if e['event']=='selected'];released_events=[e for e in events if e['event']=='released']
    exact(len(selected_events),1,kind+' one selected gate');exact(len(released_events),1,kind+' one released gate')
    se=selected_events[0];rele=released_events[0]
    ck(se['monotonic_ns']<pre['monotonic_ns']<post['monotonic_ns']<rele['monotonic_ns']<exited['monotonic_ns'],kind+' no release between held observations')
    ck(not any(e['event']=='syscall_exit' and e['tid']==held['tid'] and se['monotonic_ns']<e['monotonic_ns']<rele['monotonic_ns'] for e in events),kind+' selected thread has no syscall exit while held')
    signals=[e for e in events if e['event']=='signal_delivery'];ck(all(e['monotonic_ns']>exited['monotonic_ns'] for e in signals),kind+' no signal substitutes StopJob while held')
    ck(any(e.get('signal')==15 for e in signals),kind+' later agent teardown TERM recorded separately')
    fdheld=j('fds-held.json'); syncfd=[x for x in fdheld if int(x['fd'])==held['fd']]
    exact(len(syncfd),1,kind+' held fsync actual FD exists'); f=syncfd[0]
    exact((f['target'],int(f['device']),int(f['inode'])),('/var/lib/rustusb/agent/state.next',held['file_device'],held['file_inode']),kind+' fsync actual state.next descriptor identity')
    status=(a/'identity-agent-1.txt').read_text()
    ck(re.search(r'^Uid:\s+2000\s+2000\s+2000\s+2000$',status,re.M) is not None,kind+' actual agent UID')
    for mark in ['idle','held','before-release']:
        exact(stat_identity((a/('owner-stat-'+mark+'.txt')).read_text()),(ready['tracee_pid'],ready['tracee_starttime']),kind+' same owner PID/starttime '+mark)
    original=j('state-before-ack.json');draft=j('selected-state.json');sessions=j('selected-sessions.json');attempts=j('selected-attempts.json')
    exact(draft,j(f'draft-{candidate}.json'),kind+' selected original state.next draft')
    exact(draft['schema_version'],2,kind+' actual private application schema2'); exact(draft['sequence'],original['sequence']+1,kind+' held write is next durable sequence')
    exact(draft['audit'][-1]['action'],'resource',kind+' actual pending Resource record');exact(original['audit'][-1]['action'],'attempt',kind+' preceding committed Attempt')
    exact(draft['audit'][:-1],original['audit'],kind+' all preceding audit entries unchanged')
    exact(sessions,[x for x in draft['sessions'] if x['profile']=='pool' and x['role']=='export' and not x['terminal']],kind+' selected two live actual export sessions')
    exact(len(sessions),2,kind+' two preparation sessions');exact(attempts,[x['attempts'][0] for x in sessions],kind+' exact selected attempt list')
    exact({x['job_id'] for x in sessions},{sel['job']},kind+' single selected job')
    ck(all(x['state']=='Exporting' and x['cleanup'] is None and not x['terminal'] for x in sessions),kind+' held stage is preparation not serving')
    ck(all(x['resource_id'] is not None and x['proof'] is None for x in attempts),kind+' draft has two resource identities without root proofs')
    old_resourceids={x['resource_id'] for x in attempts};exact(len(old_resourceids),2,kind+' distinct old capabilities')
    rootheld=j('root-held.json');rows=receipt_rows(rootheld,kind);exact(len(rows),2,kind+' two actual helper resources held')
    exact(rootheld['schema_version'],2 if kind=='native' else 3,kind+' strict helper schema')
    exact(rootheld['terminals'],[],kind+' no root terminal before release')
    exact(rootheld['records'],[] if kind=='native' else rows,kind+' helper record family')
    exact({x['lease_id'] for x in rows},old_resourceids,kind+' helper resources exactly match pending application attempts')
    baseline=j('host-before.json');nodes=j('usb-nodes-before.json');fdmap=[]
    for session in sessions:
        attempt=session['attempts'][0];rr=next(x for x in rows if x['lease_id']==attempt['resource_id']);exact(rr['correlation'],attempt['correlation'],kind+' exact root correlation '+session['id'])
        owner=rr['owner'];exact((owner['uid'],owner['pid'],owner.get('starttime',owner.get('start_time'))),(2000,ready['tracee_pid'],ready['tracee_starttime']),kind+' exact helper owner identity '+session['id'])
        if kind=='native':
            registration=rr['registration'];exact(registration['device_key'],session['device_key'],kind+' exact helper key '+session['id']);g=registration['generation'];b=next(x for x in baseline if x['bus']==g['bus_id']);profile=registration['original_profile'];exact(rr['stub'],None,kind+' no Native stub before listener '+session['id']);ck(rr['bound'] is not None,kind+' Native export actually bound '+session['id'])
            exact((g['filesystem_device'],g['inode'],g['device_number'],g['bus_number']),(int(b['generation'].split(':')[0]),int(b['generation'].split(':')[1]),int(b['devnum']),int(b['busnum'])),kind+' Native immutable generation '+session['id'])
            exact(profile['configuration'],int(b['configuration']),kind+' Native original configuration '+session['id']);exact([(x['number'],x['alternate'],x['driver'])for x in profile['interfaces']],[(x['number'],x['alternate'],x['driver'])for x in normalize_profile(b)['interfaces']],kind+' Native original interface baseline '+session['id'])
        else:
            bl=rr['baseline'];exact('physical:'+bl['device_id'],session['device_key'],kind+' exact helper key '+session['id']);b=next(x for x in baseline if x['bus']==bl['bus_id']);n=next(x for x in nodes if x['bus']==bl['bus_id']);receipt=bl['receipt'];exact((receipt['sysfs_device'],receipt['sysfs_inode'],receipt['usb_device_number'],receipt['bus_number']),(int(b['generation'].split(':')[0]),int(b['generation'].split(':')[1]),int(b['devnum']),int(b['busnum'])),kind+' USBFS exact sysfs receipt '+session['id'])
            exact({'configuration':bl['configuration'],'interfaces':bl['interfaces']},normalize_profile(b),kind+' USBFS complete original baseline '+session['id'])
            exact(receipt['node_inode'],int(n['inode']),kind+' USBFS node inode '+session['id']);major,minor=(int(x,16) for x in n['rdev'].split(':'));exact(receipt['node_device'],os.makedev(major,minor),kind+' USBFS exact node rdev '+session['id'])
            matches=[f for f in fdheld if f['target']==n['node'] and f['inode']==n['inode'] and f['device']==n['filesystem_device'] and f['rdev']==n['rdev']];exact(len(matches),1,kind+' exactly one actual USBFS FD matching receipt '+session['id']);fdmap.append({'session_id':session['id'],'resource_id':rr['lease_id'],'correlation':rr['correlation'],'fd':matches[0]})
    observations=linesj(a/'held-observations.jsonl');outcome=j('held-outcome.json')
    exact(outcome['failed'],1,kind+' original held failure latched');exact(outcome['resource_released_while_gate_held'],False,kind+' observed not released while held')
    ck(float(observations[-1]['uptime'])-float(outcome['stop_uptime'])>=15,kind+' stop observation lasts actual guest uptime 15s')
    for ob in observations:
        i=ob['observation'];rr=j(f'root-observe-{i}.json');ag=j(f'agent-observe-{i}.json');h=j(f'host-observe-{i}.json');fds=j(f'fds-observe-{i}.json')
        exact(rr,rootheld,kind+f' sample {i} root still holds exact old resources')
        exact(ag,original,kind+f' sample {i} committed journal has no false ACK')
        exact(h,j('host-held.json'),kind+f' sample {i} actual sysfs unchanged while held')
        exact(stat_identity((a/f'owner-stat-observe-{i}.txt').read_text()),(ready['tracee_pid'],ready['tracee_starttime']),kind+f' sample {i} original owner still alive')
        exact(ob['exact_root_terminal'],False,kind+f' sample {i} no root terminal')
        exact(ob['gate_released'],False,kind+f' sample {i} gate unreleased')
        exact(ob['baseline_equal'],h==baseline,kind+f' sample {i} recomputed baseline comparison')
        usb=[x for x in fds if x['target'].startswith('/dev/bus/usb/')]
        exact(ob['target_fd_closed'],not usb,kind+f' sample {i} recomputed USBFS descriptor absence')
        if kind=='usbfs': exact(usb,[x for x in fdheld if x['target'].startswith('/dev/bus/usb/')],kind+f' sample {i} both exact USBFS descriptors still owned')
    exact(j('root-before-release.json'),rootheld,kind+' final pre-release root resources unchanged')
    exact(j('agent-before-release.json'),original,kind+' final pre-release committed state unchanged')
    exact(j('host-before-release.json'),j('host-held.json'),kind+' final pre-release host unchanged')
    script=(base/'server-generated-init.sh').read_text();gate_source=(base/'source/scripts/qemu_prepare_recording_shutdown/prepare-gate.c').read_text()
    ck('agent() { (exec 5<&-; /usr/bin/setpriv --reuid=1000 --regid=1000 --clear-groups' in script,kind+' frozen StopJob wrapper UID1000 closes control FD')
    ck('agent stop-job "$TARGET_JOB" > /tmp/stop-target.json' in script,kind+' frozen StopJob request route')
    ck('PTRACE_GET_SYSCALL_INFO' in gate_source and 'info.op == PTRACE_SYSCALL_INFO_ENTRY' in gate_source and 'info.entry.nr == SYS_fsync && state_next' in gate_source,kind+' frozen probe explicitly gates entry not kernel active')
    exact((a/'wait-stop.txt').read_text().strip(),'1',kind+' original StopJob caller times out with exit1')
    ck('operation timed out: agent local request' in (a/'stop-target.log').read_text(),kind+' original StopJob caller timeout not hidden')
    after=j('root-after-release.json');exact(receipt_rows(after,kind),[],kind+' original resources actually retired after gate release')
    exact(after['records'],[],kind+' no old root records after release');exact(j('host-after-release.json'),baseline,kind+' complete original driver/config/alternate/generation baseline after release')
    ts=terminals(after);exact(len(ts),2,kind+' two root terminal receipts after release')
    for session in sessions:
        attempt=session['attempts'][0];t=next(x for x in ts if x['resource_id']==attempt['resource_id']);exact(t['correlation'],attempt['correlation'],kind+' same original attempt restored '+session['id']);exact((t['kind'],t['device_key'],t['owner_uid'],t['proof']),(attempt['kind'],session['device_key'],2000,'native_export_restored' if kind=='native' else 'physical_baseline_restored'),kind+' exact post-release root proof '+session['id'])
    agafter=j('agent-after-release.json');stops=[x for x in agafter['audit'] if x['action']=='stop'];exact(len(stops),1,kind+' one persisted original StopJob');exact((stops[0]['actor_uid'],stops[0]['job_id'],stops[0]['session_id'],stops[0]['failure']),(1000,sel['job'],None,None),kind+' real UID1000 stop audit belongs to old job')
    finals=j('target-final.json')['response']['sessions'];exact({x['id']for x in finals},{x['id']for x in sessions},kind+' original session identities retained through cleanup')
    ck(all(x['terminal'] and x['state']=='Disconnected' and x['cleanup']=='verified' and x['evidence']=='released' and x['stop_requested'] for x in finals),kind+' same old sessions eventually close verified')
    exact((a/'wait-tracer.txt').read_text().strip(),'0',kind+' actual tracer parent reap exit0')
    fresh=[x for x in j('reuse-ready.json')['response']['sessions'] if not x['terminal']];exact(len(fresh),2,kind+' two fresh real ready exports')
    ck(all(x['state']=='Available' and x['evidence']=='export_ready' and x['instance_id'] not in {q['instance_id']for q in sessions} and x['job_id']!=sel['job'] for x in fresh),kind+' actual new epoch/new job reuse')
    ck({x['attempts'][0]['resource_id']for x in fresh}.isdisjoint(old_resourceids),kind+' genuinely fresh capabilities')
    exact({x['device_key']for x in fresh},{x['device_key']for x in sessions},kind+' reuse same approved devices')
    servertext=(base/'server-evidence.log').read_text();clienttext=(base/'client-evidence.log').read_text()
    order=['PREPARE_GATE_HELD','PREPARE_OBSERVED failed=1','PREPARE_TARGET_CLOSED','PREPARE_REUSE_READY','PREPARE_SERVER_CLOSED','RUSTUSB_PREPARE_RESULT: FAIL role=server stage=actual-held-cleanup-missing exit=1']
    positions=[servertext.index(x) for x in order];ck(positions==sorted(positions),kind+' original fail remains after successful cleanup/reuse')
    ck('RUSTUSB_PREPARE_RESULT: PASS role=server' not in servertext,kind+' no server PASS replacing failure')
    ck('RUSTUSB_PREPARE_RESULT: PASS role=client case='+kind+'-resource stop=job' in clienttext,kind+' actual client collection/I/O PASS')
    ck('AGENT_CLIENT_IO round=1 hid=yes rx=35 tx=35' in clienttext,kind+' actual post-release I/O stage')
    ck('Connection refused' in (c/'held-connect.stderr').read_text(),kind+' no listener during preparation')
    controls=linesj(base/'host-control.jsonl');exact([x['mark']for x in controls],['keys','start-target','held','stop-target','start-reuse','stop-reuse','server-closed','finish-server','finish-client'],kind+' actual host control sequence')
    qmp=readj(base/'qmp-input.json');exact(qmp,{'command':'send-key','arguments':{'keys':[{'type':'qcode','data':'1'}],'hold-time':100},'return':{}},kind+' actual QMP KEY1 input')
    inp=(c/'input-1.bin').read_bytes();ck(len(inp)%24==0,kind+' exact x86_64 evdev record alignment');evdev=list(struct.iter_unpack('<qqHHi',inp));keys=[(e[2],e[3],e[4])for e in evdev if e[2]==1];exact(keys,[(1,2,1),(1,2,0)],kind+' actual KEY1 press and release')
    rx=(c/'rx-1.bin').read_bytes();tx=(c/'tx-1.bin').read_bytes();exact(len(rx),35,kind+' actual FTDI RX length');exact(len(tx),35,kind+' actual FTDI TX length')
    exact(rx,(base/'ftdi-host-injected-1.bin').read_bytes(),kind+' host-to-client exact serial bytes');exact(tx,(base/'ftdi-host-rx.bin').read_bytes(),kind+' client-to-host exact serial bytes');exact(rx,(c/'expected-rx-1.bin').read_bytes(),kind+' prescribed RX')
    live=cj('client-receipts-reuse.json');exact(len(live['records']),2,kind+' actual client two VHCI records');exact(live['exports'],[],kind+' client has no export')
    local=cj('local-identities-reuse.json');crfinal=cj('root-final.json');cterm=terminals(crfinal)
    for r in live['records']:
        exact(r['state']['phase'],'observed',kind+' client actual VHCI observed '+r['channel_id']);rs=r['state']['receipt']['session'];ck(rs['status']==6 and r['state']['receipt']['kernel_socket_inode']>0,kind+' client USED socket '+r['channel_id'])
        key='hid' if rs['vendor_id']==1575 else 'serial';exact(rs['local_bus_id'],local[key],kind+' exact client local bus '+key);gen=rs['local_device_generation'];exact(f"{gen['filesystem_device']}:{gen['inode']}",local[key+'_generation'],kind+' exact client local generation '+key)
        t=next(x for x in cterm if x['resource_id']==r['channel_id']);exact((t['correlation'],t['device_key'],t['kind'],t['proof']),(r['correlation'],r['target']['device_key'],'native_import','native_import_released'),kind+' client exact old import terminal '+key)
    exact(crfinal['records'],[],kind+' final client zero channels');exact(crfinal['exports'],[],kind+' final client zero exports');exact(len(cterm),2,kind+' final client two exact proof records')
    finalvhci=[x.split()for x in(c/'vhci-final.txt').read_text().splitlines()if x.startswith(('hs ','ss '))];ck(bool(finalvhci) and all(x[2]=='004' and x[4]=='00000000' and x[-1]=='0-0' for x in finalvhci),kind+' final actual all VHCI NULL')
    ck(not any(x.startswith('DEVICE ')for x in(c/'vhci-final.txt').read_text().splitlines()),kind+' final local devices Gone')
    cf=cj('agent-final.json')['sessions'];ck(len(cf)==2 and all(x['terminal'] and x['cleanup']=='verified' and x['evidence']=='released' for x in cf),kind+' client terminal application cleanup')
    exact(j('host-final.json'),baseline,kind+' final complete host original baseline');srfinal=j('root-final.json');exact(srfinal['records'],[],kind+' final server no records');exact(srfinal.get('exports',[]),[],kind+' final server no exports')
    for rec in receipt_rows(j('root-reuse-ready.json'),kind):
        t=next(x for x in terminals(srfinal) if x['resource_id']==rec['lease_id']);exact(t['correlation'],rec['correlation'],kind+' fresh export exact final root proof')
    return {'backend':kind,'source_inputs':len(s['source_inputs']),'original_result':'FAIL','independent_result':'CONFIRMED_EXPECTED_HELD_CLEANUP_FAILURE_WITH_SUCCESSFUL_POST_RELEASE_REUSE','guest_exits':s['guest_exit_codes'],'observations':len(observations),'held_window_ns':post['monotonic_ns']-pre['monotonic_ns'],'candidate':candidate,'old_owner':{'pid':ready['tracee_pid'],'starttime':ready['tracee_starttime'],'tid':held['tid'],'fsync_fd':held['fd']},'selected_sessions':sessions,'actual_usbfs_fd_mapping':fdmap,'decoded_artifacts':decoded,'actual_serial_bytes_each_direction':35,'actual_evdev_records':len(evdev),'old_root_terminals_after_release':ts,'observed_original_verifier_metadata_not_reused':{'count':s['facts']['check_count'],'failures':s['facts']['failures']},'checks':len(checks)-start}

before=inventory();dump('raw-sha-before.json',before)
reports=[]
try:
    for kind,base in BASES.items(): reports.append(review(kind,base))
except Exception as e:
    dump('parser-error.json',{'exception':type(e).__name__,'message':str(e),'completed_checks':len(checks),'completed_runs':len(reports)})
    dump('checks-partial.json',checks)
    raise
finally:
    after=inventory();dump('raw-sha-after.json',after)
exact(before,after,'all original file bytes and file sets unchanged throughout review')
failed=[x for x in checks if not x['pass']]
report={'method':'standalone Python standard-library raw decoder, manifest+Git blob comparisons, no harness verifier import/execution','reviewer_authorship':'Method cross-review, not independent implementation: reviewer contributed production service code and earlier Rust backend test harness components; did not author the old Native prepare-gate path being reviewed. No claim of fully independent implementation.','review_result':'PASS' if not failed else 'FAIL','product_run_results_remain':'Both actual original QEMU runs FAIL; guest exit0 proves collection completion only.','head':HEAD,'old_cli_sha256':CLI,'helper_sha256':HELPER,'runs':reports,'diagnostic_predicate_count':len(checks),'failed_predicates':failed,'raw_file_count':sum(map(len,before.values())),'raw_unchanged':before==after,'git_blob_reads':len(blob_cache),'limitations':['QEMU emulated devices, not physical hardware G1/G2.','fsync syscall ENTRY held by ptrace; no claim of kernel-internal active operation or arbitrary kernel-hard-block cancellation.','The original second Resource ACK prevents listener startup; no pre-preparation sibling real I/O was exercised.','StopJob caller actually timed out after 5 seconds; later persisted actor_uid=1000 StopJob and frozen UID wrapper corroborate route. No separate live /proc receipt for transient stop client.','No TERM occurs during the held window; later TERM occurs only after original target sessions are verified closed, before fresh epoch creation.','USBFS baseline already equals original while held; actual two open character FDs and two pending root records prevent a false release conclusion.','Native held ownership uses helper export receipt plus bound sysfs state and supervised control sockets; no unsupported socket-inode-to-export receipt mapping is inferred.','Application attempt.proof remains null for normal cooperative Finish; exact restoration proof is the privileged root terminal correlated by instance/session/attempt/resource/key.','Original archive has ELF digests and actual guest /proc/exe receipts, not archived ELF binaries; this review does not rerun or retroactively hash replacement builds.','Gate, source and binary guards are contemporaneous runner evidence corroborated by saved bytes and Git blobs; not independently re-created execution.','Network PCAP files are integrity-checked, not re-audited for TLS packet semantics in this lifecycle-only review.','Original Native161 / USBFS175 verifier metadata is recorded only for traceability; independent conclusions are derived from raw artifacts above.']}
dump('report.json',report);dump('checks.json',checks)
dump('handoff.json',{'report_sha256':sha((OUT/'report.json').read_bytes()),'parser_sha256':sha(Path(__file__).read_bytes()),'raw_sha_before_sha256':sha((OUT/'raw-sha-before.json').read_bytes()),'raw_sha_after_sha256':sha((OUT/'raw-sha-after.json').read_bytes()),'result':report['review_result'],'original_product_results':'FAIL / FAIL','diagnostic_predicates_not_usb_cases':len(checks),'raw_files':report['raw_file_count']})
print(json.dumps({'review_result':report['review_result'],'diagnostic_predicates':len(checks),'failures':failed[:8],'raw_files':report['raw_file_count'],'runs':[{k:r[k]for k in ['backend','observations','decoded_artifacts','held_window_ns']}for r in reports]}))
sys.exit(bool(failed))
