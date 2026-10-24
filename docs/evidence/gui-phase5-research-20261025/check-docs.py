from pathlib import Path
import importlib.util,urllib.parse,re,json,hashlib
repo=Path('/home/ubuntu/github/usbip-client');out=Path('/tmp/rustusb-gui-research-docs-qa-20261025');out.mkdir(exist_ok=False)
s=importlib.util.spec_from_file_location('gui_research_links',repo/'scripts/check-site.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
links=[]
for name in ['docs/research/gui-installation-research.md','docs/roadmap.md','AGENTS.md']:
 p=repo/name
 for target in re.findall(r'\]\(([^)\s]+)\)',p.read_text()):
  u=urllib.parse.urlsplit(urllib.parse.unquote(target))
  if u.scheme or u.netloc:continue
  q=(p.parent/u.path).resolve()if u.path else p;assert q.is_relative_to(repo)and q.exists(),(name,target)
  if u.fragment:assert u.fragment in m.markdown_ids(q),(name,target)
  links.append({'source':name,'target':target})
review=json.loads((repo/'docs/evidence/gui-phase5-research-20261025/review.json').read_text())
for row in review['files']:
 p=repo/'docs/evidence/gui-phase5-research-20261025'/row['path'];assert hashlib.sha256(p.read_bytes()).hexdigest()==row['sha256']
r={'result':'PASS','local_links':links,'research_files':len(review['files']),'scope':'Selected research metadata/source-hash integrity and new local doc links only; no GUI build/install/runtime or full upstream source redistribution'}
(out/'summary.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps({'result':'PASS','local_links':len(links),'research_files':len(review['files'])}))
