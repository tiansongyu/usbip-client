"""Strict stale-SYN separation; original PCAP bytes and USB predicates stay intact."""
import hashlib
import ipaddress
import json
import struct

GHOST_MAC=bytes.fromhex('525400000099')
CLIENT_MAC=bytes.fromhex('525400000062')

def project(path,family):
    data=path.read_bytes()
    if len(data)<24 or len(data)>64*1024*1024:
        raise RuntimeError('bounded complete raw PCAP required')
    endian='<' if data[:4]==b'\xd4\xc3\xb2\xa1' else '>' if data[:4]==b'\xa1\xb2\xc3\xd4' else None
    if endian is None:raise RuntimeError('microsecond PCAP required')
    _,major,minor,_,_,snaplen,network=struct.unpack(endian+'IHHIIII',data[:24])
    if (major,minor,network)!=(2,4,1) or snaplen<65535:raise RuntimeError('complete Ethernet capture required')
    healthy_version=4 if family=='ipv4' else 6
    bad='fd42:7275:7374::99' if healthy_version==4 else '192.0.2.99'
    client='fd42:7275:7374::2' if healthy_version==4 else '192.0.2.2'
    output=bytearray(data[:24]);offset=24;excluded=[];healthy={};stale={};packets=0
    while offset<len(data):
        start=offset
        if offset+16>len(data):raise RuntimeError('short packet header')
        sec,usec,size,original=struct.unpack(endian+'IIII',data[offset:offset+16]);offset+=16
        frame=data[offset:offset+size];offset+=size;packets+=1
        if usec>=1_000_000 or size!=original or len(frame)!=size or size<14 or size>65535:raise RuntimeError('truncated/invalid original packet')
        eth=int.from_bytes(frame[12:14],'big');ip=frame[14:];parsed=None
        if eth==0x0800:
            if len(ip)<20 or ip[0]>>4!=4:raise RuntimeError('invalid IPv4')
            h=(ip[0]&15)*4;end=int.from_bytes(ip[2:4],'big')
            if not 20<=h<=end<=len(ip):raise RuntimeError('invalid IPv4 size')
            if ip[9]==6:
                if int.from_bytes(ip[6:8],'big')&0x3fff:raise RuntimeError('fragmented TCP')
                parsed=(4,str(ipaddress.ip_address(ip[12:16])),str(ipaddress.ip_address(ip[16:20])),ip[h:end])
        elif eth==0x86dd:
            if len(ip)<40 or ip[0]>>4!=6:raise RuntimeError('invalid IPv6')
            end=40+int.from_bytes(ip[4:6],'big')
            if end>len(ip):raise RuntimeError('invalid IPv6 size')
            # Stale candidates must use a plain TCP header. The unchanged
            # classifier still validates any normal IPv6 control/extensions.
            if ip[6]==6:parsed=(6,str(ipaddress.ip_address(ip[8:24])),str(ipaddress.ip_address(ip[24:40])),ip[40:end])
        special=False
        if parsed:
            version,src,dst,tcp=parsed
            if len(tcp)<20:raise RuntimeError('short TCP')
            th=(tcp[12]>>4)*4
            if not 20<=th<=len(tcp):raise RuntimeError('invalid TCP size')
            sport,dport,seq,ack=struct.unpack('!HHII',tcp[:12]);flags=tcp[13];stamp=sec*1_000_000+usec
            key=(version,src,sport,dst,dport,seq)
            if src==bad or dst==bad:
                if not (version!=healthy_version and src==client and dst==bad and frame[:6]==GHOST_MAC and frame[6:12]==CLIENT_MAC and sport>0 and dport==3241 and flags in [2,0xc2] and ack==0 and len(tcp)==th):
                    raise RuntimeError('unaccounted stale-candidate frame; never discard payload/response')
                special=True;stale.setdefault(key,stamp)
                excluded.append({'packet':packets,'sha256':hashlib.sha256(data[start:offset]).hexdigest(),'timestamp_micros':stamp,'flow':list(key)})
            elif version==healthy_version and dport==3241 and flags&2 and not flags&16:
                expected_src='192.0.2.2' if healthy_version==4 else 'fd42:7275:7374::2'
                expected_dst='192.0.2.1' if healthy_version==4 else 'fd42:7275:7374::1'
                if src!=expected_src or dst!=expected_dst or frame[:6]!=bytes.fromhex('525400000061') or frame[6:12]!=CLIENT_MAC:
                    raise RuntimeError('healthy SYN differs from exact private client/server endpoint')
                healthy.setdefault(key,stamp)
        if not special:output.extend(data[start:offset])
    derived=path.with_name(path.stem+'-healthy-projection.pcap');derived.write_bytes(output)
    facts={'raw_sha256':hashlib.sha256(data).hexdigest(),'derived_sha256':hashlib.sha256(output).hexdigest(),'original_packets':packets,'excluded_empty_syn_packets':excluded,'stale_first_syn_micros':sorted(stale.values()),'healthy_first_syn_micros':sorted(healthy.values()),'scope':'Only exact empty client SYNs to the configured stale address/nonexistent MAC are excluded. Every retained packet record is byte-identical; original PCAP untouched.'}
    path.with_name(path.stem+'-projection.json').write_text(json.dumps(facts,indent=2)+'\n')
    return derived,facts

def verify(evidence,args,framed_v2=False):
    client=evidence/'client-artifacts'
    expected_bad='fd42:7275:7374::99' if args.family=='ipv4' else '192.0.2.99'
    expected_good='192.0.2.1' if args.family=='ipv4' else 'fd42:7275:7374::1'
    hosts=(client/'dual-hosts.txt').read_text().splitlines()
    if hosts!=['127.0.0.1 localhost','::1 localhost',expected_bad+' rustusb-dual-stack.test',expected_good+' rustusb-dual-stack.test']:raise RuntimeError('exact protected hostname inputs differ')
    if (client/'dual-nsswitch.txt').read_text()!='hosts: files\n':raise RuntimeError('unexpected resolver backend')
    expected_gai='precedence ::/0 100\nprecedence ::ffff:0:0/96 10\n' if args.family=='ipv4' else 'precedence ::/0 10\nprecedence ::ffff:0:0/96 100\n'
    if (client/'dual-gai.txt').read_text()!=expected_gai:raise RuntimeError('exact preferred-family policy differs')
    neighbor=(client/('dual-neighbors6.txt' if args.family=='ipv4' else 'dual-neighbors4.txt')).read_text()
    if expected_bad not in neighbor or '52:54:00:00:00:99' not in neighbor or 'PERMANENT' not in neighbor:raise RuntimeError('permanent nonexistent-MAC setup not observed')
    server_config=json.loads((evidence/'server-artifacts/server-config.json').read_text())
    configurations=[server_config]
    for profile in ['hid','serial']:
        config=json.loads((client/f'client-{profile}-config.json').read_text())
        configurations.append(config)
        if config['endpoint']!={'host':'rustusb-dual-stack.test','port':3241} or config['server_name']!='rustusb-qemu.test':raise RuntimeError('endpoint hostname/service-name contract differs')
    for config in configurations:
        if config['schema_version']!=(2 if framed_v2 else 1):raise RuntimeError('actual TLS schema differs')
        if framed_v2 and config.get('data_link')!={'protocol':'framed_v2','interval_millis':5000,'response_timeout_millis':15000}:raise RuntimeError('actual framed policy differs')
        if not framed_v2 and 'data_link' in config:raise RuntimeError('unexpected framed policy on raw case')
    _,facts=project(evidence/'client-external.pcap',args.family)
    stale=facts['stale_first_syn_micros'];healthy=facts['healthy_first_syn_micros']
    if not (len(stale)==len(healthy) and len(healthy)>=7):raise RuntimeError('each actual healthy TCP connection must follow one actual stale candidate')
    if not all(a<b and b-a<10_000_000 for a,b in zip(stale,healthy)):raise RuntimeError('healthy connection did not begin after stale SYN within original ten-second deadline')
    return {'protocol':'framed_v2' if framed_v2 else 'raw_v1','hostname':'rustusb-dual-stack.test','independent_tls_name':'rustusb-qemu.test','healthy_family':args.family,'stale_address':expected_bad,'chronological_syn_pairs':len(healthy),'projection':facts,'limits':['equal-count sorted chronology is aggregate evidence, not per-process/per-operation attribution','hosts-file OS resolution, not wire DNS','no VPN/hardware/performance/fullG3 claim','strict original USB/root proof remains independently required']}
