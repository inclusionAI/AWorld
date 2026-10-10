"""Apply an independently reviewed list of disposable terminal-run copies."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from aworld.self_evolve import lifecycle as lc

ROOT = Path('/Users/wuman/Documents/workspace/aworld/.aworld/self_evolve')
allowlist = Path(sys.argv[1]).resolve()
journal_path = allowlist.with_suffix('.journal.json')
assert not journal_path.exists()
paths = [Path(p) for p in json.loads(allowlist.read_text())['paths']]
assert paths and len(paths) == len(set(paths))
runs = set()
for p in paths:
    assert p.is_relative_to(ROOT) and p.name in {'workspace', 'workspace_seed'}, p
    assert p.is_dir() and not p.is_symlink() and p.resolve() == p, p
    run = ROOT / p.relative_to(ROOT).parts[0]
    if p.name == 'workspace':
        assert (p.parent / 'execution_request.json').is_file(), p
    else:
        assert (p.parent / 'workspace_manifest.json').is_file(), p
    runs.add(run)
for run in runs:
    report = json.loads((run / 'report.json').read_text())
    assert report['target']['target_id'] == 'agent-browser'
    assert report['target']['target_type'] == 'skill'
    assert report['status'] in {'rejected', 'failed', 'cancelled'}
    assert json.loads((run / 'run.json').read_text())['status'] == report['status']
    assert not lc._has_live_run_lease(run)
    assert lc._run_measurement_resume_checkpoint(run) is None
    assert not lc._run_has_resumable_authoritative_measurement_work(run)
    assert not lc._run_has_pending_measurement_work(run)

def fingerprint(p):
    if p.is_symlink():
        return {'symlink': os.readlink(p)}
    with p.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    return {'sha256': digest, 'mode': p.stat().st_mode}

preserved = set()
path_set = set(paths)
for run in runs:
    for parent, dirs, files in os.walk(run, followlinks=False):
        dirs[:] = [d for d in dirs if Path(parent) / d not in path_set]
        preserved.update(Path(parent) / name for name in files)
preserved.add(ROOT.parents[1] / 'aworld-skills/agent-browser/SKILL.md')
before = {str(p): fingerprint(p) for p in sorted(preserved)}
journal = {'status': 'prepared', 'created_at': time.time(), 'paths': [str(p) for p in paths], 'preserved_files': before, 'disk_free_before': shutil.disk_usage(ROOT).free, 'completed': []}
journal_path.write_text(json.dumps(journal, indent=2) + '\n')
print(json.dumps({'status': 'prepared', 'count': len(paths), 'preserved_files': len(preserved), 'free': journal['disk_free_before']}), flush=True)
for p in paths:
    shutil.rmtree(p)
    journal['completed'].append(str(p))
    if len(journal['completed']) % 25 == 0:
        print(json.dumps({'completed': len(journal['completed']), 'free': shutil.disk_usage(ROOT).free}), flush=True)
assert before == {str(p): fingerprint(p) for p in sorted(preserved)}
journal.update(status='completed', preserved_files_unchanged=True, disk_free_after=shutil.disk_usage(ROOT).free)
journal_path.write_text(json.dumps(journal, indent=2) + '\n')
print(json.dumps({'status': 'completed', 'count': len(paths), 'preserved_files': len(preserved), 'free': journal['disk_free_after']}), flush=True)
