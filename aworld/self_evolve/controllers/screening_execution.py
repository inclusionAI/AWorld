"""Execution engine and private support functions for population screening."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

from aworld.logs.util import logger
from aworld.self_evolve.budget import (
    BudgetDecision,
    BudgetStage,
    BudgetUsage,
    BudgetUsageCompleteness,
    BudgetUsageObservation,
    CandidateAttemptStage,
)
from aworld.self_evolve.campaign_policy import (
    CANDIDATE_REPAIRABLE_GATE_STAGES as _CANDIDATE_REPAIRABLE_GATE_STAGES,
    FRAMEWORK_SHARED_GATE_STAGES as _FRAMEWORK_SHARED_GATE_STAGES,
    is_verified_apply_policy as _is_verified_apply_policy,
)
from aworld.self_evolve.candidate_package import (
    CandidateMutationKind,
    candidate_package_fingerprint,
    classify_candidate_mutation,
)
from aworld.self_evolve.concurrency import SelfEvolveExecutionTelemetry
from aworld.self_evolve.controllers.screening import (
    ScreeningPopulationRequest,
    ScreeningPopulationRuntime,
)
from aworld.self_evolve.controllers.screening_helpers import (
    _DEFAULT_CANDIDATE_SCREENING_TIMEOUT_SECONDS,
    _DEFAULT_CANDIDATE_SCREENING_TOOL_CALL_LIMIT,
    _DEFAULT_CANDIDATE_SCREENING_TRACE_HORIZON,
    _MAX_CANDIDATE_SCREENING_TIMEOUT_SECONDS,
    _SCREENING_STEP_TIMEOUT_SECONDS,
    _candidate_artifact_lifecycle_observations,
    _candidate_requires_task_plane_intervention,
    _candidate_screening_dataset,
    _candidate_screening_dataset_for_case_ids,
    _candidate_screening_qualification_case_limit,
    _candidate_screening_rank,
    _candidate_screening_rank_details,
    _candidate_support_baseline_incompatibility_gate,
    _candidate_task_plane_intervention_case_ids,
    _candidate_validation_report_for_persistence,
    _combined_candidate_validation_report,
    _control_qualification_identity,
    _deduplicate_conformance_phenotypes,
    _non_negative_screening_float,
    _record_candidate_screening_observation,
    _record_support_specific_control_observation,
    _screening_attempt_has_artifact_lifecycle_proof,
    _screening_attempt_is_candidate_failure,
    _screening_attempt_requires_artifact_lifecycle_proof,
    _screening_attempt_requires_candidate_repair,
    _screening_baseline_failure_case_ids,
    _screening_control_infeasible_before_candidate_observation,
    _screening_gate_has_invalid_control,
    _screening_invalid_control_case_ids,
    _screening_invalid_control_is_timeout,
    _screening_required_intervention_unobserved,
    _screening_termination_axis_counts,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.failure_events import (
    AggregatedReplayFailure,
    FailureEventSource,
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayFailureEvent,
    ReplayFailureObservation,
    aggregate_replay_failure_observations,
)
from aworld.self_evolve.overlay import create_candidate_skill_overlay
from aworld.self_evolve.repair_conformance import (
    RepairConformanceContract,
    RepairConformanceResult,
    evaluate_artifact_lifecycle_conformance,
    merge_repair_conformance_constraint_context,
)
from aworld.self_evolve.replay import (
    CandidateReplayRequest,
    CandidateReplayResult,
    _baseline_replay_is_reusable,
    _candidate_replay_request_from_mapping,
    _distributed_member_repetitions,
    _is_replayable_user_task_case,
    _load_variant_result_from_dir,
    _member_artifact_name,
    baseline_control_fingerprint,
    candidate_replay_artifact_directory,
    load_candidate_replay_result,
    normalize_replay_members,
    replay_dataset_fingerprint,
    replay_support_fingerprint,
    replay_timeout_envelope_fingerprint,
)
from aworld.self_evolve.replay_adaptation import ReplayAdaptationBundle
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.types import (
    CandidateVariant,
    GateResult,
    SelfEvolveTargetRef,
)


@dataclass(frozen=True)
class _TelemetryUsageSnapshot:
    batches: tuple[Mapping[str, object], ...] = ()


@dataclass(frozen=True)
class _TelemetryUsageDelta:
    observation: BudgetUsageObservation
    source: str



def _decimal_metric(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except Exception:
        return None
    return result if result.is_finite() and result >= 0 else None


def _sanitized_telemetry_usage_batch(
    value: Mapping[str, object],
) -> dict[str, object]:
    """Retain only bounded accounting fields from a telemetry batch."""

    result: dict[str, object] = {}
    token_usage = value.get("token_usage")
    if isinstance(token_usage, Mapping):
        result["token_usage"] = {
            key: item
            for key, item in token_usage.items()
            if key
            in {
                "total_tokens",
                "input_tokens",
                "output_tokens",
                "prompt_tokens",
                "completion_tokens",
            }
            and isinstance(item, int)
            and not isinstance(item, bool)
            and item >= 0
        }
    for key in (
        "total_cost_usd",
        "cost_usd",
        "elapsed_seconds",
        "execution_seconds",
    ):
        item = _decimal_metric(value.get(key))
        if item is not None:
            result[key] = str(item)
    return result


def _stage_telemetry_usage_snapshot(
    telemetry: SelfEvolveExecutionTelemetry,
    stage: str,
) -> _TelemetryUsageSnapshot:
    """Capture a stable cursor over sanitized per-batch stage telemetry."""

    report = telemetry.to_report()
    stage_report = report.get(stage)
    if not isinstance(stage_report, Mapping):
        return _TelemetryUsageSnapshot()
    batches = stage_report.get("batches")
    if not isinstance(batches, (list, tuple)):
        return _TelemetryUsageSnapshot()
    return _TelemetryUsageSnapshot(
        batches=tuple(
            _sanitized_telemetry_usage_batch(item)
            for item in batches
            if isinstance(item, Mapping)
        )
    )


def _canonical_batch_token_usage(batch: Mapping[str, object]) -> int | None:
    usage = batch.get("token_usage")
    if not isinstance(usage, Mapping):
        return None
    total = usage.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        return total
    for input_key, output_key in (
        ("input_tokens", "output_tokens"),
        ("prompt_tokens", "completion_tokens"),
    ):
        input_tokens = usage.get(input_key)
        output_tokens = usage.get(output_key)
        if all(
            isinstance(item, int)
            and not isinstance(item, bool)
            and item >= 0
            for item in (input_tokens, output_tokens)
        ):
            return int(input_tokens) + int(output_tokens)
    return None


def _canonical_batch_decimal_usage(
    batch: Mapping[str, object],
    *keys: str,
) -> Decimal | None:
    for key in keys:
        value = _decimal_metric(batch.get(key))
        if value is not None:
            return value
    return None


def _stage_telemetry_usage_delta(
    before: _TelemetryUsageSnapshot,
    after: _TelemetryUsageSnapshot,
) -> _TelemetryUsageDelta:
    cursor = len(before.batches)
    if len(after.batches) <= cursor or after.batches[:cursor] != before.batches:
        return _TelemetryUsageDelta(
            observation=BudgetUsageObservation(
                known_lower_bound=BudgetUsage(),
                completeness=BudgetUsageCompleteness.incomplete(),
            ),
            source="reserved_fallback_missing_stage_telemetry_delta",
        )
    new_batches = after.batches[cursor:]
    batch_tokens = tuple(_canonical_batch_token_usage(batch) for batch in new_batches)
    batch_costs = tuple(
        _canonical_batch_decimal_usage(batch, "total_cost_usd", "cost_usd")
        for batch in new_batches
    )
    batch_walls = tuple(
        _canonical_batch_decimal_usage(
            batch,
            "elapsed_seconds",
            "execution_seconds",
        )
        for batch in new_batches
    )
    token_complete = all(value is not None for value in batch_tokens)
    cost_complete = all(value is not None for value in batch_costs)
    wall_complete = all(value is not None for value in batch_walls)
    token_delta = sum(
        int(value) for value in batch_tokens if value is not None
    )
    cost_delta = sum(
        (value for value in batch_costs if value is not None), Decimal("0")
    )
    wall_delta = sum(
        (value for value in batch_walls if value is not None), Decimal("0")
    )
    observed = []
    for name, values, complete in (
        ("tokens", batch_tokens, token_complete),
        ("cost_usd", batch_costs, cost_complete),
        ("wall_seconds", batch_walls, wall_complete),
    ):
        if complete:
            observed.append(name)
        elif any(value is not None for value in values):
            observed.append(f"{name}_lower_bound")
    source = (
        "telemetry_delta_" + "+".join(observed)
        if observed
        else "reserved_fallback_missing_stage_telemetry_delta"
    )
    return _TelemetryUsageDelta(
        observation=BudgetUsageObservation(
            known_lower_bound=BudgetUsage(
                tokens=token_delta,
                cost_usd=cost_delta,
                wall_seconds=wall_delta,
            ),
            completeness=BudgetUsageCompleteness(
                tokens=token_complete,
                cost_usd=cost_complete,
                wall_seconds=wall_complete,
            ),
        ),
        source=source,
    )


def _telemetry_usage_with_observed_wall(
    usage: _TelemetryUsageDelta,
    *,
    elapsed_seconds: float,
) -> _TelemetryUsageDelta:
    """Complete wall accounting even when a stage exits before telemetry starts."""

    observation = usage.observation
    lower_bound = observation.known_lower_bound
    observed_wall = Decimal(str(max(0.0, elapsed_seconds)))
    complete_wall = max(lower_bound.wall_seconds, observed_wall)
    source = usage.source
    if not observation.completeness.wall_seconds:
        source = (
            "observed_stage_elapsed_seconds"
            if source == "reserved_fallback_missing_stage_telemetry_delta"
            else f"{source}+observed_stage_elapsed_seconds"
        )
    return _TelemetryUsageDelta(
        observation=BudgetUsageObservation(
            known_lower_bound=BudgetUsage(
                tokens=lower_bound.tokens,
                cost_usd=lower_bound.cost_usd,
                wall_seconds=complete_wall,
            ),
            completeness=BudgetUsageCompleteness(
                tokens=observation.completeness.tokens,
                cost_usd=observation.completeness.cost_usd,
                wall_seconds=True,
            ),
        ),
        source=source,
    )


def _budget_usage_for_attempt_event(
    decision: BudgetDecision,
    *,
    tokens: int | None = None,
    cost_usd: Decimal | None = None,
    wall_seconds: Decimal | None = None,
) -> BudgetUsage:
    estimate = decision.estimate.resolved_usage() or BudgetUsage()
    return BudgetUsage(
        tokens=estimate.tokens if tokens is None else tokens,
        cost_usd=estimate.cost_usd if cost_usd is None else cost_usd,
        wall_seconds=(
            estimate.wall_seconds if wall_seconds is None else wall_seconds
        ),
    )


def _emit_progress(
    progress_callback: Callable[[str, str], Any] | None,
    stage: str,
    message: str,
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(stage, message)
    except Exception as exc:
        logger.debug(f"self_evolve.progress_callback_failed stage={stage} error={exc}")


def _non_negative_int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return 0


def _candidate_screening_timeout(
    authoritative_timeout_seconds: int,
    *,
    max_steps: int | None = None,
    empirical_observation: Mapping[str, float | int] | None = None,
) -> int:
    """Bound screening using the frozen envelope and prior case latency."""

    planned_seconds = (
        max(1, max_steps) * _SCREENING_STEP_TIMEOUT_SECONDS
        if max_steps is not None
        else _DEFAULT_CANDIDATE_SCREENING_TIMEOUT_SECONDS
    )
    empirical_seconds = 0.0
    if empirical_observation:
        baseline_success_count = _non_negative_int(
            empirical_observation.get("baseline_success_count")
        )
        baseline_wall_seconds = _non_negative_screening_float(
            empirical_observation.get("baseline_success_wall_seconds")
        )
        if baseline_success_count > 0 and baseline_wall_seconds > 0:
            empirical_seconds = max(
                empirical_seconds,
                baseline_wall_seconds / baseline_success_count * 1.5,
            )
        attempt_count = _non_negative_int(
            empirical_observation.get("attempt_count")
        )
        total_wall_seconds = _non_negative_screening_float(
            empirical_observation.get("total_wall_seconds")
        )
        if attempt_count > 0 and total_wall_seconds > 0:
            empirical_seconds = max(
                empirical_seconds,
                total_wall_seconds / attempt_count * 1.25,
            )
    return min(
        authoritative_timeout_seconds,
        _MAX_CANDIDATE_SCREENING_TIMEOUT_SECONDS,
        max(
            _DEFAULT_CANDIDATE_SCREENING_TIMEOUT_SECONDS,
            planned_seconds,
            math.ceil(empirical_seconds),
        ),
    )


def _candidate_screening_escalated_timeout(
    current_timeout_seconds: int,
    *,
    authoritative_timeout_seconds: int,
) -> int | None:
    ceiling = min(
        authoritative_timeout_seconds,
        _MAX_CANDIDATE_SCREENING_TIMEOUT_SECONDS,
    )
    if current_timeout_seconds >= ceiling:
        return None
    escalated = min(ceiling, math.ceil(current_timeout_seconds * 1.5))
    return escalated if escalated > current_timeout_seconds else None


def _candidate_screening_max_steps(
    dataset: SelfEvolveDataset,
    *,
    configured_max_steps: int | None,
) -> int:
    """Expose enough source-trace depth to observe candidate intervention.

    Screening remains bounded to a small horizon, but a one-step replay cannot
    observe the result of the first tool call for multi-step source traces.  Use
    the configured replay depth as a floor and expand only to the smaller of the
    observed trace depth and the bounded screening horizon.
    """

    configured_floor = max(1, int(configured_max_steps or 1))
    observed_depth = max(
        (
            len(case.trace_pack.steps)
            for case in dataset.cases
            if case.trace_pack is not None
        ),
        default=1,
    )
    # A source trace records actions, while a replay also needs one final model
    # turn to synthesize the task result.  Reserving that terminal turn avoids
    # deterministically censoring otherwise healthy multi-tool trajectories.
    return max(
        configured_floor,
        min(observed_depth + 1, _DEFAULT_CANDIDATE_SCREENING_TRACE_HORIZON),
    )


def _schema_field_contract_fingerprint(
    details: Mapping[str, object],
) -> str | None:
    raw_constraints = details.get("schema_field_constraints")
    constraints = [
        {
            "schema_layer": item.get("schema_layer"),
            "field_path": item.get("field_path"),
            "rule": item.get("rule"),
            "expected": item.get("expected"),
            "value_domain": item.get("value_domain", "schema_value"),
            "required_operations": item.get("required_operations", ()),
            "forbidden_operations": item.get("forbidden_operations", ()),
        }
        for item in (
            raw_constraints[:100]
            if isinstance(raw_constraints, (list, tuple))
            else ()
        )
        if isinstance(item, Mapping)
    ]
    raw_runtime_constraints = details.get("runtime_response_constraints")
    runtime_constraints = [
        dict(item)
        for item in (
            raw_runtime_constraints[:64]
            if isinstance(raw_runtime_constraints, (list, tuple))
            else ()
        )
        if isinstance(item, Mapping)
    ]
    raw_runtime_artifacts = details.get("runtime_artifact_constraints")
    runtime_artifacts = [
        dict(item)
        for item in (
            raw_runtime_artifacts[:64]
            if isinstance(raw_runtime_artifacts, (list, tuple))
            else ()
        )
        if isinstance(item, Mapping)
    ]
    if not constraints and not runtime_constraints and not runtime_artifacts:
        return None
    sorted_schema_constraints = sorted(
        constraints,
        key=lambda item: json.dumps(item, sort_keys=True, default=str),
    )
    sorted_runtime_constraints = sorted(
        runtime_constraints,
        key=lambda item: json.dumps(item, sort_keys=True, default=str),
    )
    sorted_runtime_artifacts = sorted(
        runtime_artifacts,
        key=lambda item: json.dumps(item, sort_keys=True, default=str),
    )
    active_components = sum(
        bool(item)
        for item in (constraints, runtime_constraints, runtime_artifacts)
    )
    payload: object
    if active_components == 1:
        payload = (
            sorted_schema_constraints
            if constraints
            else sorted_runtime_constraints
            if runtime_constraints
            else sorted_runtime_artifacts
        )
    else:
        payload = {
            "schema_field_constraints": sorted_schema_constraints,
            "runtime_response_constraints": sorted_runtime_constraints,
            "runtime_artifact_constraints": sorted_runtime_artifacts,
        }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    prefix = (
        "schema-fields"
        if constraints and active_components == 1
        else "runtime-response"
        if runtime_constraints and active_components == 1
        else "runtime-artifact"
        if runtime_artifacts and active_components == 1
        else "typed-repair"
    )
    return f"{prefix}:sha256:" + hashlib.sha256(encoded).hexdigest()


def _with_typed_gate_failure_event(gate: GateResult) -> GateResult:
    """Connect policy gate failures to the typed Campaign causal boundary."""

    if gate.passed:
        return gate
    details = dict(gate.details) if isinstance(gate.details, Mapping) else {}
    existing_events = details.get("causal_failure_events")
    if isinstance(existing_events, (list, tuple)) and any(
        isinstance(item, Mapping) for item in existing_events
    ):
        return gate

    owner: FailureOwner | None = None
    scope: FailureScope | None = None
    stage: FailureStage | None = None
    declared_owner = details.get("failure_owner")
    declared_scope = details.get("failure_scope")
    declared_stage = details.get("failure_stage")
    try:
        if declared_owner is not None:
            owner = FailureOwner(str(declared_owner))
        if declared_scope is not None:
            scope = FailureScope(str(declared_scope))
        if declared_stage is not None:
            stage = FailureStage(str(declared_stage))
    except ValueError:
        owner = None
        scope = None
        stage = None
    candidate_stage = _CANDIDATE_REPAIRABLE_GATE_STAGES.get(
        gate.gate_name
    )
    framework_stage = _FRAMEWORK_SHARED_GATE_STAGES.get(gate.gate_name)
    stage = stage or candidate_stage or framework_stage
    if candidate_stage is not None and owner is None:
        owner = FailureOwner.CANDIDATE
        scope = FailureScope.CANDIDATE
    elif (
        gate.gate_name in {"held_out_verification", "judge_only_signal"}
        and owner is None
        and details.get("deterministic_signal_present") is False
    ):
        # A completed evaluator that rejects the candidate does not become a
        # shared framework blocker merely because verification is derivative.
        # Retain framework ownership only for genuinely missing/infeasible
        # verification authority (where no negative candidate signal exists).
        owner = FailureOwner.CANDIDATE
        scope = FailureScope.CANDIDATE
        stage = FailureStage.EVALUATION
    elif gate.gate_name == "required_verification" and owner is None:
        command_case_count = _non_negative_int(
            details.get("command_case_count")
        )
        owner = (
            FailureOwner.CANDIDATE
            if command_case_count > 0
            else FailureOwner.FRAMEWORK
        )
        scope = (
            FailureScope.CANDIDATE
            if command_case_count > 0
            else FailureScope.SHARED_RUN
        )
        stage = FailureStage.EVALUATION
    elif owner is None:
        if framework_stage is not None:
            owner = FailureOwner.FRAMEWORK
            scope = FailureScope.SHARED_RUN
    if owner is None or scope is None or stage is None:
        return gate

    repairable = details.get("repairable") is not False
    event = ReplayFailureEvent(
        code=str(details.get("code") or gate.gate_name),
        owner=owner,
        stage=stage,
        scope=scope,
        repairable=repairable,
        category="verification_gate",
        summary=gate.reason,
        diagnostics={
            "gate_name": gate.gate_name,
            "field_path": details.get("field_path"),
        },
        contract_fingerprint=(
            str(details["contract_fingerprint"])
            if isinstance(details.get("contract_fingerprint"), str)
            else None
        ),
    ).to_dict()
    details.update(
        {
            # Failure class describes the failed plane (for example,
            # measurement), while failure owner describes who can repair it.
            # Preserve an explicit class instead of collapsing both axes.
            "failure_class": details.get("failure_class") or owner.value,
            "failure_owner": owner.value,
            "failure_scope": scope.value,
            "repairable": repairable,
            "failure_event": event,
            "causal_failure_events": [event],
        }
    )
    return GateResult(
        gate_name=gate.gate_name,
        passed=False,
        reason=gate.reason,
        details=details,
    )


def _replay_artifact_path(replay_result: CandidateReplayResult) -> str:
    return str(_replay_request_artifact_path(replay_result.request))


def _replay_request_artifact_path(request: CandidateReplayRequest) -> Path:
    return candidate_replay_artifact_directory(
        workspace_root=request.workspace_root,
        run_id=request.run_id,
        candidate_id=request.candidate_id,
        artifact_namespace=request.artifact_namespace,
    )


def _baseline_replay_artifact_dir(replay_result: CandidateReplayResult) -> str:
    if replay_result.member_results is not None:
        if not replay_result.member_results:
            raise ValueError(
                "empty explicit replay members have no baseline artifact path"
            )
        return str(Path(_replay_artifact_path(replay_result)) / "members")
    if replay_result.request.baseline_replay_dir:
        return replay_result.request.baseline_replay_dir
    return str(Path(_replay_artifact_path(replay_result)) / "baseline")


def _replay_result_has_reusable_baseline(
    *,
    dataset: SelfEvolveDataset,
    replay_result: CandidateReplayResult,
) -> bool:
    normalized = normalize_replay_members(
        dataset=dataset,
        replay_result=replay_result,
    )
    return bool(normalized.members) and normalized.valid and all(
        _baseline_replay_is_reusable(
            member.baseline,
            requested_repetitions=member.request.baseline_repetitions,
        )
        for member in normalized.members
    )


def find_reusable_baseline_replay_dir(
    *,
    store: FilesystemSelfEvolveStore,
    run_id: str,
    target: SelfEvolveTargetRef,
    dataset: SelfEvolveDataset,
    baseline_repetitions: int,
    baseline_skill_fingerprint: str | None = None,
    dataset_fingerprint: str | None = None,
    adaptation_fingerprint: str | None = None,
    workspace_seed_fingerprint: str | None = None,
    support_fingerprint: str | None = None,
    timeout_envelope_fingerprint: str | None = None,
) -> str | None:
    expected_provenance = {
        "baseline_skill_fingerprint": baseline_skill_fingerprint,
        "dataset_fingerprint": dataset_fingerprint,
        "workspace_seed_fingerprint": workspace_seed_fingerprint,
        "support_fingerprint": support_fingerprint,
        "timeout_envelope_fingerprint": timeout_envelope_fingerprint,
    }
    if any(value is None for value in expected_provenance.values()):
        return None
    root = store.artifact_root
    if not root.exists():
        return None
    case_ids = tuple(
        case.case_id for case in dataset.cases if _is_replayable_user_task_case(case)
    )
    if not case_ids:
        case_ids = tuple(case.case_id for case in dataset.cases)
    # Completed replay artifacts in the current run are valid cache sources.
    # Excluding ``run_id`` forced every generation batch to repeat an identical
    # screening baseline. Incomplete replay directories fail closed below when
    # loading or validating their lifecycle and provenance.
    run_dirs = [path for path in root.iterdir() if path.is_dir()]
    for prior_run_dir in sorted(
        run_dirs,
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ):
        replay_roots = [prior_run_dir / "replay"]
        screening_root = prior_run_dir / "screening"
        if screening_root.is_dir() and not screening_root.is_symlink():
            replay_roots.extend(
                path / "replay"
                for path in screening_root.iterdir()
                if path.is_dir() and not path.is_symlink()
            )
        replay_dirs = [
            path
            for replay_root in replay_roots
            if replay_root.is_dir() and not replay_root.is_symlink()
            for path in replay_root.iterdir()
            if path.is_dir() and not path.is_symlink()
        ]
        for replay_dir in sorted(
            replay_dirs,
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ):
            incremental_members = _incremental_baseline_cache_dir(
                replay_dir=replay_dir,
                target=target,
                case_ids=case_ids,
                baseline_repetitions=baseline_repetitions,
                expected_provenance=expected_provenance,
            )
            if incremental_members is not None:
                return incremental_members
            try:
                replay_result = load_candidate_replay_result(replay_dir)
            except (FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
                continue
            if not _replay_target_matches(replay_result.request.target, target):
                continue
            if not _replay_request_provenance_matches(
                replay_result.request,
                expected=expected_provenance,
            ):
                continue
            if replay_result.request.baseline_repetitions != baseline_repetitions:
                continue
            normalized = normalize_replay_members(
                dataset=dataset,
                replay_result=replay_result,
            )
            if not normalized.valid:
                continue
            if replay_result.member_results is not None:
                member_case_ids = tuple(member.case_id for member in normalized.members)
                if member_case_ids != case_ids:
                    continue
                member_repetitions = _distributed_member_repetitions(
                    baseline_repetitions,
                    member_count=len(case_ids),
                )
                if all(
                    _baseline_replay_is_reusable(
                        member.baseline,
                        requested_repetitions=member_repetitions,
                    )
                    for member in normalized.members
                ):
                    members_dir = replay_dir / "members"
                    if (members_dir / "manifest.json").exists():
                        return str(members_dir)
                continue
            if len(case_ids) != 1 or replay_result.request.task_id != case_ids[0]:
                continue
            if _baseline_replay_is_reusable(
                replay_result.baseline,
                requested_repetitions=baseline_repetitions,
            ):
                baseline_dir = replay_dir / "baseline"
                if baseline_dir.exists():
                    return str(baseline_dir)
    return None


def _incremental_baseline_cache_dir(
    *,
    replay_dir: Path,
    target: SelfEvolveTargetRef,
    case_ids: tuple[str, ...],
    baseline_repetitions: int,
    expected_provenance: Mapping[str, str | None],
) -> str | None:
    members_root = replay_dir / "members"
    manifest_path = members_root / "baseline_cache_manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = _load_json_mapping(manifest_path)
    except (ValueError, json.JSONDecodeError, OSError):
        return None
    members = manifest.get("members")
    if not isinstance(members, list):
        return None
    eligible = set(case_ids)
    for item in members:
        if not isinstance(item, Mapping):
            continue
        case_id = str(item.get("case_id") or "")
        relative_path = str(item.get("path") or "")
        if case_id not in eligible or relative_path != _member_artifact_name(case_id):
            continue
        member_root = members_root / relative_path
        try:
            request = _load_json_mapping(member_root / "request.json")
            stored_request = _candidate_replay_request_from_mapping(request)
            declared_control_fingerprint = item.get("control_fingerprint")
            if (
                declared_control_fingerprint is not None
                and declared_control_fingerprint
                != baseline_control_fingerprint(stored_request)
            ):
                continue
            raw_target = request.get("target")
            if not isinstance(raw_target, Mapping):
                continue
            stored_target = SelfEvolveTargetRef(
                target_type=str(raw_target.get("target_type") or ""),
                target_id=str(raw_target.get("target_id") or ""),
                path=(
                    str(raw_target.get("path"))
                    if raw_target.get("path") is not None
                    else None
                ),
            )
            if not _replay_target_matches(stored_target, target):
                continue
            if int(request.get("baseline_repetitions") or 0) != baseline_repetitions:
                continue
            if not _replay_request_provenance_matches(
                stored_request,
                expected=expected_provenance,
            ):
                continue
            baseline = _load_variant_result_from_dir(
                member_root / "baseline",
                base_variant_id="baseline",
            )
        except (
            FileNotFoundError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            OSError,
        ):
            continue
        if _baseline_replay_is_reusable(
            baseline,
            requested_repetitions=baseline_repetitions,
        ):
            return str(members_root)
    return None


def _replay_request_provenance_matches(
    request: CandidateReplayRequest,
    *,
    expected: Mapping[str, str | None],
) -> bool:
    exact = all(
        value is not None and getattr(request, key, None) == value
        for key, value in expected.items()
    )
    if not exact:
        return False
    return bool(
        request.support_fingerprint
        == replay_support_fingerprint(request.replay_adaptation)
        and request.timeout_envelope_fingerprint
        == replay_timeout_envelope_fingerprint(
            timeout_seconds=request.timeout_seconds,
            max_steps=request.max_steps,
            max_tool_calls=request.max_tool_calls,
        )
    )


def _replay_target_matches(
    stored: SelfEvolveTargetRef,
    current: SelfEvolveTargetRef,
) -> bool:
    if (
        stored.target_type != current.target_type
        or stored.target_id != current.target_id
    ):
        return False
    if stored.path is None or current.path is None:
        return True
    return Path(stored.path).expanduser() == Path(current.path).expanduser()


def _load_json_mapping(path: Path) -> Mapping[str, Any]:
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _gate_has_typed_shared_infrastructure_failure(gate: GateResult) -> bool:
    details = gate.details
    if not isinstance(details, Mapping):
        return False
    raw_events: list[Mapping[str, object]] = []
    raw_event = details.get("failure_event")
    if isinstance(raw_event, Mapping):
        raw_events.append(raw_event)
    raw_causal_events = details.get("causal_failure_events")
    if isinstance(raw_causal_events, (list, tuple)):
        raw_events.extend(
            item for item in raw_causal_events if isinstance(item, Mapping)
        )
    for payload in raw_events:
        try:
            event = _typed_causal_feedback_event(payload)
        except (TypeError, ValueError):
            continue
        if (
            FailureEventSource.NATIVE.value in event.source_kinds
            and event.scope is FailureScope.SHARED_RUN
            and event.owner
            in {FailureOwner.INFRASTRUCTURE, FailureOwner.FRAMEWORK}
        ):
            return True
    return False


def _gate_has_typed_shared_measurement_failure(gate: GateResult) -> bool:
    """Return true only when the shared measurement experiment is invalid."""

    details = gate.details
    if not isinstance(details, Mapping):
        return False
    return bool(
        gate.gate_name
        in {
            "candidate_replay",
            "replay_confidence",
            "fresh_evaluator_rerun",
            "trusted_improvement_measurement",
        }
        and details.get("failure_class")
        in {"measurement", "framework", "infrastructure"}
        and details.get("failure_owner")
        in {FailureOwner.FRAMEWORK.value, FailureOwner.INFRASTRUCTURE.value}
        and details.get("failure_scope") == FailureScope.SHARED_RUN.value
        and details.get("repairable") is True
    )


def _repair_conformance_gate(
    result: RepairConformanceResult,
    *,
    contract: RepairConformanceContract | None = None,
) -> GateResult:
    public_result_details = _candidate_validation_report_for_persistence(
        dict(result.details)
    )
    if not isinstance(public_result_details, Mapping):
        public_result_details = {}
    details = {
        "failure_class": (
            None if result.passed else result.failure_class
        ),
        "repairable": bool(not result.passed and result.repairable),
        "stage": "repair_conformance",
        "code": result.code,
        **dict(public_result_details),
    }
    if result.failure_fingerprint is not None:
        details["failure_fingerprint"] = result.failure_fingerprint
    if not result.passed:
        raw_causal_events = details.get("causal_failure_events")
        causal_events = (
            [dict(item) for item in raw_causal_events if isinstance(item, Mapping)]
            if isinstance(raw_causal_events, (list, tuple))
            else []
        )
        if not causal_events:
            failure_owner = (
                FailureOwner.FRAMEWORK
                if result.failure_class == "framework"
                else (
                    FailureOwner.INFRASTRUCTURE
                    if result.failure_class == "infrastructure"
                    else FailureOwner.CANDIDATE
                )
            )
            failure_event = ReplayFailureEvent(
                code=result.code,
                owner=failure_owner,
                stage=FailureStage.CAPABILITY_PREFLIGHT,
                scope=(
                    FailureScope.CANDIDATE
                    if failure_owner is FailureOwner.CANDIDATE
                    else FailureScope.SHARED_RUN
                ),
                repairable=result.repairable,
                category="repair_conformance",
                summary=result.reason,
                contract_fingerprint=(
                    _schema_field_contract_fingerprint(details)
                    or (
                        contract.contract_identity
                        if contract is not None
                        else None
                    )
                ),
                diagnostics={
                    "focus_candidate_id": (
                        contract.focus_candidate_id if contract is not None else None
                    ),
                },
            )
            causal_events = [failure_event.to_dict()]
        details["failure_event"] = causal_events[0]
        # Conformance is an independent pre-replay gate, so publish every
        # distinct failed group through the causal feedback channel.
        details["causal_failure_events"] = causal_events
    if contract is not None:
        details["repair_conformance"] = (
            merge_repair_conformance_constraint_context(
                contract.to_public_dict(),
                details,
            )
            or contract.to_public_dict()
        )
    return GateResult(
        gate_name="candidate_repair_conformance",
        passed=result.passed,
        reason=result.reason,
        details=details,
    )


def _shared_replay_failure_blocks_population(
    replay_result: CandidateReplayResult,
) -> bool:
    variants = [replay_result.baseline, replay_result.candidate]
    for member in replay_result.member_results or ():
        variants.extend((member.baseline, member.candidate))
    events: list[ReplayFailureEvent] = []
    for variant in variants:
        if isinstance(variant.failure, ReplayFailureEvent):
            events.append(variant.failure)
        events.extend(variant.blocked_by)
    return any(
        event.scope is FailureScope.SHARED_RUN
        and event.owner in {FailureOwner.INFRASTRUCTURE, FailureOwner.FRAMEWORK}
        and event.source is FailureEventSource.NATIVE
        for event in events
    )


def _replay_evaluator_admission_gate(
    replay_result: CandidateReplayResult | None,
    *,
    apply_policy: str,
) -> GateResult | None:
    """Reject hard replay evidence invariant regressions before judge work."""

    if replay_result is None or not _is_verified_apply_policy(apply_policy):
        return None
    baseline_metrics = replay_result.baseline.metrics or {}
    candidate_metrics = replay_result.candidate.metrics or {}
    regressions: list[dict[str, object]] = []
    observed_metrics: list[str] = []

    # These are candidate-side completion invariants, not relative quality
    # signals. A baseline with the same failure must not make an incomplete or
    # policy-violating candidate admissible.
    absolute_invariants = (
        (
            "evidence_runtime_policy_passed",
            "evidence_runtime_policy_authoritative_passed",
        ),
        ("task_completion_established", "task_completion_established"),
    )
    for fallback_metric_name, authoritative_metric_name in absolute_invariants:
        metric_name = (
            authoritative_metric_name
            if authoritative_metric_name in candidate_metrics
            else fallback_metric_name
        )
        candidate_value = candidate_metrics.get(metric_name)
        if isinstance(candidate_value, bool):
            observed_metrics.append(metric_name)
        if candidate_value is False:
            regressions.append(
                {
                    "metric": metric_name,
                    "baseline": baseline_metrics.get(metric_name),
                    "candidate": False,
                    "direction": "candidate_invariant_failed",
                }
            )

    for metric_name in (
        "evidence_strategy_passed",
        "evidence_bundle_valid",
        "evidence_manifest_valid",
    ):
        baseline_value = baseline_metrics.get(metric_name)
        candidate_value = candidate_metrics.get(metric_name)
        if isinstance(baseline_value, bool) or isinstance(candidate_value, bool):
            observed_metrics.append(metric_name)
        if baseline_value is True and candidate_value is False:
            regressions.append(
                {
                    "metric": metric_name,
                    "baseline": True,
                    "candidate": False,
                    "direction": "true_to_false",
                }
            )

    for metric_name in (
        "evidence_manifest_invalid_entry_count",
        "evidence_unmanifested_artifact_reference_count",
        "evidence_runtime_policy_violation_count",
    ):
        baseline_value = baseline_metrics.get(metric_name)
        candidate_value = candidate_metrics.get(metric_name)
        if not (
            isinstance(baseline_value, (int, float))
            and not isinstance(baseline_value, bool)
            and isinstance(candidate_value, (int, float))
            and not isinstance(candidate_value, bool)
        ):
            continue
        observed_metrics.append(metric_name)
        if candidate_value > baseline_value:
            regressions.append(
                {
                    "metric": metric_name,
                    "baseline": baseline_value,
                    "candidate": candidate_value,
                    "direction": "increased",
                }
            )

    passed = not regressions
    return GateResult(
        gate_name="replay_evaluator_admission",
        passed=passed,
        reason=(
            "replay evidence invariants did not regress"
            if passed
            else (
                "candidate regressed a hard replay evidence invariant; "
                "authoritative evaluator was skipped"
            )
        ),
        details={
            "failure_class": None if passed else FailureOwner.CANDIDATE.value,
            "failure_owner": None if passed else FailureOwner.CANDIDATE.value,
            "failure_scope": None if passed else FailureScope.CANDIDATE.value,
            "repairable": not passed,
            "code": (
                None if passed else "replay_evidence_invariant_regression"
            ),
            "observed_metrics": sorted(set(observed_metrics)),
            "regressions": regressions,
            "evaluator_skipped": not passed,
        },
    )


def _typed_causal_feedback_event(
    payload: Mapping[str, object],
) -> AggregatedReplayFailure:
    """Parse causal transport without routing typed scalars through sanitization."""

    if str(payload.get("schema_version") or "").startswith(
        "aworld.self_evolve.replay_failure_aggregate."
    ):
        return AggregatedReplayFailure.from_dict(payload)
    if payload.get("schema_version") is not None:
        event = ReplayFailureEvent.from_dict(payload)
        return aggregate_replay_failure_observations(
            (ReplayFailureObservation(event=event),)
        )[0]
    return AggregatedReplayFailure.from_dict(payload)



async def execute_screen_candidate_population(
    request: ScreeningPopulationRequest,
    runtime: ScreeningPopulationRuntime,
) -> tuple[tuple[CandidateVariant, ...], dict[str, object] | None]:
    policy = runtime.policy
    run_id = request.run_id
    target = request.target
    dataset = request.dataset
    candidates = request.candidates
    apply_policy = request.apply_policy
    capability_requirements = request.capability_requirements
    repair_conformance_contracts = request.repair_conformance_contracts
    attempt_tracker = request.attempt_tracker
    attempt_keys = request.attempt_keys
    budget_context = request.budget_context
    require_single_candidate_screening = (
        request.require_single_candidate_screening
    )
    stored_measurement_resume = request.stored_measurement_resume
    if (
        not candidates
        or not runtime.replay_enabled
        or runtime.replay_backend is None
    ):
        return candidates, None

    conformance_candidates, conformance_report = (
        await runtime.validate_conformance_population(
            run_id=run_id,
            target=target,
            dataset=dataset,
            candidates=candidates,
            capability_requirements=capability_requirements,
            repair_conformance_contracts=repair_conformance_contracts,
            attempt_tracker=attempt_tracker,
            attempt_keys=attempt_keys,
            budget_context=budget_context,
        )
    )
    if not conformance_candidates:
        return (), _combined_candidate_validation_report(
            candidates=candidates,
            conformance=conformance_report,
            screening=None,
        )

    (
        conformance_candidates,
        phenotype_duplicates,
        phenotype_fingerprints,
    ) = _deduplicate_conformance_phenotypes(
        conformance_candidates,
        conformance_report=conformance_report,
        current_content=target.load_current_content(),
    )
    if phenotype_duplicates:
        for duplicate_id in phenotype_duplicates:
            duplicate_key = (
                attempt_keys.get(duplicate_id)
                if attempt_keys is not None
                else None
            )
            if (
                attempt_tracker is not None
                and duplicate_key is not None
                and not attempt_tracker.terminal(duplicate_key)
            ):
                attempt_tracker.emit(
                    duplicate_key,
                    CandidateAttemptStage.NOT_RUN,
                    reason_code="equivalent_conformance_phenotype",
                )
    if phenotype_fingerprints:
        conformance_report = {
            **dict(conformance_report or {}),
            "phenotype_representative_count": len(conformance_candidates),
            "phenotype_duplicate_count": len(phenotype_duplicates),
            "phenotype_duplicate_of": dict(phenotype_duplicates),
            "phenotype_fingerprints": dict(phenotype_fingerprints),
        }

    if stored_measurement_resume:
        if len(conformance_candidates) != 1:
            raise ValueError(
                "stored measurement resume requires exactly one frozen candidate"
            )
        sole_candidate = conformance_candidates[0]
        screening_report = {
            "screening_strategy": "stored_candidate_measurement_resume",
            "screening_role": "bypassed_for_authoritative_measurement",
            "generated_candidate_count": 1,
            "attempted_candidate_count": 0,
            "physical_pair_execution_count": 0,
            "selected_candidate_id": sole_candidate.candidate_id,
            "selected_candidate_ids": [sole_candidate.candidate_id],
            "candidate_dispositions": {
                sole_candidate.candidate_id: (
                    "promoted_to_authoritative_measurement_resume"
                ),
            },
            "selection_reason": (
                "the immutable measurement-pending candidate already passed "
                "candidate selection and resumes directly on the authoritative "
                "measurement plane"
            ),
            "attempts": [],
            "stopped_by_shared_infrastructure": False,
            "stopped_after_budget_censor": False,
            "screening_outcome": "not_required",
            "phenotype_duplicate_of": dict(phenotype_duplicates),
        }
        return (
            conformance_candidates,
            _combined_candidate_validation_report(
                candidates=candidates,
                conformance=conformance_report,
                screening=screening_report,
            ),
        )

    support_prerequisites = tuple(
        candidate
        for candidate in conformance_candidates
        if classify_candidate_mutation(
            candidate,
            current_content=target.load_current_content(),
        ).kind
        is CandidateMutationKind.EVALUATION_SUPPORT
    )
    if _is_verified_apply_policy(apply_policy) and support_prerequisites:
        # A support-only package is certified by deterministic compilation and
        # operational preflight in the prerequisite lane.  Running a complete
        # user task here creates the circular requirement that support must
        # already solve the task before a behavior candidate can inherit it.
        support_ids = [item.candidate_id for item in support_prerequisites]
        deferred_behavior_ids = {
            candidate.candidate_id
            for candidate in conformance_candidates
            if candidate.candidate_id not in support_ids
        }
        for candidate_id in deferred_behavior_ids:
            deferred_key = (
                attempt_keys.get(candidate_id)
                if attempt_keys is not None
                else None
            )
            if (
                attempt_tracker is not None
                and deferred_key is not None
                and not attempt_tracker.terminal(deferred_key)
            ):
                attempt_tracker.emit(
                    deferred_key,
                    CandidateAttemptStage.NOT_RUN,
                    reason_code="deferred_until_support_composition",
                )
        screening_report = {
            "screening_strategy": "evaluation_support_prerequisite_lane",
            "screening_role": "deferred_to_deterministic_support_preflight",
            "generated_candidate_count": len(conformance_candidates),
            "attempted_candidate_count": 0,
            "selected_candidate_id": support_ids[0],
            "selected_candidate_ids": support_ids,
            "candidate_dispositions": {
                candidate.candidate_id: (
                    "promoted_to_prerequisite_preflight"
                    if candidate.candidate_id in support_ids
                    else "deferred_until_support_composition"
                )
                for candidate in conformance_candidates
            },
            "selection_reason": (
                "support-only candidates use deterministic prerequisite "
                "preflight before target-behavior composition"
            ),
            "attempts": [],
            "stopped_by_shared_infrastructure": False,
            "stopped_after_budget_censor": False,
            "screening_outcome": "support_preflight",
            "phenotype_duplicate_of": dict(phenotype_duplicates),
        }
        return (
            support_prerequisites,
            _combined_candidate_validation_report(
                candidates=candidates,
                conformance=conformance_report,
                screening=screening_report,
            ),
        )

    if (
        _is_verified_apply_policy(apply_policy)
        and len(conformance_candidates) == 1
        and not require_single_candidate_screening
        and not any(
            (
                repair_conformance_contracts.get(candidate.candidate_id)
                is not None
                and repair_conformance_contracts[
                    candidate.candidate_id
                ].artifact_lifecycle_constraint
                is not None
            )
            for candidate in conformance_candidates
        )
    ):
        sole_candidate = conformance_candidates[0]
        screening_report = {
            "screening_strategy": "single_candidate_direct_authoritative",
            "screening_role": "ranking_not_required",
            "generated_candidate_count": 1,
            "attempted_candidate_count": 0,
            "physical_pair_execution_count": 0,
            "selected_candidate_id": sole_candidate.candidate_id,
            "selected_candidate_ids": [sole_candidate.candidate_id],
            "candidate_dispositions": {
                sole_candidate.candidate_id: "promoted_to_authoritative",
            },
            "selection_reason": (
                "the first sole conforming candidate proceeds directly; "
                "later counterexample repairs require screening"
            ),
            "attempts": [],
            "stopped_by_shared_infrastructure": False,
            "stopped_after_budget_censor": False,
            "screening_outcome": "not_required",
            "phenotype_duplicate_of": dict(phenotype_duplicates),
        }
        return (
            conformance_candidates,
            _combined_candidate_validation_report(
                candidates=candidates,
                conformance=conformance_report,
                screening=screening_report,
            ),
        )

    intervention_case_ids = (
        _candidate_task_plane_intervention_case_ids(
            capability_requirements
        )
        if any(
            _candidate_requires_task_plane_intervention(candidate)
            for candidate in conformance_candidates
        )
        else ()
    )
    configured_screening_panel = _candidate_screening_dataset(
        dataset,
        capability_requirements=capability_requirements,
        max_cases=runtime.candidate_screening_max_cases,
        required_case_ids=intervention_case_ids,
        allow_held_out_control_rescue=(
            len(conformance_candidates) == 1
        ),
        empirical_observations=runtime.case_observations,
    )
    qualification_case_limit = _candidate_screening_qualification_case_limit(
        candidate_count=len(conformance_candidates),
        configured_max_cases=runtime.candidate_screening_max_cases,
    )
    screening_dataset = _candidate_screening_dataset(
        dataset,
        capability_requirements=capability_requirements,
        max_cases=qualification_case_limit,
        required_case_ids=intervention_case_ids,
        allow_held_out_control_rescue=(
            len(conformance_candidates) == 1
        ),
        empirical_observations=runtime.case_observations,
    )
    if (
        not _is_verified_apply_policy(apply_policy)
        or screening_dataset is None
    ):
        return conformance_candidates, _combined_candidate_validation_report(
            candidates=candidates,
            conformance=conformance_report,
            screening=None,
        )

    representative_case_ids = tuple(
        case.case_id for case in screening_dataset.cases
    )
    configured_representative_case_ids = (
        tuple(case.case_id for case in configured_screening_panel.cases)
        if configured_screening_panel is not None
        else representative_case_ids
    )
    screening_max_steps = _candidate_screening_max_steps(
        screening_dataset,
        configured_max_steps=runtime.replay_max_steps,
    )
    _emit_progress(
        runtime.progress_callback,
        "candidate_screening",
        (
            "Screening candidate population on representative case panel "
            f"{','.join(representative_case_ids)} "
            f"({len(conformance_candidates)} candidate(s)); staged "
            f"qualification {len(representative_case_ids)}/"
            f"{len(configured_representative_case_ids)} configured case(s)"
        ),
    )
    attempts: list[dict[str, object]] = []
    selected_candidate: CandidateVariant | None = None
    passing_candidates: list[
        tuple[CandidateVariant, tuple[int, ...]]
    ] = []
    screening_baseline_replay_dir = find_reusable_baseline_replay_dir(
        store=runtime.store,
        run_id=run_id,
        target=target.identity,
        dataset=screening_dataset,
        baseline_repetitions=1,
        **runtime.baseline_reuse_provenance(
            run_id=run_id,
            target=target,
            dataset=screening_dataset,
        ),
    )
    screening_budget_denied_ids: set[str] = set()
    screening_terminal_ids: set[str] = set()
    stopped_by_shared_screening = False
    stopped_by_shared_measurement = False
    stopped_after_budget_censor = False
    deferred_to_authoritative_after_invalid_control = False
    population_requires_artifact_lifecycle_proof = any(
        repair_conformance_contracts.get(candidate.candidate_id) is not None
        and repair_conformance_contracts[
            candidate.candidate_id
        ].artifact_lifecycle_constraint
        is not None
        for candidate in conformance_candidates
    )
    # Every member of the already-bounded representative panel is a
    # distinct control experiment.  ``invalid_control_patience`` must not
    # truncate that panel and leave a known candidate unevaluated merely
    # because earlier controls were unhealthy.
    control_fallback_limit = len(configured_representative_case_ids)
    run_invalid_control_case_ids = (
        runtime.invalid_control_case_ids_by_run.setdefault(
            run_id,
            set(),
        )
    )
    prequalified_invalid_control_case_ids = tuple(
        case_id
        for case_id in (
            configured_screening_panel.recipe.source.get(
                "quarantined_control_case_ids",
                (),
            )
            if configured_screening_panel is not None
            else ()
        )
        if isinstance(case_id, str)
        and case_id in configured_representative_case_ids
    )
    run_invalid_control_case_ids.update(
        prequalified_invalid_control_case_ids
    )
    for candidate_index, candidate in enumerate(
        conformance_candidates,
        start=1,
    ):
        conformance_contract = repair_conformance_contracts.get(
            candidate.candidate_id
        )
        screening_execution_id = f"{candidate.candidate_id}--screening"
        baseline_cache_offered = screening_baseline_replay_dir is not None
        _emit_progress(
            runtime.progress_callback,
            "candidate_screening",
            (
                f"Screening candidate {candidate_index}/"
                f"{len(conformance_candidates)} "
                f"({candidate.candidate_id}) across "
                f"{len(screening_dataset.cases)} qualification case(s); "
                "baseline cache "
                f"{'offered' if baseline_cache_offered else 'miss'}"
            ),
        )
        screening_budget: BudgetDecision | None = None
        if budget_context is not None:
            screening_budget = budget_context.reserve(
                BudgetStage.SCREENING,
                f"{candidate.candidate_id}-screening",
                units=control_fallback_limit,
            )
            if not screening_budget.allowed:
                screening_budget_denied_ids.add(candidate.candidate_id)
                attempts.append(
                    {
                        "candidate_id": candidate.candidate_id,
                        "screening_candidate_id": screening_execution_id,
                        "passed": False,
                        "reason": (
                            "representative screening was not run because "
                            "budget was denied"
                        ),
                        "details": {
                            "failure_class": "budget",
                            "code": "screening_budget_denied",
                            "budget_decision": screening_budget.to_dict(),
                        },
                    }
                )
                attempt_key = (
                    attempt_keys.get(candidate.candidate_id)
                    if attempt_keys is not None
                    else None
                )
                if attempt_tracker is not None and attempt_key is not None:
                    attempt_tracker.emit(
                        attempt_key,
                        CandidateAttemptStage.NOT_RUN,
                        reason_code="screening_budget_denied",
                    )
                continue
        attempt_key = (
            attempt_keys.get(candidate.candidate_id)
            if attempt_keys is not None
            else None
        )
        screening_replay_started = False
        screening_physical_pair_count = 0

        def screening_lifecycle(
            stage: str,
            payload: Mapping[str, object],
        ) -> None:
            nonlocal screening_replay_started, screening_physical_pair_count
            if stage == "replay_started":
                screening_replay_started = True
                screening_physical_pair_count += 1
            if attempt_tracker is None or attempt_key is None:
                return
            if (
                stage == "adaptation_completed"
                and attempt_tracker.last_stage(attempt_key)
                is CandidateAttemptStage.LOCAL_GATES
            ):
                attempt_tracker.emit(
                    attempt_key,
                    CandidateAttemptStage.ADAPTATION,
                    case_count=len(screening_dataset.cases),
                )
            elif stage == "replay_started":
                if screening_physical_pair_count == 1:
                    attempt_tracker.emit(
                        attempt_key,
                        CandidateAttemptStage.SCREENING,
                        case_count=len(screening_dataset.cases),
                        usage=(
                            _budget_usage_for_attempt_event(screening_budget)
                            if screening_budget is not None
                            else None
                        ),
                    )
        screening_telemetry_before = _stage_telemetry_usage_snapshot(
            runtime.execution_telemetry,
            "replay",
        )
        screening_started_at = time.monotonic()
        screening_hard_limit_seconds = (
            policy.hard_limit_seconds(screening_budget)
            if screening_budget is not None
            else None
        )
        screening_deadline = (
            screening_started_at + screening_hard_limit_seconds
            if screening_hard_limit_seconds is not None
            else None
        )
        screening_stage_deadline_exceeded = False
        active_screening_dataset = screening_dataset
        active_baseline_replay_dir = screening_baseline_replay_dir
        control_case_attempts: list[dict[str, object]] = []
        control_frontier_exhausted = False
        replay_result: CandidateReplayResult | None = None
        replay_dataset: SelfEvolveDataset | None = None
        replay_gate: GateResult | None = None
        try:
            active_representative_case_ids = tuple(
                case_id
                for case_id in representative_case_ids
                if case_id not in run_invalid_control_case_ids
            )
            fallback_case_ids = tuple(
                case_id
                for case_id in configured_representative_case_ids
                if case_id not in active_representative_case_ids
                and case_id not in run_invalid_control_case_ids
            )
            control_case_datasets: list[SelfEvolveDataset] = []
            if active_representative_case_ids:
                control_case_datasets.append(
                    screening_dataset
                    if active_representative_case_ids
                    == representative_case_ids
                    else _candidate_screening_dataset_for_case_ids(
                        configured_screening_panel or screening_dataset,
                        case_ids=active_representative_case_ids,
                    )
                )
            control_case_datasets.extend(
                _candidate_screening_dataset_for_case_ids(
                    configured_screening_panel,
                    case_ids=(case_id,),
                )
                for case_id in fallback_case_ids
                if configured_screening_panel is not None
            )
            attempted_control_dataset_count = 0
            for control_panel_index, candidate_screening_dataset in enumerate(
                control_case_datasets[:control_fallback_limit],
                start=1,
            ):
                active_screening_dataset = candidate_screening_dataset
                candidate_screening_case_ids = tuple(
                    case.case_id
                    for case in candidate_screening_dataset.cases
                )
                active_baseline_replay_dir = (
                    screening_baseline_replay_dir
                    if control_panel_index == 1
                    and candidate_screening_case_ids
                    == representative_case_ids
                    else None
                )
                if control_panel_index > 1:
                    _emit_progress(
                        runtime.progress_callback,
                        "candidate_screening",
                        (
                            "Retrying representative screening after an "
                            "invalid control on fallback case "
                            f"{active_screening_dataset.cases[0].case_id} "
                            f"({control_panel_index}/{control_fallback_limit})"
                        ),
                    )
                active_screening_max_steps = _candidate_screening_max_steps(
                    active_screening_dataset,
                    configured_max_steps=runtime.replay_max_steps,
                )
                active_case_id = active_screening_dataset.cases[0].case_id
                support_adaptation: ReplayAdaptationBundle | None = None
                if target.identity.path is not None:
                    support_overlay = create_candidate_skill_overlay(
                        workspace_root=runtime.store.workspace_root,
                        run_id=run_id,
                        candidate=candidate,
                        target_skill_path=target.identity.path,
                        baseline_skill_roots=getattr(
                            target, "baseline_skill_roots", ()
                        ),
                    )
                    support_adaptation, _support_gate = (
                        runtime.prepare_adaptation(
                            run_id=run_id,
                            dataset=active_screening_dataset,
                            capability_skill_root=(
                                support_overlay.candidate_skill_path.parent
                            ),
                            candidate_package_fingerprint=(
                                candidate_package_fingerprint(candidate)
                            ),
                            emit_progress=False,
                        )
                    )
                active_screening_timeout = _candidate_screening_timeout(
                    runtime.replay_timeout_seconds,
                    max_steps=active_screening_max_steps,
                    empirical_observation=(
                        runtime.case_observations.get(active_case_id)
                    ),
                )
                screening_experiment = runtime.plan_measurement(
                    run_id=run_id,
                    target=target,
                    dataset=active_screening_dataset,
                    candidate=candidate,
                    candidate_count=len(conformance_candidates),
                    experiment_registry=runtime.measurement_experiments,
                    experiment_key=(
                        run_id,
                        candidate.candidate_id,
                        replay_dataset_fingerprint(
                            active_screening_dataset
                        ),
                    ),
                    selection_protocol=(
                        "staged_qualification_candidate"
                    ),
                    repetitions=1,
                    minimum_independent_cases=1,
                )
                escalated = False
                while True:
                    control_attempt_started_at = time.monotonic()
                    active_control_case_ids = tuple(
                        case.case_id
                        for case in active_screening_dataset.cases
                    )
                    control_identities = (
                        tuple(
                            (
                                runtime.control_qualification_identity
                                or _control_qualification_identity
                            )(
                                case_id=case_id,
                                baseline_skill_fingerprint=(
                                    target.fingerprint_current_content()
                                ),
                                replay_adaptation=support_adaptation,
                                timeout_seconds=active_screening_timeout,
                                max_steps=active_screening_max_steps,
                                max_tool_calls=(
                                    _DEFAULT_CANDIDATE_SCREENING_TOOL_CALL_LIMIT
                                ),
                            )
                            for case_id in active_control_case_ids
                        )
                        if support_adaptation is not None
                        else ()
                    )
                    circuit_gates = tuple(
                        gate
                        for identity in control_identities
                        if (
                            gate := (
                                policy
                                .support_specific_control_circuit_breaker_gate(
                                    control_identity=identity,
                                    control_observations=(
                                        runtime.control_observations
                                    ),
                                )
                            )
                        )
                        is not None
                    )
                    support_control_circuit_open = bool(
                        control_identities
                        and len(circuit_gates) == len(control_identities)
                    )
                    remaining_screening_seconds = (
                        max(0.0, screening_deadline - time.monotonic())
                        if screening_deadline is not None
                        else None
                    )
                    if support_control_circuit_open:
                        replay_result = None
                        replay_dataset = None
                        replay_gate = circuit_gates[0]
                    elif remaining_screening_seconds == 0.0:
                        replay_result = None
                        replay_dataset = None
                        replay_gate = (
                            policy.stage_budget_censor_gate(
                                hard_limit_seconds=(
                                    screening_hard_limit_seconds or 0.0
                                ),
                                elapsed_seconds=max(
                                    0.0,
                                    time.monotonic() - screening_started_at,
                                ),
                                candidate_execution_observed=(
                                    screening_replay_started
                                ),
                            )
                        )
                        screening_stage_deadline_exceeded = True
                    else:
                        replay_operation = runtime.replay_candidate(
                            run_id=run_id,
                            target=target,
                            dataset=active_screening_dataset,
                            selected_candidate=candidate,
                            apply_policy=apply_policy,
                            baseline_replay_dir=active_baseline_replay_dir,
                            baseline_repetitions=1,
                            candidate_repetitions=1,
                            progress_stage="candidate_screening",
                            timeout_seconds=active_screening_timeout,
                            max_steps=active_screening_max_steps,
                            max_tool_calls=(
                                _DEFAULT_CANDIDATE_SCREENING_TOOL_CALL_LIMIT
                            ),
                            lifecycle_callback=screening_lifecycle,
                            artifact_namespace=(
                                f"screening/{active_case_id}"
                                if not escalated
                                else (
                                    f"screening/{active_case_id}-timeout-"
                                    f"{active_screening_timeout}"
                                )
                            ),
                            measurement_experiment=screening_experiment,
                            measurement_stage="screening",
                        )
                        try:
                            if remaining_screening_seconds is None:
                                replay_result, replay_dataset, replay_gate = (
                                    await replay_operation
                                )
                            else:
                                replay_result, replay_dataset, replay_gate = (
                                    await asyncio.wait_for(
                                        replay_operation,
                                        timeout=remaining_screening_seconds,
                                    )
                                )
                        except asyncio.TimeoutError:
                            if (
                                screening_deadline is None
                                or time.monotonic() + 0.001
                                < screening_deadline
                            ):
                                # Preserve a backend-raised TimeoutError as an
                                # infrastructure failure. Only the outer stage
                                # timer is a budget-censoring event.
                                raise
                            replay_result = None
                            replay_dataset = None
                            replay_gate = (
                                policy.stage_budget_censor_gate(
                                    hard_limit_seconds=(
                                        screening_hard_limit_seconds or 0.0
                                    ),
                                    elapsed_seconds=max(
                                        0.0,
                                        time.monotonic() - screening_started_at,
                                    ),
                                    candidate_execution_observed=(
                                        screening_replay_started
                                    ),
                                )
                            )
                            screening_stage_deadline_exceeded = True
                    raw_replay_gate = replay_gate
                    timeout_escalation_required = (
                        bool(
                            raw_replay_gate is not None
                            and isinstance(raw_replay_gate.details, Mapping)
                            and raw_replay_gate.details.get("code")
                            == "screening_support_control_circuit_open"
                        )
                        or (
                            _screening_invalid_control_is_timeout(
                                raw_replay_gate
                            )
                            and not _screening_required_intervention_unobserved(
                                raw_replay_gate
                            )
                        )
                    )
                    baseline_failure_case_ids = (
                        _screening_baseline_failure_case_ids(
                            replay_gate,
                            fallback_case_ids=active_control_case_ids,
                        )
                    )
                    for control_identity in control_identities:
                        if (
                            control_identity.get("case_id")
                            not in baseline_failure_case_ids
                        ):
                            continue
                        replay_gate = (
                            _candidate_support_baseline_incompatibility_gate(
                                replay_gate,
                                control_identity=control_identity,
                                control_observations=runtime.control_observations,
                            )
                        )
                    invalid_control_case_ids = (
                        _screening_invalid_control_case_ids(
                            replay_gate,
                            fallback_case_ids=active_control_case_ids,
                        )
                    )
                    if _screening_gate_has_invalid_control(replay_gate):
                        run_invalid_control_case_ids.update(
                            invalid_control_case_ids
                        )
                    control_details = (
                        replay_gate.details
                        if replay_gate is not None
                        and isinstance(replay_gate.details, Mapping)
                        else {}
                    )
                    control_wall_seconds = max(
                        0.0,
                        time.monotonic() - control_attempt_started_at,
                    )
                    control_attempt = {
                        "case_ids": list(active_control_case_ids),
                        "invalid_control": (
                            _screening_gate_has_invalid_control(replay_gate)
                        ),
                        "invalid_control_case_ids": list(
                            invalid_control_case_ids
                        ),
                        "passed": bool(
                            replay_gate is not None and replay_gate.passed
                        ),
                        "gate_name": (
                            replay_gate.gate_name
                            if replay_gate is not None
                            else None
                        ),
                        "reason": (
                            replay_gate.reason
                            if replay_gate is not None
                            else "screening replay was unavailable"
                        ),
                        "baseline_cache_offered": bool(
                            replay_result is not None
                            and getattr(replay_result, "request", None)
                            is not None
                            and replay_result.request.baseline_replay_dir
                        ),
                        "timeout_seconds": active_screening_timeout,
                        "wall_seconds": control_wall_seconds,
                        "bounded_escalation": escalated,
                        "support_control_circuit_open": (
                            support_control_circuit_open
                        ),
                        "control_identity": (
                            control_identities[0]
                            if len(control_identities) == 1
                            else None
                        ),
                        "control_identities": [
                            dict(identity)
                            for identity in control_identities
                        ],
                        "baseline_status": control_details.get(
                            "baseline_status"
                        ),
                        "candidate_status": control_details.get(
                            "candidate_status"
                        ),
                        "baseline_failure": control_details.get(
                            "baseline_failure"
                        ),
                        "candidate_failure": control_details.get(
                            "candidate_failure"
                        ),
                    }
                    control_case_attempts.append(control_attempt)
                    observation_attempt = {
                        "passed": bool(
                            replay_gate is not None and replay_gate.passed
                        ),
                        "wall_seconds": control_wall_seconds,
                        "details": (
                            replay_gate.details
                            if replay_gate is not None
                            else {"code": "screening_replay_unavailable"}
                        ),
                    }
                    if not support_control_circuit_open:
                        _record_candidate_screening_observation(
                            runtime.case_observations,
                            case_ids=(
                                invalid_control_case_ids
                                if invalid_control_case_ids
                                else active_control_case_ids
                            ),
                            attempt=observation_attempt,
                        )
                        for control_identity in control_identities:
                            _record_support_specific_control_observation(
                                runtime.control_observations,
                                identity=control_identity,
                                attempt={
                                    **observation_attempt,
                                    "wall_seconds": (
                                        control_wall_seconds
                                        / max(1, len(control_identities))
                                    ),
                                },
                            )
                    escalated_timeout = (
                        _candidate_screening_escalated_timeout(
                            active_screening_timeout,
                            authoritative_timeout_seconds=(
                                runtime.replay_timeout_seconds
                            ),
                        )
                        if not escalated
                        and timeout_escalation_required
                        else None
                    )
                    if escalated_timeout is None:
                        break
                    escalated = True
                    active_screening_timeout = escalated_timeout
                    active_baseline_replay_dir = None
                    _emit_progress(
                        runtime.progress_callback,
                        "candidate_screening",
                        (
                            "Retrying the same control with a bounded "
                            f"timeout escalation on {active_case_id}: "
                            f"{control_attempt['timeout_seconds']}s -> "
                            f"{active_screening_timeout}s"
                        ),
                    )
                attempted_control_dataset_count += 1
                if screening_stage_deadline_exceeded:
                    break
                if not _screening_gate_has_invalid_control(replay_gate):
                    break
            attempted_control_limit = min(
                len(control_case_datasets),
                control_fallback_limit,
            )
            control_frontier_exhausted = bool(
                not screening_stage_deadline_exceeded
                and (
                    not control_case_datasets
                    or (
                        attempted_control_limit > 0
                        and attempted_control_dataset_count
                        >= attempted_control_limit
                        and all(
                            attempt.get("invalid_control") is True
                            for attempt in control_case_attempts
                        )
                    )
                )
            )
            if control_frontier_exhausted:
                last_control_details = (
                    dict(replay_gate.details)
                    if replay_gate is not None
                    and isinstance(replay_gate.details, Mapping)
                    else {}
                )
                failure_event = ReplayFailureEvent(
                    code="screening_control_infeasible",
                    owner=FailureOwner.FRAMEWORK,
                    stage=FailureStage.EVALUATION,
                    scope=FailureScope.SHARED_RUN,
                    repairable=True,
                    category="measurement_control",
                    summary=(
                        "all bounded screening controls were invalid or "
                        "right-censored before candidate observation"
                    ),
                    diagnostics={
                        "attempted_control_count": len(control_case_attempts),
                        "prequalified_invalid_control_case_ids": list(
                            prequalified_invalid_control_case_ids
                        ),
                        "invalid_control_case_ids": [
                            case_id
                            for attempt in control_case_attempts
                            for case_id in attempt.get(
                                "invalid_control_case_ids", []
                            )
                            if isinstance(case_id, str)
                        ][:32],
                    },
                )
                payload = failure_event.to_dict()
                replay_gate = GateResult(
                    gate_name="candidate_replay",
                    passed=False,
                    reason=(
                        "representative screening exhausted its control "
                        "fallback panel without a valid candidate comparison"
                    ),
                    details={
                        **last_control_details,
                        "code": "screening_control_infeasible",
                        "failure_class": "framework",
                        "failure_owner": FailureOwner.FRAMEWORK.value,
                        "failure_scope": FailureScope.SHARED_RUN.value,
                        "failure_stage": FailureStage.EVALUATION.value,
                        "repairable": True,
                        "next_action": "repair_framework",
                        "screening_outcome": "invalid_control",
                        "candidate_execution_observed": False,
                        "candidate_intervention_required": bool(
                            intervention_case_ids
                        ),
                        "candidate_intervention_observed": (
                            False if intervention_case_ids else None
                        ),
                        "prequalified_invalid_control_case_ids": list(
                            prequalified_invalid_control_case_ids
                        ),
                        "resume_safe": False,
                        "failure_event": payload,
                        "causal_failure_events": [payload],
                    },
                )
        except Exception as exc:
            replay_result = None
            replay_dataset = None
            failure_event = ReplayFailureEvent(
                code="candidate_screening_infrastructure_error",
                owner=FailureOwner.INFRASTRUCTURE,
                stage=FailureStage.TASK_ROLLOUT,
                scope=FailureScope.SHARED_RUN,
                repairable=False,
                category="candidate_screening",
                summary="candidate screening backend failed",
                diagnostics={"error_type": type(exc).__name__},
            )
            replay_gate = GateResult(
                gate_name="candidate_screening",
                passed=False,
                reason="candidate screening backend failed",
                details={
                    "failure_class": "infrastructure",
                    "code": "candidate_screening_infrastructure_error",
                    "type": type(exc).__name__,
                    "failure_event": failure_event.to_dict(),
                    "causal_failure_events": [failure_event.to_dict()],
                },
            )
        screening_elapsed_seconds = max(
            0.0,
            time.monotonic() - screening_started_at,
        )
        if screening_budget is not None:
            screening_telemetry_after = _stage_telemetry_usage_snapshot(
                runtime.execution_telemetry,
                "replay",
            )
            screening_usage = _stage_telemetry_usage_delta(
                screening_telemetry_before,
                screening_telemetry_after,
            )
            screening_usage = _telemetry_usage_with_observed_wall(
                screening_usage,
                elapsed_seconds=screening_elapsed_seconds,
            )
            budget_context.debit(
                screening_budget,
                usage_observation=screening_usage.observation,
                actual_source=screening_usage.source,
            )
        shared_screening_failure = bool(
            (
                replay_result is not None
                and _shared_replay_failure_blocks_population(replay_result)
            )
            or (
                replay_gate is not None
                and (
                    _gate_has_typed_shared_infrastructure_failure(
                        replay_gate
                    )
                    or _gate_has_typed_shared_measurement_failure(
                        replay_gate
                    )
                )
            )
        )
        shared_measurement_screening_failure = bool(
            replay_gate is not None
            and _gate_has_typed_shared_measurement_failure(replay_gate)
        )
        defer_invalid_control_to_authoritative = bool(
            not population_requires_artifact_lifecycle_proof
            and _screening_control_infeasible_before_candidate_observation(
                replay_gate,
                control_case_attempts=control_case_attempts,
            )
        )
        if (
            attempt_tracker is not None
            and attempt_key is not None
            and replay_result is None
            and (
                replay_gate is None
                or not replay_gate.passed
                or replay_dataset is None
            )
            and not defer_invalid_control_to_authoritative
            and not policy.gate_is_budget_censored(replay_gate)
            and not attempt_tracker.terminal(attempt_key)
        ):
            terminal_stage = (
                CandidateAttemptStage.BLOCKED
                if shared_screening_failure
                else CandidateAttemptStage.REJECTED
            )
            attempt_tracker.emit(
                attempt_key,
                terminal_stage,
                reason_code=(
                    "screening_adaptation_blocked"
                    if terminal_stage is CandidateAttemptStage.BLOCKED
                    else "screening_adaptation_rejected"
                ),
            )
            screening_terminal_ids.add(candidate.candidate_id)
        screening_admission_gate = _replay_evaluator_admission_gate(
            replay_result,
            apply_policy=apply_policy,
        )
        if (
            replay_gate is not None
            and replay_gate.passed
            and screening_admission_gate is not None
            and not screening_admission_gate.passed
        ):
            replay_gate = _with_typed_gate_failure_event(
                screening_admission_gate
            )
        artifact_lifecycle_conformance: RepairConformanceResult | None = None
        if (
            conformance_contract is not None
            and conformance_contract.artifact_lifecycle_constraint is not None
            and replay_result is not None
        ):
            artifact_lifecycle_conformance = (
                evaluate_artifact_lifecycle_conformance(
                    _candidate_artifact_lifecycle_observations(
                        replay_result,
                        dataset=active_screening_dataset,
                    ),
                    conformance_contract,
                )
            )
            if (
                replay_gate is not None
                and replay_gate.passed
                and artifact_lifecycle_conformance is not None
                and not artifact_lifecycle_conformance.passed
            ):
                replay_gate = _repair_conformance_gate(
                    artifact_lifecycle_conformance,
                    contract=conformance_contract,
                )
            elif (
                replay_gate is not None
                and artifact_lifecycle_conformance is not None
            ):
                replay_gate = replace(
                    replay_gate,
                    details={
                        **dict(replay_gate.details or {}),
                        "artifact_lifecycle_conformance": (
                            artifact_lifecycle_conformance.to_dict()
                        ),
                        "repair_conformance": (
                            conformance_contract.to_public_dict()
                        ),
                    },
                )
        if (
            conformance_contract is not None
            and replay_gate is not None
            and not replay_gate.passed
        ):
            replay_gate = replace(
                replay_gate,
                details={
                    **dict(replay_gate.details or {}),
                    "repair_conformance": conformance_contract.to_public_dict(),
                },
            )
        if replay_gate is not None and not replay_gate.passed:
            # Screening is a qualification experiment, never the
            # authoritative measurement graph.  A framework/shared failure
            # may justify a handoff, but it cannot authorize Campaign
            # measurement continuation from this namespace.
            replay_gate = replace(
                replay_gate,
                details={
                    **dict(replay_gate.details or {}),
                    "resume_safe": False,
                    "checkpoint_stage": "screening",
                },
            )
        if replay_result is not None:
            if _replay_result_has_reusable_baseline(
                dataset=active_screening_dataset,
                replay_result=replay_result,
            ) and tuple(
                case.case_id for case in active_screening_dataset.cases
            ) == representative_case_ids:
                screening_baseline_replay_dir = (
                    _baseline_replay_artifact_dir(replay_result)
                )
            refreshed_screening_baseline_dir = (
                find_reusable_baseline_replay_dir(
                    store=runtime.store,
                    run_id=run_id,
                    target=target.identity,
                    dataset=active_screening_dataset,
                    baseline_repetitions=1,
                    **runtime.baseline_reuse_provenance(
                        run_id=run_id,
                        target=target,
                        dataset=active_screening_dataset,
                    ),
                )
            )
            if refreshed_screening_baseline_dir is not None and tuple(
                case.case_id for case in active_screening_dataset.cases
            ) == representative_case_ids:
                screening_baseline_replay_dir = (
                    refreshed_screening_baseline_dir
                )
        passed = bool(
            replay_dataset is not None
            and replay_gate is not None
            and replay_gate.passed
        )
        screening_rank = _candidate_screening_rank(replay_result)
        attempts.append(
            {
                "candidate_id": candidate.candidate_id,
                "screening_candidate_id": screening_execution_id,
                "gate_name": (
                    replay_gate.gate_name
                    if replay_gate is not None
                    else "candidate_screening"
                ),
                "passed": passed,
                "artifact_lifecycle_proof_required": bool(
                    conformance_contract is not None
                    and conformance_contract.artifact_lifecycle_constraint
                    is not None
                ),
                "reason": (
                    replay_gate.reason
                    if replay_gate is not None
                    else "screening replay was unavailable"
                ),
                "details": replay_gate.details if replay_gate is not None else None,
                "screening_rank": _candidate_screening_rank_details(
                    screening_rank
                ),
                "qualification_case_ids": list(representative_case_ids),
                "attempted_control_case_ids": [
                    case_id
                    for item in control_case_attempts
                    for case_id in item["case_ids"]
                ],
                "control_case_attempts": control_case_attempts,
                "control_fallback_count": max(
                    0,
                    len(
                        {
                            tuple(item.get("case_ids", ()))
                            for item in control_case_attempts
                        }
                    )
                    - 1,
                ),
                "control_escalation_count": sum(
                    int(item.get("bounded_escalation") is True)
                    for item in control_case_attempts
                ),
                "support_specific_control_qualification": {
                    "schema_version": (
                        "aworld.self_evolve.support_control_qualification.v1"
                    ),
                    "required": True,
                    "status": (
                        "qualified"
                        if passed
                        else "candidate_support_incompatible"
                        if replay_gate is not None
                        and isinstance(replay_gate.details, Mapping)
                        and replay_gate.details.get("code")
                        == "candidate_replay_support_baseline_incompatible"
                        else "not_qualified"
                    ),
                    "control_identities": [
                        dict(identity)
                        for item in control_case_attempts
                        for identity in (
                            item.get("control_identities")
                            if isinstance(
                                item.get("control_identities"),
                                (list, tuple),
                            )
                            else ()
                        )
                        if isinstance(identity, Mapping)
                    ],
                },
                "baseline_cache_offered": any(
                    item.get("baseline_cache_offered") is True
                    for item in control_case_attempts
                ),
                "wall_seconds": screening_elapsed_seconds,
                "hard_deadline_seconds": screening_hard_limit_seconds,
                "hard_deadline_exceeded": (
                    screening_stage_deadline_exceeded
                ),
                "physical_pair_executed": screening_replay_started,
                "physical_pair_execution_count": (
                    screening_physical_pair_count
                ),
            }
        )
        if passed:
            passing_candidates.append((candidate, screening_rank))
        if policy.attempt_is_budget_censored(
            attempts[-1]
        ):
            # A bounded qualification replay is a ranking experiment.  When
            # both paired variants hit the same screening horizon it contains
            # no directional evidence, so repeating it for every candidate
            # only multiplies cost.  Preserve the ranked population for the
            # authoritative replay, which owns the full execution budget.
            stopped_after_budget_censor = True
            break
        if defer_invalid_control_to_authoritative:
            # Screening is only a bounded ranking optimization. When every
            # eligible train/validation control fails in the baseline phase,
            # it has observed no candidate treatment and therefore cannot
            # reject or rank the frozen candidates. Defer the bounded
            # population to the authoritative full-dataset replay, which
            # retains intervention, evidence, held-out, and promotion gates.
            deferred_to_authoritative_after_invalid_control = True
            break
        if (
            shared_screening_failure
            and not _screening_attempt_requires_candidate_repair(attempts[-1])
        ):
            if (
                attempt_tracker is not None
                and attempt_key is not None
                and not attempt_tracker.terminal(attempt_key)
            ):
                attempt_tracker.emit(
                    attempt_key,
                    CandidateAttemptStage.BLOCKED,
                    reason_code=(
                        "screening_shared_measurement_blocked"
                        if shared_measurement_screening_failure
                        else "screening_shared_infrastructure_blocked"
                    ),
                )
                screening_terminal_ids.add(candidate.candidate_id)
            stopped_by_shared_screening = True
            stopped_by_shared_measurement = (
                shared_measurement_screening_failure
            )
            break

    if passing_candidates and not stopped_by_shared_screening:
        selected_candidate = max(
            passing_candidates,
            key=lambda item: item[1],
        )[0]
    artifact_lifecycle_admission_failed = any(
        _screening_attempt_requires_artifact_lifecycle_proof(item)
        and not _screening_attempt_has_artifact_lifecycle_proof(item)
        for item in attempts
    )

    if stopped_by_shared_screening:
        attempted_ids = {
            str(attempt.get("candidate_id"))
            for attempt in attempts
            if isinstance(attempt.get("candidate_id"), str)
        }
        for pending_candidate in conformance_candidates:
            if pending_candidate.candidate_id in attempted_ids:
                continue
            screening_terminal_ids.add(pending_candidate.candidate_id)
            pending_key = (
                attempt_keys.get(pending_candidate.candidate_id)
                if attempt_keys is not None
                else None
            )
            if (
                attempt_tracker is not None
                and pending_key is not None
                and not attempt_tracker.terminal(pending_key)
            ):
                attempt_tracker.emit(
                    pending_key,
                    CandidateAttemptStage.BLOCKED,
                    reason_code=(
                        "screening_shared_measurement_blocked"
                        if stopped_by_shared_measurement
                        else "screening_shared_infrastructure_blocked"
                    ),
                )

    selection_reason = (
        "all candidates completed representative replay; the strongest "
        "deterministic replay evidence was promoted"
    )
    if selected_candidate is None:
        if stopped_by_shared_screening:
            selection_reason = (
                "screening stopped after a shared measurement prerequisite "
                "failure"
                if stopped_by_shared_measurement
                else "screening stopped after a shared infrastructure failure"
            )
            selected_candidates = ()
        elif deferred_to_authoritative_after_invalid_control:
            selection_reason = (
                "all bounded screening controls failed before candidate "
                "observation; authoritative full replay preserved the "
                "frozen conforming population"
            )
            selected_candidates = tuple(
                candidate
                for candidate in conformance_candidates
                if candidate.candidate_id not in screening_budget_denied_ids
                and candidate.candidate_id not in screening_terminal_ids
            )
        elif artifact_lifecycle_admission_failed:
            selection_reason = (
                "artifact lifecycle repair lacked behavioral screening proof; "
                "authoritative replay admission failed closed"
            )
            selected_candidates = ()
        elif stopped_after_budget_censor:
            selection_reason = (
                "bounded screening right-censored both paired variants; "
                "one deterministic representative was promoted for the "
                "authoritative full replay"
            )
            eligible_candidates = tuple(
                candidate
                for candidate in conformance_candidates
                if candidate.candidate_id not in screening_budget_denied_ids
                and candidate.candidate_id not in screening_terminal_ids
            )
            selected_candidates = eligible_candidates[:1]
        elif any(
            _screening_attempt_requires_candidate_repair(item)
            for item in attempts
        ):
            selection_reason = (
                "screening isolated a repairable candidate-owned replay "
                "failure; authoritative replay deferred to candidate repair"
            )
            selected_candidates = ()
        elif require_single_candidate_screening and any(
            _screening_attempt_is_candidate_failure(item)
            for item in attempts
        ):
            # A sole candidate normally bypasses ranking replay.  Once an
            # earlier authoritative run has produced a counterexample,
            # however, this replay is a repair admission gate: reproducing
            # the candidate-owned failure is directional evidence and must
            # not fall through to the generic "screening inconclusive"
            # preservation path.
            selection_reason = (
                "counterexample screening reproduced a candidate-owned "
                "failure; authoritative replay deferred to candidate repair"
            )
            selected_candidates = ()
        else:
            # Screening is a bounded cost filter, not an acceptance gate. An
            # unavailable or non-comparable baseline contains no evidence that
            # can distinguish candidates, so retain the complete ranked
            # population for authoritative replay instead of discarding viable
            # alternatives.
            selection_reason = (
                "screening was inconclusive; authoritative full replay preserved "
                "the ranked population"
            )
            selected_candidates = tuple(
                candidate
                for candidate in conformance_candidates
                if candidate.candidate_id not in screening_budget_denied_ids
                and candidate.candidate_id not in screening_terminal_ids
            )
    else:
        selected_candidates = (selected_candidate,)
    attempted_ids = {
        str(attempt.get("candidate_id"))
        for attempt in attempts
        if isinstance(attempt.get("candidate_id"), str)
    }
    selected_ids = {
        candidate.candidate_id for candidate in selected_candidates
    }
    if stopped_after_budget_censor:
        for pending_candidate in conformance_candidates:
            if pending_candidate.candidate_id in selected_ids:
                continue
            pending_key = (
                attempt_keys.get(pending_candidate.candidate_id)
                if attempt_keys is not None
                else None
            )
            if (
                attempt_tracker is not None
                and pending_key is not None
                and not attempt_tracker.terminal(pending_key)
            ):
                attempt_tracker.emit(
                    pending_key,
                    CandidateAttemptStage.NOT_RUN,
                    reason_code="screening_right_censored_frontier",
                )
    passing_candidate_ids = {
        candidate.candidate_id for candidate, _ in passing_candidates
    }
    ranked_below_screening_ids = tuple(
        candidate.candidate_id
        for candidate in conformance_candidates
        if candidate.candidate_id in passing_candidate_ids
        and candidate.candidate_id not in selected_ids
        and not stopped_by_shared_screening
    )
    for candidate_id in ranked_below_screening_ids:
        pending_key = (
            attempt_keys.get(candidate_id)
            if attempt_keys is not None
            else None
        )
        if (
            attempt_tracker is not None
            and pending_key is not None
            and not attempt_tracker.terminal(pending_key)
        ):
            attempt_tracker.emit(
                pending_key,
                (
                    CandidateAttemptStage.REJECTED
                    if candidate_id in attempted_ids
                    else CandidateAttemptStage.NOT_RUN
                ),
                reason_code="ranked_below_screening_frontier",
            )
    candidate_dispositions = {
        candidate.candidate_id: (
            "promoted_to_authoritative"
            if candidate.candidate_id in selected_ids
            else (
                "not_run_after_right_censor"
                if stopped_after_budget_censor
                else (
                    "ranked_below_screening_frontier"
                    if candidate.candidate_id in ranked_below_screening_ids
                    else (
                        "screening_rejected"
                        if candidate.candidate_id in attempted_ids
                        else "not_promoted"
                    )
                )
            )
        )
        for candidate in conformance_candidates
    }
    screening_report = {
            "representative_case_id": representative_case_ids[0],
            "representative_case_ids": list(representative_case_ids),
            "representative_case_count": len(representative_case_ids),
            "configured_representative_case_ids": list(
                configured_representative_case_ids
            ),
            "configured_representative_case_count": len(
                configured_representative_case_ids
            ),
            "screening_anchor_case_id": (
                configured_screening_panel.recipe.source.get(
                    "screening_anchor_case_id"
                )
                if configured_screening_panel is not None
                else None
            ),
            "known_feasible_control_case_ids": list(
                configured_screening_panel.recipe.source.get(
                    "known_feasible_control_case_ids", []
                )
                if configured_screening_panel is not None
                else []
            ),
            "held_out_control_rescue_case_ids": list(
                configured_screening_panel.recipe.source.get(
                    "held_out_control_rescue_case_ids", []
                )
                if configured_screening_panel is not None
                else []
            ),
            "required_intervention_case_ids": list(
                configured_screening_panel.recipe.source.get(
                    "required_intervention_case_ids", []
                )
                if configured_screening_panel is not None
                else []
            ),
            "intervention_exposure_case_ids": list(
                configured_screening_panel.recipe.source.get(
                    "intervention_exposure_case_ids", []
                )
                if configured_screening_panel is not None
                else []
            ),
            "quarantined_control_case_ids": list(
                configured_screening_panel.recipe.source.get(
                    "quarantined_control_case_ids", []
                )
                if configured_screening_panel is not None
                else []
            ),
            "invalid_control_case_ids": sorted(
                case_id
                for case_id, observation in (
                    runtime.case_observations.items()
                )
                if _non_negative_int(
                    observation.get("invalid_control_count")
                )
                > 0
            )[: runtime.candidate_screening_max_cases * 8],
            "control_case_retry_suppressed_count": _non_negative_int(
                configured_screening_panel.recipe.source.get(
                    "control_case_retry_suppressed_count"
                )
                if configured_screening_panel is not None
                else 0
            ),
            "qualification_case_limit": qualification_case_limit,
            "screening_strategy": "adaptive_qualification_then_authoritative",
            "generated_candidate_count": len(conformance_candidates),
            "attempted_candidate_count": len(attempts),
            "physical_pair_execution_count": sum(
                _non_negative_int(
                    attempt.get("physical_pair_execution_count")
                )
                for attempt in attempts
            ),
            "control_fallback_count": sum(
                _non_negative_int(attempt.get("control_fallback_count"))
                for attempt in attempts
            ),
            "control_escalation_count": sum(
                _non_negative_int(attempt.get("control_escalation_count"))
                for attempt in attempts
            ),
            "support_control_circuit_open_count": sum(
                int(control.get("support_control_circuit_open") is True)
                for attempt in attempts
                for control in (
                    attempt.get("control_case_attempts")
                    if isinstance(
                        attempt.get("control_case_attempts"),
                        (list, tuple),
                    )
                    else ()
                )
                if isinstance(control, Mapping)
            ),
            "support_specific_control_qualification_count": sum(
                int(
                    isinstance(
                        attempt.get(
                            "support_specific_control_qualification"
                        ),
                        Mapping,
                    )
                )
                for attempt in attempts
            ),
            "screening_wall_seconds": sum(
                float(attempt.get("wall_seconds") or 0.0)
                for attempt in attempts
                if not isinstance(attempt.get("wall_seconds"), bool)
            ),
            "hard_deadline_exceeded_count": sum(
                int(attempt.get("hard_deadline_exceeded") is True)
                for attempt in attempts
            ),
            "termination_budget_axis_counts": (
                _screening_termination_axis_counts(attempts)
            ),
            "empirical_case_observations": {
                case_id: dict(
                    runtime.case_observations.get(
                        case_id,
                        {},
                    )
                )
                for case_id in representative_case_ids
            },
            "selected_candidate_id": (
                selected_candidate.candidate_id
                if selected_candidate is not None
                else (
                    selected_candidates[0].candidate_id
                    if len(selected_candidates) == 1
                    else None
                )
            ),
            "selected_candidate_ids": [
                candidate.candidate_id for candidate in selected_candidates
            ],
            "ranked_below_screening_candidate_ids": list(
                ranked_below_screening_ids
            ),
            "candidate_dispositions": candidate_dispositions,
            "selection_reason": selection_reason,
            "baseline_repetitions": 1,
            "candidate_repetitions": 1,
            "max_steps": screening_max_steps,
            "max_tool_calls": (
                _DEFAULT_CANDIDATE_SCREENING_TOOL_CALL_LIMIT
            ),
            "progressive_repetition": True,
            "screening_role": "qualification_and_ranking",
            "baseline_cache_offered_count": sum(
                1
                for attempt in attempts
                if attempt.get("baseline_cache_offered") is True
            ),
            "single_candidate_qualification": (
                len(conformance_candidates) == 1
            ),
            "authoritative_baseline_repetitions": (
                runtime.baseline_replay_repetitions
            ),
            "authoritative_candidate_repetitions": (
                runtime.candidate_replay_repetitions
            ),
            "attempts": attempts,
            "stopped_by_shared_infrastructure": (
                stopped_by_shared_screening
                and not stopped_by_shared_measurement
            ),
            "stopped_by_shared_measurement": (
                stopped_by_shared_measurement
            ),
            "stopped_by_shared_validation": stopped_by_shared_screening,
            "stopped_by_invalid_control": control_frontier_exhausted,
            "stopped_after_budget_censor": stopped_after_budget_censor,
            "deferred_to_authoritative_after_invalid_control": (
                deferred_to_authoritative_after_invalid_control
            ),
            "screening_outcome": (
                "authoritative_fallback"
                if deferred_to_authoritative_after_invalid_control
                else "invalid_control"
                if control_frontier_exhausted
                else (
                    "right_censored"
                    if stopped_after_budget_censor
                    else "completed"
                )
            ),
        }
    return (
        selected_candidates,
        _combined_candidate_validation_report(
            candidates=candidates,
            conformance=conformance_report,
            screening=screening_report,
        ),
    )
