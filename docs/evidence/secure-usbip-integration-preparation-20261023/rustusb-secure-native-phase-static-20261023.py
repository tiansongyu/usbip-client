"""Constructed JSON assertions only, explicitly not Native/QEMU evidence."""
from pathlib import Path
import copy, hashlib, json, shlex, subprocess, tempfile
source=Path('/home/ubuntu/github/usbip-client/scripts/qemu/guest-secure-init.sh')
tokens=shlex.split(source.read_text(),comments=True)
phase=next(t for t in tokens if t.startswith('\n        def hardware:'))
journal=next(t for t in tokens if t.startswith('\n        .schema_version==1 and .records==[]'))
base=[]
for number,driver in [(1,'usbhid'),(2,'ftdi_sio')]:
    base.append({'bus':f'1-{number}','generation':f'23:{number+100}','devnum':str(number+1),'configuration':'1','num_interfaces':' 1','configuration_bytes':2,'count_bytes':3,'device_driver':'usb','usbip_status':None,'interfaces':[{'name':f'1-{number}:1.0','number':'00','alt':' 0','class':'03' if number==1 else 'ff','subclass':'01','protocol':'01','driver':driver}]})
checks=[]
with tempfile.TemporaryDirectory(prefix='rustusb-native-phase-static-') as temp:
    root=Path(temp);before=root/'before.json';before.write_text(json.dumps(base))
    def check(name,data,program,args,want=True):
        p=subprocess.run(['jq','-e',*args,program],input=json.dumps(data),text=True,capture_output=True)
        passed=p.returncode==0
        if passed!=want:raise RuntimeError(name+': '+p.stdout+p.stderr)
        checks.append({'name':name,'expected':want,'actual_exit':p.returncode})
    def ph(name,data,state,want=True):check(name,data,phase,['--arg','phase',state,'--slurpfile','before',str(before)],want)
    exported=copy.deepcopy(base)
    for d in exported:d.update(configuration='',num_interfaces='',configuration_bytes=0,count_bytes=0,interfaces=[],device_driver='usbip-host',usbip_status=1)
    held=copy.deepcopy(base)
    for d in held:
        d.update(device_driver='usbip-host',usbip_status=2)
        for interface in d['interfaces']:interface['driver']=''
    released=copy.deepcopy(held)
    for d in released:d['usbip_status']=1
    ph('Native truly empty exported profile',exported,'exported')
    ph('Native full configured USED profile',held,'held')
    ph('Native configured AVAILABLE after close',released,'released')
    bad=copy.deepcopy(exported);bad[0]['configuration_bytes']=1;ph('newline is not empty configuration',bad,'exported',False)
    bad=copy.deepcopy(held);bad[0]['generation']='23:999';ph('replacement generation rejected',bad,'held',False)
    bad=copy.deepcopy(held);bad[0]['interfaces'][0]['driver']='usbhid';ph('bound interface rejected during stub lease',bad,'held',False)
    bad=copy.deepcopy(held);bad[0]['interfaces'][0]['alt']=' 1';ph('alternate change rejected',bad,'held',False)
    ph('USED cannot pass released',held,'released',False)
    keys=['native:usb-'+str(i)*64 for i in [1,2]]
    records=[]
    for i,key in enumerate(keys):
        generation={'bus_id':f'1-{i+1}','inode':100+i}
        records.append({'lease_id':f'lease-{i}','owner':{'uid':2000,'pid':123},'registration':{'device_key':key,'original_device_driver':'usb','generation':generation},'bound':{'generation':generation,'bound_profile':{'configuration':None,'interfaces':[]}},'stub':None})
    original={'schema_version':1,'records':[],'exports':records}
    (root/'keys.json').write_text(json.dumps(keys));(root/'original.json').write_text(json.dumps(original))
    def jo(name,data,state,want=True):check(name,data,journal,['--arg','phase',state,'--argjson','pid','123','--slurpfile','keys',str(root/'keys.json'),'--slurpfile','original',str(root/'original.json')],want)
    jo('Native registration no stub',original,'exported')
    observed=copy.deepcopy(original)
    for i,row in enumerate(observed['exports']):row['stub']={'channel_id':f'channel-{i}','state':{'phase':'observed','receipt':{'usbip_status':2,'export':row['bound']}}}
    jo('Native held actual observed receipt shape',observed,'held')
    jo('Native released stable export',original,'released')
    jo('stub receipt cannot pass released',observed,'released',False)
    bad=copy.deepcopy(original);bad['exports'][0]['owner']['pid']=456;jo('different owner cannot pass',bad,'released',False)
    bad=copy.deepcopy(observed);bad['exports'][0]['stub']['state']['receipt']['export']={};jo('mismatched original attr receipt rejected',bad,'held',False)
    bad=copy.deepcopy(original);bad['records']=[{}];jo('unexpected server import rejected',bad,'released',False)
report={'scope':__doc__,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'checks':checks}
Path('/tmp/rustusb-secure-native-phase-static-20261023.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'Native_phase_assertion_fixtures':'PASS','checks':len(checks),'not_guest_evidence':True}))
