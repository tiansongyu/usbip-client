from pathlib import Path
import json,hashlib,urllib.request,subprocess
out=Path('/tmp/rustusb-authentication-admission-docs-site-20261024')
resources=['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg'];http=[]
for n in resources:
 u='http://127.0.0.1:4187/usbip-client/'+n
 with urllib.request.urlopen(u) as r:b=r.read();status=r.status
 assert b==Path('website',n).read_bytes();http.append(dict(url=u,status=status,bytes=len(b),sha256=hashlib.sha256(b).hexdigest()))
with urllib.request.urlopen('http://127.0.0.1:4187/usbip-client-nojs/') as r:
 assert r.read()==Path('website/index.html').read_bytes();csp=r.headers['Content-Security-Policy'];assert csp=="script-src 'none'"
(out/'local-http.json').write_text(json.dumps(dict(resources=http,csp_nojs=csp),indent=2)+'\n')
skill=Path('/home/ubuntu/.agents/skills/readme-site');sources={str(p.relative_to(skill)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(skill.rglob('*')) if p.is_file()};assert len(sources)==4
assets={n:hashlib.sha256(Path('website',n).read_bytes()).hexdigest() for n in resources}
assert all(Path('website',n).read_bytes()==subprocess.check_output(['git','show',f'30481f0:website/{n}']) for n in resources if n!='index.html')
(out/'unchanged-runtime-assets-and-global-skill.json').write_text(json.dumps(dict(runtime_assets=assets,global_skill_sources=sources,codex_link=str(Path('/home/ubuntu/.codex/skills/readme-site').resolve()),scope='Five runtime site assets byte-identical to 30481f0; four current global skill files hashed, not modified by this delivery.'),indent=2)+'\n')
print('PASS HTTP6 exact bytes, CSP none, runtime assets5 unchanged, global skill4 hashed')
