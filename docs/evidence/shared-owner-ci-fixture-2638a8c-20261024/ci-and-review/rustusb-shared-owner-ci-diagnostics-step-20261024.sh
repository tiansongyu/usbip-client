python3 - <<'PY'
import json
from pathlib import Path
fields = ['case', 'result', 'mutual_tls', 'authenticated_imports', 'pending_in_per_import', 'blocked', 'exit', 'elapsed_seconds', 'data_connection_closure', 'backend_cleanup_snapshot_observed']
directories = sorted(p for p in Path('/tmp').glob('rustusb-secure-foreground-tests-*') if p.is_dir())
print(json.dumps({'secure_foreground_evidence_directories': len(directories)}))
for directory in directories:
    summary = directory / 'summary.json'
    if summary.is_file():
        value = json.loads(summary.read_text())
        print(json.dumps({'scope': value.get('scope'), 'result': value.get('result'), 'failed_case': value.get('failed_case'), 'error': value.get('error'), 'cases': [{key: case[key] for key in fields if key in case} for case in value.get('cases', value.get('completed', []))]}))
    for observation in sorted(directory.glob('*/blocked-writer-observation.json')):
        print(json.dumps({'case': observation.parent.name, 'blocked_writer_observation': json.loads(observation.read_text())}))
PY
