import pathlib,json,hashlib,subprocess,re,urllib.parse,importlib.util,shutil
root=pathlib.Path('/home/ubuntu/github/usbip-client');out=root/'docs/evidence/gui-native-actions-docs-20261025';out.mkdir(parents=True,exist_ok=False)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
files=subprocess.check_output(['git','diff','--name-only'],cwd=root,text=True).splitlines()+['docs/research/gui-native-actions-design.md','docs/research/gui-native-actions-validation.md','docs/evidence/gui-native-actions-20261025/README.md','scripts/qemu_gui_actions/README.md','scripts/qemu_gui/README.md']
files=sorted({f for f in files if f.endswith('.md')});spec=importlib.util.spec_from_file_location('site_checker',root/'scripts/check-site.py');checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker);links=[]
for name in files:
 p=root/name
 for target in re.findall(r'\]\(([^)\s]+)\)',p.read_text()):
  target=urllib.parse.unquote(target)
  if target.startswith(checker.REPO+'/blob/main/'):base=root;target=target.removeprefix(checker.REPO+'/blob/main/')
  elif target.startswith(checker.REPO+'#'):base=root;target='README.md#'+target.split('#',1)[1]
  else:base=p.parent
  u=urllib.parse.urlsplit(target)
  if u.scheme or u.netloc:continue
  dest=(base/u.path).resolve()if u.path else p;assert dest.exists(),(name,target)
  if u.fragment and dest.suffix=='.md':assert u.fragment in checker.markdown_ids(dest)or u.fragment=='readme',(name,target)
  links.append({'source':name,'target':target})
commands=[]
for name,args in [('site',['python3','scripts/check-site.py']),('site-js',['node','--check','website/app.js']),('gui-js',['node','--check','apps/rustusb-gui/web/app.js']),('diff',['git','diff','--check']),('gui-help',['apps/rustusb-gui/target/release/rustusb-gui','--help']),('native-tree',['cargo','tree','-p','rustusb-cli','-e','normal','--locked'])]:
 r=subprocess.run(args,cwd=root,capture_output=True);(out/(name+'.txt')).write_bytes(r.stdout+r.stderr);assert r.returncode==0,(name,r.returncode);commands.append({'name':name,'command':args,'exit':r.returncode})
assert not any(s in(out/'native-tree.txt').read_text()for s in ['tauri v','gtk v','webkit2gtk v'])
final=json.loads(pathlib.Path('/tmp/rustusb-gui-actions-software-20261025/summary.json').read_text());changes=[]
for n,i in final['inputs'].items():
 if sha(root/n)!=i['sha256']:changes.append(n)
assert changes==['apps/rustusb-gui/README.md'],changes
for n,info in final['binaries'].items():assert sha(root/('apps/rustusb-gui/target/release/rustusb-gui'if n=='rustusb-gui'else'target/release/'+n))==info['sha256']
base=json.loads((root/'docs/evidence/tui-interactions-docs-20261025/summary.json').read_text());skill=pathlib.Path('/home/ubuntu/.agents/skills/readme-site')
for n,i in base['global_skill_unchanged'].items():assert sha(skill/n)==i['sha256']
link=pathlib.Path('/home/ubuntu/.codex/skills/readme-site');assert link.is_symlink()and link.resolve()==skill
receipt=json.loads((root/'docs/evidence/gui-native-actions-20261025/receipt.json').read_text());assert sha(root/'docs/evidence/gui-native-actions-20261025/originals.tar.gz')==receipt['archive_sha256']
assert(root/'docs/assets/gui-qemu.png').read_bytes()==(root/'website/assets/gui-qemu.png').read_bytes()==pathlib.Path('/tmp/rustusb-native-gui-qemu-filter-20261025/added.png').read_bytes()
for n in files:
 p=out/'source'/n;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/n,p)
for f in [__file__,'/tmp/rustusb-gui-actions-site-preview-20261025.py']:shutil.copyfile(f,out/pathlib.Path(f).name)
browser={'source':'Transcription of actual cua_repl tool observations, not a new automation trace or screenshot','desktop':{'width':1280,'scroll_width':1265,'gui_image_width':1120,'lazy_image_complete_at_immediate_observation':False},'mobile':{'width':390,'scroll_width':375,'gui_image_width':339,'natural_width':1180,'natural_height':760,'image_complete':True},'new_link_keyboard_focus':'IPv4/IPv6 action tests','focus_outline':'solid','script_blocked':{'method':'test response CSP script-src none, not browser-level JS preference','widths':[1280,390],'html_classes':'','visible_panels':['panel-native','panel-virtual','panel-physical'],'copy_button_hidden':True,'evidence_and_build_content_visible':True},'screenshot':'Unable to capture screenshot; no browser pixel pass','reduced_motion':'Existing CSS media rule reviewed; no emulated browser media rerun','viewport_reset':True,'tab_closed':True,'owned_server_actual_wait':143}
summary={'result':'PASS','documents':len(files),'local_links':len(links),'commands':commands,'frozen_inputs':len(final['inputs']),'all_non_readme_inputs_identical_to_8e8d50d9':len(final['inputs'])-1,'changed_inputs':changes,'binaries':final['binaries'],'global_skill_unchanged':base['global_skill_unchanged'],'discovery_symlink':str(link),'site_files':{str(p.relative_to(root/'website')):{'bytes':p.stat().st_size,'sha256':sha(p)}for p in(root/'website').rglob('*')if p.is_file()},'browser':browser,'scope':'Documentation/source/ELF/global skill checks and current browser DOM/keyboard/script-blocked checks. Native screenshot inspected separately; no new browser pixel/reduced-motion pass. CI/Pages pending.'}
(out/'links.json').write_text(json.dumps(links,indent=2)+'\n');(out/'browser-observations.json').write_text(json.dumps(browser,indent=2)+'\n');(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print({k:summary[k]for k in ['result','documents','local_links','frozen_inputs','all_non_readme_inputs_identical_to_8e8d50d9']})
