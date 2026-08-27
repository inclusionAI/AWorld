from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from aworld.core.tool.replay_policy import DynamicEndpointBinding
from aworld.self_evolve.concurrency import SelfEvolveConcurrencyPolicy
from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.overlay import create_candidate_skill_overlay
from aworld.self_evolve.overlay import cleanup_self_evolve_overlays
from aworld.self_evolve.replay import (
    AWorldCliCandidateReplayBackend,
    AWorldCliReplayExecutor,
    CandidateReplayRequest,
    CandidateReplayMemberResult,
    CandidateReplayResult,
    ReplayExecutionRequest,
    ReplayExecutionResult,
    ReplayServiceProcessExitedError,
    ReplayServiceReadinessTimeout,
    ReplayServiceProtocolError,
    ReplayVariantResult,
    baseline_control_fingerprint,
    build_paired_replay_dataset,
    build_replay_request,
    compile_authoritative_replay_evidence_policy_profile_v2,
    compile_replay_evidence_policy_profile_v2,
    candidate_replay_is_comparable,
    candidate_replay_artifact_directory,
    candidate_replay_pair_coverage,
    load_candidate_replay_result,
    normalize_replay_members,
    _aggregate_member_variant_results,
    _aggregate_variant_results,
    _invalid_evidence_manifest_entry_reason,
    _infer_baseline_skill_root_from_target,
    _evidence_manifest_metrics,
    _final_answer_artifact_reference_metrics,
    _framework_resolved_endpoint_bindings,
    _frozen_replay_capability_from_mapping,
    _execution_failure_event,
    _extract_trajectory_payload_from_stdout,
    _has_authoritative_per_member_repetitions,
    _member_artifact_name,
    _member_baseline_replay_dir,
    _probe_advertised_websockets,
    _attach_replay_service_protocol_diagnostics,
    _baseline_replay_is_reusable,
    _classify_candidate_task_rollout_nontermination,
    _preserve_replay_service_protocol_trace,
    _protocol_trace_runtime_artifact_constraint,
    _reset_replay_service_protocol_trace,
    _persist_variant_lifecycle,
    _protocol_probe_response_mismatch,
    _project_replay_capability_for_case,
    _probe_replay_service,
    _replay_capability_recorded_response_values,
    _read_websocket_frame,
    _replay_capability_fixture_summaries,
    _replay_dependency_boundary_failure,
    _replay_evidence_runtime_policy_metrics,
    _resume_root_is_compatible,
    replay_capability_fixture_leaf_values,
    replay_capability_fixture_response_leaf_values,
    _replay_service_failure_with_stderr,
    _replay_service_start_failure_details,
    _stored_baseline_matches_request,
    _task_response_signature,
    _trusted_task_response_usage_metrics,
    _replay_failure_outcome,
    _runtime_resolved_endpoint_bindings,
    replay_dataset_fingerprint,
    _run_replay_cli,
    _validate_nonempty_correlated_json_response,
    _validate_replay_service_protocol_trace,
    _wait_for_replay_service_protocol_trace,
    _validate_websocket_handshake_response,
    _load_variant_result_from_dir,
    _load_self_evolve_task_response,
    _measurement_terminal_state_for_variant,
)
from aworld.self_evolve.failure_events import (
    FailureEventSource,
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayExecutionStatus,
    ReplayFailureEvent,
)
from aworld.self_evolve.measurement import (
    ComponentIdentity,
    ControlledExperimentSpec,
    ExperimentBudget,
    FrozenIdentities,
    MeasurementPolicyMode,
    OutcomePlan,
    SamplingPlan,
    SwapAxis,
)
from aworld.self_evolve.measurement_control import (
    MeasurementArm,
    MeasurementWorkUnitState,
    MeasurementWorkUnitV1,
)
from aworld.self_evolve.types import SelfEvolveTargetRef
from aworld.self_evolve.replay_adaptation import (
    ReplayAdaptationCompiler,
    compile_replay_adaptation_isolation_decision,
)


def test_candidate_replay_artifact_directory_is_shared_with_measurement_lanes(
    tmp_path: Path,
) -> None:
    replay_dir = candidate_replay_artifact_directory(
        workspace_root=tmp_path,
        run_id="campaign/cycle-1",
        candidate_id="candidate:one",
        artifact_namespace="score/tie-break",
    )

    assert replay_dir == (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "campaigncycle-1"
        / "score"
        / "tie-break"
        / "replay"
        / "candidateone"
    )
    assert replay_dir / "measurement-lanes" == replay_dir.joinpath(
        "measurement-lanes"
    )


def test_authoritative_evidence_profile_binds_all_compiler_inputs_and_services(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bundle = ReplayAdaptationCompiler().compile(
        dataset=build_dataset_from_source(
            SelfEvolveEvalSourceConfig(kind="current_trajectory"),
            current_trajectory=(
                {
                    "meta": {"task_id": "case-1", "step": 1},
                    "state": {"input": {"content": "Run the task."}},
                    "action": {"content": "done", "tool_calls": []},
                    "reward": {"status": "success"},
                },
            ),
            task_id="case-1",
        ),
        workspace_root=workspace,
        artifact_root=tmp_path / "adaptation",
    )
    bundle = replace(
        bundle,
        replay_capability=_frozen_skill_runtime_capability(tmp_path),
    )
    fp = lambda value: "sha256:" + hashlib.sha256(value.encode()).hexdigest()
    experiment = ControlledExperimentSpec.create(
        run_id="run-profile",
        mode=MeasurementPolicyMode.REQUIRED,
        swap_axis=SwapAxis.ARTIFACT,
        control=ComponentIdentity("control", fp("control")),
        treatment=ComponentIdentity("treatment", fp("treatment")),
        frozen_identities=FrozenIdentities(
            task_model=fp("task-model"),
            generator=fp("generator"),
            scheduler=fp("scheduler"),
            evaluator=fp("evaluator"),
            dataset=fp("dataset"),
            environment=fp("environment"),
            runtime=fp("runtime"),
            prompt_context=fp("prompt"),
            budget=fp("budget"),
        ),
        sampling=SamplingPlan(independent_case_ids=("case-1",)),
        outcomes=OutcomePlan(
            primary_metric="task_success",
            minimum_independent_cases=1,
        ),
        budgets=ExperimentBudget(),
    )

    profile = compile_authoritative_replay_evidence_policy_profile_v2(
        experiment=experiment,
        target=SelfEvolveTargetRef("skill", "demo"),
        replay_adaptation=bundle,
        member_timeout_seconds=600,
    )

    assert {item.contract_kind for item in profile.contract_identities} == {
        "task_observation",
        "target_adapter",
        "replay_capability",
        "evaluator",
        "resource_policy",
    }
    assert len(profile.endpoint_bindings) == 1
    endpoint = profile.endpoint_bindings[0]
    assert endpoint.binding_id == "service-0"
    assert endpoint.environment_name == "AWORLD_REPLAY_ENDPOINT_SERVICE_0"
    assert endpoint.endpoint.startswith("http://127.0.0.1:")
    assert (
        compile_authoritative_replay_evidence_policy_profile_v2(
            experiment=experiment,
            target=SelfEvolveTargetRef("skill", "demo"),
            replay_adaptation=bundle,
            member_timeout_seconds=600,
        )
        == profile
    )
    changed_adapter = compile_authoritative_replay_evidence_policy_profile_v2(
        experiment=experiment,
        target=SelfEvolveTargetRef("skill", "demo"),
        replay_adaptation=bundle,
        member_timeout_seconds=600,
        target_adapter_identity={
            "module": "aworld.self_evolve.targets",
            "class": "SkillTextTarget",
            "version": "v2",
        },
    )
    assert changed_adapter.fingerprint != profile.fingerprint
    service_decision = compile_replay_adaptation_isolation_decision(
        bundle,
        materialization_root=tmp_path / "measurement-lanes",
        requested_lane_count=2,
    )
    assert service_decision.safe_lane_count == 1
    assert service_decision.fallback is not None
    assert service_decision.fallback.limiting_resource == "replay_service:service-0"


def test_framework_endpoint_resolution_uses_supervisor_service_identity() -> None:
    profile = compile_replay_evidence_policy_profile_v2(
        endpoint_bindings=(
            DynamicEndpointBinding(
                binding_id="service_1",
                service_identity="replay.demo.service_1",
                endpoint="http://127.0.0.1:25346",
            ),
            DynamicEndpointBinding(
                binding_id="runtime.adapter",
                service_identity="replay.adapter",
                endpoint="http://127.0.0.1:31000",
            ),
        )
    )
    environment = {
        # A stale environment value must not replace the endpoint that the
        # framework service supervisor actually started.
        "AWORLD_REPLAY_ENDPOINT_SERVICE_1": "http://127.0.0.1:29999",
        "AWORLD_REPLAY_ENDPOINT_RUNTIME_ADAPTER": "http://127.0.0.1:31000",
    }
    framework_bindings = _framework_resolved_endpoint_bindings(
        profile,
        environment=environment,
        service_endpoints={"service_1": "http://127.0.0.1:25346"},
    )
    request = ReplayExecutionRequest(
        variant_id="baseline",
        task_id="case-1",
        candidate_id="candidate-1",
        workspace_root="/tmp/workspace",
        task_input="task",
        task_text="task",
        skill_root=None,
        artifact_dir="/tmp/artifact",
        environment=environment,
        framework_endpoint_bindings=framework_bindings,
    )

    assert framework_bindings == {
        "runtime.adapter": "http://127.0.0.1:31000",
        "service_1": "http://127.0.0.1:25346",
    }
    assert _runtime_resolved_endpoint_bindings(request, profile) == {
        "runtime.adapter": "http://127.0.0.1:31000",
        "service_1": "http://127.0.0.1:25346",
    }


def test_replay_execution_request_preserves_legacy_positional_skill_names() -> None:
    request = ReplayExecutionRequest(
        "baseline",
        "case-1",
        "candidate-1",
        "/tmp/workspace",
        "task",
        "task",
        None,
        "/tmp/artifact",
        ("demo",),
    )

    assert request.skill_names == ("demo",)
    assert request.variant_role is None


def test_runtime_endpoint_alias_round_trips_without_measurement_profile() -> None:
    environment = {
        "AWORLD_REPLAY_ENDPOINT_SERVICE_1": "http://127.0.0.1:25346"
    }
    profile = compile_replay_evidence_policy_profile_v2(
        endpoint_bindings=(
            DynamicEndpointBinding(
                binding_id="runtime.service_1",
                service_identity="replay.service.1",
                endpoint="http://127.0.0.1:25346",
            ),
        )
    )
    framework_bindings = _framework_resolved_endpoint_bindings(
        None,
        environment=environment,
        service_endpoints={"service_1": "http://127.0.0.1:25346"},
    )
    request = ReplayExecutionRequest(
        variant_id="baseline",
        task_id="case-1",
        candidate_id="candidate-1",
        workspace_root="/tmp/workspace",
        task_input="task",
        task_text="task",
        skill_root=None,
        artifact_dir="/tmp/artifact",
        environment=environment,
        framework_endpoint_bindings=framework_bindings,
    )

    assert framework_bindings == {
        "service_1": "http://127.0.0.1:25346"
    }
    assert _runtime_resolved_endpoint_bindings(request, profile) == {
        "runtime.service_1": "http://127.0.0.1:25346"
    }


@pytest.mark.asyncio
async def test_required_measurement_preflight_uses_framework_endpoint_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile = compile_replay_evidence_policy_profile_v2(
        endpoint_bindings=(
            DynamicEndpointBinding(
                binding_id="service_1",
                service_identity="replay.demo.service_1",
                endpoint="http://127.0.0.1:25346",
            ),
        )
    )
    fp = lambda value: "sha256:" + hashlib.sha256(value.encode()).hexdigest()
    plan_fingerprint = fp("measurement-plan")
    work_unit = MeasurementWorkUnitV1.create(
        measurement_plan_fingerprint=plan_fingerprint,
        experiment_id="experiment-1",
        artifact_fingerprint=fp("control"),
        pairing_control_fingerprint=fp("control"),
        dataset_fingerprint=fp("dataset"),
        case_id="case-1",
        arm=MeasurementArm.CONTROL,
        repetition_id=1,
        execution_contract_fingerprint=fp("execution"),
        evidence_policy_fingerprint=profile.fingerprint,
        sampling_contract_fingerprint=fp("sampling"),
        isolation_decision_fingerprint=fp("isolation"),
        stage_id="qualification",
    )
    called = False

    def fake_run(command, **kwargs):
        nonlocal called
        called = True
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="stopped")

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)
    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="baseline",
            task_id="case-1",
            candidate_id="candidate-1",
            workspace_root=str(tmp_path),
            task_input="task",
            task_text="task",
            skill_root=None,
            artifact_dir=str(tmp_path / "artifacts"),
            environment={
                "AWORLD_REPLAY_ENDPOINT_SERVICE_1": "http://127.0.0.1:29999"
            },
            framework_endpoint_bindings={
                "service_1": "http://127.0.0.1:25346"
            },
            evidence_policy_mode="required",
            measurement_plan_fingerprint=plan_fingerprint,
            measurement_work_unit=work_unit,
            measurement_evidence_policy_profile=profile,
            lane_materialization_fingerprint=fp("lane"),
            evidence_finalization_timeout_seconds=10,
        )
    )

    assert called is True
    assert result.failure is not None
    assert result.failure.get("code") != "evidence_policy_v2_preflight_failed"


@pytest.mark.asyncio
async def test_required_shadow_preflight_round_trips_runtime_endpoint_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called = False

    def fake_run(command, **kwargs):
        nonlocal called
        called = True
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="stopped")

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)
    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="baseline",
            task_id="case-1",
            candidate_id="candidate-1",
            workspace_root=str(tmp_path),
            task_input="task",
            task_text="task",
            skill_root=None,
            artifact_dir=str(tmp_path / "artifacts-shadow"),
            environment={
                "AWORLD_REPLAY_ENDPOINT_SERVICE_1": "http://127.0.0.1:25346"
            },
            framework_endpoint_bindings={
                "service_1": "http://127.0.0.1:25346"
            },
            evidence_policy_mode="required",
        )
    )

    assert called is True
    assert result.failure is not None
    assert result.failure.get("code") != "evidence_policy_v2_preflight_failed"


def test_run_owned_inferred_draft_is_not_used_as_baseline_skill_root(
    tmp_path: Path,
) -> None:
    target = SelfEvolveTargetRef(
        "skill",
        "remote-recovery-1234567890",
        str(
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "cli-test"
            / "draft_target"
            / "remote-recovery-1234567890"
            / "SKILL.md"
        ),
    )

    assert _infer_baseline_skill_root_from_target(target) is None
from aworld.self_evolve.replay_adaptation import ReplayAdapterBinding
from aworld.self_evolve.replay_capability import (
    FrozenReplayCapability,
    FrozenReplayFile,
    ReplayReadinessProbe,
    ReplayServiceSpec,
)
from aworld.self_evolve.runner import (
    _find_reusable_baseline_replay_dir,
    _replay_result_has_reusable_baseline,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    DatasetRecipe,
    SelfEvolveTargetRef,
)
from aworld.skills.compat_provider import build_compat_registry


def test_frozen_capability_restores_framework_response_record_identity() -> None:
    capability = _frozen_replay_capability_from_mapping(
        {
            "services": [
                {
                    "service_id": "browser-runtime",
                    "requirement_id": "browser-runtime-requirement",
                    "transport": "skill_runtime",
                    "response_fixture": "fixture.bin",
                    "readiness": {"kind": "tcp", "timeout_seconds": 1.0},
                    "protocol_probes": [
                        {
                            "kind": "tcp",
                            "timeout_seconds": 1.0,
                            "request_text": '{"method":"Runtime.evaluate"}',
                            "response_contains": "recorded-value",
                            "response_record_id": "response-record-stable",
                        }
                    ],
                }
            ]
        }
    )

    assert capability is not None
    assert (
        capability.services[0].protocol_probes[0].response_record_id
        == "response-record-stable"
    )


def test_task_failure_baseline_is_reusable_but_framework_failure_is_not() -> None:
    task_failure = ReplayVariantResult(
        variant_id="baseline",
        status=ReplayExecutionStatus.FAILED,
        trajectory=[{"action": {"content": "task failed"}}],
        metrics={"repetition_count": 1, "failed_repetition_count": 1},
        failure=ReplayFailureEvent(
            code="task_outcome_failed",
            owner=FailureOwner.TASK,
            stage=FailureStage.TASK_ROLLOUT,
            scope=FailureScope.MEMBER,
            repairable=False,
        ),
    )
    framework_failure = replace(
        task_failure,
        failure=ReplayFailureEvent(
            code="framework_capture_failed",
            owner=FailureOwner.FRAMEWORK,
            stage=FailureStage.TASK_ROLLOUT,
            scope=FailureScope.SHARED_RUN,
            repairable=False,
        ),
    )

    assert _baseline_replay_is_reusable(
        task_failure, requested_repetitions=1
    )
    assert not _baseline_replay_is_reusable(
        task_failure, requested_repetitions=2
    )
    assert _baseline_replay_is_reusable(
        replace(
            task_failure,
            metrics={"repetition_count": 2, "failed_repetition_count": 2},
        ),
        requested_repetitions=1,
    )
    assert not _baseline_replay_is_reusable(
        framework_failure, requested_repetitions=1
    )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-failure", input="recover task"),),
        recipe=DatasetRecipe(
            source={"kind": "trajectory_log"},
            split_seed="task-failure",
            splits={"train": ["task-failure"]},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-task-failure",
        task_id="task-failure",
        workspace_root="/tmp/replay",
        target=SelfEvolveTargetRef("skill", "demo"),
        candidate_id="candidate",
        overlay_skill_root="/tmp/replay/overlay",
        task_input="recover task",
    )
    replay_result = CandidateReplayResult(
        request=request,
        baseline=task_failure,
        candidate=ReplayVariantResult(
            variant_id="candidate",
            status=ReplayExecutionStatus.SUCCEEDED,
            trajectory=[{"action": {"content": "recovered"}}],
        ),
    )
    assert _replay_result_has_reusable_baseline(
        dataset=dataset,
        replay_result=replay_result,
    )


def _candidate(content: str, candidate_id: str = "cand-1") -> CandidateVariant:
    return CandidateVariant(
        candidate_id=candidate_id,
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content=content,
        rationale="test candidate",
        target_fingerprint="sha256:old",
    )


@pytest.mark.asyncio
async def test_replay_backend_reports_member_phase_progress(
    tmp_path: Path,
) -> None:
    async def fake_executor(
        request: ReplayExecutionRequest,
    ) -> ReplayExecutionResult:
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "progress-test", "case_count": 2},
            split_seed="seed",
            splits={
                "train": ["task-a", "task-b"],
                "validation": [],
                "held_out": [],
            },
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nCandidate.\n",
        candidate_id="progress-candidate",
    )
    request = CandidateReplayRequest(
        run_id="run-member-progress",
        task_id="task-a",
        workspace_root=str(tmp_path),
        target=candidate.target,
        candidate_id=candidate.candidate_id,
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input=dataset.cases[0].input,
        baseline_repetitions=1,
        candidate_repetitions=1,
    )
    events: list[dict[str, object]] = []

    await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
        progress_callback=events.append,
    )

    started = [
        event
        for event in events
        if event["event"] == "member_phase_started"
    ]
    completed = [
        event
        for event in events
        if event["event"] == "member_phase_completed"
    ]
    assert [
        (event["phase"], event["case_index"], event["case_id"])
        for event in started
    ] == [
        ("baseline", 1, "task-a"),
        ("candidate", 1, "task-a"),
        ("baseline", 2, "task-b"),
        ("candidate", 2, "task-b"),
    ]
    assert [event["status"] for event in completed] == [
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
    ]
    assert completed[0]["baseline_cache_status"] == "not_offered"


@pytest.mark.asyncio
async def test_legacy_replay_overlaps_adjacent_isolated_single_controls(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    second_control_started = asyncio.Event()

    async def fake_executor(
        request: ReplayExecutionRequest,
    ) -> ReplayExecutionResult:
        calls.append((request.task_id, request.variant_id))
        if request.task_id == "task-a" and request.variant_id == "baseline":
            await asyncio.wait_for(second_control_started.wait(), timeout=1.0)
        elif request.task_id == "task-b" and request.variant_id == "baseline":
            second_control_started.set()
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "isolated-overlap", "case_count": 2},
            split_seed="seed",
            splits={
                "train": ["task-a", "task-b"],
                "validation": [],
                "held_out": [],
            },
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nCandidate.\n",
        candidate_id="overlap-candidate",
    )
    adaptation = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=tmp_path,
        artifact_root=tmp_path / "adaptation",
    )
    request = build_replay_request(
        run_id="run-isolated-control-overlap",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        replay_adaptation=adaptation,
        baseline_repetitions=1,
        candidate_repetitions=1,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor,
        concurrency_policy=SelfEvolveConcurrencyPolicy(
            max_total_concurrency=2,
            replay_concurrency=2,
        ),
    ).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )

    assert result.succeeded
    assert calls[:2] == [
        ("task-a", "baseline"),
        ("task-b", "baseline"),
    ]


@pytest.mark.asyncio
async def test_replay_backend_resumes_completed_pairs_from_prior_checkpoint(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_executor(
        request: ReplayExecutionRequest,
    ) -> ReplayExecutionResult:
        calls.append((request.task_id, request.variant_id))
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "resume-test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a", "task-b"]},
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nCandidate.\n",
        candidate_id="resume-candidate",
    )
    common = {
        "task_id": "task-a",
        "workspace_root": str(tmp_path),
        "target": candidate.target,
        "candidate_id": candidate.candidate_id,
        "overlay_skill_root": str(tmp_path / "overlay"),
        "task_input": dataset.cases[0].input,
        "dataset_fingerprint": replay_dataset_fingerprint(dataset),
        "baseline_skill_fingerprint": candidate.target_fingerprint,
        "verified_candidate_package_fingerprint": "sha256:package",
        "baseline_repetitions": 1,
        "candidate_repetitions": 1,
    }
    backend = AWorldCliCandidateReplayBackend(executor=fake_executor)
    source_request = CandidateReplayRequest(run_id="source-run", **common)
    await backend.replay_candidate(
        source_request,
        candidate=candidate,
        dataset=dataset,
    )
    source_replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "source-run"
        / "replay"
        / candidate.candidate_id
    )
    checkpoint_path = (
        source_replay_dir / "members" / "paired_replay_checkpoint.json"
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint.update(
        {
            "baseline_phase_completed_case_ids": ["task-a"],
            "candidate_phase_completed_case_ids": ["task-a"],
            "comparable_pair_case_ids": ["task-a"],
            "reusable_baseline_case_ids": ["task-a"],
            "pending_case_ids": ["task-b"],
        }
    )
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    calls.clear()
    events: list[dict[str, object]] = []
    resumed = await backend.replay_candidate(
        CandidateReplayRequest(
            run_id="resumed-run",
            resume_replay_dir=str(source_replay_dir),
            **common,
        ),
        candidate=candidate,
        dataset=dataset,
        progress_callback=events.append,
    )

    assert calls == [
        ("task-b", "baseline"),
        ("task-b", candidate.candidate_id),
    ]
    assert [member.case_id for member in resumed.member_results] == [
        "task-a",
        "task-b",
    ]
    assert any(
        event["event"] == "checkpoint_pairs_reused"
        and event["reused_case_count"] == 1
        for event in events
    )
    resumed_checkpoint = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "resumed-run"
            / "replay"
            / candidate.candidate_id
            / "members"
            / "paired_replay_checkpoint.json"
        ).read_text(encoding="utf-8")
    )
    assert resumed_checkpoint["resumed_pair_case_ids"] == ["task-a"]
    assert resumed_checkpoint["pending_case_ids"] == []


def test_replay_checkpoint_rejects_evidence_contract_mode_drift(
    tmp_path: Path,
) -> None:
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nCandidate.\n",
        candidate_id="resume-contract-candidate",
    )
    legacy = CandidateReplayRequest(
        run_id="legacy-run",
        task_id="task-a",
        workspace_root=str(tmp_path),
        target=candidate.target,
        candidate_id=candidate.candidate_id,
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input="task",
        evidence_policy_mode="legacy",
    )

    assert _resume_root_is_compatible(
        replace(legacy, run_id="required-run", evidence_policy_mode="required"),
        legacy,
    ) is False


@pytest.mark.asyncio
async def test_replay_member_deadline_allows_supervised_teardown_grace(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def finishing_executor(
        request: ReplayExecutionRequest,
    ) -> ReplayExecutionResult:
        calls.append(request.variant_id)
        await asyncio.sleep(0.02)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-a", input="Replay task A"),),
        recipe=DatasetRecipe(
            source={"kind": "deadline-grace-test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-a"]},
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nCandidate.\n",
        candidate_id="deadline-grace-candidate",
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=finishing_executor
    ).replay_candidate(
        CandidateReplayRequest(
            run_id="run-member-deadline-grace",
            task_id="task-a",
            workspace_root=str(tmp_path),
            target=candidate.target,
            candidate_id=candidate.candidate_id,
            overlay_skill_root=str(tmp_path / "overlay"),
            task_input=dataset.cases[0].input,
            timeout_seconds=0.01,
        ),
        candidate=candidate,
        dataset=dataset,
    )

    assert result.succeeded is True
    assert calls == ["baseline", candidate.candidate_id]
    assert all(
        member.baseline.failure is None and member.candidate.failure is None
        for member in result.member_results
    )


@pytest.mark.asyncio
async def test_replay_member_deadline_stops_invalid_control_frontier(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    async def slow_executor(
        request: ReplayExecutionRequest,
    ) -> ReplayExecutionResult:
        calls.append((request.task_id, request.variant_id))
        await asyncio.sleep(1)
        return ReplayExecutionResult(status="succeeded", trajectory=[])

    dataset = SelfEvolveDataset(
        cases=tuple(
            EvalCase(case_id=f"task-{suffix}", input=f"Replay task {suffix}")
            for suffix in ("a", "b", "c")
        ),
        recipe=DatasetRecipe(
            source={"kind": "deadline-test", "case_count": 3},
            split_seed="seed",
            splits={
                "train": ["task-a", "task-b", "task-c"],
                "validation": [],
                "held_out": [],
            },
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nCandidate.\n",
        candidate_id="deadline-candidate",
    )
    request = CandidateReplayRequest(
        run_id="run-member-deadline",
        task_id="task-a",
        workspace_root=str(tmp_path),
        target=candidate.target,
        candidate_id=candidate.candidate_id,
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input=dataset.cases[0].input,
        timeout_seconds=0.01,
        invalid_control_patience=2,
        measurement_early_stop_enabled=True,
    )
    events: list[dict[str, object]] = []

    started_at = time.monotonic()
    result = await AWorldCliCandidateReplayBackend(
        executor=slow_executor
    ).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
        progress_callback=events.append,
    )
    assert time.monotonic() - started_at < 0.5

    assert len(calls) <= 2
    assert all(variant_id == "baseline" for _case_id, variant_id in calls)
    assert {case_id for case_id, _variant_id in calls} <= {"task-a", "task-b"}
    assert [
        member.baseline.status for member in result.member_results
    ] == [
        ReplayExecutionStatus.FAILED,
        ReplayExecutionStatus.FAILED,
        ReplayExecutionStatus.BLOCKED,
    ]
    assert all(
        member.candidate.status is ReplayExecutionStatus.BLOCKED
        for member in result.member_results
    )
    assert result.member_results[0].baseline.failure is not None
    assert (
        result.member_results[0].baseline.failure.code
        == "replay_member_phase_timeout"
    )
    stop_events = [
        event
        for event in events
        if event.get("event") == "measurement_stop_triggered"
    ]
    assert len(stop_events) == 1
    assert stop_events[0]["trigger"] == "repeated_control_invalidity"
    assert stop_events[0]["unused_case_count"] == 1
    manifest = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-member-deadline"
            / "replay"
            / "deadline-candidate"
            / "members"
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["measurement_stop"]["resume_safe"] is True
    checkpoint = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-member-deadline"
            / "replay"
            / "deadline-candidate"
            / "members"
            / "paired_replay_checkpoint.json"
        ).read_text(encoding="utf-8")
    )
    assert checkpoint["baseline_phase_completed_case_ids"] == ["task-a", "task-b"]
    assert checkpoint["candidate_phase_completed_case_ids"] == []
    assert checkpoint["pending_case_ids"] == ["task-a", "task-b", "task-c"]

    calls.clear()
    shadow_events: list[dict[str, object]] = []
    shadow_started_at = time.monotonic()
    shadow_result = await AWorldCliCandidateReplayBackend(
        executor=slow_executor
    ).replay_candidate(
        replace(
            request,
            run_id="run-member-deadline-shadow",
            measurement_early_stop_enabled=False,
        ),
        candidate=candidate,
        dataset=dataset,
        progress_callback=shadow_events.append,
    )
    assert time.monotonic() - shadow_started_at < 0.5

    assert calls == [
        ("task-a", "baseline"),
        ("task-b", "baseline"),
        ("task-c", "baseline"),
    ]
    assert all(
        member.baseline.status is ReplayExecutionStatus.FAILED
        and member.candidate.status is ReplayExecutionStatus.BLOCKED
        for member in shadow_result.member_results
    )
    assert not any(
        event.get("event") == "measurement_stop_triggered"
        for event in shadow_events
    )


@pytest.mark.asyncio
async def test_authoritative_replay_stops_after_first_incomparable_candidate_member(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    async def executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append((request.task_id, request.variant_id))
        if request.variant_id.startswith("candidate"):
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={
                    "outcome": "candidate_failure",
                    "reason": "runtime policy counterexample",
                },
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": "baseline"}}],
        )

    dataset = SelfEvolveDataset(
        cases=tuple(
            EvalCase(case_id=f"case-{index}", input=f"task {index}")
            for index in range(1, 4)
        ),
        recipe=DatasetRecipe(
            source={"kind": "authoritative-stop"},
            split_seed="seed",
            splits={"train": ["case-1", "case-2", "case-3"]},
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nCandidate.\n",
        candidate_id="candidate",
    )
    request = build_replay_request(
        run_id="run-authoritative-stop",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        stop_on_incomparable_member=True,
    )
    events: list[dict[str, object]] = []

    result = await AWorldCliCandidateReplayBackend(
        executor=executor
    ).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
        progress_callback=events.append,
    )

    assert calls == [
        ("case-1", "baseline"),
        ("case-1", "candidate"),
    ]
    assert result.member_results is not None
    assert result.member_results[0].candidate.status is ReplayExecutionStatus.FAILED
    assert all(
        member.candidate.status is ReplayExecutionStatus.BLOCKED
        for member in result.member_results[1:]
    )
    stop_events = [
        event
        for event in events
        if event.get("event") == "authoritative_stop_triggered"
    ]
    assert len(stop_events) == 1
    assert stop_events[0]["unused_case_count"] == 2
    manifest = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-authoritative-stop"
            / "replay"
            / "candidate"
            / "members"
            / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["authoritative_stop"]["trigger"] == (
        "incomparable_candidate_member"
    )


@pytest.mark.asyncio
async def test_authoritative_replay_attributes_invalid_control_stop_to_framework(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    async def executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append((request.task_id, request.variant_id))
        return ReplayExecutionResult(
            status="failed",
            trajectory=[],
            failure={
                "outcome": "infrastructure_failure",
                "reason": "control environment unavailable",
            },
        )

    dataset = SelfEvolveDataset(
        cases=tuple(
            EvalCase(case_id=f"case-{index}", input=f"task {index}")
            for index in range(1, 4)
        ),
        recipe=DatasetRecipe(
            source={"kind": "invalid-control-stop"},
            split_seed="seed",
            splits={"train": ["case-1"]},
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nCandidate.\n",
        candidate_id="candidate",
    )
    request = build_replay_request(
        run_id="run-invalid-control-stop",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        stop_on_incomparable_member=True,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=executor
    ).replay_candidate(request, candidate=candidate, dataset=dataset)

    assert calls == [("case-1", "baseline")]
    assert result.member_results is not None
    assert all(
        member.baseline.status is ReplayExecutionStatus.BLOCKED
        for member in result.member_results[1:]
    )
    assert all(
        member.candidate.status is ReplayExecutionStatus.BLOCKED
        for member in result.member_results
    )
    blocked_by = result.member_results[0].candidate.blocked_by
    assert len(blocked_by) == 1
    assert blocked_by[0].code == "authoritative_replay_invalid_control"
    assert blocked_by[0].owner is FailureOwner.FRAMEWORK
    assert blocked_by[0].scope is FailureScope.SHARED_RUN


@pytest.mark.asyncio
async def test_current_run_completed_replay_is_available_for_baseline_reuse(
    tmp_path: Path,
) -> None:
    async def fake_executor(
        request: ReplayExecutionRequest,
    ) -> ReplayExecutionResult:
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-a", input="Replay task A"),),
        recipe=DatasetRecipe(
            source={"kind": "baseline-cache-test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-a"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nCandidate.\n",
        candidate_id="cached-candidate",
    )
    adaptation = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=tmp_path,
        artifact_root=tmp_path / "adaptation",
    )
    request = build_replay_request(
        run_id="run-current-cache",
        workspace_root=str(tmp_path),
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=str(tmp_path / "overlay"),
        dataset=dataset,
        baseline_repetitions=1,
        candidate_repetitions=1,
        replay_adaptation=adaptation,
    )
    provenance = {
        "baseline_skill_fingerprint": request.baseline_skill_fingerprint,
        "dataset_fingerprint": request.dataset_fingerprint,
        "adaptation_fingerprint": request.adaptation_fingerprint,
        "workspace_seed_fingerprint": request.workspace_seed_fingerprint,
        "support_fingerprint": request.support_fingerprint,
        "timeout_envelope_fingerprint": (
            request.timeout_envelope_fingerprint
        ),
    }
    await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )

    reusable = _find_reusable_baseline_replay_dir(
        store=FilesystemSelfEvolveStore(tmp_path),
        run_id=request.run_id,
        target=candidate.target,
        dataset=dataset,
        baseline_repetitions=1,
        **provenance,
    )

    assert reusable == str(
        tmp_path
        / ".aworld"
        / "self_evolve"
        / request.run_id
        / "replay"
        / candidate.candidate_id
        / "members"
    )


def test_replay_failure_event_round_trip_preserves_orthogonal_semantics() -> None:
    event = ReplayFailureEvent(
        event_id="replay-event-contract",
        code="capability_start_failed",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.CANDIDATE,
        repairable=True,
        category="replay_capability",
        summary="candidate capability could not start",
        diagnostics={"attempt": 1},
        artifact_refs=("artifact/runtime.log",),
    )

    loaded = ReplayFailureEvent.from_dict(event.to_dict())

    assert loaded.to_dict() == event.to_dict()
    assert loaded.owner is FailureOwner.CANDIDATE
    assert loaded.stage is FailureStage.CAPABILITY_PREFLIGHT
    assert loaded.scope is FailureScope.CANDIDATE
    with pytest.raises(ValueError, match="shared_run failures"):
        ReplayFailureEvent(
            code="invalid_shared_scope",
            owner=FailureOwner.CANDIDATE,
            stage=FailureStage.CAPABILITY_PREFLIGHT,
            scope=FailureScope.SHARED_RUN,
            repairable=True,
        )


def test_unknown_legacy_failure_never_gains_shared_run_scope() -> None:
    event = ReplayFailureEvent.from_legacy_mapping(
        {"reason": "an old artifact did not record machine failure fields"}
    )

    assert event.owner is FailureOwner.FRAMEWORK
    assert event.stage is FailureStage.LEGACY_IMPORT
    assert event.scope is FailureScope.CANDIDATE
    assert event.source is FailureEventSource.LEGACY_UNKNOWN
    assert event.code == "legacy_unclassified_failure"


def test_trajectory_stdout_parser_preserves_unicode_separators_in_large_json() -> None:
    trajectory = [
        {
            "action": {
                "content": (
                    ("browser output " * 90_000)
                    + "mojibake\u0085content\u2028still in the same JSON record"
                )
            }
        }
    ]
    payload = {
        "trajectory": trajectory,
        "trajectory_capture_mode": "task_response",
    }
    stdout = "diagnostic output\n" + json.dumps(payload, ensure_ascii=False) + "\n"

    assert len(stdout.splitlines()) > len(stdout.split("\n"))
    assert _extract_trajectory_payload_from_stdout(stdout) == payload


def test_pair_coverage_counts_framework_failure_in_physical_repetition(
    tmp_path: Path,
) -> None:
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input="Replay this task"),),
        recipe=DatasetRecipe(
            source={"kind": "test"},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-physical-failure-attribution",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        baseline_repetitions=2,
        candidate_repetitions=3,
    )
    succeeded = ReplayVariantResult(
        variant_id="candidate-success",
        status=ReplayExecutionStatus.SUCCEEDED,
        trajectory=[{"action": {"content": "completed"}}],
    )
    capture_failure = ReplayVariantResult(
        variant_id="candidate-capture-failure",
        status=ReplayExecutionStatus.FAILED,
        trajectory=[],
        failure=ReplayFailureEvent(
            code="trajectory_capture_unavailable",
            owner=FailureOwner.FRAMEWORK,
            stage=FailureStage.EVALUATION,
            scope=FailureScope.MEMBER,
            repairable=True,
            summary="trajectory capture was unavailable",
        ),
    )
    baseline = replace(
        succeeded,
        variant_id="baseline",
        metrics={
            "repetition_count": 2,
            "successful_repetition_count": 2,
            "failed_repetition_count": 0,
        },
        repetition_results=(succeeded, succeeded),
    )
    candidate_result = replace(
        succeeded,
        variant_id=candidate.candidate_id,
        metrics={
            "repetition_count": 3,
            "successful_repetition_count": 2,
            "failed_repetition_count": 1,
        },
        repetition_results=(succeeded, succeeded, capture_failure),
    )
    replay_result = CandidateReplayResult(
        request=request,
        baseline=baseline,
        candidate=candidate_result,
        member_results=(
            CandidateReplayMemberResult(
                case_id="task-1",
                request=request,
                baseline=baseline,
                candidate=candidate_result,
            ),
        ),
    )

    coverage = candidate_replay_pair_coverage(
        dataset=dataset,
        replay_result=replay_result,
    )

    assert coverage["framework_owned_failure_count"] == 1
    assert coverage["candidate_owned_failure_count"] == 0


def test_non_native_failure_event_cannot_claim_shared_run_scope() -> None:
    with pytest.raises(ValueError, match="native failure events"):
        ReplayFailureEvent(
            code="legacy_shared_failure",
            owner=FailureOwner.INFRASTRUCTURE,
            stage=FailureStage.LEGACY_IMPORT,
            scope=FailureScope.SHARED_RUN,
            repairable=False,
            source=FailureEventSource.LEGACY_INFERRED,
        )

    payload = {
        "schema_version": "aworld.self_evolve.replay_failure.v2",
        "event_id": "legacy-shared-event",
        "code": "legacy_shared_failure",
        "owner": "infrastructure",
        "stage": "legacy_import",
        "scope": "shared_run",
        "repairable": False,
        "source": "legacy_inferred",
    }
    with pytest.raises(ValueError, match="native failure events"):
        ReplayFailureEvent.from_dict(payload)


def test_v2_failure_event_requires_explicit_source() -> None:
    event = ReplayFailureEvent(
        code="task_failed",
        owner=FailureOwner.TASK,
        stage=FailureStage.TASK_ROLLOUT,
        scope=FailureScope.MEMBER,
        repairable=False,
    )
    payload = event.to_dict()
    payload.pop("source")

    with pytest.raises(ValueError, match="source is required"):
        ReplayFailureEvent.from_dict(payload)


def test_lifecycle_loader_rejects_v2_failure_without_source(tmp_path: Path) -> None:
    variant_dir = tmp_path / "missing-source"
    variant_dir.mkdir()
    event = ReplayFailureEvent(
        code="task_failed",
        owner=FailureOwner.TASK,
        stage=FailureStage.TASK_ROLLOUT,
        scope=FailureScope.MEMBER,
        repairable=False,
    ).to_dict()
    event.pop("source")
    (variant_dir / "lifecycle.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld.self_evolve.replay_lifecycle.v2",
                "variant_id": "candidate",
                "status": "failed",
                "failure": event,
                "blocked_by": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="source is required"):
        _load_variant_result_from_dir(variant_dir, base_variant_id="candidate")


def test_lifecycle_loader_rejects_non_native_shared_run_failure(tmp_path: Path) -> None:
    variant_dir = tmp_path / "invalid-lifecycle"
    variant_dir.mkdir()
    (variant_dir / "lifecycle.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld.self_evolve.replay_lifecycle.v2",
                "variant_id": "candidate",
                "status": "failed",
                "failure": {
                    "schema_version": "aworld.self_evolve.replay_failure.v2",
                    "event_id": "legacy-shared-event",
                    "code": "legacy_shared_failure",
                    "owner": "infrastructure",
                    "stage": "legacy_import",
                    "scope": "shared_run",
                    "repairable": False,
                    "source": "legacy_inferred",
                },
                "blocked_by": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="native failure events"):
        _load_variant_result_from_dir(variant_dir, base_variant_id="candidate")


def test_replay_variant_status_rejects_impossible_lifecycle_combinations() -> None:
    event = ReplayFailureEvent(
        code="execution_failed",
        owner=FailureOwner.TASK,
        stage=FailureStage.TASK_ROLLOUT,
        scope=FailureScope.MEMBER,
        repairable=False,
    )

    with pytest.raises(ValueError, match="failed replay variant requires"):
        ReplayVariantResult(
            variant_id="candidate",
            status=ReplayExecutionStatus.FAILED,
            trajectory=[],
        )
    with pytest.raises(ValueError, match="blocked replay variant requires"):
        ReplayVariantResult(
            variant_id="candidate",
            status=ReplayExecutionStatus.BLOCKED,
            trajectory=[],
        )
    with pytest.raises(ValueError, match="blocked replay variant cannot"):
        ReplayVariantResult(
            variant_id="candidate",
            status=ReplayExecutionStatus.BLOCKED,
            trajectory=[{"action": {}}],
            blocked_by=(event,),
        )
    with pytest.raises(ValueError, match="executed replay variant cannot have blocked_by"):
        ReplayVariantResult(
            variant_id="candidate",
            status=ReplayExecutionStatus.FAILED,
            trajectory=[],
            failure=event,
            blocked_by=(event,),
        )
    with pytest.raises(ValueError, match="unexecuted replay variant cannot contain execution artifacts"):
        ReplayVariantResult(
            variant_id="candidate",
            status=ReplayExecutionStatus.BLOCKED,
            trajectory=[],
            stdout_path="stale.stdout",
            blocked_by=(event,),
        )
    with pytest.raises(ValueError, match="unexecuted replay variant cannot contain execution artifacts"):
        ReplayVariantResult(
            variant_id="candidate",
            status=ReplayExecutionStatus.NOT_RUN,
            trajectory=[],
            repetition_results=(
                ReplayVariantResult(
                    variant_id="candidate-1",
                    status=ReplayExecutionStatus.SUCCEEDED,
                    trajectory=[{"action": {"content": "ran"}}],
                ),
            ),
        )


def test_not_run_lifecycle_is_materialized_and_loaded_without_failure(
    tmp_path: Path,
) -> None:
    variant_dir = tmp_path / "not-run"
    _persist_variant_lifecycle(
        variant_dir,
        ReplayVariantResult(
            variant_id="candidate",
            status=ReplayExecutionStatus.NOT_RUN,
            trajectory=[],
        ),
    )

    loaded = _load_variant_result_from_dir(
        variant_dir,
        base_variant_id="candidate",
    )

    assert loaded.status is ReplayExecutionStatus.NOT_RUN
    assert loaded.failure is None
    assert loaded.blocked_by == ()


@pytest.mark.parametrize("status", (ReplayExecutionStatus.BLOCKED, ReplayExecutionStatus.NOT_RUN))
def test_persist_unexecuted_lifecycle_removes_stale_stream_artifacts(
    tmp_path: Path,
    status: ReplayExecutionStatus,
) -> None:
    variant_dir = tmp_path / status.value
    variant_dir.mkdir()
    (variant_dir / "stdout.txt").write_text("stale stdout", encoding="utf-8")
    (variant_dir / "stderr.txt").write_text("stale stderr", encoding="utf-8")
    blocker = ReplayFailureEvent(
        code="candidate_preflight_failed",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.CANDIDATE,
        repairable=True,
    )
    result = ReplayVariantResult(
        variant_id="candidate",
        status=status,
        trajectory=[],
        blocked_by=(blocker,) if status is ReplayExecutionStatus.BLOCKED else (),
    )

    _persist_variant_lifecycle(variant_dir, result)

    assert not (variant_dir / "stdout.txt").exists()
    assert not (variant_dir / "stderr.txt").exists()


def test_mixed_member_aggregate_has_consistent_unexecuted_lifecycle(
    tmp_path: Path,
) -> None:
    request = CandidateReplayRequest(
        run_id="run-mixed",
        task_id="root",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="candidate",
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input="root",
    )
    succeeded = ReplayVariantResult(
        variant_id="candidate",
        status=ReplayExecutionStatus.SUCCEEDED,
        trajectory=[{"action": {"content": "executed"}}],
        stdout_path=str(tmp_path / "executed.stdout"),
    )
    blocker = ReplayFailureEvent(
        code="candidate_preflight_failed",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.CANDIDATE,
        repairable=True,
    )
    blocked = ReplayVariantResult(
        variant_id="candidate",
        status=ReplayExecutionStatus.BLOCKED,
        trajectory=[],
        blocked_by=(blocker,),
    )
    members = (
        CandidateReplayMemberResult("one", replace(request, task_id="one"), succeeded, succeeded),
        CandidateReplayMemberResult("two", replace(request, task_id="two"), blocked, blocked),
    )

    aggregate = _aggregate_member_variant_results(
        base_variant_id="candidate",
        members=members,
        select=lambda member: member.candidate,
        artifact_dir=tmp_path / "aggregate",
        persist=False,
    )

    assert aggregate.status is ReplayExecutionStatus.BLOCKED
    assert aggregate.metrics["successful_member_count"] == 1
    assert aggregate.metrics["blocked_member_count"] == 1
    assert aggregate.trajectory == []
    assert aggregate.stdout_path is None
    assert aggregate.stderr_path is None
    assert aggregate.repetition_results == ()


@pytest.mark.parametrize("case_count", (1, 3))
def test_normalized_replay_members_are_dataset_ordered_and_detect_contract_gaps(
    tmp_path: Path,
    case_count: int,
) -> None:
    case_ids = tuple(f"member-{index}" for index in range(case_count))
    dataset = SelfEvolveDataset(
        cases=tuple(EvalCase(case_id=case_id, input={"index": index}) for index, case_id in enumerate(case_ids)),
        recipe=DatasetRecipe(
            source={"kind": "typed_lifecycle_contract"},
            split_seed="seed",
            splits={"train": list(case_ids), "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    root_request = build_replay_request(
        run_id="run-normalized-members",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
    )
    succeeded = ReplayVariantResult(
        variant_id="baseline",
        status=ReplayExecutionStatus.SUCCEEDED,
        trajectory=[{"action": {"content": "ok"}}],
    )
    members = tuple(
        CandidateReplayMemberResult(
            case_id=case_id,
            request=replace(
                root_request,
                task_id=case_id,
                task_input=next(
                    case.input for case in dataset.cases if case.case_id == case_id
                ),
            ),
            baseline=succeeded,
            candidate=replace(succeeded, variant_id=candidate.candidate_id),
        )
        for case_id in reversed(case_ids)
    )
    replay_result = CandidateReplayResult(
        request=root_request,
        baseline=succeeded,
        candidate=replace(succeeded, variant_id=candidate.candidate_id),
        member_results=members,
    )

    normalized = normalize_replay_members(dataset=dataset, replay_result=replay_result)

    assert normalized.valid
    assert tuple(member.case_id for member in normalized.members) == case_ids

    malformed_members = members[:-1]
    if malformed_members:
        malformed_members = (*malformed_members, malformed_members[0])
    malformed = replace(replay_result, member_results=malformed_members)
    normalized_malformed = normalize_replay_members(
        dataset=dataset,
        replay_result=malformed,
    )
    assert not normalized_malformed.valid
    assert normalized_malformed.missing_case_ids
    if malformed_members:
        assert normalized_malformed.duplicate_case_ids
    assert all(
        event.owner is FailureOwner.FRAMEWORK
        and event.stage is FailureStage.RESULT_NORMALIZATION
        and event.scope is FailureScope.CANDIDATE
        for event in normalized_malformed.failure_events
    )


@pytest.mark.parametrize("case_count", (1, 3))
@pytest.mark.parametrize(
    "contract_gap",
    ("duplicate", "unexpected", "request_mismatch", "explicit_empty"),
)
def test_structurally_invalid_members_are_never_comparable_without_adaptation(
    tmp_path: Path,
    case_count: int,
    contract_gap: str,
) -> None:
    case_ids = tuple(f"case-{index}" for index in range(case_count))
    dataset = SelfEvolveDataset(
        cases=tuple(
            EvalCase(case_id=case_id, input={"index": index})
            for index, case_id in enumerate(case_ids)
        ),
        recipe=DatasetRecipe(
            source={"kind": "normalization_adversarial"},
            split_seed="seed",
            splits={"train": list(case_ids), "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    root_request = build_replay_request(
        run_id="run-normalization-adversarial",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        baseline_repetitions=1,
        candidate_repetitions=1,
    )
    succeeded = ReplayVariantResult(
        variant_id="baseline",
        status=ReplayExecutionStatus.SUCCEEDED,
        trajectory=[{"action": {"content": "ok"}}],
    )
    members = tuple(
        CandidateReplayMemberResult(
            case_id=case_id,
            request=replace(
                root_request,
                task_id=case_id,
                task_input=dataset.cases[index].input,
                baseline_repetitions=1,
                candidate_repetitions=1,
            ),
            baseline=succeeded,
            candidate=replace(succeeded, variant_id=candidate.candidate_id),
        )
        for index, case_id in enumerate(case_ids)
    )
    if contract_gap == "duplicate":
        malformed_members = (*members, members[0])
    elif contract_gap == "unexpected":
        malformed_members = (
            *members,
            replace(
                members[0],
                case_id="outside-dataset",
                request=replace(members[0].request, task_id="outside-dataset"),
            ),
        )
    elif contract_gap == "request_mismatch":
        malformed_members = (
            replace(
                members[0],
                request=replace(members[0].request, run_id="different-run"),
            ),
            *members[1:],
        )
    else:
        malformed_members = ()
    replay_result = CandidateReplayResult(
        request=root_request,
        baseline=succeeded,
        candidate=replace(succeeded, variant_id=candidate.candidate_id),
        member_results=malformed_members,
    )

    normalized = normalize_replay_members(dataset=dataset, replay_result=replay_result)

    assert root_request.adaptation_fingerprint is None
    assert not normalized.valid
    assert candidate_replay_is_comparable(
        dataset=dataset,
        replay_result=replay_result,
    ) is False
    coverage = candidate_replay_pair_coverage(
        dataset=dataset,
        replay_result=replay_result,
        normalized=normalized,
    )
    assert coverage["normalization_failure_count"] > 0
    assert coverage["member_count"] == case_count
    assert (
        coverage["comparable_pair_count"] + coverage["incomparable_pair_count"]
        == case_count
    )


@pytest.mark.parametrize("case_count", (1, 3))
@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    (
        ("task_input", {"tampered": True}),
        ("task_input_fingerprint", "sha256:tampered"),
        ("baseline_replay_dir", "/tmp/tampered-baseline"),
    ),
)
def test_member_derived_request_fields_fail_closed_for_comparison_and_reuse(
    tmp_path: Path,
    case_count: int,
    field_name: str,
    tampered_value: object,
) -> None:
    case_ids = tuple(f"derived-{index}" for index in range(case_count))
    dataset = SelfEvolveDataset(
        cases=tuple(
            EvalCase(case_id=case_id, input={"index": index})
            for index, case_id in enumerate(case_ids)
        ),
        recipe=DatasetRecipe(
            source={"kind": "member_derived_identity"},
            split_seed="seed",
            splits={"train": list(case_ids), "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    root_request = build_replay_request(
        run_id="run-derived-identity",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        baseline_repetitions=1,
        candidate_repetitions=1,
    )
    succeeded = ReplayVariantResult(
        variant_id="baseline",
        status=ReplayExecutionStatus.SUCCEEDED,
        trajectory=[{"action": {"content": "ok"}}],
    )
    members = tuple(
        CandidateReplayMemberResult(
            case_id=case.case_id,
            request=replace(
                root_request,
                task_id=case.case_id,
                task_input=(tampered_value if index == 0 and field_name == "task_input" else case.input),
                task_input_fingerprint=(
                    tampered_value
                    if index == 0 and field_name == "task_input_fingerprint"
                    else root_request.task_input_fingerprint
                ),
                baseline_replay_dir=(
                    tampered_value
                    if index == 0 and field_name == "baseline_replay_dir"
                    else None
                ),
                baseline_repetitions=1,
                candidate_repetitions=1,
            ),
            baseline=succeeded,
            candidate=replace(succeeded, variant_id=candidate.candidate_id),
        )
        for index, case in enumerate(dataset.cases)
    )
    replay_result = CandidateReplayResult(
        request=root_request,
        baseline=succeeded,
        candidate=replace(succeeded, variant_id=candidate.candidate_id),
        member_results=members,
    )

    normalized = normalize_replay_members(dataset=dataset, replay_result=replay_result)
    coverage = candidate_replay_pair_coverage(
        dataset=dataset,
        replay_result=replay_result,
        normalized=normalized,
    )

    assert normalized.request_mismatch_case_ids == (case_ids[0],)
    mismatch = next(
        event
        for event in normalized.failure_events
        if event.code == "replay_request_member_mismatch"
    )
    assert field_name in mismatch.diagnostics["fields"]
    assert candidate_replay_is_comparable(
        dataset=dataset,
        replay_result=replay_result,
        normalized=normalized,
    ) is False
    assert _replay_result_has_reusable_baseline(
        dataset=dataset,
        replay_result=replay_result,
    ) is False
    assert coverage["member_count"] == case_count
    assert coverage["comparable_pair_count"] + coverage["incomparable_pair_count"] == case_count


@pytest.mark.parametrize(
    "occurrence_order",
    ("bad_bad", "bad_good", "good_bad"),
)
def test_duplicate_member_occurrences_are_order_independent_and_never_selected(
    tmp_path: Path,
    occurrence_order: str,
) -> None:
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="case-a", input={"expected": True}),),
        recipe=DatasetRecipe(
            source={"kind": "duplicate_occurrence_contract"},
            split_seed="seed",
            splits={"train": ["case-a"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    root_request = build_replay_request(
        run_id="run-duplicate-occurrence",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
    )
    succeeded = ReplayVariantResult(
        variant_id="baseline",
        status=ReplayExecutionStatus.SUCCEEDED,
        trajectory=[{"action": {"content": "ok"}}],
    )

    def occurrence(**request_updates: object) -> CandidateReplayMemberResult:
        derived_request_values = {
            "task_id": "case-a",
            "task_input": dataset.cases[0].input,
            **request_updates,
        }
        return CandidateReplayMemberResult(
            case_id="case-a",
            request=replace(root_request, **derived_request_values),
            baseline=succeeded,
            candidate=replace(succeeded, variant_id=candidate.candidate_id),
        )

    good = occurrence()
    bad_input = occurrence(task_input={"bad": "input"})
    bad_fingerprint = occurrence(task_input_fingerprint="sha256:bad")
    occurrence_pairs = {
        "bad_bad": (bad_input, bad_fingerprint),
        "bad_good": (bad_input, good),
        "good_bad": (good, bad_input),
    }
    replay_result = CandidateReplayResult(
        request=root_request,
        baseline=succeeded,
        candidate=replace(succeeded, variant_id=candidate.candidate_id),
        member_results=occurrence_pairs[occurrence_order],
    )

    normalized = normalize_replay_members(dataset=dataset, replay_result=replay_result)
    coverage = candidate_replay_pair_coverage(
        dataset=dataset,
        replay_result=replay_result,
        normalized=normalized,
    )

    assert normalized.members == ()
    assert normalized.missing_case_ids == ()
    assert normalized.duplicate_case_ids == ("case-a",)
    assert normalized.request_mismatch_case_ids == ("case-a",)
    mismatch = next(
        event
        for event in normalized.failure_events
        if event.code == "replay_request_member_mismatch"
    )
    expected_fields = (
        {"task_input", "task_input_fingerprint"}
        if occurrence_order == "bad_bad"
        else {"task_input"}
    )
    assert set(mismatch.diagnostics["fields"]) == expected_fields
    assert coverage["member_count"] == 1
    assert coverage["comparable_pair_count"] == 0
    assert coverage["incomparable_pair_count"] == 1
    assert coverage["duplicate_member_count"] == 1
    assert coverage["request_mismatch_count"] == 1


def test_member_request_per_member_repetitions_are_part_of_normalization_contract(
    tmp_path: Path,
) -> None:
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="one", input="one"),
            EvalCase(case_id="two", input="two"),
            EvalCase(case_id="three", input="three"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "repetition_distribution_contract"},
            split_seed="seed",
            splits={"train": ["one", "two", "three"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    root_request = build_replay_request(
        run_id="run-repetition-contract",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        baseline_repetitions=6,
        candidate_repetitions=9,
    )
    succeeded = ReplayVariantResult(
        variant_id="baseline",
        status=ReplayExecutionStatus.SUCCEEDED,
        trajectory=[{"action": {"content": "ok"}}],
    )
    members = tuple(
        CandidateReplayMemberResult(
            case_id=case.case_id,
            request=replace(
                root_request,
                task_id=case.case_id,
                task_input=case.input,
                baseline_repetitions=(99 if index == 0 else 6),
                candidate_repetitions=9,
            ),
            baseline=succeeded,
            candidate=replace(succeeded, variant_id=candidate.candidate_id),
        )
        for index, case in enumerate(dataset.cases)
    )
    replay_result = CandidateReplayResult(
        request=root_request,
        baseline=succeeded,
        candidate=replace(succeeded, variant_id=candidate.candidate_id),
        member_results=members,
    )

    normalized = normalize_replay_members(dataset=dataset, replay_result=replay_result)

    assert not normalized.valid
    mismatch = next(
        event
        for event in normalized.failure_events
        if event.code == "replay_request_member_mismatch"
    )
    assert mismatch.diagnostics["case_ids"] == ["one"]
    assert "baseline_repetitions" in mismatch.diagnostics["fields"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case_count", (1, 3))
async def test_replay_lifecycle_v3_round_trip_materializes_blocked_members(
    tmp_path: Path,
    case_count: int,
) -> None:
    case_ids = tuple(f"member-{index}" for index in range(case_count))
    dataset = SelfEvolveDataset(
        cases=tuple(EvalCase(case_id=case_id, input=case_id) for case_id in case_ids),
        recipe=DatasetRecipe(
            source={"kind": "lifecycle_round_trip"},
            split_seed="seed",
            splits={"train": list(case_ids), "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\n",
        candidate_id="candidate-lifecycle",
    )

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        if request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={
                    "type": "ReplayServiceProtocolError",
                    "outcome": "candidate_failure",
                    "reason": "synthetic preflight failure",
                },
            )
        raise AssertionError("candidate execution must be blocked")

    request = build_replay_request(
        run_id=f"run-lifecycle-round-trip-{case_count}",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
    )
    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )
    replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / request.run_id
        / "replay"
        / candidate.candidate_id
    )
    loaded = load_candidate_replay_result(replay_dir)

    assert loaded.member_results is not None
    assert tuple(member.case_id for member in loaded.member_results) == case_ids
    assert all(
        member.candidate.status is ReplayExecutionStatus.BLOCKED
        for member in loaded.member_results
    )
    cause_id = result.member_results[0].baseline.failure.event_id
    assert all(
        member.candidate.blocked_by[0].event_id == cause_id
        for member in loaded.member_results
    )


@pytest.mark.asyncio
async def test_replay_artifact_namespace_isolated_under_run_root(
    tmp_path: Path,
) -> None:
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="regression-case", input="verify stability"),),
        recipe=DatasetRecipe(
            source={"kind": "jsonl"},
            split_seed="seed",
            splits={
                "train": ["regression-case"],
                "validation": [],
                "held_out": [],
            },
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\n",
        candidate_id="candidate-regression",
    )

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    request = build_replay_request(
        run_id="run-regression-namespace",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        artifact_namespace="regression/suite-one",
    )
    await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )

    replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / request.run_id
        / "regression"
        / "suite-one"
        / "replay"
        / candidate.candidate_id
    )
    assert (replay_dir / "request.json").is_file()
    assert not (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / request.run_id
        / "replay"
        / candidate.candidate_id
    ).exists()

    unsafe_request = replace(request, artifact_namespace="../escape")
    with pytest.raises(ValueError, match="invalid replay artifact namespace"):
        await AWorldCliCandidateReplayBackend(
            executor=fake_executor
        ).replay_candidate(
            unsafe_request,
            candidate=candidate,
            dataset=dataset,
        )
    manifest = json.loads((replay_dir / "members" / "manifest.json").read_text())
    assert manifest["schema_version"] == "aworld.self_evolve.member_replay.v3"
    assert manifest["repetition_semantics"] == "per_member_v3"
    assert all(
        (
            replay_dir
            / "members"
            / item["path"]
            / candidate.candidate_id
            / "lifecycle.json"
        ).exists()
        for item in manifest["members"]
    )
    assert all(
        json.loads(path.read_text())["schema_version"]
        == "aworld.self_evolve.replay_lifecycle.v3"
        and json.loads(path.read_text())["repetition_semantics"]
        == "per_member_v3"
        for path in replay_dir.rglob("lifecycle.json")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case_count", (1, 3))
async def test_lifecycle_v3_round_trip_restores_member_repetition_children(
    tmp_path: Path,
    case_count: int,
) -> None:
    case_ids = tuple(f"repetition-member-{index}" for index in range(case_count))
    dataset = SelfEvolveDataset(
        cases=tuple(EvalCase(case_id=case_id, input=case_id) for case_id in case_ids),
        recipe=DatasetRecipe(
            source={"kind": "repetition_lifecycle_round_trip"},
            split_seed="seed",
            splits={"train": list(case_ids), "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\n",
        candidate_id="candidate-repetitions",
    )

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "action": {
                        "content": f"{request.task_id}:{request.variant_id}"
                    }
                }
            ],
        )

    request = build_replay_request(
        run_id=f"run-repetition-lifecycle-{case_count}",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        baseline_repetitions=2,
        candidate_repetitions=3,
    )
    before = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )
    replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / request.run_id
        / "replay"
        / candidate.candidate_id
    )
    after = load_candidate_replay_result(replay_dir)

    assert before.member_results is not None
    assert after.member_results is not None
    for replay_result in (before, after):
        assert [len(member.baseline.repetition_results) for member in replay_result.member_results] == [
            2
        ] * case_count
        assert [len(member.candidate.repetition_results) for member in replay_result.member_results] == [
            3
        ] * case_count
    before_paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=before,
        candidate=candidate,
    )
    normalized_after = normalize_replay_members(dataset=dataset, replay_result=after)
    after_paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=after,
        candidate=candidate,
        normalized=normalized_after,
    )
    assert len(before_paired.cases) == len(after_paired.cases) == 3 * case_count
    assert [
        case.metadata["replay"]["replay_case_count"] for case in after_paired.cases
    ] == [3] * (3 * case_count)


@pytest.mark.asyncio
async def test_v2_distributed_artifact_migrates_to_non_authoritative_per_member_view(
    tmp_path: Path,
) -> None:
    case_ids = ("legacy-a", "legacy-b", "legacy-c")
    dataset = SelfEvolveDataset(
        cases=tuple(EvalCase(case_id=case_id, input=case_id) for case_id in case_ids),
        recipe=DatasetRecipe(
            source={"kind": "legacy_distributed_repetitions"},
            split_seed="seed",
            splits={"train": list(case_ids), "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\n",
        candidate_id="legacy-candidate",
    )
    adaptation = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=tmp_path,
        artifact_root=tmp_path / "adaptation",
    )

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    request = build_replay_request(
        run_id="legacy-v2-distributed",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        replay_adaptation=adaptation,
        baseline_repetitions=2,
        candidate_repetitions=3,
    )
    backend = AWorldCliCandidateReplayBackend(executor=fake_executor)
    await backend.replay_candidate(request, candidate=candidate, dataset=dataset)
    replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / request.run_id
        / "replay"
        / candidate.candidate_id
    )

    root_payload = json.loads((replay_dir / "request.json").read_text())
    root_payload.pop("repetition_semantics")
    root_payload["baseline_repetitions"] = 6
    root_payload["candidate_repetitions"] = 9
    _write_json(replay_dir / "request.json", root_payload)
    manifest_path = replay_dir / "members" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = "aworld.self_evolve.member_replay.v2"
    manifest.pop("repetition_semantics")
    _write_json(manifest_path, manifest)
    for member_request_path in (replay_dir / "members").glob("*/request.json"):
        member_payload = json.loads(member_request_path.read_text())
        member_payload.pop("repetition_semantics")
        _write_json(member_request_path, member_payload)
    for lifecycle_path in replay_dir.rglob("lifecycle.json"):
        lifecycle = json.loads(lifecycle_path.read_text())
        lifecycle["schema_version"] = "aworld.self_evolve.replay_lifecycle.v2"
        lifecycle.pop("repetition_semantics")
        _write_json(lifecycle_path, lifecycle)

    loaded = load_candidate_replay_result(replay_dir)
    normalized = normalize_replay_members(dataset=dataset, replay_result=loaded)

    assert loaded.request.baseline_repetitions == 2
    assert loaded.request.candidate_repetitions == 3
    assert loaded.request.repetition_semantics == "distributed_v2_migrated"
    assert loaded.baseline.metrics["repetition_count"] == 6
    assert loaded.candidate.metrics["repetition_count"] == 9
    assert all(
        member.request.baseline_repetitions == 2
        and member.request.candidate_repetitions == 3
        and member.request.repetition_semantics == "distributed_v2_migrated"
        for member in loaded.member_results
    )
    assert len(normalized.members) == 3
    assert not normalized.valid
    assert normalized.failure_events[0].code == (
        "legacy_repetition_semantics_non_authoritative"
    )
    assert not _replay_result_has_reusable_baseline(
        dataset=dataset,
        replay_result=loaded,
    )
    assert not _has_authoritative_per_member_repetitions(loaded.request)

    first_member = loaded.member_results[0]
    first_member_root = (
        replay_dir / "members" / _member_artifact_name(first_member.case_id)
    )
    new_request = replace(
        first_member.request,
        baseline_replay_dir=str(first_member_root / "baseline"),
        repetition_semantics="per_member_v3",
    )
    assert not _stored_baseline_matches_request(new_request)
    with pytest.raises(ValueError, match="explicit per-member repetition semantics"):
        await backend.replay_candidate(
            loaded.request,
            candidate=candidate,
            dataset=dataset,
        )


@pytest.mark.asyncio
async def test_v2_lifecycle_cannot_claim_v3_per_member_authority(
    tmp_path: Path,
) -> None:
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="legacy-lifecycle", input="task"),),
        recipe=DatasetRecipe(
            source={"kind": "legacy_lifecycle"},
            split_seed="seed",
            splits={"train": ["legacy-lifecycle"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    request = build_replay_request(
        run_id="legacy-lifecycle-v2",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
    )
    await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )
    replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / request.run_id
        / "replay"
        / candidate.candidate_id
    )
    for lifecycle_path in replay_dir.rglob("lifecycle.json"):
        lifecycle = json.loads(lifecycle_path.read_text())
        lifecycle["schema_version"] = "aworld.self_evolve.replay_lifecycle.v2"
        lifecycle.pop("repetition_semantics")
        _write_json(lifecycle_path, lifecycle)

    loaded = load_candidate_replay_result(replay_dir)
    normalized = normalize_replay_members(dataset=dataset, replay_result=loaded)

    assert loaded.request.repetition_semantics == "distributed_v2_migrated"
    assert not normalized.valid
    assert normalized.failure_events[0].code == (
        "legacy_repetition_semantics_non_authoritative"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tamper", "expected_code"),
    (
        ("delete_child", "replay_v3_repetition_children_invalid"),
        ("delete_other_member_candidate_child", "replay_v3_repetition_children_invalid"),
        ("delete_lifecycle", "replay_v3_lifecycle_missing"),
        ("unexpected_child", "replay_v3_repetition_children_invalid"),
        ("aggregate_count", "replay_v3_repetition_count_mismatch"),
        ("root_aggregate_count", "replay_v3_root_aggregate_metrics_mismatch"),
        ("request_count", "replay_v3_repetition_children_invalid"),
        ("manifest_status", "replay_v3_manifest_status_mismatch"),
        ("mixed_lifecycle", "replay_v3_lifecycle_contract_invalid"),
    ),
)
async def test_v3_repetition_artifact_tamper_is_typed_and_non_authoritative(
    tmp_path: Path,
    tamper: str,
    expected_code: str,
) -> None:
    case_ids = ("tamper-a", "tamper-b", "tamper-c")
    dataset = SelfEvolveDataset(
        cases=tuple(EvalCase(case_id=case_id, input=case_id) for case_id in case_ids),
        recipe=DatasetRecipe(
            source={"kind": "v3_repetition_tamper"},
            split_seed="seed",
            splits={"train": list(case_ids), "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\n",
        candidate_id="tamper-candidate",
    )

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    replay_adaptation = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=tmp_path,
        artifact_root=tmp_path / "adaptation",
    )
    request = build_replay_request(
        run_id=f"v3-tamper-{tamper}",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        baseline_repetitions=2,
        candidate_repetitions=3,
        replay_adaptation=replay_adaptation,
    )
    original = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )
    replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / request.run_id
        / "replay"
        / candidate.candidate_id
    )
    manifest_path = replay_dir / "members" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    first_member_root = replay_dir / "members" / manifest["members"][0]["path"]
    other_member_root = replay_dir / "members" / manifest["members"][1]["path"]
    first_baseline = first_member_root / "baseline"
    assert original.member_results is not None
    direct_reuse_request = replace(
        original.member_results[0].request,
        baseline_replay_dir=str(first_baseline),
    )
    assert _stored_baseline_matches_request(direct_reuse_request)
    assert _stored_baseline_matches_request(
        replace(
            direct_reuse_request,
            adaptation_fingerprint="candidate-package-changed",
        )
    )

    if tamper == "delete_child":
        shutil.rmtree(first_baseline / "2")
    elif tamper == "delete_other_member_candidate_child":
        shutil.rmtree(other_member_root / candidate.candidate_id / "3")
    elif tamper == "delete_lifecycle":
        for lifecycle_path in replay_dir.rglob("lifecycle.json"):
            lifecycle_path.unlink()
    elif tamper == "unexpected_child":
        shutil.copytree(first_baseline / "1", first_baseline / "01")
    elif tamper == "aggregate_count":
        aggregate_path = first_baseline / "aggregate_metrics.json"
        aggregate = json.loads(aggregate_path.read_text())
        aggregate["repetition_count"] = 99
        aggregate["successful_repetition_count"] = 99
        _write_json(aggregate_path, aggregate)
    elif tamper == "root_aggregate_count":
        aggregate_path = replay_dir / "baseline" / "aggregate_metrics.json"
        aggregate = json.loads(aggregate_path.read_text())
        aggregate["repetition_count"] = 99
        aggregate["successful_repetition_count"] = 99
        _write_json(aggregate_path, aggregate)
    elif tamper == "request_count":
        root_payload = json.loads((replay_dir / "request.json").read_text())
        root_payload["baseline_repetitions"] = 1
        _write_json(replay_dir / "request.json", root_payload)
        for member_request_path in (replay_dir / "members").glob("*/request.json"):
            member_payload = json.loads(member_request_path.read_text())
            member_payload["baseline_repetitions"] = 1
            _write_json(member_request_path, member_payload)
    elif tamper == "manifest_status":
        manifest["members"][0]["baseline_status"] = "failed"
        _write_json(manifest_path, manifest)
    else:
        lifecycle_path = first_baseline / "1" / "lifecycle.json"
        lifecycle = json.loads(lifecycle_path.read_text())
        lifecycle["schema_version"] = "aworld.self_evolve.replay_lifecycle.v2"
        lifecycle.pop("repetition_semantics")
        _write_json(lifecycle_path, lifecycle)

    loaded = load_candidate_replay_result(replay_dir)
    normalized = normalize_replay_members(dataset=dataset, replay_result=loaded)
    failure_codes = {event.code for event in normalized.failure_events}

    assert expected_code in failure_codes
    assert not normalized.valid
    assert not _has_authoritative_per_member_repetitions(loaded.request)
    assert not _replay_result_has_reusable_baseline(
        dataset=dataset,
        replay_result=loaded,
    )
    if tamper not in {
        "delete_other_member_candidate_child",
        "manifest_status",
        "root_aggregate_count",
    }:
        assert not _stored_baseline_matches_request(direct_reuse_request)
    assert not candidate_replay_is_comparable(
        dataset=dataset,
        replay_result=loaded,
        normalized=normalized,
    )
    with pytest.raises(ValueError, match="member result contract is invalid"):
        build_paired_replay_dataset(
            dataset=dataset,
            replay_result=loaded,
            candidate=candidate,
            normalized=normalized,
        )
    if tamper == "delete_child":
        assert loaded.member_results is not None
        assert len(loaded.member_results[0].baseline.repetition_results) == 1
        assert loaded.member_results[0].baseline.metrics["repetition_count"] == 1
    if tamper == "aggregate_count":
        assert loaded.member_results is not None
        assert loaded.member_results[0].baseline.metrics["repetition_count"] == 2
        assert (
            loaded.member_results[0].baseline.metrics[
                "successful_repetition_count"
            ]
            == 2
        )
    if tamper == "delete_other_member_candidate_child":
        assert loaded.member_results is not None
        assert len(loaded.member_results[1].candidate.repetition_results) == 2
        assert loaded.member_results[1].candidate.metrics["repetition_count"] == 2
    if tamper == "root_aggregate_count":
        assert loaded.baseline.metrics["repetition_count"] == 6
        assert loaded.baseline.metrics["successful_repetition_count"] == 6


def test_all_blocked_repetition_aggregate_is_unexecuted_and_artifact_free(
    tmp_path: Path,
) -> None:
    blocker = ReplayFailureEvent(
        code="shared_preflight_failed",
        owner=FailureOwner.INFRASTRUCTURE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.SHARED_RUN,
        repairable=False,
    )
    blocked = [
        ReplayVariantResult(
            variant_id=f"candidate-{index}",
            status=ReplayExecutionStatus.BLOCKED,
            trajectory=[],
            blocked_by=(blocker,),
        )
        for index in (1, 2)
    ]

    aggregate = _aggregate_variant_results(
        base_variant_id="candidate",
        results=blocked,
        artifact_dir=tmp_path / "aggregate",
        persist=False,
    )

    assert aggregate.status is ReplayExecutionStatus.BLOCKED
    assert aggregate.blocked_by == (blocker,)
    assert aggregate.trajectory == []
    assert aggregate.stdout_path is None
    assert aggregate.stderr_path is None
    assert aggregate.repetition_results == ()
    assert aggregate.metrics["blocked_repetition_count"] == 2


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, default=lambda value: value.__dict__, indent=2),
        encoding="utf-8",
    )


def test_replay_capability_fixture_summary_exposes_shape_without_content(
    tmp_path: Path,
) -> None:
    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixture.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('[{"private": "secret-value"}]', encoding="utf-8")
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixture.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    summaries = _replay_capability_fixture_summaries(capability)

    assert summaries == [
        {
            "service_id": "svc-1",
            "fixture_bytes": fixture.stat().st_size,
            "json_root_type": "array",
        }
    ]
    assert "secret-value" not in json.dumps(summaries)


def test_recorded_response_values_are_scoped_by_sidecar_operation(
    tmp_path: Path,
) -> None:
    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixture.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("{}", encoding="utf-8")
    fixture.with_suffix(".responses.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld.self_evolve.recorded_response_index.v1",
                "records": [
                    {
                        "operation": "records.alpha",
                        "non_empty": True,
                        "value": {
                            "message": "alpha recorded",
                            "items": [1, 2],
                        },
                    },
                    {
                        "operation": "records.beta",
                        "non_empty": True,
                        "value": {"message": "beta recorded"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixture.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    values = _replay_capability_recorded_response_values(capability)

    assert values["fixture.json"]["records.alpha"] == (
        '{"items":[1,2],"message":"alpha recorded"}',
        "alpha recorded",
        "1",
        "2",
    )
    assert "beta recorded" not in values["fixture.json"]["records.alpha"]
    assert values["fixture.json"]["records.beta"] == (
        '{"message":"beta recorded"}',
        "beta recorded",
    )


def test_correlated_probe_matches_encoded_record_container_semantically() -> None:
    recorded_container = '{"items":[1,2],"message":"alpha recorded"}'
    response_payload = json.dumps(
        {
            "id": 7,
            "result": {
                "items": [1, 2],
                "message": "alpha recorded",
            },
        }
    ).encode("utf-8")

    _validate_nonempty_correlated_json_response(
        request_text='{"id":7,"method":"records.alpha"}',
        response_payload=response_payload,
        response_contains="alpha recorded",
        required_recorded_response_values=(
            recorded_container,
            "alpha recorded",
        ),
    )

    with pytest.raises(
        ReplayServiceProtocolError,
        match="surrounding recorded response context",
    ) as exc_info:
        _validate_nonempty_correlated_json_response(
            request_text='{"id":7,"method":"records.alpha"}',
            response_payload=json.dumps(
                {"id": 7, "result": {"message": "alpha recorded"}}
            ).encode("utf-8"),
            response_contains="alpha recorded",
            required_recorded_response_values=(
                recorded_container,
                "alpha recorded",
            ),
        )
    assert exc_info.value.code == "recorded_response_context_incomplete"
    constraint = exc_info.value.details["runtime_response_constraints"][0]
    assert constraint["constraint_kind"] == "recorded_response_context"
    assert constraint["minimum_recorded_value_matches"] == 2
    assert constraint["maximum_response_bytes"] == 48 * 1024
    assert exc_info.value.details["runtime_response_observation"] == {
        "schema_version": "aworld.self_evolve.runtime_response_observation.v1",
        "constraint_kind": "recorded_response_context",
        "observed_recorded_value_matches": 1,
        "response_payload_bytes": len(
            json.dumps(
                {"message": "alpha recorded"},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ),
        "response_shape": "json_object",
    }


def test_http_probe_requires_surrounding_recorded_response_context() -> None:
    class RecordedHTTPHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps(
                {
                    "ok": True,
                    "result": {
                        "items": [1, 2],
                        "message": "alpha recorded",
                    },
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), RecordedHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _probe_replay_service(
            "127.0.0.1",
            server.server_port,
            "http",
            "/",
            required_recorded_response_values=(
                '{"items":[1,2],"message":"alpha recorded"}',
                "alpha recorded",
            ),
        )
        with pytest.raises(
            ReplayServiceProtocolError,
            match="surrounding recorded response context",
        ) as exc_info:
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "http",
                "/",
                required_recorded_response_values=(
                    '{"items":[1,2],"message":"alpha recorded"}',
                    "missing recorded sibling",
                ),
            )
        assert exc_info.value.code == "recorded_response_context_incomplete"
        constraint = exc_info.value.details[
            "runtime_response_constraints"
        ][0]
        assert constraint["probe_kind"] == "http"
        assert constraint["probe_path"] == "/"
        assert constraint["projection_minimum_scalar_descendants"] == 2
        observation = exc_info.value.details[
            "runtime_response_observation"
        ]
        assert observation["observed_recorded_value_matches"] == 1
        assert observation["response_shape"] == "json_object"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_replay_capability_fixture_leaf_values_walk_arbitrary_nested_arrays(
    tmp_path: Path,
) -> None:
    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixtures" / "recorded.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        json.dumps(
            {
                "envelope": [
                    {"response": {"items": []}},
                    {"response": [{"payload": "recorded nested value"}]},
                ]
            }
        ),
        encoding="utf-8",
    )
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixtures/recorded.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    values = replay_capability_fixture_leaf_values(capability)

    assert values == {
        "fixtures/recorded.json": ("recorded nested value",),
    }


def test_fixture_response_leaf_values_decode_nested_trajectory_outputs(
    tmp_path: Path,
) -> None:
    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixtures" / "recorded.json"
    fixture.parent.mkdir(parents=True)
    encoded_payload = json.dumps(
        {
            "result": {
                "items": [{"text": "recorded response value"}]
            }
        }
    )
    fixture.write_text(
        json.dumps(
            [
                {
                    "action": {
                        "result": {"value": "ignored result value"},
                        "tool_calls": [
                            {
                                "function": {
                                    "arguments": json.dumps(
                                        {"request": "ignored request value"}
                                    )
                                }
                            }
                        ]
                    },
                    "state": {
                        "input": {
                            "action_result": [
                                {
                                    "name": "ignored tool name",
                                    "tool_call_id": "ignored-tool-call-id",
                                    "success": True,
                                    "content": encoded_payload
                                }
                            ]
                        }
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixtures/recorded.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    values = replay_capability_fixture_response_leaf_values(capability)

    assert values == {
        "fixtures/recorded.json": (
            encoded_payload,
            "recorded response value",
        ),
    }


def test_fixture_response_leaf_values_find_nested_gateway_without_top_level_trace_keys(
    tmp_path: Path,
) -> None:
    """Nested trajectory gateways must not leak envelope metadata as payload."""

    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixtures" / "recorded.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        json.dumps(
            {
                "context": {
                    "state": {
                        "action_result": [
                            {
                                "tool_name": "ignored metadata",
                                "success": True,
                                "content": json.dumps(
                                    {
                                        "records": [
                                            {"payload": "late recorded value"}
                                        ]
                                    }
                                ),
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixtures/recorded.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    values = replay_capability_fixture_response_leaf_values(capability)

    assert values == {
        "fixtures/recorded.json": (
            json.dumps({"records": [{"payload": "late recorded value"}]}),
            "late recorded value",
        ),
    }


def test_fixture_response_leaf_values_treat_tool_outputs_as_gateway_before_payload(
    tmp_path: Path,
) -> None:
    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixtures" / "recorded.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        json.dumps(
            {
                "wrapper": {
                    "tool_outputs": [
                        {
                            "tool_name": "ignored tool name",
                            "success": True,
                            "response": {
                                "items": [{"text": "tool output value"}]
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixtures/recorded.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    values = replay_capability_fixture_response_leaf_values(capability)

    assert values == {
        "fixtures/recorded.json": ("tool output value",),
    }


def test_fixture_response_leaf_values_skip_metadata_inside_encoded_content_envelope(
    tmp_path: Path,
) -> None:
    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixtures" / "recorded.json"
    fixture.parent.mkdir(parents=True)
    encoded_content = json.dumps(
        {
            "type": "text",
            "content": "actual recorded output",
            "is_done": False,
        }
    )
    fixture.write_text(
        json.dumps(
            {
                "nested": {
                    "action_result": [
                        {
                            "success": "False",
                            "content": encoded_content,
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixtures/recorded.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    values = replay_capability_fixture_response_leaf_values(capability)

    assert values == {
        "fixtures/recorded.json": (
            encoded_content,
            "actual recorded output",
        ),
    }


def test_fixture_response_leaf_values_support_deep_bounded_nesting(
    tmp_path: Path,
) -> None:
    """Discovery is bounded by nodes, not an arbitrary shallow depth cutoff."""

    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixtures" / "recorded.json"
    fixture.parent.mkdir(parents=True)
    nested: object = {
        "action_result": [
            {"content": "deep recorded output", "success": "False"}
        ]
    }
    for _ in range(72):
        nested = {"wrapper": nested}
    fixture.write_text(json.dumps(nested), encoding="utf-8")
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixtures/recorded.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    values = replay_capability_fixture_response_leaf_values(capability)

    assert values == {"fixtures/recorded.json": ("deep recorded output",)}


def test_fixture_response_leaf_values_keep_message_after_encoded_success_metadata(
    tmp_path: Path,
) -> None:
    frozen_root = tmp_path / "frozen"
    fixture = frozen_root / "fixtures" / "fixtures" / "recorded.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        json.dumps(
            {
                "action_result": [
                    {
                        "content": json.dumps(
                            {
                                "success": True,
                                "message": "recorded message output",
                            }
                        )
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    capability = FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(frozen_root),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="svc-1",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixtures/recorded.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )

    values = replay_capability_fixture_response_leaf_values(capability)

    assert values == {
        "fixtures/recorded.json": (
            json.dumps({"success": True, "message": "recorded message output"}),
            "recorded message output",
        )
    }


def test_candidate_skill_overlay_materializes_shadow_root_without_mutating_real_skill(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    demo_path = skills_root / "demo" / "SKILL.md"
    helper_path = skills_root / "helper" / "SKILL.md"
    demo_path.parent.mkdir(parents=True)
    helper_path.parent.mkdir(parents=True)
    original_demo = "---\nname: demo\n---\n# Demo\n\nOriginal.\n"
    candidate_demo = "---\nname: demo\n---\n# Demo\n\nCandidate.\n"
    demo_path.write_text(original_demo, encoding="utf-8")
    helper_path.write_text("---\nname: helper\n---\n# Helper\n", encoding="utf-8")

    overlay = create_candidate_skill_overlay(
        workspace_root=tmp_path,
        run_id="run-1",
        candidate=_candidate(candidate_demo),
        target_skill_path=demo_path,
        baseline_skill_roots=(skills_root,),
    )

    assert overlay.shadow_root == tmp_path / ".aworld" / "self_evolve" / "run-1" / "overlays" / "cand-1" / "skills"
    assert overlay.candidate_skill_path.read_text(encoding="utf-8") == candidate_demo
    assert (overlay.shadow_root / "helper" / "SKILL.md").exists()
    assert demo_path.read_text(encoding="utf-8") == original_demo
    assert overlay.candidate_skill_package_fingerprint.startswith("sha256:")

    registry = build_compat_registry(overlay.shadow_root)
    descriptors = {descriptor.skill_name: descriptor for descriptor in registry.list_descriptors()}
    loaded_demo = registry.load_content(descriptors["demo"].skill_id)
    loaded_helper = registry.load_content(descriptors["helper"].skill_id)
    assert "Candidate." in loaded_demo.usage
    assert "Original." not in loaded_demo.usage
    assert "Helper" in loaded_helper.usage


def test_candidate_overlay_applies_replay_package_on_copy_of_target_skill(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    skill_root = skills_root / "demo"
    skill_path = skill_root / "SKILL.md"
    replay_root = skill_root / "replay"
    replay_root.mkdir(parents=True)
    skill_path.write_text("# Original\n", encoding="utf-8")
    (skill_root / "reference.md").write_text("keep\n", encoding="utf-8")
    (replay_root / "obsolete.py").write_text("old\n", encoding="utf-8")
    candidate = CandidateVariant(
        candidate_id="cand-package",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Candidate\n",
        rationale="add replay capability",
        target_fingerprint="sha256:old",
        files=(
            CandidateFileDelta(
                path="replay/compiler.py",
                content="print('compile')\n",
                executable=True,
            ),
            CandidateFileDelta(
                path="replay/obsolete.py",
                operation="delete",
            ),
        ),
    )

    overlay = create_candidate_skill_overlay(
        workspace_root=tmp_path,
        run_id="run-package",
        candidate=candidate,
        target_skill_path=skill_path,
        baseline_skill_roots=(skills_root,),
    )

    candidate_root = overlay.candidate_skill_path.parent
    assert overlay.candidate_skill_path.read_text(encoding="utf-8") == "# Candidate\n"
    assert (candidate_root / "reference.md").read_text(encoding="utf-8") == "keep\n"
    assert (candidate_root / "replay/compiler.py").read_text(encoding="utf-8") == (
        "print('compile')\n"
    )
    assert (candidate_root / "replay/compiler.py").stat().st_mode & 0o111
    assert not (candidate_root / "replay/obsolete.py").exists()
    assert (replay_root / "obsolete.py").read_text(encoding="utf-8") == "old\n"


def test_candidate_overlay_rejects_missing_skill_package_dependency(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "skills"
    skill_path = skills_root / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Original\n", encoding="utf-8")
    candidate = CandidateVariant(
        candidate_id="cand-missing-package-file",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Candidate\n\nRun `python3 replay/missing_probe.py`.\n",
        rationale="add a replay probe",
        target_fingerprint="sha256:old",
    )

    with pytest.raises(ValueError, match="missing referenced files"):
        create_candidate_skill_overlay(
            workspace_root=tmp_path,
            run_id="run-missing-package-file",
            candidate=candidate,
            target_skill_path=skill_path,
            baseline_skill_roots=(skills_root,),
        )

    assert skill_path.read_text(encoding="utf-8") == "# Original\n"


def test_cleanup_self_evolve_overlays_retains_latest_runs(tmp_path: Path) -> None:
    root = tmp_path / ".aworld" / "self_evolve"
    old_overlay = root / "run-old" / "overlays" / "cand-1" / "skills"
    new_overlay = root / "run-new" / "overlays" / "cand-2" / "skills"
    old_overlay.mkdir(parents=True)
    new_overlay.mkdir(parents=True)
    old_report = root / "run-old" / "report.json"
    new_report = root / "run-new" / "report.json"
    old_report.write_text("{}", encoding="utf-8")
    new_report.write_text("{}", encoding="utf-8")

    cleanup = cleanup_self_evolve_overlays(tmp_path, keep_latest_runs=1)

    assert cleanup["removed_run_count"] == 1
    assert not (root / "run-old" / "overlays").exists()
    assert (root / "run-new" / "overlays").exists()


def test_http_probe_automatically_rejects_unreachable_advertised_websocket() -> None:
    class DiscoveryOnlyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps(
                {
                    "socket": (
                        f"ws://127.0.0.1:{self.server.server_port}/stateful"
                    )
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), DiscoveryOnlyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(
            OSError,
            match=(
                "advertised WebSocket handshake (?:failed|requires HTTP/1.1)"
            ),
        ):
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "http",
                "/json/version",
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_skill_runtime_http_readiness_accepts_live_advertised_websocket() -> None:
    class StatefulHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            if self.headers.get("Upgrade", "").lower() == "websocket":
                key = self.headers["Sec-WebSocket-Key"]
                accept = base64.b64encode(
                    hashlib.sha1(
                        (
                            key
                            + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                        ).encode("ascii")
                    ).digest()
                ).decode("ascii")
                self.send_response(101)
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.send_header("Sec-WebSocket-Accept", accept)
                self.end_headers()
                header = self.connection.recv(2)
                length = header[1] & 0x7F
                mask = self.connection.recv(4)
                payload = self.connection.recv(length)
                payload = bytes(
                    value ^ mask[index % 4]
                    for index, value in enumerate(payload)
                )
                self.connection.sendall(bytes([0x8A, len(payload)]) + payload)
                return
            body = json.dumps(
                {
                    "socket": (
                        f"ws://127.0.0.1:{self.server.server_port}/stateful"
                    )
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), StatefulHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _probe_replay_service(
            "127.0.0.1",
            server.server_port,
            "http",
            "/json/version",
            validate_advertised_websockets=True,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_protocol_probe_mismatch_reports_actionable_bounded_diagnostics() -> None:
    class DiscoveryHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            body = b'{"Browser":"ReplayChrome","webSocketDebuggerUrl":"ws://local"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), DiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ReplayServiceProtocolError) as error:
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "http",
                "/json/version",
                response_contains="recorded fixture marker",
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    message = str(error.value)
    assert "kind=http path=/json/version" in message
    assert "expected_sha256=" in message
    assert "expected_bytes=23" in message
    assert "match=substring" in message
    assert "expected_shape=utf8_text" in message
    assert "response_sha256=" in message
    assert "response_shape=json_object" in message
    assert "recorded fixture marker" not in message
    assert "ReplayChrome" not in message
    assert len(message) < 500


def test_protocol_probe_mismatch_classifies_recorded_response_selector_drift() -> None:
    recorded = {
        "success": True,
        "message": "recorded tool response",
    }

    class RecordedResponseHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            body = json.dumps(recorded).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), RecordedResponseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ReplayServiceProtocolError) as error:
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "http",
                "/",
                response_contains="compiler selected another fixture leaf",
                diagnostic_recorded_response_values=(
                    json.dumps(
                        recorded,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "recorded tool response",
                ),
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    message = str(error.value)
    assert "classification=recorded_response_selector_drift" in message
    assert (
        "required_change=align_compiler_runtime_recorded_response_selection"
        in message
    )
    assert "recorded tool response" not in message


def test_protocol_probe_mismatch_does_not_misclassify_recorded_expected_value() -> None:
    message = _protocol_probe_response_mismatch(
        kind="http",
        path="/",
        expected="recorded expected",
        response=b'HTTP/1.0 200 OK\r\n\r\n{"value":"different"}',
        diagnostic_recorded_response_values=("recorded expected",),
    )

    assert "recorded_response_selector_drift" not in message


def test_http_probe_accepts_semantically_equivalent_json_descendant() -> None:
    expected_value = {
        "success": True,
        "message": "recorded fixture\nwith escaped lines",
    }

    class SemanticDiscoveryHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            body = json.dumps(
                {"recorded_container": expected_value},
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SemanticDiscoveryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _probe_replay_service(
            "127.0.0.1",
            server.server_port,
            "http",
            "/json/version",
            response_contains=json.dumps(
                expected_value,
                ensure_ascii=False,
                indent=2,
            ),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_advertised_websocket_invalid_port_reports_actionable_protocol_error() -> None:
    response = (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n"
        b'{"webSocketDebuggerUrl":"ws://127.0.0.1:REPLACE_PORT/devtools/browser"}'
    )

    with pytest.raises(
        ReplayServiceProtocolError,
        match=(
            "advertised WebSocket URL has an invalid port; construct it from "
            "the supplied --port integer"
        ),
    ):
        _probe_advertised_websockets(
            response,
            expected_host="127.0.0.1",
            expected_port=54321,
        )


def test_websocket_probe_rejects_http_1_0_upgrade_response() -> None:
    with pytest.raises(
        ReplayServiceProtocolError,
        match="requires HTTP/1.1",
    ) as error:
        _validate_websocket_handshake_response(
            (
                b"HTTP/1.0 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\n"
                b"Connection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: expected\r\n\r\n"
            ),
            expected_accept="expected",
        )

    assert error.value.code == "websocket_handshake_http_version_invalid"
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "runtime",
            "field_path": "websocket_handshake.http_version",
            "rule": "enum",
            "expected": ["HTTP/1.1"],
            "value_domain": "source_behavior",
            "required_operations": [
                "emit_http_1_1_websocket_upgrade_status_line"
            ],
            "forbidden_operations": [
                "emit_http_1_0_websocket_upgrade_status_line"
            ],
        }
    ]


def test_websocket_probe_reports_content_free_invalid_handshake_diagnostics() -> None:
    with pytest.raises(ReplayServiceProtocolError) as error:
        _validate_websocket_handshake_response(
            (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n\r\n"
                b'{"error":"upgrade route not reached"}'
            ),
            expected_accept="expected",
        )

    message = str(error.value)
    assert "response_bytes=" in message
    assert "response_sha256=" in message
    assert "response_shape=utf8_text" in message
    assert "HTTP/1.1 200 OK" not in message
    assert "upgrade route not reached" not in message
    assert len(message) < 500


def test_skill_runtime_rejects_websocket_handshake_only_stub() -> None:
    class HandshakeOnlyHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            if self.headers.get("Upgrade", "").lower() == "websocket":
                key = self.headers["Sec-WebSocket-Key"]
                accept = base64.b64encode(
                    hashlib.sha1(
                        (
                            key
                            + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                        ).encode("ascii")
                    ).digest()
                ).decode("ascii")
                self.send_response(101)
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.send_header("Sec-WebSocket-Accept", accept)
                self.end_headers()
                return
            body = json.dumps(
                {
                    "socket": (
                        f"ws://127.0.0.1:{self.server.server_port}/stateful"
                    )
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), HandshakeOnlyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(OSError, match="WebSocket control frame failed"):
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "http",
                "/json/version",
                validate_advertised_websockets=True,
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_skill_runtime_websocket_data_plane_probe_validates_response() -> None:
    class DataPlaneHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            key = self.headers["Sec-WebSocket-Key"]
            accept = base64.b64encode(
                hashlib.sha1(
                    (
                        key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                    ).encode("ascii")
                ).digest()
            ).decode("ascii")
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()

            opcode, payload = self._read_masked_frame()
            assert opcode == 0x9
            self.connection.sendall(bytes([0x8A, len(payload)]) + payload)
            opcode, payload = self._read_masked_frame()
            assert opcode == 0x1
            assert json.loads(payload) == {"op": "read"}
            response = b'{"result":"recorded fixture"}'
            self.connection.sendall(bytes([0x81, len(response)]) + response)

        def _read_masked_frame(self) -> tuple[int, bytes]:
            header = self.connection.recv(2)
            length = header[1] & 0x7F
            mask = self.connection.recv(4)
            payload = self.connection.recv(length)
            return (
                header[0] & 0x0F,
                bytes(
                    value ^ mask[index % 4]
                    for index, value in enumerate(payload)
                ),
            )

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), DataPlaneHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _probe_replay_service(
            "127.0.0.1",
            server.server_port,
            "websocket",
            "/stateful",
            request_text='{"op":"read"}',
            response_contains="recorded fixture",
        )
        with pytest.raises(
            OSError,
            match="protocol probe response mismatch: kind=websocket",
        ):
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "websocket",
                "/stateful",
                request_text='{"op":"read"}',
                response_contains="missing fixture",
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_task_plane_probe_requires_fixture_content_in_nonempty_correlated_result() -> None:
    class CorrelatedDataPlaneHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            key = self.headers["Sec-WebSocket-Key"]
            accept = base64.b64encode(
                hashlib.sha1(
                    (
                        key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                    ).encode("ascii")
                ).digest()
            ).decode("ascii")
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()

            opcode, payload = self._read_masked_frame()
            assert opcode == 0x9
            self.connection.sendall(bytes([0x8A, len(payload)]) + payload)
            opcode, payload = self._read_masked_frame()
            assert opcode == 0x1
            request = json.loads(payload)
            if request["method"] == "records.empty":
                response_payload = {
                    "id": request["id"],
                    "result": [],
                    "replay_token": "recorded fixture",
                }
            elif request["method"] == "records.query":
                response_payload = {
                    "id": request["id"],
                    "result": {"records": ["recorded fixture"]},
                }
            else:
                response_payload = {
                    "id": request["id"],
                    "result": {
                        "records": [
                            "recorded fixture",
                            "second recorded value",
                        ]
                    },
                }
            response = json.dumps(response_payload).encode("utf-8")
            self.connection.sendall(bytes([0x81, len(response)]) + response)

        def _read_masked_frame(self) -> tuple[int, bytes]:
            header = self.connection.recv(2)
            length = header[1] & 0x7F
            mask = self.connection.recv(4)
            payload = self.connection.recv(length)
            return (
                header[0] & 0x0F,
                bytes(
                    value ^ mask[index % 4]
                    for index, value in enumerate(payload)
                ),
            )

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), CorrelatedDataPlaneHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(
            ReplayServiceProtocolError,
            match="fixture-derived content must be inside a non-empty correlated result",
        ):
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "websocket",
                "/stateful",
                request_text='{"id":7,"method":"records.empty"}',
                response_contains="recorded fixture",
                require_nonempty_correlated_response=True,
            )

        _probe_replay_service(
            "127.0.0.1",
            server.server_port,
            "websocket",
            "/stateful",
            request_text='{"id":8,"method":"records.query"}',
            response_contains="recorded fixture",
            require_nonempty_correlated_response=True,
        )

        with pytest.raises(
            ReplayServiceProtocolError,
            match="surrounding recorded response context",
        ):
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "websocket",
                "/stateful",
                request_text='{"id":9,"method":"records.query"}',
                response_contains="recorded fixture",
                require_nonempty_correlated_response=True,
                required_recorded_response_values=(
                    "recorded fixture",
                    "second recorded value",
                ),
            )

        _probe_replay_service(
            "127.0.0.1",
            server.server_port,
            "websocket",
            "/stateful",
            request_text='{"id":10,"method":"records.structured"}',
            response_contains="recorded fixture",
            require_nonempty_correlated_response=True,
            required_recorded_response_values=(
                "recorded fixture",
                "second recorded value",
            ),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_task_plane_probe_accepts_short_recorded_response_leaf() -> None:
    """Fixture reconstruction must not impose an arbitrary token length."""

    class ShortLeafHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            key = self.headers["Sec-WebSocket-Key"]
            accept = base64.b64encode(
                hashlib.sha1(
                    (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode(
                        "ascii"
                    )
                ).digest()
            ).decode("ascii")
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            opcode, payload = self._read_masked_frame()
            assert opcode == 0x9
            self.connection.sendall(bytes([0x8A, len(payload)]) + payload)
            opcode, payload = self._read_masked_frame()
            assert opcode == 0x1
            request = json.loads(payload)
            response = json.dumps(
                {
                    "id": request["id"],
                    "result": {"content": "OK"},
                }
            ).encode("utf-8")
            self.connection.sendall(bytes([0x81, len(response)]) + response)

        def _read_masked_frame(self) -> tuple[int, bytes]:
            header = self.connection.recv(2)
            length = header[1] & 0x7F
            mask = self.connection.recv(4)
            payload = self.connection.recv(length)
            return (
                header[0] & 0x0F,
                bytes(
                    value ^ mask[index % 4]
                    for index, value in enumerate(payload)
                ),
            )

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ShortLeafHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(
            ReplayServiceProtocolError,
            match="surrounding recorded response context",
        ):
            _probe_replay_service(
                "127.0.0.1",
                server.server_port,
                "websocket",
                "/stateful",
                request_text='{"id":11,"method":"records.query"}',
                response_contains="OK",
                require_nonempty_correlated_response=True,
                required_recorded_response_values=("OK", "YES"),
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_websocket_probe_rejects_masked_server_frame() -> None:
    client, server = socket.socketpair()
    try:
        payload = b"pong"
        mask = b"mask"
        masked = bytes(
            value ^ mask[index % 4]
            for index, value in enumerate(payload)
        )
        server.sendall(
            bytes([0x81, 0x80 | len(payload)]) + mask + masked
        )

        with pytest.raises(
            ReplayServiceProtocolError,
            match="WebSocket server frame must not be masked",
        ):
            _read_websocket_frame(client)
    finally:
        client.close()
        server.close()


def test_candidate_owned_runtime_protocol_failure_is_not_infrastructure() -> None:
    variant = ReplayVariantResult(
        variant_id="candidate",
        status=ReplayExecutionStatus.FAILED,
        trajectory=[],
        failure={
            "type": "ReplayServiceProtocolError",
            "reason": "advertised WebSocket handshake failed",
            "outcome": "candidate_failure",
        },
    )

    assert isinstance(variant.failure, ReplayFailureEvent)
    assert variant.failure.source is FailureEventSource.LEGACY_INFERRED
    assert _replay_failure_outcome(variant.failure) == "candidate_failure"


def _frozen_skill_runtime_capability(tmp_path: Path) -> FrozenReplayCapability:
    return FrozenReplayCapability(
        capability_id="demo.replay",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(tmp_path / "frozen"),
        handled_requirements=("req-1",),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=(
            ReplayServiceSpec(
                service_id="service-0",
                requirement_id="req-1",
                transport="skill_runtime",
                response_fixture="fixture.json",
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )


def test_case_projection_starts_only_reachable_replay_services(
    tmp_path: Path,
) -> None:
    base = _frozen_skill_runtime_capability(tmp_path)
    capability = replace(
        base,
        endpoint_replacements={
            "https://example.test/a": "service-a",
            "https://example.test/b": "service-b",
        },
        services=(
            replace(base.services[0], service_id="service-a"),
            replace(base.services[0], service_id="service-b"),
        ),
    )

    projected = _project_replay_capability_for_case(
        capability,
        task_input={"content": "open https://example.test/a"},
        dependency_ids=(),
    )

    assert [service.service_id for service in projected.services] == [
        "service-a"
    ]
    assert projected.endpoint_replacements == {
        "https://example.test/a": "service-a"
    }


def test_case_projection_omits_unreferenced_replay_services(
    tmp_path: Path,
) -> None:
    base = _frozen_skill_runtime_capability(tmp_path)
    capability = replace(
        base,
        endpoint_replacements={"https://example.test/a": "service-a"},
        services=(replace(base.services[0], service_id="service-a"),),
    )

    projected = _project_replay_capability_for_case(
        capability,
        task_input={"content": "answer from retained context"},
        dependency_ids=(),
    )

    assert projected.services == ()
    assert projected.endpoint_replacements == {}


def test_replay_service_startup_timeout_is_typed_as_retryable_infrastructure(
    tmp_path: Path,
) -> None:
    timeout = ReplayServiceReadinessTimeout(
        "replay service readiness timed out after 5.0s: connection refused",
        phase="startup",
        timeout_seconds=5.0,
        service_id="service-0",
        transport="skill_runtime",
        last_error_type="ConnectionRefusedError",
        last_error_errno=61,
        process_returncode=None,
    )

    details = _replay_service_start_failure_details(
        timeout,
        replay_capability=_frozen_skill_runtime_capability(tmp_path),
    )
    event = _execution_failure_event(
        details,
        default_stage=FailureStage.TASK_ROLLOUT,
        service_preflight=True,
    )

    assert details["outcome"] == "infrastructure_failure"
    assert details["code"] == "replay_service_startup_timeout"
    assert details["diagnostics"]["last_error_errno"] == 61
    assert event.owner is FailureOwner.INFRASTRUCTURE
    assert event.stage is FailureStage.CAPABILITY_PREFLIGHT
    assert event.scope is FailureScope.SHARED_RUN
    assert event.repairable is True


def test_evidence_finalization_failure_preserves_typed_framework_stage() -> None:
    event = _execution_failure_event(
        {
            "code": "evidence_policy_v2_attestation_failed",
            "outcome": "framework_failure",
            "failure_owner": "framework",
            "failure_scope": "shared_run",
            "failure_stage": "evidence_finalization",
            "repairable": True,
            "reason": "canonical evidence finalization failed",
        },
        default_stage=FailureStage.TASK_ROLLOUT,
    )

    assert event.owner is FailureOwner.FRAMEWORK
    assert event.stage is FailureStage.EVIDENCE_FINALIZATION
    assert event.scope is FailureScope.SHARED_RUN
    assert event.repairable is True


def test_replay_service_protocol_probe_timeout_remains_candidate_owned(
    tmp_path: Path,
) -> None:
    timeout = ReplayServiceReadinessTimeout(
        "replay service protocol probe timed out",
        phase="protocol_probe",
        timeout_seconds=5.0,
        service_id="service-0",
        transport="skill_runtime",
        last_error_type="TimeoutError",
        last_error_errno=None,
        process_returncode=None,
    )

    details = _replay_service_start_failure_details(
        timeout,
        replay_capability=_frozen_skill_runtime_capability(tmp_path),
    )
    event = _execution_failure_event(
        details,
        default_stage=FailureStage.TASK_ROLLOUT,
        service_preflight=True,
    )

    assert details["outcome"] == "candidate_failure"
    assert details["code"] == "replay_service_protocol_probe_timeout"
    assert event.owner is FailureOwner.CANDIDATE
    assert event.scope is FailureScope.CANDIDATE


def test_replay_service_candidate_runtime_exit_has_explicit_candidate_ownership(
    tmp_path: Path,
) -> None:
    exited = ReplayServiceProcessExitedError(
        "replay service exited before readiness (exit=2)",
        phase="startup",
        service_id="service-0",
        transport="skill_runtime",
        process_returncode=2,
    )

    details = _replay_service_start_failure_details(
        exited,
        replay_capability=_frozen_skill_runtime_capability(tmp_path),
    )

    assert details["outcome"] == "candidate_failure"
    assert details["code"] == "replay_service_candidate_runtime_exited"
    assert details["diagnostics"]["process_returncode"] == 2


def test_untyped_service_preflight_exception_cannot_claim_candidate_ownership(
    tmp_path: Path,
) -> None:
    details = _replay_service_start_failure_details(
        ValueError("frozen replay capability directories are missing"),
        replay_capability=_frozen_skill_runtime_capability(tmp_path),
    )

    assert details["outcome"] == "infrastructure_failure"
    assert details["code"] == "replay_service_infrastructure_failed"
    assert details["repairable"] is False


def test_replay_service_failure_includes_bounded_sanitized_runtime_stderr(
    tmp_path: Path,
) -> None:
    stderr_path = tmp_path / "stderr.txt"
    stderr_path.write_text(
        "Traceback at /private/tmp/runtime.py\n"
        "OSError: [Errno 48] Address already in use\n",
        encoding="utf-8",
    )

    enriched = _replay_service_failure_with_stderr(
        TimeoutError("replay service readiness timed out"),
        stderr_path=stderr_path,
    )

    assert isinstance(enriched, TimeoutError)
    assert "Address already in use" in str(enriched)
    assert "/private/tmp/runtime.py" not in str(enriched)
    assert "<LOCAL_PATH>" in str(enriched)


def test_replay_service_stderr_enrichment_preserves_typed_diagnostics(
    tmp_path: Path,
) -> None:
    stderr_path = tmp_path / "stderr.txt"
    stderr_path.write_text("runtime diagnostic\n", encoding="utf-8")
    constraint = {
        "schema_layer": "protocol_trace",
        "field_path": "records[*].correlation",
        "rule": "required",
        "expected": [],
    }
    error = ReplayServiceProtocolError(
        "protocol trace field is missing",
        code="protocol_trace_schema_field_validation_failed",
        details={"schema_field_constraints": [constraint]},
    )

    enriched = _replay_service_failure_with_stderr(
        error,
        stderr_path=stderr_path,
    )

    assert isinstance(enriched, ReplayServiceProtocolError)
    assert enriched.code == "protocol_trace_schema_field_validation_failed"
    assert enriched.details == {"schema_field_constraints": [constraint]}
    assert "runtime diagnostic" in str(enriched)


def test_replay_service_protocol_trace_is_bounded_and_sanitized(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scratch" / "protocol_trace.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(
        ("x" * 80_000)
        + '\n{"direction":"client_to_runtime","token=very-secret":"ignored"}'
        + '\n{"direction":"runtime_to_client","path":"/Users/me/private.json"}\n',
        encoding="utf-8",
    )
    destination = tmp_path / "diagnostics" / "protocol_trace.log"

    assert _preserve_replay_service_protocol_trace(source, destination) is True

    preserved = destination.read_text(encoding="utf-8")
    assert len(preserved) <= 64 * 1024
    assert "very-secret" not in preserved
    assert "<REDACTED_SECRET>" in preserved
    assert "/Users/me/private.json" not in preserved
    assert "<LOCAL_PATH>" in preserved


def test_protocol_trace_reset_separates_preflight_from_task_interactions(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "scratch" / "protocol_trace.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text(
        '{"direction":"in","sequence":0,"kind":"preflight"}\n',
        encoding="utf-8",
    )

    _reset_replay_service_protocol_trace(trace)
    with trace.open("a", encoding="utf-8") as handle:
        handle.write('{"direction":"in","sequence":1,"kind":"task"}\n')

    assert "preflight" not in trace.read_text(encoding="utf-8")
    assert '"kind":"task"' in trace.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_protocol_trace_wait_tolerates_post_response_flush_race(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "scratch" / "protocol_trace.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text("", encoding="utf-8")

    class RunningProcess:
        @staticmethod
        def poll() -> None:
            return None

    async def finish_trace() -> None:
        await asyncio.sleep(0.03)
        trace.write_text(
            '{"direction":"in","sequence":1,"kind":"http_request",'
            '"fields":["path"],"correlation":{}}\n'
            '{"direction":"out","sequence":2,"kind":"http_response",'
            '"fields":["status"],"correlation":{}}\n',
            encoding="utf-8",
        )

    writer = asyncio.create_task(finish_trace())
    await _wait_for_replay_service_protocol_trace(
        RunningProcess(),  # type: ignore[arg-type]
        trace,
        timeout_seconds=0.5,
    )
    await writer


@pytest.mark.asyncio
async def test_protocol_trace_wait_keeps_empty_trace_failure_at_deadline(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "scratch" / "protocol_trace.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text("", encoding="utf-8")

    class RunningProcess:
        @staticmethod
        def poll() -> None:
            return None

    with pytest.raises(ReplayServiceProtocolError, match="empty"):
        await _wait_for_replay_service_protocol_trace(
            RunningProcess(),  # type: ignore[arg-type]
            trace,
            timeout_seconds=0.03,
        )


def test_successful_replay_records_task_plane_intervention_metric(
    tmp_path: Path,
) -> None:
    trace = (
        tmp_path
        / "artifacts"
        / "replay_services"
        / "recorded-endpoint"
        / "protocol_trace.log"
    )
    trace.parent.mkdir(parents=True)
    trace.write_text(
        '{"direction":"in","sequence":1,"kind":"task"}\n',
        encoding="utf-8",
    )
    result = ReplayExecutionResult(status="succeeded", trajectory=[])

    attached = _attach_replay_service_protocol_diagnostics(
        result,
        artifact_dir=tmp_path / "artifacts",
    )

    assert attached.failure is None
    assert attached.metrics["replay_service_protocol_trace_count"] == 1


def test_failed_replay_includes_preserved_protocol_trace_diagnostics(
    tmp_path: Path,
) -> None:
    trace = (
        tmp_path
        / "artifacts"
        / "replay_services"
        / "recorded-endpoint"
        / "protocol_trace.log"
    )
    trace.parent.mkdir(parents=True)
    trace.write_text(
        '{"direction":"client_to_runtime","fields":["id","channel"]}\n'
        '{"direction":"runtime_to_client","fields":["id"]}\n',
        encoding="utf-8",
    )
    result = ReplayExecutionResult(
        status="failed",
        trajectory=[],
        failure={
            "type": "TimeoutExpired",
            "reason": "replay timed out",
            "outcome": "candidate_failure",
        },
    )

    attached = _attach_replay_service_protocol_diagnostics(
        result,
        artifact_dir=tmp_path / "artifacts",
    )

    assert attached.failure is not None
    assert attached.failure["outcome"] == "candidate_failure"
    assert attached.failure["diagnostics"]["replay_service_protocol_traces"] == [
        {
            "path": "replay_services/recorded-endpoint/protocol_trace.log",
            "tail": (
                '{"direction":"client_to_runtime","fields":["id","channel"]}\n'
                '{"direction":"runtime_to_client","fields":["id"]}'
            ),
        }
    ]


def test_timeout_after_completed_data_interaction_defers_causal_attribution() -> None:
    trace = "\n".join(
        (
            '{"direction":"received","sequence":0,"kind":"http",'
            '"fields":["healthz"],"correlation":{"id":"opaque"}}',
            '{"direction":"emitted","sequence":1,"kind":"http",'
            '"fields":["healthz"],"correlation":{"id":"opaque"}}',
            '{"direction":"received","sequence":2,"kind":"http",'
            '"fields":["content"],"correlation":{"id":"opaque"}}',
            '{"direction":"emitted","sequence":3,"kind":"http",'
            '"fields":["content"],"correlation":{"id":"opaque"}}',
        )
    )
    result = ReplayExecutionResult(
        status="failed",
        trajectory=[],
        failure={
            "type": "TimeoutExpired",
            "reason": "replay timed out",
            "diagnostics": {
                "replay_service_protocol_traces": [{"tail": trace}],
            },
        },
    )

    classified = _classify_candidate_task_rollout_nontermination(
        result,
        variant_id="candidate-1",
    )
    baseline = _classify_candidate_task_rollout_nontermination(
        result,
        variant_id="baseline",
    )

    for observed in (classified, baseline):
        assert observed.failure is not None
        assert observed.failure["failure_stage"] == "task_rollout"
        assert observed.failure["completed_data_plane_operations"] == ["content"]
        assert observed.failure["diagnostics"][
            "completed_data_plane_operations"
        ] == ["content"]
        assert "outcome" not in observed.failure
        assert "failure_class" not in observed.failure


def test_candidate_timeout_records_completed_root_http_interaction() -> None:
    trace = "\n".join(
        (
            '{"direction":"received","sequence":0,"kind":"http",'
            '"fields":["method","path"],'
            '"correlation":{"path":"/","method":"GET"}}',
            '{"direction":"emitted","sequence":1,"kind":"http",'
            '"fields":["ok","result","correlation"],'
            '"correlation":{"path":"/","method":"GET"}}',
        )
    )
    result = ReplayExecutionResult(
        status="failed",
        trajectory=[],
        failure={
            "type": "TimeoutExpired",
            "reason": "replay timed out",
            "diagnostics": {
                "replay_service_protocol_traces": [{"tail": trace}],
            },
        },
    )

    classified = _classify_candidate_task_rollout_nontermination(
        result,
        variant_id="candidate-1",
    )

    assert classified.failure is not None
    assert classified.failure["completed_data_plane_operations"] == ["/"]
    assert "outcome" not in classified.failure


def test_candidate_readiness_only_timeout_is_not_task_behavior_failure() -> None:
    trace = "\n".join(
        (
            '{"direction":"received","sequence":0,"kind":"http",'
            '"fields":["healthz"],"correlation":{"id":"opaque"}}',
            '{"direction":"emitted","sequence":1,"kind":"http",'
            '"fields":["healthz"],"correlation":{"id":"opaque"}}',
        )
    )
    result = ReplayExecutionResult(
        status="failed",
        trajectory=[],
        failure={
            "type": "TimeoutExpired",
            "reason": "replay timed out",
            "diagnostics": {
                "replay_service_protocol_traces": [{"tail": trace}],
            },
        },
    )

    classified = _classify_candidate_task_rollout_nontermination(
        result,
        variant_id="candidate-1",
    )

    assert classified.failure == result.failure


def test_protocol_trace_diagnostics_keep_terminal_interactions(
    tmp_path: Path,
) -> None:
    trace = (
        tmp_path
        / "artifacts"
        / "replay_services"
        / "recorded-endpoint"
        / "protocol_trace.log"
    )
    trace.parent.mkdir(parents=True)
    trace.write_text(
        ("old-interaction\n" * 600)
        + '{"sequence":999,"kind":"terminal_unmatched_request"}\n',
        encoding="utf-8",
    )
    result = ReplayExecutionResult(
        status="failed",
        trajectory=[],
        failure={"type": "TimeoutExpired", "reason": "replay timed out"},
    )

    attached = _attach_replay_service_protocol_diagnostics(
        result,
        artifact_dir=tmp_path / "artifacts",
    )

    assert attached.failure is not None
    tail = attached.failure["diagnostics"][
        "replay_service_protocol_traces"
    ][0]["tail"]
    assert len(tail) <= 4_000
    assert "terminal_unmatched_request" in tail
    assert tail.endswith('{"sequence":999,"kind":"terminal_unmatched_request"}')


def test_replay_service_protocol_trace_contract_requires_bidirectional_records(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "protocol_trace.jsonl"
    trace.write_text(
        json.dumps(
            {
                "direction": "in",
                "sequence": 1,
                "kind": "request",
                "fields": ["id", "method"],
                "correlation": {"id": 1},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ReplayServiceProtocolError,
        match="must record both received and emitted interactions",
    ) as error:
        _validate_replay_service_protocol_trace(trace)

    assert error.value.code == "protocol_trace_direction_coverage_failed"
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "protocol_trace",
            "field_path": "records[*].direction",
            "rule": "contains_all",
            "expected": ["in", "out"],
        }
    ]


def test_protocol_trace_legacy_aliases_are_normalized_but_still_require_both_directions(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "protocol_trace.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "direction": "request",
                    "message_kind": "http",
                    "top_level_fields": ["path"],
                },
                {
                    "direction": "response",
                    "message_kind": "http",
                    "top_level_fields": ["status"],
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    _validate_replay_service_protocol_trace(trace)


def test_protocol_trace_canonical_record_missing_fields_emits_typed_constraints(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "protocol_trace.jsonl"
    trace.write_text(
        json.dumps(
            {
                "direction": "inbound",
                "sequence": 1,
                "correlation": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ReplayServiceProtocolError) as error:
        _validate_replay_service_protocol_trace(trace)

    assert error.value.code == "protocol_trace_schema_field_validation_failed"
    assert error.value.details["schema_field_constraints"] == [
        {
            "schema_layer": "protocol_trace",
            "field_path": "records[*].fields",
            "rule": "required",
            "expected": [],
        },
        {
            "schema_layer": "protocol_trace",
            "field_path": "records[*].kind",
            "rule": "required",
            "expected": [],
        },
    ]


def test_protocol_trace_runtime_artifact_contract_requires_pre_shutdown_output() -> None:
    constraint = _protocol_trace_runtime_artifact_constraint()

    assert constraint["producer_layer"] == "runtime"
    assert constraint["availability_milestone"] == "post_probe_pre_shutdown"
    assert constraint["write_mode"] == "incremental"
    assert constraint["required_directions"] == ["in", "out"]


def test_replay_service_protocol_trace_contract_accepts_sanitized_summary(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "protocol_trace.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "direction": "inbound",
                    "sequence": 1,
                    "kind": "request",
                    "fields": ["id", "method", "sessionId"],
                    "correlation": {"id": 1, "sessionId": "opaque"},
                },
                {
                    "direction": "outbound",
                    "sequence": 2,
                    "kind": "response",
                    "fields": ["id", "result", "sessionId"],
                    "correlation": {"id": 1, "sessionId": "opaque"},
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    _validate_replay_service_protocol_trace(trace)


def test_replay_service_protocol_trace_contract_accepts_recv_send_directions(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "protocol_trace.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "direction": "recv",
                    "sequence": 1,
                    "kind": "request",
                    "fields": ["id", "method"],
                    "correlation": {"id": 1},
                },
                {
                    "direction": "send",
                    "sequence": 2,
                    "kind": "response",
                    "fields": ["id", "result"],
                    "correlation": {"id": 1},
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    _validate_replay_service_protocol_trace(trace)


def test_replay_service_protocol_trace_contract_rejects_missing_trace(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ReplayServiceProtocolError,
        match="did not write protocol_trace.jsonl",
    ):
        _validate_replay_service_protocol_trace(
            tmp_path / "protocol_trace.jsonl"
        )


@pytest.mark.asyncio
@pytest.mark.replay_sandbox
async def test_skill_owned_replay_service_is_isolated_per_variant(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "frozen" / "runtime" / "replay" / "runtime.py"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(
        """
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer

parser = argparse.ArgumentParser()
parser.add_argument('--port', required=True, type=int)
args = parser.parse_args()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'candidate-controlled-response')
    def log_message(self, *args):
        pass

HTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
""",
        encoding="utf-8",
    )
    fixtures = tmp_path / "frozen" / "fixtures"
    fixtures.mkdir(parents=True)
    response_fixture = fixtures / "recording.txt"
    response_fixture.write_bytes(b"recorded")
    frozen = FrozenReplayCapability(
        capability_id="recorded-http",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(tmp_path / "frozen"),
        handled_requirements=("requirement-local",),
        unhandled_requirements=(),
        evidence_refs={"requirement-local": ("context:task-1",)},
        fixture_evidence_refs={"recording.txt": ("context:task-1",)},
        fixtures=(
            FrozenReplayFile(
                path="recording.txt",
                sha256="sha256:"
                + hashlib.sha256(response_fixture.read_bytes()).hexdigest(),
                size=response_fixture.stat().st_size,
            ),
        ),
        runtime_files=(
            FrozenReplayFile(
                path="replay/runtime.py",
                sha256="sha256:" + hashlib.sha256(runtime.read_bytes()).hexdigest(),
                size=runtime.stat().st_size,
            ),
        ),
        endpoint_replacements={
            "http://127.0.0.1:9222": "recorded-http",
        },
        services=(
            ReplayServiceSpec(
                service_id="recorded-http",
                requirement_id="requirement-local",
                transport="http_fixture",
                response_fixture="recording.txt",
                readiness=ReplayReadinessProbe(kind="tcp", timeout_seconds=2),
            ),
        ),
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )
    frozen_payload = {
        "schema_version": "aworld.replay.capability_result.v1",
        "capability_id": frozen.capability_id,
        "capability_package_fingerprint": frozen.capability_package_fingerprint,
        "request_fingerprint": frozen.request_fingerprint,
        "handled_requirements": list(frozen.handled_requirements),
        "unhandled_requirements": list(frozen.unhandled_requirements),
        "evidence_refs": frozen.evidence_refs,
        "fixture_evidence_refs": frozen.fixture_evidence_refs,
        "fixtures": [asdict(item) for item in frozen.fixtures],
        "runtime_files": [asdict(item) for item in frozen.runtime_files],
        "endpoint_replacements": frozen.endpoint_replacements,
        "services": [asdict(item) for item in frozen.services],
        "deterministic": frozen.deterministic,
    }
    frozen_fingerprint = "sha256:" + hashlib.sha256(
        json.dumps(
            frozen_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    frozen = replace(frozen, fingerprint=frozen_fingerprint)
    (tmp_path / "frozen/frozen_manifest.json").write_text(
        json.dumps(
            {**frozen_payload, "fingerprint": frozen_fingerprint},
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    class LocalEndpointAdapter:
        adapter_id = "test.local-endpoint"

        def bind(self, dependency, *, context):
            if dependency.kind != "local_endpoint":
                return None
            return ReplayAdapterBinding(
                adapter_id=self.adapter_id,
                dependency_id=dependency.identifier,
                deterministic=True,
            )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="task-1",
                input={"content": "Inspect http://127.0.0.1:9222"},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    adaptation = ReplayAdaptationCompiler(
        adapters=(LocalEndpointAdapter(),)
    ).compile(
        dataset=dataset,
        workspace_root=tmp_path,
        artifact_root=tmp_path / "adaptation",
    )
    adaptation = replace(adaptation, replay_capability=frozen)
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-service-isolation",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
        replay_adaptation=adaptation,
    )
    observed_ports: list[int] = []

    async def fake_executor(
        execution_request: ReplayExecutionRequest,
    ) -> ReplayExecutionResult:
        url = execution_request.task_input["content"].split()[-1]
        port = int(url.rsplit(":", 1)[1])
        observed_ports.append(port)
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            connection.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
            chunks: list[bytes] = []
            while chunk := connection.recv(4096):
                chunks.append(chunk)
            response = b"".join(chunks)
            assert b"recorded" in response
            assert str(execution_request.artifact_dir).encode() not in response
            assert b"baseline" not in response
            assert b"cand-1" not in response
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": execution_request.variant_id}}],
        )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(request, candidate=candidate, dataset=dataset)

    assert result.succeeded is True
    assert result.baseline.metrics["frozen_capability_fingerprint"] == (
        result.candidate.metrics["frozen_capability_fingerprint"]
    )
    assert result.baseline.metrics["service_endpoint"] != (
        result.candidate.metrics["service_endpoint"]
    )
    assert result.baseline.metrics["service_cleanup_status"] == "stopped"
    assert result.candidate.metrics["service_cleanup_status"] == "stopped"
    assert len(observed_ports) == 2
    assert len(set(observed_ports)) == 2
    for port in observed_ports:
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.1)


def test_paired_replay_dataset_maps_baseline_and_candidate_trajectories() -> None:
    baseline_trajectory = [
        {"state": {"input": {"content": "task"}}, "action": {"content": "old"}, "reward": {}}
    ]
    candidate_trajectory = [
        {"state": {"input": {"content": "task"}}, "action": {"content": "new"}, "reward": {}}
    ]
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="task-1",
                input={"content": "task"},
                metadata={"baseline_trajectory": baseline_trajectory},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    replay = CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-1",
            task_id="task-1",
            workspace_root=str(Path("/tmp/workspace")),
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate_id="cand-1",
            overlay_skill_root="/tmp/overlay",
            task_input={"content": "task"},
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="succeeded",
            trajectory=baseline_trajectory,
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=candidate_trajectory,
            metrics={"latency_ms": 120.0},
        ),
    )

    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=replay,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
    )

    assert paired.cases[0].metadata["variant_trajectories"]["baseline"] == baseline_trajectory
    assert paired.cases[0].metadata["variant_trajectories"]["cand-1"] == candidate_trajectory
    assert paired.cases[0].metadata["replay"]["candidate"]["metrics"]["latency_ms"] == 120.0


def test_build_replay_request_skips_framework_generated_eval_cases(tmp_path: Path) -> None:
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="framework-evaluator-case",
                input={
                    "content": json.dumps(
                        {
                            "evaluation_runtime_contract": {
                                "do_not_call_external_tools": True,
                                "trajectory_log_path": str(
                                    tmp_path
                                    / ".aworld"
                                    / "self_evolve"
                                    / "evaluator"
                                    / "old-run"
                                    / "trajectory.log"
                                ),
                            },
                            "report_output_path": str(tmp_path / "report.json"),
                        }
                    )
                },
                metadata={"framework_meta_trajectory": True},
            ),
            EvalCase(
                case_id="user-task",
                input={"content": "Summarize the referenced page with grounded citations."},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["framework-evaluator-case", "user-task"], "validation": [], "held_out": []},
        ),
    )

    request = build_replay_request(
        run_id="run-1",
        workspace_root=tmp_path,
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    assert request.task_id == "user-task"
    assert request.task_input == {"content": "Summarize the referenced page with grounded citations."}


def test_build_replay_request_rejects_framework_only_dataset(tmp_path: Path) -> None:
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="framework-evaluator-case",
                input={
                    "content": (
                        "evaluation_runtime_contract: do_not_call_external_tools=true "
                        f"trajectory_log_path={tmp_path}/.aworld/self_evolve/evaluator/run/trajectory.log"
                    )
                },
                metadata={"framework_meta_trajectory": True},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["framework-evaluator-case"], "validation": [], "held_out": []},
        ),
    )

    with pytest.raises(ValueError, match="user task eval case"):
        build_replay_request(
            run_id="run-1",
            workspace_root=tmp_path,
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
            overlay_skill_root=tmp_path / "overlay-skills",
            dataset=dataset,
        )


def test_paired_replay_dataset_expands_repetition_trajectories_into_eval_cases() -> None:
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input={"content": "task"}),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    baseline_1 = ReplayVariantResult(
        variant_id="baseline-1",
        status="succeeded",
        trajectory=[{"action": {"content": "baseline-1"}}],
    )
    baseline_2 = ReplayVariantResult(
        variant_id="baseline-2",
        status="succeeded",
        trajectory=[{"action": {"content": "baseline-2"}}],
    )
    candidate_1 = ReplayVariantResult(
        variant_id="cand-1-1",
        status="succeeded",
        trajectory=[{"action": {"content": "candidate-1"}}],
    )
    candidate_2 = ReplayVariantResult(
        variant_id="cand-1-2",
        status="succeeded",
        trajectory=[{"action": {"content": "candidate-2"}}],
    )
    candidate_3 = ReplayVariantResult(
        variant_id="cand-1-3",
        status="succeeded",
        trajectory=[{"action": {"content": "candidate-3"}}],
    )
    replay = CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-1",
            task_id="task-1",
            workspace_root=str(Path("/tmp/workspace")),
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate_id="cand-1",
            overlay_skill_root="/tmp/overlay",
            task_input={"content": "task"},
            baseline_repetitions=2,
            candidate_repetitions=3,
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="succeeded",
            trajectory=baseline_2.trajectory,
            metrics={"repetition_count": 2, "successful_repetition_count": 2},
            repetition_results=(baseline_1, baseline_2),
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=candidate_3.trajectory,
            metrics={"repetition_count": 3, "successful_repetition_count": 3},
            repetition_results=(candidate_1, candidate_2, candidate_3),
        ),
    )

    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=replay,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
    )

    assert [case.case_id for case in paired.cases] == [
        "task-1__replay_1",
        "task-1__replay_2",
        "task-1__replay_3",
    ]
    assert [
        case.metadata["variant_trajectories"]["baseline"][0]["action"]["content"]
        for case in paired.cases
    ] == ["baseline-1", "baseline-2", "baseline-1"]
    assert [
        case.metadata["variant_trajectories"]["cand-1"][0]["action"]["content"]
        for case in paired.cases
    ] == ["candidate-1", "candidate-2", "candidate-3"]
    assert paired.recipe.source["paired_replay"] is True
    assert paired.recipe.source["original_case_count"] == 1
    assert paired.recipe.source["replay_case_count"] == 3
    assert paired.recipe.splits["train"] == [
        "task-1__replay_1",
        "task-1__replay_2",
        "task-1__replay_3",
    ]


def test_load_candidate_replay_result_restores_repetition_artifacts(tmp_path: Path) -> None:
    replay_dir = tmp_path / "replay" / "cand-1"
    request = CandidateReplayRequest(
        run_id="run-1",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input={"content": "task"},
        baseline_repetitions=2,
        candidate_repetitions=3,
    )
    _write_json(replay_dir / "request.json", request)
    for variant_root, base_variant_id, count in (
        (replay_dir / "baseline", "baseline", 2),
        (replay_dir / "cand-1", "cand-1", 3),
    ):
        variant_root.mkdir(parents=True)
        _write_json(
            variant_root / "aggregate_metrics.json",
            {
                "repetition_count": count,
                "successful_repetition_count": count,
                "failed_repetition_count": 0,
            },
        )
        for index in range(1, count + 1):
            repetition_dir = variant_root / str(index)
            repetition_dir.mkdir()
            (repetition_dir / "stdout.txt").write_text("", encoding="utf-8")
            (repetition_dir / "stderr.txt").write_text("", encoding="utf-8")
            _write_json(repetition_dir / "metrics.json", {"returncode": 0})
            _write_json(
                repetition_dir / "trajectory.json",
                [{"action": {"content": f"{base_variant_id}-{index}"}}],
            )

    loaded = load_candidate_replay_result(replay_dir)

    assert loaded.request.candidate_id == "cand-1"
    assert loaded.succeeded is True
    assert len(loaded.baseline.repetition_results) == 2
    assert len(loaded.candidate.repetition_results) == 3
    assert loaded.candidate.trajectory[0]["action"]["content"] == "cand-1-3"


def test_load_candidate_replay_result_prefers_successful_single_evidence_retry(
    tmp_path: Path,
) -> None:
    replay_dir = tmp_path / "replay" / "cand-1"
    request = CandidateReplayRequest(
        run_id="run-single-retry",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input={"content": "task"},
    )
    _write_json(replay_dir / "request.json", request)
    baseline_dir = replay_dir / "baseline"
    _write_json(
        baseline_dir / "trajectory.json",
        [{"action": {"content": "compacted baseline"}}],
    )
    _write_json(
        baseline_dir / "failure.json",
        {"reason": "evidence_quality_failed"},
    )
    retry_dir = baseline_dir / "evidence_retry_2"
    _write_json(
        retry_dir / "trajectory.json",
        [{"action": {"content": "complete baseline"}}],
    )
    _write_json(retry_dir / "metrics.json", {"evidence_strategy_passed": True})
    candidate_dir = replay_dir / "cand-1"
    _write_json(
        candidate_dir / "trajectory.json",
        [{"action": {"content": "candidate"}}],
    )
    _write_json(candidate_dir / "metrics.json", {})

    loaded = load_candidate_replay_result(replay_dir)

    assert loaded.baseline.succeeded is True
    assert loaded.baseline.trajectory[0]["action"]["content"] == "complete baseline"
    assert loaded.succeeded is True


def test_paired_replay_dataset_requires_successful_candidate_replay() -> None:
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input="task"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    replay = CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-1",
            task_id="task-1",
            workspace_root=str(Path("/tmp/workspace")),
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate_id="cand-1",
            overlay_skill_root="/tmp/overlay",
            task_input=dataset.cases[0].input,
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="succeeded",
            trajectory=[],
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-1",
            status="failed",
            trajectory=[],
            failure={"reason": "missing browser"},
        ),
    )

    with pytest.raises(ValueError, match="candidate replay did not succeed"):
        build_paired_replay_dataset(
            dataset=dataset,
            replay_result=replay,
            candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
        )


def test_paired_replay_dataset_rejects_source_trajectory_baseline_fallback() -> None:
    source_trajectory = [
        {
            "state": {"input": {"content": "task"}},
            "action": {"content": "baseline did not finish"},
            "reward": {"status": "failed"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=source_trajectory,
        task_id="task-1",
    )
    replay = CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-task-failure",
            task_id="task-1",
            workspace_root=str(Path("/tmp/workspace")),
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate_id="cand-1",
            overlay_skill_root="/tmp/overlay",
            task_input=dataset.cases[0].input,
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="failed",
            trajectory=[],
            failure={"type": "TimeoutExpired", "reason": "replay timed out"},
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=[{"action": {"content": "candidate completed"}}],
        ),
    )

    assert candidate_replay_is_comparable(dataset=dataset, replay_result=replay) is False
    with pytest.raises(ValueError, match="comparable paired outcomes"):
        build_paired_replay_dataset(
            dataset=dataset,
            replay_result=replay,
            candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
        )


def test_paired_replay_dataset_rejects_infrastructure_baseline_failure() -> None:
    source_trajectory = [
        {
            "state": {"input": {"content": "task"}},
            "action": {"content": "baseline"},
            "reward": {"status": "failed"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=source_trajectory,
        task_id="task-1",
    )
    replay = CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-infrastructure-failure",
            task_id="task-1",
            workspace_root=str(Path("/tmp/workspace")),
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
            candidate_id="cand-1",
            overlay_skill_root="/tmp/overlay",
            task_input=dataset.cases[0].input,
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="failed",
            trajectory=[],
            failure={"type": "ProcessError", "reason": "aworld-cli run failed"},
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=[{"action": {"content": "candidate completed"}}],
        ),
    )

    assert candidate_replay_is_comparable(dataset=dataset, replay_result=replay) is False
    with pytest.raises(ValueError, match="comparable paired outcomes"):
        build_paired_replay_dataset(
            dataset=dataset,
            replay_result=replay,
            candidate=_candidate("---\nname: demo\n---\n# Demo\n"),
        )


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_aggregates_repetitions(
    tmp_path: Path,
) -> None:
    calls = []
    scores = {
        "baseline-1": 0.4,
        "baseline-2": 0.6,
        "cand-1-1": 0.8,
        "cand-1-2": 0.9,
        "cand-1-3": 1.0,
    }

    async def fake_executor(request):
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                    "reward": {"status": "ok"},
                }
            ],
            metrics={"score": scores[request.variant_id]},
        )

    request = CandidateReplayRequest(
        run_id="run-repetitions",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=2,
        candidate_repetitions=3,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=SelfEvolveDataset(
            cases=(EvalCase(case_id="task-1", input="Replay this task"),),
            recipe=DatasetRecipe(
                source={"kind": "test", "case_count": 1},
                split_seed="seed",
                splits={"train": ["task-1"], "validation": [], "held_out": []},
            ),
        ),
    )

    assert [call.variant_id for call in calls] == [
        "baseline-1",
        "baseline-2",
        "cand-1-1",
        "cand-1-2",
        "cand-1-3",
    ]
    assert result.baseline.variant_id == "baseline"
    assert result.baseline.metrics["repetition_count"] == 2
    assert result.baseline.metrics["score"] == pytest.approx(0.5)
    assert result.candidate.variant_id == "cand-1"
    assert result.candidate.metrics["repetition_count"] == 3
    assert result.candidate.metrics["score"] == pytest.approx(0.9)
    assert [item.variant_id for item in result.candidate.repetition_results] == [
        "cand-1-1",
        "cand-1-2",
        "cand-1-3",
    ]
    assert result.candidate.trajectory[0]["action"]["content"] == "cand-1-3"


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_does_not_reuse_legacy_baseline_without_provenance(
    tmp_path: Path,
) -> None:
    baseline_dir = tmp_path / "stored-baseline"
    (baseline_dir / "1").mkdir(parents=True)
    (baseline_dir / "1" / "trajectory.json").write_text(
        json.dumps([{"action": {"content": "stored baseline"}}]),
        encoding="utf-8",
    )
    (baseline_dir / "1" / "metrics.json").write_text(
        json.dumps({"score": 0.7}),
        encoding="utf-8",
    )
    (baseline_dir / "2").mkdir(parents=True)
    (baseline_dir / "2" / "trajectory.json").write_text(
        json.dumps([{"action": {"content": "stored baseline selected"}}]),
        encoding="utf-8",
    )
    (baseline_dir / "2" / "metrics.json").write_text(
        json.dumps({"score": 0.9}),
        encoding="utf-8",
    )
    (baseline_dir / "aggregate_metrics.json").write_text(
        json.dumps(
            {
                "repetition_count": 2,
                "successful_repetition_count": 2,
                "score": 0.8,
            }
        ),
        encoding="utf-8",
    )

    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request):
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
            metrics={"score": 1.0},
        )

    request = CandidateReplayRequest(
        run_id="run-baseline-reuse",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=2,
        candidate_repetitions=3,
        baseline_replay_dir=str(baseline_dir),
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=SelfEvolveDataset(
            cases=(EvalCase(case_id="task-1", input="Replay this task"),),
            recipe=DatasetRecipe(
                source={"kind": "test", "case_count": 1},
                split_seed="seed",
                splits={"train": ["task-1"], "validation": [], "held_out": []},
            ),
        ),
    )

    assert [call.variant_id for call in calls] == [
        "baseline-1",
        "baseline-2",
        "cand-1-1",
        "cand-1-2",
        "cand-1-3",
    ]
    assert result.baseline.succeeded is True
    assert result.baseline.metrics["repetition_count"] == 2
    assert result.baseline.trajectory[0]["action"]["content"] == "baseline-2"
    assert result.candidate.succeeded is True


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_allows_partial_repetition_success(
    tmp_path: Path,
) -> None:
    async def fake_executor(request):
        if request.variant_id == "baseline-2":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={"type": "TimeoutExpired", "reason": "replay timed out"},
                metrics={"latency_ms": 600000},
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                    "reward": {"status": "ok"},
                }
            ],
            metrics={"latency_ms": 1000},
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input="Replay this task"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-partial-repetitions",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=2,
        candidate_repetitions=3,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=dataset,
    )

    assert result.succeeded is True
    assert result.baseline.succeeded is True
    assert result.baseline.metrics["repetition_count"] == 2
    assert result.baseline.metrics["successful_repetition_count"] == 1
    assert result.baseline.metrics["failed_repetition_count"] == 1
    assert result.baseline.metrics["repetition_failures"] == [
        {"type": "TimeoutExpired", "reason": "replay timed out"}
    ]
    assert result.baseline.trajectory[0]["action"]["content"] == "baseline-1"
    assert result.baseline.failure is None

    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=result,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
    )

    assert [case.case_id for case in paired.cases] == [
        "task-1__replay_1",
        "task-1__replay_2",
        "task-1__replay_3",
    ]
    assert {
        case.metadata["variant_trajectories"]["baseline"][0]["action"]["content"]
        for case in paired.cases
    } == {"baseline-1"}
    assert paired.cases[0].metadata["replay"]["baseline"]["metrics"][
        "failed_repetition_count"
    ] == 1


@pytest.mark.asyncio
async def test_multi_member_replay_executes_and_maps_each_member_independently(
    tmp_path: Path,
) -> None:
    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {
                        "content": f"{request.task_id}:{request.variant_id}"
                    },
                    "reward": {"status": "ok"},
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={
                "train": ["task-a"],
                "validation": [],
                "held_out": ["task-b"],
            },
            trainable_case_ids=("task-a",),
            held_out_case_ids=("task-b",),
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\n",
        candidate_id="cand-1",
    )
    request = build_replay_request(
        run_id="run-members",
        workspace_root=tmp_path,
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )

    assert [(call.task_id, call.variant_id) for call in calls] == [
        ("task-a", "baseline"),
        ("task-a", "cand-1"),
        ("task-b", "baseline"),
        ("task-b", "cand-1"),
    ]
    assert [member.case_id for member in result.member_results] == [
        "task-a",
        "task-b",
    ]
    assert len({Path(call.artifact_dir) for call in calls}) == 4
    assert len({Path(call.artifact_dir).parent for call in calls}) == 2

    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=result,
        candidate=candidate,
    )

    assert [case.case_id for case in paired.cases] == ["task-a", "task-b"]
    for case in paired.cases:
        variants = case.metadata["variant_trajectories"]
        assert variants["baseline"][0]["action"]["content"] == (
            f"{case.case_id}:baseline"
        )
        assert variants["cand-1"][0]["action"]["content"] == (
            f"{case.case_id}:cand-1"
        )
    assert paired.recipe.splits == {
        "train": ["task-a"],
        "validation": [],
        "held_out": ["task-b"],
    }
    assert paired.recipe.trainable_case_ids == ("task-a",)
    assert paired.recipe.held_out_case_ids == ("task-b",)


@pytest.mark.asyncio
@pytest.mark.parametrize("case_count", (1, 2, 3, 4))
async def test_replay_repetitions_apply_to_every_normalized_member(
    tmp_path: Path,
    case_count: int,
) -> None:
    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": f"{request.task_id}:{request.variant_id}"},
                    "reward": {"status": "ok"},
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=tuple(
            EvalCase(case_id=f"task-{index}", input=f"Replay task {index}")
            for index in range(1, case_count + 1)
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": case_count},
            split_seed="seed",
            splits={
                "train": [f"task-{index}" for index in range(1, case_count + 1)],
                "validation": [],
                "held_out": [],
            },
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-distributed-repetitions",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
        baseline_repetitions=2,
        candidate_repetitions=3,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )

    expected_calls = [
        (f"task-{member}", f"{variant}-{repetition}")
        for member in range(1, case_count + 1)
        for variant, repetition_count in (("baseline", 2), ("cand-1", 3))
        for repetition in range(1, repetition_count + 1)
    ]
    assert [(call.task_id, call.variant_id) for call in calls] == expected_calls
    assert result.baseline.metrics["repetition_count"] == case_count * 2
    assert result.candidate.metrics["repetition_count"] == case_count * 3
    assert all(
        member.request.baseline_repetitions == 2
        and member.request.candidate_repetitions == 3
        and member.baseline.metrics["repetition_count"] == 2
        and member.candidate.metrics["repetition_count"] == 3
        for member in result.member_results
    )


@pytest.mark.asyncio
async def test_multi_member_replay_stops_after_shared_baseline_infrastructure_failure(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append((request.task_id, request.variant_id))
        if request.task_id == "task-a" and request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={
                    "type": "TimeoutExpired",
                    "reason": "replay timed out",
                    "outcome": "infrastructure_failure",
                    "diagnostics": {
                        "task_artifacts": [
                            {
                                "path": "artifact/workspace/scrape.log",
                                "tail": "CDP endpoint does not implement /json/version",
                            }
                        ]
                    },
                },
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.task_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=tuple(
            EvalCase(case_id=f"task-{suffix}", input=f"Replay task {suffix}")
            for suffix in ("a", "b", "c")
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 3},
            split_seed="seed",
            splits={
                "train": ["task-a", "task-b"],
                "validation": [],
                "held_out": ["task-c"],
            },
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-baseline-infrastructure-fail-fast",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(request, candidate=candidate, dataset=dataset)

    assert calls == [("task-a", "baseline")]
    assert [member.case_id for member in result.member_results] == [
        "task-a",
        "task-b",
        "task-c",
    ]
    assert result.member_results[0].baseline.failure["diagnostics"] == {
        "task_artifacts": [
            {
                "path": "artifact/workspace/scrape.log",
                "tail": "CDP endpoint does not implement /json/version",
            }
        ]
    }
    for member in result.member_results[1:]:
        assert member.baseline.status is ReplayExecutionStatus.BLOCKED
        assert member.baseline.failure is None
        assert member.baseline.blocked_by[0].event_id == (
            result.member_results[0].baseline.failure.event_id
        )
    assert all(
        member.candidate.status is ReplayExecutionStatus.BLOCKED
        and member.candidate.failure is None
        and member.candidate.blocked_by[0].scope is FailureScope.SHARED_RUN
        for member in result.member_results
    )


@pytest.mark.asyncio
async def test_single_member_replay_skips_candidate_after_capability_preflight_failure(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request.variant_id)
        return ReplayExecutionResult(
            status="failed",
            trajectory=[],
            failure={
                "type": "ReplayServiceProtocolError",
                "reason": "advertised WebSocket handshake requires HTTP/1.1",
                "outcome": "candidate_failure",
            },
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-a", input="Replay task A"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-a"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-single-capability-preflight",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(request, candidate=candidate, dataset=dataset)

    assert calls == ["baseline"]
    assert result.baseline.failure.owner is FailureOwner.CANDIDATE
    assert result.baseline.failure.stage is FailureStage.CAPABILITY_PREFLIGHT
    assert result.baseline.failure.scope is FailureScope.CANDIDATE
    assert result.candidate.status is ReplayExecutionStatus.BLOCKED
    assert result.candidate.failure is None
    assert result.candidate.blocked_by[0].event_id == result.baseline.failure.event_id


@pytest.mark.asyncio
async def test_single_member_replay_runs_candidate_after_rollout_capability_failure(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request.variant_id)
        if request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={
                    "type": "TimeoutExpired",
                    "reason": "replay timed out",
                    "outcome": "infrastructure_failure",
                    "failure_class": "candidate_replay_capability",
                    "failure_stage": "task_rollout",
                    "repairable": True,
                    "diagnostics": {
                        "task_artifacts": [
                            {
                                "path": "artifact/protocol.json",
                                "tail": "recorded data plane is incomplete",
                            }
                        ]
                    },
                },
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": "candidate completed"}}],
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-a", input="Replay task A"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-a"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-single-rollout-capability-failure",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(request, candidate=candidate, dataset=dataset)

    assert calls == ["baseline", "cand-1"]
    assert result.baseline.succeeded is False
    assert result.candidate.succeeded is True
    assert candidate_replay_is_comparable(dataset=dataset, replay_result=result)


@pytest.mark.asyncio
async def test_single_member_replay_runs_candidate_after_baseline_evidence_producer_failure(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request.variant_id)
        if request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[
                    {"action": {"content": "baseline omitted evidence"}}
                ],
                failure={
                    "code": "replay_evidence_production_failed",
                    "outcome": "task_failure",
                    "failure_class": "baseline_evidence_production",
                    "failure_owner": "task",
                    "failure_scope": "member",
                    "failure_stage": "evidence_finalization",
                    "repairable": False,
                    "reason": "framework evidence inventory is empty",
                },
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {"action": {"content": "candidate persisted bounded evidence"}}
            ],
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-a", input="Replay task A"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-a"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-baseline-evidence-producer-failure",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(request, candidate=candidate, dataset=dataset)

    assert calls == ["baseline", "cand-1"]
    assert result.baseline.failure.owner is FailureOwner.TASK
    assert result.baseline.failure.stage is FailureStage.EVIDENCE_FINALIZATION
    assert result.candidate.succeeded is True
    assert candidate_replay_is_comparable(dataset=dataset, replay_result=result)
    assert candidate_replay_pair_coverage(
        dataset=dataset,
        replay_result=result,
    )["task_failure_pair_count"] == 1


@pytest.mark.asyncio
async def test_multi_member_replay_reports_failed_case_without_masking_it(
    tmp_path: Path,
) -> None:
    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        if request.task_id == "task-b" and request.variant_id == "cand-1":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={"type": "TaskFailure", "reason": "task-b failed"},
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.task_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a", "task-b"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-member-failure",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(request, candidate=candidate, dataset=dataset)

    assert result.succeeded is False
    assert result.candidate.status == "failed"
    assert result.candidate.metrics["successful_member_count"] == 1
    assert result.candidate.metrics["failed_member_count"] == 1
    assert result.candidate.metrics["member_failures"] == [
        {
            "case_id": "task-b",
            "failure": {"type": "TaskFailure", "reason": "task-b failed"},
        }
    ]


@pytest.mark.asyncio
async def test_load_candidate_replay_result_restores_multi_member_mapping(
    tmp_path: Path,
) -> None:
    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {
                        "content": f"{request.task_id}:{request.variant_id}"
                    },
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a"], "validation": [], "held_out": ["task-b"]},
            trainable_case_ids=("task-a",),
            held_out_case_ids=("task-b",),
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-load-members",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
        baseline_repetitions=2,
        candidate_repetitions=2,
    )
    await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )
    replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-load-members"
        / "replay"
        / "cand-1"
    )

    loaded = load_candidate_replay_result(replay_dir)

    assert loaded.succeeded is True
    assert [member.case_id for member in loaded.member_results] == [
        "task-a",
        "task-b",
    ]
    assert all(
        len(member.baseline.repetition_results) == 2
        and member.baseline.metrics["repetition_count"] == 2
        and len(member.candidate.repetition_results) == 2
        and member.candidate.metrics["repetition_count"] == 2
        for member in loaded.member_results
    )
    assert loaded.baseline.metrics["repetition_count"] == 4
    assert loaded.candidate.metrics["repetition_count"] == 4
    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=loaded,
        candidate=candidate,
    )
    assert {
        case.case_id.split("__replay_", 1)[0]: case.metadata[
            "variant_trajectories"
        ]["cand-1"][0]["action"]["content"].split(":", 1)[0]
        for case in paired.cases
    } == {"task-a": "task-a", "task-b": "task-b"}


@pytest.mark.asyncio
async def test_multi_member_replay_reuses_each_members_baseline(
    tmp_path: Path,
) -> None:
    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a", "task-b"], "validation": [], "held_out": []},
        ),
    )
    first_candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nFirst.\n",
        candidate_id="cand-1",
    )
    replay_adaptation = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=tmp_path,
        artifact_root=tmp_path / ".aworld" / "self_evolve" / "run-reuse-members" / "adaptation",
    )
    first_request = build_replay_request(
        run_id="run-reuse-members",
        workspace_root=tmp_path,
        target=first_candidate.target,
        candidate=first_candidate,
        overlay_skill_root=tmp_path / "overlay-1",
        dataset=dataset,
        replay_adaptation=replay_adaptation,
        baseline_repetitions=2,
        candidate_repetitions=3,
    )
    backend = AWorldCliCandidateReplayBackend(executor=fake_executor)
    await backend.replay_candidate(
        first_request,
        candidate=first_candidate,
        dataset=dataset,
    )
    assert [(call.task_id, call.variant_id) for call in calls] == [
        ("task-a", "baseline-1"),
        ("task-a", "baseline-2"),
        ("task-a", "cand-1-1"),
        ("task-a", "cand-1-2"),
        ("task-a", "cand-1-3"),
        ("task-b", "baseline-1"),
        ("task-b", "baseline-2"),
        ("task-b", "cand-1-1"),
        ("task-b", "cand-1-2"),
        ("task-b", "cand-1-3"),
    ]
    calls.clear()
    second_candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nSecond.\n",
        candidate_id="cand-2",
    )
    members_root = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-reuse-members"
        / "replay"
        / "cand-1"
        / "members"
    )
    incremental_manifest = members_root / "baseline_cache_manifest.json"
    assert incremental_manifest.exists()
    incremental_payload = json.loads(incremental_manifest.read_text())
    assert {
        item["case_id"]: item["control_fingerprint"]
        for item in incremental_payload["members"]
    } == {
        member.case_id: baseline_control_fingerprint(member.request)
        for member in load_candidate_replay_result(
            members_root.parent
        ).member_results
    }
    (members_root / "manifest.json").unlink()
    second_request = build_replay_request(
        run_id="run-reuse-members",
        workspace_root=tmp_path,
        target=second_candidate.target,
        candidate=second_candidate,
        overlay_skill_root=tmp_path / "overlay-2",
        dataset=dataset,
        baseline_replay_dir=members_root,
        replay_adaptation=replay_adaptation,
        baseline_repetitions=2,
        candidate_repetitions=3,
    )

    result = await backend.replay_candidate(
        second_request,
        candidate=second_candidate,
        dataset=dataset,
    )

    assert result.succeeded is True
    assert [(call.task_id, call.variant_id) for call in calls] == [
        ("task-a", "cand-2-1"),
        ("task-a", "cand-2-2"),
        ("task-a", "cand-2-3"),
        ("task-b", "cand-2-1"),
        ("task-b", "cand-2-2"),
        ("task-b", "cand-2-3"),
    ]
    assert all(member.baseline.succeeded for member in result.member_results)
    assert result.baseline.metrics["repetition_count"] == 4
    assert result.candidate.metrics["repetition_count"] == 6
    second_replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-reuse-members"
        / "replay"
        / "cand-2"
    )
    loaded = load_candidate_replay_result(second_replay_dir)
    assert loaded.succeeded is True
    assert [member.case_id for member in loaded.member_results] == [
        "task-a",
        "task-b",
    ]
    assert all(member.baseline.succeeded for member in loaded.member_results)


@pytest.mark.asyncio
async def test_multi_member_replay_reuses_complete_task_failure_baselines(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []
    baseline_attempts: dict[str, int] = {}

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append((request.task_id, request.variant_id))
        if request.variant_id == "baseline":
            baseline_attempts[request.task_id] = (
                baseline_attempts.get(request.task_id, 0) + 1
            )
            if request.task_id == "task-b" and baseline_attempts[request.task_id] == 1:
                return ReplayExecutionResult(
                    status="failed",
                    trajectory=[],
                    failure={"type": "TimeoutExpired", "reason": "replay timed out"},
                )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": f"{request.task_id}:{request.variant_id}"},
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a", "task-b"], "validation": [], "held_out": []},
        ),
    )
    backend = AWorldCliCandidateReplayBackend(executor=fake_executor)
    replay_adaptation = ReplayAdaptationCompiler().compile(
        dataset=dataset,
        workspace_root=tmp_path,
        artifact_root=(
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-partial-member-cache"
            / "adaptation"
        ),
    )
    first_candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nFirst.\n",
        candidate_id="cand-1",
    )
    first_request = build_replay_request(
        run_id="run-partial-member-cache",
        workspace_root=tmp_path,
        target=first_candidate.target,
        candidate=first_candidate,
        overlay_skill_root=tmp_path / "overlay-1",
        dataset=dataset,
        replay_adaptation=replay_adaptation,
    )

    first_result = await backend.replay_candidate(
        first_request,
        candidate=first_candidate,
        dataset=dataset,
    )

    assert first_result.baseline.succeeded is False
    members_root = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-partial-member-cache"
        / "replay"
        / "cand-1"
        / "members"
    )
    calls.clear()
    second_candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nSecond.\n",
        candidate_id="cand-2",
    )
    second_request = build_replay_request(
        run_id="run-partial-member-cache",
        workspace_root=tmp_path,
        target=second_candidate.target,
        candidate=second_candidate,
        overlay_skill_root=tmp_path / "overlay-2",
        dataset=dataset,
        baseline_replay_dir=members_root,
        replay_adaptation=replay_adaptation,
    )

    second_result = await backend.replay_candidate(
        second_request,
        candidate=second_candidate,
        dataset=dataset,
    )

    assert second_result.baseline.succeeded is False
    assert calls == [
        ("task-a", "cand-2"),
        ("task-b", "cand-2"),
    ]
    assert [
        member.baseline.metrics["baseline_cache_status"]
        for member in second_result.member_results
    ] == ["hit", "hit"]


@pytest.mark.asyncio
async def test_progressive_replay_preserves_earlier_pair_before_later_baseline_failure(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append((request.task_id, request.variant_id))
        if request.task_id == "task-b" and request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="failed",
                trajectory=[],
                failure={"reason": "replay_compacted_argument_unavailable"},
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="Replay task A"),
            EvalCase(case_id="task-b", input="Replay task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a", "task-b"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate(
        "---\nname: demo\n---\n# Demo\nCandidate.\n",
        candidate_id="cand-1",
    )
    request = build_replay_request(
        run_id="run-baseline-preflight",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )

    assert calls == [
        ("task-a", "baseline"),
        ("task-a", "cand-1"),
        ("task-b", "baseline"),
    ]
    assert result.baseline.succeeded is False
    assert result.member_results[0].candidate.succeeded is True
    assert result.member_results[1].candidate.status is ReplayExecutionStatus.BLOCKED


def test_member_baseline_replay_dir_maps_legacy_member_root_without_manifest(
    tmp_path: Path,
) -> None:
    members_root = tmp_path / "members"
    case_id = "task-a"
    member_dir = members_root / _member_artifact_name(case_id)
    member_dir.mkdir(parents=True)
    (member_dir / "request.json").write_text(
        json.dumps(
            {
                "run_id": "old-run",
                "task_id": case_id,
                "workspace_root": str(tmp_path),
                "target": {"target_type": "skill", "target_id": "demo"},
                "candidate_id": "cand-1",
                "overlay_skill_root": str(tmp_path / "overlay"),
                "task_input": "Replay task A",
            }
        ),
        encoding="utf-8",
    )

    assert _member_baseline_replay_dir(str(members_root), case_id) == str(
        member_dir / "baseline"
    )


def test_member_baseline_replay_dir_rejects_mismatched_chained_baseline(
    tmp_path: Path,
) -> None:
    members_root = tmp_path / "members"
    case_id = "task-b"
    member_name = _member_artifact_name(case_id)
    member_dir = members_root / member_name
    member_dir.mkdir(parents=True)
    stale_replay_root = tmp_path / "old-replay"
    stale_baseline = stale_replay_root / "baseline"
    stale_baseline.mkdir(parents=True)
    (stale_replay_root / "request.json").write_text(
        json.dumps({"task_id": "task-a"}),
        encoding="utf-8",
    )
    (member_dir / "request.json").write_text(
        json.dumps(
            {
                "task_id": case_id,
                "baseline_replay_dir": str(stale_baseline),
            }
        ),
        encoding="utf-8",
    )
    (members_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld.self_evolve.member_replay.v1",
                "members": [
                    {
                        "case_id": case_id,
                        "path": member_name,
                        "succeeded": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert _member_baseline_replay_dir(str(members_root), case_id) is None


def test_member_baseline_replay_dir_follows_matching_chained_baseline(
    tmp_path: Path,
) -> None:
    members_root = tmp_path / "members"
    case_id = "task-a"
    member_name = _member_artifact_name(case_id)
    member_dir = members_root / member_name
    member_dir.mkdir(parents=True)
    prior_replay_root = tmp_path / "old-replay"
    prior_baseline = prior_replay_root / "baseline"
    prior_baseline.mkdir(parents=True)
    (prior_baseline / "trajectory.json").write_text(
        json.dumps([{"action": {"content": "stored baseline"}}]),
        encoding="utf-8",
    )
    (prior_baseline / "metrics.json").write_text("{}\n", encoding="utf-8")
    (prior_replay_root / "request.json").write_text(
        json.dumps({"task_id": case_id}),
        encoding="utf-8",
    )
    (member_dir / "request.json").write_text(
        json.dumps(
            {
                "task_id": case_id,
                "baseline_replay_dir": str(prior_baseline),
            }
        ),
        encoding="utf-8",
    )
    (members_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld.self_evolve.member_replay.v1",
                "members": [
                    {
                        "case_id": case_id,
                        "path": member_name,
                        "succeeded": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert _member_baseline_replay_dir(str(members_root), case_id) == str(
        prior_baseline
    )


@pytest.mark.asyncio
async def test_multi_member_replay_paths_do_not_collide_after_sanitization(
    tmp_path: Path,
) -> None:
    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.task_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task/a", input="Task A"),
            EvalCase(case_id="task?a", input="Task B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task/a", "task?a"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-collision",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
    )
    await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )
    replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-collision"
        / "replay"
        / "cand-1"
    )

    loaded = load_candidate_replay_result(replay_dir)

    assert [member.case_id for member in loaded.member_results] == ["task/a", "task?a"]
    assert [
        member.candidate.trajectory[0]["action"]["content"]
        for member in loaded.member_results
    ] == ["task/a", "task?a"]


@pytest.mark.asyncio
async def test_replay_excludes_framework_advisory_members_from_paired_dataset(
    tmp_path: Path,
) -> None:
    calls: list[ReplayExecutionRequest] = []

    async def fake_executor(request: ReplayExecutionRequest) -> ReplayExecutionResult:
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.task_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="user-task", input="Replay user task"),
            EvalCase(
                case_id="prior-run-summary",
                input={"status": "rejected"},
                source={"kind": "prior_self_evolve_run", "framework_generated": True},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={
                "train": ["user-task", "prior-run-summary"],
                "validation": [],
                "held_out": [],
            },
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-advisory",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=candidate,
        dataset=dataset,
    )
    paired = build_paired_replay_dataset(
        dataset=dataset,
        replay_result=result,
        candidate=candidate,
    )

    assert [(call.task_id, call.variant_id) for call in calls] == [
        ("user-task", "baseline"),
        ("user-task", "cand-1"),
    ]
    assert [member.case_id for member in result.member_results] == ["user-task"]
    assert [case.case_id for case in paired.cases] == ["user-task"]


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_aggregates_evidence_metrics(
    tmp_path: Path,
) -> None:
    async def fake_executor(request):
        compacted = request.variant_id == "cand-1-2"
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                    "reward": {"status": "ok"},
                }
            ],
            metrics={
                "evidence_compacted": compacted,
                "evidence_strategy_passed": not compacted,
                "evidence_compaction_signals": (
                    ["tool_output_compacted"] if compacted else []
                ),
            },
        )

    request = CandidateReplayRequest(
        run_id="run-evidence-metrics",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=1,
        candidate_repetitions=3,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=SelfEvolveDataset(
            cases=(EvalCase(case_id="task-1", input="Replay this task"),),
            recipe=DatasetRecipe(
                source={"kind": "test", "case_count": 1},
                split_seed="seed",
                splits={"train": ["task-1"], "validation": [], "held_out": []},
            ),
        ),
    )

    assert result.candidate.succeeded is True
    assert result.candidate.metrics["evidence_compacted"] is False
    assert result.candidate.metrics["evidence_strategy_passed"] is True
    assert result.candidate.metrics["evidence_retry_count"] == 1.0
    assert result.candidate.metrics["evidence_compaction_signals"] == [
        "tool_output_compacted"
    ]


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_fails_when_evidence_retries_still_compact(
    tmp_path: Path,
) -> None:
    async def fake_executor(request):
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                    "reward": {"status": "ok"},
                }
            ],
            metrics={
                "evidence_compacted": True,
                "evidence_strategy_passed": False,
                "evidence_compaction_signals": ["tool_output_compacted"],
            },
        )

    request = CandidateReplayRequest(
        run_id="run-evidence-hard-fail",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=1,
        candidate_repetitions=1,
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=SelfEvolveDataset(
            cases=(EvalCase(case_id="task-1", input="Replay this task"),),
            recipe=DatasetRecipe(
                source={"kind": "test", "case_count": 1},
                split_seed="seed",
                splits={"train": ["task-1"], "validation": [], "held_out": []},
            ),
        ),
    )

    assert result.succeeded is False
    assert result.candidate.status == "failed"
    assert result.candidate.failure["reason"] == "evidence_quality_failed"
    assert result.candidate.metrics["evidence_retry_count"] == 1
    assert result.candidate.metrics["evidence_compaction_signals"] == [
        "tool_output_compacted"
    ]


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_retries_framework_capture_failure(
    tmp_path: Path,
) -> None:
    candidate_calls: list[str] = []

    async def fake_executor(request):
        if request.variant_id == "cand-1":
            candidate_calls.append(request.variant_id)
            return ReplayExecutionResult(
                status="succeeded",
                trajectory=[],
            )
        if request.variant_id.startswith("cand-1__evidence_retry_"):
            candidate_calls.append(request.variant_id)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": "captured"},
                    "reward": {"status": "ok"},
                }
            ],
        )

    request = CandidateReplayRequest(
        run_id="run-framework-capture-retry",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
        baseline_repetitions=1,
        candidate_repetitions=1,
    )
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input="Replay this task"),),
        recipe=DatasetRecipe(
            source={"kind": "test"},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(
        request,
        candidate=_candidate(
            "---\nname: demo\n---\n# Demo\n",
            candidate_id="cand-1",
        ),
        dataset=dataset,
    )

    assert result.candidate.succeeded is True
    assert candidate_calls == ["cand-1", "cand-1__evidence_retry_2"]
    assert result.candidate.metrics["replay_attempt_count"] == 2.0
    assert result.candidate.metrics["framework_capture_retry_count"] == 1.0
    assert result.candidate.metrics["evidence_retry_count"] == 0.0


@pytest.mark.asyncio
async def test_replay_variant_retries_transient_service_startup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = AWorldCliCandidateReplayBackend()
    calls: list[str] = []
    startup_failure = ReplayVariantResult(
        variant_id="candidate-1",
        status=ReplayExecutionStatus.FAILED,
        trajectory=[],
        failure=ReplayFailureEvent(
            code="replay_service_startup_timeout",
            owner=FailureOwner.INFRASTRUCTURE,
            stage=FailureStage.CAPABILITY_PREFLIGHT,
            scope=FailureScope.SHARED_RUN,
            repairable=True,
            summary="replay service readiness timed out",
        ),
    )
    succeeded = ReplayVariantResult(
        variant_id="candidate-1__evidence_retry_2",
        status=ReplayExecutionStatus.SUCCEEDED,
        trajectory=[{"action": {"content": "replayed"}}],
        metrics={"service_startup_status": "ready"},
    )

    async def fake_run_variant(
        request,
        *,
        variant_id: str,
        skill_root: str | None,
        artifact_dir: Path,
        measurement_arm: MeasurementArm | None = None,
        repetition_id: int = 1,
    ) -> ReplayVariantResult:
        del request, skill_root, artifact_dir, measurement_arm, repetition_id
        calls.append(variant_id)
        return startup_failure if len(calls) == 1 else succeeded

    monkeypatch.setattr(backend, "_run_variant", fake_run_variant)
    request = CandidateReplayRequest(
        run_id="run-service-startup-retry",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="candidate-1",
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input="Replay this task",
    )

    result = await backend._run_variant_with_evidence_retries(
        request,
        variant_id="candidate-1",
        skill_root=request.overlay_skill_root,
        artifact_dir=tmp_path / "replay",
    )

    assert calls == ["candidate-1", "candidate-1__evidence_retry_2"]
    assert result.succeeded is True
    assert result.variant_id == "candidate-1"
    assert result.metrics["service_startup_retry_count"] == 1
    assert result.metrics["framework_capture_retry_count"] == 0
    assert result.metrics["evidence_retry_count"] == 0
    assert result.metrics["service_startup_status"] == "ready"
    assert result.metrics["retry_failures"][0]["outcome"] == (
        "infrastructure_failure"
    )


@pytest.mark.asyncio
async def test_legacy_baseline_evidence_retry_preserves_control_role(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str | None]] = []

    async def fake_executor(
        request: ReplayExecutionRequest,
    ) -> ReplayExecutionResult:
        calls.append((request.variant_id, request.variant_role))
        if request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="succeeded",
                trajectory=[{"action": {"content": "compacted control"}}],
                metrics={
                    "evidence_compacted": True,
                    "evidence_strategy_passed": False,
                    "evidence_compaction_signals": ["tool_output_compacted"],
                },
            )
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
            metrics={
                "evidence_compacted": False,
                "evidence_strategy_passed": True,
            },
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input="Replay this task"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    candidate = _candidate("---\nname: demo\n---\n# Demo\n")
    request = build_replay_request(
        run_id="run-legacy-baseline-evidence-retry-role",
        workspace_root=tmp_path,
        target=candidate.target,
        candidate=candidate,
        overlay_skill_root=tmp_path / "overlay-skills",
        dataset=dataset,
    )

    result = await AWorldCliCandidateReplayBackend(
        executor=fake_executor
    ).replay_candidate(request, candidate=candidate, dataset=dataset)

    assert calls == [
        ("baseline", "baseline"),
        ("baseline__evidence_retry_2", "baseline"),
        (candidate.candidate_id, "candidate"),
    ]
    assert result.baseline.succeeded is True
    assert result.baseline.metrics["evidence_retry_count"] == 1
    baseline_attempt = result.member_results[0].baseline.repetition_results[0]
    assert baseline_attempt.metrics["retry_failures"][0]["outcome"] == (
        "task_failure"
    )
    assert result.candidate.succeeded is True


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_runs_baseline_and_candidate_with_skill_roots(
    tmp_path: Path,
) -> None:
    calls = []

    async def fake_executor(request):
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": f"{request.variant_id} output"},
                    "reward": {"status": "ok"},
                }
            ],
            metrics={"score": 0.9 if request.variant_id == "cand-1" else 0.4},
            stdout=f"{request.variant_id} stdout",
            stderr="",
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input={"content": "Replay this task"}),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-1",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="demo",
            path=str(tmp_path / "skills" / "demo" / "SKILL.md"),
        ),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        baseline_skill_root=str(tmp_path / "skills"),
        task_input={"content": "Replay this task"},
        agent="Aworld",
        timeout_seconds=42,
        max_steps=5,
        max_tokens=100,
    )

    backend = AWorldCliCandidateReplayBackend(executor=fake_executor)

    result = await backend.replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=dataset,
    )

    assert result.succeeded is True
    assert [call.variant_id for call in calls] == ["baseline", "cand-1"]
    assert calls[0].skill_root == str(tmp_path / "skills")
    assert calls[1].skill_root == str(tmp_path / "overlay-skills")
    assert calls[0].task_text == "Replay this task"
    assert calls[1].agent == "Aworld"
    assert calls[1].timeout_seconds == 42
    assert result.baseline.trajectory[0]["action"]["content"] == "baseline output"
    assert result.candidate.trajectory[0]["action"]["content"] == "cand-1 output"

    replay_dir = tmp_path / ".aworld" / "self_evolve" / "run-1" / "replay" / "cand-1"
    assert (replay_dir / "request.json").exists()
    assert (replay_dir / "baseline" / "stdout.txt").read_text(encoding="utf-8") == "baseline stdout"
    assert (replay_dir / "cand-1" / "metrics.json").exists()


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_leaves_baseline_loader_default(
    tmp_path: Path,
) -> None:
    calls = []

    async def fake_executor(request):
        calls.append(request)
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[{"action": {"content": request.variant_id}}],
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input={"content": "Replay this task"}),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-1",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="draft-skill",
            path=str(
                tmp_path
                / ".aworld"
                / "self_evolve"
                / "drafts"
                / "skills"
                / "draft-skill"
                / "SKILL.md"
            ),
        ),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        baseline_skill_root=None,
        task_input={"content": "Replay this task"},
    )

    result = await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: draft-skill\n---\n# Draft\n", candidate_id="cand-1"),
        dataset=dataset,
    )

    assert result.succeeded is True
    assert [call.variant_id for call in calls] == ["baseline", "cand-1"]
    assert calls[0].skill_root is None
    assert calls[1].skill_root == str(tmp_path / "overlay-skills")


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_logs_replay_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = []
    monkeypatch.setattr(
        "aworld.self_evolve.replay.logger.info",
        messages.append,
    )

    async def fake_executor(request):
        return ReplayExecutionResult(
            status="succeeded",
            trajectory=[
                {
                    "state": {"input": request.task_input},
                    "action": {"content": request.variant_id},
                    "reward": {"status": "ok"},
                }
            ],
        )

    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input="Replay this task"),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-logs",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
    )

    await AWorldCliCandidateReplayBackend(executor=fake_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=dataset,
    )

    assert any("self_evolve.replay.start" in message for message in messages)
    assert any(
        "self_evolve.replay.repetition.start" in message and "variant_id=baseline" in message
        for message in messages
    )
    assert any(
        "self_evolve.replay.repetition.end" in message and "variant_id=cand-1" in message
        for message in messages
    )
    assert any("self_evolve.replay.end" in message for message in messages)


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_requests_machine_readable_trajectory_and_disables_auto_drain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NO_PROXY", "internal.example")
    monkeypatch.setenv("no_proxy", "legacy.example")
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1080")
    captured: dict[str, object] = {}
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "input": {"content": "Replay this task"},
                "messages": [
                    {
                        "role": "assistant",
                        "raw_response": {
                            "id": "response-usage-1",
                            "usage": {"total_tokens": 321},
                        },
                    }
                ],
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        runtime_root = Path(kwargs["env"]["HOME"]).parent
        captured["runtime_root"] = runtime_root
        captured["runtime_paths_existed"] = all(
            path.is_dir()
            for path in (
                runtime_root / "home",
                runtime_root / "xdg-config",
                runtime_root / "xdg-cache",
                runtime_root / "xdg-data",
                runtime_root / "xdg-state",
                runtime_root / "tmp",
                runtime_root / "memory",
            )
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="human output\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            skill_names=("demo",),
            agent="Aworld",
            max_tool_calls=8,
        )
    )

    assert result.succeeded is True
    assert result.trajectory == trajectory
    assert "total_tokens" not in result.metrics
    assert "--emit-trajectory" in captured["command"]
    assert captured["command"][
        captured["command"].index("--skill") + 1
    ] == "demo"
    task_index = captured["command"].index("--task") + 1
    task_text = captured["command"][task_index]
    assert task_text.startswith("Replay this task")
    assert "Self-evolve replay evidence requirements" in task_text
    assert "artifact-first" in task_text
    assert "`head -N` is not a byte bound" in task_text
    assert "explicit byte-bounded excerpts or selected fields" in task_text
    assert "compacted" in task_text
    assert "Self-evolve replay runtime contract" in task_text
    assert "Required task-plane actions are allowed" in task_text
    assert "control-plane actions require explicit task authorization" in task_text
    assert "External prerequisites are attach-only" in task_text
    assert "Preserve supplied HOME, TMPDIR, XDG_*" in task_text
    assert "Use only AWORLD_REPLAY_ENDPOINT_* endpoints" in task_text
    assert "return prerequisite-unavailable" in task_text
    assert "A valid artifact-backed sample is terminal" in task_text
    assert len(task_text) < 3_500
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["kwargs"]["env"]["AWORLD_SELF_EVOLVE_AUTO_DRAIN"] == "0"
    assert captured["kwargs"]["env"]["AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"] == str(
        tmp_path / "artifacts" / "evidence"
    )
    assert captured["kwargs"]["env"]["AWORLD_REPLAY_ARTIFACT_DIR"] == str(
        tmp_path / "artifacts" / "evidence"
    )
    assert captured["kwargs"]["env"]["AWORLD_SELF_EVOLVE_EVIDENCE_MANIFEST"] == str(
        tmp_path / "artifacts" / "evidence" / "evidence_manifest.jsonl"
    )
    assert captured["kwargs"]["env"]["AWORLD_LOG_PATH"] == str(
        tmp_path / "artifacts" / "logs"
    )
    assert captured["kwargs"]["env"]["AWORLD_TRAJECTORY_LOG_DISABLED"] == "1"
    assert captured["kwargs"]["env"]["AWORLD_TOOL_CALL_LIMIT"] == "8"
    assert captured["kwargs"]["env"]["AWORLD_REPLAY_EVIDENCE_POLICY"] == "1"
    assert captured["kwargs"]["env"]["AWORLD_REPLAY_ARTIFACT_FILE_LIMIT"] == "8"
    assert (
        captured["kwargs"]["env"]["AWORLD_REPLAY_ARTIFACT_BYTE_LIMIT"]
        == "2000000"
    )
    assert captured["kwargs"]["env"][
        "AWORLD_REPLAY_MAX_CONSECUTIVE_FAILED_ACTIONS"
    ] == "2"
    assert captured["kwargs"]["env"][
        "AWORLD_PROMPT_BUDGET_RESERVED_OUTPUT_TOKENS"
    ] == "4096"
    assert captured["kwargs"]["env"][
        "AWORLD_MCP_STDIO_INHERIT_ENV_PREFIXES"
    ] == "AWORLD_REPLAY_"
    assert captured["kwargs"]["start_new_session"] is True
    runtime_root = captured["runtime_root"]
    assert isinstance(runtime_root, Path)
    assert runtime_root.parent.resolve() == Path("/tmp").resolve()
    assert runtime_root.name.startswith("aworld-replay-runtime-")
    assert len(str(runtime_root)) < 100
    assert captured["kwargs"]["env"]["HOME"] == str(runtime_root / "home")
    assert captured["kwargs"]["env"]["XDG_CONFIG_HOME"] == str(
        runtime_root / "xdg-config"
    )
    assert captured["kwargs"]["env"]["XDG_CACHE_HOME"] == str(
        runtime_root / "xdg-cache"
    )
    assert captured["kwargs"]["env"]["XDG_DATA_HOME"] == str(
        runtime_root / "xdg-data"
    )
    assert captured["kwargs"]["env"]["XDG_STATE_HOME"] == str(
        runtime_root / "xdg-state"
    )
    assert captured["kwargs"]["env"]["TMPDIR"] == str(runtime_root / "tmp")
    assert captured["kwargs"]["env"]["AWORLD_MEMORY_ROOT"] == str(
        runtime_root / "memory"
    )
    assert captured["kwargs"]["env"]["NO_PROXY"] == (
        "internal.example,127.0.0.1,localhost,::1"
    )
    assert captured["kwargs"]["env"]["no_proxy"] == (
        "legacy.example,127.0.0.1,localhost,::1"
    )
    assert captured["kwargs"]["env"]["ALL_PROXY"] == (
        "socks5://127.0.0.1:1080"
    )
    assert captured["runtime_paths_existed"] is True
    assert not runtime_root.exists()
    assert "AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR" in task_text
    assert str(tmp_path / "artifacts") in task_text
    assert str(
        tmp_path / "artifacts" / "evidence" / "evidence_manifest.jsonl"
    ) in task_text
    assert "evidence_manifest.jsonl" in task_text


@pytest.mark.asyncio
async def test_required_replay_runtime_builds_parent_attested_v2_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills"
    skill_root.mkdir()
    expected_skill_fingerprint = "sha256:" + "a" * 64
    trajectory = [
        {
            "state": {"input": "task"},
            "action": {
                "content": "done",
                "is_agent_finished": "True",
            },
        }
    ]
    captured: dict[str, Any] = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)
        evidence_manifest = Path(kwargs["evidence_manifest"])
        evidence_path = evidence_manifest.parent / "result.json"
        evidence_path.write_text('{"value":1}', encoding="utf-8")
        evidence_manifest.write_text(
            json.dumps(
                {
                    "source_id": "result",
                    "artifact_path": "result.json",
                    "extraction_method": "bounded_extract",
                    "fields": ["value"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (evidence_manifest.parent / "framework_evidence_state.json").write_text(
            json.dumps(
                {
                    "schema_version": "aworld.replay.evidence_policy.v1",
                    "phase": "evidence_ready",
                    "evidence_policy_mode": "shadow",
                    "evidence_policy_authority": "advisory",
                    "tool_call_attempt_count": 2,
                    "manifest_entry_count": 1,
                    "artifact_file_limit": 64,
                    "artifact_byte_limit": 256_000_000,
                }
            ),
            encoding="utf-8",
        )
        (
            evidence_manifest.parent / "framework_evidence_policy.jsonl"
        ).write_text(
            json.dumps(
                {
                    "schema_version": "aworld.replay.evidence_policy.v1",
                    "evidence_policy_mode": "shadow",
                    "evidence_policy_authority": "advisory",
                    "code": "tool_call_after_evidence_ready",
                    "phase": "evidence_ready",
                    "tool_name": "bash",
                    "action_name": "run",
                    "required_transition": "finalize_task_response",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        response = {
            "schema_version": "aworld.self_evolve.task_response.v1",
            "trajectory": trajectory,
            "trajectory_capture_mode": "task_response",
        }
        response["framework_attestation"] = {
            "schema_version": (
                "aworld.self_evolve.task_response_attestation.v2"
            ),
            "signature": _task_response_signature(
                response, kwargs["task_response_attestation_key"]
            ),
        }
        Path(kwargs["task_response_path"]).write_text(
            json.dumps(response), encoding="utf-8"
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)
    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input="task",
            task_text="task",
            skill_root=str(skill_root),
            skill_names=("demo",),
            artifact_dir=str(tmp_path / "artifacts"),
            evidence_policy_mode="required",
            expected_skill_package_fingerprint=expected_skill_fingerprint,
        )
    )

    assert result.succeeded is True
    assert result.metrics["evidence_policy_v2_runtime_trust_passed"] is True
    # A mocked process that never ran the CLI resolver must not be able to
    # promote request-side expected values into activation evidence.
    assert result.metrics["skill_activation_attested"] is False
    assert result.metrics["activated_skill_names"] == []
    assert result.metrics["activated_skill_package_fingerprint"] is None
    assert result.metrics["evidence_runtime_policy_passed"] is False
    assert result.metrics["evidence_runtime_policy_authority"] == "advisory"
    assert result.metrics[
        "evidence_runtime_policy_authoritative_passed"
    ] is True
    assert result.metrics[
        "evidence_runtime_policy_advisory_violation_count"
    ] == 1
    assert result.metrics["evidence_strategy_passed"] is True
    trusted_manifest = json.loads(
        Path(result.metrics["evidence_policy_v2_manifest_path"]).read_text(
            encoding="utf-8"
        )
    )
    envelope = trusted_manifest["runtime_trust_envelope"]
    assert envelope["evidence_policy_fingerprint"] == result.metrics[
        "evidence_policy_v2_profile_fingerprint"
    ]
    assert envelope["work_unit_fingerprint"] == result.metrics[
        "evidence_policy_v2_work_unit_fingerprint"
    ]
    candidate_env = captured["env"]
    assert "AWORLD_REPLAY_EVIDENCE_WRITER_ATTESTATION_JSON" not in candidate_env
    assert "AWORLD_REPLAY_EVIDENCE_PRODUCERS_JSON" not in candidate_env
    assert "AWORLD_REPLAY_EVIDENCE_POLICY_PROFILE_JSON" not in candidate_env
    assert candidate_env["AWORLD_REPLAY_EVIDENCE_POLICY_MODE"] == "shadow"
    signing_key = captured["task_response_attestation_key"]
    assert isinstance(signing_key, bytes)
    assert signing_key.hex() not in json.dumps(candidate_env, sort_keys=True)


@pytest.mark.asyncio
async def test_required_replay_attests_signed_zero_tool_task_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "state": {"input": "continue the discussion"},
            "action": {
                "content": "A complete answer derived from supplied context.",
                "is_agent_finished": "True",
                "tool_calls": [],
            },
        }
    ]

    def fake_run(command, **kwargs):
        response = {
            "schema_version": "aworld.self_evolve.task_response.v1",
            "trajectory": trajectory,
            "trajectory_capture_mode": "task_response",
        }
        response["framework_attestation"] = {
            "schema_version": "aworld.self_evolve.task_response_attestation.v2",
            "signature": _task_response_signature(
                response,
                kwargs["task_response_attestation_key"],
            ),
        }
        Path(kwargs["task_response_path"]).write_text(
            json.dumps(response),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)
    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="baseline",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input="continue the discussion",
            task_text="continue the discussion",
            skill_root=None,
            artifact_dir=str(tmp_path / "artifacts"),
            evidence_policy_mode="required",
        )
    )

    assert result.succeeded is True
    assert result.metrics["evidence_policy_v2_runtime_trust_passed"] is True
    assert result.metrics["framework_task_response_only_evidence"] is True
    assert result.metrics["framework_trusted_evidence_file_count"] == 1
    receipt = (
        tmp_path
        / "artifacts"
        / "evidence"
        / "framework_task_response_only_evidence.json"
    )
    assert receipt.is_file()
    assert json.loads(receipt.read_text(encoding="utf-8"))[
        "external_tool_call_count"
    ] == 0


@pytest.mark.asyncio
async def test_required_replay_ignores_unexecuted_message_tool_call_proposal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    rejected_call = {
        "id": "call-rejected",
        "function": {
            "name": "bash",
            "arguments": (
                '{"_aworld_replay":"compacted_tool_call_arguments",'
                '"sanitized_reason":"invalid_json_arguments"}'
            ),
        },
    }
    trajectory = [
        {
            "state": {
                "input": "continue the discussion",
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [rejected_call],
                    }
                ],
            },
            "action": {
                "content": "A complete answer derived from supplied context.",
                "is_agent_finished": "True",
                "tool_calls": [],
            },
            "reward": {"tool_outputs": []},
        }
    ]

    def fake_run(command, **kwargs):
        response = {
            "schema_version": "aworld.self_evolve.task_response.v1",
            "trajectory": trajectory,
            "trajectory_capture_mode": "task_response",
        }
        response["framework_attestation"] = {
            "schema_version": "aworld.self_evolve.task_response_attestation.v2",
            "signature": _task_response_signature(
                response,
                kwargs["task_response_attestation_key"],
            ),
        }
        Path(kwargs["task_response_path"]).write_text(
            json.dumps(response),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)
    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="baseline",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input="continue the discussion",
            task_text="continue the discussion",
            skill_root=None,
            artifact_dir=str(tmp_path / "artifacts"),
            evidence_policy_mode="required",
        )
    )

    assert result.succeeded is True
    assert result.metrics["evidence_policy_v2_runtime_trust_passed"] is True
    assert result.metrics["framework_task_response_only_evidence"] is True
    receipt = (
        tmp_path
        / "artifacts"
        / "evidence"
        / "framework_task_response_only_evidence.json"
    )
    assert json.loads(receipt.read_text(encoding="utf-8"))[
        "external_tool_call_count"
    ] == 0


@pytest.mark.asyncio
async def test_required_replay_keeps_evidence_requirement_after_tool_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "state": {"input": "inspect a source"},
            "action": {
                "content": "Claim based on an unpersisted source.",
                "is_agent_finished": "True",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "browser",
                            "arguments": "{}",
                        },
                    }
                ],
            },
        }
    ]

    def fake_run(command, **kwargs):
        response = {
            "schema_version": "aworld.self_evolve.task_response.v1",
            "trajectory": trajectory,
            "trajectory_capture_mode": "task_response",
        }
        response["framework_attestation"] = {
            "schema_version": "aworld.self_evolve.task_response_attestation.v2",
            "signature": _task_response_signature(
                response,
                kwargs["task_response_attestation_key"],
            ),
        }
        Path(kwargs["task_response_path"]).write_text(
            json.dumps(response),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)
    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="baseline",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input="inspect a source",
            task_text="inspect a source",
            skill_root=None,
            artifact_dir=str(tmp_path / "artifacts"),
            evidence_policy_mode="required",
        )
    )

    assert result.status == "failed"
    assert result.failure["code"] == "replay_task_completion_not_established"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.metrics["signed_task_response_validated"] is True
    assert result.metrics["task_completion_established"] is False
    assert result.metrics["replay_counterexamples"][0]["trigger"] == (
        "agent_not_finished"
    )
    assert result.metrics["replay_counterexamples"][0][
        "required_transition"
    ] == "continue_rollout_until_terminal_action"


@pytest.mark.asyncio
async def test_required_replay_classifies_signed_unfinished_tool_turn_before_evidence_finalization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "state": {"input": "framework placeholder"},
            "action": {
                "content": "None",
                "is_agent_finished": True,
                "tool_calls": [],
            },
        },
        {
            "state": {"input": "inspect the supplied page"},
            "action": {
                "content": "I opened the page and need to inspect it next.",
                "is_agent_finished": False,
                "tool_calls": [
                    {
                        "id": "call-open",
                        "function": {
                            "name": "mcp",
                            "arguments": json.dumps(
                                {
                                    "command": (
                                        "agent-browser open "
                                        "supplied-page"
                                    )
                                }
                            ),
                        },
                    }
                ],
            },
        }
    ]

    def fake_run(command, **kwargs):
        protocol_trace = (
            Path(kwargs["artifact_dir"])
            / "replay_services"
            / "service_1"
            / "protocol_trace.log"
        )
        protocol_trace.parent.mkdir(parents=True)
        protocol_trace.write_text(
            '{"kind":"http_response","status":200}\n',
            encoding="utf-8",
        )
        response = {
            "schema_version": "aworld.self_evolve.task_response.v1",
            "trajectory": trajectory,
            "trajectory_capture_mode": "task_response",
        }
        response["framework_attestation"] = {
            "schema_version": "aworld.self_evolve.task_response_attestation.v2",
            "signature": _task_response_signature(
                response,
                kwargs["task_response_attestation_key"],
            ),
        }
        Path(kwargs["task_response_path"]).write_text(
            json.dumps(response),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)
    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="baseline",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input="inspect the supplied page",
            task_text="inspect the supplied page",
            skill_root=None,
            artifact_dir=str(tmp_path / "artifacts"),
            evidence_policy_mode="required",
            max_steps=1,
        )
    )

    assert result.status == "failed"
    assert result.failure["code"] == "replay_task_completion_not_established"
    assert result.failure["outcome"] == "task_failure"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.metrics["signed_task_response_validated"] is True
    assert result.metrics["task_completion_established"] is False
    assert result.metrics["replay_counterexamples"][0]["trigger"] == (
        "agent_not_finished"
    )
    assert result.metrics["replay_counterexamples"][0][
        "required_transition"
    ] == "continue_rollout_until_terminal_action"
    assert not (
        tmp_path
        / "artifacts"
        / "evidence"
        / "framework_canonical_evidence_manifest.jsonl"
    ).exists()


def test_measurement_terminal_state_keeps_framework_evidence_failure_retryable() -> None:
    failure = ReplayFailureEvent(
        code="evidence_policy_v2_attestation_failed",
        owner=FailureOwner.FRAMEWORK,
        stage=FailureStage.EVIDENCE_FINALIZATION,
        scope=FailureScope.SHARED_RUN,
        repairable=True,
    )
    failed = ReplayVariantResult(
        variant_id="baseline",
        status=ReplayExecutionStatus.FAILED,
        trajectory=[],
        failure=failure,
    )
    blocked = ReplayVariantResult(
        variant_id="candidate",
        status=ReplayExecutionStatus.BLOCKED,
        trajectory=[],
        blocked_by=(failure,),
    )

    assert _measurement_terminal_state_for_variant(failed) is (
        MeasurementWorkUnitState.EVIDENCE_INVALID
    )
    assert _measurement_terminal_state_for_variant(blocked) is (
        MeasurementWorkUnitState.EVIDENCE_INVALID
    )


def test_measurement_terminal_state_treats_producer_evidence_failure_as_task_result() -> None:
    failure = ReplayFailureEvent(
        code="replay_evidence_production_failed",
        owner=FailureOwner.TASK,
        stage=FailureStage.EVIDENCE_FINALIZATION,
        scope=FailureScope.MEMBER,
        repairable=False,
    )
    failed = ReplayVariantResult(
        variant_id="baseline",
        status=ReplayExecutionStatus.FAILED,
        trajectory=[{"action": {"content": "evidence omitted"}}],
        failure=failure,
    )

    assert _measurement_terminal_state_for_variant(failed) is (
        MeasurementWorkUnitState.TASK_FAILED
    )


@pytest.mark.asyncio
async def test_required_replay_runtime_inventories_legacy_files_without_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "state": {"input": "task"},
            "action": {"content": "done", "is_agent_finished": "True"},
        }
    ]

    def fake_run(command, **kwargs):
        evidence_dir = Path(kwargs["evidence_manifest"]).parent
        (evidence_dir / "page_text.txt").write_text(
            "framework inventories this legacy artifact",
            encoding="utf-8",
        )
        response = {
            "schema_version": "aworld.self_evolve.task_response.v1",
            "trajectory": trajectory,
            "trajectory_capture_mode": "task_response",
        }
        response["framework_attestation"] = {
            "schema_version": "aworld.self_evolve.task_response_attestation.v2",
            "signature": _task_response_signature(
                response, kwargs["task_response_attestation_key"]
            ),
        }
        Path(kwargs["task_response_path"]).write_text(
            json.dumps(response), encoding="utf-8"
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)
    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="baseline",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input="task",
            task_text="task",
            skill_root=None,
            artifact_dir=str(tmp_path / "artifacts"),
            evidence_policy_mode="required",
        )
    )

    assert result.succeeded is True
    assert result.metrics["evidence_policy_v2_runtime_trust_passed"] is True
    manifest = Path(
        result.metrics["framework_evidence_manifest_path"]
    ).read_text(encoding="utf-8")
    assert "framework.inventory.1" in manifest
    assert not (
        tmp_path / "artifacts" / "evidence" / "evidence_manifest.jsonl"
    ).exists()
    bundle = json.loads(
        (
            tmp_path / "artifacts" / "evidence" / "evidence_bundle.json"
        ).read_text(encoding="utf-8")
    )
    assert bundle["valid"] is True
    assert bundle["entries"][0]["artifact_path"].endswith("page_text.txt")


@pytest.mark.parametrize(
    ("variant_id", "expected_outcome", "expected_owner", "expected_action"),
    (
        (
            "baseline",
            "task_failure",
            "task",
            "repair_target_evidence_production",
        ),
        (
            "candidate-1",
            "candidate_failure",
            "candidate",
            "repair_candidate_evidence_production",
        ),
    ),
)
@pytest.mark.asyncio
async def test_required_replay_attributes_empty_canonical_inventory_to_producer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    variant_id: str,
    expected_outcome: str,
    expected_owner: str,
    expected_action: str,
) -> None:
    trajectory = [
        {
            "state": {"input": "task"},
            "action": {
                "content": "inspect task data",
                "tool_calls": [
                    {
                        "function": {
                            "name": "shell",
                            "arguments": '{"command":"inspect"}',
                        }
                    }
                ],
            },
        },
        {
            "state": {"input": "task"},
            "action": {"content": "done", "is_agent_finished": "True"},
        }
    ]

    def fake_run(command, **kwargs):
        response = {
            "schema_version": "aworld.self_evolve.task_response.v1",
            "trajectory": trajectory,
            "trajectory_capture_mode": "task_response",
        }
        response["framework_attestation"] = {
            "schema_version": "aworld.self_evolve.task_response_attestation.v2",
            "signature": _task_response_signature(
                response, kwargs["task_response_attestation_key"]
            ),
        }
        Path(kwargs["task_response_path"]).write_text(
            json.dumps(response), encoding="utf-8"
        )
        # Reproduce the live campaign defect: the rollout completed and its
        # TaskResponse is valid, but it placed no regular artifact under the
        # parent-designated evidence namespace.
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)
    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id=variant_id,
            task_id="task-empty-evidence",
            candidate_id="candidate-1",
            workspace_root=str(tmp_path),
            task_input="task",
            task_text="task",
            skill_root=(
                None if variant_id == "baseline" else str(tmp_path / "skills")
            ),
            artifact_dir=str(tmp_path / f"artifacts-{variant_id}"),
            evidence_policy_mode="required",
        )
    )

    assert result.status == "failed"
    assert result.failure["code"] == "replay_evidence_production_failed"
    assert result.failure["outcome"] == expected_outcome
    assert result.failure["failure_owner"] == expected_owner
    assert result.failure["failure_scope"] == "member"
    assert result.failure["failure_stage"] == "evidence_finalization"
    assert result.failure["diagnostics"]["producer_failure_code"] == (
        "canonical_evidence_inventory_empty"
    )
    assert result.failure["diagnostics"]["required_action"] == expected_action
    assert result.metrics["signed_task_response_validated"] is True
    assert result.metrics["evidence_policy_v2_runtime_trust_passed"] is False


@pytest.mark.asyncio
async def test_required_replay_uses_parent_inventory_when_child_path_repeats_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "state": {"input": "task"},
            "action": {"content": "done", "is_agent_finished": "True"},
        }
    ]

    def fake_run(command, **kwargs):
        evidence_manifest = Path(kwargs["evidence_manifest"])
        (evidence_manifest.parent / "result.html").write_text(
            "<html>bounded result</html>", encoding="utf-8"
        )
        (tmp_path / "outside.html").write_text(
            "<html>must never be authorized</html>", encoding="utf-8"
        )
        # This is a common legacy spelling: the manifest already lives under
        # evidence/, but the artifact path repeats that directory name.
        evidence_manifest.write_text(
            json.dumps(
                {
                    "source_id": "legacy.result",
                    "artifact_path": "evidence/result.html",
                    "extraction_method": "browser_snapshot",
                    "fields": ["html"],
                }
            )
            + "\n"
            + json.dumps(
                {
                    "source_id": "outside",
                    "artifact_path": str(tmp_path / "outside.html"),
                    "extraction_method": "untrusted_path",
                    "fields": ["html"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        response = {
            "schema_version": "aworld.self_evolve.task_response.v1",
            "trajectory": trajectory,
            "trajectory_capture_mode": "task_response",
        }
        response["framework_attestation"] = {
            "schema_version": "aworld.self_evolve.task_response_attestation.v2",
            "signature": _task_response_signature(
                response, kwargs["task_response_attestation_key"]
            ),
        }
        Path(kwargs["task_response_path"]).write_text(
            json.dumps(response), encoding="utf-8"
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)
    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="baseline",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input="task",
            task_text="task",
            skill_root=None,
            artifact_dir=str(tmp_path / "artifacts"),
            evidence_policy_mode="required",
        )
    )

    assert result.succeeded is True
    assert result.metrics["candidate_evidence_manifest_advisory"] is True
    assert result.metrics["candidate_evidence_manifest_matched_artifact_count"] == 1
    assert result.metrics["candidate_evidence_manifest_diagnostic_count"] == 1
    assert "not in canonical inventory" in result.metrics[
        "candidate_evidence_manifest_diagnostics"
    ][0]
    bundle = json.loads(
        (tmp_path / "artifacts" / "evidence" / "evidence_bundle.json").read_text(
            encoding="utf-8"
        )
    )
    assert bundle["valid"] is True
    assert bundle["entries"][0]["source_id"] == "framework.inventory.1"
    assert bundle["entries"][0]["extraction_method"] == (
        "framework_deterministic_projection"
    )
    assert bundle["entries"][0]["bounded_evidence"]["fields_used"] == [
        "framework_bounded_source_preview"
    ]
    assert "fields" not in bundle["entries"][0]["bounded_evidence"]
    assert bundle["entries"][0]["artifact_path"].endswith("result.html")


@pytest.mark.asyncio
async def test_required_replay_ignores_symlinked_advisory_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {"action": {"content": "done", "is_agent_finished": "True"}}
    ]

    def fake_run(command, **kwargs):
        evidence_manifest = Path(kwargs["evidence_manifest"])
        (evidence_manifest.parent / "result.txt").write_text(
            "bounded result", encoding="utf-8"
        )
        outside_manifest = tmp_path / "outside-manifest.jsonl"
        outside_manifest.write_text(
            '{"source_id":"outside","artifact_path":"outside.txt"}\n',
            encoding="utf-8",
        )
        evidence_manifest.symlink_to(outside_manifest)
        response = {
            "schema_version": "aworld.self_evolve.task_response.v1",
            "trajectory": trajectory,
            "trajectory_capture_mode": "task_response",
        }
        response["framework_attestation"] = {
            "schema_version": "aworld.self_evolve.task_response_attestation.v2",
            "signature": _task_response_signature(
                response, kwargs["task_response_attestation_key"]
            ),
        }
        Path(kwargs["task_response_path"]).write_text(
            json.dumps(response), encoding="utf-8"
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)
    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="baseline",
            task_id="task-symlink-manifest",
            candidate_id="candidate-1",
            workspace_root=str(tmp_path),
            task_input="task",
            task_text="task",
            skill_root=None,
            artifact_dir=str(tmp_path / "artifacts-symlink-manifest"),
            evidence_policy_mode="required",
        )
    )

    assert result.succeeded is True
    assert result.metrics["candidate_evidence_manifest_present"] is True
    assert result.metrics["candidate_evidence_manifest_diagnostic_count"] == 1
    assert result.metrics["candidate_evidence_manifest_diagnostics"] == [
        "manifest is a symlink and was ignored"
    ]


@pytest.mark.asyncio
async def test_required_replay_projects_large_scratch_artifact_before_trust_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [{"action": {"content": "done", "is_agent_finished": "True"}}]

    def fake_run(command, **kwargs):
        evidence_manifest = Path(kwargs["evidence_manifest"])
        artifact = evidence_manifest.parent / "export.pdf"
        artifact.write_bytes(b"%PDF-1.7\n" + b"\xff" * 4_800_000)
        evidence_manifest.write_text(
            json.dumps(
                {
                    "source_id": "candidate-claim-must-not-be-trusted",
                    "artifact_path": "export.pdf",
                    "extraction_method": "candidate-summary",
                    "summary": "unverified candidate summary",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        response = {
            "schema_version": "aworld.self_evolve.task_response.v1",
            "trajectory": trajectory,
            "trajectory_capture_mode": "task_response",
        }
        response["framework_attestation"] = {
            "schema_version": "aworld.self_evolve.task_response_attestation.v2",
            "signature": _task_response_signature(
                response, kwargs["task_response_attestation_key"]
            ),
        }
        Path(kwargs["task_response_path"]).write_text(
            json.dumps(response), encoding="utf-8"
        )
        assert int(kwargs["env"]["AWORLD_REPLAY_ARTIFACT_BYTE_LIMIT"]) > (
            artifact.stat().st_size
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)
    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="baseline",
            task_id="task-large",
            candidate_id="candidate-large",
            workspace_root=str(tmp_path),
            task_input="task",
            task_text="task",
            skill_root=None,
            artifact_dir=str(tmp_path / "artifacts-large"),
            evidence_policy_mode="required",
        )
    )

    assert result.succeeded is True
    assert result.metrics["framework_evidence_inventory_bytes"] > 4_000_000
    assert result.metrics["evidence_policy_v2_runtime_trust_passed"] is True
    bundle = json.loads(
        (
            tmp_path
            / "artifacts-large"
            / "evidence"
            / "evidence_bundle.json"
        ).read_text(encoding="utf-8")
    )
    entry = bundle["entries"][0]
    assert entry["source_id"] == "framework.inventory.1"
    assert "unverified candidate summary" not in json.dumps(entry)
    assert entry["bounded_evidence"]["structured_summary"][
        "projection_kind"
    ] == "framework_binary_identity"


@pytest.mark.asyncio
async def test_required_replay_runtime_rejects_trust_injection_before_rollout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("expensive rollout must not start")

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)
    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input="task",
            task_text="task",
            skill_root=None,
            artifact_dir=str(tmp_path / "artifacts"),
            environment={
                "AWORLD_REPLAY_EVIDENCE_POLICY_FINGERPRINT": "sha256:forged"
            },
            evidence_policy_mode="required",
        )
    )

    assert called is False
    assert result.failure["code"] == "evidence_policy_v2_preflight_failed"
    assert result.metrics["evidence_policy_v2_preflight_passed"] is False


@pytest.mark.asyncio
async def test_required_replay_runtime_rejects_unsigned_task_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [{"action": {"content": "done", "is_agent_finished": "True"}}]

    def fake_run(command, **kwargs):
        evidence_manifest = Path(kwargs["evidence_manifest"])
        (evidence_manifest.parent / "result.json").write_text(
            '{"value":1}', encoding="utf-8"
        )
        evidence_manifest.write_text(
            json.dumps(
                {
                    "source_id": "result",
                    "artifact_path": "result.json",
                    "extraction_method": "bounded_extract",
                    "fields": ["value"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        Path(kwargs["task_response_path"]).write_text(
            json.dumps(
                {
                    "schema_version": "aworld.self_evolve.task_response.v1",
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)
    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input="task",
            task_text="task",
            skill_root=None,
            artifact_dir=str(tmp_path / "artifacts"),
            evidence_policy_mode="required",
        )
    )

    assert result.status == "failed"
    assert result.failure["code"] == "evidence_policy_v2_attestation_failed"
    assert result.failure["failure_owner"] == "framework"
    assert result.failure["failure_scope"] == "shared_run"
    assert result.metrics["evidence_policy_v2_runtime_trust_passed"] is False


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_rejects_undeclared_loopback_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld"},
            "state": {"input": {"content": "Replay this task"}},
            "action": {
                "content": "Try the host service as a fallback.",
                "tool_calls": [
                    {
                        "function": {
                            "name": "mcp",
                            "arguments": json.dumps(
                                {
                                    "command": (
                                        "curl http://127.0.0.1:9222/json/version"
                                    )
                                }
                            ),
                        }
                    }
                ],
            },
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:54321"
            },
        )
    )

    assert result.status == "failed"
    assert result.failure == {
        "type": "ReplayBoundaryViolation",
        "reason": "replay_dependency_boundary_violation",
        "outcome": "task_failure",
        "undeclared_loopback_endpoints": ["http://127.0.0.1:9222"],
    }
    assert result.metrics["replay_dependency_boundary_passed"] is False
    assert result.metrics["undeclared_loopback_endpoint_count"] == 1


def test_replay_dependency_boundary_ignores_unresolved_endpoint_templates() -> None:
    trajectory = [
        {
            "action": {
                "tool_calls": [
                    {
                        "function": {
                            "name": "mcp",
                            "arguments": json.dumps(
                                {
                                    "command": (
                                        "for port in 50001 50002; do "
                                        'curl "http://127.0.0.1:${port}/"; '
                                        "done; "
                                        "curl http://127.0.0.1:50001/"
                                    )
                                }
                            ),
                        }
                    }
                ]
            }
        }
    ]

    failure = _replay_dependency_boundary_failure(
        trajectory,
        environment={
            "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:50001"
        },
    )

    assert failure is None


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_normalizes_stale_workspace_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}
    workspace_root = tmp_path / "aworld"
    workspace_root.mkdir()
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {"input": {"content": "Replay this task"}},
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(workspace_root),
            task_input={"content": "Replay this task"},
            task_text=(
                "Use /Users/manwu/Documents/workspace/aworld/examples/skill_agent/"
                "skills/x-scraper and write /Users/manwu/Documents/workspace/"
                "aworld/x_ai_daily_extra.json"
            ),
            skill_root=str(workspace_root / "skills"),
            artifact_dir=str(workspace_root / "artifacts"),
        )
    )

    task_index = captured["command"].index("--task") + 1
    task_text = captured["command"][task_index]
    assert "/Users/manwu/Documents/workspace/aworld" not in task_text
    assert str(workspace_root / "examples" / "skill_agent" / "skills" / "x-scraper") in task_text
    assert str(workspace_root / "x_ai_daily_extra.json") in task_text


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_accepts_compacted_markers_with_valid_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        artifact_dir = Path(
            kwargs["env"]["AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"]
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = artifact_dir / "episode_extract.txt"
        evidence_path.write_text("bounded non-compacted evidence excerpt", encoding="utf-8")
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "episode_raw",
                    "artifact_path": "episode_extract.txt",
                    "extraction_method": "raw_download",
                    "size_bytes": evidence_path.stat().st_size,
                }
            )
            + "\n"
            + json.dumps(
                {
                    "source_id": "episode",
                    "artifact_path": "episode_extract.txt",
                    "extraction_method": "bounded_extract",
                    "bounded_excerpts": {
                        "summary": "bounded non-compacted evidence excerpt",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    assert result.succeeded is True
    assert result.failure is None
    assert result.metrics["evidence_compacted"] is True
    assert result.metrics["evidence_strategy_passed"] is True
    assert result.metrics["evidence_manifest_present"] is True
    assert result.metrics["evidence_manifest_entry_count"] == 2
    assert "evidence_manifest_invalid_entry_count" not in result.metrics
    bundle = json.loads(
        (tmp_path / "artifacts" / "evidence" / "evidence_bundle.json").read_text()
    )
    assert bundle["valid"] is True
    assert bundle["entries"][0]["bounded_evidence"]["source"] == "artifact_preview"
    assert (
        bundle["entries"][0]["bounded_evidence"]["bounded_excerpt"]
        == "bounded non-compacted evidence excerpt"
    )
    assert bundle["entries"][0]["bounded_evidence"]["truncated"] is False


def test_evidence_manifest_accepts_consecutive_pretty_printed_json_objects(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    first_artifact = artifact_dir / "first.txt"
    second_artifact = artifact_dir / "second.txt"
    first_artifact.write_text("first bounded evidence", encoding="utf-8")
    second_artifact.write_text("second bounded evidence", encoding="utf-8")
    manifest = artifact_dir / "evidence_manifest.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "source_id": "first",
                "artifact_path": str(first_artifact),
                "extraction_method": "bounded_extract",
                "bounded_excerpt": "first bounded evidence",
            },
            indent=2,
        )
        + "\n"
        + json.dumps(
            {
                "source_id": "second",
                "artifact_path": str(second_artifact),
                "extraction_method": "bounded_extract",
                "bounded_excerpt": "second bounded evidence",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    metrics = _evidence_manifest_metrics(
        artifact_dir=artifact_dir,
        evidence_manifest=manifest,
        workspace_root=tmp_path,
    )

    assert metrics["evidence_manifest_valid"] is True
    assert metrics["evidence_manifest_entry_count"] == 2
    assert "evidence_manifest_invalid_entry_count" not in metrics
    assert metrics["evidence_bundle_valid"] is True


def test_final_answer_artifact_references_are_reconciled_with_bundle(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    manifested = artifact_dir / "source.json"
    manifested.write_text("{}", encoding="utf-8")
    (artifact_dir / "unregistered.txt").write_text("raw", encoding="utf-8")
    (artifact_dir / "evidence_bundle.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "source_id": "source",
                        "artifact_path": str(manifested),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    trajectory = [
        {
            "action": {
                "content": (
                    "Evidence: `source.json`, `unregistered.txt`, and "
                    "`evidence_manifest.jsonl`."
                ),
                "is_agent_finished": True,
            }
        }
    ]

    metrics = _final_answer_artifact_reference_metrics(
        trajectory=trajectory,
        artifact_dir=artifact_dir,
    )

    assert metrics["evidence_artifact_reference_count"] == 3
    assert metrics["evidence_manifested_artifact_reference_count"] == 2
    assert metrics["evidence_unmanifested_artifact_reference_count"] == 1
    assert len(
        metrics["evidence_unmanifested_artifact_reference_identity_digests"]
    ) == 1
    assert "unregistered.txt" not in json.dumps(metrics)


def test_evidence_manifest_normalizes_bounded_excerpt_fields(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    artifact = artifact_dir / "source.json"
    artifact.write_text("x" * 20_000, encoding="utf-8")
    manifest = artifact_dir / "evidence_manifest.jsonl"
    excerpts = [
        "Evaluation runs compare outputs against explicit criteria.",
        "Scores can be attached to traces for later analysis.",
    ]
    manifest.write_text(
        json.dumps(
            {
                "source_id": "documentation",
                "artifact_path": str(artifact),
                "extraction_method": "selected_fields",
                "bounded_excerpt_fields": excerpts,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    metrics = _evidence_manifest_metrics(
        artifact_dir=artifact_dir,
        evidence_manifest=manifest,
        workspace_root=tmp_path,
    )
    bundle = json.loads((artifact_dir / "evidence_bundle.json").read_text())

    assert metrics["evidence_manifest_valid"] is True
    assert metrics["evidence_bundle_valid"] is True
    assert bundle["entries"][0]["bounded_evidence"] == {
        "bounded_excerpts": excerpts,
    }


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_writes_canonical_evidence_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        artifact_dir = Path(
            kwargs["env"]["AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"]
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = artifact_dir / "bounded_extract.txt"
        evidence_path.write_text("bounded non-compacted evidence excerpt", encoding="utf-8")
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "source-1",
                    "evidence_type": "file",
                    "artifact_path": "bounded_extract.txt",
                    "extraction_method": "bounded_extract",
                    "bounded_excerpt": "bounded non-compacted evidence excerpt",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    bundle_path = tmp_path / "artifacts" / "evidence" / "evidence_bundle.json"
    evidence_path = tmp_path / "artifacts" / "evidence" / "bounded_extract.txt"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))

    assert result.succeeded is True
    assert result.metrics["evidence_compacted"] is True
    assert result.metrics["evidence_strategy_passed"] is True
    assert result.metrics["evidence_bundle_present"] is True
    assert result.metrics["evidence_bundle_valid"] is True
    assert result.metrics["evidence_bundle_entry_count"] == 1
    assert result.metrics["evidence_bundle_path"] == str(bundle_path)
    assert result.metrics["evidence_manifest_readable"] is True
    assert result.metrics["evidence_manifest_size_bytes"] > 0
    assert result.metrics["evidence_manifest_fingerprint"].startswith("sha256:")
    assert bundle["format"] == "aworld.self_evolve.evidence_bundle"
    assert bundle["manifest"] == {
        "path": str(
            tmp_path / "artifacts" / "evidence" / "evidence_manifest.jsonl"
        ),
        "present": True,
        "readable": True,
        "valid": True,
        "entry_count": 1,
        "invalid_entry_count": 0,
        "size_bytes": result.metrics["evidence_manifest_size_bytes"],
        "fingerprint": result.metrics["evidence_manifest_fingerprint"],
    }
    assert bundle["entries"][0]["source_id"] == "source-1"
    assert bundle["entries"][0]["artifact_path"] == str(evidence_path)
    assert bundle["entries"][0]["bounded_evidence"]["bounded_excerpt"] == (
        "bounded non-compacted evidence excerpt"
    )


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_accepts_non_file_evidence_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Notification scheduled.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        artifact_dir = Path(
            kwargs["env"]["AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"]
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "scheduled_notification",
                    "evidence_type": "metadata",
                    "extraction_method": "scheduler_response",
                    "metadata": {
                        "operation": "schedule_notification",
                        "reference_id": "job-123",
                        "status": "scheduled",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    assert result.succeeded is True
    assert result.metrics["evidence_bundle_valid"] is True
    bundle = json.loads(
        (tmp_path / "artifacts" / "evidence" / "evidence_bundle.json").read_text()
    )
    assert bundle["entries"] == [
        {
            "bounded_evidence": {},
            "evidence_type": "metadata",
            "extraction_method": "scheduler_response",
            "metadata": {
                "operation": "schedule_notification",
                "reference_id": "job-123",
                "status": "scheduled",
            },
            "source_id": "scheduled_notification",
        }
    ]


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_canonicalizes_bounded_metadata_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Analysis completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        artifact_dir = Path(
            kwargs["env"]["AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"]
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "aggregate_analysis",
                    "evidence_type": "metadata",
                    "extraction_method": "bounded structured synthesis",
                    "bounded_excerpt": {
                        "sources_examined": 4,
                        "claims_supported": ["claim-a", "claim-b"],
                        "complete": True,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    assert result.succeeded is True
    assert result.metrics["evidence_bundle_valid"] is True
    assert "evidence_manifest_invalid_entry_count" not in result.metrics
    bundle = json.loads(
        (tmp_path / "artifacts" / "evidence" / "evidence_bundle.json").read_text()
    )
    assert bundle["entries"][0]["metadata"] == {
        "bounded_excerpt": {
            "sources_examined": 4,
            "claims_supported": ["claim-a", "claim-b"],
            "complete": True,
        }
    }


def test_replay_evidence_manifest_rejects_oversized_metadata(tmp_path: Path) -> None:
    reason = _invalid_evidence_manifest_entry_reason(
        {
            "source_id": "operation_result",
            "evidence_type": "metadata",
            "extraction_method": "structured_result",
            "metadata": {"value": "x" * 20_000},
        },
        artifact_dir=tmp_path,
    )

    assert reason == "metadata exceeds bounded evidence limit"


def test_replay_evidence_manifest_rejects_oversized_manifest_file(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    manifest = artifact_dir / "evidence_manifest.jsonl"
    manifest.write_bytes(b"x" * (1024 * 1024 + 1))

    metrics = _evidence_manifest_metrics(
        artifact_dir=artifact_dir,
        evidence_manifest=manifest,
        workspace_root=tmp_path,
    )
    bundle = json.loads(
        (artifact_dir / "evidence_bundle.json").read_text(encoding="utf-8")
    )

    assert metrics["evidence_manifest_present"] is True
    assert metrics["evidence_manifest_readable"] is True
    assert metrics["evidence_manifest_valid"] is False
    assert metrics["evidence_manifest_invalid_entry_count"] == 1
    assert "byte limit" in metrics["evidence_manifest_invalid_reasons"][0]
    assert bundle["valid"] is False
    assert bundle["manifest"]["size_bytes"] == 1024 * 1024 + 1
    assert bundle["manifest"]["valid"] is False


def test_replay_evidence_manifest_rejects_excessive_entry_count(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    manifest = artifact_dir / "evidence_manifest.jsonl"
    manifest.write_text(
        "\n".join(
            json.dumps(
                {
                    "source_id": f"source-{index}",
                    "evidence_type": "metadata",
                    "extraction_method": "bounded structured synthesis",
                    "bounded_excerpt": {"index": index},
                }
            )
            for index in range(257)
        )
        + "\n",
        encoding="utf-8",
    )

    metrics = _evidence_manifest_metrics(
        artifact_dir=artifact_dir,
        evidence_manifest=manifest,
        workspace_root=tmp_path,
    )
    bundle = json.loads(
        (artifact_dir / "evidence_bundle.json").read_text(encoding="utf-8")
    )

    assert metrics["evidence_manifest_valid"] is False
    assert metrics["evidence_manifest_entry_count"] == 256
    assert metrics["evidence_manifest_invalid_entry_count"] == 1
    assert "entry limit" in metrics["evidence_manifest_invalid_reasons"][0]
    assert len(bundle["entries"]) == 256
    assert bundle["manifest"]["entry_count"] == 256
    assert bundle["manifest"]["valid"] is False


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_reports_compacted_argument_without_evidence_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": (
                            "replay_compacted_argument_unavailable: tool call argument "
                            "contains compacted_string_field"
                        ),
                    }
                ]
            },
            "action": {"content": "Replay stopped.", "is_agent_finished": "True"},
            "reward": {"status": "failed"},
        }
    ]

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    assert result.succeeded is False
    assert result.failure == {
        "reason": "replay_compacted_argument_unavailable",
        "detail": "replay stopped before executing compacted tool arguments",
    }
    assert result.metrics["replay_compacted_argument_blocked"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_archives_workspace_manifest_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir(parents=True, exist_ok=True)
        artifact_dir = Path(
            kwargs["env"]["AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"]
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        output_path = workspace_root / "x_ai_daily_extra.json"
        output_path.write_text(
            json.dumps({"meta": {"count": 1}, "tweets": [{"text": "AI news"}]}),
            encoding="utf-8",
        )
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "workspace_output",
                    "artifact_path": str(output_path),
                    "extraction_method": "task_output_json",
                    "fields_used": ["content"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path / "workspace"),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "workspace" / "skills"),
            artifact_dir=str(tmp_path / "workspace" / "artifacts"),
        )
    )

    assert result.succeeded is True
    assert result.failure is None
    assert result.metrics["evidence_manifest_entry_count"] == 1
    assert result.metrics["evidence_manifest_archived_entry_count"] == 1
    assert "evidence_manifest_invalid_entry_count" not in result.metrics

    bundle = json.loads(
        (
            tmp_path
            / "workspace"
            / "artifacts"
            / "evidence"
            / "evidence_bundle.json"
        ).read_text(
            encoding="utf-8"
        )
    )
    archived_path = Path(bundle["entries"][0]["artifact_path"])
    assert archived_path.is_relative_to(tmp_path / "workspace" / "artifacts")
    assert archived_path.exists()
    assert bundle["entries"][0]["bounded_evidence"]["source"] == "artifact_preview"


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_rejects_untrusted_manifest_artifact_outside_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir(parents=True, exist_ok=True)
        artifact_dir = Path(
            kwargs["env"]["AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"]
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        outside_path = tmp_path / "outside.txt"
        outside_path.write_text("secret should not be allowlisted", encoding="utf-8")
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "outside",
                    "artifact_path": str(outside_path),
                    "extraction_method": "outside_file",
                    "fields_used": ["content"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path / "workspace"),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "workspace" / "skills"),
            artifact_dir=str(tmp_path / "workspace" / "artifacts"),
        )
    )

    assert result.succeeded is False
    assert result.failure["reason"] == "evidence_quality_failed"
    assert result.metrics["evidence_manifest_entry_count"] == 0
    assert result.metrics["evidence_manifest_invalid_entry_count"] == 1
    assert result.metrics["evidence_manifest_invalid_reasons"] == [
        "line 1: artifact_path is outside trusted replay/workspace directories"
    ]


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_accepts_bounded_excerpt_for_outside_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir(parents=True, exist_ok=True)
        artifact_dir = Path(
            kwargs["env"]["AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"]
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        outside_path = tmp_path / "scrape_stderr.log"
        outside_path.write_text("large outside log should not be read", encoding="utf-8")
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "scrape_stderr_log",
                    "artifact_path": str(outside_path),
                    "extraction_method": "stderr capture",
                    "fields": ["scroll_rounds", "final_total", "ai_count"],
                    "bounded_excerpt": (
                        "search flow: 10 scrolls, 121 raw -> 20 deduped; "
                        "RESULT: total=20, ai_count=16"
                    ),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path / "workspace"),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "workspace" / "skills"),
            artifact_dir=str(tmp_path / "workspace" / "artifacts"),
        )
    )

    assert result.succeeded is True
    assert result.failure is None
    assert result.metrics["evidence_manifest_entry_count"] == 1
    assert "evidence_manifest_invalid_entry_count" not in result.metrics
    bundle = json.loads(
        (
            tmp_path
            / "workspace"
            / "artifacts"
            / "evidence"
            / "evidence_bundle.json"
        ).read_text(
            encoding="utf-8"
        )
    )
    assert bundle["valid"] is True
    assert bundle["entries"][0]["bounded_evidence"]["bounded_excerpt"].startswith(
        "search flow"
    )
    assert bundle["entries"][0]["bounded_evidence"]["fields"] == [
        "scroll_rounds",
        "final_total",
        "ai_count",
    ]


def test_replay_aggregate_metrics_include_bundle_validity() -> None:
    from aworld.self_evolve.replay import _aggregate_variant_results

    artifact_dir = Path("/tmp/self-evolve-replay-aggregate")
    results = [
        ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=[{"action": {"content": "answer 1"}}],
            metrics={
                "evidence_bundle_valid": True,
                "evidence_bundle_entry_count": 2,
                "evidence_bundle_path": "/tmp/bundle-1.json",
            },
        ),
        ReplayVariantResult(
            variant_id="cand-2",
            status="succeeded",
            trajectory=[{"action": {"content": "answer 2"}}],
            metrics={
                "evidence_bundle_valid": True,
                "evidence_bundle_entry_count": 4,
                "evidence_bundle_path": "/tmp/bundle-2.json",
            },
        ),
    ]

    aggregate = _aggregate_variant_results(
        base_variant_id="candidate",
        results=results,
        artifact_dir=artifact_dir,
    )

    assert aggregate.metrics["evidence_bundle_valid"] is True
    assert aggregate.metrics["evidence_bundle_valid_values"] == [True, True]
    assert aggregate.metrics["evidence_bundle_valid_coverage_count"] == 2
    assert aggregate.metrics["evidence_bundle_entry_count"] == 3.0
    assert aggregate.metrics["evidence_bundle_entry_count_values"] == [2.0, 4.0]
    assert aggregate.metrics["evidence_bundle_entry_count_min"] == 2.0
    assert aggregate.metrics["evidence_bundle_entry_count_coverage_count"] == 2
    assert aggregate.metrics["evidence_bundle_path"] == "/tmp/bundle-2.json"


def test_replay_aggregate_preserves_authoritative_policy_verdict() -> None:
    results = [
        ReplayVariantResult(
            variant_id=f"candidate-{index}",
            status="succeeded",
            trajectory=[{"action": {"content": "completed"}}],
            metrics={
                "evidence_runtime_policy_passed": False,
                "evidence_runtime_policy_authoritative_passed": True,
            },
        )
        for index in range(2)
    ]

    aggregate = _aggregate_variant_results(
        base_variant_id="candidate",
        results=results,
        artifact_dir=Path("/tmp/self-evolve-authority-aggregate"),
    )

    assert aggregate.metrics["evidence_runtime_policy_passed"] is False
    assert (
        aggregate.metrics["evidence_runtime_policy_authoritative_passed"]
        is True
    )


def test_replay_aggregate_evidence_metrics_fail_closed_on_missing_repetition() -> None:
    from aworld.self_evolve.replay import _aggregate_variant_results

    results = [
        ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=[{"action": {"content": "answer 1"}}],
            metrics={
                "evidence_strategy_passed": True,
                "evidence_manifest_present": True,
                "evidence_manifest_readable": True,
                "evidence_manifest_valid": True,
                "evidence_manifest_entry_count": 2,
                "evidence_bundle_present": True,
                "evidence_bundle_valid": True,
                "evidence_bundle_entry_count": 2,
            },
        ),
        ReplayVariantResult(
            variant_id="cand-2",
            status="succeeded",
            trajectory=[{"action": {"content": "answer 2"}}],
            metrics={
                "evidence_strategy_passed": True,
                "evidence_manifest_present": True,
                "evidence_manifest_readable": True,
                "evidence_manifest_valid": True,
                "evidence_manifest_entry_count": 4,
                "evidence_bundle_present": True,
                "evidence_bundle_valid": True,
                "evidence_bundle_entry_count": 4,
            },
        ),
        ReplayVariantResult(
            variant_id="cand-3",
            status="succeeded",
            trajectory=[{"action": {"content": "answer 3"}}],
            metrics={
                "evidence_manifest_present": False,
                "evidence_manifest_readable": False,
                "evidence_manifest_valid": False,
                "evidence_manifest_entry_count": 0,
            },
        ),
    ]

    aggregate = _aggregate_variant_results(
        base_variant_id="candidate",
        results=results,
        artifact_dir=Path("/tmp/self-evolve-replay-aggregate-missing"),
    )

    assert aggregate.metrics["evidence_manifest_valid"] is False
    assert aggregate.metrics["evidence_manifest_valid_values"] == [True, True, False]
    assert aggregate.metrics["evidence_manifest_valid_coverage_count"] == 3
    assert aggregate.metrics["evidence_bundle_valid"] is False
    assert aggregate.metrics["evidence_bundle_valid_values"] == [True, True, False]
    assert aggregate.metrics["evidence_bundle_valid_coverage_count"] == 2
    assert aggregate.metrics["evidence_bundle_entry_count_values"] == [2.0, 4.0, 0.0]
    assert aggregate.metrics["evidence_bundle_entry_count_min"] == 0.0
    assert aggregate.metrics["evidence_bundle_entry_count_coverage_count"] == 2
    assert aggregate.metrics["evidence_strategy_passed"] is False
    assert aggregate.metrics["evidence_strategy_passed_values"] == [True, True, False]
    assert aggregate.metrics["evidence_strategy_passed_coverage_count"] == 2
    assert aggregate.metrics["evidence_strategy_passed_coverage"] == pytest.approx(2 / 3)


def test_replay_aggregate_preserves_any_timeout_and_requires_all_completion(
    tmp_path: Path,
) -> None:
    results = [
        ReplayVariantResult(
            variant_id="cand-1",
            status="succeeded",
            trajectory=[{"action": {"content": "completed"}}],
            metrics={
                "task_completion_established": True,
                "timeout_evidence_recovered": False,
            },
        ),
        ReplayVariantResult(
            variant_id="cand-2",
            status="failed",
            trajectory=[],
            metrics={
                "task_completion_established": False,
                "timeout_evidence_recovered": True,
            },
            failure=ReplayFailureEvent(
                code="replay_task_timeout_with_recoverable_evidence",
                owner=FailureOwner.CANDIDATE,
                stage=FailureStage.TASK_ROLLOUT,
                scope=FailureScope.MEMBER,
                repairable=True,
            ),
        ),
    ]

    aggregate = _aggregate_variant_results(
        base_variant_id="candidate",
        results=results,
        artifact_dir=tmp_path,
    )

    assert aggregate.metrics["task_completion_established"] is False
    assert aggregate.metrics["timeout_evidence_recovered"] is True
    assert aggregate.metrics["task_completion_established_values"] == [True, False]
    assert aggregate.metrics["timeout_evidence_recovered_values"] == [False, True]


def test_replay_runtime_policy_violation_becomes_bounded_counterexample(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    (artifact_dir / "framework_evidence_state.json").write_text(
        json.dumps(
            {
                "schema_version": "aworld.replay.evidence_policy.v1",
                "phase": "evidence_ready",
                "tool_call_attempt_count": 5,
                "manifest_entry_count": 1,
                "artifact_file_limit": 8,
                "artifact_byte_limit": 2_000_000,
            }
        ),
        encoding="utf-8",
    )
    (artifact_dir / "framework_evidence_policy.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "aworld.replay.evidence_policy.v1",
                "code": "tool_call_after_evidence_ready",
                "phase": "evidence_ready",
                "tool_name": "bash",
                "action_name": "run",
                "manifest_entry_count": 1,
                "artifact_file_count": 1,
                "artifact_bytes": 128,
                "required_transition": "finalize_task_response",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    metrics = _replay_evidence_runtime_policy_metrics(artifact_dir)

    assert metrics["evidence_runtime_policy_active"] is True
    assert metrics["evidence_runtime_policy_passed"] is False
    assert metrics["evidence_runtime_policy_authority"] == "authoritative"
    assert metrics["evidence_runtime_policy_authoritative_passed"] is False
    assert metrics["evidence_runtime_policy_advisory_violation_count"] == 0
    assert metrics["evidence_runtime_policy_violation_count"] == 1
    assert metrics["replay_counterexamples"] == [
        {
            "schema_version": "aworld.replay.counterexample.v1",
            "sequence": 1,
            "failure_code": "tool_call_after_evidence_ready",
            "stage": "task_rollout",
            "state_before": "evidence_ready",
            "trigger": "tool_call",
            "tool_name": "bash",
            "action_name": "run",
            "manifest_entry_count": 1,
            "artifact_file_count": 1,
            "artifact_bytes": 128,
            "required_transition": "finalize_task_response",
        }
    ]


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_rejects_compacted_evidence_without_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "Aworld", "pre_agent": "runner"},
            "state": {
                "messages": [
                    {
                        "role": "tool",
                        "content": "Tool output compacted for context reuse.",
                    }
                ]
            },
            "action": {"content": "Replay completed.", "is_agent_finished": "True"},
            "reward": {"status": "ok"},
        }
    ]

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Tool output compacted for context reuse.\n"
            + json.dumps(
                {
                    "trajectory": trajectory,
                    "trajectory_capture_mode": "task_response",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
        )
    )

    assert result.succeeded is False
    assert result.failure["reason"] == "evidence_quality_failed"
    assert result.metrics["evidence_compacted"] is True
    assert result.metrics["evidence_strategy_passed"] is False
    assert result.metrics["evidence_compaction_signals"] == [
        "tool_output_compacted"
    ]


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_rejects_summary_synthetic_trajectory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "trajectory": [
                        {
                            "state": {"input": {"content": "Replay this task"}},
                            "action": {"content": "summary only", "tool_calls": []},
                            "reward": {"status": "ok"},
                        }
                    ],
                    "trajectory_capture_mode": "summary_synthetic",
                }
            )
            + "\n",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            agent="Aworld",
        )
    )

    assert result.succeeded is False
    assert result.failure["code"] == "replay_task_completion_not_established"
    assert result.failure["diagnostics"]["trajectory_capture_mode"] == (
        "summary_synthetic"
    )
    assert result.failure["diagnostics"]["replay_counterexamples"][0][
        "trigger"
    ] == "unsupported_trajectory_capture_mode"


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_stops_worker_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    started = threading.Event()
    stopped = threading.Event()

    def fake_run(command, **kwargs):
        cancellation_event = kwargs["cancellation_event"]
        started.set()
        assert cancellation_event.wait(timeout=1)
        stopped.set()
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)
    running = asyncio.create_task(
        AWorldCliReplayExecutor()(
            ReplayExecutionRequest(
                variant_id="candidate",
                task_id="task-1",
                candidate_id="cand-1",
                workspace_root=str(tmp_path),
                task_input={"content": "Replay this task"},
                task_text="Replay this task",
                skill_root=str(tmp_path / "skills"),
                artifact_dir=str(tmp_path / "artifacts"),
                timeout_seconds=60,
            )
        )
    )
    assert await asyncio.to_thread(started.wait, 1)
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running
    assert stopped.wait(timeout=1)


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_decodes_timeout_output_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        (tmp_path / "scrape_output.log").write_text(
            "WebSocket protocol error: HTTP version must be 1.1 or higher; "
            "API_KEY=top-secret-value",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                b"partial stdout: CDP discovery failed at "
                b"/Users/example/private/runtime.py"
            ),
            stderr=b"partial stderr API_KEY=top-secret-value",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.succeeded is False
    assert result.stdout.startswith("partial stdout: CDP discovery failed")
    assert result.stderr.startswith("partial stderr")
    assert result.failure == {
        "type": "TimeoutExpired",
        "reason": "replay timed out",
        "outcome": "candidate_failure",
        "failure_class": "candidate_replay_capability",
        "failure_stage": "task_rollout",
        "repairable": True,
        "termination_kind": "budget_exhausted",
        "termination_budget_axis": "wall_time",
        "timeout_seconds": 1,
        "max_steps": None,
        "max_tool_calls": 24,
        "tool_calls_used": 0,
        "terminal_synthesis_attempted": False,
        "evidence_phase": "collecting",
        "diagnostics": {
            "stdout_tail": "partial stdout: CDP discovery failed at <LOCAL_PATH>",
            "stderr_tail": "partial stderr <REDACTED_SECRET>",
            "task_artifacts": [
                {
                    "path": "workspace/scrape_output.log",
                    "tail": (
                        "WebSocket protocol error: HTTP version must be 1.1 "
                        "or higher; <REDACTED_SECRET>"
                    ),
                }
            ],
            "termination_kind": "budget_exhausted",
            "termination_budget_axis": "wall_time",
            "timeout_seconds": 1,
            "max_steps": None,
            "max_tool_calls": 24,
            "tool_calls_used": 0,
            "terminal_synthesis_attempted": False,
            "evidence_phase": "collecting",
        },
    }


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_ignores_static_task_contract_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                "Current task: use http://127.0.0.1:49533\n"
                "On the first terminal protocol signal, such as a protocol error, "
                "report a replay capability mismatch.\n"
                "🔄 Running task: task_20260716110210\n"
            ).encode(),
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.succeeded is False
    assert result.failure["type"] == "TimeoutExpired"
    assert "failure_class" not in result.failure
    assert "repairable" not in result.failure


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_closed_cdp_response_channel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        (tmp_path / "scrape_stdout.log").write_text(
            "CDP response channel closed",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=b"The browser task remained active without producing output",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_browser_operation_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        (tmp_path / "scrape_stdout.log").write_text(
            "Operation timed out. The page may still be loading or the element may not exist.",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=b"The browser replay task did not produce an artifact",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_replay_endpoint_navigation_stall(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                b"The script hung during navigation against "
                b"http://127.0.0.1:49533 while waiting for the page to load"
            ),
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_incomplete_navigation_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                b"The script is still navigating on http://127.0.0.1:49533; "
                b"it exited without producing output"
            ),
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_localized_navigation_stall(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                "Replay endpoint: http://127.0.0.1:49533\n"
                "[03:12:23] 正在导航到 X 首页..."
            ).encode(),
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_unresponsive_bound_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                b"The task is stuck while connecting to "
                b"http://127.0.0.1:49533 and the service is unresponsive"
            ),
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_wrong_bound_endpoint_protocol(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                b"The endpoint at http://127.0.0.1:49533 appears to be a "
                b"fixture service, not a required browser protocol endpoint"
            ),
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_classifies_bound_endpoint_schema_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=(
                b"The supplied endpoint at port 49533 failed to deserialize "
                b"the protocol response: missing field sessionId"
            ),
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_trusts_scoped_task_protocol_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        artifact_dir = Path(
            kwargs["env"]["AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"]
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "scrape_output.log").write_text(
            "Failed to deserialize protocol response: missing field sessionId",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=b"The replay task did not finish",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant_id",
    (
        "candidate",
        "baseline",
    ),
)
async def test_aworld_cli_replay_executor_preserves_timeout_with_recoverable_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    variant_id: str,
) -> None:
    def fake_run(command, **kwargs):
        artifact_dir = Path(
            kwargs["env"]["AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"]
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = artifact_dir / "x_ai_daily_extra.json"
        evidence_path.write_text(
            json.dumps(
                {
                    "meta": {"count": 1, "ai_related_count": 1},
                    "tweets": [
                        {
                            "author_name": "A",
                            "author_handle": "@a",
                            "time": "now",
                            "text": "OpenAI agent update",
                            "link": "https://x.com/a/status/1",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "final_output",
                    "artifact_path": "x_ai_daily_extra.json",
                    "extraction_method": "bounded_replay_extract",
                    "fields": ["meta.count", "meta.ai_related_count", "tweets[].link"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=b"partial stdout",
            stderr=b"partial stderr",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id=variant_id,
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
        )
    )

    assert result.succeeded is False
    assert result.status == "failed"
    assert result.failure["code"] == "replay_task_timeout_with_recoverable_evidence"
    assert result.failure["outcome"] == "task_failure"
    assert result.failure["failure_class"] == (
        "task_timeout_with_recoverable_evidence"
    )
    assert result.failure["repairable"] is False
    assert result.failure["termination_kind"] == "budget_exhausted"
    assert result.failure["termination_budget_axis"] == "wall_time"
    assert result.failure["terminal_synthesis_attempted"] is False
    assert result.failure["diagnostics"]["evidence_recoverable"] is True
    assert result.stdout == "partial stdout"
    assert result.stderr == "partial stderr"
    assert result.metrics["timeout_evidence_recovered"] is True
    assert result.metrics["task_completion_established"] is False
    assert result.metrics["evidence_bundle_valid"] is True
    assert result.trajectory == []
    counterexample = result.failure["diagnostics"]["replay_counterexamples"][0]
    assert counterexample["owner"] == "task"
    assert counterexample["state_before"] == "evidence_ready"
    assert counterexample["required_transition"] == (
        "finalize_task_response_before_timeout"
    )


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_rejects_zero_exit_without_task_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="ordinary process output without trajectory framing",
            stderr="",
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
        )
    )

    assert result.succeeded is False
    assert result.failure["code"] == "replay_task_completion_not_established"
    assert result.failure["outcome"] == "candidate_failure"
    assert result.metrics["task_completion_established"] is False
    assert result.metrics["replay_counterexamples"][0]["owner"] == "candidate"
    assert result.metrics["replay_counterexamples"][0]["trigger"] == (
        "trajectory_unavailable"
    )


@pytest.mark.asyncio
async def test_aworld_cli_replay_executor_does_not_recover_dependency_mismatch_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run(command, **kwargs):
        artifact_dir = Path(
            kwargs["env"]["AWORLD_SELF_EVOLVE_REPLAY_ARTIFACT_DIR"]
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "diag_replay_capability_mismatch.json").write_text(
            json.dumps(
                {
                    "diagnostic_type": "replay_capability_mismatch",
                    "endpoint": "http://127.0.0.1:49533",
                    "error": "WebSocket protocol error: missing upgrade header",
                }
            ),
            encoding="utf-8",
        )
        (artifact_dir / "evidence_manifest.jsonl").write_text(
            json.dumps(
                {
                    "source_id": "replay_mismatch",
                    "extraction_method": "diagnostic",
                    "evidence_type": "metadata",
                    "metadata": {
                        "diagnostic_type": "replay_capability_mismatch",
                        "endpoint": "http://127.0.0.1:49533",
                        "error": "WebSocket protocol error",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
        )

    monkeypatch.setattr("aworld.self_evolve.replay._run_replay_cli", fake_run)

    result = await AWorldCliReplayExecutor()(
        ReplayExecutionRequest(
            variant_id="candidate",
            task_id="task-1",
            candidate_id="cand-1",
            workspace_root=str(tmp_path),
            task_input={"content": "Replay this task"},
            task_text="Replay this task",
            skill_root=str(tmp_path / "skills"),
            artifact_dir=str(tmp_path / "artifacts"),
            timeout_seconds=1,
            environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )
    )

    assert result.succeeded is False
    assert result.metrics.get("timeout_evidence_recovered") is None
    assert result.failure["outcome"] == "candidate_failure"
    assert result.failure["failure_class"] == "candidate_replay_capability"
    assert result.failure["failure_stage"] == "task_rollout"
    assert result.failure["repairable"] is True
    assert result.failure["diagnostics"]["task_artifacts"][0]["path"] == (
        "artifact/evidence/diag_replay_capability_mismatch.json"
    )


def test_replay_cli_supervisor_stops_on_terminal_dependency_diagnostic(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    diagnostic_path = artifact_dir / "diag_replay_capability_mismatch.json"
    diagnostic = json.dumps(
        {
            "diagnostic_type": "replay_capability_mismatch",
            "endpoint": "http://127.0.0.1:49533",
            "error": "protocol mismatch",
        }
    )
    script = (
        "from pathlib import Path; import time; "
        f"Path({str(diagnostic_path)!r}).write_text("
        f"{diagnostic!r}); "
        "time.sleep(30)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_replay_cli(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            text=True,
            capture_output=True,
            timeout=20,
            start_new_session=True,
            env={},
            artifact_dir=artifact_dir,
            execution_started_at=time.time(),
            replay_environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )

    assert time.monotonic() - started < 5
    assert getattr(exc_info.value, "terminal_diagnostic", False) is True


def test_replay_cli_supervisor_does_not_stop_on_live_progress_artifact(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    progress_path = artifact_dir / "scrape_output.log"
    script = (
        "from pathlib import Path; import time; "
        "print('Replay runtime contract for http://127.0.0.1:49533: on a "
        "protocol error report replay capability mismatch', flush=True); "
        f"Path({str(progress_path)!r}).write_text("
        "'[03:12:23] 正在导航到 X 首页...'); "
        "time.sleep(1.2); print('completed', flush=True)"
    )
    started = time.monotonic()

    completed = _run_replay_cli(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        text=True,
        capture_output=True,
        timeout=5,
        start_new_session=True,
        env={},
        artifact_dir=artifact_dir,
        execution_started_at=time.time(),
        replay_environment={
            "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
        },
    )

    assert completed.returncode == 0
    assert completed.stdout.rstrip().endswith("completed")
    assert time.monotonic() - started >= 1


def test_replay_cli_supervisor_stops_after_evidence_and_task_response(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    evidence_dir = artifact_dir / "evidence"
    evidence_dir.mkdir(parents=True)
    evidence_path = evidence_dir / "result.json"
    manifest_path = evidence_dir / "evidence_manifest.jsonl"
    task_response_path = artifact_dir / "framework_task_response.json"
    trajectory = [
        {
            "action": {
                "content": "finished",
                "is_agent_finished": "True",
            }
        }
    ]
    manifest_text = json.dumps(
        {
            "source_id": "result",
            "artifact_path": "result.json",
            "extraction_method": "bounded_extract",
            "fields": ["value"],
        }
    ) + "\n"
    task_response_text = json.dumps(
        {
            "schema_version": "aworld.self_evolve.task_response.v1",
            "trajectory": trajectory,
            "trajectory_capture_mode": "task_response",
        }
    ) + "\n"
    script = (
        "from pathlib import Path; import time; "
        f"Path({str(evidence_path)!r}).write_text('{{\"value\": 1}}'); "
        f"Path({str(manifest_path)!r}).write_text({manifest_text!r}); "
        f"Path({str(task_response_path)!r}).write_text({task_response_text!r}); "
        "time.sleep(30)"
    )
    started = time.monotonic()

    completed = _run_replay_cli(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        text=True,
        capture_output=True,
        timeout=20,
        start_new_session=True,
        env={},
        artifact_dir=artifact_dir,
        evidence_manifest=manifest_path,
        task_response_path=task_response_path,
        execution_started_at=time.time(),
        replay_environment={},
    )

    assert time.monotonic() - started < 5
    assert completed.returncode == 0
    assert getattr(completed, "evidence_ready_early_stop", False) is True
    payload = _extract_trajectory_payload_from_stdout(completed.stdout)
    assert payload["trajectory_capture_mode"] == "task_response"
    assert payload["trajectory"] == trajectory


def test_replay_cli_parent_attests_response_received_over_capability_fd(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    response_path = artifact_dir / "framework_task_response.json"
    capability_reader, capability_writer = os.pipe()
    key = b"p" * 32
    payload = {
        "schema_version": "aworld.self_evolve.task_response.v1",
        "trajectory": [{"action": {"content": "done"}}],
        "trajectory_capture_mode": "task_response",
        "llm_usage": {
            "schema_version": "aworld.llm_usage_summary.v1",
            "call_count": 2,
            "usage_call_count": 2,
            "total_tokens": 321,
            "coverage_complete": True,
            "ledger_consistent": True,
        },
    }
    script = (
        "import json, os; "
        "fd=int(os.environ['AWORLD_SELF_EVOLVE_TASK_RESPONSE_CAPABILITY_FD']); "
        f"os.write(fd, json.dumps({payload!r}).encode()); os.close(fd)"
    )

    completed = _run_replay_cli(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        text=True,
        capture_output=True,
        timeout=5,
        start_new_session=True,
        env={
            "AWORLD_SELF_EVOLVE_TASK_RESPONSE_CAPABILITY_FD": str(
                capability_writer
            )
        },
        artifact_dir=artifact_dir,
        execution_started_at=time.time(),
        replay_environment={},
        task_response_path=response_path,
        task_response_capability_fd=capability_writer,
        task_response_capability_reader_fd=capability_reader,
        task_response_attestation_key=key,
    )

    assert completed.returncode == 0
    attested = _load_self_evolve_task_response(
        response_path, attestation_key=key
    )
    assert attested is not None
    assert attested["trajectory"] == payload["trajectory"]
    assert _trusted_task_response_usage_metrics(attested) == {
        "total_tokens": 321,
        "llm_usage_call_count": 2,
        "llm_usage_coverage_complete": True,
    }


def test_trusted_task_response_usage_rejects_partial_coverage() -> None:
    assert _trusted_task_response_usage_metrics(
        {
            "llm_usage": {
                "schema_version": "aworld.llm_usage_summary.v1",
                "call_count": 2,
                "usage_call_count": 1,
                "total_tokens": 321,
                "coverage_complete": True,
                "ledger_consistent": True,
            }
        }
    ) == {}


def test_replay_cli_supervisor_bounds_evidence_finalization_without_output(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    evidence_dir = artifact_dir / "evidence"
    evidence_dir.mkdir(parents=True)
    evidence_path = evidence_dir / "result.json"
    manifest_path = evidence_dir / "evidence_manifest.jsonl"
    manifest_text = json.dumps(
        {
            "source_id": "result",
            "artifact_path": "result.json",
            "extraction_method": "bounded_extract",
            "fields": ["value"],
        }
    ) + "\n"
    script = (
        "from pathlib import Path; import time; "
        f"Path({str(evidence_path)!r}).write_text('{{\"value\": 1}}'); "
        f"Path({str(manifest_path)!r}).write_text({manifest_text!r}); "
        "time.sleep(30)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_replay_cli(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            text=True,
            capture_output=True,
            timeout=20,
            start_new_session=True,
            env={},
            artifact_dir=artifact_dir,
            evidence_manifest=manifest_path,
            task_response_path=(
                artifact_dir / "framework_task_response.json"
            ),
            execution_started_at=time.time(),
            replay_environment={},
            evidence_finalization_timeout_seconds=0.2,
        )

    assert time.monotonic() - started < 5
    assert getattr(
        exc_info.value,
        "evidence_finalization_deadline",
        False,
    ) is True


def test_replay_cli_supervisor_stops_on_skill_owned_capability_mismatch(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    diagnostic_path = artifact_dir / "replay_capability_mismatch.json"
    diagnostic = json.dumps(
        {
            "diagnostic_type": "replay_capability_mismatch",
            "endpoint": "http://127.0.0.1:49533",
            "observed_errors": ["All protocol discovery methods failed"],
        }
    )
    script = (
        "from pathlib import Path; import time; "
        f"Path({str(diagnostic_path)!r}).write_text("
        f"{diagnostic!r}); "
        "time.sleep(30)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_replay_cli(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            text=True,
            capture_output=True,
            timeout=20,
            start_new_session=True,
            env={},
            artifact_dir=artifact_dir,
            execution_started_at=time.time(),
            replay_environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )

    assert time.monotonic() - started < 5
    assert getattr(exc_info.value, "terminal_diagnostic", False) is True


def test_replay_cli_supervisor_stops_on_partial_process_diagnostic(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    script = (
        "import time; "
        "print('The task is still navigating on http://127.0.0.1:49533 and "
        "exited without producing output', flush=True); "
        "time.sleep(30)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_replay_cli(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            text=True,
            capture_output=True,
            timeout=20,
            start_new_session=True,
            env={},
            artifact_dir=artifact_dir,
            execution_started_at=time.time(),
            replay_environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )

    assert time.monotonic() - started < 5
    assert getattr(exc_info.value, "terminal_diagnostic", False) is True


def test_replay_cli_supervisor_stops_on_observed_protocol_signal(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    script = (
        "import time; "
        "print('Replay endpoint http://127.0.0.1:49533 returned not_found for '"
        "      'the required protocol path. This is a protocol signal.', "
        "      flush=True); "
        "time.sleep(30)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_replay_cli(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            text=True,
            capture_output=True,
            timeout=20,
            start_new_session=True,
            env={},
            artifact_dir=artifact_dir,
            execution_started_at=time.time(),
            replay_environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )

    assert time.monotonic() - started < 5
    assert getattr(exc_info.value, "terminal_diagnostic", False) is True


def test_replay_cli_supervisor_stops_on_workspace_root_diagnostic(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    workspace_dir = artifact_dir / "workspace"
    workspace_dir.mkdir(parents=True)
    diagnostic_path = workspace_dir / "scrape_stdout.log"
    diagnostic = (
        "All protocol discovery methods failed for "
        "http://127.0.0.1:49533"
    )
    script = (
        "from pathlib import Path; import time; "
        f"Path({str(diagnostic_path)!r}).write_text({diagnostic!r}); "
        "time.sleep(30)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_replay_cli(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            text=True,
            capture_output=True,
            timeout=20,
            start_new_session=True,
            env={},
            artifact_dir=artifact_dir,
            execution_started_at=time.time(),
            replay_environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )

    assert time.monotonic() - started < 5
    assert getattr(exc_info.value, "terminal_diagnostic", False) is True


def test_replay_cli_supervisor_combines_live_artifact_with_endpoint_context(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    diagnostic_path = artifact_dir / "scrape_stdout.log"
    script = (
        "from pathlib import Path; import time; "
        "print('Using replay endpoint http://127.0.0.1:49533', flush=True); "
        f"Path({str(diagnostic_path)!r}).write_text("
        "'Failed to deserialize response: missing field targetInfos'); "
        "time.sleep(30)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_replay_cli(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            text=True,
            capture_output=True,
            timeout=20,
            start_new_session=True,
            env={},
            artifact_dir=artifact_dir,
            execution_started_at=time.time(),
            replay_environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )

    assert time.monotonic() - started < 5
    assert getattr(exc_info.value, "terminal_diagnostic", False) is True


def test_replay_cli_supervisor_ignores_static_contract_language(
    tmp_path: Path,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    script = (
        "import time; "
        "print('If a supplied endpoint does not implement the protocol, report a '"
        "      'replay capability mismatch.', flush=True); "
        "time.sleep(5)"
    )
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        _run_replay_cli(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            text=True,
            capture_output=True,
            timeout=1.5,
            start_new_session=True,
            env={},
            artifact_dir=artifact_dir,
            execution_started_at=time.time(),
            replay_environment={
                "AWORLD_REPLAY_ENDPOINT_RECORDED": "http://127.0.0.1:49533"
            },
        )

    assert time.monotonic() - started >= 1.0
    assert getattr(exc_info.value, "terminal_diagnostic", False) is False


@pytest.mark.asyncio
async def test_aworld_cli_candidate_replay_backend_returns_structured_failure(
    tmp_path: Path,
) -> None:
    async def failing_executor(request):
        if request.variant_id == "baseline":
            return ReplayExecutionResult(
                status="succeeded",
                trajectory=[{"action": {"content": "baseline"}}],
            )
        return ReplayExecutionResult(
            status="failed",
            trajectory=[],
            failure={"reason": "missing model configuration"},
            stdout="",
            stderr="No model configuration",
        )

    request = CandidateReplayRequest(
        run_id="run-failure",
        task_id="task-1",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="cand-1",
        overlay_skill_root=str(tmp_path / "overlay-skills"),
        task_input="Replay this task",
    )

    result = await AWorldCliCandidateReplayBackend(executor=failing_executor).replay_candidate(
        request,
        candidate=_candidate("---\nname: demo\n---\n# Demo\n", candidate_id="cand-1"),
        dataset=SelfEvolveDataset(
            cases=(EvalCase(case_id="task-1", input="Replay this task"),),
            recipe=DatasetRecipe(
                source={"kind": "test", "case_count": 1},
                split_seed="seed",
                splits={"train": ["task-1"], "validation": [], "held_out": []},
            ),
        ),
    )

    assert result.succeeded is False
    assert result.candidate.status == "failed"
    assert result.candidate.failure == {"reason": "missing model configuration"}
    failure_path = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-failure"
        / "replay"
        / "cand-1"
        / "cand-1"
        / "failure.json"
    )
    assert failure_path.exists()
