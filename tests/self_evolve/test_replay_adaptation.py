from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from aworld.self_evolve.datasets import (
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.replay_adaptation import (
    ReplayAdapterBinding,
    ReplayAdaptationCompiler,
    ReplayDependency,
    materialize_replay_workspace,
)
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    CandidateReplayResult,
    ReplayExecutionRequest,
    ReplayExecutionResult,
    ReplayVariantResult,
    build_replay_request,
    candidate_replay_is_comparable,
)
from aworld.self_evolve.runner import (
    SelfEvolveRunner,
    _find_reusable_baseline_replay_dir,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SkillTextTarget
from aworld.self_evolve.types import CandidateVariant, SelfEvolveTargetRef


def _dataset(task: str, *, task_id: str = "task-1"):
    trajectory = [
        {
            "meta": {"task_id": task_id, "step": 1},
            "state": {"input": {"content": task}},
            "action": {"content": "historical result", "tool_calls": []},
            "reward": {"status": "success"},
        }
    ]
    return build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id=task_id,
    )


def test_compiler_normalizes_workspace_paths_and_persists_bundle(tmp_path: Path) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    (workspace / "input.txt").write_text("fixture", encoding="utf-8")
    dataset = _dataset(
        "Read /Users/old/Documents/workspace/demo/input.txt and summarize it."
    )

    bundle = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    case = bundle.case("task-1")
    assert (
        "${AWORLD_REPLAY_WORKSPACE}/input.txt"
        in case.adapted_task_input["content"]
    )
    assert Path(bundle.workspace_seed).is_dir()
    assert (Path(bundle.workspace_seed) / "input.txt").read_text(encoding="utf-8") == "fixture"
    assert Path(bundle.manifest_path).is_file()
    assert Path(bundle.environment_snapshot_path).is_file()
    assert bundle.workspace_seed_fingerprint.startswith("sha256:")
    assert bundle.environment_fingerprint.startswith("sha256:")
    assert bundle.adaptation_fingerprint.startswith("sha256:")
    assert bundle.ready is True
    persisted = json.loads(
        (tmp_path / "run" / "adaptation" / "bundle.json").read_text(encoding="utf-8")
    )
    assert persisted["adaptation_fingerprint"] == bundle.adaptation_fingerprint
    environment = json.loads(Path(bundle.environment_snapshot_path).read_text())
    assert environment["runtime"]["python_version"]
    assert not any("token" in key.lower() for key in environment["environment"])


def test_compiler_marks_continuation_without_prior_context_incomplete(tmp_path: Path) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset("Continue the current task with this additional operator steering: retry."),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    case = bundle.case("task-1")
    assert case.readiness == "context_incomplete"
    assert any(item.status == "context_incomplete" for item in case.dependencies)
    assert bundle.ready is False


def test_compiler_marks_unbound_local_endpoint_runtime_required(tmp_path: Path) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset("Connect to http://[::1]:9222 and inspect the page."),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    dependency = next(
        item for item in bundle.case("task-1").dependencies if item.kind == "local_endpoint"
    )
    assert dependency.status == "runtime_required"
    assert dependency.deterministic is False
    assert bundle.ready is False


def test_compiler_snapshots_explicit_bounded_external_file(tmp_path: Path) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    external = tmp_path / "inputs" / "article.txt"
    external.parent.mkdir()
    external.write_text("recorded input", encoding="utf-8")

    bundle = ReplayAdaptationCompiler(max_external_file_bytes=1024).compile(
        dataset=_dataset(f"Summarize {external}"),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    case = bundle.case("task-1")
    dependency = next(item for item in case.dependencies if item.kind == "local_file")
    assert dependency.status == "snapshotted"
    adapted_path = case.adapted_task_input["content"].split("Summarize ", 1)[1]
    assert adapted_path.startswith("${AWORLD_REPLAY_WORKSPACE}/.aworld_replay_fixtures/")
    relative = adapted_path.removeprefix("${AWORLD_REPLAY_WORKSPACE}/")
    assert (Path(bundle.workspace_seed) / relative).read_text(encoding="utf-8") == "recorded input"
    assert bundle.ready is True


def test_compiler_does_not_snapshot_secret_like_external_file(tmp_path: Path) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    secret = tmp_path / "inputs" / ".env"
    secret.parent.mkdir()
    secret.write_text("TOKEN=secret", encoding="utf-8")

    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset(f"Read {secret}"),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    dependency = next(
        item for item in bundle.case("task-1").dependencies if item.kind == "local_file"
    )
    assert dependency.status == "unresolved"
    assert bundle.ready is False
    assert "secret" not in json.dumps(json.loads((tmp_path / "run" / "adaptation" / "bundle.json").read_text()), ensure_ascii=False)


def test_registered_adapter_can_make_local_endpoint_deterministic(tmp_path: Path) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    class LocalEndpointFixtureAdapter:
        adapter_id = "test.local-endpoint-fixture.v1"

        def bind(self, dependency, *, context):
            if dependency.kind != "local_endpoint":
                return None
            fixture = context.artifact_root / "fixtures" / "cdp.json"
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_text('{"pages": []}', encoding="utf-8")
            return ReplayAdapterBinding(
                adapter_id=self.adapter_id,
                dependency_id=dependency.identifier,
                deterministic=True,
                environment={"AWORLD_REPLAY_CDP_FIXTURE": str(fixture)},
                fixture_paths=(str(fixture),),
            )

    bundle = ReplayAdaptationCompiler(
        adapters=(LocalEndpointFixtureAdapter(),)
    ).compile(
        dataset=_dataset("Connect to http://127.0.0.1:9222 and inspect the page."),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )

    case = bundle.case("task-1")
    dependency: ReplayDependency = next(
        item for item in case.dependencies if item.kind == "local_endpoint"
    )
    assert dependency.status == "adapter_bound"
    assert dependency.adapter_id == "test.local-endpoint-fixture.v1"
    assert case.bindings[0].environment["AWORLD_REPLAY_CDP_FIXTURE"].endswith("cdp.json")
    assert bundle.ready is True


def test_materialize_replay_workspace_replaces_dirty_destination(tmp_path: Path) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    (workspace / "input.txt").write_text("seed", encoding="utf-8")
    bundle = ReplayAdaptationCompiler().compile(
        dataset=_dataset(f"Read {workspace}/input.txt"),
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )
    destination = tmp_path / "rollout"
    destination.mkdir()
    (destination / "dirty.txt").write_text("old mutation", encoding="utf-8")

    materialize_replay_workspace(bundle, destination)

    assert (destination / "input.txt").read_text(encoding="utf-8") == "seed"
    assert not (destination / "dirty.txt").exists()


@pytest.mark.asyncio
async def test_each_variant_and_repetition_starts_from_same_clean_seed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "source" / "demo"
    workspace.mkdir(parents=True)
    (workspace / "input.txt").write_text("seed", encoding="utf-8")
    dataset = _dataset(f"Read {workspace}/input.txt")
    bundle = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=workspace,
        artifact_root=tmp_path / "run" / "adaptation",
    )
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo")
    candidate = CandidateVariant(
        candidate_id="cand-1",
        target=target,
        content="---\nname: demo\n---\n# Demo\n",
        rationale="test",
        target_fingerprint="sha256:baseline",
    )
    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request)
        isolated_workspace = Path(request.workspace_root)
        assert isolated_workspace != workspace
        assert (isolated_workspace / "input.txt").read_text(encoding="utf-8") == "seed"
        assert not (isolated_workspace / "mutation.txt").exists()
        assert str(isolated_workspace / "input.txt") in request.task_text
        assert request.workspace_seed_fingerprint == bundle.workspace_seed_fingerprint
        assert request.adaptation_fingerprint == bundle.adaptation_fingerprint
        (isolated_workspace / "mutation.txt").write_text(
            request.variant_id,
            encoding="utf-8",
        )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    request = build_replay_request(
        run_id="run-isolated",
        workspace_root=workspace,
        target=target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        replay_adaptation=bundle,
        baseline_repetitions=2,
        candidate_repetitions=2,
    )

    assert request.dataset_fingerprint.startswith("sha256:")
    assert request.baseline_skill_fingerprint == "sha256:baseline"
    assert request.adaptation_fingerprint == bundle.adaptation_fingerprint
    assert request.workspace_seed_fingerprint == bundle.workspace_seed_fingerprint
    assert request.task_input_fingerprint == bundle.case("task-1").task_input_fingerprint

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )

    assert len(calls) == 4
    assert len({call.workspace_root for call in calls}) == 4
    assert {
        Path(call.workspace_root).parents[1].name for call in calls
    } == {"baseline", "cand-1"}
    assert candidate_replay_is_comparable(dataset=dataset, replay_result=result) is True

    mismatched_candidate = replace(
        result.candidate,
        metrics={
            **dict(result.candidate.metrics),
            "workspace_seed_fingerprint": "sha256:different-seed",
        },
    )
    assert candidate_replay_is_comparable(
        dataset=dataset,
        replay_result=replace(result, candidate=mismatched_candidate),
    ) is False

    lookup = {
        "store": FilesystemSelfEvolveStore(workspace),
        "run_id": "next-run",
        "target": target,
        "dataset": dataset,
        "baseline_repetitions": 2,
        "baseline_skill_fingerprint": request.baseline_skill_fingerprint,
        "dataset_fingerprint": request.dataset_fingerprint,
        "adaptation_fingerprint": request.adaptation_fingerprint,
        "workspace_seed_fingerprint": request.workspace_seed_fingerprint,
    }
    assert _find_reusable_baseline_replay_dir(**lookup) is not None
    assert _find_reusable_baseline_replay_dir(
        **{**lookup, "baseline_skill_fingerprint": "sha256:changed-skill"}
    ) is None
    assert _find_reusable_baseline_replay_dir(
        **{**lookup, "dataset_fingerprint": "sha256:changed-dataset"}
    ) is None
    assert _find_reusable_baseline_replay_dir(
        **{**lookup, "adaptation_fingerprint": "sha256:changed-adaptation"}
    ) is None


def _result_with_request_provenance(request, candidate_id: str) -> CandidateReplayResult:
    metrics = {
        "adaptation_fingerprint": request.adaptation_fingerprint,
        "workspace_seed_fingerprint": request.workspace_seed_fingerprint,
        "task_input_fingerprint": request.task_input_fingerprint,
        "dataset_fingerprint": request.dataset_fingerprint,
        "baseline_skill_fingerprint": request.baseline_skill_fingerprint,
    }
    return CandidateReplayResult(
        request=request,
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="succeeded",
            trajectory=[{"action": {"content": "baseline"}}],
            metrics=metrics,
        ),
        candidate=ReplayVariantResult(
            variant_id=candidate_id,
            status="succeeded",
            trajectory=[{"action": {"content": candidate_id}}],
            metrics=metrics,
        ),
    )


@pytest.mark.asyncio
async def test_runner_compiles_adaptation_before_building_replay_request(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    dataset = _dataset(f"Read {tmp_path}/input.txt")
    (tmp_path / "input.txt").write_text("seed", encoding="utf-8")
    target = SkillTextTarget(skill_path)
    candidate = CandidateVariant(
        candidate_id="cand-runner",
        target=target.identity,
        content="---\nname: demo\n---\n# Demo\nImproved.\n",
        rationale="test",
        target_fingerprint=target.fingerprint_current_content(),
    )

    class CapturingBackend:
        def __init__(self) -> None:
            self.requests = []

        async def replay_candidate(self, request, *, candidate, dataset):
            self.requests.append(request)
            return _result_with_request_provenance(request, candidate.candidate_id)

    backend = CapturingBackend()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=object(),
        replay_enabled=True,
        candidate_replay_backend=backend,
    )

    replay_result, paired_dataset, gate = await runner._replay_selected_candidate(
        run_id="run-adapted",
        target=target,
        dataset=dataset,
        selected_candidate=candidate,
        apply_policy="proposal",
    )

    assert replay_result is not None
    assert paired_dataset is not None
    assert gate is not None and gate.passed is True
    request = backend.requests[0]
    assert request.replay_adaptation is not None
    assert request.replay_adaptation.ready is True
    assert "${AWORLD_REPLAY_WORKSPACE}/input.txt" in request.task_input["content"]
    assert (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-adapted"
        / "replay_adaptation"
    ).is_dir()


@pytest.mark.asyncio
async def test_runner_blocks_unresolved_adaptation_before_rollout(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path)
    candidate = CandidateVariant(
        candidate_id="cand-blocked",
        target=target.identity,
        content="---\nname: demo\n---\n# Demo\nImproved.\n",
        rationale="test",
        target_fingerprint=target.fingerprint_current_content(),
    )

    class FailingBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            raise AssertionError("rollout must not start for unresolved adaptation")

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=object(),
        replay_enabled=True,
        candidate_replay_backend=FailingBackend(),
    )

    for policy in ("proposal", "auto_verified"):
        replay_result, paired_dataset, gate = await runner._replay_selected_candidate(
            run_id=f"run-blocked-{policy}",
            target=target,
            dataset=_dataset("Inspect http://127.0.0.1:9222"),
            selected_candidate=candidate,
            apply_policy=policy,
        )
        assert replay_result is None
        assert paired_dataset is None
        assert gate is not None
        assert gate.gate_name == "replay_adaptation"
        assert gate.passed is False
        assert gate.details["readiness"] == "runtime_required"
