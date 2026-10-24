from pathlib import Path
exec(compile(Path('/tmp/archive-rustusb-rotating-logs-initial-20261024.py').read_text().split('\narchive(',1)[0],'<archive helper>','exec'))
trees=[('/tmp/rustusb-authentication-native-ipv4-fifth-25870c6-20261024','native-ipv4')]
trees += [(f'/tmp/rustusb-authentication-{b}-ipv4-first-25870c6-20261024',f'{b}-ipv4') for b in ['virtual','usbfs']]
trees += [(f'/tmp/rustusb-authentication-{b}-ipv6-first-25870c6-20261024',f'{b}-ipv6') for b in ['native','virtual','usbfs']]
trees += [('/tmp/rustusb-authentication-ipv6-matrix-25870c6-20261024','ipv6-matrix'),('/tmp/rustusb-authentication-six-raw-method-25870c6-20261024','raw-provenance-method')]
archive('authentication-qemu-final-25870c6-20261024',trees,[
 ('/tmp/run-rustusb-authentication-ipv6-25870c6-20261024.py','run-ipv6.py'),
 ('/tmp/review-rustusb-authentication-six-25870c6-20261024.py','review-six.py'),
],[],dict(result='PASS',head='25870c61b1c7dffb9c6ccb4bed8e1e3a6bc2856b',clean=True,
 paired_runs=6,guest_exits_zero=12,credential_epochs=12,actual_io_rounds=12,targeted_negative_child_waits=264,
 selected_harness_inputs=201,selected_software_source_build_inputs=192,
 cli_sha256='9708a50ee836378cd64f8726dae36caded96d4fca39faae215aa30658ef49b48',
 helper_sha256='7c690c62dbc02b7e08b224e2f3cfe773c77bebc6a8e6c10ebd716b2696806322',
 notes=['All six actual Native/virtual/USBFS x IPv4/IPv6 runs use same clean25870c6/default locked allfeatures build/ELFs/201source and all4guards. Same192 source/build inputs as actual clean3d7 software797.',
 'Every pair: two same-guest server credential epochs with same existing helper processes, four actual before/after refusal barriers,44 targeted negative child waits plus two restricted catalog positive controls.',
 'Exact old DER pin removed only after actual original serverwait0/driver baseline/empty grant or channel state; actual new broker PID/start, oldpin refusal andB actual kernel I/O. Cooperative startup retirement, not hot revocation, CRL or crash recovery.',
 'Every epoch actual HID event plus serial35B each direction (virtual adds35B echo). Raw-v1 only. Original client channels/Gone/NULL and full original Native/USBFS baseline verified; old operation/cleanup boundary not changed.',
 'Unmapped/retired may retain missing-close-notify IO or Linux104 reset, not clean EOF or independently traced reset cause. Other PKI/name/trust cases retain fixed fatal-alert/service categories.',
 'Four unauthorized direct IMPORT replies4 and two authorized duplicate replies2 per pair are exact standard8B responses through independent authenticated OpenSSL. Requests/bytes/publiccerts/configs/real waits saved; no private key bytes.',
 'Fast negative CLI/OpenSSL children have fixed setpriv/config/source and actual waits, without assumed per-executed-ELF samples. Actual long-lived broker/helper proc/ELFs retained. Virtual server has no helper; existing client helper is matched.',
 'Same author separately wrote7042 raw/provenance predicates, decoded2342 guest artifacts and verified3844 originals unchanged. Not independent implementation audit or extra software/USB testcases.',
 'Raw PCAP retained without all-positive mixed-handshake classifier verdict. Earlier four hostFAILs remain separately immutable and are not rejudged.',
 'HardwareG1/G2, fullG3, framed-v2-specific negative coverage, sustained exhaustion, CRL/OCSP/active-stream expiry/hot reload and later phases remain unfinished.']))
