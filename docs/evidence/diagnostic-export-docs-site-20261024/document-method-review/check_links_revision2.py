#!/usr/bin/env python3
"""Local documentation targets/anchors/tables; no browser, build or product run."""
from pathlib import Path
import re,json,hashlib,html
from urllib.parse import urlsplit,unquote
R=Path('/home/ubuntu/github/usbip-client');O=Path('/tmp/rustusb-diagnostic-final-doc-review-20261024')
files=list(json.loads((O/'sources-before.json').read_text()))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def strip_fences(text):
 result=[];inside=False;marker=''
 for line in text.splitlines():
  m=re.match(r'^\s*(`{3,}|~{3,})',line)
  if m:
   if not inside:inside=True;marker=m[1][0]
   elif m[1][0]==marker:inside=False
   result.append('');continue
  result.append(''if inside else line)
 return '\n'.join(result)
def anchors(path):
 text=path.read_text();found=set(re.findall(r'\bid=["\']([^"\']+)["\']',text));seen={}
 if path.suffix.lower() in ('.md','.markdown'):
  for line in strip_fences(text).splitlines():
   m=re.match(r'^#{1,6}\s+(.+?)\s*#*$',line)
   if not m:continue
   label=re.sub(r'<[^>]*>','',m[1]);label=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',label);label=html.unescape(label).lower();label=re.sub(r'[^\w\- ]','',label,flags=re.UNICODE).replace(' ','-')
   n=seen.get(label,0);seen[label]=n+1;found.add(label+(f'-{n}'if n else''))
 return found
rows=[];table_errors=[];anchor_cache={}
for name in files:
 path=R/name;text=strip_fences(path.read_text());links=[]
 for m in re.finditer(r'\]\((<[^>]+>|[^\s)]+)(?:\s+["\'][^)]*["\'])?\)',text):links.append((text[:m.start()].count('\n')+1,m[1].strip('<>')))
 for m in re.finditer(r'(?m)^\s*\[[^\]]+\]:\s*(\S+)',text):links.append((text[:m.start()].count('\n')+1,m[1].strip('<>')))
 for m in re.finditer(r'\b(?:href|src)=["\']([^"\']+)["\']',text):links.append((text[:m.start()].count('\n')+1,m[1]))
 for line,url in links:
  parsed=urlsplit(html.unescape(url));mapped=False
  if parsed.scheme or parsed.netloc:
   prefix='https://github.com/tiansongyu/usbip-client/blob/main/'
   if not url.startswith(prefix):continue
   local=R/unquote(parsed.path.split('/blob/main/',1)[1]);mapped=True
  else:local=(path.parent/unquote(parsed.path)).resolve() if parsed.path else path
  exists=local.exists();anchor=unquote(parsed.fragment);ok=exists
  if exists and anchor:
   if str(local)not in anchor_cache:anchor_cache[str(local)]=anchors(local)
   ok=anchor in anchor_cache[str(local)]
  rows.append({'source':name,'line':line,'url':url,'local_target':str(local),'github_main_mapped':mapped,'fragment':anchor or None,'pass':ok})
 lines=text.splitlines()
 for i,line in enumerate(lines):
  if not re.match(r'^\s*\|?\s*:?-{3,}:?\s*\|',line):continue
  def cells(l):return [x.strip()for x in re.split(r'(?<!\\)\|',l.strip().strip('|'))]
  header=cells(lines[i-1])if i else[];separator=cells(line);n=len(separator)
  if len(header)!=n:table_errors.append({'source':name,'line':i+1,'problem':'header/separator cell count','header':len(header),'separator':n})
  k=i+1
  while k<len(lines)and lines[k].lstrip().startswith('|'):
   if len(cells(lines[k]))!=n:table_errors.append({'source':name,'line':k+1,'problem':'body cell count','cells':len(cells(lines[k])),'expected':n})
   k+=1
report={'files':files,'local_links_checked':len(rows),'local_link_failures':[r for r in rows if not r['pass']],'table_errors':table_errors,'checks':rows,'limits':['Static local resolution/Markdown heading anchors only; external HTTP links not fetched.','Source and content QA only, no browser/rendering or pixel validation.']}
(O/'local-links-revision2.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:report[k]for k in ('local_links_checked','local_link_failures','table_errors')},indent=2))
