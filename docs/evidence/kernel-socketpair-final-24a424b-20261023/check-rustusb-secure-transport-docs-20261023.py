import hashlib, importlib.util, json, pathlib, re, sys
from urllib.parse import unquote, urlsplit
sys.dont_write_bytecode = True
root = pathlib.Path('/home/ubuntu/github/usbip-client')
spec = importlib.util.spec_from_file_location('sitecheck', root/'scripts/check-site.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
files = ['README.md','README.zh-CN.md','AGENTS.md','docs/architecture.md','docs/roadmap.md','docs/testing.md','docs/third-party.md','docs/acceptance.md','docs/research/secure-transport-foundation.md','docs/research/secure-transport-design.md']
checks, inputs = [], []
for name in files:
    path = root/name
    data = path.read_bytes()
    inputs.append({'path':name,'sha256':hashlib.sha256(data).hexdigest()})
    for target in re.findall(r'\]\(([^)\s]+)\)', data.decode()):
        value = urlsplit(unquote(target))
        if value.scheme or value.netloc:
            continue
        dest = (path.parent/value.path).resolve() if value.path else path
        passed = dest.is_relative_to(root) and dest.exists()
        if passed and value.fragment and dest.suffix == '.md':
            passed = value.fragment in module.markdown_ids(dest)
        checks.append({'source':name,'target':target,'passed':passed})
failures = [entry for entry in checks if not entry['passed']]
result = {'scope':'Local Markdown file/heading references for ten maintained changed documents; no external URL/browser test.','files':inputs,'checks':checks,'count':len(checks),'failures':failures,'result':'FAIL' if failures else 'PASS'}
pathlib.Path('/tmp/rustusb-secure-transport-docs-delivery-20261023.json').write_text(json.dumps(result,indent=2)+'\n')
print(result['result'],len(files),'documents',len(checks),'local references',len(failures),'failures')
raise SystemExit(1 if failures else 0)
