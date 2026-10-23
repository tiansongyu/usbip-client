#!/usr/bin/env python3
"""Read completed agent runs; independent packet decoder, no harness imports."""
from pathlib import Path
from datetime import timezone
import base64
import collections
import hashlib
import importlib.util
import json
import re
import struct
import subprocess
import sys
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID

ROOT=Path('/home/ubuntu/github/usbip-client')
HEAD='eaa19366486b8b99412efbfce1f13d49f73fe0cc'
DECODER=Path('/tmp/rustusb-secure-independent-decoder-20261023.py')
spec=importlib.util.spec_from_file_location('independent_agent_packet_decoder',DECODER)
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
read,load,require,check=d.read,d.load,d.require,d.check
checks=d.checks;records=[];errors=[]
read(DECODER)

def same_meta(value,metadata):
    return len(value)==metadata['bytes'] and hashlib.sha256(value).hexdigest()==metadata['sha256']

def certificate_signature(cert,issuer):
    key=issuer.public_key()
    if isinstance(key,rsa.RSAPublicKey): key.verify(cert.signature,cert.tbs_certificate_bytes,padding.PKCS1v15(),cert.signature_hash_algorithm)
    elif isinstance(key,ec.EllipticCurvePublicKey): key.verify(cert.signature,cert.tbs_certificate_bytes,ec.ECDSA(cert.signature_hash_algorithm))
    else: key.verify(cert.signature,cert.tbs_certificate_bytes)

def condensed_capture(value):
    groups=collections.Counter((c['icmp_type'],c.get('hbh_hex'),c['hop_limit']) for c in value['controls'])
    return {**{k:v for k,v in value.items() if k!='controls'},'control_classes':[{'icmp_type':k[0],'hbh_hex':k[1],'hop_limit':k[2],'count':v} for k,v in groups.items()]}

def app_schema(value,markers,certs):
    require(set(value)=={'schema_version','sequence','jobs','sessions','audit','requests'} and value['schema_version']==1,'application state schema')
    allowed_session={'id','instance_id','job_id','profile','role','device_key','principal','state','evidence','stop_requested','terminal','operation_error','cleanup','metrics','attempts'}
    uuid=re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
    failures={None,'permission','invalid_input','io','timeout','state','unsupported','cancelled','persistence'}
    jobids={j['id'] for j in value['jobs']}
    for job in value['jobs']:
        require(set(job)=={'id','instance_id','profile','operator_uid','state','stop_requested','operation_error'},'job arbitrary fields')
        require(job['profile'] in {'server','hid','serial'} and job['operation_error'] in failures,'job bounded labels/error')
    for session in value['sessions']:
        require(set(session)==allowed_session and session['job_id'] in jobids,'session fields/reference')
        require(session['profile'] in {'server','hid','serial'} and session['role'] in {'import','export','incoming'} and session['principal'] in (None,'qemu-client'),'session unbounded text')
        require(re.fullmatch(r'native:usb-[0-9a-f]{64}',session['device_key']) is not None,'raw serial/device key')
        require(session['operation_error'] in failures and set(session['metrics'])=={'app_rx_bytes','app_tx_bytes','saturated','elapsed_millis'},'session metrics/error fields')
        for attempt in session['attempts']:
            require(set(attempt)=={'kind','correlation','resource_id','proof'} and set(attempt['correlation'])=={'instance_id','session_id','attempt_id'},'attempt arbitrary fields')
            require(all(uuid.fullmatch(v) for v in attempt['correlation'].values()) and (attempt['resource_id'] is None or uuid.fullmatch(attempt['resource_id'])),'attempt raw identity')
    for event in value['audit']:
        require(set(event)=={'sequence','actor_uid','profile','job_id','session_id','action','failure','state','evidence','cleanup'} and event['failure'] in failures,'audit arbitrary payload or error')
        require(event['profile'] in (None,'server','hid','serial'),'audit unbounded profile')
    encoded=json.dumps(value,sort_keys=True).encode()
    require(not any(marker in encoded for _,marker in markers),'raw USB test fixture in app/audit state')
    require(not any(token in encoded for token in [b'-----BEGIN',b'PRIVATE KEY',b'certificate_chain',b'private_key',b'\x00']),'key/cert/raw binary in application state')
    require(not any(base64.b64encode(cert) in encoded or cert in encoded for cert in certs),'full DER certificate in state')
    return len(value['audit'])

for supplied in sys.argv[1:]:
    base=Path(supplied);family='ipv6' if 'ipv6' in base.name else 'ipv4';name=family
    try:
        summary=load(base/'summary.json')
        require(bool(summary.get('completed_utc')),'run not completed; do not inspect partial raw')
        check(name+' completed PASS/actual two guest0/head and recorded clean guards',summary['result']=='PASS' and summary['head']==summary['head_after']==HEAD and summary['guest_exit_codes']=={'server':0,'client':0} and all(summary[k] is True for k in ['source_guard','binary_guard','head_guard','clean_guard']) and summary['git_status_before']==summary['git_status_after']=='' and not summary['build_skipped'])
        source_bad=[]
        for path,metadata in summary['source_inputs'].items():
            value=read(base/'source'/path)
            blob=subprocess.check_output(['git','show',HEAD+':'+path],cwd=ROOT)
            if not same_meta(value,metadata) or value!=blob:source_bad.append(path)
        check(name+' frozen source bytes match exact Git blobs',not source_bad,{'count':len(summary['source_inputs']),'mismatches':source_bad})
        artifact_bad=[]
        for path,metadata in summary['artifact_manifest'].items():
            if not same_meta(read(base/path),metadata):artifact_bad.append(path)
        check(name+' every indexed artifact actual length/SHA',not artifact_bad,{'count':len(summary['artifact_manifest']),'mismatches':artifact_bad})
        directories={r:base/(r+'-artifacts') for r in ['server','client']}
        for role,folder in directories.items():
            evidence=read(base/(role+'-evidence.log')).decode();kernel=read(base/(role+'-serial.log')).decode(errors='replace')
            dumped={};blocks=list(re.finditer(r'^SECURE_ARTIFACT_BEGIN ([a-zA-Z0-9_.-]+) (\d+)\n(.*?)^SECURE_ARTIFACT_END \1\s*$',evidence,re.M|re.S))
            for block in blocks:
                label,length,encoded=block.groups();value=base64.b64decode(''.join(encoded.split()),validate=True)
                require(label not in dumped and len(value)==int(length) and value==read(folder/label),'raw base64 extraction mismatch')
                dumped[label]=hashlib.sha256(value).hexdigest()
            check(name+' '+role+' raw S1 blocks exactly reconstruct all original files',len(blocks)==evidence.count('SECURE_ARTIFACT_BEGIN ') and set(dumped)=={f.name for f in folder.iterdir() if f.is_file()})
            check(name+' '+role+' terminal/evidence/kernel fault guards',evidence.count('RUSTUSB_AGENT_RESULT: PASS role='+role)==1 and not any(token in evidence+kernel for token in ['RUSTUSB_AGENT_RESULT: FAIL','Kernel panic','Oops:','BUG:','Call Trace:','SECURE_ARTIFACT_ERROR','SECURE_ARTIFACT_OVERSIZE','SECURE_POWEROFF_RETURNED']))
            for who in ['helper','agent-1','agent-2']:
                status=read(folder/f'identity-{who}.txt').decode();uid='0' if who=='helper' else '2000'
                observed=re.search(r'^Uid:\s+(.+)$',status,re.M).group(1).split();pid=re.search(r'^Pid:\s+(\d+)$',status,re.M).group(1)
                check(name+' '+role+'/'+who+' actual OS UID and capability boundary',observed==[uid]*4 and (who=='helper' or all(re.search(r'^'+field+r':\s+0+$',status,re.M) for field in ['CapInh','CapPrm','CapEff','CapAmb'])))
                expected=summary['binary_sha256']['rustusb-helper' if who=='helper' else 'rustusb']+'  /proc/'+pid+'/exe\n'
                check(name+' '+role+'/'+who+' actual proc executable receipt matches frozen ELF',read(folder/f'executable-{who}.txt').decode()==expected)
        keys=load(directories['client']/'keys.json');serverkeys=load(directories['server']/'keys.json')
        require(keys==serverkeys and len(set(keys))==2,'device pool differs')
        for role,folder in directories.items():
            manifest=load(folder/'agent-manifest.json');policy=load(folder/'helper-policy.json')
            profiles=['server'] if role=='server' else ['hid','serial']
            check(name+' '+role+' actual root manifest fixed profiles/operator ACL',manifest['schema_version']==1 and manifest['broker_uid']==2000 and manifest['helper_socket']=='/run/rustusb/control.sock' and manifest['control_directory']=='/run/agent' and manifest['data_directory']=='/var/lib/rustusb/agent' and [p['id'] for p in manifest['profiles']]==profiles and [p['backend'] for p in manifest['profiles']]==(['native'] if role=='server' else ['client','client']) and manifest['operators']==[{'uid':1000,'profiles':profiles,'actions':['read','start','stop','discover','audit']}])
            check(name+' '+role+' actual helper policy separate secure broker/legacy pool',policy['secure_broker_uid']==2000 and policy['allowed_uids']==[1000] and policy['export_device_ids']==[] and policy['peers']==[] and policy['trusted_network'] is False and (set(policy['secure_native_device_ids'])=={k.split(':',1)[1] for k in keys} if role=='server' else policy['secure_imports']==[{'server_id':'qemu-server','device_keys':keys}]))
            after=load(folder/'agent-after-denied.json')
            check(name+' '+role+' actual absent pre-store plus public empty jobs/sessions',read(folder/'agent-before-denied.status').strip()==b'absent' and load(folder/'jobs-before-denied.json')['response']=={'result':'jobs','jobs':[],'next_after':None} and load(folder/'sessions-before-denied.json')['response']=={'result':'sessions','sessions':[],'next_after':None})
            denials=[load(folder/f'denied-{reason}.json') for reason in ['uid','profile']]
            check(name+' '+role+' real denials create no job/session/request/root capability',all(v['schema_version']==1 and v['journal_healthy'] is True and v['response']=={'result':'error','code':'permission'} for v in denials) and all(after[k]==[] for k in ['jobs','sessions','requests']) and read(folder/'root-before-denied.json')==read(folder/'root-after-denied.json') and len([e for e in after['audit'] if e['action']=='denied' and e['failure']=='permission'])==2)
            for epoch in [1,2]:
                identity=read(folder/f'agent-identity-{epoch}.txt').decode()
                check(name+' '+role+f' actual runtime directory/socket modes epoch{epoch}',all(token in identity for token in ['control=2000:711','data=2000:700','socket=2000:666']))
        # Certificates are public fixture DER, not decrypted handshake artifacts.
        ca_bytes=read(base/'ca-public.der');client_bytes=read(base/'client-public.der');server_bytes=read(base/'server-public.der')
        ca=x509.load_der_x509_certificate(ca_bytes);client=x509.load_der_x509_certificate(client_bytes);server=x509.load_der_x509_certificate(server_bytes)
        for cert,issuer in [(ca,ca),(client,ca),(server,ca)]:certificate_signature(cert,issuer)
        epochs=[int(re.search(r'guest_epoch=(\d+)',read(folder/'clock-observation.txt').decode()).group(1)) for folder in directories.values()]
        check(name+' offline public DER chain/EKU/SAN and observed guest time',ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca and client.issuer==server.issuer==ca.subject and ExtendedKeyUsageOID.CLIENT_AUTH in client.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value and ExtendedKeyUsageOID.SERVER_AUTH in server.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value and 'rustusb-qemu.test' in server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName) and all(c.not_valid_before.replace(tzinfo=timezone.utc).timestamp()<=t<=c.not_valid_after.replace(tzinfo=timezone.utc).timestamp() for c in [ca,client,server] for t in epochs))
        pin=hashlib.sha256(client_bytes).hexdigest()
        support=read(base/'source/scripts/qemu/run-secure-qemu.py').decode()
        check(name+' fixed payload producer derives pin from copied public client DER',all(text in support for text in ['shutil.copyfile(certificates / name, config / name)','(config / "client-pin.txt").write_text(digest(config / "client.der") + "\\n")']))
        generated=read(base/'server-generated-agent-init.sh').decode()
        check(name+' executed frozen init derives exact server TLS pool/pin and client fixed keys',all(text in generated for text in ['PIN=$(cat /etc/rustusb/client-pin.txt)','devices:$keys[0],principals:[{id:"qemu-client",pins:[$pin],devices:$keys[0]}]','server_name:"rustusb-qemu.test",device:$key','chmod 600 /etc/rustusb/server-key.der','chmod 600 /etc/rustusb/client-key.der']) and all('AGENT_START_RETURNED' in read(base/(role+'-evidence.log')).decode() for role in ['server','client']))
        # No actual /etc TLS JSON/mode snapshot was collected in these old runs.
        markers=[(tag,bytes.fromhex(value)) for tag,value in [('DEVLISTreq','0111800500000000'),('DEVLISTreply','0111000500000000'),('IMPORTreq','0111800300000000'),('IMPORTreply','0111000300000000')]]
        for round_id in [1,2,3]:
            for direction in ['rx','tx']:markers.append((direction+str(round_id),read(directories['client']/f'{direction}-{round_id}.bin')))
        catalog=load(directories['client']/'catalog-initial.json')['response']['devices'];devices={entry['device_key']:entry['device'] for entry in catalog}
        check(name+' actual authenticated catalog exactly two approved keys',len(catalog)==len(devices)==2 and set(devices)==set(keys))
        device_ids={(v['bus_number']<<16)|v['device_number'] for v in devices.values()}
        captures={role:d.capture(base/(role+'-external.pcap'),markers,6 if family=='ipv6' else 4,device_ids,'rustusb-qemu.test') for role in ['client','server']}
        check(name+' both full PCAPs independently reconstruct identical TLS byte streams',{(tuple(v['tuple']),v['wire_bytes'],v['sha256']) for v in captures['client']['TLS_directional_flows']}=={(tuple(v['tuple']),v['wire_bytes'],v['sha256']) for v in captures['server']['TLS_directional_flows']})
        check(name+' all selected-family TCP payload is complete TLS13 with ALPNoffer/SNI and both3240 RST only',all(c['all_other_payload_accounted'] and {r['family'] for r in c['plain_3240_rst']}=={4,6} and all(f['tuple'][0]==(6 if family=='ipv6' else 4) and f['specified_clear_markers_absent'] and f['recognizable_urb_headers_absent'] and (f['hello'].get('selected_version')==772 if f['tuple'][2]==3241 else f['hello']['alpn_offered']==['rustusb-usbip/1']) for f in c['TLS_directional_flows']) for c in captures.values()))
        observed_metrics={};audit_counts={};state_count=0
        for role,folder in directories.items():
            states=[]
            for path in folder.glob('agent-*.json'):
                value=load(path)
                if set(value).issuperset({'schema_version','jobs','sessions','audit','requests'}):
                    app_schema(value,markers[4:],[ca_bytes,client_bytes,server_bytes]);states.append((path.name,value));state_count+=1
            final=load(folder/('agent-stopped-2.json' if role=='server' else 'agent-final.json'))
            final_audit=final['audit'];audit_counts[role]=len(final_audit)
            check(name+' '+role+' bounded safe audit fields and increasing sequences',len(final_audit)<=1024 and all(a['sequence']<b['sequence'] for a,b in zip(final_audit,final_audit[1:])))
            sessions=[s for s in final['sessions'] if s['role'] in ['import','incoming']]
            observed_metrics[role]=[{'session_id':s['id'],'profile':s['profile'],'operation_error':s['operation_error'],'cleanup':s['cleanup'],'metrics':s['metrics']} for s in sessions]
            check(name+' '+role+' actual positive retained application counters and separate operation/cleanup',len(sessions)==4 and all(s['metrics']['app_rx_bytes']>0 and s['metrics']['app_tx_bytes']>0 and s['metrics']['saturated'] is False and s['cleanup']=='verified' for s in sessions))
            metrics_by=collections.defaultdict(list)
            for _,state in states:
                for session in state['sessions']:metrics_by[session['id']].append((state['sequence'],session['metrics']))
            monotonic=True
            for observations in metrics_by.values():
                observations.sort(key=lambda item:item[0])
                for (_,a),(_,b) in zip(observations,observations[1:]):
                    monotonic &= all(a[k]<=b[k] for k in ['app_rx_bytes','app_tx_bytes','elapsed_millis']) and (not a['saturated'] or b['saturated'])
            check(name+' '+role+' collected same-session counter samples never go backward',monotonic)
        check(name+' state/audit snapshots contain only bounded payload-free schema',state_count>0,{'snapshots':state_count,'final_audit_counts':audit_counts})
        c=directories['client'];sv=directories['server']
        before=load(c/'agent-before-kill.json');after=load(c/'agent-after-kill.json');recovered=load(c/'client-serial-recovered.json')['response']['sessions'][0]
        old=next(s for s in before['sessions'] if s['profile']=='serial');lost=next(s for s in after['sessions'] if s['id']==old['id'])
        terminal=[entry['terminal'] for entry in load(c/'root-after-kill.json')['terminals'] if entry['terminal']['correlation']==old['attempts'][0]['correlation']]
        check(name+' actual owned SIGKILL137 preserves old active identity then exact root-only release proof',read(c/'wait-agent-kill.txt').strip()==b'137' and old['state']=='Connected' and old['terminal'] is False and old['cleanup'] is None and all(old[k]==lost[k]==recovered[k] for k in ['id','instance_id','job_id','device_key']) and lost['terminal'] is False and lost['cleanup'] is None and recovered['evidence']=='root_journal_proof' and len(terminal)==1 and terminal[0]['proof']=='native_import_released' and terminal[0]['resource_id']==recovered['attempts'][0]['resource_id'])
        targeted=load(sv/'server-target-stopped.json')['response']['sessions'];incoming=[s for s in targeted if s['role']=='incoming']
        check(name+' targeted HID stop leaves serial forwarding with independent cleanup',len(incoming)==2 and next(s for s in incoming if s['device_key']==keys[0])['cleanup']=='verified' and next(s for s in incoming if s['device_key']==keys[1])['state']=='Connected' and read(base/'ftdi-host-rx.bin')==b''.join(read(c/f'tx-{i}.bin') for i in [1,2,3]))
        records.append({'family':family,'directory':str(base),'actual_result':summary['result'],'guest_exit_codes':summary['guest_exit_codes'],'source_inputs':len(summary['source_inputs']),'indexed_artifacts':len(summary['artifact_manifest']),'binary_sha256':summary['binary_sha256'],'keys':keys,'public_client_leaf_sha256':pin,'captures':{role:condensed_capture(cap) for role,cap in captures.items()},'application_metrics':observed_metrics,'final_audit_counts':audit_counts,'scope_gap':'Actual runtime TLS JSON and key-mode snapshots absent; exact config is producer-source/payload-derived, not a raw /etc observation.'})
    except Exception as error:
        errors.append({'directory':str(base),'error':type(error).__name__+': '+str(error)});check(name+' independent parser completed',False,errors[-1])

check('all inspected raw/source bytes stay unchanged',all(Path(path).read_bytes()==Path(path).read_bytes() and len(Path(path).read_bytes())==meta['length'] and hashlib.sha256(Path(path).read_bytes()).hexdigest()==meta['sha256'] for path,meta in d.seen.items()))
report={'schema_version':1,'result':'PASS_WITH_COLLECTION_LIMITATIONS' if not errors and all(v['passed'] for v in checks) else 'FAIL','scope':'Independent security/raw review of completed eaa1936 agent Native runs; no runner/verifier/secure.pcap_review calls, no product/build/QEMU or repository writes.','head':HEAD,'checks':checks,'runs':records,'errors':errors,'original_artifacts':list(d.seen.values()),'reviewer_corrections':'Initial script/report retained with -initial: reviewer incorrectly expected an etc/client-pin.txt entry in executable/module-only payload provenance. Corrected to a source-derived pin construction check; actual pin file bytes are not claimed. Original run bytes unchanged.','limits':['TLS1.3 ServerHello version and ClientHello ALPN offer/SNI are plaintext observations. Encrypted ALPN selection, peer Certificate/CertificateVerify and TLS payload are not decrypted or independently proven by PCAP.','Public DER chain/EKU/SAN/guest-time validation is offline fixture validation, linked by audited payload/TLS/loader source; it is not extraction of the encrypted certificate handshake.','Actual /etc TLS JSON files, client-pin.txt bytes and key/config mode snapshots were not collected. Root manifest, helper policy, root resource receipts, executed init generation logic, public DER and live TLS streams are checked; actual TLS JSON/pin bytes/modes are not claimed.','The test grants its client principal both devices. It does not test a valid but ungranted remote certificate or arbitrary local/root bypass.','Absence of specified clear signatures/recognizable URB headers is bounded to these entire captured links, not a universal proof of no information leakage.','Metrics are successful application reads/writes including headers/partial writes, not USB payload/TLS bytes, throughput, RTT or heartbeat.','Killed serial-session counters are only last persisted samples; no fabricated final counters/old terminal0. Round2 closes the tty FD before KILL, so no particular USB URB/ioctl pending-at-KILL proof.','Minimal bounded field/known-fixture/DER/PEM scan of app state/audit cannot prove absence of every imaginable secret; raw USB test artifacts intentionally exist separately.','No real hardware G1/G2, ISO, crash FD revocation, or full Phase3 completion claim.']}
output=Path('/tmp/rustusb-agent-security-independent-review-20261023.json');output.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'result':report['result'],'checks':len(checks),'passed':sum(v['passed'] for v in checks),'runs':len(records),'errors':errors,'report':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}))
