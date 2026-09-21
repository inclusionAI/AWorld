import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld.self_evolve.replay import _evidence_manifest_metrics
from aworld_cli.evaluator_runtime import _build_trajectory_prompt


def _fixture(tmp_path: Path, *, observation: bool = True) -> tuple[Path, Path]:
    records = []
    for name in ("source.html", "clean.txt", "context.txt"):
        source = tmp_path / name
        source.write_text(f"Evidence from {name}", encoding="utf-8")
        records.append({
            "source_id": name,
            "artifact_path": str(source),
            "extraction_method": "bounded_extract",
            "bounded_excerpt": f"Evidence from {name}",
        })
    agent_manifest = tmp_path / "evidence_manifest.jsonl"
    agent_bytes = "".join(json.dumps(item) + "\n" for item in records[1:]).encode()
    agent_manifest.write_bytes(agent_bytes)
    canonical_manifest = tmp_path / "framework_canonical_evidence_manifest.jsonl"
    canonical_manifest.write_text("".join(json.dumps(item) + "\n" for item in records))
    _evidence_manifest_metrics(artifact_dir=tmp_path, evidence_manifest=canonical_manifest)
    bundle_path = tmp_path / "evidence_bundle.json"
    if observation:
        bundle = json.loads(bundle_path.read_text())
        bundle["agent_manifest_observation"] = {
            "path": str(agent_manifest), "present": True, "readable": True,
            "valid": True, "entry_count": 2, "invalid_entry_count": 0,
            "size_bytes": len(agent_bytes),
            "fingerprint": "sha256:" + hashlib.sha256(agent_bytes).hexdigest(),
        }
        bundle_path.write_text(json.dumps(bundle))
    return bundle_path, agent_manifest


def _prompt(tmp_path: Path, bundle_path: Path, answer: str) -> dict:
    extracted = tmp_path / "extracted.json"
    extracted.write_text(json.dumps({
        "task_id": "manifest-scope", "question": "Summarize the evidence.",
        "final_answer": answer, "steps": [], "evidence": [],
        "evidence_bundle_path": str(bundle_path),
    }))
    return json.loads(_build_trajectory_prompt(
        {"task_id": "manifest-scope"},
        {"artifacts": {"outcome": {"extracted_path": str(extracted)}}},
        suite=None,
    ))


@pytest.mark.parametrize("reported_count", [2, 8])
def test_named_agent_manifest_count_is_distinct_from_full_canonical_inventory(
    tmp_path: Path, reported_count: int,
):
    bundle_path, _ = _fixture(tmp_path)
    answer = f"evidence_manifest.jsonl contains {reported_count} records."

    prompt = _prompt(tmp_path, bundle_path, answer)

    digest = prompt["evidence_digest"]
    scopes = digest["manifest_scopes"]
    canonical = scopes["canonical_bundle_manifest"]
    agent = scopes["agent_authored_manifest"]
    assert digest["canonical_bundle_valid"] is True
    assert canonical["filename"] == "framework_canonical_evidence_manifest.jsonl"
    assert canonical["entry_count"] == 3
    assert canonical["record_count_verified"] is True
    assert agent["filename"] == "evidence_manifest.jsonl"
    assert agent["entry_count"] == 2
    assert agent["record_count_verified"] is True
    assert agent["authoritative_evidence"] is False
    # A wrong answer remains wrong against the observed count; no answer text
    # or evidence is rewritten to agree with either inventory.
    assert prompt["extracted_trajectory"]["final_answer"] == answer
    assert (reported_count == agent["entry_count"]) is (reported_count == 2)
    assert len(digest["entries"]) == 3
    sources = [item for item in prompt["artifact_backed_evidence"]["artifacts"]
               if item["kind"] == "source_artifact"]
    assert {Path(item["path"]).name for item in sources} == {
        "source.html", "clean.txt", "context.txt"
    }


@pytest.mark.parametrize("mutation", ["claimed_count", "changed_file", "symlink"])
def test_agent_manifest_observation_requires_independent_file_verification(
    tmp_path: Path, mutation: str,
):
    bundle_path, agent_manifest = _fixture(tmp_path)
    if mutation == "claimed_count":
        bundle = json.loads(bundle_path.read_text())
        bundle["agent_manifest_observation"]["entry_count"] = 8
        bundle_path.write_text(json.dumps(bundle))
    elif mutation == "changed_file":
        agent_manifest.write_text(agent_manifest.read_text() + "{}\n")
    else:
        copied = tmp_path / "copied.jsonl"
        agent_manifest.rename(copied)
        agent_manifest.symlink_to(copied)

    prompt = _prompt(tmp_path, bundle_path, "evidence_manifest.jsonl contains 8 records.")

    digest = prompt["evidence_digest"]
    agent = digest["manifest_scopes"]["agent_authored_manifest"]
    assert agent["record_count_verified"] is False
    assert "entry_count" not in agent
    assert agent["validation_errors"]
    assert digest["canonical_bundle_valid"] is True
    assert len(digest["entries"]) == 3


def test_legacy_bundle_does_not_infer_task_time_agent_manifest_count(tmp_path: Path):
    bundle_path, agent_manifest = _fixture(tmp_path, observation=False)
    assert agent_manifest.exists()

    prompt = _prompt(tmp_path, bundle_path, "evidence_manifest.jsonl contains 2 records.")

    agent = prompt["evidence_digest"]["manifest_scopes"]["agent_authored_manifest"]
    assert agent == {"observation_available": False}
    assert prompt["evidence_digest"]["entry_count"] == 3


def test_invalid_canonical_manifest_never_acquires_authority_from_scope_metadata(tmp_path: Path):
    bundle_path, _ = _fixture(tmp_path)
    canonical = tmp_path / "framework_canonical_evidence_manifest.jsonl"
    canonical.write_text(canonical.read_text() + "{}\n")

    prompt = _prompt(tmp_path, bundle_path, "evidence_manifest.jsonl contains 2 records.")

    digest = prompt["evidence_digest"]
    scope = digest["manifest_scopes"]["canonical_bundle_manifest"]
    assert digest["canonical_bundle_valid"] is False
    assert scope["record_count_verified"] is False
    assert scope["authoritative_evidence"] is False
