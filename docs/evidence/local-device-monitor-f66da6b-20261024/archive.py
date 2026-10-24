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


archive('local-device-monitor-f66da6b-20261024', [('/tmp/rustusb-hotplug-software-first-20261024', 'software/first-failed'), ('/tmp/rustusb-hotplug-software-second-20261024', 'software/second-pre-signal-fix'), ('/tmp/rustusb-hotplug-software-third-20261024', 'software/final'), ('/tmp/rustusb-hotplug-missing-address-negative-20261024', 'development/missing-address-negative'), ('/tmp/rustusb-hotplug-qemu-fixture-check-20261024', 'fixture-query-checks'), ('/tmp/rustusb-hotplug-qemu-default-first-20261024', 'qemu/first-timeout'), ('/tmp/rustusb-hotplug-qemu-default-second-20261024', 'qemu/second-panic'), ('/tmp/rustusb-hotplug-qemu-default-third-20261024', 'qemu/final-default'), ('/tmp/rustusb-hotplug-qemu-all-first-20261024', 'qemu/final-all'), ('/tmp/rustusb-hotplug-readonly-review-20261024', 'readonly-review'), ('/tmp/rustusb-hotplug-regressions-f66da6b-20261024', 'original-local-regressions'), ('/tmp/rustusb-hotplug-final-local-method-20261024', 'final-local-method'), ('/tmp/rustusb-hotplug-environment-20261024', 'post-run-environment'), ('/tmp/rustusb-hotplug-public-negative-inputs-20261024', 'public-negative-list'), ('/tmp/rustusb-hotplug-development-first-20261024', 'development/first'), ('/tmp/rustusb-hotplug-development-second-20261024', 'development/second'), ('/tmp/rustusb-hotplug-development-third-20261024', 'development/third'), ('/tmp/rustusb-hotplug-development-fourth-20261024', 'development/fourth'), ('/tmp/rustusb-hotplug-development-fifth-20261024', 'development/fifth'), ('/tmp/rustusb-hotplug-development-sixth-20261024', 'development/sixth')], [('/tmp/run-rustusb-hotplug-development-20261024.py', 'methods/run-rustusb-hotplug-development-20261024.py'), ('/tmp/run-rustusb-hotplug-development-second-20261024.py', 'methods/run-rustusb-hotplug-development-second-20261024.py'), ('/tmp/run-rustusb-hotplug-development-third-20261024.py', 'methods/run-rustusb-hotplug-development-third-20261024.py'), ('/tmp/run-rustusb-hotplug-development-fourth-20261024.py', 'methods/run-rustusb-hotplug-development-fourth-20261024.py'), ('/tmp/run-rustusb-hotplug-development-fifth-20261024.py', 'methods/run-rustusb-hotplug-development-fifth-20261024.py'), ('/tmp/run-rustusb-hotplug-development-sixth-20261024.py', 'methods/run-rustusb-hotplug-development-sixth-20261024.py'), ('/tmp/run-rustusb-hotplug-software-20261024.py', 'methods/run-rustusb-hotplug-software-20261024.py'), ('/tmp/run-rustusb-hotplug-software-second-20261024.py', 'methods/run-rustusb-hotplug-software-second-20261024.py'), ('/tmp/run-rustusb-hotplug-software-third-20261024.py', 'methods/run-rustusb-hotplug-software-third-20261024.py'), ('/tmp/review-rustusb-hotplug-software-20261024.py', 'methods/review-rustusb-hotplug-software-20261024.py'), ('/tmp/review-rustusb-hotplug-software-third-20261024.py', 'methods/review-rustusb-hotplug-software-third-20261024.py'), ('/tmp/review-rustusb-hotplug-evidence-20261024.py', 'methods/review-rustusb-hotplug-evidence-20261024.py'), ('/tmp/run-rustusb-hotplug-regressions-20261024.py', 'methods/run-rustusb-hotplug-regressions-20261024.py'), ('/tmp/run-rustusb-hotplug-final-local-method-20261024.py', 'methods/run-rustusb-hotplug-final-local-method-20261024.py'), ('/tmp/check-rustusb-hotplug-qemu-query-20261024.py', 'methods/check-rustusb-hotplug-qemu-query-20261024.py'), ('/tmp/rustusb-hotplug-udev-research-20261024/handoff.json', 'methods/handoff.json'), ('/tmp/rustusb-hotplug-netlink-research-20261024/research-files.json', 'methods/research-files.json'), ('/tmp/rustusb-hotplug-local-all-third-20261024/archive-files.json', 'local-manifests/all-third.json'), ('/tmp/rustusb-hotplug-local-default-first-20261024/archive-files.json', 'local-manifests/default-first.json'), ('/tmp/rustusb-hotplug-local-all-final-20261024/archive-files.json', 'local-manifests/all-final.json'), ('/tmp/rustusb-hotplug-local-default-final-20261024/archive-files.json', 'local-manifests/default-final.json')], [('/tmp/rustusb-hotplug-udev-research-20261024/handoff.json', 'files', 'research/udev'), ('/tmp/rustusb-hotplug-netlink-research-20261024/research-files.json', 'files', 'research/netlink'), ('/tmp/rustusb-hotplug-public-negative-inputs-20261024/files.json', 'files', 'local/old-negative-public'), ('/tmp/rustusb-hotplug-local-all-third-20261024/archive-files.json', 'files', 'local/all-third'), ('/tmp/rustusb-hotplug-local-default-first-20261024/archive-files.json', 'files', 'local/default-first'), ('/tmp/rustusb-hotplug-local-all-final-20261024/archive-files.json', 'files', 'local/all-final'), ('/tmp/rustusb-hotplug-local-default-final-20261024/archive-files.json', 'files', 'local/default-final')], {'kind': 'scoped_local_device_observer_research_software_process_and_guest_evidence', 'software_commit': 'c0346d61d01b6a5f1605f55a6591d87fedd0d615', 'software_executions': 872, 'software_inputs': 206, 'qemu_commit': 'f66da6bd64b960aa54a2878ffab0bbe232e8c85d', 'successful_single_guest_runs': 2, 'final_local_process_cases': 6, 'notes': ['No ELF, target/cache or private host device output bodies copied', 'Use explicit research/local public lists, not recursive private roots', 'Earlier duplicate method process successes retained without double-counting final six', 'Original failures stay failures; first old child wait is not reconstructed', 'Development first source copy is post-run, not an invented before snapshot', 'Research GPL/LGPL material is reference-only, not imported into MIT product', 'Same-author raw review is not an independent security audit', 'Not paired USB/IP forwarding, hardware, induced ENOBUFS or full Phase0-7 completion']})
