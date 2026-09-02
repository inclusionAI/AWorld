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
from aworld.self_evolve.optimizers.base import CandidateGenerationOutcome
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


@dataclass(frozen=True)
class VerificationFunnelRequest:
    """Run policy and observations needed for the verification funnel."""

    screening_max_cases: int
    repair_iteration_horizon: int
    candidate_generation_batch_count: int
    max_generated_candidates: int
    repair_reserved_slot_count: int
    unique_generated_candidate_count: int
    policy_filtered_candidate_count: int
    max_authoritative_candidates: int
    max_score_tiebreak_candidates: int
    authoritative_case_observations: Mapping[
        str,
        Mapping[str, object],
    ] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "screening_max_cases",
            "repair_iteration_horizon",
            "candidate_generation_batch_count",
            "max_generated_candidates",
            "repair_reserved_slot_count",
            "unique_generated_candidate_count",
            "policy_filtered_candidate_count",
            "max_authoritative_candidates",
            "max_score_tiebreak_candidates",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ValueError(f"{field_name} must be a non-negative integer")
        observations = dict(self.authoritative_case_observations)
        if not all(
            isinstance(case_id, str)
            and bool(case_id)
            and isinstance(observation, Mapping)
            for case_id, observation in observations.items()
        ):
            raise TypeError(
                "authoritative case observations must map ids to mappings"
            )
        object.__setattr__(
            self,
            "authoritative_case_observations",
            observations,
        )


@dataclass(frozen=True)
class ConformanceProgressTransition:
    """One typed conformance observation and its bounded retry decision."""

    new_repair_requests: tuple[str, ...]
    semantic_progress: tuple[str, ...]
    changed_strategies: tuple[str, ...]
    stagnant_failures: tuple[str, ...]
    exhausted_failures: tuple[str, ...]
    prior_strategy_fingerprints: tuple[str, ...]

    @property
    def frontier_exhausted(self) -> bool:
        return bool(self.exhausted_failures)


@dataclass
class GenerationFrontierState:
    """Generation, repair, and conformance progress for one explicit run."""

    progress_repair_families: set[str] = field(default_factory=set)
    duplicate_population_stalls: int = 0
    consecutive_policy_filter_stalls: int = 0
    consecutive_materialization_stalls: int = 0
    last_policy_filter_signature: str | None = None
    last_materialization_stall_signature: str | None = None
    last_policy_filter_outcomes: tuple[CandidateGenerationOutcome, ...] = ()
    policy_frontier_exhausted: bool = False
    materialization_frontier_exhausted: bool = False
    protocol_frontier_exhausted: bool = False
    conformance_frontier_exhausted: bool = False
    conformance_strategy_switch_count: int = 0
    conformance_strategy_switch_request_count: int = 0
    conformance_strategy_attempts: dict[str, int] = field(
        default_factory=dict
    )
    conformance_strategy_topologies: dict[str, set[str]] = field(
        default_factory=dict
    )
    conformance_result_observations: dict[str, set[str]] = field(
        default_factory=dict
    )
    conformance_stagnant_attempts: dict[str, int] = field(
        default_factory=dict
    )
    conformance_targeted_repair_request_count: int = 0
    conformance_semantic_progress_count: int = 0
    conformance_semantic_frontier_stalled: bool = False
    pending_conformance_counterexamples: set[str] = field(
        default_factory=set
    )
    resolved_conformance_counterexamples: set[str] = field(
        default_factory=set
    )
    conformance_counterexamples_by_stage: dict[str, set[str]] = field(
        default_factory=dict
    )
    repeated_contract_replacement_candidate_ids: set[str] = field(
        default_factory=set
    )
    conformance_same_slot_repair_count: int = 0
    serialized_new_contract_repair_count: int = 0
    conformance_strategy_switch_not_materialized: bool = False
    infrastructure_retries: int = 0
    raw_generation_attempt_count: int = 0
    semantic_lesson_duplicate_attempt_count: int = 0
    generated_candidate_slot_count: int = 0
    candidate_generation_attempt_slot_count: int = 0
    effective_generated_candidate_ids: set[str] = field(default_factory=set)
    frontier_exhausted: bool = False
    repair_capacity_reserved: bool = False
    verification_frontier_exhausted: bool = False

    def begin_generation_slots(self, slot_count: int) -> int:
        """Reserve generation-attempt slots and return the first slot number."""

        if isinstance(slot_count, bool) or not isinstance(slot_count, int):
            raise TypeError("generation slot count must be an integer")
        if slot_count < 1:
            raise ValueError("generation slot count must be positive")
        first_slot = self.candidate_generation_attempt_slot_count + 1
        self.candidate_generation_attempt_slot_count += slot_count
        return first_slot

    def record_effective_candidate(
        self,
        candidate_id: str,
        *,
        consumes_slot: bool,
    ) -> None:
        """Admit one unique candidate into the effective slot frontier."""

        if consumes_slot:
            self.effective_generated_candidate_ids.add(candidate_id)
        self.generated_candidate_slot_count = len(
            self.effective_generated_candidate_ids
        )

    def exhaust_generation_limit(self, *, max_generated_candidates: int) -> None:
        """Freeze generation and record whether repair capacity was reserved."""

        self.frontier_exhausted = True
        self.repair_capacity_reserved = bool(
            self.generated_candidate_slot_count < max_generated_candidates
        )

    def record_policy_filter_stall(
        self,
        *,
        signature: str,
        outcomes: tuple[CandidateGenerationOutcome, ...],
        fully_filtered: bool,
        max_consecutive_stalls: int,
    ) -> bool:
        """Record one policy-filter signature and return frontier exhaustion."""

        self.last_policy_filter_outcomes = tuple(outcomes)
        if signature == self.last_policy_filter_signature:
            self.consecutive_policy_filter_stalls += 1
        else:
            self.last_policy_filter_signature = signature
            self.consecutive_policy_filter_stalls = 1
        if (
            fully_filtered
            and self.consecutive_policy_filter_stalls >= max_consecutive_stalls
        ):
            self.policy_frontier_exhausted = True
        return self.policy_frontier_exhausted

    def record_materialization_stall(
        self,
        *,
        signature: str | None,
        full_population_failed: bool,
        max_consecutive_stalls: int,
    ) -> bool:
        """Record or clear the repeated materialization frontier."""

        if not full_population_failed:
            self.consecutive_materialization_stalls = 0
            self.last_materialization_stall_signature = None
            return False
        if (
            signature is not None
            and signature == self.last_materialization_stall_signature
        ):
            self.consecutive_materialization_stalls += 1
        else:
            self.last_materialization_stall_signature = signature
            self.consecutive_materialization_stalls = 1
        if (
            signature is not None
            and self.consecutive_materialization_stalls
            >= max_consecutive_stalls
        ):
            self.materialization_frontier_exhausted = True
        return self.materialization_frontier_exhausted

    def record_duplicate_population(
        self,
        *,
        all_candidates_previously_attempted: bool,
        max_consecutive_stalls: int,
    ) -> bool:
        """Record a duplicate-only population and return whether to stop."""

        if all_candidates_previously_attempted:
            self.duplicate_population_stalls += 1
        return self.duplicate_population_stalls >= max_consecutive_stalls

    def reset_candidate_progress_stalls(self) -> None:
        """Reset retry/stall state after a non-empty candidate population."""

        self.duplicate_population_stalls = 0
        self.consecutive_policy_filter_stalls = 0
        self.consecutive_materialization_stalls = 0
        self.last_policy_filter_signature = None
        self.last_materialization_stall_signature = None
        self.infrastructure_retries = 0

    def claim_infrastructure_retry(
        self,
        *,
        retryable: bool,
        max_retries: int,
    ) -> bool:
        """Claim one bounded generation-infrastructure retry."""

        if not retryable or self.infrastructure_retries >= max_retries:
            return False
        self.infrastructure_retries += 1
        return True

    def record_conformance_counterexamples(
        self,
        *,
        observed: set[str],
        by_stage: Mapping[str, set[str]],
    ) -> set[str]:
        """Advance pending/resolved counterexamples and return repeats."""

        prior = set(self.pending_conformance_counterexamples)
        for stage, counterexample_ids in by_stage.items():
            self.conformance_counterexamples_by_stage.setdefault(
                stage,
                set(),
            ).update(counterexample_ids)
        repeated = prior & observed
        self.resolved_conformance_counterexamples.update(prior - observed)
        self.pending_conformance_counterexamples = set(observed)
        return repeated

    def observe_conformance_progress(
        self,
        *,
        signatures: tuple[str, ...],
        result_observations_by_signature: Mapping[str, tuple[str, ...]],
        strategy_by_signature: Mapping[str, tuple[str, ...]],
        max_stagnant_attempts: int,
    ) -> ConformanceProgressTransition:
        """Advance repair using result progress instead of owner/file churn."""

        if max_stagnant_attempts < 1:
            raise ValueError("max_stagnant_attempts must be positive")
        new_repair_requests: list[str] = []
        semantic_progress: list[str] = []
        changed_strategies: list[str] = []
        stagnant_failures: list[str] = []
        exhausted_failures: list[str] = []
        for signature in signatures:
            current_results = set(
                result_observations_by_signature.get(signature, ())
            )
            current_strategies = set(strategy_by_signature.get(signature, ()))
            prior_results = self.conformance_result_observations.get(signature)
            prior_strategies = self.conformance_strategy_topologies.get(signature)
            if prior_results is None:
                self.conformance_result_observations[signature] = set(
                    current_results
                )
                self.conformance_strategy_topologies[signature] = set(
                    current_strategies
                )
                self.conformance_stagnant_attempts[signature] = 0
                new_repair_requests.append(signature)
                continue
            new_strategies = current_strategies - (prior_strategies or set())
            if new_strategies:
                self.conformance_strategy_topologies.setdefault(
                    signature, set()
                ).update(new_strategies)
                self.conformance_strategy_attempts[signature] = (
                    self.conformance_strategy_attempts.get(signature, 0) + 1
                )
                changed_strategies.append(signature)
            new_results = current_results - prior_results
            if new_results:
                prior_results.update(new_results)
                self.conformance_stagnant_attempts[signature] = 0
                semantic_progress.append(signature)
                new_repair_requests.append(signature)
            else:
                stagnant_count = (
                    self.conformance_stagnant_attempts.get(signature, 0) + 1
                )
                self.conformance_stagnant_attempts[signature] = stagnant_count
                stagnant_failures.append(signature)
                if stagnant_count >= max_stagnant_attempts:
                    exhausted_failures.append(signature)
                else:
                    new_repair_requests.append(signature)
        if exhausted_failures:
            self.conformance_frontier_exhausted = True
            self.conformance_semantic_frontier_stalled = True
            self.conformance_strategy_switch_not_materialized = any(
                not self.conformance_strategy_attempts.get(signature, 0)
                for signature in exhausted_failures
            )
        self.conformance_strategy_switch_count += len(changed_strategies)
        self.conformance_strategy_switch_request_count += len(
            new_repair_requests
        )
        self.conformance_targeted_repair_request_count += len(
            new_repair_requests
        )
        self.conformance_semantic_progress_count += len(semantic_progress)
        prior_strategy_fingerprints = tuple(
            sorted(
                {
                    strategy
                    for signature in new_repair_requests
                    for strategy in self.conformance_strategy_topologies.get(
                        signature,
                        set(),
                    )
                }
            )
        )
        return ConformanceProgressTransition(
            new_repair_requests=tuple(new_repair_requests),
            semantic_progress=tuple(semantic_progress),
            changed_strategies=tuple(changed_strategies),
            stagnant_failures=tuple(stagnant_failures),
            exhausted_failures=tuple(exhausted_failures),
            prior_strategy_fingerprints=prior_strategy_fingerprints,
        )

    def release_effective_candidates(
        self,
        candidate_ids: set[str],
        *,
        same_slot_repair: bool = False,
        repeated_contract_replacement: bool = False,
    ) -> None:
        """Release effective generation slots for a bounded repair attempt."""

        self.effective_generated_candidate_ids.difference_update(candidate_ids)
        self.generated_candidate_slot_count = len(
            self.effective_generated_candidate_ids
        )
        if same_slot_repair:
            self.conformance_same_slot_repair_count += len(candidate_ids)
        if repeated_contract_replacement:
            self.repeated_contract_replacement_candidate_ids.update(
                candidate_ids
            )

    def stop_reason(self) -> str | None:
        """Project the terminal generation-frontier reason."""

        if self.conformance_semantic_frontier_stalled:
            return "conformance_semantic_frontier_stalled"
        if self.conformance_strategy_switch_not_materialized:
            return "conformance_strategy_switch_not_materialized"
        if self.conformance_frontier_exhausted:
            return "conformance_frontier_repeated_after_strategy_switch"
        if self.materialization_frontier_exhausted:
            return "materialization_frontier_repeated"
        if self.policy_frontier_exhausted:
            return "generation_policy_frontier_repeated"
        if self.repair_capacity_reserved:
            return "repair_capacity_reserved_without_typed_frontier"
        if self.frontier_exhausted:
            return "generated_candidate_slot_limit_reached"
        if self.verification_frontier_exhausted:
            return "authoritative_candidate_limit_reached"
        return None


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
    generation: GenerationFrontierState = field(
        default_factory=GenerationFrontierState
    )

    def verification_funnel_report(
        self,
        request: VerificationFunnelRequest,
    ) -> dict[str, object]:
        """Project authoritative generation/evaluation state for reporting."""

        generation_stop_reason = self.generation.stop_reason()
        report: dict[str, object] = {
            "screening_max_cases": request.screening_max_cases,
            "repair_iteration_horizon": request.repair_iteration_horizon,
            "candidate_generation_batch_count": (
                request.candidate_generation_batch_count
            ),
            "max_generated_candidates": request.max_generated_candidates,
            "repair_reserved_slot_count": request.repair_reserved_slot_count,
            "generated_candidate_slot_count": (
                self.generation.generated_candidate_slot_count
            ),
            "candidate_generation_attempt_slot_count": (
                self.generation.candidate_generation_attempt_slot_count
            ),
            "unique_generated_candidate_count": (
                request.unique_generated_candidate_count
            ),
            "generation_frontier_exhausted": self.generation.frontier_exhausted,
            "generation_repair_capacity_reserved": (
                self.generation.repair_capacity_reserved
            ),
            "generation_policy_frontier_exhausted": (
                self.generation.policy_frontier_exhausted
            ),
            "conformance_strategy_switch_count": (
                self.generation.conformance_strategy_switch_count
            ),
            "conformance_strategy_switch_request_count": (
                self.generation.conformance_strategy_switch_request_count
            ),
            "conformance_strategy_switch_not_materialized": (
                self.generation.conformance_strategy_switch_not_materialized
            ),
            "conformance_targeted_repair_request_count": (
                self.generation.conformance_targeted_repair_request_count
            ),
            "conformance_semantic_progress_count": (
                self.generation.conformance_semantic_progress_count
            ),
            "conformance_stagnant_attempt_count": sum(
                self.generation.conformance_stagnant_attempts.values()
            ),
            "conformance_semantic_frontier_stalled": (
                self.generation.conformance_semantic_frontier_stalled
            ),
            "pending_conformance_counterexample_count": len(
                self.generation.pending_conformance_counterexamples
            ),
            "resolved_conformance_counterexample_count": len(
                self.generation.resolved_conformance_counterexamples
            ),
            "conformance_counterexamples_by_stage": {
                stage: {
                    "count": len(counterexample_ids),
                    "counterexample_ids": sorted(counterexample_ids),
                }
                for stage, counterexample_ids in sorted(
                    self.generation.conformance_counterexamples_by_stage.items()
                )
            },
            "repeated_contract_replacement_candidate_count": len(
                self.generation.repeated_contract_replacement_candidate_ids
            ),
            "repeated_contract_replacement_candidate_ids": sorted(
                self.generation.repeated_contract_replacement_candidate_ids
            ),
            "conformance_same_slot_repair_count": (
                self.generation.conformance_same_slot_repair_count
            ),
            "serialized_new_contract_repair_count": (
                self.generation.serialized_new_contract_repair_count
            ),
            "policy_filtered_candidate_count": (
                request.policy_filtered_candidate_count
            ),
            "max_authoritative_candidates": (
                request.max_authoritative_candidates
            ),
            "authoritative_candidate_count": self.authoritative_candidate_count,
            "authoritative_candidate_attempt_count": (
                self.authoritative_candidate_attempt_count
            ),
            "authoritative_candidate_ids": sorted(
                self.authoritative_candidate_ids
            ),
            "authoritative_candidate_attempt_ids": sorted(
                self.authoritative_candidate_attempt_ids
            ),
            "max_score_tiebreak_candidates": (
                request.max_score_tiebreak_candidates
            ),
            "score_tiebreak_candidate_count": (
                self.score_tiebreak_candidate_count
            ),
            "frontier_exhausted": (
                self.generation.verification_frontier_exhausted
            ),
            "authoritative_evaluation_uses_full_dataset": True,
            "prerequisite_candidate_ids": list(
                dict.fromkeys(self.prerequisite_candidate_ids)
            ),
        }
        if self.generation.materialization_frontier_exhausted:
            report["generation_materialization_frontier_exhausted"] = True
        if self.generation.protocol_frontier_exhausted:
            report["generation_protocol_frontier_exhausted"] = True
        if self.generation.conformance_frontier_exhausted:
            report["generation_conformance_frontier_exhausted"] = True
        if generation_stop_reason is not None:
            report["generation_stop_reason"] = generation_stop_reason
        if request.authoritative_case_observations:
            report["authoritative_case_observations"] = {
                case_id: dict(observation)
                for case_id, observation in sorted(
                    request.authoritative_case_observations.items()
                )
            }
            report["authoritative_case_observations_advisory_only"] = True
        return report

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
