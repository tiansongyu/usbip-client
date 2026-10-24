from pathlib import Path
import base64, hashlib, json, re, subprocess

root = Path('/home/ubuntu/github/usbip-client')
out = Path('/tmp/rustusb-authentication-admission-review-final-d619b22-20261024')
out.mkdir(exist_ok=False)
head = 'd619b22bb1db0983e7dad197e2bfbc79d7dd924a'
software = Path('/tmp/rustusb-authentication-admission-software-first-47fcb75-20261024')
build = json.loads((software/'result.json').read_text())
inputs = {row['path']: {'bytes': row['bytes'], 'sha256': row['sha256']} for row in json.loads((software/'inputs-before.json').read_text())}
binary = {Path(row['path']).name: row['sha256'] for row in build['artifacts']}
directories = [Path('/tmp/rustusb-authentication-admission-native-ipv4-second-d619b22-20261024')]
directories += [Path(f'/tmp/rustusb-authentication-admission-{backend}-{family}-d619b22-20261024') for backend in ['native','virtual','usbfs'] for family in ['ipv4','ipv6'] if (backend,family)!=('native','ipv4')]
directories += [Path(f'/tmp/rustusb-authentication-admission-default-{backend}-{family}-d619b22-20261024') for backend in ['native','virtual','usbfs'] for family in ['ipv4','ipv6']]
directories.append(Path('/tmp/rustusb-authentication-admission-v2-diagnostic-d619b22-20261024'))

def digest(path):
    data = path.read_bytes()
    return {'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest()}

def inventory():
    return {str(p):digest(p) for directory in directories for p in sorted(directory.rglob('*')) if p.is_file()}

for name,metadata in inputs.items():
    raw=subprocess.check_output(['git','show',f'{head}:{name}'],cwd=root)
    assert {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}==metadata
originals_before = inventory()
rows = []
decoded = 0
for directory in directories:
    s = json.loads((directory/'summary.json').read_text())
    assert s['result']=='PASS' and s['guest_exit_codes']=={'client':0,'server':0}
    assert s['head']==s['head_after']==head
    assert s['git_status_before']==s['git_status_after']==''
    assert all(s[name] is True for name in ['source_guard','binary_guard','head_guard','clean_guard'])
    assert s['binary_sha256']==binary and s['build_skipped'] is False
    missing=set(inputs)-set(s['source_inputs'])
    assert missing==({'rust-toolchain.toml','scripts/check.sh'} if 'default-' in directory.name else set())
    assert all(s['source_inputs'][name]==value for name,value in inputs.items() if name not in missing)
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
    row={'directory':str(directory),'backend':s['backend'],'family':s['family'],'source_inputs':len(s['source_inputs']),'software_input_subset':len(inputs)-len(missing),'software_inputs_not_selected_by_original_runner':sorted(missing),'guest_exits':s['guest_exit_codes']}
    if 'default-' not in directory.name and 'v2-diagnostic' not in directory.name:
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
        probes=[]
        for epoch in [1,2]:
            samples={stage:json.loads((directory/f'server-artifacts/rate-admission-{stage}-{epoch}.json').read_bytes()) for stage in ['before','limited','after-io','drained','recovered']}
            assert all(x['consistent'] and not x['saturated'] and x['policy']=={'burst':32,'interval_millis':500} for x in samples.values())
            before,drained,recovered=(samples[x] for x in ['before','drained','recovered'])
            assert before['limited']==0 < samples['limited']['limited'] < samples['after-io']['limited'] <= drained['limited']==recovered['limited']
            assert recovered['admitted']==drained['admitted']+1
            tls={stage:json.loads((directory/f'server-artifacts/rate-tls-{stage}-{epoch}.json').read_bytes()) for stage in ['before','drained','recovered']}
            assert all(x['tls']['in_flight']==0 for x in tls.values())
            attempts=0
            for worker in range(1,9):
                p=directory/f'client-artifacts/rate-worker-{epoch}-{worker}'
                assert Path(str(p)+'.wait').read_text().strip()=='0'
                lines=Path(str(p)+'.timeline').read_text().splitlines()
                rows2=[list(map(int,line.split())) for line in lines]
                assert [r[0] for r in rows2]==list(range(1,len(rows2)+1))
                assert all(len(r)==4 and r[3] in [0,1] for r in rows2)
                attempts+=len(rows2)
            admitted=drained['admitted']-before['admitted'];refused=drained['limited']-before['limited']
            quota=tls['drained']['tls']['connection_limit']-tls['before']['tls']['connection_limit']
            outer=tls['drained']['listener_connection_limit']-tls['before']['listener_connection_limit']
            assert attempts==admitted+refused+quota+outer
            assert tls['drained']['tls']['attempts']-tls['before']['tls']['attempts']==admitted+quota
            assert tls['drained']['tls']['unmapped_certificate']-tls['before']['tls']['unmapped_certificate']==admitted
            assert tls['recovered']['tls']['authenticated']==tls['drained']['tls']['authenticated']+1
            probes.append(dict(epoch=epoch,actual_client_wait_rows=attempts,admitted=admitted,limited=refused,tls_quota=quota,outer_quota=outer))
        row['probe_totals']=probes
    rows.append(row)
after=inventory()
assert originals_before==after
(out/'originals-before.json').write_text(json.dumps(originals_before,indent=2)+'\n')
(out/'originals-after.json').write_text(json.dumps(after,indent=2)+'\n')
result={'result':'PASS','scope':'Same-author read-only raw/provenance method; no original runner/verifier import, no independent security audit, no new runtime cases or PCAP classifier verdict','head':head,'original_files_unchanged':len(originals_before),'base64_artifacts_redecoded':decoded,'runs':rows,'binary_sha256':binary,'software_input_subset':len(inputs)}
(out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({key:value for key,value in result.items() if key not in ['runs','binary_sha256']}))
