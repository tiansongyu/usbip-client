"""Read-only, bounded-to-selected-files documentation checks. No network/build/runtime."""
from pathlib import Path
import hashlib,json,re,unicodedata,urllib.parse
ROOT=Path('/home/ubuntu/github/usbip-client')
OUT=Path(__file__).parent
FILES=[x['path'] for x in json.loads((OUT/'source-before.json').read_text())]
def hashes():
 return [{'path':f,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()} for f in FILES for b in [(ROOT/f).read_bytes()]]
def without_fences(text):
 return re.sub(r'```.*?```|~~~.*?~~~','',text,flags=re.S)
def anchors(path):
 txt=path.read_text();result=set(re.findall(r'\b(?:id|name)=[\"\x27]([^\"\x27]+)',txt));counts={}
 for m in re.finditer(r'^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$',without_fences(txt),re.M):
  heading=re.sub(r'<[^>]+>','',m[1]);heading=re.sub(r'\[([^]]+)\]\([^)]*\)',r'\1',heading);heading=heading.replace('`','').lower()
  slug=''.join(c for c in heading if c.isalnum() or c in '-_ ' or unicodedata.category(c).startswith('M')).replace(' ','-')
  count=counts.get(slug,0);counts[slug]=count+1;result.add(slug+(f'-{count}' if count else ''))
 return result
before=hashes();links=[];external=[];tables=[];errors=[]
for name in FILES:
 path=ROOT/name;txt=path.read_text();mask=without_fences(txt)
 refs=[(m.start(),m.group(1).strip('<>')) for m in re.finditer(r'!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[\"\x27][^\n]*?)?\)',mask)]
 refs += [(m.start(),m.group(1)) for m in re.finditer(r'\b(?:href|src)=[\"\x27]([^\"\x27]+)',mask)]
 for pos,ref in refs:
  parsed=urllib.parse.urlsplit(ref);mapped=False
  if parsed.scheme or parsed.netloc:
   if ref.startswith('https://github.com/tiansongyu/usbip-client/blob/main/'):
    target=ROOT/urllib.parse.unquote(parsed.path.split('/blob/main/',1)[1]);mapped=True
   else:external.append({'source':name,'reference':ref});continue
  else:target=(path.parent/urllib.parse.unquote(parsed.path)).resolve() if parsed.path else path
  item={'source':name,'line_approx':mask.count('\n',0,pos)+1,'reference':ref,'target':str(target.relative_to(ROOT)) if target.is_relative_to(ROOT) else str(target),'exists':target.exists(),'repo_url_mapped':mapped}
  if not target.exists():errors.append({'kind':'missing_target',**item})
  elif parsed.fragment and target.is_file() and target.suffix in ('.md','.html'):
   item['anchor']=urllib.parse.unquote(parsed.fragment);item['anchor_exists']=item['anchor'] in anchors(target)
   if not item['anchor_exists']:errors.append({'kind':'missing_anchor',**item})
  links.append(item)
 lines=txt.splitlines();i=0
 def cells(s):return re.split(r'(?<!\\)\|',s.strip().strip('|'))
 while i<len(lines):
  if re.match(r'^\s*\|?\s*:?-{3,}:?\s*\|',lines[i]):
   h=i-1;count=len(cells(lines[i]));bad=[]
   if h>=0 and len(cells(lines[h]))!=count:bad.append({'line':h+1,'columns':len(cells(lines[h]))})
   j=i+1
   while j<len(lines) and lines[j].strip().startswith('|'):
    if len(cells(lines[j]))!=count:bad.append({'line':j+1,'columns':len(cells(lines[j]))})
    j+=1
   blank=h==0 or not lines[h-1].strip();item={'source':name,'header_line':h+1,'columns':count,'row_count':j-i-1,'blank_before':blank,'mismatched_rows':bad};tables.append(item)
   if bad or not blank:errors.append({'kind':'table',**item})
   i=j
  else:i+=1
after=hashes()
result={'method':'Inline Markdown links and HTML href/src; same-repository blob/main URLs map to local files, not publication. Fenced code omitted. ATX GitHub-style slug/explicit id anchors. Table separator/header/body arity and preceding blank line. External URLs not fetched. This is a scoped preflight, not a complete CommonMark/browser parser.','source_before':before,'source_after':after,'source_stable':before==after,'links':links,'external_skipped':external,'tables':tables,'errors':errors}
(OUT/'local-links-tables-final.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
print(json.dumps({'local_links':len(links),'anchor_links':sum('anchor' in x for x in links),'tables':len(tables),'errors':errors,'source_stable':before==after}))
