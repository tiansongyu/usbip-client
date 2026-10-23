"""Extracted read-only journal capture on temporary fixtures; no guest/helper/USB execution."""
from pathlib import Path
import hashlib,json,os,shlex,subprocess,tempfile
assert os.geteuid()!=0
source=Path('/home/ubuntu/github/usbip-client/scripts/qemu/guest-secure-init.sh')
text=source.read_text();function=text[text.index('failure_journal() {'):text.index('\ncleanup() {')]
report={'scope':__doc__,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'cases':{}}
with tempfile.TemporaryDirectory(prefix='rustusb-journal-failure-static-') as temp:
    root=Path(temp);out=root/'out';out.mkdir();inputs=root/'inputs';inputs.mkdir()
    paths={name:inputs/name for name in ['copied','absent','unreadable','over_limit','symlink','directory']}
    data=b'{"schema_version":1,"records":[],"exports":[]}\n'
    paths['copied'].write_bytes(data);paths['unreadable'].write_bytes(data);paths['unreadable'].chmod(0)
    paths['over_limit'].write_bytes(b'x'*(1048576+22));paths['symlink'].symlink_to(paths['copied']);paths['directory'].mkdir()
    original={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in paths.items() if name in ['copied','over_limit']}
    function=function.replace('/tmp/failure-$journal_label',str(out)+'/failure-$journal_label')
    script=root/'fixture.sh';script.write_text('#!/bin/sh\nset +e\nstatus=7\n'+function+'\n'+''.join('failure_journal '+shlex.quote(name)+' '+shlex.quote(str(path))+'\n' for name,path in paths.items())+'\n[ "$status" = 7 ]\n')
    process=subprocess.run(['/bin/busybox','sh',str(script)],capture_output=True,text=True)
    assert process.returncode==0,(process.stdout,process.stderr)
    expected={'copied':'copied','absent':'absent','unreadable':'unreadable','over_limit':'over_limit','symlink':'nonregular','directory':'nonregular'}
    for name,status in expected.items():
        result=(out/f'failure-{name}.status.txt').read_text()
        assert f'status={status}\n' in result,result
        assert not (out/f'failure-{name}.capture').exists()
        report['cases'][name]={'expected_status':status,'actual_status_text':result}
    assert (out/'failure-copied.json').read_bytes()==data
    assert (out/'failure-over_limit.prefix.bin').stat().st_size==1048576
    assert not (out/'failure-over_limit.json').exists()
    assert not (out/'failure-unreadable.json').exists()
    assert all(hashlib.sha256(paths[name].read_bytes()).hexdigest()==sha for name,sha in original.items())
    report['source_contents_unchanged']=True;report['original_failure_status_preserved']=True;report['oversize_prefix_exactly_1MiB']=True
    paths['unreadable'].chmod(0o600)
Path('/tmp/rustusb-secure-journal-failure-fixture-20261023.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'fixture_cases':len(report['cases']),'source_contents_unchanged':True,'original_failure_status_preserved':True,'not_QEMU':True}))
