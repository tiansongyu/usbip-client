from pathlib import Path
import hashlib,json,re,xml.etree.ElementTree as ET
from html.parser import HTMLParser
ROOT=Path('/home/ubuntu/github/usbip-client')
FILES=['README.md','README.zh-CN.md','website/index.html','website/assets/usbip-flow.svg','website/assets/usbip-flow-mobile.svg','docs/secure-guide.md','docs/architecture.md','docs/security.md','docs/roadmap.md','docs/compatibility.md','docs/third-party.md','AGENTS.md','docs/research/secure-usbip-validation.md','docs/acceptance.md','docs/testing.md']
class H(HTMLParser):
 def __init__(self):super().__init__();self.refs=[];self.ids=[]
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if 'id'in a:self.ids.append(a['id'])
  for k in ['href','src']:
   if k in a:self.refs.append(a[k])
  if 'srcset'in a:self.refs.extend(x.strip().split()[0] for x in a['srcset'].split(','))
report={'schema_version':1,'scope':'Independent text/source/link/evidence-attribution review only; no repo edits,Cargo,QEMU,product run,browser or pixel screenshot claim','files':{},'links':{'checked':[],'missing':[],'external_not_fetched':0},'tables':[],'svg':[],'findings':[]}
for f in FILES:
 p=ROOT/f;s=p.read_text();report['files'][f]=hashlib.sha256(p.read_bytes()).hexdigest()
 if p.suffix=='.md':refs=re.findall(r'(?<!!)\[[^\]]*\]\(([^)]+)\)',s)
 elif p.suffix=='.html':
  h=H();h.feed(s);refs=h.refs;assert len(h.ids)==len(set(h.ids));report['html_unique_ids']=len(h.ids)
 else:
  xml=ET.fromstring(s);assert xml.tag.endswith('svg');report['svg'].append({'file':f,'viewBox':xml.attrib['viewBox'],'title':xml.find('{http://www.w3.org/2000/svg}title').text,'text':' '.join(n.text or '' for n in xml.iter() if n.tag.endswith('text'))});refs=[]
 for ref in refs:
  if ref.startswith('#'):
   if p.suffix=='.html':assert ref[1:] in h.ids
   continue
  prefix='https://github.com/tiansongyu/usbip-client/blob/main/'
  if ref.startswith(prefix):target=ROOT/ref[len(prefix):].split('#')[0]
  elif '://' in ref or ref.startswith('mailto:'):report['links']['external_not_fetched']+=1;continue
  else:target=p.parent/ref.split('#')[0]
  item={'source':f,'reference':ref}
  report['links']['checked'].append(item)
  if not target.exists():report['links']['missing'].append(item)
 if p.suffix=='.md':
  lines=s.splitlines()
  for i,line in enumerate(lines):
   if line.startswith('|') and (i==0 or not lines[i-1].startswith('|')):
    assert i>0 and lines[i-1]=='',(f,i,'missing blank table boundary')
    assert re.match(r'^\|[ |:-]+\|$',lines[i+1]),(f,i,'missing separator')
    report['tables'].append({'file':f,'line':i+1})
base=ROOT/'docs/evidence/secure-usbip-final-b83c931-20261023';m=json.loads((base/'manifest.json').read_text())
assert len(m['files'])==1518
runs=[]
for b in ['virtual','usbfs','native']:
 for f in ['ipv4','ipv6']:
  d=json.loads((base/f'{b}-{f}/summary.json').read_text());assert d['result']=='PASS' and d['head']=='b83c931afb1789ae178bf49c6402f996fc9fb3c5' and len(d['source_inputs'])==125 and all(d[x] for x in ['source_guard','head_guard','clean_guard','binary_guard']) and d['guest_exit_codes']=={'server':0,'client':0};runs.append({'backend':b,'family':f,'result':'PASS'})
report['evidence']={'manifest_entries':1518,'runs':runs,'software':'412=159default+233allfeatures+20vendor;123input candidate gate;exact f955 CI39953971059 each412/35','history':'396 gates and 41e2c67 CI retain old revisions; all four QEMU FAIL retained','raw_reviews':{}}
for name,expected in [('rustusb-secure-six-lifecycle-raw-review-20261023.json',492),('rustusb-secure-six-independent-review-20261023.json',350)]:
 d=json.loads((base/'reviews'/name).read_text());report['evidence']['raw_reviews'][name]={'sha256':hashlib.sha256((base/'reviews'/name).read_bytes()).hexdigest(),'result':d.get('result'),'expected_audit_predicates_not_usb_cases':expected};assert d['result']=='PASS'
report['review_boundaries']=['No new QEMU certificate-negative case claimed; single valid principal authorized two devices in actual six runs','TLS13 ServerHello/ClientHello offer visible; encrypted negotiated ALPN/client certificate depend on frozen source/software/real I/O,not PCAP decryption','USBFS only final journal retained,not independently all intermediate immutable grant fields','Legacy commands require external network protection; secure commands exact config/role/pool/anonymous channel','No hardwareG1/G2,ISO,general crash/crossconfig/fullPhase3 or UI completion claim','SVGs are labeled legacy path; this review parses text/XML only; root DOM QA cannot be promoted to pixel screenshot validation','External live GitHub/Pages content not fetched by this document review']
text={f:(ROOT/f).read_text() for f in FILES}
checks=[('README.zh-CN.md','真实硬件、内核内部挂起、ISO、认证和TLS仍未验收','Unqualified historical TLS-not-accepted sentence conflicts with current evidence'),('docs/architecture.md','同一TLS stream承载有界稳定key目录、标准IMPORT及后续URB','Catalog uses independent authenticated connection; only IMPORT and subsequent URB share stream'),('docs/acceptance.md','独立raw复核进行中','Final independent reports already PASS; pending wording stale')]
for f,needle,reason in checks:
 if needle in text[f]:report['findings'].append({'severity':'mustfix_text','file':f,'reason':reason})
report['result']='PASS' if not report['findings'] and not report['links']['missing'] else 'CHANGES_REQUESTED'
out=Path('/tmp/rustusb-secure-final-doc-independent-review-20261023.json');out.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
print(json.dumps({'result':report['result'],'findings':report['findings'],'links_checked':len(report['links']['checked']),'missing_links':report['links']['missing'],'tables':len(report['tables']),'source_files':len(FILES)},ensure_ascii=False))
