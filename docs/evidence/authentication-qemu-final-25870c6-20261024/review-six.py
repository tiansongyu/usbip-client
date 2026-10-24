from pathlib import Path
import json,hashlib,base64,re,subprocess,struct

repo=Path('/home/ubuntu/github/usbip-client')
out=Path('/tmp/rustusb-authentication-six-raw-method-25870c6-20261024');out.mkdir(mode=0o700)
head=subprocess.check_output(['git','rev-parse','25870c6'],cwd=repo,text=True).strip()
paths={('native','ipv4'):Path('/tmp/rustusb-authentication-native-ipv4-fifth-25870c6-20261024')}
for backend in ['virtual','usbfs']:
    paths[(backend,'ipv4')]=Path(f'/tmp/rustusb-authentication-{backend}-ipv4-first-25870c6-20261024')
for backend in ['native','virtual','usbfs']:
    paths[(backend,'ipv6')]=Path(f'/tmp/rustusb-authentication-{backend}-ipv6-first-25870c6-20261024')
def sha(b):return hashlib.sha256(b).hexdigest()
def inventory():
    return {str(p):{'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for root in paths.values() for p in root.rglob('*') if p.is_file()}
before=inventory();(out/'originals-before.json').write_text(json.dumps(before,indent=2)+'\n')
checks=[];rows=[];selected=None;binary=None;total_negative=0;encoded_files=0
pattern=re.compile(rb'^SECURE_ARTIFACT_BEGIN ([a-zA-Z0-9_.-]+) ([0-9]+)\n(.*?)^SECURE_ARTIFACT_END \1\s*$',re.M|re.S)
def require(ok,label):
    if not ok:raise AssertionError(label)
    checks.append(label)
for (backend,family),root in paths.items():
    tag=backend+'/'+family;s=json.loads((root/'summary.json').read_text())
    require(s['result']=='PASS' and s['head']==s['head_after']==head,tag+' actual matching accepted head')
    require(s['guest_exit_codes']=={'server':0,'client':0},tag+' both guests exit0')
    require(all(s[k] for k in ['source_guard','binary_guard','head_guard','clean_guard']),tag+' all four actual guards')
    require(s['build_skipped'] is False and s['build_features']=='--workspace --all-features --release --locked',tag+' actual default locked build')
    require(s['backend']==backend and s['family']==family,tag+' actual requested backend/family')
    if selected is None:selected=s['source_inputs'];binary=s['binary_sha256']
    require(selected==s['source_inputs'] and binary==s['binary_sha256'],tag+' same201 source inputs and actual binaries')
    for name,x in s['artifact_manifest'].items():
        b=(root/name).read_bytes();require(len(b)==x['bytes'] and sha(b)==x['sha256'],tag+' original artifact '+name)
    for role in ['server','client']:
        raw=(root/f'{role}-evidence.log').read_bytes();count=0
        for match in pattern.finditer(raw):
            name,n,encoded=match.groups();b=base64.b64decode(b''.join(encoded.split()),validate=True)
            require(len(b)==int(n) and b==(root/f'{role}-artifacts'/name.decode()).read_bytes(),tag+' exact independent base64 '+role+'/'+name.decode());count+=1
        require(raw.count(b'SECURE_ARTIFACT_BEGIN ')==count,tag+' no incomplete '+role+' artifact block');encoded_files+=count
        require(b'RUSTUSB_SECURE_RESULT: FAIL' not in raw and f'RUSTUSB_SECURE_RESULT: PASS role={role}'.encode() in raw,tag+' original '+role+' terminal verdict')
    server=root/'server-artifacts';client=root/'client-artifacts'
    policies=[json.loads((server/f'auth-server-config-{n}.json').read_text()) for n in [1,2]]
    old=sha((root/'client-public.der').read_bytes());new=sha((root/'replacement-public.der').read_bytes())
    require(any(old in x['pins'] for x in policies[0]['principals']) and not any(old in x['pins'] for x in policies[1]['principals']),tag+' old exact DER pin removed')
    require(all(any(new in x['pins'] for x in policy['principals']) for policy in policies),tag+' exact replacement pin retained')
    waits=list(client.glob('auth-*.wait'));require(len(waits)==44,tag+'44 targeted actual negative child wait records');total_negative+=len(waits)
    for p in waits:
        require(p.read_text().strip() in (['0','1'] if any(t in p.name for t in ['unauthorized','duplicate']) else ['1']),tag+' actual child exit '+p.name)
    for label in ['free-1','held-1','free-2','held-2']:
        require((client/f'auth-client-before-{label}.txt').read_bytes()==(client/f'auth-client-after-{label}.txt').read_bytes(),tag+' unchanged client kernel '+label)
        for kind,status in [('unauthorized',4)]+([('duplicate',2)] if label.startswith('held') else []):
            b=(client/f'auth-{label}-{kind}.reply').read_bytes();require(b==struct.pack('>HHI',0x111,3,status),tag+' actual direct '+kind+' '+label)
    for epoch in [1,2]:
        require((client/f'rx-{epoch}.bin').read_bytes()==f'RUSTUSB-RX-{epoch}-0001:0123456789abcdef\n'.encode(),tag+' actual serial RX epoch'+str(epoch))
        require((client/f'tx-{epoch}.bin').read_bytes()==f'RUSTUSB-TX-{epoch}-0001:fedcba9876543210\n'.encode(),tag+' actual serial TX epoch'+str(epoch))
        data=(client/f'input-{epoch}.bin').read_bytes();require(len(data)%24==0,tag+' input event boundaries')
        events={struct.unpack('<qqHHi',data[i:i+24])[2:] for i in range(0,len(data),24)}
        require({(1,epoch+1,0),(1,epoch+1,1)}<=events,tag+' actual HID up/down epoch'+str(epoch))
        status=[line.split()[2] for line in (client/f'native-empty-{epoch}.txt').read_text().splitlines() if line.split() and line.split()[0] in ['hs','ss']]
        require(len(status)==16 and all(int(v)==4 for v in status),tag+' all16 actual ports NULL epoch'+str(epoch))
    if backend!='virtual':
        original=(server/'host-before.json').read_bytes()
        require(original==(server/'auth-host-after-retire.json').read_bytes()==(server/'host-final.json').read_bytes(),tag+' full original baseline at retirement/final')
    rows.append({'backend':backend,'family':family,'evidence':str(root),'head':head,'result':s['result'],'guest_exits':s['guest_exit_codes'],'targeted_negative_child_waits':44,'epochs':2,'io_rounds':2,'host_predicates':len(s['facts']['checks']),'raw_pcap_bytes':{role:(root/f'{role}-external.pcap').stat().st_size for role in ['server','client']}})
require(len(selected)==201,'all201 selected harness inputs')
for name,x in selected.items():
    b=subprocess.check_output(['git','show',head+':'+name],cwd=repo);require(len(b)==x['bytes'] and sha(b)==x['sha256'],'selected201 Git blob '+name)
software=json.loads(Path('/tmp/rustusb-authentication-software-3d7e126-20261024/inputs-before.json').read_text());require(len(software)==192,'actual software192 source count')
for x in software:require(selected[x['path']]=={'bytes':x['bytes'],'sha256':x['sha256']},'software192 selected subset '+x['path'])
after=inventory();require(before==after,'all actual originals unchanged by raw method')
(out/'originals-after.json').write_text(json.dumps(after,indent=2)+'\n')
report={'result':'PASS','scope':'Separately written standard-library raw/provenance method by the harness author. Does not import original runner/verifier, run product/QEMU, or change original verdicts. Predicates are not test cases.','head':head,'paired_runs':6,'guest_exits_zero':12,'credential_epochs':12,'io_rounds':12,'targeted_negative_child_waits':total_negative,'public_or_raw_original_files':len(before),'independently_decoded_guest_files':encoded_files,'method_predicates':len(checks),'same_selected_inputs':201,'same_software_source_build_subset':192,'binary_sha256':binary,'runs':rows,'checks':checks,'limits':['raw-v1 startup policy retirement, no CRL/OCSP/hot revocation','fast negative process execution is fixed setpriv/source/config plus waits, not individual executed ELF receipt','raw PCAP retained without mixed-handshake classifier verdict','original four failed development runs not relabelled','no hardwareG1/G2, fullG3 or later phase completion']}
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ['checks','runs']},indent=2))
