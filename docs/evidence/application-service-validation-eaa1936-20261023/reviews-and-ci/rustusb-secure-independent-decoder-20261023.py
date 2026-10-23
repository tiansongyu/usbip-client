#!/usr/bin/env python3
"""Independent read-only decoder, generalized family/device IDs and observed counts; no runner import."""
from pathlib import Path
import collections
import hashlib
import ipaddress
import json
import re
import struct
import subprocess

BASE=None
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


def hello(body,server,expected_name):
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
    sni=ext.get(0,b'');require(len(sni)>=5 and int.from_bytes(sni[:2],'big')==len(sni)-2 and sni[2]==0 and int.from_bytes(sni[3:5],'big')==len(sni)-5,'SNI vector');require(sni[5:].decode('ascii')==expected_name,'SNI differs from configured verification name')
    return {'legacy_version':version,'offered_versions':offered,'alpn_offered':protocols,'sni':expected_name}


def tls_stream(raw,server,markers,device_ids,expected_name):
    require(raw and raw[0]==22,'application does not begin TLS handshake')
    for name,marker in markers:
        require(marker not in raw,'clear marker '+name)
    # Independent structural search for recognizable USB/IP base headers.
    for i in range(max(0,len(raw)-19)):
        command,seq,devid,direction,endpoint=struct.unpack('>IIIII',raw[i:i+20])
        require(not (command in (1,2,3,4) and devid in device_ids and direction in (0,1) and endpoint<=15),'recognizable clear URB header')
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
    return {'wire_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'record_types':dict(types),'hello':hello(plaintext[4:],server,expected_name),'specified_clear_markers_absent':True,'recognizable_urb_headers_absent':True}


def capture(path,markers,expected_family,device_ids,expected_name):
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
                        require(pos<=len(payload) and rtype in range(1,7) and aux==0 and sources<=64 and ipaddress.ip_address(address).is_multicast,'unexpected MLD record')
                        records.append({'record_type':rtype,'meaning':{1:'MODE_IS_INCLUDE',2:'MODE_IS_EXCLUDE',3:'CHANGE_TO_INCLUDE_MODE',4:'CHANGE_TO_EXCLUDE_MODE',5:'ALLOW_NEW_SOURCES',6:'BLOCK_OLD_SOURCES'}[rtype],'aux_words':aux,'source_count':sources,'multicast_address':str(ipaddress.ip_address(address))})
                    require(pos==len(payload) and 1<=count<=64,'MLDv2 trailing/count')
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
        require(3241 in (sport,dport) and family==expected_family,'unexpected TCP application port/family')
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
        decoded=tls_stream(bytes(assembled),key[2]==3241,markers,device_ids,expected_name)
        output.append({'tuple':list(key),'segments':state['segments'],'retransmit_overlap_bytes':retransmit,**decoded})
    require(output and len(output)%2==0,'missing bidirectional TLS flows')
    keys={tuple(f['tuple']) for f in output}
    require(all((k[0],k[3],k[4],k[1],k[2]) in keys for k in keys),'missing opposite TCP direction')
    return {'packets':packets,'ether_types':dict(protocol_counts),'controls':controls,'tcp_checksum_counts':dict(tcp_checksums),'plain_3240_syn':plain_syn,'plain_3240_rst':rst,'TLS_directional_flows':output,'all_other_payload_accounted':True}


