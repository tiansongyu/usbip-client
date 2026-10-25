from pathlib import Path
import json,hashlib,subprocess,re,importlib.util
root=Path('/home/ubuntu/github/usbip-client')
out=root/'docs/evidence/automatic-sharing-foundation-software-20261025'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def git(*args):return subprocess.check_output(['git',*args],cwd=root)
s=json.loads((out/'summary.json').read_text())
spec=importlib.util.spec_from_file_location('inputs',root/'scripts/qemu_gui_link/run.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
assert m.inputs()==s['inputs']
assert git('rev-parse','HEAD').decode().strip()==s['head']
for name,meta in s['binaries'].items():
    p=(root/'apps/rustusb-gui/target/release/rustusb-gui') if name=='rustusb-gui' else (root/'target/release'/name)
    assert p.stat().st_size==meta['bytes'] and sha(p)==meta['sha256']
unchanged={}
for name in ['crates/rustusb-service/src/config.rs','crates/rustusb-service/src/store.rs','crates/rustusb-service/src/model.rs','crates/rustusb-service/src/rpc.rs','crates/rustusb-service/src/ipc.rs','crates/rustusb-service/src/runtime.rs','crates/rustusb-cli/src/agent.rs','apps/rustusb-gui/web/app.js','Cargo.toml','Cargo.lock','apps/rustusb-gui/Cargo.lock','README.md']:
    p=root/name
    assert p.read_bytes()==git('show','fd6c6a0e:'+name)
    unchanged[name]=sha(p)
site={}
for p in sorted((root/'website').rglob('*')):
    if p.is_file():
        rel=str(p.relative_to(root));assert p.read_bytes()==git('show','fd6c6a0e:'+rel)
        site[rel]=sha(p)
baseline=json.loads((root/'docs/evidence/tui-interactions-docs-20261025/summary.json').read_text())['global_skill_unchanged']
skill=Path('/home/ubuntu/.agents/skills/readme-site')
for name,meta in baseline.items():
    p=skill/name
    assert p.stat().st_size==meta['bytes'] and sha(p)==meta['sha256']
link=Path('/home/ubuntu/.codex/skills/readme-site')
assert link.is_symlink() and link.resolve()==skill.resolve()
docs=['docs/research/automatic-sharing-foundation-validation.md','docs/research/automatic-sharing-design.md','docs/research/automatic-sharing-config-audit.md','docs/research/automatic-sharing-owner-audit.md','docs/architecture.md','docs/acceptance.md','docs/roadmap.md','docs/work-log.md','AGENTS.md','.agents/skills/rustusb-development/SKILL.md']
targets=set()
for name in docs:
    for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)',(root/name).read_text()):
        if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:|^#',target):continue
        path=target.split('#',1)[0]
        if not path:continue
        p=(root/name).parent/path
        assert p.exists(),(name,target)
        targets.add(str(p.resolve()))
for name in ['automation.rs','automation_tests.rs']:
    body=(root/'crates/rustusb-service/src'/name).read_text()
    assert not re.search(r'\.unwrap\s*\(|\.expect\s*\(|\bpanic!\s*\(|\bunsafe\b',body)
for name in ['summary.json','core.log','gui.log']:
    assert (out/name).read_bytes()==(Path('/tmp/rustusb-automation-foundation-software-20261025')/name).read_bytes()
help_result=subprocess.run([str(root/'target/release/rustusb'),'agent','run','--help'],cwd=root,capture_output=True,check=True)
assert b'automation-policy' not in help_result.stdout
(out/'agent-run-help.txt').write_bytes(help_result.stdout)
(out/'qa-method.py').write_bytes(Path(__file__).read_bytes())
qa={'result':'PASS','source_head':s['head'],'frozen_inputs_checked':len(s['inputs']),'same_gate_binaries':s['binaries'],'unchanged_paths':unchanged,'unchanged_site':site,'global_skill_unchanged':baseline,'global_skill_symlink':str(link.resolve()),'documentation_files_checked':len(docs),'local_link_targets_checked':len(targets),'original_gate_files_exact':True,'no_execution_entry_added':True,'new_qemu_runs':0,'new_hardware_runs':0}
(out/'qa.json').write_text(json.dumps(qa,indent=2)+'\n')
manifest={'files':[]}
for p in sorted(out.iterdir()):
    assert p.is_file() and p.name!='manifest.json'
    manifest['files'].append({'path':p.name,'bytes':p.stat().st_size,'sha256':sha(p)})
(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'result':'PASS','source_head':s['head'],'inputs':len(s['inputs']),'docs':len(docs),'targets':len(targets),'site_files':len(site),'evidence_members':len(manifest['files']),'evidence_bytes':sum(v['bytes'] for v in manifest['files'])}))
