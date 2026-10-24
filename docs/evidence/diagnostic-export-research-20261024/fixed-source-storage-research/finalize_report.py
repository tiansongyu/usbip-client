#!/usr/bin/env python3
"""Recheck consulted source bytes and summarize this research without executing RustUSB."""
from pathlib import Path
import hashlib,json
p=Path(__file__).resolve().parent
prov=json.loads((p/'provenance.json').read_text())
def item(path):
 b=path.read_bytes();return {'path':str(path),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
changed=[x['path'] for x in prov['repository_files'] if item(Path(x['path']))['sha256']!=x['sha256']]
archive_ok=all(x['archive_matches_lock'] and all(f['matches_locked_archive'] for f in x['files']) for x in prov['packages'])
report={'schema_version':1,'result':'RESEARCH_COMPLETE_PROPOSED_ONLY','source_baseline':'Parent-supplied clean46cd950; no Git command executed to assert current HEAD.','repository_before_after_bytes_equal':not changed,'changed_consulted_repository_files':changed,'consulted_repository_files':len(prov['repository_files']),'fixed_packages':[{k:x[k] for k in ['name','version','license','archive_matches_lock']} for x in prov['packages']],'locked_archive_source_comparisons':sum(len(x['files']) for x in prov['packages']),'all_locked_source_comparisons_passed':archive_ok,'std_exact_commit':prov['std']['ref']['resolved_object'],'recommendation':'Closed typed4MiB JSON, caller-local protected destination, fresh0600 partial+NOREPLACE+fsync, distinct logical incomplete vs filesystem partial/uncertain, actual collector runtime blocking-pool join and explicit child reaping.','root_selected_contract':['New nonempty diagnostics library; default Native/helper and optional one-profile authorized service data.','4MiB bounded JSON; logical incomplete can be a valid complete=false published file with exit1.','Filesystem failure retains partial; post-rename error may already have published the final entry.'],'status_boundaries':prov['boundaries'],'author_scope':'Storage/serialization source research; prior Native/logging contributor, not independent full-product or security acceptance.','unresolved':['Final closed schema, per-section bounds and authorized projection are for root/sibling design.','Existing doctor child cleanup is only a bounded observation with prose unconfirmed detail; no total-child-join proof may be inferred.','Trusted same-UID/root interference and hard-blocking filesystem shutdown are not revocable by these APIs.','No filesystem/application tests executed; proposed tests are not results.'],'artifacts':[]}
for f in sorted(p.rglob('*')):
 if f.is_file() and f.name not in ['review.json','handoff.json']:
  report['artifacts'].append(item(f))
(p/'review.json').write_text(json.dumps(report,indent=2)+'\n')
assert not changed and archive_ok
handoff={'schema_version':1,'result':report['result'],'frozen':True,'files':[item(p/n) for n in ['recommendation.md','provenance.json','review.json','collect_provenance.py','finalize_report.py']],'boundary':'Read-only research; no repo/product edits, Git, Cargo, CLI, application tests, QEMU or USB operations.'}
(p/'handoff.json').write_text(json.dumps(handoff,indent=2)+'\n')
print(json.dumps(handoff,indent=2))
