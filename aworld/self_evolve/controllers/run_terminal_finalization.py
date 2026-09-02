"""Terminal report enrichment and persistence for explicit-target runs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aworld.self_evolve.controllers.run_terminal import (
    project_post_apply_report,
    release_normalization_report,
)
from aworld.self_evolve.credit_assignment import TargetSelectionReport
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.diagnostics import (
    HarnessDiagnostic,
    extract_harness_diagnostics,
)
from aworld.self_evolve.failure_events import (
    AggregatedReplayFailure,
    aggregate_replay_failures,
)
from aworld.self_evolve.lessons import LessonRecord, extract_lesson_records
from aworld.self_evolve.optimizers.base import CandidateSourceDisposition
from aworld.self_evolve.release_checks import (
    build_content_quality_diagnostics,
    build_release_checklist,
)
from aworld.self_evolve.replay import (
    CandidateReplayResult,
    normalize_replay_members,
)
from aworld.self_evolve.sanitization import public_diagnostic_projection
from aworld.self_evolve.trace_pack import TracePack
from aworld.self_evolve.types import (
    CandidateVariant,
    EvaluationSummary,
    GateResult,
    SelfEvolveRun,
    SelfEvolveRunStatus,
    SelfEvolveTargetRef,
    to_json_dict,
)


ReportProjector = Callable[..., dict[str, object] | None]
FinalReportPersister = Callable[..., Path]


class TerminalFinalizationStore(Protocol):
    """Store operations owned by terminal finalization."""

    def read_all_candidate_attempt_events(self, run_id: str) -> list[Any]: ...

    def write_lesson_records(
        self,
        run_id: str,
        lessons: tuple[LessonRecord, ...],
    ) -> Path: ...

    def write_harness_diagnostics(
        self,
        run_id: str,
        diagnostics: tuple[HarnessDiagnostic, ...],
    ) -> Path: ...


@dataclass(frozen=True)
class TerminalFinalizationRequest:
    """Frozen terminal evidence and report inputs for one explicit run."""

    run_id: str
    target: SelfEvolveTargetRef
    final_status: SelfEvolveRunStatus
    reported_selected_candidate: CandidateVariant | None
    repair_focus_candidate: CandidateVariant | None
    apply_policy: str
    base_report: Mapping[str, Any]
    optimizer_diagnostics: tuple[dict[str, object], ...]
    gate_results: tuple[GateResult, ...]
    scheduler_decisions: tuple[Mapping[str, object], ...]
    population_screening_reports: tuple[Mapping[str, object], ...]
    iteration_states: tuple[Mapping[str, object], ...]
    iteration_reports: tuple[dict[str, object], ...]
    generation_stop_reason: str | None
    dataset: SelfEvolveDataset
    all_candidates: tuple[CandidateVariant, ...]
    replay_candidate_limit: int
    budget_report: Mapping[str, object]
    optimizer_lineage_paths: tuple[str, ...]
    target_selection_report: TargetSelectionReport | None
    post_apply: Mapping[str, object] | None
    promotion: Mapping[str, object] | None
    baseline_summary: EvaluationSummary | None
    candidate_summary: EvaluationSummary | None
    held_out_summary: EvaluationSummary | None
    replay_result: CandidateReplayResult | None
    replay_dataset: SelfEvolveDataset | None
    skill_evolution_progress: Mapping[str, object] | None
    trace_packs: tuple[TracePack, ...]
    candidate_source_dispositions: Mapping[
        str,
        CandidateSourceDisposition,
    ]
    deprecated_config_mappings: Mapping[str, str] | tuple[str, ...]
    previous_artifact_retention: Mapping[str, object] | None

    def __post_init__(self) -> None:
        run_id = self.run_id.strip() if isinstance(self.run_id, str) else ""
        if not run_id:
            raise ValueError("terminal finalization requires a run_id")
        if not isinstance(self.target, SelfEvolveTargetRef):
            raise TypeError("terminal finalization target must be typed")
        if not isinstance(self.final_status, SelfEvolveRunStatus):
            raise TypeError("terminal final status must be typed")
        for field_name in (
            "reported_selected_candidate",
            "repair_focus_candidate",
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, CandidateVariant):
                raise TypeError(f"{field_name} must be typed when present")
        if self.apply_policy not in {
            "proposal",
            "verified_only",
            "auto_verified",
        }:
            raise ValueError(f"unsupported apply policy: {self.apply_policy}")
        object.__setattr__(self, "base_report", dict(self.base_report))
        for field_name in (
            "optimizer_diagnostics",
            "scheduler_decisions",
            "population_screening_reports",
            "iteration_states",
            "iteration_reports",
            "all_candidates",
            "optimizer_lineage_paths",
            "trace_packs",
        ):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))
        if not all(
            isinstance(item, dict) for item in self.optimizer_diagnostics
        ):
            raise TypeError("optimizer_diagnostics must contain dictionaries")
        for field_name in (
            "scheduler_decisions",
            "population_screening_reports",
            "iteration_states",
        ):
            if not all(
                isinstance(item, Mapping) for item in getattr(self, field_name)
            ):
                raise TypeError(f"{field_name} must contain mappings")
        if not all(
            isinstance(item, dict) for item in self.iteration_reports
        ):
            raise TypeError("iteration_reports must contain dictionaries")
        for state in self.iteration_states:
            feedback = state.get("feedback", ())
            if not isinstance(feedback, (list, tuple)) or not all(
                isinstance(item, EvaluationSummary) for item in feedback
            ):
                raise TypeError(
                    "iteration state feedback must contain typed summaries"
                )
        if not all(
            isinstance(candidate, CandidateVariant)
            for candidate in self.all_candidates
        ):
            raise TypeError("all_candidates must contain typed candidates")
        if not all(
            isinstance(path, str) and bool(path)
            for path in self.optimizer_lineage_paths
        ):
            raise TypeError("optimizer lineage paths must be non-empty strings")
        if not all(isinstance(pack, TracePack) for pack in self.trace_packs):
            raise TypeError("trace_packs must contain typed trace packs")
        gates = tuple(self.gate_results)
        if not all(isinstance(gate, GateResult) for gate in gates):
            raise TypeError("terminal finalization gates must be typed")
        object.__setattr__(self, "gate_results", gates)
        if not isinstance(self.dataset, SelfEvolveDataset):
            raise TypeError("terminal finalization dataset must be typed")
        if self.replay_dataset is not None and not isinstance(
            self.replay_dataset,
            SelfEvolveDataset,
        ):
            raise TypeError("replay_dataset must be typed when present")
        for field_name in (
            "baseline_summary",
            "candidate_summary",
            "held_out_summary",
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, EvaluationSummary):
                raise TypeError(f"{field_name} must be typed when present")
        if self.replay_result is not None and not isinstance(
            self.replay_result,
            CandidateReplayResult,
        ):
            raise TypeError("replay_result must be typed when present")
        if not isinstance(self.budget_report, Mapping):
            raise TypeError("budget_report must be a mapping")
        if self.target_selection_report is not None and not isinstance(
            self.target_selection_report,
            TargetSelectionReport,
        ):
            raise TypeError("target_selection_report must be typed when present")
        for field_name in (
            "post_apply",
            "promotion",
            "skill_evolution_progress",
            "previous_artifact_retention",
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, Mapping):
                raise TypeError(f"{field_name} must be a mapping when present")
        if not isinstance(
            self.deprecated_config_mappings,
            (Mapping, tuple),
        ):
            raise TypeError(
                "deprecated_config_mappings must be a mapping or tuple"
            )
        if (
            isinstance(self.replay_candidate_limit, bool)
            or not isinstance(self.replay_candidate_limit, int)
            or self.replay_candidate_limit < 0
        ):
            raise ValueError("replay_candidate_limit must be non-negative")
        dispositions = dict(self.candidate_source_dispositions)
        if not all(
            isinstance(candidate_id, str)
            and candidate_id
            and isinstance(disposition, CandidateSourceDisposition)
            for candidate_id, disposition in dispositions.items()
        ):
            raise TypeError("candidate source dispositions must be typed")
        object.__setattr__(
            self,
            "candidate_source_dispositions",
            dispositions,
        )


@dataclass(frozen=True)
class TerminalFinalizationRuntime:
    """Injected store and compatibility projection seams."""

    store: TerminalFinalizationStore
    terminal_cause: ReportProjector
    rejection_attribution: ReportProjector
    resolved_contract_fingerprints: Callable[..., tuple[str, ...]]
    campaign_failure_attribution: ReportProjector
    trajectory_set_report: ReportProjector
    population_report: ReportProjector
    no_op_report: ReportProjector
    replay_report: Callable[[CandidateReplayResult], dict[str, object]]
    replay_artifact_path: Callable[[CandidateReplayResult], str | None]
    campaign_measurement_outcome: ReportProjector
    replay_capability_report: ReportProjector
    evaluator_report_paths: Callable[..., tuple[str, ...]]
    acceptance_confidence_report: ReportProjector
    finalize_run_report: FinalReportPersister

    def __post_init__(self) -> None:
        for method_name in (
            "read_all_candidate_attempt_events",
            "write_lesson_records",
            "write_harness_diagnostics",
        ):
            if not callable(getattr(self.store, method_name, None)):
                raise TypeError(f"terminal store requires {method_name}")
        for field_name in (
            "terminal_cause",
            "rejection_attribution",
            "resolved_contract_fingerprints",
            "campaign_failure_attribution",
            "trajectory_set_report",
            "population_report",
            "no_op_report",
            "replay_report",
            "replay_artifact_path",
            "campaign_measurement_outcome",
            "replay_capability_report",
            "evaluator_report_paths",
            "acceptance_confidence_report",
            "finalize_run_report",
        ):
            if not callable(getattr(self, field_name)):
                raise TypeError(f"{field_name} must be callable")


@dataclass(frozen=True)
class TerminalFinalizationResult:
    """Persisted terminal report and completed run."""

    completed_run: SelfEvolveRun
    report: Mapping[str, Any]
    report_path: Path
    lesson_records: tuple[LessonRecord, ...]
    harness_diagnostics: tuple[HarnessDiagnostic, ...]


def lesson_extraction_counts(
    lessons: tuple[LessonRecord, ...],
) -> dict[str, object]:
    occurrence_counts = [max(1, lesson.occurrence_count) for lesson in lessons]
    code_counts: dict[str, int] = {}
    code_occurrence_counts: dict[str, int] = {}
    for lesson in lessons:
        code = lesson.metrics.get("causal_code")
        if isinstance(code, str) and code:
            code_counts[code] = code_counts.get(code, 0) + 1
            code_occurrence_counts[code] = (
                code_occurrence_counts.get(code, 0)
                + max(1, lesson.occurrence_count)
            )
    return {
        "count": len(lessons),
        "unique_lesson_count": len(lessons),
        "raw_occurrence_count": sum(occurrence_counts),
        "total_occurrence_count": sum(occurrence_counts),
        "max_occurrence_count": max(occurrence_counts, default=0),
        "codes": code_counts,
        "occurrences_by_code": code_occurrence_counts,
    }


def lesson_type_counts(
    lessons: tuple[LessonRecord, ...],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for lesson in lessons:
        counts[lesson.lesson_type] = counts.get(lesson.lesson_type, 0) + 1
    return counts


def harness_diagnostic_type_counts(
    diagnostics: tuple[HarnessDiagnostic, ...],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for diagnostic in diagnostics:
        kind = diagnostic.kind.value
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def harness_diagnostic_promotion_counts(
    diagnostics: tuple[HarnessDiagnostic, ...],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for diagnostic in diagnostics:
        status = diagnostic.promotion_status.value
        counts[status] = counts.get(status, 0) + 1
    return counts


def final_replay_causal_events(
    *,
    replay_result: CandidateReplayResult | None,
    replay_dataset: SelfEvolveDataset | None,
) -> tuple[AggregatedReplayFailure, ...]:
    """Aggregate final replay failures for harness diagnostics."""

    if replay_result is None:
        return ()
    normalized = (
        normalize_replay_members(
            dataset=replay_dataset,
            replay_result=replay_result,
        )
        if replay_dataset is not None
        else None
    )
    return aggregate_replay_failures(replay_result, normalized=normalized)


def finalize_terminal_run(
    request: TerminalFinalizationRequest,
    *,
    runtime: TerminalFinalizationRuntime,
) -> TerminalFinalizationResult:
    """Enrich and persist the terminal report as one typed transaction."""

    report = dict(request.base_report)
    if request.candidate_source_dispositions:
        report["candidate_source_dispositions"] = {
            candidate_id: disposition.to_dict()
            for candidate_id, disposition in sorted(
                request.candidate_source_dispositions.items()
            )
        }
    if request.deprecated_config_mappings:
        report["deprecated_config_mappings"] = (
            dict(request.deprecated_config_mappings)
            if isinstance(request.deprecated_config_mappings, Mapping)
            else list(request.deprecated_config_mappings)
        )

    terminal_cause = runtime.terminal_cause(
        final_status=request.final_status,
        optimizer_diagnostics=list(request.optimizer_diagnostics),
        gate_results=request.gate_results,
    )
    if terminal_cause is not None:
        report["terminal_cause"] = terminal_cause
    selected_candidate_id = (
        request.repair_focus_candidate.candidate_id
        if request.repair_focus_candidate is not None
        else request.reported_selected_candidate.candidate_id
        if request.reported_selected_candidate is not None
        else None
    )
    rejection_attribution = runtime.rejection_attribution(
        final_status=request.final_status,
        selected_candidate_id=selected_candidate_id,
        gate_results=request.gate_results,
        scheduler_decisions=request.scheduler_decisions,
    )
    if rejection_attribution is not None:
        report["rejection_attribution"] = rejection_attribution
    if request.final_status is SelfEvolveRunStatus.REJECTED:
        resolved_contracts = runtime.resolved_contract_fingerprints(
            request.population_screening_reports
        )
        campaign_failure = runtime.campaign_failure_attribution(
            request.iteration_states,
            generation_stop_reason=request.generation_stop_reason,
            terminal_gates=request.gate_results,
            resolved_contract_fingerprints=resolved_contracts,
        )
        if campaign_failure is not None:
            report["campaign_failure_attribution"] = campaign_failure
        if resolved_contracts:
            report["resolved_conformance_frontiers"] = {
                "count": len(resolved_contracts),
                "contract_fingerprints": list(resolved_contracts),
            }

    trajectory_report = runtime.trajectory_set_report(request.dataset)
    if trajectory_report is not None:
        report["trajectory_set"] = trajectory_report
    population_report = runtime.population_report(
        all_candidates=request.all_candidates,
        iteration_reports=request.iteration_reports,
        replay_candidate_limit=request.replay_candidate_limit,
        optimizer_diagnostics=request.optimizer_diagnostics,
        screening_reports=request.population_screening_reports,
        attempt_events=runtime.store.read_all_candidate_attempt_events(
            request.run_id
        ),
        budget_report=request.budget_report,
        scheduler_decisions=request.scheduler_decisions,
    )
    if population_report is not None:
        report["population"] = population_report
    no_op_report = runtime.no_op_report(
        request.gate_results,
        request.iteration_reports,
    )
    if no_op_report is not None:
        report["no_op"] = no_op_report
    if request.optimizer_lineage_paths:
        report["optimizer_lineage"] = {
            "count": len(request.optimizer_lineage_paths),
            "paths": list(request.optimizer_lineage_paths),
        }
    if request.target_selection_report is not None:
        report["target_selection"] = to_json_dict(
            request.target_selection_report
        )
    report.update(
        project_post_apply_report(
            request.post_apply,
            release_normalization=release_normalization_report,
        )
    )
    if request.promotion is not None:
        report["promotion"] = dict(request.promotion)
    for field_name, summary in (
        ("baseline_metrics", request.baseline_summary),
        ("candidate_metrics", request.candidate_summary),
        ("held_out_metrics", request.held_out_summary),
    ):
        if summary is not None:
            report[field_name] = public_diagnostic_projection(
                dict(summary.metrics)
            )

    if request.replay_result is not None:
        report["replay"] = runtime.replay_report(request.replay_result)
        report["replay_path"] = runtime.replay_artifact_path(
            request.replay_result
        )
        campaign_measurement = runtime.campaign_measurement_outcome(
            request.replay_result,
            final_status=request.final_status,
            gate_results=request.gate_results,
        )
        if campaign_measurement is not None:
            report["campaign_measurement_outcome"] = campaign_measurement
        replay_capability = runtime.replay_capability_report(
            request.replay_result
        )
        if replay_capability is not None:
            report["replay_capability"] = replay_capability
    if request.skill_evolution_progress is not None:
        report["skill_evolution"] = dict(request.skill_evolution_progress)

    replay_evidence_reuse_gate = next(
        (
            gate
            for gate in request.gate_results
            if gate.gate_name == "candidate_replay_evidence_reuse"
        ),
        None,
    )
    if (
        replay_evidence_reuse_gate is not None
        and isinstance(replay_evidence_reuse_gate.details, Mapping)
    ):
        report["replay_evidence_reuse"] = {
            key: replay_evidence_reuse_gate.details.get(key)
            for key in (
                "disposition",
                "provenance_path",
                "source_request_run_id",
                "source_request_candidate_id",
                "source_dataset_fingerprint",
                "current_dataset_fingerprint",
                "dataset_fingerprint_matches",
                "source_dataset_snapshot_fingerprint",
                "current_dataset_snapshot_fingerprint",
                "dataset_snapshot_fingerprint_matches",
                "dataset_authority_matches",
                "replay_case_count",
                "normalized_member_count",
            )
        }
    evaluator_report_paths = runtime.evaluator_report_paths(
        request.baseline_summary,
        request.candidate_summary,
        request.held_out_summary,
    )
    if evaluator_report_paths:
        report["evaluator_report_paths"] = evaluator_report_paths
    if request.gate_results:
        report["gate_results"] = [
            {
                "gate_name": gate.gate_name,
                "passed": gate.passed,
                "reason": public_diagnostic_projection(gate.reason),
                "details": public_diagnostic_projection(gate.details),
            }
            for gate in request.gate_results
        ]
        acceptance_confidence = runtime.acceptance_confidence_report(
            request.gate_results
        )
        if acceptance_confidence is not None:
            report["acceptance_confidence"] = acceptance_confidence
        report["release_checklist"] = build_release_checklist(
            apply_policy=request.apply_policy,
            gate_results=report["gate_results"],
        )

    lesson_records = extract_lesson_records(
        tuple(
            feedback
            for state in request.iteration_states
            for feedback in state.get("feedback", ())
        ),
        target_scope={
            "target_type": request.target.target_type,
            "target_id": request.target.target_id,
        },
        trace_packs=request.trace_packs,
    )
    if lesson_records:
        lessons_path = runtime.store.write_lesson_records(
            request.run_id,
            lesson_records,
        )
        lesson_report = {
            "path": str(lessons_path),
            **lesson_extraction_counts(lesson_records),
            "types": lesson_type_counts(lesson_records),
        }
        report["lessons"] = dict(lesson_report)
        report["lesson_extraction"] = dict(lesson_report)

    harness_diagnostics = extract_harness_diagnostics(
        gate_results=request.gate_results,
        summaries=(
            request.baseline_summary,
            request.candidate_summary,
            request.held_out_summary,
        ),
        replay_result=request.replay_result,
        causal_events=final_replay_causal_events(
            replay_result=request.replay_result,
            replay_dataset=request.replay_dataset,
        ),
    )
    if harness_diagnostics:
        diagnostics_path = runtime.store.write_harness_diagnostics(
            request.run_id,
            harness_diagnostics,
        )
        report["harness_diagnostics"] = {
            "path": str(diagnostics_path),
            "count": len(harness_diagnostics),
            "types": harness_diagnostic_type_counts(harness_diagnostics),
            "promotion_statuses": (
                harness_diagnostic_promotion_counts(harness_diagnostics)
            ),
        }
    content_quality_metrics = (
        dict(request.held_out_summary.metrics)
        if request.held_out_summary is not None
        else dict(request.candidate_summary.metrics)
        if request.candidate_summary is not None
        else {}
    )
    report["content_quality_diagnostics"] = (
        build_content_quality_diagnostics(content_quality_metrics)
    )

    completed_run = SelfEvolveRun(
        run_id=request.run_id,
        target=request.target,
        status=request.final_status,
        selected_candidate_id=(
            request.reported_selected_candidate.candidate_id
            if request.reported_selected_candidate is not None
            else None
        ),
        metrics=tuple(
            summary
            for summary in (
                request.baseline_summary,
                request.candidate_summary,
            )
            if summary is not None
        ),
        gate_results=request.gate_results,
    )
    report_path = runtime.finalize_run_report(
        runtime.store,
        request.run_id,
        report=report,
        completed_run=completed_run,
        previous_artifact_retention=request.previous_artifact_retention,
    )
    return TerminalFinalizationResult(
        completed_run=completed_run,
        report=report,
        report_path=report_path,
        lesson_records=lesson_records,
        harness_diagnostics=harness_diagnostics,
    )
