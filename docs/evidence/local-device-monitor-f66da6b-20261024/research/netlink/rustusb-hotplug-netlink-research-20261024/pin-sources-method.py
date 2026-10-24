from pathlib import Path
import urllib.request,json,hashlib,shutil
out=Path('/tmp/rustusb-hotplug-netlink-research-20261024');pins=[]
def fetch(url):
 with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'RustUSB-component-research'}),timeout=30) as r:return r.read()
for repo,version,label,suffix,existing in [('torvalds/linux','v5.15','linux-5.15','lib/kobject_uevent.c','linux-5.15-kobject_uevent.c'),('torvalds/linux','v6.8','linux-6.8','lib/kobject_uevent.c','linux-6.8-kobject_uevent.c'),('systemd/systemd','v249','systemd-249','src/libsystemd/sd-device/device-monitor.c','systemd-249-device-monitor.c'),('systemd/systemd','v255','systemd-255','src/libsystemd/sd-device/device-monitor.c','systemd-255-device-monitor.c')]:
 url=f'https://api.github.com/repos/{repo}/git/ref/tags/{version}';raw=fetch(url);(out/(label+'-git-ref.json')).write_bytes(raw);d=json.loads(raw);o=d['object'];chain=[]
 while o['type']=='tag':
  raw=fetch(o['url']);(out/(label+'-git-tag-'+o['sha']+'.json')).write_bytes(raw);chain.append(o['sha']);o=json.loads(raw)['object']
 assert o['type']=='commit';commit=o['sha'];url=f'https://raw.githubusercontent.com/{repo}/{commit}/{suffix}';raw=fetch(url);assert raw==(out/existing).read_bytes();(out/(label+'-immutable-source-match.json')).write_text(json.dumps({'repo':repo,'tag':version,'tag_chain':chain,'commit':commit,'url':url,'existing':existing,'sha256':hashlib.sha256(raw).hexdigest(),'byte_match':True},indent=2)+'\n');pins.append({'repo':repo,'tag':version,'commit':commit});print(label,commit)
(out/'fixed-revisions.json').write_text(json.dumps(pins,indent=2)+'\n')
(out/'pin-sources-method.py').write_bytes(Path(__file__).read_bytes())
(out/'finalize-method.py').write_bytes(Path('/tmp/finalize-rustusb-hotplug-netlink-20261024.py').read_bytes())
selected=[]
for p in sorted(out.rglob('*')):
 if p.is_file() and 'target' not in p.relative_to(out).parts and p.name!='research-files.json':
  b=p.read_bytes();selected.append({'path':str(p),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
(out/'research-files.json').write_text(json.dumps({'root':str(out),'files':selected,'scope':'Explicit source, lock, metadata, method and raw probe logs; target/ELF/cache bodies excluded.'},indent=2)+'\n');print('selected',len(selected),'bytes',sum(x['bytes'] for x in selected))
