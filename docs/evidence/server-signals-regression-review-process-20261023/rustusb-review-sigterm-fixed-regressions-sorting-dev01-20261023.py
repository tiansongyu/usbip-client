from pathlib import Path
import json,re,hashlib,subprocess,collections,datetime
ROOT=Path('/home/ubuntu/github/usbip-client')
BASE=Path('/tmp/rustusb-sigterm-fixed-qemu-934358f-20261023')
COMMIT='934358fa457b71c5e3436cc544ca4031b656f147'
NAMES=['virtual','physical-normal','physical-hot']
checks=[]; results={}; inputs={}; blobs={}
def sha(b): return hashlib.sha256(b).hexdigest()
def file(p):
 p=Path(p); b=p.read_bytes(); inputs[str(p)]={'length':len(b),'sha256':sha(b)}; return b
def js(p): return json.loads(file(p))
def ck(name,condition,detail=None):
 checks.append({'name':name,'passed':bool(condition),'detail':detail})
 if not condition: raise AssertionError(name)
def gitblob(n):
 if n not in blobs: blobs[n]=subprocess.check_output(['git','show',COMMIT+':'+n],cwd=ROOT)
 return blobs[n]
allpaths=subprocess.check_output(['git','ls-tree','-r','--name-only',COMMIT],cwd=ROOT,text=True).splitlines()
nativepaths=['Cargo.toml','Cargo.lock','scripts/qemu/guest-init.sh','scripts/qemu/run-native-qemu.py']+[n for n in allpaths if n.startswith('crates/') and (n.endswith('.rs') or n.endswith('/Cargo.toml'))]
h=hashlib.sha256()
for n in sorted(nativepaths):
 b=gitblob(n); nb=n.encode(); h.update(len(nb).to_bytes(8,'big')); h.update(nb); h.update(len(b).to_bytes(8,'big')); h.update(b)
nativefp=h.digest()
def fp(virtual):
 h=hashlib.sha256(nativefp)
 own=['run-userspace-qemu.py','guest-userspace-init.sh','userspace-wire-probe.rs'] if virtual else ['run-physical-qemu.py','guest-physical-init.sh','physical-wire-probe.rs']
 for n in own:
  b=gitblob('scripts/qemu/'+n);h.update(n.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
 if not virtual:
  for n in sorted(n for n in allpaths if n.startswith('vendor/') and (n.endswith('.rs') or n.rsplit('/',1)[-1] in ['Cargo.toml','Cargo.lock'])):
   b=gitblob(n);h.update(n.encode());h.update(len(b).to_bytes(8,'big'));h.update(b)
 return h.hexdigest()
def logs(directory,virtual):
 result={}; channels={}
 for role in ['server','client']:
  channels[role]={}
  for channel in (['serial'] if virtual else ['serial','evidence']):
   p=directory/(role+'-'+channel+'.log');b=file(p);t=b.decode('utf-8').replace('\r\n','\n');channels[role][channel]=t
   ck(f'{directory.name}/{role}/{channel}: kernel integrity',not any(x in t for x in ['Kernel panic','BUG:','was already given back','seqnum max']))
   ck(f'{directory.name}/{role}/{channel}: no FAIL or ANSI evidence',': FAIL' not in t and '\x1b[' not in t)
  result[role]=channels[role]['serial' if virtual else 'evidence']
 return result,channels
def block(t,prefix):
 lines=t.splitlines();begin=prefix+'_EVENTS_BEGIN';end=prefix+'_EVENTS_END'
 ck(prefix+': one ordered raw events block',lines.count(begin)==lines.count(end)==1 and lines.index(begin)<lines.index(end))
 return [json.loads(x) for x in lines[lines.index(begin)+1:lines.index(end)]]
def mark(t,m): ck('exact guest assertion: '+m,t.splitlines().count(m)==1)
def receipts(t):
 found=[];decoder=json.JSONDecoder()
 for match in re.finditer(r'(?m)^[ \t]*\{',t):
  try: x,_=decoder.raw_decode(t,match.end()-1)
  except json.JSONDecodeError: continue
  if isinstance(x,dict) and {'local_bus_id','local_device_generation','local_device_address','port','remote_device_id','status','vendor_id','product_id'}<=x.keys(): found.append(x)
 return found
sequence=js(BASE/'sequence.json')
ck('whole sequence terminal PASS/clean934/no remaining',sequence['result']=='PASS' and sequence['git_commit']==COMMIT and sequence['working_tree_clean'] is True and sequence['source_after_guard_passed'] is True and sequence['remaining']==[])
entries={x['name']:x for x in sequence['runs']}
expectedbins={True:{'rustusb':'05dcacc24c173c8a2bbc032fe2f6438f2003f172de9e96cc3ae837c6f3a0f393','rustusb-helper':'390ee333129f852a31869bbfef4394e4b2c85ed2eac05c47d32bc5229d5a2b99'},False:{'rustusb':'adedf4b1c90d5f1a95033d49fd72caa425860d192855d827d5a88eb0e654699e','rustusb-helper':'837aec7045ab08d935e8d887b686db8e160a5c849fd236c653710c0320dca46b'}}
virtualprobes=['control_bound_and_invalid_interface_stall','pending_unlink_suppresses_old_completion','cdc_bulk_out_and_bounded_in_fifo','unknown_version','submit_before_import','truncated_handshake','duplicate_import','oversized_out','negative_length','invalid_direction','invalid_endpoint','wrong_device','iso_rejected','truncated_data_header','all_protocol_checks']+['both_profiles_exclusive_busy','health_two_profiles']*2
physicalprobes=['real_unsupported_control_stall_and_survival','real_control_descriptor_and_speed','real_configuration_barrier_preserves_following_in','real_unlink_reaped_no_old_completion_or_input_loss','real_pending_eof_releases_lease','real_zero_bulk_out_actual_zero','real_one_byte_bulk_in_pending_cancelled','real_zero_bulk_in_original_length','real_non_mps_fifteen_byte_bulk_in','real_ftdi_descriptor_and_speed','all_lifecycle_checks']+['both_real_devices_exclusive_busy','both_real_devices_available']*2
for name in NAMES:
 d=BASE/name;v=name=='virtual';hot=name=='physical-hot';s=js(d/'summary.json');log,channels=logs(d,v);prefix='RUSTUSB_USERSPACE' if v else 'RUSTUSB_PHYSICAL'
 ck(name+': exact recorded commit/clean/build/PASS/guest0',s['git_commit']==COMMIT and s['working_tree_dirty'] is False and s['build_performed'] is True and s['result']=='PASS' and s['guest_exit_codes']=={'server':0,'client':0})
 cmd=['cargo','build','--workspace','--release','--locked']+(['--features','rustusb-cli/rust-backend'] if v else ['--all-features'])
 ck(name+': actual build feature vector',s['build_command']==cmd,cmd)
 ck(name+': feature-specific binary identities',s['binary_sha256']==expectedbins[v],s['binary_sha256'])
 if not v: ck(name+': copied guest binary identities',s['guest_binary_sha256']==expectedbins[v])
 ck(name+': sequence entry and summary byte identity',entries[name]['exit_code']==0 and entries[name]['result']=='PASS' and entries[name]['summary_sha256']==sha((d/'summary.json').read_bytes()))
 ck(name+': source fingerprint reconstructed from exact Git934 inputs',s['source_fingerprint_sha256']==fp(v),fp(v))
 ck(name+': lock identity from exact Git934',s['cargo_lock_sha256']==sha(gitblob('Cargo.lock')))
 for n,digest in s['script_sha256'].items(): ck(name+': exact Git script '+n,sha(gitblob('scripts/qemu/'+n))==digest)
 if (d/'source').is_dir():
  for p in sorted((d/'source').iterdir()): ck(name+': retained source copy '+p.name,file(p)==gitblob('scripts/qemu/'+p.name))
 build=file(d/'build.log').decode('utf-8');ck(name+': actual successful release build log','Finished `release` profile' in build and not re.search(r'^error',build,re.M))
 runner=file(BASE/(name+'-runner.log')).decode('utf-8');ck(name+': actual terminal runner PASS',bool(re.search(r'"result"\s*:\s*"PASS"',runner)))
 for role in ['server','client']:
  mark(log[role],prefix+'_RESULT: PASS role='+role)
  mark(log[role],prefix+'_ISOLATION role='+role+' ip_forward=0')
  if not v: mark(log[role],f'RUSTUSB_PHYSICAL_EVIDENCE_CHANNEL: PASS role={role} device=ttyS1 kernel_console=ttyS0')
  file(d/(role+'-qemu.stderr'))
 probes=[json.loads(x) for x in log['client'].splitlines() if x.startswith('{"event":"'+('probe_case' if v else 'physical_probe_case')+'"')]
 ck(name+': raw exact probe multiset and total',collections.Counter(x['case'] for x in probes)==collections.Counter(virtualprobes if v else physicalprobes) and all(x['result']=='PASS' for x in probes),{'count':len(probes)})
 ck(name+': raw probes match recorded summary',probes==s['protocol_probe_cases'])
 ev=block(log['server'],prefix);ck(name+': raw event bytes parse to recorded events',ev==s['server_events'])
 ck(name+': schema/no error/one ready/one final stopped',all(e.get('schema_version')==1 and e.get('event')!='error' for e in ev) and sum(e['event']=='ready' for e in ev)==1 and sum(e['event']=='stopped' for e in ev)==1 and ev[-1]['event']=='stopped')
 snaps={e['id']:e['snapshot'] for e in ev if e['event']=='snapshot'}
 ids={'baseline','held-1','io-1','released-1','held-2','io-2','released-2'}|({'unplug-held','unplug-gone','unplug-io','unplug-released','replug-stale'} if hot else set())
 ck(name+': exact snapshot timeline',set(snaps)==ids and len(snaps)==sum(e['event']=='snapshot' for e in ev))
 rec=receipts(log['client']);ck(name+': four complete printed cycle receipts',len(rec)==4 and all(r['status']==6 and r['local_device_address']>0 and r['local_device_generation']['inode']>0 and r['local_device_generation']['filesystem_device']>0 for r in rec))
 expectedids=[(0x1234,1),(0x1234,2)] if v else [(0x0627,1),(0x0403,0x6001)]
 for cycle in [1,2]:
  rr=rec[(cycle-1)*2:cycle*2];ck(name+f': cycle{cycle} distinct ports and device identities',rr[0]['port']!=rr[1]['port'] and rr[0]['local_bus_id']!=rr[1]['local_bus_id'] and [(r['vendor_id'],r['product_id']) for r in rr]==expectedids)
  for role,m in [('client',prefix+f'_HID: PASS cycle={cycle} key_code={cycle+1} down=true up=true same_receipt=true'),('client',prefix+f'_CLIENT_RELEASED: PASS cycle={cycle} kernel_sessions=0 both_devices_removed=true')]: mark(log[role],m)
 for i in [0,1]: ck(name+': reconnect printed generation changed '+str(i),rec[i]['local_device_generation']!=rec[i+2]['local_device_generation'] and rec[i]['local_device_address']!=rec[i+2]['local_device_address'])
 final=ev[-1]['snapshot']
 if v:
  for label in ['baseline','released-1','released-2']:
   snap=snaps[label];ck(name+': released profile state '+label,len(snap['devices'])==2 and all(not q['leased'] and q['pending_urbs']==q['hid_queued_reports']==q['cdc_queued_bytes']==q['configuration']==0 for q in snap['devices']))
  ck(name+': final profile cleanup',all(not q['leased'] and q['pending_urbs']==q['hid_queued_reports']==q['cdc_queued_bytes']==q['configuration']==0 for q in final['devices']))
  out=b'RUSTUSB-PROBE-01';inside=out
  for cycle in [1,2]:
   tx=f'RUSTUSB-TX-{cycle}-0001:fedcba9876543210\n'.encode();rx=f'RUSTUSB-RX-{cycle}-0001:0123456789abcdef\n'.encode();out+=tx;inside+=rx+tx
   devices=snaps[f'io-{cycle}']['devices'];cdc=next(x for x in devices if x['profile']=='cdc_acm');hid=next(x for x in devices if x['profile']=='hid_keyboard')
   ck(name+f': cycle{cycle} actual held dual lease/config',len(devices)==2 and all(x['leased'] and x['configuration']==1 for x in devices))
   ck(name+f': cycle{cycle} CDC nondefault line coding/DTR/RTS',cdc['line_coding']=={'rate':57600,'data_bits':8,'parity':0,'stop_bits':0} and cdc['line_state']==3)
   ck(name+f': cycle{cycle} cumulative exact CDC digest and counts',cdc['cdc_out_sha256']==sha(out) and cdc['cdc_in_sha256']==sha(inside) and cdc['counters']['cdc_out_bytes']==len(out) and cdc['counters']['cdc_in_bytes']==len(inside) and cdc['counters']['cdc_injected_bytes']==cycle*35)
   ck(name+f': cycle{cycle} HID taps and actual interrupt',hid['counters']['hid_taps']==cycle and hid['counters']['interrupt']>=2*cycle)
   mark(log['client'],f'RUSTUSB_USERSPACE_CDC: PASS cycle={cycle} injected_in=35 out=35 echoed_in=35 exact_bytes=true tty_echo=false same_receipt=true')
   mark(log['client'],f'RUSTUSB_USERSPACE_ATTACHED: PASS cycle={cycle} sessions=2 hid_driver=usbhid cdc_driver=cdc_acm cdc_interfaces=2')
   mark(log['server'],f'RUSTUSB_USERSPACE_SERVER_RELEASED: PASS cycle={cycle} lease=0 pending=0 queues=0')
  finalcdc=next(x for x in final['devices'] if x['profile']=='cdc_acm');ck(name+': final exact CDC 86/156/70 and SHA',finalcdc['cdc_out_sha256']==sha(out) and finalcdc['cdc_in_sha256']==sha(inside) and [finalcdc['counters'][k] for k in ['cdc_out_bytes','cdc_in_bytes','cdc_injected_bytes']]==[86,156,70])
  mark(log['server'],'RUSTUSB_USERSPACE_SERVER_MODULES: PASS usbip_host=false vhci_hcd=false');mark(log['server'],'RUSTUSB_USERSPACE_MALFORMED_SURVIVAL: PASS same_pid=true uid=1000 lease=0 pending=0')
  result={'probe_executions':len(probes),'printed_receipts':rec,'cdc_exact_reconstructed':{'out_bytes':len(out),'in_bytes':len(inside),'injected':70,'out_sha256':sha(out),'in_sha256':sha(inside)}}
 else:
  baseline=snaps['baseline']['devices'];ck(name+': descriptor speed/EP0 identities',[(q['speed'],q['ep0_max_packet_size']) for q in baseline]==[(3,64),(2,8)])
  ck(name+': original driver bindings',baseline[0]['original_drivers']==[{'interface':0,'driver':'usbhid'}] and baseline[1]['original_drivers']==[{'interface':0,'driver':'ftdi_sio'}])
  for label in ['baseline','released-1','released-2']+([] if hot else ['final']):
   snap=final if label=='final' else snaps[label];ck(name+': restored physical resources '+label,snap['buffer_bytes_reserved']==0 and len(snap['devices'])==2 and all(q['state']=='available' and not q['leased'] and q['cleanup_verified'] and q['configuration']==1 and q['pending_urbs']==q['queued_urbs']==q['usbfs_inflight']==0 and q['current_drivers']==q['original_drivers'] for q in snap['devices']))
  ck(name+': baseline actual unlink/reap prior wire lifecycle',baseline[0]['cancelled']>=2 and baseline[1]['cancelled']>=1 and all(x['usbfs_reaped']>=4 for x in baseline))
  for cycle in [1,2]:
   for phase in ['held','io']:
    devices=snaps[f'{phase}-{cycle}']['devices'];ck(name+f': actual physical claims {phase}{cycle}',len(devices)==2 and all(q['state']=='leased' and q['leased'] and q['configuration']==1 and q['current_drivers']==[{'interface':0,'driver':'usbfs'}] for q in devices))
   mark(log['client'],f'RUSTUSB_PHYSICAL_FTDI: PASS cycle={cycle} in=35 out=35 exact_bytes=true tty_echo=false same_receipt=true');mark(log['server'],f'RUSTUSB_PHYSICAL_SERVER_RELEASED: PASS cycle={cycle} inflight=0 drivers=usbhid,ftdi_sio')
  count=4 if hot else 2;payload=b''.join(f'RUSTUSB-TX-{i}-0001:fedcba9876543210\n'.encode() for i in range(1,count+1));so=s['serial_output']
  ck(name+': host FTDI expected exact full ledger',so['exact_match'] is True and so['expected_bytes']==so['received_bytes']==len(payload) and so['sha256']==sha(payload) and s['serial_received_bytes']==len(payload) and s['serial_received_sha256']==sha(payload),so)
  ck(name+': probe USB status and payload accounted separately',s['serial_input_ledger']=={'raw_probe_payload_bytes':13,'raw_probe_usb_status_bytes':2,'kernel_serial_payload_bytes':35*count})
  for marker in ['RUSTUSB_PHYSICAL_FD_GATE: PASS direct_uid1000=denied direct_uid1001=denied nodes=root0600','RUSTUSB_PHYSICAL_OPEN_FD_DENIED: PASS uid=1001 route=open_physical json_refusal=true closed=true','RUSTUSB_PHYSICAL_SERVER_UID: PASS uid=1000 app_root=false capabilities=0']:
   mark(log['server'],marker)
  if hot:
   for label in ['unplug-gone','unplug-io','unplug-released','replug-stale']:
    hh,ff=snaps[label]['devices'];ck(name+': old HID stays gone '+label,hh['bus_id']==baseline[0]['bus_id'] and hh['device_id']==baseline[0]['device_id'] and hh['state']=='gone' and not hh['leased'] and hh['pending_urbs']==hh['queued_urbs']==hh['usbfs_inflight']==0)
   fbefore=snaps['unplug-gone']['devices'][1];fafter=snaps['unplug-io']['devices'][1]
   ck(name+': remaining FTDI same slot continues real bidirectional I/O',fbefore['device_id']==fafter['device_id']==baseline[1]['device_id'] and fbefore['state']==fafter['state']=='leased' and fbefore['leased'] and fafter['leased'] and fafter['counters']['bulk_in']>fbefore['counters']['bulk_in'] and fafter['counters']['bulk_out']>fbefore['counters']['bulk_out'])
   for label in ['unplug-released','replug-stale','final']:
    snap=final if label=='final' else snaps[label];hh,ff=snap['devices'];ck(name+': hot final resources '+label,snap['buffer_bytes_reserved']==0 and all(q['pending_urbs']==q['queued_urbs']==q['usbfs_inflight']==0 for q in snap['devices']) and ff['state']=='available' and not ff['leased'] and ff['cleanup_verified'] and ff['current_drivers']==ff['original_drivers']==baseline[1]['original_drivers'])
   for role,m in [('server','RUSTUSB_PHYSICAL_GONE: PASS old_hid_gone=true ftdi_leased=true inflight_hid=0'),('server','RUSTUSB_PHYSICAL_REINSERT_STALE: PASS new_generation=true old_slot_gone=true nodes=root0600'),('client','RUSTUSB_PHYSICAL_UNPLUG: PASS hid_kernel_gone=true ftdi_same_receipt=true'),('client','RUSTUSB_PHYSICAL_FTDI_CONTINUES: PASS before=35 after=35 exact_bytes=true same_receipt=true'),('client','RUSTUSB_PHYSICAL_NO_AUTO_AUTH: PASS reinserted_hid_not_advertised=true available_devices=1')]:mark(log[role],m)
   ck(name+': actual QMP removal recorded',any(e.get('event')=='DEVICE_DELETED' and e.get('data',{}).get('device')=='physical-kbd' for e in s['qmp_events']),s['qmp_events'])
  result={'probe_executions':len(probes),'printed_cycle_receipts':rec,'ftdi_host_recorded_bytes':len(payload),'ftdi_independently_recomputed_expected_sha256':sha(payload),'hot':hot}
 result.update({'result':'PASS_REVIEW','git_commit':s['git_commit'],'build_command':cmd,'binary_sha256':s['binary_sha256'],'guest_exit_codes':s['guest_exit_codes'],'source_fingerprint_reconstructed':fp(v),'raw_snapshot_ids':list(snaps),'raw_event_count':len(ev)})
 results[name]=result
ck('virtual and all-features deliberately different ELF groups',expectedbins[True]!=expectedbins[False] and results['physical-normal']['binary_sha256']==results['physical-hot']['binary_sha256'])
ck('three reviewed runs represent49probe executions and6guest0',sum(x['probe_executions'] for x in results.values())==49 and sum(len(x['guest_exit_codes']) for x in results.values())==6)
# The older runners enforce post-source/binary guards before setting PASS but do not persist after hashes.
sourceguardnotes={}
for n in ['run-userspace-qemu.py','run-physical-qemu.py']:
 t=gitblob('scripts/qemu/'+n).decode();a=t.index('if source_fingerprint() != summary["source_fingerprint_sha256"]:');b=t.index('summary["result"] = "PASS"',a)
 ck(n+': exact frozen source enforces post source/binary before PASS',a<b and 'release binaries changed' in t[a:b])
 sourceguardnotes[n]='Producer enforces post source and binary equality before PASS; separate after-hash fields are absent, not manufactured in this review.'
for p,meta in list(inputs.items()): ck('review input unchanged: '+p,Path(p).stat().st_size==meta['length'] and sha(Path(p).read_bytes())==meta['sha256'])
report={'schema_version':1,'review_kind':'independent offline raw/source/context verification; no runner verifier imported or called','result':'PASS_REVIEW','at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'frozen_commit':COMMIT,'check_count':len(checks),'checks':checks,'failures':[x for x in checks if not x['passed']],'runs':results,'source_guard_scope':sourceguardnotes,'input_artifacts':inputs,'git_source_files_reconstructed':len(blobs),'limitations':['No build, QEMU, child execution, signals, host networking, USB/module/ptrace operation or repository write performed. Only /tmp review script/report are written.','Virtual CDC digests and byte totals were independently reconstructed from known synthetic payloads and checked against raw backend events. HID/serial native-driver success also relies on exact guest assertions; underlying evdev/TTY capture files are not preserved by these older successful runners.','Physical FTDI host raw payload file is not persisted. Review recomputes expected SHA and checks actual recorded sink byte count/SHA, guest exact-byte assertions, and frozen producer enforcement; it does not claim an independent decode of an unavailable raw sink file.','Four printed attach receipts per run prove the two normal cycles and changed reconnect generations. The third hot-phase receipt files are not printed/persisted; same-receipt hot assertions are guest validations, corroborated by the unchanged FTDI slot and increasing real counters, not a new independently deserialized third client receipt.','Hot reinsertion device generation/node changes are explicitly checked by the frozen guest and strict raw marker; full new-device receipt is not persisted by this older harness. Old slot remains Gone and client advertising excludes reinserted HID.','These three regressions are not the new SIGTERM acceptance scenario and do not establish repeated-signal cleanup, SIGKILL recovery, kernel-internal blocking or real hardware support.']}
out=Path('/tmp/rustusb-sigterm-fixed-regressions-independent-review-20261023.json');out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'path':str(out),'bytes':out.stat().st_size,'sha256':sha(out.read_bytes()),'result':report['result'],'checks':len(checks),'runs':{n:{'probe_executions':x['probe_executions'],'guest_exit_codes':x['guest_exit_codes']} for n,x in results.items()}}))
