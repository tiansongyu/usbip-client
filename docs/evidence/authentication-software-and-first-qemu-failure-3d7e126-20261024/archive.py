from pathlib import Path
common=Path('/tmp/archive-rustusb-rotating-logs-initial-20261024.py').read_text().split('\narchive(',1)[0]
exec(compile(common,'<exact-copy archive helper>','exec'))
archive('authentication-software-and-first-qemu-failure-3d7e126-20261024',[
 ('/tmp/rustusb-authentication-software-3d7e126-20261024','actual-software'),
 ('/tmp/rustusb-authentication-preflight-first-20261024','initial-fixtures'),
 ('/tmp/rustusb-authentication-preflight-second-20261024','wire-and-coordinator-method'),
 ('/tmp/rustusb-authentication-native-ipv4-first-3d7e126-20261024','actual-first-qemu-failure'),
 ('/tmp/rustusb-authentication-eof-correction-20261024','source-eof-correction'),
], [('/tmp/run-rustusb-authentication-software-3d7e126-20261024.py','run-software.py'),
    ('/tmp/rustusb-authentication-generated-third-20261024.sh','preflight-generated-final-before-3d7.sh')], [], dict(
 result='SoftwarePASS actual clean3d7; first QEMFAIL retained',head='3d7e126d50ac31b4dea7e77c7f4a7819a03e73f4',clean=True,
 software_executions={'default':343,'all_features':434,'vendor':20},total=797,result_rows=47,selected_source_build_inputs=192,
 first_qemu={'backend':'native','family':'ipv4','parent_exit':1,'guest_exits':{'server':0,'client':0},'all_four_guards':True,'selected_harness_inputs':201},
 notes=['Actual new clean software gate, same Rust192 source as prior797; not new unique tests or relabelled old runs. No Rust product changes/new dependencies.',
 'First actual QEMU exits at first free refusal batch: unmapped CLI actualexit1/stdoutempty with retained Rustls peer-closed-without-close_notify IO. Grep assertion omitted this real TLS1.3 mapping-refusal outcome; hostFAIL retained.',
 'Later OpenSSL/USB I/O/credential retirement not executed in this original run. Two guestexit0 do not override hostFAIL.',
 'Initial OpenSSL fixture/signature/Purpose/expiry checks and isolated BusyBox byte generation/constructed coordinator checks are separate method scopes, not product or USB cases.',
 'Static early held-barrier deadlock finding and new INPUT_READY ordering retained; no actual QEMU hang was observed for that preliminary source.',
 'Correction recognizes only fixed TLS close/certificate-alert/handshake categories; constructed unrelated IO rejected. Original evidence unchanged; new clean source/fresh directory required.',
 'Ephemeral private keys never copied into evidence; public DER/inspection and role metadata only. Older all-positive PCAP classifier is not reused for rejected TLS flows.',
 'Raw-v1 startup pin retirement and full six new backend/family runs not yet accepted; hardwareG1/G2/fullG3 and later phases unfinished.']))
