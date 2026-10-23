"""Constructed parser fixtures and ephemeral OpenSSL fixture, never guest results."""
from pathlib import Path
import ast, base64, datetime, hashlib, ipaddress, json, re, shutil, socket, struct, subprocess, tempfile
REPO=Path('/home/ubuntu/github/usbip-client')
source=REPO/'scripts/qemu/run-secure-qemu.py'
tree=ast.parse(source.read_text())
wanted={'digest','command','credentials','artifacts','pcap_review'}
ns=globals()
exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in wanted],type_ignores=[]),str(source),'exec'),ns)
report={'scope':'synthetic parser fixtures and real temporary OpenSSL generation only; no guest/build/probe/QEMU','checks':[]}
def check(name, fn, reject=False):
    try:
        result=fn()
    except Exception as error:
        if not reject: raise
        report['checks'].append({'name':name,'expected_rejection':type(error).__name__,'message':str(error)})
    else:
        if reject: raise AssertionError(name+' accepted bad fixture')
        report['checks'].append({'name':name,'passed':True})
def packet(version, source, destination, sport, dport, seq, flags, payload=b''):
    tcp=struct.pack('!HHIIBBHHH',sport,dport,seq,0,0x50,flags,65535,0,0)+payload
    if version==4:
        ip=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(tcp),0,0,64,6,0,socket.inet_aton(source),socket.inet_aton(destination)); kind=0x800
    else:
        ip=struct.pack('!IHBB16s16s',6<<28,len(tcp),6,64,socket.inet_pton(socket.AF_INET6,source),socket.inet_pton(socket.AF_INET6,destination)); kind=0x86dd
    return bytes(12)+struct.pack('!H',kind)+ip+tcp
def tls(kind, payload):return struct.pack('!BHH',kind,0x303,len(payload))+payload
def frames(version=4, marker=b'', gap=False):
    source,destination=('192.0.2.2','192.0.2.1') if version==4 else ('fd42:7275:7374::2','fd42:7275:7374::1')
    rows=[]
    for n in range(8):
        port=40000+n
        rows += [packet(version,source,destination,port,3241,1000,2),packet(version,destination,source,3241,port,2000,18)]
        c=tls(22,b'\x01rustusb-usbip/1')+tls(23,b'ciphertext-client'+marker)
        hello=b'\x03\x03'+bytes(32)+b'\x00\x13\x01\x00\x00\x06\x00\x2b\x00\x02\x03\x04'
        s=tls(22,b'\x02'+len(hello).to_bytes(3,'big')+hello)+tls(23,b'ciphertext-server')
        rows += [packet(version,source,destination,port,3241,1001,24,c[:9]),packet(version,source,destination,port,3241,1010+int(gap),24,c[9:]),packet(version,source,destination,port,3241,1001,24,c[:9]),packet(version,destination,source,3241,port,2001,24,s)]
    for v,a,b in [(4,'192.0.2.1','192.0.2.2'),(6,'fd42:7275:7374::1','fd42:7275:7374::2')]:rows.append(packet(v,a,b,3240,50000,0,20))
    return rows
def pcap(rows):return struct.pack('<IHHIIII',0xa1b2c3d4,2,4,0,0,65536,1)+b''.join(struct.pack('<IIII',0,0,len(row),len(row))+row for row in rows)
with tempfile.TemporaryDirectory(prefix='rustusb-secure-static-') as temp:
    root=Path(temp)
    path=root/'fixture.pcap'
    def review(rows,family='ipv4'):
        path.write_bytes(pcap(rows));return pcap_review(path,family,{65537,65538})
    check('constructed ipv4 segmented/retransmitted TLS framing',lambda:review(frames()))
    check('constructed ipv6 TLS framing',lambda:review(frames(6),'ipv6'))
    check('explicit clear test marker rejected',lambda:review(frames(marker=b'RUSTUSB-RX-')),True)
    check('explicit clear management rejected',lambda:review(frames(marker=b'\x01\x11\x80\x05\0\0\0\0')),True)
    check('explicit clear URB signature rejected',lambda:review(frames(marker=struct.pack('!IIIII',1,7,65537,1,1))),True)
    check('TCP gap rejected',lambda:review(frames(gap=True)),True)
    check('missing IPv6 refusal rejected',lambda:review(frames()[:-1]),True)
    check('wrong TLS address family rejected',lambda:review(frames(6)),True)
    rows=frames(); rows[2]=rows[2][:-1]
    check('truncated TLS bytes rejected',lambda:review(rows),True)
    evidence=root/'artifacts'; evidence.mkdir()
    for role in ['server','client']:(evidence/f'{role}-evidence.log').write_text('SECURE_ARTIFACT_BEGIN one.bin 3\nYWJj\nSECURE_ARTIFACT_END one.bin\n')
    check('exact base64 byte artifact extracted',lambda:artifacts(evidence))
    (evidence/'server-evidence.log').write_text('SECURE_ARTIFACT_BEGIN one.bin 3\nYWJj\n')
    check('truncated artifact rejected',lambda:artifacts(evidence),True)
    work=root/'openssl';work.mkdir(); evidence=root/'cert-evidence';evidence.mkdir()
    directory=credentials(work,evidence)
    check('OpenSSL actual generated server PKCS8 DER',lambda:subprocess.run(['openssl','pkey','-inform','DER','-in',str(directory/'server-key.der'),'-noout'],check=True,capture_output=True))
    check('OpenSSL actual server chain verified',lambda:subprocess.run(['openssl','verify','-CAfile',str(directory/'ca.pem'),'-purpose','sslserver','-verify_hostname','rustusb-qemu.test',str(directory/'server.pem')],check=True,capture_output=True))
    check('OpenSSL actual client chain verified',lambda:subprocess.run(['openssl','verify','-CAfile',str(directory/'ca.pem'),'-purpose','sslclient',str(directory/'client.pem')],check=True,capture_output=True))
    report['public_certificates']={name:digest(directory/name) for name in ['ca.der','server.der','client.der']}
    shutil.copyfile(evidence/'certificate-generation.log','/tmp/rustusb-secure-harness-openssl-static-20261023.log')
report['source_sha256']=digest(source)
Path('/tmp/rustusb-secure-harness-static-20261023.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'passed':len(report['checks']),'scope':report['scope']}))
