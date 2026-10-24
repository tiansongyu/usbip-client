"""Focused diagnostic export assertions plus the unchanged Native epoch contract.

The imported lifecycle predicates are source-locked reuse, not a claim of an
independently implemented auditor. No standalone execution or USB side effects.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct

ROOT=Path(__file__).resolve().parents[2]
SPEC=importlib.util.spec_from_file_location('diagnostic_native_lifecycle',ROOT/'scripts/qemu_process_logging/verify.py')
if SPEC is None or SPEC.loader is None: raise RuntimeError('cannot load locked lifecycle predicates')
L=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(L)


def rows(raw):
    return [L.decode(line) for line in raw.splitlines() if line]


def stable(state):
    # Owner metrics advance independently; their original values remain saved.
    return {'jobs':state['jobs'],'sessions':[{k:v for k,v in s.items() if k!='metrics'} for s in state['sessions']], 'requests':state['requests']}


class AliasBinding:
    def __init__(self, check, counts):
        self.forward={};self.reverse={};self.check=check;self.counts=counts
    def bind(self, namespace, original, alias):
        if original is None:
            self.check(alias is None,'nullable '+namespace+' identity remains absent');return
        self.check(type(alias) is int and 1<=alias<=self.counts[namespace]<=8192,'bounded '+namespace+' alias')
        key=(namespace,str(original));reverse=(namespace,alias)
        self.check(self.forward.get(key,alias)==alias and self.reverse.get(reverse,str(original))==str(original),'consistent injective '+namespace+' relation')
        self.forward[key]=alias;self.reverse[reverse]=str(original)


def check_projection(o):
    check=o.check
    raw=o.raw('server','diagnostic-bundle.json');bundle=L.decode(raw)
    receipt=o.data('server','export-held.stdout')
    check(0<len(raw)<=4*1024*1024 and raw.endswith(b'\n'),'actual complete private artifact <=4MiB')
    check(set(bundle)=={'schema_version','capture','summary','program_version','build','privacy','limits','alias_counts','requested_agent_scope','native','helper','agent','health','logs'},'closed top-level artifact fields')
    check(bundle['schema_version']==1 and bundle['capture']=='complete' and bundle['summary']['complete'] is True,'complete requested capture')
    check(receipt['schema_version']==1 and receipt['event']=='diagnostic_export' and receipt['capture']=='complete' and receipt['cancel_observed'] is False and receipt['summary']==bundle['summary'],'actual CLI single receipt matches document')
    check(receipt['file']=={'result':'written','receipt':{'publication':'published','partial_name':None,'written_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}},'actual synced publication receipt and bytes SHA')
    check(o.raw('server','export-held.wait')==b'0\n','actual operator exporter wait0')
    check(len(bundle['summary']['sections'])==5 and {r['stage'] for r in bundle['summary']['sections']}=={'native','helper','agent','health','logs'} and all(r['outcome']=='complete' and r['failure'] is None and type(r['observed_at_millis']) is int for r in bundle['summary']['sections']),'five independently sampled complete sections')
    check(bundle['build']['secure_agent_collection'] is True and bundle['native']['os']=='linux' and bundle['native']['architecture']=='x86_64' and bundle['helper']['peer_verified'] is True,'installed Native and real authenticated helper observations')
    check(bundle['limits']=={'serialized_bytes':4194304,'aliases':8192,'jobs':512,'sessions':512,'audit':1024,'health':512,'log_records':512,'log_pages':32},'declared finite collection limits')
    check(set(bundle['native'])=={'os','architecture','kernel','kernel_numeric','usbip_host','vhci_hcd','usbip','usbipd'} and all(set(bundle['native'][name])=={'loaded','sysfs','availability'} for name in ('usbip_host','vhci_hcd')),'closed Native fields exclude tool paths/version/detail/action')
    check(set(bundle['helper'])=={'status','peer_verified','kernel_available','active_sessions','external_trusted_network_policy_statement'},'closed helper fields exclude raw detail/action')
    check(set(bundle['agent'])=={'application_epoch','profile','caller','backend','committed_sequence','publication_age_millis','audit_oldest_retained_sequence','expires_after_millis','expected_jobs','expected_sessions','expected_audit','jobs','sessions','audit'},'closed agent header omits raw token and source identifiers')
    audit_fields={'application_epoch','sequence','actor','profile','job','session','action','failure','state','evidence','cleanup'}
    check(all(set(row)=={'job','application_epoch','profile','operator','state','stop_requested','operation_error'} for row in bundle['agent']['jobs']) and all(set(row)=={'session','application_epoch','job','profile','role','device','principal','state','evidence','stop_requested','terminal','operation_error','cleanup','metrics','attempts'} for row in bundle['agent']['sessions']) and all(set(a)=={'kind','application_epoch','session','attempt','resource','proof'} for row in bundle['agent']['sessions'] for a in row['attempts']) and all(set(row)==audit_fields for row in bundle['agent']['audit']),'closed owner/audit/attempt fields cannot carry raw DTO additions')
    check(all(set(sample)=={'session','application_epoch','role','availability','not_in_committed_view','observed_at_millis','link'} and (sample['link'] is None or set(sample['link'])=={'protocol','connection','sample_sequence','elapsed_millis','phase','probe','last_probe_sequence','last_probe_enqueued_millis','last_reply_sequence','last_reply_millis','last_round_trip_micros','stats','end'}) for sample in bundle['health']['samples']),'closed independent health omits raw TCP identity')
    check(all(set(record)=={'process_epoch','sequence','elapsed_millis','level','event'} and set(record['event'])==audit_fields|{'kind'} for record in bundle['logs']['records']),'closed authorized log projection cannot carry generic message/target')
    counts=bundle['alias_counts']
    check(set(counts)=={'application_epochs','process_epochs','jobs','sessions','attempts','resources','devices','principals','profiles','uids','connections'} and all(type(v) is int and 0<=v<=8192 for v in counts.values()) and sum(counts.values())<=8192,'bounded separate alias namespaces')
    bind=AliasBinding(check,counts).bind
    committed=o.data('server','selected-committed.json');draft=o.data('server','selected-draft.json');view=bundle['agent']
    jobs=sorted((j for j in committed['jobs'] if j['profile']=='server'),key=lambda row:row['id'])
    sessions=sorted((s for s in committed['sessions'] if s['profile']=='server'),key=lambda row:row['id'])
    audit=[a for a in committed['audit'] if a['profile']=='server' or (a['profile'] is None and a['action']=='denied' and a['actor_uid']==1000)]
    check(len(jobs)==1 and len(sessions)==4 and len(audit)>16,'real retained nonempty single-profile view spans multiple audit pages')
    check(view['backend']=='native' and view['committed_sequence']==committed['sequence'] and draft['sequence']==committed['sequence']+1,'snapshot is original committed sequence; draft excluded')
    check(view['expected_jobs']==len(jobs)==len(view['jobs']) and view['expected_sessions']==len(sessions)==len(view['sessions']) and view['expected_audit']==len(audit)==len(view['audit']),'all filtered retained rows from every bounded page present')
    check(view['expires_after_millis']==30000 and view['audit_oldest_retained_sequence']==committed['audit'][0]['sequence'],'real retention boundary and fixed snapshot expiry')
    bind('profiles','server',view['profile']);bind('uids',1000,view['caller'])
    check(bundle['requested_agent_scope']=={'profile':view['profile'],'caller':view['caller']},'single requested profile/peer scope')
    bind('application_epochs',jobs[0]['instance_id'],view['application_epoch'])
    for old,new in zip(jobs,view['jobs'],strict=True):
        for ns,source,target in [('jobs','id','job'),('application_epochs','instance_id','application_epoch'),('profiles','profile','profile'),('uids','operator_uid','operator')]: bind(ns,old[source],new[target])
        check({k:new[k] for k in ['state','stop_requested','operation_error']}=={k:old[k] for k in ['state','stop_requested','operation_error']},'job state remains actual committed running state')
    for old,new in zip(sessions,view['sessions'],strict=True):
        for ns,source,target in [('sessions','id','session'),('application_epochs','instance_id','application_epoch'),('jobs','job_id','job'),('profiles','profile','profile'),('devices','device_key','device'),('principals','principal','principal')]: bind(ns,old[source],new[target])
        check({k:new[k] for k in ['role','state','evidence','stop_requested','terminal','operation_error','cleanup','metrics']}=={k:old[k] for k in ['role','state','evidence','stop_requested','terminal','operation_error','cleanup','metrics']},'session typed facts and metrics are original pinned commit, not later health')
        check(len(old['attempts'])==len(new['attempts'])==1,'one exact original capability per session')
        for a,b in zip(old['attempts'],new['attempts'],strict=True):
            check(a['kind']==b['kind'] and a['proof']==b['proof'],'capability kind/proof preserved')
            for ns,value,target in [('application_epochs',a['correlation']['instance_id'],'application_epoch'),('sessions',a['correlation']['session_id'],'session'),('attempts',a['correlation']['attempt_id'],'attempt'),('resources',a['kind']+':'+a['resource_id'],'resource')]: bind(ns,value,b[target])
    audit_by_seq={a['sequence']:a for a in audit}
    def audit_row(old,new,epoch):
        check(new['sequence']==old['sequence'] and all(new[k]==old[k] for k in ['action','failure','state','evidence','cleanup']),'typed audit original sequence/outcomes')
        if epoch: bind('application_epochs',jobs[0]['instance_id'],new['application_epoch'])
        else: check(new['application_epoch'] is None,'source audit has no invented historical epoch')
        for ns,source,target in [('uids','actor_uid','actor'),('profiles','profile','profile'),('jobs','job_id','job'),('sessions','session_id','session')]: bind(ns,old[source],new[target])
    for old,new in zip(audit,view['audit'],strict=True): audit_row(old,new,False)
    health=bundle['health'];check(health['pages']==1 and len(health['samples'])==4,'independent health includes exact same four sessions')
    session_alias={r['session']:r for r in view['sessions']}
    for sample in health['samples']:
        check(sample['session'] in session_alias and sample['application_epoch']==view['application_epoch'] and sample['not_in_committed_view'] is False,'health is correlated to committed session without copying network tuple')
        owner=session_alias[sample['session']]
        check(sample['role']==owner['role'],'health role matches actual owner')
        if owner['role']=='export': check(sample['link'] is None and sample['availability']=='not_data_session','export is not a fictitious data link')
        else:
            link=sample['link']
            check(sample['availability']=='live' and link['protocol']=='framed_v2' and link['phase']=='open' and link['end'] is None and type(link['sample_sequence']) is int and link['sample_sequence']>0,'actual independently sampled live framed data connection')
            check('identity' not in link and 'tcp' not in link and type(link['connection']) is int,'health connection is alias only')
    pages=bundle['logs']['pages'];records=bundle['logs']['records']
    check(1<len(pages)<=32 and 16<len(records)<=512 and pages[-1]['more'] is False,'real bounded multi-page logs finish within budget')
    check(all(not p['gap'] and not p['partial_tail'] and p['scanned_bytes']<=262144 and p['live_status_availability']=='observed' and p['live_status']['healthy'] and p['live_status']['failure'] is None and p['live_status']['dropped']==p['live_status']['oversized']==p['live_status']['projection_errors']==0 for p in pages),'actual log status/gap/loss retained and clean')
    check(sum(p['exported_records'] for p in pages)==len(records),'each exported log record accounted by page')
    for record in records:
        event=record['event']
        check(event['kind']=='audit' and event['sequence'] in audit_by_seq,'operator page contains only authorized committed typed audits')
        audit_row(audit_by_seq[event['sequence']],event,True)
    check(draft['sequence'] not in [a['sequence'] for a in view['audit']] and draft['sequence'] not in [r['event']['sequence'] for r in records],'withheld uncommitted audit absent from state and projected logs')
    denied=[a for a in committed['audit'] if a['action']=='denied' and a['actor_uid'] in (1002,1003)]
    check({a['actor_uid'] for a in denied}=={1002,1003} and all(a['profile'] is None for a in denied),'real read-only and audit-only peer refusals committed before gate')
    forbidden={'server','qemu-server','qemu-client','rustusb-qemu.test','192.0.2.1','192.0.2.2','diagnostic-private-canary','withheld-denial-canary'}
    for s in committed['sessions']:
        forbidden.update([s['id'],s['instance_id'],s['job_id'],s['device_key']])
        for a in s['attempts']: forbidden.update([a['resource_id'],a['correlation']['attempt_id']])
    def strings(value):
        if isinstance(value,str): yield value
        elif isinstance(value,list):
            for item in value: yield from strings(item)
        elif isinstance(value,dict):
            for item in value.values(): yield from strings(item)
    values=list(strings(bundle))
    check(not any(v in forbidden or '/' in v or re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-',v) or 'BEGIN PRIVATE' in v or 'RUSTUSB-RX-' in v for v in values),'actual document omits known raw identities/paths/network/canaries/payload strings')
    check(not any(word in raw for word in (b'"actor_uid"',b'"instance_id"',b'"device_key"',b'"tcp"',b'"cursor"',b'"message"',b'"target"',b'"pid"')),'no forbidden raw DTO fields serialized')
    check(bundle['privacy']['identifiers_aliased'] and bundle['privacy']['independent_section_samples'] and bundle['privacy']['network_security_verified'] is False and bundle['privacy']['hardware_test_performed'] is False,'privacy and observation-only scope explicit')
    for uid in (1002,1003):
        denied_receipt=o.data('server',f'export-denied-{uid}.stdout');denied_bundle=o.data('server',f'denied-bundle-{uid}.json')
        check(o.raw('server',f'export-denied-{uid}.wait')==b'1\n' and denied_receipt['capture']=='partial' and denied_bundle['agent'] is None,'real denied exporter cannot get snapshot')
        check(all(r['outcome']=='incomplete' and r['failure']=='permission' for r in denied_receipt['summary']['sections'] if r['stage'] in ('agent','health','logs')),'actual caller lacking either permission refused before all agent reads')
    check(o.data('server','root-before-refusals.json')==o.data('server','root-after-refusals.json') and o.data('server','resources-before-refusals.json')==o.data('server','resources-after-refusals.json'),'permission refusals do not change capabilities/jobs/sessions')
    return {'committed_sequence':committed['sequence'],'excluded_draft_sequence':draft['sequence'],'audit_rows':len(audit),'log_records':len(records),'log_pages':len(pages),'artifact_bytes':len(raw),'artifact_sha256':hashlib.sha256(raw).hexdigest()}


def check_gate(o, broker_sha):
    check=o.check
    selected=int(o.raw('server','selected-candidate.txt'))
    events=rows(o.raw('server','gate-events.jsonl'))
    candidate=o.data('server',f'gate-candidate-{selected}.json');end=o.data('server',f'gate-exit-{selected}.json')
    held=[o.data('server','gate-held-'+mark+'.json') for mark in ('before','after')]
    check(candidate['event']=='held' and candidate['operation']=='resource' and candidate['request']==74 and candidate['nonce']=='prepare-stop-v1','actual Linux x86_64 fsync-entry on original state.next')
    ready=o.data('server','gate-ready.json')
    armed=o.data('server','gate-armed.json')
    start=L.saved_proc(o,'server','agent-start',2000,broker_sha)
    check(ready['event']=='ready' and ready['uid']==2000 and ready['tracee_pid']==candidate['tracee_pid']==start['pid'] and ready['tracee_starttime']==candidate['tracee_starttime']==start['starttime'] and ready['tracer_pid']==candidate['tracer_pid'],'actual candidate is the original ready/retained broker PID and starttime')
    check(armed['event']=='armed' and all(armed[k]==candidate[k] for k in ('nonce','operation','tracer_pid','tracee_pid','tracee_starttime')) and all(end[k]==candidate[k] for k in ('nonce','operation','tracer_pid')),'actual available nonce/operation/tracer identities bind arm/candidate/exit')
    fields=('candidate','tracee_pid','tracee_starttime','tid','fd','request','file_device','file_inode')
    check(all(h['held'] is True and all(h[k]==candidate[k] for k in fields) for h in held),'same actual syscall/thread/file identity held across complete export and I/O')
    check(all(end[k]==candidate[k] for k in fields) and end['event']=='exit' and end['return_value']==0 and end['is_error'] is False,'same original syscall released then actually returned success')
    times=[candidate['monotonic_ns']]+[h['monotonic_ns'] for h in held]+[end['monotonic_ns']]
    check(times==sorted(times) and len(set(times))==4 and times[-1]-times[0]<30_000_000_000,'actual monotonic hold bounded by unchanged 30-second tracer limit')
    matching=[r for r in events if r['event']=='syscall_entry' and r['tid']==candidate['tid'] and r['fd']==candidate['fd'] and r.get('file_inode')==candidate['file_inode']]
    check(len(matching)==1 and matching[0]['syscall_nr']==74,'one original actual syscall entry, not a fake acknowledgement')
    released=[r for r in events if r['event']=='released']
    check(len(released)==1 and held[-1]['monotonic_ns']<released[0]['monotonic_ns']<end['monotonic_ns'],'explicit same-gate release follows final held receipt')
    check(not any(r['event'] in {'abort_cleanup','gate_timeout','process_timeout','failure'} for r in events),'success never uses watchdog/abort release')
    check(any(r['event']=='tracee_exit' and r['exit_code']==0 and r['signal']==0 for r in events) and any(r['event']=='finished' for r in events) and o.raw('server','wait-tracer.txt')==b'0\n','actual traced broker exit and parent tracer wait0')
    for mark in ('before','after'):
        actual=L.saved_proc(o,'server','gate-'+mark,2000,broker_sha)
        check(actual['pid']==candidate['tracee_pid'] and actual['starttime']==candidate['tracee_starttime'],'actual held '+mark+' observation retains the same broker PID/starttime/ELF')
        fds=rows(o.raw('server','proc-gate-'+mark+'-fds.jsonl'))
        fd=L.one(fds,lambda r:int(r['fd'])==candidate['fd'],'same held state.next FD')
        stat=fd['stat'].split(':',4)
        check(fd['target']=='/var/lib/rustusb/agent/state.next' and stat[:4]==[str(candidate['file_device']),str(candidate['file_inode']),'2000','600'] and stat[4]=='regular file','actual broker FD identity maps to held WAL inode')
        status=o.raw('server','proc-gate-'+mark+'.status').decode()
        check(re.search(r'^Uid:\s+2000\s+2000\s+2000\s+2000$',status,re.M) is not None,'actual retained gate owner UID2000')
    committed=o.raw('server','selected-committed.json')
    check(all(o.raw('server','agent-gate-'+mark+'.json')==committed for mark in ('before','after')),'saved before/after source bytes remain the identical original commit')
    check(o.data('server','root-gate-before.json')==o.data('server','root-gate-after.json'),'all original Root capabilities unchanged while diagnostics and I/O complete')
    old=L.decode(committed);new=o.data('server','agent-after-release.json')
    check(stable(old)==stable(new) and not any(a['action'] in ('stop_requested','session_stop_requested') for a in new['audit'] if a['sequence']>old['sequence']),'no diagnostic Stop or owner mutation during held/released observation')
    check(any(a['sequence']==old['sequence']+1 and a['actor_uid']==1000 and a['action']=='denied' for a in new['audit']),'withheld denial commits only after actual fsync return')
    # Complete original exporter wait plus same-UID fresh Begin proves the old
    # metadata slot was released; no raw old token is inserted into the bundle.
    for mark in ('post-export-begin','post-export-end'):
        wire=o.raw('server','rpc-'+mark+'.response.bin')
        check(len(wire)>=4 and struct.unpack('>I',wire[:4])[0]==len(wire)-4 and L.decode(wire[4:])==o.data('server','rpc-'+mark+'.response.json'),'exact framed read-only '+mark+' reply')
    begin=o.data('server','rpc-post-export-begin.response.json')['response']
    ended=o.data('server','rpc-post-export-end.response.json')['response']
    check(begin['result']=='diagnostic_started' and begin['header']['committed_sequence']==old['sequence'] and ended=={'result':'diagnostic_ended'},'same UID can begin/end another metadata snapshot while original WAL is still held')
    req=o.data('server','rpc-post-export-end.request.json')
    check(req['request']['snapshot']==begin['header']['snapshot'] and req['request']['profile']=='server','actual new token End is exact, not a guessed receipt')
    for name in ('export-held-before.uptime','export-held-after.uptime','rpc-post-export-begin.uptime','rpc-post-export-end.uptime'):
        stamp=float(o.raw('server',name).split()[0])*1e9
        check(held[0]['monotonic_ns']-20_000_000<=stamp<=held[-1]['monotonic_ns']+20_000_000,'export/metadata receipt lies inside actual held interval: '+name)
    return {'candidate':selected,'tracee_pid':candidate['tracee_pid'],'tracee_starttime':candidate['tracee_starttime'],'tid':candidate['tid'],'fd':candidate['fd'],'file_device':candidate['file_device'],'file_inode':candidate['file_inode'],'held_nanoseconds':times[-1]-times[0],'metadata_retirement_scope':'complete collector receipt and fresh same-UID Begin/End; no tracing of the original token bytes'}


def check_files(o):
    check=o.check
    for uid in (1000,1002,1003):
        parent=o.data('server',f'file-parent-{uid}.json')
        check(parent['mode']=='700' and parent['uid']==parent['gid']==uid and parent['kind']=='directory' and parent['path']==parent['canonical'],'actual private caller-owned export parent')
    before=o.data('server','file-held.json');after=o.data('server','file-existing.json')
    check(before==after and before['mode']=='600' and before['uid']==before['gid']==1000 and before['links']==1 and before['kind']=='regular file' and before['canonical']==before['path'] and before['bytes']==len(o.raw('server','diagnostic-bundle.json')),'actual artifact private regular single-link inode preserved')
    refused=o.data('server','export-existing.stdout')
    check(o.raw('server','export-existing.wait')==b'1\n' and refused['file']['result']=='failed' and refused['file']['failure']['kind']=='already_exists' and refused['file']['failure']['publication']=='not_created' and refused['file']['failure']['written_bytes']==0,'actual NOREPLACE refusal without replacing original artifact')


def check_configuration(o,keys):
    for role in ('server','client'):
        manifest=o.data(role,'actual-config-1-agent.json')
        check=o.check
        profile_names=['server'] if role=='server' else ['hid','serial']
        profiles=[{'id':'server','backend':'native','config':'/etc/rustusb/server.json'}] if role=='server' else [{'id':name,'backend':'client','config':'/etc/rustusb/client-'+name+'.json'} for name in profile_names]
        expected_manifest={'schema_version':1,'broker_uid':2000,'helper_socket':'/run/rustusb/control.sock','control_directory':'/run/agent','data_directory':'/var/lib/rustusb/agent','profiles':profiles,'operators':[{'uid':1000,'profiles':profile_names,'actions':['read','start','stop','discover','audit']},{'uid':1002,'profiles':profile_names,'actions':['read']},{'uid':1003,'profiles':profile_names,'actions':['audit']}]}
        check(manifest==expected_manifest,'actual exact root-controlled manifest and operator read/audit separation '+role)
        policy=o.data(role,'actual-config-1-policy.json')
        expected_policy={'allowed_uids':[1000],'export_device_ids':[],'peers':[],'trusted_network':False,'secure_broker_uid':2000}
        if role=='server':expected_policy['secure_native_device_ids']=[k.split(':',1)[1] for k in keys]
        else:expected_policy['secure_imports']=[{'server_id':'qemu-server','device_keys':keys}]
        check(policy==expected_policy,'actual dedicated helper role and exact secure device pool '+role)
        metadata=o.data(role,'config-metadata-1.json')
        for row in metadata:
            check(row['path']==row['canonical'] and row['inode']>0,'actual protected config identity')
            if row['kind']=='private_key_metadata_only': check(row['uid']==row['gid']==2000 and row['mode']=='600' and row['captured_file'] is None and row['links']==1,'private credential metadata only')
            elif row['kind']=='public_file': check(row['uid']==row['gid']==0 and row['mode']=='644' and row['links']==1 and row['bytes']==len(o.raw(role,row['captured_file'])),'actual root-controlled public config')
            else: check(row['kind']=='directory' and int(row['mode'],8)&0o022==0,'protected trusted ancestors')
        for name in ('ca','server','client'):
            check(o.raw(role,'actual-config-1-'+name+'.der')==(o.evidence/(name+'-public.der')).read_bytes(),'actual public certificate DER '+role+'/'+name)
        pin=hashlib.sha256((o.evidence/'client-public.der').read_bytes()).hexdigest()
        check(o.raw(role,'actual-config-1-client-pin.txt')==(pin+'\n').encode(),'actual public client pin')
        names=['server'] if role=='server' else ['client-hid','client-serial']
        for name in names:
            config=o.data(role,'actual-config-1-'+name+'.json')
            expected={'schema_version':2,'data_link':L.DATA_LINK,'broker_uid':2000,'ca_certificates':['/etc/rustusb/ca.der'],'certificate_chain':['/etc/rustusb/'+role+'.der'],'private_key':'/etc/rustusb/'+role+'-key.der'}
            if role=='server':expected.update(listen='192.0.2.1:3241',devices=keys,principals=[{'id':'qemu-client','pins':[pin],'devices':keys}])
            else:expected.update(server_id='qemu-server',endpoint={'host':'192.0.2.1','port':3241},server_name='rustusb-qemu.test',device=keys[0 if name=='client-hid' else 1])
            check(config==expected,'actual exact framed-v2 credential/endpoint/key policy '+role+'/'+name)
        check(not any(p.name.endswith('-key.der') for p in (o.evidence/(role+'-artifacts')).iterdir()),'no private key bytes collected')


def verify(evidence,summary):
    o=L.Originals(evidence)
    try:
        keys=o.data('server','keys.json');baseline=o.data('server','host-before.json')
        check_configuration(o,keys)
        for role in ('server','client'):
            for owner,uid,program in [('helper',0,'rustusb-helper'),('agent-1',2000,'rustusb')]:
                status=o.raw(role,'identity-'+owner+'.txt').decode()
                o.check(re.search(r'^Uid:\s+'+r'\s+'.join([str(uid)]*4)+r'$',status,re.M) is not None,'actual '+role+'/'+owner+' UID')
                o.check(o.raw(role,'executable-'+owner+'.txt').decode().split()[0]==summary['binary_sha256'][program],'actual '+role+'/'+owner+' frozen ELF')
            ready=o.data(role,'network-ready.json');o.check(ready['role']==role and ready['carrier']=='1' and ready['operstate']=='up','actual dual-guest link readiness '+role)
            o.check(o.raw(role,'wait-helper.txt')==b'0\n','actual privileged helper wait0 '+role)
        for role in ('server','client'):
            actual=L.saved_proc(o,role,'agent-start',2000,summary['binary_sha256']['rustusb'])
            root=o.data(role,'root-held-1.json')
            owners=[r['owner'] for r in (root['exports'] if role=='server' else root['records'])]
            o.check(len(owners)==2 and all(r['uid']==2000 and r['pid']==actual['pid'] and r['starttime']==actual['starttime'] for r in owners),'actual Root owner PID/starttime bound to retained nonroot broker '+role)
        tracer=L.saved_proc(o,'server','tracer-start',0,summary['payload_sha256']['server']['usr/bin/prepare-gate'])
        gate_ready=o.data('server','gate-ready.json')
        o.check(gate_ready['tracer_pid']==tracer['pid'] and gate_ready['tracee_pid']==L.saved_proc(o,'server','agent-start',2000,summary['binary_sha256']['rustusb'])['pid'],'actual root-only tracer and tracee process relation')
        L.saved_proc(o,'server','export-held',1000,summary['binary_sha256']['rustusb'])
        export_status=o.raw('server','proc-export-held.status').decode()
        o.check(all(re.search(r'^'+cap+r':\s+0000000000000000$',export_status,re.M) is not None for cap in ('CapInh','CapPrm','CapEff','CapAmb')),'actual exporter has no inherited/permitted/effective/ambient capabilities')
        peer=L.client_first_peer_closes(o,1)
        lifecycle=L.check_epoch(o,1,keys,baseline,server_peer_closes=peer)
        o.check(o.data('server','host-gate-after.json')==o.data('server','host-held-1.json'),'original physical hardware generation/configuration/IF remains USED after export and second I/O')
        o.check(o.data('client','root-preserved.json')==o.data('client','root-held-1.json'),'client original two local generations and Root capabilities unchanged')
        for role,name in [('server','agent-gate-before.json'),('server','agent-gate-after.json'),('client','agent-preserved.json')]:
            state=o.data(role,name);held=o.data(role,'agent-held-1.json')
            o.check(stable(state)==stable(held),'no Stop/resource or session replacement during diagnostic '+role+'/'+name)
        event=o.raw('client','input-2.bin');o.check(bool(event) and len(event)%24==0,'complete actual second evdev stream')
        events=[struct.unpack('<qqHHi',event[i:i+24])[2:] for i in range(0,len(event),24)]
        o.check((1,3,1) in events and (1,3,0) in events,'same actual HID receives KEY2 after export')
        expected_tx=b''.join(f'RUSTUSB-TX-{i}-0001:fedcba9876543210\n'.encode() for i in (1,2))
        o.check((Path(evidence)/'ftdi-host-rx.bin').read_bytes()==expected_tx and o.raw('client','tx-2.bin')==expected_tx[35:] and o.raw('client','rx-2.bin')==(Path(evidence)/'ftdi-host-injected-2.bin').read_bytes()==b'RUSTUSB-RX-2-0001:0123456789abcdef\n','actual same FTDI transfers exactly 70 bytes each direction across capture')
        controls=rows((Path(evidence)/'host-control.jsonl').read_bytes())
        commands=[r['command'] for r in controls]
        o.check(commands.index('OBSERVE1')<commands.index('IO2')<commands.index('IO2_COMPLETE')<commands.index('CLOSE1')<commands.index('STOP1'),'host actual I/O/control barriers keep cleanup after export/second I/O/release')
        facts={'projection':check_projection(o),'gate':check_gate(o,summary['binary_sha256']['rustusb']),'lifecycle':lifecycle,'scope':'one Native-v4/framed-v2 epoch with two real I/O rounds; reused source-locked lifecycle predicates; removable syscall-entry gate, not hard-kernel blocking/hardware; same-token End code receipt plus new same-UID Begin/End, not a raw trace of old token'}
        check_files(o)
        return {'checks':o.checks,'facts':facts,'source_artifacts':o.files}
    except Exception as error:
        (Path(evidence)/'diagnostic-verifier-failure.json').write_text(json.dumps({'error':str(error),'checks':o.checks,'source_artifacts':o.files},indent=2)+'\n')
        raise
