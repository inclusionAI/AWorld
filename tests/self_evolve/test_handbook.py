from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from aworld.self_evolve.datasets import EvalCase, SelfEvolveDataset
from aworld.self_evolve.evolution_context import compile_evolution_context
from aworld.self_evolve.handbook import (
    HANDBOOK_SCHEMA_VERSION,
    LOCATOR_ACTIVE,
    LOCATOR_FROZEN,
    HandbookLocatorIntegrityError,
    HandbookSnapshot,
    handbook_slice_for_target,
    load_handbook_slice_for_target,
    load_or_refresh_handbook,
    refresh_handbook_snapshot,
    validate_source_locator,
)
from aworld.self_evolve.optimizers import OptimizerRequest
from aworld.self_evolve.types import DatasetRecipe, SelfEvolveTargetRef


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _copy_self_evolve_tree(destination: Path) -> None:
    source = _workspace_root() / "aworld" / "self_evolve"
    shutil.copytree(source, destination / "aworld" / "self_evolve")


def _optimizer_request(
    *,
    target_path: Path,
    handbook_slice: dict[str, object] | None,
) -> OptimizerRequest:
    case = EvalCase(case_id="case", input={"content": "task"})
    dataset = SelfEvolveDataset(
        cases=(case,),
        recipe=DatasetRecipe(
            source={"kind": "test"},
            split_seed="seed",
            splits={"train": [case.case_id]},
            trainable_case_ids=(case.case_id,),
        ),
    )
    return OptimizerRequest.from_dataset(
        target=SelfEvolveTargetRef(
            target_type="workspace-artifact",
            target_id="runner",
            path=str(target_path),
        ),
        current_content="current",
        target_fingerprint="sha256:target",
        trace_packs=(),
        validation_feedback=(),
        dataset=dataset,
        handbook_slice=handbook_slice,
    )


def test_handbook_indexes_framework_components_stages_and_state_register() -> None:
    workspace = _workspace_root()

    snapshot = refresh_handbook_snapshot(workspace)

    assert snapshot.schema_version == HANDBOOK_SCHEMA_VERSION
    assert len(snapshot.modules) >= 60
    assert len(snapshot.components) == 9
    assert len(snapshot.stages) == 10
    assert {item.state_id for item in snapshot.state_register} == {
        "target_fingerprint",
        "environment_fingerprint",
        "candidate_lineage",
        "budget_ledger",
        "apply_journal",
        "campaign_frontier",
    }
    assert all(item.status == LOCATOR_ACTIVE for item in snapshot.components)
    assert all(item.status == LOCATOR_ACTIVE for item in snapshot.state_register)
    assert all(item.entry_locator.status == LOCATOR_ACTIVE for item in snapshot.stages)
    stage_ids = {item.stage_id for item in snapshot.stages}
    assert all(
        next_stage in stage_ids
        for item in snapshot.stages
        for next_stage in item.next_stages
    )
    assert all(item.failure_exits for item in snapshot.stages if not item.terminal)
    assert validate_source_locator(
        snapshot.stages[0].entry_locator,
        workspace_root=workspace,
    )
    round_trip = HandbookSnapshot.from_dict(
        json.loads(json.dumps(snapshot.to_dict()))
    )
    assert round_trip.fingerprint == snapshot.fingerprint


def test_handbook_resync_reuses_unchanged_modules_and_reindexes_changed_file(
    tmp_path: Path,
) -> None:
    _copy_self_evolve_tree(tmp_path)
    first = refresh_handbook_snapshot(tmp_path)
    by_path = {item.relative_path: item for item in first.modules}
    changed_path = tmp_path / "aworld" / "self_evolve" / "diagnostics.py"
    changed_path.write_text(
        changed_path.read_text(encoding="utf-8") + "\n# handbook resync\n",
        encoding="utf-8",
    )

    second = refresh_handbook_snapshot(
        tmp_path,
        previous=first,
        changed_paths=(changed_path,),
    )
    refreshed = {item.relative_path: item for item in second.modules}

    assert refreshed["aworld/self_evolve/diagnostics.py"].fingerprint != (
        by_path["aworld/self_evolve/diagnostics.py"].fingerprint
    )
    assert refreshed["aworld/self_evolve/gates.py"] is by_path[
        "aworld/self_evolve/gates.py"
    ]


def test_handbook_snapshot_is_persisted_and_reloaded(tmp_path: Path) -> None:
    _copy_self_evolve_tree(tmp_path)
    snapshot_path = tmp_path / ".aworld" / "self_evolve" / "handbook" / "snapshot.json"

    first = load_or_refresh_handbook(tmp_path, snapshot_path=snapshot_path)
    second = load_or_refresh_handbook(tmp_path, snapshot_path=snapshot_path)

    assert snapshot_path.is_file()
    assert second.fingerprint == first.fingerprint
    assert not tuple(snapshot_path.parent.glob("*.tmp"))


def test_handbook_freezes_broken_locator_and_blocks_mutation_context(
    tmp_path: Path,
) -> None:
    _copy_self_evolve_tree(tmp_path)
    first = refresh_handbook_snapshot(tmp_path)
    runner_path = tmp_path / "aworld" / "self_evolve" / "runner.py"
    runner_path.write_text("def broken(:\n", encoding="utf-8")

    broken = refresh_handbook_snapshot(
        tmp_path,
        previous=first,
        changed_paths=(runner_path,),
    )
    handbook_slice = handbook_slice_for_target(
        broken,
        workspace_root=tmp_path,
        target_path=runner_path,
    )

    assert handbook_slice is not None
    assert handbook_slice.mutation_allowed is False
    assert handbook_slice.frozen_locator_ids
    assert any(
        item.status == LOCATOR_FROZEN
        for item in broken.components
        if item.component_id == "orchestration"
    )
    with pytest.raises(HandbookLocatorIntegrityError):
        compile_evolution_context(
            _optimizer_request(
                target_path=runner_path,
                handbook_slice=handbook_slice.to_prompt_dict(),
            )
        )


def test_evolution_context_retrieves_only_target_relevant_handbook_slice() -> None:
    workspace = _workspace_root()
    target_path = workspace / "aworld" / "self_evolve" / "runner.py"
    snapshot = refresh_handbook_snapshot(workspace)
    handbook_slice = handbook_slice_for_target(
        snapshot,
        workspace_root=workspace,
        target_path=target_path,
        behavior_signals=("budget ledger",),
    )

    assert handbook_slice is not None
    assert handbook_slice.mutation_allowed is True
    assert handbook_slice.component_ids == ("orchestration",)
    context = compile_evolution_context(
        _optimizer_request(
            target_path=target_path,
            handbook_slice=handbook_slice.to_prompt_dict(),
        )
    )
    prompt = context.to_prompt_payload(candidate_index=0)

    assert prompt["handbook"]["component_ids"] == ["orchestration"]
    assert prompt["handbook"]["mutation_allowed"] is True
    assert all(
        component["component_id"] != "ingestion"
        for component in prompt["handbook"]["components"]
    )


def test_non_framework_target_does_not_build_or_receive_handbook(
    tmp_path: Path,
) -> None:
    target_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"

    handbook_slice = load_handbook_slice_for_target(
        tmp_path,
        target_path=target_path,
    )

    assert handbook_slice is None
    assert not (tmp_path / ".aworld").exists()
