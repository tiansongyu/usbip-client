from pathlib import Path
import base64, hashlib, json, re, subprocess

root = Path('/home/ubuntu/github/usbip-client')
out = Path('/tmp/rustusb-security-observations-review-c097cf8-20261024')
out.mkdir(exist_ok=False)
head = 'c097cf8556b0b75f95918503daa819dfc2911831'
software = Path('/tmp/rustusb-security-observations-software-first-20261024')
build = json.loads((software/'result.json').read_text())
inputs = {row['path']: {'bytes': row['bytes'], 'sha256': row['sha256']} for row in json.loads((software/'inputs-before.json').read_text())}
binary = {Path(row['path']).name: row['sha256'] for row in build['artifacts']}
directories = [Path(f'/tmp/rustusb-security-observations-{backend}-{family}-first-c097cf8-20261024') for backend in ['native','virtual','usbfs'] for family in ['ipv4','ipv6']]
directories.append(Path('/tmp/rustusb-security-observations-v2-diagnostic-regression-c097cf8-20261024'))

def digest(path):
    data = path.read_bytes()
    return {'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest()}

def inventory():
    return {str(p):digest(p) for directory in directories for p in sorted(directory.rglob('*')) if p.is_file()}

before = inventory()
rows = []
decoded = 0
for directory in directories:
    s = json.loads((directory/'summary.json').read_text())
    assert s['result']=='PASS' and s['guest_exit_codes']=={'client':0,'server':0}
    assert s['head']==s['head_after']==head
    assert s['git_status_before']==s['git_status_after']==''
    assert all(s[name] is True for name in ['source_guard','binary_guard','head_guard','clean_guard'])
    assert s['binary_sha256']==binary and s['build_skipped'] is False
    assert all(s['source_inputs'][name]==value for name,value in inputs.items())
    for name,metadata in s['source_inputs'].items():
        original = subprocess.check_output(['git','show',f'{head}:{name}'],cwd=root)
        assert {'bytes':len(original),'sha256':hashlib.sha256(original).hexdigest()}==metadata
        assert (directory/'source'/name).read_bytes()==original
    for name,metadata in s['artifact_manifest'].items():
        assert digest(directory/name)==metadata
    for role in ['server','client']:
        text = (directory/f'{role}-evidence.log').read_text()
        blocks = re.findall(r'^SECURE_ARTIFACT_BEGIN (\S+) ([0-9]+)\n(.*?)^SECURE_ARTIFACT_END \1\s*$',text,re.M|re.S)
        assert len(blocks)==text.count('SECURE_ARTIFACT_BEGIN ')==len(s['guest_artifacts'][role])
        names=set()
        for name,size,encoded in blocks:
            assert name not in names
            names.add(name)
            raw=base64.b64decode(''.join(encoded.split()),validate=True)
            assert len(raw)==int(size) and raw==(directory/f'{role}-artifacts'/name).read_bytes()
            assert digest(directory/f'{role}-artifacts'/name)==s['guest_artifacts'][role][name]
            decoded+=1
    row={'directory':str(directory),'backend':s['backend'],'family':s['family'],'source_inputs':len(s['source_inputs']),'software_input_subset':len(inputs),'guest_exits':s['guest_exit_codes']}
    if 'v2-diagnostic' not in directory.name:
        deltas=[]
        for label in ['free-1','held-1','free-2','held-2']:
            values={side:json.loads((directory/f'server-artifacts/auth-security-{side}-{label}.json').read_text()) for side in ['before','after']}
            for sample in values.values():
                assert sample['scope']=='loaded_tls_server_configuration' and sample['consistent'] is True and sample['saturated'] is False
                tls=sample['tls'];assert tls['attempts']==sum(value for key,value in tls.items() if key!='attempts')
                assert sample['listener_connection_limit']==0 and all(value==0 for value in sample['framing'].values())
            delta={key:values['after']['tls'][key]-values['before']['tls'][key] for key in values['before']['tls']}
            expected={'attempts':12 if label.endswith('2') else 11,'authenticated':2,'handshake_failed':7,'protocol_mismatch':1,'unmapped_certificate':2 if label.endswith('2') else 1,'in_flight':0,'cancelled':0,'connection_limit':0,'timed_out':0}
            assert delta==expected
            if label=='free-2': assert all(value==0 for value in values['before']['tls'].values())
            deltas.append({'batch':label,'actual_delta':delta})
        row['controlled_batch_deltas']=deltas
    rows.append(row)
after=inventory()
assert before==after
(out/'originals-before.json').write_text(json.dumps(before,indent=2)+'\n')
(out/'originals-after.json').write_text(json.dumps(after,indent=2)+'\n')
result={'result':'PASS','scope':'Same-author read-only raw/provenance method; no original runner/verifier import, no independent security audit, no new runtime cases or PCAP classifier verdict','head':head,'original_files_unchanged':len(before),'base64_artifacts_redecoded':decoded,'runs':rows,'binary_sha256':binary,'software_input_subset':len(inputs)}
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({key:value for key,value in result.items() if key not in ['runs','binary_sha256']}))
