"""Persisted run, replay, and acceptance report projections."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

from aworld.self_evolve.budget import RepairFrontier, SchedulerState
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.failure_events import ReplayFailureEvent
from aworld.self_evolve.history_support import _load_json_mapping, _non_negative_int
from aworld.self_evolve.measurement_control import estimate_measurement_feasibility
from aworld.self_evolve.measurement_planner import measurement_preflight_projection
from aworld.self_evolve.replay import CandidateReplayResult, ReplayVariantResult
from aworld.self_evolve.run_history import _prior_report_paths, _report_matches_target
from aworld.self_evolve.sanitization import public_diagnostic_projection
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.types import EvaluationSummary, GateResult, SelfEvolveTargetRef


def _replay_report(replay_result: CandidateReplayResult) -> dict[str, object]:
    def lifecycle(variant: ReplayVariantResult) -> dict[str, object]:
        return {
            "variant_id": variant.variant_id,
            "status": variant.status,
            "metrics": public_diagnostic_projection(dict(variant.metrics)),
            "stdout_path": variant.stdout_path,
            "stderr_path": variant.stderr_path,
            # Retained for readers of v1 reports.
            "failure": public_diagnostic_projection(
                variant.failure.compatibility_dict()
                if isinstance(variant.failure, ReplayFailureEvent)
                else variant.failure
            ),
            "failure_event": public_diagnostic_projection(
                variant.failure.to_dict()
                if isinstance(variant.failure, ReplayFailureEvent)
                else None
            ),
            "blocked_by": public_diagnostic_projection(
                [event.to_dict() for event in variant.blocked_by]
            ),
        }

    report: dict[str, object] = {
        "request": {
            "run_id": replay_result.request.run_id,
            "task_id": replay_result.request.task_id,
            "candidate_id": replay_result.request.candidate_id,
            "overlay_skill_root": replay_result.request.overlay_skill_root,
            "baseline_replay_dir": replay_result.request.baseline_replay_dir,
            "resume_replay_dir": replay_result.request.resume_replay_dir,
            "timeout_seconds": replay_result.request.timeout_seconds,
            "max_steps": replay_result.request.max_steps,
            "max_tokens": replay_result.request.max_tokens,
            "dataset_fingerprint": replay_result.request.dataset_fingerprint,
            "baseline_skill_fingerprint": (
                replay_result.request.baseline_skill_fingerprint
            ),
            "adaptation_fingerprint": (replay_result.request.adaptation_fingerprint),
            "support_fingerprint": replay_result.request.support_fingerprint,
            "timeout_envelope_fingerprint": (
                replay_result.request.timeout_envelope_fingerprint
            ),
            "workspace_seed_fingerprint": (
                replay_result.request.workspace_seed_fingerprint
            ),
        },
        "overlay_skill_root": replay_result.request.overlay_skill_root,
        "baseline": lifecycle(replay_result.baseline),
        "candidate": lifecycle(replay_result.candidate),
    }
    if replay_result.request.measurement_plan is not None:
        assert replay_result.request.measurement_isolation_decision is not None
        feasibility = estimate_measurement_feasibility(
            replay_result.request.measurement_plan
        )
        report["measurement_control"] = {
            "measurement_plan_fingerprint": (
                replay_result.request.measurement_plan.measurement_plan_fingerprint
            ),
            "isolation_decision_fingerprint": (
                replay_result.request.measurement_plan.isolation_decision_fingerprint
            ),
            "evidence_policy_fingerprint": (
                replay_result.request.measurement_plan.evidence_policy_fingerprint
            ),
            "decision": public_diagnostic_projection(
                dict(replay_result.measurement_decision or {})
            ),
            "preflight": public_diagnostic_projection(
                measurement_preflight_projection(
                    plan=replay_result.request.measurement_plan,
                    feasibility=feasibility,
                    isolation_decision=(
                        replay_result.request.measurement_isolation_decision
                    ),
                )
            ),
        }
    if replay_result.request.replay_adaptation is not None:
        adaptation = replay_result.request.replay_adaptation
        report["adaptation"] = {
            "schema_version": adaptation.schema_version,
            "ready": adaptation.ready,
            "adaptation_fingerprint": adaptation.adaptation_fingerprint,
            "workspace_seed_fingerprint": adaptation.workspace_seed_fingerprint,
            "environment_fingerprint": adaptation.environment_fingerprint,
            "manifest_path": adaptation.manifest_path,
            "environment_snapshot_path": adaptation.environment_snapshot_path,
            "cases": [
                {
                    "case_id": case.case_id,
                    "readiness": case.readiness,
                    "task_input_fingerprint": case.task_input_fingerprint,
                }
                for case in adaptation.cases
            ],
        }
        capability_report = _replay_capability_report(replay_result)
        if capability_report is not None:
            report["replay_capability"] = capability_report
    if replay_result.member_results:
        report["members"] = [
            {
                "case_id": member.case_id,
                "baseline_status": member.baseline.status,
                "candidate_status": member.candidate.status,
                "baseline_metrics": public_diagnostic_projection(
                    dict(member.baseline.metrics)
                ),
                "candidate_metrics": public_diagnostic_projection(
                    dict(member.candidate.metrics)
                ),
                "baseline_failure": lifecycle(member.baseline)["failure"],
                "candidate_failure": lifecycle(member.candidate)["failure"],
                "baseline_lifecycle": lifecycle(member.baseline),
                "candidate_lifecycle": lifecycle(member.candidate),
            }
            for member in replay_result.member_results
        ]
    return report


def _replay_capability_report(
    replay_result: CandidateReplayResult,
) -> dict[str, object] | None:
    adaptation = replay_result.request.replay_adaptation
    capability = adaptation.replay_capability if adaptation is not None else None
    if capability is None:
        return None
    frozen_root = Path(capability.frozen_root)
    return {
        "source": "candidate",
        "capability_id": capability.capability_id,
        "capability_package_fingerprint": (capability.capability_package_fingerprint),
        "request_fingerprint": capability.request_fingerprint,
        "frozen_capability_fingerprint": capability.fingerprint,
        "deterministic": capability.deterministic,
        "ready": capability.ready,
        "handled_requirements": list(capability.handled_requirements),
        "unhandled_requirements": list(capability.unhandled_requirements),
        "frozen_root": capability.frozen_root,
        "compile_a_path": str(frozen_root.parent / "compile-a"),
        "compile_b_path": str(frozen_root.parent / "compile-b"),
        "frozen_manifest_path": str(frozen_root / "frozen_manifest.json"),
        "fixtures": [
            {"path": item.path, "sha256": item.sha256, "size": item.size}
            for item in capability.fixtures
        ],
        "runtime_files": [
            {"path": item.path, "sha256": item.sha256, "size": item.size}
            for item in capability.runtime_files
        ],
        "service_ids": [item.service_id for item in capability.services],
    }


def _evaluator_report_paths(
    *summaries: EvaluationSummary | None,
) -> list[str]:
    paths: list[str] = []
    for summary in summaries:
        if summary is None:
            continue
        path = summary.metrics.get("report_path")
        if isinstance(path, str) and path not in paths:
            paths.append(path)
    return paths


def _repair_frontier_state_report(
    *,
    store: FilesystemSelfEvolveStore,
    target: SelfEvolveTargetRef,
    current_run_id: str,
    allowed_run_ids: Iterable[str] | None,
    observed_frontiers: tuple[RepairFrontier, ...],
    scheduler_state: SchedulerState,
    selected_candidate_id: str | None,
    run_succeeded: bool,
    campaign_id: str | None,
    campaign_cycle: int | None,
) -> dict[str, object]:
    previous_records: dict[str, Mapping[str, object]] = {}
    if allowed_run_ids:
        for report_path in _prior_report_paths(
            store,
            current_run_id=current_run_id,
            allowed_run_ids=allowed_run_ids,
        ):
            try:
                report = _load_json_mapping(report_path)
            except Exception:
                continue
            if not _report_matches_target(report, target, require_path=False):
                continue
            previous_state = report.get("repair_frontier_state")
            records = (
                previous_state.get("records")
                if isinstance(previous_state, Mapping)
                else None
            )
            if isinstance(records, list):
                previous_records = {
                    str(item["semantic_key"]): item
                    for item in records
                    if isinstance(item, Mapping)
                    and isinstance(item.get("semantic_key"), str)
                }
                break

    observed = {item.semantic_key: item for item in observed_frontiers}
    records: list[dict[str, object]] = []
    for semantic_key in sorted({*previous_records, *observed}):
        previous = previous_records.get(semantic_key, {})
        frontier = observed.get(semantic_key)
        previous_status = str(previous.get("status") or "active")
        if previous_status not in {"active", "dormant", "resolved", "regressed"}:
            previous_status = "active"
        previous_progress = _non_negative_int(previous.get("current_progress"))
        previous_best = max(
            previous_progress,
            _non_negative_int(previous.get("best_progress")),
        )
        if frontier is None:
            if run_succeeded:
                status = "resolved"
            elif previous_status in {"active", "regressed"}:
                status = "dormant"
            else:
                status = previous_status
            current_progress = previous_progress
            best_progress = previous_best
            owner = str(previous.get("owner") or "candidate")
            scope = str(previous.get("scope") or "candidate")
            repairable = previous.get("repairable") is True
        else:
            current_progress = frontier.progress
            best_progress = max(previous_best, current_progress)
            status = (
                "regressed"
                if previous_status == "resolved"
                or (previous_best > 0 and current_progress < previous_best)
                else "active"
            )
            owner = frontier.owner.value
            scope = frontier.scope.value
            repairable = frontier.repairable
        champion_candidate_id = previous.get("champion_candidate_id")
        if (
            frontier is not None
            and selected_candidate_id is not None
            and current_progress >= previous_best
        ):
            champion_candidate_id = selected_candidate_id
        previous_mutation_families = previous.get("mutation_families")
        if not isinstance(previous_mutation_families, (list, tuple)):
            previous_mutation_families = ()
        records.append(
            {
                "semantic_key": semantic_key,
                "status": status,
                "owner": owner,
                "scope": scope,
                "repairable": repairable,
                "current_progress": current_progress,
                "best_progress": best_progress,
                "first_seen_run_id": str(
                    previous.get("first_seen_run_id") or current_run_id
                ),
                "last_seen_run_id": (
                    current_run_id
                    if frontier is not None
                    else previous.get("last_seen_run_id")
                ),
                "champion_candidate_id": champion_candidate_id,
                "mutation_families": list(
                    scheduler_state.frontier_mutation_families.get(
                        semantic_key,
                        tuple(
                            str(item)
                            for item in previous_mutation_families
                            if isinstance(item, str) and item
                        ),
                    )
                ),
                "regression_count": _non_negative_int(previous.get("regression_count"))
                + (
                    1 if status == "regressed" and previous_status != "regressed" else 0
                ),
            }
        )
    return {
        "schema_version": "aworld.self_evolve.repair_frontier_state.v1",
        "campaign_id": campaign_id,
        "campaign_cycle": campaign_cycle,
        "run_id": current_run_id,
        "records": records,
        "active_count": sum(item["status"] == "active" for item in records),
        "dormant_count": sum(item["status"] == "dormant" for item in records),
        "resolved_count": sum(item["status"] == "resolved" for item in records),
        "regressed_count": sum(item["status"] == "regressed" for item in records),
        "scheduler_state": scheduler_state.to_dict(),
    }


def _trajectory_set_report(dataset: SelfEvolveDataset) -> dict[str, object] | None:
    source = dict(dataset.recipe.source)
    has_trajectory_set_source = source.get("kind") == "trajectory_set"
    auto_grouping = source.get("auto_grouping")
    prior_case_ids = [
        case.case_id
        for case in dataset.cases
        if case.source.get("kind") == "prior_self_evolve_run"
    ]
    member_roles: dict[str, int] = {}
    set_ids: set[str] = set()
    for case in dataset.cases:
        metadata = case.metadata.get("trajectory_set")
        if not isinstance(metadata, Mapping):
            continue
        set_id = metadata.get("set_id")
        if isinstance(set_id, str) and set_id:
            set_ids.add(set_id)
        member = metadata.get("member")
        if isinstance(member, Mapping):
            role = member.get("role")
            if isinstance(role, str) and role:
                member_roles[role] = member_roles.get(role, 0) + 1
    if (
        not has_trajectory_set_source
        and not prior_case_ids
        and not set_ids
        and not auto_grouping
    ):
        return None
    report: dict[str, object] = {
        "source_kind": source.get("kind"),
        "set_ids": sorted(set_ids),
        "case_count": len(dataset.cases),
        "member_roles": member_roles,
        "include_prior_runs": bool(source.get("include_prior_runs")),
        "prior_run_case_count": len(prior_case_ids),
        "prior_run_case_ids": prior_case_ids,
    }
    if isinstance(auto_grouping, Mapping):
        report["auto_grouping"] = dict(auto_grouping)
    return report


def _no_op_report(
    gate_results: list[GateResult],
    iteration_reports: list[dict[str, object]],
) -> dict[str, object] | None:
    no_candidate_gate = next(
        (gate for gate in gate_results if gate.gate_name == "no_candidate"),
        None,
    )
    no_candidate_iteration = next(
        (item for item in iteration_reports if item.get("status") == "no_candidate"),
        None,
    )
    if no_candidate_gate is None and no_candidate_iteration is None:
        return None
    return {
        "status": "no_candidate",
        "reason": (
            no_candidate_gate.reason
            if no_candidate_gate is not None
            else "optimizer did not produce a candidate"
        ),
        "iterations": [
            item for item in iteration_reports if item.get("status") == "no_candidate"
        ],
    }


def _acceptance_confidence_report(
    gate_results: list[GateResult],
) -> dict[str, object] | None:
    for gate in gate_results:
        if gate.gate_name != "held_out_verification" or not isinstance(
            gate.details, Mapping
        ):
            continue
        details = gate.details
        verification_mode = details.get("verification_mode")
        verification_split = details.get("verification_split")
        if not isinstance(verification_mode, str) and isinstance(
            verification_split, str
        ):
            verification_mode = verification_split
        if not isinstance(verification_mode, str):
            verification_mode = "unknown"
        return {
            "confidence": details.get("confidence"),
            "verification_mode": verification_mode,
            "verification_split": verification_split,
            "held_out_case_count": details.get("held_out_case_count"),
            "min_eval_cases": details.get("min_eval_cases"),
            "baseline_replay_count": details.get("baseline_replay_count"),
            "candidate_replay_count": details.get("candidate_replay_count"),
            "passed": gate.passed,
        }
    return None
