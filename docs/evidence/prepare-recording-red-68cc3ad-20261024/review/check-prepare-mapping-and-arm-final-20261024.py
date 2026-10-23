import copy,hashlib,importlib.util,json,os,re,subprocess
from pathlib import Path
repo=Path('/home/ubuntu/github/usbip-client');folder=repo/'scripts/qemu_prepare_recording_shutdown'
out=Path('/tmp/rustusb-prepare-harness-revision2-final-fixtures-20261024');out.mkdir(exist_ok=False)
report={'scope':'static real-jq barrier fixtures and constructed FD/receipt mapping fixtures, no guest/Cargo/QEMU','checks':[]}
def record(label,good,raw):
 report['checks'].append({'check':label,'passed':bool(good)})
 (out/(label+'.json')).write_text(json.dumps(raw,indent=2)+'\n')
 if not good:raise RuntimeError(label)
spec=importlib.util.spec_from_file_location('prepare_verify',folder/'verify.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
base=Path('/tmp/rustusb-data-link-ready-531e0c3-20261024/health-usbfs-ipv4/server-artifacts')
records=json.loads((base/'root-pre-both.json').read_text())['records'];hosts=json.loads((base/'host-before.json').read_text())
nodes=[];fds=[]
for i,r in enumerate(records):
 b=r['baseline'];rc=b['receipt'];bus=b['bus_id'];path='/dev/bus/usb/%03d/%03d'%(rc['bus_number'],rc['usb_device_number']);rdev=f"{os.major(rc['node_device']):x}:{os.minor(rc['node_device']):x}"
 nodes.append({'bus':bus,'node':path,'filesystem_device':'123','inode':str(rc['node_inode']),'rdev':rdev,'sysfs_dev':f"{os.major(rc['node_device'])}:{os.minor(rc['node_device'])}",'generation':f"{rc['sysfs_device']}:{rc['sysfs_inode']}",'mode':'600','uid':0,'links':1,'character_device':True})
 fds.append({'fd':str(20+i),'target':path,'device':'123','inode':str(rc['node_inode']),'rdev':rdev})
basefixture={'fds':fds,'records':records,'hosts':hosts,'nodes':nodes}
variants={
'valid':lambda d:None,
'fd-fs-device':lambda d:d['fds'][0].update(device='999'),
'fd-inode':lambda d:d['fds'][0].update(inode='999'),
'fd-rdev':lambda d:d['fds'][0].update(rdev='bd:7f'),
'fd-path':lambda d:d['fds'][0].update(target='/dev/bus/usb/001/127'),
'root-rdev-not-stdev':lambda d:d['records'][0]['baseline']['receipt'].update(node_device=123),
'root-node-inode':lambda d:d['records'][0]['baseline']['receipt'].update(node_inode=999),
'root-sysfs-generation':lambda d:d['records'][0]['baseline']['receipt'].update(sysfs_inode=999),
'host-generation':lambda d:d['hosts'][0].update(generation='23:999'),
'host-address':lambda d:d['hosts'][0].update(devnum='127'),
'node-sysfs-rdev':lambda d:d['nodes'][0].update(sysfs_dev='189:127'),
'node-character':lambda d:d['nodes'][0].update(character_device=False),
'node-mode':lambda d:d['nodes'][0].update(mode='660'),
'node-owner':lambda d:d['nodes'][0].update(uid=2000),
'node-linkcount':lambda d:d['nodes'][0].update(links=2),
'wrong-config':lambda d:d['records'][0]['baseline'].update(configuration=2),
'wrong-if-driver':lambda d:d['records'][0]['baseline']['interfaces'][0].update(driver='usbfs'),
'wrong-if-alt':lambda d:d['records'][0]['baseline']['interfaces'][0].update(alternate=1),
'missing-fd':lambda d:d['fds'].pop(),
'duplicate-fd':lambda d:d['fds'].append(copy.deepcopy(d['fds'][0])),
'duplicate-receipt':lambda d:d['records'].__setitem__(1,copy.deepcopy(d['records'][0])),
}
for name,mutate in variants.items():
 d=copy.deepcopy(basefixture);mutate(d);facts=m.usbfs_fd_mapping(**d);good=all(p for p,_ in facts);record('mapping-'+name,good==(name=='valid'),{'fixture':d,'facts':facts,'expected_valid':name=='valid'})
s=(folder/'guest-init.sh').read_text();expr=re.search(r"jq -es --arg operation.*? '(length==1[^']*)'",s)[1]
ready={'tracee_pid':77,'tracee_starttime':1234};armed={'event':'armed','nonce':'prepare-stop-v1','operation':'resource',**ready}
variants={'valid':json.dumps(armed)+'\n','empty':'','partial':json.dumps(armed)[:-1],'duplicate':(json.dumps(armed)+'\n')*2,'wrong-pid':json.dumps({**armed,'tracee_pid':78})+'\n','wrong-starttime':json.dumps({**armed,'tracee_starttime':999})+'\n','wrong-nonce':json.dumps({**armed,'nonce':'wrong'})+'\n','wrong-operation':json.dumps({**armed,'operation':'initializer'})+'\n'}
for name,data in variants.items():
 awk=subprocess.run(['awk','/"event":"armed"/ && /}$/ {print}'],input=data.replace(': ',':'),capture_output=True,text=True,check=True)
 p=subprocess.run(['jq','-es','--arg','operation','resource','--argjson','ready',json.dumps([ready]),expr],input=awk.stdout,capture_output=True,text=True)
 record('armed-'+name,(p.returncode==0)==(name=='valid'),{'input':data,'complete_lines':awk.stdout,'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr,'expected_valid':name=='valid'})
report['source_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()};report['check_count']=len(report['checks']);report['passed']=all(c['passed'] for c in report['checks']);(out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'passed':report['passed'],'checks':report['check_count'],'output':str(out)}))
