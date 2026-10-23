"""Posthoc original PCAP reads and constructed parser fixtures, never QEMU verdicts."""
from pathlib import Path
import ast,hashlib,ipaddress,json,socket,struct,tempfile
ROOT=Path('/home/ubuntu/github/usbip-client')
SOURCE=ROOT/'scripts/qemu/run-secure-qemu.py'
def functions(path,names):
 tree=ast.parse(path.read_text())
 exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),str(path),'exec'),globals())
functions(SOURCE,{'pcap_review'})
functions(Path('/tmp/rustusb-secure-harness-static-20261023.py'),{'packet','tls','frames','pcap'})
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
BASE=Path('/tmp/rustusb-secure-qemu-virtual-ipv4-fixed-20261023')
originals={name:sha(BASE/name) for name in ['summary.json','server-external.pcap','client-external.pcap']}
report={'scope':'posthoc parser verification on unchanged original FAIL capture plus synthetic fixtures; no Cargo/QEMU/probe/USB, not new guest evidence','original_sha256':originals,'checks':[]}
catalog=json.loads((BASE/'client-artifacts/catalog-initial.json').read_text())
ids={(x['device']['bus_number']<<16)|x['device']['device_number'] for x in catalog}
report['actual_posthoc']={role:pcap_review(BASE/f'{role}-external.pcap','ipv4',ids) for role in ['server','client']}
assert all(r['packets']==533 and r['ipv6_hop_by_hop_headers']==8 and r['icmpv6_control_types']=={'143_mldv2_report':8,'133_router_solicitation':3,'135_neighbor_solicitation':1,'136_neighbor_advertisement':1} for r in report['actual_posthoc'].values())

def wrap(row,ext=b'\x06\x00\x01\x04\0\0\0\0',protocol=0):
 assert row[12:14]==b'\x86\xdd'
 b=bytearray(row);b[20]=protocol;b[18:20]=(len(row)-54+len(ext)).to_bytes(2,'big');return bytes(b[:54])+ext+bytes(b[54:])
def ipv6(protocol,body,hop=1,src='fe80::1',dst='ff02::16'):
 return bytes(12)+b'\x86\xdd'+struct.pack('!IHBB16s16s',6<<28,len(body),protocol,hop,socket.inet_pton(socket.AF_INET6,src),socket.inet_pton(socket.AF_INET6,dst))+body
def checksum_control(body,src='fe80::1',dst='ff02::16'):
 body=body[:2]+b'\0\0'+body[4:]
 b=socket.inet_pton(socket.AF_INET6,src)+socket.inet_pton(socket.AF_INET6,dst)+len(body).to_bytes(4,'big')+b'\0\0\0\x3a'+body
 if len(b)%2:b+=b'\0'
 c=sum(int.from_bytes(b[i:i+2],'big') for i in range(0,len(b),2))
 while c>>16:c=(c&65535)+(c>>16)
 return body[:2]+(c^65535).to_bytes(2,'big')+body[4:]
def mld(body=None,hop=1,alert=b'\x3a\x00\x05\x02\0\0\x01\0'):
 if body is None:body=b'\x8f\0\0\0\0\0\0\x01'+b'\x04\0\0\0'+socket.inet_pton(socket.AF_INET6,'ff02::1:ff00:1')
 return ipv6(0,alert+checksum_control(body),hop)
with tempfile.TemporaryDirectory(prefix='rustusb-pcap-extension-fixtures-') as temp:
 path=Path(temp)/'fixture.pcap'
 def check(name,rows,family='ipv6',error=None):
  path.write_bytes(pcap(rows))
  try:r=pcap_review(path,family,{65537,65538})
  except RuntimeError as e:
   if error is None or error not in str(e):raise AssertionError((name,str(e),error)) from e
   report['checks'].append({'name':name,'expected_rejection':str(e)})
  else:
   if error is not None:raise AssertionError(name+' incorrectly accepted')
   report['checks'].append({'name':name,'accepted':True,'flows':len(r['flows']),'control_types':r['icmpv6_control_types']})
 base=frames(6)
 wrapped=[wrap(r) if r[12:14]==b'\x86\xdd' else r for r in base]
 check('HBH padding-wrapped TCP retains segmented retransmitted complete TLS inspection',wrapped)
 for marker,name in [(b'RUSTUSB-RX-','fixture'),(b'\x01\x11\x80\x05\0\0\0\0','DEVLIST'),(struct.pack('!IIIII',1,7,65537,1,1),'URB')]:
  rows=[wrap(r) if r[12:14]==b'\x86\xdd' else r for r in frames(6,marker=marker)]
  check('HBH-wrapped '+name+' plaintext rejected',rows,error='clear')
 check('HBH-wrapped TCP cannot hide wrong selected family',wrapped,'ipv4',error='wrong IP family')
 check('legitimate bounded MLDv2 report counted',base+[mld()])
 for typ,body,dst in [(133,b'\x85\0\0\0'+bytes(4)+b'\x01\x01'+bytes(6),'ff02::2'),(135,b'\x87\0\0\0'+bytes(4)+socket.inet_pton(socket.AF_INET6,'fd42::1')+b'\x01\x01'+bytes(6),'ff02::1:ff00:1'),(136,b'\x88\0\0\0'+b'\x60\0\0\0'+socket.inet_pton(socket.AF_INET6,'fd42::1')+b'\x02\x01'+bytes(6),'fe80::2')]:
  good=ipv6(58,checksum_control(body,dst=dst),255,dst=dst)
  check('NDP '+str(typ)+' parsed and counted',base+[good])
  bad=bytearray(good);bad[21]=64
  check('NDP '+str(typ)+' wrong hop limit rejected',base+[bytes(bad)],error='hop limit')
 check('MLDv2 checksum corruption rejected',base+[mld()[:-1]+b'\x02'],error='checksum')
 check('MLDv2 wrong hop limit rejected',base+[mld(hop=255)],error='link scope')
 check('MLDv2 missing Router Alert rejected',base+[mld(alert=b'\x3a\0\x01\x04\0\0\0\0')],error='link scope')
 goodbody=b'\x8f\0\0\0\0\0\0\x01'+b'\x04\0\0\0'+socket.inet_pton(socket.AF_INET6,'ff02::1:ff00:1')
 check('MLDv2 extra trailing payload rejected',base+[mld(goodbody+b'USBIP')],error='trailing')
 check('MLDv2 source count overrun rejected',base+[mld(goodbody[:10]+b'\0\x10'+goodbody[12:])],error='record shape')
 check('MLDv2 too many records rejected',base+[mld(goodbody[:6]+b'\0\x11'+goodbody[8:])],error='record count')
 check('ICMPv6 echo payload not silently accepted',base+[ipv6(58,checksum_control(b'\x80\0\0\0'+bytes(4)))],error='control type')
 for proto,name in [(17,'UDP'),(44,'fragment'),(43,'routing'),(60,'destination options'),(50,'ESP'),(51,'AH'),(59,'no next header'),(253,'unknown')]:
  check('IPv6 '+name+' rejected',base+[ipv6(proto,bytes(8))],error='unaccounted IPv6')
 for body,name,error in [(b'\x06','truncated header','truncated IPv6'),(b'\x06\x01'+bytes(6),'length overrun','length exceeds'),(b'\x06\x20'+bytes(262),'header exceeds 256 bytes','length exceeds'),(b'\x06\0\x01\x05'+bytes(4),'option overrun','option overrun'),(b'\x06\0\x01\x04\0\0\0\x01','nonzero PadN','nonzero'),(b'\x06\0\x22\x04'+bytes(4),'unknown option','unaccounted'),(b'\x00\0\x01\x04'+bytes(4),'second HBH','unaccounted'),(b'\x06\0\x05\x02\0\0\x01\0','Router Alert on TCP','Router Alert on TCP')]:
  check('HBH '+name+' rejected',base+[ipv6(0,body)],error=error)
 for protocol in [1,17,253]:
  row=bytearray(frames()[0]);row[23]=protocol
  check('IPv4 protocol '+str(protocol)+' no longer silently skipped',frames()+[bytes(row)],'ipv4',error='unaccounted external IPv4 protocol')
report['original_unchanged']=originals=={name:sha(BASE/name) for name in originals}
assert report['original_unchanged'] and json.loads((BASE/'summary.json').read_text())['result']=='FAIL'
report['original_result']='FAIL (unchanged)'
report['source_sha256']=sha(SOURCE)
report['references']=['https://www.rfc-editor.org/rfc/rfc8200.html','https://www.rfc-editor.org/rfc/rfc3810.html','https://www.rfc-editor.org/rfc/rfc4861.html']
Path('/tmp/rustusb-secure-pcap-extension-static-20261023.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'constructed_checks':len(report['checks']),'posthoc_capture_packets':{r:d['packets'] for r,d in report['actual_posthoc'].items()},'posthoc_tls_flows':{r:len(d['flows']) for r,d in report['actual_posthoc'].items()},'original_result':report['original_result'],'source_sha256':report['source_sha256']}))
