from pathlib import Path
import re,json,urllib.parse,html.parser,hashlib
root=Path('/home/ubuntu/github/usbip-client')
files=['README.md', 'README.zh-CN.md', 'website/index.html', 'AGENTS.md', '.agents/skills/rustusb-development/SKILL.md', 'docs/acceptance.md', 'docs/testing.md', 'docs/roadmap.md', 'docs/architecture.md', 'docs/compatibility.md', 'docs/website.md', 'docs/security.md', 'docs/logging.md', 'docs/research/rotating-logs-design.md', 'docs/research/rotating-logs-validation.md', 'docs/application-service-guide.md', 'docs/data-link-health.md', 'docs/third-party.md', 'docs/research/prepare-recording-shutdown-validation.md','docs/diagnostics.md','docs/native-guide.md','docs/research/diagnostic-export-design.md','docs/research/diagnostic-export-validation.md','docs/research/stderr-write-failure-design.md']
class Links(html.parser.HTMLParser):
 def __init__(self):super().__init__();self.links=[];self.ids=set()
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if 'id' in a:self.ids.add(a['id'])
  for n in ['href','src']:
   if n in a:self.links.append(a[n])
  if 'srcset' in a:self.links.extend(x.strip().split()[0] for x in a['srcset'].split(','))
def anchors(p):
 t=p.read_text()
 if p.suffix=='.html':h=Links();h.feed(t);return h.ids
 out=set();counts={}
 for line in t.splitlines():
  m=re.match(r'^#{1,6}\s+(.*?)\s*#*$',line)
  if not m:continue
  s=m[1].lower().replace(' ','-');s=''.join(c for c in s if c.isalnum() or c in '_-');n=counts.get(s,0);counts[s]=n+1;out.add(s if not n else f'{s}-{n}')
 return out
checks=[];external=[];spacing=[];widths=[]
for name in files:
 p=root/name;t=p.read_text();lines=t.splitlines()
 if p.suffix=='.html':h=Links();h.feed(t);links=h.links
 else:
  cleaned=re.sub(r'^```.*?^```\s*$', '',t,flags=re.M|re.S)
  links=re.findall(r'!?\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)',cleaned)
  for i,l in enumerate(lines):
   if re.match(r'^\|\s*:?-{3,}',l) and i>1 and lines[i-2].strip():spacing.append({'file':name,'line':i+1})
  i=0
  while i<len(lines)-1:
   if lines[i].startswith('|') and re.match(r'^\|\s*:?-{3,}',lines[i+1]):
    width=len(re.split(r'(?<!\\)\|',lines[i]))-2;j=i+2
    while j<len(lines) and lines[j].startswith('|'):
     count=len(re.split(r'(?<!\\)\|',lines[j]))-2
     if count!=width:widths.append({'file':name,'line':j+1,'actual':count,'header':width})
     j+=1
    i=j
   else:i+=1
 for link in links:
  u=urllib.parse.urlsplit(link)
  if u.scheme in ('http','https'):
   prefix='https://github.com/tiansongyu/usbip-client/blob/main/'
   if link.startswith(prefix):target=root/urllib.parse.unquote(u.path.split('/blob/main/',1)[1])
   else:external.append({'file':name,'url':link});continue
  elif u.scheme:continue
  else:target=(p.parent/urllib.parse.unquote(u.path)).resolve() if u.path else p
  item={'file':name,'target':link,'exists':target.exists()}
  if target.exists() and u.fragment and target.suffix in ('.md','.html'):item['anchor_found']=urllib.parse.unquote(u.fragment) in anchors(target)
  checks.append(item)
report={'source':{n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in files},'local_or_repo_mapped_links':checks,'external_links_not_fetched':len(external),'table_spacing_errors':spacing,'table_width_errors':widths,'failures':[x for x in checks if not x['exists'] or x.get('anchor_found') is False],'scope':'Local targets and repository-mapped GitHub links only; no remote HTTP, browser, pixel or accessibility execution.'}
p=Path('/tmp/rustusb-diagnostic-publication-94e0d07-20261024/final-doc-checks/delivery-doc-links.json');p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'path':str(p),'source_count':len(report['source']),'local_links':len(checks),'external_not_fetched':len(external),'failures':report['failures'],'table_spacing_errors':spacing,'table_width_errors':widths}))
