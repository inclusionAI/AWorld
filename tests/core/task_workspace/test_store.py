from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from aworld.core.task_workspace.store import (
    StoreConflictError, StoreIntegrityError, StorePolicyError, TaskWorkspaceStore,
)
from aworld.core.task_workspace.store_io import canonical, fingerprint

CHECKS = [{"id": "measure", "kind": "synthetic-distance"}]
POLICY = {"mandatory_checks": ["measure"],
          "hard_constraints": [{"artifact": "result.txt", "max_bytes": 8}],
          "objective": {"metric": "measure.score", "direction": "maximize"}}


async def actual_validator(candidate_files, inputs, checks, **kwargs):
    """A real child measures candidate values; caller pass/metric fields are unused."""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c",
        "import json,pathlib,sys; values=[int(pathlib.Path(p).read_text()) for p in sys.argv[1:]]; "
        "print(json.dumps(dict(success=all(0<=x<=100 for x in values),score=sum(100-abs(x-42) for x in values))))",
        *map(str, candidate_files.values()), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await process.communicate()
    measured = json.loads(out) if process.returncode == 0 else {"success": False, "score": -1000}
    binding = lambda files: {k: {"state": "regular", "size": p.stat().st_size,
                               "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                             for k, p in files.items()}
    return {"schema_version": "aworld.validation/v1", "success": measured["success"],
            "checks": [{"id": c["id"], "kind": c["kind"], "success": measured["success"]}
                       for c in checks], "metrics": {"measure.score": measured["score"]},
            "bindings": {"artifacts": binding(candidate_files), "inputs": binding(inputs),
                         "check_definitions_sha256": fingerprint(checks)}, "provenance": []}


@pytest.fixture
def store(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return TaskWorkspaceStore(workspace, {"session": "s", "task": "t"}, root=tmp_path / "state",
                              validator=actual_validator, policy=POLICY)


async def checked(store, value, key="result.txt", policy=None):
    path = store.workspace / "candidate.txt"
    path.write_text(str(value))
    candidate = store.register_candidate({key: path})
    receipt = await store.validate_candidate(candidate["candidate_id"], CHECKS, policy)
    return candidate, receipt


@pytest.mark.asyncio
async def test_real_measurement_constraints_and_best_candidate_survive_failed_attempts(store):
    candidate, receipt = await checked(store, 40)
    assert receipt["metrics"] == {"measure.score": 98}
    assert store.promote(candidate["candidate_id"], receipt["receipt_id"])["promoted"]
    for value, reason in [(10, "not_better"), (-1, "constraints_failed"), ("00000000042", "constraints_failed")]:
        other, evidence = await checked(store, value)
        assert store.promote(other["candidate_id"], evidence["receipt_id"])["reason"] == reason
        assert (store.workspace / "result.txt").read_text() == "40"
    better, evidence = await checked(store, 42)
    assert store.promote(better["candidate_id"], evidence["receipt_id"])["readback"]["valid"]
    (store.workspace / "result.txt").write_text("changed after verification")
    assert not store.readback()["valid"]


@pytest.mark.asyncio
async def test_plain_delivery_never_invents_an_optimization_objective(store):
    policy = {"mandatory_checks": ["measure"]}
    a, proof = await checked(store, 10, policy=policy)
    assert store.promote(a["candidate_id"], proof["receipt_id"], policy)["promoted"]
    b, proof = await checked(store, 42, policy=policy)
    assert store.promote(b["candidate_id"], proof["receipt_id"], policy)["reason"] == "equivalent_no_objective"
    assert (store.workspace / "result.txt").read_text() == "10"


@pytest.mark.asyncio
async def test_forged_stale_and_mismatched_receipts_cannot_promote(store):
    a, proof = await checked(store, 40)
    b, _ = await checked(store, 41)
    with pytest.raises(StoreConflictError):
        store.promote(b["candidate_id"], proof["receipt_id"])
    with pytest.raises(TypeError):
        await store.validate_candidate(a["candidate_id"], CHECKS, passed=True, metrics={"measure.score": 999})
    receipt_path = store.store_path / "receipts" / (proof["receipt_id"] + ".json")
    forged = json.loads(receipt_path.read_text())
    forged["receipt"]["validation"]["metrics"]["measure.score"] = 999
    receipt_path.write_bytes(canonical(forged))
    with pytest.raises(StoreIntegrityError, match="forged"):
        store.promote(a["candidate_id"], proof["receipt_id"])
    a, proof = await checked(store, 40)
    source = store.workspace / "new-input"
    source.write_text("source")
    store.protect_inputs([source])
    with pytest.raises(StoreConflictError, match="stale"):
        store.promote(a["candidate_id"], proof["receipt_id"])


@pytest.mark.asyncio
async def test_changed_bytes_during_checker_execution_cannot_receive_receipt(store):
    async def mutating(files, inputs, checks, **kwargs):
        result = await actual_validator(files, inputs, checks, **kwargs)
        next(iter(files.values())).write_text("tampered")
        return result
    store.validator = mutating
    with pytest.raises(StoreIntegrityError):
        await checked(store, 40)
    assert store.status()["receipt_count"] == 0


@pytest.mark.asyncio
async def test_cancellation_leaves_no_receipt_or_published_candidate(store):
    started = asyncio.Event()
    stopped = asyncio.Event()
    async def waiting(*args, **kwargs):
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(30)")
        started.set()
        try:
            await process.wait()
        finally:
            process.terminate()
            await process.wait()
            stopped.set()
    store.validator = waiting
    task = asyncio.create_task(checked(store, 40))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()
    assert store.status()["receipt_count"] == 0
    assert store.status()["best"] is None
    assert not list((store.store_path / "validation").iterdir())


@pytest.mark.asyncio
async def test_concurrent_promotions_compare_against_latest_accepted_candidate(store):
    choices = [await checked(store, value) for value in (40, 42)]
    def promote(item):
        opened = TaskWorkspaceStore.open_existing(store.store_path, policy=POLICY)
        return opened.promote(item[0]["candidate_id"], item[1]["receipt_id"])
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(promote, choices))
    assert (store.workspace / "result.txt").read_text() == "42"
    assert store.readback()["valid"]


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_event, expected", [("installed", [10, 20]), ("committed", [42, 42])])
async def test_process_crash_multi_file_publication_recovers_last_consistent_candidate(store, crash_event, expected):
    policy = {"mandatory_checks": ["measure"], "objective": POLICY["objective"]}
    async def candidate(values):
        sources = {}
        for name, value in zip(("a.txt", "b.txt"), values):
            path = store.workspace / ("source-" + name)
            path.write_text(str(value))
            sources[name] = path
        candidate = store.register_candidate(sources)
        proof = await store.validate_candidate(candidate["candidate_id"], CHECKS, policy)
        return candidate, proof
    old, proof = await candidate([10, 20])
    store.promote(old["candidate_id"], proof["receipt_id"], policy)
    new, proof = await candidate([42, 42])
    child = """import json,os,sys
from aworld.core.task_workspace.store import TaskWorkspaceStore
s=TaskWorkspaceStore.open_existing(sys.argv[1],policy=json.loads(sys.argv[2]))
s._fault_injector=lambda event,index: os._exit(79) if event==sys.argv[5] and index==0 else None
s.promote(sys.argv[3],sys.argv[4])
"""
    result = subprocess.run([sys.executable, "-c", child, str(store.store_path), json.dumps(policy),
                             new["candidate_id"], proof["receipt_id"], crash_event], check=False)
    assert result.returncode == 79
    reopened = TaskWorkspaceStore.open_existing(store.store_path)
    assert [int((store.workspace / name).read_text()) for name in ("a.txt", "b.txt")] == expected
    assert reopened.readback()["valid"]
    assert not reopened.status()["pending_transaction"]
    assert reopened.last_recovery_result["action"] == ("rollback" if crash_event == "installed" else "complete")
    assert reopened.recover()["action"] == "none"
    assert reopened.status()["last_recovery_result"]["recovered"]


def test_input_snapshots_accumulate_and_never_rebaseline_or_downgrade(store):
    source = store.workspace / "source.txt"
    source.write_text("original")
    first = store.protect_inputs([source])
    source.write_text("newer user data")
    second = store.protect_inputs([source])
    assert first["snapshot_id"] == second["snapshot_id"]
    upgraded = store.protect_inputs([{"path": source, "immutable": True}])
    third = store.workspace / "another"
    third.write_text("another")
    source.unlink()
    accumulated = store.protect_inputs([source, third])
    assert len(accumulated["files"]) == 2
    assert str(source) in accumulated["immutable_paths"]
    assert accumulated["files"][str(source)] == first["files"][str(source)]
    assert not store.immutable_input_evidence()[0]["unchanged"]
    copy = store.working_copy(first["snapshot_id"], store.workspace / "recovery")
    assert Path(copy["files"][str(source)]).read_text() == "original"
    assert upgraded["snapshot_id"] != first["snapshot_id"]


def test_restore_does_not_overwrite_newer_or_damaged_inputs(store):
    source = store.workspace / "source.txt"
    source.write_text("original")
    snapshot = store.protect_inputs([source])
    source.write_text("newer")
    with pytest.raises(StoreConflictError):
        store.restore_inputs(snapshot["snapshot_id"])
    assert source.read_text() == "newer"
    shutil.rmtree(store.workspace)
    store.workspace.mkdir()
    reopened = TaskWorkspaceStore.open_existing(store.store_path)
    assert reopened.restore_inputs(snapshot["snapshot_id"])["restored"] == [str(source)]
    assert source.read_text() == "original"


def test_sqlite_wal_group_preserves_latest_committed_rows_after_raw_deletion(store):
    database = store.workspace / "records.data"
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE samples(value INTEGER)")
    connection.execute("INSERT INTO samples VALUES (42)")
    connection.commit()
    try:
        snapshot = store.protect_inputs([database])
        assert len(snapshot["files"]) == 3
        assert snapshot["groups"][0]["strategy"].startswith("sqlite-")
    finally:
        connection.close()
    for path in snapshot["files"]:
        Path(path).unlink(missing_ok=True)
    restored = store.working_copy(snapshot["snapshot_id"], store.workspace / "recovered")
    with sqlite3.connect(restored["files"][str(database)]) as copy:
        assert copy.execute("SELECT value FROM samples").fetchall() == [(42,)]
        copy.execute("DELETE FROM samples")
    store.restore_inputs(snapshot["snapshot_id"])
    with sqlite3.connect(database) as original:
        assert original.execute("SELECT value FROM samples").fetchall() == [(42,)]
    database.write_bytes(b"damaged")
    with pytest.raises(StoreConflictError):
        store.restore_inputs(snapshot["snapshot_id"])
    assert store.protected_input_files(snapshot["snapshot_id"])[str(database)].is_file()


def test_active_input_group_change_is_refused(store, monkeypatch):
    from aworld.core.task_workspace import store_inputs
    source = store.workspace / "source.txt"
    source.write_text("first")
    real = store_inputs.capture
    def changed(path, blobs, limit):
        result = real(path, blobs, limit)
        path.write_text("changed")
        return result
    monkeypatch.setattr(store_inputs, "capture", changed)
    with pytest.raises(StoreConflictError, match="group changed"):
        store.protect_inputs([source])
    assert store.current_input_snapshot_id is None


def test_exact_external_file_authority_does_not_expand_after_deletion(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = tmp_path / "external.data"
    source.write_text("original")
    store = TaskWorkspaceStore(workspace, {"task": "exact"}, root=tmp_path / "state",
                              declared_roots=[source])
    snapshot = store.protect_inputs([source])
    source.unlink()
    source.mkdir()
    nested = source / "not-authorized"
    nested.write_text("private")
    reopened = TaskWorkspaceStore.open_existing(store.store_path)
    with pytest.raises(StorePolicyError):
        reopened.protect_inputs([nested])
    assert reopened.input_snapshot()["snapshot_id"] == snapshot["snapshot_id"]


def test_registered_provenance_binds_artifact_and_source_byte_ranges(store):
    source = store.workspace / "source.bin"
    source.write_bytes(b"abcdefghij")
    snapshot = store.protect_inputs([{"path": source, "source": {"quote": "use source.bin", "span": [0, 14]}}])
    candidate = store.workspace / "candidate.bin"
    candidate.write_bytes(b"cdef")
    record = store.register_candidate({"result.bin": candidate}, provenance=[{
        "artifact": "result.bin", "start": 0, "end": 4,
        "sources": [{"path": str(source), "start": 2, "end": 6}]}])
    provenance = store.provenance(record["candidate_id"])["artifacts"][0]
    assert provenance["sha256"] == hashlib.sha256(b"cdef").hexdigest()
    assert provenance["sources"][0]["sha256"] == snapshot["files"][str(source)]["sha256"]
    assert provenance["sources"][0]["range_sha256"] == hashlib.sha256(b"cdef").hexdigest()
    assert provenance["range_sha256"] == hashlib.sha256(b"cdef").hexdigest()
    assert provenance["claim_type"] == "declared_derivation_not_semantic_proof"
    with pytest.raises(StorePolicyError):
        store.register_candidate({"result.bin": candidate}, provenance=[{
            "artifact": "result.bin", "start": 0, "end": 99}])


@pytest.mark.asyncio
async def test_nonimmutable_input_can_be_repaired_in_place_but_original_is_retained(store):
    source = store.workspace / "source.txt"
    source.write_text("10")
    snapshot = store.protect_inputs([source])
    policy = {"mandatory_checks": ["measure"]}
    candidate, proof = await checked(store, 42, key="source.txt", policy=policy)
    assert store.promote(candidate["candidate_id"], proof["receipt_id"], policy)["promoted"]
    assert source.read_text() == "42"
    assert store.protected_input_files(snapshot["snapshot_id"])[str(source)].read_text() == "10"


@pytest.mark.asyncio
async def test_immutable_input_cannot_be_a_publication_target(store):
    source = store.workspace / "source.txt"
    source.write_text("10")
    store.protect_inputs([source], immutable=True)
    policy = {"mandatory_checks": ["measure"]}
    candidate, proof = await checked(store, 42, key="source.txt", policy=policy)
    with pytest.raises(StorePolicyError, match="immutable"):
        store.promote(candidate["candidate_id"], proof["receipt_id"], policy)


@pytest.mark.asyncio
async def test_same_best_refreshes_new_policy_and_restores_deleted_bytes(store):
    candidate, proof = await checked(store, 42)
    store.promote(candidate["candidate_id"], proof["receipt_id"])
    changed = {**POLICY, "hard_constraints": [{"artifact": "result.txt", "max_bytes": 2}]}
    updated = await store.validate_candidate(candidate["candidate_id"], CHECKS, changed)
    original = (store.workspace / "result.txt").stat().st_ino
    assert store.promote(candidate["candidate_id"], updated["receipt_id"], changed)["reason"] == "receipt_refreshed"
    assert (store.workspace / "result.txt").stat().st_ino == original
    (store.workspace / "result.txt").unlink()
    assert store.promote(candidate["candidate_id"], updated["receipt_id"], changed)["reason"] == "published_bytes_restored"
    assert store.readback()["valid"]


@pytest.mark.asyncio
async def test_new_mandatory_policy_can_disqualify_and_replace_plain_delivery(store):
    original = {"mandatory_checks": ["measure"]}
    candidate, proof = await checked(store, 10, policy=original)
    store.promote(candidate["candidate_id"], proof["receipt_id"], original)
    changed = {**original, "hard_constraints": [{"artifact": "result.txt", "min_bytes": 3}]}
    result = await store.revalidate_best(CHECKS, changed)
    assert result["eligible"] is False
    assert store.readback()["valid"] is False
    candidate, proof = await checked(store, "042", policy=changed)
    assert store.promote(candidate["candidate_id"], proof["receipt_id"], changed)["promoted"]
    assert store.readback()["valid"]
    assert list((store.store_path / "accepted").glob("*.json"))


@pytest.mark.asyncio
async def test_incumbent_rebinds_accumulated_inputs_and_candidate_ids_remain_discoverable(store):
    candidate, proof = await checked(store, 40)
    store.promote(candidate["candidate_id"], proof["receipt_id"])
    source = store.workspace / "additional.txt"
    source.write_text("public input")
    store.protect_inputs([source])
    assert not store.readback()["valid"]
    assert (await store.revalidate_best(CHECKS))["eligible"]
    assert store.readback()["valid"]
    candidate, proof = await checked(store, 42)
    store.promote(candidate["candidate_id"], proof["receipt_id"])
    page = store.list_candidates(limit=1)
    assert page["total"] == 3
    assert page["items"][0]["candidate_id"] == candidate["candidate_id"]
    assert page["items"][0]["metrics"] == {"measure.score": 100}
    assert store.list_candidates(limit=1, offset=1)["items"][0]["candidate_id"] != candidate["candidate_id"]


@pytest.mark.asyncio
async def test_real_semantic_validator_rejects_invalid_candidate_and_measures_improvement(store):
    validation = pytest.importorskip("aworld.core.task_workspace.validation")
    store.validator = validation.validate_candidate
    source = store.workspace / "source.json"
    source.write_text('[{"id": 1}]')
    store.protect_inputs([source])
    checks = [
        {"id": "rows", "kind": "preserve", "path": "result.json", "input": str(source),
         "format": "json", "columns": ["id"], "primary_key": ["id"]},
        {"id": "fractions", "kind": "numeric", "path": "result.json", "format": "json",
         "columns": ["a", "b"], "min_value": 0, "max_value": 1, "sum_equals": 1,
         "abs_tolerance": 0.001},
    ]
    policy = {"mandatory_checks": ["rows", "fractions"],
              "objective": {"metric": "fractions.max_sum_error", "direction": "minimize"},
              "check_definitions_sha256": fingerprint(checks)}
    for values, accepted in [((0.5, 0.5005), True), ((0.8, 0.8), False), ((0.5, 0.5), True)]:
        path = store.workspace / "proposal.json"
        path.write_text(json.dumps([{"id": 1, "a": values[0], "b": values[1]}]))
        candidate = store.register_candidate({"result.json": path})
        receipt = await store.validate_candidate(candidate["candidate_id"], checks, policy)
        assert store.promote(candidate["candidate_id"], receipt["receipt_id"], policy)["promoted"] == accepted
    assert store.status()["best"]["objective"]["value"] == 0


@pytest.mark.asyncio
async def test_executed_checker_mutation_invalidates_receipt_and_final_readback(store):
    checker = store.workspace / "checker.py"
    checker.write_text("import pathlib,sys; assert 0 <= int(pathlib.Path(sys.argv[1]).read_text()) <= 100\n")
    checks = [{"id": "measure", "kind": "command"}]
    async def execute(files, inputs, definitions, **kwargs):
        before = {"state": "regular", "size": checker.stat().st_size,
                  "sha256": hashlib.sha256(checker.read_bytes()).hexdigest()}
        process = await asyncio.create_subprocess_exec(sys.executable, str(checker), str(next(iter(files.values()))))
        assert await process.wait() == 0
        result = await actual_validator(files, inputs, definitions, **kwargs)
        result["checks"][0]["evidence"] = {
            "checker_paths": {"program": str(checker)}, "checker_files": {"program": before}}
        return result
    store.validator = execute
    path = store.workspace / "candidate.txt"
    path.write_text("42")
    candidate = store.register_candidate({"result.txt": path})
    receipt = await store.validate_candidate(candidate["candidate_id"], checks)
    store.promote(candidate["candidate_id"], receipt["receipt_id"])
    checker.write_text("raise RuntimeError('changed checker')\n")
    with pytest.raises(StoreIntegrityError):
        store.promote(candidate["candidate_id"], receipt["receipt_id"])
    assert "checker_definition_changed" in store.readback()["problems"]


@pytest.mark.asyncio
async def test_receipt_must_bind_caller_execution_environment(store):
    policy = {**POLICY, "execution_environment_sha256": fingerprint({"PROFILE": "caller"})}
    with pytest.raises(StoreIntegrityError, match="environment"):
        await checked(store, 42, policy=policy)
    assert store.status()["receipt_count"] == 0


def test_checksum_corruption_in_wal_is_not_silently_dropped_by_sqlite(store):
    path = store.workspace / "data.db"
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE sample(value INTEGER)")
    db.execute("INSERT INTO sample VALUES(42)")
    db.commit()
    try:
        wal = Path(str(path) + "-wal")
        with wal.open("r+b") as stream:
            stream.seek(32 + 24 + 100)
            value = stream.read(1)
            stream.seek(-1, 1)
            stream.write(bytes([value[0] ^ 1]))
        with pytest.raises(StoreIntegrityError, match="WAL frame checksum"):
            store.protect_inputs([path])
        assert store.current_input_snapshot_id is None
    finally:
        db.close()


def test_regular_file_replaced_by_fifo_during_open_cannot_block_snapshot(store, monkeypatch):
    from aworld.core.task_workspace import store_io
    source = store.workspace / "source"
    source.write_text("value")
    original = store_io.identity
    switched = False
    def identity(path):
        nonlocal switched
        value = original(path)
        if path == source and not switched:
            switched = True
            path.unlink()
            os.mkfifo(path)
        return value
    monkeypatch.setattr(store_io, "identity", identity)
    with pytest.raises(StoreConflictError):
        store_io.capture(source, store.blobs, 1024)
