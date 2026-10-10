"""Reclaim only independently reviewed, disposable self-evolve copies."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time

ROOT = Path('/Users/wuman/Documents/workspace/aworld/.aworld/self_evolve')
JOURNAL = ROOT.parents[1] / 'tmp/self-evolve-reviewed-reclaim-20260918.json'
IDS = '''0173f16ddf874ffbb1c393131f67a362 02a8f429913d455a9dfd20f1cd220512 070c0a3dd10e477795517378c6e9f071 0a0b6f96bae94e0dbf56a982ee436336 132221eb088247fe81a2e1eca8d7b6db 17dd939e6bb142b492c1f66a4de64b05 1bf537c1a5854ca791c3bde786589502 215604fdda154fec8dffaec491e58de8 2ae1d86b2bd14dfbbf19c322575567be 2dd4ef42a1fc417596e993eaad92c40e 35e192a7aa3d42d6a6800363fcdfe688 3749d6cafddb48e0945a5835bda0c8a5 3a146c9fefa64a39a60e7d5698cf94f9 3c32cb0197d241f4804816f5fa465e56 43f59dbfe07e494e970b515ca4dbbbc8 4b1154449e454805aa554f13bf7cbd6d 4d470892f0894784b01ba46ea4e8956e 54c94f38ee38431a8d143797d24e7911 5640a590c50741febf4599d08a82f273 5d08c03d0e8548b3a0f42444c5c6abc8 5e16ddca04de49d48bc3c64e59c115ff 6b8c927051224d6dafcd3c10269fe73b 752c63763c4d49ef84ec08b4062a2bc8 7f5d0f9621064361930942ba7380c9b7 7fdbfb79924b4c758e143aaf3ad12485 848a7f6bc28c49639f6a78389e40dc10 86cdf0cee9f54284a5b25f43418875bb 8a4953b386214ee0aa9f302536587950 8d9cbeedd93a4f8885b116ed2763d028 9241865a38a7457c854af46f47366314 9b091ab255304b3291722330f2ca9939 a0e819afbcb643408f5e6d0ae18de820 a1464b76afc641d5852db27f4c31bc72 a419cada28b744ec88e74299a921b4ef aa8a0c96fff14094bea16680428b82ab ac9dbfd81cdd43de9b7b7043162268ce b1d491f73fc74899bab0b74d78f5e804 b2b24033204c46a38ded87ddd28bf0dc b4158e6d507f468b8e1e24793052b832 c770cd419ee540e6bcb9c619bc64897b c8ab0795b7fe4938854890aa46873f16 c8fb10f912f347fabd329362c3b86d02 cd0d316250fc4acdaac4bed7b37386da cf709b78a1454978b71e3885e711b1e0 d8c9e68429a94c56acffec9b6cf4c52a dba1d282dbcd4552a494e6adf3de25cd dc12b61652ac4c80be65150b53649de5 de73075a8f2c4f739e3d00b58e0d1f6b e42efc9888e340a78cdf689faf9ce6b6 eb08317a99c64f9aabc8a1d9e7ad538c'''.split()
SEEDS = {
 'campaign-d069eb69598d2b9e784e-cycle-002': ('73b4c84d7eb32654', '0e78019c01d7817b 28b48876c50e2676 3424a7a7b3951b52 5e16d200655c221d 89c4d3e98d062620'),
 'campaign-ecb82b219304c5a03ffa-cycle-001': ('be4eb91589754be3', '57de1b923872d9c2 880fb3761fe5a5f7 df0b70703a461c3c'),
 'campaign-ecb82b219304c5a03ffa-cycle-002': ('be4eb91589754be3', '57de1b923872d9c2 a0e2905c24dd92d5 df0b70703a461c3c'),
 'campaign-ecb82b219304c5a03ffa-cycle-003': ('be4eb91589754be3', '1547809545e0ded6 57de1b923872d9c2 df0b70703a461c3c'),
}

def read(p):
    return json.loads(p.read_text())

def fingerprint(p):
    if p.is_symlink():
        return {'symlink': os.readlink(p)}
    with p.open('rb') as f:
        h = hashlib.file_digest(f, 'sha256').hexdigest()
    return {'sha256': h, 'mode': p.stat().st_mode}

def native_paths(run):
    for parent, dirs, files in os.walk(run, followlinks=False):
        dirs[:] = [d for d in dirs if d not in {'workspace', 'workspace_seed'}]
        for name in files:
            yield Path(parent) / name

def main():
    from aworld.self_evolve import lifecycle as lc
    assert not JOURNAL.exists(), 'journal already exists; do not repeat a completed batch'
    proof = {}
    for run in ROOT.iterdir():
        if not run.is_dir() or run.is_symlink() or run.name.startswith('.'):
            continue
        report = run / 'report.json'
        if report.exists():
            data = read(report)
            if data.get('status') in {'rejected', 'succeeded', 'failed', 'cancelled'}:
                for source in data.get('artifact_retention', {}).get('removed_paths', []):
                    proof.setdefault(source, []).append(str(report))
        for tx in (run / 'artifact_retention_transactions').glob('*.json'):
            data = read(tx)
            if data.get('status') == 'completed':
                for source in data.get('result', {}).get('removed_paths', []):
                    proof.setdefault(source, []).append(str(tx))
    targets = []
    preserved = set()
    runs = set()
    for op in IDS:
        parent = ROOT / '.artifact-retention-trash' / op
        owner_path = parent / 'owner.json'
        owner = read(owner_path)
        source = Path(owner['source_path'])
        assert source.is_relative_to(ROOT) and source.name in {'workspace', 'workspace_seed'}
        assert not source.exists() and not source.is_symlink()
        assert owner['hostname'] == socket.gethostname()
        try:
            os.kill(owner['pid'], 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError(f'owner still alive: {owner}')
        assert str(source) in proof, f'no completed removal record: {source}'
        run = ROOT / source.relative_to(ROOT).parts[0]
        origin = read(run / 'run.json')
        target = origin.get('target', {})
        if not target:
            target = read(run / 'artifact_retention_archive.json').get('target', {})
        assert target.get('target_id') == 'agent-browser', (run, target)
        assert target.get('target_type') == 'skill', (run, target)
        targets.append(parent / 'artifact')
        preserved.add(owner_path)
        preserved.update(Path(p) for p in proof[str(source)])
        runs.add(run)
    for name, (capability, datasets) in SEEDS.items():
        run = ROOT / name
        assert read(run / 'report.json')['status'] == 'rejected'
        assert not lc._has_live_run_lease(run)
        assert lc._run_measurement_resume_checkpoint(run) is None
        assert not lc._run_has_resumable_authoritative_measurement_work(run)
        assert not lc._run_has_pending_measurement_work(run)
        for dataset in datasets.split():
            seed = run / 'replay_adaptation' / dataset / capability / 'workspace_seed'
            assert (seed.parent / 'workspace_manifest.json').is_file()
            targets.append(seed)
        runs.add(run)
    for p in targets:
        assert p.is_dir() and not p.is_symlink(), p
        assert p.resolve() == p and p.is_relative_to(ROOT)
    assert len(targets) == 64 and len(set(targets)) == 64
    for run in runs:
        preserved.update(native_paths(run))
    preserved.add(ROOT.parents[1] / 'aworld-skills/agent-browser/SKILL.md')
    before = {str(p): fingerprint(p) for p in sorted(preserved)}
    journal = {'status': 'prepared', 'created_at': time.time(), 'paths': [str(p) for p in targets], 'preserved_files': before, 'disk_free_before': shutil.disk_usage(ROOT).free, 'completed': []}
    JOURNAL.write_text(json.dumps(journal, indent=2) + '\n')
    print(json.dumps({'status': 'prepared', 'paths': len(targets), 'preserved_files': len(before), 'free': journal['disk_free_before']}), flush=True)
    for p in targets:
        shutil.rmtree(p)
        journal['completed'].append(str(p))
        if len(journal['completed']) % 10 == 0:
            print(json.dumps({'completed': len(journal['completed']), 'free': shutil.disk_usage(ROOT).free}), flush=True)
    after = {str(p): fingerprint(p) for p in sorted(preserved)}
    assert before == after, 'preserved native file changed'
    journal.update(status='completed', preserved_files_unchanged=True, disk_free_after=shutil.disk_usage(ROOT).free)
    JOURNAL.write_text(json.dumps(journal, indent=2) + '\n')
    print(json.dumps({'status': 'completed', 'paths': len(targets), 'preserved_files': len(before), 'free': journal['disk_free_after']}), flush=True)

if __name__ == '__main__':
    main()
