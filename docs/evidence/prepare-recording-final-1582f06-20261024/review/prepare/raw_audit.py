"""Author's separate original-byte lifecycle audit; no harness imports or calls.

Reads only completed original runs. This is a separate parsing method by the
original harness author, not an independently implemented acceptance suite.
"""
import argparse
import base64
import copy
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import struct

MODES = [(c,s) for c in ('native-resource','usbfs-resource','usbfs-initializer') for s in ('job','term')] + [('native-resource','session'),('native-available','session')]
EXPECTED = {
    'rustusb':'72adb291645fdcf3cb41c1ff9e9e781fe9eed344730a4444407f983c9b63e5f2',
    'rustusb-helper':'b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5'}
PROOF = {'native_import':'native_import_released','native_export':'native_export_restored','native_stub':'native_stub_available','usbfs_grant':'physical_baseline_restored'}
def stamp(data):
    return {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
def read_json(p):
    return json.loads(p.read_bytes())
def all_files(root):
    return {str(p.relative_to(root)):stamp(p.read_bytes()) for p in sorted(root.rglob('*')) if p.is_file()}
def active(root):
    return root.get('exports',[]) + root['records']
def proof_rows(root):
    return [r.get('terminal',r) for r in root['terminals']]
def expected_proof(session,attempt):
    return dict(resource_id=attempt['resource_id'],kind=attempt['kind'],device_key=session['device_key'],owner_uid=2000,correlation=attempt['correlation'],proof=PROOF[attempt['kind']])
def sorted_rows(rows):
    return sorted(rows,key=lambda r:json.dumps(r,sort_keys=True))
def listened(data):
    return any(p[3]=='0A' and p[1].endswith(':0CA9') for line in data.decode().splitlines() if len(p:=line.split())>3)

class Review:
    def __init__(self,evidence,head):
        self.e=evidence; self.head=head; self.checks=[]; self.details={}
        self.summary=read_json(evidence/'summary.json')
        if not self.summary.get('completed_utc'):
            raise RuntimeError('Refusing to review nonterminal run '+str(evidence))
        self.before=all_files(evidence)
        self.case=self.summary['case']; self.stop=self.summary['stop']; self.native=self.summary['backend']=='native'
    def check(self,ok,label):
        self.checks.append({'check':label,'passed':bool(ok)})
        if not ok:
            raise RuntimeError(label)
    def raw(self,role,name):return (self.e/(role+'-artifacts')/name).read_bytes()
    def j(self,role,name):return json.loads(self.raw(role,name))
    def s(self,name):return self.j('server',name)
    def lines(self,name):return [json.loads(x) for x in self.raw('server',name).splitlines()]
    def proc(self,mark):
        text=self.raw('server','owner-stat-'+mark+'.txt').decode()
        pid=int(text.split('(',1)[0]);rest=text.rsplit(')',1)[1].split()
        self.check(pid==self.candidate['tracee_pid'] and int(rest[19])==self.candidate['tracee_starttime'],'actual /proc owner identity '+mark)
    def provenance(self):
        s=self.summary
        self.check(s['result']=='PASS' and s['head']==s['head_after']==self.head,'terminal PASS belongs to exact original clean head')
        self.check(s['guest_exit_codes']=={'server':0,'client':0},'two actual guests exited zero')
        self.check(all(s[k] for k in ('source_guard','binary_guard','head_guard','clean_guard')),'four original source/ELF/head/clean guards')
        self.check(s['binary_sha256']==EXPECTED,'exact software-gate ELF pair')
        counts={};warnings={}
        for role in ('server','client'):
            raw=(self.e/(role+'-evidence.log')).read_bytes();text=raw.decode()
            frames=list(re.finditer(r'^SECURE_ARTIFACT_BEGIN ([a-zA-Z0-9_.-]+) ([0-9]+)\n(.*?)^SECURE_ARTIFACT_END \1\s*$',text,re.M|re.S))
            self.check(text.count('SECURE_ARTIFACT_BEGIN ')==len(frames),role+' complete base64 framing')
            names=set()
            for match in frames:
                name,size,encoded=match.groups();self.check(name not in names,role+' unique raw frame '+name);names.add(name)
                data=base64.b64decode(''.join(encoded.split()),validate=True)
                self.check(len(data)==int(size) and data==self.raw(role,name),role+' raw serial/base64 original equality '+name)
                self.check(s['guest_artifacts'][role][name]==stamp(data),role+' raw artifact declared hash '+name)
            self.check(names==set(s['guest_artifacts'][role]),role+' exact complete declared artifact set');counts[role]=len(names)
            self.check('RUSTUSB_PREPARE_RESULT: PASS role='+role in text and 'RUSTUSB_PREPARE_RESULT: FAIL' not in text,role+' actual guest verdict separate from process exit')
            kernel=(self.e/(role+'-serial.log')).read_text(errors='replace')
            self.check('Kernel panic' not in kernel and 'Oops:' not in kernel,role+' final complete S0 no panic/Oops')
            warnings[role]=[line for line in kernel.splitlines() if any(t in line.lower() for t in ('warning','error','reset','not attached'))]
            for who in ['helper','agent-1']+(['agent-2'] if role=='server' else []):
                fields=dict(line.split(':',1) for line in self.raw(role,'identity-'+who+'.txt').decode().splitlines() if ':' in line)
                self.check(fields['Uid'].split()==(['0']*4 if who=='helper' else ['2000']*4),role+'/'+who+' actual four UID fields')
                if who!='helper':self.check(all(int(fields[k],16)==0 for k in ('CapInh','CapPrm','CapEff','CapAmb')),role+'/'+who+' actual unprivileged capability masks')
                program='rustusb-helper' if who=='helper' else 'rustusb'
                self.check(self.raw(role,'executable-'+who+'.txt').decode().split()[0]==EXPECTED[program],role+'/'+who+' actual proc executable SHA')
            self.check(stamp((self.e/(role+'-generated-init.sh')).read_bytes())['sha256']==s['payload_sha256'][role]['init'],role+' actual generated init bytes bound to payload')
        for name,value in s['source_inputs'].items():self.check(stamp((self.e/'source'/name).read_bytes())==value,'copied frozen source '+name)
        self.check(self.raw('server','executable-gate.txt').decode().split()[0]==s['probe_sha256'],'actual guest tracer executable SHA')
        f=dict(line.split(':',1) for line in self.raw('server','identity-gate.txt').decode().splitlines() if ':' in line)
        self.check(f['Uid'].split()==['0']*4,'actual guest tracer root identity')
        self.details.update(artifact_counts=counts,source_count=len(s['source_inputs']),kernel_lines_retained_without_reclassifying=warnings)
    def original_gate(self):
        self.selection=self.s('selection.json');self.sessions=self.s('selected-sessions.json');self.held=self.s('root-held.json');self.baseline=self.s('host-before.json')
        self.candidate=self.s('gate-candidate-'+str(self.selection['candidate'])+'.json');self.ended=self.s('gate-exit-'+str(self.selection['candidate'])+'.json')
        self.pre=self.s('gate-held-before.json');self.post=self.s('gate-held-after.json');self.events=self.lines('gate-events.jsonl')
        a=self.s('gate-armed.json');r=self.s('gate-ready.json')
        self.check(self.selection['case']==self.case and self.selection['stop']==self.stop,'selected actual operation and stop mode')
        self.check([e for e in self.events if e['event']=='armed']==[a] and a['nonce']=='prepare-stop-v1','unique actual tracer arming acknowledgement')
        self.check(r['tracee_pid']==a['tracee_pid']==self.candidate['tracee_pid'] and r['tracee_starttime']==a['tracee_starttime']==self.candidate['tracee_starttime'] and r['monotonic_ns']<a['monotonic_ns']<self.candidate['monotonic_ns'],'same real owner armed before selected syscall')
        identity=('candidate','tracee_pid','tracee_starttime','tid','fd','request','file_device','file_inode')
        self.check(all(self.candidate[k]==self.pre[k]==self.post[k]==self.ended[k] for k in identity),'same original syscall/TID/FD/file generation across hold and return')
        release=[e for e in self.events if e['event']=='released']
        self.check(len(release)==1 and self.pre['held'] is True and self.post['held'] is True and self.candidate['monotonic_ns']<=self.pre['monotonic_ns']<self.post['monotonic_ns']<release[0]['monotonic_ns']<self.ended['monotonic_ns'],'actual held receipts bracket observations before exactly one explicit release')
        self.check(self.ended['return_value']==(1 if self.case=='usbfs-initializer' else 0) and self.ended['is_error'] is False and 0<self.ended['monotonic_ns']-self.candidate['monotonic_ns']<30_000_000_000,'same syscall actually completes successfully within test gate budget')
        self.check(not any(e['event'] in ('failure','abort_cleanup','trace_event_error') for e in self.events),'no emergency release or tracer error masquerades as completion')
        self.check(self.events[-1]['event']=='finished' and self.events[-1]['tracee_exit_code']==0 and self.raw('server','wait-tracer.txt')==b'0\n','actual original traced owner plus tracer reaped')
        term=[e for e in self.events if e['event']=='signal_delivery' and e.get('signal')==15 and self.pre['monotonic_ns']<e['monotonic_ns']<self.post['monotonic_ns']]
        self.check(bool(term)==(self.stop=='term'),'actual held-window SIGTERM versus authorized IPC stop distinguished')
        self.check(len(self.sessions)==2 and len(active(self.held))==2 and self.held['terminals']==[],'two actual original accepted capabilities, zero terminals')
        self.check(len({s['job_id'] for s in self.sessions})==1 and self.sessions[0]['job_id']==self.selection['job'] and len({s['id'] for s in self.sessions})==2,'two original sessions belong to exact same job')
        self.accepted={r['lease_id']:r for r in active(self.held)}
        for session in self.sessions:
            self.check(session['role']=='export' and len(session['attempts'])==1,'one actual export attempt '+session['id'])
            attempt=session['attempts'][0];record=self.accepted[attempt['resource_id']];owner=record['owner']
            self.check(record['correlation']==attempt['correlation'] and owner['uid']==2000 and owner['pid']==self.candidate['tracee_pid'] and owner.get('starttime',owner.get('start_time'))==self.candidate['tracee_starttime'],'exact accepted correlation and actual process generation '+session['id'])
            self.check(attempt['correlation']['session_id']==session['id'] and attempt['correlation']['instance_id']==session['instance_id'],'attempt correlation belongs to same actual session/epoch')
            if self.native:self.check(record['stub'] is None and record['bound'] is not None and record['registration']['device_key']==session['device_key'],'real Native bound export without IMPORT '+session['id'])
            else:self.check(session['device_key']=='physical:'+record['baseline']['device_id'] and record['recovery']=='manual_only','exact physical pool key and no automatic recovery authorization')
        self.check(len(self.baseline)==2 and {x['vendor'] for x in self.baseline}=={'0627','0403'} and {x['interfaces'][0]['driver'] for x in self.baseline}=={'usbhid','ftdi_sio'},'complete nonempty original HID/FTDI driver baseline')
        self.proc('held');self.proc('before-release')
        selected=self.s('selected-state.json')
        if self.case!='usbfs-initializer':
            old=self.s('state-before-ack.json')
            self.check(selected['sequence']==old['sequence']+1,'original selected fsync holds the next unpublished WAL sequence')
            if self.case=='native-available':
                self.check(selected['audit'][-1]['action']=='observe' and selected['audit'][-1]['state']=='Available' and all(s['attempts'][0]['resource_id'] for s in old['sessions']),'actual Available transition after both Resource acknowledgements')
            else:self.check(selected['audit'][-1]['action']=='resource' and sum(a['resource_id'] is not None for s in old['sessions'] for a in s['attempts'])==1,'actual second Resource ACK held after first acknowledged owner')
            self.check(self.candidate['operation']=='resource' and self.candidate['request']==74,'actual fsync entry, not fabricated application acknowledgement')
        held_host=self.s('host-held.json')
        if self.native:
            self.check(all(x['configuration']==x['num_interfaces']=='' and x['configuration_bytes']==x['count_bytes']==0 and x['interfaces']==[] and x['device_driver']=='usbip-host' and x['usbip_status']==1 for x in held_host),'actual Native export phase unconfigured and AVAILABLE')
        else:
            self.check(held_host==self.baseline,'USBFS accepted source preserves full original baseline before claims')
            self.usb_fd_map(self.s('fds-held.json'),'held')
        self.details['gate']={'candidate':self.selection['candidate'],'pid':self.candidate['tracee_pid'],'starttime':self.candidate['tracee_starttime'],'tid':self.candidate['tid'],'fd':self.candidate['fd'],'before_ns':self.pre['monotonic_ns'],'after_ns':self.post['monotonic_ns'],'release_ns':release[0]['monotonic_ns'],'actual_return':self.ended['return_value']}
    def usb_fd_map(self,fds,label):
        usb=[x for x in fds if x['target'].startswith('/dev/bus/usb/')];nodes=self.s('usb-nodes-before.json')
        self.check(len(usb)==len(nodes)==2,label+' exactly two observed USB character FDs and original nodes')
        used=set()
        for record in self.accepted.values():
            base=record['baseline'];receipt=base['receipt'];bus=base['bus_id'];host=next(x for x in self.baseline if x['bus']==bus);node=next(x for x in nodes if x['bus']==bus)
            path='/dev/bus/usb/%03d/%03d'%(receipt['bus_number'],receipt['usb_device_number']);matches=[x for x in usb if x['target']==path]
            self.check(len(matches)==1 and node['node']==path,label+' unique actual USB node '+bus);fd=matches[0];used.add(fd['fd'])
            rdev=tuple(int(x,16) for x in fd['rdev'].split(':'))
            self.check(rdev==tuple(int(x,16) for x in node['rdev'].split(':'))==tuple(int(x) for x in node['sysfs_dev'].split(':')) and os.makedev(*rdev)==receipt['node_device'],label+' actual st_rdev/major/minor chain '+bus)
            self.check(int(fd['device'])==int(node['filesystem_device']) and int(fd['inode'])==int(node['inode'])==receipt['node_inode'],label+' actual FD/node filesystem generation '+bus)
            self.check(node['character_device'] is True and node['uid']==0 and node['mode']=='600' and node['links']==1,label+' protected original node '+bus)
            self.check(tuple(map(int,host['generation'].split(':')))==tuple(map(int,node['generation'].split(':')))==(receipt['sysfs_device'],receipt['sysfs_inode']),label+' exact original sysfs generation '+bus)
            self.check((int(host['busnum']),int(host['devnum']))==(receipt['bus_number'],receipt['usb_device_number']) and int(bus.split('-',1)[0])==receipt['bus_number'],label+' original bus/address '+bus)
            self.check(base['configuration']==int(host['configuration']) and base['interfaces']==[{'number':int(i['number'],16),'alternate':int(i['alt']),'driver':i['driver']} for i in host['interfaces']],label+' original complete configuration/interface/driver receipt '+bus)
        self.check(len(used)==2,label+' bijection of both actual capability FDs')
    def expected_initial(self):return [expected_proof(s,s['attempts'][0]) for s in self.sessions]
    def all_initial_retired(self,root):
        return not active(root) and sorted_rows(proof_rows(root))==sorted_rows(self.expected_initial())
    def global_flow(self):
        rows=self.lines('held-observations.jsonl');good=[]
        self.check([r['observation'] for r in rows]==list(range(1,len(rows)+1)) and 0<len(rows)<=500,'contiguous actual held samples')
        for row in rows:
            n=row['observation'];root=self.s('root-observe-'+str(n)+'.json');host=self.s('host-observe-'+str(n)+'.json');fds=self.s('fds-observe-'+str(n)+'.json')
            proof=all(p in proof_rows(root) and not any(r['lease_id']==p['resource_id'] for r in active(root)) for p in self.expected_initial())
            closed=not any(x['target'].startswith('/dev/bus/usb/') for x in fds)
            self.check(row['baseline_equal']==(host==self.baseline) and row['target_fd_closed']==closed and row['exact_root_terminal']==proof and row['gate_released'] is False,'saved sample itself determines every global observation '+str(n))
            self.proc('observe-'+str(n))
            if proof and closed and host==self.baseline:good.append(n)
            if self.case=='usbfs-initializer':
                self.check(root==self.held and host==self.baseline and not closed and not proof,'same accepted initializer resources retained before actual syscall return '+str(n));self.usb_fd_map(fds,'initializer '+str(n))
        self.check(self.s('held-outcome.json')['failed']==0,'actual held-stage verdict zero')
        if self.case=='usbfs-initializer':
            self.check(not good and self.s('held-outcome.json')['resource_released_while_gate_held'] is False,'initializer is retained through hold; early closure is not fabricated')
            c=self.candidate;hid=next(x for x in self.s('usb-nodes-before.json') if x['bus']==next(h['bus'] for h in self.baseline if h['vendor']=='0627'));fd=next(x for x in self.s('fds-held.json') if x['target']==hid['node'])
            self.check(c['operation']=='initializer' and c['request']!=74 and c['fd']==int(fd['fd']) and c['node_device']==int(fd['device']) and c['node_inode']==int(fd['inode']) and c['node_rdev']==os.makedev(*(int(x,16) for x in fd['rdev'].split(':'))),'actual GET_CONFIGURATION entry belongs to received HID FD')
            self.check(not any(x['event']=='syscall_entry' and x.get('kind') in ('claim','configuration','alternate','release','driver') and x['monotonic_ns']<self.ended['monotonic_ns'] for x in self.events),'no observed initializer host-state mutation before original entry resumes')
        else:
            self.check(bool(good) and self.s('held-outcome.json')['resource_released_while_gate_held'] is True,'both actual resources and complete baseline released before original Resource ACK')
            self.check(self.all_initial_retired(self.s('root-before-release.json')) and self.s('host-before-release.json')==self.baseline,'independent before-release samples retain both exact proofs and full baseline')
        self.check(self.all_initial_retired(self.s('root-after-release.json')) and self.s('host-after-release.json')==self.baseline,'original same-call return followed by exact cleanup/Finish')
        self.check(not listened(self.raw('server','listeners-held.txt')) and b'Connection refused' in self.raw('client','held-connect.stderr') and self.raw('client','held-connect.wait')==b'1\n','real TCP refusal and no listener before prepared pool binds')
        if self.stop=='job':
            audit=self.s('agent-after-release.json')['audit'];self.check(any(x['action']=='stop' and x['actor_uid']==1000 and x['job_id']==self.selection['job'] for x in audit),'actual authorized StopJob exact target audit')
        self.details['held_observations']=len(rows);self.details['actual_release_samples']=good
    def key(self,role,name,label):
        data=self.raw(role,name);self.check(bool(data) and len(data)%24==0,label+' complete actual evdev records')
        decoded=[struct.unpack('<qqHHi',data[i:i+24])[2:] for i in range(0,len(data),24)]
        self.check((1,2,1) in decoded and (1,2,0) in decoded,label+' actual KEY1 down/up')
    def local_flow(self):
        target=self.s('local-target.json');survivor=self.s('local-survivor.json');keys=self.s('keys.json')
        self.check(target['device_key']==keys[1] and survivor['device_key']==keys[0] and target['id']!=survivor['id'],'actual second serial target and original HID survivor')
        original=self.accepted[survivor['attempts'][0]['resource_id']];targetproof=expected_proof(target,target['attempts'][0]);hostheld=self.s('host-held.json')
        hidbus=original['registration']['generation']['bus_id'];wanted=[copy.deepcopy(next(x for x in (hostheld if h['bus']==hidbus else self.baseline) if x['bus']==h['bus'])) for h in self.baseline]
        def local_root(r):return r['records']==[] and r['exports']==[original] and proof_rows(r)==[targetproof]
        rows=self.lines('local-held-observations.jsonl');good=[]
        self.check(bool(rows) and [r['observation'] for r in rows]==list(range(1,len(rows)+1)),'nonempty contiguous actual local observations')
        for row in rows:
            n=row['observation'];r=self.s('root-observe-'+str(n)+'.json');h=self.s('host-observe-'+str(n)+'.json')
            self.check(row['target_exact_terminal_and_original_sibling']==local_root(r) and row['target_restored_and_sibling_bound']==(h==wanted) and row['gate_released'] is False,'saved sample determines target-only local result '+str(n))
            self.proc('observe-'+str(n))
            if local_root(r) and h==wanted:good.append(n)
        self.check(bool(good) and self.s('local-held-outcome.json')['failed']==0,'actual target closes while original HID owner survives held ACK')
        self.check(local_root(self.s('root-local-after-ack.json')) and self.s('host-local-after-ack.json')==wanted,'original sibling still exact after same ACK returns')
        self.check(listened(self.raw('server','listeners-held.txt'))==(self.case=='native-available'),'actual Resource/Available listener boundary differs')
        self.check(self.raw('client','held-connect.wait')==(b'0\n' if self.case=='native-available' else b'1\n'),'actual corresponding TCP connection result')
        jobs=self.s('local-jobs.json')['response']['jobs'];self.check(len(jobs)==1 and jobs[0]['id']==self.selection['job'] and jobs[0]['state']=='running','same original job remains running after local stop')
        catalog=self.j('client','local-catalog.json')['response'];self.check(catalog['result']=='catalog' and catalog['devices']==[{'device_key':survivor['device_key'],'device':original['registration']['published_device']}],'real authenticated catalog contains only original surviving HID publication')
        denied=self.j('client','local-denied-sessions.json')['response']['sessions'];self.check(len(denied)==1 and denied[0]['device_key']==target['device_key'] and denied[0]['operation_error']=='permission' and denied[0]['cleanup']=='not_started' and denied[0]['terminal'] and denied[0]['attempts']==[],'real ordinary start rejects cancelled serial without fake helper attempt/proof')
        self.check(self.raw('client','local-before-denied-root.json')==self.raw('client','local-after-denied-root.json'),'actual refusal leaves helper store byte-identical')
        self.key('client','local-survivor-input.bin','original surviving HID')
        during=self.s('root-local-survivor-io.json');savedstub=self.s('local-detach-stub.json');live=copy.deepcopy(during['exports'][0]);stub=live.pop('stub');without=copy.deepcopy(original);without.pop('stub')
        self.check(live==without and stub==savedstub and stub['state']['phase']=='observed' and proof_rows(during)==[targetproof],'same original export owns exact actually used stub')
        imported=self.j('client','local-survivor-receipt.json')['records'];self.check(len(imported)==1 and imported[0]['state']['phase']=='observed' and imported[0]['state']['receipt']['session']['status']==6 and imported[0]['target']['device_key']==survivor['device_key'],'surviving original HID truly enumerated through VHCI')
        client_session=self.j('client','local-survivor-sessions.json')['response']['sessions'][0];detached=self.j('client','root-local-detached.json')
        self.check(not active(detached) and proof_rows(detached)==[expected_proof(client_session,client_session['attempts'][0])],'actual client detach has exact original import proof')
        expectedhost=copy.deepcopy(self.baseline)
        for row in expectedhost:
            if row['bus']==hidbus:
                row.update(device_driver='usbip-host',usbip_status=1)
                for interface in row['interfaces']:interface['driver']=''
        stubproof={'resource_id':stub['channel_id'],'kind':'native_stub','device_key':survivor['device_key'],'owner_uid':2000,'correlation':stub['correlation'],'proof':'native_stub_available'}
        def server_detached(r):return r['records']==[] and r['exports']==[original] and sorted_rows(proof_rows(r))==sorted_rows([targetproof,stubproof])
        polls=self.lines('local-detach-observations.jsonl');barrier=self.s('local-detach-barrier.json');start=Decimal(barrier['started_uptime']);previous=start
        self.check(len(polls)==barrier['observations'] and 0<len(polls)<=500 and barrier['limit_seconds']==15 and barrier['maximum_observations']==500,'actual bounded server read-only detach barrier')
        for i,row in enumerate(polls,1):
            self.check(row['observation']==i,'contiguous server detach original sample '+str(i));r=self.s('root-local-detach-poll-'+str(i)+'.json');h=self.s('host-local-detach-poll-'+str(i)+'.json');app=self.s('agent-local-detach-poll-'+str(i)+'.json');now=Decimal(row['uptime'])
            self.check(row['root_retired_exact']==server_detached(r) and row['host_available_original']==(h==expectedhost) and row['within_bound']==(start<=now<=start+15) and previous<=now,'exact saved server stub/generation/config proof '+str(i))
            self.check(row['read_only'] is True and row['last_export_stop_sent'] is False and not any(a['action']=='stop' and a['session_id']==survivor['id'] for a in app['audit']),'no last export stop precedes server retirement sample '+str(i));previous=now
        self.check(barrier['retired'] is True and all(polls[-1][k] is True for k in ('root_retired_exact','host_available_original','within_bound')) and Decimal(barrier['completed_uptime'])==previous,'server original stub genuinely retired before last export stop')
        n=len(polls);self.check(self.raw('server','root-local-after-detach.json')==self.raw('server','root-local-detach-poll-'+str(n)+'.json') and self.raw('server','host-local-after-detach.json')==self.raw('server','host-local-detach-poll-'+str(n)+'.json'),'accepted original sample byte identity forbids post-stop backfill')
        self.check(Decimal(self.raw('server','local-last-export-stop-uptime.txt').decode().strip())>=previous,'actual final export stop starts after accepted sample')
        text=(self.e/'server-evidence.log').read_text();marks=['PREPARE_LOCAL_STUB_RETIRED\n','PREPARE_LOCAL_LAST_EXPORT_STOP_REQUEST\n','PREPARE_LOCAL_EMPTY_READY\n']
        self.check(all(text.count(m)==1 for m in marks) and [text.index(m) for m in marks]==sorted(text.index(m) for m in marks),'actual server retirement/last-stop/empty-ready marker order')
        empty=self.s('root-local-empty.json');self.check(not active(empty) and sorted_rows(proof_rows(empty))==sorted_rows([targetproof,stubproof,expected_proof(survivor,survivor['attempts'][0])]) and self.s('host-local-empty.json')==self.baseline,'both individual exports fully restore while original job persists')
        jobs=self.s('local-empty-jobs.json')['response']['jobs'];self.check(len(jobs)==1 and jobs[0]['id']==self.selection['job'] and jobs[0]['state']=='running' and listened(self.raw('server','local-empty-listeners.txt')),'same job and actual listener survive cancellation of all exports')
        catalog=self.j('client','local-empty-catalog.json')['response'];self.check(catalog['result']=='catalog' and catalog['devices']==[],'real authenticated empty catalog on original listener')
        self.details.update(held_observations=len(rows),actual_release_samples=good,detach_read_samples=len(polls),target_session=target['id'],surviving_session=survivor['id'])
    def final_reuse(self):
        state=self.s('agent-reuse-ready.json');fresh=[s for s in state['sessions'] if not s['terminal'] and s['role']=='export'];old_ids={s['attempts'][0]['resource_id'] for s in self.sessions}
        self.check(len(fresh)==2 and {s['device_key'] for s in fresh}=={s['device_key'] for s in self.sessions} and {s['attempts'][0]['resource_id'] for s in fresh}.isdisjoint(old_ids),'fresh actual two-device grants use new identities for same original pool')
        receipt=self.j('client','client-receipts-reuse.json')['records'];self.check(len(receipt)==2 and {r['target']['device_key'] for r in receipt}=={s['device_key'] for s in self.sessions} and all(r['state']['phase']=='observed' and r['state']['receipt']['session']['status']==6 for r in receipt),'fresh actual two-device VHCI enumeration receipts')
        self.check(self.raw('client','tx-1.bin')==(self.e/'ftdi-host-rx.bin').read_bytes()==b'RUSTUSB-TX-1-0001:fedcba9876543210\n','fresh real FTDI 35 bytes guest to host')
        self.check(self.raw('client','rx-1.bin')==(self.e/'ftdi-host-injected-1.bin').read_bytes()==b'RUSTUSB-RX-1-0001:0123456789abcdef\n','fresh real FTDI 35 bytes host to guest')
        self.key('client','input-1.bin','fresh HID')
        self.check(self.s('host-final.json')==self.baseline,'final exact full original device/config/interface/driver baseline')
        self.check(self.raw('server','wait-agent-2.txt')==self.raw('client','wait-agent-1.txt')==b'0\n','fresh server/client owner processes actually reaped zero')
        self.details['history']={}
        for role in ('server','client'):
            state=self.j(role,'agent-final.json');root=self.j(role,'root-final.json');sessions=state['sessions'];attempts=[(s,a) for s in sessions for a in s['attempts']]
            jobs_expected=2 if role=='server' or self.stop!='session' else 4
            session_expected=(7 if self.stop=='session' else 6) if role=='server' else (4 if self.stop=='session' else 2)
            proof_expected=(7 if self.stop=='session' else (6 if self.native else 4)) if role=='server' else (3 if self.stop=='session' else 2)
            self.check(len(state['jobs'])==jobs_expected and all(j['state']=='finished' for j in state['jobs']) and len(sessions)==session_expected and all(s['terminal'] for s in sessions),role+' exact retained job/session history')
            self.check(not active(root) and len(proof_rows(root))==len(attempts)==proof_expected,role+' exact nonempty terminal-proof cardinality and no actual live root owners')
            for session,attempt in attempts:self.check(session['cleanup']=='verified' and proof_rows(root).count(expected_proof(session,attempt))==1,role+' exact kind/UID/key/correlation/resource proof '+session['id'])
            if role=='server':self.check({s['id'] for s in self.sessions}.issubset({s['id'] for s in sessions}),'original session identities preserved into final history')
            self.details['history'][role]={'jobs':len(state['jobs']),'sessions':len(sessions),'actual_attempts':len(attempts),'root_terminals':len(proof_rows(root))}
        for name in ['vhci-final.txt']+(['local-detached-vhci.txt','local-vhci-empty.txt'] if self.stop=='session' else []):
            rows=[line.split() for line in self.raw('client',name).decode().splitlines() if line.split() and line.split()[0] in ('hs','ss')]
            self.check(bool(rows) and all(int(row[2])==4 for row in rows),'actual kernel VHCI NULL '+name)
    def run(self):
        self.provenance();self.original_gate()
        if self.stop=='session':self.local_flow()
        else:self.global_flow()
        self.final_reuse()
        self.check(all_files(self.e)==self.before,'every original byte/length/SHA unchanged through review')
        return {'case':self.case,'stop':self.stop,'directory':str(self.e),'runtime_head':self.head,'runtime_result':self.summary['result'],'completed_utc':self.summary['completed_utc'],'review_passed':True,'checks':self.checks,'diagnostic_count':len(self.checks),'details':self.details,'original_file_count':len(self.before)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--evidence',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--only',help='one completed directory for a separately labelled partial review');args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    head=read_json(args.evidence/'start.json')['head'];names=['prepare-'+c+'-'+s for c,s in MODES]
    if args.only:
        if args.only not in names:raise RuntimeError('unknown prepare scenario')
        names=[args.only]
    # Reject pending/missing groups before opening any complete-report verdict.
    for name in names:
        summary=read_json(args.evidence/name/'summary.json')
        if not summary.get('completed_utc'):raise RuntimeError('not terminal: '+name)
    reports=[];manifests={}
    for name in names:
        review=Review(args.evidence/name,head)
        try:reports.append(review.run())
        except Exception as exc:
            (args.output/(name+'-review-failure.json')).write_text(json.dumps({'error':str(exc),'checks':review.checks,'scope':'review parser assertion; original run verdict unchanged'},indent=2)+'\n')
            raise
        manifests[name]=review.before
    report={'schema_version':1,'review_passed':True,'complete_eight_scenario_review':len(reports)==8,'author_role':'Original harness author using separate original-byte parser; not an independent implementation','runtime_head':head,'cases':reports,'case_count':len(reports),'diagnostic_count':sum(r['diagnostic_count'] for r in reports),'count_scope':'diagnostic assertions, not new QEMU cases or Rust executions','original_file_count':sum(len(v) for v in manifests.values()),'scope':['actual accepted capability/WAL or initializer syscall-entry, retained owner, exact root proof and full baseline, subsequent real HID/FTDI reuse','Local cases additionally check original HID survives target stop, actual read-only server detach completion, and same listener returns authenticated empty catalog.'],'limits':['Sequential root/sysfs/FD observations are not an atomic multi-resource snapshot.','No mapping of arbitrary Native anonymous control sockets to individual channels; exact root receipts and actual kernel/baseline proof supply ownership evidence.','GET_CONFIGURATION entry is not in-kernel active blocking, interface claim, or pending URB evidence.','Native server incoming state alone is not client enumeration; actual VHCI receipts and evdev/serial bytes are checked.','No hardware or ARM execution claim.','Historical 68/b1/b529/222 FAIL results remain unchanged and are not backfilled.','No production harness/verifier was imported or called; no build/QEMU/Git command or original-file write.']}
    (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n');(args.output/'originals-manifest.json').write_text(json.dumps(manifests,indent=2)+'\n')
    print(json.dumps({'review_passed':True,'complete_eight':len(reports)==8,'cases':len(reports),'diagnostics':report['diagnostic_count'],'original_files':report['original_file_count'],'output':str(args.output)}))

if __name__=='__main__':main()
