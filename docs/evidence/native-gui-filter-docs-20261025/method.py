import pathlib,json,hashlib,subprocess,re,urllib.parse,importlib.util,shutil
root=pathlib.Path('/home/ubuntu/github/usbip-client');out=root/'docs/evidence/native-gui-filter-docs-20261025';out.mkdir(parents=True,exist_ok=False)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
files=subprocess.check_output(['git','diff','--name-only'],cwd=root,text=True).splitlines()+['docs/gui.md','docs/research/native-gui-validation.md','docs/evidence/native-gui-20261025/README.md','apps/rustusb-gui/README.md']
files += [str(p.relative_to(root/'docs/evidence/native-gui-docs-20261025/source')) for p in (root/'docs/evidence/native-gui-docs-20261025/source').rglob('*.md')]
files=sorted({f for f in files if f.endswith('.md')and not f.startswith('docs/evidence/')});files+=['scripts/qemu_gui/README.md']
spec=importlib.util.spec_from_file_location('site_checker',root/'scripts/check-site.py');checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
links=[]
for name in files:
 p=root/name
 for target in re.findall(r'\]\(([^)\s]+)\)',p.read_text()):
  target=urllib.parse.unquote(target)
  if target.startswith(checker.REPO+'/blob/main/'):base=root;target=target.removeprefix(checker.REPO+'/blob/main/')
  elif target.startswith(checker.REPO+'#'):base=root;target='README.md#'+target.split('#',1)[1]
  else:base=p.parent
  u=urllib.parse.urlsplit(target)
  if u.scheme or u.netloc:continue
  dest=(base/u.path).resolve()if u.path else p
  assert dest.exists(),(name,target)
  if u.fragment and dest.suffix=='.md':assert u.fragment in checker.markdown_ids(dest)or u.fragment=='readme',(name,target)
  links.append({'source':name,'target':target})
commands=[]
for name,args in [('site',['python3','scripts/check-site.py']),('site-js',['node','--check','website/app.js']),('gui-js',['node','--check','apps/rustusb-gui/web/app.js']),('diff',['git','diff','--check']),('gui-help',['apps/rustusb-gui/target/release/rustusb-gui','--help']),('native-tree',['cargo','tree','-p','rustusb-cli','-e','normal','--locked'])]:
 r=subprocess.run(args,cwd=root,capture_output=True);(out/(name+'.txt')).write_bytes(r.stdout+r.stderr);assert r.returncode==0,(name,r.returncode)
 commands.append({'name':name,'command':args,'exit':r.returncode})
assert not any(s in(out/'native-tree.txt').read_text()for s in ['tauri v','gtk v','webkit2gtk v'])
core=json.loads(pathlib.Path('/tmp/rustusb-native-gui-gates-b15dd113-20261025/summary.json').read_text());core_inputs={n:i for n,i in core['inputs'].items()if n.startswith(('crates/','vendor/nusb/'))or n in ['Cargo.toml','Cargo.lock','rust-toolchain.toml','scripts/check.sh']}
for n,i in core_inputs.items():assert sha(root/n)==i['sha256'],n
final=json.loads(pathlib.Path('/tmp/rustusb-native-gui-gates-filter-20261025/summary.json').read_text());gui_inputs={n:i for n,i in final['inputs'].items()if n.startswith('apps/rustusb-gui/')and not n.endswith('README.md')}
for n,i in gui_inputs.items():assert sha(root/n)==i['sha256'],n
for n,info in final['binaries'].items():
 p=root/('apps/rustusb-gui/target/release/rustusb-gui'if n=='rustusb-gui'else'target/release/'+n);assert sha(p)==info['sha256']
base=json.loads((root/'docs/evidence/tui-interactions-docs-20261025/summary.json').read_text());skill=pathlib.Path('/home/ubuntu/.agents/skills/readme-site')
for n,i in base['global_skill_unchanged'].items():assert sha(skill/n)==i['sha256']
link=pathlib.Path('/home/ubuntu/.codex/skills/readme-site');assert link.is_symlink()and link.resolve()==skill
for name,expected in [('originals.tar.gz','archive_sha256'),('glib-evidence.tar.gz','archive_sha256')]:
 receipt='receipt.json'if name=='originals.tar.gz'else'glib-receipt.json';r=json.loads((root/'docs/evidence/native-gui-20261025'/receipt).read_text());assert sha(root/'docs/evidence/native-gui-20261025'/name)==r[expected]
assert (root/'docs/assets/gui-qemu.png').read_bytes()==(root/'website/assets/gui-qemu.png').read_bytes()==pathlib.Path('/tmp/rustusb-native-gui-qemu-filter-20261025/added.png').read_bytes()
for n in files:
 p=out/'source'/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/n,p)
shutil.copyfile(__file__,out/'method.py')
summary={'result':'PASS','documents':len(files),'local_links':len(links),'commands':commands,'core_inputs_identical_to_b15':len(core_inputs),'gui_non_readme_inputs_identical_to_036':len(gui_inputs),'global_skill_unchanged':base['global_skill_unchanged'],'discovery_symlink':str(link),'site_files':{str(p.relative_to(root/'website')):{'bytes':p.stat().st_size,'sha256':sha(p)}for p in(root/'website').rglob('*')if p.is_file()},'browser':{'scope':'In-app browser /usbip-client/ prefix, read-only DOM and keyboard checks; screenshot APIs unavailable, not a pixel pass','desktop':{'viewport_width':1280,'scroll_width':1265,'gui_image_width':1120,'natural_width':1180,'natural_height':760},'mobile':{'viewport_width':390,'scroll_width':375,'gui_image_width':339},'new_link_tab_focus':'Build the GUI preview','focus_outline':'solid','viewport_reset_and_tab_closed':True,'no_javascript_and_reduced_motion_rerun':False},'scope':'Documentation/static checks; native QEMU screenshots inspected separately. CI/Pages publication not yet checked.'}
(out/'links.json').write_text(json.dumps(links,indent=2)+'\n');(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print({k:summary[k]for k in ['result','documents','local_links','core_inputs_identical_to_b15','gui_non_readme_inputs_identical_to_036']})
