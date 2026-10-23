from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit,unquote
import subprocess,json,re,hashlib,datetime,collections
root=Path('/home/ubuntu/github/usbip-client'); files=['README.md','README.zh-CN.md','docs/native-guide.md','docs/rust-physical-server.md','docs/rust-virtual-server.md','docs/research/readme-website-design.md','website/index.html','website/app.js','website/styles.css','website/assets/favicon.svg','website/assets/usbip-flow.svg']; sha=lambda b:hashlib.sha256(b).hexdigest(); checks=[];mustfix=[];counts={}
def check(name,passed,detail):
 checks.append({'name':name,'result':'PASS' if passed else 'FAIL','detail':detail})
 if not passed:mustfix.append(checks[-1])
class Doc(HTMLParser):
 def __init__(self,text):super().__init__();self.tags=[];self.feed(text)
 def handle_starttag(self,tag,attrs):self.tags.append((tag,dict(attrs)))
html=(root/'website/index.html').read_text();dom=Doc(html);ids=[a['id'] for t,a in dom.tags if 'id'in a];links=[];problems=[]
def anchors(path):
 if path.suffix=='.md':
  slugs=[];duplicates=collections.Counter()
  for m in re.finditer(r'^#{1,6}\s+(.+?)\s*#*$',path.read_text(),re.M):
   s=re.sub(r'<[^>]*>','',m.group(1));s=re.sub(r'[^\w\-\s]','',s.lower());s=re.sub(r'\s','-',s);n=duplicates[s];duplicates[s]+=1;slugs.append(s+('-'+str(n) if n else ''))
  return slugs
 return [a['id'] for _,a in Doc(path.read_text()).tags if'id'in a]
def validate(source,href):
 u=urlsplit(href);p=None
 if u.scheme or u.netloc:
  if u.netloc=='github.com' and u.path.startswith('/tiansongyu/usbip-client'):
   rel=u.path[len('/tiansongyu/usbip-client'):]
   if rel in ['', '/']:p=root/'README.md'
   elif rel.startswith('/blob/main/'):p=root/rel[len('/blob/main/'):]
   else:return
  else:return
 else:p=(root/source).parent/unquote(u.path) if u.path else root/source
 links.append({'from':source,'href':href})
 if not p.exists():problems.append({'from':source,'href':href,'error':'missing target'})
 elif u.fragment and u.fragment!='readme' and unquote(u.fragment) not in anchors(p):problems.append({'from':source,'href':href,'error':'missing heading/id'})
for p in files:
 if p.endswith('.md'):
  for m in re.finditer(r'\[[^\]]*\]\(([^\s)]+)(?:\s+"[^"]*")?\)',(root/p).read_text()):validate(p,m.group(1))
for tag,a in dom.tags:
 for key in ['href','src']:
  if key in a:validate('website/index.html',a[key])
counts['reviewed_files']=len(files);counts['local_and_repo_link_occurrences']=len(links);check('local files and GitHub repository deep links',not problems,{'occurrences':len(links),'problems':problems,'scope':'Local current files and heading/id targets; no live publishing assertion'})
check('HTML structural accessibility',len(ids)==len(set(ids)) and len([1 for t,a in dom.tags if t=='h1'])==1 and any(t=='a'and a.get('href')=='#main'and a.get('class')=='skip-link'for t,a in dom.tags) and all('alt'in a and 'width'in a and 'height'in a for t,a in dom.tags if t=='img'),{'unique_ids':len(ids),'h1':1,'images':len([1 for t,a in dom.tags if t=='img']),'manual':'English document language, native controls, landmarks, descriptive links, visible focus and polite copy-status; no form controls.'})
check('No-JavaScript content is present',all('hidden'not in a for t,a in dom.tags if a.get('class')=='backend-panel') and len([1 for t,a in dom.tags if a.get('class')=='backend-panel'])==3 and all('hidden'in a for t,a in dom.tags if a.get('class')=='backend-tabs' or a.get('id')=='copy-build'),{'backend_panels':3,'scope':'HTML/CSS source inspection, not disabled-JS browser rendering; essential copy/commands/links do not depend on scripts'})
check('Project-subpath assets',all(not a[k].startswith('/') for t,a in dom.tags for k in ['href','src'] if k in a and not urlsplit(a[k]).netloc),{'assets':'Relative styles.css/app.js/assets SVG, external documentation uses explicit GitHub URLs'})
old=subprocess.check_output(['git','show','b02b97c:README.md'],cwd=root).decode();zh=(root/'README.zh-CN.md').read_text();native=(root/'docs/native-guide.md').read_text();chunk=old[old.index('## 构建与检查'):old.index('## 测试与研究')].strip().replace('(docs/','(').replace('(examples/','(../examples/');check('Original README and Native guidance retained',zh.replace('[English](README.md) · 中文\n\n','',1)==old and native[native.index('## 构建与检查'):].strip()==chunk,{'source_commit':'b02b97c','chinese_readme':'Original bytes except inserted language-navigation line','native_guide':'Three original sections with only local relative link adjustments','deep_links':'3 old Native anchors migrated in physical and virtual guides'})
source=(root/'crates/rustusb-cli/src/main.rs').read_text();manifest=(root/'crates/rustusb-cli/Cargo.toml').read_text();check('Commands and feature prerequisites correspond to source',all(x in source for x in ['PhysicalPending','VirtualServer','Devices','Doctor','Discover','Export','Attach','Detach','Sessions','Status','default_value = "127.0.0.1:3240"']) and all(x in manifest for x in ['rust-backend','physical-nusb']),{'method':'Read CLI clap declarations, virtual control implementation, package features, workspace manifest and toolchain; did not execute cargo or mutate devices','examples':'Native arguments and global --json valid; virtual server defaults/--control-stdin shutdown correct; physical-pending requires installed all-features binary/helper and root; build instructions start from source checkout'})
check('Claims and historical test attribution',True,{'manual_review':'250 means executions across default/allfeatures/vendor, not new unique tests; five QEMU ten guests/four native software platforms correctly linked to prior 933e720 lifecycle report. Native and virtual separately link their own baselines. No new USB evidence claimed for the site change. Real hardware/aarch64 USB kernel/automatic KILL recovery/TLS/ISO and future UI limits retained.'})
check('Illustration accuracy and licensing',True,{'manual_review':'SVG title/desc and visible caption label architecture illustration. usbip_host+usbipd on Native server, vhci_hcd and native driver on client, external managed secure network, RustUSB restricted helpers. No live output or shipped GUI implied; diagram is original, local assets and license paths preserved.'})
css=(root/'website/styles.css').read_text();check('Responsive and interaction CSS source',all(x in css for x in ['@media(max-width:900px)','@media(max-width:680px)','grid-template-columns:1fr','min-width:0','overflow-wrap:anywhere',':focus-visible','prefers-reduced-motion:reduce','color-scheme:dark']) and 'transition:all'not in css and 'outline:none'not in css,{'manual_review':'Stacked mobile grids, wrapping code, wrapped footer, responsive image, touch targets for tabs/navigation/copy, reduced-motion disables smooth scrolling/transitions. Static inspection only; desktop/mobile/zoom and actual pointer/focus rendering delegated to parent browser review.'})
def luminance(hex):
 vals=[int(hex[i:i+2],16)/255 for i in (1,3,5)];vals=[x/12.92 if x<=.04045 else ((x+.055)/1.055)**2.4 for x in vals];return sum(a*b for a,b in zip(vals,[.2126,.7152,.0722]))
def contrast(a,b):l=sorted([luminance(a),luminance(b)]);return (l[1]+.05)/(l[0]+.05)
pairs=[('body','#edf4f9','#080d12'),('muted body','#9bafbe','#080d12'),('muted panel','#9bafbe','#101a24'),('selected tab','#83ddf2','#132c37'),('dark CTA','#080d12','#edf4f9'),('warm warning','#eec18d','#080d12')];ratios=[{'name':n,'ratio':round(contrast(f,b),2)}for n,f,b in pairs];check('Principal text contrast tokens',all(x['ratio']>=4.5 for x in ratios),{'pairs':ratios,'scope':'Specified flat color pairs only; not a screenshot-derived exhaustive color audit'})
js= json.loads(Path('/tmp/rustusb-site-js-review.json').read_text());check('Optional tab and clipboard logic',len(js['checks'])==13 and all(x['result']=='PASS'for x in js['checks']),{'method':js['method'],'cases':js['checks'],'limits':'Native browser activation/focus/clipboard policies still need parent browser checks'})
check('Node JavaScript syntax',subprocess.run(['node','--check','website/app.js'],cwd=root,capture_output=True).returncode==0,{'command':'node --check website/app.js'})
report={'schema_version':1,'reviewer':'/root/journal_harness_review','reviewed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'result':'PASS_WITH_NONBLOCKING_NOTE'if not mustfix else 'FAIL','head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root).decode().strip(),'guidelines_source':'https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md','skills':['web-design-guidelines','readme-site'],'files':{p:sha((root/p).read_bytes())for p in files},'verification_groups':len(checks),'checks':checks,'counts':counts,'mustfix':mustfix,'nonblocking_findings':[{'location':'website/app.js:36','issue':'Backend tab state is not reflected in the URL; loading #panel-physical still selects Native and hides the target.','recommendation':'Optionally initialize from an allowlisted panel hash and preserve valid tab state with hashchange/history.replaceState. Existing public links target #explore, so the shipped documented navigation works.'}],'scope_limits':['No browser or screenshots used; rendering, zoom, actual assistive technology and clipboard permission behavior are not proven by this static/stub review.','No Cargo/QEMU/device operation executed; this review does not add software/device test coverage.','Pages workflow and scripts/check-site.py not present at initial review, to be reviewed when parent adds them.','Website deployment and live link availability are separate from reviewed local file/anchor correctness.'],'repo_modified':False}
out=Path('/tmp/rustusb-site-review.json');out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'report':str(out),'result':report['result'],'groups':len(checks),'mustfix':mustfix,'counts':counts,'sha256':sha(out.read_bytes())},ensure_ascii=False))
