#!/usr/bin/env python3
"""Independent terminal evidence audit; never imports a runner or PCAP verifier."""
import base64, hashlib, json, re, struct, subprocess
from pathlib import Path
ROOT=Path('/tmp/rustusb-secure-qemu-virtual-ipv4-fixed-20261023')
OUT=Path('/tmp/rustusb-secure-fixed-lifecycle-raw-review-20261023.json')
S=json.loads((ROOT/'summary.json').read_text())
checks=[]
def check(name,value,detail=None):
    checks.append({'name':name,'passed':bool(value),**({'detail':detail} if detail is not None else {})})
def data(role,name): return (ROOT/f'{role}-artifacts'/name).read_bytes()
def obj(role,name): return json.loads(data(role,name))
def events(role,name): return [json.loads(line) for line in data(role,name).splitlines()]
def sha(b): return hashlib.sha256(b).hexdigest()
def status(name):
    rows=[]
    for line in data('client',name).decode().splitlines():
        f=line.split()
        if f and f[0] in ('hs','ss'):
            if len(f)!=7: raise ValueError('unexpected status row')
            rows.append(dict(hub=f[0],port=int(f[1]),state=int(f[2]),speed=int(f[3]),devid=int(f[4],16),historical_sockfd=int(f[5]),local=f[6]))
    return rows

def null(rows):
    return len(rows)==16 and {r['port'] for r in rows}==set(range(16)) and all(r['state']==4 and r['speed']==0 and r['devid']==0 and r['historical_sockfd']==0 and r['local']=='0-0' for r in rows)

check('overall failed result is retained',S['result']=='FAIL' and S['error']=='RuntimeError: unexpected external IPv6 extension/protocol')
check('frozen source attribution',S['head']=='f9558fcd7c0c29e3df1b13bbd109f7d471806c9f' and S['head_after']==S['head'])
check('both real QEMU exits zero',S['guest_exit_codes']=={'server':0,'client':0})
check('run guards and full build',all(S[k] for k in ['source_guard','binary_guard','head_guard','clean_guard']) and not S['build_skipped'])
source_count=0
for path,record in S['source_inputs'].items():
    b=(ROOT/'source'/path).read_bytes()
    blob=subprocess.check_output(['git','show',S['head']+':'+path],cwd='/home/ubuntu/github/usbip-client')
    if not (len(b)==record['bytes'] and sha(b)==record['sha256'] and b==blob): raise ValueError('source mismatch '+path)
    source_count+=1
check('all frozen inputs equal recorded hash and exact source commit Git blobs',True,{'inputs':source_count})
fix_source=(ROOT/'source/crates/rustusb-native/src/channel.rs').read_bytes()
check('actual run frozen source contains reviewed Native initialization fix',sha(fix_source)=='49d6c77aecf0715bf1b9ceace046989ff630da519a02e722eb7536369a9ae2d6')
manifest_count=0
for path,record in S['artifact_manifest'].items():
    b=(ROOT/path).read_bytes()
    if len(b)!=record['bytes'] or sha(b)!=record['sha256']: raise ValueError('artifact mismatch '+path)
    manifest_count+=1
check('all retained original artifact manifest entries match bytes',True,{'files':manifest_count})
extracted_count=0
for role in ['server','client']:
    lines=(ROOT/f'{role}-evidence.log').read_text().splitlines();seen=set();i=0
    while i<len(lines):
        if not lines[i].startswith('SECURE_ARTIFACT_BEGIN '):i+=1;continue
        _,name,length=lines[i].split();i+=1;encoded=[]
        while i<len(lines) and lines[i]!='SECURE_ARTIFACT_END '+name: encoded.append(lines[i]);i+=1
        if i>=len(lines) or name in seen:raise ValueError('incomplete/duplicate artifact '+role+'/'+name)
        b=base64.b64decode(''.join(encoded),validate=True);r=S['guest_artifacts'][role][name]
        if not(len(b)==int(length)==r['bytes'] and sha(b)==r['sha256'] and data(role,name)==b):raise ValueError('raw extraction mismatch '+name)
        seen.add(name);extracted_count+=1;i+=1
    check(role+' every raw serial artifact equals extracted file',seen==set(S['guest_artifacts'][role]),{'files':len(seen)})
    text='\n'.join(lines)
    check(role+' one terminal guest PASS and no FAIL',text.count('RUSTUSB_SECURE_RESULT: PASS role='+role)==1 and 'RUSTUSB_SECURE_RESULT: FAIL' not in text)
    check(role+' role key separation and real CRNG/RTC assertions',f'SECURE_CREDENTIAL_ISOLATION role={role} own_key=true opposite_key=false ca_key=false' in text and 'SECURE_RNG_RTC_READY crng=true ' in text)
    kernel=(ROOT/f'{role}-serial.log').read_text()
    check(role+' orderly kernel poweroff without panic/Oops','Power down' in kernel and 'Kernel panic' not in kernel and 'Oops:' not in kernel)
    names=S['credential_payloads'][role]
    other='client' if role=='server' else 'server'
    check(role+' role payload private key inventory',f'{role}-key.der' in names and f'{other}-key.der' not in names and not any(n.startswith('ca-key') for n in names))
    for exe in (ROOT/f'{role}-artifacts').glob('executable-*.txt'):
        program='rustusb-helper' if exe.name=='executable-helper.txt' else 'rustusb'
        check(role+'/'+exe.name+' actual ELF',exe.read_text().split()[0]==S['binary_sha256'][program] and S['payload_sha256'][role]['usr/bin/'+program]==S['binary_sha256'][program])
    for p in (ROOT/f'{role}-artifacts').glob('identity-*.txt'):
        values={x.split(':',1)[0]:x.split(':',1)[1].strip() for x in p.read_text().splitlines() if ':' in x}
        if p.name=='identity-helper.txt': check(role+'/'+p.name+' root helper',values['Uid'].split()==['0']*4)
        else:check(role+'/'+p.name+' nonroot/no capabilities',values['Uid'].split()==['2000']*4 and all(values[k]=='0000000000000000' for k in ['CapInh','CapPrm','CapEff','CapAmb']))

catalog=obj('client','catalog-initial.json');bykey={x['device_key']:x['device'] for x in catalog}
check('catalog exact virtual stable keys',set(bykey)=={'virtual:hid','virtual:cdc-acm'} and len(catalog)==2)
check('catalog publication full profiles',bykey['virtual:hid']['interfaces']==[{'class':3,'subclass':1,'protocol':1}] and bykey['virtual:cdc-acm']['interfaces']==[{'class':2,'subclass':2,'protocol':0},{'class':10,'subclass':0,'protocol':0}] and all(d['configuration']==1 and d['speed']==2 and d['vendor_id']==0x1234 for d in bykey.values()))
policy=obj('client','helper-policy.json')
check('helper roles do not assert legacy trusted network',policy['trusted_network'] is False and policy['allowed_uids']==[1000] and policy['secure_broker_uid']==2000 and policy['peers']==[] and policy['export_device_ids']==[] and policy['secure_imports']==[{'server_id':'qemu-server','device_keys':['virtual:hid','virtual:cdc-acm']}])
denied=obj('client','denied-response.json')
check('actual UID1000 request rejected',denied['ok'] is False and denied['data'] is None and 'not the configured secure broker' in denied['error'])
check('denial leaves exact all-NULL kernel baseline',data('client','native-before-denied.txt')==data('client','native-after-denied.txt') and null(status('native-after-denied.txt')))
for family in [4,6]:check(f'IPv{family} real direct 3240 refusal log',b'Connection refused' in data('client',f'plaintext-{family}.log'))
allreceipts=[];cycles=[]
for cycle in [1,2]:
    held=obj('client',f'channels-held-{cycle}.json');rows=status(f'native-held-{cycle}.txt');used=[r for r in rows if r['state']==6]
    check(f'cycle{cycle} two real USED ports',len(rows)==16 and len(used)==2 and {r['devid'] for r in used}=={65537,65538})
    check(f'cycle{cycle} journal exact two observed imports',held['schema_version']==1 and held['exports']==[] and len(held['records'])==2 and all(r['state']['phase']=='observed' and r['owner']['uid']==2000 and r['owner']['starttime']>0 for r in held['records']))
    receipts=[]
    for profile,key in [('hid','virtual:hid'),('serial','virtual:cdc-acm')]:
        ev=events('client',f'attach-{profile}-{cycle}.events');connected=[x for x in ev if x['event']=='connected'];disconnected=[x for x in ev if x['event']=='disconnected']
        check(f'cycle{cycle}/{profile} exact lifecycle events',len(connected)==len(disconnected)==1 and len(ev)==2 and connected[0]['transport']=='mtls' and disconnected[0]['cleanup_verified'] is True)
        receipt=connected[0]['receipt'];receipts.append(receipt);allreceipts.append(receipt)
        k=receipt['kernel'];local=k['session'];remote=bykey[key]
        check(f'cycle{cycle}/{profile} exact selected catalog metadata',receipt['target']=={'server_id':'qemu-server','device_key':key,'remote':remote} and connected[0]['device_key']==key)
        record=next(r for r in held['records'] if r['channel_id']==receipt['channel_id'])
        identity={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in data('client',f'identity-{profile}-{cycle}.txt').decode().splitlines() if ':' in line}
        check(f'cycle{cycle}/{profile} durable owner and receipt',record['owner']['pid']==int(identity['Pid']) and record['target']==receipt['target'] and record['state']['receipt']==k)
        actual=next(r for r in used if r['port']==local['port'])
        check(f'cycle{cycle}/{profile} full kernel receipt matches raw row',actual['local']==local['local_bus_id'] and actual['devid']==local['remote_device_id']==(remote['bus_number']<<16|remote['device_number']) and actual['speed']==remote['speed'] and local['status']==6 and local['vendor_id']==remote['vendor_id'] and local['product_id']==remote['product_id'] and 1<=local['local_device_address']<=127 and local['local_device_generation']['inode']>0 and local['local_device_generation']['filesystem_device']>0 and k['kernel_socket_inode']>0 and k['intent']['port']==local['port'])
        check(f'cycle{cycle}/{profile} full imported actual configuration',k['profile']['configuration']==1 and [{'class':i['class'],'subclass':i['subclass'],'protocol':i['protocol']} for i in k['profile']['interfaces']]==remote['interfaces'] and all(i['alternate']==0 for i in k['profile']['interfaces']))
        check(f'cycle{cycle}/{profile} child wait0 and no NotFound',data('client',f'wait-{profile}-{cycle}.txt').strip()==b'0' and b'NotFound' not in data('client',f'attach-{profile}-{cycle}.log') and b'cleanup is unconfirmed' not in data('client',f'attach-{profile}-{cycle}.log'))
    encoded=data('client',f'input-{cycle}.bin');events_input=[struct.unpack_from('<qqHHi',encoded,n)[2:] for n in range(0,len(encoded),24)]
    check(f'cycle{cycle} actual Linux input key down/up',len(encoded)>0 and len(encoded)%24==0 and (1,cycle+1,1) in events_input and (1,cycle+1,0) in events_input)
    rx=f'RUSTUSB-RX-{cycle}-0001:0123456789abcdef\n'.encode();tx=f'RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n'.encode()
    check(f'cycle{cycle} exact 35B both serial directions and echo',len(rx)==len(tx)==35 and data('client',f'rx-{cycle}.bin')==data('server',f'injected-{cycle}.bin')==rx and data('client',f'tx-{cycle}.bin')==data('client',f'echo-{cycle}.bin')==tx)
    io=obj('server',f'io-{cycle}.json');devices={d['profile']:d for d in io['devices']};cdc=devices['cdc_acm']
    expectedout=b''.join(f'RUSTUSB-TX-{c}-0001:fedcba9876543210\n'.encode() for c in range(1,cycle+1));expectedin=b''.join(f'RUSTUSB-RX-{c}-0001:0123456789abcdef\nRUSTUSB-TX-{c}-0001:fedcba9876543210\n'.encode() for c in range(1,cycle+1))
    check(f'cycle{cycle} server actual I/O counters and byte hashes',all(d['leased'] and d['configuration']==1 for d in devices.values()) and devices['hid_keyboard']['counters']['hid_taps']==cycle and cdc['counters']['cdc_out_bytes']==35*cycle and cdc['counters']['cdc_in_bytes']==70*cycle and cdc['counters']['cdc_injected_bytes']==35*cycle and cdc['cdc_out_sha256']==sha(expectedout) and cdc['cdc_in_sha256']==sha(expectedin))
    empty=obj('client',f'channels-empty-{cycle}.json')
    check(f'cycle{cycle} allNULL plus empty durable state',null(status(f'native-empty-{cycle}.txt')) and empty=={'schema_version':1,'records':[],'exports':[]})
    released=obj('server',f'released-{cycle}.json')
    check(f'cycle{cycle} server independent resources zero',all(d['leased'] is False and d['pending_urbs']==0 and d['configuration']==0 and d['hid_queued_reports']==0 and d['cdc_queued_bytes']==0 for d in released['devices']))
    check(f'cycle{cycle} same catalog restored for reuse',obj('client',f'catalog-reuse-{cycle}.json')==catalog)
    check(f'cycle{cycle} contender refusal leaves both kernel rows unchanged',data('client',f'native-held-{cycle}.txt')==data('client',f'native-after-contender-{cycle}.txt') and b'configured remote device is unavailable or unauthorized' in data('client',f'contender-{cycle}.log'))
    cycles.append(dict(cycle=cycle,key_code=cycle+1,serial_bytes_each_direction=35,channel_ids=[r['channel_id'] for r in receipts],local_generations=[r['kernel']['session']['local_device_generation'] for r in receipts]))
check('four independent import IDs and local generations',len({r['channel_id'] for r in allreceipts})==4 and len({tuple(sorted(r['kernel']['session']['local_device_generation'].items())) for r in allreceipts})==4)
serverevents=events('server','server.events');ready=[e for e in serverevents if e['event']=='ready'];stopped=[e for e in serverevents if e['event']=='stopped']
check('actual secure server ready and orderly stopped',len(ready)==len(stopped)==1 and ready[0]['transport']=='mutual_tls' and ready[0]['address']=='192.0.2.1:3241' and data('server','wait-server.txt').strip()==b'0' and all(not d['leased'] and d['pending_urbs']==0 for d in stopped[0]['snapshot']['devices']))
kernel=(ROOT/'client-serial.log').read_text()
check('real kernel enumerated HID and CDC twice then disconnected exact addresses',len(re.findall(r'New USB device found, idVendor=1234, idProduct=0001',kernel))==2 and len(re.findall(r'New USB device found, idVendor=1234, idProduct=0002',kernel))==2 and all(f'USB disconnect, device number {address}' in kernel for address in [2,3,4,5]) and kernel.count('cdc_acm 1-2:1.0: ttyACM0: USB ACM device')==2)
helper=data('client','helper.log')
check('helper no recurrent NotFound/IOfailure',b'NotFound' not in helper and b'error_category\x1b[0m\x1b[2m=\x1b[0m"io"' not in helper)
report={'result':'PARTIAL_LIFECYCLE_VERIFIED_RUN_REMAINS_FAIL' if all(c['passed'] for c in checks) else 'RAW_REVIEW_FAIL','original_overall_result':S['result'],'original_overall_error':S['error'],'source_head':S['head'],'evidence_root':str(ROOT),'checks_count':len(checks),'checks':checks,'cycles':cycles,'source_inputs_checked':source_count,'artifact_manifest_entries_checked':manifest_count,'guest_artifacts_redecoded':extracted_count,'boundaries':['OriginaloverallFAIL is unchanged. ExternalPCAPparser failed; no complete TLSsecurity orsixmodeacceptance claim. PCAP review is deliberately excluded andassignedseparately.','Two actualvirtualHID/CDC cycles:35B RX+35B TX eachcycle (70B eachdirectiontotal), additionally35B CDCecho eachcycle. No FTDI/physical/Native-server/hardware evidence inthisrun.','Native initialization sourcefix is locked and fouractual imports completed withoutNotFound; no instrumentation proves exactlywhich retrybranch occurred.','Only host originalread-onlyfiles audited; no runner.verify/pcap_review/import, Cargo, QEMU, USB operation orrepositoryedit.','LocalGone assertions retained in PID1 marker and strengthened by raw kerneldisconnect+allNULL+freshdistinctsecondcyclegenerations; guestpoweroffprecludes newindependentpostmortem sysfsreads.']}
OUT.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'report':str(OUT),'sha256':sha(OUT.read_bytes()),'result':report['result'],'checks':len(checks),'failed':[c['name'] for c in checks if not c['passed']],'source_count':source_count,'artifact_count':manifest_count,'guest_artifacts':extracted_count},indent=2))
