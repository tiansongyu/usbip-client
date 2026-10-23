import copy, hashlib, importlib.util, json, pathlib, sys
sys.dont_write_bytecode=True
root=pathlib.Path('/home/ubuntu/github/usbip-client')
def load(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
n=load('n',root/'scripts/qemu/run-autorestore-negative-qemu.py');p=load('p',root/'scripts/qemu/run-autorestore-qemu.py');b=load('b',root/'scripts/qemu/run-journal-qemu.py')
src=root/'docs/evidence/physical-journal-final-933e720-20261023/journal';old=(src/'server-evidence.log').read_text();client=(src/'client-evidence.log').read_text()
rs={r['label']:r for r in b.json_block(old,'RECORDS')};ob={r['label']:r for r in b.json_block(old,'OBSERVATIONS')};ws={w['label']:w for w in b.json_block(old,'WAITS') if 'label' in w}
def enc(x):return json.dumps(x,separators=(',',':'))
def line(pre,x):return pre+enc(x)+'\n'
def block(tag,values):return 'RUSTUSB_JOURNAL_'+tag+'_BEGIN\n'+''.join(enc(x)+'\n' for x in values)+'RUSTUSB_JOURNAL_'+tag+'_END\n'
def fixture(mode):
 labels=['initial','normal-ready','normal-pending','normal-closed','reopened-ready','reopened-closed','killed-ready','killed-pending','negative-observed','negative-new-grant-refused']+(['manual-restored'] if mode=='legacy-schema1' else [])+['tail-retired','recovered-ready','recovered-pending','recovered-closed']
 mapping={'negative-observed':'killed-post','negative-new-grant-refused':'denied-before-helper-restart-refused','manual-restored':'guest-admin-restored','tail-retired':'admin-retired',**{k:k.replace('recovered','reconciled') for k in labels if k.startswith('recovered')}}
 records=[];observed=[]
 for label in labels:
  origlabel=mapping.get(label,label);r=copy.deepcopy(rs[origlabel]);r['label']=label
  version=1 if label=='initial' or mode=='legacy-schema1' and labels.index(label)<labels.index('recovered-ready') else 2
  r['journal']['schema_version']=version
  if version==2:
   for entry in r['journal']['records']:entry['recovery']='connect_original_drivers'
   r['pending']['storage_schema_version']=2;r['pending']['recovery_authorizations']=[{'lease_id':e['lease_id'],'recovery':e['recovery']} for e in r['journal']['records']]
  if mode=='external-claim' and label in ['negative-observed','negative-new-grant-refused']:
   for d in r['sysfs']:d['interfaces'][0]['driver']='usbfs'
  r['journal_sha256']=hashlib.sha256((enc(r['journal'])+'\n').encode()).hexdigest();records.append(r)
  if origlabel in ob:
   x=copy.deepcopy(ob[origlabel]);x['label']=label;x['epoch']=x['epoch'].replace('reconciled','recovered');observed.append(x)
 by={r['label']:r for r in records};original=by['initial']['sysfs'];killed=by['killed-pending']['journal'];owner=ob['killed-pending']['identity'];ids=[d['device_id'] for d in original]
 helpers=[{'epoch':i,'pid':10000+i,'starttime':i*10,'exe':'/usr/libexec/rustusb-helper-legacy' if mode=='legacy-schema1' and i==1 else '/usr/bin/rustusb-helper','sha256':'2'*64 if mode=='legacy-schema1' and i==1 else '1'*64,'uids':[0]*4} for i in [1,2,3]]
 binaries={'helpers':[{'path':'/usr/bin/rustusb-helper','sha256':'1'*64}]+([{'path':'/usr/libexec/rustusb-helper-legacy','sha256':'2'*64}] if mode=='legacy-schema1' else []),'external_claim_probe':{'sha256':'3'*64} if mode=='external-claim' else None,'legacy_commit':n.LEGACY_COMMIT if mode=='legacy-schema1' else None}
 meta={'scenario':mode,'boot_id':killed['records'][0]['owner']['boot_id'],'binaries':binaries,'tail_automatic_recovery':mode!='legacy-schema1'}
 policies=[]
 for i in [1,2,3]:
  policy={'allowed_uids':[1000],'export_device_ids':[],'physical_device_ids':ids,'peers':[],'trusted_network':True}
  if not(mode=='legacy-schema1' and i==1):policy['physical_connect_restore_device_ids']=[] if mode=='policy-revoked' and i==2 else ids
  raw=enc(policy)+'\n';policies.append({'helper_epoch':i,'raw':raw,'policy':policy,'sha256':hashlib.sha256(raw.encode()).hexdigest()})
 def ops(phase):
  out=[]
  for lease in [e['lease_id'] for e in killed['records']]:
   for ph in ['attempt',phase]:
    raw=f'INFO operation="physical_auto_restore" lease_id="{lease}" phase="{ph}"'
    if ph=='failed':raw+=' reason="physical device is unsafe, already exported, claimed, or incompletely described"'
    out.append({'lease_id':lease,'phase':ph,'raw':raw})
  return out
 logs=[]
 def helperlog(label,epoch,operations):
  row={'label':label,'tick':1,'helper':helpers[epoch-1],'operations':operations,'log':'\n'.join(o['raw'] for o in operations)+'\n'};logs.append(row);return line('RUSTUSB_AUTONEG_HELPER_LOG ',row)
 pure=[];windows=[]
 external={'pid':20001,'starttime':200,'exe':'/usr/bin/usbfs-external-claim-probe','uids':[0]*4,'sha256':'3'*64,'fds':owner['fds']}
 def readonly(label,tick,sysfs,journal,epoch,c=None):
  row={'label':label,'sequence':len(pure)+1,'tick':tick,'sysfs':copy.deepcopy(sysfs),'journal':journal,'journal_raw':enc(journal)+'\n','journal_directory_owner_mode':'0:700','journal_file_owner_mode':'0:600','helper':helpers[epoch-1] if epoch else None,'external':c,'pending_api_called':False};row['journal_sha256']=hashlib.sha256(row['journal_raw'].encode()).hexdigest();pure.append(row);return line('RUSTUSB_AUTONEG_READONLY ',row)
 def window(label,start,end,maximum=False):
  row={'label':label,'start_tick':start,'end_tick':end,('maximum_centiseconds' if maximum else 'minimum_centiseconds'):(2000 if maximum or label=='rejected' else 500)};windows.append(row);return line('RUSTUSB_AUTONEG_WINDOW ',row)
 sw=[]
 for epoch in ['normal','reopened','killed','negative-new-grant','recovered']:
  name={'negative-new-grant':'denied-before-helper-restart','recovered':'reconciled'}.get(epoch,epoch);x=copy.deepcopy(ws[name]);x['label']=epoch;sw.append(x)
 hw=[{'event':'negative_helper_wait','epoch':i,'pid':helpers[i-1]['pid'],'starttime':helpers[i-1]['starttime'],'wait_code':0,'signal':15} for i in [1,2]]
 waits=[sw[0],sw[1],hw[0],sw[2],sw[3]];cw={'event':'external_c_wait','pid':20001,'starttime':200,'wait_code':0}
 if mode=='external-claim':waits.append(cw)
 waits+=[hw[1],sw[4]]
 markerprefix=('RUSTUSB_JOURNAL_EVIDENCE_CHANNEL:','RUSTUSB_JOURNAL_ISOLATION:','RUSTUSB_JOURNAL_FD_GATE:','RUSTUSB_JOURNAL_SERVER_READY:','RUSTUSB_JOURNAL_DISK_GATE:')
 s='\n'.join(x for x in old.splitlines() if x.startswith(markerprefix))+'\n'+line('RUSTUSB_AUTONEG_METADATA ',meta)
 s+=line('RUSTUSB_AUTONEG_POLICY ',policies[0])+line('RUSTUSB_AUTONEG_HELPER_START ',helpers[0])
 def record(row):
  result=line('RUSTUSB_JOURNAL_RECORD ',row)
  for kind,value in [('DISK',row['journal']),('PENDING',row['pending'])]:result+=f'RUSTUSB_JOURNAL_{kind}_BEGIN label={row["label"]}\n'+enc(value)+'\n'+f'RUSTUSB_JOURNAL_{kind}_END label={row["label"]}\n'
  return result
 for r in records[:8]:s+=record(r)
 for w in waits[:3]:s+=line('RUSTUSB_JOURNAL_WAIT ',w)
 s+=helperlog('before-stop',1,[])
 s+=f'RUSTUSB_JOURNAL_SIGNAL: PASS signal=9 count=1 sender_uid=1000 pid={owner["pid"]} starttime={owner["starttime"]}\n'+line('RUSTUSB_JOURNAL_WAIT ',sw[2])
 null=copy.deepcopy(original)
 for d in null:d['interfaces'][0]['driver']=None
 s+=readonly('killed-with-helper-stopped',0,null,killed,0)
 c_events=[]
 if mode=='external-claim':
  s+=line('RUSTUSB_AUTONEG_C_CONTROL ',{'directory_owner_mode':'0:700','fifo_owner_mode':'0:600','is_fifo':True,'shell_holds_control_fd':False})
  for i,d in enumerate(original):
   rec=next(e['baseline']['receipt'] for e in killed['records'] if e['baseline']['bus_id']==d['bus_id'])
   for phase in ['before-open','independently-opened','claimed']:
    c_events.append({'event':'state','phase':phase,'device':i,'pid':20001,'starttime':200,'node_dev':int(d['node_identity'].split(':')[0]),'node_inode':rec['node_inode'],'node_rdev':rec['node_device'],'sysfs_dev':rec['sysfs_device'],'sysfs_inode':rec['sysfs_inode'],'bus_number':rec['bus_number'],'device_number':rec['usb_device_number'],'configuration':1,'interfaces':[{'number':0,'alternate':0,'driver':'usbfs' if phase=='claimed' else None}]})
    if phase=='independently-opened':c_events.append({'event':'ioctl','op':'claim','device':i,'interface':0,'rc':0,'errno':0})
  c_events.append({'event':'ready','pid':20001,'starttime':200,'uid':0});s+=''.join(line('RUSTUSB_EXTERNAL_C ',e) for e in c_events)
 if mode=='external-claim':s+=readonly('c-claimed-before-helper',50,by['negative-observed']['sysfs'],killed,0,external)
 s+=line('RUSTUSB_AUTONEG_POLICY ',policies[1])+line('RUSTUSB_AUTONEG_HELPER_START ',helpers[1])
 negative_state=by['negative-observed']['sysfs']
 for tick in [100,2100]:s+=readonly('rejected',tick,negative_state,killed,2,external if mode=='external-claim' else None)
 s+=window('rejected',100,2101)+helperlog('rejected',2,ops('failed') if mode=='external-claim' else [])+line('RUSTUSB_AUTONEG_REJECTION_PRIMARY ',pure[-1])
 s+=record(by['negative-observed'])+line('RUSTUSB_JOURNAL_WAIT ',sw[3])+record(by['negative-new-grant-refused'])+helperlog('after-refused-grant',2,ops('failed') if mode=='external-claim' else [])
 if mode=='external-claim':
  more=[]
  for i,d in enumerate(original):
   rec=next(e['baseline']['receipt'] for e in killed['records'] if e['baseline']['bus_id']==d['bus_id'])
   for phase in ['before-release','released','closed']:
    more.append({'event':'state','phase':phase,'device':i,'pid':20001,'starttime':200,'node_dev':int(d['node_identity'].split(':')[0]),'node_inode':rec['node_inode'],'node_rdev':rec['node_device'],'sysfs_dev':rec['sysfs_device'],'sysfs_inode':rec['sysfs_inode'],'bus_number':rec['bus_number'],'device_number':rec['usb_device_number'],'configuration':1,'interfaces':[{'number':0,'alternate':0,'driver':'usbfs' if phase=='before-release' else None}]})
    if phase=='before-release':more.append({'event':'ioctl','op':'release','device':i,'interface':0,'rc':0,'errno':0})
    if phase=='released':more.append({'event':'close','device':i,'close_rc':0,'getfd_rc':-1,'getfd_errno':9})
  more.append({'event':'done'});c_events+=more;s+=''.join(line('RUSTUSB_EXTERNAL_C ',e) for e in more)+line('RUSTUSB_JOURNAL_WAIT ',cw)
  for tick in [2200,2700]:s+=readonly('released-no-retry',tick,null,killed,2)
  s+=window('released-no-retry',2200,2701)+helperlog('released-no-retry',2,ops('failed'))
 s+=helperlog('before-stop',2,ops('failed') if mode=='external-claim' else [])+line('RUSTUSB_JOURNAL_WAIT ',hw[1])+line('RUSTUSB_AUTONEG_POLICY ',policies[2])+line('RUSTUSB_AUTONEG_HELPER_START ',helpers[2])
 admin=[]
 if mode=='legacy-schema1':
  for tick in [3000,3500]:s+=readonly('manual-restored-still-pending',tick,original,killed,3)
  s+=window('manual-restored-still-pending',3000,3501)+helperlog('manual-restored-still-pending',3,[])+record(by['manual-restored'])
  for entry in killed['records']:
   lease=entry['lease_id'];admin.append({'phase':'legacy-manual-retired','lease_id':lease,'uid':0,'exit_code':0,'stdout':enc({'lease_id':lease,'observation':'restored','status':'retired'}),'stderr':''})
 else:
  s+=readonly('tail-automatic-recovery',3000,original,{'schema_version':2,'records':[]},3)+line('RUSTUSB_AUTONEG_RECOVERY_PRIMARY ',pure[-1])+window('tail-automatic-recovery',3000,3001,True)
 s+=record(by['tail-retired'])
 for label in ['recovered-ready','recovered-pending','recovered-closed']:s+=record(by[label])
 s+=line('RUSTUSB_JOURNAL_WAIT ',sw[-1])+helperlog('final',3,[] if mode=='legacy-schema1' else ops('retired'))
 for epoch in ['normal','reopened','killed','negative-new-grant','recovered']:
  originalepoch={'negative-new-grant':'denied-before-helper-restart','recovered':'reconciled'}.get(epoch,epoch)
  s+=f'RUSTUSB_JOURNAL_EVENTS_BEGIN epoch={epoch}\n'+''.join(enc(e)+'\n' for e in b.epoch_events(old,originalepoch))+f'RUSTUSB_JOURNAL_EVENTS_END epoch={epoch}\n'
 if mode in n.SCENARIOS:s+='RUSTUSB_JOURNAL_STDERR_BEGIN epoch=negative-new-grant\nunresolved physical grant blocks new authorization; inspect physical-pending and reconcile the original baseline\nRUSTUSB_JOURNAL_STDERR_END epoch=negative-new-grant\n'
 s+=block('RECORDS',records)+block('OBSERVATIONS',observed)+block('WAITS',waits)+block('ADMIN',admin)+'RUSTUSB_JOURNAL_COLLECTION_COMPLETE role=server\nRUSTUSB_JOURNAL_RESULT: PASS role=server\n'
 return {'server':s,'client':client},{'server':'','client':''},binaries
cases=[]
for mode in n.SCENARIOS:
 program,kernel,binaries=fixture(mode);out=n.verify_negative(b,p,mode,program,kernel,binaries)
 failed=[c for c in out['checks'] if c['result']!='PASS'];print(mode,len(out['checks']),failed)
 assert not failed
 cases.append({'scenario':mode,'kind':'synthetic_parser_fixture_only','checks':len(out['checks']),'passed':True})
 bad=copy.deepcopy(program);bad['server']=bad['server'].replace('"wait_code":137','"wait_code":0');out=n.verify_negative(b,p,mode,bad,kernel,binaries)
 assert any(c['name']=='one_owned_actual_KILL137' and c['result']=='FAIL' for c in out['checks'])
 cases.append({'scenario':mode,'kind':'bad_wait_rejected','passed':True})
report={'scope':'Constructed parser fixtures using archived I/O objects, not new USB/recovery runtime evidence.','result':'PASS','cases':cases,'source_sha256':{f:hashlib.sha256((root/'scripts/qemu'/f).read_bytes()).hexdigest() for f in n.OWN}}
out=pathlib.Path('/tmp/rustusb-autorestore-negative-verifier-fixtures-20261023.json');out.write_text(json.dumps(report,indent=2)+'\n');print(out)
