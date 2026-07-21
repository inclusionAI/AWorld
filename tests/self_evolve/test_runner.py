from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import aworld.self_evolve.runner as runner_module

from aworld.config.conf import ModelConfig
from aworld.core.common import TaskStatusValue
from aworld.core.task import TaskResponse
from aworld.runner import Runners

from aworld.self_evolve.candidate_generation import CandidateGenerationAgent
from aworld.self_evolve.datasets import (
    EvalCase,
    SelfEvolveDataset,
    SelfEvolveEvalSourceConfig,
    build_dataset_from_source,
)
from aworld.self_evolve.optimizers.llm_mutator import TraceReflectiveLLMMutator
from aworld.self_evolve.optimizers.base import OptimizerRequest, OptimizerResult
from aworld.self_evolve.failure_events import (
    FailureEventSource,
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayExecutionStatus,
    ReplayFailureEvent,
    ReplayFailureObservation,
    aggregate_replay_failure_observations,
)
from aworld.self_evolve.replay import (
    CandidateReplayMemberResult,
    CandidateReplayRequest,
    CandidateReplayResult as _CandidateReplayResult,
    ReplayVariantResult,
    _distributed_member_repetitions,
    _member_artifact_name,
    _member_baseline_replay_dir,
)
from aworld.self_evolve.replay_adaptation import (
    ReplayAdapterBinding,
    ReplayAdaptationCompiler,
    ReplayCapabilityRequirement,
)
from aworld.self_evolve.runner import (
    SelfEvolveRunner,
    _aggregate_target_selection_decisions,
    _auto_group_trajectory_log_dataset,
    _baseline_replay_artifact_dir,
    _candidate_screening_timeout,
    _candidate_validation_report_for_persistence,
    _candidate_gate_results,
    _candidate_screening_repair_feedback,
    _default_cli_skill_candidate,
    _default_iteration_budget,
    _default_post_apply_evaluator,
    _candidate_generation_limit,
    _feedback_from_report,
    _include_prior_run_cases,
    _iteration_validation_feedback,
    _load_target_provenance,
    _load_target_selection_report,
    _merge_validation_feedback,
    _parse_candidate_mutation_model_output,
    _population_report,
    _next_progress_repair_extension_family,
    _rank_candidate_population,
    _rejected_candidate_ids_from_report,
    _replay_confidence_gate,
    _replay_gate_details,
    _replay_adaptation_exception_details,
    _replay_report,
    _repair_conformance_failure_diagnostics,
    _repair_conformance_gate,
    _repair_conformance_required_nonempty_operations,
    _retryable_candidate_generation_failure,
    _select_iteration_state,
    _candidate_screening_dataset,
    _explicit_target_selection_report,
    _source_config_from_stored_dataset_recipe,
    _summary_with_replay_evidence_metrics,
    _shared_replay_failure_blocks_population,
    _infer_target_from_trace_packs,
    optimize_explicit_target,
    optimize_from_cli_request,
)
from aworld.self_evolve.replay_capability import (
    FrozenReplayCapability,
    ReplayCapabilityError,
    ReplayProtocolProbe,
    ReplayReadinessProbe,
    ReplayServiceSpec,
)
from aworld.self_evolve.provenance import (
    TargetProvenance,
    TargetProvenanceResolution,
    TargetSelectionOrigin,
)
from aworld.self_evolve.repair_conformance import (
    ExactRepairProbe,
    RepairConformanceContract,
    RepairConformanceResult,
    compile_repair_conformance_contract,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SkillTextTarget
from aworld.self_evolve.trace_pack import build_trace_pack
from aworld.self_evolve.credit_assignment import (
    TargetInventory,
    TargetSelectionDecision,
    TargetSelectionReport,
    build_target_selection_decision,
)
from aworld.self_evolve.types import (
    CandidateFileDelta,
    CandidateVariant,
    EvaluationSummary,
    GateResult,
    DatasetRecipe,
    OptimizerLineage,
    SelfEvolveRun,
    SelfEvolveRunStatus,
    SelfEvolveTargetRef,
    to_json_dict,
)


_REPLAY_PROVENANCE_KEYS = (
    "adaptation_fingerprint",
    "workspace_seed_fingerprint",
    "task_input_fingerprint",
    "dataset_fingerprint",
    "baseline_skill_fingerprint",
)


def test_progress_repair_extension_requires_a_novel_repairable_failure_family() -> None:
    def feedback(candidate_id: str, diagnostic_code: str) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            dataset_split="validation",
            metrics={
                "failed_gates": ["candidate_replay"],
                "failure_class": "candidate",
                "repairable": True,
                "candidate_validation_diagnostics": [
                    {
                        "code": diagnostic_code,
                        "stage": "replay_capability",
                        "reason": diagnostic_code,
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {
                            "path": "replay/runtime.py",
                            "content": "# candidate runtime\n",
                        }
                    ],
                },
            },
        )

    target_id_feedback = feedback("candidate-target", "missing_target_id")
    session_id_feedback = feedback("candidate-session", "missing_session_id")
    consumed: set[str] = set()

    first_family = _next_progress_repair_extension_family(
        (target_id_feedback,),
        consumed_families=consumed,
    )
    assert first_family is not None
    consumed.add(first_family)
    assert (
        _next_progress_repair_extension_family(
            (target_id_feedback,),
            consumed_families=consumed,
        )
        is None
    )
    assert _next_progress_repair_extension_family(
        (target_id_feedback, session_id_feedback),
        consumed_families=consumed,
    ) not in {None, first_family}


def test_feedback_from_report_restores_latest_repairable_screening_package(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run-old"
    for candidate_id, rationale, content in (
        (
            "candidate-earlier-progress",
            "preserve the earlier protocol progress",
            "def respond():\n    return {'targetId': 'target-1'}\n",
        ),
        (
            "candidate-near-success",
            "preserve the working runtime and repair its final probe",
            "def respond():\n    return {'id': 1, 'result': {}}\n",
        ),
    ):
        candidate_path = run_root / "candidates" / f"{candidate_id}.json"
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        candidate_path.write_text(
            json.dumps(
                {
                    "candidate_id": candidate_id,
                    "rationale": rationale,
                    "files": [
                        {
                            "path": "replay/runtime.py",
                            "operation": "upsert",
                            "executable": False,
                            "content": content,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
    report_path = run_root / "report.json"
    report = {
        "run_id": "run-old",
        "population": {
            "screening_iterations": [
                {
                    "attempts": [
                        {
                            "candidate_id": "candidate-earlier-progress",
                            "passed": False,
                            "reason": "candidate replay reached the next protocol stage",
                            "details": {
                                "failure_class": "candidate",
                                "failure_stage": "replay_capability",
                                "repairable": True,
                                "baseline_failure": {
                                    "reason": "protocol response missing sessionId"
                                },
                            },
                        }
                    ]
                },
                {
                    "attempts": [
                        {
                            "candidate_id": "candidate-near-success",
                            "passed": False,
                            "reason": "candidate replay did not produce comparable paired outcomes",
                            "details": {
                                "failure_class": "candidate",
                                "failure_stage": "replay_capability",
                                "repairable": True,
                                "baseline_failure": {
                                    "reason": "protocol probe response mismatch"
                                },
                            },
                        }
                    ]
                },
            ],
            "screening": {
                "attempts": [
                    {
                        "candidate_id": "candidate-near-success",
                        "passed": False,
                        "reason": "candidate replay did not produce comparable paired outcomes",
                        "details": {
                            "failure_class": "candidate",
                            "failure_stage": "replay_capability",
                            "repairable": True,
                            "baseline_failure": {
                                "reason": "protocol probe response mismatch"
                            },
                        },
                    }
                ]
            }
        },
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")

    feedback = _feedback_from_report(report, report_path=report_path)

    assert [item.variant_id for item in feedback[:2]] == [
        "candidate-near-success",
        "candidate-earlier-progress",
    ]
    assert feedback[0].variant_id == "candidate-near-success"
    assert feedback[0].dataset_split == "historical_repair"
    assert feedback[0].metrics["failure_class"] == "candidate"
    assert feedback[0].metrics["repairable"] is True
    assert feedback[0].metrics["repair_candidate_package"] == {
        "candidate_id": "candidate-near-success",
        "rationale": "preserve the working runtime and repair its final probe",
        "files": [
            {
                "path": "replay/runtime.py",
                "operation": "upsert",
                "executable": False,
                "content": "def respond():\n    return {'id': 1, 'result': {}}",
            }
        ],
    }


def test_feedback_from_report_restores_selected_candidate_authoritative_failure(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run-authoritative"
    candidate_id = "candidate-authoritative-repair"
    candidate_root = run_root / "candidates" / candidate_id
    candidate_root.mkdir(parents=True)
    (candidate_root / "candidate.json").write_text(
        json.dumps(
            {
                "candidate_id": candidate_id,
                "rationale": "repair the full-dataset compiler failure",
                "files": [
                    {
                        "path": "replay/compiler.py",
                        "operation": "upsert",
                        "executable": False,
                        "content": "def compile_all_requirements():\n    pass\n",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report_path = run_root / "report.json"
    compiler_artifact = (
        run_root
        / "replay_adaptation"
        / "dataset"
        / "candidate"
        / "skill_replay_capability"
        / "compile-a"
        / "compiler.stderr.txt"
    )
    compiler_artifact.parent.mkdir(parents=True)
    compiler_artifact.write_text(
        "PermissionError: fixture destination is read-only\n",
        encoding="utf-8",
    )
    report = {
        "run_id": "run-authoritative",
        "selected_candidate_id": candidate_id,
        "gate_results": [
            {
                "gate_name": "replay_adaptation",
                "passed": False,
                "reason": "replay adaptation compilation failed",
                "details": {
                    "failure_class": "candidate",
                    "repairable": True,
                    "artifact_root": str(
                        run_root / "replay_adaptation" / "dataset" / "candidate"
                    ),
                    "diagnostics": [
                        {
                            "code": "invalid_replay_capability_compile",
                            "stage": "capability_compile",
                            "reason": "fixture destination is read-only",
                        }
                    ],
                },
            }
        ],
        "population": {},
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")

    feedback = _feedback_from_report(report, report_path=report_path)

    assert feedback[0].variant_id == candidate_id
    assert feedback[0].dataset_split == "historical_repair"
    assert feedback[0].metrics["failure_class"] == "candidate"
    assert feedback[0].metrics["repairable"] is True
    assert feedback[0].metrics["failed_gates"] == ["replay_adaptation"]
    assert feedback[0].metrics["repair_candidate_package"]["candidate_id"] == (
        candidate_id
    )
    assert "PermissionError: fixture destination is read-only" in json.dumps(
        feedback[0].metrics["candidate_validation_diagnostics"]
    )
    assert feedback[0].metrics["candidate_validation_diagnostics"][0]["code"] == (
        "repair_candidate_output_permission_collision"
    )


def test_feedback_from_report_joins_selected_candidate_held_out_judge_metrics(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "run-held-out-repair"
    candidate_id = "candidate-held-out-repair"
    candidate_root = run_root / "candidates" / candidate_id
    candidate_root.mkdir(parents=True)
    (candidate_root / "candidate.json").write_text(
        json.dumps(
            {
                "candidate_id": candidate_id,
                "rationale": "preserve replay and repair claim support",
                "files": [
                    {
                        "path": "SKILL.md",
                        "operation": "upsert",
                        "executable": False,
                        "content": "# Grounded finalization\n",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report_path = run_root / "report.json"
    report = {
        "run_id": "run-held-out-repair",
        "selected_candidate_id": candidate_id,
        "gate_results": [
            {
                "gate_name": "evidence_quality",
                "passed": False,
                "reason": "evaluation evidence is incomplete",
                "details": {"evidence_incomplete": True},
            },
            {
                "gate_name": "required_verification",
                "passed": False,
                "reason": "held-out verification failed",
                "details": {},
            },
        ],
        "iterations": [
            {
                "candidate_id": candidate_id,
                "status": "rejected",
                "failed_gates": ["evidence_quality", "required_verification"],
                "candidate_metrics": {
                    "score": 86.0,
                    "A1_groundedness": 4,
                    "evidence_incomplete": False,
                },
                "held_out_metrics": {
                    "score": 69.6,
                    "A1_groundedness": 3,
                    "A2_completeness": 4,
                    "evidence_incomplete": True,
                    "evidence_issues": ["claims exceed bounded excerpts"],
                },
            }
        ],
        "population": {},
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")

    feedback = _feedback_from_report(report, report_path=report_path)

    assert feedback[0].variant_id == candidate_id
    assert feedback[0].dataset_split == "held_out"
    assert feedback[0].metrics["score"] == 69.6
    assert feedback[0].metrics["A1_groundedness"] == 3
    assert feedback[0].metrics["evidence_incomplete"] is True
    assert feedback[0].metrics["evidence_issues"] == [
        "claims exceed bounded excerpts"
    ]
    assert feedback[0].metrics["repair_candidate_package"]["candidate_id"] == (
        candidate_id
    )


def test_iteration_selection_prefers_fewer_failed_gates_without_scores() -> None:
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo")
    first = CandidateVariant(
        candidate_id="first",
        target=target,
        content="# First\n",
        rationale="first",
    )
    second = CandidateVariant(
        candidate_id="second",
        target=target,
        content="# Second\n",
        rationale="second",
    )
    selected = _select_iteration_state(
        [
            {
                "candidate": first,
                "candidate_summary": None,
                "status": "rejected",
                "gate_results": (
                    GateResult("skill_markdown", False, "missing frontmatter"),
                    GateResult("replay_adaptation", False, "compile failed"),
                ),
            },
            {
                "candidate": second,
                "candidate_summary": None,
                "status": "rejected",
                "gate_results": (
                    GateResult("replay_adaptation", False, "compile failed"),
                ),
            },
        ]
    )

    assert selected is not None
    assert selected["candidate"] is second


def test_iteration_selection_prefers_candidate_that_reached_runtime_replay() -> None:
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo")
    compile_failed = CandidateVariant(
        candidate_id="compile-failed",
        target=target,
        content="# Compile failed\n",
        rationale="first",
    )
    runtime_failed = CandidateVariant(
        candidate_id="runtime-failed",
        target=target,
        content="# Runtime failed\n",
        rationale="later repair",
    )

    selected = _select_iteration_state(
        [
            {
                "candidate": compile_failed,
                "candidate_summary": None,
                "status": "rejected",
                "gate_results": (
                    GateResult("replay_adaptation", False, "compile failed"),
                ),
            },
            {
                "candidate": runtime_failed,
                "candidate_summary": None,
                "status": "rejected",
                "gate_results": (
                    GateResult("candidate_replay", False, "runtime probe failed"),
                    GateResult("replay_confidence", False, "pair incomparable"),
                ),
            },
        ]
    )

    assert selected is not None
    assert selected["candidate"] is runtime_failed


def test_iteration_selection_does_not_prefer_duplicate_only_retry() -> None:
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo")
    evaluated = CandidateVariant(
        candidate_id="evaluated",
        target=target,
        content="# Evaluated\n",
        rationale="evaluated",
    )
    duplicate = CandidateVariant(
        candidate_id="duplicate",
        target=target,
        content="# Duplicate\n",
        rationale="duplicate",
    )

    selected = _select_iteration_state(
        [
            {
                "candidate": evaluated,
                "candidate_summary": None,
                "status": "rejected",
                "gate_results": (
                    GateResult("candidate_replay", False, "replay failed"),
                    GateResult("replay_confidence", False, "not comparable"),
                ),
            },
            {
                "candidate": duplicate,
                "candidate_summary": None,
                "status": "rejected",
                "gate_results": (
                    GateResult(
                        "duplicate_rejected_candidate",
                        False,
                        "already rejected",
                    ),
                ),
            },
        ]
    )

    assert selected is not None
    assert selected["candidate"] is evaluated


def test_candidate_screening_prefers_case_exercising_replay_requirements() -> None:
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="first-case", input="first user task"),
            EvalCase(case_id="capability-case", input="recorded endpoint task"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "trajectory"},
            split_seed="seed",
            splits={"train": ["first-case", "capability-case"]},
            trainable_case_ids=("first-case", "capability-case"),
        ),
    )
    requirements = (
        ReplayCapabilityRequirement(
            requirement_id="requirement-1",
            kind="local_endpoint",
            identifier="http://127.0.0.1:9222",
            case_ids=("capability-case",),
            evidence_refs=("context:1",),
            status="unbound",
        ),
    )

    screening = _candidate_screening_dataset(
        dataset,
        capability_requirements=requirements,
    )

    assert screening is not None
    assert screening.cases[0].case_id == "capability-case"


def CandidateReplayResult(*args, **kwargs):
    """Build a fake backend result that honours the replay provenance contract."""

    result = _CandidateReplayResult(*args, **kwargs)
    if result.request.adaptation_fingerprint is None:
        return result

    def attested(variant: ReplayVariantResult, request: CandidateReplayRequest):
        repetition_count = int(variant.metrics.get("repetition_count", 1))
        workspace_base = (
            Path(request.workspace_root).resolve()
            / ".fake_replay_workspaces"
            / request.task_id
            / variant.variant_id
        )
        workspace_metrics = (
            {"isolated_workspace_path": str(workspace_base / "1")}
            if repetition_count == 1
            else {
                "isolated_workspace_path_values": [
                    str(workspace_base / str(index))
                    for index in range(1, repetition_count + 1)
                ]
            }
        )
        return replace(
            variant,
            metrics={
                **dict(variant.metrics),
                **{
                    key: getattr(request, key)
                    for key in _REPLAY_PROVENANCE_KEYS
                },
                "adapter_determinism": "deterministic",
                **workspace_metrics,
            },
        )

    members = None
    if result.member_results is not None:
        member_count = len(result.member_results)
        normalized_members = []
        for member in result.member_results:
            adaptation_case = (
                result.request.replay_adaptation.case(member.case_id)
                if result.request.replay_adaptation is not None
                else None
            )
            member_request = replace(
                member.request,
                task_input=(
                    adaptation_case.adapted_task_input
                    if adaptation_case is not None
                    else member.request.task_input
                ),
                task_input_fingerprint=(
                    adaptation_case.task_input_fingerprint
                    if adaptation_case is not None
                    else result.request.task_input_fingerprint
                ),
                baseline_replay_dir=_member_baseline_replay_dir(
                    result.request.baseline_replay_dir,
                    member.case_id,
                ),
                baseline_repetitions=_distributed_member_repetitions(
                    result.request.baseline_repetitions,
                    member_count=member_count,
                ),
                candidate_repetitions=_distributed_member_repetitions(
                    result.request.candidate_repetitions,
                    member_count=member_count,
                ),
            )
            normalized_members.append(
                replace(
                    member,
                    request=member_request,
                    baseline=attested(member.baseline, member_request),
                    candidate=attested(member.candidate, member_request),
                )
            )
        members = tuple(normalized_members)
    return replace(
        result,
        baseline=attested(result.baseline, result.request),
        candidate=attested(result.candidate, result.request),
        member_results=members,
    )


class EmptyOptimizer:
    async def propose(self, request: OptimizerRequest) -> OptimizerResult:
        return OptimizerResult(
            candidates=(),
            lineage=(),
            diagnostics={"filtered_noop_candidates": 1},
        )


class CaptureOptimizer:
    def __init__(self) -> None:
        self.requests: list[OptimizerRequest] = []

    async def propose(self, request: OptimizerRequest) -> OptimizerResult:
        self.requests.append(request)
        return OptimizerResult(
            candidates=(
                CandidateVariant(
                    candidate_id="candidate-1",
                    target=request.target,
                    content=request.current_content + "\nNew guidance.\n",
                    rationale="captured request",
                    target_fingerprint=request.target_fingerprint,
                ),
            ),
        )


def test_replay_only_rejection_does_not_permanently_blacklist_candidate() -> None:
    report = {
        "status": "rejected",
        "selected_candidate_id": "candidate-retry",
        "iterations": [
            {
                "candidate_id": "candidate-retry",
                "status": "rejected",
                "baseline_metrics": None,
                "candidate_metrics": None,
                "held_out_metrics": None,
                "failed_gates": ["candidate_replay", "replay_confidence"],
            }
        ],
    }

    assert _rejected_candidate_ids_from_report(report) == set()


def test_auto_verified_default_iteration_budget_allows_multi_stage_capability_repair() -> None:
    assert _default_iteration_budget(
        apply_policy="auto_verified",
        explicit_iterations=None,
    ) == 10


def test_candidate_screening_timeout_is_bounded_without_extending_short_timeouts() -> None:
    assert _candidate_screening_timeout(600) == 240
    assert _candidate_screening_timeout(240) == 240
    assert _candidate_screening_timeout(180) == 180
    assert _candidate_screening_timeout(120) == 120


def test_candidate_generation_retries_only_transient_provider_failures() -> None:
    assert _retryable_candidate_generation_failure(
        {
            "stage": "model_provider",
            "error_type": "LLMResponseError",
        }
    )
    assert _retryable_candidate_generation_failure(
        {
            "stage": "model_provider",
            "error_type": "RateLimitError",
        }
    )
    assert not _retryable_candidate_generation_failure(
        {
            "stage": "model_call",
            "error_type": "RuntimeError",
        }
    )


def test_explicit_iteration_budget_is_the_exact_upper_bound() -> None:
    assert _default_iteration_budget(
        apply_policy="auto_verified",
        explicit_iterations=1,
    ) == 1
    assert _default_iteration_budget(
        apply_policy="proposal",
        explicit_iterations=None,
    ) == 1


def test_iteration_budget_rejects_non_positive_explicit_values() -> None:
    with pytest.raises(ValueError, match="iterations must be positive"):
        _default_iteration_budget(
            apply_policy="auto_verified",
            explicit_iterations=0,
        )


def test_candidate_replay_capability_compile_error_is_typed_repair_feedback() -> None:
    details = _replay_adaptation_exception_details(
        ReplayCapabilityError("unsupported replay binding concurrency mode: sequential"),
        candidate_capability=True,
    )

    assert details["failure_class"] == "candidate"
    assert details["repairable"] is True
    diagnostic = details["diagnostics"][0]
    assert diagnostic["code"] == "invalid_replay_capability_compile"
    assert diagnostic["stage"] == "capability_compile"
    assert diagnostic["failure_class"] == "candidate"
    assert diagnostic["repairable"] is True
    assert diagnostic["reason"] == (
        "unsupported replay binding concurrency mode: sequential"
    )
    assert diagnostic["required_manifest_contract"]["protocol"] == (
        "aworld.replay.subprocess.v1"
    )
    assert diagnostic["required_manifest_contract"]["handles_values"] == [
        "conversation_context",
        "http_resource",
        "local_endpoint",
        "local_file",
        "stateful_tool",
    ]
    assert diagnostic["required_compile_result_contract"][
        "runtime_service_transport"
    ] == "skill_runtime"
    assert "runtime_required is a requirement status" in diagnostic[
        "layering_rules"
    ][2]


def test_candidate_generation_does_not_prepay_for_historical_duplicates() -> None:
    assert _candidate_generation_limit(replay_candidate_limit=2) == 2


def test_duplicate_only_rejection_does_not_create_new_blacklist_record() -> None:
    report = {
        "status": "rejected",
        "selected_candidate_id": "candidate-retry",
        "iterations": [
            {
                "candidate_id": "candidate-retry",
                "status": "rejected",
                "baseline_metrics": None,
                "candidate_metrics": None,
                "held_out_metrics": None,
                "failed_gates": ["duplicate_rejected_candidate"],
            }
        ],
    }

    assert _rejected_candidate_ids_from_report(report) == set()


def _write_terminal_run_with_raw_artifacts(root: Path, run_id: str, timestamp: float) -> None:
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": run_id, "status": "succeeded"}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "report.json").write_text(
        json.dumps({"run_id": run_id, "status": "succeeded"}) + "\n",
        encoding="utf-8",
    )
    replay_file = run_dir / "replay" / "cand-1" / "result.json"
    replay_file.parent.mkdir(parents=True)
    replay_file.write_text("{}\n", encoding="utf-8")
    overlay_file = run_dir / "overlays" / "cand-1" / "skills" / "demo" / "SKILL.md"
    overlay_file.parent.mkdir(parents=True)
    overlay_file.write_text("# Demo\n", encoding="utf-8")
    for child in sorted(run_dir.rglob("*"), reverse=True):
        os.utime(child, (timestamp, timestamp))
    os.utime(run_dir, (timestamp, timestamp))


def test_multi_member_replay_reuses_member_baseline_root(tmp_path: Path) -> None:
    request = CandidateReplayRequest(
        run_id="run-members",
        task_id="task-a",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="candidate-1",
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input="task A",
    )
    successful = ReplayVariantResult(
        variant_id="baseline",
        status="succeeded",
        trajectory=[{"action": {"content": "ok"}}],
    )
    result = CandidateReplayResult(
        request=request,
        baseline=successful,
        candidate=ReplayVariantResult(
            variant_id="candidate-1",
            status="succeeded",
            trajectory=[{"action": {"content": "candidate"}}],
        ),
        member_results=(
            CandidateReplayMemberResult(
                case_id="task-a",
                request=request,
                baseline=successful,
                candidate=successful,
            ),
        ),
    )

    assert _baseline_replay_artifact_dir(result).endswith(
        "/replay/candidate-1/members"
    )
    replay_report = _replay_report(result)
    member_report = replay_report["members"][0]
    assert {
        key: member_report[key]
        for key in (
            "case_id",
            "baseline_status",
            "candidate_status",
            "baseline_metrics",
            "candidate_metrics",
            "baseline_failure",
            "candidate_failure",
        )
    } == {
        "case_id": "task-a",
        "baseline_status": "succeeded",
        "candidate_status": "succeeded",
        "baseline_metrics": {},
        "candidate_metrics": {},
        "baseline_failure": None,
        "candidate_failure": None,
    }
    assert member_report["baseline_lifecycle"]["blocked_by"] == []
    assert member_report["candidate_lifecycle"]["failure_event"] is None


def test_multi_member_replay_advances_from_historical_to_current_member_root(
    tmp_path: Path,
) -> None:
    historical_members = tmp_path / "historical" / "members"
    request = CandidateReplayRequest(
        run_id="run-members",
        task_id="task-a",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="candidate-1",
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input="task A",
        baseline_replay_dir=str(historical_members),
    )
    successful = ReplayVariantResult(
        variant_id="baseline",
        status="succeeded",
        trajectory=[{"action": {"content": "ok"}}],
    )
    result = CandidateReplayResult(
        request=request,
        baseline=successful,
        candidate=ReplayVariantResult(
            variant_id="candidate-1",
            status="succeeded",
            trajectory=[{"action": {"content": "candidate"}}],
        ),
        member_results=(
            CandidateReplayMemberResult(
                case_id="task-a",
                request=request,
                baseline=successful,
                candidate=successful,
            ),
        ),
    )

    assert _baseline_replay_artifact_dir(result) == str(
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-members"
        / "replay"
        / "candidate-1"
        / "members"
    )


def test_replay_confidence_counts_comparable_baseline_task_failures() -> None:
    baseline_trajectory = [{"action": {"content": "baseline failed task"}}]
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="task-a",
                input="task A",
                metadata={"baseline_trajectory": baseline_trajectory},
            ),
            EvalCase(
                case_id="task-b",
                input="task B",
                metadata={"baseline_trajectory": baseline_trajectory},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a", "task-b"], "validation": [], "held_out": []},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-comparable",
        task_id="task-a",
        workspace_root="/tmp/workspace",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="candidate-1",
        overlay_skill_root="/tmp/overlay",
        task_input="task A",
    )
    succeeded = ReplayVariantResult(
        variant_id="succeeded",
        status="succeeded",
        trajectory=[{"action": {"content": "completed"}}],
    )
    timeout = ReplayVariantResult(
        variant_id="baseline",
        status="failed",
        trajectory=[],
        metrics={"latency_ms": 180000},
        failure={"type": "TimeoutExpired", "reason": "replay timed out"},
    )
    replay = CandidateReplayResult(
        request=request,
        baseline=timeout,
        candidate=ReplayVariantResult(
            variant_id="candidate-1",
            status="succeeded",
            trajectory=succeeded.trajectory,
            metrics={
                "repetition_count": 3,
                "successful_repetition_count": 3,
                "failed_repetition_count": 0,
            },
        ),
        member_results=(
            CandidateReplayMemberResult(
                case_id="task-a",
                request=request,
                baseline=succeeded,
                candidate=succeeded,
            ),
            CandidateReplayMemberResult(
                case_id="task-b",
                request=replace(request, task_id="task-b", task_input="task B"),
                baseline=timeout,
                candidate=succeeded,
            ),
        ),
    )

    gate = _replay_confidence_gate(
        replay,
        dataset=dataset,
        apply_policy="auto_verified",
    )

    assert gate is not None
    assert gate.passed is True
    assert gate.details["strict_pair_count"] == 1
    assert gate.details["task_failure_pair_count"] == 1
    assert gate.details["incomparable_pair_count"] == 0


def test_replay_confidence_rejects_infrastructure_failure_pair() -> None:
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="task-a",
                input="task A",
                metadata={
                    "baseline_trajectory": [{"action": {"content": "baseline"}}]
                },
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-a"], "validation": [], "held_out": []},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-infrastructure",
        task_id="task-a",
        workspace_root="/tmp/workspace",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="candidate-1",
        overlay_skill_root="/tmp/overlay",
        task_input="task A",
    )
    candidate = ReplayVariantResult(
        variant_id="candidate-1",
        status="succeeded",
        trajectory=[{"action": {"content": "completed"}}],
        metrics={
            "repetition_count": 3,
            "successful_repetition_count": 3,
            "failed_repetition_count": 0,
        },
    )
    infrastructure_failure = ReplayVariantResult(
        variant_id="baseline",
        status="failed",
        trajectory=[],
        failure={"type": "ProcessError", "reason": "model initialization failed"},
    )
    replay = CandidateReplayResult(
        request=request,
        baseline=infrastructure_failure,
        candidate=candidate,
        member_results=(
            CandidateReplayMemberResult(
                case_id="task-a",
                request=request,
                baseline=infrastructure_failure,
                candidate=candidate,
            ),
        ),
    )

    gate = _replay_confidence_gate(
        replay,
        dataset=dataset,
        apply_policy="auto_verified",
    )

    assert gate is not None
    assert gate.passed is False
    assert gate.reason == "replay comparison contains incomparable member outcomes"
    assert gate.details["infrastructure_failure_count"] == 1
    assert gate.details["incomparable_pair_count"] == 1


def test_stored_dataset_recipe_restores_auto_grouped_member_ids(
    tmp_path: Path,
) -> None:
    recipe_path = tmp_path / "dataset_recipe.json"
    recipe_path.write_text(
        json.dumps(
            {
                "source": {
                    "kind": "trajectory_log",
                    "path": "/tmp/trajectory.log",
                    "task_ids": [],
                    "auto_grouping": {
                        "auto_grouped": True,
                        "selected_case_ids": ["task-a", "task-b"],
                    },
                },
                "split_seed": "stored-seed",
            }
        ),
        encoding="utf-8",
    )

    source_config, split_seed = _source_config_from_stored_dataset_recipe(
        recipe_path
    )

    assert source_config.task_ids == ("task-a", "task-b")
    assert split_seed == "stored-seed"


def _write_trajectory_log(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(
            repr(
                {
                    "task_id": record["task_id"],
                    "is_sub_task": False,
                    "trajectory": json.dumps(record["trajectory"]),
                }
            )
            for record in records
        )
        + "\n",
        encoding="utf-8",
    )


def test_auto_groups_multi_task_trajectory_log_by_inferred_target(tmp_path) -> None:
    log_path = tmp_path / "trajectory.log"
    _write_trajectory_log(
        log_path,
        [
            {
                "task_id": "task-alpha",
                "trajectory": [
                    {
                        "meta": {"step": 1},
                        "state": {"input": {"content": "alpha task"}},
                        "action": {"content": "alpha failure"},
                        "reward": {"status": "failed"},
                    }
                ],
            },
            {
                "task_id": "task-beta",
                "trajectory": [
                    {
                        "meta": {"step": 1},
                        "state": {"input": {"content": "beta task"}},
                        "action": {"content": "beta failure"},
                        "reward": {"status": "failed"},
                    }
                ],
            },
            {
                "task_id": "task-alpha-2",
                "trajectory": [
                    {
                        "meta": {"step": 1},
                        "state": {"input": {"content": "another alpha task"}},
                        "action": {"content": "alpha followup failure"},
                        "reward": {"status": "failed"},
                    }
                ],
            },
        ],
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="trajectory_log", path=str(log_path))
    )
    trace_packs = tuple(case.trace_pack for case in dataset.cases if case.trace_pack)
    alpha_target = SelfEvolveTargetRef("skill", "alpha", str(tmp_path / "alpha.md"))
    beta_target = SelfEvolveTargetRef("skill", "beta", str(tmp_path / "beta.md"))

    def fake_infer(pack_group, *, workspace_root):
        pack = pack_group[0]
        target = beta_target if pack.task_id == "task-beta" else alpha_target
        return build_target_selection_decision(
            TargetSelectionReport(
                selected_target=target,
                confidence=0.9,
                evidence_step_ids=(f"{pack.task_id}:step-1",),
                failure_category="skill",
                signals=("test_signal",),
                diagnostics={"task_id": pack.task_id},
            ),
            inventory=TargetInventory(entries=()),
            selection_origin="inferred",
        )

    grouped_dataset, grouped_trace_packs, grouping = _auto_group_trajectory_log_dataset(
        dataset,
        trace_packs,
        source_config=SelfEvolveEvalSourceConfig(kind="trajectory_log", path=str(log_path)),
        workspace_root=tmp_path,
        infer_target=fake_infer,
    )

    assert [case.case_id for case in grouped_dataset.cases] == [
        "task-alpha",
        "task-alpha-2",
    ]
    assert [pack.task_id for pack in grouped_trace_packs] == [
        "task-alpha",
        "task-alpha-2",
    ]
    assert grouping["selected_group_id"] == "skill:alpha"
    assert grouping["group_count"] == 2
    assert grouping["auto_grouped"] is True
    assert grouping["low_dataset_support"] is False
    assert grouped_dataset.recipe.source["auto_grouping"]["selected_case_ids"] == [
        "task-alpha",
        "task-alpha-2",
    ]
    assert grouped_dataset.recipe.source["auto_grouping"]["skipped_group_count"] == 1


def test_auto_group_prefers_larger_group_when_confidence_ties_by_bucket(tmp_path) -> None:
    log_path = tmp_path / "trajectory.log"
    _write_trajectory_log(
        log_path,
        [
            {
                "task_id": "task-singleton",
                "trajectory": [
                    {
                        "meta": {"step": 1},
                        "state": {"input": {"content": "singleton task"}},
                        "action": {"content": "singleton failure"},
                        "reward": {"status": "failed"},
                    }
                ],
            },
            {
                "task_id": "task-cluster-1",
                "trajectory": [
                    {
                        "meta": {"step": 1},
                        "state": {"input": {"content": "cluster task"}},
                        "action": {"content": "cluster failure"},
                        "reward": {"status": "failed"},
                    }
                ],
            },
            {
                "task_id": "task-cluster-2",
                "trajectory": [
                    {
                        "meta": {"step": 1},
                        "state": {"input": {"content": "cluster followup"}},
                        "action": {"content": "cluster followup failure"},
                        "reward": {"status": "failed"},
                    }
                ],
            },
        ],
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="trajectory_log", path=str(log_path))
    )
    trace_packs = tuple(case.trace_pack for case in dataset.cases if case.trace_pack)
    singleton_target = SelfEvolveTargetRef("skill", "singleton", str(tmp_path / "singleton.md"))
    cluster_target = SelfEvolveTargetRef("skill", "cluster", str(tmp_path / "cluster.md"))

    def fake_infer(pack_group, *, workspace_root):
        pack = pack_group[0]
        is_singleton = pack.task_id == "task-singleton"
        return build_target_selection_decision(
            TargetSelectionReport(
                selected_target=singleton_target if is_singleton else cluster_target,
                confidence=0.9 if is_singleton else 0.8999999999999999,
                evidence_step_ids=(f"{pack.task_id}:step-1",),
                failure_category="skill",
                signals=("test_signal",),
                diagnostics={"task_id": pack.task_id},
            ),
            inventory=TargetInventory(entries=()),
            selection_origin="inferred",
        )

    grouped_dataset, _, grouping = _auto_group_trajectory_log_dataset(
        dataset,
        trace_packs,
        source_config=SelfEvolveEvalSourceConfig(kind="trajectory_log", path=str(log_path)),
        workspace_root=tmp_path,
        infer_target=fake_infer,
    )

    assert grouping["selected_group_id"] == "skill:cluster"
    assert grouping["low_dataset_support"] is False
    assert grouping["selected_case_count"] == 2
    assert [case.case_id for case in grouped_dataset.cases] == [
        "task-cluster-1",
        "task-cluster-2",
    ]


@pytest.mark.parametrize("trajectory_count", [1, 3])
def test_inferred_target_provenance_and_evidence_are_target_level(
    tmp_path,
    trajectory_count: int,
) -> None:
    skill_path = tmp_path / "aworld-skills" / "generic-capability" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: generic-capability\n---\n# Generic capability\n",
        encoding="utf-8",
    )
    packs = tuple(
        build_trace_pack(
            [
                {
                    "meta": {"step": 1},
                    "state": {
                        "input": {
                            "content": "Use generic-capability for this workflow."
                        }
                    },
                    "action": {"content": "The generic capability workflow failed."},
                    "reward": {"status": "failed"},
                }
            ],
            source_kind="current_trajectory",
            task_id=f"browser-member-{index}",
        )
        for index in range(trajectory_count)
    )

    decision = _infer_target_from_trace_packs(packs, workspace_root=tmp_path)

    assert isinstance(decision, TargetSelectionDecision)
    assert decision.report.selected_target is not None
    assert decision.report.selected_target.target_id == "generic-capability"
    assert decision.provenance is not None
    assert decision.provenance.trust_level == "local"
    assert decision.provenance.protected is False
    assert len(decision.report.evidence_step_ids) == trajectory_count
    assert len({decision.provenance}) == 1


def test_candidate_gate_results_never_omit_unresolved_trust_gate(tmp_path) -> None:
    candidate = CandidateVariant(
        candidate_id="candidate-unresolved",
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="demo",
            path=str(tmp_path / "SKILL.md"),
        ),
        content="---\nname: demo\n---\n# Demo\n",
        rationale="test unresolved provenance",
        target_fingerprint="sha256:old",
    )

    results = _candidate_gate_results(
        candidate,
        current_content="---\nname: demo\n---\n# Old\n",
        workspace_root=tmp_path,
        max_chars=10_000,
        target_provenance=None,
    )

    trust_results = [result for result in results if result.gate_name == "trust_provenance"]
    assert len(trust_results) == 1
    assert trust_results[0].passed is False


def test_candidate_gate_results_require_named_generated_mutation_policy(tmp_path) -> None:
    target = SelfEvolveTargetRef(
        target_type="skill",
        target_id="generated-capability",
        path=str(tmp_path / "drafts" / "generated-capability" / "SKILL.md"),
    )
    candidate = CandidateVariant(
        candidate_id="candidate-generated",
        target=target,
        content="---\nname: generated-capability\n---\n# Generated capability\n",
        rationale="generic generated proposal",
        target_fingerprint="sha256:old",
    )
    provenance = TargetProvenance(
        target=target,
        source_kind="skill",
        write_origin="target_inference",
        trust_level="generated",
        protected=False,
        reason="inferred target is absent from inventory",
    )

    denied = _candidate_gate_results(
        candidate,
        current_content="---\nname: generated-capability\n---\n# Old\n",
        workspace_root=tmp_path,
        max_chars=10_000,
        target_provenance=provenance,
    )
    allowed = _candidate_gate_results(
        candidate,
        current_content="---\nname: generated-capability\n---\n# Old\n",
        workspace_root=tmp_path,
        max_chars=10_000,
        target_provenance=provenance,
        allow_generated_target_mutation=True,
    )

    assert next(result for result in denied if result.gate_name == "trust_provenance").passed is False
    assert next(result for result in allowed if result.gate_name == "trust_provenance").passed is True


@pytest.mark.parametrize("trajectory_count", [1, 3])
def test_generated_target_aggregation_keeps_one_fail_closed_provenance_decision(
    tmp_path,
    trajectory_count: int,
) -> None:
    target = SelfEvolveTargetRef(
        target_type="skill",
        target_id="generated-capability",
        path=str(tmp_path / "drafts" / "generated-capability" / "SKILL.md"),
    )
    decisions = tuple(
        build_target_selection_decision(
            TargetSelectionReport(
                selected_target=target,
                confidence=0.85,
                evidence_step_ids=(f"member-{index}:step-1",),
                failure_category="skill",
                signals=("new_skill_candidate", "low_confidence"),
                diagnostics={"pack_id": f"pack-{index}"},
            ),
            inventory=TargetInventory(entries=()),
            selection_origin="inferred",
        )
        for index in range(trajectory_count)
    )

    aggregated = _aggregate_target_selection_decisions(decisions)

    assert aggregated.provenance is decisions[0].provenance
    assert aggregated.provenance is not None
    assert aggregated.provenance.trust_level == "generated"
    assert len(aggregated.report.evidence_step_ids) == trajectory_count
    assert aggregated.report.provenance_status == "resolved"
    assert aggregated.report.selection_origin == "inferred"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "target": {
                "target_type": "skill",
                "target_id": "capability",
                "path": "/workspace/skills/capability/SKILL.md",
            },
            "source_kind": "skill",
            "write_origin": "installed_skill",
            "trust_level": "local",
            "protected": False,
            "reason": "legacy sidecar without schema",
        },
        {
            "schema_version": 1,
            "target": {
                "target_type": "skill",
                "target_id": "capability",
                "path": "/workspace/skills/capability/SKILL.md",
            },
            "source_kind": "unknown",
            "write_origin": "installed_skill",
            "trust_level": "local",
            "protected": False,
            "reason": "unknown source kind",
        },
        {
            "schema_version": 1,
            "target": {
                "target_type": "skill",
                "target_id": "capability",
                "path": "/workspace/skills/capability/SKILL.md",
            },
            "source_kind": "skill",
            "write_origin": "target_inference",
            "trust_level": "local",
            "protected": False,
            "reason": "inconsistent classification",
        },
    ],
)
def test_load_target_provenance_rejects_legacy_unknown_and_malformed_sidecars(
    tmp_path,
    payload,
) -> None:
    sidecar = tmp_path / "target_provenance.json"
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    resolution = _load_target_provenance(sidecar)

    assert resolution.status == "unresolved"
    assert resolution.provenance is None


def test_load_target_selection_report_requires_typed_selection_origin(tmp_path) -> None:
    sidecar = tmp_path / "target_selection.json"
    sidecar.write_text(
        json.dumps(
            {
                "selected_target": {
                    "target_type": "skill",
                    "target_id": "capability",
                    "path": "/workspace/skills/capability/SKILL.md",
                },
                "confidence": 0.95,
                "evidence_step_ids": [],
                "failure_category": "skill",
                "signals": ["explicit_target"],
                "selection_origin": "unknown-origin",
            }
        ),
        encoding="utf-8",
    )

    report = _load_target_selection_report(sidecar)

    assert report is not None
    assert report.selection_origin is None


def test_load_target_selection_report_preserves_typed_selection_origin(tmp_path) -> None:
    sidecar = tmp_path / "target_selection.json"
    sidecar.write_text(
        json.dumps(
            {
                "selected_target": {
                    "target_type": "skill",
                    "target_id": "capability",
                    "path": "/workspace/skills/capability/SKILL.md",
                },
                "confidence": 0.95,
                "evidence_step_ids": [],
                "failure_category": "skill",
                "selection_origin": "inferred",
            }
        ),
        encoding="utf-8",
    )

    report = _load_target_selection_report(sidecar)

    assert report is not None
    assert report.selection_origin == "inferred"


@pytest.mark.asyncio
async def test_runner_does_not_treat_supplied_provenance_as_inventory_authority(
    tmp_path,
) -> None:
    skill_path = tmp_path / "aworld-skills" / "capability" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: capability\n---\n# Capability\n", encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="member-1", input={"task": "exercise capability"}),),
        recipe=DatasetRecipe(
            source={"kind": "test"},
            split_seed="seed",
            splits={"train": ["member-1"], "validation": [], "held_out": []},
        ),
    )
    report = TargetSelectionReport(
        selected_target=target.identity,
        confidence=1.0,
        evidence_step_ids=(),
        failure_category="explicit_target",
        selection_origin="operator_explicit",
    )
    supplied = TargetProvenance(
        target=target.identity,
        source_kind="skill",
        write_origin="operator_selection",
        trust_level="local",
        protected=False,
        reason="caller-supplied authorization claim",
    )

    await SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=EmptyOptimizer(),
        evaluation_backend=None,
        max_iterations=0,
        min_eval_cases=0,
    ).run_explicit_target(
        run_id="run-supplied-provenance",
        target=target,
        dataset=dataset,
        trace_packs=(),
        apply_policy="auto_verified",
        target_selection_report=report,
        target_provenance=supplied,
    )

    persisted = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-supplied-provenance"
            / "report.json"
        ).read_text(encoding="utf-8")
    )
    assert persisted["target_provenance"]["status"] == "unresolved"
    assert persisted["target_provenance"]["reason"] == (
        "supplied provenance does not match authoritative resolution"
    )
    assert not (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-supplied-provenance"
        / "target_provenance.json"
    ).exists()


@pytest.mark.asyncio
async def test_runner_treats_supplied_decision_as_consistency_claim_only(tmp_path) -> None:
    skill_path = tmp_path / "aworld-skills" / "capability" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: capability\n---\n# Capability\n", encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="member-1", input={"task": "exercise capability"}),),
        recipe=DatasetRecipe(
            source={"kind": "test"},
            split_seed="seed",
            splits={"train": ["member-1"], "validation": [], "held_out": []},
        ),
    )
    report = TargetSelectionReport(
        selected_target=target.identity,
        confidence=1.0,
        evidence_step_ids=(),
        failure_category="explicit_target",
        selection_origin=TargetSelectionOrigin.OPERATOR_EXPLICIT,
    )
    supplied_decision = TargetSelectionDecision(
        report=report,
        provenance_resolution=TargetProvenanceResolution(
            status="unresolved",
            provenance=None,
            reason="caller asserted a different resolution",
        ),
        selection_origin=TargetSelectionOrigin.OPERATOR_EXPLICIT,
    )

    await SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=EmptyOptimizer(),
        evaluation_backend=None,
        max_iterations=0,
        min_eval_cases=0,
    ).run_explicit_target(
        run_id="run-supplied-decision",
        target=target,
        dataset=dataset,
        trace_packs=(),
        apply_policy="auto_verified",
        target_selection_decision=supplied_decision,
    )

    run_path = (
        tmp_path / ".aworld" / "self_evolve" / "run-supplied-decision"
    )
    persisted = json.loads((run_path / "report.json").read_text(encoding="utf-8"))
    assert persisted["target_provenance"] == {
        "status": "unresolved",
        "path": None,
        "reason": "supplied target decision does not match authoritative resolution",
    }
    assert not (run_path / "target_provenance.json").exists()


@pytest.mark.asyncio
async def test_runner_persists_authoritative_explicit_selection_without_traces(
    tmp_path,
) -> None:
    skill_path = tmp_path / "aworld-skills" / "capability" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: capability\n---\n# Capability\n", encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="member-1", input={"task": "exercise capability"}),),
        recipe=DatasetRecipe(
            source={"kind": "test"},
            split_seed="seed",
            splits={"train": ["member-1"], "validation": [], "held_out": []},
        ),
    )

    await SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=EmptyOptimizer(),
        evaluation_backend=None,
        max_iterations=0,
        min_eval_cases=0,
    ).run_explicit_target(
        run_id="run-direct-explicit",
        target=target,
        dataset=dataset,
        trace_packs=(),
    )

    run_path = tmp_path / ".aworld" / "self_evolve" / "run-direct-explicit"
    selection = json.loads(
        (run_path / "target_selection.json").read_text(encoding="utf-8")
    )
    report = json.loads((run_path / "report.json").read_text(encoding="utf-8"))
    assert selection["selected_target"] == to_json_dict(target.identity)
    assert selection["selection_origin"] == "operator_explicit"
    assert selection["evidence_step_ids"] == []
    assert report["target_selection"] == selection


def test_explicit_target_selection_report_is_typed_without_trace_packs() -> None:
    report = _explicit_target_selection_report(
        SelfEvolveTargetRef("skill", "capability", "/workspace/capability/SKILL.md"),
        (),
    )

    assert report.selected_target is not None
    assert report.evidence_step_ids == ()
    assert report.diagnostics == {"pack_ids": [], "target_inference": "bypassed"}
    assert report.selection_origin is TargetSelectionOrigin.OPERATOR_EXPLICIT


def test_explicit_jsonl_target_preserves_origin_through_evaluator_rerun(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import aworld.self_evolve.runner as runner_module

    skill_path = tmp_path / "aworld-skills" / "capability" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: capability\n---\n# Capability\n\nOld guidance.\n",
        encoding="utf-8",
    )
    dataset_path = tmp_path / "evaluation.jsonl"
    dataset_path.write_text(
        json.dumps(
            {
                "case_id": "member-1",
                "input": {"task": "exercise the capability"},
                "expected_output": "completed",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner_module,
        "_default_cli_skill_candidate",
        lambda **_: (
            "---\nname: capability\n---\n# Capability\n\nImproved guidance.\n"
        ),
    )

    class ReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            result = CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[
                        {
                            "state": {"input": request.task_input},
                            "action": {"content": "old"},
                        }
                    ],
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[
                        {
                            "state": {"input": request.task_input},
                            "action": {"content": "improved"},
                        }
                    ],
                ),
            )
            replay_root = (
                Path(request.workspace_root)
                / ".aworld"
                / "self_evolve"
                / request.run_id
                / "replay"
                / candidate.candidate_id
            )
            replay_root.mkdir(parents=True, exist_ok=True)
            (replay_root / "request.json").write_text(
                json.dumps(to_json_dict(request), sort_keys=True),
                encoding="utf-8",
            )
            for directory_name, variant in (
                ("baseline", result.baseline),
                (candidate.candidate_id, result.candidate),
            ):
                variant_root = replay_root / directory_name
                variant_root.mkdir(parents=True, exist_ok=True)
                (variant_root / "trajectory.json").write_text(
                    json.dumps(to_json_dict(variant.trajectory), sort_keys=True),
                    encoding="utf-8",
                )
                (variant_root / "metrics.json").write_text(
                    json.dumps(to_json_dict(variant.metrics), sort_keys=True),
                    encoding="utf-8",
                )
            return result

    initial = optimize_from_cli_request(
        workspace_root=tmp_path,
        target="skill:capability",
        dataset=str(dataset_path),
        apply_policy="proposal",
        replay_enabled=True,
        candidate_replay_backend=ReplayBackend(),
        replay_candidate_limit=1,
    )

    source_selection_path = Path(initial["target_selection_path"])
    source_selection = json.loads(source_selection_path.read_text(encoding="utf-8"))
    assert source_selection["selection_origin"] == "operator_explicit"
    assert source_selection["evidence_step_ids"] == []
    assert initial["selected_candidate_id"] is not None

    class EvaluationBackend:
        async def evaluate_variant(self, request):
            score = 0.2 if request.candidate is None else 0.8
            return EvaluationSummary(
                variant_id=(
                    "baseline"
                    if request.candidate is None
                    else request.candidate.candidate_id
                ),
                metrics={"score": score, "latency_ms": 1.0, "cost_usd": 0.0},
                dataset_split=request.dataset_split,
            )

    rerun = optimize_from_cli_request(
        workspace_root=tmp_path,
        from_run=initial["run_id"],
        rerun_evaluator=True,
        apply_policy="proposal",
        evaluation_backend=EvaluationBackend(),
        min_eval_cases=0,
    )

    rerun_selection = json.loads(
        Path(rerun["target_selection_path"]).read_text(encoding="utf-8")
    )
    rerun_report = json.loads(Path(rerun["report_path"]).read_text(encoding="utf-8"))
    assert rerun_selection["selection_origin"] == "operator_explicit"
    assert rerun_selection["evidence_step_ids"] == []
    assert rerun_report["target_selection"]["selection_origin"] == "operator_explicit"
    assert rerun_report["target_provenance"]["status"] == "resolved"


def test_explicit_target_keeps_multi_task_trajectory_log_without_auto_grouping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import aworld.self_evolve.runner as runner_module

    skill_path = tmp_path / "aworld-skills" / "chosen" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: chosen\n---\n# Chosen\n", encoding="utf-8")
    log_path = tmp_path / "trajectory.log"
    _write_trajectory_log(
        log_path,
        [
            {
                "task_id": "task-one",
                "trajectory": [
                    {
                        "meta": {"step": 1},
                        "state": {"input": {"content": "first task"}},
                        "action": {"content": "first failure"},
                        "reward": {"status": "failed"},
                    }
                ],
            },
            {
                "task_id": "task-two",
                "trajectory": [
                    {
                        "meta": {"step": 1},
                        "state": {"input": {"content": "second task"}},
                        "action": {"content": "second failure"},
                        "reward": {"status": "failed"},
                    }
                ],
            },
        ],
    )

    def fail_auto_grouping(*args, **kwargs):
        pytest.fail("explicit --target must not run inferred-target auto grouping")

    monkeypatch.setattr(
        runner_module,
        "_auto_group_trajectory_log_dataset",
        fail_auto_grouping,
    )

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        target="skill:chosen",
        from_trajectory=str(log_path),
        apply_policy="proposal",
    )

    assert report_summary["status"] == "succeeded"
    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["target"]["target_id"] == "chosen"
    assert report["target_selection"]["diagnostics"]["target_inference"] == "bypassed"
    assert report["trajectory_set"]["member_roles"] == {"baseline": 2}
    assert "auto_grouping" not in report["trajectory_set"]


@pytest.mark.asyncio
async def test_auto_verified_no_candidate_is_rejected(tmp_path) -> None:
    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    store = FilesystemSelfEvolveStore(tmp_path)
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "summarize page"}},
            "action": {"content": "summary failed"},
            "reward": {"status": "failed"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="task-1",
    )
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="task-1",
    )

    result = await SelfEvolveRunner(
        store=store,
        optimizer=EmptyOptimizer(),
        evaluation_backend=None,
    ).run_explicit_target(
        run_id="run-no-candidate",
        target=target,
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    report = json.loads(
        (
            tmp_path / ".aworld" / "self_evolve" / "run-no-candidate" / "report.json"
        ).read_text(encoding="utf-8")
    )
    assert result.run.status.value == "rejected"
    assert report["status"] == "rejected"
    assert report["candidate_ids"] == []
    assert report["iterations"][0]["status"] == "no_candidate"
    assert report["gate_results"] == [
        {
            "gate_name": "candidate_generation",
            "passed": False,
            "reason": "optimizer did not produce a replayable candidate",
            "details": {
                "generated_candidate_count": 0,
                "iterations": 1,
            },
        }
    ]


@pytest.mark.asyncio
async def test_proposal_no_candidate_is_rejected_not_succeeded(tmp_path) -> None:
    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    target = SkillTextTarget(skill_path)
    store = FilesystemSelfEvolveStore(tmp_path)
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "summarize page"}},
            "action": {"content": "summary failed"},
            "reward": {"status": "failed"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="task-1",
    )
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="task-1",
    )

    result = await SelfEvolveRunner(
        store=store,
        optimizer=EmptyOptimizer(),
        evaluation_backend=None,
    ).run_explicit_target(
        run_id="run-proposal-no-candidate",
        target=target,
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="proposal",
    )

    report = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-proposal-no-candidate"
            / "report.json"
        ).read_text(encoding="utf-8")
    )
    assert result.run.status.value == "rejected"
    assert report["status"] == "rejected"
    assert report["selected_candidate_id"] is None
    assert report["no_op"]["status"] == "no_candidate"
    assert report["no_op"]["reason"] == "optimizer did not produce a candidate"
    assert report["population"]["generated_candidate_count"] == 0
    assert report["gate_results"] == [
        {
            "gate_name": "no_candidate",
            "passed": False,
            "reason": "optimizer did not produce a candidate",
            "details": None,
        }
    ]


@pytest.mark.asyncio
async def test_runner_records_terminal_artifact_retention_cleanup(tmp_path) -> None:
    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    artifact_root = tmp_path / ".aworld" / "self_evolve"
    for index in range(6):
        _write_terminal_run_with_raw_artifacts(
            artifact_root,
            f"run-old-{index}",
            1_000.0 + index,
        )
    target = SkillTextTarget(skill_path)
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "summarize page"}},
            "action": {"content": "summary failed"},
            "reward": {"status": "failed"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="task-1",
    )
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="task-1",
    )

    await SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=EmptyOptimizer(),
        evaluation_backend=None,
    ).run_explicit_target(
        run_id="run-current",
        target=target,
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="proposal",
    )

    report = json.loads(
        (artifact_root / "run-current" / "report.json").read_text(encoding="utf-8")
    )
    cleanup = report["artifact_retention"]
    assert cleanup["removed_run_count"] >= 1
    assert any("run-old-0/replay" in path for path in cleanup["removed_paths"])
    assert "run-old-0" not in cleanup["protected_run_ids"]
    assert not (artifact_root / "run-old-0" / "replay").exists()
    assert not (artifact_root / "run-old-0" / "overlays").exists()
    assert (artifact_root / "run-old-0" / "report.json").exists()
    assert (artifact_root / "run-current" / "run.json").exists()


@pytest.mark.asyncio
async def test_runner_passes_trace_lessons_to_candidate_generation(tmp_path) -> None:
    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    trajectory = [
        {
            "id": "step-a",
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "summarize page"}},
            "action": {"content": "summary failed"},
            "reward": {"status": "failed"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="lesson-task",
    )
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="lesson-task",
    )
    optimizer = CaptureOptimizer()

    await SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=optimizer,
        evaluation_backend=None,
    ).run_explicit_target(
        run_id="run-generation-lessons",
        target=SkillTextTarget(skill_path),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="proposal",
    )

    assert len(optimizer.requests) == 1
    assert optimizer.requests[0].evolution_context is not None
    assert optimizer.requests[0].evolution_context.trainable_cases
    lesson_types = [lesson.lesson_type for lesson in optimizer.requests[0].lesson_records]
    assert "trajectory_failure_memory" in lesson_types
    assert optimizer.requests[0].lesson_records[0].source_task_ids == ("lesson-task",)


@pytest.mark.asyncio
async def test_runner_passes_prior_rejected_feedback_as_generation_lessons(tmp_path) -> None:
    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    store = FilesystemSelfEvolveStore(tmp_path)
    store.write_report(
        "prior-rejected",
        {
            "run_id": "prior-rejected",
            "status": "rejected",
            "target": {
                "target_type": "skill",
                "target_id": "demo",
                "path": str(skill_path),
            },
            "iterations": [
                {
                    "candidate_id": "candidate-old",
                    "status": "rejected",
                    "failed_gates": ["score_improvement", "evidence_quality"],
                    "baseline_metrics": {"score": 91.0, "B2_efficiency": 4.5},
                    "candidate_metrics": {
                        "score": 84.0,
                        "B2_efficiency": 2.0,
                        "evidence_compacted": True,
                    },
                }
            ],
        },
    )
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Improve current task."}},
            "action": {"content": "Need a safer delta."},
            "reward": {"status": "failed"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="current-task",
    )
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="current-task",
    )
    optimizer = CaptureOptimizer()

    await SelfEvolveRunner(
        store=store,
        optimizer=optimizer,
        evaluation_backend=None,
    ).run_explicit_target(
        run_id="run-prior-lessons",
        target=SkillTextTarget(skill_path),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="proposal",
    )

    lesson_types = [lesson.lesson_type for lesson in optimizer.requests[0].lesson_records]
    assert "failure_memory" in lesson_types
    prior_lesson = next(
        lesson
        for lesson in optimizer.requests[0].lesson_records
        if lesson.lesson_type == "failure_memory"
    )
    assert "score_improvement" in prior_lesson.summary
    assert prior_lesson.metrics["candidate_score"] == 84.0


def test_rank_candidate_population_prefers_lesson_backed_small_deltas() -> None:
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo")
    broad_candidate = CandidateVariant(
        candidate_id="candidate-broad",
        target=target,
        content="# Demo\n\n" + "Broad guidance.\n" * 80,
        rationale="broad",
    )
    small_candidate = CandidateVariant(
        candidate_id="candidate-small",
        target=target,
        content="# Demo\n\nSmall lesson-backed delta.\n",
        rationale="small",
    )

    ranked = _rank_candidate_population(
        (broad_candidate, small_candidate),
        optimizer_diagnostics={
            "candidate_strategies": [
                {
                    "candidate_id": "candidate-broad",
                    "replay_priority": "medium",
                    "addressed_lessons": ["lesson-1"],
                    "preserved_success_behaviors": [],
                },
                {
                    "candidate_id": "candidate-small",
                    "replay_priority": "high",
                    "addressed_lessons": ["lesson-1"],
                    "preserved_success_behaviors": ["preserve lean path"],
                },
            ]
        },
        current_content="# Demo\n",
    )

    assert [candidate.candidate_id for candidate in ranked] == [
        "candidate-small",
        "candidate-broad",
    ]


def test_iteration_validation_feedback_includes_baseline_comparison_metrics() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-1",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="test",
    )
    baseline_summary = EvaluationSummary(
        variant_id="baseline",
        metrics={
            "score": 75.4,
            "A1_groundedness": 5.0,
            "A2_completeness": 4.7,
            "B2_efficiency": 3.0,
            "evidence_block_count": 22.3,
            "evidence_incomplete": 0.33,
            "latency_ms": 202_372,
        },
        dataset_split="validation",
    )
    candidate_summary = EvaluationSummary(
        variant_id="cand-1",
        metrics={
            "score": 70.3,
            "A1_groundedness": 4.0,
            "A2_completeness": 3.7,
            "B2_efficiency": 2.7,
            "evidence_block_count": 30.0,
            "evidence_incomplete": 0.67,
            "latency_ms": 333_973,
        },
        dataset_split="validation",
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=baseline_summary,
        candidate_summary=candidate_summary,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="score_improvement",
                passed=False,
                reason="score improvement below minimum delta",
            )
        ],
    )

    assert len(feedback) == 1
    metrics = feedback[0].metrics
    assert metrics["baseline_score"] == 75.4
    assert metrics["candidate_score"] == 70.3
    assert metrics["score_delta"] == pytest.approx(-5.1)
    assert metrics["baseline_A1_groundedness"] == 5.0
    assert metrics["candidate_A1_groundedness"] == 4.0
    assert metrics["A1_groundedness_delta"] == pytest.approx(-1.0)
    assert metrics["baseline_A2_completeness"] == 4.7
    assert metrics["candidate_A2_completeness"] == 3.7
    assert metrics["A2_completeness_delta"] == pytest.approx(-1.0)
    assert metrics["baseline_B2_efficiency"] == 3.0
    assert metrics["candidate_B2_efficiency"] == 2.7
    assert metrics["B2_efficiency_delta"] == pytest.approx(-0.3)
    assert metrics["baseline_evidence_block_count"] == 22.3
    assert metrics["candidate_evidence_block_count"] == 30.0
    assert metrics["evidence_block_count_delta"] == pytest.approx(7.7)
    assert metrics["baseline_evidence_incomplete"] == 0.33
    assert metrics["candidate_evidence_incomplete"] == 0.67
    assert metrics["evidence_incomplete_delta"] == pytest.approx(0.34)
    assert metrics["baseline_latency_ms"] == 202_372
    assert metrics["candidate_latency_ms"] == 333_973
    assert metrics["latency_ms_delta"] == 131_601
    assert metrics["failed_gates"] == ["score_improvement"]
    assert metrics["candidate_validation_diagnostics"] == [
        {
            "code": "failed_gate",
            "stage": "score_improvement",
            "reason": "score improvement below minimum delta",
        }
    ]


def test_iteration_validation_feedback_preserves_nested_root_cause_and_repair_package() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-runtime",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="implement candidate-owned runtime",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def websocket_control_frame():\n    return 'incomplete'\n",
            ),
        ),
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate replay did not produce comparable paired outcomes",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "candidate_failure": {
                        "type": "ReplayServiceProtocolError",
                        "reason": "WebSocket control frame failed",
                        "outcome": "candidate_failure",
                    },
                },
            )
        ],
    )

    metrics = feedback[0].metrics
    diagnostics = metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["stage"] == "candidate_replay"
    assert diagnostics[0]["reason"] == (
        "candidate replay did not produce comparable paired outcomes"
    )
    assert diagnostics[0]["details"]["candidate_failure"]["reason"] == (
        "WebSocket control frame failed"
    )
    assert metrics["repair_candidate_package"] == {
        "candidate_id": "cand-runtime",
        "rationale": "implement candidate-owned runtime",
        "content": "# Demo",
        "files": [
            {
                "path": "replay/runtime.py",
                "operation": "upsert",
                "executable": False,
                "content": "def websocket_control_frame():\n    return 'incomplete'",
            }
        ],
    }


def test_iteration_validation_feedback_preserves_complete_large_runtime_source() -> None:
    runtime_source = (
        "def handle(message):\n"
        + "    observed = message\n" * 800
        + "def main():\n    return 'runtime-tail-preserved'\n"
    )
    assert len(runtime_source) > 16_000
    candidate = CandidateVariant(
        candidate_id="cand-large-runtime",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="repair a complete candidate-owned runtime",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content=runtime_source,
            ),
        ),
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="runtime needs a focused protocol repair",
                details={"failure_class": "candidate", "repairable": True},
            )
        ],
    )

    preserved = feedback[0].metrics["repair_candidate_package"]["files"][0][
        "content"
    ]
    assert preserved == runtime_source.strip()
    assert preserved.endswith("return 'runtime-tail-preserved'")


def test_replay_gate_marks_candidate_owned_protocol_failure_as_repairable() -> None:
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo")
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input="Replay task"),),
        recipe=DatasetRecipe(
            source={"kind": "test"},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    replay_result = _CandidateReplayResult(
        request=CandidateReplayRequest(
            run_id="run-protocol-failure",
            task_id="task-1",
            workspace_root="/tmp/workspace",
            target=target,
            candidate_id="cand-runtime",
            overlay_skill_root="/tmp/overlay",
            task_input="Replay task",
        ),
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="failed",
            trajectory=[],
            failure={
                "type": "ReplayServiceProtocolError",
                "reason": "WebSocket control frame failed",
                "outcome": "candidate_failure",
            },
        ),
        candidate=ReplayVariantResult(
            variant_id="cand-runtime",
            status="failed",
            trajectory=[],
            failure={"reason": "baseline_preflight_failed"},
        ),
    )

    details = _replay_gate_details(replay_result, dataset=dataset)

    assert details["failure_class"] == "candidate"
    assert details["repairable"] is True
    assert details["failure_stage"] == "replay_capability"


def test_typed_gate_feedback_keeps_candidate_cause_from_baseline_slot_once() -> None:
    target = SelfEvolveTargetRef(target_type="skill", target_id="generic")
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="case-a", input="Replay task"),),
        recipe=DatasetRecipe(
            source={"kind": "test"},
            split_seed="seed",
            splits={"train": ["case-a"], "validation": [], "held_out": []},
        ),
    )
    request = CandidateReplayRequest(
        run_id="run-generic",
        task_id="case-a",
        workspace_root="/tmp/workspace",
        target=target,
        candidate_id="candidate-generic",
        overlay_skill_root="/tmp/overlay",
        task_input="Replay task",
    )
    cause = ReplayFailureEvent(
        event_id="cause-once",
        code="capability_contract_rejected",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.CANDIDATE,
        repairable=True,
        category="capability_contract",
        diagnostics={"raw_response": "SECRET_TOKEN=abc123"},
    )
    replay_result = _CandidateReplayResult(
        request=request,
        baseline=ReplayVariantResult(
            variant_id="baseline",
            status="failed",
            trajectory=[],
            failure=cause,
        ),
        candidate=ReplayVariantResult(
            variant_id="candidate-generic",
            status="blocked",
            trajectory=[],
            blocked_by=(cause,),
        ),
    )
    details = _replay_gate_details(replay_result, dataset=dataset)
    candidate = CandidateVariant(
        candidate_id="candidate-generic",
        target=target,
        content="# Generic\n",
        rationale="repair typed cause",
    )
    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=(
            GateResult("candidate_replay", False, "typed replay failure", details),
        ),
    )

    events = feedback[0].metrics["causal_failure_events"]
    assert len(events) == 1
    assert events[0]["owner"] == "candidate"
    assert events[0]["occurrence_count"] == 1
    assert events[0]["affected_member_count"] == 1
    assert "raw_response" not in str(events)


def test_typed_gate_feedback_preserves_exact_aggregate_scalars_without_raw_payload() -> None:
    observations = tuple(
        ReplayFailureObservation(
            event=ReplayFailureEvent(
                event_id=f"event-{index:03d}",
                code="capability_contract_rejected",
                owner=FailureOwner.CANDIDATE,
                stage=FailureStage.CAPABILITY_PREFLIGHT,
                scope=FailureScope.CANDIDATE,
                repairable=True,
                capability_id="/private/raw/capability-identity",
                summary=f"raw summary {index}",
                diagnostics={"raw_response": f"SECRET_TOKEN={index}"},
                artifact_refs=(f"/private/raw/{index}.json",),
            ),
            case_id=f"case-{index:03d}",
            run_id=f"run-{index:03d}",
            task_id=f"task-{index:03d}",
            candidate_id=f"candidate-{index:03d}",
        )
        for index in range(70)
    )
    aggregate = aggregate_replay_failure_observations(observations)[0]
    gate = GateResult(
        "candidate_replay",
        False,
        "typed replay failed",
        {"causal_failure_events": [aggregate.to_dict()]},
    )

    metrics = runner_module._typed_gate_feedback_metrics((gate, gate))
    events = metrics["causal_failure_events"]

    assert len(events) == 1
    assert events[0]["occurrence_count"] == 70
    assert events[0]["affected_member_count"] == 70
    assert events[0]["distinct_source_count"] == 70
    assert len(events[0]["occurrence_ids"]) == 64
    assert len(events[0]["source_task_ids"]) == 32
    serialized = json.dumps(events)
    assert "raw summary" not in serialized
    assert "raw_response" not in serialized
    assert "SECRET_TOKEN" not in serialized
    assert "/private/raw" not in serialized
    assert events[0]["capability_identity_digest"] is not None


def test_merge_validation_feedback_accumulates_and_deduplicates_current_run_history() -> None:
    first = EvaluationSummary(
        variant_id="candidate-1",
        metrics={"failed_gates": ["replay_adaptation"]},
        dataset_split="validation",
    )
    second = EvaluationSummary(
        variant_id="candidate-2",
        metrics={"failed_gates": ["candidate_replay"]},
        dataset_split="validation",
    )

    merged = _merge_validation_feedback((first,), (first, second))

    assert merged == (first, second)


def test_merge_validation_feedback_keeps_latest_repair_package_per_failure_family() -> None:
    def replay_failure(candidate_id: str, source: str) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            metrics={
                "failed_gates": ["candidate_replay"],
                "failure_class": "candidate",
                "candidate_validation_diagnostics": [
                    {
                        "code": "failed_gate",
                        "stage": "candidate_replay",
                        "reason": "candidate screening replay failed",
                        "details": {
                            "baseline_failure": {
                                "type": "ReplayServiceProtocolError",
                                "reason": "protocol probe response mismatch",
                            }
                        },
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {"path": "replay/runtime.py", "content": source}
                    ],
                },
            },
            dataset_split="validation",
        )

    first = replay_failure("candidate-1", "old source")
    latest = replay_failure("candidate-2", "latest source")

    merged = _merge_validation_feedback((first,), (latest,))

    assert merged == (latest,)


def test_merge_validation_feedback_keeps_deepest_interaction_frontier() -> None:
    def replay_failure(
        candidate_id: str,
        *,
        interaction_progress: int,
    ) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate_id,
            metrics={
                "failed_gates": ["candidate_replay"],
                "failure_class": "candidate",
                "interaction_progress": interaction_progress,
                "candidate_validation_diagnostics": [
                    {
                        "code": "implement_async_endpoint_completion",
                        "stage": "replay_capability",
                        "reason": "navigation awaits a completion event",
                    }
                ],
                "repair_candidate_package": {
                    "candidate_id": candidate_id,
                    "files": [
                        {
                            "path": "replay/runtime.py",
                            "content": f"# {candidate_id}\n",
                        }
                    ],
                },
            },
            dataset_split="validation",
        )

    deeper = replay_failure("candidate-deeper", interaction_progress=32)
    newer_but_shallow = replay_failure(
        "candidate-shallow",
        interaction_progress=6,
    )

    merged = _merge_validation_feedback((deeper,), (newer_but_shallow,))

    assert merged == (deeper,)


def test_protocol_probe_mismatch_feedback_requires_exact_branch_verification() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-runtime",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="repair protocol runtime",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def handle(request):\n    return request\n",
            ),
        ),
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate screening replay failed",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "baseline_failure": {
                        "type": "ReplayServiceProtocolError",
                        "reason": (
                            "protocol probe response mismatch: kind=websocket "
                            "path=/ws match=substring expected_sha256=abc "
                            "expected_bytes=8 expected_shape=utf8_text "
                            "response_bytes=77 response_payload_bytes=77 "
                            "response_sha256=def response_shape=json_object"
                        ),
                    },
                },
            )
        ],
    )

    diagnostics = feedback[0].metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["code"] == "verify_declared_protocol_probe_branch"
    assert "request_text" in diagnostics[0]["reason"]
    assert "returned candidate source" in diagnostics[0]["reason"]
    assert "semantic containment" in diagnostics[0]["reason"]
    assert "Every declared probe is executed" in diagnostics[0]["reason"]
    assert "remove redundant probes" in diagnostics[0]["reason"]
    assert diagnostics[0]["probe_kind"] == "websocket"
    assert diagnostics[0]["probe_path"] == "/ws"
    assert diagnostics[0]["expected_sha256"] == "abc"
    assert diagnostics[0]["response_sha256"] == "def"
    assert diagnostics[0]["expected_shape"] == "utf8_text"
    assert diagnostics[0]["response_shape"] == "json_object"
    assert "compiler and runtime" in diagnostics[0]["reason"]
    assert "one canonical deterministic selector" in diagnostics[0]["reason"]
    assert "hard-code" in diagnostics[0]["reason"]
    assert "put that literal" not in diagnostics[0]["reason"]


def test_recorded_response_selector_drift_feedback_requires_both_sources() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-selector-drift",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="repair protocol runtime",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def handle(request):\n    return request\n",
            ),
        ),
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_repair_conformance",
                passed=False,
                reason="candidate declared repair probe failed before task rollout",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "diagnostics": [
                        {
                            "reason": (
                                "protocol probe response mismatch: kind=http "
                                "path=/ expected_preview=compiler-leaf "
                                "response_preview={\"message\":\"recorded\"} "
                                "classification=recorded_response_selector_drift "
                                "required_change="
                                "align_compiler_runtime_recorded_response_selection"
                            )
                        }
                    ],
                },
            )
        ],
    )

    diagnostics = feedback[0].metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["code"] == (
        "align_compiler_runtime_recorded_response_selection"
    )
    assert "Change both the compiler probe builder and the runtime selector" in (
        diagnostics[0]["reason"]
    )
    assert "hard-code" in diagnostics[0]["reason"]


def test_protocol_trace_contract_failure_gets_structured_repair_feedback() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-trace-contract",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="repair protocol trace",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def trace():\n    return None\n",
            ),
        ),
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_repair_conformance",
                passed=False,
                reason="candidate declared repair probe failed before task rollout",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "diagnostics": [
                        {
                            "reason": (
                                "skill runtime protocol_trace.jsonl record is "
                                "missing required summary fields: correlation"
                            )
                        }
                    ],
                },
            )
        ],
    )

    diagnostics = feedback[0].metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["code"] == "repair_protocol_trace_contract"
    assert "direction, sequence, kind, fields, and correlation" in (
        diagnostics[0]["reason"]
    )
    assert "lifecycle-only directions such as system" in diagnostics[0]["reason"]


def test_task_level_endpoint_mismatch_requires_real_interaction_repair() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-runtime",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="publish fixture runtime",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def handle(request):\n    return b'fixture'\n",
            ),
        ),
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate screening replay failed",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "baseline_failure": {
                        "type": "TimeoutExpired",
                        "reason": "replay timed out",
                        "diagnostics": {
                            "task_artifacts": [
                                {
                                    "tail": (
                                        "All endpoint discovery methods failed; "
                                        "WebSocket protocol error"
                                    )
                                }
                            ]
                        },
                    },
                },
            )
        ],
    )

    diagnostics = feedback[0].metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["code"] == "implement_observed_endpoint_interactions"
    assert "declared probes passed" in diagnostics[0]["reason"]
    assert "bounded task diagnostics" in diagnostics[0]["reason"]


def test_task_level_endpoint_mismatch_classification_survives_long_stdout_prefix() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-runtime",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="publish generic websocket runtime",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def handle(request):\n    return b'fixture'\n",
            ),
        ),
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate screening replay failed",
                details={
                    "failure_class": "candidate_replay_capability",
                    "repairable": True,
                    "baseline_failure": {
                        "type": "TimeoutExpired",
                        "reason": "replay timed out",
                        "diagnostics": {
                            "stdout_tail": (
                                "bounded earlier task output " * 100
                                + "the supplied service is not a CDP browser endpoint"
                            )
                        },
                    },
                },
            )
        ],
    )

    diagnostics = feedback[0].metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["code"] == "implement_observed_endpoint_interactions"


def test_progressing_replay_timeout_requires_task_plane_interaction_repair() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-runtime",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="publish a responsive protocol runtime",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def handle(request):\n    return {'token': 'placeholder'}\n",
            ),
        ),
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate screening replay failed",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "baseline_failure": {
                        "type": "TimeoutExpired",
                        "reason": "replay timed out",
                        "diagnostics": {
                            "replay_service_protocol_traces": [
                                {
                                    "tail": (
                                        '{"direction":"in","sequence":135,'
                                        '"kind":"request","correlation":'
                                        '{"method":"Target.getTargets"}}\n'
                                        '{"direction":"in","sequence":137,'
                                        '"kind":"request","correlation":'
                                        '{"method":"Runtime.evaluate"}}\n'
                                        '{"direction":"out","sequence":138,'
                                        '"kind":"response","correlation":{"id":17}}'
                                    )
                                }
                            ]
                        },
                    },
                },
            )
        ],
    )

    diagnostics = feedback[0].metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["code"] == "implement_observed_endpoint_interactions"
    assert "task-plane" in diagnostics[0]["reason"]
    assert "placeholder" in diagnostics[0]["reason"]
    assert "representative probe" in diagnostics[0]["reason"]
    assert diagnostics[0]["observed_request_operations"] == [
        "Target.getTargets",
        "Runtime.evaluate",
    ]
    assert feedback[0].metrics["interaction_progress"] == 138


def test_completed_candidate_interaction_requires_bounded_finalization() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-runtime",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n\nPreserve grounded evidence.\n",
        rationale="publish a responsive protocol runtime",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def handle(request):\n    return {'content': 'recorded'}\n",
            ),
        ),
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate replay did not produce comparable paired outcomes",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "candidate_failure": {
                        "type": "TimeoutExpired",
                        "reason": "replay timed out",
                        "outcome": "candidate_failure",
                        "failure_class": "candidate_task_behavior",
                        "failure_stage": "task_rollout",
                        "repairable": True,
                        "completed_data_plane_operations": ["content"],
                    },
                },
            )
        ],
    )

    metrics = feedback[0].metrics
    diagnostics = metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["code"] == (
        "finalize_after_successful_endpoint_interaction"
    )
    assert diagnostics[0]["completed_data_plane_operations"] == ["content"]
    assert metrics["required_behaviors"] == [
        "persist_first_successful_structured_evidence",
        "write_manifest_before_additional_collection",
        "verify_task_semantic_sufficiency_before_finalizing",
        "do_not_treat_transport_success_as_task_completion",
        "continue_bounded_acquisition_when_payload_is_only_metadata_or_execution_summary",
        "stop_after_sufficient_evidence",
        "return_bounded_evidence_ledger",
    ]
    assert "delivery signal rather than task completion" in diagnostics[0]["reason"]
    assert metrics["repair_candidate_package"]["content"] == candidate.content.rstrip()
    assert metrics["authoritative_replay_failure"] is True


def test_progressing_timeout_extracts_operations_nested_in_trace_fields() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-runtime",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="publish a responsive protocol runtime",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def handle(request):\n    return {'status': 'ready'}\n",
            ),
        ),
    )
    trace = "\n".join(
        (
            '{"direction":"inbound","sequence":21,"kind":"http_request",'
            '"fields":[{"method":"GET","path":"/json"}],'
            '"correlation":{"correlation_id":"opaque"}}',
            '{"direction":"outbound","sequence":22,"kind":"http_response",'
            '"fields":[{"status":404}],"correlation":{"correlation_id":"opaque"}}',
            '{"direction":"inbound","sequence":23,"kind":"http_request",'
            '"fields":["method:GET","path:/json/version"],'
            '"correlation":{"correlation_id":"opaque"}}',
            '{"direction":"inbound","sequence":24,"kind":"websocket_text",'
            '"fields":[{"method":"Runtime.evaluate"}],'
            '"correlation":{"correlation_id":"opaque"}}',
        )
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate screening replay failed",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "baseline_failure": {
                        "type": "TimeoutExpired",
                        "reason": "replay timed out",
                        "diagnostics": {
                            "replay_service_protocol_traces": [{"tail": trace}],
                        },
                    },
                },
            )
        ],
    )

    diagnostics = feedback[0].metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["code"] == "implement_observed_endpoint_interactions"
    assert diagnostics[0]["observed_request_operations"] == [
        "/json",
        "/json/version",
        "Runtime.evaluate",
    ]
    assert feedback[0].metrics["interaction_progress"] == 24


def test_progressing_task_plane_timeout_outranks_stale_navigation_output() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-runtime",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="publish a responsive protocol runtime",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def handle(request):\n    return {'token': 'placeholder'}\n",
            ),
        ),
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate screening replay failed",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "baseline_failure": {
                        "type": "TimeoutExpired",
                        "reason": "replay timed out",
                        "diagnostics": {
                            "stdout_tail": (
                                "earlier output: 正在导航到首页; 等待页面加载; "
                                "later output: snapshot and extraction commands ran"
                            ),
                            "replay_service_protocol_traces": [
                                {
                                    "tail": (
                                        '{"direction":"in","sequence":149,'
                                        '"kind":"ws_request","correlation":'
                                        '{"method":"Runtime.evaluate"}}\n'
                                        '{"direction":"out","sequence":150,'
                                        '"kind":"ws_response","correlation":{"id":62}}\n'
                                        '{"direction":"in","sequence":151,'
                                        '"kind":"ws_request","correlation":'
                                        '{"method":"Runtime.evaluate"}}\n'
                                        '{"direction":"out","sequence":152,'
                                        '"kind":"ws_response","correlation":{"id":63}}'
                                    )
                                }
                            ],
                        },
                    },
                },
            )
        ],
    )

    diagnostics = feedback[0].metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["code"] == "implement_observed_endpoint_interactions"
    assert diagnostics[0]["observed_request_operations"] == ["Runtime.evaluate"]
    assert feedback[0].metrics["interaction_progress"] == 152


def test_candidate_repair_diagnostics_ignore_baseline_only_routing_gap() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-runtime",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="publish a responsive protocol runtime",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def handle(request):\n    return {'items': []}\n",
            ),
        ),
    )
    baseline_trace = (
        '{"direction":"in","sequence":20,"kind":"ws_request",'
        '"correlation":{"method":"records.query","sessionId":"s-1"}}\n'
        '{"direction":"out","sequence":21,"kind":"ws_response",'
        '"correlation":{"id":1}}'
    )
    candidate_trace = (
        '{"direction":"in","sequence":98,"kind":"ws_request",'
        '"correlation":{"method":"records.query","sessionId":"s-1"}}\n'
        '{"direction":"out","sequence":99,"kind":"ws_response",'
        '"correlation":{"id":8,"sessionId":"s-1"}}'
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate screening replay failed",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "baseline_failure": {
                        "type": "TimeoutExpired",
                        "reason": "replay timed out",
                        "diagnostics": {
                            "replay_service_protocol_traces": [
                                {"tail": baseline_trace}
                            ]
                        },
                    },
                    "candidate_failure": {
                        "type": "TimeoutExpired",
                        "reason": "replay timed out",
                        "diagnostics": {
                            "replay_service_protocol_traces": [
                                {"tail": candidate_trace}
                            ],
                            "stdout_tail": "recorded response was empty",
                        },
                    },
                },
            )
        ],
    )

    diagnostics = feedback[0].metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["code"] == "implement_observed_endpoint_interactions"
    assert diagnostics[0]["observed_request_operations"] == ["records.query"]
    assert feedback[0].metrics["interaction_progress"] == 99


def test_task_level_endpoint_schema_mismatch_requires_real_interaction_repair() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-runtime",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="publish fixture runtime",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def handle(request):\n    return b'fixture'\n",
            ),
        ),
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate screening replay failed",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "baseline_failure": {
                        "reason": (
                            "failed to deserialize response from supplied replay "
                            "endpoint: missing field sessionId"
                        )
                    },
                },
            )
        ],
    )

    diagnostics = feedback[0].metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["code"] == "implement_observed_endpoint_interactions"


def test_task_level_endpoint_navigation_stall_requires_event_interaction_repair() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-runtime",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="publish command-response runtime",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def handle(request):\n    return {'frameId': 'frame-1'}\n",
            ),
        ),
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate screening replay failed",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "baseline_failure": {
                        "reason": "replay timed out",
                        "diagnostics": {
                            "stdout_tail": (
                                "The script hung during navigation against the supplied "
                                "replay endpoint while waiting for the page to load"
                            ),
                            "replay_service_protocol_traces": [
                                {
                                    "tail": (
                                        '{"sequence":31,"kind":"request"}\n'
                                        '{"sequence":32,"kind":"response"}'
                                    )
                                }
                            ],
                        },
                    },
                },
            )
        ],
    )

    diagnostics = feedback[0].metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["code"] == "implement_async_endpoint_completion"
    assert "stateful interactions" in diagnostics[0]["reason"]
    assert "asynchronous completion" in diagnostics[0]["reason"]
    assert feedback[0].metrics["interaction_progress"] == 32


def test_inbound_only_protocol_trace_requires_handler_abort_diagnosis() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-runtime",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="publish fixture-backed runtime",
        files=(
            CandidateFileDelta(
                path="replay/runtime.py",
                content=(
                    "def handle(data):\n"
                    "    return data.get('result')\n"
                    "try:\n"
                    "    handle([])\n"
                    "except Exception:\n"
                    "    pass\n"
                ),
            ),
        ),
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate screening replay failed",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "baseline_failure": {
                        "reason": "WebSocket frame is incomplete",
                        "diagnostics": {
                            "replay_fixture_summaries": [
                                {
                                    "service_id": "svc-1",
                                    "fixture_bytes": 27,
                                    "json_root_type": "array",
                                }
                            ],
                            "replay_service_protocol_traces": [
                                {
                                    "tail": (
                                        '{"direction":"in","sequence":5,'
                                        '"kind":"ws_request","fields":'
                                        '["id","method"]}'
                                    )
                                }
                            ]
                        },
                    },
                },
            )
        ],
    )

    diagnostics = feedback[0].metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["code"] == "diagnose_protocol_handler_abort"
    assert diagnostics[0]["observed_fixture_root_types"] == ["array"]
    assert "arbitrary JSON root types" in diagnostics[0]["reason"]
    assert "Observed frozen fixture root types: array" in diagnostics[0]["reason"]
    assert "do not swallow" in diagnostics[0]["reason"]


def test_task_level_protocol_trace_identifies_dropped_routing_fields() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-runtime",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="publish multiplexed runtime",
    )
    trace = "\n".join(
        json.dumps(item)
        for item in (
            {
                "direction": "in",
                "sequence": 31,
                "kind": "request",
                "fields": ["id", "method", "params", "sessionId"],
                "correlation": {
                    "id": 10,
                    "method": "navigate",
                    "sessionId": "opaque-session",
                },
            },
            {
                "direction": "out",
                "sequence": 32,
                "kind": "response",
                "fields": ["id", "result"],
                "correlation": {"id": 10},
            },
            {
                "direction": "out",
                "sequence": 33,
                "kind": "completion-event",
                "fields": ["method", "params"],
                "correlation": {"method": "completed"},
            },
        )
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate screening replay failed",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "baseline_failure": {
                        "reason": "replay timed out while waiting for completion",
                        "diagnostics": {
                            "replay_service_protocol_traces": [{"tail": trace}],
                        },
                    },
                },
            )
        ],
    )

    diagnostics = feedback[0].metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["code"] == "preserve_protocol_routing_continuity"
    assert diagnostics[0]["routing_fields"] == ["sessionId"]
    assert "every response and follow-up event" in diagnostics[0]["reason"]
    assert feedback[0].metrics["interaction_progress"] == 33


def test_null_routing_correlation_does_not_create_a_false_gap() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-runtime",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="publish multiplexed runtime",
    )
    trace = "\n".join(
        json.dumps(item)
        for item in (
            {
                "direction": "in",
                "sequence": 98,
                "kind": "ws_request",
                "fields": ["id", "method"],
                "correlation": {
                    "id": 37,
                    "method": "records.query",
                    "sessionId": None,
                },
            },
            {
                "direction": "out",
                "sequence": 99,
                "kind": "ws_response",
                "fields": ["id", "result"],
                "correlation": {"id": 37},
            },
        )
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=None,
        candidate_summary=None,
        held_out_summary=None,
        failed_gates=[
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate screening replay failed",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "candidate_failure": {
                        "type": "TimeoutExpired",
                        "reason": "replay timed out",
                        "diagnostics": {
                            "replay_service_protocol_traces": [
                                {"tail": trace}
                            ]
                        },
                    },
                },
            )
        ],
    )

    diagnostics = feedback[0].metrics["candidate_validation_diagnostics"]
    assert diagnostics[0]["code"] == "implement_observed_endpoint_interactions"
    assert diagnostics[0]["observed_request_operations"] == ["records.query"]


def test_iteration_validation_feedback_does_not_mix_validation_delta_into_held_out() -> None:
    candidate = CandidateVariant(
        candidate_id="cand-1",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        content="# Demo\n",
        rationale="test",
    )
    baseline_summary = EvaluationSummary(
        variant_id="baseline",
        metrics={"score": 82.0, "A1_groundedness": 4.0},
        dataset_split="validation",
    )
    candidate_summary = EvaluationSummary(
        variant_id="cand-1",
        metrics={"score": 84.0, "A1_groundedness": 4.0},
        dataset_split="validation",
    )
    held_out_summary = EvaluationSummary(
        variant_id="cand-1",
        metrics={
            "score": 63.0,
            "A1_groundedness": 2.0,
            "evidence_incomplete": True,
        },
        dataset_split="held_out",
    )

    feedback = _iteration_validation_feedback(
        candidate=candidate,
        baseline_summary=baseline_summary,
        candidate_summary=candidate_summary,
        held_out_summary=held_out_summary,
        failed_gates=[
            GateResult(
                gate_name="global_regression_benchmark",
                passed=False,
                reason="held-out regression",
            )
        ],
    )

    assert len(feedback) == 2
    validation_metrics = feedback[0].metrics
    held_out_metrics = feedback[1].metrics
    assert validation_metrics["score_delta"] == 2.0
    assert held_out_metrics["score"] == 63.0
    assert held_out_metrics["A1_groundedness"] == 2.0
    assert held_out_metrics["evidence_incomplete"] is True
    assert "baseline_score" not in held_out_metrics
    assert "candidate_score" not in held_out_metrics
    assert "score_delta" not in held_out_metrics


def test_summary_with_replay_evidence_metrics_includes_replay_failure_diagnostics() -> None:
    summary = EvaluationSummary(
        variant_id="cand-1",
        metrics={
            "score": 68.0,
            "evidence_bundle_valid": False,
            "evidence_bundle_entry_count": 1,
        },
        dataset_split="validation",
    )
    replay_variant = ReplayVariantResult(
        variant_id="cand-1",
        status="succeeded",
        trajectory=[{"action": {"content": "answer"}}],
        metrics={
            "failed_repetition_count": 2,
            "evidence_bundle_valid": True,
            "evidence_bundle_entry_count": 2,
            "evidence_bundle_path": "/tmp/evidence_bundle.json",
            "repetition_failures": [
                {"type": "TimeoutExpired", "reason": "replay timed out"},
                {
                    "reason": "evidence_quality_failed",
                    "evidence_manifest_invalid_entry_count": 1,
                    "evidence_compaction_signals": ["tool_output_compacted"],
                },
            ],
        },
    )

    merged = _summary_with_replay_evidence_metrics(summary, replay_variant)

    assert merged.metrics["failed_repetition_count"] == 2
    assert merged.metrics["replay_failed_repetition_count"] == 2
    assert merged.metrics["replay_failure_reasons"] == [
        "replay timed out",
        "evidence_quality_failed",
    ]
    assert merged.metrics["replay_failure_types"] == [
        "TimeoutExpired",
        "evidence_quality_failed",
    ]
    assert merged.metrics["replay_evidence_manifest_invalid_entry_count"] == 1
    assert merged.metrics["evidence_bundle_valid"] is False
    assert merged.metrics["replay_evidence_bundle_valid"] is True
    assert merged.metrics["evidence_bundle_entry_count"] == 1
    assert merged.metrics["replay_evidence_bundle_entry_count"] == 2


@pytest.mark.asyncio
async def test_runner_persists_proposal_artifacts_without_mutating_skill_target(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix browser login guidance."}},
            "action": {"content": "I will inspect login traces."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="run-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="run-task",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nMention CDP profile mismatch.\n",
            "rationale": "Trace shows browser profile mismatch.",
        }

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
    )

    result = await runner.run_explicit_target(
        run_id="run-proposal",
        target=SkillTextTarget(skill_path),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="proposal",
    )

    assert result.run.status.value == "succeeded"
    assert result.selected_candidate is not None
    assert skill_path.read_text(encoding="utf-8") == original_content

    run_dir = store.run_path("run-proposal")
    candidate_path = run_dir / "candidates" / f"{result.selected_candidate.candidate_id}.md"
    diff_path = candidate_path.with_suffix(".diff")
    lineage_path = run_dir / "optimizer_lineage" / f"{result.selected_candidate.candidate_id}.json"
    report_path = run_dir / "report.json"

    assert candidate_path.exists()
    assert diff_path.exists()
    candidate_artifact = candidate_path.read_text(encoding="utf-8")
    assert "release_state: candidate" in candidate_artifact
    assert "Mention CDP profile mismatch." in candidate_artifact
    assert "-Old guidance." in diff_path.read_text(encoding="utf-8")
    assert "+Mention CDP profile mismatch." in diff_path.read_text(encoding="utf-8")
    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
    assert lineage["trainable_case_ids"] == ["run-task"]
    assert isinstance(lineage["content_fingerprint"], str)
    assert isinstance(lineage["semantic_fingerprint"], str)
    assert isinstance(lineage["lesson_set_fingerprint"], str)
    assert lineage["addressed_lesson_ids"]
    assert any(
        lesson_id.startswith("trajectory_failure_memory-")
        for lesson_id in lineage["addressed_lesson_ids"]
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["apply_policy"] == "proposal"
    assert report["optimizer_lineage"]["count"] == 1
    assert report["optimizer_lineage"]["paths"] == [str(lineage_path)]


@pytest.mark.asyncio
async def test_runner_writes_lesson_artifacts_from_validation_feedback(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Improve evidence handling."}},
            "action": {"content": "Evidence was compacted."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="lesson-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="lesson-task",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nNew guidance.\n",
            "rationale": "Try a bounded evidence path.",
        }

    class LessonEvaluationBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 90.0, "A1_groundedness": 5.0},
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 60.0,
                    "A1_groundedness": 2.0,
                    "evidence_compacted": True,
                    "evidence_incomplete": True,
                    "run_id": "run-lessons",
                    "task_id": "lesson-task",
                },
                dataset_split=request.dataset_split,
            )

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=LessonEvaluationBackend(),
    )

    await runner.run_explicit_target(
        run_id="run-lessons",
        target=SkillTextTarget(skill_path),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="proposal",
    )

    report = json.loads((store.run_path("run-lessons") / "report.json").read_text())
    lessons_path = Path(report["lessons"]["path"])
    lesson_lines = [
        json.loads(line)
        for line in lessons_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert report["lessons"]["count"] == len(lesson_lines)
    assert report["lessons"]["types"]["failure_memory"] == 1
    assert report["lessons"]["types"]["required_runtime_behavior"] == 1
    assert report["lessons"]["types"]["trajectory_failure_memory"] == 1
    assert lesson_lines[0]["source_task_ids"] == ["lesson-task"]
    assert "score_improvement" in lesson_lines[0]["metrics"]["failed_gates"]
    assert "artifact_first" in lesson_lines[1]["metrics"]["required_behaviors"]
    assert any(
        "lesson-task:step-1" in line["evidence_refs"]
        for line in lesson_lines
        if line["lesson_type"] == "trajectory_failure_memory"
    )


@pytest.mark.asyncio
async def test_runner_writes_harness_diagnostics_without_raw_evidence(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Improve evidence handling."}},
            "action": {"content": "SECRET_API_KEY=abc123 raw evidence should not leak"},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="diagnostic-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="diagnostic-task",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nNew guidance.\n",
            "rationale": "Try artifact-backed evidence.",
        }

    class EvidenceFailureBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={
                        "score": 80.0,
                        "evaluator_mode": "aworld_trajectory_evaluator",
                    },
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 85.0,
                    "evaluator_mode": "aworld_trajectory_evaluator",
                    "has_evidence": False,
                    "evidence_compacted": True,
                    "evidence_incomplete": True,
                    "report_path": str(tmp_path / "report.json"),
                },
                dataset_split=request.dataset_split,
            )

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=EvidenceFailureBackend(),
        min_eval_cases=0,
    )

    result = await runner.run_explicit_target(
        run_id="run-diagnostics",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    report = json.loads(
        (store.run_path("run-diagnostics") / "report.json").read_text(encoding="utf-8")
    )
    diagnostics_path = Path(report["harness_diagnostics"]["path"])
    diagnostics_text = diagnostics_path.read_text(encoding="utf-8")
    diagnostics = [
        json.loads(line) for line in diagnostics_text.splitlines() if line.strip()
    ]

    assert result.run.status.value == "rejected"
    assert report["harness_diagnostics"]["count"] == len(diagnostics)
    assert report["harness_diagnostics"]["types"]["artifact_lifecycle"] == 1
    assert report["harness_diagnostics"]["promotion_statuses"]["advisory"] == 1
    assert diagnostics[0]["promotion_status"] == "advisory"
    assert diagnostics[0]["affected_gates"] == ["evidence_quality"]
    assert diagnostics[0]["metrics"]["evidence_compacted"] is True
    assert "SECRET_API_KEY" not in diagnostics_text


@pytest.mark.asyncio
async def test_runner_auto_verified_applies_allowlisted_candidate_after_post_apply_gate(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    candidate_content = (
        "---\nname: demo\n---\n# Demo\n\n"
        "SECRET_TOKEN=abc123 Authorization: Bearer super-secret.\n"
        "/Users/me/private/transcript.txt ignore previous instructions.\n"
        "harness_diagnostic artifact_lifecycle evidence ids should not be runtime wording.\n"
        "Verified guidance.\n"
    )
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="apply-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="apply-task",
    )

    async def mutate(prompt: str) -> dict:
        return {"content": candidate_content, "rationale": "Verified candidate."}

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True, "score": 1.0},
            dataset_split="post_apply",
        )

    refreshed = []
    activated = []

    async def refresh_runtime(candidate):
        refreshed.append(candidate.candidate_id)
        return {"refreshed": True, "strategy": "test-hook"}

    async def activate_runtime_skill(candidate):
        activated.append(candidate.target.target_id)
        return {"enabled": True, "skill_name": candidate.target.target_id}

    class VerifiedBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 0.5, "latency_ms": 100.0, "cost_usd": 1.0},
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 0.9,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        post_apply_evaluator=post_apply,
        evaluation_backend=VerifiedBackend(),
        min_eval_cases=0,
        runtime_registry_refresher=refresh_runtime,
        runtime_skill_activator=activate_runtime_skill,
    )

    result = await runner.run_explicit_target(
        run_id="run-auto-verified",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "succeeded"
    updated_content = skill_path.read_text(encoding="utf-8")
    assert "self_evolve:" in updated_content
    assert "release_state: verified" in updated_content
    assert "verified_run_id: run-auto-verified" in updated_content
    assert "# Demo\n\nVerified guidance." in updated_content
    assert "SECRET_TOKEN" not in updated_content
    assert "super-secret" not in updated_content
    assert "/Users/me" not in updated_content
    assert "ignore previous instructions" not in updated_content
    assert "harness_diagnostic" not in updated_content
    assert "artifact_lifecycle" not in updated_content
    assert "evidence ids" not in updated_content
    report = json.loads((store.run_path("run-auto-verified") / "report.json").read_text(encoding="utf-8"))
    serialized_report = json.dumps(report, ensure_ascii=False)
    assert "SECRET_TOKEN" not in serialized_report
    assert "super-secret" not in serialized_report
    assert "/Users/me" not in serialized_report
    assert "ignore previous instructions" not in serialized_report
    assert report["apply_policy"] == "auto_verified"
    assert report["post_apply"]["status"] == "accepted"
    assert report["post_apply"]["metrics"]["post_apply_passed"] is True
    assert report["release_normalization"]["normalization_verification_passed"] is True
    assert report["release_normalization"]["pre_normalization_fingerprint"].startswith(
        "sha256:"
    )
    assert report["release_normalization"]["normalized_release_fingerprint"].startswith(
        "sha256:"
    )
    assert "Verified guidance." in report["release_normalization"]["preserved_runtime_constraints"]
    assert report["release_normalization"]["runtime_constraint_lesson_map"] == [
        {
            "constraint": "Verified guidance.",
            "lesson_ids": report["post_apply"]["metrics"]["addressed_lesson_ids"],
        }
    ]
    assert report["post_apply"]["metrics"]["addressed_lesson_ids"]
    assert report["release_checklist"]["status"] == "passed"
    assert report["acceptance_confidence"]["confidence"] == "verified"
    assert report["acceptance_confidence"]["verification_mode"] == "held_out"
    assert report["acceptance_confidence"]["passed"] is True
    assert {
        check["check_id"]: check["status"]
        for check in report["release_checklist"]["checks"]
    }["verification"] == "passed"
    assert report["content_quality_diagnostics"]["blocking"] is False
    assert activated == ["demo"]
    assert report["post_apply"]["activation"] == {"enabled": True, "skill_name": "demo"}
    assert refreshed == [result.selected_candidate.candidate_id]
    assert report["post_apply"]["refresh"] == {"refreshed": True, "strategy": "test-hook"}
    assert {gate["gate_name"] for gate in report["gate_results"]} >= {
        "score_improvement",
        "required_verification",
        "held_out_verification",
        "global_regression_benchmark",
    }
    apply_dir = store.run_path("run-auto-verified") / "apply"
    assert (apply_dir / f"{result.selected_candidate.candidate_id}.backup.md").read_text(encoding="utf-8") == original_content
    journal = json.loads((apply_dir / f"{result.selected_candidate.candidate_id}.journal.json").read_text(encoding="utf-8"))
    assert journal["candidate_id"] == result.selected_candidate.candidate_id
    assert journal["status"] == "accepted"
    assert journal["target"]["target_id"] == "demo"
    assert journal["backup_path"].endswith(".backup.md")


@pytest.mark.asyncio
async def test_runner_rejects_apply_when_release_normalization_removes_runtime_constraints(
    tmp_path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    internal_only_candidate = (
        "---\nname: demo\n---\n# Demo\n\n"
        "candidate_score exceeds baseline_score for source task ids: task_123.\n"
        "Preserve A1_groundedness and pass evidence_quality gate.\n"
    )
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="normalization-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="normalization-task",
    )

    async def mutate(prompt: str) -> dict:
        return {"content": internal_only_candidate, "rationale": "internal only"}

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True, "deterministic_signal": True},
            dataset_split="post_apply",
        )

    class VerifiedBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 0.5, "latency_ms": 100.0, "cost_usd": 1.0},
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 0.9,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        post_apply_evaluator=post_apply,
        evaluation_backend=VerifiedBackend(),
        min_eval_cases=0,
    )

    result = await runner.run_explicit_target(
        run_id="run-normalization-reject",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    report = json.loads(
        (store.run_path("run-normalization-reject") / "report.json").read_text(
            encoding="utf-8"
        )
    )

    assert result.run.status.value == "rejected"
    assert skill_path.read_text(encoding="utf-8") == original_content
    assert report["post_apply"]["status"] == "rejected"
    assert report["post_apply"]["metrics"]["normalization_equivalence_passed"] is False
    assert "release_normalization" in report["post_apply"]["metrics"]["evaluator_mode"]
    assert report["release_normalization"]["status"] == "rejected"
    assert report["release_normalization"]["normalization_verification_passed"] is False
    assert report["release_normalization"]["pre_normalization_fingerprint"].startswith(
        "sha256:"
    )


@pytest.mark.asyncio
async def test_runner_refines_candidates_across_iterations_with_validation_feedback(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix evidence handling."}},
            "action": {"content": "Evidence was missing."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="iter-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="iter-task",
    )
    bad_candidate = CandidateVariant(
        candidate_id="candidate-1",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nWeak guidance.\n",
        rationale="first attempt",
        target_fingerprint="fingerprint",
    )
    good_candidate = CandidateVariant(
        candidate_id="candidate-2",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nVerified guidance.\n",
        rationale="refined attempt",
        parent_candidate_ids=("candidate-1",),
        target_fingerprint="fingerprint",
    )

    class IteratingOptimizer:
        def __init__(self) -> None:
            self.requests: list[OptimizerRequest] = []

        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            self.requests.append(request)
            candidate = bad_candidate if len(self.requests) == 1 else good_candidate
            return OptimizerResult(candidates=(candidate,))

    class IteratingBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 0.5, "latency_ms": 100.0, "cost_usd": 1.0},
                    dataset_split=request.dataset_split,
                )
            score = 0.4 if request.candidate.candidate_id == "candidate-1" else 0.9
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": score,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": score >= 0.9,
                },
                dataset_split=request.dataset_split,
            )

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True},
            dataset_split="post_apply",
        )

    optimizer = IteratingOptimizer()
    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=optimizer,
        post_apply_evaluator=post_apply,
        evaluation_backend=IteratingBackend(),
        min_eval_cases=0,
        max_iterations=2,
    )

    result = await runner.run_explicit_target(
        run_id="run-iterative",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "succeeded"
    assert result.selected_candidate is good_candidate
    assert len(optimizer.requests) == 2
    assert optimizer.requests[0].validation_feedback == ()
    assert optimizer.requests[1].validation_feedback
    assert optimizer.requests[1].validation_feedback[0].variant_id == "candidate-1"
    assert optimizer.requests[1].validation_feedback[0].metrics["score"] == 0.4
    assert "Verified guidance." in skill_path.read_text(encoding="utf-8")
    report = json.loads((store.run_path("run-iterative") / "report.json").read_text(encoding="utf-8"))
    assert report["candidate_ids"] == ["candidate-1", "candidate-2"]
    assert report["selected_candidate_id"] == "candidate-2"
    assert report["iterations"][0]["candidate_id"] == "candidate-1"
    assert report["iterations"][0]["status"] == "rejected"
    assert report["iterations"][1]["candidate_id"] == "candidate-2"
    assert report["iterations"][1]["status"] == "accepted"


@pytest.mark.asyncio
async def test_runner_evaluates_candidate_population_until_one_passes(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Improve high baseline behavior."}},
            "action": {"content": "Baseline was already strong."},
            "reward": {"status": "ok"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="population-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="population-task",
    )
    weak_candidate = CandidateVariant(
        candidate_id="candidate-weak",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nBroad extra guidance.\n",
        rationale="too broad",
        target_fingerprint="fingerprint",
    )
    strong_candidate = CandidateVariant(
        candidate_id="candidate-strong",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nSmall verified delta.\n",
        rationale="targeted delta",
        target_fingerprint="fingerprint",
    )

    class PopulationOptimizer:
        def __init__(self) -> None:
            self.requests: list[OptimizerRequest] = []

        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            self.requests.append(request)
            return OptimizerResult(candidates=(weak_candidate, strong_candidate))

    class PopulationBackend:
        def __init__(self) -> None:
            self.candidate_ids: list[str | None] = []

        async def evaluate_variant(self, request):
            self.candidate_ids.append(
                request.candidate.candidate_id if request.candidate is not None else None
            )
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={
                        "score": 90.0,
                        "A1_groundedness": 5.0,
                        "A2_completeness": 4.5,
                        "B2_efficiency": 4.0,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                    },
                    dataset_split=request.dataset_split,
                )
            score = 88.0 if request.candidate.candidate_id == "candidate-weak" else 94.0
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": score,
                    "A1_groundedness": 5.0,
                    "A2_completeness": 4.5,
                    "B2_efficiency": 4.0,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                    "evidence_block_count": 1,
                    "evidence_compacted": False,
                    "evidence_incomplete": False,
                },
                dataset_split=request.dataset_split,
            )

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True},
            dataset_split="post_apply",
        )

    optimizer = PopulationOptimizer()
    backend = PopulationBackend()
    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=optimizer,
        post_apply_evaluator=post_apply,
        evaluation_backend=backend,
        min_eval_cases=0,
        replay_candidate_limit=2,
    )

    result = await runner.run_explicit_target(
        run_id="run-population",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "succeeded"
    assert optimizer.requests[0].max_candidates == 2
    assert result.selected_candidate is strong_candidate
    assert backend.candidate_ids.count("candidate-weak") == 2
    assert backend.candidate_ids.count("candidate-strong") == 2
    report = json.loads((store.run_path("run-population") / "report.json").read_text(encoding="utf-8"))
    assert report["candidate_ids"] == ["candidate-weak", "candidate-strong"]
    assert report["selected_candidate_id"] == "candidate-strong"
    assert report["population"]["generated_candidate_count"] == 2
    assert report["population"]["replayed_candidate_ids"] == [
        "candidate-weak",
        "candidate-strong",
    ]
    assert report["iterations"][0]["candidate_id"] == "candidate-weak"
    assert report["iterations"][0]["status"] == "rejected"
    assert report["iterations"][1]["candidate_id"] == "candidate-strong"
    assert report["iterations"][1]["status"] == "accepted"
    assert "Small verified delta." in skill_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_runner_persists_non_replayed_candidate_strategies(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Improve population handling."}},
            "action": {"content": "Need bounded candidate selection."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="population-audit-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="population-audit-task",
    )

    candidates = tuple(
        CandidateVariant(
            candidate_id=f"candidate-{index}",
            target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
            content=f"---\nname: demo\n---\n# Demo\n\nGuidance {index}.\n",
            rationale=f"candidate {index}",
            target_fingerprint="fingerprint",
        )
        for index in range(3)
    )

    class PopulationOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(
                candidates=candidates,
                diagnostics={
                    "candidate_strategies": [
                        {
                            "candidate_id": candidate.candidate_id,
                            "strategy_id": f"strategy-{candidate.candidate_id}",
                            "replay_priority": "high" if index == 0 else "medium",
                            "addressed_lessons": [f"lesson-{index}"],
                        }
                        for index, candidate in enumerate(candidates)
                    ]
                },
            )

    class PassingBackend:
        async def evaluate_variant(self, request):
            variant_id = request.candidate.candidate_id if request.candidate is not None else "baseline"
            return EvaluationSummary(
                variant_id=variant_id,
                metrics={
                    "score": 90.0 if request.candidate is None else 95.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                    "evidence_block_count": 1,
                    "evidence_compacted": False,
                    "evidence_incomplete": False,
                },
                dataset_split=request.dataset_split,
            )

    store = FilesystemSelfEvolveStore(tmp_path)
    await SelfEvolveRunner(
        store=store,
        optimizer=PopulationOptimizer(),
        evaluation_backend=PassingBackend(),
        min_eval_cases=0,
        replay_candidate_limit=1,
    ).run_explicit_target(
        run_id="run-population-audit",
        target=SkillTextTarget(skill_path),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="proposal",
    )

    report = json.loads((store.run_path("run-population-audit") / "report.json").read_text(encoding="utf-8"))
    assert report["population"]["replayed_candidate_ids"] == ["candidate-0"]
    assert report["population"]["non_replayed_candidate_count"] == 2
    assert report["population"]["non_replayed_candidate_strategies"] == [
        {
            "candidate_id": "candidate-1",
            "strategy_id": "strategy-candidate-1",
            "not_replayed_reason": "not_replayed_due_to_budget",
            "replay_priority": "medium",
            "addressed_lessons": ["lesson-1"],
        },
        {
            "candidate_id": "candidate-2",
            "strategy_id": "strategy-candidate-2",
            "not_replayed_reason": "not_replayed_due_to_budget",
            "replay_priority": "medium",
            "addressed_lessons": ["lesson-2"],
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_empty_members", (False, True))
async def test_runner_reuses_successful_baseline_replay_across_candidate_population(
    tmp_path,
    explicit_empty_members: bool,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Improve high baseline behavior."}},
            "action": {"content": "Baseline was already strong."},
            "reward": {"status": "ok"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="population-replay-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="population-replay-task",
    )
    candidate_one = CandidateVariant(
        candidate_id="candidate-one",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nSmall delta one.\n",
        rationale="first",
        target_fingerprint="fingerprint",
    )
    candidate_two = CandidateVariant(
        candidate_id="candidate-two",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nSmall delta two.\n",
        rationale="second",
        target_fingerprint="fingerprint",
    )

    class PopulationOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=(candidate_one, candidate_two))

    class ReplayBackend:
        def __init__(self) -> None:
            self.baseline_replay_dirs: list[str | None] = []

        async def replay_candidate(self, request, *, candidate, dataset):
            self.baseline_replay_dirs.append(getattr(request, "baseline_replay_dir", None))
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "baseline"}}],
                    metrics={"repetition_count": 2, "successful_repetition_count": 2},
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[{"action": {"content": candidate.candidate_id}}],
                    metrics={"repetition_count": 3, "successful_repetition_count": 3},
                ),
                member_results=() if explicit_empty_members else None,
            )

    class EvaluationBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                score = 90.0
            elif request.candidate.candidate_id == "candidate-one":
                score = 89.0
            else:
                score = 92.0
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={
                    "score": score,
                    "A1_groundedness": 5.0,
                    "A2_completeness": 5.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                    "evidence_block_count": 1,
                    "evidence_compacted": False,
                    "evidence_incomplete": False,
                },
                dataset_split=request.dataset_split,
            )

    replay_backend = ReplayBackend()

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True},
            dataset_split="post_apply",
        )

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=PopulationOptimizer(),
        evaluation_backend=EvaluationBackend(),
        post_apply_evaluator=post_apply,
        min_eval_cases=0,
        replay_enabled=True,
        candidate_replay_backend=replay_backend,
        replay_candidate_limit=2,
    )

    result = await runner.run_explicit_target(
        run_id="run-baseline-reuse",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    expected_baseline_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "run-baseline-reuse"
        / "replay"
        / "candidate-one"
        / "baseline"
    )
    assert result.run.status.value == (
        "rejected" if explicit_empty_members else "succeeded"
    )
    assert replay_backend.baseline_replay_dirs == (
        [None, None]
        if explicit_empty_members
        else [None, str(expected_baseline_dir)]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_owner", "failure_scope", "expected_candidate_ids"),
    (
        pytest.param(
            FailureOwner.CANDIDATE,
            FailureScope.CANDIDATE,
            ["candidate-one", "candidate-two"],
            id="candidate_owned_continues",
        ),
        pytest.param(
            FailureOwner.INFRASTRUCTURE,
            FailureScope.SHARED_RUN,
            ["candidate-one"],
            id="shared_run_stops",
        ),
    ),
)
async def test_runner_population_disposition_uses_typed_failure_owner_and_scope(
    tmp_path: Path,
    failure_owner: FailureOwner,
    failure_scope: FailureScope,
    expected_candidate_ids: list[str],
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: demo\n---\n# Demo\n\nOld guidance.\n",
        encoding="utf-8",
    )
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Improve replay behavior."}},
            "action": {"content": "Baseline output."},
            "reward": {"status": "ok"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="baseline-preflight-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="baseline-preflight-task",
    )
    target = SelfEvolveTargetRef(
        target_type="skill",
        target_id="demo",
        path=str(skill_path),
    )
    candidate_one = CandidateVariant(
        candidate_id="candidate-one",
        target=target,
        content="---\nname: demo\n---\n# Demo\n\nCandidate one.\n",
        rationale="first",
        target_fingerprint="fingerprint",
    )
    candidate_two = CandidateVariant(
        candidate_id="candidate-two",
        target=target,
        content="---\nname: demo\n---\n# Demo\n\nCandidate two.\n",
        rationale="second",
        target_fingerprint="fingerprint",
    )

    class PopulationOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=(candidate_one, candidate_two))

    class ReplayBackend:
        def __init__(self) -> None:
            self.candidate_ids: list[str] = []

        async def replay_candidate(self, request, *, candidate, dataset):
            self.candidate_ids.append(candidate.candidate_id)
            event = ReplayFailureEvent(
                code="candidate_capability_preflight_failed",
                owner=failure_owner,
                stage=FailureStage.CAPABILITY_PREFLIGHT,
                scope=failure_scope,
                repairable=failure_owner is FailureOwner.CANDIDATE,
            )
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status=ReplayExecutionStatus.FAILED,
                    trajectory=[],
                    failure=event,
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status=ReplayExecutionStatus.BLOCKED,
                    trajectory=[],
                    blocked_by=(event,),
                ),
            )

    replay_backend = ReplayBackend()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=PopulationOptimizer(),
        evaluation_backend=None,
        replay_enabled=True,
        candidate_replay_backend=replay_backend,
        replay_candidate_limit=2,
    )

    result = await runner.run_explicit_target(
        run_id="run-baseline-preflight-stop",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert replay_backend.candidate_ids == expected_candidate_ids


@pytest.mark.asyncio
async def test_runner_validates_registered_capability_before_replay(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: demo\n---\n# Demo\n\nOld guidance.\n",
        encoding="utf-8",
    )
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {
                "input": {
                    "content": "Read the recorded service at http://127.0.0.1:9888/data"
                }
            },
            "action": {"content": "The service was unavailable."},
            "reward": {"status": "failed"},
        }
    ]
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="capability-validation-task",
    )
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="capability-validation-task",
    )
    target = SkillTextTarget(skill_path, allow_auto_apply=True)
    candidate = CandidateVariant(
        candidate_id="candidate-without-required-capability",
        target=target.identity,
        content="---\nname: demo\n---\n# Demo\n\nNew guidance.\n",
        rationale="candidate omitted its required capability",
        target_fingerprint=target.fingerprint_current_content(),
    )

    class Optimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=(candidate,))

    class ReplayBackend:
        calls = 0

        async def replay_candidate(self, request, *, candidate, dataset):
            self.calls += 1
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="failed",
                    trajectory=[],
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="failed",
                    trajectory=[],
                ),
            )

    replay_backend = ReplayBackend()
    result = await SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=Optimizer(),
        replay_enabled=True,
        candidate_replay_backend=replay_backend,
    ).run_explicit_target(
        run_id="run-capability-validation",
        target=target,
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    report = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-capability-validation"
            / "report.json"
        ).read_text(encoding="utf-8")
    )
    assert replay_backend.calls == 0
    assert result.run.status.value == "rejected"
    capability_gate = next(
        gate
        for gate in report["gate_results"]
        if gate["gate_name"] == "candidate_capability_replay"
    )
    assert capability_gate["passed"] is False
    assert capability_gate["details"]["diagnostics"][0]["code"] == (
        "missing_capability_manifest"
    )


@pytest.mark.asyncio
async def test_runner_screens_population_on_representative_member_before_full_replay(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: demo\n---\n# Demo\n\nOld guidance.\n",
        encoding="utf-8",
    )
    cases = (
        EvalCase(case_id="task-a", input={"content": "Replay task A"}),
        EvalCase(case_id="task-b", input={"content": "Replay task B"}),
    )
    dataset = SelfEvolveDataset(
        cases=cases,
        recipe=DatasetRecipe(
            source={"kind": "trajectory_set", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a"], "validation": ["task-b"], "held_out": []},
            trainable_case_ids=("task-a", "task-b"),
        ),
    )
    trace_pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                "state": {"input": {"content": "Replay task A"}},
                "action": {"content": "Baseline output."},
                "reward": {"status": "ok"},
            }
        ],
        source_kind="current_trajectory",
        task_id="task-a",
    )
    target = SelfEvolveTargetRef(
        target_type="skill",
        target_id="demo",
        path=str(skill_path),
    )
    candidates = tuple(
        CandidateVariant(
            candidate_id=f"candidate-{index}",
            target=target,
            content=f"---\nname: demo\n---\n# Demo\n\nCandidate {index}.\n",
            rationale=f"candidate {index}",
            target_fingerprint="fingerprint",
        )
        for index in (1, 2)
    )

    class PopulationOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=candidates)

    class ReplayBackend:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[str, ...]]] = []

        async def replay_candidate(self, request, *, candidate, dataset):
            case_ids = tuple(case.case_id for case in dataset.cases)
            self.calls.append((candidate.candidate_id, case_ids))
            baseline = ReplayVariantResult(
                variant_id="baseline",
                status="succeeded",
                trajectory=[{"action": {"content": "baseline"}}],
                metrics={"repetition_count": 1, "successful_repetition_count": 1},
            )
            candidate_result = ReplayVariantResult(
                variant_id=candidate.candidate_id,
                status="succeeded",
                trajectory=[{"action": {"content": candidate.candidate_id}}],
                metrics={"repetition_count": 1, "successful_repetition_count": 1},
            )
            members = tuple(
                CandidateReplayMemberResult(
                    case_id=case.case_id,
                    request=replace(request, task_id=case.case_id),
                    baseline=baseline,
                    candidate=candidate_result,
                )
                for case in dataset.cases
            )
            return CandidateReplayResult(
                request=request,
                baseline=baseline,
                candidate=candidate_result,
                member_results=members,
            )

    class EvaluationBackend:
        async def evaluate_variant(self, request):
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={
                    "score": 90.0 if request.candidate is None else 80.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                    "evidence_block_count": 1,
                    "evidence_compacted": False,
                    "evidence_incomplete": False,
                },
                dataset_split=request.dataset_split,
            )

    replay_backend = ReplayBackend()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=PopulationOptimizer(),
        evaluation_backend=EvaluationBackend(),
        replay_enabled=True,
        candidate_replay_backend=replay_backend,
        replay_candidate_limit=2,
        min_eval_cases=0,
    )

    result = await runner.run_explicit_target(
        run_id="run-population-screening",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert replay_backend.calls == [
        ("candidate-1--screening", ("task-a",)),
        ("candidate-1", ("task-a", "task-b")),
    ]
    report = json.loads(
        (tmp_path / ".aworld" / "self_evolve" / "run-population-screening" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["population"]["screening"]["selected_candidate_id"] == "candidate-1"
    assert report["population"]["screening"]["representative_case_id"] == "task-a"


@pytest.mark.asyncio
async def test_population_screening_does_not_offer_explicit_empty_members_for_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input="A"),
            EvalCase(case_id="task-b", input="B"),
        ),
        recipe=DatasetRecipe(
            source={"kind": "screening_empty_members"},
            split_seed="seed",
            splits={"train": ["task-a", "task-b"], "validation": [], "held_out": []},
        ),
    )
    target_ref = SelfEvolveTargetRef(
        target_type="skill",
        target_id="demo",
        path=str(skill_path),
    )
    candidates = tuple(
        CandidateVariant(
            candidate_id=f"candidate-{index}",
            target=target_ref,
            content=f"---\nname: demo\n---\n# Demo\n\nCandidate {index}.\n",
            rationale="screen",
        )
        for index in (1, 2)
    )

    class NoopOptimizer:
        async def propose(self, request):
            return OptimizerResult(candidates=())

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=NoopOptimizer(),
        replay_enabled=True,
        candidate_replay_backend=object(),
    )
    offered_baseline_dirs: list[str | None] = []

    async def empty_member_replay(**kwargs):
        offered_baseline_dirs.append(kwargs["baseline_replay_dir"])
        screening_candidate = kwargs["selected_candidate"]
        screening_dataset = kwargs["dataset"]
        request = CandidateReplayRequest(
            run_id="run-screening-empty",
            task_id=screening_dataset.cases[0].case_id,
            workspace_root=str(tmp_path),
            target=target_ref,
            candidate_id=screening_candidate.candidate_id,
            overlay_skill_root=str(tmp_path / "overlay"),
            task_input=screening_dataset.cases[0].input,
        )
        succeeded = ReplayVariantResult(
            variant_id="baseline",
            status=ReplayExecutionStatus.SUCCEEDED,
            trajectory=[{"action": {"content": "baseline"}}],
        )
        replay_result = _CandidateReplayResult(
            request=request,
            baseline=succeeded,
            candidate=replace(succeeded, variant_id=screening_candidate.candidate_id),
            member_results=(),
        )
        return (
            replay_result,
            None,
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="invalid explicit member result",
            ),
        )

    monkeypatch.setattr(runner, "_replay_selected_candidate", empty_member_replay)

    await runner._screen_candidate_population(
        run_id="run-screening-empty",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        candidates=candidates,
        apply_policy="auto_verified",
    )

    assert offered_baseline_dirs == [None, None]


def test_explicit_empty_members_have_no_baseline_artifact_path(tmp_path: Path) -> None:
    request = CandidateReplayRequest(
        run_id="run-empty-path",
        task_id="task-a",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="candidate",
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input="A",
    )
    succeeded = ReplayVariantResult(
        variant_id="baseline",
        status=ReplayExecutionStatus.SUCCEEDED,
        trajectory=[{"action": {"content": "ok"}}],
    )
    replay_result = _CandidateReplayResult(
        request=request,
        baseline=succeeded,
        candidate=replace(succeeded, variant_id="candidate"),
        member_results=(),
    )

    with pytest.raises(ValueError, match="empty explicit replay members"):
        _baseline_replay_artifact_dir(replay_result)


def test_population_stop_defensively_requires_native_shared_run_event(
    tmp_path: Path,
) -> None:
    request = CandidateReplayRequest(
        run_id="run-source-defense",
        task_id="task-a",
        workspace_root=str(tmp_path),
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo"),
        candidate_id="candidate",
        overlay_skill_root=str(tmp_path / "overlay"),
        task_input="A",
    )
    event = ReplayFailureEvent(
        code="shared_runtime_failed",
        owner=FailureOwner.INFRASTRUCTURE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.SHARED_RUN,
        repairable=False,
    )
    object.__setattr__(event, "source", FailureEventSource.LEGACY_INFERRED)
    failed = ReplayVariantResult(
        variant_id="baseline",
        status=ReplayExecutionStatus.FAILED,
        trajectory=[],
        failure=event,
    )
    replay_result = _CandidateReplayResult(
        request=request,
        baseline=failed,
        candidate=ReplayVariantResult(
            variant_id="candidate",
            status=ReplayExecutionStatus.BLOCKED,
            trajectory=[],
            blocked_by=(event,),
        ),
    )

    assert _shared_replay_failure_blocks_population(replay_result) is False


@pytest.mark.asyncio
async def test_population_screening_preserves_all_candidates_when_baseline_is_inconclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: demo\n---\n# Demo\n\nOld guidance.\n",
        encoding="utf-8",
    )
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input={"content": "Replay task A"}),
            EvalCase(case_id="task-b", input={"content": "Replay task B"}),
        ),
        recipe=DatasetRecipe(
            source={"kind": "trajectory_set", "case_count": 2},
            split_seed="seed",
            splits={"train": ["task-a"], "validation": ["task-b"], "held_out": []},
            trainable_case_ids=("task-a", "task-b"),
        ),
    )
    target_ref = SelfEvolveTargetRef(
        target_type="skill",
        target_id="demo",
        path=str(skill_path),
    )
    candidates = tuple(
        CandidateVariant(
            candidate_id=f"candidate-{index}",
            target=target_ref,
            content=f"---\nname: demo\n---\n# Demo\n\nCandidate {index}.\n",
            rationale=f"candidate {index}",
        )
        for index in (1, 2)
    )

    class NoopOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=())

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=NoopOptimizer(),
        replay_enabled=True,
        candidate_replay_backend=object(),
    )

    class InconclusiveReplay:
        member_results = ()
        baseline = ReplayVariantResult(
            variant_id="baseline",
            status="failed",
            trajectory=[],
            failure={"type": "TimeoutExpired", "reason": "replay timed out"},
        )
        candidate = ReplayVariantResult(
            variant_id="candidate-1--screening",
            status="failed",
            trajectory=[],
            failure={"reason": "baseline_preflight_failed"},
        )

    async def inconclusive_replay(**kwargs):
        return (
            InconclusiveReplay(),
            None,
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate replay did not produce comparable paired outcomes",
            ),
        )

    monkeypatch.setattr(runner, "_replay_selected_candidate", inconclusive_replay)

    screened, report = await runner._screen_candidate_population(
        run_id="run-screening-inconclusive",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        candidates=candidates,
        apply_policy="auto_verified",
    )

    assert screened == candidates
    assert report is not None
    assert report["selected_candidate_id"] is None
    assert report["selected_candidate_ids"] == ["candidate-1", "candidate-2"]
    assert report["attempted_candidate_count"] == 2
    assert "preserved the ranked population" in report["selection_reason"]

    async def repairable_capability_replay(**kwargs):
        return (
            InconclusiveReplay(),
            None,
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="candidate replay did not produce comparable paired outcomes",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "baseline_failure": {
                        "failure_class": "candidate_replay_capability",
                        "repairable": True,
                        "diagnostics": {
                            "task_artifacts": [
                                {
                                    "path": "artifact/workspace/scrape.log",
                                    "tail": "replay endpoint protocol mismatch",
                                }
                            ]
                        },
                    },
                },
            ),
        )

    monkeypatch.setattr(
        runner,
        "_replay_selected_candidate",
        repairable_capability_replay,
    )

    screened, report = await runner._screen_candidate_population(
        run_id="run-screening-repairable-capability",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        candidates=candidates,
        apply_policy="auto_verified",
    )

    assert screened == ()
    assert report is not None
    assert report["attempted_candidate_count"] == 2
    assert "candidate repair" in report["selection_reason"]
    repair_feedback = _candidate_screening_repair_feedback(candidates, report)
    assert len(repair_feedback) == 2
    assert repair_feedback[0].variant_id == "candidate-1"
    assert repair_feedback[0].metrics["failed_gates"] == ["candidate_replay"]
    assert repair_feedback[0].metrics["failure_class"] == "candidate"
    assert repair_feedback[0].metrics["repairable"] is True

    screened, report = await runner._screen_candidate_population(
        run_id="run-single-candidate-capability-screening",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        candidates=candidates[:1],
        apply_policy="auto_verified",
        capability_requirements=(
            ReplayCapabilityRequirement(
                requirement_id="requirement-1",
                kind="local_endpoint",
                identifier="http://127.0.0.1:9222",
                case_ids=("task-a",),
                evidence_refs=("context:1",),
                status="unbound",
            ),
        ),
    )

    assert screened == ()
    assert report is not None
    assert report["generated_candidate_count"] == 1
    assert report["attempted_candidate_count"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case_count", [1, 3])
async def test_repair_conformance_precedes_optional_screening_for_every_cardinality(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_count: int,
) -> None:
    skill_path = tmp_path / "skills" / "generic" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: generic\n---\n# Generic\n", encoding="utf-8")
    case_ids = tuple(f"member-{index}" for index in range(case_count))
    dataset = SelfEvolveDataset(
        cases=tuple(EvalCase(case_id=case_id, input={"task": index}) for index, case_id in enumerate(case_ids)),
        recipe=DatasetRecipe(
            source={"kind": "contract_matrix"},
            split_seed="generic",
            splits={"train": list(case_ids), "validation": [], "held_out": []},
            trainable_case_ids=case_ids,
        ),
    )
    target_ref = SelfEvolveTargetRef(
        target_type="skill", target_id="generic", path=str(skill_path)
    )
    candidates = tuple(
        CandidateVariant(
            candidate_id=f"candidate-{index}",
            target=target_ref,
            content="# Generic\n",
            rationale="generic repair",
        )
        for index in (1, 2)
    )
    contract = RepairConformanceContract(
        focus_candidate_id="parent",
        failure_codes=("generic_failure",),
        interaction_progress=1,
        base_file_fingerprints={"replay/runtime.py": "sha256:base"},
        required_branch_paths=("replay/runtime.py",),
        base_branch_fingerprints={"replay/runtime.py": "sha256:branch"},
    )

    class NoopOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=())

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=NoopOptimizer(),
        replay_enabled=True,
        candidate_replay_backend=object(),
    )
    preflight_calls: list[tuple[str, tuple[str, ...]]] = []

    def source_conformance(candidate, candidate_contract):
        return RepairConformanceResult(
            passed=candidate.candidate_id == "candidate-2",
            code=(
                "repair_source_passed"
                if candidate.candidate_id == "candidate-2"
                else "repair_branch_unchanged"
            ),
            reason="generic source result",
            details={},
        )

    async def runtime_conformance(**kwargs):
        preflight_calls.append(
            (
                kwargs["candidate"].candidate_id,
                tuple(case.case_id for case in kwargs["dataset"].cases),
            )
        )
        return GateResult(
            gate_name="candidate_repair_conformance",
            passed=True,
            reason="passed",
            details={"code": "repair_conformance_passed"},
        )

    async def unexpected_task_screening(**kwargs):
        raise AssertionError("proposal mode must not run optional task screening")

    monkeypatch.setattr(
        runner_module, "evaluate_candidate_source_conformance", source_conformance
    )
    monkeypatch.setattr(
        runner, "_preflight_candidate_repair_conformance", runtime_conformance
    )
    monkeypatch.setattr(runner, "_replay_selected_candidate", unexpected_task_screening)

    screened, report = await runner._screen_candidate_population(
        run_id=f"run-conformance-{case_count}",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        candidates=candidates,
        apply_policy="proposal",
        repair_conformance_contracts={
            candidate.candidate_id: contract for candidate in candidates
        },
    )

    assert screened == (candidates[1],)
    assert preflight_calls == [("candidate-2", case_ids)]
    assert report is not None
    assert report["screening"] is None
    assert report["conformance"]["passed_candidate_ids"] == ["candidate-2"]
    failed_attempt = report["conformance"]["attempts"][0]
    assert failed_attempt["stage"] == "conformance"
    assert failed_attempt["details"]["failure_event"]["owner"] == "candidate"
    repair_feedback = _candidate_screening_repair_feedback(candidates, report)
    assert len(repair_feedback) == 1
    causal_events = repair_feedback[0].metrics["causal_failure_events"]
    assert len(causal_events) == 1
    assert causal_events[0]["code"] == "repair_branch_unchanged"
    assert causal_events[0]["owner"] == "candidate"
    assert causal_events[0]["stage"] == "capability_preflight"


@pytest.mark.asyncio
async def test_repair_conformance_stops_population_only_for_typed_shared_infrastructure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_path = tmp_path / "skills" / "generic" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Generic\n", encoding="utf-8")
    case_ids = ("member-a", "member-b", "member-c")
    dataset = SelfEvolveDataset(
        cases=tuple(EvalCase(case_id=case_id, input=case_id) for case_id in case_ids),
        recipe=DatasetRecipe(
            source={"kind": "contract_matrix"},
            split_seed="generic",
            splits={"train": list(case_ids), "validation": [], "held_out": []},
            trainable_case_ids=case_ids,
        ),
    )
    target_ref = SelfEvolveTargetRef(
        target_type="skill", target_id="generic", path=str(skill_path)
    )
    candidates = tuple(
        CandidateVariant(
            candidate_id=f"candidate-{index}",
            target=target_ref,
            content="# Generic\n",
            rationale="repair",
        )
        for index in (1, 2)
    )
    contract = RepairConformanceContract(
        focus_candidate_id="parent",
        failure_codes=("generic_failure",),
        interaction_progress=1,
        base_file_fingerprints={"runtime.py": "sha256:base"},
        required_branch_paths=("runtime.py",),
        base_branch_fingerprints={"runtime.py": "sha256:branch"},
    )

    class NoopOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=())

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=NoopOptimizer(),
        replay_enabled=True,
        candidate_replay_backend=object(),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        runner_module,
        "evaluate_candidate_source_conformance",
        lambda candidate, candidate_contract: RepairConformanceResult(
            passed=True, code="passed", reason="passed", details={}
        ),
    )

    async def unavailable_infrastructure(**kwargs):
        calls.append(kwargs["candidate"].candidate_id)
        event = ReplayFailureEvent(
            code="conformance_sandbox_unavailable",
            owner=FailureOwner.INFRASTRUCTURE,
            stage=FailureStage.CAPABILITY_PREFLIGHT,
            scope=FailureScope.SHARED_RUN,
            repairable=False,
            summary="sandbox unavailable",
        )
        return GateResult(
            gate_name="candidate_repair_conformance",
            passed=False,
            reason="shared infrastructure unavailable",
            details={
                "failure_class": "infrastructure",
                "repairable": False,
                "failure_event": event.to_dict(),
            },
        )

    monkeypatch.setattr(
        runner, "_preflight_candidate_repair_conformance", unavailable_infrastructure
    )

    screened, report = await runner._screen_candidate_population(
        run_id="run-shared-conformance-infrastructure",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        candidates=candidates,
        apply_policy="auto_verified",
        repair_conformance_contracts={
            candidate.candidate_id: contract for candidate in candidates
        },
    )

    assert screened == ()
    assert calls == ["candidate-1"]
    assert report is not None
    assert report["conformance"]["stopped_by_shared_infrastructure"] is True


@pytest.mark.asyncio
async def test_missing_candidate_capability_rejects_candidate_but_continues_population(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_path = tmp_path / "skills" / "generic" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Generic\n", encoding="utf-8")
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="member-1", input="task"),),
        recipe=DatasetRecipe(
            source={"kind": "contract_matrix"},
            split_seed="generic",
            splits={"train": ["member-1"], "validation": [], "held_out": []},
            trainable_case_ids=("member-1",),
        ),
    )
    target_ref = SelfEvolveTargetRef(
        target_type="skill", target_id="generic", path=str(skill_path)
    )
    candidates = tuple(
        CandidateVariant(
            candidate_id=f"candidate-{index}",
            target=target_ref,
            content="# Generic\n",
            rationale="generic repair",
        )
        for index in (1, 2)
    )
    contract = RepairConformanceContract(
        focus_candidate_id="candidate-parent",
        failure_codes=("generic_failure",),
        interaction_progress=1,
        base_file_fingerprints={"runtime.py": "sha256:base"},
        required_branch_paths=("runtime.py",),
        base_branch_fingerprints={"runtime.py": "sha256:branch"},
    )

    class NoopOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=())

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=NoopOptimizer(),
        replay_enabled=True,
        candidate_replay_backend=object(),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        runner_module,
        "evaluate_candidate_source_conformance",
        lambda candidate, candidate_contract: RepairConformanceResult(
            passed=True, code="passed", reason="passed", details={}
        ),
    )

    async def candidate_preflight(**kwargs):
        candidate_id = kwargs["candidate"].candidate_id
        calls.append(candidate_id)
        if candidate_id == "candidate-2":
            return GateResult(
                gate_name="candidate_repair_conformance",
                passed=True,
                reason="passed",
                details={},
            )
        event = ReplayFailureEvent(
            code="candidate_replay_capability_missing",
            owner=FailureOwner.CANDIDATE,
            stage=FailureStage.CAPABILITY_COMPILE,
            scope=FailureScope.CANDIDATE,
            repairable=True,
            source=FailureEventSource.NATIVE,
        )
        return GateResult(
            gate_name="candidate_repair_conformance",
            passed=False,
            reason="candidate capability missing",
            details={
                "failure_class": "candidate",
                "failure_event": event.to_dict(),
                "causal_failure_events": [event.to_dict()],
            },
        )

    monkeypatch.setattr(
        runner, "_preflight_candidate_repair_conformance", candidate_preflight
    )

    screened, report = await runner._screen_candidate_population(
        run_id="run-missing-candidate-capability",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        candidates=candidates,
        apply_policy="proposal",
        repair_conformance_contracts={
            candidate.candidate_id: contract for candidate in candidates
        },
    )

    assert calls == ["candidate-1", "candidate-2"]
    assert screened == (candidates[1],)
    assert report is not None
    assert report["conformance"]["stopped_by_shared_infrastructure"] is False


@pytest.mark.asyncio
async def test_conformance_executes_each_projected_group_once_and_attributes_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_path = tmp_path / "skills" / "generic" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("# Generic\n", encoding="utf-8")
    case_ids = ("member-a", "member-b", "member-c")
    dataset = SelfEvolveDataset(
        cases=tuple(EvalCase(case_id=item, input=item) for item in case_ids),
        recipe=DatasetRecipe(
            source={"kind": "contract_matrix"},
            split_seed="generic",
            splits={"train": list(case_ids), "validation": [], "held_out": []},
            trainable_case_ids=case_ids,
        ),
    )
    requirements = (
        ReplayCapabilityRequirement(
            requirement_id="requirement-a",
            kind="local_endpoint",
            identifier="endpoint-a",
            case_ids=("member-a", "member-b"),
            evidence_refs=("evidence-a",),
            status="runtime_required",
        ),
        ReplayCapabilityRequirement(
            requirement_id="requirement-b",
            kind="local_endpoint",
            identifier="endpoint-b",
            case_ids=case_ids,
            evidence_refs=("evidence-b",),
            status="runtime_required",
        ),
    )
    services = tuple(
        ReplayServiceSpec(
            service_id=f"service-{suffix}",
            requirement_id=f"requirement-{suffix}",
            transport="skill_runtime",
            response_fixture=f"fixture-{suffix}.json",
            runtime_entrypoint="runtime.py",
            readiness=ReplayReadinessProbe(kind="http", timeout_seconds=1),
            protocol_probes=(
                ReplayProtocolProbe(
                    kind="http",
                    timeout_seconds=1,
                    path=f"/query-{suffix}",
                    request_text=json.dumps({"operation": f"records.{suffix}"}),
                ),
            ),
        )
        for suffix in ("a", "b")
    )
    capability = FrozenReplayCapability(
        capability_id="generic-capability",
        capability_package_fingerprint="sha256:package",
        request_fingerprint="sha256:request",
        frozen_root=str(tmp_path / "frozen"),
        handled_requirements=("requirement-a", "requirement-b"),
        unhandled_requirements=(),
        evidence_refs={},
        fixture_evidence_refs={},
        fixtures=(),
        runtime_files=(),
        endpoint_replacements={},
        services=services,
        deterministic=True,
        fingerprint="sha256:frozen",
        ready=True,
    )
    candidate = CandidateVariant(
        candidate_id="candidate-repair",
        target=SelfEvolveTargetRef(
            target_type="skill", target_id="generic", path=str(skill_path)
        ),
        content="# Generic\n",
        rationale="repair",
    )
    contract = RepairConformanceContract(
        focus_candidate_id="candidate-parent",
        failure_codes=("generic_failure",),
        interaction_progress=1,
        base_file_fingerprints={"runtime.py": "sha256:base"},
        required_branch_paths=("runtime.py",),
        base_branch_fingerprints={"runtime.py": "sha256:branch"},
        exact_probe=ExactRepairProbe(
            kind="http",
            path="/query-b",
            expected_response="PRIVATE_RAW_RECORDED_FIXTURE_VALUE",
        ),
    )

    class NoopOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=())

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=NoopOptimizer(),
        replay_enabled=True,
        candidate_replay_backend=object(),
    )
    monkeypatch.setattr(
        runner_module,
        "create_candidate_skill_overlay",
        lambda **kwargs: SimpleNamespace(candidate_skill_path=skill_path),
    )
    monkeypatch.setattr(
        runner,
        "_prepare_replay_adaptation",
        lambda **kwargs: (
            SimpleNamespace(replay_capability=capability),
            GateResult("replay_adaptation", True, "passed"),
        ),
    )
    monkeypatch.setattr(
        runner_module,
        "evaluate_compiled_probe_conformance",
        lambda *args, **kwargs: RepairConformanceResult(
            passed=True, code="passed", reason="passed", details={}
        ),
    )
    monkeypatch.setattr(
        runner_module,
        "frozen_replay_fixture_shape_fingerprints",
        lambda frozen: {
            "fixture-a.json": "sha256:shape-a",
            "fixture-b.json": "sha256:shape-b",
        },
    )
    monkeypatch.setattr(
        runner_module, "replay_capability_fixture_leaf_values", lambda frozen: {}
    )
    monkeypatch.setattr(
        runner_module,
        "replay_capability_fixture_response_leaf_values",
        lambda frozen: {},
    )
    calls: list[str] = []

    async def projected_preflight(frozen, **kwargs):
        assert len(frozen.services) == 1
        service_id = frozen.services[0].service_id
        calls.append(service_id)
        if service_id == "service-b":
            raise RuntimeError("generic group failure")

    monkeypatch.setattr(
        runner_module,
        "preflight_frozen_replay_capability",
        projected_preflight,
    )

    gate = await runner._preflight_candidate_repair_conformance(
        run_id="run-projected-groups",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        candidate=candidate,
        contract=contract,
        capability_requirements=requirements,
    )

    assert sorted(calls) == ["service-a", "service-b"]
    assert len(calls) == 2
    assert gate.passed is False
    events = gate.details["causal_failure_events"]
    assert len(events) == 1
    assert events[0]["affected_member_count"] == 3
    assert len(events[0]["affected_case_identity_digests"]) == 3
    assert events[0]["capability_identity_digest"]
    assert events[0]["requirement_identity_digest"]
    assert events[0]["contract_identity_digest"]
    assert "capability_id" not in events[0]
    assert "requirement_id" not in events[0]
    assert "contract_fingerprint" not in events[0]
    assert "PRIVATE_RAW_RECORDED_FIXTURE_VALUE" not in json.dumps(
        gate.details, sort_keys=True
    )
    serialized_gate = json.dumps(gate.details, sort_keys=True)
    assert "generic-capability" not in serialized_gate
    assert "requirement-a" not in serialized_gate
    assert "requirement-b" not in serialized_gate
    persisted = _candidate_validation_report_for_persistence(
        {"conformance": {"attempts": [{"details": gate.details}]}}
    )
    assert "PRIVATE_RAW_RECORDED_FIXTURE_VALUE" not in json.dumps(
        persisted, sort_keys=True
    )


def test_persisted_conformance_report_hashes_payload_bearing_assertions() -> None:
    persisted = _candidate_validation_report_for_persistence(
        {
            "attempts": [
                {
                    "details": {
                        "repair_conformance": {
                            "exact_probe": {
                                "kind": "http",
                                "path": "/query",
                                "expected_response": "private-recorded-value",
                            }
                        },
                        "declared_response_contains": ["private-recorded-value"],
                    }
                }
            ]
        }
    )
    encoded = json.dumps(persisted, sort_keys=True)

    assert "private-recorded-value" not in encoded
    assert "expected_response_fingerprint" in encoded
    assert "declared_response_contains_fingerprint" in encoded


def test_repair_conformance_gate_never_exposes_private_assertion_values() -> None:
    secret = "PRIVATE_RAW_RECORDED_FIXTURE_VALUE"
    contract = RepairConformanceContract(
        focus_candidate_id="candidate-parent",
        failure_codes=("exact_repair_probe_missing",),
        interaction_progress=1,
        base_file_fingerprints={"runtime.py": "sha256:base"},
        required_branch_paths=("runtime.py",),
        base_branch_fingerprints={"runtime.py": "sha256:branch"},
        exact_probe=ExactRepairProbe(
            kind="http",
            path="/query",
            expected_response=secret,
        ),
    )

    gate = _repair_conformance_gate(
        RepairConformanceResult(
            passed=False,
            code="exact_repair_probe_missing",
            reason="probe missing",
            details={
                "expected_preview": secret,
                "previous_expected_preview": secret,
            },
        ),
        contract=contract,
    )
    encoded = json.dumps(gate.details, sort_keys=True)
    feedback_encoded = json.dumps(
        runner_module._typed_gate_feedback_metrics((gate,)),
        sort_keys=True,
    )

    assert secret not in encoded
    assert secret not in feedback_encoded
    assert "expected_preview_fingerprint" in encoded
    assert "previous_expected_preview_fingerprint" in encoded
    assert gate.details["repair_conformance"] == contract.to_public_dict()


def test_public_projection_recursively_seals_misplaced_private_contracts() -> None:
    secret = "PRIVATE_RECURSIVE_CONTRACT_VALUE"
    contract = RepairConformanceContract(
        focus_candidate_id="candidate-parent",
        failure_codes=("failure",),
        interaction_progress=1,
        base_file_fingerprints={"runtime.py": "sha256:base"},
        required_branch_paths=("runtime.py",),
        base_branch_fingerprints={},
        exact_probe=ExactRepairProbe(
            kind="http",
            path="/query",
            expected_response=secret,
        ),
    )
    misplaced: object = contract
    for index in range(12):
        misplaced = {f"level_{index}": misplaced}
    projected = _candidate_validation_report_for_persistence(
        {
            "typed_contract": {"nested": contract},
            "over_depth_budget": misplaced,
            "raw_contract_mapping": contract.to_dict(),
        }
    )
    encoded = json.dumps(projected, sort_keys=True)
    repeated = _candidate_validation_report_for_persistence(
        {
            "typed_contract": {"nested": contract},
            "over_depth_budget": misplaced,
            "raw_contract_mapping": contract.to_dict(),
        }
    )

    assert secret not in encoded
    assert projected == repeated
    assert "expected_response_fingerprint" in encoded
    assert "bounded_public_summary" in encoded
    assert "fingerprint" in encoded


def test_report_run_and_harness_persistence_boundaries_do_not_leak_contracts(
    tmp_path: Path,
) -> None:
    secret = "PRIVATE_PERSISTENCE_BOUNDARY_VALUE"
    contract = RepairConformanceContract(
        focus_candidate_id="candidate-parent",
        failure_codes=("failure",),
        interaction_progress=1,
        base_file_fingerprints={"runtime.py": "sha256:base"},
        required_branch_paths=("runtime.py",),
        base_branch_fingerprints={},
        exact_probe=ExactRepairProbe(
            kind="http",
            path="/query",
            expected_response=secret,
        ),
    )
    legacy_reason = (
        "protocol probe response mismatch: kind=http path=/query "
        f"expected_preview={secret} response_bytes=10 "
        f"response_preview={secret}"
    )
    store = FilesystemSelfEvolveStore(tmp_path)
    report = {f"static_{index}": index for index in range(80)}
    report.update(
        {
            "optimizer_diagnostics": {"nested": {"contract": contract}},
            "gate_results": [
                {
                    "gate_name": "candidate_replay",
                    "passed": False,
                    "reason": legacy_reason,
                    "details": {"contract": contract},
                }
            ],
            "replay": {
                "candidate": {
                    "status": "failed",
                    "failure": {"reason": legacy_reason},
                    "metrics": {"raw_response": secret},
                },
                "members": [
                    {
                        "case_id": "case-1",
                        "candidate_failure": {"reason": legacy_reason},
                    }
                ],
            },
        }
    )
    report_path = store.write_report("run-public-boundary", report)
    store.create_run(
        SelfEvolveRun(
            run_id="run-public-boundary",
            target=SelfEvolveTargetRef(target_type="skill", target_id="generic"),
            status=SelfEvolveRunStatus.REJECTED,
            gate_results=(
                GateResult(
                    gate_name="candidate_replay",
                    passed=False,
                    reason=legacy_reason,
                    details={"contract": contract},
                ),
            ),
        )
    )
    diagnostics_path = store.write_harness_diagnostics(
        "run-public-boundary",
        ({"reason": legacy_reason, "contract": contract},),
    )

    persisted = "\n".join(
        (
            report_path.read_text(encoding="utf-8"),
            store.run_path("run-public-boundary")
            .joinpath("run.json")
            .read_text(encoding="utf-8"),
            diagnostics_path.read_text(encoding="utf-8"),
        )
    )
    persisted_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert secret not in persisted
    assert "expected_response_fingerprint" in persisted
    assert all(f"static_{index}" in persisted_report for index in range(80))


def test_population_report_separates_validation_stages_from_authoritative_replay() -> None:
    candidates = [
        CandidateVariant(
            candidate_id=f"candidate-{index}",
            target=SelfEvolveTargetRef(target_type="skill", target_id="generic"),
            content="# Generic\n",
            rationale="generic",
        )
        for index in (1, 2, 3)
    ]
    report = _population_report(
        all_candidates=candidates,
        iteration_reports=[
            {"candidate_id": "candidate-1", "status": "screening_rejected"},
            {"candidate_id": "candidate-2", "status": "screening_rejected"},
            {
                "candidate_id": "candidate-3",
                "status": "rejected",
                "lifecycle_stage": "authoritative_replay",
            },
        ],
        replay_candidate_limit=3,
        screening_reports=[
            {
                "conformance": {
                    "attempts": [
                        {"candidate_id": "candidate-1", "passed": False},
                        {"candidate_id": "candidate-2", "passed": True},
                    ]
                },
                "screening": {
                    "attempts": [
                        {"candidate_id": "candidate-2", "passed": False}
                    ]
                },
            }
        ],
    )

    assert report is not None
    assert report["replayed_candidate_ids"] == ["candidate-3"]
    assert report["lifecycle"]["generated"]["candidate_count"] == 3
    assert report["lifecycle"]["conformance"] == {
        "attempted_candidate_count": 2,
        "rejected_candidate_count": 1,
        "attempted_candidate_ids": ["candidate-1", "candidate-2"],
        "rejected_candidate_ids": ["candidate-1"],
    }
    assert report["lifecycle"]["screening"]["rejected_candidate_ids"] == [
        "candidate-2"
    ]
    assert report["lifecycle"]["authoritative_replay"][
        "attempted_candidate_ids"
    ] == ["candidate-3"]


@pytest.mark.asyncio
async def test_population_screening_rejects_unchanged_repair_branch_before_rollout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: demo\n---\n# Demo\n\nOld guidance.\n",
        encoding="utf-8",
    )
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input={"content": "Replay task A"}),
            EvalCase(case_id="task-b", input={"content": "Replay task B"}),
        ),
        recipe=DatasetRecipe(
            source={"kind": "trajectory_set", "case_count": 1},
            split_seed="seed",
            splits={
                "train": ["task-a"],
                "validation": ["task-b"],
                "held_out": [],
            },
            trainable_case_ids=("task-a", "task-b"),
        ),
    )
    candidate = CandidateVariant(
        candidate_id="candidate-rationale-only",
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="demo",
            path=str(skill_path),
        ),
        content="---\nname: demo\n---\n# Demo\n\nClaimed repair.\n",
        rationale="The failed branch is fixed.",
        files=(
            CandidateFileDelta(
                path="replay/compiler.py",
                content="def compile_request():\n    return 'unrelated change'\n",
            ),
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def respond():\n    return {}\n",
            ),
        ),
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": {
                "candidate_id": "candidate-failed",
                "files": [
                    {
                        "path": "replay/capability.json",
                        "content": json.dumps(
                            {
                                "schema_version": "aworld.skill.replay_capability.v1",
                                "entrypoint": "replay/compiler.py",
                                "runtime_files": ["replay/runtime.py"],
                            }
                        ),
                    },
                    {
                        "path": "replay/compiler.py",
                        "content": "def compile_request():\n    return None\n",
                    },
                    {
                        "path": "replay/runtime.py",
                        "content": "def respond():\n    return {}\n",
                    },
                ],
            },
            "candidate_validation_diagnostics": [
                {
                    "code": "verify_declared_protocol_probe_branch",
                    "stage": "replay_capability",
                    "probe_kind": "websocket",
                    "probe_path": "/session",
                    "expected_preview": "recorded_value",
                }
            ],
        }
    )
    assert contract is not None

    class NoopOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=())

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=NoopOptimizer(),
        replay_enabled=True,
        candidate_replay_backend=object(),
    )
    rollout_calls = 0

    async def unexpected_rollout(**kwargs):
        nonlocal rollout_calls
        rollout_calls += 1
        raise AssertionError("paired rollout must not start")

    monkeypatch.setattr(runner, "_replay_selected_candidate", unexpected_rollout)

    screened, report = await runner._screen_candidate_population(
        run_id="run-screening-conformance",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        candidates=(candidate,),
        apply_policy="auto_verified",
        repair_conformance_contracts={candidate.candidate_id: contract},
    )

    assert rollout_calls == 0
    assert screened == ()
    assert report is not None
    assert report["attempts"][0]["details"]["code"] == (
        "repair_branch_unchanged"
    )
    assert report["attempts"][0]["details"]["repair_conformance"] == (
        contract.to_public_dict()
    )
    feedback = _candidate_screening_repair_feedback((candidate,), report)
    assert len(feedback) == 1
    assert feedback[0].metrics["failure_class"] == "candidate"
    inherited_contract = compile_repair_conformance_contract(
        feedback[0].metrics
    )
    assert inherited_contract is not None
    assert inherited_contract.exact_probe is None


@pytest.mark.asyncio
async def test_population_screening_rollout_failure_preserves_passed_conformance_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(case_id="task-a", input={"content": "Replay task A"}),
            EvalCase(case_id="task-b", input={"content": "Replay task B"}),
        ),
        recipe=DatasetRecipe(
            source={"kind": "trajectory_set", "case_count": 1},
            split_seed="seed",
            splits={
                "train": ["task-a"],
                "validation": ["task-b"],
                "held_out": [],
            },
            trainable_case_ids=("task-a", "task-b"),
        ),
    )
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": {
                "candidate_id": "candidate-failed",
                "files": [
                    {
                        "path": "replay/capability.json",
                        "content": json.dumps(
                            {
                                "schema_version": "aworld.skill.replay_capability.v1",
                                "entrypoint": "replay/compiler.py",
                                "runtime_files": ["replay/runtime.py"],
                            }
                        ),
                    },
                    {
                        "path": "replay/compiler.py",
                        "content": "def compile_request():\n    return None\n",
                    },
                    {
                        "path": "replay/runtime.py",
                        "content": "def respond():\n    return {}\n",
                    },
                ],
            },
            "candidate_validation_diagnostics": [
                {
                    "code": "implement_observed_endpoint_interactions",
                    "observed_request_operations": ["records.query"],
                }
            ],
        }
    )
    assert contract is not None
    candidate = CandidateVariant(
        candidate_id="candidate-rollout-timeout",
        target=SelfEvolveTargetRef(
            target_type="skill", target_id="demo", path=str(skill_path)
        ),
        content="---\nname: demo\n---\n# Demo\n",
        rationale="Repair task plane.",
        files=(
            CandidateFileDelta(
                path="replay/compiler.py",
                content="def compile_request():\n    return None\n",
            ),
            CandidateFileDelta(
                path="replay/runtime.py",
                content="def respond():\n    return {'recorded': True}\n",
            ),
        ),
    )

    class NoopOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=())

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=NoopOptimizer(),
        replay_enabled=True,
        candidate_replay_backend=object(),
    )
    preflight_case_ids: tuple[str, ...] = ()
    rollout_case_ids: tuple[str, ...] = ()

    async def passed_preflight(**kwargs):
        nonlocal preflight_case_ids
        preflight_case_ids = tuple(
            case.case_id for case in kwargs["dataset"].cases
        )
        return GateResult(
            gate_name="candidate_repair_conformance",
            passed=True,
            reason="passed",
            details={},
        )

    async def failed_rollout(**kwargs):
        nonlocal rollout_case_ids
        rollout_case_ids = tuple(
            case.case_id for case in kwargs["dataset"].cases
        )
        return (
            None,
            None,
            GateResult(
                gate_name="candidate_replay",
                passed=False,
                reason="replay timed out",
                details={
                    "failure_class": "candidate",
                    "repairable": True,
                    "candidate_failure": {
                        "reason": "replay timed out",
                        "outcome": "candidate_failure",
                    },
                },
            ),
        )

    monkeypatch.setattr(
        runner, "_preflight_candidate_repair_conformance", passed_preflight
    )
    monkeypatch.setattr(runner, "_replay_selected_candidate", failed_rollout)

    screened, report = await runner._screen_candidate_population(
        run_id="run-screening-rollout-contract",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        candidates=(candidate,),
        apply_policy="auto_verified",
        repair_conformance_contracts={candidate.candidate_id: contract},
    )

    assert screened == ()
    assert report is not None
    assert preflight_case_ids == ("task-a", "task-b")
    assert rollout_case_ids == ("task-a",)
    details = report["attempts"][0]["details"]
    assert details["repair_conformance"] == contract.to_public_dict()
    feedback = _candidate_screening_repair_feedback((candidate,), report)
    inherited = compile_repair_conformance_contract(feedback[0].metrics)
    assert inherited is not None
    # Persisted/public feedback is useful repair context but is deliberately
    # not an executable contract. Exact execution state travels only through
    # OptimizerResult.private_context.
    assert inherited.required_fixture_probe_operations == ()


def test_repair_conformance_failure_preserves_fixture_shape_and_trace_tail(
    tmp_path: Path,
) -> None:
    frozen_root = tmp_path / "frozen"
    fixture_path = frozen_root / "fixtures" / "fixtures" / "recorded.json"
    fixture_path.parent.mkdir(parents=True)
    fixture_path.write_text('[{"records":[{"value":"captured"}]}]', encoding="utf-8")
    artifact_root = tmp_path / "conformance"
    trace_path = artifact_root / "replay_services" / "service-1" / "protocol_trace.log"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text(
        '{"direction":"in","sequence":5,"kind":"request",'
        '"correlation":{"operation":"records.query"}}\n'
        '{"direction":"out","sequence":6,"kind":"error",'
        '"correlation":{"error":"list root has no attribute get"}}\n',
        encoding="utf-8",
    )
    capability = SimpleNamespace(
        frozen_root=str(frozen_root),
        services=(
            SimpleNamespace(
                service_id="service-1",
                response_fixture="fixtures/recorded.json",
            ),
        ),
    )

    diagnostics = _repair_conformance_failure_diagnostics(
        capability,
        artifact_dir=artifact_root,
    )

    assert diagnostics["replay_fixture_summaries"][0]["json_root_type"] == "array"
    assert diagnostics["replay_service_protocol_traces"][0]["tail"].endswith(
        '"correlation":{"error":"list root has no attribute get"}}'
    )


def test_exact_repair_probe_requires_correlated_result_validation() -> None:
    contract = compile_repair_conformance_contract(
        {
            "repair_candidate_package": {
                "candidate_id": "candidate-failed",
                "files": [
                    {
                        "path": "replay/runtime.py",
                        "content": "def handle():\n    return {}\n",
                    }
                ],
            },
            "candidate_validation_diagnostics": [
                {
                    "code": "verify_declared_protocol_probe_branch",
                    "probe_kind": "websocket",
                    "probe_path": "/session",
                    "expected_preview": "recorded_value",
                    "observed_request_operations": ["Target.getTargets"],
                }
            ],
        }
    )
    assert contract is not None
    contract = replace(
        contract,
        exact_probe=ExactRepairProbe(
            kind="websocket",
            path="/session",
            expected_response="recorded_value",
        ),
    )
    assert contract.requires_fixture_derived_probe is False

    assert _repair_conformance_required_nonempty_operations(contract) == (
        "Target.getTargets",
    )


@pytest.mark.asyncio
async def test_runner_does_not_reuse_legacy_member_baseline_without_provenance(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Replay this task."}},
            "action": {"content": "Baseline was already strong."},
            "reward": {"status": "ok"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="task-prior-baseline",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="task-prior-baseline",
    )
    dataset = SelfEvolveDataset(
        cases=(
            dataset.cases[0],
            EvalCase(case_id="grouped-extra-case", input={"content": "Extra grouped task."}),
        ),
        recipe=dataset.recipe,
    )
    prior_replay_dir = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "old-run"
        / "replay"
        / "old-candidate"
    )
    (prior_replay_dir / "members").mkdir(parents=True, exist_ok=True)
    root_request_payload = {
        "run_id": "old-run",
        "task_id": "task-prior-baseline",
        "workspace_root": str(tmp_path),
        "target": {
            "target_type": "skill",
            "target_id": "demo",
            "path": str(skill_path),
        },
        "candidate_id": "old-candidate",
        "overlay_skill_root": str(tmp_path / "old-overlay"),
        "task_input": {"content": "Replay this task."},
        "baseline_skill_root": str(tmp_path / "skills"),
        "baseline_repetitions": 2,
        "candidate_repetitions": 3,
    }
    (prior_replay_dir / "request.json").write_text(
        json.dumps(root_request_payload), encoding="utf-8"
    )
    for case_id in ("task-prior-baseline", "grouped-extra-case"):
        member_dir = prior_replay_dir / "members" / _member_artifact_name(case_id)
        (member_dir / "baseline" / "1").mkdir(parents=True)
        (member_dir / "old-candidate").mkdir(parents=True)
        member_request_payload = {**root_request_payload, "task_id": case_id}
        (member_dir / "request.json").write_text(
            json.dumps(member_request_payload), encoding="utf-8"
        )
        for repetition in ("1", "2"):
            rep_dir = member_dir / "baseline" / repetition
            rep_dir.mkdir(parents=True, exist_ok=True)
            (rep_dir / "trajectory.json").write_text(
                json.dumps([{"action": {"content": f"{case_id} baseline {repetition}"}}]),
                encoding="utf-8",
            )
            (rep_dir / "metrics.json").write_text(json.dumps({"score": 1.0}), encoding="utf-8")
        (member_dir / "old-candidate" / "trajectory.json").write_text(
            json.dumps([{"action": {"content": f"{case_id} old candidate"}}]),
            encoding="utf-8",
        )

    class OneCandidateOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(
                candidates=(
                    CandidateVariant(
                        candidate_id="candidate-new",
                        target=request.target,
                        content="---\nname: demo\n---\n# Demo\n\nSmall delta.\n",
                        rationale="new",
                        target_fingerprint=request.target_fingerprint,
                    ),
                )
            )

    class ReplayBackend:
        def __init__(self) -> None:
            self.baseline_replay_dirs: list[str | None] = []

        async def replay_candidate(self, request, *, candidate, dataset):
            self.baseline_replay_dirs.append(getattr(request, "baseline_replay_dir", None))
            baseline = ReplayVariantResult(
                variant_id="baseline",
                status="succeeded",
                trajectory=[{"action": {"content": "baseline"}}],
                metrics={"repetition_count": 2, "successful_repetition_count": 2},
            )
            candidate_result = ReplayVariantResult(
                variant_id=candidate.candidate_id,
                status="succeeded",
                trajectory=[{"action": {"content": candidate.candidate_id}}],
                metrics={"repetition_count": 3, "successful_repetition_count": 3},
            )
            return CandidateReplayResult(
                request=request,
                baseline=baseline,
                candidate=candidate_result,
                member_results=tuple(
                    CandidateReplayMemberResult(
                        case_id=case.case_id,
                        request=replace(
                            request,
                            task_id=case.case_id,
                            task_input=case.input,
                        ),
                        baseline=baseline,
                        candidate=candidate_result,
                    )
                    for case in dataset.cases
                ),
            )

    class EvaluationBackend:
        async def evaluate_variant(self, request):
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={
                    "score": 92.0,
                    "A1_groundedness": 5.0,
                    "A2_completeness": 5.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                    "evidence_block_count": 1,
                    "evidence_compacted": False,
                    "evidence_incomplete": False,
                },
                dataset_split=request.dataset_split,
            )

    replay_backend = ReplayBackend()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=OneCandidateOptimizer(),
        evaluation_backend=EvaluationBackend(),
        post_apply_evaluator=lambda candidate: EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True},
            dataset_split="post_apply",
        ),
        min_eval_cases=0,
        replay_enabled=True,
        candidate_replay_backend=replay_backend,
    )

    result = await runner.run_explicit_target(
        run_id="new-run",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "succeeded"
    assert replay_backend.baseline_replay_dirs == [None]


@pytest.mark.asyncio
async def test_runner_filters_quality_rejection_but_retries_replay_only_candidate(
    tmp_path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    historical_dir = tmp_path / ".aworld" / "self_evolve" / "old-run"
    historical_dir.mkdir(parents=True)
    (historical_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": "old-run",
                "target": {
                    "target_type": "skill",
                    "target_id": "demo",
                    "path": str(skill_path),
                },
                "status": "rejected",
                "iterations": [
                    {
                        "iteration": 1,
                        "candidate_id": "candidate-dup-1",
                        "status": "rejected",
                        "failed_gates": ["score_improvement"],
                    },
                    {
                        "iteration": 2,
                        "candidate_id": "candidate-dup-2",
                        "status": "rejected",
                        "failed_gates": ["candidate_replay"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Improve candidate generation."}},
            "action": {"content": "Prior candidates repeated."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="duplicate-filter-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="duplicate-filter-task",
    )
    duplicate_one = CandidateVariant(
        candidate_id="candidate-dup-1",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nDuplicate one.\n",
        rationale="duplicate",
        target_fingerprint="fingerprint",
    )
    duplicate_two = CandidateVariant(
        candidate_id="candidate-dup-2",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nDuplicate two.\n",
        rationale="duplicate",
        target_fingerprint="fingerprint",
    )
    fresh_candidate = CandidateVariant(
        candidate_id="candidate-fresh",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nFresh candidate.\n",
        rationale="fresh",
        target_fingerprint="fingerprint",
    )

    class PopulationOptimizer:
        def __init__(self) -> None:
            self.requests: list[OptimizerRequest] = []

        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            self.requests.append(request)
            return OptimizerResult(
                candidates=(duplicate_one, duplicate_two, fresh_candidate)
            )

    class ReplayBackend:
        def __init__(self) -> None:
            self.candidate_ids: list[str] = []

        async def replay_candidate(self, request, *, candidate, dataset):
            self.candidate_ids.append(candidate.candidate_id)
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "baseline"}}],
                    metrics={
                        "repetition_count": request.baseline_repetitions,
                        "successful_repetition_count": request.baseline_repetitions,
                    },
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[{"action": {"content": "candidate"}}],
                    metrics={
                        "repetition_count": request.candidate_repetitions,
                        "successful_repetition_count": request.candidate_repetitions,
                    },
                ),
            )

    class EvaluationBackend:
        async def evaluate_variant(self, request):
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={
                    "score": 0.9 if request.candidate is not None else 0.2,
                    "A1_groundedness": 5.0,
                    "A2_completeness": 5.0,
                    "A3_relevance": 5.0,
                    "A4_readability": 5.0,
                    "B1_tool_use": 5.0,
                    "B2_efficiency": 5.0,
                    "B3_compliance": 5.0,
                    "B4_robustness": 5.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "command_pass_rate": 1.0,
                    "global_regression_passed": True,
                    "has_evidence": 1.0,
                    "evidence_block_count": 1,
                    "evidence_bundle_valid": True,
                    "evidence_bundle_entry_count": 1,
                    "evidence_manifest_entry_count": 1,
                    "evidence_manifest_invalid_entry_count": 0,
                    "evidence_strategy_passed": True,
                    "evidence_compacted": False,
                    "evidence_incomplete": False,
                },
                dataset_split=request.dataset_split,
            )

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True},
            dataset_split="post_apply",
        )

    optimizer = PopulationOptimizer()
    replay_backend = ReplayBackend()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=optimizer,
        evaluation_backend=EvaluationBackend(),
        post_apply_evaluator=post_apply,
        min_eval_cases=0,
        replay_enabled=True,
        candidate_replay_backend=replay_backend,
        replay_candidate_limit=2,
        baseline_replay_repetitions=2,
        candidate_replay_repetitions=3,
    )

    result = await runner.run_explicit_target(
        run_id="run-filter-duplicates",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "succeeded"
    assert optimizer.requests[0].max_candidates == 2
    assert replay_backend.candidate_ids == ["candidate-dup-2"]
    report = json.loads(
        (tmp_path / ".aworld" / "self_evolve" / "run-filter-duplicates" / "report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["selected_candidate_id"] == "candidate-dup-2"
    assert report["optimizer_diagnostics"]["filtered_known_duplicate_candidates"] == 1
    assert report["iterations"][0]["candidate_id"] == "candidate-dup-2"


@pytest.mark.asyncio
async def test_runner_filters_prior_semantic_lesson_duplicate_candidates_before_replay(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    historical_dir = tmp_path / ".aworld" / "self_evolve" / "old-semantic-run"
    lineage_dir = historical_dir / "optimizer_lineage"
    lineage_dir.mkdir(parents=True)
    (historical_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": "old-semantic-run",
                "target": {
                    "target_type": "skill",
                    "target_id": "demo",
                    "path": str(skill_path),
                },
                "status": "rejected",
                "iterations": [
                    {
                        "iteration": 1,
                        "candidate_id": "candidate-old-semantic",
                        "status": "rejected",
                        "failed_gates": ["score_improvement"],
                    }
                ],
                "optimizer_lineage": {
                    "count": 1,
                    "paths": [str(lineage_dir / "candidate-old-semantic.json")],
                },
            }
        ),
        encoding="utf-8",
    )
    (lineage_dir / "candidate-old-semantic.json").write_text(
        json.dumps(
            {
                "candidate_id": "candidate-old-semantic",
                "optimizer_name": "test",
                "optimizer_version": "1",
                "semantic_fingerprint": "semantic-same",
                "lesson_set_fingerprint": "lesson-set-same",
            }
        ),
        encoding="utf-8",
    )
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Improve candidate generation."}},
            "action": {"content": "Prior candidates repeated semantically."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="semantic-filter-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="semantic-filter-task",
    )
    semantic_duplicate = CandidateVariant(
        candidate_id="candidate-new-semantic",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nSame semantic weak variant with different words.\n",
        rationale="semantic duplicate",
        target_fingerprint="fingerprint",
    )
    fresh_candidate = CandidateVariant(
        candidate_id="candidate-fresh-semantic",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nFresh semantic candidate.\n",
        rationale="fresh",
        target_fingerprint="fingerprint",
    )

    class SemanticOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(
                candidates=(semantic_duplicate, fresh_candidate),
                lineage=(
                    OptimizerLineage(
                        candidate_id=semantic_duplicate.candidate_id,
                        optimizer_name="test",
                        optimizer_version="1",
                        semantic_fingerprint="semantic-same",
                        lesson_set_fingerprint="lesson-set-same",
                    ),
                    OptimizerLineage(
                        candidate_id=fresh_candidate.candidate_id,
                        optimizer_name="test",
                        optimizer_version="1",
                        semantic_fingerprint="semantic-fresh",
                        lesson_set_fingerprint="lesson-set-same",
                    ),
                ),
            )

    class ReplayBackend:
        def __init__(self) -> None:
            self.candidate_ids: list[str] = []

        async def replay_candidate(self, request, *, candidate, dataset):
            self.candidate_ids.append(candidate.candidate_id)
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "baseline"}}],
                    metrics={"repetition_count": 2, "successful_repetition_count": 2},
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[{"action": {"content": "candidate"}}],
                    metrics={"repetition_count": 3, "successful_repetition_count": 3},
                ),
            )

    class EvaluationBackend:
        async def evaluate_variant(self, request):
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={
                    "score": 0.9 if request.candidate is not None else 0.2,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                    "evidence_block_count": 1,
                    "evidence_compacted": False,
                    "evidence_incomplete": False,
                },
                dataset_split=request.dataset_split,
            )

    replay_backend = ReplayBackend()
    await SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=SemanticOptimizer(),
        evaluation_backend=EvaluationBackend(),
        replay_enabled=True,
        candidate_replay_backend=replay_backend,
        replay_candidate_limit=2,
        min_eval_cases=0,
    ).run_explicit_target(
        run_id="run-filter-semantic-duplicates",
        target=SkillTextTarget(skill_path),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="proposal",
    )

    assert replay_backend.candidate_ids == ["candidate-fresh-semantic"]
    report = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-filter-semantic-duplicates"
            / "report.json"
        ).read_text(encoding="utf-8")
    )
    assert report["optimizer_diagnostics"]["filtered_semantic_lesson_duplicate_candidates"] == 1
    assert report["iterations"][0]["candidate_id"] == "candidate-fresh-semantic"


@pytest.mark.asyncio
async def test_runner_lazily_imports_prior_report_lineage_for_semantic_filter(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    historical_dir = tmp_path / ".aworld" / "self_evolve" / "old-legacy-run"
    historical_dir.mkdir(parents=True)
    (historical_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": "old-legacy-run",
                "target": {
                    "target_type": "skill",
                    "target_id": "demo",
                    "path": str(skill_path),
                },
                "status": "rejected",
                "selected_candidate_id": "candidate-old-legacy",
                "iterations": [
                    {
                        "iteration": 1,
                        "candidate_id": "candidate-old-legacy",
                        "status": "rejected",
                        "failed_gates": ["score_improvement"],
                        "semantic_fingerprint": "semantic-legacy",
                        "lesson_set_fingerprint": "lesson-set-legacy",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Improve candidate generation."}},
            "action": {"content": "Prior candidates repeated semantically."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="legacy-lineage-filter-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="legacy-lineage-filter-task",
    )
    legacy_duplicate = CandidateVariant(
        candidate_id="candidate-new-legacy",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nSame legacy semantic candidate.\n",
        rationale="legacy duplicate",
        target_fingerprint="fingerprint",
    )
    fresh_candidate = CandidateVariant(
        candidate_id="candidate-fresh-legacy",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nFresh legacy candidate.\n",
        rationale="fresh",
        target_fingerprint="fingerprint",
    )

    class LegacyOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(
                candidates=(legacy_duplicate, fresh_candidate),
                lineage=(
                    OptimizerLineage(
                        candidate_id=legacy_duplicate.candidate_id,
                        optimizer_name="test",
                        optimizer_version="1",
                        semantic_fingerprint="semantic-legacy",
                        lesson_set_fingerprint="lesson-set-legacy",
                    ),
                    OptimizerLineage(
                        candidate_id=fresh_candidate.candidate_id,
                        optimizer_name="test",
                        optimizer_version="1",
                        semantic_fingerprint="semantic-fresh-legacy",
                        lesson_set_fingerprint="lesson-set-legacy",
                    ),
                ),
            )

    class ReplayBackend:
        def __init__(self) -> None:
            self.candidate_ids: list[str] = []

        async def replay_candidate(self, request, *, candidate, dataset):
            self.candidate_ids.append(candidate.candidate_id)
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "baseline"}}],
                    metrics={"repetition_count": 1, "successful_repetition_count": 1},
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[{"action": {"content": "candidate"}}],
                    metrics={"repetition_count": 1, "successful_repetition_count": 1},
                ),
            )

    class EvaluationBackend:
        async def evaluate_variant(self, request):
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={
                    "score": 0.9 if request.candidate is not None else 0.2,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                    "evidence_block_count": 1,
                    "evidence_compacted": False,
                    "evidence_incomplete": False,
                },
                dataset_split=request.dataset_split,
            )

    replay_backend = ReplayBackend()
    await SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=LegacyOptimizer(),
        evaluation_backend=EvaluationBackend(),
        replay_enabled=True,
        candidate_replay_backend=replay_backend,
        replay_candidate_limit=2,
        min_eval_cases=0,
    ).run_explicit_target(
        run_id="run-import-legacy-lineage",
        target=SkillTextTarget(skill_path),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="proposal",
    )

    assert replay_backend.candidate_ids == ["candidate-fresh-legacy"]
    imported_path = historical_dir / "optimizer_lineage" / "candidate-old-legacy.json"
    assert imported_path.exists()
    imported = json.loads(imported_path.read_text(encoding="utf-8"))
    assert imported["optimizer_name"] == "prior-report-import"
    assert imported["semantic_fingerprint"] == "semantic-legacy"
    assert imported["lesson_set_fingerprint"] == "lesson-set-legacy"


@pytest.mark.asyncio
async def test_runner_persists_lineage_lifecycle_for_rejected_and_accepted_candidates(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Improve quality."}},
            "action": {"content": "Need a better strategy."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="lineage-lifecycle-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="lineage-lifecycle-task",
    )
    weak_candidate = CandidateVariant(
        candidate_id="candidate-weak-lineage",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nWeak candidate.\n",
        rationale="weak",
        target_fingerprint="fingerprint",
    )
    strong_candidate = CandidateVariant(
        candidate_id="candidate-strong-lineage",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nStrong candidate.\n",
        rationale="strong",
        target_fingerprint="fingerprint",
    )

    class LifecycleOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(
                candidates=(weak_candidate, strong_candidate),
                lineage=(
                    OptimizerLineage(
                        candidate_id=weak_candidate.candidate_id,
                        optimizer_name="test",
                        optimizer_version="1",
                        semantic_fingerprint="semantic-weak",
                        lesson_set_fingerprint="lesson-set",
                    ),
                    OptimizerLineage(
                        candidate_id=strong_candidate.candidate_id,
                        optimizer_name="test",
                        optimizer_version="1",
                        semantic_fingerprint="semantic-strong",
                        lesson_set_fingerprint="lesson-set",
                    ),
                ),
            )

    class ReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "baseline"}}],
                    metrics={"repetition_count": 2, "successful_repetition_count": 2},
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[{"action": {"content": "candidate"}}],
                    metrics={"repetition_count": 3, "successful_repetition_count": 3},
                ),
            )

    class EvaluationBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                score = 0.5
            elif request.candidate.candidate_id == weak_candidate.candidate_id:
                score = 0.4
            else:
                score = 0.9
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={
                    "score": score,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                    "evidence_block_count": 1,
                    "evidence_compacted": False,
                    "evidence_incomplete": False,
                },
                dataset_split=request.dataset_split,
            )

    def post_apply_evaluator(candidate: CandidateVariant) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True},
            dataset_split="post_apply",
        )

    await SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=LifecycleOptimizer(),
        evaluation_backend=EvaluationBackend(),
        replay_enabled=True,
        candidate_replay_backend=ReplayBackend(),
        replay_candidate_limit=2,
        baseline_replay_repetitions=2,
        candidate_replay_repetitions=3,
        min_eval_cases=0,
        post_apply_evaluator=post_apply_evaluator,
    ).run_explicit_target(
        run_id="run-lineage-lifecycle",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    lineage_dir = (
        tmp_path / ".aworld" / "self_evolve" / "run-lineage-lifecycle" / "optimizer_lineage"
    )
    weak_lineage = json.loads(
        (lineage_dir / "candidate-weak-lineage.json").read_text(encoding="utf-8")
    )
    strong_lineage = json.loads(
        (lineage_dir / "candidate-strong-lineage.json").read_text(encoding="utf-8")
    )
    assert weak_lineage["lifecycle_status"] == "rejected"
    assert weak_lineage["replayed"] is True
    assert "score_improvement" in weak_lineage["failed_gates"]
    assert strong_lineage["lifecycle_status"] == "accepted"
    assert strong_lineage["replayed"] is True
    assert strong_lineage["post_apply_status"] == "accepted"


@pytest.mark.asyncio
async def test_runner_reports_gate_results_when_all_candidates_are_rejected(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="rejected-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="rejected-task",
    )
    candidate = CandidateVariant(
        candidate_id="candidate-rejected",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nCandidate guidance.\n",
        rationale="will be rejected",
        target_fingerprint="fingerprint",
    )

    class Optimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=(candidate,))

    class FailingBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 0.9, "latency_ms": 100.0, "cost_usd": 1.0},
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 0.1,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True},
            dataset_split="post_apply",
        )

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=Optimizer(),
        post_apply_evaluator=post_apply,
        evaluation_backend=FailingBackend(),
        min_eval_cases=0,
        max_iterations=1,
    )

    result = await runner.run_explicit_target(
        run_id="run-all-rejected",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    report = json.loads((store.run_path("run-all-rejected") / "report.json").read_text(encoding="utf-8"))
    assert report["selected_candidate_id"] == "candidate-rejected"
    assert report["release_checklist"]["status"] == "blocked"
    assert any(
        gate["gate_name"] == "score_improvement" and gate["passed"] is False
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_runner_emits_progress_events_for_long_optimize_phases(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix evidence handling."}},
            "action": {"content": "Evidence was missing."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="progress-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="progress-task",
    )
    candidate = CandidateVariant(
        candidate_id="candidate-progress",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nCandidate guidance.\n",
        rationale="progress test",
        target_fingerprint="fingerprint",
    )

    class Optimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=(candidate,))

    class Backend:
        async def evaluate_variant(self, request):
            score = 0.9 if request.candidate is not None else 0.2
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={
                    "score": score,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                    "evidence_block_count": 1,
                    "evidence_compacted": False,
                    "evidence_incomplete": False,
                },
                dataset_split=request.dataset_split,
            )

    class ReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "baseline"}}],
                    metrics={"repetition_count": 2, "successful_repetition_count": 2},
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[{"action": {"content": "candidate"}}],
                    metrics={"repetition_count": 3, "successful_repetition_count": 3},
                ),
            )

    def post_apply(candidate: CandidateVariant) -> EvaluationSummary:
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True},
            dataset_split="post_apply",
        )

    events: list[tuple[str, str]] = []
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=Optimizer(),
        evaluation_backend=Backend(),
        min_eval_cases=0,
        replay_enabled=True,
        candidate_replay_backend=ReplayBackend(),
        baseline_replay_repetitions=2,
        candidate_replay_repetitions=3,
        post_apply_evaluator=post_apply,
        progress_callback=lambda stage, message: events.append((stage, message)),
    )

    await runner.run_explicit_target(
        run_id="run-progress",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    stages = [stage for stage, _ in events]
    assert stages[:7] == [
        "start",
        "trajectory_set_loading",
        "candidate_generation",
        "population_generation",
        "replay_adaptation",
        "candidate_replay",
        "evaluation",
    ]
    assert "lesson_extraction" in stages
    assert "release_normalization" in stages
    assert stages[-1] == "completed"


@pytest.mark.asyncio
async def test_runner_uses_prior_rejected_candidate_feedback_across_runs(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    historical_dir = tmp_path / ".aworld" / "self_evolve" / "old-run"
    historical_dir.mkdir(parents=True)
    (historical_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": "old-run",
                "target": {
                    "target_type": "skill",
                    "target_id": "demo",
                    "path": str(skill_path),
                },
                "status": "rejected",
                "candidate_ids": ["candidate-dup"],
                "iterations": [
                    {
                        "iteration": 1,
                        "candidate_id": "candidate-dup",
                        "status": "rejected",
                        "baseline_metrics": {
                            "score": 68.0,
                            "evidence_block_count": 22.0,
                            "evidence_incomplete": 0.3,
                            "latency_ms": 200_000.0,
                        },
                        "candidate_metrics": {
                            "score": 35.0,
                            "A1_groundedness": 1.0,
                            "evidence_compacted": True,
                            "evidence_block_count": 30.0,
                            "evidence_incomplete": 0.8,
                            "latency_ms": 330_000.0,
                        },
                        "failed_gates": ["evidence_quality", "score_improvement"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix evidence handling."}},
            "action": {"content": "Evidence was missing."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="history-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="history-task",
    )
    duplicate_candidate = CandidateVariant(
        candidate_id="candidate-dup",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nDuplicate guidance.\n",
        rationale="repeated historical failure",
        target_fingerprint="fingerprint",
    )
    fresh_candidate = CandidateVariant(
        candidate_id="candidate-fresh",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nFresh guidance.\n",
        rationale="uses historical failure feedback",
        target_fingerprint="fingerprint",
    )

    class HistoryAwareOptimizer:
        def __init__(self) -> None:
            self.requests: list[OptimizerRequest] = []

        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            self.requests.append(request)
            return OptimizerResult(
                candidates=(
                    duplicate_candidate
                    if len(self.requests) == 1
                    else fresh_candidate,
                )
            )

    class VerifiedBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 0.2, "latency_ms": 100.0, "cost_usd": 1.0},
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 0.9,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True},
            dataset_split="post_apply",
        )

    optimizer = HistoryAwareOptimizer()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=optimizer,
        post_apply_evaluator=post_apply,
        evaluation_backend=VerifiedBackend(),
        min_eval_cases=0,
        max_iterations=2,
    )

    result = await runner.run_explicit_target(
        run_id="new-run",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "succeeded"
    assert result.selected_candidate is fresh_candidate
    assert len(optimizer.requests) == 2
    assert optimizer.requests[0].prior_feedback
    assert optimizer.requests[0].prior_feedback[0].variant_id == "candidate-dup"
    assert optimizer.requests[0].prior_feedback[0].metrics["failed_gates"] == [
        "evidence_quality",
        "score_improvement",
    ]
    assert optimizer.requests[0].prior_feedback[0].metrics["baseline_score"] == 68.0
    assert optimizer.requests[0].prior_feedback[0].metrics["candidate_score"] == 35.0
    assert optimizer.requests[0].prior_feedback[0].metrics["score_delta"] == pytest.approx(-33.0)
    assert (
        optimizer.requests[0]
        .prior_feedback[0]
        .metrics["evidence_block_count_delta"]
        == pytest.approx(8.0)
    )
    assert (
        optimizer.requests[0]
        .prior_feedback[0]
        .metrics["evidence_incomplete_delta"]
        == pytest.approx(0.5)
    )
    assert optimizer.requests[0].prior_feedback[0].metrics["latency_ms_delta"] == pytest.approx(
        130_000.0
    )
    assert optimizer.requests[1].validation_feedback[0].metrics["failed_gates"] == [
        "duplicate_rejected_candidate"
    ]
    report = json.loads((tmp_path / ".aworld" / "self_evolve" / "new-run" / "report.json").read_text())
    assert report["iterations"][0]["status"] == "rejected"
    assert report["iterations"][0]["failed_gates"] == ["duplicate_rejected_candidate"]
    assert report["iterations"][1]["status"] == "accepted"


def test_include_prior_run_cases_normalizes_accepted_rejected_and_replay_refs(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    store = FilesystemSelfEvolveStore(tmp_path)
    target = SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path))
    for run_id, status, candidate_id in (
        ("accepted-run", "succeeded", "candidate-good"),
        ("rejected-run", "rejected", "candidate-bad"),
    ):
        run_dir = store.run_path(run_id)
        run_dir.mkdir(parents=True)
        (run_dir / "report.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "target": {
                        "target_type": "skill",
                        "target_id": "demo",
                        "path": str(skill_path),
                    },
                    "status": status,
                    "selected_candidate_id": candidate_id,
                    "replay_path": str(run_dir / "replay" / candidate_id),
                    "evaluator_report_paths": [
                        str(run_dir / "evaluator" / candidate_id / "report.json")
                    ],
                    "post_apply": (
                        {"status": "accepted", "release_state": "verified"}
                        if status == "succeeded"
                        else None
                    ),
                    "gate_results": [
                        {
                            "gate_name": "score_improvement",
                            "passed": status == "succeeded",
                        }
                    ],
                    "candidate_metrics": {"score": 90.0 if status == "succeeded" else 40.0},
                }
            ),
            encoding="utf-8",
        )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=[
            {
                "meta": {"step": 1, "agent_id": "agent"},
                "state": {"input": {"content": "Improve."}},
                "action": {"content": "Baseline."},
            }
        ],
        task_id="current-task",
    )

    updated = _include_prior_run_cases(
        dataset,
        store=store,
        target=target,
        current_run_id="new-run",
    )

    prior_cases = [
        case for case in updated.cases if case.source.get("kind") == "prior_self_evolve_run"
    ]
    assert {case.source["role"] for case in prior_cases} == {
        "accepted_followup",
        "rejected_candidate",
    }
    assert all(case.case_id in updated.recipe.trainable_case_ids for case in prior_cases)
    accepted_case = next(
        case for case in prior_cases if case.source["role"] == "accepted_followup"
    )
    rejected_case = next(
        case for case in prior_cases if case.source["role"] == "rejected_candidate"
    )
    assert accepted_case.input["post_apply_status"] == "accepted"
    assert accepted_case.input["replay_path"].startswith("<LOCAL_PATH>/")
    assert accepted_case.input["replay_path"].endswith("/candidate-good")
    assert accepted_case.input["evaluator_report_paths"][0].startswith("<LOCAL_PATH>/")
    assert accepted_case.input["evaluator_report_paths"][0].endswith(
        "/candidate-good/report.json"
    )
    assert rejected_case.input["failed_gates"] == ["score_improvement"]


@pytest.mark.asyncio
async def test_runner_uses_trajectory_set_learning_before_replay_selection(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trace_pack = build_trace_pack(
        [
            {
                "meta": {"step": 1, "agent_id": "agent"},
                "state": {"input": {"content": "Train case."}},
                "action": {"content": "Failure evidence."},
                "reward": {"status": "failed"},
            }
        ],
        source_kind="trajectory_set",
        task_id="train-case",
    )
    train_case = EvalCase(
        case_id="train-case",
        input={"task": "train"},
        trace_pack=trace_pack,
        metadata={"trajectory_set": {"member": {"role": "baseline"}}},
        source={"kind": "trajectory_set", "role": "baseline"},
    )
    held_out_case = EvalCase(
        case_id="held-out-case",
        input={"task": "held out"},
        metadata={"trajectory_set": {"member": {"role": "accepted_followup"}}},
        source={"kind": "trajectory_set", "role": "accepted_followup"},
    )
    dataset = SelfEvolveDataset(
        cases=(train_case, held_out_case),
        recipe=DatasetRecipe(
            source={"kind": "trajectory_set", "path": "set.json", "case_count": 2},
            split_seed="trajectory-set-learning",
            splits={
                "train": ["train-case"],
                "validation": [],
                "held_out": ["held-out-case"],
            },
            trainable_case_ids=("train-case",),
            held_out_case_ids=("held-out-case",),
        ),
    )
    candidate = CandidateVariant(
        candidate_id="candidate-trajectory-set",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content="---\nname: demo\n---\n# Demo\n\nTrajectory-set guidance.\n",
        rationale="trajectory-set learning",
        target_fingerprint="fingerprint",
    )
    events: list[str] = []

    class CapturingOptimizer:
        def __init__(self) -> None:
            self.requests: list[OptimizerRequest] = []

        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            events.append("optimizer")
            self.requests.append(request)
            return OptimizerResult(candidates=(candidate,))

    class ReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            events.append("replay")
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "baseline"}}],
                    metrics={"repetition_count": 1, "successful_repetition_count": 1},
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[{"action": {"content": "candidate"}}],
                    metrics={"repetition_count": 1, "successful_repetition_count": 1},
                ),
            )

    optimizer = CapturingOptimizer()
    await SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=optimizer,
        replay_enabled=True,
        candidate_replay_backend=ReplayBackend(),
        evaluation_backend=None,
    ).run_explicit_target(
        run_id="run-trajectory-set-learning",
        target=SkillTextTarget(skill_path),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="proposal",
    )

    assert events == ["optimizer", "replay"]
    assert [case.case_id for case in optimizer.requests[0].trainable_cases] == [
        "train-case"
    ]
    report = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-trajectory-set-learning"
            / "report.json"
        ).read_text(encoding="utf-8")
    )
    assert report["trajectory_set"]["source_kind"] == "trajectory_set"
    assert report["trajectory_set"]["member_roles"] == {
        "accepted_followup": 1,
        "baseline": 1,
    }


@pytest.mark.asyncio
async def test_runner_feeds_prior_lesson_memory_into_optimizer_request(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    historical_dir = tmp_path / ".aworld" / "self_evolve" / "old-run"
    lessons_dir = historical_dir / "lessons"
    lessons_dir.mkdir(parents=True)
    lessons_path = lessons_dir / "lessons.jsonl"
    lessons_path.write_text(
        json.dumps(
            {
                "lesson_id": "required-runtime-behavior-1",
                "lesson_type": "required_runtime_behavior",
                "title": "Preserve required runtime behavior",
                "summary": "Future candidates should preserve artifact-first behavior.",
                "target_scope": {"target_type": "skill", "target_id": "demo"},
                "source_run_ids": ["old-run"],
                "source_task_ids": ["old-task"],
                "metrics": {
                    "failed_gates": ["evidence_quality"],
                    "required_behaviors": ["artifact_first", "claim_evidence_ledger"],
                    "evidence_compacted": True,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (historical_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": "old-run",
                "target": {
                    "target_type": "skill",
                    "target_id": "demo",
                    "path": str(skill_path),
                },
                "lessons": {"path": str(lessons_path), "count": 1},
                "iterations": [],
            }
        ),
        encoding="utf-8",
    )
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Improve evidence handling."}},
            "action": {"content": "Evidence was missing."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="new-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="new-task",
    )

    class CapturingOptimizer:
        def __init__(self) -> None:
            self.requests: list[OptimizerRequest] = []

        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            self.requests.append(request)
            return OptimizerResult(candidates=())

    optimizer = CapturingOptimizer()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=optimizer,
    )

    await runner.run_explicit_target(
        run_id="new-run",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="proposal",
    )

    assert optimizer.requests
    assert len(optimizer.requests[0].prior_feedback) == 1
    lesson_feedback = optimizer.requests[0].prior_feedback[0]
    assert lesson_feedback.dataset_split == "lesson_memory"
    assert lesson_feedback.metrics["lesson_id"] == "required-runtime-behavior-1"
    assert lesson_feedback.metrics["occurrence_count"] == 1
    assert "artifact_first" in lesson_feedback.metrics["required_behaviors"]


@pytest.mark.asyncio
async def test_runner_skips_duplicate_rejected_candidate_before_replay(
    tmp_path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    helper_path = tmp_path / "skills" / "helper" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    helper_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    candidate_content = "---\nname: demo\n---\n# Demo\n\nDuplicate guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    helper_path.write_text("---\nname: helper\n---\n# Helper\n", encoding="utf-8")
    historical_dir = tmp_path / ".aworld" / "self_evolve" / "old-run"
    historical_dir.mkdir(parents=True)
    (historical_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": "old-run",
                "target": {
                    "target_type": "skill",
                    "target_id": "demo",
                    "path": str(skill_path),
                },
                "status": "rejected",
                "candidate_ids": ["candidate-dup"],
                "iterations": [
                    {
                        "iteration": 1,
                        "candidate_id": "candidate-dup",
                        "status": "rejected",
                        "candidate_metrics": {"score": 0.3},
                        "failed_gates": ["evidence_quality"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix evidence handling."}},
            "action": {"content": "Evidence was missing."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="duplicate-replay-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="duplicate-replay-task",
    )

    duplicate_candidate = CandidateVariant(
        candidate_id="candidate-dup",
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="demo",
            path=str(skill_path),
        ),
        content=candidate_content,
        rationale="repeat candidate under a changed replay environment",
        target_fingerprint="fingerprint",
    )

    class DuplicateOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=(duplicate_candidate,))

    class ReplayBackend:
        def __init__(self) -> None:
            self.requests = []

        async def replay_candidate(self, request, *, candidate, dataset):
            self.requests.append(request)
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "old"}}],
                    metrics={"repetition_count": 1, "successful_repetition_count": 1},
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[{"action": {"content": "new"}}],
                    metrics={"repetition_count": 1, "successful_repetition_count": 1},
                ),
            )

    class VerifiedBackend:
        def __init__(self) -> None:
            self.requests = []

        async def evaluate_variant(self, request):
            self.requests.append(request)
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={
                    "score": 0.9 if request.candidate else 0.2,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": len(request.dataset.cases),
                    "command_pass_count": len(request.dataset.cases),
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    async def post_apply(candidate):
        pytest.fail("duplicate rejected candidate must not auto apply")

    replay_backend = ReplayBackend()
    evaluation_backend = VerifiedBackend()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=DuplicateOptimizer(),
        evaluation_backend=evaluation_backend,
        post_apply_evaluator=post_apply,
        min_eval_cases=0,
        replay_enabled=True,
        candidate_replay_backend=replay_backend,
    )

    result = await runner.run_explicit_target(
        run_id="run-duplicate-replay",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert replay_backend.requests == []
    assert evaluation_backend.requests == []
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-duplicate-replay"
            / "report.json"
        ).read_text(encoding="utf-8")
    )
    assert "replay" not in report
    assert report["iterations"][0]["failed_gates"] == ["duplicate_rejected_candidate"]


@pytest.mark.asyncio
async def test_iteration_duplicate_rejection_preserves_feedback_state(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld.\n", encoding="utf-8")
    candidate = CandidateVariant(
        candidate_id="candidate-duplicate",
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="demo",
            path=str(skill_path),
        ),
        content="---\nname: demo\n---\n# Demo\n\nChanged.\n",
        rationale="duplicate repair",
        target_fingerprint="sha256:old",
    )
    dataset = SelfEvolveDataset(
        cases=(EvalCase(case_id="task-1", input={"content": "task"}),),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["task-1"], "validation": [], "held_out": []},
        ),
    )
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=EmptyOptimizer(),
        evaluation_backend=None,
        min_eval_cases=0,
    )

    state, report_item, feedback = await runner._evaluate_iteration_candidate(
        run_id="run-duplicate-state",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        candidate=candidate,
        apply_policy="auto_verified",
        target_provenance=TargetProvenance(
            target=candidate.target,
            source_kind="skill",
            write_origin="operator_selection",
            trust_level="local",
            protected=False,
            reason="explicit local target for duplicate-gate isolation",
        ),
        iteration_number=1,
        candidate_number=1,
        candidate_count=1,
        rejected_candidate_ids={candidate.candidate_id},
        accepted_candidate_ids=set(),
    )

    assert state["status"] == "rejected"
    assert state["feedback"] == feedback
    assert feedback[0].metrics["failed_gates"] == [
        "duplicate_rejected_candidate"
    ]
    assert report_item["failed_gates"] == ["duplicate_rejected_candidate"]


@pytest.mark.asyncio
async def test_runner_allows_duplicate_rejected_candidate_after_judge_infrastructure_failure(
    tmp_path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    candidate_content = "---\nname: demo\n---\n# Demo\n\nRetryable guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    historical_dir = tmp_path / ".aworld" / "self_evolve" / "old-run"
    historical_dir.mkdir(parents=True)
    judge_failure = {
        "judge_attempt_count": 3,
        "judge_success_count": 0,
        "judge_failure_count": 3,
        "judge_failures": [
            {
                "attempt": 1,
                "type": "KeyError",
                "reason": "'model profile not found or incomplete: judge'",
            }
        ],
    }
    (historical_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": "old-run",
                "target": {
                    "target_type": "skill",
                    "target_id": "demo",
                    "path": str(skill_path),
                },
                "status": "rejected",
                "selected_candidate_id": "candidate-dup",
                "iterations": [
                    {
                        "iteration": 1,
                        "candidate_id": "candidate-dup",
                        "status": "rejected",
                        "baseline_metrics": {"score": 0.0, **judge_failure},
                        "candidate_metrics": {"score": 0.0, **judge_failure},
                        "held_out_metrics": {"score": 0.0, **judge_failure},
                        "failed_gates": [
                            "score_improvement",
                            "required_verification",
                            "held_out_verification",
                            "judge_only_signal",
                            "global_regression_benchmark",
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix evidence handling."}},
            "action": {"content": "Evidence was missing."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="duplicate-infra-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="duplicate-infra-task",
    )

    duplicate_candidate = CandidateVariant(
        candidate_id="candidate-dup",
        target=SelfEvolveTargetRef(
            target_type="skill",
            target_id="demo",
            path=str(skill_path),
        ),
        content=candidate_content,
        rationale="repeat candidate after evaluator infrastructure fix",
        target_fingerprint="fingerprint",
    )

    class DuplicateOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=(duplicate_candidate,))

    class ReplayBackend:
        def __init__(self) -> None:
            self.requests = []

        async def replay_candidate(self, request, *, candidate, dataset):
            self.requests.append(request)
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "old"}}],
                    metrics={"repetition_count": 2, "successful_repetition_count": 2},
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[{"action": {"content": "new"}}],
                    metrics={"repetition_count": 3, "successful_repetition_count": 3},
                ),
            )

    class VerifiedBackend:
        def __init__(self) -> None:
            self.requests = []

        async def evaluate_variant(self, request):
            self.requests.append(request)
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={
                    "score": 0.9 if request.candidate else 0.2,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": len(request.dataset.cases),
                    "command_pass_count": len(request.dataset.cases),
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True, "deterministic_signal": True},
            dataset_split="post_apply",
        )

    replay_backend = ReplayBackend()
    evaluation_backend = VerifiedBackend()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=DuplicateOptimizer(),
        evaluation_backend=evaluation_backend,
        post_apply_evaluator=post_apply,
        min_eval_cases=0,
        replay_enabled=True,
        candidate_replay_backend=replay_backend,
        baseline_replay_repetitions=2,
        candidate_replay_repetitions=3,
    )

    result = await runner.run_explicit_target(
        run_id="run-duplicate-infra-retry",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "succeeded"
    assert replay_backend.requests
    assert evaluation_backend.requests
    applied_content = skill_path.read_text(encoding="utf-8")
    assert "Retryable guidance." in applied_content
    assert "release_state: verified" in applied_content


@pytest.mark.asyncio
async def test_runner_rejects_duplicate_previously_accepted_candidate(
    tmp_path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    candidate_content = "---\nname: demo\n---\n# Demo\n\nAlready accepted guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    historical_dir = tmp_path / ".aworld" / "self_evolve" / "old-accepted-run"
    historical_dir.mkdir(parents=True)
    (historical_dir / "report.json").write_text(
        json.dumps(
            {
                "run_id": "old-accepted-run",
                "target": {
                    "target_type": "skill",
                    "target_id": "demo",
                    "path": str(skill_path),
                },
                "status": "succeeded",
                "candidate_ids": ["candidate-accepted"],
                "iterations": [
                    {
                        "iteration": 1,
                        "candidate_id": "candidate-accepted",
                        "status": "accepted",
                        "baseline_metrics": {"score": 50.0},
                        "candidate_metrics": {"score": 90.0},
                        "held_out_metrics": {
                            "deterministic_signal": True,
                            "command_case_count": 1,
                            "command_pass_count": 1,
                            "global_regression_passed": True,
                        },
                        "failed_gates": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Improve guidance."}},
            "action": {"content": "Need better guidance."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="accepted-dup-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="accepted-dup-task",
    )
    duplicate_candidate = CandidateVariant(
        candidate_id="candidate-accepted",
        target=SelfEvolveTargetRef(target_type="skill", target_id="demo", path=str(skill_path)),
        content=candidate_content,
        rationale="repeat previously accepted candidate",
        target_fingerprint="fingerprint",
    )

    class DuplicateAcceptedOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            return OptimizerResult(candidates=(duplicate_candidate,))

    class ShouldNotEvaluate:
        async def evaluate_variant(self, request):
            pytest.fail("duplicate accepted candidate should not reach evaluation")

    async def post_apply(candidate):
        pytest.fail("duplicate accepted candidate should not auto apply")

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=DuplicateAcceptedOptimizer(),
        post_apply_evaluator=post_apply,
        evaluation_backend=ShouldNotEvaluate(),
        min_eval_cases=0,
    )

    result = await runner.run_explicit_target(
        run_id="run-duplicate-accepted",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    report = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-duplicate-accepted"
            / "report.json"
        ).read_text(encoding="utf-8")
    )
    assert report["iterations"][0]["failed_gates"] == ["duplicate_accepted_candidate"]


@pytest.mark.asyncio
async def test_runner_auto_verified_rejects_without_evaluation_backend(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    candidate_content = "---\nname: demo\n---\n# Demo\n\nUnsafe guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="no-eval-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="no-eval-task",
    )

    async def mutate(prompt: str) -> dict:
        return {"content": candidate_content, "rationale": "No verification."}

    async def post_apply(candidate):
        pytest.fail("auto_verified must not apply before verification gates pass")

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        post_apply_evaluator=post_apply,
    )

    result = await runner.run_explicit_target(
        run_id="run-auto-no-eval",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "failed"
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads((store.run_path("run-auto-no-eval") / "report.json").read_text(encoding="utf-8"))
    assert any(
        gate["gate_name"] == "auto_verified_evaluation"
        and gate["passed"] is False
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_runner_auto_verified_rejects_failed_candidate_gates_before_apply(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="bad-candidate")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="bad-candidate",
    )

    async def mutate(prompt: str) -> dict:
        return {"content": "# Demo\n\nMissing frontmatter.\n", "rationale": "Malformed."}

    async def post_apply(candidate):
        pytest.fail("malformed auto_verified candidate must not be applied")

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        post_apply_evaluator=post_apply,
    )

    result = await runner.run_explicit_target(
        run_id="run-auto-bad-candidate",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads((store.run_path("run-auto-bad-candidate") / "report.json").read_text(encoding="utf-8"))
    assert any(
        gate["gate_name"] == "skill_markdown"
        and gate["passed"] is False
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_runner_auto_verified_rolls_back_when_post_apply_gate_fails(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    candidate_content = "---\nname: demo\n---\n# Demo\n\nRegressing guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="rollback-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="rollback-task",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": candidate_content,
            "rationale": "Regressing candidate.",
            "files": [
                {
                    "path": "replay/compiler.py",
                    "content": "print('candidate compiler')\n",
                    "executable": True,
                }
            ],
        }

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": False, "score": 0.0},
            dataset_split="post_apply",
        )

    class VerifiedBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 0.5, "latency_ms": 100.0, "cost_usd": 1.0},
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 0.9,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        post_apply_evaluator=post_apply,
        evaluation_backend=VerifiedBackend(),
        min_eval_cases=0,
    )

    result = await runner.run_explicit_target(
        run_id="run-auto-rollback",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert skill_path.read_text(encoding="utf-8") == original_content
    assert not (skill_path.parent / "replay/compiler.py").exists()
    report = json.loads((store.run_path("run-auto-rollback") / "report.json").read_text(encoding="utf-8"))
    assert report["post_apply"]["status"] == "rolled_back"
    assert report["post_apply"]["metrics"]["post_apply_passed"] is False
    journal = json.loads(Path(report["post_apply"]["journal_path"]).read_text(encoding="utf-8"))
    assert journal["status"] == "rolled_back"


@pytest.mark.asyncio
async def test_runner_auto_verified_rolls_back_when_runtime_skill_activation_fails(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    candidate_content = "---\nname: demo\n---\n# Demo\n\nVerified guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="activation-fail-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="activation-fail-task",
    )

    async def mutate(prompt: str) -> dict:
        return {"content": candidate_content, "rationale": "Verified candidate."}

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True, "deterministic_signal": True},
            dataset_split="post_apply",
        )

    def activate_runtime_skill(candidate):
        raise RuntimeError("skill activation failed")

    class VerifiedBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 0.5},
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 0.9,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    store = FilesystemSelfEvolveStore(tmp_path)
    runner = SelfEvolveRunner(
        store=store,
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        post_apply_evaluator=post_apply,
        evaluation_backend=VerifiedBackend(),
        min_eval_cases=0,
        runtime_skill_activator=activate_runtime_skill,
    )

    result = await runner.run_explicit_target(
        run_id="run-activation-fail",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads(
        (store.run_path("run-activation-fail") / "report.json").read_text(encoding="utf-8")
    )
    assert report["post_apply"]["status"] == "rolled_back"
    assert report["post_apply"]["metrics"]["post_apply_passed"] is True
    assert report["post_apply"]["metrics"]["activation_passed"] is False
    assert report["post_apply"]["metrics"]["activation_error"] == "skill activation failed"
    journal = json.loads(Path(report["post_apply"]["journal_path"]).read_text(encoding="utf-8"))
    assert journal["status"] == "rolled_back"
    assert journal["details"]["activation_passed"] is False


@pytest.mark.asyncio
async def test_runner_auto_verified_rejects_skill_candidate_when_replay_backend_missing(
    tmp_path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="replay-required")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="replay-required",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nCandidate guidance.\n",
            "rationale": "Candidate requires replay.",
        }

    async def post_apply(candidate):
        pytest.fail("auto_verified must not apply when replay backend is missing")

    class VerifiedBackend:
        async def evaluate_variant(self, request):
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={
                    "score": 1.0 if request.candidate else 0.1,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=VerifiedBackend(),
        post_apply_evaluator=post_apply,
        min_eval_cases=0,
        replay_enabled=True,
        candidate_replay_backend=None,
    )

    result = await runner.run_explicit_target(
        run_id="run-replay-required",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads((tmp_path / ".aworld" / "self_evolve" / "run-replay-required" / "report.json").read_text())
    assert any(
        gate["gate_name"] == "candidate_replay"
        and gate["passed"] is False
        and gate["reason"] == "auto_verified skill apply requires candidate replay backend"
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_candidate_preflight_uses_only_replayable_user_cases(
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    dataset = SelfEvolveDataset(
        cases=(
            EvalCase(
                case_id="user-case",
                input={"content": "Summarize the supplied deterministic fixture."},
            ),
            EvalCase(
                case_id="framework-case",
                input={"content": "Inspect http://127.0.0.1:9222"},
                metadata={"framework_meta_trajectory": True},
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 2},
            split_seed="seed",
            splits={"train": ["user-case", "framework-case"]},
        ),
    )
    optimizer_requests: list[OptimizerRequest] = []

    class CapturingOptimizer:
        async def propose(self, request: OptimizerRequest) -> OptimizerResult:
            optimizer_requests.append(request)
            return OptimizerResult(candidates=())

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=CapturingOptimizer(),
    )

    await runner.run_explicit_target(
        run_id="run-filtered-preflight",
        target=SkillTextTarget(skill_path),
        dataset=dataset,
        trace_packs=(),
        apply_policy="proposal",
    )

    assert len(optimizer_requests) == 1
    assert optimizer_requests[0].replay_requirements == ()
    persisted = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-filtered-preflight"
            / "replay_requirements.json"
        ).read_text(encoding="utf-8")
    )
    assert persisted["requirements"] == []


@pytest.mark.asyncio
async def test_runner_rejects_when_replay_dataset_has_only_framework_tasks(
    tmp_path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "trajectory-evaluator-agent-md"},
            "state": {
                "input": {
                    "content": (
                        "evaluation_runtime_contract: do_not_call_external_tools=true "
                        f"trajectory_log_path={tmp_path}/.aworld/self_evolve/evaluator/run/trajectory.log"
                    )
                }
            },
            "action": {"content": "judge evaluation output"},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="trajectory_log",
        task_id="framework-evaluator-case",
    )
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
                trace_pack=trace_pack,
            ),
        ),
        recipe=DatasetRecipe(
            source={"kind": "test", "case_count": 1},
            split_seed="seed",
            splits={"train": ["framework-evaluator-case"], "validation": [], "held_out": []},
        ),
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nCandidate guidance.\n",
            "rationale": "Candidate requires replay.",
        }

    class ReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            pytest.fail("framework-generated evaluation tasks must not be replayed")

    async def post_apply(candidate):
        pytest.fail("auto_verified must not apply without a replayable user task")

    class VerifiedBackend:
        async def evaluate_variant(self, request):
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={
                    "score": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=VerifiedBackend(),
        post_apply_evaluator=post_apply,
        min_eval_cases=0,
        replay_enabled=True,
        candidate_replay_backend=ReplayBackend(),
    )

    result = await runner.run_explicit_target(
        run_id="run-framework-only-replay",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-framework-only-replay"
            / "report.json"
        ).read_text()
    )
    assert any(
        gate["gate_name"] == "candidate_replay"
        and gate["passed"] is False
        and "requires at least one user task eval case" in gate["reason"]
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_runner_auto_verified_uses_candidate_replay_dataset_for_evaluation(
    tmp_path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    helper_path = tmp_path / "skills" / "helper" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    helper_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    helper_path.write_text("---\nname: helper\n---\n# Helper\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="replay-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="replay-task",
    )

    candidate_content = "---\nname: demo\n---\n# Demo\n\nReplay verified guidance.\n"

    async def mutate(prompt: str) -> dict:
        return {"content": candidate_content, "rationale": "Replay verified candidate."}

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True, "deterministic_signal": True},
            dataset_split="post_apply",
        )

    class FakeReplayBackend:
        def __init__(self):
            self.requests = []

        async def replay_candidate(self, request, *, candidate, dataset):
            self.requests.append(request)
            baseline_repetitions = tuple(
                ReplayVariantResult(
                    variant_id=f"baseline-{index}",
                    status="succeeded",
                    trajectory=[
                        {
                            "state": {"input": request.task_input},
                            "action": {"content": f"old-{index}"},
                        }
                    ],
                )
                for index in range(1, request.baseline_repetitions + 1)
            )
            candidate_repetitions = tuple(
                ReplayVariantResult(
                    variant_id=f"{candidate.candidate_id}-{index}",
                    status="succeeded",
                    trajectory=[
                        {
                            "state": {"input": request.task_input},
                            "action": {"content": f"new-{index}"},
                        }
                    ],
                )
                for index in range(1, request.candidate_repetitions + 1)
            )
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=baseline_repetitions[-1].trajectory,
                    metrics={
                        "score": 0.4,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "repetition_count": len(baseline_repetitions),
                        "successful_repetition_count": len(baseline_repetitions),
                    },
                    repetition_results=baseline_repetitions,
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=candidate_repetitions[-1].trajectory,
                    metrics={
                        "score": 0.9,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "repetition_count": len(candidate_repetitions),
                        "successful_repetition_count": len(candidate_repetitions),
                    },
                    repetition_results=candidate_repetitions,
                ),
            )

    class PairedDatasetBackend:
        def __init__(self):
            self.requests = []

        async def evaluate_variant(self, request):
            self.requests.append(request)
            if request.candidate is None:
                baseline_outputs = [
                    case.metadata["variant_trajectories"]["baseline"][0]["action"]["content"]
                    for case in request.dataset.cases
                ]
                assert baseline_outputs == ["old-1", "old-2", "old-1"]
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={
                        "score": 0.4,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "deterministic_signal": True,
                        "command_case_count": len(request.dataset.cases),
                        "command_pass_count": len(request.dataset.cases),
                        "global_regression_passed": True,
                        "report_path": str(tmp_path / "baseline-eval-report.json"),
                    },
                    dataset_split=request.dataset_split,
                )
            candidate_outputs = [
                case.metadata["variant_trajectories"][request.candidate.candidate_id][0][
                    "action"
                ]["content"]
                for case in request.dataset.cases
            ]
            assert candidate_outputs == ["new-1", "new-2", "new-3"]
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 0.9,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": len(request.dataset.cases),
                    "command_pass_count": len(request.dataset.cases),
                    "global_regression_passed": True,
                    "report_path": str(tmp_path / "candidate-eval-report.json"),
                },
                dataset_split=request.dataset_split,
            )

    replay_backend = FakeReplayBackend()
    evaluation_backend = PairedDatasetBackend()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=evaluation_backend,
        post_apply_evaluator=post_apply,
        min_eval_cases=0,
        replay_enabled=True,
        candidate_replay_backend=replay_backend,
        baseline_replay_repetitions=2,
        candidate_replay_repetitions=3,
        replay_stability_margin=0.1,
    )

    result = await runner.run_explicit_target(
        run_id="run-replay-eval",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "succeeded"
    applied_content = skill_path.read_text(encoding="utf-8")
    assert "release_state: verified" in applied_content
    assert "verified_run_id: run-replay-eval" in applied_content
    assert "# Demo\n\nReplay verified guidance." in applied_content
    assert replay_backend.requests
    assert replay_backend.requests[0].baseline_repetitions == 2
    assert replay_backend.requests[0].candidate_repetitions == 3
    assert Path(replay_backend.requests[0].overlay_skill_root, "demo", "SKILL.md").read_text(encoding="utf-8") == candidate_content
    assert Path(replay_backend.requests[0].overlay_skill_root, "helper", "SKILL.md").exists()
    assert all(
        request.dataset.cases[0].metadata["variant_trajectories"]
        for request in evaluation_backend.requests
    )
    assert len(evaluation_backend.requests[0].dataset.cases) == 3
    report = json.loads((tmp_path / ".aworld" / "self_evolve" / "run-replay-eval" / "report.json").read_text())
    assert report["replay"]["candidate"]["status"] == "succeeded"
    assert report["replay"]["overlay_skill_root"] == replay_backend.requests[0].overlay_skill_root
    assert "/run-replay-eval/replay/llm-mutator-" in report["replay_path"]
    assert str(tmp_path / "candidate-eval-report.json") in report["evaluator_report_paths"]
    assert any(
        gate["gate_name"] == "candidate_replay" and gate["passed"] is True
        for gate in report["gate_results"]
    )
    assert any(
        gate["gate_name"] == "replay_stability_margin"
        and gate["passed"] is True
        and gate["details"]["delta"] == 0.5
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_runner_auto_verified_accepts_stable_single_case_replay(
    tmp_path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="single-replay-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="single-replay-task",
    )
    candidate_content = "---\nname: demo\n---\n# Demo\n\nStable single-case replay guidance.\n"

    async def mutate(prompt: str) -> dict:
        return {"content": candidate_content, "rationale": "Stable replay candidate."}

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={"post_apply_passed": True, "deterministic_signal": True},
            dataset_split="post_apply",
        )

    class StableReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[
                        {"state": {"input": request.task_input}, "action": {"content": "old"}}
                    ],
                    metrics={
                        "score": 0.3,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "repetition_count": request.baseline_repetitions,
                        "successful_repetition_count": request.baseline_repetitions,
                    },
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[
                        {"state": {"input": request.task_input}, "action": {"content": "new"}}
                    ],
                    metrics={
                        "score": 0.9,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "repetition_count": request.candidate_repetitions,
                        "successful_repetition_count": request.candidate_repetitions,
                    },
                ),
            )

    evaluation_calls = []

    class PositiveReplayEvaluationBackend:
        async def evaluate_variant(self, request):
            evaluation_calls.append((request.variant_id, request.dataset_split))
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={
                        "score": 0.3,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "deterministic_signal": True,
                        "command_case_count": 1,
                        "command_pass_count": 1,
                        "global_regression_passed": True,
                    },
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 0.9,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=PositiveReplayEvaluationBackend(),
        post_apply_evaluator=post_apply,
        min_eval_cases=30,
        replay_enabled=True,
        candidate_replay_backend=StableReplayBackend(),
        baseline_replay_repetitions=2,
        candidate_replay_repetitions=3,
    )

    result = await runner.run_explicit_target(
        run_id="run-single-case-replay-verified",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "succeeded"
    assert skill_path.read_text(encoding="utf-8") != original_content
    report = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-single-case-replay-verified"
            / "report.json"
        ).read_text()
    )
    assert report["post_apply"]["status"] == "accepted"
    assert any(
        gate["gate_name"] == "held_out_verification"
        and gate["passed"] is True
        and gate["details"]["verification_mode"] == "single_case_replay"
        for gate in report["gate_results"]
    )
    assert evaluation_calls == [
        ("baseline", "validation"),
        (report["selected_candidate_id"], "validation"),
    ]


@pytest.mark.asyncio
async def test_runner_auto_verified_rejects_compacted_evaluator_evidence(
    tmp_path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="compacted-evidence-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="compacted-evidence-task",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nCandidate guidance.\n",
            "rationale": "Candidate with compacted evidence.",
        }

    async def post_apply(candidate):
        pytest.fail("compacted evaluator evidence must not auto apply")

    class ReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            baseline_repetitions = tuple(
                ReplayVariantResult(
                    variant_id=f"baseline-{index}",
                    status="succeeded",
                    trajectory=[{"action": {"content": f"old-{index}"}}],
                )
                for index in range(1, request.baseline_repetitions + 1)
            )
            candidate_repetitions = tuple(
                ReplayVariantResult(
                    variant_id=f"{candidate.candidate_id}-{index}",
                    status="succeeded",
                    trajectory=[{"action": {"content": f"new-{index}"}}],
                )
                for index in range(1, request.candidate_repetitions + 1)
            )
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=baseline_repetitions[-1].trajectory,
                    metrics={
                        "repetition_count": len(baseline_repetitions),
                        "successful_repetition_count": len(baseline_repetitions),
                    },
                    repetition_results=baseline_repetitions,
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=candidate_repetitions[-1].trajectory,
                    metrics={
                        "repetition_count": len(candidate_repetitions),
                        "successful_repetition_count": len(candidate_repetitions),
                    },
                    repetition_results=candidate_repetitions,
                ),
            )

    class CompactedEvidenceBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={
                        "score": 0.2,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "deterministic_signal": True,
                        "command_case_count": len(request.dataset.cases),
                        "command_pass_count": len(request.dataset.cases),
                        "global_regression_passed": True,
                        "has_evidence": 1.0,
                        "evidence_block_count": 1,
                        "evidence_compacted": False,
                    },
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 0.9,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": len(request.dataset.cases),
                    "command_pass_count": len(request.dataset.cases),
                    "global_regression_passed": True,
                    "has_evidence": 1.0,
                    "evidence_block_count": 1,
                    "evidence_compacted": True,
                },
                dataset_split=request.dataset_split,
            )

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=CompactedEvidenceBackend(),
        post_apply_evaluator=post_apply,
        min_eval_cases=30,
        replay_enabled=True,
        candidate_replay_backend=ReplayBackend(),
        baseline_replay_repetitions=2,
        candidate_replay_repetitions=3,
    )

    result = await runner.run_explicit_target(
        run_id="run-compacted-evidence",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-compacted-evidence"
            / "report.json"
        ).read_text()
    )
    assert any(
        gate["gate_name"] == "evidence_quality"
        and gate["passed"] is False
        and gate["reason"] == "evaluation evidence is compacted or incomplete"
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_runner_evidence_quality_rejects_unverifiable_replay_artifact_manifest(
    tmp_path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Ground web evidence."}},
            "action": {"content": "Evidence was compacted."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="artifact-evidence",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="artifact-evidence",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nCandidate guidance.\n",
            "rationale": "Candidate with artifact-first evidence.",
        }

    async def post_apply(candidate):
        pytest.fail("score regression should stop auto apply before post-apply")

    class ReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "old"}}],
                    metrics={
                        "repetition_count": 2,
                        "successful_repetition_count": 2,
                        "evidence_strategy_passed": True,
                        "evidence_manifest_entry_count": 1,
                        "evidence_manifest_invalid_entry_count": 1,
                    },
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[{"action": {"content": "new"}}],
                    metrics={
                        "repetition_count": 3,
                        "successful_repetition_count": 3,
                        "evidence_strategy_passed": True,
                        "evidence_manifest_entry_count": 2,
                        "evidence_manifest_invalid_entry_count": 1,
                    },
                ),
            )

    class CompactedButManifestedEvaluator:
        async def evaluate_variant(self, request):
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={
                    "score": 0.8 if request.candidate is None else 0.7,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": len(request.dataset.cases),
                    "command_pass_count": len(request.dataset.cases),
                    "global_regression_passed": True,
                    "has_evidence": 1.0,
                    "evidence_block_count": 1,
                    "evidence_compacted": True,
                    "evidence_incomplete": True,
                },
                dataset_split=request.dataset_split,
            )

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=CompactedButManifestedEvaluator(),
        post_apply_evaluator=post_apply,
        min_eval_cases=30,
        replay_enabled=True,
        candidate_replay_backend=ReplayBackend(),
        baseline_replay_repetitions=2,
        candidate_replay_repetitions=3,
    )

    result = await runner.run_explicit_target(
        run_id="run-artifact-evidence",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    report = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-artifact-evidence"
            / "report.json"
        ).read_text()
    )
    evidence_gates = [
        gate for gate in report["gate_results"] if gate["gate_name"] == "evidence_quality"
    ]
    assert evidence_gates
    assert all(gate["passed"] is False for gate in evidence_gates)
    assert {gate["reason"] for gate in evidence_gates} == {
        "artifact-first evidence is not fully verifiable"
    }
    assert "score_improvement" in report["iterations"][0]["failed_gates"]
    assert "evidence_quality" in report["iterations"][0]["failed_gates"]


@pytest.mark.asyncio
async def test_runner_auto_verified_rejects_single_candidate_rerun_without_baseline_rerun(
    tmp_path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="limited-replay")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="limited-replay",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nCandidate guidance.\n",
            "rationale": "Limited replay candidate.",
        }

    async def post_apply(candidate):
        pytest.fail("limited confidence replay must not apply")

    class LimitedReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "historical baseline"}}],
                    metrics={"replay_source": "historical", "score": 0.2},
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[{"action": {"content": "candidate rerun"}}],
                    metrics={"repetition_count": 1, "score": 0.9},
                ),
            )

    class PositiveBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={
                        "score": 0.2,
                        "latency_ms": 100.0,
                        "cost_usd": 1.0,
                        "deterministic_signal": True,
                        "command_case_count": 1,
                        "command_pass_count": 1,
                        "global_regression_passed": True,
                    },
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 0.9,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=PositiveBackend(),
        post_apply_evaluator=post_apply,
        min_eval_cases=0,
        replay_enabled=True,
        candidate_replay_backend=LimitedReplayBackend(),
    )

    result = await runner.run_explicit_target(
        run_id="run-limited-replay",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads((tmp_path / ".aworld" / "self_evolve" / "run-limited-replay" / "report.json").read_text())
    assert any(
        gate["gate_name"] == "replay_confidence"
        and gate["passed"] is False
        and gate["reason"] == "fixed historical baseline plus one candidate rerun is limited confidence"
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_runner_auto_verified_rejects_when_candidate_successful_replays_are_low(
    tmp_path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="partial-replay-success",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="partial-replay-success",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nCandidate guidance.\n",
            "rationale": "Partial replay candidate.",
        }

    async def post_apply(candidate):
        pytest.fail("low successful replay count must not apply")

    class PartialReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "baseline"}}],
                    metrics={
                        "repetition_count": 2,
                        "successful_repetition_count": 2,
                        "failed_repetition_count": 0,
                    },
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=[{"action": {"content": "candidate"}}],
                    metrics={
                        "repetition_count": 3,
                        "successful_repetition_count": 1,
                        "failed_repetition_count": 2,
                        "repetition_failures": [
                            {"type": "TimeoutExpired", "reason": "replay timed out"},
                            {"type": "TimeoutExpired", "reason": "replay timed out"},
                        ],
                    },
                ),
            )

    class PositiveBackend:
        async def evaluate_variant(self, request):
            return EvaluationSummary(
                variant_id=request.variant_id,
                metrics={
                    "score": 0.9 if request.candidate else 0.2,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=PositiveBackend(),
        post_apply_evaluator=post_apply,
        min_eval_cases=0,
        replay_enabled=True,
        candidate_replay_backend=PartialReplayBackend(),
    )

    result = await runner.run_explicit_target(
        run_id="run-partial-replay-success",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "rejected"
    report = json.loads(
        (
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "run-partial-replay-success"
            / "report.json"
        ).read_text(encoding="utf-8")
    )
    assert any(
        gate["gate_name"] == "replay_confidence"
        and gate["passed"] is False
        and gate["reason"] == "candidate replay successful repetitions are insufficient"
        and gate["details"]["candidate_successful_repetition_count"] == 1
        for gate in report["gate_results"]
    )


def test_default_post_apply_evaluator_requires_runtime_loader_match(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    candidate_content = "---\nname: demo\n---\n# Demo\n\nApplied guidance.\n"
    skill_path.write_text(candidate_content, encoding="utf-8")
    target = SkillTextTarget(skill_path, target_id="missing", allow_auto_apply=True)
    candidate = CandidateVariant(
        candidate_id="cand-1",
        target=SelfEvolveTargetRef(target_type="skill", target_id="missing", path=str(skill_path)),
        content=candidate_content,
        rationale="loader mismatch",
    )

    assert target.load_current_content() == candidate_content

    summary = _default_post_apply_evaluator(target)(candidate)

    assert summary.metrics["post_apply_passed"] is False
    assert summary.metrics["content_matches_target_file"] is True
    assert summary.metrics["runtime_skill_found"] is False
    assert summary.metrics["evaluator_mode"] == "post_apply_runtime_loader"


@pytest.mark.asyncio
async def test_optimize_explicit_target_python_api_uses_framework_runner(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nSDK proposal.\n",
            "rationale": "SDK path candidate.",
        }

    result = await optimize_explicit_target(
        workspace_root=tmp_path,
        run_id="run-sdk",
        target=SkillTextTarget(skill_path),
        current_trajectory=trajectory,
        task_id="sdk-task",
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
    )

    assert result.run.run_id == "run-sdk"
    assert result.run.status.value == "succeeded"
    assert result.selected_candidate is not None
    assert (tmp_path / ".aworld" / "self_evolve" / "run-sdk" / "report.json").exists()


@pytest.mark.asyncio
async def test_runner_orchestrates_baseline_candidate_evaluation_and_gates(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="gate-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="gate-task",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nBetter guidance.\n",
            "rationale": "Improve guidance.",
        }

    class RecordingBackend:
        def __init__(self):
            self.requests = []

        async def evaluate_variant(self, request):
            self.requests.append(request)
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 0.4, "latency_ms": 100.0, "cost_usd": 1.0},
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={"score": 0.8, "latency_ms": 110.0, "cost_usd": 1.0},
                dataset_split=request.dataset_split,
            )

    backend = RecordingBackend()
    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=backend,
    )

    result = await runner.run_explicit_target(
        run_id="run-orchestrated",
        target=SkillTextTarget(skill_path),
        dataset=dataset,
        trace_packs=(trace_pack,),
    )

    assert result.run.status.value == "succeeded"
    assert [request.variant_id for request in backend.requests][0] == "baseline"
    assert backend.requests[1].candidate is result.selected_candidate
    report = json.loads((tmp_path / ".aworld" / "self_evolve" / "run-orchestrated" / "report.json").read_text())
    assert report["baseline_metrics"]["score"] == 0.4
    assert report["candidate_metrics"]["score"] == 0.8
    assert any(
        gate["gate_name"] == "score_improvement"
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_runner_rejects_auto_verified_candidate_when_evaluation_backend_fails(
    tmp_path,
) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(
        trajectory,
        source_kind="current_trajectory",
        task_id="eval-failure-task",
    )
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="eval-failure-task",
    )

    async def mutate(prompt: str) -> dict:
        return {
            "content": "---\nname: demo\n---\n# Demo\n\nBetter guidance.\n",
            "rationale": "Improve guidance.",
        }

    class BrokenBackend:
        async def evaluate_variant(self, request):
            raise ValueError("judge response does not contain a valid JSON object")

    async def post_apply(candidate):
        pytest.fail("evaluation failure must block auto apply")

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        evaluation_backend=BrokenBackend(),
        post_apply_evaluator=post_apply,
    )

    result = await runner.run_explicit_target(
        run_id="run-eval-failure",
        target=SkillTextTarget(skill_path, allow_auto_apply=True),
        dataset=dataset,
        trace_packs=(trace_pack,),
        apply_policy="auto_verified",
    )

    assert result.run.status.value == "failed"
    assert skill_path.read_text(encoding="utf-8") == original_content
    report = json.loads((tmp_path / ".aworld" / "self_evolve" / "run-eval-failure" / "report.json").read_text())
    assert any(
        gate["gate_name"] == "evaluation"
        and gate["passed"] is False
        and gate["details"]["type"] == "ValueError"
        for gate in report["gate_results"]
    )


@pytest.mark.asyncio
async def test_runner_stops_when_duplicate_pending_proposal_exists(tmp_path) -> None:
    skill_path = tmp_path / "skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix guidance."}},
            "action": {"content": "Guidance failed."},
            "reward": {"status": "failed"},
        }
    ]
    trace_pack = build_trace_pack(trajectory, source_kind="current_trajectory", task_id="stop-task")
    dataset = build_dataset_from_source(
        SelfEvolveEvalSourceConfig(kind="current_trajectory"),
        current_trajectory=trajectory,
        task_id="stop-task",
    )

    async def mutate(prompt: str) -> dict:
        pytest.fail("optimizer should not run when stopping conditions block")

    runner = SelfEvolveRunner(
        store=FilesystemSelfEvolveStore(tmp_path),
        optimizer=TraceReflectiveLLMMutator(mutate_text=mutate),
        pending_duplicate=True,
    )

    result = await runner.run_explicit_target(
        run_id="run-stopped",
        target=SkillTextTarget(skill_path),
        dataset=dataset,
        trace_packs=(trace_pack,),
    )

    assert result.run.status.value == "rejected"
    report = json.loads((tmp_path / ".aworld" / "self_evolve" / "run-stopped" / "report.json").read_text())
    assert report["stopping_condition"]["reason"] == "duplicate pending proposal exists"


def test_optimize_cli_request_infers_skill_target_from_trajectory_log(tmp_path) -> None:
    from aworld.self_evolve import optimize_from_cli_request

    skill_path = tmp_path / "aworld-skills" / "agent-browser" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = (
        "---\n"
        "name: agent-browser\n"
        "---\n"
        "# Browser Automation\n\n"
        "Keep existing guidance.\n"
    )
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {
                "input": {
                    "content": (
                        "I am definitely logged in. Why do you keep seeing a "
                        "logged-out browser?"
                    )
                },
                "messages": [],
            },
            "action": {
                "content": "I will inspect browser login traces and Chrome profiles.",
                "tool_calls": [],
                "is_agent_finished": False,
            },
            "reward": {"status": "failed"},
        },
        {
            "meta": {"step": 2, "agent_id": "agent", "pre_agent": "agent"},
            "state": {"messages": []},
            "action": {
                "content": "No login traces were found in the checked browser sessions.",
                "tool_calls": [],
                "is_agent_finished": True,
            },
            "reward": {"status": "failed"},
        },
    ]
    trajectory_log = tmp_path / "trajectory.log"
    trajectory_log.write_text(
        repr(
            {
                "task_id": "browser-login-task",
                "is_sub_task": False,
                "trajectory": json.dumps(trajectory),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        task="fix browser login",
        target=None,
        from_trajectory=str(trajectory_log),
        apply_policy="proposal",
        infer_target=True,
    )

    assert report_summary["status"] == "succeeded"
    assert skill_path.read_text(encoding="utf-8") == original_content

    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["target"]["target_type"] == "skill"
    assert report["target"]["target_id"] == "agent-browser"
    assert report["target_selection"]["confidence"] >= 0.8
    assert report["target_selection"]["evidence_step_ids"]
    assert report["trajectory_set"]["source_kind"] == "trajectory_log"
    assert report["trajectory_set"]["member_roles"] == {"baseline": 1}
    assert report["trajectory_set"]["case_count"] == 1
    assert report["candidate_ids"]
    assert report["selected_candidate_id"] == report["candidate_ids"][0]

    target_selection_path = Path(report_summary["target_selection_path"])
    target_selection = json.loads(target_selection_path.read_text(encoding="utf-8"))
    assert target_selection["selected_target"]["target_id"] == "agent-browser"
    assert target_selection["failure_category"] == "skill"

    candidate_path = (
        Path(report_summary["report_path"]).parent
        / "candidates"
        / f"{report['selected_candidate_id']}.md"
    )
    candidate_content = candidate_path.read_text(encoding="utf-8")
    assert candidate_content.startswith("---\nname: agent-browser\nself_evolve:")
    assert "release_state: candidate" in candidate_content
    assert "Runtime Behavior Delta" in candidate_content
    assert "After one failed tool or evidence path" in candidate_content
    assert "browser-login-task" not in candidate_content
    assert "Self-Evolve Trace Guidance" not in candidate_content


def test_optimize_cli_request_uses_model_generated_candidate_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import aworld.self_evolve.runner as runner_module

    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {
                "input": {"content": "Summarize https://example.com/report"},
                "messages": [],
            },
            "action": {"content": "The live dependency was unavailable."},
            "reward": {"status": "failed"},
        }
    ]
    trajectory_log = tmp_path / "trajectory.log"
    trajectory_log.write_text(
        repr({"task_id": "demo-task", "trajectory": json.dumps(trajectory)}) + "\n",
        encoding="utf-8",
    )
    model_calls: list[tuple[CandidateGenerationAgent, str]] = []
    manifest = {
        "schema_version": "aworld.skill.replay_capability.v1",
        "capability_id": "recorded-http",
        "protocol": "aworld.replay.subprocess.v1",
        "entrypoint": "replay/compiler.py",
        "handles": ["http_resource"],
    }
    model_output = {
        "content": "---\nname: demo\n---\n# Demo\n\nUse recorded replay inputs.\n",
        "rationale": "Add a skill-owned deterministic replay capability.",
        "files": [
            {
                "path": "replay/capability.json",
                "content": json.dumps(manifest, sort_keys=True),
            },
            {
                "path": "replay/compiler.py",
                "content": "print('compile recorded input')\n",
                "executable": True,
            },
        ],
    }
    original_run_task = Runners.run_task

    async def fake_run_task(task, run_conf=None):
        if task.runner_cls == "aworld.self_evolve.runtime.SelfEvolveTaskRunner":
            return await original_run_task(task, run_conf=run_conf)
        agent = task.agent
        prompt = task.input
        model_calls.append((agent, prompt))
        if len(model_calls) == 1:
            answer = json.dumps(
                {
                    "content": model_output["content"],
                    "rationale": "Invalid package path must trigger repair.",
                    "files": [{"path": "../escape.py", "content": "bad"}],
                }
            )
        else:
            answer = "```json\n" + json.dumps(model_output) + "\n```"
        return {
            task.id: TaskResponse(
                id=task.id,
                success=True,
                status=TaskStatusValue.SUCCESS,
                answer=answer,
            )
        }

    monkeypatch.setattr(
        Runners,
        "run_task",
        fake_run_task,
    )
    mutation_model_config = ModelConfig(
        llm_provider="openai",
        llm_model_name="mutation-model",
        llm_api_key="test-key",
    )

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        target="skill:demo",
        from_trajectory=str(trajectory_log),
        apply_policy="proposal",
        mutation_model_config=mutation_model_config,
        replay_candidate_limit=1,
    )

    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    candidate_id = report["selected_candidate_id"]
    candidate_package = Path(report_summary["report_path"]).parent / "candidates" / candidate_id
    candidate_json = json.loads(
        (candidate_package / "candidate.json").read_text(encoding="utf-8")
    )
    assert len(model_calls) == 2
    assert all(call[0] is model_calls[0][0] for call in model_calls)
    assert model_calls[0][0].conf.llm_config.llm_model_name == "mutation-model"
    assert '"capability_requirements"' in model_calls[0][1]
    assert "Repair representation only" in model_calls[1][1]
    assert "invalid_response" in model_calls[1][1]
    assert [item["path"] for item in candidate_json["files"]] == [
        "replay/capability.json",
        "replay/compiler.py",
    ]
    assert (candidate_package / "replay" / "capability.json").is_file()
    assert (candidate_package / "replay" / "compiler.py").is_file()


def test_optimize_cli_request_stops_population_after_model_runtime_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import aworld.self_evolve.runner as runner_module

    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    trajectory_log = tmp_path / "trajectory.log"
    trajectory_log.write_text(
        repr(
            {
                "task_id": "demo-task",
                "trajectory": json.dumps(
                    [
                        {
                            "meta": {"step": 1, "agent_id": "agent"},
                            "state": {"input": {"content": "Summarize a report"}},
                            "action": {"content": "The dependency was unavailable."},
                            "reward": {"status": "failed"},
                        }
                    ]
                ),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    model_call_count = 0
    original_run_task = Runners.run_task

    async def fake_run_task(task, run_conf=None):
        nonlocal model_call_count
        if task.runner_cls == "aworld.self_evolve.runtime.SelfEvolveTaskRunner":
            return await original_run_task(task, run_conf=run_conf)
        model_call_count += 1
        task.agent._task_failures[task.id] = {
            "code": "candidate_generation_infrastructure_error",
            "stage": "model_call",
            "error_type": "RuntimeError",
        }
        raise RuntimeError("Authorization: Bearer should-not-leak")

    monkeypatch.setattr(
        Runners,
        "run_task",
        fake_run_task,
    )

    summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        target="skill:demo",
        from_trajectory=str(trajectory_log),
        apply_policy="proposal",
        mutation_model_config=ModelConfig(
            llm_provider="openai",
            llm_model_name="mutation-model",
            llm_api_key="test-key",
        ),
        replay_candidate_limit=3,
    )

    report_text = Path(summary["report_path"]).read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert summary["status"] == "failed"
    assert report["status"] == "failed"
    assert report["terminal_cause"] == {
        "failure_class": "infrastructure",
        "stage": "candidate_generation",
        "code": "candidate_generation_infrastructure_error",
        "error_type": "RuntimeError",
    }
    assert 1 <= model_call_count <= 2
    assert report["optimizer_diagnostics"]["candidate_generation_failure"] == {
        "code": "candidate_generation_infrastructure_error",
        "stage": "model_call",
        "error_type": "RuntimeError",
    }
    assert "should-not-leak" not in report_text


def test_candidate_model_parser_rejects_invalid_patch_intent_before_optimizer() -> None:
    with pytest.raises(ValueError, match="patch_intent.operations"):
        _parse_candidate_mutation_model_output(
            {
                "patch_intent": {},
                "rationale": "invalid patch",
                "files": [],
            },
            current_content="---\nname: demo\n---\n# Demo\n",
        )


def test_default_cli_skill_candidate_ignores_non_actionable_iteration_feedback() -> None:
    current_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    prompt = (
        "Propose one concise text-only self-evolve candidate.\n"
        + json.dumps(
            {
                "validation_feedback": [
                    {
                        "variant_id": "candidate-1",
                        "dataset_split": "held_out",
                        "metrics": {
                            "score": 68.0,
                            "failed_gates": [
                                "held_out_verification",
                                "global_regression_benchmark",
                            ],
                        },
                    }
                ]
            }
        )
    )

    candidate_content = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt,
    )

    assert candidate_content == current_content


def test_default_cli_skill_candidate_materializes_runtime_only_behavior_delta() -> None:
    current_content = "---\nname: demo\n---\n# Demo\n\nExisting runtime rules.\n"
    prompt = (
        "Propose one concise text-only self-evolve candidate.\n"
        + json.dumps(
            {
                "prior_feedback": [
                    {
                        "feedback_summary": {
                            "variant_id": "candidate-internal-id",
                            "dataset_split": "historical",
                            "metrics": {
                                "score": 42.0,
                                "failed_gates": ["evidence_quality"],
                                "evidence_compacted": True,
                            },
                            "required_behaviors": [
                                "artifact_first",
                                "bounded_structured_summary",
                                "claim_by_claim_verification",
                            ],
                            "repair_plan": {
                                "issues": ["replay_evidence_quality_failure"],
                                "actions": ["persist_evidence_before_inspection"],
                                "acceptance_criteria": [
                                    "evidence_manifest_has_bounded_payload"
                                ],
                            },
                        }
                    }
                ]
            }
        )
    )

    candidate_content = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt,
    )

    assert "## Runtime Behavior Delta" in candidate_content
    assert "Persist large or unknown-size evidence" in candidate_content
    assert "bounded structured extracts" in candidate_content
    assert "Self-Evolve" not in candidate_content
    assert "candidate-internal-id" not in candidate_content
    assert "evidence_quality" not in candidate_content
    assert "score=42.0" not in candidate_content
    assert "Previous validation feedback" not in candidate_content


def test_default_cli_skill_candidate_uses_feedback_behavior_without_internal_ids() -> None:
    current_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    prompt = (
        "Propose one concise text-only self-evolve candidate.\n"
        + json.dumps(
            {
                "prior_feedback": [
                    {
                        "feedback_summary": {
                            "variant_id": "latest-candidate",
                            "dataset_split": "historical",
                            "metrics": {
                                "score": 68.0,
                                "baseline_score": 72.5,
                                "candidate_score": 68.0,
                                "score_delta": -4.5,
                            },
                            "failed_gates": ["score_improvement"],
                            "required_behaviors": [
                                "plan_before_tools"
                            ],
                        }
                    },
                    {
                        "feedback_summary": {
                            "variant_id": "older-candidate",
                            "dataset_split": "historical",
                            "metrics": {"score": 0.0},
                            "failed_gates": ["required_verification"],
                        }
                    },
                    {
                        "feedback_summary": {
                            "variant_id": "oldest-candidate",
                            "dataset_split": "historical",
                            "metrics": {"score": 0.0},
                            "failed_gates": ["judge_only_signal"],
                        }
                    },
                    {
                        "feedback_summary": {
                            "variant_id": "stale-candidate",
                            "dataset_split": "historical",
                            "metrics": {"score": 0.0},
                            "failed_gates": ["global_regression_benchmark"],
                        }
                    },
                ]
            }
        )
    )

    candidate_content = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt,
    )

    assert "Plan the shortest viable evidence path" in candidate_content
    assert "latest-candidate" not in candidate_content
    assert "stale-candidate" not in candidate_content
    assert "score_improvement" not in candidate_content


def test_default_cli_skill_candidate_turns_compacted_evidence_feedback_into_preservation_guidance() -> None:
    current_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    prompt = (
        "Propose one concise text-only self-evolve candidate.\n"
        + json.dumps(
            {
                "prior_feedback": [
                    {
                        "variant_id": "candidate-1",
                        "dataset_split": "historical",
                        "metrics": {
                            "score": 45.0,
                            "failed_gates": ["evidence_quality"],
                            "evidence_compacted": True,
                            "evidence_incomplete": True,
                            "evidence_issues": [
                                "tool output compacted for context reuse",
                                "final answer claims not verifiable from available evidence",
                            ],
                        },
                    }
                ]
            }
        )
    )

    candidate_content = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt,
    )

    assert "## Runtime Behavior Delta" in candidate_content
    assert "Persist large or unknown-size evidence" in candidate_content
    assert "bounded structured extracts" in candidate_content
    assert "valid artifact-backed evidence bundle" in candidate_content
    assert "Treat compacted, truncated" not in candidate_content
    assert "curl" not in candidate_content.lower()
    assert "evidence_compacted" not in candidate_content
    assert "candidate-1" not in candidate_content


def test_default_cli_skill_candidate_turns_scope_regression_feedback_into_generic_guidance() -> None:
    current_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    prompt = (
        "Propose one concise text-only self-evolve candidate.\n"
        + json.dumps(
            {
                "prior_feedback": [
                    {
                        "feedback_summary": {
                            "variant_id": "candidate-1",
                            "dataset_split": "historical",
                            "metrics": {
                                "score": 60.6,
                                "baseline_score": 61.3,
                                "candidate_score": 60.6,
                                "score_delta": -0.7,
                                "baseline_latency_ms": 218_595,
                                "candidate_latency_ms": 348_558,
                                "latency_ms_delta": 129_963,
                                "B2_efficiency": 2.3,
                                "B4_robustness": 2.7,
                            },
                            "failed_gates": ["score_improvement"],
                            "required_behaviors": [
                                "reduce_answer_scope_to_verified_claims",
                                "prefer_fewer_verified_claims_over_broad_synthesis",
                                "optimize_verifiability_per_evidence_block",
                                "avoid_collecting_more_evidence_without_verifiability_gain",
                                "cap_evidence_acquisition_and_summarization_cost",
                                "plan_before_tools",
                                "minimize_failed_attempts",
                                "avoid_repeated_paths",
                                "stop_after_sufficient_evidence",
                            ],
                        }
                    }
                ]
            }
        )
    )

    candidate_content = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt,
    )

    assert "## Runtime Behavior Delta" in candidate_content
    assert "Plan the shortest viable evidence path" in candidate_content
    assert "do not broaden the synthesis" in candidate_content
    assert "without a verifiability gain" in candidate_content
    assert "curl" not in candidate_content.lower()
    assert "podcast" not in candidate_content.lower()
    assert "B2_efficiency" not in candidate_content


def test_default_cli_skill_candidate_generates_targeted_delta_for_high_baseline_regression() -> None:
    current_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    prompt = (
        "Propose one concise text-only self-evolve candidate.\n"
        + json.dumps(
            {
                "prior_feedback": [
                    {
                        "feedback_summary": {
                            "variant_id": "candidate-1",
                            "dataset_split": "historical",
                            "metrics": {
                                "score": 88.0,
                                "baseline_score": 89.5,
                                "candidate_score": 88.0,
                                "score_delta": -1.5,
                                "B2_efficiency": 3.5,
                                "B3_compliance": 4.0,
                            },
                            "failed_gates": ["score_improvement"],
                            "required_behaviors": [
                                "differentiate_from_high_scoring_baseline",
                                "preserve_baseline_strengths",
                                "define_behavior_delta_before_tools",
                                "prefer_targeted_changes_over_broad_rewrites",
                            ],
                        }
                    }
                ]
            }
        )
    )

    candidate_content = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt,
    )

    assert candidate_content != current_content
    assert "## Runtime Behavior Delta" in candidate_content
    assert "Preserve the existing successful workflow" in candidate_content
    assert "smallest repair" in candidate_content
    assert "Self-Evolve" not in candidate_content
    assert "candidate_score" not in candidate_content
    assert "A1_groundedness" not in candidate_content
    assert "candidate-1" not in candidate_content


def test_default_cli_skill_candidate_uses_efficiency_delta_for_high_baseline_score_only_regression() -> None:
    current_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    prompt = (
        "Propose one concise text-only self-evolve candidate.\n"
        + json.dumps(
            {
                "population_strategy": {"name": "score_dimension_repair_delta"},
                "prior_feedback": [
                    {
                        "feedback_summary": {
                            "variant_id": "candidate-1",
                            "dataset_split": "historical",
                            "metrics": {
                                "score": 87.3,
                                "baseline_score": 88.0,
                                "candidate_score": 87.3,
                                "score_delta": -0.7,
                                "baseline_A1_groundedness": 4.7,
                                "candidate_A1_groundedness": 4.3,
                                "A1_groundedness_delta": -0.4,
                                "baseline_B2_efficiency": 3.3,
                                "candidate_B2_efficiency": 3.3,
                                "B2_efficiency_delta": 0.0,
                            },
                            "failed_gates": ["score_improvement"],
                            "required_behaviors": [
                                "differentiate_from_high_scoring_baseline",
                                "preserve_baseline_strengths",
                                "define_behavior_delta_before_tools",
                                "use_efficiency_delta_for_high_baseline",
                                "preserve_claim_set_and_source_links",
                                "do_not_add_verification_steps_without_score_gain",
                            ],
                            "repair_plan": {
                                "issues": [
                                    "score_or_efficiency_regression",
                                    "high_baseline_without_efficiency_gain",
                                    "dimension_regression",
                                ],
                                "actions": [
                                    "define_candidate_behavior_delta",
                                    "replace_broad_validation_with_efficiency_delta",
                                    "restore_A1_groundedness",
                                ],
                                "acceptance_criteria": [
                                    "candidate_score_exceeds_baseline_score",
                                    "candidate_uses_no_more_steps_than_baseline",
                                    "candidate_groundedness_is_no_worse_than_baseline",
                                ],
                            },
                        }
                    }
                ],
            }
        )
    )

    candidate_content = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt,
    )

    assert "Preserve the supported claim set" in candidate_content
    assert "using no more tool or evidence steps" in candidate_content
    assert "do not add broad comparison passes" in candidate_content
    assert "candidate_uses_no_more_steps_than_baseline" not in candidate_content
    assert "A1_groundedness" not in candidate_content
    assert "curl" not in candidate_content.lower()
    assert "podcast" not in candidate_content.lower()


def test_default_cli_skill_candidate_uses_population_strategy_for_distinct_fallbacks() -> None:
    current_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"

    def prompt_for(strategy_name: str) -> str:
        return (
            "Propose one concise text-only self-evolve candidate.\n"
            + json.dumps(
                {
                    "population_strategy": {"name": strategy_name},
                    "prior_feedback": [
                        {
                            "feedback_summary": {
                                "variant_id": "candidate-1",
                                "dataset_split": "historical",
                                "metrics": {
                                    "score": 85.0,
                                    "baseline_score": 90.0,
                                    "candidate_score": 85.0,
                                    "score_delta": -5.0,
                                    "A1_groundedness_delta": -1.0,
                                },
                                "failed_gates": [
                                    "score_improvement",
                                    "evidence_quality",
                                ],
                                "repair_plan": {
                                    "issues": [
                                        "invalid_evidence_manifest",
                                        "dimension_regression",
                                    ],
                                    "actions": [
                                        "write_valid_bounded_evidence_manifest",
                                        "restore_A1_groundedness",
                                    ],
                                    "acceptance_criteria": [
                                        "candidate_score_exceeds_baseline_score"
                                    ],
                                },
                            }
                        }
                    ],
                }
            )
        )

    conservative = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt_for("conservative_preserve_then_delta"),
    )
    evidence = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt_for("evidence_integrity_delta"),
    )
    dimension = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt_for("score_dimension_repair_delta"),
    )

    assert len({conservative, evidence, dimension}) == 3
    assert "Preserve the existing successful workflow" in conservative
    assert "Make evidence integrity the only changed behavior" in evidence
    assert "Restore grounded and complete supported claims" in dimension
    assert "conservative_preserve_then_delta" not in conservative
    assert "evidence_integrity_delta" not in evidence
    assert "score_dimension_repair_delta" not in dimension
    assert "A1_groundedness" not in dimension
    assert "podcast" not in evidence.lower()


def test_default_cli_skill_candidate_turns_repair_plan_into_acceptance_criteria() -> None:
    current_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    prompt = (
        "Propose one concise text-only self-evolve candidate.\n"
        + json.dumps(
            {
                "prior_feedback": [
                    {
                        "feedback_summary": {
                            "variant_id": "candidate-1",
                            "dataset_split": "historical",
                            "metrics": {"score": 62.0},
                            "failed_gates": ["evidence_quality", "score_improvement"],
                            "required_behaviors": [
                                "manifest_schema_compliance",
                                "claim_by_claim_verification",
                            ],
                            "repair_plan": {
                                "priority": "evidence_verifiability",
                                "issues": [
                                    "compacted_or_incomplete_evidence",
                                    "invalid_evidence_manifest",
                                ],
                                "actions": [
                                    "write_valid_bounded_evidence_manifest",
                                    "limit_final_answer_to_supported_claims",
                                ],
                                "acceptance_criteria": [
                                    "all_final_claims_have_non_compacted_support",
                                    "manifest_has_no_invalid_entries",
                                ],
                            },
                        }
                    }
                ]
            }
        )
    )

    candidate_content = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt,
    )

    assert "Validate each evidence manifest entry before finalizing" in candidate_content
    assert "bounded excerpt, structured extract, or source span" in candidate_content
    assert "all_final_claims_have_non_compacted_support" not in candidate_content
    assert "curl" not in candidate_content.lower()
    assert "podcast" not in candidate_content.lower()


def test_default_cli_skill_candidate_turns_replay_failures_into_recovery_rules() -> None:
    current_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    prompt = (
        "Propose one concise text-only self-evolve candidate.\n"
        + json.dumps(
            {
                "prior_feedback": [
                    {
                        "feedback_summary": {
                            "variant_id": "candidate-1",
                            "dataset_split": "historical",
                            "failed_gates": ["evidence_quality"],
                            "repair_plan": {
                                "priority": "evidence_verifiability",
                                "issues": [
                                    "replay_timeout",
                                    "replay_evidence_quality_failure",
                                ],
                                "actions": [
                                    "change_strategy_after_failed_replay",
                                    "do_not_finalize_after_failed_evidence_retry",
                                ],
                                "acceptance_criteria": [
                                    "replay_repetitions_complete_without_evidence_failures",
                                ],
                            },
                        }
                    }
                ]
            }
        )
    )

    candidate_content = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt,
    )

    assert "After one failed tool or evidence path" in candidate_content
    assert "change strategy before retrying" in candidate_content
    assert "do not finalize without a captured result" in candidate_content
    assert "replay_timeout" not in candidate_content
    assert "curl" not in candidate_content.lower()
    assert "podcast" not in candidate_content.lower()


def test_default_cli_skill_candidate_turns_missing_trajectory_capture_into_recovery_rules() -> None:
    current_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    prompt = (
        "Propose one concise text-only self-evolve candidate.\n"
        + json.dumps(
            {
                "prior_feedback": [
                    {
                        "feedback_summary": {
                            "variant_id": "candidate-1",
                            "dataset_split": "historical",
                            "failed_gates": ["candidate_replay", "evidence_quality"],
                            "metrics": {
                                "replay_failure_reasons": [
                                    "trajectory_capture_unavailable"
                                ],
                                "replay_failure_types": [
                                    "trajectory_capture_unavailable"
                                ],
                            },
                            "repair_plan": {
                                "priority": "evidence_verifiability",
                                "issues": [
                                    "replay_trajectory_capture_failure",
                                ],
                                "actions": [
                                    "change_strategy_after_failed_replay",
                                    "ensure_replay_returns_trajectory_evidence",
                                    "do_not_finalize_without_captured_trajectory",
                                ],
                                "acceptance_criteria": [
                                    "replay_repetitions_return_trajectory_evidence",
                                ],
                            },
                        }
                    }
                ]
            }
        )
    )

    candidate_content = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt,
    )

    assert "After one failed tool or evidence path" in candidate_content
    assert "do not finalize without a captured result" in candidate_content
    assert "trajectory_capture_unavailable" not in candidate_content
    assert "curl" not in candidate_content.lower()
    assert "podcast" not in candidate_content.lower()


def test_default_cli_skill_candidate_turns_compacted_tool_arguments_into_recovery_rules() -> None:
    current_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    prompt = (
        "Propose one concise text-only self-evolve candidate.\n"
        + json.dumps(
            {
                "prior_feedback": [
                    {
                        "feedback_summary": {
                            "variant_id": "candidate-1",
                            "dataset_split": "historical",
                            "failed_gates": ["candidate_replay"],
                            "metrics": {
                                "replay_failure_reasons": [
                                    "tool call argument field command contains compacted_string_field",
                                    "tool schema rejected invalid tool argument",
                                ],
                                "replay_failure_types": [
                                    "compacted_tool_argument_replayed",
                                    "invalid_tool_argument",
                                ],
                            },
                            "required_behaviors": [
                                "avoid_compacted_tool_arguments",
                                "regenerate_schema_valid_tool_arguments",
                                "stop_repeating_invalid_tool_calls",
                                "switch_to_artifact_read_after_invalid_tool_argument",
                            ],
                            "repair_plan": {
                                "priority": "score_and_efficiency",
                                "issues": ["compacted_tool_argument_replay"],
                                "actions": [
                                    "regenerate_compacted_tool_arguments",
                                    "switch_to_artifact_read_after_invalid_tool_argument",
                                    "stop_repeating_invalid_tool_calls",
                                ],
                                "acceptance_criteria": [
                                    "tool_arguments_are_schema_valid_and_non_compacted",
                                ],
                            },
                        }
                    }
                ]
            }
        )
    )

    candidate_content = _default_cli_skill_candidate(
        current_content=current_content,
        trace_packs=(),
        mutation_prompt=prompt,
    )

    assert "regenerate the smallest schema-valid arguments" in candidate_content
    assert "current task or a saved artifact" in candidate_content
    assert "never execute compacted placeholders" in candidate_content
    assert "compacted_tool_argument_replay" not in candidate_content
    assert "curl" not in candidate_content.lower()
    assert "podcast" not in candidate_content.lower()


def test_proposal_inferred_target_can_preserve_generated_skill_draft(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import aworld.self_evolve.runner as runner_module

    skill_path = tmp_path / "aworld-skills" / "agent-browser" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: agent-browser\n---\n# Browser\n\nOriginal guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {
                "input": {
                    "content": (
                        "Summarize this podcast page with grounded evidence: "
                        "https://www.xiaoyuzhoufm.com/episode/6a26b911b30e1571aea2c09d"
                    )
                }
            },
            "action": {"content": "The final answer drifted away from the podcast evidence."},
            "reward": {"status": "failed"},
        }
    ]
    draft_skill_path = (
        tmp_path
        / ".aworld"
        / "self_evolve"
        / "drafts"
        / "skills"
        / "generated-capability"
        / "SKILL.md"
    )
    new_skill_path = tmp_path / "aworld-skills" / "generated-capability" / "SKILL.md"
    inferred_target = SelfEvolveTargetRef(
        target_type="skill",
        target_id="generated-capability",
        path=str(draft_skill_path),
    )

    def fake_infer_target_from_trace_packs(trace_packs, *, workspace_root):
        return build_target_selection_decision(
            TargetSelectionReport(
                selected_target=inferred_target,
                confidence=0.85,
                evidence_step_ids=("weak-task:step-1",),
                failure_category="skill",
                signals=("low_confidence", "new_skill_candidate"),
                diagnostics={"rationale": "task needs a dedicated grounded web-summary skill"},
            ),
            inventory=TargetInventory(entries=()),
            selection_origin="inferred",
        )

    class FakeReplayBackend:
        def __init__(self):
            self.requests = []

        async def replay_candidate(self, request, *, candidate, dataset):
            self.requests.append(request)
            baseline_repetitions = tuple(
                ReplayVariantResult(
                    variant_id=f"baseline-{index}",
                    status="succeeded",
                    trajectory=[{"action": {"content": f"baseline-{index}"}}],
                )
                for index in range(1, request.baseline_repetitions + 1)
            )
            candidate_repetitions = tuple(
                ReplayVariantResult(
                    variant_id=f"{candidate.candidate_id}-{index}",
                    status="succeeded",
                    trajectory=[{"action": {"content": f"candidate-{index}"}}],
                )
                for index in range(1, request.candidate_repetitions + 1)
            )
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=baseline_repetitions[-1].trajectory,
                    metrics={
                        "repetition_count": len(baseline_repetitions),
                        "successful_repetition_count": len(baseline_repetitions),
                    },
                    repetition_results=baseline_repetitions,
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="succeeded",
                    trajectory=candidate_repetitions[-1].trajectory,
                    metrics={
                        "repetition_count": len(candidate_repetitions),
                        "successful_repetition_count": len(candidate_repetitions),
                    },
                    repetition_results=candidate_repetitions,
                ),
            )

    class PositiveEvaluationBackend:
        def __init__(self):
            self.requests = []

        async def evaluate_variant(self, request):
            self.requests.append(request)
            score = 0.4 if request.candidate is None else 0.9
            return EvaluationSummary(
                variant_id=request.variant_id,
                dataset_split=request.dataset_split,
                metrics={
                    "score": score,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "global_regression_passed": True,
                    "command_case_count": len(request.dataset.cases),
                    "command_pass_count": len(request.dataset.cases),
                    "report_path": str(tmp_path / f"{request.variant_id}-{request.dataset_split}.json"),
                },
            )

    async def post_apply(candidate):
        return EvaluationSummary(
            variant_id=candidate.candidate_id,
            dataset_split="post_apply",
            metrics={"post_apply_passed": True},
        )

    monkeypatch.setattr(
        runner_module,
        "_infer_target_from_trace_packs",
        fake_infer_target_from_trace_packs,
    )

    replay_backend = FakeReplayBackend()
    evaluation_backend = PositiveEvaluationBackend()

    class RecordedHttpAdapter:
        adapter_id = "test.recorded-http.v1"

        def bind(self, dependency, *, context):
            if dependency.kind != "http_resource":
                return None
            return ReplayAdapterBinding(
                adapter_id=self.adapter_id,
                dependency_id=dependency.identifier,
                deterministic=True,
            )

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        current_trajectory=trajectory,
        task="weak-task",
        target=None,
        apply_policy="proposal",
        infer_target=True,
        candidate_replay_backend=replay_backend,
        evaluation_backend=evaluation_backend,
        post_apply_evaluator=post_apply,
        min_eval_cases=0,
        replay_enabled=True,
        baseline_replay_repetitions=2,
        candidate_replay_repetitions=3,
        replay_adaptation_compiler=ReplayAdaptationCompiler(
            adapters=(RecordedHttpAdapter(),)
        ),
    )

    assert report_summary["status"] == "succeeded"
    assert report_summary["best_candidate_id"] is None
    assert replay_backend.requests
    assert replay_backend.requests[0].baseline_repetitions == 2
    assert replay_backend.requests[0].candidate_repetitions == 3
    assert replay_backend.requests[0].baseline_skill_root is None
    assert Path(
        replay_backend.requests[0].overlay_skill_root,
        "generated-capability",
        "SKILL.md",
    ).exists()
    assert skill_path.read_text(encoding="utf-8") == original_content
    assert [request.dataset_split for request in evaluation_backend.requests] == [
        "validation",
        "validation",
    ]
    assert not new_skill_path.exists()
    assert Path(report_summary["target_provenance_path"]).exists()
    provenance = json.loads(
        Path(report_summary["target_provenance_path"]).read_text(encoding="utf-8")
    )
    assert provenance["trust_level"] == "generated"
    assert provenance["write_origin"] == "target_inference"
    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["apply_policy"] == "proposal"
    assert report["target"]["target_id"] == "generated-capability"
    assert report["target"]["path"] == str(draft_skill_path)
    assert report["candidate_ids"]
    assert report["selected_candidate_id"] == report["candidate_ids"][0]
    assert report["target_selection"]["selected_target"]["target_id"] == "generated-capability"
    assert report["target_selection"]["selected_target"]["path"] == str(draft_skill_path)
    assert report["target_selection"]["confidence"] == 0.85
    assert report["target_selection"]["selection_origin"] == "inferred"
    assert "low_confidence" in report["target_selection"]["signals"]
    assert report["replay"]["baseline"]["status"] == "succeeded"
    assert report["replay"]["candidate"]["status"] == "succeeded"
    assert any(
        gate["gate_name"] == "trust_provenance" and gate["passed"] is False
        for gate in report["gate_results"]
    )
    assert "post_apply" not in report


def test_auto_verified_inferred_target_blocks_low_confidence_auto_apply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import aworld.self_evolve.runner as runner_module

    skill_path = tmp_path / "aworld-skills" / "agent-browser" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: agent-browser\n---\n# Browser\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Fix browser workflow."}},
            "action": {"content": "Browser alias matched, but evidence is weak."},
            "reward": {"status": "failed"},
        }
    ]
    inferred_target = SelfEvolveTargetRef(
        target_type="skill",
        target_id="generated-capability",
        path=str(
            tmp_path
            / ".aworld"
            / "self_evolve"
            / "drafts"
            / "skills"
            / "generated-capability"
            / "SKILL.md"
        ),
    )

    def fake_infer_target_from_trace_packs(trace_packs, *, workspace_root):
        return build_target_selection_decision(
            TargetSelectionReport(
                selected_target=inferred_target,
                confidence=0.85,
                evidence_step_ids=("weak-task:step-1",),
                failure_category="skill",
                signals=("low_confidence", "new_skill_candidate"),
                diagnostics={"draft_skill_reason": "reusable capability gap"},
            ),
            inventory=TargetInventory(entries=()),
            selection_origin="inferred",
        )

    def fail_if_target_is_loaded(*args, **kwargs):
        pytest.fail("low-confidence inferred targets must not be loaded for auto apply")

    monkeypatch.setattr(
        runner_module,
        "_infer_target_from_trace_packs",
        fake_infer_target_from_trace_packs,
    )
    monkeypatch.setattr(runner_module, "_target_from_ref", fail_if_target_is_loaded)

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        current_trajectory=trajectory,
        task="weak-task",
        target=None,
        apply_policy="auto_verified",
        infer_target=True,
    )

    assert report_summary["status"] == "rejected"
    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["target"]["target_type"] == "no_target"
    assert report["target_selection"]["selected_target"] is None
    assert report["target_selection"]["confidence"] == 0.85
    assert report["target_selection"]["no_target_reason"] == (
        "auto_verified target inference requires confidence >= 0.9 without low_confidence signal"
    )
    assert "auto_verified_low_confidence_blocked" in report["target_selection"]["signals"]
    assert report["target_selection"]["diagnostics"]["blocked_selected_target"][
        "target_id"
    ] == "generated-capability"


def test_optimize_cli_request_filters_unsupported_inferred_target_before_adapter(
    tmp_path,
) -> None:
    trajectory_log = tmp_path / "trajectory.log"
    _write_trajectory_log(
        trajectory_log,
        [
            {
                "task_id": "validation-task",
                "trajectory": [
                    {
                        "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                        "state": {
                            "input": {"content": "Validate result anchors before writing."},
                            "messages": [],
                        },
                        "action": {
                            "content": (
                                "Result validation mismatch: required anchors are missing "
                                "from source evidence."
                            ),
                            "tool_calls": [],
                            "is_agent_finished": True,
                        },
                        "reward": {"status": "failed"},
                    }
                ],
            }
        ],
    )

    from aworld.self_evolve import optimize_from_cli_request

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        task="fix validation anchors",
        target=None,
        from_trajectory=str(trajectory_log),
        apply_policy="proposal",
        infer_target=True,
    )

    assert report_summary["status"] == "rejected"
    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["target"]["target_type"] == "no_target"
    assert report["target_selection"]["selected_target"] is None
    assert "unavailable_signaled_target:prompt-section" in report["target_selection"][
        "signals"
    ]
    assert report["target_selection"]["diagnostics"]["unavailable_signaled_target"] == {
        "target_id": "result-validation-anchor-policy",
        "target_type": "prompt-section",
    }
    assert report["candidate_ids"] == []

    assert Path(report_summary["target_selection_path"]).exists()
    assert "target_provenance_path" not in report_summary


def test_optimize_cli_request_infers_highest_confidence_target_from_trajectory_log(tmp_path) -> None:
    skill_path = tmp_path / "aworld-skills" / "agent-browser" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: agent-browser\n---\n# Browser Login Guidance\n",
        encoding="utf-8",
    )
    trajectory_log = tmp_path / "trajectory.log"
    _write_trajectory_log(
        trajectory_log,
        [
            {
                "task_id": "browser-task",
                "trajectory": [
                    {
                        "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                        "state": {
                            "input": {
                                "content": "I am logged in but you see a logged-out browser."
                            }
                        },
                        "action": {"content": "I will inspect login traces."},
                        "reward": {"status": "failed"},
                    }
                ],
            },
            {
                "task_id": "validation-task",
                "trajectory": [
                    {
                        "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                        "state": {"input": {"content": "Validate anchors."}},
                        "action": {
                            "content": "Result validation mismatch: anchors are missing.",
                            "tool_calls": [],
                            "is_agent_finished": True,
                        },
                        "reward": {"status": "failed"},
                    }
                ],
            },
        ],
    )

    from aworld.self_evolve import optimize_from_cli_request

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        task="fix validation anchors",
        target=None,
        from_trajectory=str(trajectory_log),
        apply_policy="proposal",
        infer_target=True,
    )

    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["status"] == "succeeded"
    assert report["target"]["target_type"] == "skill"
    assert report["target"]["target_id"] == "agent-browser"
    assert report["target_selection"]["confidence"] == 0.9


def test_optimize_cli_request_infers_target_from_session_trajectory(tmp_path) -> None:
    skill_path = tmp_path / "aworld-skills" / "agent-browser" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: agent-browser\n---\n# Browser Automation\n",
        encoding="utf-8",
    )
    session_log = tmp_path / ".aworld" / "memory" / "sessions" / "session-1.jsonl"
    session_log.parent.mkdir(parents=True)
    session_log.write_text(
        json.dumps(
            {
                "session_id": "session-1",
                "task_id": "browser-session-task",
                "input": {"content": "I am logged in but you see a logged-out browser."},
                "final_answer": "No login traces were found in browser sessions.",
                "task_status": "failed",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    from aworld.self_evolve import optimize_from_cli_request

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        task="fix browser login",
        target=None,
        from_session="session-1",
        apply_policy="proposal",
        infer_target=True,
    )

    assert report_summary["status"] == "succeeded"
    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["target"]["target_id"] == "agent-browser"
    assert report["target_selection"]["evidence_step_ids"] == [
        "browser-session-task:step-1"
    ]


def test_optimize_cli_request_records_explicit_target_trajectory_evidence(tmp_path) -> None:
    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
    trajectory_log = tmp_path / "trajectory.log"
    _write_trajectory_log(
        trajectory_log,
        [
            {
                "task_id": "explicit-task",
                "trajectory": [
                    {
                        "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                        "state": {"input": {"content": "Fix demo guidance."}},
                        "action": {"content": "Guidance failed."},
                        "reward": {"status": "failed"},
                    }
                ],
            }
        ],
    )

    from aworld.self_evolve import optimize_from_cli_request

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        target="skill:demo",
        from_trajectory=str(trajectory_log),
        apply_policy="proposal",
        infer_target=False,
    )

    assert Path(report_summary["target_selection_path"]).exists()
    assert Path(report_summary["target_provenance_path"]).exists()
    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["target_selection"]["failure_category"] == "explicit_target"
    assert report["target_selection"]["evidence_step_ids"] == ["explicit-task:step-1"]
    assert report["target_selection"]["provenance_status"] == "resolved"
    assert report["target_selection"]["selection_origin"] == "operator_explicit"
    assert report["target_provenance"] == {
        "status": "resolved",
        "path": report_summary["target_provenance_path"],
        "reason": "selected target uses inventory provenance",
    }


def test_optimize_cli_request_uses_framework_default_replay_backend_when_enabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from aworld.self_evolve import optimize_from_cli_request

    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("---\nname: demo\n---\n# Demo\n\nOld guidance.\n", encoding="utf-8")
    trajectory = [
        {
            "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
            "state": {"input": {"content": "Use demo skill for this task."}},
            "action": {"content": "demo failed"},
            "reward": {"status": "failed"},
        }
    ]
    created = {"count": 0}
    replay_agents = []
    replay_max_steps = []

    class FakeDefaultReplayBackend:
        def __init__(self):
            created["count"] += 1

        async def replay_candidate(self, request, *, candidate, dataset):
            from aworld.self_evolve.replay import CandidateReplayResult, ReplayVariantResult

            replay_agents.append(request.agent)
            replay_max_steps.append(request.max_steps)
            return CandidateReplayResult(
                request=request,
                baseline=ReplayVariantResult(
                    variant_id="baseline",
                    status="succeeded",
                    trajectory=[{"action": {"content": "old"}}],
                ),
                candidate=ReplayVariantResult(
                    variant_id=candidate.candidate_id,
                    status="failed",
                    trajectory=[],
                    failure={"reason": "fake replay rejection"},
                ),
            )

    monkeypatch.setattr(
        "aworld.self_evolve.runner.AWorldCliCandidateReplayBackend",
        FakeDefaultReplayBackend,
    )

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        agent="Aworld",
        target="skill:demo",
        current_trajectory=trajectory,
        task="default-replay",
        apply_policy="auto_verified",
        replay_enabled=True,
        min_eval_cases=0,
    )

    assert created["count"] == 1
    assert replay_agents == ["Aworld"]
    assert replay_max_steps == [1]
    assert report_summary["best_candidate_id"] is None
    assert report_summary["selected_candidate_id"] is not None
    assert any(
        gate["gate_name"] == "candidate_replay" and gate["passed"] is False
        for gate in report_summary["gate_results"]
    )
    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    assert report["status"] == "rejected"
    optimizer_iterations = report["optimizer_diagnostics"]["iterations"]
    assert optimizer_iterations[0]["diagnostics"]["filtered_duplicate_candidates"] == 1
    assert report["population"]["generated_candidate_count"] == 2
    assert len(report["optimizer_diagnostics"]["iterations"]) == 2
    assert report["replay"]["candidate"]["failure"] == {"reason": "fake replay rejection"}


def test_optimize_cli_request_auto_verified_smoke_applies_and_loads_real_skill(tmp_path: Path) -> None:
    skill_path = tmp_path / "aworld-skills" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original_content = "---\nname: demo\n---\n# Demo\n\nOld guidance.\n"
    skill_path.write_text(original_content, encoding="utf-8")
    trajectory_log = tmp_path / "trajectory.log"
    _write_trajectory_log(
        trajectory_log,
        [
            {
                "task_id": f"release-smoke-{index}",
                "trajectory": [
                    {
                        "meta": {"step": 1, "agent_id": "agent", "pre_agent": "runner"},
                        "state": {"input": {"content": f"Improve demo guidance {index}."}},
                        "action": {"content": "demo guidance failed"},
                        "reward": {"status": "failed"},
                    }
                ],
            }
            for index in range(5)
        ],
    )

    class SuccessfulReplayBackend:
        async def replay_candidate(self, request, *, candidate, dataset):
            baseline = ReplayVariantResult(
                variant_id="baseline",
                status="succeeded",
                trajectory=[
                    {"state": {"input": request.task_input}, "action": {"content": "old"}}
                ],
                metrics={"repetition_count": 1},
            )
            candidate_result = ReplayVariantResult(
                variant_id=candidate.candidate_id,
                status="succeeded",
                trajectory=[
                    {"state": {"input": request.task_input}, "action": {"content": "new"}}
                ],
                metrics={"repetition_count": 1},
            )
            return CandidateReplayResult(
                request=request,
                baseline=baseline,
                candidate=candidate_result,
                member_results=tuple(
                    CandidateReplayMemberResult(
                        case_id=case.case_id,
                        request=replace(
                            request,
                            task_id=case.case_id,
                            task_input=case.input,
                        ),
                        baseline=baseline,
                        candidate=candidate_result,
                    )
                    for case in dataset.cases
                ),
            )

    class VerifiedEvaluationBackend:
        async def evaluate_variant(self, request):
            if request.candidate is None:
                return EvaluationSummary(
                    variant_id="baseline",
                    metrics={"score": 0.2, "latency_ms": 100.0, "cost_usd": 1.0},
                    dataset_split=request.dataset_split,
                )
            return EvaluationSummary(
                variant_id=request.candidate.candidate_id,
                metrics={
                    "score": 0.9,
                    "latency_ms": 100.0,
                    "cost_usd": 1.0,
                    "deterministic_signal": True,
                    "command_case_count": 1,
                    "command_pass_count": 1,
                    "global_regression_passed": True,
                },
                dataset_split=request.dataset_split,
            )

    refresh_calls = []

    def refresh_runtime(candidate):
        refresh_calls.append(candidate.candidate_id)
        return {"status": "refreshed", "runtime_skill_count": 1}

    report_summary = optimize_from_cli_request(
        workspace_root=tmp_path,
        target="skill:demo",
        from_trajectory=str(trajectory_log),
        apply_policy="auto_verified",
        replay_enabled=True,
        candidate_replay_backend=SuccessfulReplayBackend(),
        evaluation_backend=VerifiedEvaluationBackend(),
        min_eval_cases=1,
        runtime_registry_refresher=refresh_runtime,
    )

    report = json.loads(Path(report_summary["report_path"]).read_text(encoding="utf-8"))
    updated_content = skill_path.read_text(encoding="utf-8")
    candidate_id = report_summary["best_candidate_id"]

    assert report_summary["status"] == "succeeded"
    assert candidate_id == report["selected_candidate_id"]
    assert updated_content != original_content
    assert "release_state: verified" in updated_content
    assert f"verified_run_id: {report['run_id']}" in updated_content
    assert "Runtime Behavior Delta" in updated_content
    assert "Self-Evolve Trace Guidance" not in updated_content
    assert report["post_apply"]["status"] == "accepted"
    assert refresh_calls == [candidate_id]
    assert report["post_apply"]["refresh"] == {
        "status": "refreshed",
        "runtime_skill_count": 1,
    }
    assert report["post_apply"]["metrics"]["post_apply_passed"] is True
    assert report["post_apply"]["metrics"]["runtime_content_matches"] is True
    assert report["post_apply"]["metrics"]["loaded_from_real_path"] is True
    assert Path(report["post_apply"]["backup_path"]).exists()
    journal = json.loads(Path(report["post_apply"]["journal_path"]).read_text(encoding="utf-8"))
    assert journal["status"] == "accepted"
    assert journal["target"]["target_id"] == "demo"
