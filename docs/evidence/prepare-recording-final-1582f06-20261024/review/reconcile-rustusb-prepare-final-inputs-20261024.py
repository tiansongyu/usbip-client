from pathlib import Path
import hashlib,json,subprocess
root=Path('/home/ubuntu/github/usbip-client')
actual=Path('/tmp/rustusb-prepare-stop-software-ab-first-20261024')
rows=json.loads((actual/'inputs-before.json').read_text());assert rows==json.loads((actual/'inputs-after.json').read_text()) and len(rows)==167
commits=['8ec570ad160c59c89303a0f63ed9651696a70b71','2222de87439f61e8a2e939e7dc15cd845d38aa4e','1582f06776f4c1ea50e9b5109a9ad7d0b7cdbe16','5ffdc6cfcc0e3c2de386121d0277d7daf0ed4e9f']
checks=[]
for commit in commits:
    values=[commit+':'+r['path'] for r in rows]
    data=subprocess.check_output(['git','cat-file','--batch'],cwd=root,input=('\n'.join(values)+'\n').encode());offset=0
    for r in rows:
        end=data.index(b'\n',offset);header=data[offset:end].split();assert header[1]==b'blob';size=int(header[2]);offset=end+1;raw=data[offset:offset+size];offset+=size+1
        digest=hashlib.sha256(raw).hexdigest();assert size==r['bytes'] and digest==r['sha256'];assert (root/r['path']).read_bytes()==raw
        source=actual/'source'/r['path'];assert source.read_bytes()==raw
        checks.append(dict(commit=commit,path=r['path'],bytes=size,sha256=digest,git_blob=header[0].decode(),matches_actual_saved_source=True,matches_current_selected_input=True))
    assert offset==len(data)
q=Path('/tmp/rustusb-prepare-stop-final-1582f06-20261024');qrows=json.loads((q/'inputs-before.json').read_text());values=['1582f06776f4c1ea50e9b5109a9ad7d0b7cdbe16:'+r['path'] for r in qrows];data=subprocess.check_output(['git','cat-file','--batch'],cwd=root,input=('\n'.join(values)+'\n').encode());offset=0
for r in qrows:
    end=data.index(b'\n',offset);header=data[offset:end].split();assert header[1]==b'blob';size=int(header[2]);offset=end+1;raw=data[offset:offset+size];offset+=size+1;assert size==r['bytes'] and hashlib.sha256(raw).hexdigest()==r['sha256']
assert offset==len(data)
out=Path('/tmp/rustusb-prepare-final-input-map-20261024');out.mkdir(exist_ok=False)
(out/'report.json').write_text(json.dumps(dict(result='PASS',software_actual='dirty b1de034',software_selected_inputs=167,checks=checks,qemu_commit='1582f06776f4c1ea50e9b5109a9ad7d0b7cdbe16',qemu_selected_inputs=len(qrows),qemu_input_blob_match=True,notes=['Input-by-input reconciliation does not relabel the actual dirty software run as clean.','QEMU matrix currently active; this input map does not assign an outcome to unexecuted cases.','Selected inputs/source checks are not USB case counts.']),indent=2)+'\n')
print('PASS software167 x4 Git commits;QEMU',len(qrows),'Git blobs')
