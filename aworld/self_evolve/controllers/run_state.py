"""Typed mutable state for the explicit-target run state machine."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from aworld.self_evolve.challenger import ChallengeReport
from aworld.self_evolve.controllers.run_execution import (
    CandidateEvaluationResult,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.measurement import AttributionReport, MeasurementSummary
from aworld.self_evolve.regression import RegressionEvidence
from aworld.self_evolve.replay import CandidateReplayResult
from aworld.self_evolve.types import (
    CandidateVariant,
    EvaluationSummary,
    GateResult,
)


FeedbackMerger = Callable[
    [tuple[EvaluationSummary, ...], tuple[EvaluationSummary, ...]],
    tuple[EvaluationSummary, ...],
]
GatePredicate = Callable[[GateResult], bool]
AuthoritativeAttemptConsumed = Callable[[dict[str, object]], bool]
SharedReplayFailurePredicate = Callable[[CandidateReplayResult], bool]
InfrastructureEvaluationPredicate = Callable[..., bool]
IterationStateSelector = Callable[
    [list[dict[str, object]]],
    dict[str, object] | None,
]


@dataclass(frozen=True)
class CandidateEvaluationRecord:
    """One candidate result admitted into the run accumulator."""

    candidate_id: str
    state: dict[str, object]
    report_item: dict[str, object]
    feedback: tuple[EvaluationSummary, ...]
    failed_gates: tuple[GateResult, ...]
    shared_measurement_invalid: bool
    replay_result: CandidateReplayResult | None
    measurement_summary: MeasurementSummary | None

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate evaluation record requires candidate_id")
        if not isinstance(self.state, dict):
            raise TypeError("candidate evaluation record state must be a dict")
        if not isinstance(self.report_item, dict):
            raise TypeError("candidate evaluation report item must be a dict")
        object.__setattr__(self, "feedback", tuple(self.feedback))
        object.__setattr__(self, "failed_gates", tuple(self.failed_gates))


@dataclass(frozen=True)
class CandidateEvaluationLoopDecision:
    """Stop/continue decision after candidate result side effects complete."""

    accepted: bool
    should_stop: bool
    stop_reason: str | None = None


@dataclass(frozen=True)
class ExplicitRunSelection:
    """Typed evidence projected from the selected legacy iteration state."""

    state: dict[str, object]
    selected_candidate: CandidateVariant | None
    baseline_summary: EvaluationSummary | None
    candidate_summary: EvaluationSummary | None
    held_out_summary: EvaluationSummary | None
    regression_evidence: RegressionEvidence | None
    challenge_report: ChallengeReport | None
    replay_result: CandidateReplayResult | None
    replay_dataset: SelfEvolveDataset | None
    gate_results: tuple[GateResult, ...]


@dataclass
class ExplicitRunStateAccumulator:
    """Mutable evidence, quota, and frontier state for one explicit run."""

    validation_feedback: tuple[EvaluationSummary, ...] = ()
    baseline_evaluation_cache: dict[str, EvaluationSummary] = field(
        default_factory=dict
    )
    iteration_reports: list[dict[str, object]] = field(default_factory=list)
    iteration_states: list[dict[str, object]] = field(default_factory=list)
    measurement_attributions: list[AttributionReport] = field(
        default_factory=list
    )
    current_run_attempted_candidate_ids: set[str] = field(
        default_factory=set
    )
    rejected_candidate_ids: set[str] = field(default_factory=set)
    accepted_candidate_ids: set[str] = field(default_factory=set)
    authoritative_candidate_count: int = 0
    authoritative_candidate_attempt_count: int = 0
    authoritative_candidate_ids: set[str] = field(default_factory=set)
    authoritative_candidate_attempt_ids: set[str] = field(
        default_factory=set
    )
    score_tiebreak_candidate_count: int = 0
    prerequisite_candidate_ids: list[str] = field(default_factory=list)
    measurement_frontier_stopped: bool = False
    baseline_preflight_blocked: bool = False
    infrastructure_blocked: bool = False

    def select_iteration_evidence(
        self,
        *,
        fresh_evaluation_required: bool,
        selector: IterationStateSelector,
    ) -> ExplicitRunSelection | None:
        """Select and validate the terminal candidate evidence projection."""

        selected_state = selector(self.iteration_states)
        if selected_state is None:
            return None

        def optional_typed(field_name: str, expected_type: type):
            value = selected_state.get(field_name)
            if value is not None and not isinstance(value, expected_type):
                raise TypeError(
                    f"selected iteration {field_name} must be typed when present"
                )
            return value

        candidate = optional_typed("candidate", CandidateVariant)
        if candidate is None:
            raise TypeError("selected iteration requires a typed candidate")
        raw_gates = selected_state.get("gate_results")
        if not isinstance(raw_gates, (list, tuple)) or not all(
            isinstance(gate, GateResult) for gate in raw_gates
        ):
            raise TypeError("selected iteration gate_results must be typed")
        selected_candidate = (
            candidate
            if (
                not fresh_evaluation_required
                or selected_state.get("status") == "accepted"
            )
            else None
        )
        return ExplicitRunSelection(
            state=selected_state,
            selected_candidate=selected_candidate,
            baseline_summary=optional_typed(
                "baseline_summary",
                EvaluationSummary,
            ),
            candidate_summary=optional_typed(
                "candidate_summary",
                EvaluationSummary,
            ),
            held_out_summary=optional_typed(
                "held_out_summary",
                EvaluationSummary,
            ),
            regression_evidence=optional_typed(
                "regression_evidence",
                RegressionEvidence,
            ),
            challenge_report=optional_typed(
                "challenge_report",
                ChallengeReport,
            ),
            replay_result=optional_typed(
                "replay_result",
                CandidateReplayResult,
            ),
            replay_dataset=optional_typed(
                "replay_dataset",
                SelfEvolveDataset,
            ),
            gate_results=tuple(raw_gates),
        )

    def begin_authoritative_candidate(
        self,
        candidate_id: str,
        *,
        counts_toward_authoritative: bool,
    ) -> None:
        """Reserve one authoritative slot before candidate execution."""

        if not counts_toward_authoritative:
            return
        self.authoritative_candidate_attempt_count += 1
        self.authoritative_candidate_count += 1
        self.authoritative_candidate_attempt_ids.add(candidate_id)

    def record_candidate_evaluation(
        self,
        *,
        candidate_id: str,
        result: CandidateEvaluationResult,
        counts_toward_authoritative: bool,
        merge_feedback: FeedbackMerger,
        shared_measurement_failure: GatePredicate,
        authoritative_attempt_consumed: AuthoritativeAttemptConsumed,
    ) -> CandidateEvaluationRecord:
        """Admit one typed evaluation result into cumulative run evidence."""

        evaluation_state = result.state
        state = evaluation_state.payload
        report_item = result.report_item
        candidate_feedback = result.feedback
        shared_measurement_invalid = any(
            not gate.passed and shared_measurement_failure(gate)
            for gate in evaluation_state.gate_results
        )
        if shared_measurement_invalid:
            candidate_feedback = ()
            state["feedback"] = ()

        if (
            counts_toward_authoritative
            and not authoritative_attempt_consumed(state)
        ):
            if self.authoritative_candidate_count < 1:
                raise RuntimeError(
                    "authoritative candidate reservation underflow"
                )
            self.authoritative_candidate_count -= 1
        elif counts_toward_authoritative:
            self.authoritative_candidate_ids.add(candidate_id)

        raw_gates = state.get("gate_results")
        if not isinstance(raw_gates, (list, tuple)) or not all(
            isinstance(gate, GateResult) for gate in raw_gates
        ):
            raise TypeError("candidate evaluation state gates must be typed")
        gates = tuple(raw_gates)
        if any(
            gate.gate_name == "score_improvement"
            and isinstance(gate.details, Mapping)
            and gate.details.get("tiebreak_round") is not None
            for gate in gates
        ):
            self.score_tiebreak_candidate_count += 1

        if not shared_measurement_invalid:
            self.validation_feedback = merge_feedback(
                self.validation_feedback,
                candidate_feedback,
            )
            self.current_run_attempted_candidate_ids.add(candidate_id)
        self.iteration_reports.append(report_item)
        self.iteration_states.append(state)

        replay_result = state.get("replay_result")
        measurement_summary = state.get("measurement_summary")
        return CandidateEvaluationRecord(
            candidate_id=candidate_id,
            state=state,
            report_item=report_item,
            feedback=candidate_feedback,
            failed_gates=tuple(gate for gate in gates if not gate.passed),
            shared_measurement_invalid=shared_measurement_invalid,
            replay_result=(
                replay_result
                if isinstance(replay_result, CandidateReplayResult)
                else None
            ),
            measurement_summary=(
                measurement_summary
                if isinstance(measurement_summary, MeasurementSummary)
                else None
            ),
        )

    def finalize_candidate_record(
        self,
        record: CandidateEvaluationRecord,
        *,
        shared_replay_failure_blocks_population: (
            SharedReplayFailurePredicate
        ),
        infrastructure_prevented_comparable_evaluation: (
            InfrastructureEvaluationPredicate
        ),
    ) -> CandidateEvaluationLoopDecision:
        """Update candidate labels/frontiers after external side effects."""

        status = record.state.get("status")
        if (
            record.failed_gates
            and status != "prerequisite"
            and not record.shared_measurement_invalid
        ):
            self.rejected_candidate_ids.add(record.candidate_id)
        elif status == "prerequisite":
            self.prerequisite_candidate_ids.append(record.candidate_id)

        if (
            record.replay_result is not None
            and shared_replay_failure_blocks_population(record.replay_result)
        ):
            self.baseline_preflight_blocked = True
            return CandidateEvaluationLoopDecision(
                accepted=False,
                should_stop=True,
                stop_reason="shared_replay_failure",
            )
        if record.shared_measurement_invalid:
            self.baseline_preflight_blocked = True
            return CandidateEvaluationLoopDecision(
                accepted=False,
                should_stop=True,
                stop_reason="shared_measurement_invalid",
            )
        if infrastructure_prevented_comparable_evaluation(
            record.failed_gates,
            baseline_summary=record.state.get("baseline_summary"),
            candidate_summary=record.state.get("candidate_summary"),
        ):
            self.infrastructure_blocked = True
            return CandidateEvaluationLoopDecision(
                accepted=False,
                should_stop=True,
                stop_reason="infrastructure_blocked",
            )
        if status == "accepted":
            return CandidateEvaluationLoopDecision(
                accepted=True,
                should_stop=True,
                stop_reason="candidate_accepted",
            )
        if self.measurement_frontier_stopped:
            return CandidateEvaluationLoopDecision(
                accepted=False,
                should_stop=True,
                stop_reason="measurement_frontier_stopped",
            )
        return CandidateEvaluationLoopDecision(
            accepted=False,
            should_stop=False,
        )
