#!/usr/bin/env python3
"""Read six completed runs only. No runner import/verify or product execution."""
from pathlib import Path
import collections
from datetime import timezone
import hashlib
import importlib.util
import json
import re
import struct
import subprocess
import cryptography
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID

ROOT=Path('/home/ubuntu/github/usbip-client')
HEAD='b83c931afb1789ae178bf49c6402f996fc9fb3c5'
PARSER=Path('/tmp/rustusb-secure-independent-decoder-20261023.py')
spec=importlib.util.spec_from_file_location('own_independent_decoder',PARSER)
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
checks=d.checks;check=d.check;read=d.read;load=d.load
read(PARSER)
RUNS=[(kind,family) for kind in ['virtual','usbfs','native'] for family in ['ipv4','ipv6']]
sources=None;binary=None;records=[];errors=[]
all_artifact_paths=[]


def rows(path):
    output=[]
    for line in read(path).decode().splitlines():
        fields=line.split()
        if len(fields)==7 and fields[0] in ('hs','ss'):
            output.append({'hub':fields[0],'port':int(fields[1]),'status':int(fields[2]),'speed':int(fields[3]),'devid':int(fields[4],16),'local_bus':fields[6]})
    return output


def event_lines(path):
    return [json.loads(line) for line in read(path).decode().splitlines() if line]


def verify_cert(cert,issuer):
    key=issuer.public_key()
    if isinstance(key,rsa.RSAPublicKey):key.verify(cert.signature,cert.tbs_certificate_bytes,padding.PKCS1v15(),cert.signature_hash_algorithm)
    elif isinstance(key,ec.EllipticCurvePublicKey):key.verify(cert.signature,cert.tbs_certificate_bytes,ec.ECDSA(cert.signature_hash_algorithm))
    else:key.verify(cert.signature,cert.tbs_certificate_bytes)


def compact_capture(value):
    counts=collections.Counter();examples={}
    for c in value['controls']:
        key=(c['icmp_type'],c.get('hbh_hex'),c['hop_limit'])
        counts[key]+=1;examples.setdefault(key,c)
    controls=[{'count':counts[k],'decoded_example':examples[k]} for k in counts]
    return {**{k:v for k,v in value.items() if k!='controls'},'control_classes':controls}


def hardware(rows):
    return [{k:v for k,v in row.items() if k not in ['configuration','num_interfaces','configuration_bytes','count_bytes','interfaces','device_driver','usbip_status']} for row in rows]


for kind,family in RUNS:
    name=kind+'-'+family;base=Path('/tmp/rustusb-secure-qemu-final-'+name+'-20261023')
    try:
        summary=load(base/'summary.json')
        if not summary.get('completed_utc'):raise ValueError('run not terminal; do not read partial artifacts')
        check(name+' actual completed PASS/two guest0/samecleanHEAD/all guards',summary['result']=='PASS' and summary['guest_exit_codes']=={'client':0,'server':0} and summary['head']==summary['head_after']==HEAD and summary['git_status_before']==summary['git_status_after']=='' and all(summary[k] is True for k in ['source_guard','binary_guard','head_guard','clean_guard']))
        if sources is None:sources=summary['source_inputs'];binary=summary['binary_sha256']
        check(name+' identical 125 source inputs and actual built binary hashes across all six',summary['source_inputs']==sources and len(sources)==125 and summary['binary_sha256']==binary and not summary['build_skipped'])
        mismatches=[]
        for path,meta in summary['source_inputs'].items():
            original=read(base/'source'/path)
            committed=subprocess.check_output(['git','show',HEAD+':'+path],cwd=ROOT)
            if original!=committed or len(original)!=meta['bytes'] or hashlib.sha256(original).hexdigest()!=meta['sha256']:mismatches.append(path)
        check(name+' all frozen source snapshot bytes equal immutable Git commit',not mismatches,mismatches)
        manifest_mismatch=[]
        for path,meta in summary['artifact_manifest'].items():
            value=read(base/path);all_artifact_paths.append(base/path)
            if len(value)!=meta['bytes'] or hashlib.sha256(value).hexdigest()!=meta['sha256']:manifest_mismatch.append(path)
        check(name+' full indexed artifact length/SHA independently matches originals',not manifest_mismatch,{'artifacts':len(summary['artifact_manifest']),'mismatches':manifest_mismatch})
        # Raw S1 base64 dump must reconstruct every retained per-guest artifact.
        for role in ['client','server']:
            evidence=read(base/(role+'-evidence.log')).decode();kernel=read(base/(role+'-serial.log')).decode(errors='replace')
            matches=list(re.finditer(r'^SECURE_ARTIFACT_BEGIN ([a-zA-Z0-9_.-]+) (\d+)\n(.*?)^SECURE_ARTIFACT_END \1\s*$',evidence,re.M|re.S))
            dumps_ok=True;dump_names=set()
            import base64
            for match in matches:
                artifact,length,encoded=match.groups();value=base64.b64decode(''.join(encoded.splitlines()),validate=True)
                dumps_ok &= len(value)==int(length) and value==read(base/(role+'-artifacts')/artifact) and artifact not in dump_names
                dump_names.add(artifact)
            check(name+' '+role+' full rawserial artifact dumps exactly reconstruct files',dumps_ok and evidence.count('SECURE_ARTIFACT_BEGIN ')==len(matches) and dump_names=={p.name for p in (base/(role+'-artifacts')).iterdir() if p.is_file()})
            check(name+' '+role+' guestPASS and kernel/evidence guards',f'RUSTUSB_SECURE_RESULT: PASS role={role}\n' in evidence and 'RUSTUSB_SECURE_RESULT: FAIL' not in evidence and not any(marker in kernel+evidence for marker in ['Kernel panic','Oops:','BUG:','Call Trace:','SECURE_ARTIFACT_ERROR','SECURE_ARTIFACT_OVERSIZE','SECURE_POWEROFF_RETURNED']))
            check(name+' '+role+' own credential isolation assertion actually emitted',f'SECURE_CREDENTIAL_ISOLATION role={role} own_key=true opposite_key=false ca_key=false' in evidence)
        clientdir=base/'client-artifacts';serverdir=base/'server-artifacts'
        server_config=load(serverdir/'server-config.json');catalog=load(clientdir/'catalog-initial.json')
        expectedkeys=load(clientdir/'keys.json');cmap={e['device_key']:e['device'] for e in catalog}
        check(name+' initial authorized catalog exactly configured pool/rule and unique BusIDs',len(cmap)==len(catalog)==len(expectedkeys)==2 and set(cmap)==set(expectedkeys)==set(server_config['devices'])==set(server_config['principals'][0]['devices']) and len({x['bus_id'] for x in cmap.values()})==2 and all(k.startswith({'virtual':'virtual:','usbfs':'physical:','native':'native:'}[kind]) for k in cmap))
        prefix='192.0.2.1:3241' if family=='ipv4' else '[fd42:7275:7374::1]:3241'
        check(name+' actual server startup brokerUID and selected literal endpoint',server_config['schema_version']==1 and server_config['broker_uid']==2000 and server_config['listen']==prefix)
        cp=load(clientdir/'helper-policy.json')
        check(name+' client local role separated and exact remote pool',cp['secure_broker_uid']==2000 and 2000 not in cp['allowed_uids'] and cp['trusted_network'] is False and cp['peers']==[] and set(cp['secure_imports'][0]['device_keys'])==set(expectedkeys) and cp['secure_imports'][0]['server_id']=='qemu-server')
        name_for_tls=None
        for profile,key in [('hid',expectedkeys[0]),('serial',expectedkeys[1])]:
            config=load(clientdir/f'client-{profile}-config.json');name_for_tls=config['server_name']
            check(name+' '+profile+' actual fixed endpoint/serverid/key/credentials',config['broker_uid']==2000 and config['server_id']=='qemu-server' and config['device']==key and config['endpoint']=={'host':'192.0.2.1' if family=='ipv4' else 'fd42:7275:7374::1','port':3241} and config['server_name']=='rustusb-qemu.test' and config['ca_certificates']==['/etc/rustusb/ca.der'] and config['certificate_chain']==['/etc/rustusb/client.der'] and config['private_key']=='/etc/rustusb/client-key.der')
        # Validate public DER signatures/EKU/DNS/time, never load any private key.
        ca_bytes=read(base/'ca-public.der');client_bytes=read(base/'client-public.der');server_bytes=read(base/'server-public.der')
        ca=x509.load_der_x509_certificate(ca_bytes);client=x509.load_der_x509_certificate(client_bytes);server=x509.load_der_x509_certificate(server_bytes)
        verify_cert(ca,ca);verify_cert(client,ca);verify_cert(server,ca)
        epochs=[int(re.search(r'guest_epoch=(\d+)',read(p).decode()).group(1)) for p in [clientdir/'clock-observation.txt',serverdir/'clock-observation.txt']]
        check(name+' actual publicDER chain signatures/CA role/EKU/DNS/guest validity',ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca and client.issuer==server.issuer==ca.subject and ExtendedKeyUsageOID.CLIENT_AUTH in client.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value and ExtendedKeyUsageOID.SERVER_AUTH in server.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value and name_for_tls in server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName) and all(cert.not_valid_before.replace(tzinfo=timezone.utc).timestamp()<=epoch<=cert.not_valid_after.replace(tzinfo=timezone.utc).timestamp() for cert in [ca,client,server] for epoch in epochs))
        check(name+' actual client leaf SHA exactly principal pin, no CA signing keys archived',server_config['principals'][0]['pins']==[hashlib.sha256(client_bytes).hexdigest()] and all(not ('key.der' in path or path.endswith('.key')) for path in summary['artifact_manifest']))
        for role,who in [('client',['helper','hid-1','hid-2','serial-1','serial-2']),('server',['server']+(['helper'] if kind!='virtual' else []))]:
            for process in who:
                state=read(base/(role+'-artifacts')/f'identity-{process}.txt').decode();uid=0 if process=='helper' else 2000
                check(name+' '+role+'/'+process+' real four UID fields and broker caps zero',re.search(r'^Uid:\s+(.+)$',state,re.M).group(1).split()==[str(uid)]*4 and (uid==0 or all(re.search(r'^'+field+r':\s+0+$',state,re.M) is not None for field in ['CapInh','CapPrm','CapEff','CapAmb'])))
                exe=read(base/(role+'-artifacts')/f'executable-{process}.txt').decode();pid=re.search(r'^Pid:\s+(\d+)$',state,re.M).group(1)
                check(name+' '+role+'/'+process+' actual procPID ELF hash matches frozen build',exe==summary['binary_sha256']['rustusb-helper' if process=='helper' else 'rustusb']+'  /proc/'+pid+'/exe\n')
        denial=load(clientdir/'denied-response.json')
        check(name+' actual nonbroker secure request refused without VHCI mutation',denial['ok'] is False and 'configured secure broker' in denial['error'] and read(clientdir/'native-before-denied.txt')==read(clientdir/'native-after-denied.txt'))
        if kind!='virtual':
            sp=load(serverdir/'helper-policy.json');pool=sp['secure_physical_device_ids' if kind=='usbfs' else 'secure_native_device_ids']
            check(name+' server helper exact secure pool separate legacy/direct path',sp['secure_broker_uid']==2000 and 2000 not in sp['allowed_uids'] and sp['export_device_ids']==[] and sp['peers']==[] and sp['trusted_network'] is False and set(pool)=={k.split(':',1)[1] for k in expectedkeys} and load(serverdir/'denied-response.json')['ok'] is False and load(serverdir/'host-before.json')==load(serverdir/'host-after-denied.json'))
        markers=[(n,bytes.fromhex(v)) for n,v in [('DEVLISTreq','0111800500000000'),('DEVLISTreply','0111000500000000'),('IMPORTreq','0111800300000000'),('IMPORTreply','0111000300000000')]]
        clientlog=read(base/'client-evidence.log').decode();serverlog=read(base/'server-evidence.log').decode();sessions=[]
        for cycle in [1,2]:
            rx=read(clientdir/f'rx-{cycle}.bin');tx=read(clientdir/f'tx-{cycle}.bin');echo=read(clientdir/f'echo-{cycle}.bin');expectedrx=read(clientdir/f'expected-rx-{cycle}.bin')
            check(name+f' cycle{cycle} exact actual35B RX+TXecho',len(rx)==len(tx)==len(echo)==35 and rx==expectedrx and echo==tx)
            for label,value in [('RX',rx),('TX',tx)]:markers.append((f'{label}{cycle}',value))
            events=read(clientdir/f'input-{cycle}.bin');require(len(events)%24==0,'partial evdev event')
            keys=[(t,c,v) for sec,usec,t,c,v in struct.iter_unpack('<qqHHi',events) if t==1]
            check(name+f' cycle{cycle} actual key{cycle+1} press/release events',keys==[(1,cycle+1,1),(1,cycle+1,0)])
            check(name+f' cycle{cycle} real IO/SIGTERM0/NULLgone/reuse markers',f'SECURE_CLIENT_IO cycle={cycle} key_code={cycle+1} rx=35 tx=35' in clientlog and f'SECURE_CLIENT_RELEASED cycle={cycle} signal=SIGTERM wait=0 kernel_null=true local_gone=true' in clientlog and f'SECURE_CLIENT_REUSE cycle={cycle}' in clientlog and f'SECURE_SERVER_IO cycle={cycle}' in serverlog and f'SECURE_SERVER_RELEASED cycle={cycle}' in serverlog)
            received=[]
            for profile,key in [('hid',expectedkeys[0]),('serial',expectedkeys[1])]:
                e=event_lines(clientdir/f'attach-{profile}-{cycle}.events');connected=[v for v in e if v.get('event')=='connected'];disconnected=[v for v in e if v.get('event')=='disconnected']
                require(len(connected)==len(disconnected)==1,'connected/disconnected event count')
                receipt=connected[0]['receipt'];kernel=receipt['kernel'];remote=cmap[key]
                check(name+f' cycle{cycle}/{profile} real logical key/remote metadata/kernel identity and verifiedclose',receipt['target']=={'server_id':'qemu-server','device_key':key,'remote':remote} and kernel['session']['remote_device_id']==((remote['bus_number']<<16)|remote['device_number']) and kernel['session']['vendor_id']==remote['vendor_id'] and kernel['session']['product_id']==remote['product_id'] and kernel['session']['status']==6 and kernel['session']['local_device_generation']['inode']>0 and kernel['kernel_socket_inode']>0 and disconnected[0]['cleanup_verified'] is True and read(clientdir/f'wait-{profile}-{cycle}.txt').strip()==b'0')
                received.append(kernel['session']);sessions.append(receipt)
            active=rows(clientdir/f'native-held-{cycle}.txt');used=[v for v in active if v['status']==6]
            check(name+f' cycle{cycle} independently dumped realVHCI rows match two strong receipts',len(used)==2 and all(any(r['port']==s['port'] and r['local_bus']==s['local_bus_id'] and r['devid']==s['remote_device_id'] for r in used) for s in received))
            check(name+f' cycle{cycle} contender catalog hidden/busy unchanged',read(clientdir/f'native-held-{cycle}.txt')==read(clientdir/f'native-after-contender-{cycle}.txt') and 'unavailable or unauthorized' in read(clientdir/f'contender-{cycle}.log').decode())
            check(name+f' cycle{cycle} rawNULL rows and empty durable importjournal',all(r['status']==4 and r['devid']==0 and r['local_bus']=='0-0' for r in rows(clientdir/f'native-empty-{cycle}.txt')) and load(clientdir/f'channels-empty-{cycle}.json')=={'schema_version':1,'records':[],'exports':[]})
            recatalog=load(clientdir/f'catalog-reuse-{cycle}.json')
            check(name+f' cycle{cycle} actual authorized catalog reusable same stable mappings', {e['device_key']:e['device'] for e in recatalog}==cmap)
        captures={role:d.capture(base/f'{role}-external.pcap',markers,4 if family=='ipv4' else 6,{(v['bus_number']<<16)|v['device_number'] for v in cmap.values()},name_for_tls) for role in ['client','server']}
        check(name+' both full external captures reconstruct identical complete bidirectional TLS bytes',{(tuple(v['tuple']),v['sha256'],v['wire_bytes']) for v in captures['client']['TLS_directional_flows']}=={(tuple(v['tuple']),v['sha256'],v['wire_bytes']) for v in captures['server']['TLS_directional_flows']})
        check(name+' chosenfamily TLS only, no specified clearUSBIP/fixture signatures and matchingdual3240RST',all(v['all_other_payload_accounted'] and {r['family'] for r in v['plain_3240_rst']}=={4,6} and all(f['tuple'][0]==(4 if family=='ipv4' else 6) and f['specified_clear_markers_absent'] and f['recognizable_urb_headers_absent'] for f in v['TLS_directional_flows']) for v in captures.values()))
        if kind=='virtual':
            stopped=[e for e in event_lines(serverdir/'server.events') if e.get('event')=='stopped'];require(len(stopped)==1,'virtual stopped')
            check(name+' virtual stopped reallease/pending/queues/config zero',all(v['leased'] is False and v['pending_urbs']==v['hid_queued_reports']==v['cdc_queued_bytes']==v['configuration']==0 for v in stopped[0]['snapshot']['devices']))
        else:
            baseline=load(serverdir/'host-before.json');final=load(serverdir/'host-final.json')
            check(name+' same realgeneration/config/alt/fulloriginaldriver restored',baseline==final)
            check(name+' actual FTDI hostsink exact70B TX and two35B injectedRX',read(base/'ftdi-host-rx.bin')==read(clientdir/'tx-1.bin')+read(clientdir/'tx-2.bin') and all(read(base/f'ftdi-host-injected-{c}.bin')==read(clientdir/f'rx-{c}.bin') for c in [1,2]))
            stopped=[e for e in event_lines(serverdir/'server.events') if e.get('event')=='stopped'];require(len(stopped)==1,'physical stopped')
            if kind=='usbfs':
                snap=stopped[0]['snapshot']
                check(name+' USBFS finalresources0 and independent durablejournal retired',snap['buffer_bytes_reserved']==0 and all(v['state']=='available' and v['leased'] is False and v['pending_urbs']==v['queued_urbs']==v['usbfs_inflight']==0 and v['cleanup_verified'] is True and v['original_drivers']==v['current_drivers'] for v in snap['devices']) and load(serverdir/'physical-final.json')=={'schema_version':1,'records':[]})
            else:
                exported=load(serverdir/'host-exported.json');journal=load(serverdir/'server-native-exported.json');baseby={r['bus']:r for r in baseline}
                check(name+' Native originalpublished baseline distinct from actualempty export phase',hardware(exported)==hardware(baseline) and all(r['configuration']==r['num_interfaces']=='' and r['configuration_bytes']==r['count_bytes']==0 and r['interfaces']==[] and r['device_driver']=='usbip-host' and r['usbip_status']==1 for r in exported) and len(journal['exports'])==2 and journal['records']==[] and all(r['registration']['published_device']==cmap[r['registration']['device_key']] and r['registration']['original_device_driver']=='usb' and r['bound']['bound_profile']=={'configuration':None,'interfaces':[]} for r in journal['exports']))
                for cycle in [1,2]:
                    held=load(serverdir/f'host-held-{cycle}.json');released=load(serverdir/f'host-released-{cycle}.json');jh=load(serverdir/f'server-native-held-{cycle}.json');jr=load(serverdir/f'server-native-released-{cycle}.json')
                    check(name+f' Native cycle{cycle} realUSED/nullIFdriver thenAVAILABLE samegeneration',hardware(held)==hardware(released)==hardware(baseline) and all(r['usbip_status']==2 and r['device_driver']=='usbip-host' and r['configuration']==baseby[r['bus']]['configuration'] and all(i['driver']=='' for i in r['interfaces']) for r in held) and all(r['usbip_status']==1 and r['device_driver']=='usbip-host' and all(i['driver']=='' for i in r['interfaces']) for r in released))
                    check(name+f' Native cycle{cycle} actualstub observedreceipt thenownedstub cleared',len(jh['exports'])==len(jr['exports'])==2 and all(r['stub']['state']['phase']=='observed' and r['stub']['state']['receipt']['usbip_status']==2 and r['stub']['state']['receipt']['kernel_socket_inode']>0 for r in jh['exports']) and all(r['stub'] is None for r in jr['exports']))
                check(name+' Native final actualoriginalbaseline+emptyexport journal/verified event',load(serverdir/'server-native-final.json')=={'schema_version':1,'records':[],'exports':[]} and stopped[0]['exports_closed']==2 and stopped[0]['export_cleanup_verified'] is True)
        check(name+' actual server SIGTERM exit0',read(serverdir/'wait-server.txt').strip()==b'0')
        commands=summary['qemu_commands']
        check(name+' recordedguest network is private socket link nothostUSB or configured hostnetwork',all('-nic' in command and command[command.index('-nic')+1]=='none' and any(v.startswith('socket,id=external,fd=') for v in command) and not any('usb-host' in v or v.startswith(('tap,','user,','bridge,')) for v in command) for command in commands.values()))
        records.append({'name':name,'head':summary['head'],'result':summary['result'],'guest_exit_codes':summary['guest_exit_codes'],'source_inputs':len(summary['source_inputs']),'indexed_artifacts':len(summary['artifact_manifest']),'binary_sha256':summary['binary_sha256'],'selected_keys':expectedkeys,'public_leaf_sha256':{'client':hashlib.sha256(client_bytes).hexdigest(),'server':hashlib.sha256(server_bytes).hexdigest()},'actual_guest_epochs':epochs,'captures':{role:compact_capture(c) for role,c in captures.items()},'connected_receipts_count':len(sessions),'serial_actual_bytes_each_direction':70,'actual_keyboard_cycles':2})
    except Exception as error:
        errors.append({'run':name,'error':type(error).__name__+': '+str(error)})
        check(name+' independent parser completed',False,errors[-1]['error'])

check('all six completed candidates independently parsed',len(records)==6 and not errors)
check('all inspected original artifacts stay byteidentical through review',all(len(Path(p).read_bytes())==v['length'] and hashlib.sha256(Path(p).read_bytes()).hexdigest()==v['sha256'] for p,v in d.seen.items()))
report={'schema_version':1,'result':'PASS' if all(c['passed'] for c in checks) else 'FAIL','head':HEAD,'scope':'Independent completed-sixrun raw/base64/sysfs/identity/config/publicDER and entire Ethernet/IP/TCP/TLS decoding; no runner functions, product execution, build, QEMU or hostdevice mutation.',
        'runs':records,'counts':{'runs':len(records),'guest_exit0':sum(v==0 for r in records for v in r['guest_exit_codes'].values()),'test_cycles':2*len(records),'serial_bytes_each_direction':70*len(records),'checks':len(checks),'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks)},'checks':checks,'parser_errors':errors,'review_crypto_library':cryptography.__version__,
        'limitations':['These are six disposable Linux6.8 x86_64 QEMU model runs. Real hardwareG1/G2, other kernels/devices, ISO, generic crossconfiguration/recovery and entirePhase3 completion remain separate.','All two pool devices are granted to the single client principal in these QEMU configs. Authorized catalog visibility, busy hiding and localUID denials are observed; an otherwise valid but ungranted network certificate guessing a key is not a newQEMU negative here.','No TLS decryption/keylog. Capture proves ClientHello ALPN/SNI offers, ServerHelloTLS1.3 selection, complete encrypted-record framing and absence of specified signatures. Negotiated ALPN/clientcert verification additionally depend on fixed auditedTLS source and successful actual protocol/I/O; not packet-only visibility.','Public DER signatures/CA/EKU/name and recordedguest time checked offline; no secret/privatekey bytes read or archived. Actual trusted-file modes are enforced at startup by audited loader; do not invent independentstat fields absent from raw.','ProcUID/caps/ELF SHA and kernel/sysfs facts are original guest observations correlated across raw files and exact frozen source, not live re-observation after guests stopped or a downloadedELF rehash.','TLSstream purposes cannot be decrypted from PCAP; association with DEVLIST/IMPORT/URB follows actual CLI source, catalog/receipts and real I/O plus no other application transport on complete external captures.','Recognizable specified management/URB/35B fixture signature absence is a finite leak check, not a universal no-information-leak theorem.','Earlier first/second/third failures and f955 capture-parserFAIL remain unchanged, independently attributed. This report covers only six terminal b83c931 runs.','Current documentationdirty worktree after run is distinct from recordedcleanrun guards; immutableGit blobs and archived bytes anchor the runningcandidate.'],
        'artifacts':[d.seen[p] for p in sorted(d.seen)]}
out=Path('/tmp/rustusb-secure-six-independent-review-20261023.json');out.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'result':report['result'],'counts':report['counts'],'path':str(out),'length':out.stat().st_size,'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'errors':errors}))
for c in checks:
    if not c['passed']:print('FAILED',c)
