#!/usr/bin/env python3
"""Disposable ordinary-user filesystem fixtures only; no CLI or subprocesses."""
import ast,hashlib,importlib.util,json,os,stat,tempfile
from pathlib import Path
from unittest import mock
ROOT=Path('/home/ubuntu/github/usbip-client');SCRIPT=ROOT/'scripts/test-process-logging.py';OUT=Path('/tmp/rustusb-logging-cache-precondition-fix-20261024')
source=SCRIPT.read_bytes();ast.parse(source,filename=str(SCRIPT))
spec=importlib.util.spec_from_file_location('logging_cache_source',SCRIPT);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
rows=[]
def ck(name,fn):
 try: detail=fn();rows.append({'fixture':name,'pass':True,'detail':detail})
 except Exception as e:rows.append({'fixture':name,'pass':False,'error':type(e).__name__+': '+str(e)})
def must_refuse(home):
 try:module.prepare_cache(home)
 except (RuntimeError,OSError):return True
 raise AssertionError('unsafe path unexpectedly accepted')
def mode(path):return stat.S_IMODE(path.lstat().st_mode)
home=Path.home();home_before=(home.lstat().st_dev,home.lstat().st_ino,home.lstat().st_uid,mode(home));env_before=dict(os.environ)
with tempfile.TemporaryDirectory(prefix='rustusb-cache-fixture-',dir=home) as tmp:
 base=Path(tmp)
 def make(name):p=base/name;p.mkdir(mode=0o700);return p
 def missing():
  p=make('missing');c=module.prepare_cache(p);assert c==p/'.cache' and mode(c)==0o700 and c.lstat().st_uid==os.getuid();return {'actual_mode':oct(mode(c)),'actual_uid':c.lstat().st_uid}
 ck('missing cache creates actual owned0700',missing)
 def existing():
  p=make('existing');c=p/'.cache';c.mkdir(mode=0o755);before=c.lstat();module.prepare_cache(p);after=c.lstat();assert mode(c)==0o755 and before.st_ino==after.st_ino and before.st_mode==after.st_mode;return {'actual_existing_mode_preserved':oct(mode(c))}
 ck('existing safe0755 unchanged',existing)
 def link():
  p=make('leaf-link');target=make('leaf-target');(p/'.cache').symlink_to(target,target_is_directory=True);return must_refuse(p)
 ck('cache symlink refused without following or repairing',link)
 def regular():
  p=make('leaf-file');(p/'.cache').write_bytes(b'fixture');return must_refuse(p)
 ck('cache regular file refused',regular)
 def writable():
  p=make('writable-cache');c=p/'.cache';c.mkdir(mode=0o777);old=mode(c);assert old&0o022;must_refuse(p);assert mode(c)==old;return {'unsafe_existing_mode_unmodified':oct(old)}
 ck('writable existing cache refused not chmodded',writable)
 def unsafe_parent():
  ancestor=base/'unsafe-ancestor';ancestor.mkdir(mode=0o777);p=ancestor/'home';p.mkdir(mode=0o700);must_refuse(p);assert not (p/'.cache').exists();return {'actual_ancestor_mode':oct(mode(ancestor)),'nothing_created':True}
 ck('writable ancestor refused before creation',unsafe_parent)
 def home_link():
  real=make('real-home');p=base/'linked-home';p.symlink_to(real,target_is_directory=True);must_refuse(p);assert not (real/'.cache').exists();return True
 ck('HOME symlink refused before creation',home_link)
 def ancestor_link():
  real=make('real-parent');(real/'home').mkdir(mode=0o700);alias=base/'linked-parent';alias.symlink_to(real,target_is_directory=True);must_refuse(alias/'home');assert not (real/'home'/'.cache').exists();return True
 ck('ancestor symlink refused before creation',ancestor_link)
 ck('relative HOME refused',lambda:must_refuse(Path('relative-fixture-home')))
 ck('parent traversal HOME refused',lambda:must_refuse(base/'unused'/'..'/'other'))
 def concurrent_safe():
  p=make('concurrent-safe');c=p/'.cache';original=Path.mkdir
  def other_creates(path,*args,**kwargs):
   if path==c:original(path,mode=0o755);raise FileExistsError('fixture concurrent create')
   return original(path,*args,**kwargs)
  with mock.patch.object(Path,'mkdir',new=other_creates):module.prepare_cache(p)
  assert mode(c)==0o755;return {'actual_concurrent_mode_preserved':oct(mode(c))}
 ck('concurrent existing safe cache revalidated not chmodded',concurrent_safe)
 def concurrent_link():
  p=make('concurrent-link');c=p/'.cache';target=make('race-target');original=Path.mkdir
  def other_creates(path,*args,**kwargs):
   if path==c:path.symlink_to(target,target_is_directory=True);raise FileExistsError('fixture concurrent symlink')
   return original(path,*args,**kwargs)
  with mock.patch.object(Path,'mkdir',new=other_creates):must_refuse(p)
  assert c.is_symlink();return True
 ck('concurrent symlink replacement refused',concurrent_link)
assert dict(os.environ)==env_before
assert (home.lstat().st_dev,home.lstat().st_ino,home.lstat().st_uid,mode(home))==home_before
result={'result':'PASS' if all(r['pass'] for r in rows) else 'FAIL','scope':'AST parse and imported pure cache function over newly created disposable ordinary-user FS fixtures. No CLI/build/QEMU/USB operation, HOME environment or preexisting path mode changed. Existing-owner mismatch branch source checked; no false root ownership fixture.','uid':os.getuid(),'source':{'path':str(SCRIPT),'bytes':len(source),'sha256':hashlib.sha256(source).hexdigest()},'fixtures':rows,'fixture_count':len(rows),'HOME_env_unchanged':dict(os.environ)==env_before,'existing_HOME_identity_owner_mode_unchanged':True}
(OUT/'static-fixtures.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
