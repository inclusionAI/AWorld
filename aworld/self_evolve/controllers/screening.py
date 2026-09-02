"""Shared control-health and deadline policy for candidate screening."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from aworld.self_evolve.budget import (
    BudgetDecision,
    BudgetStage,
    CandidateAttemptKey,
    CandidateAttemptStage,
)
from aworld.self_evolve.concurrency import (
    SelfEvolveExecutionTelemetry,
)
from aworld.self_evolve.controllers.screening_helpers import (
    SCREENING_BUDGET_CENSORED_CODE,
)
from aworld.self_evolve.history_support import (
    _non_negative_numeric_int as _non_negative_int,
)
from aworld.self_evolve.datasets import SelfEvolveDataset
from aworld.self_evolve.failure_events import (
    FailureOwner,
    FailureScope,
    FailureStage,
    ReplayExecutionStatus,
    ReplayFailureEvent,
)
from aworld.self_evolve.measurement import ControlledExperimentSpec
from aworld.self_evolve.repair_conformance import RepairConformanceContract
from aworld.self_evolve.replay import (
    CandidateReplayBackend,
    CandidateReplayResult,
)
from aworld.self_evolve.replay_adaptation import (
    ReplayAdaptationBundle,
    ReplayCapabilityRequirement,
)
from aworld.self_evolve.store import FilesystemSelfEvolveStore
from aworld.self_evolve.targets import SelfEvolveTarget
from aworld.self_evolve.types import CandidateVariant, GateResult


class CandidateAttemptTrackerProtocol(Protocol):
    """Attempt-lifecycle surface consumed by population screening."""

    def emit(
        self,
        key: CandidateAttemptKey,
        stage: CandidateAttemptStage,
        *,
        reason_code: str | None = None,
        case_count: int | None = None,
        usage: object | None = None,
    ) -> None: ...

    def last_stage(self, key: CandidateAttemptKey) -> CandidateAttemptStage: ...

    def terminal(self, key: CandidateAttemptKey) -> bool: ...


class ScreeningBudgetContextProtocol(Protocol):
    """Budget ledger surface consumed by population screening."""

    def reserve(
        self,
        stage: BudgetStage,
        item_id: str,
        *,
        units: int = 1,
        **kwargs: object,
    ) -> BudgetDecision: ...

    def debit(
        self,
        decision: BudgetDecision,
        **kwargs: object,
    ) -> object: ...


class StoredCandidateScreeningBypass(str, Enum):
    """Why an immutable candidate skips comparative ranking replay."""

    MEASUREMENT_RESUME = "stored_candidate_measurement_resume"
    FRESH_EVALUATION_RERUN = "stored_candidate_fresh_evaluation"


@dataclass(frozen=True)
class ScreeningPopulationRequest:
    """Frozen run inputs for comparative candidate screening."""

    run_id: str
    target: SelfEvolveTarget
    dataset: SelfEvolveDataset
    candidates: tuple[CandidateVariant, ...]
    apply_policy: str
    capability_requirements: tuple[ReplayCapabilityRequirement, ...] = ()
    repair_conformance_contracts: Mapping[
        str, RepairConformanceContract
    ] = field(default_factory=dict)
    attempt_tracker: CandidateAttemptTrackerProtocol | None = None
    attempt_keys: Mapping[str, CandidateAttemptKey] | None = None
    budget_context: ScreeningBudgetContextProtocol | None = None
    require_single_candidate_screening: bool = False
    stored_candidate_bypass: StoredCandidateScreeningBypass | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("screening population run_id must be non-empty")
        if self.apply_policy not in {
            "proposal",
            "auto_verified",
            "verified_only",
        }:
            raise ValueError(
                f"unsupported screening apply policy: {self.apply_policy}"
            )
        object.__setattr__(self, "run_id", self.run_id.strip())
        object.__setattr__(self, "candidates", tuple(self.candidates))
        if self.stored_candidate_bypass is not None:
            object.__setattr__(
                self,
                "stored_candidate_bypass",
                StoredCandidateScreeningBypass(self.stored_candidate_bypass),
            )
        object.__setattr__(
            self,
            "capability_requirements",
            tuple(self.capability_requirements),
        )


@dataclass(frozen=True)
class ScreeningPopulationResult:
    """Validated candidate frontier and its persisted screening report."""

    candidates: tuple[CandidateVariant, ...]
    report: dict[str, object] | None


@dataclass
class ScreeningPopulationRuntime:
    """Explicit services, configuration, and mutable campaign state."""

    store: FilesystemSelfEvolveStore
    execution_telemetry: SelfEvolveExecutionTelemetry
    replay_enabled: bool
    replay_backend: CandidateReplayBackend | None
    candidate_screening_max_cases: int
    replay_max_steps: int | None
    replay_timeout_seconds: int
    baseline_replay_repetitions: int
    candidate_replay_repetitions: int
    progress_callback: Callable[[str, str], object] | None
    case_observations: dict[str, dict[str, float | int]]
    control_observations: dict[str, dict[str, object]]
    invalid_control_case_ids_by_run: dict[str, set[str]]
    measurement_experiments: dict[object, ControlledExperimentSpec]
    validate_conformance_population: Callable[
        ..., Awaitable[tuple[tuple[CandidateVariant, ...], dict[str, object] | None]]
    ]
    plan_measurement: Callable[..., ControlledExperimentSpec | None]
    prepare_adaptation: Callable[
        ..., tuple[ReplayAdaptationBundle | None, GateResult]
    ]
    replay_candidate: Callable[
        ...,
        Awaitable[
            tuple[
                CandidateReplayResult | None,
                SelfEvolveDataset | None,
                GateResult | None,
            ]
        ],
    ]
    baseline_reuse_provenance: Callable[..., dict[str, str | None]]
    policy: CandidateScreeningController
    control_qualification_identity: Callable[
        ..., dict[str, object]
    ] | None = None


ScreeningPopulationExecutor = Callable[
    [ScreeningPopulationRequest, ScreeningPopulationRuntime],
    Awaitable[
        tuple[tuple[CandidateVariant, ...], dict[str, object] | None]
    ],
]


@dataclass(frozen=True)
class CandidateScreeningController:
    """Owns screening policies that must be consistent across candidates."""

    support_control_failure_patience: int = 3

    def __post_init__(self) -> None:
        if (
            isinstance(self.support_control_failure_patience, bool)
            or self.support_control_failure_patience <= 0
        ):
            raise ValueError("support_control_failure_patience must be positive")

    async def screen_population(
        self,
        request: ScreeningPopulationRequest,
        *,
        execute: ScreeningPopulationExecutor,
        runtime: ScreeningPopulationRuntime,
    ) -> ScreeningPopulationResult:
        """Execute screening behind a validated typed phase boundary."""

        selected, report = await execute(request, runtime)
        selected = tuple(selected)
        requested_candidates = set(request.candidates)
        if any(candidate not in requested_candidates for candidate in selected):
            raise ValueError(
                "screening selected a candidate outside the requested population"
            )
        if len({candidate.candidate_id for candidate in selected}) != len(selected):
            raise ValueError("screening selected duplicate candidate ids")
        return ScreeningPopulationResult(
            candidates=selected,
            report=dict(report) if report is not None else None,
        )

    def hard_limit_seconds(self, decision: BudgetDecision) -> float | None:
        """Return the reserved wall envelope as an executable stage deadline."""

        usage = decision.estimate.resolved_usage()
        if usage is None or usage.wall_seconds <= 0:
            return None
        return float(usage.wall_seconds)

    def support_specific_control_circuit_breaker_gate(
        self,
        *,
        control_identity: Mapping[str, object],
        control_observations: Mapping[str, Mapping[str, object]],
    ) -> GateResult | None:
        """Skip an exact support/envelope control after repeated timeouts."""

        fingerprint = control_identity.get("control_identity_fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            return None
        observation = control_observations.get(fingerprint)
        if not isinstance(observation, Mapping):
            return None
        attempts = _non_negative_int(observation.get("baseline_attempt_count"))
        successes = _non_negative_int(observation.get("baseline_success_count"))
        timeouts = _non_negative_int(observation.get("baseline_timeout_count"))
        failure_patience = self.support_control_failure_patience
        if (
            attempts < failure_patience
            or successes > 0
            or timeouts < failure_patience
        ):
            return None
        event = ReplayFailureEvent(
            code="screening_support_control_circuit_open",
            owner=FailureOwner.FRAMEWORK,
            stage=FailureStage.EVALUATION,
            scope=FailureScope.MEMBER,
            repairable=True,
            category="measurement_control",
            summary=(
                "exact support-specific screening control was skipped after "
                "repeated baseline timeouts"
            ),
            diagnostics={
                "control_identity_fingerprint": fingerprint,
                "baseline_attempt_count": attempts,
                "baseline_timeout_count": timeouts,
                "failure_patience": failure_patience,
            },
        )
        payload = event.to_dict()
        timeout_event = ReplayFailureEvent(
            code="replay_member_phase_timeout",
            owner=FailureOwner.FRAMEWORK,
            stage=FailureStage.EVALUATION,
            scope=FailureScope.MEMBER,
            repairable=True,
            category="measurement_control",
            summary="historical baseline attempts exhausted this timeout envelope",
            diagnostics={
                "phase": "baseline",
                "termination_budget_axis": "member_phase_wall_seconds",
                "control_identity_fingerprint": fingerprint,
            },
        )
        return GateResult(
            gate_name="candidate_replay",
            passed=False,
            reason=(
                "support-specific control circuit is open for this exact "
                "timeout envelope"
            ),
            details={
                "code": "screening_support_control_circuit_open",
                "failure_class": "framework",
                "failure_owner": FailureOwner.FRAMEWORK.value,
                "failure_scope": FailureScope.MEMBER.value,
                "failure_stage": FailureStage.EVALUATION.value,
                "repairable": True,
                "screening_outcome": "invalid_control",
                "candidate_execution_observed": False,
                "candidate_intervention_required": True,
                "candidate_intervention_observed": None,
                "baseline_status": ReplayExecutionStatus.FAILED.value,
                "candidate_status": ReplayExecutionStatus.BLOCKED.value,
                "baseline_failure": timeout_event.to_dict(),
                "control_identity": dict(control_identity),
                "control_health_observation": dict(observation),
                "failure_event": payload,
                "causal_failure_events": [payload],
                "resume_safe": False,
            },
        )

    def stage_budget_censor_gate(
        self,
        *,
        hard_limit_seconds: float,
        elapsed_seconds: float,
        candidate_execution_observed: bool,
    ) -> GateResult:
        """Describe an exhausted screening envelope without candidate blame."""

        event = ReplayFailureEvent(
            code=SCREENING_BUDGET_CENSORED_CODE,
            owner=FailureOwner.FRAMEWORK,
            stage=FailureStage.EVALUATION,
            scope=FailureScope.MEMBER,
            repairable=False,
            category="candidate_screening",
            summary=(
                "candidate screening exhausted its reserved wall-time envelope "
                "before a directional comparison completed"
            ),
            diagnostics={
                "censor_basis": "stage_deadline",
                "hard_limit_seconds": hard_limit_seconds,
                "elapsed_seconds": elapsed_seconds,
                "termination_budget_axis": "screening_stage_wall_seconds",
                "candidate_execution_observed": candidate_execution_observed,
            },
        )
        payload = event.to_dict()
        return GateResult(
            gate_name="candidate_screening",
            passed=False,
            reason=(
                "representative screening reached its reserved stage deadline; "
                "authoritative replay must decide the candidate"
            ),
            details={
                "code": SCREENING_BUDGET_CENSORED_CODE,
                "failure_class": "framework",
                "failure_owner": FailureOwner.FRAMEWORK.value,
                "failure_scope": FailureScope.MEMBER.value,
                "failure_stage": FailureStage.EVALUATION.value,
                "repairable": False,
                "screening_outcome": "right_censored",
                "screening_budget_censored": True,
                "screening_censor_basis": "stage_deadline",
                "termination_budget_axis": "screening_stage_wall_seconds",
                "hard_limit_seconds": hard_limit_seconds,
                "elapsed_seconds": elapsed_seconds,
                "candidate_execution_observed": candidate_execution_observed,
                "resume_safe": False,
                "failure_event": payload,
                "causal_failure_events": [payload],
            },
        )

    def attempt_is_budget_censored(self, attempt: Mapping[str, object]) -> bool:
        details = attempt.get("details")
        return bool(
            isinstance(details, Mapping)
            and details.get("code") == SCREENING_BUDGET_CENSORED_CODE
            and details.get("screening_outcome") == "right_censored"
        )

    def gate_is_budget_censored(self, gate: GateResult | None) -> bool:
        return bool(
            gate is not None
            and isinstance(gate.details, Mapping)
            and gate.details.get("code") == SCREENING_BUDGET_CENSORED_CODE
            and gate.details.get("screening_outcome") == "right_censored"
        )


def budget_decision_wall_limit_seconds(
    decision: BudgetDecision,
) -> float | None:
    """Compatibility helper for callers migrating to the controller."""

    return CandidateScreeningController().hard_limit_seconds(decision)


def support_specific_control_circuit_breaker_gate(
    *,
    control_identity: Mapping[str, object],
    control_observations: Mapping[str, Mapping[str, object]],
    failure_patience: int = 3,
) -> GateResult | None:
    """Compatibility helper for callers migrating to the controller."""

    return CandidateScreeningController(
        support_control_failure_patience=failure_patience,
    ).support_specific_control_circuit_breaker_gate(
        control_identity=control_identity,
        control_observations=control_observations,
    )


def screening_stage_budget_censor_gate(
    *,
    hard_limit_seconds: float,
    elapsed_seconds: float,
    candidate_execution_observed: bool,
) -> GateResult:
    """Compatibility helper for callers migrating to the controller."""

    return CandidateScreeningController().stage_budget_censor_gate(
        hard_limit_seconds=hard_limit_seconds,
        elapsed_seconds=elapsed_seconds,
        candidate_execution_observed=candidate_execution_observed,
    )


def screening_attempt_is_budget_censored(
    attempt: Mapping[str, object],
) -> bool:
    return CandidateScreeningController().attempt_is_budget_censored(attempt)


def screening_gate_is_budget_censored(gate: GateResult | None) -> bool:
    return CandidateScreeningController().gate_is_budget_censored(gate)
