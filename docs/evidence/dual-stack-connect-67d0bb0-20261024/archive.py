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


archive('dual-stack-connect-67d0bb0-20261024', [('/tmp/rustusb-dual-stack-tokio-research-20261024', 'research/tokio'), ('/tmp/rustusb-dual-stack-rfc-research-20261024', 'research/rfc'), ('/tmp/rustusb-dual-stack-nss-research-20261024', 'research/nss'), ('/tmp/rustusb-dual-stack-component-review-20261024', 'review/component'), ('/tmp/rustusb-dual-stack-policy-review-20261024', 'review/policy'), ('/tmp/rustusb-dual-stack-software-first-20261024', 'software'), ('/tmp/rustusb-dual-stack-signals-67d0bb0-20261024', 'local/signals'), ('/tmp/rustusb-dual-stack-backpressure-67d0bb0-20261024', 'local/backpressure'), ('/tmp/rustusb-dual-stack-legacy-cli-first-20261024', 'local/legacy-first-fail'), ('/tmp/rustusb-dual-stack-legacy-cli-second-20261024', 'local/legacy-final'), ('/tmp/rustusb-dual-stack-static-final-20261024', 'static/initial'), ('/tmp/rustusb-dual-stack-framed-static-20261024', 'static/framed'), ('/tmp/rustusb-dual-stack-product-draft-20261024', 'development/drafts-not-execution-snapshots'), ('/tmp/rustusb-dual-stack-old-product-preparation-20261024', 'counterfactual/preparation'), ('/tmp/rustusb-dual-stack-negative-review-20261024', 'review/negatives'), ('/tmp/rustusb-dual-stack-raw-review-67d0bb0-20261024', 'review/raw'), ('/tmp/rustusb-dual-stack-remaining-67d0bb0-20261024', 'qemu/coordinator'), ('/tmp/rustusb-dual-stack-native-ipv4-multi-on-first-20261024', 'qemu/pass-0-rustusb-dual-stack-native-ipv4-multi-on-first-20261024'), ('/tmp/rustusb-dual-stack-native-ipv6-67d0bb0-20261024', 'qemu/pass-1-rustusb-dual-stack-native-ipv6-67d0bb0-20261024'), ('/tmp/rustusb-dual-stack-virtual-ipv4-67d0bb0-20261024', 'qemu/pass-2-rustusb-dual-stack-virtual-ipv4-67d0bb0-20261024'), ('/tmp/rustusb-dual-stack-virtual-ipv6-67d0bb0-20261024', 'qemu/pass-3-rustusb-dual-stack-virtual-ipv6-67d0bb0-20261024'), ('/tmp/rustusb-dual-stack-usbfs-ipv4-67d0bb0-20261024', 'qemu/pass-4-rustusb-dual-stack-usbfs-ipv4-67d0bb0-20261024'), ('/tmp/rustusb-dual-stack-usbfs-ipv6-67d0bb0-20261024', 'qemu/pass-5-rustusb-dual-stack-usbfs-ipv6-67d0bb0-20261024'), ('/tmp/rustusb-dual-stack-native-ipv4-framed-67d0bb0-20261024', 'qemu/pass-6-rustusb-dual-stack-native-ipv4-framed-67d0bb0-20261024'), ('/tmp/rustusb-dual-stack-default-native-ipv4-67d0bb0-20261024', 'qemu/pass-7-rustusb-dual-stack-default-native-ipv4-67d0bb0-20261024'), ('/tmp/rustusb-dual-stack-default-native-ipv6-67d0bb0-20261024', 'qemu/pass-8-rustusb-dual-stack-default-native-ipv6-67d0bb0-20261024'), ('/tmp/rustusb-dual-stack-default-virtual-ipv4-67d0bb0-20261024', 'qemu/pass-9-rustusb-dual-stack-default-virtual-ipv4-67d0bb0-20261024'), ('/tmp/rustusb-dual-stack-default-virtual-ipv6-67d0bb0-20261024', 'qemu/pass-10-rustusb-dual-stack-default-virtual-ipv6-67d0bb0-20261024'), ('/tmp/rustusb-dual-stack-default-usbfs-ipv4-67d0bb0-20261024', 'qemu/pass-11-rustusb-dual-stack-default-usbfs-ipv4-67d0bb0-20261024'), ('/tmp/rustusb-dual-stack-default-usbfs-ipv6-67d0bb0-20261024', 'qemu/pass-12-rustusb-dual-stack-default-usbfs-ipv6-67d0bb0-20261024'), ('/tmp/rustusb-dual-stack-v2-diagnostic-67d0bb0-20261024', 'qemu/pass-13-rustusb-dual-stack-v2-diagnostic-67d0bb0-20261024'), ('/tmp/rustusb-dual-stack-old-native-ipv4-first-5b26bba-20261024', 'qemu/fail-0-rustusb-dual-stack-old-native-ipv4-first-5b26bba-20261024'), ('/tmp/rustusb-dual-stack-old-native-ipv4-second-20261024', 'qemu/fail-1-rustusb-dual-stack-old-native-ipv4-second-20261024'), ('/tmp/rustusb-dual-stack-native-ipv4-first-20261024', 'qemu/fail-2-rustusb-dual-stack-native-ipv4-first-20261024'), ('/tmp/rustusb-dual-stack-native-ipv4-framed-first-20261024', 'qemu/fail-3-rustusb-dual-stack-native-ipv4-framed-first-20261024'), ('/tmp/rustusb-dual-stack-native-ipv4-probe-first-20261024', 'qemu/fail-4-rustusb-dual-stack-native-ipv4-probe-first-20261024'), ('/tmp/rustusb-dual-stack-old-valid-counterfactual-0d116f0-20261024', 'qemu/fail-5-rustusb-dual-stack-old-valid-counterfactual-0d116f0-20261024')], [('/tmp/run-rustusb-dual-stack-software-20261024.py', 'methods/run-rustusb-dual-stack-software-20261024.py'), ('/tmp/review-rustusb-dual-stack-software-20261024.py', 'methods/review-rustusb-dual-stack-software-20261024.py'), ('/tmp/run-rustusb-dual-stack-remaining-20261024.py', 'methods/run-rustusb-dual-stack-remaining-20261024.py'), ('/tmp/review-rustusb-dual-stack-raw-20261024.py', 'methods/review-rustusb-dual-stack-raw-20261024.py'), ('/tmp/review-rustusb-dual-stack-negatives-20261024.py', 'methods/review-rustusb-dual-stack-negatives-20261024.py'), ('/tmp/rustusb-dual-stack-development-observations-20261024.txt', 'methods/rustusb-dual-stack-development-observations-20261024.txt'), ('/tmp/rustusb-dual-stack-generated-first-20261024.sh', 'methods/rustusb-dual-stack-generated-first-20261024.sh')], [], {'kind': 'bounded_hostname_dual_stack_product_validation', 'software_clean': 'f3bf579c79d361c96c9381c74a5a151557a5756a', 'software_executions': 842, 'selected_software_inputs': 198, 'installed_clean': '67d0bb002d910d75bcb77a51abb85ade2c5d51c5', 'successful_paired_runs': 14, 'successful_guest_exits': 28, 'preserved_host_fails': 6, 'valid_old_counterfactual': '0d116f0c85db40b24b8c1c103b9b6ec4b82c3ee2', 'raw_review': {'original_files_unchanged': 4949, 'base64_artifacts_redecoded': 1695}, 'notes': ['First five hostFAILs did not establish both eligible candidates; final corrected oldproduct does. Every original FAIL remains FAIL.', 'Local two CLI cases remain separate from842 Cargo executions; first wrongempty-stdout fixture FAIL preserved without invented waitcode.', 'Component/library/NSS research and synthetic probes are not installed product/USB cases or security audit.', 'Default-policy original runner omits two buildcontrol inputs explicitly; full198 Git mapping is separate.', 'Private keys, probe/build ELF bodies and target directories excluded. Research files retain their own license notices.', 'Owned sparse counterfactual tree removed without force; Git bundle/patch/source snapshots retained.', 'No wire-DNS/VPN/performance/fullRFC8305/hardware/fullG3/fullPhase0-7 claim.']})
