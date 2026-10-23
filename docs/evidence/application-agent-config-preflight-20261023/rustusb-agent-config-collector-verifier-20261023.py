def verify_collected_configuration(evidence, family):
    """Exact observed public configuration/DER and permission metadata only."""
    observations=[]
    def get(role,name):return (evidence/f'{role}-artifacts'/name).read_bytes()
    def require(ok,description):
        if not ok:raise RuntimeError('actual configuration evidence: '+description)
    public={name:(evidence/f'{name}-public.der').read_bytes() for name in ['ca','server','client']}
    pin=hashlib.sha256(public['client']).hexdigest()
    keys=json.loads(get('server','keys.json'))
    for role in ['server','client']:
        profiles=[{'id':'server','backend':'native','config':'/etc/rustusb/server.json'}] if role=='server' else [{'id':profile,'backend':'client','config':f'/etc/rustusb/client-{profile}.json'} for profile in ['hid','serial']]
        manifest={'schema_version':1,'broker_uid':2000,'helper_socket':'/run/rustusb/control.sock','control_directory':'/run/agent','data_directory':'/var/lib/rustusb/agent','profiles':profiles,'operators':[{'uid':1000,'profiles':[p['id'] for p in profiles],'actions':['read','start','stop','discover','audit']}]}
        expected={'agent.json':manifest}
        if role=='server':
            expected['server.json']={'schema_version':1,'broker_uid':2000,'listen':'192.0.2.1:3241' if family=='ipv4' else '[fd42:7275:7374::1]:3241','devices':keys,'principals':[{'id':'qemu-client','pins':[pin],'devices':keys}],'ca_certificates':['/etc/rustusb/ca.der'],'certificate_chain':['/etc/rustusb/server.der'],'private_key':'/etc/rustusb/server-key.der'}
        else:
            for profile,key in zip(['hid','serial'],keys,strict=True):
                expected[f'client-{profile}.json']={'schema_version':1,'broker_uid':2000,'server_id':'qemu-server','endpoint':{'host':'192.0.2.1' if family=='ipv4' else 'fd42:7275:7374::1','port':3241},'server_name':'rustusb-qemu.test','device':key,'ca_certificates':['/etc/rustusb/ca.der'],'certificate_chain':['/etc/rustusb/client.der'],'private_key':'/etc/rustusb/client-key.der'}
        directories={'/','/etc','/etc/rustusb','/run','/run/agent','/var','/var/lib','/var/lib/rustusb','/var/lib/rustusb/agent'}
        public_paths={'/etc/rustusb/'+name for name in expected}|{'/etc/rustusb/'+name+'.der' for name in public}
        private='/etc/rustusb/'+role+'-key.der'
        for epoch in [1,2]:
            prefix=f'actual-config-{epoch}-'
            for name,value in expected.items():
                require(json.loads(get(role,prefix+name))==value,f'{role} epoch{epoch} actual {name} exact semantic fields')
                require(get(role,prefix+name)==get(role,'actual-config-1-'+name),f'{role} {name} bytes unchanged over restart')
            for name,der in public.items():
                require(get(role,prefix+name+'.der')==der,f'{role} epoch{epoch} actual {name} public DER equals retained host DER')
            metadata=json.loads(get(role,f'config-metadata-{epoch}.json'));by={row['path']:row for row in metadata}
            require(len(metadata)==len(by) and set(by)==directories|public_paths|{private},f'{role} epoch{epoch} exact fixed permission coverage')
            for path,row in by.items():
                require(row['canonical']==path and row['inode']>0 and row['device']>=0,f'{role} exact nonsymlink path {path}')
                mode=int(row['mode'],8)
                if path in directories:
                    broker=path in ['/run/agent','/var/lib/rustusb/agent']
                    require(row['kind']=='directory' and row['captured_file'] is None and row['uid']==(2000 if broker else 0) and (mode==(0o711 if path=='/run/agent' else 0o700) if broker else mode&0o022==0),f'{role} actual protected ancestor {path}')
                elif path==private:
                    require(row['kind']=='private_key_metadata_only' and row['uid']==row['gid']==2000 and mode==0o600 and row['links']==1 and row['captured_file'] is None and row['bytes']>0,f'{role} own key metadata only')
                else:
                    capture=prefix+path.rsplit('/',1)[1]
                    require(row['kind']=='public_file' and row['uid']==0 and mode==0o644 and row['links']==1 and row['captured_file']==capture and row['bytes']==len(get(role,capture)),f'{role} actual root-controlled public file {path}')
            require(not any(p.name.endswith('-key.der') for p in (evidence/f'{role}-artifacts').iterdir()),f'{role} no key bytes in artifacts')
            observations.append({'role':role,'epoch':epoch,'fixed_metadata_entries':len(metadata),'json_files':sorted(expected),'public_der_sha256':{name:hashlib.sha256(der).hexdigest() for name,der in public.items()},'private_key_bytes_captured':False})
    return observations
