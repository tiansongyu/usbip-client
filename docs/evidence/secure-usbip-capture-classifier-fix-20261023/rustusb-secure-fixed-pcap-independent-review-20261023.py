#!/usr/bin/env python3
"""Existing PCAP independent Ethernet/IP/TCP/TLS decoder, no runner imports."""
from pathlib import Path
import collections
import hashlib
import ipaddress
import json
import re
import struct
import subprocess

BASE=Path('/tmp/rustusb-secure-qemu-virtual-ipv4-fixed-20261023')
ROOT=Path('/home/ubuntu/github/usbip-client')
checks=[]
seen={}


def read(path):
    path=Path(path); raw=path.read_bytes()
    seen[str(path)]={'path':str(path),'length':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    return raw


def load(path):
    return json.loads(read(path))


def check(name,condition,detail=None):
    item={'name':name,'passed':bool(condition)}
    if detail is not None:item['detail']=detail
    checks.append(item)


def require(condition,reason):
    if not condition:raise ValueError(reason)


def checksum(raw):
    raw+=b'\0'*(len(raw)%2)
    n=sum(struct.unpack('>'+str(len(raw)//2)+'H',raw))
    while n>>16:n=(n&65535)+(n>>16)
    return (~n)&65535


def extensions(raw):
    require(len(raw)>=2,'missing extension vector')
    size=int.from_bytes(raw[:2],'big');require(size==len(raw)-2,'extension length')
    found={};cursor=2
    while cursor<len(raw):
        require(cursor+4<=len(raw),'extension header')
        kind,size=struct.unpack('>HH',raw[cursor:cursor+4]);cursor+=4
        require(cursor+size<=len(raw) and kind not in found,'extension overrun/duplicate')
        found[kind]=raw[cursor:cursor+size];cursor+=size
    return found


def hello(body,server):
    require(len(body)>=35,'hello short')
    version=int.from_bytes(body[:2],'big');cursor=35+body[34]
    require(cursor<=len(body),'session id')
    if server:
        require(cursor+3<=len(body),'server cipher')
        cipher=int.from_bytes(body[cursor:cursor+2],'big');cursor+=3
        ext=extensions(body[cursor:]);require(ext.get(43)==b'\x03\x04','TLS1.3 selection absent')
        return {'legacy_version':version,'selected_version':772,'cipher_suite':cipher}
    require(cursor+2<=len(body),'client ciphers')
    size=int.from_bytes(body[cursor:cursor+2],'big');cursor+=2+size
    require(cursor<len(body),'client compression');cursor+=1+body[cursor]
    ext=extensions(body[cursor:]);versions=ext.get(43,b'')
    require(versions and versions[0]==len(versions)-1 and len(versions[1:])%2==0,'client versions')
    offered=[int.from_bytes(versions[i:i+2],'big') for i in range(1,len(versions),2)]
    require(772 in offered,'client lacks TLS1.3')
    alpn=ext.get(16,b'');require(len(alpn)>=3 and int.from_bytes(alpn[:2],'big')==len(alpn)-2,'ALPN vector')
    protocols=[];cursor=2
    while cursor<len(alpn):
        size=alpn[cursor];cursor+=1;require(size>0 and cursor+size<=len(alpn),'ALPN name')
        protocols.append(alpn[cursor:cursor+size].decode('ascii'));cursor+=size
    require(protocols==['rustusb-usbip/1'],'wrong ALPN offer')
    return {'legacy_version':version,'offered_versions':offered,'alpn_offered':protocols}


def tls_stream(raw,server,markers):
    require(raw and raw[0]==22,'application does not begin TLS handshake')
    for name,marker in markers:
        require(marker not in raw,'clear marker '+name)
    # Independent structural search for recognizable USB/IP base headers.
    for i in range(max(0,len(raw)-19)):
        command,seq,devid,direction,endpoint=struct.unpack('>IIIII',raw[i:i+20])
        require(not (command in (1,2,3,4) and devid in (65537,65538) and direction in (0,1) and endpoint<=15),'recognizable clear URB header')
    cursor=0; types=collections.Counter();plaintext=b''
    while cursor<len(raw):
        require(cursor+5<=len(raw),'partial TLS record')
        kind,version,length=struct.unpack('>BHH',raw[cursor:cursor+5]);cursor+=5
        require(kind in (20,21,22,23) and version in (769,770,771) and length<=16640 and cursor+length<=len(raw),'TLS record bounds')
        value=raw[cursor:cursor+length];cursor+=length;types[kind]+=1
        if kind==22:plaintext+=value
        if kind==20:require(value==b'\x01','invalid compatibility CCS')
    require(types[23]>0,'no encrypted application records')
    require(len(plaintext)>=4,'no complete plaintext handshake')
    wanted=2 if server else 1
    require(plaintext[0]==wanted,'wrong first hello')
    length=int.from_bytes(plaintext[1:4],'big');require(4+length==len(plaintext),'hello incomplete/additional plaintext handshake')
    return {'wire_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'record_types':dict(types),'hello':hello(plaintext[4:],server),'specified_clear_markers_absent':True,'recognizable_urb_headers_absent':True}


def capture(path,markers):
    raw=read(path);require(len(raw)>=24,'PCAP global short')
    endian={b'\xd4\xc3\xb2\xa1':'<',b'\xa1\xb2\xc3\xd4':'>'}.get(raw[:4]);require(endian is not None,'PCAP encoding')
    major,minor,zone,sig,snap,network=struct.unpack(endian+'HHIIII',raw[4:24]);require((major,minor,network)==(2,4,1) and snap>=65535,'PCAP format')
    cursor=24; packets=0; protocol_counts=collections.Counter();controls=[];streams={};rst=[];plain_syn=[];tcp_checksums=collections.Counter()
    while cursor<len(raw):
        require(cursor+16<=len(raw),'PCAP packet header')
        sec,usec,captured,original=struct.unpack(endian+'IIII',raw[cursor:cursor+16]);cursor+=16
        frame=raw[cursor:cursor+captured];cursor+=captured;packets+=1
        require(captured==original==len(frame) and len(frame)>=14,'truncated frame')
        kind=int.from_bytes(frame[12:14],'big');packet=frame[14:];protocol_counts[hex(kind)]+=1
        if kind==2054:
            require(len(packet)>=28 and packet[:6]==b'\x00\x01\x08\x00\x06\x04' and int.from_bytes(packet[6:8],'big') in (1,2),'unknown ARP')
            continue
        if kind==2048:
            require(len(packet)>=20 and packet[0]>>4==4,'IPv4 header')
            ihl=(packet[0]&15)*4;total=int.from_bytes(packet[2:4],'big')
            require(ihl>=20 and total>=ihl and total<=len(packet) and packet[9]==6 and int.from_bytes(packet[6:8],'big')&0x3fff==0,'IPv4 packet/protocol/fragment')
            require(checksum(packet[:ihl])==0,'IPv4 checksum')
            src=packet[12:16];dst=packet[16:20];tcp=packet[ihl:total];family=4
            pseudo=src+dst+b'\0\x06'+len(tcp).to_bytes(2,'big')
        elif kind==34525:
            require(len(packet)>=40 and packet[0]>>4==6,'IPv6 header')
            length=int.from_bytes(packet[4:6],'big');require(40+length<=len(packet),'IPv6 length')
            src=packet[8:24];dst=packet[24:40];next_header=packet[6];payload=packet[40:40+length];hop=packet[7]
            extension=None
            if next_header==0:
                require(len(payload)>=8,'short HBH');size=8*(payload[1]+1);require(size<=len(payload),'HBH length')
                extension=payload[:size];next_header=extension[0];payload=payload[size:]
                require(extension==bytes.fromhex('3a00050200000100') and next_header==58,'unknown IPv6 extension or HBH contents')
            if next_header==58:
                require(len(payload)>=8 and payload[1]==0,'ICMPv6 short/code')
                require(checksum(src+dst+len(payload).to_bytes(4,'big')+b'\0\0\0\x3a'+payload)==0,'ICMPv6 checksum')
                item={'packet':packets,'source':str(ipaddress.ip_address(src)),'destination':str(ipaddress.ip_address(dst)),'hop_limit':hop,'icmp_type':payload[0],'icmp_code':payload[1],'checksum_valid':True,'icmp_length':len(payload)}
                if extension is not None:
                    require(payload[0]==143 and hop==1 and ipaddress.ip_address(src).is_link_local and dst==ipaddress.ip_address('ff02::16').packed,'non MLD extension traffic')
                    require(payload[4:6]==b'\0\0','MLDv2 reserved');count=int.from_bytes(payload[6:8],'big');pos=8;records=[]
                    for _ in range(count):
                        require(pos+20<=len(payload),'MLD record short');rtype,aux,sources=struct.unpack('>BBH',payload[pos:pos+4]);address=payload[pos+4:pos+20];pos+=20+16*sources+4*aux
                        require(pos<=len(payload) and rtype==4 and aux==sources==0 and ipaddress.ip_address(address).is_multicast,'unexpected MLD record')
                        records.append({'record_type':rtype,'meaning':'CHANGE_TO_EXCLUDE_MODE','aux_words':aux,'source_count':sources,'multicast_address':str(ipaddress.ip_address(address))})
                    require(pos==len(payload) and count==2,'MLDv2 trailing/count')
                    item.update({'hbh_hex':extension.hex(),'router_alert_type':5,'router_alert_length':2,'router_alert_value':0,'padding':'PadN type1 length0','records':records})
                else:
                    require(payload[0] in (133,135,136) and hop==255,'unexpected bare ICMPv6')
                    item['meaning']={133:'Router Solicitation',135:'Neighbor Solicitation',136:'Neighbor Advertisement'}[payload[0]]
                    if payload[0]==133:require(len(payload)==16 and payload[4:8]==b'\0'*4 and payload[8:10]==b'\x01\x01','RS shape')
                    else:require(len(payload)==32 and payload[24:26] in (b'\x01\x01',b'\x02\x01'),'ND shape')
                controls.append(item);continue
            require(next_header==6 and extension is None,'unexpected IPv6 protocol')
            tcp=payload;family=6;pseudo=src+dst+len(tcp).to_bytes(4,'big')+b'\0\0\0\x06'
        else:raise ValueError('unknown Ethernet protocol')
        require(len(tcp)>=20,'short TCP');sport,dport,seq,ack=struct.unpack('>HHII',tcp[:12]);offset=(tcp[12]>>4)*4;flags=tcp[13]
        require(20<=offset<=len(tcp),'TCP offset');content=tcp[offset:];tcp_checksums['valid' if checksum(pseudo+tcp)==0 else 'invalid_or_offloaded']+=1
        if 3240 in (sport,dport):
            require(not content,'3240 carried application data')
            if flags&2 and dport==3240:plain_syn.append({'family':family,'source_port':sport,'sequence':seq,'packet':packets})
            if flags&4 and sport==3240:rst.append({'family':family,'destination_port':dport,'ack':ack,'packet':packets})
            continue
        require(3241 in (sport,dport) and family==4,'unexpected TCP application port/family')
        key=(family,str(ipaddress.ip_address(src)),sport,str(ipaddress.ip_address(dst)),dport)
        stream=streams.setdefault(key,{'isn':None,'parts':[],'segments':0})
        stream['segments']+=1
        if flags&2:
            require(stream['isn'] in (None,seq),'TCP tuple reused');stream['isn']=seq
        if content:stream['parts'].append(((seq+(1 if flags&2 else 0))&0xffffffff,content))
    require(cursor==len(raw),'PCAP extra bytes')
    require({i['family'] for i in rst}=={4,6},'missing both RST families')
    for syn in plain_syn:
        require(any(r['family']==syn['family'] and r['destination_port']==syn['source_port'] and r['ack']==((syn['sequence']+1)&0xffffffff) for r in rst),'RST not matching SYN')
    output=[]
    for key,state in sorted(streams.items()):
        require(state['isn'] is not None,'missing SYN')
        assembled=bytearray();retransmit=0
        for seq,part in sorted(state['parts'],key=lambda p:(p[0]-state['isn']-1)&0xffffffff):
            pos=(seq-state['isn']-1)&0xffffffff;require(pos<=len(assembled),'TCP missing bytes')
            overlap=min(len(part),len(assembled)-pos);require(assembled[pos:pos+overlap]==part[:overlap],'conflicting TCP retransmit')
            retransmit+=overlap;assembled.extend(part[overlap:])
        decoded=tls_stream(bytes(assembled),key[2]==3241,markers)
        output.append({'tuple':list(key),'segments':state['segments'],'retransmit_overlap_bytes':retransmit,**decoded})
    require(len(output)>=16 and len(output)%2==0,'too few bidirectional TLS flows')
    return {'packets':packets,'ether_types':dict(protocol_counts),'controls':controls,'tcp_checksum_counts':dict(tcp_checksums),'plain_3240_syn':plain_syn,'plain_3240_rst':rst,'TLS_directional_flows':output,'all_other_payload_accounted':True}


summary=load(BASE/'summary.json')
check('original wholeFAIL exactly parser IPv6 exception, two guest0 and frozen guards',
      summary['result']=='FAIL' and summary['error']=='RuntimeError: unexpected external IPv6 extension/protocol'
      and summary['guest_exit_codes']=={'client':0,'server':0}
      and all(summary[k] is True for k in ['source_guard','binary_guard','head_guard','clean_guard']))
markers=[('standard_DEVLIST_request',bytes.fromhex('0111800500000000')),('standard_DEVLIST_reply',bytes.fromhex('0111000500000000')),('standard_IMPORT_request',bytes.fromhex('0111800300000000')),('standard_IMPORT_reply',bytes.fromhex('0111000300000000'))]
for cycle in (1,2):
    for name in ('rx','tx','echo'):
        value=read(BASE/'client-artifacts'/f'{name}-{cycle}.bin');require(len(value)==35,'fixture not35')
        markers.append((f'actual_test_fixture_{name}_{cycle}',value))
captures={}
for role in ('client','server'):
    captures[role]=capture(BASE/(role+'-external.pcap'),markers)
    c=captures[role]
    check(role+' full capture decoded: 533 frames, exact8 HBHMLDv2 +5ND, TLSIPv4 and dual3240RST',c['packets']==533 and len([x for x in c['controls'] if 'hbh_hex' in x])==8 and len(c['controls'])==13)
    check(role+' complete TLS records, CHALPNoffer and SH1.3, specified signatures absent',all(f['specified_clear_markers_absent'] and f['recognizable_urb_headers_absent'] for f in c['TLS_directional_flows']))
check('both sides reconstruct exactly same TCP/TLS byte streams',
      {(tuple(f['tuple']),f['sha256'],f['wire_bytes']) for f in captures['client']['TLS_directional_flows']}
      == {(tuple(f['tuple']),f['sha256'],f['wire_bytes']) for f in captures['server']['TLS_directional_flows']})

# Source/archive identity and selected actual role/config observations only.
source_mismatches=[]
for name,entry in summary['source_inputs'].items():
    actual=read(BASE/'source'/name)
    committed=subprocess.check_output(['git','show',summary['head']+':'+name],cwd=ROOT)
    if len(actual)!=entry['bytes'] or hashlib.sha256(actual).hexdigest()!=entry['sha256'] or actual!=committed:source_mismatches.append(name)
check('125 captured run source inputs equal exact clean Git commit',len(summary['source_inputs'])==125 and not source_mismatches,source_mismatches)
client_policy=load(BASE/'client-artifacts/helper-policy.json')
server_config=load(BASE/'server-artifacts/server-config.json')
check('actual dedicated broker policy separate and untrusted legacy network false',client_policy['secure_broker_uid']==2000 and client_policy['allowed_uids']==[1000] and client_policy['trusted_network'] is False and client_policy['secure_imports'][0]['device_keys']==['virtual:hid','virtual:cdc-acm'])
check('actual server config exact IPv4 TLS endpoint/pool and public client leaf pin',server_config['broker_uid']==2000 and server_config['listen']=='192.0.2.1:3241' and server_config['devices']==['virtual:hid','virtual:cdc-acm'] and server_config['principals'][0]['pins']==[hashlib.sha256(read(BASE/'client-public.der')).hexdigest()])
for profile,key in [('hid','virtual:hid'),('serial','virtual:cdc-acm')]:
    config=load(BASE/'client-artifacts'/f'client-{profile}-config.json')
    check(profile+' actual fixed servername/logicalserver/key/endpoint',config['broker_uid']==2000 and config['server_id']=='qemu-server' and config['server_name']=='rustusb-qemu.test' and config['endpoint']=={'host':'192.0.2.1','port':3241} and config['device']==key)
for role,files in [('client',['helper','hid-1','hid-2','serial-1','serial-2']),('server',['server'])]:
    for name in files:
        status=read(BASE/(role+'-artifacts')/('identity-'+name+'.txt')).decode()
        uid=0 if name=='helper' else 2000
        check(role+' '+name+' actual OSuid and unprivileged broker capEff',re.findall(r'^Uid:\s+(.+)$',status,re.M)[0].split()==[str(uid)]*4 and (uid==0 or re.search(r'^CapEff:\s+0+$',status,re.M)))
        exe=read(BASE/(role+'-artifacts')/('executable-'+name+'.txt')).decode()
        expected=summary['binary_sha256']['rustusb-helper' if name=='helper' else 'rustusb']
        check(role+' '+name+' actual /proc executable SHA matches run ELF',bool(re.fullmatch(re.escape(expected)+r'  /proc/[0-9]+/exe\n',exe)))
check('actual nonbroker request denied and prior Native rows unchanged',load(BASE/'client-artifacts/denied-response.json')['ok'] is False and read(BASE/'client-artifacts/native-before-denied.txt')==read(BASE/'client-artifacts/native-after-denied.txt'))
for role in ('client','server'):
    log=read(BASE/(role+'-evidence.log')).decode()
    check(role+' raw terminal guestPASS and credential isolation assertions',f'RUSTUSB_SECURE_RESULT: PASS role={role}\n' in log and 'RUSTUSB_SECURE_RESULT: FAIL' not in log and f'SECURE_CREDENTIAL_ISOLATION role={role} own_key=true opposite_key=false ca_key=false' in log)
    for cycle in (1,2):
        if role=='client':
            check(f'client cycle{cycle} actual IO/release/reuse markers',f'SECURE_CLIENT_IO cycle={cycle} key_code={cycle+1} rx=35 tx=35' in log and f'SECURE_CLIENT_RELEASED cycle={cycle} signal=SIGTERM wait=0 kernel_null=true local_gone=true' in log and f'SECURE_CLIENT_REUSE cycle={cycle}' in log)
            check(f'client cycle{cycle} kernel journal truly empty',load(BASE/'client-artifacts'/f'channels-empty-{cycle}.json')=={'schema_version':1,'records':[],'exports':[]})
        else:check(f'server cycle{cycle} actual IO/released markers',f'SECURE_SERVER_IO cycle={cycle}' in log and f'SECURE_SERVER_RELEASED cycle={cycle}' in log)
check('all read original artifacts unchanged across independent review',all(len(Path(p).read_bytes())==r['length'] and hashlib.sha256(Path(p).read_bytes()).hexdigest()==r['sha256'] for p,r in seen.items()))
report={'schema_version':1,'result':'SCOPED_CHECKS_PASS_ORIGINAL_WHOLE_RUN_FAIL_RETAINED' if all(c['passed'] for c in checks) else 'INDEPENDENT_CHECK_FAIL',
        'original_result':summary['result'],'original_error':summary['error'],'head':summary['head'],'guest_exit_codes':summary['guest_exit_codes'],
        'scope':'Independent complete-PCAP decoder and selected actual identity/config/raw evidence; no import/call of runner pcap_review or verifier, no product/build/QEMU/USB execution.',
        'captures':captures,'checks':checks,'counts':{'checks':len(checks),'passed':sum(c['passed'] for c in checks),'failed':sum(not c['passed'] for c in checks)},
        'references':[{'url':'https://www.rfc-editor.org/rfc/rfc3810.html#section-5.2','supports':'MLDv2 type143, record layout/type4, group ff02::16, hoplimit1 and Router Alert requirements'}, {'url':'https://www.rfc-editor.org/rfc/rfc2711.html#section-2','supports':'HBH Router Alert type5 length2 value0 for multicast listener discovery'}, {'url':'https://www.rfc-editor.org/rfc/rfc8446.html#section-4.2.1','supports':'ServerHello supported_versions selection0x0304 and TLS1.3 record interpretation'}, {'url':'https://www.rfc-editor.org/rfc/rfc7301.html#section-3.1','supports':'ALPN extension16 length-prefixed protocol vector'}],
        'limits':['Original FAIL is never rewritten: successful offline scoped checks are not a new clean-run acceptance result.','No TLS keylogging/decryption; capture proves visible ClientHello ALPN offer and TLS1.3 ServerHello selection, not the encrypted ALPN selection or peer certificate validation by packet inspection alone.','Recognizable exact management/header/actual35B test fixture signatures absent is a finite check, not a proof against every possible plaintext leak.','This virtual/IPv4 run has actual IPv4 TLS only; IPv6 observations here are control packets and3240 refusal, not IPv6 TLS or physical/Native server acceptance.','UID/capability and /proc ELF SHA are retained guest observations correlated to run manifest, not independently downloadable executable bytes.','Credential file modes are enforced by loader source and startup success; this selected report does not invent missing independent stat output.','Published source guards describe clean runstart/final; current post-run runner edits do not change those original bytes or guards.'],
        'artifacts':[seen[p] for p in sorted(seen)]}
out=Path('/tmp/rustusb-secure-fixed-pcap-independent-review-20261023.json');out.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'path':str(out),'result':report['result'],'counts':report['counts'],'length':out.stat().st_size,'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'flows_per_capture':[len(c['TLS_directional_flows']) for c in captures.values()]}))
for item in checks:
    if not item['passed']:print('FAILED',item)
