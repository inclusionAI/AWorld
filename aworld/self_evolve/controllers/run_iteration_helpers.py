"""Local admission, conformance, and iteration lifecycle helpers."""

from __future__ import annotations
import ast
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from aworld.self_evolve.campaign_policy import (
    gate_has_candidate_owned_repair as _gate_has_candidate_owned_repair,
    is_verified_apply_policy as _is_verified_apply_policy,
)
from aworld.self_evolve.evaluation_reporting import _metric_number
from aworld.self_evolve.screening_observation_history import (
    _control_qualification_identity_from_request,
)
from aworld.self_evolve.controllers.screening_execution import (
    _gate_has_typed_shared_measurement_failure,
    _non_negative_int,
)
from aworld.self_evolve.schema_diagnostics import _repair_contract_fingerprint
from aworld.self_evolve.controllers.screening_helpers import (
    _non_negative_screening_float,
    _record_support_specific_control_observation,
    _repairable_capability_failure,
    _screening_attempt_requires_candidate_repair,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayExecutionStatus,
    ReplayFailureEvent,
    _typed_causal_feedback_event,
)
from aworld.self_evolve.repair_conformance_diagnostics import (
    _gate_has_typed_shared_infrastructure_failure,
)
from aworld.self_evolve.replay_gates import (
    _gate_is_replay_execution_infrastructure_failure,
)
from aworld.self_evolve.feedback_diagnostics import _typed_gate_feedback_metrics
from aworld.self_evolve.gates import (
    CandidatePackageGate,
    ExternalCodeEvolutionGate,
    MalformedCandidateGate,
    NewSkillPromotionGate,
    NoopCandidateGate,
    ProtectedPathGate,
    SkillMarkdownGate,
    SkillReleaseFidelityGate,
    TokenLimitGate,
    TrustProvenanceGate,
)
from aworld.self_evolve.optimizers.base import OptimizerResult
from aworld.self_evolve.provenance import (
    InferredNewSkillPolicy,
    TargetMutationIntent,
    TargetProvenance,
)
from aworld.self_evolve.repair_conformance import RepairConformanceContract
from aworld.self_evolve.replay import (
    CandidateReplayResult,
    _baseline_invalid_for_measurement,
    _replay_member_pair_is_comparable,
    normalize_replay_members,
)
from aworld.self_evolve.sanitization import sanitize_source_text, sanitize_text
from aworld.self_evolve.types import CandidateVariant, EvaluationSummary, GateResult

_MAX_REPAIR_CANDIDATE_PACKAGE_CHARS = 64_000
_MAX_REPAIR_CANDIDATE_FILE_CHARS = 32_000
_MAX_MIXED_REPAIR_TARGET_CHARS = 32_000


def _feedback_failure_reference(
    summary: EvaluationSummary,
) -> tuple[str | None, str | None]:
    raw_events = summary.metrics.get("causal_failure_events")
    if not isinstance(raw_events, (list, tuple)):
        return None, None
    for payload in raw_events:
        if not isinstance(payload, Mapping):
            continue
        try:
            event = _typed_causal_feedback_event(payload)
        except (TypeError, ValueError):
            continue
        occurrence_id = event.occurrence_ids[0] if event.occurrence_ids else None
        return occurrence_id, event.semantic_key
    return None, None


def _candidate_conformance_stall_signature(
    failures: Iterable[tuple[CandidateVariant, GateResult]],
) -> str | None:
    """Group repeated typed conformance failures independently of candidate ids."""

    signatures = _candidate_conformance_failure_signatures(failures)
    if not signatures:
        return None
    encoded = json.dumps(
        list(signatures),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _candidate_conformance_failure_signatures(
    failures: Iterable[tuple[CandidateVariant, GateResult]],
) -> tuple[str, ...]:
    """Return atomic typed failure identities for progress-aware retries.

    Batch composition is not repair progress.  Tracking one hash for a whole
    population lets the same failure receive another strategy switch whenever a
    sibling failure appears or disappears.  Atomic identities bound retries per
    actual failed contract instead.
    """

    shapes: dict[str, dict[str, object]] = {}
    for _, gate in failures:
        if gate.gate_name != "candidate_repair_conformance":
            continue
        details = gate.details
        if not isinstance(details, Mapping):
            continue
        failure_fingerprint = details.get("failure_fingerprint")
        shape = {
            "code": details.get("code"),
            "capability_error_code": details.get("capability_error_code"),
            "stage": details.get("stage"),
            "failure_fingerprint": failure_fingerprint,
            "contract_fingerprint": _repair_contract_fingerprint(details),
        }
        if any(value is not None for value in shape.values()):
            identity = json.dumps(
                shape,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            )
            shapes[identity] = shape
    if not shapes:
        return ()
    return tuple(
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                shapes[key],
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        for key in sorted(shapes)
    )


def _candidate_conformance_counterexample_ids(
    failures: Iterable[tuple[CandidateVariant, GateResult]],
) -> set[str]:
    """Return executable counterexample identities still failing this batch."""

    identities: set[str] = set()
    for _, gate in failures:
        details = gate.details
        if not isinstance(details, Mapping):
            continue
        raw_contracts = details.get("counterexample_contracts")
        if not isinstance(raw_contracts, (list, tuple)):
            continue
        for contract in raw_contracts:
            if not isinstance(contract, Mapping):
                continue
            identity = contract.get("counterexample_id")
            if isinstance(identity, str) and identity:
                identities.add(identity)
    return identities


def _candidate_conformance_counterexample_stages(
    failures: Iterable[tuple[CandidateVariant, GateResult]],
) -> dict[str, set[str]]:
    """Group typed conformance counterexamples by the stage that discovered them."""

    stages: dict[str, set[str]] = {}
    for _, gate in failures:
        details = gate.details
        if not isinstance(details, Mapping):
            continue
        raw_contracts = details.get("counterexample_contracts")
        if not isinstance(raw_contracts, (list, tuple)):
            continue
        for contract in raw_contracts:
            if not isinstance(contract, Mapping):
                continue
            identity = contract.get("counterexample_id")
            schema = str(contract.get("schema_version") or "")
            if not isinstance(identity, str) or not identity:
                continue
            if schema == "aworld.self_evolve.schema_counterexample.v1":
                stage = "capability_parse_schema"
            elif schema == "aworld.self_evolve.fixture_probe_counterexample.v1":
                stage = "compiled_probe_conformance"
            else:
                stage = "candidate_conformance"
            stages.setdefault(stage, set()).add(identity)
    return stages


def _candidate_conformance_repair_topologies(
    failures: Iterable[tuple[CandidateVariant, GateResult]],
) -> dict[str, tuple[str, ...]]:
    """Describe actual repair topology per typed failure identity.

    Candidate-family labels are intent, not evidence of a structural switch.
    This fingerprint records the authorized owners, edited package paths, and
    source control/data-flow shape while deliberately ignoring identifiers and
    literal values.
    """

    topologies: dict[str, set[str]] = {}
    for candidate, gate in failures:
        signatures = _candidate_conformance_failure_signatures(((candidate, gate),))
        if not signatures:
            continue
        details = gate.details if isinstance(gate.details, Mapping) else {}
        raw_contract = details.get("repair_conformance")
        contract = raw_contract if isinstance(raw_contract, Mapping) else {}
        owner_paths = sorted(
            str(path)
            for path in tuple(contract.get("required_branch_paths") or ())
            if isinstance(path, str) and path
        )
        edited_files: list[dict[str, object]] = []
        for item in sorted(candidate.files, key=lambda value: value.path):
            if owner_paths and item.path not in owner_paths:
                continue
            source_shape: object | None = None
            if item.operation == "upsert" and isinstance(item.content, str):
                source_shape = _source_control_flow_shape(
                    item.path,
                    item.content,
                )
            edited_files.append(
                {
                    "path": item.path,
                    "operation": item.operation,
                    "source_shape": source_shape,
                }
            )
        raw_counterexamples = details.get("counterexample_contracts")
        output_witnesses = sorted(
            (
                str(item.get("counterexample_id") or ""),
                str(item.get("actual_type") or ""),
                str(item.get("actual_fingerprint") or ""),
            )
            for item in (
                raw_counterexamples
                if isinstance(raw_counterexamples, (list, tuple))
                else ()
            )
            if isinstance(item, Mapping)
            and isinstance(item.get("counterexample_id"), str)
            and isinstance(item.get("actual_fingerprint"), str)
        )
        proof_witnesses = sorted(
            str(item)
            for item in tuple(details.get("proof_fingerprints") or ())
            if isinstance(item, str) and item
        )
        payload = {
            "owner_paths": owner_paths,
            # A typed counterexample is an executable output witness. Source
            # refactors do not constitute a strategy switch while the selected
            # subject retains the same type and content fingerprint.
            "counterexample_output_witnesses": output_witnesses,
            "source_behavior_proof_witnesses": proof_witnesses,
            **(
                {}
                if output_witnesses or proof_witnesses
                else {
                    "edited_files": edited_files,
                    "structural_authorization": (
                        candidate.structural_edit_intent.authorization
                        if candidate.structural_edit_intent is not None
                        else None
                    ),
                }
            ),
        }
        fingerprint = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
        )
        for signature in signatures:
            topologies.setdefault(signature, set()).add(fingerprint)
    return {
        signature: tuple(sorted(values))
        for signature, values in sorted(topologies.items())
    }


def _source_control_flow_shape(path: str, source: str) -> object:
    """Return a bounded, value-free source topology for switch accounting."""

    if Path(path).suffix.casefold() != ".py":
        headings = [
            len(line) - len(line.lstrip("#"))
            for line in source.splitlines()
            if line.startswith("#")
        ]
        return {"kind": "text", "heading_depths": headings[:128]}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"kind": "python_invalid"}
    node_counts = Counter(type(node).__name__ for node in ast.walk(tree))
    edges = Counter(
        (type(parent).__name__, type(child).__name__)
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    )
    return {
        "kind": "python_ast",
        "nodes": sorted(node_counts.items()),
        "edges": [
            [parent, child, count] for (parent, child), count in sorted(edges.items())
        ],
    }


def _candidate_conformance_strategy_switch_feedback(
    *,
    signature: str,
    prior_topology_fingerprints: Sequence[str] = (),
) -> EvaluationSummary:
    event = ReplayFailureEvent(
        code="candidate_conformance_strategy_switch_required",
        owner=FailureOwner.CANDIDATE,
        stage=FailureStage.CAPABILITY_PREFLIGHT,
        scope=FailureScope.CANDIDATE,
        repairable=True,
        category="repair_conformance",
        summary="typed conformance failure requires a structural strategy switch",
        contract_fingerprint=signature,
    ).to_dict()
    return EvaluationSummary(
        variant_id=(
            "candidate-conformance-strategy-switch-"
            f"{signature.removeprefix('sha256:')[:16]}"
        ),
        dataset_split="validation",
        metrics={
            "failed_gates": ["candidate_repair_conformance"],
            "candidate_status": "repair_strategy_switch",
            "failure_class": "candidate",
            "repairable": True,
            "candidate_validation_diagnostics": [
                {
                    "code": "candidate_conformance_strategy_switch_required",
                    "stage": "repair_conformance",
                    "failure_fingerprint": signature,
                    "required_action": (
                        "change the failing data-flow or control-flow topology"
                    ),
                    "prior_topology_fingerprints": list(prior_topology_fingerprints),
                }
            ],
            "failure_event": event,
            "causal_failure_events": [event],
        },
    )


def _infrastructure_prevented_comparable_evaluation(
    failed_gates: Iterable[GateResult],
    *,
    baseline_summary: EvaluationSummary | None,
    candidate_summary: EvaluationSummary | None,
) -> bool:
    # Failed evaluator calls are represented by synthetic summaries so the
    # report remains structurally complete. Their presence does not mean the
    # baseline/candidate pair was actually comparable.
    del baseline_summary, candidate_summary
    gates = tuple(failed_gates)
    has_infrastructure_failure = any(
        isinstance(gate.details, Mapping)
        and gate.details.get("failure_class") == "infrastructure"
        for gate in gates
    )
    has_candidate_owned_failure = any(
        not isinstance(gate.details, Mapping)
        or gate.details.get("failure_class") != "infrastructure"
        for gate in gates
    )
    return has_infrastructure_failure and not has_candidate_owned_failure






def _authoritative_attempt_consumed(
    state: Mapping[str, object],
) -> bool:
    """Return whether an authoritative reservation produced candidate evidence.

    Framework-owned failures before candidate execution are resumable
    measurement work, not candidate opportunities. Candidate-owned gate
    failures do consume the opportunity because they are an authoritative
    conclusion about that candidate package.
    """

    gates = state.get("gate_results")
    if isinstance(gates, (list, tuple)) and any(
        isinstance(gate, GateResult)
        and not gate.passed
        and (
            _gate_has_typed_shared_measurement_failure(gate)
            or _gate_is_replay_execution_infrastructure_failure(gate)
        )
        for gate in gates
    ):
        return False

    replay_result = state.get("replay_result")
    if isinstance(replay_result, CandidateReplayResult):
        members = replay_result.member_results
        if members is None:
            if replay_result.candidate.executed:
                return True
        elif any(member.candidate.executed for member in members):
            return True
    if any(
        state.get(key) is not None for key in ("candidate_summary", "held_out_summary")
    ):
        return True
    if isinstance(gates, (list, tuple)):
        for gate in gates:
            if (
                isinstance(gate, GateResult)
                and not gate.passed
                and isinstance(gate.details, Mapping)
                and (
                    gate.details.get("failure_owner") == FailureOwner.CANDIDATE.value
                    or gate.details.get("failure_class") == "candidate"
                )
            ):
                return True
    return state.get("status") == "accepted"


def _candidate_validation_stopped_by_shared_infrastructure(
    report: Mapping[str, object] | None,
) -> bool:
    if not isinstance(report, Mapping):
        return False
    return any(
        isinstance(stage_report, Mapping)
        and (
            stage_report.get("stopped_by_shared_infrastructure") is True
            or stage_report.get("stopped_by_shared_measurement") is True
            or stage_report.get("stopped_by_shared_validation") is True
        )
        for stage_report in (report.get("conformance"), report.get("screening"))
    )


def _candidate_validation_shared_failure_gate(
    report: Mapping[str, object] | None,
) -> GateResult:
    """Preserve the typed shared blocker instead of emitting no-candidate."""

    if isinstance(report, Mapping):
        for stage_name, gate_name in (
            ("conformance", "candidate_repair_conformance"),
            ("screening", "candidate_replay"),
        ):
            stage_report = report.get(stage_name)
            attempts = (
                stage_report.get("attempts")
                if isinstance(stage_report, Mapping)
                else None
            )
            if not isinstance(attempts, list):
                continue
            for attempt in attempts:
                if not isinstance(attempt, Mapping):
                    continue
                details = attempt.get("details")
                gate = GateResult(
                    gate_name=(
                        str(attempt.get("gate_name"))
                        if isinstance(attempt.get("gate_name"), str)
                        and attempt.get("gate_name")
                        else gate_name
                    ),
                    passed=False,
                    reason=str(
                        attempt.get("reason")
                        or "candidate validation was blocked by a shared framework failure"
                    ),
                    details=(dict(details) if isinstance(details, Mapping) else None),
                )
                if _gate_has_typed_shared_infrastructure_failure(
                    gate
                ) or _gate_has_typed_shared_measurement_failure(gate):
                    return gate

    failure_event = ReplayFailureEvent(
        code="candidate_validation_shared_blocked",
        owner=FailureOwner.FRAMEWORK,
        stage=FailureStage.ADAPTATION,
        scope=FailureScope.SHARED_RUN,
        repairable=False,
        category="candidate_validation",
        summary="candidate validation stopped on a shared framework blocker",
    )
    payload = failure_event.to_dict()
    return GateResult(
        gate_name="candidate_validation",
        passed=False,
        reason="candidate validation stopped on a shared framework blocker",
        details={
            "failure_class": "framework",
            "failure_owner": "framework",
            "failure_scope": "shared_run",
            "repairable": False,
            "code": failure_event.code,
            "failure_event": payload,
            "causal_failure_events": [payload],
        },
    )


def _candidate_repair_conformance_contracts(
    optimizer_result: OptimizerResult,
) -> dict[str, RepairConformanceContract]:
    """Read exact contracts only from the optimizer's ephemeral channel."""

    candidate_ids = {
        candidate.candidate_id for candidate in optimizer_result.candidates
    }
    return {
        candidate_id: contract
        for candidate_id, contract in optimizer_result.private_context.items()
        if candidate_id in candidate_ids
        and isinstance(contract, RepairConformanceContract)
        and contract.focus_candidate_id
        and contract.required_branch_paths
    }


def _candidate_screening_repair_feedback(
    candidates: Iterable[CandidateVariant],
    report: Mapping[str, object] | None,
) -> tuple[EvaluationSummary, ...]:
    failures = _candidate_screening_repair_failures(candidates, report)
    feedback: list[EvaluationSummary] = []
    for candidate, gate in failures:
        feedback.extend(
            _iteration_validation_feedback(
                candidate=candidate,
                baseline_summary=None,
                candidate_summary=None,
                held_out_summary=None,
                failed_gates=[gate],
            )
        )
    return tuple(feedback)


def _candidate_screening_repair_failures(
    candidates: Iterable[CandidateVariant],
    report: Mapping[str, object] | None,
) -> tuple[tuple[CandidateVariant, GateResult], ...]:
    if not isinstance(report, Mapping):
        return ()
    attempts = report.get("attempts")
    if not isinstance(attempts, list):
        return ()
    candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    failures: list[tuple[CandidateVariant, GateResult]] = []
    for attempt in attempts:
        if not isinstance(attempt, Mapping):
            continue
        if not _screening_attempt_requires_candidate_repair(attempt):
            continue
        candidate_id = attempt.get("candidate_id")
        candidate = candidates_by_id.get(str(candidate_id))
        if candidate is None:
            continue
        details = attempt.get("details")
        gate = GateResult(
            gate_name=(
                "candidate_repair_conformance"
                if attempt.get("stage") == "conformance"
                else "candidate_replay"
            ),
            passed=False,
            reason=str(
                attempt.get("reason")
                or "screening replay requires candidate capability repair"
            ),
            details=(dict(details) if isinstance(details, Mapping) else None),
        )
        failures.append((candidate, gate))
    return tuple(failures)


def _iteration_validation_feedback(
    *,
    candidate: CandidateVariant,
    baseline_summary: EvaluationSummary | None,
    candidate_summary: EvaluationSummary | None,
    held_out_summary: EvaluationSummary | None,
    failed_gates: list[GateResult],
) -> tuple[EvaluationSummary, ...]:
    if any(_gate_has_typed_shared_measurement_failure(gate) for gate in failed_gates):
        # An invalid shared experiment has no candidate label.  Preserve the
        # gate in the run report, but never turn it into optimizer feedback or
        # lesson memory.
        return ()
    feedback: list[EvaluationSummary] = []
    typed_gate_metrics = _typed_gate_feedback_metrics(failed_gates)
    typed_candidate_status = next(
        (
            str(gate.details["candidate_status"])
            for gate in failed_gates
            if isinstance(gate.details, Mapping)
            and isinstance(gate.details.get("candidate_status"), str)
        ),
        None,
    )
    if typed_candidate_status is not None:
        typed_gate_metrics["candidate_status"] = typed_candidate_status
    repair_candidate_package = _repair_candidate_package_feedback(
        candidate,
        failed_gates=failed_gates,
    )
    if repair_candidate_package is not None:
        typed_gate_metrics["repair_candidate_package"] = repair_candidate_package
        # This helper is called only from the full candidate evaluation path.
        # Mark its repair frontier explicitly so bounded representative screening
        # or historical task-rollout feedback cannot outrank a later failure
        # discovered across the authoritative dataset.
        typed_gate_metrics["authoritative_replay_failure"] = (
            typed_candidate_status != "prerequisite"
        )
    comparison_metrics = _baseline_comparison_feedback_metrics(
        baseline_summary=baseline_summary,
        candidate_summary=candidate_summary,
    )
    if candidate_summary is not None:
        feedback.append(
            EvaluationSummary(
                variant_id=candidate_summary.variant_id,
                metrics={
                    **dict(candidate_summary.metrics),
                    **comparison_metrics,
                    **typed_gate_metrics,
                    "failed_gates": [gate.gate_name for gate in failed_gates],
                },
                dataset_split=candidate_summary.dataset_split,
            )
        )
    if held_out_summary is not None:
        feedback.append(
            EvaluationSummary(
                variant_id=held_out_summary.variant_id,
                metrics={
                    **dict(held_out_summary.metrics),
                    **typed_gate_metrics,
                    "failed_gates": [gate.gate_name for gate in failed_gates],
                },
                dataset_split=held_out_summary.dataset_split,
            )
        )
    if feedback:
        return tuple(feedback)
    return (
        EvaluationSummary(
            variant_id=candidate.candidate_id,
            metrics={
                **comparison_metrics,
                **typed_gate_metrics,
                "failed_gates": [gate.gate_name for gate in failed_gates],
                "candidate_status": (
                    typed_candidate_status
                    or ("rejected" if failed_gates else "accepted")
                ),
            },
            dataset_split="validation",
        ),
    )


def _bounded_repair_candidate_target_content(
    content: str,
    *,
    has_files: bool,
) -> str:
    """Keep the judge-scored target delta intact whenever it fits the budget."""

    limit = (
        _MAX_MIXED_REPAIR_TARGET_CHARS
        if has_files
        else _MAX_REPAIR_CANDIDATE_PACKAGE_CHARS
    )
    return sanitize_source_text(content, max_chars=limit)


def _repair_candidate_package_feedback(
    candidate: CandidateVariant,
    *,
    failed_gates: Iterable[GateResult],
) -> dict[str, object] | None:
    failed_gate_items = tuple(gate for gate in failed_gates if not gate.passed)
    if not any(_gate_has_candidate_owned_repair(gate) for gate in failed_gate_items):
        return None
    target_content = _bounded_repair_candidate_target_content(
        candidate.content,
        has_files=bool(candidate.files),
    )
    remaining_chars = _MAX_REPAIR_CANDIDATE_PACKAGE_CHARS
    remaining_chars -= len(target_content)
    files: list[dict[str, object]] = []
    for item in candidate.files[:8]:
        file_payload: dict[str, object] = {
            "path": sanitize_text(item.path, max_chars=240),
            "operation": sanitize_text(item.operation, max_chars=40),
            "executable": item.executable,
        }
        if item.content is not None and remaining_chars > 0:
            content_limit = min(
                remaining_chars,
                _MAX_REPAIR_CANDIDATE_FILE_CHARS,
            )
            content = sanitize_source_text(
                item.content,
                max_chars=content_limit,
                preserve_format=True,
            )
            file_payload["content"] = content
            remaining_chars -= len(content)
        files.append(file_payload)
    return {
        "candidate_id": sanitize_text(candidate.candidate_id, max_chars=160),
        "rationale": sanitize_text(candidate.rationale, max_chars=1_000),
        "content": target_content,
        "files": files,
    }


def _record_authoritative_replay_observations(
    observations: dict[str, dict[str, float | int]],
    *,
    dataset: SelfEvolveDataset,
    replay_result: CandidateReplayResult,
    run_observations: dict[str, dict[str, int]] | None = None,
    control_observations: dict[str, dict[str, object]] | None = None,
) -> None:
    """Prioritize bounded counterexample cases in later screening panels."""

    normalized = normalize_replay_members(
        dataset=dataset,
        replay_result=replay_result,
    )
    for member in normalized.members:
        if not member.baseline.executed:
            continue
        invalid_control = _baseline_invalid_for_measurement(member.baseline)
        comparable_pair = _replay_member_pair_is_comparable(
            member.case,
            member.baseline,
            member.candidate,
        )
        candidate_failure = bool(
            member.candidate.status is ReplayExecutionStatus.FAILED
            and _repairable_capability_failure(member.candidate.failure)
        )
        if control_observations is not None:
            control_identity = _control_qualification_identity_from_request(
                member.request
            )
            if control_identity is not None:
                _record_support_specific_control_observation(
                    control_observations,
                    identity=control_identity,
                    attempt={
                        "passed": comparable_pair,
                        "wall_seconds": (
                            _non_negative_screening_float(
                                member.baseline.metrics.get("latency_ms")
                            )
                            / 1000.0
                        ),
                        "details": {
                            "baseline_status": member.baseline.status.value,
                            "candidate_status": member.candidate.status.value,
                            "baseline_failure": (
                                member.baseline.failure.compatibility_dict()
                                if isinstance(
                                    member.baseline.failure,
                                    ReplayFailureEvent,
                                )
                                else member.baseline.failure
                            ),
                            "candidate_failure": (
                                member.candidate.failure.compatibility_dict()
                                if isinstance(
                                    member.candidate.failure,
                                    ReplayFailureEvent,
                                )
                                else member.candidate.failure
                            ),
                        },
                    },
                )
        for destination in (
            observations,
            *((run_observations,) if run_observations is not None else ()),
        ):
            current = destination.setdefault(member.case_id, {})
            current["attempt_count"] = (
                _non_negative_int(current.get("attempt_count")) + 1
            )
            if invalid_control or "invalid_control_count" in current:
                current["invalid_control_count"] = _non_negative_int(
                    current.get("invalid_control_count")
                ) + int(invalid_control)
            if comparable_pair or "passed_count" in current:
                current["passed_count"] = _non_negative_int(
                    current.get("passed_count")
                ) + int(comparable_pair)
            if candidate_failure or "authoritative_failure_count" in current:
                current["authoritative_failure_count"] = _non_negative_int(
                    current.get("authoritative_failure_count")
                ) + int(candidate_failure)


def _baseline_comparison_feedback_metrics(
    *,
    baseline_summary: EvaluationSummary | None,
    candidate_summary: EvaluationSummary | None,
) -> dict[str, float]:
    if baseline_summary is None or candidate_summary is None:
        return {}
    comparison: dict[str, float] = {}
    for metric_key in (
        "score",
        "A1_groundedness",
        "A2_completeness",
        "A3_relevance",
        "A4_readability",
        "B1_tool_use",
        "B2_efficiency",
        "B3_compliance",
        "B4_robustness",
        "evidence_block_count",
        "evidence_incomplete",
        "latency_ms",
    ):
        baseline_value = _metric_number(baseline_summary.metrics, metric_key)
        candidate_value = _metric_number(candidate_summary.metrics, metric_key)
        if baseline_value is None or candidate_value is None:
            continue
        comparison[f"baseline_{metric_key}"] = baseline_value
        comparison[f"candidate_{metric_key}"] = candidate_value
        comparison[f"{metric_key}_delta"] = candidate_value - baseline_value
    return comparison


def _candidate_gate_results(
    candidate: CandidateVariant,
    *,
    current_content: str,
    workspace_root: str | Path,
    max_chars: int,
    target_provenance: TargetProvenance | None,
    target_provenance_unresolved_reason: str | None = None,
    allow_generated_target_mutation: bool = False,
    allow_external_target_mutation: bool = False,
    target_intent: TargetMutationIntent | str | None = None,
    inferred_new_skill_policy: InferredNewSkillPolicy
    | str = InferredNewSkillPolicy.AUTO_VERIFIED,
    apply_policy: str = "proposal",
) -> list[GateResult]:
    token_limit_result = TokenLimitGate(max_chars=max_chars).evaluate(candidate)
    results = [
        NoopCandidateGate().evaluate(
            current_content=current_content, candidate=candidate
        ),
        MalformedCandidateGate().evaluate(candidate),
        CandidatePackageGate().evaluate(candidate),
        token_limit_result,
        ProtectedPathGate(workspace_root=workspace_root).evaluate(candidate),
        ExternalCodeEvolutionGate().evaluate(candidate),
    ]
    if candidate.target.target_type == "skill" and token_limit_result.passed:
        results.append(SkillMarkdownGate().evaluate(candidate))
        results.append(
            SkillReleaseFidelityGate().evaluate(
                candidate,
                current_content=current_content,
                require_exact_deletion_intent=(_is_verified_apply_policy(apply_policy)),
            )
        )
    results.append(
        TrustProvenanceGate(
            allow_generated=allow_generated_target_mutation,
            allow_external=allow_external_target_mutation,
        ).evaluate(
            target_provenance,
            unresolved_reason=target_provenance_unresolved_reason,
            target_intent=target_intent,
        )
    )
    results.append(
        NewSkillPromotionGate().evaluate(
            candidate,
            target_intent=target_intent,
            policy=inferred_new_skill_policy,
            apply_policy=apply_policy,
            workspace_root=workspace_root,
            provenance=target_provenance,
        )
    )
    return results
