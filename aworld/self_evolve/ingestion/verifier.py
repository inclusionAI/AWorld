from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .mapping import (
    MappingMaterialization,
    SourceManifest,
    materialize_mapping,
)
from .types import (
    DATASET_INGESTION_GATE_SCHEMA_VERSION,
    DatasetExtractor,
    DatasetMappingSpec,
    IngestionContractError,
    IngestionLimits,
    IngestionMode,
    IngestionQualityReport,
    IngestorTrustLevel,
    FrozenIngestionSnapshot,
    NormalizedCaseRecord,
    SourceInventory,
    fingerprint_json,
)


@dataclass(frozen=True)
class DatasetIngestionGate:
    passed: bool
    reason_code: str
    warning_reason_codes: tuple[str, ...]
    details: Mapping[str, Any]
    gate_name: str = "dataset_ingestion"
    schema_version: str = DATASET_INGESTION_GATE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "gate_name": self.gate_name,
            "passed": self.passed,
            "reason_code": self.reason_code,
            "warning_reason_codes": list(self.warning_reason_codes),
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class IngestionVerificationResult:
    selected_mapping: DatasetMappingSpec
    materialization: MappingMaterialization
    quality_report: IngestionQualityReport
    gate: DatasetIngestionGate
    evaluated_candidate_count: int
    valid_candidate_count: int


@dataclass(frozen=True)
class _EvaluatedCandidate:
    spec: DatasetMappingSpec
    first: MappingMaterialization
    second: MappingMaterialization
    report: IngestionQualityReport
    gate: DatasetIngestionGate

    @property
    def deterministic(self) -> bool:
        return (
            self.first.materialization_fingerprint
            == self.second.materialization_fingerprint
        )

    @property
    def quality_rank(self) -> tuple[float, ...]:
        return (
            float(self.gate.passed),
            self.report.required_asset_coverage_rate,
            self.report.record_coverage_rate,
            self.report.input_present_rate,
            self.report.expected_output_present_rate,
            -float(self.report.unmatched_required_join_count),
            -float(self.report.join_cardinality_violation_count),
            -float(self.report.rejected_record_count),
            self.report.trace_replayable_rate,
        )


class IngestionVerifier:
    """Materialize twice, select deterministically, and enforce mode gates."""

    def __init__(
        self,
        *,
        limits: IngestionLimits | None = None,
        extractors: Iterable[DatasetExtractor] | None = None,
    ) -> None:
        self.limits = limits or IngestionLimits()
        self.extractors = tuple(extractors) if extractors is not None else None

    def verify(
        self,
        source_path: str | Path,
        *,
        inventory: SourceInventory,
        mapping_specs: Sequence[DatasetMappingSpec],
        mode: IngestionMode = IngestionMode.PROPOSAL,
        trust_level: IngestorTrustLevel = IngestorTrustLevel.FRAMEWORK_BUILTIN,
        manifest: SourceManifest | None = None,
        snapshot_frozen: bool = False,
        split_frozen: bool = False,
    ) -> IngestionVerificationResult:
        if not mapping_specs:
            raise IngestionContractError(
                "mapping_protocol_invalid",
                "no mapping candidates were provided",
            )
        evaluated: list[_EvaluatedCandidate] = []
        failures: list[IngestionContractError] = []
        for spec in mapping_specs:
            try:
                first = materialize_mapping(
                    source_path,
                    inventory=inventory,
                    mapping_spec=spec,
                    manifest=manifest,
                    limits=self.limits,
                    extractors=self.extractors,
                )
                second = materialize_mapping(
                    source_path,
                    inventory=inventory,
                    mapping_spec=spec,
                    manifest=manifest,
                    limits=self.limits,
                    extractors=self.extractors,
                )
                deterministic = (
                    first.materialization_fingerprint
                    == second.materialization_fingerprint
                )
                report = build_quality_report(
                    inventory,
                    first,
                    mapping_candidate_count=len(mapping_specs),
                    valid_mapping_candidate_count=0,
                    deterministic_replay_match=deterministic,
                    case_id_stability=(
                        tuple(case.case_id for case in first.normalized_cases)
                        == tuple(case.case_id for case in second.normalized_cases)
                    ),
                    mapping_execution_count=2,
                )
                gate = evaluate_ingestion_gate(
                    report,
                    mode=mode,
                    trust_level=trust_level,
                    snapshot_frozen=snapshot_frozen,
                    split_frozen=split_frozen,
                    manifest=manifest,
                )
                evaluated.append(
                    _EvaluatedCandidate(
                        spec=spec,
                        first=first,
                        second=second,
                        report=report,
                        gate=gate,
                    )
                )
            except IngestionContractError as exc:
                failures.append(exc)
        if not evaluated:
            if failures:
                raise failures[0]
            raise IngestionContractError(
                "mapping_protocol_invalid",
                "no mapping candidate could be evaluated",
            )

        hard_valid = [
            candidate
            for candidate in evaluated
            if (
                not candidate.report.failure_reason_codes
                and not _manifest_policy_failures(
                    candidate.report,
                    manifest,
                )
            )
        ]
        if not hard_valid:
            first = evaluated[0]
            manifest_failures = _manifest_policy_failures(
                first.report,
                manifest,
            )
            first_reason = (
                first.report.failure_reason_codes[0]
                if first.report.failure_reason_codes
                else manifest_failures[0]
                if manifest_failures
                else first.gate.reason_code
            )
            raise IngestionContractError(
                first_reason,
                "all mapping candidates failed ingestion hard gates",
            )
        best_rank = max(candidate.quality_rank for candidate in hard_valid)
        best = [
            candidate
            for candidate in hard_valid
            if candidate.quality_rank == best_rank
        ]
        normalized_identities = {
            candidate.first.normalized_fingerprint for candidate in best
        }
        if len(best) > 1 and len(normalized_identities) > 1:
            raise IngestionContractError(
                "mapping_ambiguous",
                "equally ranked mappings produce different normalized datasets",
            )
        selected = min(best, key=lambda item: item.spec.fingerprint)
        valid_count = len(hard_valid)
        report = build_quality_report(
            inventory,
            selected.first,
            mapping_candidate_count=len(mapping_specs),
            valid_mapping_candidate_count=valid_count,
            deterministic_replay_match=selected.deterministic,
            case_id_stability=selected.report.case_id_stability,
            mapping_execution_count=2,
        )
        gate = evaluate_ingestion_gate(
            report,
            mode=mode,
            trust_level=trust_level,
            snapshot_frozen=snapshot_frozen,
            split_frozen=split_frozen,
            manifest=manifest,
        )
        return IngestionVerificationResult(
            selected_mapping=selected.spec,
            materialization=selected.first,
            quality_report=report,
            gate=gate,
            evaluated_candidate_count=len(mapping_specs),
            valid_candidate_count=valid_count,
        )


def build_quality_report(
    inventory: SourceInventory,
    materialization: MappingMaterialization,
    *,
    mapping_candidate_count: int = 1,
    valid_mapping_candidate_count: int = 1,
    deterministic_replay_match: bool = True,
    case_id_stability: bool = True,
    mapping_execution_count: int = 2,
) -> IngestionQualityReport:
    cases = materialization.normalized_cases
    rejected = materialization.rejected_records
    eligible = max(
        materialization.eligible_record_count,
        len(cases) + len(rejected),
    )
    case_ids = [case.case_id for case in cases]
    duplicate_count = len(case_ids) - len(set(case_ids))
    trace_cases = [case for case in cases if case.trajectory is not None]
    replayable_cases = [
        case for case in trace_cases if case.trace_replayability == "replayable"
    ]
    (
        unknown_status_count,
        terminal_status_rate,
        state_input_rate,
        tool_call_rate,
        opportunity_counts,
    ) = _trajectory_metrics(trace_cases)
    required_assets = set(materialization.required_asset_ids)
    required_asset_coverage = (
        len(required_assets & set(materialization.matched_required_asset_ids))
        / len(required_assets)
        if required_assets
        else 1.0
    )
    supported = sum(
        int(asset.extractor_name is not None) for asset in inventory.assets
    )
    discovered = len(inventory.assets)
    normalized_fingerprint = materialization.normalized_fingerprint
    provisional = {
        "discovered_asset_count": discovered,
        "supported_asset_count": supported,
        "ignored_asset_count": len(inventory.ignored_assets),
        "rejected_asset_count": len(inventory.rejected_assets),
        "total_source_bytes": sum(asset.size_bytes for asset in inventory.assets),
        "mapping_candidate_count": mapping_candidate_count,
        "valid_mapping_candidate_count": valid_mapping_candidate_count,
        "selected_mapping_fingerprint": materialization.mapping_spec.fingerprint,
        "eligible_record_count": eligible,
        "normalized_case_count": len(cases),
        "rejected_record_count": len(rejected),
        "record_coverage_rate": _fraction(len(cases), eligible),
        "required_asset_coverage_rate": required_asset_coverage,
        "input_present_rate": _fraction(
            sum(case.input is not None for case in cases),
            len(cases),
        ),
        "expected_output_present_rate": _fraction(
            sum(case.expected_output is not None for case in cases),
            len(cases),
        ),
        "verification_present_rate": _fraction(
            sum(case.verification_command is not None for case in cases),
            len(cases),
        ),
        "trace_present_rate": _fraction(len(trace_cases), len(cases)),
        "trace_replayable_rate": _fraction(
            len(replayable_cases),
            len(trace_cases),
        ),
        "duplicate_case_id_count": duplicate_count,
        "case_id_stability": case_id_stability,
        "source_fingerprint": inventory.source_root_fingerprint,
        "normalized_dataset_fingerprint": normalized_fingerprint,
        "required_join_count": materialization.required_join_count,
        "unmatched_required_join_count": (
            materialization.unmatched_required_join_count
        ),
        "join_cardinality_violation_count": (
            materialization.join_cardinality_violation_count
        ),
        "deterministic_replay_match": deterministic_replay_match,
        "mapping_execution_count": mapping_execution_count,
        "source_escape_count": materialization.source_escape_count,
        "symlink_rejection_count": sum(
            int(item.reason_code in {"internal_symlink_ignored", "source_symlink_not_allowed"})
            for item in (*inventory.ignored_assets, *inventory.rejected_assets)
        ),
        "generated_executable_count": materialization.generated_executable_count,
        "generated_command_count": materialization.generated_command_count,
        "held_out_value_exposure_count": (
            materialization.held_out_value_exposure_count
        ),
        "unknown_status_count": unknown_status_count,
        "terminal_status_coverage_rate": terminal_status_rate,
        "state_input_coverage_rate": state_input_rate,
        "tool_call_structure_rate": tool_call_rate,
        "unrecovered_failure_count": opportunity_counts["unrecovered_failure"],
        "recovered_path_count": opportunity_counts["recovered_path"],
        "repeated_action_loop_count": opportunity_counts["repeated_action_loop"],
        "no_recovery_opportunity_count": opportunity_counts["none"],
        "agent_confidence": materialization.mapping_spec.agent_confidence,
    }
    failures, warnings = _hard_failures_and_warnings(provisional)
    return IngestionQualityReport(
        **provisional,
        warning_reason_codes=tuple(warnings),
        failure_reason_codes=tuple(failures),
    )


def validate_frozen_snapshot_quality(
    snapshot: FrozenIngestionSnapshot,
) -> None:
    """Reject self-inconsistent persisted or registered-ingestor metrics."""

    report = snapshot.quality_report
    synthetic = MappingMaterialization(
        mapping_spec=snapshot.selected_mapping,
        normalized_cases=snapshot.normalized_cases,
        rejected_records=snapshot.rejected_records,
        eligible_record_count=(
            len(snapshot.normalized_cases) + len(snapshot.rejected_records)
        ),
        selected_asset_ids=tuple(
            sorted(
                {
                    asset_id
                    for case in snapshot.normalized_cases
                    for asset_id in case.source.asset_ids
                }
            )
        ),
        required_asset_ids=(),
        matched_required_asset_ids=(),
        required_join_count=sum(
            int(join.required) for join in snapshot.selected_mapping.joins
        ),
        unmatched_required_join_count=report.unmatched_required_join_count,
        join_cardinality_violation_count=(
            report.join_cardinality_violation_count
        ),
        source_escape_count=report.source_escape_count,
        generated_executable_count=report.generated_executable_count,
        generated_command_count=report.generated_command_count,
        held_out_value_exposure_count=report.held_out_value_exposure_count,
    )
    rebuilt = build_quality_report(
        snapshot.inventory,
        synthetic,
        mapping_candidate_count=report.mapping_candidate_count,
        valid_mapping_candidate_count=report.valid_mapping_candidate_count,
        deterministic_replay_match=report.deterministic_replay_match,
        case_id_stability=report.case_id_stability,
        mapping_execution_count=report.mapping_execution_count,
    )
    derived_fields = (
        "discovered_asset_count",
        "supported_asset_count",
        "ignored_asset_count",
        "rejected_asset_count",
        "total_source_bytes",
        "selected_mapping_fingerprint",
        "eligible_record_count",
        "normalized_case_count",
        "rejected_record_count",
        "record_coverage_rate",
        "input_present_rate",
        "expected_output_present_rate",
        "verification_present_rate",
        "trace_present_rate",
        "trace_replayable_rate",
        "duplicate_case_id_count",
        "source_fingerprint",
        "normalized_dataset_fingerprint",
        "required_join_count",
        "symlink_rejection_count",
        "unknown_status_count",
        "terminal_status_coverage_rate",
        "state_input_coverage_rate",
        "tool_call_structure_rate",
        "unrecovered_failure_count",
        "recovered_path_count",
        "repeated_action_loop_count",
        "no_recovery_opportunity_count",
    )
    mismatches = [
        field_name
        for field_name in derived_fields
        if getattr(report, field_name) != getattr(rebuilt, field_name)
    ]
    if mismatches:
        raise IngestionContractError(
            "quality_report_mismatch",
            "frozen ingestion quality report does not match normalized "
            f"artifacts: {', '.join(mismatches)}",
        )
    if report.mapping_execution_count < 2:
        raise IngestionContractError(
            "mapping_nondeterministic",
            "frozen ingestion must record at least two mapping executions",
        )


def evaluate_ingestion_gate(
    report: IngestionQualityReport,
    *,
    mode: IngestionMode,
    trust_level: IngestorTrustLevel,
    snapshot_frozen: bool,
    split_frozen: bool,
    manifest: SourceManifest | None = None,
) -> DatasetIngestionGate:
    failures, warnings = _hard_failures_and_warnings(
        report.to_dict(public=False)
    )
    if mode == IngestionMode.AUTO_VERIFIED:
        if report.record_coverage_rate < 0.95:
            failures.append("required_record_coverage_insufficient")
        if report.required_asset_coverage_rate != 1.0:
            failures.append("required_asset_coverage_insufficient")
        if report.unmatched_required_join_count:
            failures.append("required_join_unmatched")
        if report.held_out_value_exposure_count:
            failures.append("held_out_value_exposed")
        if trust_level not in {
            IngestorTrustLevel.FRAMEWORK_BUILTIN,
            IngestorTrustLevel.WORKSPACE_ALLOWLISTED,
        }:
            failures.append("ingestor_not_trusted_for_auto_verified")
        if not snapshot_frozen:
            failures.append("normalized_snapshot_not_frozen")
        if not split_frozen:
            failures.append("dataset_split_not_frozen")
        if report.trace_present_rate == 0.0:
            warnings.append("target_evidence_may_be_missing")
    elif trust_level == IngestorTrustLevel.EXTERNAL_UNTRUSTED:
        warnings.append("ingestor_untrusted_proposal_only")

    failures.extend(_manifest_policy_failures(report, manifest))

    failures = _stable_unique(failures)
    warnings = _stable_unique(warnings)
    passed = not failures
    reason = "ingestion_verified" if passed and not warnings else (
        "ingestion_passed_with_warnings" if passed else failures[0]
    )
    details = report.public_projection()
    details["mode"] = mode.value
    details["trust_level"] = trust_level.value
    details["agent_confidence_authoritative"] = False
    details["snapshot_frozen"] = snapshot_frozen
    details["split_frozen"] = split_frozen
    return DatasetIngestionGate(
        passed=passed,
        reason_code=reason,
        warning_reason_codes=tuple(warnings),
        details=details,
    )


def _manifest_policy_failures(
    report: IngestionQualityReport,
    manifest: SourceManifest | None,
) -> list[str]:
    if manifest is None:
        return []
    failures: list[str] = []
    if (
        manifest.policy.expected_output_required
        and report.expected_output_present_rate < 1.0
    ):
        failures.append("expected_output_required")
    if manifest.policy.trace_required and report.trace_present_rate < 1.0:
        failures.append("trace_required")
    observed_rejected_ratio = _fraction(
        report.rejected_record_count,
        report.eligible_record_count,
    )
    if observed_rejected_ratio > manifest.policy.allow_rejected_record_ratio:
        failures.append("manifest_rejected_record_ratio_exceeded")
    return failures


def _hard_failures_and_warnings(
    metrics: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    if metrics["normalized_case_count"] < 1:
        failures.append("normalized_dataset_empty")
    if metrics["duplicate_case_id_count"]:
        failures.append("duplicate_case_identity")
    if metrics["input_present_rate"] != 1.0:
        failures.append("input_missing")
    if not metrics["deterministic_replay_match"] or not metrics["case_id_stability"]:
        failures.append("mapping_nondeterministic")
    if metrics["join_cardinality_violation_count"]:
        failures.append("required_join_cardinality_violation")
    if metrics["source_escape_count"]:
        failures.append("source_escape")
    if metrics["generated_executable_count"]:
        failures.append("generated_executable_not_allowed")
    if metrics["generated_command_count"]:
        failures.append("generated_command_not_allowed")
    if metrics["expected_output_present_rate"] < 1.0:
        warnings.append("expected_output_incomplete")
    if metrics["trace_present_rate"] < 1.0:
        warnings.append("trace_incomplete")
    elif metrics["trace_replayable_rate"] < 1.0:
        warnings.append("trace_not_replayable")
    if metrics["rejected_record_count"]:
        warnings.append("records_rejected")
    if metrics["rejected_asset_count"]:
        warnings.append("assets_rejected")
    if metrics.get("agent_confidence") is not None and metrics["agent_confidence"] < 0.5:
        warnings.append("agent_confidence_low")
    return failures, warnings


def _trajectory_metrics(
    cases: Sequence[NormalizedCaseRecord],
) -> tuple[int, float, float, float, dict[str, int]]:
    unknown_statuses = 0
    terminal_cases = 0
    state_input_steps = 0
    total_steps = 0
    valid_tool_calls = 0
    total_tool_calls = 0
    opportunities = {
        "unrecovered_failure": 0,
        "recovered_path": 0,
        "repeated_action_loop": 0,
        "none": 0,
    }
    for case in cases:
        trajectory = case.trajectory or {}
        steps = trajectory.get("steps", ())
        if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
            continue
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            total_steps += 1
            reward = step.get("reward")
            if isinstance(reward, Mapping) and reward.get("status") == "unknown":
                unknown_statuses += 1
            state = step.get("state")
            if isinstance(state, Mapping) and state.get("input") is not None:
                state_input_steps += 1
            action = step.get("action")
            tool_calls = action.get("tool_calls", ()) if isinstance(action, Mapping) else ()
            if isinstance(tool_calls, Sequence) and not isinstance(
                tool_calls,
                (str, bytes),
            ):
                for tool_call in tool_calls:
                    total_tool_calls += 1
                    if _valid_tool_call(tool_call):
                        valid_tool_calls += 1
        if steps:
            final = steps[-1]
            reward = final.get("reward") if isinstance(final, Mapping) else None
            if isinstance(reward, Mapping) and reward.get("status") != "unknown":
                terminal_cases += 1
        opportunity = _recovery_opportunity(case)
        opportunities[opportunity] += 1
    return (
        unknown_statuses,
        _fraction(terminal_cases, len(cases)),
        _fraction(state_input_steps, total_steps),
        _fraction(valid_tool_calls, total_tool_calls),
        opportunities,
    )


def _recovery_opportunity(case: NormalizedCaseRecord) -> str:
    try:
        from aworld.self_evolve.recovery_trace import (
            trace_pack_recovery_opportunity,
        )
        from aworld.self_evolve.trace_pack import build_trace_pack

        trajectory = case.trajectory or {}
        steps = trajectory.get("steps", ())
        pack = build_trace_pack(
            list(steps),
            source_kind="agentic_source",
            task_id=str(trajectory.get("task_id") or case.case_id),
        )
        result = trace_pack_recovery_opportunity(pack)
        kind = str(result.get("kind") or "none")
        if kind in {
            "unrecovered_failure",
            "recovered_path",
            "repeated_action_loop",
            "none",
        }:
            return kind
    except (TypeError, ValueError, KeyError):
        pass
    return "none"


def _valid_tool_call(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    function = value.get("function")
    if isinstance(function, Mapping):
        return isinstance(function.get("name"), str) and bool(function["name"])
    return isinstance(value.get("name"), str) and bool(value["name"])


def _fraction(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _stable_unique(values: Sequence[str]) -> list[str]:
    return sorted(set(values))


def quality_rank_fingerprint(report: IngestionQualityReport) -> str:
    """Public helper for diagnostics; never an authorization signal."""

    return fingerprint_json(
        {
            "record_coverage_rate": report.record_coverage_rate,
            "required_asset_coverage_rate": report.required_asset_coverage_rate,
            "input_present_rate": report.input_present_rate,
            "expected_output_present_rate": report.expected_output_present_rate,
            "unmatched_required_join_count": report.unmatched_required_join_count,
            "rejected_record_count": report.rejected_record_count,
            "trace_replayable_rate": report.trace_replayable_rate,
        }
    )
