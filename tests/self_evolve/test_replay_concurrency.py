from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aworld.runner import Runners
from aworld.runners.batch import DeterministicTaskBatchExecutor
from aworld.self_evolve.concurrency import SelfEvolveConcurrencyPolicy
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    CandidateReplayRequest,
    ReplayVariantResult,
)
from aworld.self_evolve.replay_adaptation import (
    ReplayAdaptationBundle,
    ReplayAdapterBinding,
    ReplayCaseAdaptation,
    validate_replay_binding_concurrency,
)
from aworld.self_evolve.replay_capability import (
    ReplayCapabilityError,
    discover_replay_capability,
)
from aworld.self_evolve.types import SelfEvolveTargetRef


def _request(tmp_path: Path, *, mode: str) -> CandidateReplayRequest:
    seed = tmp_path / "seed"
    seed.mkdir(exist_ok=True)
    binding = ReplayAdapterBinding(
        adapter_id="skill-replay:demo",
        dependency_id="recorded-state",
        deterministic=True,
        concurrency_mode=mode,
        resource_key=(None if mode == "isolated" else "recorded-state"),
        binding_fingerprint="sha256:binding",
    )
    adaptation = ReplayAdaptationBundle(
        schema_version="test",
        source_workspace_root=str(tmp_path),
        workspace_seed=str(seed),
        workspace_seed_fingerprint="sha256:seed",
        manifest_path=str(tmp_path / "manifest.json"),
        environment_snapshot_path=str(tmp_path / "environment.json"),
        environment_fingerprint="sha256:environment",
        cases=(
            ReplayCaseAdaptation(
                case_id="case-1",
                adapted_task_input="task",
                task_input_fingerprint="sha256:task",
                dependencies=(),
                bindings=(binding,),
                tool_names=(),
                readiness="ready",
            ),
        ),
        adaptation_fingerprint="sha256:adaptation",
        ready=True,
    )
    return CandidateReplayRequest(
        run_id="run",
        task_id="case-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="candidate",
        overlay_skill_root=str(tmp_path / "candidate"),
        task_input="task",
        replay_adaptation=adaptation,
        workspace_seed_fingerprint="sha256:seed",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_max_active"),
    [("isolated", 3), ("shared_read_only", 3), ("exclusive", 1)],
)
async def test_replay_repetitions_respect_skill_owned_concurrency_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_max_active: int,
) -> None:
    active = 0
    max_active = 0
    captured_runner_classes: list[str | None] = []
    original_run_task = Runners.run_task

    async def recording_run_task(task, run_conf=None):
        captured_runner_classes.append(task.runner_cls)
        return await original_run_task(task, run_conf=run_conf)

    backend = AWorldCliCandidateReplayBackend(
        concurrency_policy=SelfEvolveConcurrencyPolicy(
            max_total_concurrency=3,
            replay_concurrency=3,
        ),
        task_batch_executor=DeterministicTaskBatchExecutor(
            run_task=recording_run_task
        ),
    )

    async def fake_run_variant(
        request,
        *,
        variant_id,
        skill_root,
        artifact_dir,
    ):
        nonlocal active, max_active
        del request, skill_root
        active += 1
        max_active = max(max_active, active)
        index = int(variant_id.rsplit("-", 1)[-1])
        await asyncio.sleep(0.01 * (4 - index))
        active -= 1
        return ReplayVariantResult(
            variant_id=variant_id,
            status="succeeded",
            trajectory=[],
            metrics={
                "workspace_seed_fingerprint": "sha256:seed",
                "isolated_workspace_path": str(Path(artifact_dir) / "workspace"),
            },
        )

    monkeypatch.setattr(backend, "_run_variant_with_evidence_retries", fake_run_variant)

    result = await backend._run_repetitions(
        _request(tmp_path, mode=mode),
        base_variant_id="candidate",
        skill_root=None,
        artifact_dir=tmp_path / "repetitions",
        repetitions=3,
    )

    assert max_active == expected_max_active
    assert [item.variant_id for item in result.repetition_results] == [
        "candidate-1",
        "candidate-2",
        "candidate-3",
    ]
    assert len(
        {
            item.metrics["isolated_workspace_path"]
            for item in result.repetition_results
        }
    ) == 3
    assert all(
        runner_cls == "aworld.self_evolve.runtime.SelfEvolveReplayTaskRunner"
        for runner_cls in captured_runner_classes
    )


def test_isolated_binding_rejects_shared_resource_key() -> None:
    with pytest.raises(ValueError, match="isolated"):
        validate_replay_binding_concurrency(
            ReplayAdapterBinding(
                adapter_id="skill-replay:demo",
                dependency_id="recorded-state",
                deterministic=True,
                concurrency_mode="isolated",
                resource_key="shared-browser-session",
                binding_fingerprint="sha256:binding",
            )
        )


def test_missing_binding_concurrency_metadata_defaults_to_exclusive() -> None:
    binding = ReplayAdapterBinding(
        adapter_id="legacy-adapter",
        dependency_id="recorded-state",
        deterministic=True,
    )

    validated = validate_replay_binding_concurrency(binding)

    assert validated.concurrency_mode == "exclusive"
    assert validated.resource_key == "replay-adapter:legacy-adapter"


def test_skill_capability_manifest_publishes_generic_concurrency_metadata(
    tmp_path: Path,
) -> None:
    replay_root = tmp_path / "replay"
    replay_root.mkdir()
    (replay_root / "compiler.py").write_text("pass\n", encoding="utf-8")
    (replay_root / "capability.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld.skill.replay_capability.v1",
                "capability_id": "recorded-state",
                "protocol": "aworld.replay.subprocess.v1",
                "entrypoint": "replay/compiler.py",
                "handles": ["stateful_tool"],
                "concurrency_mode": "shared_read_only",
                "resource_key": "recorded-state-snapshot",
            }
        ),
        encoding="utf-8",
    )

    capability = discover_replay_capability(tmp_path)

    assert capability is not None
    assert capability.manifest.concurrency_mode == "shared_read_only"
    assert capability.manifest.resource_key == "recorded-state-snapshot"


def test_skill_capability_manifest_rejects_contradictory_isolation(
    tmp_path: Path,
) -> None:
    replay_root = tmp_path / "replay"
    replay_root.mkdir()
    (replay_root / "compiler.py").write_text("pass\n", encoding="utf-8")
    (replay_root / "capability.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld.skill.replay_capability.v1",
                "capability_id": "recorded-state",
                "protocol": "aworld.replay.subprocess.v1",
                "entrypoint": "replay/compiler.py",
                "handles": ["stateful_tool"],
                "concurrency_mode": "isolated",
                "resource_key": "shared-session",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReplayCapabilityError, match="isolated"):
        discover_replay_capability(tmp_path)
