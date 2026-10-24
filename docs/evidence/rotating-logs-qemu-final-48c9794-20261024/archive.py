from pathlib import Path
import json, hashlib

repo = Path('/home/ubuntu/github/usbip-client')
def archive(name, trees, singles, listed, scope):
    out = repo / 'docs/evidence' / name
    out.mkdir(exist_ok=False)
    records = []
    seen = set()
    def copy(source, dest, expected=None):
        source = Path(source)
        assert source.is_file() and not source.is_symlink(), source
        b = source.read_bytes()
        digest = hashlib.sha256(b).hexdigest()
        if expected:
            assert len(b) == expected['bytes'] and digest == expected['sha256'], source
        assert dest not in seen, dest
        seen.add(dest)
        p = out / dest
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)
        assert p.read_bytes() == b
        records.append(dict(path=dest, original=str(source), bytes=len(b), sha256=digest))
    for root, prefix in trees:
        root = Path(root)
        for p in sorted(root.rglob('*')):
            if p.is_file():
                copy(p, prefix + '/' + str(p.relative_to(root)))
    for source, dest in singles:
        copy(source, dest)
    for handoff, key, prefix in listed:
        for row in json.loads(Path(handoff).read_text())[key]:
            copy(row['path'], prefix + '/' + str(Path(row['path']).relative_to('/tmp')), row)
    copy(__file__, 'archive.py')
    b = (json.dumps(scope, indent=2) + '\n').encode()
    (out/'scope.json').write_bytes(b)
    records.append(dict(path='scope.json', original='generated archive scope', bytes=len(b), sha256=hashlib.sha256(b).hexdigest()))
    (out/'manifest.json').write_text(json.dumps(dict(schema_version=1, files=records), indent=2)+'\n')
    print(name, 'files',len(records)+1,'bytes',sum(r['bytes'] for r in records))


archive('rotating-logs-qemu-final-48c9794-20261024', [
 ('/tmp/rustusb-process-logging-qemu-final-coordinator-20261024','actual-run'),
 ('/tmp/rustusb-process-logging-verifier-peer-close-revision-20261024','verifier-correction'),
 ('/tmp/rustusb-process-logging-qemu-final-raw-method-20261024','method/lifecycle'),
 ('/tmp/rustusb-process-logging-qemu-final-log-review-20261024','method/logging'),
], [(str(Path('/tmp')/n),'review/'+n) for n in [
 'run-rustusb-process-logging-qemu-final-20261024.py',
 'review-rustusb-process-logging-final-20261024.py',
 'rustusb-process-logging-verifier-peer-close-source-cross-review-20261024.json',
]], [], dict(result='PASS',commit='48c9794d4a5f1e8116e504b7396937e808e5b32f',clean=True,
 paired_runs=1,epochs_in_same_run=3,guest_exit_zero=2,host_exit_code=0,
 selected_harness_inputs=189,matching_software_inputs=177,actual_QEMU_original_artifacts=745,
 cli_sha256='6cc828f3769d1ebd4c2e7b566707b7788f107151bc21155843c315a818b8b474',
 helper_sha256='bb0ea543bf6cc1af840c4e793b26906a6f9062b534c00b0c6549dce3fce834a1',
 notes=['Default locked all-features build from this exact clean head and all source/binary/head/clean guards passed; actual ELF matches software723.',
 'Three actual key pairs and105bytes per FTDI direction, original server12/client6 full root terminals, full original driver/configuration baseline, clientNULL/generation disappearance and six fresh capabilities/generations verified.',
 'Same independent gate PID/start/FD/inode/nonce remains held through actual TERM and complete original cleanup, writerEX and two readerSH actual blocked syscalls, realBusy, then same nonce release/gatewait0 and faultagentwait1.',
 'Actual faultserverStopped accepted=written281 and dropped290, unhealthy but no storage/oversize/projection/overflow error. Other7 loggerowners healthy/drained. Originalfirst dropped289 not reused.',
 'Seven actual pages76filtered records match authorization and successful committed audit sequence/currentcommittinginstance, actual UID/permissions/4096x4 bounds and sixagent/twohelper executable receipts.',
 'Four normal/freshserverincoming State/Protocol, two faultclient Io/Io and other original terminal classifications retained. Not cleanEOF or traced on-wire reset causality.',
 'Host3081 and independent logging3183/lifecycle method diagnostic assertions are not software execution counts or extra USB cases.',
 '745 original artifacts and529 base64 blocks verified unchanged. Reviewers authored implementation/harness portions: independent raw methods are not independent implementation audits.',
 'The first44b1 actualFAIL and source-only fixture/parser failures remain separate unchanged archives. Revised verifier was not used to relabel it.',
 'This one scenario does not relabel or combine prior23 atclean1582, nor complete hardwareG1/G2, kernel-active/permanentblocking, allsecurity or Phase3.']))

