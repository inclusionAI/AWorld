from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from aworld.self_evolve.counterexamples import normalize_counterexample
from aworld.self_evolve.causal_admission import (
    causal_admission_prerequisite_blocker,
)
from aworld.self_evolve.recovery_trace import (
    RECOVERY_TRACE_SCHEMA_VERSION,
    validate_public_recovery_trace,
)
from aworld.self_evolve.measurement_checkpoint import (
    MeasurementResumeCheckpointV1,
    PairedReplayResumeCheckpointV1,
    discover_paired_replay_resume_checkpoint,
    load_measurement_resume_checkpoint,
    load_paired_replay_resume_checkpoint,
)
from aworld.self_evolve.schema_diagnostics import (
    websocket_handshake_http_version_constraint,
)
from aworld.self_evolve.replay_capability import (
    python_source_syntax_counterexample,
    recorded_response_index_source_behavior_proof,
)


CAMPAIGN_SCHEMA_VERSION = "aworld.self_evolve.campaign.v1"
CAMPAIGN_MEASUREMENT_OUTCOME_SCHEMA_VERSION = (
    "aworld.self_evolve.campaign_measurement_outcome.v2"
)
CAMPAIGN_MEASUREMENT_LEDGER_SCHEMA_VERSION = (
    "aworld.self_evolve.campaign_measurement_ledger.v2"
)
DISPOSITION_SCHEMA_VERSION = "aworld.self_evolve.disposition.v1"
PROGRESS_SCHEMA_VERSION = "aworld.self_evolve.progress.v1"
DEFAULT_MAX_IMPROVEMENT_CYCLES = 3
DEFAULT_MAX_MEASUREMENT_RETRIES = 2
DEFAULT_MAX_INFRASTRUCTURE_RETRIES = 2
LEGACY_SINGLE_TURN_REPLAY_REPLACEMENT_STEPS = 24

_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,159}$")
_RUNTIME_ONLY_REQUEST_KEYS = {
    "candidate_replay_backend",
    "concurrency_policy",
    "evaluation_backend",
    "ingestion_model_config",
    "ingestion_registry",
    "mutation_model_config",
    "post_apply_evaluator",
    "progress_callback",
    "replay_adaptation_compiler",
    "runtime_registry_refresher",
    "runtime_skill_activator",
}
_SOURCE_REQUEST_KEYS = {
    "batch_config",
    "current_trajectory",
    "dataset",
    "from_session",
    "from_trajectory",
    "from_trajectory_set",
    "from_source",
    "source_ingestor",
    "source_manifest",
    "frozen_ingestion_id",
}
_RESUME_CONFLICT_KEYS = {
    *_SOURCE_REQUEST_KEYS,
    "from_run",
    "target",
}
_STAGE_RANK = {
    "target_selection": 1,
    "candidate_generation": 2,
    "candidate_repair_conformance": 3,
    "candidate_screening": 4,
    "candidate_replay": 5,
    "replay_confidence": 6,
    "evaluation": 7,
    "held_out_verification": 8,
    "trusted_improvement_measurement": 9,
    "apply": 9,
    "post_apply": 10,
    # Typed causal events use lifecycle stage names rather than gate names.
    "capability_compile": 3,
    "capability_preflight": 4,
    "task_rollout": 5,
    "evaluator": 7,
    "post_apply_verification": 10,
}
_MEASUREMENT_READINESS_RANK = {
    "unplanned": 0,
    "experiment_planned": 1,
    "identity_contract_complete": 2,
    "capability_compile": 3,
    "capability_preflight": 4,
    "control_executable": 5,
    "task_rollout": 6,
    "paired_observations": 7,
    "first_comparable_pair": 8,
    "minimum_independent_evidence": 9,
    "transfer_panel_executable": 10,
}


class SelfImprovementCampaignStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    BUDGET_LIMITED = "budget_limited"
    EXHAUSTED = "exhausted"
    COMPLETE = "complete"


class MeasurementExecutionStatus(str, Enum):
    NOT_STARTED = "not_started"
    PLANNED = "planned"
    RUNNING = "running"
    CHECKPOINTED = "checkpointed"
    COMPLETED = "completed"
    INVALID = "invalid"
    FRAMEWORK_BLOCKED = "framework_blocked"


class CandidateImprovementOutcome(str, Enum):
    UNKNOWN = "unknown"
    POSITIVE = "positive"
    NO_EFFECT = "no_effect"
    REGRESSION = "regression"
    CANDIDATE_INVALID = "candidate_invalid"


class CampaignMeasurementProjection(str, Enum):
    SUCCEEDED = "succeeded"
    CANDIDATE_REJECTED = "candidate_rejected"
    MEASUREMENT_INCOMPLETE = "measurement_incomplete"
    MEASUREMENT_INVALID = "measurement_invalid"
    FRAMEWORK_BLOCKED = "framework_blocked"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True)
class CampaignMeasurementOutcomeV2:
    """Orthogonal measurement execution and candidate improvement outcome."""

    execution_status: MeasurementExecutionStatus
    improvement_outcome: CandidateImprovementOutcome
    release_gates_passed: bool
    continuation_available: bool
    reason_code: str
    schema_version: str = CAMPAIGN_MEASUREMENT_OUTCOME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CAMPAIGN_MEASUREMENT_OUTCOME_SCHEMA_VERSION:
            raise ValueError("unsupported campaign measurement outcome schema")
        object.__setattr__(
            self, "execution_status", MeasurementExecutionStatus(self.execution_status)
        )
        object.__setattr__(
            self,
            "improvement_outcome",
            CandidateImprovementOutcome(self.improvement_outcome),
        )
        if not isinstance(self.release_gates_passed, bool):
            raise ValueError("release_gates_passed must be boolean")
        if not isinstance(self.continuation_available, bool):
            raise ValueError("continuation_available must be boolean")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", self.reason_code):
            raise ValueError("measurement outcome reason_code must be lower_snake_case")
        if (
            self.execution_status is not MeasurementExecutionStatus.COMPLETED
            and self.improvement_outcome
            in {
                CandidateImprovementOutcome.POSITIVE,
                CandidateImprovementOutcome.NO_EFFECT,
                CandidateImprovementOutcome.REGRESSION,
            }
        ):
            raise ValueError("effect outcome requires completed measurement")
        if (
            self.execution_status is MeasurementExecutionStatus.COMPLETED
            and self.improvement_outcome is CandidateImprovementOutcome.UNKNOWN
        ):
            raise ValueError("completed measurement requires an improvement outcome")
        if self.release_gates_passed and (
            self.execution_status is not MeasurementExecutionStatus.COMPLETED
            or self.improvement_outcome is not CandidateImprovementOutcome.POSITIVE
        ):
            raise ValueError(
                "release gates can pass only for completed positive effect"
            )

    @property
    def projection(self) -> CampaignMeasurementProjection:
        if self.execution_status is MeasurementExecutionStatus.FRAMEWORK_BLOCKED:
            return CampaignMeasurementProjection.FRAMEWORK_BLOCKED
        if self.execution_status is MeasurementExecutionStatus.INVALID:
            return CampaignMeasurementProjection.MEASUREMENT_INVALID
        if self.execution_status in {
            MeasurementExecutionStatus.NOT_STARTED,
            MeasurementExecutionStatus.PLANNED,
            MeasurementExecutionStatus.RUNNING,
            MeasurementExecutionStatus.CHECKPOINTED,
        }:
            return (
                CampaignMeasurementProjection.MEASUREMENT_INCOMPLETE
                if self.continuation_available
                else CampaignMeasurementProjection.EXHAUSTED
            )
        if (
            self.improvement_outcome is CandidateImprovementOutcome.POSITIVE
            and self.release_gates_passed
        ):
            return CampaignMeasurementProjection.SUCCEEDED
        return CampaignMeasurementProjection.CANDIDATE_REJECTED

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "execution_status": self.execution_status.value,
            "improvement_outcome": self.improvement_outcome.value,
            "release_gates_passed": self.release_gates_passed,
            "continuation_available": self.continuation_available,
            "reason_code": self.reason_code,
            "projection": self.projection.value,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, object]
    ) -> "CampaignMeasurementOutcomeV2":
        if value.get("schema_version") != CAMPAIGN_MEASUREMENT_OUTCOME_SCHEMA_VERSION:
            raise ValueError("unsupported campaign measurement outcome schema")
        release_gates_passed = value.get("release_gates_passed")
        continuation_available = value.get("continuation_available")
        if not isinstance(release_gates_passed, bool) or not isinstance(
            continuation_available, bool
        ):
            raise ValueError("campaign measurement booleans must be strict")
        loaded = cls(
            execution_status=MeasurementExecutionStatus(
                str(value.get("execution_status"))
            ),
            improvement_outcome=CandidateImprovementOutcome(
                str(value.get("improvement_outcome"))
            ),
            release_gates_passed=release_gates_passed,
            continuation_available=continuation_available,
            reason_code=str(value.get("reason_code")),
        )
        if value.get("projection") != loaded.projection.value:
            raise ValueError("campaign measurement projection is not canonical")
        return loaded


@dataclass(frozen=True)
class CampaignMeasurementLedgerV2:
    """Single source of Campaign control-plane continuation accounting."""

    continuation_run_ids: tuple[str, ...] = ()
    invalid_retry_run_ids: tuple[str, ...] = ()
    framework_blocked_run_ids: tuple[str, ...] = ()
    infrastructure_retry_run_ids: tuple[str, ...] = ()
    schema_version: str = CAMPAIGN_MEASUREMENT_LEDGER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CAMPAIGN_MEASUREMENT_LEDGER_SCHEMA_VERSION:
            raise ValueError("unsupported campaign measurement ledger schema")
        for values, label in (
            (self.continuation_run_ids, "measurement continuation run"),
            (self.invalid_retry_run_ids, "measurement invalid retry run"),
            (self.framework_blocked_run_ids, "framework blocked run"),
            (self.infrastructure_retry_run_ids, "infrastructure retry run"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} ids must be unique")
            for run_id in values:
                _validate_id(run_id, label)
        ledger_sets = (
            set(self.continuation_run_ids),
            set(self.invalid_retry_run_ids),
            set(self.framework_blocked_run_ids),
            set(self.infrastructure_retry_run_ids),
        )
        if any(
            left & right
            for index, left in enumerate(ledger_sets)
            for right in ledger_sets[index + 1 :]
        ):
            raise ValueError("a measurement run cannot be charged to two ledgers")

    @property
    def continuation_count(self) -> int:
        return len(self.continuation_run_ids)

    @property
    def invalid_retry_count(self) -> int:
        return len(self.invalid_retry_run_ids)

    @property
    def framework_blocked_count(self) -> int:
        return len(self.framework_blocked_run_ids)

    @property
    def infrastructure_retry_count(self) -> int:
        return len(self.infrastructure_retry_run_ids)

    @property
    def control_plane_run_count(self) -> int:
        return (
            self.continuation_count
            + self.invalid_retry_count
            + self.framework_blocked_count
            + self.infrastructure_retry_count
        )

    def charge_continuation(self, run_id: str) -> "CampaignMeasurementLedgerV2":
        if run_id in self.continuation_run_ids:
            return self
        return replace(
            self, continuation_run_ids=(*self.continuation_run_ids, run_id)
        )

    def charge_invalid_retry(self, run_id: str) -> "CampaignMeasurementLedgerV2":
        if run_id in self.invalid_retry_run_ids:
            return self
        return replace(
            self, invalid_retry_run_ids=(*self.invalid_retry_run_ids, run_id)
        )

    def charge_framework_blocked(self, run_id: str) -> "CampaignMeasurementLedgerV2":
        """Record a framework-owned shared run without spending a candidate cycle."""

        if run_id in self.framework_blocked_run_ids:
            return self
        return replace(
            self,
            framework_blocked_run_ids=(*self.framework_blocked_run_ids, run_id),
        )

    def charge_infrastructure_retry(
        self, run_id: str
    ) -> "CampaignMeasurementLedgerV2":
        """Record system execution that produced no candidate conclusion."""

        if run_id in self.infrastructure_retry_run_ids:
            return self
        return replace(
            self,
            infrastructure_retry_run_ids=(
                *self.infrastructure_retry_run_ids,
                run_id,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "continuation_run_ids": list(self.continuation_run_ids),
            "invalid_retry_run_ids": list(self.invalid_retry_run_ids),
            "framework_blocked_run_ids": list(self.framework_blocked_run_ids),
            "infrastructure_retry_run_ids": list(
                self.infrastructure_retry_run_ids
            ),
            "continuation_count": self.continuation_count,
            "invalid_retry_count": self.invalid_retry_count,
            "framework_blocked_count": self.framework_blocked_count,
            "infrastructure_retry_count": self.infrastructure_retry_count,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, object]
    ) -> "CampaignMeasurementLedgerV2":
        if value.get("schema_version") != CAMPAIGN_MEASUREMENT_LEDGER_SCHEMA_VERSION:
            raise ValueError("unsupported campaign measurement ledger schema")
        loaded = cls(
            continuation_run_ids=_string_tuple(value.get("continuation_run_ids")),
            invalid_retry_run_ids=_string_tuple(value.get("invalid_retry_run_ids")),
            framework_blocked_run_ids=_string_tuple(
                value.get("framework_blocked_run_ids")
            ),
            infrastructure_retry_run_ids=_string_tuple(
                value.get("infrastructure_retry_run_ids")
            ),
        )
        continuation_count = value.get("continuation_count")
        invalid_retry_count = value.get("invalid_retry_count")
        framework_blocked_count = value.get(
            "framework_blocked_count",
            loaded.framework_blocked_count,
        )
        infrastructure_retry_count = value.get(
            "infrastructure_retry_count",
            loaded.infrastructure_retry_count,
        )
        if (
            isinstance(continuation_count, bool)
            or not isinstance(continuation_count, int)
            or continuation_count != loaded.continuation_count
        ):
            raise ValueError("measurement continuation count is not canonical")
        if (
            isinstance(invalid_retry_count, bool)
            or not isinstance(invalid_retry_count, int)
            or invalid_retry_count != loaded.invalid_retry_count
        ):
            raise ValueError("measurement retry count is not canonical")
        if (
            isinstance(framework_blocked_count, bool)
            or not isinstance(framework_blocked_count, int)
            or framework_blocked_count != loaded.framework_blocked_count
        ):
            raise ValueError("framework blocked count is not canonical")
        if (
            isinstance(infrastructure_retry_count, bool)
            or not isinstance(infrastructure_retry_count, int)
            or infrastructure_retry_count
            != loaded.infrastructure_retry_count
        ):
            raise ValueError("infrastructure retry count is not canonical")
        return loaded


class SelfImprovementDispositionKind(str, Enum):
    COMPLETE = "complete"
    CONTINUE_CANDIDATE = "continue_candidate"
    CONTINUE_CAMPAIGN = "continue_campaign"
    RETRY_INFRASTRUCTURE = "retry_infrastructure"
    HANDOFF_GOAL = "handoff_goal"
    PAUSE_OPERATOR = "pause_operator"
    COLLECT_MORE_EVIDENCE = "collect_more_evidence"
    REPAIR_MEASUREMENT = "repair_measurement"
    SWITCH_GENERATOR = "switch_generator"
    SWITCH_SCHEDULER = "switch_scheduler"
    STOP_NO_EFFECT = "stop_no_effect"
    STOP_NEGATIVE_EFFECT = "stop_negative_effect"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True)
class CampaignUsage:
    tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    wall_seconds: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if isinstance(self.tokens, bool) or self.tokens < 0:
            raise ValueError("campaign token usage must be non-negative")
        object.__setattr__(self, "cost_usd", Decimal(str(self.cost_usd)))
        object.__setattr__(self, "wall_seconds", Decimal(str(self.wall_seconds)))
        if self.cost_usd < 0 or self.wall_seconds < 0:
            raise ValueError("campaign decimal usage must be non-negative")

    def __add__(self, other: "CampaignUsage") -> "CampaignUsage":
        if not isinstance(other, CampaignUsage):
            return NotImplemented
        return CampaignUsage(
            tokens=self.tokens + other.tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            wall_seconds=self.wall_seconds + other.wall_seconds,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "tokens": self.tokens,
            "cost_usd": str(self.cost_usd),
            "wall_seconds": str(self.wall_seconds),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CampaignUsage":
        return cls(
            tokens=_non_negative_int(value.get("tokens"), "campaign tokens"),
            cost_usd=Decimal(str(value.get("cost_usd", "0"))),
            wall_seconds=Decimal(str(value.get("wall_seconds", "0"))),
        )


@dataclass(frozen=True)
class CandidateQualityProgress:
    """Bounded, comparable candidate quality signals for cross-run progress."""

    score_points: int | None = None
    groundedness_tenths: int | None = None
    command_pass_basis_points: int | None = None
    evidence_incomplete: bool | None = None
    deterministic_signal: bool | None = None
    global_regression_passed: bool | None = None
    failed_repetition_count: int | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "score_points",
            "groundedness_tenths",
            "command_pass_basis_points",
            "failed_repetition_count",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{field_name} must be a non-negative integer")
        if (
            self.command_pass_basis_points is not None
            and self.command_pass_basis_points > 10_000
        ):
            raise ValueError("command pass rate basis points exceed one hundred percent")
        for field_name in (
            "evidence_incomplete",
            "deterministic_signal",
            "global_regression_passed",
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{field_name} must be boolean when present")

    def delta_from(
        self,
        previous: "CandidateQualityProgress | None",
    ) -> tuple[str, ...] | None:
        if previous is None:
            return ()
        # These signals are candidate-independent verification invariants or
        # safety-critical quality floors. A later run may not trade them away
        # merely to obtain a higher stochastic judge score.
        if _optional_int_regressed(
            self.command_pass_basis_points,
            previous.command_pass_basis_points,
        ):
            return None
        if _optional_int_regressed(
            self.groundedness_tenths,
            previous.groundedness_tenths,
        ):
            return None
        if _optional_int_increased(
            self.failed_repetition_count,
            previous.failed_repetition_count,
        ):
            return None
        if (
            previous.evidence_incomplete is False
            and self.evidence_incomplete is True
        ):
            return None
        if (
            previous.deterministic_signal is True
            and self.deterministic_signal is False
        ):
            return None
        if (
            previous.global_regression_passed is True
            and self.global_regression_passed is False
        ):
            return None

        delta: set[str] = set()
        if _optional_int_increased(self.score_points, previous.score_points):
            delta.add(f"quality-score-points:{self.score_points}")
        if _optional_int_increased(
            self.groundedness_tenths,
            previous.groundedness_tenths,
        ):
            delta.add(
                f"quality-groundedness-tenths:{self.groundedness_tenths}"
            )
        if _optional_int_increased(
            self.command_pass_basis_points,
            previous.command_pass_basis_points,
        ):
            delta.add(
                f"quality-command-pass-bps:{self.command_pass_basis_points}"
            )
        if (
            previous.evidence_incomplete is True
            and self.evidence_incomplete is False
        ):
            delta.add("quality-evidence-complete")
        if (
            previous.deterministic_signal is False
            and self.deterministic_signal is True
        ):
            delta.add("quality-deterministic-signal")
        if (
            previous.global_regression_passed is False
            and self.global_regression_passed is True
        ):
            delta.add("quality-global-regression-passed")
        if (
            self.failed_repetition_count is not None
            and previous.failed_repetition_count is not None
            and self.failed_repetition_count < previous.failed_repetition_count
        ):
            delta.add(
                f"quality-failed-repetitions:{self.failed_repetition_count}"
            )
        return tuple(sorted(delta))

    def to_dict(self) -> dict[str, object]:
        return {
            "score_points": self.score_points,
            "groundedness_tenths": self.groundedness_tenths,
            "command_pass_basis_points": self.command_pass_basis_points,
            "evidence_incomplete": self.evidence_incomplete,
            "deterministic_signal": self.deterministic_signal,
            "global_regression_passed": self.global_regression_passed,
            "failed_repetition_count": self.failed_repetition_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CandidateQualityProgress":
        return cls(
            score_points=_optional_non_negative_int(value.get("score_points")),
            groundedness_tenths=_optional_non_negative_int(
                value.get("groundedness_tenths")
            ),
            command_pass_basis_points=_optional_non_negative_int(
                value.get("command_pass_basis_points")
            ),
            evidence_incomplete=_optional_bool(value.get("evidence_incomplete")),
            deterministic_signal=_optional_bool(value.get("deterministic_signal")),
            global_regression_passed=_optional_bool(
                value.get("global_regression_passed")
            ),
            failed_repetition_count=_optional_non_negative_int(
                value.get("failed_repetition_count")
            ),
        )


@dataclass(frozen=True)
class TrustedMeasurementProgress:
    """Campaign progress from controlled evidence, separate from candidate score."""

    authoritative: bool = False
    readiness_rank: int = 0
    independent_case_count: int = 0
    comparable_pair_count: int = 0
    validity_status: str | None = None
    effect_direction: str | None = None
    confidence_lower_micros: int | None = None
    promotion_eligible: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "readiness_rank",
            "independent_case_count",
            "comparable_pair_count",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if self.confidence_lower_micros is not None and (
            isinstance(self.confidence_lower_micros, bool)
            or not isinstance(self.confidence_lower_micros, int)
        ):
            raise ValueError("confidence_lower_micros must be an integer")

    def delta_from(
        self,
        previous: "TrustedMeasurementProgress | None",
    ) -> tuple[str, ...] | None:
        if previous is None:
            return tuple(
                item
                for item in (
                    f"measurement-readiness:{self.readiness_rank}",
                    f"measurement-independent-cases:{self.independent_case_count}",
                    f"measurement-comparable-pairs:{self.comparable_pair_count}",
                    (
                        "measurement-promotion-eligible"
                        if self.promotion_eligible
                        else None
                    ),
                )
                if item is not None
            )
        if previous.authoritative and (
            self.readiness_rank < previous.readiness_rank
            or self.independent_case_count < previous.independent_case_count
            or self.comparable_pair_count < previous.comparable_pair_count
            or (previous.promotion_eligible and not self.promotion_eligible)
            or (
                previous.effect_direction == "positive"
                and self.effect_direction in {"neutral", "negative", "unmeasured"}
            )
        ):
            return None
        delta: set[str] = set()
        if self.readiness_rank > previous.readiness_rank:
            delta.add(f"measurement-readiness:{self.readiness_rank}")
        if self.independent_case_count > previous.independent_case_count:
            delta.add(
                f"measurement-independent-cases:{self.independent_case_count}"
            )
        if self.comparable_pair_count > previous.comparable_pair_count:
            delta.add(
                f"measurement-comparable-pairs:{self.comparable_pair_count}"
            )
        if self.promotion_eligible and not previous.promotion_eligible:
            delta.add("measurement-promotion-eligible")
        if (
            self.confidence_lower_micros is not None
            and previous.confidence_lower_micros is not None
            and self.confidence_lower_micros
            > previous.confidence_lower_micros
        ):
            delta.add(
                "measurement-confidence-lower-micros:"
                f"{self.confidence_lower_micros}"
            )
        return tuple(sorted(delta))

    def to_dict(self) -> dict[str, object]:
        return {
            "authoritative": self.authoritative,
            "readiness_rank": self.readiness_rank,
            "independent_case_count": self.independent_case_count,
            "comparable_pair_count": self.comparable_pair_count,
            "validity_status": self.validity_status,
            "effect_direction": self.effect_direction,
            "confidence_lower_micros": self.confidence_lower_micros,
            "promotion_eligible": self.promotion_eligible,
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
    ) -> "TrustedMeasurementProgress":
        raw_lower = value.get("confidence_lower_micros")
        return cls(
            authoritative=value.get("authoritative") is True,
            readiness_rank=_non_negative_int(
                value.get("readiness_rank"), "measurement readiness rank"
            ),
            independent_case_count=_non_negative_int(
                value.get("independent_case_count"),
                "measurement independent case count",
            ),
            comparable_pair_count=_non_negative_int(
                value.get("comparable_pair_count"),
                "measurement comparable pair count",
            ),
            validity_status=_optional_string(value.get("validity_status")),
            effect_direction=_optional_string(value.get("effect_direction")),
            confidence_lower_micros=(
                int(raw_lower)
                if isinstance(raw_lower, int) and not isinstance(raw_lower, bool)
                else None
            ),
            promotion_eligible=value.get("promotion_eligible") is True,
        )


@dataclass(frozen=True)
class SelfImprovementProgress:
    deepest_stage_rank: int = 0
    semantic_frontier_ids: tuple[str, ...] = ()
    constraint_ids: tuple[str, ...] = ()
    passed_gate_ids: tuple[str, ...] = ()
    candidate_quality: CandidateQualityProgress | None = None
    measurement: "TrustedMeasurementProgress | None" = None
    covered_capability_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.deepest_stage_rank, bool) or self.deepest_stage_rank < 0:
            raise ValueError("deepest stage rank must be non-negative")
        for field_name in (
            "semantic_frontier_ids",
            "constraint_ids",
            "passed_gate_ids",
            "covered_capability_ids",
        ):
            values = tuple(sorted({str(item) for item in getattr(self, field_name) if str(item)}))
            object.__setattr__(self, field_name, values)

    def delta_from(self, previous: "SelfImprovementProgress | None") -> tuple[str, ...]:
        if previous is None:
            return tuple(
                sorted(
                    (
                        *self.semantic_frontier_ids,
                        *self.constraint_ids,
                        *(f"passed-gate:{item}" for item in self.passed_gate_ids),
                        *(
                            f"covered-capability:{item}"
                            for item in self.covered_capability_ids
                        ),
                    )
                )
            )
        # Campaign progress is monotonic. A new diagnostic identity is useful
        # feedback, but it is not improvement when the run lost an already
        # reached stage, recovery achievement, or passing verification gate.
        # This prevents a later run from replacing a stronger champion merely
        # because it exposed a differently worded failure frontier.
        constraint_delta = set(self.constraint_ids) - set(
            previous.constraint_ids
        )
        if self.deepest_stage_rank < previous.deepest_stage_rank:
            # A candidate may intentionally return from replay/evaluation to
            # source conformance after those stages expose a deeper owner or
            # invariant. That is repair-contract progress even though the
            # execution-stage rank is lower; refusing it strands a newly
            # discovered constraint at the Campaign boundary.
            return tuple(sorted(constraint_delta))
        previous_recovery = {
            item
            for item in previous.semantic_frontier_ids
            if item.startswith("recovery-member-")
        }
        current_recovery = {
            item
            for item in self.semantic_frontier_ids
            if item.startswith("recovery-member-")
        }
        if not previous_recovery.issubset(current_recovery):
            return ()
        if not set(previous.passed_gate_ids).issubset(self.passed_gate_ids):
            return ()
        if not set(previous.covered_capability_ids).issubset(
            self.covered_capability_ids
        ):
            return ()
        quality_delta: tuple[str, ...] = ()
        if self.candidate_quality is not None:
            quality_delta_result = self.candidate_quality.delta_from(
                previous.candidate_quality
            )
            if quality_delta_result is None:
                return ()
            quality_delta = quality_delta_result
        measurement_delta: tuple[str, ...] = ()
        if self.measurement is not None:
            measurement_delta_result = self.measurement.delta_from(
                previous.measurement
            )
            if measurement_delta_result is None:
                return ()
            measurement_delta = measurement_delta_result
        elif previous.measurement is not None and previous.measurement.authoritative:
            return ()
        # New failure-event identities are diagnostics, not achievements.
        # They seed the first cycle so a typed repair can start, but only
        # monotonic recovery or coarsely bucketed quality improvements may
        # advance a later Campaign cycle.
        delta = current_recovery - previous_recovery
        delta.update(constraint_delta)
        delta.update(quality_delta)
        delta.update(measurement_delta)
        delta.update(
            f"passed-gate:{item}"
            for item in set(self.passed_gate_ids) - set(previous.passed_gate_ids)
        )
        delta.update(
            f"covered-capability:{item}"
            for item in set(self.covered_capability_ids)
            - set(previous.covered_capability_ids)
        )
        if self.deepest_stage_rank > previous.deepest_stage_rank:
            delta.add(f"stage-rank:{self.deepest_stage_rank}")
        return tuple(sorted(delta))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "deepest_stage_rank": self.deepest_stage_rank,
            "semantic_frontier_ids": list(self.semantic_frontier_ids),
            "constraint_ids": list(self.constraint_ids),
            "passed_gate_ids": list(self.passed_gate_ids),
            "candidate_quality": (
                self.candidate_quality.to_dict()
                if self.candidate_quality is not None
                else None
            ),
            "measurement": (
                self.measurement.to_dict()
                if self.measurement is not None
                else None
            ),
            "covered_capability_ids": list(self.covered_capability_ids),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SelfImprovementProgress":
        if value.get("schema_version") != PROGRESS_SCHEMA_VERSION:
            raise ValueError("unsupported self-improvement progress schema")
        raw_quality = value.get("candidate_quality")
        raw_measurement = value.get("measurement")
        return cls(
            deepest_stage_rank=_non_negative_int(
                value.get("deepest_stage_rank"), "deepest stage rank"
            ),
            semantic_frontier_ids=_string_tuple(value.get("semantic_frontier_ids")),
            constraint_ids=_string_tuple(value.get("constraint_ids")),
            passed_gate_ids=_string_tuple(value.get("passed_gate_ids")),
            candidate_quality=(
                CandidateQualityProgress.from_dict(raw_quality)
                if isinstance(raw_quality, Mapping)
                else None
            ),
            measurement=(
                TrustedMeasurementProgress.from_dict(raw_measurement)
                if isinstance(raw_measurement, Mapping)
                else None
            ),
            covered_capability_ids=_string_tuple(
                value.get("covered_capability_ids", ())
            ),
        )


@dataclass(frozen=True)
class SelfImprovementDisposition:
    kind: SelfImprovementDispositionKind
    reason_code: str
    owner: str | None = None
    stage: str | None = None
    scope: str | None = None
    repairable: bool = False
    progress_delta_ids: tuple[str, ...] = ()
    diagnostic_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", SelfImprovementDispositionKind(self.kind))
        if not re.fullmatch(r"[a-z0-9][a-z0-9_]{0,95}", self.reason_code):
            raise ValueError("disposition reason_code must be stable lower-snake-case")
        object.__setattr__(self, "progress_delta_ids", tuple(sorted(set(self.progress_delta_ids))))
        object.__setattr__(self, "diagnostic_refs", tuple(sorted(set(self.diagnostic_refs)))[:16])
        if self.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE and (
            self.owner != "candidate" or not self.repairable
        ):
            raise ValueError("candidate continuation requires candidate-owned repairable work")
        if self.kind is SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE and (
            self.owner != "infrastructure" or not self.repairable
        ):
            raise ValueError(
                "infrastructure retry requires retryable infrastructure ownership"
            )

    @property
    def continuable(self) -> bool:
        return self.kind in {
            SelfImprovementDispositionKind.CONTINUE_CANDIDATE,
            SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
            SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE,
            SelfImprovementDispositionKind.COLLECT_MORE_EVIDENCE,
            SelfImprovementDispositionKind.REPAIR_MEASUREMENT,
            SelfImprovementDispositionKind.SWITCH_GENERATOR,
            SelfImprovementDispositionKind.SWITCH_SCHEDULER,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": DISPOSITION_SCHEMA_VERSION,
            "kind": self.kind.value,
            "reason_code": self.reason_code,
            "continuable": self.continuable,
            "owner": self.owner,
            "stage": self.stage,
            "scope": self.scope,
            "repairable": self.repairable,
            "progress_delta_ids": list(self.progress_delta_ids),
            "diagnostic_refs": list(self.diagnostic_refs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SelfImprovementDisposition":
        if value.get("schema_version") != DISPOSITION_SCHEMA_VERSION:
            raise ValueError("unsupported self-improvement disposition schema")
        return cls(
            kind=SelfImprovementDispositionKind(str(value.get("kind"))),
            reason_code=str(value.get("reason_code") or ""),
            owner=_optional_string(value.get("owner")),
            stage=_optional_string(value.get("stage")),
            scope=_optional_string(value.get("scope")),
            repairable=value.get("repairable") is True,
            progress_delta_ids=_string_tuple(value.get("progress_delta_ids")),
            diagnostic_refs=_string_tuple(value.get("diagnostic_refs")),
        )


@dataclass(frozen=True)
class SelfImprovementCampaign:
    campaign_id: str
    objective: str
    status: SelfImprovementCampaignStatus
    request: Mapping[str, Any]
    request_fingerprint: str
    source_fingerprint: str
    source_snapshot: Mapping[str, Any]
    target_fingerprint: str
    verification_fingerprint: str
    max_cycles: int = DEFAULT_MAX_IMPROVEMENT_CYCLES
    cycle_index: int = 0
    run_ids: tuple[str, ...] = ()
    cumulative_usage: CampaignUsage = CampaignUsage()
    cumulative_authoritative_candidates: int = 0
    repair_continuation_used: bool = False
    max_measurement_retries: int = DEFAULT_MAX_MEASUREMENT_RETRIES
    max_infrastructure_retries: int = DEFAULT_MAX_INFRASTRUCTURE_RETRIES
    measurement_ledger: CampaignMeasurementLedgerV2 = CampaignMeasurementLedgerV2()
    measurement_pending_run_id: str | None = None
    measurement_pending_candidate_id: str | None = None
    latest_progress: SelfImprovementProgress | None = None
    latest_disposition: SelfImprovementDisposition | None = None
    latest_measurement_outcome: CampaignMeasurementOutcomeV2 | None = None
    latest_report_path: str | None = None
    goal_handoff_path: str | None = None
    contract_stable_cycle_count: int = 0

    def __post_init__(self) -> None:
        _validate_id(self.campaign_id, "campaign_id")
        object.__setattr__(self, "status", SelfImprovementCampaignStatus(self.status))
        if not str(self.objective).strip():
            raise ValueError("campaign objective must be non-empty")
        if isinstance(self.max_cycles, bool) or self.max_cycles <= 0:
            raise ValueError("max_cycles must be positive")
        if not isinstance(self.repair_continuation_used, bool):
            raise ValueError("repair_continuation_used must be boolean")
        if (
            isinstance(self.max_measurement_retries, bool)
            or self.max_measurement_retries < 0
        ):
            raise ValueError("campaign measurement retry limit must be non-negative")
        if (
            self.measurement_ledger.invalid_retry_count
            > self.max_measurement_retries
        ):
            raise ValueError("campaign measurement retry count is outside its bound")
        if (
            isinstance(self.max_infrastructure_retries, bool)
            or self.max_infrastructure_retries < 0
        ):
            raise ValueError(
                "campaign infrastructure retry limit must be non-negative"
            )
        effective_max_cycles = (
            self.max_cycles
            + self.measurement_ledger.control_plane_run_count
            + int(self.repair_continuation_used)
        )
        if (
            isinstance(self.cycle_index, bool)
            or not 0 <= self.cycle_index <= effective_max_cycles
        ):
            raise ValueError("campaign cycle index is outside its bound")
        if len(self.run_ids) != self.cycle_index:
            raise ValueError("campaign run lineage must match its cycle index")
        for run_id in self.run_ids:
            _validate_id(run_id, "run_id")
        pending_values = (
            self.measurement_pending_run_id,
            self.measurement_pending_candidate_id,
        )
        if any(pending_values) and not all(pending_values):
            raise ValueError(
                "measurement-pending run and candidate ids must be declared together"
            )
        for pending_value in pending_values:
            if pending_value is not None:
                _validate_id(pending_value, "measurement_pending_id")
        if (
            isinstance(self.cumulative_authoritative_candidates, bool)
            or self.cumulative_authoritative_candidates < 0
        ):
            raise ValueError(
                "campaign cumulative authoritative candidate count must be non-negative"
            )
        if (
            isinstance(self.contract_stable_cycle_count, bool)
            or not isinstance(self.contract_stable_cycle_count, int)
            or not 0 <= self.contract_stable_cycle_count <= self.cycle_index
        ):
            raise ValueError(
                "contract stable cycle count must be within campaign lineage"
            )
        for fingerprint in (
            self.request_fingerprint,
            self.source_fingerprint,
            self.target_fingerprint,
            self.verification_fingerprint,
        ):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint):
                raise ValueError("campaign fingerprints must be full sha256 values")
        request = dict(self.request)
        object.__setattr__(self, "request", request)
        source_snapshot = dict(self.source_snapshot)
        object.__setattr__(self, "source_snapshot", source_snapshot)
        if _fingerprint(request) != self.request_fingerprint:
            raise ValueError("campaign request fingerprint does not match request")
        if _fingerprint(source_snapshot) != self.source_fingerprint:
            raise ValueError("campaign source fingerprint does not match snapshot")
        if _fingerprint(
            {
                "target": request.get("target"),
                "infer_target": request.get("infer_target"),
                "inferred_new_skill_policy": request.get(
                    "inferred_new_skill_policy"
                ),
            }
        ) != self.target_fingerprint:
            raise ValueError("campaign target fingerprint does not match request")
        if _fingerprint(_verification_request(request)) != self.verification_fingerprint:
            raise ValueError("campaign verification fingerprint does not match request")
        if self.status is SelfImprovementCampaignStatus.COMPLETE and (
            self.latest_disposition is None
            or self.latest_disposition.kind is not SelfImprovementDispositionKind.COMPLETE
        ):
            raise ValueError("complete campaign requires a complete disposition")
        raw_contract = request.get("skill_evolution_contract")
        if (
            self.status is SelfImprovementCampaignStatus.COMPLETE
            and isinstance(raw_contract, Mapping)
        ):
            required_stable_cycles = _positive_int(
                raw_contract.get("required_stable_cycles", 1),
                "required_stable_cycles",
            )
            if self.contract_stable_cycle_count < required_stable_cycles:
                raise ValueError(
                    "complete campaign requires stable Skill contract cycles"
                )
        if (
            self.status is SelfImprovementCampaignStatus.COMPLETE
            and self.latest_measurement_outcome is not None
            and self.latest_measurement_outcome.projection
            is not CampaignMeasurementProjection.SUCCEEDED
        ):
            raise ValueError("complete campaign requires a succeeded measurement")

    @property
    def measurement_retry_count(self) -> int:
        """Legacy projection; the v2 ledger is the only stored counter source."""

        return self.measurement_ledger.invalid_retry_count

    @property
    def measurement_continuation_count(self) -> int:
        return self.measurement_ledger.continuation_count

    @property
    def framework_blocked_count(self) -> int:
        return self.measurement_ledger.framework_blocked_count

    @property
    def infrastructure_retry_count(self) -> int:
        return self.measurement_ledger.infrastructure_retry_count

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CAMPAIGN_SCHEMA_VERSION,
            "campaign_id": self.campaign_id,
            "objective": self.objective,
            "status": self.status.value,
            "request": _json_value(self.request),
            "request_fingerprint": self.request_fingerprint,
            "source_fingerprint": self.source_fingerprint,
            "source_snapshot": _json_value(self.source_snapshot),
            "target_fingerprint": self.target_fingerprint,
            "verification_fingerprint": self.verification_fingerprint,
            "max_cycles": self.max_cycles,
            "cycle_index": self.cycle_index,
            "run_ids": list(self.run_ids),
            "cumulative_usage": self.cumulative_usage.to_dict(),
            "cumulative_authoritative_candidates": (
                self.cumulative_authoritative_candidates
            ),
            "repair_continuation_used": self.repair_continuation_used,
            "max_measurement_retries": self.max_measurement_retries,
            "max_infrastructure_retries": self.max_infrastructure_retries,
            "measurement_ledger": self.measurement_ledger.to_dict(),
            "measurement_retry_count": self.measurement_retry_count,
            "measurement_continuation_count": self.measurement_continuation_count,
            "framework_blocked_count": self.framework_blocked_count,
            "infrastructure_retry_count": self.infrastructure_retry_count,
            "measurement_pending_run_id": self.measurement_pending_run_id,
            "measurement_pending_candidate_id": (
                self.measurement_pending_candidate_id
            ),
            "latest_progress": (
                self.latest_progress.to_dict() if self.latest_progress is not None else None
            ),
            "latest_disposition": (
                self.latest_disposition.to_dict()
                if self.latest_disposition is not None
                else None
            ),
            "latest_measurement_outcome": (
                self.latest_measurement_outcome.to_dict()
                if self.latest_measurement_outcome is not None
                else None
            ),
            "latest_report_path": self.latest_report_path,
            "goal_handoff_path": self.goal_handoff_path,
            "contract_stable_cycle_count": self.contract_stable_cycle_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SelfImprovementCampaign":
        if value.get("schema_version") != CAMPAIGN_SCHEMA_VERSION:
            raise ValueError("unsupported self-improvement campaign schema")
        raw_request = value.get("request")
        raw_usage = value.get("cumulative_usage")
        raw_source_snapshot = value.get("source_snapshot")
        if (
            not isinstance(raw_request, Mapping)
            or not isinstance(raw_source_snapshot, Mapping)
            or not isinstance(raw_usage, Mapping)
        ):
            raise ValueError("campaign request, source snapshot, and usage must be mappings")
        raw_progress = value.get("latest_progress")
        raw_disposition = value.get("latest_disposition")
        raw_measurement_outcome = value.get("latest_measurement_outcome")
        run_ids = _string_tuple(value.get("run_ids"))
        raw_ledger = value.get("measurement_ledger")
        if isinstance(raw_ledger, Mapping):
            measurement_ledger = CampaignMeasurementLedgerV2.from_dict(raw_ledger)
            legacy_retry_count = _non_negative_int(
                value.get(
                    "measurement_retry_count",
                    measurement_ledger.invalid_retry_count,
                ),
                "measurement_retry_count",
            )
            if legacy_retry_count != measurement_ledger.invalid_retry_count:
                raise ValueError("legacy measurement retry count differs from ledger")
            legacy_continuation_count = _non_negative_int(
                value.get(
                    "measurement_continuation_count",
                    measurement_ledger.continuation_count,
                ),
                "measurement_continuation_count",
            )
            if legacy_continuation_count != measurement_ledger.continuation_count:
                raise ValueError(
                    "legacy measurement continuation count differs from ledger"
                )
            legacy_framework_blocked_count = _non_negative_int(
                value.get(
                    "framework_blocked_count",
                    measurement_ledger.framework_blocked_count,
                ),
                "framework_blocked_count",
            )
            if (
                legacy_framework_blocked_count
                != measurement_ledger.framework_blocked_count
            ):
                raise ValueError(
                    "legacy framework blocked count differs from ledger"
                )
        else:
            legacy_retry_count = _non_negative_int(
                value.get("measurement_retry_count", 0),
                "measurement_retry_count",
            )
            if legacy_retry_count > len(run_ids):
                raise ValueError("legacy measurement retry count exceeds run lineage")
            measurement_ledger = CampaignMeasurementLedgerV2(
                invalid_retry_run_ids=(
                    run_ids[-legacy_retry_count:] if legacy_retry_count else ()
                )
            )
        return cls(
            campaign_id=str(value.get("campaign_id") or ""),
            objective=str(value.get("objective") or ""),
            status=SelfImprovementCampaignStatus(str(value.get("status"))),
            request=dict(raw_request),
            request_fingerprint=str(value.get("request_fingerprint") or ""),
            source_fingerprint=str(value.get("source_fingerprint") or ""),
            source_snapshot=dict(raw_source_snapshot),
            target_fingerprint=str(value.get("target_fingerprint") or ""),
            verification_fingerprint=str(value.get("verification_fingerprint") or ""),
            max_cycles=_positive_int(value.get("max_cycles"), "max_cycles"),
            cycle_index=_non_negative_int(value.get("cycle_index"), "cycle_index"),
            run_ids=run_ids,
            cumulative_usage=CampaignUsage.from_dict(raw_usage),
            cumulative_authoritative_candidates=_non_negative_int(
                value.get("cumulative_authoritative_candidates", 0),
                "cumulative_authoritative_candidates",
            ),
            repair_continuation_used=(
                value.get("repair_continuation_used") is True
            ),
            max_measurement_retries=_non_negative_int(
                value.get(
                    "max_measurement_retries",
                    DEFAULT_MAX_MEASUREMENT_RETRIES,
                ),
                "max_measurement_retries",
            ),
            max_infrastructure_retries=_non_negative_int(
                value.get(
                    "max_infrastructure_retries",
                    DEFAULT_MAX_INFRASTRUCTURE_RETRIES,
                ),
                "max_infrastructure_retries",
            ),
            measurement_ledger=measurement_ledger,
            measurement_pending_run_id=_optional_string(
                value.get("measurement_pending_run_id")
            ),
            measurement_pending_candidate_id=_optional_string(
                value.get("measurement_pending_candidate_id")
            ),
            latest_progress=(
                SelfImprovementProgress.from_dict(raw_progress)
                if isinstance(raw_progress, Mapping)
                else None
            ),
            latest_disposition=(
                SelfImprovementDisposition.from_dict(raw_disposition)
                if isinstance(raw_disposition, Mapping)
                else None
            ),
            latest_measurement_outcome=(
                CampaignMeasurementOutcomeV2.from_dict(raw_measurement_outcome)
                if isinstance(raw_measurement_outcome, Mapping)
                else None
            ),
            latest_report_path=_optional_string(value.get("latest_report_path")),
            goal_handoff_path=_optional_string(value.get("goal_handoff_path")),
            contract_stable_cycle_count=_non_negative_int(
                value.get("contract_stable_cycle_count", 0),
                "contract_stable_cycle_count",
            ),
        )


RunOnce = Callable[..., Mapping[str, Any]]


class SelfImprovementCampaignController:
    def __init__(
        self,
        *,
        workspace_root: str | Path,
        run_once: RunOnce | None = None,
    ) -> None:
        from aworld.self_evolve.store import FilesystemSelfEvolveStore

        self.workspace_root = Path(workspace_root)
        self.store = FilesystemSelfEvolveStore(self.workspace_root)
        self.run_once = run_once or _default_run_once

    def create(
        self,
        request: Mapping[str, Any],
        *,
        max_cycles: int = DEFAULT_MAX_IMPROVEMENT_CYCLES,
    ) -> SelfImprovementCampaign:
        max_cycles = _positive_int(max_cycles, "max_improvement_cycles")
        persistent = persistent_campaign_request(request)
        explicit_total_tokens = request.get("total_run_token_budget")
        explicit_legacy_tokens = request.get("max_run_tokens")
        if explicit_total_tokens is not None:
            persistent["_campaign_total_run_token_budget"] = int(
                explicit_total_tokens
            )
        elif explicit_legacy_tokens is not None:
            persistent["_campaign_total_run_token_budget"] = int(
                explicit_legacy_tokens
            )
        if str(persistent.get("apply_policy") or "proposal") not in {
            "auto_verified",
            "verified_only",
        }:
            raise ValueError(
                "self-improvement campaigns require a verified apply policy"
            )
        if not _request_has_source(persistent):
            raise ValueError("a self-improvement campaign requires an eval source")
        raw_skill_evolution_contract = persistent.get(
            "skill_evolution_contract"
        )
        if isinstance(raw_skill_evolution_contract, Mapping):
            from aworld.self_evolve.skill_evolution_contract import (
                SkillEvolutionContract,
            )

            contract = SkillEvolutionContract.from_dict(
                raw_skill_evolution_contract
            )
            if contract.required_stable_cycles > max_cycles:
                raise ValueError(
                    "max_improvement_cycles must cover required_stable_cycles"
                )
            authoritative_limit = _positive_int(
                persistent.get("max_full_evaluation_candidates") or 3,
                "max_full_evaluation_candidates",
            )
            if contract.required_stable_cycles > authoritative_limit:
                raise ValueError(
                    "max_full_evaluation_candidates must cover "
                    "required_stable_cycles"
                )
        request_fingerprint = _fingerprint(persistent)
        source_snapshot = _source_snapshot(
            persistent,
            workspace_root=self.workspace_root,
        )
        seed = hashlib.sha256(
            f"{request_fingerprint}:{time.time_ns()}:{uuid.uuid4().hex}".encode("utf-8")
        ).hexdigest()[:20]
        campaign_id = f"campaign-{seed}"
        objective = str(
            persistent.get("task")
            or f"Reach a verified self-evolve outcome for {persistent.get('target') or 'the inferred target'}"
        )
        campaign = SelfImprovementCampaign(
            campaign_id=campaign_id,
            objective=objective,
            status=SelfImprovementCampaignStatus.ACTIVE,
            request=persistent,
            request_fingerprint=request_fingerprint,
            source_fingerprint=_fingerprint(source_snapshot),
            source_snapshot=source_snapshot,
            target_fingerprint=_fingerprint(
                {
                    "target": persistent.get("target"),
                    "infer_target": persistent.get("infer_target"),
                    "inferred_new_skill_policy": persistent.get(
                        "inferred_new_skill_policy"
                    ),
                }
            ),
            verification_fingerprint=_fingerprint(_verification_request(persistent)),
            max_cycles=max_cycles,
        )
        self.store.write_campaign(campaign)
        return campaign

    def load(self, campaign_id: str) -> SelfImprovementCampaign:
        return self.store.read_campaign(campaign_id)

    def advance_once(
        self,
        campaign: SelfImprovementCampaign,
        *,
        runtime_request: Mapping[str, Any] | None = None,
    ) -> tuple[SelfImprovementCampaign, Mapping[str, Any]]:
        if campaign.status in {
            SelfImprovementCampaignStatus.COMPLETE,
            SelfImprovementCampaignStatus.BUDGET_LIMITED,
            SelfImprovementCampaignStatus.EXHAUSTED,
        }:
            raise ValueError(f"campaign {campaign.campaign_id} is terminal")
        stored = self.store.read_campaign(campaign.campaign_id)
        if stored.to_dict() != campaign.to_dict():
            raise ValueError("campaign checkpoint changed before advance")
        checkpoint_migration: dict[str, object] | None = None
        # Legacy campaigns could persist a candidate-only pending marker after
        # screening even though no authoritative replay checkpoint existed.
        # Migrate that state before building the next request.  Continuing with
        # a missing/invalid typed checkpoint would either crash in the runner or
        # silently mix qualification evidence into authoritative measurement.
        if (
            campaign.measurement_pending_run_id is not None
            and campaign.measurement_pending_candidate_id is not None
            and _campaign_measurement_resume_checkpoint(
                self.store,
                campaign=campaign,
            )
            is None
        ):
            checkpoint_migration = {
                "schema_version": (
                    "aworld.self_evolve.measurement_checkpoint_migration.v1"
                ),
                "action": "cleared_invalid_legacy_pending_marker",
                "source_run_id": campaign.measurement_pending_run_id,
                "candidate_id": campaign.measurement_pending_candidate_id,
                "reason_code": "authoritative_checkpoint_missing_or_invalid",
            }
            campaign = replace(
                campaign,
                measurement_pending_run_id=None,
                measurement_pending_candidate_id=None,
            )
            self.store.write_campaign(campaign)
        if campaign.cycle_index >= _campaign_effective_max_cycles(campaign):
            exhausted = _exhaust_campaign(
                campaign,
                reason_code=_campaign_exhaustion_reason(campaign),
            )
            self.store.write_campaign(exhausted)
            return exhausted, _campaign_summary(exhausted, {})

        request = dict(campaign.request)
        for key, value in dict(runtime_request or {}).items():
            if value is None:
                continue
            if key in _RUNTIME_ONLY_REQUEST_KEYS:
                request[key] = value
                continue
            if key in campaign.request and _json_value(value) == campaign.request[key]:
                request[key] = value
        try:
            request.update(_remaining_budget_request(campaign))
        except ValueError:
            limited = _limit_campaign(
                campaign,
                reason_code="campaign_cumulative_budget_exhausted",
            )
            self.store.write_campaign(limited)
            return limited, _campaign_summary(limited, {})
        authoritative_limit = _campaign_effective_authoritative_candidate_limit(
            campaign
        )
        remaining_authoritative_candidates = (
            authoritative_limit
            - campaign.cumulative_authoritative_candidates
        )
        if remaining_authoritative_candidates <= 0:
            exhausted = _exhaust_campaign(
                campaign,
                reason_code="campaign_authoritative_frontier_exhausted",
            )
            self.store.write_campaign(exhausted)
            return exhausted, _campaign_summary(exhausted, {})
        stable_cycle_reserve = 0
        raw_contract = campaign.request.get("skill_evolution_contract")
        if isinstance(raw_contract, Mapping):
            raw_required_stable_cycles = raw_contract.get(
                "required_stable_cycles", 1
            )
            required_stable_cycles = _positive_int(
                raw_required_stable_cycles,
                "required_stable_cycles",
            )
            stable_cycle_reserve = max(
                0,
                required_stable_cycles
                - campaign.contract_stable_cycle_count
                - 1,
            )
        available_authoritative_candidates = max(
            1,
            remaining_authoritative_candidates - stable_cycle_reserve,
        )
        request["max_full_evaluation_candidates"] = min(
            _positive_int(
                request.get("max_full_evaluation_candidates")
                or authoritative_limit,
                "max_full_evaluation_candidates",
            ),
            available_authoritative_candidates,
        )
        request.pop("_campaign_total_run_token_budget", None)
        next_cycle = campaign.cycle_index + 1
        run_id = f"{campaign.campaign_id}-cycle-{next_cycle:03d}"
        run_path = self.store.run_path(run_id)
        expected_report_path = run_path / "report.json"
        interrupted_archive_path: Path | None = None
        if (
            run_path.exists()
            and not expected_report_path.is_file()
        ):
            reservation = _interrupted_run_reservation(request)
            interrupted_archive_path = self.store.archive_interrupted_campaign_run(
                campaign_id=campaign.campaign_id,
                run_id=run_id,
                reserved_usage=reservation.to_dict(),
            )
            campaign = replace(
                campaign,
                cumulative_usage=campaign.cumulative_usage + reservation,
            )
            self.store.write_campaign(campaign)
            try:
                request.update(_remaining_budget_request(campaign))
            except ValueError:
                limited = _limit_campaign(
                    campaign,
                    reason_code="campaign_cumulative_budget_exhausted",
                )
                self.store.write_campaign(limited)
                summary = _campaign_summary(limited, {})
                summary["interrupted_run_archive_path"] = str(
                    interrupted_archive_path
                )
                return limited, summary
        prior_run_ids = _campaign_prior_run_ids_by_champion(
            self.store,
            campaign.run_ids,
        )
        request.update(
            {
                "workspace_root": str(self.workspace_root),
                "campaign_id": campaign.campaign_id,
                "campaign_cycle": next_cycle,
                "campaign_prior_run_ids": prior_run_ids,
                # Candidate feedback is champion-ordered, but scheduler state
                # is a checkpoint log and must follow Campaign chronology.
                "campaign_scheduler_checkpoint_run_ids": campaign.run_ids,
            }
        )
        if (
            campaign.measurement_pending_run_id is not None
            and campaign.measurement_pending_candidate_id is not None
        ):
            request["campaign_measurement_pending_run_id"] = (
                campaign.measurement_pending_run_id
            )
            request["campaign_measurement_pending_candidate_id"] = (
                campaign.measurement_pending_candidate_id
            )
        if campaign.run_ids:
            prior_target = self.store.read_report(prior_run_ids[-1]).get("target")
            if isinstance(prior_target, Mapping):
                request["campaign_expected_target"] = {
                    "target_type": prior_target.get("target_type"),
                    "target_id": prior_target.get("target_id"),
                }
        if expected_report_path.is_file() and not expected_report_path.is_symlink():
            recovered_report = self.store.read_report(run_id)
            summary = {
                "run_id": run_id,
                "status": recovered_report.get("status"),
                "report_path": str(expected_report_path),
                "selected_candidate_id": recovered_report.get(
                    "selected_candidate_id"
                ),
            }
        else:
            if run_path.exists():
                raise ValueError("campaign generation has an incomplete run checkpoint")
            summary = dict(self.run_once(**request))
            if interrupted_archive_path is not None:
                summary["interrupted_run_archive_path"] = str(
                    interrupted_archive_path
                )
        actual_run_id = str(summary.get("run_id") or run_id)
        if actual_run_id != run_id:
            raise ValueError("self-evolve run did not honor campaign run identity")
        report_path = Path(str(summary.get("report_path") or ""))
        if not report_path.is_file():
            raise ValueError("campaign run did not produce a report")
        report = self.store.read_report(actual_run_id)
        measurement_outcome = campaign_measurement_outcome_from_report(report)
        progress = self_improvement_progress(report)
        disposition = derive_self_improvement_disposition(
            report,
            previous_progress=campaign.latest_progress,
        )
        contract_stable_cycle_count = campaign.contract_stable_cycle_count
        skill_evolution = report.get("skill_evolution")
        if isinstance(skill_evolution, Mapping):
            required_stable_cycles = _positive_int(
                skill_evolution.get("required_stable_cycles", 1),
                "required_stable_cycles",
            )
            if (
                disposition.kind is SelfImprovementDispositionKind.COMPLETE
                and skill_evolution.get("coverage_satisfied") is True
            ):
                contract_stable_cycle_count = (
                    campaign.contract_stable_cycle_count + 1
                )
                if contract_stable_cycle_count < required_stable_cycles:
                    disposition = SelfImprovementDisposition(
                        kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
                        reason_code="skill_contract_stability_pending",
                        owner="campaign",
                        stage="held_out_verification",
                        scope="candidate",
                        repairable=True,
                        progress_delta_ids=disposition.progress_delta_ids,
                    )
            elif skill_evolution.get("coverage_satisfied") is not True:
                contract_stable_cycle_count = 0
            skill_evolution = dict(skill_evolution)
            skill_evolution.update(
                {
                    "stable_cycle_count": contract_stable_cycle_count,
                    "converged": bool(
                        skill_evolution.get("coverage_satisfied") is True
                        and contract_stable_cycle_count
                        >= required_stable_cycles
                    ),
                }
            )
            report["skill_evolution"] = skill_evolution
        elif (
            isinstance(
                campaign.request.get("skill_evolution_contract"), Mapping
            )
            and disposition.owner == "candidate"
        ):
            # Candidate-owned failure before authoritative replay breaks a
            # consecutive verification streak. Framework/measurement retries
            # preserve the last verified streak because they did not evaluate
            # a new candidate outcome.
            contract_stable_cycle_count = 0
        try:
            usage = campaign.cumulative_usage + campaign_usage_from_report(report)
        except ValueError:
            usage = campaign.cumulative_usage
            if (
                measurement_outcome is not None
                and disposition.kind is not SelfImprovementDispositionKind.COMPLETE
            ):
                measurement_outcome = CampaignMeasurementOutcomeV2(
                    execution_status=MeasurementExecutionStatus.FRAMEWORK_BLOCKED,
                    improvement_outcome=CandidateImprovementOutcome.UNKNOWN,
                    release_gates_passed=False,
                    continuation_available=False,
                    reason_code="campaign_usage_telemetry_missing",
                )
                disposition = _measurement_outcome_disposition(
                    measurement_outcome,
                    progress_delta_ids=disposition.progress_delta_ids,
                )
            elif disposition.kind is not SelfImprovementDispositionKind.COMPLETE:
                disposition = SelfImprovementDisposition(
                    kind=SelfImprovementDispositionKind.EXHAUSTED,
                    reason_code="campaign_usage_telemetry_missing",
                    owner=disposition.owner,
                    stage=disposition.stage,
                    scope=disposition.scope,
                    repairable=False,
                    progress_delta_ids=disposition.progress_delta_ids,
                    diagnostic_refs=disposition.diagnostic_refs,
                )
        status = _status_for_disposition(disposition)
        report_authoritative_candidate_count = (
            _report_authoritative_candidate_count(report)
        )
        pending_candidate_already_charged = (
            _campaign_pending_candidate_was_authoritative(
                self.store,
                campaign=campaign,
            )
        )
        cumulative_authoritative_candidates = (
            campaign.cumulative_authoritative_candidates
            + (
                0
                if pending_candidate_already_charged
                else report_authoritative_candidate_count
            )
        )
        paired_replay_continuation_requested = (
            _report_requests_paired_replay_continuation(report)
        )
        measurement_retry_requested = (
            disposition.kind
            is SelfImprovementDispositionKind.REPAIR_MEASUREMENT
            and disposition.scope == "shared_run"
            and not paired_replay_continuation_requested
            and (
                measurement_outcome is None
                or measurement_outcome.continuation_available
            )
        )
        measurement_continuation_requested = bool(
            paired_replay_continuation_requested
            or (
                measurement_outcome is not None
                and measurement_outcome.projection
                is CampaignMeasurementProjection.MEASUREMENT_INCOMPLETE
            )
        )
        framework_handoff_requested = bool(
            measurement_outcome is not None
            and measurement_outcome.projection
            is CampaignMeasurementProjection.FRAMEWORK_BLOCKED
            and disposition.kind is SelfImprovementDispositionKind.HANDOFF_GOAL
        )
        measurement_checkpoint = (
            _measurement_resume_checkpoint(
                self.store,
                run_id=actual_run_id,
                report=report,
            )
            if (
                measurement_retry_requested
                or measurement_continuation_requested
                or framework_handoff_requested
            )
            else None
        )
        if (
            measurement_checkpoint is None
            and (
                measurement_retry_requested
                or measurement_continuation_requested
                or framework_handoff_requested
            )
            and campaign.measurement_pending_run_id is not None
            and campaign.measurement_pending_candidate_id is not None
        ):
            # Resumed execution appends to the original frozen authority
            # graph. A later timeout has no cycle-local journal to discover,
            # so revalidate and retain the source checkpoint instead.
            measurement_checkpoint = _campaign_measurement_resume_checkpoint(
                self.store,
                campaign=campaign,
            )
        measurement_pending_candidate_id = (
            measurement_checkpoint.candidate_id
            if measurement_checkpoint is not None
            else None
        )
        measurement_retry_available = bool(
            measurement_retry_requested
            and measurement_pending_candidate_id is not None
            and campaign.measurement_retry_count
            < campaign.max_measurement_retries
        )
        if (
            measurement_retry_requested
            and not measurement_retry_available
            and measurement_outcome is not None
        ):
            measurement_outcome = CampaignMeasurementOutcomeV2(
                execution_status=measurement_outcome.execution_status,
                improvement_outcome=measurement_outcome.improvement_outcome,
                release_gates_passed=False,
                continuation_available=False,
                reason_code=(
                    "measurement_authority_checkpoint_missing_or_invalid"
                    if measurement_checkpoint is None
                    else "campaign_measurement_retry_limit_reached"
                ),
            )
            disposition = _measurement_outcome_disposition(
                measurement_outcome,
                progress_delta_ids=disposition.progress_delta_ids,
            )
            status = _status_for_disposition(disposition)
        measurement_continuation_available = bool(
            measurement_continuation_requested
            and measurement_pending_candidate_id is not None
        )
        framework_handoff_checkpoint_available = bool(
            framework_handoff_requested
            and measurement_checkpoint is not None
            and not isinstance(
                measurement_checkpoint, PairedReplayResumeCheckpointV1
            )
        )
        framework_control_plane_blocked = bool(
            disposition.kind is SelfImprovementDispositionKind.HANDOFF_GOAL
            and disposition.owner == "framework"
            and disposition.scope == "shared_run"
        )
        infrastructure_retry_requested = bool(
            disposition.kind
            is SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE
            and disposition.owner == "infrastructure"
            and disposition.scope == "shared_run"
        )
        infrastructure_retry_available = bool(
            infrastructure_retry_requested
            and campaign.infrastructure_retry_count
            < campaign.max_infrastructure_retries
        )
        if (
            measurement_continuation_requested
            and not measurement_continuation_available
            and measurement_outcome is not None
        ):
            measurement_outcome = CampaignMeasurementOutcomeV2(
                execution_status=measurement_outcome.execution_status,
                improvement_outcome=measurement_outcome.improvement_outcome,
                release_gates_passed=False,
                continuation_available=False,
                reason_code=(
                    "measurement_authority_checkpoint_missing_or_invalid"
                ),
            )
            disposition = _measurement_outcome_disposition(
                measurement_outcome,
                progress_delta_ids=disposition.progress_delta_ids,
            )
            status = _status_for_disposition(disposition)
        elif (
            paired_replay_continuation_requested
            and not measurement_continuation_available
        ):
            disposition = SelfImprovementDisposition(
                kind=SelfImprovementDispositionKind.EXHAUSTED,
                reason_code="paired_replay_checkpoint_missing_or_invalid",
                owner="evaluation_harness",
                stage="candidate_replay",
                scope="shared_run",
                repairable=False,
                progress_delta_ids=disposition.progress_delta_ids,
            )
            status = _status_for_disposition(disposition)
        measurement_ledger = campaign.measurement_ledger
        if infrastructure_retry_requested:
            # Infrastructure execution is control-plane work.  It gets its own
            # bounded retry ledger and never spends the mutation-cycle axis.
            measurement_ledger = measurement_ledger.charge_infrastructure_retry(
                actual_run_id
            )
        elif framework_control_plane_blocked:
            # A retained checkpoint describes where framework repair should
            # resume; it is not evidence that the measurement itself made
            # causal progress. Keep blocker and continuation ledgers
            # orthogonal so repeated framework failures cannot masquerade as
            # useful experiment continuation.
            measurement_ledger = measurement_ledger.charge_framework_blocked(
                actual_run_id
            )
        elif (
            measurement_continuation_available
            or framework_handoff_checkpoint_available
        ):
            measurement_ledger = measurement_ledger.charge_continuation(
                actual_run_id
            )
        elif measurement_retry_available:
            measurement_ledger = measurement_ledger.charge_invalid_retry(
                actual_run_id
            )
        preserve_measurement_checkpoint = bool(
            campaign.measurement_pending_run_id is not None
            and campaign.measurement_pending_candidate_id is not None
            and disposition.kind
            is SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE
            and disposition.scope == "shared_run"
        )
        advanced = replace(
            campaign,
            status=status,
            cycle_index=next_cycle,
            run_ids=(*campaign.run_ids, actual_run_id),
            cumulative_usage=usage,
            cumulative_authoritative_candidates=(
                cumulative_authoritative_candidates
            ),
            latest_progress=progress,
            latest_disposition=disposition,
            latest_measurement_outcome=measurement_outcome,
            latest_report_path=str(report_path),
            goal_handoff_path=None,
            contract_stable_cycle_count=contract_stable_cycle_count,
            measurement_ledger=measurement_ledger,
            measurement_pending_run_id=(
                getattr(measurement_checkpoint, "source_run_id", actual_run_id)
                if (
                    measurement_retry_available
                    or measurement_continuation_available
                    or framework_handoff_checkpoint_available
                )
                else campaign.measurement_pending_run_id
                if preserve_measurement_checkpoint
                else None
            ),
            measurement_pending_candidate_id=(
                measurement_pending_candidate_id
                if (
                    measurement_retry_available
                    or measurement_continuation_available
                    or framework_handoff_checkpoint_available
                )
                else campaign.measurement_pending_candidate_id
                if preserve_measurement_checkpoint
                else None
            ),
        )
        grant_repair_continuation = (
            disposition.kind is SelfImprovementDispositionKind.CONTINUE_CANDIDATE
            and disposition.owner == "candidate"
            and disposition.repairable
            and (
                next_cycle >= _campaign_effective_max_cycles(campaign)
                or cumulative_authoritative_candidates
                >= _campaign_effective_authoritative_candidate_limit(campaign)
            )
            and not campaign.repair_continuation_used
            and _report_has_new_candidate_repair_evidence(
                report,
                prior_reports=(
                    self.store.read_report(run_id)
                    for run_id in campaign.run_ids
                ),
            )
        )
        if infrastructure_retry_available:
            advanced = replace(
                advanced,
                status=SelfImprovementCampaignStatus.ACTIVE,
            )
        elif infrastructure_retry_requested:
            disposition = SelfImprovementDisposition(
                kind=SelfImprovementDispositionKind.PAUSE_OPERATOR,
                reason_code="campaign_infrastructure_retry_limit_reached",
                owner="infrastructure",
                stage=disposition.stage,
                scope="shared_run",
                repairable=False,
                progress_delta_ids=disposition.progress_delta_ids,
                diagnostic_refs=disposition.diagnostic_refs,
            )
            advanced = replace(
                advanced,
                status=SelfImprovementCampaignStatus.PAUSED,
                latest_disposition=disposition,
            )
        elif measurement_continuation_available:
            # A scheduler quantum/deadline boundary is resumable execution,
            # not a failed experiment or a candidate/measurement retry.
            advanced = replace(
                advanced,
                status=SelfImprovementCampaignStatus.ACTIVE,
            )
        elif measurement_retry_available:
            # The candidate package already passed candidate-owned admission.
            # Preserve it as an immutable measurement checkpoint and grant a
            # separately bounded control-plane retry.  This run does not
            # consume the candidate cycle frontier.
            advanced = replace(
                advanced,
                status=SelfImprovementCampaignStatus.ACTIVE,
            )
        elif measurement_retry_requested:
            advanced = _exhaust_campaign(
                advanced,
                reason_code=(
                    "measurement_authority_checkpoint_missing_or_invalid"
                    if measurement_checkpoint is None
                    else "campaign_measurement_retry_limit_reached"
                ),
            )
            disposition = advanced.latest_disposition
            assert disposition is not None
        elif grant_repair_continuation:
            # A newly observed authoritative counterexample on the final
            # configured cycle needs one bounded repair opportunity.  This is
            # not an open-ended budget increase: both the cycle and
            # authoritative frontiers receive exactly one auditable reserve.
            advanced = replace(
                advanced,
                status=SelfImprovementCampaignStatus.ACTIVE,
                repair_continuation_used=True,
            )
            disposition = advanced.latest_disposition
            assert disposition is not None
        elif (
            disposition.continuable
            and next_cycle >= _campaign_effective_max_cycles(advanced)
        ):
            advanced = _exhaust_campaign(
                advanced,
                reason_code=_campaign_exhaustion_reason(advanced),
            )
            disposition = advanced.latest_disposition
            assert disposition is not None
        elif (
            disposition.continuable
            and cumulative_authoritative_candidates
            >= _campaign_effective_authoritative_candidate_limit(advanced)
        ):
            advanced = _exhaust_campaign(
                advanced,
                reason_code="campaign_authoritative_frontier_exhausted",
            )
            disposition = advanced.latest_disposition
            assert disposition is not None
        report["campaign"] = {
            "campaign_id": advanced.campaign_id,
            "cycle": advanced.cycle_index,
            "max_cycles": _campaign_effective_max_cycles(advanced),
            "configured_max_cycles": advanced.max_cycles,
            "repair_continuation_used": advanced.repair_continuation_used,
            "candidate_cycle_count": _campaign_candidate_cycle_count(
                advanced
            ),
            "measurement_retry_count": advanced.measurement_retry_count,
            "measurement_continuation_count": (
                advanced.measurement_continuation_count
            ),
            "framework_blocked_count": advanced.framework_blocked_count,
            "measurement_projection": (
                measurement_outcome.projection.value
                if measurement_outcome is not None
                else None
            ),
            "measurement_execution_status": (
                measurement_outcome.execution_status.value
                if measurement_outcome is not None
                else None
            ),
            "candidate_improvement_outcome": (
                measurement_outcome.improvement_outcome.value
                if measurement_outcome is not None
                else None
            ),
            "max_measurement_retries": advanced.max_measurement_retries,
            "max_infrastructure_retries": (
                advanced.max_infrastructure_retries
            ),
            "infrastructure_retry_count": (
                advanced.infrastructure_retry_count
            ),
            "measurement_pending_run_id": (
                advanced.measurement_pending_run_id
            ),
            "measurement_pending_candidate_id": (
                advanced.measurement_pending_candidate_id
            ),
            "authoritative_candidate_count": (
                advanced.cumulative_authoritative_candidates
            ),
            "max_authoritative_candidates": (
                _campaign_effective_authoritative_candidate_limit(advanced)
            ),
            "configured_max_authoritative_candidates": (
                _campaign_authoritative_candidate_limit(advanced)
            ),
            "exhaustion_axes": list(_campaign_exhaustion_axes(advanced)),
            "checkpoint_migration": checkpoint_migration,
            "contract_stable_cycle_count": (
                advanced.contract_stable_cycle_count
            ),
        }
        report["self_improvement_disposition"] = disposition.to_dict()
        if measurement_outcome is not None:
            report["campaign_measurement_outcome"] = measurement_outcome.to_dict()
            if (
                measurement_outcome.projection
                is CampaignMeasurementProjection.SUCCEEDED
            ):
                report["status"] = "succeeded"
                summary["status"] = "succeeded"
            elif str(report.get("status") or "") == "succeeded":
                # A legacy scalar status cannot override the orthogonal v2
                # execution/improvement contract.
                report["status"] = "rejected"
                summary["status"] = "rejected"
        self.store.write_report(actual_run_id, report)
        if disposition.kind is SelfImprovementDispositionKind.HANDOFF_GOAL:
            handoff = build_goal_handoff(advanced, report)
            handoff_path = self.store.write_campaign_goal_handoff(
                advanced.campaign_id,
                handoff,
            )
            advanced = replace(advanced, goal_handoff_path=str(handoff_path))
        self.store.write_campaign(advanced)
        summary.update(_campaign_summary(advanced, summary))
        summary["self_improvement_disposition"] = disposition.to_dict()
        if checkpoint_migration is not None:
            summary["campaign_checkpoint_migration"] = checkpoint_migration
        return advanced, summary

    def run_bounded(
        self,
        campaign: SelfImprovementCampaign,
        *,
        runtime_request: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        latest: Mapping[str, Any] = {}
        current = campaign
        while current.status in {
            SelfImprovementCampaignStatus.ACTIVE,
            SelfImprovementCampaignStatus.PAUSED,
        }:
            if (
                current.status is SelfImprovementCampaignStatus.PAUSED
                and current.latest_disposition is not None
                and current.latest_disposition.kind
                not in {
                    SelfImprovementDispositionKind.CONTINUE_CANDIDATE,
                    SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
                    SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE,
                    SelfImprovementDispositionKind.COLLECT_MORE_EVIDENCE,
                    SelfImprovementDispositionKind.REPAIR_MEASUREMENT,
                    SelfImprovementDispositionKind.SWITCH_GENERATOR,
                    SelfImprovementDispositionKind.SWITCH_SCHEDULER,
                }
            ):
                break
            current, latest = self.advance_once(
                current,
                runtime_request=runtime_request,
            )
            if current.latest_disposition is None or not current.latest_disposition.continuable:
                break
        return _campaign_summary(current, latest)


def run_self_improvement_campaign(
    *,
    workspace_root: str | Path,
    request: Mapping[str, Any],
    max_improvement_cycles: int = DEFAULT_MAX_IMPROVEMENT_CYCLES,
    resume_campaign: str | None = None,
    advance_once_only: bool = False,
    run_once: RunOnce | None = None,
) -> Mapping[str, Any]:
    controller = SelfImprovementCampaignController(
        workspace_root=workspace_root,
        run_once=run_once,
    )
    if resume_campaign:
        runtime_request = {
            key: value
            for key, value in request.items()
            if key in _RUNTIME_ONLY_REQUEST_KEYS and value is not None
        }
        conflicting = sorted(
            key for key in _RESUME_CONFLICT_KEYS if request.get(key) is not None
        )
        if request.get("rerun_evaluator") is True:
            conflicting.append("rerun_evaluator")
        if conflicting:
            raise ValueError(
                "--resume-campaign cannot replace the persisted source/target contract: "
                + ", ".join(conflicting)
            )
        campaign = controller.load(resume_campaign)
        campaign = _migrate_misattributed_candidate_blocker_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_lost_repair_champion_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_repairable_no_effect_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_unattempted_task_behavior_repair_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_unattempted_websocket_http_version_repair_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_no_work_constraint_frontier_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_no_work_after_screening_control_fix_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_discarded_screening_baseline_cache_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_suppressed_task_behavior_materialization_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_source_behavior_materialization_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_fixture_source_selection_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_no_work_after_fixture_source_selection_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_regressing_measurement_checkpoint_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_paired_replay_member_timeout_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_paired_replay_timeout_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_retryable_member_measurement_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_unobserved_support_timeout_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_path_sensitive_support_identity_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_untyped_runtime_python_syntax_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_unselected_recorded_response_fixture_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_legacy_single_turn_replay_budget_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_insufficient_evidence_replay_budget_for_resume(
            controller,
            campaign,
        )
        campaign = _migrate_no_work_after_single_turn_budget_fix_for_resume(
            controller,
            campaign,
        )
        if campaign.status in {
            SelfImprovementCampaignStatus.COMPLETE,
            SelfImprovementCampaignStatus.BUDGET_LIMITED,
            SelfImprovementCampaignStatus.EXHAUSTED,
        }:
            raise ValueError(f"campaign {resume_campaign} is terminal")
        if campaign.status is SelfImprovementCampaignStatus.PAUSED:
            campaign = replace(
                campaign,
                status=SelfImprovementCampaignStatus.ACTIVE,
            )
            controller.store.write_campaign(campaign)
    else:
        prepared_request = dict(request)
        if (
            prepared_request.get("from_source") is not None
            and prepared_request.get("frozen_ingestion_id") is None
        ):
            from aworld.self_evolve.runner import (
                prepare_ingestion_from_cli_request,
            )

            snapshot = prepare_ingestion_from_cli_request(
                workspace_root=workspace_root,
                from_source=str(prepared_request["from_source"]),
                source_ingestor=str(
                    prepared_request.get("source_ingestor") or "auto"
                ),
                source_manifest=(
                    str(prepared_request["source_manifest"])
                    if prepared_request.get("source_manifest") is not None
                    else None
                ),
                semantic_evidence_approval=(
                    str(
                        prepared_request[
                            "semantic_evidence_approval"
                        ]
                    )
                    if prepared_request.get(
                        "semantic_evidence_approval"
                    )
                    is not None
                    else None
                ),
                semantic_qualification_report=(
                    str(
                        prepared_request[
                            "semantic_qualification_report"
                        ]
                    )
                    if prepared_request.get(
                        "semantic_qualification_report"
                    )
                    is not None
                    else None
                ),
                apply_policy=str(
                    prepared_request.get("apply_policy") or "auto_verified"
                ),
                ingestion_model_config=prepared_request.get(
                    "ingestion_model_config"
                ),
                ingestion_registry=prepared_request.get("ingestion_registry"),
            )
            prepared_request["frozen_ingestion_id"] = snapshot.ingestion_id
            prepared_request["semantic_evidence_approval"] = None
            prepared_request["semantic_qualification_report"] = None
        elif (
            prepared_request.get("frozen_ingestion_id") is not None
            and (
                prepared_request.get("semantic_evidence_approval")
                is not None
                or prepared_request.get(
                    "semantic_qualification_report"
                )
                is not None
            )
        ):
            from aworld.self_evolve.runner import (
                promote_ingestion_from_cli_request,
            )

            promoted = promote_ingestion_from_cli_request(
                workspace_root=workspace_root,
                frozen_ingestion_id=str(
                    prepared_request["frozen_ingestion_id"]
                ),
                semantic_evidence_approval=(
                    str(
                        prepared_request[
                            "semantic_evidence_approval"
                        ]
                    )
                    if prepared_request.get(
                        "semantic_evidence_approval"
                    )
                    is not None
                    else None
                ),
                semantic_qualification_report=(
                    str(
                        prepared_request[
                            "semantic_qualification_report"
                        ]
                    )
                    if prepared_request.get(
                        "semantic_qualification_report"
                    )
                    is not None
                    else None
                ),
                apply_policy=str(
                    prepared_request.get("apply_policy")
                    or "auto_verified"
                ),
            )
            prepared_request["frozen_ingestion_id"] = (
                promoted.ingestion_id
            )
            prepared_request["semantic_evidence_approval"] = None
            prepared_request["semantic_qualification_report"] = None
        runtime_request = dict(prepared_request)
        campaign = controller.create(
            prepared_request,
            max_cycles=max_improvement_cycles,
        )
    if advance_once_only:
        _, summary = controller.advance_once(
            campaign,
            runtime_request=runtime_request,
        )
        return summary
    return controller.run_bounded(campaign, runtime_request=runtime_request)


def _migrate_misattributed_candidate_blocker_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Reopen a terminal Campaign only when causal authority was overwritten.

    Earlier Campaign projection could exhaust a screening-rejected candidate
    as ``measurement_authority_checkpoint_missing_or_invalid``.  Re-derive the
    disposition from the immutable report under the shared causal-admission
    contract; do not reopen genuinely terminal candidate or measurement runs.
    """

    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "measurement_authority_checkpoint_missing_or_invalid"
        or not campaign.run_ids
        or campaign.cycle_index >= _campaign_effective_max_cycles(campaign)
        or campaign.cumulative_authoritative_candidates
        >= _campaign_effective_authoritative_candidate_limit(campaign)
    ):
        return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    legacy_unobserved_intervention = (
        _report_has_legacy_unobserved_intervention_screening_failure(report)
    )
    framework_screening_admission = (
        _report_has_repairable_framework_screening_admission_failure(report)
    )
    if (
        not (
            _report_has_repairable_candidate_prerequisite_failure(report)
            or legacy_unobserved_intervention
            or framework_screening_admission
        )
        or _report_has_authoritative_measurement_observation(report)
    ):
        return campaign
    if legacy_unobserved_intervention:
        _normalize_legacy_unobserved_intervention_screening_failure(report)
    previous_progress: SelfImprovementProgress | None = None
    if len(campaign.run_ids) > 1:
        try:
            previous_progress = self_improvement_progress(
                controller.store.read_report(campaign.run_ids[-2])
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return campaign
    corrected = derive_self_improvement_disposition(
        report,
        previous_progress=previous_progress,
    )
    if legacy_unobserved_intervention:
        corrected = SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
            reason_code="framework_control_selection_repaired",
            owner="framework",
            stage="candidate_screening",
            scope="shared_run",
            repairable=True,
            progress_delta_ids=corrected.progress_delta_ids,
            diagnostic_refs=corrected.diagnostic_refs,
        )
    elif framework_screening_admission:
        corrected = SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
            reason_code="framework_screening_admission_repaired",
            owner="framework",
            stage="candidate_screening",
            scope="shared_run",
            repairable=True,
            progress_delta_ids=corrected.progress_delta_ids,
            diagnostic_refs=corrected.diagnostic_refs,
        )
    elif corrected.kind is not SelfImprovementDispositionKind.CONTINUE_CANDIDATE:
        return campaign
    migration = {
        "schema_version": "aworld.self_evolve.causal_campaign_migration.v1",
        "action": (
            "restore_framework_control_selection_continuation"
            if legacy_unobserved_intervention
            else "restore_framework_screening_admission_continuation"
            if framework_screening_admission
            else "restore_candidate_repair_continuation"
        ),
        "source_run_id": latest_run_id,
        "previous_reason_code": campaign.latest_disposition.reason_code,
        "corrected_reason_code": corrected.reason_code,
    }
    report["campaign_causal_migration"] = migration
    report["self_improvement_disposition"] = corrected.to_dict()
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _report_has_repairable_framework_screening_admission_failure(
    report: Mapping[str, Any],
) -> bool:
    """Recognize a pre-authority framework failure in screening plan admission.

    This is deliberately narrower than a generic measurement failure.  No
    treatment observation or authoritative candidate may exist, and the typed
    candidate-replay gate must identify the screening checkpoint.  Therefore a
    resume starts a new framework Campaign cycle; it never requests measurement
    checkpoint reuse or consumes the candidate repair reserve.
    """

    attribution = report.get("campaign_failure_attribution")
    if not isinstance(attribution, Mapping) or not (
        attribution.get("primary_gate") == "candidate_replay"
        and attribution.get("code") == "measurement_plan_admission_failed"
        and attribution.get("failure_class") == "measurement"
        and attribution.get("failure_owner") == "framework"
        and attribution.get("failure_scope") == "shared_run"
        and attribution.get("repairable") is True
    ):
        return False
    raw_gates = report.get("gate_results")
    screening_gate = bool(
        isinstance(raw_gates, list)
        and any(
            isinstance(gate, Mapping)
            and gate.get("gate_name") == "candidate_replay"
            and gate.get("passed") is False
            and isinstance(gate.get("details"), Mapping)
            and gate["details"].get("code")
            == "measurement_plan_admission_failed"
            and gate["details"].get("checkpoint_stage") == "screening"
            and gate["details"].get("failure_owner") == "framework"
            and gate["details"].get("failure_scope") == "shared_run"
            and gate["details"].get("repairable") is True
            for gate in raw_gates
        )
    )
    if not screening_gate or _report_has_authoritative_measurement_observation(
        report
    ):
        return False
    measurement = report.get("measurement")
    if not isinstance(measurement, Mapping) or not (
        measurement.get("status") == "not_started"
        and measurement.get("validity_status") == "prerequisite_blocked"
        and measurement.get("decision_reason")
        == "measurement_plan_admission_failed"
    ):
        return False
    funnel = report.get("verification_funnel")
    if not isinstance(funnel, Mapping):
        return False
    raw_count = funnel.get("authoritative_candidate_count", 0)
    if isinstance(raw_count, bool) or not isinstance(raw_count, (int, float)):
        return False
    # Generation/screening telemetry in legacy reports may project one
    # advisory historical observation into the raw funnel.  The Campaign
    # causal counter already discounts exactly that shared framework failure.
    if raw_count > 0 and funnel.get(
        "authoritative_case_observations_advisory_only"
    ) is not True:
        return False
    return _report_authoritative_candidate_count(report) == 0


def _report_has_legacy_unobserved_intervention_screening_failure(
    report: Mapping[str, Any],
) -> bool:
    """Recognize reports written before intervention-aware panel selection."""

    population = report.get("population")
    screening = (
        population.get("screening")
        if isinstance(population, Mapping)
        else None
    )
    attempts = (
        screening.get("attempts")
        if isinstance(screening, Mapping)
        else None
    )
    if not isinstance(attempts, list) or not any(
        isinstance(attempt, Mapping)
        and isinstance(attempt.get("details"), Mapping)
        and attempt["details"].get("code")
        == "candidate_intervention_unobserved"
        for attempt in attempts
    ):
        return False
    raw_gates = report.get("gate_results")
    return bool(
        isinstance(raw_gates, list)
        and any(
            isinstance(gate, Mapping)
            and gate.get("gate_name") == "candidate_replay"
            and gate.get("passed") is not True
            and isinstance(gate.get("details"), Mapping)
            and gate["details"].get("code")
            == "candidate_intervention_unobserved"
            for gate in raw_gates
        )
    )


def _normalize_legacy_unobserved_intervention_screening_failure(
    report: dict[str, Any],
) -> None:
    """Upgrade the old terminal screening blocker to the typed prerequisite."""

    raw_gates = report.get("gate_results")
    if isinstance(raw_gates, list):
        for gate in raw_gates:
            if (
                not isinstance(gate, dict)
                or gate.get("gate_name") != "candidate_replay"
                or not isinstance(gate.get("details"), dict)
                or gate["details"].get("code")
                != "candidate_intervention_unobserved"
            ):
                continue
            gate["details"].update(
                {
                    "repairable": True,
                    "checkpoint_stage": "screening",
                    "evaluator_skipped": True,
                    "next_action": "repair_framework_control_selection",
                }
            )
    attribution = report.get("campaign_failure_attribution")
    if (
        isinstance(attribution, dict)
        and attribution.get("code") == "candidate_intervention_unobserved"
    ):
        attribution["repairable"] = True


def _migrate_lost_repair_champion_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Restore one bounded cycle when a deeper repair champion was abandoned."""

    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "candidate_repair_frontier_stalled"
        or len(campaign.run_ids) < 2
        or campaign.repair_continuation_used
    ):
        return campaign
    reports: dict[str, Mapping[str, Any]] = {}
    for run_id in campaign.run_ids:
        try:
            report = controller.store.read_report(run_id)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return campaign
        if isinstance(report.get("repair_focus_candidate_id"), str):
            reports[run_id] = report
    if len(reports) < 2:
        return campaign
    positions = {run_id: index for index, run_id in enumerate(campaign.run_ids)}
    champion_run_id = max(
        reports,
        key=lambda run_id: (
            self_improvement_progress(reports[run_id]).deepest_stage_rank,
            positions[run_id],
        ),
    )
    latest_run_id = campaign.run_ids[-1]
    if champion_run_id == latest_run_id:
        return campaign
    champion_report = reports[champion_run_id]
    latest_report = reports.get(latest_run_id)
    if latest_report is None or (
        self_improvement_progress(champion_report).deepest_stage_rank
        <= self_improvement_progress(latest_report).deepest_stage_rank
    ):
        return campaign
    focus_candidate_id = champion_report.get("repair_focus_candidate_id")
    if not isinstance(focus_candidate_id, str) or not (
        controller.store.run_path(champion_run_id)
        / "candidates"
        / f"{focus_candidate_id}.json"
    ).is_file():
        return campaign
    champion_index = positions[champion_run_id]
    previous_progress = (
        self_improvement_progress(
            controller.store.read_report(campaign.run_ids[champion_index - 1])
        )
        if champion_index > 0
        else None
    )
    corrected = derive_self_improvement_disposition(
        champion_report,
        previous_progress=previous_progress,
    )
    if corrected.kind is not SelfImprovementDispositionKind.CONTINUE_CANDIDATE:
        return campaign
    migration = {
        "schema_version": "aworld.self_evolve.repair_champion_migration.v1",
        "action": "restore_deepest_repair_champion",
        "source_run_id": latest_run_id,
        "champion_run_id": champion_run_id,
        "champion_candidate_id": focus_candidate_id,
        "previous_reason_code": campaign.latest_disposition.reason_code,
        "corrected_reason_code": corrected.reason_code,
    }
    latest_report["campaign_causal_migration"] = migration
    controller.store.write_report(latest_run_id, latest_report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        repair_continuation_used=True,
        latest_progress=self_improvement_progress(champion_report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        latest_report_path=str(
            controller.store.run_path(champion_run_id) / "report.json"
        ),
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_repairable_no_effect_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Restore one bounded repair for legacy terminal neutral measurements.

    Reports written before candidate release failures were projected into the
    typed measurement outcome can say ``no_effect`` and terminally pause even
    though the same immutable report contains a new candidate-owned repair
    constraint.  Reopen only that typed case and consume the existing one-shot
    repair reserve when either Campaign frontier is already full.
    """

    if (
        campaign.status
        not in {
            SelfImprovementCampaignStatus.PAUSED,
            SelfImprovementCampaignStatus.EXHAUSTED,
        }
        or not campaign.run_ids
    ):
        return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
        outcome = campaign_measurement_outcome_from_report(report)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    if (
        outcome is None
        or outcome.execution_status is not MeasurementExecutionStatus.COMPLETED
        or outcome.improvement_outcome is not CandidateImprovementOutcome.NO_EFFECT
        or outcome.continuation_available
        or not any(
            event.get("owner") == "candidate"
            and event.get("repairable") is True
            for event in _typed_failure_events(report)
        )
    ):
        return campaign

    needs_reserve = bool(
        campaign.cycle_index >= _campaign_effective_max_cycles(campaign)
        or campaign.cumulative_authoritative_candidates
        >= _campaign_effective_authoritative_candidate_limit(campaign)
    )
    prior_reports: list[Mapping[str, Any]] = []
    try:
        prior_reports = [
            controller.store.read_report(run_id)
            for run_id in campaign.run_ids[:-1]
        ]
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    if needs_reserve and (
        campaign.repair_continuation_used
        or not _report_has_new_candidate_repair_evidence(
            report,
            prior_reports=prior_reports,
        )
    ):
        return campaign

    corrected_outcome = CampaignMeasurementOutcomeV2(
        execution_status=outcome.execution_status,
        improvement_outcome=outcome.improvement_outcome,
        release_gates_passed=False,
        continuation_available=True,
        reason_code="no_effect_candidate_repair_available",
    )
    corrected = _measurement_outcome_disposition(
        corrected_outcome,
        progress_delta_ids=(
            campaign.latest_disposition.progress_delta_ids
            if campaign.latest_disposition is not None
            else ()
        ),
    )
    migration = {
        "schema_version": "aworld.self_evolve.neutral_repair_migration.v1",
        "action": "restore_repairable_neutral_candidate",
        "source_run_id": latest_run_id,
        "previous_reason_code": outcome.reason_code,
        "corrected_reason_code": corrected_outcome.reason_code,
        "repair_reserve_consumed": needs_reserve,
    }
    report["campaign_causal_migration"] = migration
    report["campaign_measurement_outcome"] = corrected_outcome.to_dict()
    report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["repair_continuation_used"] = bool(
            campaign.repair_continuation_used or needs_reserve
        )
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + campaign.measurement_ledger.control_plane_run_count
            + int(campaign.repair_continuation_used or needs_reserve)
        )
        raw_campaign["max_authoritative_candidates"] = (
            _campaign_authoritative_candidate_limit(campaign)
            + int(campaign.repair_continuation_used or needs_reserve)
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        repair_continuation_used=(
            campaign.repair_continuation_used or needs_reserve
        ),
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected,
        latest_measurement_outcome=corrected_outcome,
        latest_report_path=str(
            controller.store.run_path(latest_run_id) / "report.json"
        ),
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_unattempted_task_behavior_repair_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Reopen a stalled frontier after task-behavior routing is introduced."""

    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "candidate_repair_frontier_stalled"
        or not campaign.run_ids
        or campaign.cycle_index >= _campaign_effective_max_cycles(campaign)
        or campaign.cumulative_authoritative_candidates
        >= _campaign_effective_authoritative_candidate_limit(campaign)
    ):
        return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    migration = report.get("campaign_causal_migration")
    if (
        isinstance(migration, Mapping)
        and migration.get("action")
        == "restore_unattempted_task_behavior_repair"
    ):
        return campaign
    task_event = next(
        (
            event
            for event in _typed_failure_events(report)
            if event.get("owner") == "candidate"
            and event.get("repairable") is True
            and event.get("stage") == "task_rollout"
            and event.get("code")
            in {
                "candidate_recovery_incomplete",
                "target_behavior_completion_missing",
            }
        ),
        None,
    )
    if task_event is None:
        return campaign
    semantic_key = task_event.get("semantic_key")
    repair_state = report.get("repair_frontier_state")
    scheduler_state = (
        repair_state.get("scheduler_state")
        if isinstance(repair_state, Mapping)
        else None
    )
    family_map = (
        scheduler_state.get("frontier_mutation_families")
        if isinstance(scheduler_state, Mapping)
        else None
    )
    attempted_families = (
        family_map.get(semantic_key, ())
        if isinstance(family_map, Mapping)
        and isinstance(semantic_key, str)
        else ()
    )
    if (
        isinstance(attempted_families, (list, tuple))
        and "target_behavior_composition" in attempted_families
    ):
        return campaign

    corrected = _event_disposition(
        SelfImprovementDispositionKind.CONTINUE_CANDIDATE,
        "candidate_task_behavior_repair_available",
        task_event,
        (),
    )
    report["campaign_causal_migration"] = {
        "schema_version": "aworld.self_evolve.task_behavior_repair_migration.v1",
        "action": "restore_unattempted_task_behavior_repair",
        "source_run_id": latest_run_id,
        "frontier_semantic_key": semantic_key,
        "required_mutation_family": "target_behavior_composition",
    }
    report["self_improvement_disposition"] = corrected.to_dict()
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected,
        latest_report_path=str(
            controller.store.run_path(latest_run_id) / "report.json"
        ),
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_unattempted_websocket_http_version_repair_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Restore one bounded repair after a legacy WebSocket diagnostic collapse.

    Older reports classified an invalid HTTP/1.0 WebSocket upgrade as a generic
    protocol-trace failure. Reopen only a candidate-owned, pre-authority
    conformance frontier whose bounded diagnostic proves that exact failure,
    then persist the new typed producer constraint before generation resumes.
    """

    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "candidate_repair_frontier_stalled"
        or campaign.repair_continuation_used
        or not campaign.run_ids
        or campaign.cumulative_authoritative_candidates
        >= _campaign_effective_authoritative_candidate_limit(campaign)
    ):
        return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    migration = report.get("campaign_causal_migration")
    if (
        isinstance(migration, Mapping)
        and migration.get("action")
        == "restore_unattempted_websocket_http_version_repair"
    ):
        return campaign
    focus_candidate_id = report.get("repair_focus_candidate_id")
    if not isinstance(focus_candidate_id, str) or not (
        controller.store.run_path(latest_run_id)
        / "candidates"
        / f"{focus_candidate_id}.json"
    ).is_file():
        return campaign

    matching_details: list[dict[str, Any]] = []
    raw_gates = report.get("gate_results")
    if isinstance(raw_gates, list):
        for gate in raw_gates:
            if not (
                isinstance(gate, dict)
                and gate.get("gate_name") == "candidate_repair_conformance"
                and gate.get("passed") is False
                and isinstance(gate.get("details"), dict)
                and gate["details"].get("code")
                == "repair_probe_execution_failed"
                and gate["details"].get("failure_class") == "candidate"
                and gate["details"].get("repairable") is True
            ):
                continue
            diagnostics = gate["details"].get("diagnostics")
            if not isinstance(diagnostics, list):
                continue
            if any(
                isinstance(item, Mapping)
                and item.get("code")
                == "websocket_handshake_http_version_invalid"
                for item in diagnostics
            ):
                # The typed constraint was already attempted; this migration is
                # reserved for reports written before that constraint existed.
                return campaign
            if any(
                isinstance(item, Mapping)
                and item.get("code") == "protocol_trace_contract_failed"
                and str(item.get("reason") or "").startswith(
                    "advertised WebSocket handshake requires HTTP/1.1"
                )
                for item in diagnostics
            ):
                matching_details.append(gate["details"])
    if not matching_details or _report_has_authoritative_measurement_observation(
        report
    ):
        return campaign
    funnel = report.get("verification_funnel")
    if not isinstance(funnel, Mapping) or _report_authoritative_candidate_count(
        report
    ) != 0:
        return campaign

    constraint_object = websocket_handshake_http_version_constraint()
    constraint = constraint_object.to_dict()
    for details in matching_details:
        details["schema_field_constraints"] = _merge_constraint_dicts(
            details.get("schema_field_constraints"),
            constraint,
        )
        diagnostics = details.get("diagnostics")
        if isinstance(diagnostics, list):
            for item in diagnostics:
                if not (
                    isinstance(item, dict)
                    and item.get("code") == "protocol_trace_contract_failed"
                    and str(item.get("reason") or "").startswith(
                        "advertised WebSocket handshake requires HTTP/1.1"
                    )
                ):
                    continue
                item["code"] = "websocket_handshake_http_version_invalid"
                item["schema_field_constraints"] = _merge_constraint_dicts(
                    item.get("schema_field_constraints"),
                    constraint,
                )
        repair_conformance = details.get("repair_conformance")
        if isinstance(repair_conformance, dict):
            repair_conformance["schema_field_constraints"] = (
                _merge_constraint_dicts(
                    repair_conformance.get("schema_field_constraints"),
                    constraint,
                )
            )
            raw_failure_codes = repair_conformance.get("failure_codes")
            failure_codes = (
                [
                    str(item)
                    for item in raw_failure_codes
                    if isinstance(item, str)
                ]
                if isinstance(raw_failure_codes, list)
                else []
            )
            repair_conformance["failure_codes"] = list(
                dict.fromkeys(
                    [
                        *failure_codes,
                        "websocket_handshake_http_version_invalid",
                    ]
                )
            )

    corrected_progress = self_improvement_progress(report)
    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CANDIDATE,
        reason_code="candidate_websocket_http_version_repair_available",
        owner="candidate",
        stage="capability_preflight",
        scope="candidate",
        repairable=True,
        progress_delta_ids=corrected_progress.delta_from(
            campaign.latest_progress
        ),
    )
    report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.websocket_http_version_repair_migration.v1"
        ),
        "action": "restore_unattempted_websocket_http_version_repair",
        "source_run_id": latest_run_id,
        "constraint_identity_digest": constraint_object.identity_digest,
        "repair_reserve_consumed": True,
    }
    report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["repair_continuation_used"] = True
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + campaign.measurement_ledger.control_plane_run_count
            + 1
        )
        raw_campaign["max_authoritative_candidates"] = (
            _campaign_authoritative_candidate_limit(campaign) + 1
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        repair_continuation_used=True,
        latest_progress=corrected_progress,
        latest_disposition=corrected,
        latest_report_path=str(
            controller.store.run_path(latest_run_id) / "report.json"
        ),
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _merge_constraint_dicts(
    raw_constraints: Any,
    constraint: Mapping[str, Any],
) -> list[dict[str, Any]]:
    constraints = (
        [dict(item) for item in raw_constraints if isinstance(item, Mapping)]
        if isinstance(raw_constraints, list)
        else []
    )
    identity = json.dumps(
        constraint,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if all(
        json.dumps(
            item,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        != identity
        for item in constraints
    ):
        constraints.append(dict(constraint))
    return constraints


def _migrate_no_work_constraint_frontier_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Recover a migrated repair contract that scheduler state kept dormant.

    The candidate repair reserve is still the sole candidate-work allowance.
    A cycle that scheduled zero optimizer iterations is charged as one
    framework control-plane run, and only the semantic frontier owning the new
    typed constraint is reactivated.
    """

    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or not campaign.repair_continuation_used
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "candidate_repair_frontier_stalled"
        or len(campaign.run_ids) < 2
    ):
        return campaign
    no_work_run_id = campaign.run_ids[-1]
    if no_work_run_id in campaign.measurement_ledger.framework_blocked_run_ids:
        return campaign
    try:
        no_work_report = controller.store.read_report(no_work_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    source_run_id: str | None = None
    source_report: Mapping[str, Any] | None = None
    prior_reactivation_run_id: str | None = None
    prior_reactivation_report: Mapping[str, Any] | None = None
    checkpoint_lineage_repair_already_applied = False
    for prior_run_id in reversed(campaign.run_ids[:-1]):
        try:
            prior_report = controller.store.read_report(prior_run_id)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        migration = prior_report.get("campaign_causal_migration")
        action = (
            migration.get("action")
            if isinstance(migration, Mapping)
            else None
        )
        if action == (
            "reactivate_migrated_constraint_after_checkpoint_lineage_regression"
        ):
            checkpoint_lineage_repair_already_applied = True
        elif (
            prior_reactivation_report is None
            and action == "reactivate_migrated_constraint_after_no_work_cycle"
        ):
            prior_reactivation_run_id = prior_run_id
            prior_reactivation_report = prior_report
        elif (
            source_report is None
            and action == "restore_unattempted_websocket_http_version_repair"
        ):
            source_run_id = prior_run_id
            source_report = prior_report
    if source_run_id is None or source_report is None:
        return campaign
    raw_gates = no_work_report.get("gate_results")
    no_work_gate = bool(
        isinstance(raw_gates, list)
        and len(raw_gates) == 1
        and isinstance(raw_gates[0], Mapping)
        and raw_gates[0].get("gate_name") == "candidate_generation"
        and raw_gates[0].get("passed") is False
        and isinstance(raw_gates[0].get("details"), Mapping)
        and raw_gates[0]["details"].get("generated_candidate_count") == 0
        and raw_gates[0]["details"].get("iterations") == 0
    )
    if not no_work_gate or _report_authoritative_candidate_count(
        no_work_report
    ) != 0:
        return campaign
    frontier_keys = _websocket_http_version_frontier_keys(source_report)
    if not frontier_keys:
        return campaign
    repair_state = no_work_report.get("repair_frontier_state")
    scheduler_state = (
        repair_state.get("scheduler_state")
        if isinstance(repair_state, dict)
        else None
    )
    if not isinstance(scheduler_state, dict):
        return campaign
    raw_progress = scheduler_state.get("frontier_progress")
    raw_stalls = scheduler_state.get("frontier_stalls")
    raw_families = scheduler_state.get("frontier_mutation_families")
    if not all(
        isinstance(value, dict)
        for value in (raw_progress, raw_stalls, raw_families)
    ):
        return campaign
    eligible_keys = tuple(
        key
        for key in frontier_keys
        if key in raw_progress and key in raw_stalls
    )
    if not eligible_keys:
        return campaign
    checkpoint_lineage_regressed = prior_reactivation_report is not None
    if checkpoint_lineage_regressed:
        if (
            checkpoint_lineage_repair_already_applied
            or prior_reactivation_run_id
            not in campaign.measurement_ledger.framework_blocked_run_ids
        ):
            return campaign
        prior_repair_state = prior_reactivation_report.get(
            "repair_frontier_state"
        )
        prior_scheduler_state = (
            prior_repair_state.get("scheduler_state")
            if isinstance(prior_repair_state, Mapping)
            else None
        )
        prior_stalls = (
            prior_scheduler_state.get("frontier_stalls")
            if isinstance(prior_scheduler_state, Mapping)
            else None
        )
        prior_families = (
            prior_scheduler_state.get("frontier_mutation_families")
            if isinstance(prior_scheduler_state, Mapping)
            else None
        )
        prior_reset_is_authoritative = bool(
            isinstance(prior_stalls, Mapping)
            and isinstance(prior_families, Mapping)
            and prior_scheduler_state.get("last_focused_frontier") is None
            and all(prior_stalls.get(key) == 0 for key in eligible_keys)
            and all(prior_families.get(key) == [] for key in eligible_keys)
        )
        current_state_reverted = bool(
            any(raw_stalls.get(key) != 0 for key in eligible_keys)
            or any(raw_families.get(key) != [] for key in eligible_keys)
            or scheduler_state.get("last_focused_frontier") is not None
        )
        if not prior_reset_is_authoritative or not current_state_reverted:
            return campaign
    for key in eligible_keys:
        raw_stalls[key] = 0
        raw_families[key] = []
    scheduler_state["last_focused_frontier"] = None
    records = repair_state.get("records")
    if isinstance(records, list):
        for record in records:
            if not (
                isinstance(record, dict)
                and record.get("semantic_key") in eligible_keys
            ):
                continue
            record["status"] = "active"
            record["mutation_families"] = []
        repair_state["active_count"] = sum(
            isinstance(record, Mapping) and record.get("status") == "active"
            for record in records
        )
        repair_state["dormant_count"] = sum(
            isinstance(record, Mapping) and record.get("status") == "dormant"
            for record in records
        )
        repair_state["resolved_count"] = sum(
            isinstance(record, Mapping) and record.get("status") == "resolved"
            for record in records
        )
        repair_state["regressed_count"] = sum(
            isinstance(record, Mapping) and record.get("status") == "regressed"
            for record in records
        )

    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
        reason_code=(
            "framework_scheduler_checkpoint_lineage_repaired"
            if checkpoint_lineage_regressed
            else "framework_migrated_constraint_frontier_reactivated"
        ),
        owner="framework",
        stage="candidate_generation",
        scope="shared_run",
        repairable=True,
        progress_delta_ids=(
            campaign.latest_disposition.progress_delta_ids
            if campaign.latest_disposition is not None
            else ()
        ),
    )
    no_work_report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.scheduler_checkpoint_lineage_migration.v1"
            if checkpoint_lineage_regressed
            else "aworld.self_evolve.no_work_frontier_migration.v1"
        ),
        "action": (
            "reactivate_migrated_constraint_after_checkpoint_lineage_regression"
            if checkpoint_lineage_regressed
            else "reactivate_migrated_constraint_after_no_work_cycle"
        ),
        "source_run_id": source_run_id,
        "no_work_run_id": no_work_run_id,
        "superseded_checkpoint_run_id": (
            prior_reactivation_run_id
            if checkpoint_lineage_regressed
            else None
        ),
        "reactivated_frontier_keys": list(eligible_keys),
        "candidate_generation_iterations": 0,
        "candidate_generation_count": 0,
    }
    no_work_report["self_improvement_disposition"] = corrected.to_dict()
    ledger = campaign.measurement_ledger.charge_framework_blocked(
        no_work_run_id
    )
    raw_campaign = no_work_report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["framework_blocked_count"] = ledger.framework_blocked_count
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + ledger.control_plane_run_count
            + int(campaign.repair_continuation_used)
        )
    controller.store.write_report(no_work_run_id, no_work_report)
    source_progress = self_improvement_progress(source_report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        measurement_ledger=ledger,
        latest_progress=source_progress,
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        latest_report_path=str(
            controller.store.run_path(no_work_run_id) / "report.json"
        ),
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _websocket_http_version_frontier_keys(
    report: Mapping[str, Any],
) -> tuple[str, ...]:
    keys: list[str] = []
    raw_gates = report.get("gate_results")
    if not isinstance(raw_gates, list):
        return ()
    for gate in raw_gates:
        details = gate.get("details") if isinstance(gate, Mapping) else None
        if not (
            gate.get("gate_name") == "candidate_repair_conformance"
            and gate.get("passed") is False
            and isinstance(details, Mapping)
        ):
            continue
        diagnostics = details.get("diagnostics")
        if not (
            isinstance(diagnostics, list)
            and any(
                isinstance(item, Mapping)
                and item.get("code")
                == "websocket_handshake_http_version_invalid"
                for item in diagnostics
            )
        ):
            continue
        raw_events = details.get("causal_failure_events")
        if not isinstance(raw_events, list):
            continue
        for event in raw_events:
            if not (
                isinstance(event, Mapping)
                and event.get("code") == "protocol_trace_contract_failed"
                and event.get("owner") == "candidate"
                and event.get("scope") == "candidate"
                and event.get("stage") == "capability_preflight"
                and event.get("repairable") is True
                and isinstance(event.get("semantic_key"), str)
            ):
                continue
            keys.append(str(event["semantic_key"]))
    return tuple(dict.fromkeys(keys))


def _migrate_no_work_after_screening_control_fix_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Recover zero-work scheduler state after a framework screening blocker."""

    migration_action = "restore_exploration_after_screening_control_no_work"
    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "candidate_repair_frontier_stalled"
        or len(campaign.run_ids) < 2
    ):
        return campaign
    no_work_run_id = campaign.run_ids[-1]
    source_run_id: str | None = None
    if no_work_run_id in campaign.measurement_ledger.framework_blocked_run_ids:
        return campaign
    try:
        no_work_report = controller.store.read_report(no_work_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    prior_migration = no_work_report.get("campaign_causal_migration")
    if (
        isinstance(prior_migration, Mapping)
        and prior_migration.get("action") == migration_action
    ):
        return campaign
    raw_gates = no_work_report.get("gate_results")
    no_work_gate = bool(
        isinstance(raw_gates, list)
        and len(raw_gates) == 1
        and isinstance(raw_gates[0], Mapping)
        and raw_gates[0].get("gate_name") == "candidate_generation"
        and raw_gates[0].get("passed") is False
        and isinstance(raw_gates[0].get("details"), Mapping)
        and raw_gates[0]["details"].get("generated_candidate_count") == 0
        and raw_gates[0]["details"].get("iterations") == 0
        and _report_authoritative_candidate_count(no_work_report) == 0
    )
    source_report: Mapping[str, Any] | None = None
    for prior_run_id in reversed(campaign.run_ids[:-1]):
        try:
            prior_report = controller.store.read_report(prior_run_id)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        source_attribution = prior_report.get("campaign_failure_attribution")
        if not (
            isinstance(source_attribution, Mapping)
            and source_attribution.get("primary_gate") == "candidate_replay"
            and source_attribution.get("code") == "screening_control_infeasible"
            and source_attribution.get("failure_owner") == "framework"
            and source_attribution.get("failure_scope") == "shared_run"
            and source_attribution.get("repairable") is True
            and _report_authoritative_candidate_count(prior_report) == 0
        ):
            continue
        source_run_id = prior_run_id
        source_report = prior_report
        break
    if not no_work_gate or source_run_id is None or source_report is None:
        return campaign
    repair_state = no_work_report.get("repair_frontier_state")
    scheduler_state = (
        repair_state.get("scheduler_state")
        if isinstance(repair_state, dict)
        else None
    )
    if not (
        isinstance(repair_state, dict)
        and isinstance(scheduler_state, dict)
        and repair_state.get("active_count") == 0
        and int(repair_state.get("dormant_count") or 0) > 0
    ):
        return campaign
    frontier_keys = _report_generation_frontier_keys(source_report)
    raw_stalls = scheduler_state.get("frontier_stalls")
    raw_families = scheduler_state.get("frontier_mutation_families")
    if not (
        frontier_keys
        and isinstance(raw_stalls, dict)
        and isinstance(raw_families, dict)
    ):
        return campaign
    reactivated_keys = tuple(
        key for key in frontier_keys if key in raw_stalls and key in raw_families
    )
    if not reactivated_keys:
        return campaign
    for key in reactivated_keys:
        raw_stalls[key] = 0
        raw_families[key] = []
    scheduler_state["initial_exploration_scheduled"] = False
    scheduler_state["untyped_frontier_exploration_scheduled"] = False
    scheduler_state["last_focused_frontier"] = None
    records = repair_state.get("records")
    if isinstance(records, list):
        for record in records:
            if (
                isinstance(record, dict)
                and record.get("semantic_key") in reactivated_keys
            ):
                record["status"] = "active"
                record["mutation_families"] = []
        repair_state["active_count"] = sum(
            isinstance(record, Mapping) and record.get("status") == "active"
            for record in records
        )
        repair_state["dormant_count"] = sum(
            isinstance(record, Mapping) and record.get("status") == "dormant"
            for record in records
        )

    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
        reason_code="framework_screening_scheduler_checkpoint_repaired",
        owner="framework",
        stage="candidate_generation",
        scope="shared_run",
        repairable=True,
        progress_delta_ids=campaign.latest_disposition.progress_delta_ids,
    )
    ledger = campaign.measurement_ledger.charge_framework_blocked(
        no_work_run_id
    )
    no_work_report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.screening_no_work_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": source_run_id,
        "no_work_run_id": no_work_run_id,
        "candidate_generation_iterations": 0,
        "candidate_generation_count": 0,
        "reactivated_frontier_keys": list(reactivated_keys),
        "candidate_reserve_granted": False,
        "measurement_retry_granted": False,
    }
    no_work_report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = no_work_report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["framework_blocked_count"] = ledger.framework_blocked_count
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + ledger.control_plane_run_count
            + int(campaign.repair_continuation_used)
        )
    controller.store.write_report(no_work_run_id, no_work_report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        measurement_ledger=ledger,
        latest_progress=self_improvement_progress(source_report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        latest_report_path=str(
            controller.store.run_path(no_work_run_id) / "report.json"
        ),
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _report_generation_frontier_keys(
    report: Mapping[str, Any],
) -> tuple[str, ...]:
    diagnostics = report.get("optimizer_diagnostics")
    stack: list[object] = [diagnostics]
    keys: list[str] = []
    inspected = 0
    while stack and inspected < 2_000:
        current = stack.pop()
        inspected += 1
        if isinstance(current, Mapping):
            key = current.get("active_frontier_key")
            if isinstance(key, str) and key:
                keys.append(key)
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    if keys:
        return tuple(dict.fromkeys(keys))
    repair_state = report.get("repair_frontier_state")
    scheduler_state = (
        repair_state.get("scheduler_state")
        if isinstance(repair_state, Mapping)
        else None
    )
    focused = (
        scheduler_state.get("last_focused_frontier")
        if isinstance(scheduler_state, Mapping)
        else None
    )
    return (focused,) if isinstance(focused, str) and focused else ()


def _migrate_discarded_screening_baseline_cache_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Reopen one run whose qualified control cache was silently discarded.

    The legacy screening loop reported a cache at candidate admission, then
    unconditionally cleared it before the first control request.  Reopen only
    when the immutable report proves a successful baseline for a case followed
    by an uncached invalid baseline on that same case.  The failed cycle is
    charged to the framework ledger and no candidate or measurement reserve is
    granted.
    """

    migration_action = "restore_discarded_screening_baseline_cache"
    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "campaign_infrastructure_retry_limit_reached"
        or not campaign.run_ids
        or campaign.cumulative_authoritative_candidates
        >= _campaign_effective_authoritative_candidate_limit(campaign)
    ):
        return campaign
    for run_id in campaign.run_ids:
        try:
            prior_report = controller.store.read_report(run_id)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        migration = prior_report.get("campaign_causal_migration")
        if (
            isinstance(migration, Mapping)
            and migration.get("action") == migration_action
        ):
            return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    raw_gates = report.get("gate_results")
    typed_failure = bool(
        isinstance(raw_gates, list)
        and any(
            isinstance(gate, Mapping)
            and gate.get("gate_name") == "candidate_replay"
            and gate.get("passed") is False
            and isinstance(gate.get("details"), Mapping)
            and gate["details"].get("code") == "screening_control_infeasible"
            and gate["details"].get("checkpoint_stage") == "screening"
            and gate["details"].get("failure_class") == "framework"
            and gate["details"].get("failure_owner") == "framework"
            and gate["details"].get("failure_scope") == "shared_run"
            and gate["details"].get("repairable") is True
            for gate in raw_gates
        )
    )
    measurement = report.get("measurement")
    if not typed_failure or not (
        isinstance(measurement, Mapping)
        and measurement.get("status") == "not_started"
        and measurement.get("decision_reason") == "screening_control_infeasible"
    ):
        return campaign
    population = report.get("population")
    screening = (
        population.get("screening")
        if isinstance(population, Mapping)
        else None
    )
    attempts = (
        screening.get("attempts")
        if isinstance(screening, Mapping)
        else None
    )
    if not isinstance(attempts, list):
        return campaign
    successful_uncached_controls: set[str] = set()
    later_uncached_invalid_controls: set[str] = set()
    for attempt in attempts:
        control_attempts = (
            attempt.get("control_case_attempts")
            if isinstance(attempt, Mapping)
            else None
        )
        if not isinstance(control_attempts, list):
            continue
        for control in control_attempts:
            if not isinstance(control, Mapping):
                continue
            raw_case_ids = control.get("case_ids")
            case_ids = tuple(
                str(case_id)
                for case_id in (
                    raw_case_ids
                    if isinstance(raw_case_ids, (list, tuple))
                    else ()
                )
                if isinstance(case_id, str) and case_id
            )
            if control.get("baseline_cache_offered") is not False:
                continue
            if control.get("baseline_status") == "succeeded":
                successful_uncached_controls.update(case_ids)
            elif (
                control.get("invalid_control") is True
                and control.get("baseline_status") == "failed"
            ):
                later_uncached_invalid_controls.update(case_ids)
    affected_case_ids = tuple(
        sorted(successful_uncached_controls & later_uncached_invalid_controls)
    )
    if not affected_case_ids:
        return campaign

    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
        reason_code="framework_screening_baseline_reuse_repaired",
        owner="framework",
        stage="candidate_screening",
        scope="shared_run",
        repairable=True,
        progress_delta_ids=(
            campaign.latest_disposition.progress_delta_ids
            if campaign.latest_disposition is not None
            else ()
        ),
    )
    ledger = campaign.measurement_ledger.charge_framework_blocked(latest_run_id)
    report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.screening_baseline_cache_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": latest_run_id,
        "affected_case_ids": list(affected_case_ids),
        "candidate_reserve_granted": False,
        "measurement_retry_granted": False,
    }
    report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["framework_blocked_count"] = ledger.framework_blocked_count
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + ledger.control_plane_run_count
            + int(campaign.repair_continuation_used)
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        measurement_ledger=ledger,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        latest_report_path=str(
            controller.store.run_path(latest_run_id) / "report.json"
        ),
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_suppressed_task_behavior_materialization_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Recover one frontier whose SKILL.md delta was overwritten by framework.

    Legacy materialization inherited parent content for every source contract,
    including task-rollout contracts that explicitly named SKILL.md as a
    producer. Require repeated typed materialization failures and an otherwise
    unmeasured cycle before charging one framework control-plane continuation.
    """

    migration_action = "restore_suppressed_task_behavior_materialization"
    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "candidate_repair_frontier_stalled"
        or not campaign.run_ids
        or campaign.cumulative_authoritative_candidates
        >= _campaign_effective_authoritative_candidate_limit(campaign)
    ):
        return campaign
    for run_id in campaign.run_ids:
        try:
            prior_report = controller.store.read_report(run_id)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        migration = prior_report.get("campaign_causal_migration")
        if (
            isinstance(migration, Mapping)
            and migration.get("action") == migration_action
        ):
            return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    funnel = report.get("verification_funnel")
    if not (
        isinstance(funnel, Mapping)
        and funnel.get("generation_materialization_frontier_exhausted") is True
        and funnel.get("generation_stop_reason")
        == "materialization_frontier_repeated"
        and _report_authoritative_candidate_count(report) == 0
    ):
        return campaign
    raw_gates = report.get("gate_results")
    candidate_frontier = bool(
        isinstance(raw_gates, list)
        and any(
            isinstance(gate, Mapping)
            and gate.get("gate_name") == "candidate_replay"
            and gate.get("passed") is False
            and isinstance(gate.get("details"), Mapping)
            and gate["details"].get("code")
            == "candidate_screening_deadline_exceeded"
            and gate["details"].get("failure_owner") == "candidate"
            and gate["details"].get("failure_scope") == "candidate"
            and gate["details"].get("repairable") is True
            for gate in raw_gates
        )
    )
    optimizer = report.get("optimizer_diagnostics")
    raw_iterations = (
        optimizer.get("iterations")
        if isinstance(optimizer, Mapping)
        else None
    )
    if not candidate_frontier or not isinstance(raw_iterations, list):
        return campaign
    suppressed_count = 0
    contract_identity_digests: set[str] = set()
    for iteration in raw_iterations:
        diagnostics = (
            iteration.get("diagnostics")
            if isinstance(iteration, Mapping)
            else None
        )
        failures = (
            diagnostics.get("candidate_materialization_failures")
            if isinstance(diagnostics, Mapping)
            else None
        )
        if not isinstance(failures, list):
            continue
        for failure in failures:
            if not isinstance(failure, Mapping) or not (
                failure.get("code") == "repair_target_behavior_unchanged"
                and failure.get("stage") == "candidate_semantic_validation"
                and failure.get("representation") == "candidate_package"
                and failure.get("repairable") is True
            ):
                continue
            details = failure.get("details")
            conformance = (
                details.get("repair_conformance")
                if isinstance(details, Mapping)
                else None
            )
            reason = (
                str(conformance.get("reason") or "")
                if isinstance(conformance, Mapping)
                else ""
            )
            if "task-rollout repair must materially change SKILL.md" not in reason:
                continue
            suppressed_count += 1
            digest = failure.get("contract_identity_digest")
            if isinstance(digest, str) and digest:
                contract_identity_digests.add(digest)
    if suppressed_count < 2:
        return campaign

    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
        reason_code="framework_task_behavior_materialization_repaired",
        owner="framework",
        stage="candidate_generation",
        scope="shared_run",
        repairable=True,
        progress_delta_ids=(
            campaign.latest_disposition.progress_delta_ids
            if campaign.latest_disposition is not None
            else ()
        ),
    )
    ledger = campaign.measurement_ledger.charge_framework_blocked(latest_run_id)
    report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.task_behavior_materialization_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": latest_run_id,
        "suppressed_candidate_count": suppressed_count,
        "contract_identity_digests": sorted(contract_identity_digests),
        "candidate_reserve_granted": False,
        "measurement_retry_granted": False,
    }
    report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["framework_blocked_count"] = ledger.framework_blocked_count
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + ledger.control_plane_run_count
            + int(campaign.repair_continuation_used)
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        measurement_ledger=ledger,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        latest_report_path=str(
            controller.store.run_path(latest_run_id) / "report.json"
        ),
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_source_behavior_materialization_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Reopen one cycle after adding deterministic source canonicalization.

    Legacy generation could spend both model attempts on the same valid
    response-index helper topology even though the bounded analyzer cannot
    follow returned helper values.  The new mutator canonicalizes that exact
    topology before validation.  Charge the historical zero-candidate cycle to
    framework control-plane work and grant no candidate or measurement reserve.
    """

    migration_action = "restore_source_behavior_materialization_frontier"
    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "candidate_repair_frontier_stalled"
        or not campaign.run_ids
        or campaign.cumulative_authoritative_candidates
        >= _campaign_effective_authoritative_candidate_limit(campaign)
    ):
        return campaign
    for run_id in campaign.run_ids:
        try:
            prior_report = controller.store.read_report(run_id)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        migration = prior_report.get("campaign_causal_migration")
        if (
            isinstance(migration, Mapping)
            and migration.get("action") == migration_action
        ):
            return campaign

    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    funnel = report.get("verification_funnel")
    if not (
        isinstance(funnel, Mapping)
        and funnel.get("generation_materialization_frontier_exhausted") is True
        and funnel.get("generation_stop_reason")
        == "materialization_frontier_repeated"
        and _report_authoritative_candidate_count(report) == 0
        and not _report_has_authoritative_measurement_observation(report)
    ):
        return campaign

    optimizer = report.get("optimizer_diagnostics")
    iterations = (
        optimizer.get("iterations") if isinstance(optimizer, Mapping) else None
    )
    if not isinstance(iterations, list):
        return campaign
    source_behavior_failures: list[Mapping[str, Any]] = []
    all_materialization_failures: list[Mapping[str, Any]] = []
    for iteration in iterations:
        diagnostics = (
            iteration.get("diagnostics")
            if isinstance(iteration, Mapping)
            else None
        )
        failures = (
            diagnostics.get("candidate_materialization_failures")
            if isinstance(diagnostics, Mapping)
            else None
        )
        if not isinstance(failures, list):
            continue
        for failure in failures:
            if not isinstance(failure, Mapping):
                continue
            all_materialization_failures.append(failure)
            if (
                failure.get("code") == "source_behavior_proof_failed"
                and failure.get("stage") == "candidate_semantic_validation"
                and failure.get("representation") == "candidate_package"
                and failure.get("repairable") is True
            ):
                source_behavior_failures.append(failure)
    if (
        len(source_behavior_failures) < 2
        or len(source_behavior_failures) != len(all_materialization_failures)
    ):
        return campaign
    contract_identity_digests = {
        str(failure["contract_identity_digest"])
        for failure in source_behavior_failures
        if isinstance(failure.get("contract_identity_digest"), str)
        and failure.get("contract_identity_digest")
    }
    if len(contract_identity_digests) != 1:
        return campaign

    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
        reason_code="framework_source_behavior_canonicalization_repaired",
        owner="framework",
        stage="candidate_generation",
        scope="shared_run",
        repairable=True,
        progress_delta_ids=campaign.latest_disposition.progress_delta_ids,
    )
    ledger = campaign.measurement_ledger.charge_framework_blocked(latest_run_id)
    report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.source_behavior_materialization_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": latest_run_id,
        "failed_generation_attempt_count": len(source_behavior_failures),
        "contract_identity_digests": sorted(contract_identity_digests),
        "candidate_reserve_granted": False,
        "measurement_retry_granted": False,
    }
    report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["framework_blocked_count"] = ledger.framework_blocked_count
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + ledger.control_plane_run_count
            + int(campaign.repair_continuation_used)
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        measurement_ledger=ledger,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        latest_report_path=str(
            controller.store.run_path(latest_run_id) / "report.json"
        ),
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_fixture_source_selection_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Reopen the exact compile frontier fixed by source selection normalization.

    A legacy selector ranked every evidence fragment by minimum byte length,
    before checking whether it carried recorded responses.  Reclassify only a
    repeated, zero-authority ``protocol_probe_not_fixture_derived`` frontier as
    framework control-plane work.  This grants neither candidate nor
    measurement reserve; the charged control-plane cycle is the sole retry.
    """

    migration_action = "restore_fixture_source_selection_frontier"
    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "campaign_cycle_limit_reached"
        or campaign.latest_disposition.stage != "capability_compile"
        or not campaign.run_ids
        or campaign.cumulative_authoritative_candidates
        >= _campaign_effective_authoritative_candidate_limit(campaign)
    ):
        return campaign
    for run_id in campaign.run_ids:
        try:
            prior_report = controller.store.read_report(run_id)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        migration = prior_report.get("campaign_causal_migration")
        if (
            isinstance(migration, Mapping)
            and migration.get("action") == migration_action
        ):
            return campaign

    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    attribution = report.get("campaign_failure_attribution")
    rejection = report.get("rejection_attribution")
    funnel = report.get("verification_funnel")
    if not (
        isinstance(attribution, Mapping)
        and attribution.get("code") == "repair_capability_compile_failed"
        and attribution.get("primary_gate") == "candidate_repair_conformance"
        and attribution.get("repairable") is True
        and isinstance(rejection, Mapping)
        and rejection.get("code") == "repair_capability_compile_failed"
        and rejection.get("primary_gate") == "candidate_repair_conformance"
        and rejection.get("capability_error_code")
        == "protocol_probe_not_fixture_derived"
        and rejection.get("repairable") is True
        and isinstance(funnel, Mapping)
        and _report_authoritative_candidate_count(report) == 0
        and funnel.get("authoritative_candidate_attempt_count") == 0
        and not _report_has_authoritative_measurement_observation(report)
    ):
        return campaign
    repeated_failure_count = funnel.get("conformance_same_slot_repair_count")
    occurrence_count = attribution.get("occurrence_count")
    affected_candidate_count = attribution.get("affected_candidate_count")
    if not (
        isinstance(repeated_failure_count, int)
        and not isinstance(repeated_failure_count, bool)
        and repeated_failure_count >= 2
        and occurrence_count == repeated_failure_count
        and affected_candidate_count == repeated_failure_count
    ):
        return campaign

    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
        reason_code="framework_fixture_source_selection_repaired",
        owner="framework",
        stage="capability_compile",
        scope="shared_run",
        repairable=True,
        progress_delta_ids=campaign.latest_disposition.progress_delta_ids,
    )
    ledger = campaign.measurement_ledger.charge_framework_blocked(latest_run_id)
    report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.fixture_source_selection_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": latest_run_id,
        "capability_error_code": "protocol_probe_not_fixture_derived",
        "repeated_same_slot_failure_count": repeated_failure_count,
        "candidate_reserve_granted": False,
        "measurement_retry_granted": False,
    }
    report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["framework_blocked_count"] = ledger.framework_blocked_count
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + ledger.control_plane_run_count
            + int(campaign.repair_continuation_used)
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        measurement_ledger=ledger,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        latest_report_path=str(
            controller.store.run_path(latest_run_id) / "report.json"
        ),
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_no_work_after_fixture_source_selection_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Reactivate compile frontiers suppressed by their stale scheduler checkpoint."""

    migration_action = "reactivate_fixture_source_frontier_after_no_work_cycle"
    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "candidate_repair_frontier_stalled"
        or len(campaign.run_ids) < 2
        or campaign.cumulative_authoritative_candidates
        >= _campaign_effective_authoritative_candidate_limit(campaign)
    ):
        return campaign
    no_work_run_id = campaign.run_ids[-1]
    if no_work_run_id in campaign.measurement_ledger.framework_blocked_run_ids:
        return campaign
    try:
        no_work_report = controller.store.read_report(no_work_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    raw_gates = no_work_report.get("gate_results")
    if not (
        isinstance(raw_gates, list)
        and len(raw_gates) == 1
        and isinstance(raw_gates[0], Mapping)
        and raw_gates[0].get("gate_name") == "candidate_generation"
        and raw_gates[0].get("passed") is False
        and isinstance(raw_gates[0].get("details"), Mapping)
        and raw_gates[0]["details"].get("generated_candidate_count") == 0
        and raw_gates[0]["details"].get("iterations") == 0
        and _report_authoritative_candidate_count(no_work_report) == 0
        and not _report_has_authoritative_measurement_observation(no_work_report)
    ):
        return campaign

    source_run_id: str | None = None
    source_report: Mapping[str, Any] | None = None
    for prior_run_id in reversed(campaign.run_ids[:-1]):
        try:
            prior_report = controller.store.read_report(prior_run_id)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        migration = prior_report.get("campaign_causal_migration")
        if not (
            isinstance(migration, Mapping)
            and migration.get("action")
            == "restore_fixture_source_selection_frontier"
            and migration.get("candidate_reserve_granted") is False
            and migration.get("measurement_retry_granted") is False
        ):
            continue
        source_run_id = prior_run_id
        source_report = prior_report
        break
    if source_run_id is None or source_report is None:
        return campaign

    repair_state = no_work_report.get("repair_frontier_state")
    scheduler_state = (
        repair_state.get("scheduler_state")
        if isinstance(repair_state, dict)
        else None
    )
    if not (
        isinstance(repair_state, dict)
        and isinstance(scheduler_state, dict)
        and repair_state.get("active_count") == 0
        and int(repair_state.get("dormant_count") or 0) > 0
    ):
        return campaign
    frontier_keys = _report_generation_frontier_keys(source_report)
    raw_stalls = scheduler_state.get("frontier_stalls")
    raw_families = scheduler_state.get("frontier_mutation_families")
    if not (
        frontier_keys
        and isinstance(raw_stalls, dict)
        and isinstance(raw_families, dict)
    ):
        return campaign
    reactivated_keys = tuple(
        sorted(
            key
            for key in frontier_keys
            if key in raw_stalls and key in raw_families
        )
    )
    if not reactivated_keys:
        return campaign
    for key in reactivated_keys:
        raw_stalls[key] = 0
        raw_families[key] = []
    scheduler_state["last_focused_frontier"] = None
    records = repair_state.get("records")
    if isinstance(records, list):
        for record in records:
            if (
                isinstance(record, dict)
                and record.get("semantic_key") in reactivated_keys
            ):
                record["status"] = "active"
                record["mutation_families"] = []
        repair_state["active_count"] = sum(
            isinstance(record, Mapping) and record.get("status") == "active"
            for record in records
        )
        repair_state["dormant_count"] = sum(
            isinstance(record, Mapping) and record.get("status") == "dormant"
            for record in records
        )

    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
        reason_code="framework_fixture_source_frontier_reactivated",
        owner="framework",
        stage="candidate_generation",
        scope="shared_run",
        repairable=True,
        progress_delta_ids=campaign.latest_disposition.progress_delta_ids,
    )
    ledger = campaign.measurement_ledger.charge_framework_blocked(no_work_run_id)
    no_work_report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.fixture_source_no_work_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": source_run_id,
        "no_work_run_id": no_work_run_id,
        "reactivated_frontier_keys": list(reactivated_keys),
        "candidate_generation_iterations": 0,
        "candidate_generation_count": 0,
        "candidate_reserve_granted": False,
        "measurement_retry_granted": False,
    }
    no_work_report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = no_work_report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["framework_blocked_count"] = ledger.framework_blocked_count
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + ledger.control_plane_run_count
            + int(campaign.repair_continuation_used)
        )
    controller.store.write_report(no_work_run_id, no_work_report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        measurement_ledger=ledger,
        latest_progress=self_improvement_progress(source_report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        latest_report_path=str(
            controller.store.run_path(no_work_run_id) / "report.json"
        ),
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_regressing_measurement_checkpoint_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Discard a legacy checkpoint selected by absolute rather than paired score.

    A framework-blocked rejected state used to rank candidates by their raw
    candidate score. Because every candidate can have a different judge
    baseline, that could retain a proven regression even when the same run had
    already measured a positive paired effect. The stale immutable checkpoint
    must not be resumed after the ranking and projection-attestation fixes.
    """

    migration_action = "discard_regressing_measurement_checkpoint"
    if (
        campaign.status is not SelfImprovementCampaignStatus.PAUSED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "baseline_evidence_policy_infeasible"
        or campaign.measurement_pending_run_id is None
        or campaign.measurement_pending_candidate_id is None
        or not campaign.run_ids
        or campaign.measurement_pending_run_id != campaign.run_ids[-1]
    ):
        return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    prior_migration = report.get("campaign_causal_migration")
    if (
        isinstance(prior_migration, Mapping)
        and prior_migration.get("action") == migration_action
    ):
        return campaign
    selected_candidate_id = campaign.measurement_pending_candidate_id
    if report.get("selected_candidate_id") != selected_candidate_id:
        return campaign
    deltas = _report_iteration_score_deltas(report)
    selected_delta = deltas.get(selected_candidate_id)
    positive_candidates = tuple(
        (candidate_id, delta)
        for candidate_id, delta in deltas.items()
        if delta > 0.0
    )
    if selected_delta is None or selected_delta >= 0.0 or not positive_candidates:
        return campaign
    preferred_candidate_id, preferred_delta = max(
        positive_candidates,
        key=lambda item: (item[1], item[0]),
    )
    if not _report_has_unattested_mixed_projection_constraint(
        report,
        candidate_id=selected_candidate_id,
    ):
        return campaign

    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
        reason_code="framework_candidate_checkpoint_selection_repaired",
        owner="framework",
        stage="measurement",
        scope="shared_run",
        repairable=True,
        progress_delta_ids=campaign.latest_disposition.progress_delta_ids,
    )
    ledger = campaign.measurement_ledger.charge_framework_blocked(latest_run_id)
    report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.paired_candidate_checkpoint_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": latest_run_id,
        "discarded_candidate_id": selected_candidate_id,
        "discarded_score_delta": selected_delta,
        "preferred_candidate_id": preferred_candidate_id,
        "preferred_score_delta": preferred_delta,
        "candidate_reserve_granted": False,
        "measurement_retry_granted": False,
    }
    report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["framework_blocked_count"] = ledger.framework_blocked_count
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + ledger.control_plane_run_count
            + int(campaign.repair_continuation_used)
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        measurement_ledger=ledger,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        latest_report_path=str(
            controller.store.run_path(latest_run_id) / "report.json"
        ),
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_paired_replay_member_timeout_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Reopen a legacy handoff after fixing the member timeout envelope."""

    migration_action = "restore_paired_replay_member_timeout_checkpoint"
    if (
        campaign.status is not SelfImprovementCampaignStatus.PAUSED
        or not campaign.run_ids
        or campaign.latest_disposition is None
        or campaign.latest_disposition.kind
        is not SelfImprovementDispositionKind.HANDOFF_GOAL
        or campaign.measurement_retry_count >= campaign.max_measurement_retries
    ):
        return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    prior_migration = report.get("campaign_causal_migration")
    if (
        isinstance(prior_migration, Mapping)
        and prior_migration.get("action") == migration_action
    ):
        return campaign
    attribution = report.get("rejection_attribution")
    if not (
        isinstance(attribution, Mapping)
        and attribution.get("code") == "replay_member_phase_timeout"
        and attribution.get("failure_owner") == "framework"
        and attribution.get("failure_scope") == "member"
        and attribution.get("repairable") is True
    ):
        return campaign
    raw_candidate_ids = report.get("candidate_ids")
    candidate_ids = tuple(
        dict.fromkeys(
            item
            for item in (
                raw_candidate_ids
                if isinstance(raw_candidate_ids, (list, tuple))
                else ()
            )
            if isinstance(item, str) and item
        )
    )
    if len(candidate_ids) != 1:
        return campaign
    candidate_id = candidate_ids[0]
    request_path = (
        controller.store.run_path(latest_run_id)
        / "replay"
        / candidate_id
        / "request.json"
    )
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    fingerprint = (
        request.get("verified_candidate_package_fingerprint")
        if isinstance(request, Mapping)
        else None
    )
    if not isinstance(fingerprint, str) or not fingerprint:
        return campaign
    checkpoint = discover_paired_replay_resume_checkpoint(
        controller.store,
        run_id=latest_run_id,
        candidate_id=candidate_id,
        verified_candidate_package_fingerprint=fingerprint,
    )
    if checkpoint is None:
        return campaign

    corrected_disposition = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.REPAIR_MEASUREMENT,
        reason_code="replay_member_phase_timeout",
        owner="evaluation_harness",
        stage="evaluation",
        scope="shared_run",
        repairable=True,
        progress_delta_ids=campaign.latest_disposition.progress_delta_ids,
        diagnostic_refs=_string_tuple(attribution.get("diagnostic_refs")),
    )
    ledger = campaign.measurement_ledger.charge_invalid_retry(latest_run_id)
    report["paired_replay_resume_checkpoint"] = checkpoint.to_dict()
    report["measurement_pending_candidate_id"] = checkpoint.candidate_id
    report["measurement_pending_candidate_fingerprint"] = (
        checkpoint.candidate_fingerprint
    )
    report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.paired_replay_member_timeout_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": latest_run_id,
        "candidate_id": checkpoint.candidate_id,
        "measurement_retry_granted": True,
    }
    report["self_improvement_disposition"] = corrected_disposition.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign.update(
            {
                "measurement_retry_count": ledger.invalid_retry_count,
                "measurement_pending_run_id": checkpoint.source_run_id,
                "measurement_pending_candidate_id": checkpoint.candidate_id,
                "max_cycles": (
                    campaign.max_cycles
                    + ledger.control_plane_run_count
                    + int(campaign.repair_continuation_used)
                ),
            }
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        measurement_ledger=ledger,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected_disposition,
        latest_measurement_outcome=None,
        latest_report_path=str(
            controller.store.run_path(latest_run_id) / "report.json"
        ),
        measurement_pending_run_id=checkpoint.source_run_id,
        measurement_pending_candidate_id=checkpoint.candidate_id,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_paired_replay_timeout_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Reopen a campaign that discarded a safe progressive replay cursor."""

    migration_action = "restore_paired_replay_timeout_checkpoint"
    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or not campaign.run_ids
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        not in {
            "measurement_authority_checkpoint_missing_or_invalid",
            "paired_replay_checkpoint_missing_or_invalid",
        }
    ):
        return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    prior_migration = report.get("campaign_causal_migration")
    if (
        isinstance(prior_migration, Mapping)
        and prior_migration.get("action") == migration_action
    ):
        return campaign
    if not _report_requests_paired_replay_continuation(report):
        return campaign
    attribution = next(
        item
        for item in (
            report.get("rejection_attribution"),
            report.get("campaign_failure_attribution"),
        )
        if isinstance(item, Mapping)
        and item.get("code") == "replay_total_timeout"
    )
    candidate_id = attribution.get("resume_candidate_id")
    verified_candidate_package_fingerprint = attribution.get(
        "resume_candidate_package_fingerprint"
    )
    if not (
        isinstance(candidate_id, str)
        and candidate_id
        and isinstance(verified_candidate_package_fingerprint, str)
        and verified_candidate_package_fingerprint
    ):
        return campaign
    checkpoint = discover_paired_replay_resume_checkpoint(
        controller.store,
        run_id=latest_run_id,
        candidate_id=candidate_id,
        verified_candidate_package_fingerprint=(
            verified_candidate_package_fingerprint
        ),
    )
    if checkpoint is None:
        return campaign

    corrected_disposition = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.COLLECT_MORE_EVIDENCE,
        reason_code="replay_total_timeout",
        owner="evaluation_harness",
        stage=_optional_string(
            attribution.get("failure_stage") or "candidate_replay"
        ),
        scope="shared_run",
        repairable=True,
        progress_delta_ids=campaign.latest_disposition.progress_delta_ids,
        diagnostic_refs=_string_tuple(attribution.get("diagnostic_refs")),
    )
    ledger = campaign.measurement_ledger.charge_continuation(latest_run_id)
    report["paired_replay_resume_checkpoint"] = checkpoint.to_dict()
    report["measurement_pending_candidate_id"] = checkpoint.candidate_id
    report["measurement_pending_candidate_fingerprint"] = (
        checkpoint.candidate_fingerprint
    )
    report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.paired_replay_timeout_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": latest_run_id,
        "candidate_id": checkpoint.candidate_id,
        "continuation_granted": True,
    }
    report["self_improvement_disposition"] = corrected_disposition.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign.update(
            {
                "measurement_continuation_count": ledger.continuation_count,
                "measurement_pending_run_id": checkpoint.source_run_id,
                "measurement_pending_candidate_id": checkpoint.candidate_id,
                "max_cycles": (
                    campaign.max_cycles
                    + ledger.control_plane_run_count
                    + int(campaign.repair_continuation_used)
                ),
            }
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        measurement_ledger=ledger,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected_disposition,
        latest_measurement_outcome=None,
        latest_report_path=str(
            controller.store.run_path(latest_run_id) / "report.json"
        ),
        measurement_pending_run_id=checkpoint.source_run_id,
        measurement_pending_candidate_id=checkpoint.candidate_id,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_retryable_member_measurement_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Restore a frozen measurement after legacy member-timeout attribution.

    Older reports projected a retryable framework-owned member timeout as a
    completed no-effect candidate conclusion. That both charged the immutable
    candidate again and discarded the original measurement journal, even when
    all other work units were trusted and reusable.
    """

    migration_action = "restore_retryable_member_measurement_checkpoint"
    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or not campaign.run_ids
        or campaign.measurement_retry_count >= campaign.max_measurement_retries
    ):
        return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    prior_migration = report.get("campaign_causal_migration")
    if (
        isinstance(prior_migration, Mapping)
        and prior_migration.get("action") == migration_action
    ):
        return campaign
    if not _report_has_retryable_framework_member_measurement_failure(report):
        return campaign

    raw_candidate_ids = report.get("candidate_ids")
    candidate_ids = {
        item
        for item in (
            raw_candidate_ids
            if isinstance(raw_candidate_ids, (list, tuple))
            else ()
        )
        if isinstance(item, str) and item
    }
    if not candidate_ids:
        replay = report.get("replay")
        candidate = replay.get("candidate") if isinstance(replay, Mapping) else None
        variant_id = (
            candidate.get("variant_id") if isinstance(candidate, Mapping) else None
        )
        if isinstance(variant_id, str) and variant_id:
            candidate_ids.add(variant_id)
    checkpoint: MeasurementResumeCheckpointV1 | None = None
    for source_run_id in reversed(campaign.run_ids):
        try:
            source_report = controller.store.read_report(source_run_id)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        candidate_checkpoint = _measurement_resume_checkpoint(
            controller.store,
            run_id=source_run_id,
            report=source_report,
        )
        if candidate_checkpoint is not None and (
            not candidate_ids or candidate_checkpoint.candidate_id in candidate_ids
        ):
            checkpoint = candidate_checkpoint
            break
    if checkpoint is None:
        return campaign

    charged_candidate_count = _report_authoritative_candidate_count(report)
    corrected_outcome = CampaignMeasurementOutcomeV2(
        execution_status=MeasurementExecutionStatus.INVALID,
        improvement_outcome=CandidateImprovementOutcome.UNKNOWN,
        release_gates_passed=False,
        continuation_available=True,
        reason_code="measurement_infrastructure_retry",
    )
    corrected_disposition = _measurement_outcome_disposition(
        corrected_outcome,
        progress_delta_ids=(
            campaign.latest_disposition.progress_delta_ids
            if campaign.latest_disposition is not None
            else ()
        ),
    )
    ledger = campaign.measurement_ledger.charge_invalid_retry(latest_run_id)
    authoritative_count = max(
        0,
        campaign.cumulative_authoritative_candidates - charged_candidate_count,
    )
    report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.retryable_member_measurement_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": latest_run_id,
        "checkpoint_run_id": checkpoint.source_run_id,
        "candidate_id": checkpoint.candidate_id,
        "candidate_reserve_restored": charged_candidate_count,
        "measurement_retry_granted": True,
    }
    report["campaign_measurement_outcome"] = corrected_outcome.to_dict()
    report["self_improvement_disposition"] = corrected_disposition.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign.update(
            {
                "measurement_retry_count": ledger.invalid_retry_count,
                "measurement_projection": corrected_outcome.projection.value,
                "measurement_execution_status": (
                    corrected_outcome.execution_status.value
                ),
                "candidate_improvement_outcome": (
                    corrected_outcome.improvement_outcome.value
                ),
                "measurement_pending_run_id": checkpoint.source_run_id,
                "measurement_pending_candidate_id": checkpoint.candidate_id,
                "authoritative_candidate_count": authoritative_count,
                "max_cycles": (
                    campaign.max_cycles
                    + ledger.control_plane_run_count
                    + int(campaign.repair_continuation_used)
                ),
            }
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        cumulative_authoritative_candidates=authoritative_count,
        measurement_ledger=ledger,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected_disposition,
        latest_measurement_outcome=corrected_outcome,
        latest_report_path=str(
            controller.store.run_path(latest_run_id) / "report.json"
        ),
        measurement_pending_run_id=checkpoint.source_run_id,
        measurement_pending_candidate_id=checkpoint.candidate_id,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _report_has_retryable_framework_member_measurement_failure(
    report: Mapping[str, Any],
) -> bool:
    retryable_codes = {
        "replay_member_phase_timeout",
        "evidence_policy_v2_attestation_failed",
        "replay_evidence_runtime_policy_violation",
    }
    return any(
        item.get("code") in retryable_codes
        and (item.get("owner") or item.get("failure_owner"))
        in {"framework", "infrastructure"}
        and (item.get("scope") or item.get("failure_scope")) == "member"
        and item.get("repairable") is True
        for item in _walk_mappings(report)
    )


def _migrate_unobserved_support_timeout_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Reopen a cycle exhausted by a legacy baseline-timeout misattribution."""

    migration_action = "restore_unobserved_support_timeout_control_cycle"
    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "candidate_repair_frontier_stalled"
        or not campaign.run_ids
    ):
        return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    prior_migration = report.get("campaign_causal_migration")
    if (
        isinstance(prior_migration, Mapping)
        and prior_migration.get("action") == migration_action
    ):
        return campaign
    affected = _report_unobserved_support_timeout_candidates(report)
    if not affected or _report_has_authoritative_measurement_observation(report):
        return campaign

    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
        reason_code="framework_screening_control_attribution_repaired",
        owner="framework",
        stage="candidate_screening",
        scope="shared_run",
        repairable=True,
        progress_delta_ids=(
            campaign.latest_disposition.progress_delta_ids
        ),
    )
    ledger = campaign.measurement_ledger.charge_framework_blocked(latest_run_id)
    report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.unobserved_support_timeout_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": latest_run_id,
        "affected_case_ids": sorted(affected),
        "affected_candidate_ids": sorted(
            {
                candidate_id
                for candidate_ids in affected.values()
                for candidate_id in candidate_ids
            }
        ),
        "candidate_reserve_granted": False,
        "measurement_retry_granted": False,
    }
    report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["framework_blocked_count"] = ledger.framework_blocked_count
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + ledger.control_plane_run_count
            + int(campaign.repair_continuation_used)
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        measurement_ledger=ledger,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_path_sensitive_support_identity_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Reopen a stored-candidate frontier exhausted by legacy path identity."""

    migration_action = "restore_path_independent_support_repair_frontier"
    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "candidate_repair_frontier_stalled"
        or campaign.repair_continuation_used
        or not campaign.run_ids
    ):
        return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    prior_migration = report.get("campaign_causal_migration")
    if (
        isinstance(prior_migration, Mapping)
        and prior_migration.get("action") == migration_action
    ):
        return campaign
    has_legacy_support_gate = any(
        item.get("code") == "candidate_replay_support_baseline_incompatible"
        for item in _walk_mappings(report.get("gate_results"))
    )
    raw_sources = report.get("candidate_source_dispositions")
    stored_measurement_rerun = bool(
        isinstance(raw_sources, Mapping)
        and any(
            isinstance(value, Mapping)
            and value.get("kind") == "stored_evidence_rerun"
            for value in raw_sources.values()
        )
    )
    if not has_legacy_support_gate or not stored_measurement_rerun:
        return campaign

    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CANDIDATE,
        reason_code="replay_support_identity_repaired",
        owner="candidate",
        stage="task_rollout",
        scope="candidate",
        repairable=True,
        progress_delta_ids=campaign.latest_disposition.progress_delta_ids,
    )
    report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.path_independent_support_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": latest_run_id,
        "previous_reason_code": campaign.latest_disposition.reason_code,
        "corrected_reason_code": corrected.reason_code,
        "candidate_reserve_granted": True,
    }
    report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["repair_continuation_used"] = True
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + campaign.measurement_ledger.control_plane_run_count
            + 1
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        repair_continuation_used=True,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_untyped_runtime_python_syntax_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Restore one repair reserve for a legacy untyped runtime SyntaxError."""

    migration_action = "restore_typed_runtime_python_syntax_repair"
    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "campaign_cycle_limit_reached"
        or campaign.latest_disposition.stage != "capability_preflight"
        or campaign.repair_continuation_used
        or not campaign.run_ids
        or campaign.cumulative_authoritative_candidates
        >= _campaign_authoritative_candidate_limit(campaign)
    ):
        return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    prior_migration = report.get("campaign_causal_migration")
    if (
        isinstance(prior_migration, Mapping)
        and prior_migration.get("action") == migration_action
    ):
        return campaign
    funnel = report.get("verification_funnel")
    if not (
        isinstance(funnel, Mapping)
        and _report_authoritative_candidate_count(report) == 0
        and funnel.get("authoritative_candidate_attempt_count") == 0
        and not _report_has_authoritative_measurement_observation(report)
    ):
        return campaign
    focus_candidate_id = report.get("repair_focus_candidate_id")
    if not isinstance(focus_candidate_id, str) or not focus_candidate_id:
        return campaign
    runtime_path = (
        controller.store.run_path(latest_run_id)
        / "candidates"
        / focus_candidate_id
        / "replay"
        / "runtime.py"
    )
    if runtime_path.is_symlink() or not runtime_path.is_file():
        return campaign
    try:
        compile(runtime_path.read_bytes(), "replay/runtime.py", "exec")
    except SyntaxError as exc:
        counterexample = python_source_syntax_counterexample(
            source_path="replay/runtime.py",
            error=exc,
        )
    except (OSError, UnicodeError):
        return campaign
    else:
        return campaign
    if counterexample.get("syntax_kind") != "global_declaration_after_use":
        return campaign

    matching_details: list[dict[str, Any]] = []
    raw_gates = report.get("gate_results")
    if isinstance(raw_gates, list):
        for gate in raw_gates:
            details = gate.get("details") if isinstance(gate, dict) else None
            if not (
                isinstance(gate, dict)
                and gate.get("gate_name") == "candidate_repair_conformance"
                and gate.get("passed") is False
                and isinstance(details, dict)
                and details.get("code") == "repair_probe_execution_failed"
                and details.get("failure_class") == "candidate"
                and details.get("repairable") is True
            ):
                continue
            diagnostics = details.get("diagnostics")
            if not (
                isinstance(diagnostics, list)
                and any(
                    isinstance(item, Mapping)
                    and item.get("error_type")
                    == "ReplayServiceProcessExitedError"
                    and "SyntaxError:" in str(item.get("reason") or "")
                    and "global declaration" in str(item.get("reason") or "")
                    for item in diagnostics
                )
            ):
                continue
            matching_details.append(details)
    if not matching_details:
        return campaign

    for details in matching_details:
        details["capability_error_code"] = "runtime_python_syntax_invalid"
        existing = details.get("counterexample_contracts")
        contracts = {
            str(item.get("counterexample_id")): dict(item)
            for item in (
                existing if isinstance(existing, list) else ()
            )
            if isinstance(item, Mapping)
            and isinstance(item.get("counterexample_id"), str)
        }
        contracts[str(counterexample["counterexample_id"])] = counterexample
        details["counterexample_contracts"] = [
            contracts[key] for key in sorted(contracts)
        ]
        diagnostics = details.get("diagnostics")
        if isinstance(diagnostics, list):
            diagnostics.append(
                {
                    "code": "runtime_python_syntax_invalid",
                    "root_cause_code": "runtime_python_syntax_invalid",
                    "source_path": counterexample["source_path"],
                    "syntax_kind": counterexample["syntax_kind"],
                    "source_line": counterexample["line"],
                    "counterexample_contracts": [counterexample],
                }
            )

    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CANDIDATE,
        reason_code="candidate_runtime_python_syntax_repair_available",
        owner="candidate",
        stage="capability_compile",
        scope="candidate",
        repairable=True,
        progress_delta_ids=campaign.latest_disposition.progress_delta_ids,
    )
    report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.runtime_python_syntax_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": latest_run_id,
        "candidate_id": focus_candidate_id,
        "counterexample_id": counterexample["counterexample_id"],
        "candidate_reserve_granted": True,
        "measurement_retry_granted": False,
    }
    report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["repair_continuation_used"] = True
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + campaign.measurement_ledger.control_plane_run_count
            + 1
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        repair_continuation_used=True,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_unselected_recorded_response_fixture_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Refund a cycle consumed before fixture-source fail-fast existed."""

    migration_action = "restore_recorded_response_fixture_selection_repair"
    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "campaign_cycle_limit_reached"
        or campaign.latest_disposition.stage != "capability_preflight"
        or not campaign.run_ids
        or campaign.cumulative_authoritative_candidates
        >= _campaign_effective_authoritative_candidate_limit(campaign)
    ):
        return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    prior_migration = report.get("campaign_causal_migration")
    if (
        isinstance(prior_migration, Mapping)
        and prior_migration.get("action") == migration_action
    ):
        return campaign
    focus_candidate_id = report.get("repair_focus_candidate_id")
    if not isinstance(focus_candidate_id, str) or not focus_candidate_id:
        return campaign
    run_root = controller.store.run_path(latest_run_id)
    candidate_root = run_root / "candidates" / focus_candidate_id / "replay"
    compiler_path = candidate_root / "compiler.py"
    runtime_path = candidate_root / "runtime.py"
    if any(
        path.is_symlink() or not path.is_file()
        for path in (compiler_path, runtime_path)
    ):
        return campaign
    try:
        compiler_source = compiler_path.read_text(encoding="utf-8")
        runtime_source = runtime_path.read_text(encoding="utf-8")
        runtime_sha256 = hashlib.sha256(runtime_path.read_bytes()).hexdigest()
    except (OSError, UnicodeError):
        return campaign
    if not (
        "byte_length" in compiler_source
        and "sorted(" in compiler_source
        and "response_record_count" not in compiler_source
        and recorded_response_index_source_behavior_proof(runtime_source).get(
            "proven"
        ) is True
    ):
        return campaign
    if not _run_has_unbound_recorded_response_fixture(
        run_root,
        runtime_sha256=runtime_sha256,
    ):
        return campaign

    matching_details: list[dict[str, Any]] = []
    raw_gates = report.get("gate_results")
    if isinstance(raw_gates, list):
        for gate in raw_gates:
            details = gate.get("details") if isinstance(gate, dict) else None
            if not (
                isinstance(gate, dict)
                and gate.get("gate_name") == "candidate_repair_conformance"
                and gate.get("passed") is False
                and isinstance(details, dict)
                and details.get("code") == "repair_probe_execution_failed"
                and details.get("failure_class") == "candidate"
                and details.get("repairable") is True
            ):
                continue
            diagnostics = details.get("diagnostics")
            if not (
                isinstance(diagnostics, list)
                and any(
                    isinstance(item, Mapping)
                    and item.get("error_type")
                    == "ReplayServiceReadinessTimeout"
                    and "TypeError" in str(item.get("reason") or "")
                    for item in diagnostics
                )
            ):
                continue
            matching_details.append(details)
    if not matching_details:
        return campaign

    constraint = {
        "schema_layer": "compiler",
        "field_path": "evidence_derivations[*].response_index_path",
        "rule": "enum",
        "expected": ["recorded_response_source"],
        "value_domain": "source_behavior",
        "required_operations": [
            "restrict_to_positive_recorded_response_sources",
            "prefer_maximum_response_record_count",
        ],
        "forbidden_operations": [
            "rank_all_sources_by_minimum_byte_length_first"
        ],
    }
    counterexample_identity = json.dumps(
        {
            "candidate_id": focus_candidate_id,
            "constraint": constraint,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    counterexample = {
        "schema_version": (
            "aworld.self_evolve.fixture_source_counterexample.v1"
        ),
        "counterexample_id": (
            "fixture-source-counterexample-"
            + hashlib.sha256(counterexample_identity).hexdigest()
        ),
        "candidate_id": focus_candidate_id,
        "selected_fixture_has_recorded_response": False,
        "recorded_response_source_available": True,
        "required_checks": [
            "selected_source_has_positive_response_record_count",
            "frozen_fixture_has_response_index_sidecar",
            "runtime_response_index_binding_is_nonempty",
        ],
    }
    for details in matching_details:
        details["capability_error_code"] = (
            "recorded_response_fixture_unselected"
        )
        details["schema_field_constraints"] = [constraint]
        details["counterexample_contracts"] = [counterexample]
        diagnostics = details.get("diagnostics")
        if isinstance(diagnostics, list):
            diagnostics.append(
                {
                    "code": "recorded_response_fixture_unselected",
                    "root_cause_code": (
                        "recorded_response_fixture_unselected"
                    ),
                    "counterexample_contracts": [counterexample],
                }
            )

    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CANDIDATE,
        reason_code="candidate_fixture_source_selection_repair_available",
        owner="candidate",
        stage="capability_compile",
        scope="candidate",
        repairable=True,
        progress_delta_ids=campaign.latest_disposition.progress_delta_ids,
    )
    ledger = campaign.measurement_ledger.charge_framework_blocked(latest_run_id)
    report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.recorded_response_fixture_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": latest_run_id,
        "candidate_id": focus_candidate_id,
        "counterexample_id": counterexample["counterexample_id"],
        "candidate_reserve_granted": False,
        "framework_cycle_refunded": True,
    }
    report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["framework_blocked_count"] = (
            ledger.framework_blocked_count
        )
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + ledger.control_plane_run_count
            + int(campaign.repair_continuation_used)
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        measurement_ledger=ledger,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _run_has_unbound_recorded_response_fixture(
    run_root: Path,
    *,
    runtime_sha256: str,
) -> bool:
    """Prove the legacy frozen bundle omitted a required sidecar binding."""

    adaptation_root = run_root / "replay_adaptation"
    if not adaptation_root.is_dir():
        return False
    for manifest_path in adaptation_root.rglob("frozen_manifest.json"):
        if manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        runtime_files = manifest.get("runtime_files")
        if not (
            isinstance(runtime_files, list)
            and any(
                isinstance(item, Mapping)
                and str(item.get("sha256") or "").removeprefix("sha256:")
                == runtime_sha256
                for item in runtime_files
            )
        ):
            continue
        services = manifest.get("services")
        if not isinstance(services, list):
            continue
        unbound_requirements: set[str] = set()
        for service in services:
            if not (
                isinstance(service, Mapping)
                and service.get("transport") == "skill_runtime"
                and isinstance(service.get("protocol_probes"), list)
                and service.get("protocol_probes")
                and isinstance(service.get("response_fixture"), str)
            ):
                continue
            probes = service["protocol_probes"]
            if any(
                isinstance(probe, Mapping)
                and isinstance(probe.get("response_record_id"), str)
                and bool(probe.get("response_record_id"))
                for probe in probes
            ):
                continue
            fixture_path = (
                manifest_path.parent
                / "fixtures"
                / str(service["response_fixture"])
            )
            if fixture_path.with_suffix(".responses.json").is_file():
                continue
            requirement_id = service.get("requirement_id")
            if isinstance(requirement_id, str):
                unbound_requirements.add(requirement_id)
        if not unbound_requirements:
            continue
        request_path = manifest_path.parent.parent / "compile-a" / "request.json"
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        requirements = request.get("requirements")
        derivations = request.get("evidence_derivations")
        if not isinstance(requirements, list) or not isinstance(derivations, Mapping):
            continue
        for requirement in requirements:
            if not (
                isinstance(requirement, Mapping)
                and requirement.get("requirement_id") in unbound_requirements
                and isinstance(requirement.get("evidence_refs"), list)
            ):
                continue
            if any(
                isinstance(source, Mapping)
                and isinstance(source.get("response_index_path"), str)
                and bool(source.get("response_index_path"))
                and isinstance(source.get("response_record_count"), int)
                and not isinstance(source.get("response_record_count"), bool)
                and int(source["response_record_count"]) > 0
                for evidence_ref in requirement["evidence_refs"]
                for source in (
                    derivations.get(evidence_ref, ())
                    if isinstance(derivations.get(evidence_ref), list)
                    else ()
                )
            ):
                return True
    return False


def _migrate_legacy_single_turn_replay_budget_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Reopen an implicitly one-turn campaign censored before tool results.

    Older optimize defaults froze ``max_steps=1`` into authoritative
    measurement even when the user did not specify ``--replay-max-runs``.  A
    browser agent can issue its first action in that envelope but cannot
    observe the result or emit a terminal response.  Upgrade only that exact
    implicit historical shape; an explicitly persisted one-turn budget remains
    an operator-owned contract.
    """

    migration_action = "replace_implicit_single_turn_replay_budget"
    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "campaign_cycle_limit_reached"
        or "replay_max_steps" in campaign.request
        or not campaign.run_ids
        or campaign.cumulative_authoritative_candidates
        >= _campaign_effective_authoritative_candidate_limit(campaign)
    ):
        return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    prior_migration = report.get("campaign_causal_migration")
    if (
        isinstance(prior_migration, Mapping)
        and prior_migration.get("action") == migration_action
    ):
        return campaign

    raw_gates = report.get("gate_results")
    affected_case_ids: set[str] = set()
    exact_legacy_budget = False
    if isinstance(raw_gates, list):
        for gate in raw_gates:
            details = (
                gate.get("details")
                if isinstance(gate, Mapping)
                and gate.get("gate_name") == "candidate_replay"
                and gate.get("passed") is False
                else None
            )
            if not isinstance(details, Mapping):
                continue
            control_identity = details.get("control_identity")
            if not (
                isinstance(control_identity, Mapping)
                and control_identity.get("max_steps") == 1
            ):
                continue
            failed_members = details.get("failed_members")
            if not isinstance(failed_members, list):
                continue
            for member in failed_members:
                if not isinstance(member, Mapping):
                    continue
                failures = (
                    member.get("baseline_failure"),
                    member.get("candidate_failure"),
                )
                if not any(
                    isinstance(failure, Mapping)
                    and failure.get("code")
                    == "replay_task_completion_not_established"
                    for failure in failures
                ):
                    continue
                case_id = member.get("case_id")
                if isinstance(case_id, str) and case_id:
                    affected_case_ids.add(case_id)
            exact_legacy_budget = bool(affected_case_ids)
            if exact_legacy_budget:
                break
    if not exact_legacy_budget:
        return campaign

    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
        reason_code="framework_single_turn_replay_budget_repaired",
        owner="framework",
        stage="measurement",
        scope="shared_run",
        repairable=True,
        progress_delta_ids=campaign.latest_disposition.progress_delta_ids,
    )
    ledger = campaign.measurement_ledger.charge_framework_blocked(latest_run_id)
    upgraded_request = dict(campaign.request)
    upgraded_request["replay_max_steps"] = (
        LEGACY_SINGLE_TURN_REPLAY_REPLACEMENT_STEPS
    )
    report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.single_turn_replay_budget_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": latest_run_id,
        "affected_case_ids": sorted(affected_case_ids),
        "previous_replay_max_steps": 1,
        "replacement_replay_max_steps": (
            LEGACY_SINGLE_TURN_REPLAY_REPLACEMENT_STEPS
        ),
        "operator_budget_overridden": False,
        "candidate_reserve_granted": False,
        "measurement_retry_granted": False,
    }
    report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["framework_blocked_count"] = ledger.framework_blocked_count
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + ledger.control_plane_run_count
            + int(campaign.repair_continuation_used)
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        request=upgraded_request,
        request_fingerprint=_fingerprint(upgraded_request),
        verification_fingerprint=_fingerprint(
            _verification_request(upgraded_request)
        ),
        measurement_ledger=ledger,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_insufficient_evidence_replay_budget_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Replace the intermediate ten-slot budget that cannot close evidence."""

    migration_action = "replace_insufficient_evidence_replay_budget"
    if (
        campaign.status is not SelfImprovementCampaignStatus.PAUSED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "evidence_policy_v2_attestation_failed"
        or campaign.request.get("replay_max_steps") != 10
        or not campaign.run_ids
    ):
        return campaign
    latest_run_id = campaign.run_ids[-1]
    try:
        report = controller.store.read_report(latest_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    prior_migration = report.get("campaign_causal_migration")
    if (
        isinstance(prior_migration, Mapping)
        and prior_migration.get("action") == migration_action
    ):
        return campaign
    raw_gates = report.get("gate_results")
    affected_case_ids: set[str] = set()
    exact_budget_censor = False
    if isinstance(raw_gates, list):
        for gate in raw_gates:
            details = (
                gate.get("details")
                if isinstance(gate, Mapping)
                and gate.get("gate_name") == "candidate_replay"
                and gate.get("passed") is False
                else None
            )
            if not (
                isinstance(details, Mapping)
                and details.get("code")
                == "evidence_policy_v2_attestation_failed"
            ):
                continue
            baseline_failure = details.get("baseline_failure")
            candidate_failure = details.get("candidate_failure")
            if not (
                isinstance(baseline_failure, Mapping)
                and baseline_failure.get("code")
                == "replay_task_completion_not_established"
                and isinstance(candidate_failure, Mapping)
                and candidate_failure.get("code")
                == "evidence_policy_v2_attestation_failed"
                and candidate_failure.get("reason")
                == "framework evidence inventory is empty"
            ):
                continue
            failed_members = details.get("failed_members")
            if isinstance(failed_members, list):
                affected_case_ids.update(
                    str(member["case_id"])
                    for member in failed_members
                    if isinstance(member, Mapping)
                    and isinstance(member.get("case_id"), str)
                    and member.get("case_id")
                )
            exact_budget_censor = bool(affected_case_ids)
            if exact_budget_censor:
                break
    if not exact_budget_censor:
        return campaign

    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
        reason_code="framework_evidence_rollout_budget_repaired",
        owner="framework",
        stage="measurement",
        scope="shared_run",
        repairable=True,
        progress_delta_ids=campaign.latest_disposition.progress_delta_ids,
    )
    ledger = campaign.measurement_ledger.charge_framework_blocked(latest_run_id)
    upgraded_request = dict(campaign.request)
    upgraded_request["replay_max_steps"] = (
        LEGACY_SINGLE_TURN_REPLAY_REPLACEMENT_STEPS
    )
    report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.evidence_rollout_budget_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": latest_run_id,
        "affected_case_ids": sorted(affected_case_ids),
        "previous_replay_max_steps": 10,
        "replacement_replay_max_steps": (
            LEGACY_SINGLE_TURN_REPLAY_REPLACEMENT_STEPS
        ),
        "candidate_reserve_granted": False,
        "measurement_retry_granted": False,
    }
    report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["framework_blocked_count"] = ledger.framework_blocked_count
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + ledger.control_plane_run_count
            + int(campaign.repair_continuation_used)
        )
    controller.store.write_report(latest_run_id, report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        request=upgraded_request,
        request_fingerprint=_fingerprint(upgraded_request),
        verification_fingerprint=_fingerprint(
            _verification_request(upgraded_request)
        ),
        measurement_ledger=ledger,
        latest_progress=self_improvement_progress(report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _migrate_no_work_after_single_turn_budget_fix_for_resume(
    controller: SelfImprovementCampaignController,
    campaign: SelfImprovementCampaign,
) -> SelfImprovementCampaign:
    """Reactivate the exact frontier suppressed after budget migration."""

    migration_action = "restore_exploration_after_single_turn_budget_fix"
    if (
        campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED
        or campaign.latest_disposition is None
        or campaign.latest_disposition.reason_code
        != "candidate_repair_frontier_stalled"
        or len(campaign.run_ids) < 2
    ):
        return campaign
    no_work_run_id = campaign.run_ids[-1]
    if no_work_run_id in campaign.measurement_ledger.framework_blocked_run_ids:
        return campaign
    try:
        no_work_report = controller.store.read_report(no_work_run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return campaign
    prior_migration = no_work_report.get("campaign_causal_migration")
    if (
        isinstance(prior_migration, Mapping)
        and prior_migration.get("action") == migration_action
    ):
        return campaign
    raw_gates = no_work_report.get("gate_results")
    no_work_gate = bool(
        isinstance(raw_gates, list)
        and len(raw_gates) == 1
        and isinstance(raw_gates[0], Mapping)
        and raw_gates[0].get("gate_name") == "candidate_generation"
        and raw_gates[0].get("passed") is False
        and isinstance(raw_gates[0].get("details"), Mapping)
        and raw_gates[0]["details"].get("generated_candidate_count") == 0
        and raw_gates[0]["details"].get("iterations") == 0
        and _report_authoritative_candidate_count(no_work_report) == 0
    )
    if not no_work_gate:
        return campaign

    source_run_id: str | None = None
    source_report: Mapping[str, Any] | None = None
    for prior_run_id in reversed(campaign.run_ids[:-1]):
        try:
            prior_report = controller.store.read_report(prior_run_id)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        migration = prior_report.get("campaign_causal_migration")
        if not (
            isinstance(migration, Mapping)
            and migration.get("action")
            in {
                "replace_implicit_single_turn_replay_budget",
                "replace_insufficient_evidence_replay_budget",
            }
        ):
            continue
        source_run_id = prior_run_id
        source_report = prior_report
        break
    if source_run_id is None or source_report is None:
        return campaign

    repair_state = no_work_report.get("repair_frontier_state")
    scheduler_state = (
        repair_state.get("scheduler_state")
        if isinstance(repair_state, dict)
        else None
    )
    if not (
        isinstance(repair_state, dict)
        and isinstance(scheduler_state, dict)
        and repair_state.get("active_count") == 0
        and int(repair_state.get("dormant_count") or 0) > 0
    ):
        return campaign
    frontier_keys = _report_generation_frontier_keys(source_report)
    raw_stalls = scheduler_state.get("frontier_stalls")
    raw_families = scheduler_state.get("frontier_mutation_families")
    if not (
        frontier_keys
        and isinstance(raw_stalls, dict)
        and isinstance(raw_families, dict)
    ):
        return campaign
    reactivated_keys = tuple(
        key for key in frontier_keys if key in raw_stalls and key in raw_families
    )
    if not reactivated_keys:
        return campaign
    for key in reactivated_keys:
        raw_stalls[key] = 0
        raw_families[key] = []
    scheduler_state["initial_exploration_scheduled"] = False
    scheduler_state["untyped_frontier_exploration_scheduled"] = False
    scheduler_state["last_focused_frontier"] = None
    records = repair_state.get("records")
    if isinstance(records, list):
        for record in records:
            if (
                isinstance(record, dict)
                and record.get("semantic_key") in reactivated_keys
            ):
                record["status"] = "active"
                record["mutation_families"] = []
        repair_state["active_count"] = sum(
            isinstance(record, Mapping) and record.get("status") == "active"
            for record in records
        )
        repair_state["dormant_count"] = sum(
            isinstance(record, Mapping) and record.get("status") == "dormant"
            for record in records
        )

    corrected = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
        reason_code="framework_single_turn_scheduler_checkpoint_repaired",
        owner="framework",
        stage="candidate_generation",
        scope="shared_run",
        repairable=True,
        progress_delta_ids=campaign.latest_disposition.progress_delta_ids,
    )
    ledger = campaign.measurement_ledger.charge_framework_blocked(
        no_work_run_id
    )
    no_work_report["campaign_causal_migration"] = {
        "schema_version": (
            "aworld.self_evolve.single_turn_no_work_migration.v1"
        ),
        "action": migration_action,
        "source_run_id": source_run_id,
        "no_work_run_id": no_work_run_id,
        "candidate_generation_iterations": 0,
        "candidate_generation_count": 0,
        "reactivated_frontier_keys": list(reactivated_keys),
        "candidate_reserve_granted": False,
        "measurement_retry_granted": False,
    }
    no_work_report["self_improvement_disposition"] = corrected.to_dict()
    raw_campaign = no_work_report.get("campaign")
    if isinstance(raw_campaign, dict):
        raw_campaign["framework_blocked_count"] = ledger.framework_blocked_count
        raw_campaign["max_cycles"] = (
            campaign.max_cycles
            + ledger.control_plane_run_count
            + int(campaign.repair_continuation_used)
        )
    controller.store.write_report(no_work_run_id, no_work_report)
    migrated = replace(
        campaign,
        status=SelfImprovementCampaignStatus.ACTIVE,
        measurement_ledger=ledger,
        latest_progress=self_improvement_progress(source_report),
        latest_disposition=corrected,
        latest_measurement_outcome=None,
        latest_report_path=str(
            controller.store.run_path(no_work_run_id) / "report.json"
        ),
        measurement_pending_run_id=None,
        measurement_pending_candidate_id=None,
        goal_handoff_path=None,
    )
    controller.store.write_campaign(migrated)
    return migrated


def _report_unobserved_support_timeout_candidates(
    report: Mapping[str, Any],
) -> dict[str, set[str]]:
    """Find screening timeouts rewritten as candidate support failures."""

    attribution = report.get("campaign_failure_attribution")
    if not (
        isinstance(attribution, Mapping)
        and attribution.get("code")
        == "candidate_replay_support_baseline_incompatible"
        and attribution.get("failure_owner") == "candidate"
    ):
        return {}
    affected: dict[str, set[str]] = {}
    stack: list[object] = [report.get("population")]
    inspected = 0
    while stack and inspected < 20_000:
        current = stack.pop()
        inspected += 1
        if isinstance(current, Mapping):
            attempts = current.get("attempts")
            if (
                current.get("screening_strategy") is not None
                and isinstance(attempts, list)
            ):
                for attempt in attempts:
                    if not isinstance(attempt, Mapping):
                        continue
                    candidate_id = attempt.get("candidate_id")
                    details = attempt.get("details")
                    if not (
                        isinstance(candidate_id, str)
                        and isinstance(details, Mapping)
                        and details.get("code")
                        == "candidate_replay_support_baseline_incompatible"
                        and details.get("candidate_intervention_required") is True
                        and details.get("candidate_intervention_observed")
                        is not True
                        and details.get("candidate_execution_observed") is False
                        and _legacy_framework_timeout(
                            details.get("baseline_failure")
                        )
                    ):
                        continue
                    case_ids = {
                        str(member.get("case_id"))
                        for member in details.get("failed_members", [])
                        if isinstance(member, Mapping)
                        and isinstance(member.get("case_id"), str)
                    }
                    control_identity = details.get("control_identity")
                    if (
                        not case_ids
                        and isinstance(control_identity, Mapping)
                        and isinstance(control_identity.get("case_id"), str)
                    ):
                        case_ids.add(str(control_identity["case_id"]))
                    for case_id in case_ids:
                        affected.setdefault(case_id, set()).add(candidate_id)
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    return {
        case_id: candidate_ids
        for case_id, candidate_ids in affected.items()
        if len(candidate_ids) >= 2
    }


def _legacy_framework_timeout(value: object) -> bool:
    return bool(
        isinstance(value, Mapping)
        and value.get("code") == "replay_member_phase_timeout"
        and (value.get("failure_owner") or value.get("owner", "framework"))
        == "framework"
    )


def _report_iteration_score_deltas(
    report: Mapping[str, Any],
) -> dict[str, float]:
    raw_iterations = report.get("iterations")
    if not isinstance(raw_iterations, list):
        return {}
    deltas: dict[str, float] = {}
    for iteration in raw_iterations:
        if not isinstance(iteration, Mapping):
            continue
        candidate_id = iteration.get("candidate_id")
        baseline = iteration.get("baseline_metrics")
        candidate = iteration.get("candidate_metrics")
        if not (
            isinstance(candidate_id, str)
            and candidate_id
            and isinstance(baseline, Mapping)
            and isinstance(candidate, Mapping)
        ):
            continue
        baseline_score = baseline.get("score")
        candidate_score = candidate.get("score")
        if not (
            isinstance(baseline_score, (int, float))
            and not isinstance(baseline_score, bool)
            and math.isfinite(float(baseline_score))
            and isinstance(candidate_score, (int, float))
            and not isinstance(candidate_score, bool)
            and math.isfinite(float(candidate_score))
        ):
            continue
        deltas[candidate_id] = float(candidate_score) - float(baseline_score)
    return deltas


def _report_has_unattested_mixed_projection_constraint(
    report: Mapping[str, Any],
    *,
    candidate_id: str,
) -> bool:
    raw_iterations = report.get("iterations")
    selected_metrics: Mapping[str, Any] | None = None
    if isinstance(raw_iterations, list):
        for iteration in raw_iterations:
            if not (
                isinstance(iteration, Mapping)
                and iteration.get("candidate_id") == candidate_id
            ):
                continue
            metrics = iteration.get("candidate_metrics")
            if isinstance(metrics, Mapping):
                selected_metrics = metrics
                break
    if selected_metrics is None or (
        selected_metrics.get("judge_artifact_read_budget_exhausted") is True
        and selected_metrics.get("judge_artifact_projection_incomplete") is True
    ):
        return False
    raw_gates = report.get("gate_results")
    if not isinstance(raw_gates, list):
        return False
    for gate in raw_gates:
        if not isinstance(gate, Mapping):
            continue
        details = gate.get("details")
        constraints = (
            details.get("evidence_repair_constraints")
            if isinstance(details, Mapping)
            else None
        )
        if not (
            gate.get("gate_name") == "evidence_quality"
            and gate.get("passed") is False
            and isinstance(constraints, list)
        ):
            continue
        has_framework_projection = any(
            isinstance(item, Mapping)
            and item.get("owner") == "framework"
            and item.get("failure_mode") == "projection_compacted"
            and item.get("source_layer") == "artifact_projection"
            and item.get("required_action") == "expand_bounded_projection"
            for item in constraints
        )
        has_candidate_constraint = any(
            isinstance(item, Mapping) and item.get("owner") == "candidate"
            for item in constraints
        )
        if has_framework_projection and has_candidate_constraint:
            return True
    return False


def persistent_campaign_request(request: Mapping[str, Any]) -> dict[str, Any]:
    payload = {
        str(key): _json_value(value)
        for key, value in request.items()
        if key not in _RUNTIME_ONLY_REQUEST_KEYS
        and key
        not in {
            "campaign_id",
            "campaign_cycle",
            "campaign_prior_run_ids",
            "campaign_scheduler_checkpoint_run_ids",
            "campaign_expected_target",
            "campaign_measurement_pending_run_id",
            "campaign_measurement_pending_candidate_id",
            "workspace_root",
        }
    }
    payload.setdefault("apply_policy", "proposal")
    return json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def validate_campaign_source_snapshot(
    campaign: SelfImprovementCampaign,
    *,
    workspace_root: str | Path,
) -> None:
    current = _source_snapshot(campaign.request, workspace_root=workspace_root)
    if current != campaign.source_snapshot:
        raise ValueError(
            f"campaign {campaign.campaign_id} source changed since it was created"
        )


def self_improvement_progress(report: Mapping[str, Any]) -> SelfImprovementProgress:
    events = _typed_failure_events(report)
    semantic_ids = tuple(
        sorted(
            {
                *(_event_identity(item) for item in events),
                *_recovery_frontier_identities(report),
            }
        )
    )
    constraint_ids = tuple(sorted(_constraint_identities(report)))
    passed_gates: set[str] = set()
    deepest = 0
    raw_gates = report.get("gate_results")
    if isinstance(raw_gates, list):
        for item in raw_gates:
            if not isinstance(item, Mapping) or item.get("passed") is not True:
                continue
            gate = str(item.get("gate_name") or "")
            if gate:
                passed_gates.add(gate)
                deepest = max(deepest, _STAGE_RANK.get(gate, 0))
    for event in events:
        deepest = max(deepest, _STAGE_RANK.get(str(event.get("stage") or ""), 0))
    raw_skill_evolution = report.get("skill_evolution")
    covered_capability_ids = (
        _string_tuple(raw_skill_evolution.get("covered_capability_ids", ()))
        if isinstance(raw_skill_evolution, Mapping)
        else ()
    )
    return SelfImprovementProgress(
        deepest_stage_rank=deepest,
        semantic_frontier_ids=semantic_ids,
        constraint_ids=constraint_ids,
        passed_gate_ids=tuple(passed_gates),
        candidate_quality=_candidate_quality_progress(report),
        measurement=_trusted_measurement_progress(report),
        covered_capability_ids=covered_capability_ids,
    )


def campaign_measurement_outcome_from_report(
    report: Mapping[str, Any],
) -> CampaignMeasurementOutcomeV2 | None:
    """Read only the explicit v2 artifact; legacy reports remain descriptive."""

    raw = report.get("campaign_measurement_outcome")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("campaign measurement outcome must be a mapping")
    return CampaignMeasurementOutcomeV2.from_dict(raw)


def _measurement_outcome_disposition(
    outcome: CampaignMeasurementOutcomeV2,
    *,
    progress_delta_ids: tuple[str, ...],
) -> SelfImprovementDisposition:
    projection = outcome.projection
    if projection is CampaignMeasurementProjection.SUCCEEDED:
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.COMPLETE,
            reason_code=outcome.reason_code,
            stage="measurement",
            progress_delta_ids=progress_delta_ids,
        )
    if projection is CampaignMeasurementProjection.CANDIDATE_REJECTED:
        negative = (
            outcome.improvement_outcome
            is CandidateImprovementOutcome.REGRESSION
        )
        candidate_repair_available = (
            outcome.improvement_outcome
            is not CandidateImprovementOutcome.REGRESSION
            and outcome.continuation_available
        )
        return SelfImprovementDisposition(
            kind=(
                SelfImprovementDispositionKind.CONTINUE_CANDIDATE
                if candidate_repair_available
                else SelfImprovementDispositionKind.STOP_NEGATIVE_EFFECT
                if negative
                else SelfImprovementDispositionKind.STOP_NO_EFFECT
            ),
            reason_code=outcome.reason_code,
            owner="candidate",
            stage="measurement",
            scope="candidate",
            repairable=candidate_repair_available,
            progress_delta_ids=progress_delta_ids,
        )
    if projection is CampaignMeasurementProjection.MEASUREMENT_INCOMPLETE:
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.COLLECT_MORE_EVIDENCE,
            reason_code=outcome.reason_code,
            owner="measurement_scheduler",
            stage="measurement",
            scope="shared_run",
            repairable=True,
            progress_delta_ids=progress_delta_ids,
        )
    if projection is CampaignMeasurementProjection.MEASUREMENT_INVALID:
        return SelfImprovementDisposition(
            kind=(
                SelfImprovementDispositionKind.REPAIR_MEASUREMENT
                if outcome.continuation_available
                else SelfImprovementDispositionKind.PAUSE_OPERATOR
            ),
            reason_code=outcome.reason_code,
            owner="evaluation_harness",
            stage="measurement",
            scope="shared_run",
            repairable=outcome.continuation_available,
            progress_delta_ids=progress_delta_ids,
        )
    if projection is CampaignMeasurementProjection.FRAMEWORK_BLOCKED:
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.HANDOFF_GOAL,
            reason_code=outcome.reason_code,
            owner="framework",
            stage="measurement",
            scope="shared_run",
            repairable=True,
            progress_delta_ids=progress_delta_ids,
        )
    return SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.EXHAUSTED,
        reason_code=outcome.reason_code,
        owner="measurement_scheduler",
        stage="measurement",
        scope="shared_run",
        repairable=False,
        progress_delta_ids=progress_delta_ids,
    )


def _measurement_policy_disposition(
    report: Mapping[str, Any],
    *,
    progress_delta_ids: tuple[str, ...],
) -> SelfImprovementDisposition | None:
    raw = report.get("measurement")
    required_gate_failed = any(
        isinstance(item, Mapping)
        and item.get("gate_name") == "trusted_improvement_measurement"
        and item.get("passed") is False
        for item in (
            report.get("gate_results")
            if isinstance(report.get("gate_results"), list)
            else ()
        )
    )
    if not isinstance(raw, Mapping):
        if not required_gate_failed:
            return None
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.REPAIR_MEASUREMENT,
            reason_code="trusted_measurement_artifact_missing",
            owner="evaluation_harness",
            stage="measurement",
            scope="shared_run",
            repairable=True,
            progress_delta_ids=progress_delta_ids,
        )
    mode = str(raw.get("mode") or "off")
    # Advisory measurement is diagnostic only.  It may be surfaced in the
    # report, but it must never replace the legacy release/Campaign authority.
    # Required mode is the only mode allowed to steer campaign disposition.
    if mode != "required" or raw.get("promotion_eligible") is True:
        return None
    next_action = str(raw.get("next_action") or "repair_measurement")
    kinds = {
        "continue_candidate_repair": (
            SelfImprovementDispositionKind.CONTINUE_CANDIDATE,
            "trusted_measurement_candidate_repair",
            "candidate",
            True,
        ),
        "collect_more_evidence": (
            SelfImprovementDispositionKind.COLLECT_MORE_EVIDENCE,
            "trusted_measurement_needs_more_evidence",
            "evaluation_harness",
            True,
        ),
        "repair_measurement": (
            SelfImprovementDispositionKind.REPAIR_MEASUREMENT,
            "trusted_measurement_invalid",
            "evaluation_harness",
            True,
        ),
        "repair_framework": (
            SelfImprovementDispositionKind.HANDOFF_GOAL,
            "typed_framework_or_shared_blocker",
            "framework",
            True,
        ),
        "switch_generator": (
            SelfImprovementDispositionKind.SWITCH_GENERATOR,
            "trusted_measurement_switch_generator",
            "framework",
            True,
        ),
        "switch_scheduler": (
            SelfImprovementDispositionKind.SWITCH_SCHEDULER,
            "trusted_measurement_switch_scheduler",
            "framework",
            True,
        ),
        "stop_no_effect": (
            SelfImprovementDispositionKind.STOP_NO_EFFECT,
            "trusted_measurement_no_effect",
            "candidate",
            False,
        ),
        "stop_negative_effect": (
            SelfImprovementDispositionKind.STOP_NEGATIVE_EFFECT,
            "trusted_measurement_negative_effect",
            "candidate",
            False,
        ),
        "pause_operator": (
            SelfImprovementDispositionKind.PAUSE_OPERATOR,
            "trusted_measurement_operator_review",
            "operator",
            False,
        ),
    }
    kind, reason_code, owner, repairable = kinds.get(
        next_action,
        kinds["repair_measurement"],
    )
    attribution_ref = raw.get("attribution_report_path")
    return SelfImprovementDisposition(
        kind=kind,
        reason_code=reason_code,
        owner=owner,
        stage="measurement",
        scope="shared_run" if owner != "candidate" else "candidate",
        repairable=repairable,
        progress_delta_ids=progress_delta_ids,
        diagnostic_refs=(
            (str(attribution_ref),)
            if isinstance(attribution_ref, str) and attribution_ref
            else ()
        ),
    )


def _report_requests_paired_replay_continuation(
    report: Mapping[str, Any],
) -> bool:
    """Recognize the typed, safe continuation contract for progressive replay."""

    return any(
        isinstance(attribution, Mapping)
        and attribution.get("code") == "replay_total_timeout"
        and attribution.get("failure_class") == "measurement"
        and attribution.get("failure_owner")
        in {"framework", "infrastructure", "evaluation_harness"}
        and attribution.get("failure_scope") == "shared_run"
        and attribution.get("repairable") is True
        and attribution.get("resume_safe") is True
        and attribution.get("next_action") == "continue_measurement"
        and isinstance(attribution.get("resume_candidate_id"), str)
        and bool(attribution.get("resume_candidate_id"))
        and isinstance(
            attribution.get("resume_candidate_package_fingerprint"), str
        )
        for attribution in (
            report.get("rejection_attribution"),
            report.get("campaign_failure_attribution"),
        )
    )


def _report_requests_paired_replay_retry(
    report: Mapping[str, Any],
) -> bool:
    """Recognize bounded retry of a persisted framework member timeout."""

    checkpoint = report.get("paired_replay_resume_checkpoint")
    if not isinstance(checkpoint, Mapping):
        return False
    return any(
        isinstance(attribution, Mapping)
        and attribution.get("code") == "replay_member_phase_timeout"
        and attribution.get("failure_owner") == "framework"
        and attribution.get("failure_scope") == "member"
        and attribution.get("repairable") is True
        for attribution in (
            report.get("rejection_attribution"),
            report.get("campaign_failure_attribution"),
        )
    )


def derive_self_improvement_disposition(
    report: Mapping[str, Any],
    previous_progress: SelfImprovementProgress | None = None,
) -> SelfImprovementDisposition:
    status = str(report.get("status") or "")
    progress = self_improvement_progress(report)
    delta = progress.delta_from(previous_progress)
    candidate_prerequisite_blocked = bool(
        _report_has_repairable_candidate_prerequisite_failure(report)
        and not _report_has_authoritative_measurement_observation(report)
    )
    skill_evolution = report.get("skill_evolution")
    if (
        isinstance(skill_evolution, Mapping)
        and skill_evolution.get("coverage_satisfied") is not True
    ):
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.CONTINUE_CANDIDATE,
            reason_code="skill_contract_coverage_incomplete",
            owner="candidate",
            stage="held_out_verification",
            scope="candidate",
            repairable=True,
            progress_delta_ids=delta,
        )
    measurement_outcome = campaign_measurement_outcome_from_report(report)
    if measurement_outcome is not None and not candidate_prerequisite_blocked:
        return _measurement_outcome_disposition(
            measurement_outcome,
            progress_delta_ids=delta,
        )
    measurement_disposition = (
        None
        if candidate_prerequisite_blocked
        else _measurement_policy_disposition(
            report,
            progress_delta_ids=delta,
        )
    )
    if measurement_disposition is not None:
        return measurement_disposition
    if status == "succeeded":
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.COMPLETE,
            reason_code="verified_run_succeeded",
            progress_delta_ids=delta,
        )

    target = report.get("target")
    target_selection = report.get("target_selection")
    if (
        isinstance(target, Mapping)
        and target.get("target_type") == "no_target"
        and isinstance(target_selection, Mapping)
        and target_selection.get("failure_category") == "no_target"
    ):
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.PAUSE_OPERATOR,
            reason_code="target_selection_no_target",
            owner="task",
            stage="target_selection",
            scope="shared_run",
            repairable=False,
            progress_delta_ids=delta,
        )

    events = _typed_failure_events(report)
    terminal = report.get("terminal_cause")
    attribution = report.get("rejection_attribution")
    campaign_attribution = report.get("campaign_failure_attribution")
    if _report_requests_paired_replay_continuation(report):
        typed_attribution = next(
            item
            for item in (attribution, campaign_attribution)
            if isinstance(item, Mapping)
            and item.get("code") == "replay_total_timeout"
        )
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.COLLECT_MORE_EVIDENCE,
            reason_code="replay_total_timeout",
            owner="evaluation_harness",
            stage=_optional_string(
                typed_attribution.get("failure_stage") or "candidate_replay"
            ),
            scope="shared_run",
            repairable=True,
            progress_delta_ids=delta,
            diagnostic_refs=_string_tuple(
                typed_attribution.get("diagnostic_refs")
            ),
        )
    if _report_requests_paired_replay_retry(report):
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.REPAIR_MEASUREMENT,
            reason_code="replay_member_phase_timeout",
            owner="evaluation_harness",
            stage="evaluation",
            scope="shared_run",
            repairable=True,
            progress_delta_ids=delta,
            diagnostic_refs=_string_tuple(
                attribution.get("diagnostic_refs")
                if isinstance(attribution, Mapping)
                else ()
            ),
        )
    shared_measurement = next(
        (
            item
            for item in (attribution, campaign_attribution)
            if isinstance(item, Mapping)
            and item.get("failure_class") == "measurement"
            and item.get("failure_owner")
            in {"framework", "infrastructure", "evaluation_harness"}
            and item.get("failure_scope") == "shared_run"
        ),
        None,
    )
    if shared_measurement is not None:
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.REPAIR_MEASUREMENT,
            reason_code="shared_measurement_control_invalid",
            owner="evaluation_harness",
            stage=_optional_string(
                shared_measurement.get("failure_stage")
                or shared_measurement.get("primary_gate")
                or "measurement"
            ),
            scope="shared_run",
            repairable=True,
            progress_delta_ids=delta,
            diagnostic_refs=_string_tuple(
                shared_measurement.get("diagnostic_refs")
            ),
        )
    if (
        isinstance(attribution, Mapping)
        and attribution.get("scheduler_reason_code") == "focused_budget_denied"
    ):
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.CONTINUE_CAMPAIGN,
            reason_code="cycle_focused_budget_denied",
            owner="budget",
            stage="candidate_generation",
            scope="shared_run",
            repairable=False,
            progress_delta_ids=delta,
        )
    if (
        isinstance(attribution, Mapping)
        and attribution.get("failure_class") == "candidate"
        and attribution.get("scheduler_stop") is True
        and attribution.get("scheduler_reason_code")
        == "repair_frontier_stalled"
        and (
            attribution.get("primary_gate")
            in {"candidate_generation", "no_candidate"}
            or attribution.get("code")
            == "candidate_repair_frontier_stalled"
        )
    ):
        # The scheduler has already consumed the typed repair frontier and
        # found no eligible mutation family.  A newly observed diagnostic or
        # recovery identity must not reopen another Campaign cycle: the next
        # run would restore the same scheduler state and terminate before
        # candidate generation.  Treat the scheduler's terminal decision as
        # the authoritative candidate-level exhaustion signal, including for
        # reports produced before a causal failure event was attached to the
        # candidate-generation gate.
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="candidate_repair_frontier_stalled",
            owner="candidate",
            stage=_optional_string(
                attribution.get("primary_gate") or "candidate_generation"
            ),
            scope="candidate",
            repairable=False,
            progress_delta_ids=delta,
        )
    if (
        isinstance(attribution, Mapping)
        and attribution.get("scheduler_stop") is True
        and attribution.get("scheduler_reason_code") == "shared_run_blocked"
    ):
        # Older reports attributed the empty candidate-generation gate to a
        # candidate even though the scheduler stopped on a framework-owned
        # shared blocker.  The typed scheduler decision is authoritative and
        # must produce a Goal handoff rather than a legacy operator pause.
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.HANDOFF_GOAL,
            reason_code="typed_framework_or_shared_blocker",
            owner="framework",
            stage=_optional_string(
                attribution.get("primary_gate") or "candidate_generation"
            ),
            scope="shared_run",
            repairable=False,
            progress_delta_ids=delta,
        )
    primary_failure: Mapping[str, Any] | None = None
    if (
        isinstance(campaign_attribution, Mapping)
        and campaign_attribution.get("failure_class")
        in {"infrastructure", "framework", "task"}
        and campaign_attribution.get("failure_scope") == "shared_run"
    ):
        # The runner's aggregate attribution is the authority for a shared-run
        # blocker.  A derived candidate gate (for example replay confidence on
        # a different member) must not turn system work into another mutation
        # cycle.
        primary_failure = campaign_attribution
    elif (
        isinstance(terminal, Mapping)
        and terminal.get("failure_class")
        in {"infrastructure", "framework", "task"}
    ):
        primary_failure = terminal
    elif (
        isinstance(attribution, Mapping)
        and attribution.get("failure_class")
        in {"infrastructure", "framework", "task"}
    ):
        primary_failure = attribution

    actionable_candidate = next(
        (
            item
            for item in events
            if item.get("owner") == "candidate"
            and item.get("repairable") is True
        ),
        None,
    )
    if (
        primary_failure is not None
        and primary_failure.get("failure_class") == "framework"
        and primary_failure.get("code") == "score_improvement_inconclusive"
        and actionable_candidate is not None
    ):
        # Score uncertainty describes missing verification confidence; it is
        # not a shared blocker while the same candidate has a typed repairable
        # failure. Continue that causal frontier instead of handing framework
        # work to an operator/Goal.
        primary_failure = None

    if primary_failure is not None:
        primary_owner = str(primary_failure["failure_class"])
        primary_code = str(primary_failure.get("code") or "")
        primary_event = next(
            (
                item
                for item in events
                if item.get("owner") == primary_owner
                and (not primary_code or item.get("code") == primary_code)
            ),
            None,
        )
        if primary_event is None and not primary_code:
            primary_event = next(
                (item for item in events if item.get("owner") == primary_owner),
                None,
            )
        if primary_owner == "infrastructure":
            retryable = (
                primary_failure.get("retryable") is True
                or primary_failure.get("repairable") is True
                or (
                    primary_event is not None
                    and primary_event.get("repairable") is True
                )
            )
            if primary_event is not None:
                return _event_disposition(
                    (
                        SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE
                        if retryable
                        else SelfImprovementDispositionKind.PAUSE_OPERATOR
                    ),
                    (
                        "typed_infrastructure_failure"
                        if retryable
                        else "typed_infrastructure_failure_not_retryable"
                    ),
                    primary_event,
                    delta,
                )
            return SelfImprovementDisposition(
                kind=(
                    SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE
                    if retryable
                    else SelfImprovementDispositionKind.PAUSE_OPERATOR
                ),
                reason_code=(
                    "typed_infrastructure_failure"
                    if retryable
                    else "typed_infrastructure_failure_not_retryable"
                ),
                owner="infrastructure",
                stage=_optional_string(
                    primary_failure.get("stage")
                    or primary_failure.get("primary_gate")
                ),
                scope="shared_run",
                repairable=retryable,
                progress_delta_ids=delta,
                diagnostic_refs=_string_tuple(
                    primary_failure.get("diagnostic_refs")
                ),
            )
        if primary_owner == "framework":
            if primary_event is not None:
                return _event_disposition(
                    SelfImprovementDispositionKind.HANDOFF_GOAL,
                    "typed_framework_or_shared_blocker",
                    primary_event,
                    delta,
                )
            return SelfImprovementDisposition(
                kind=SelfImprovementDispositionKind.HANDOFF_GOAL,
                reason_code="typed_framework_or_shared_blocker",
                owner="framework",
                stage=_optional_string(
                    primary_failure.get("stage")
                    or primary_failure.get("primary_gate")
                ),
                scope="shared_run",
                repairable=False,
                progress_delta_ids=delta,
                diagnostic_refs=_string_tuple(
                    primary_failure.get("diagnostic_refs")
                ),
            )
        if primary_event is not None:
            return _event_disposition(
                SelfImprovementDispositionKind.PAUSE_OPERATOR,
                "typed_task_failure_not_repairable",
                primary_event,
                delta,
            )
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.PAUSE_OPERATOR,
            reason_code="typed_task_failure_not_repairable",
            owner="task",
            stage=_optional_string(
                primary_failure.get("stage")
                or primary_failure.get("primary_gate")
            ),
            scope="shared_run",
            repairable=False,
            progress_delta_ids=delta,
            diagnostic_refs=_string_tuple(
                primary_failure.get("diagnostic_refs")
            ),
        )

    candidate = actionable_candidate
    if candidate is not None:
        if candidate.get("code") == "evaluation_support_bootstrap_only":
            return _event_disposition(
                (
                    SelfImprovementDispositionKind.CONTINUE_CANDIDATE
                    if delta
                    else SelfImprovementDispositionKind.EXHAUSTED
                ),
                (
                    "evaluation_support_composition_required"
                    if delta
                    else "evaluation_support_composition_stalled"
                ),
                candidate,
                delta,
            )
        if delta:
            return _event_disposition(
                SelfImprovementDispositionKind.CONTINUE_CANDIDATE,
                "candidate_repair_frontier_progressed",
                candidate,
                delta,
            )
        return _event_disposition(
            SelfImprovementDispositionKind.EXHAUSTED,
            "candidate_repair_frontier_stalled",
            candidate,
            delta,
        )

    if (
        isinstance(terminal, Mapping)
        and terminal.get("failure_class") == "infrastructure"
        and terminal.get("retryable") is True
    ):
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE,
            reason_code="typed_infrastructure_failure",
            owner="infrastructure",
            stage=_optional_string(terminal.get("stage")),
            scope="shared_run",
            repairable=True,
            progress_delta_ids=delta,
        )
    infrastructure = next(
        (
            item
            for item in events
            if item.get("owner") == "infrastructure"
            and item.get("repairable") is True
        ),
        None,
    )
    if infrastructure is not None:
        return _event_disposition(
            SelfImprovementDispositionKind.RETRY_INFRASTRUCTURE,
            "typed_infrastructure_failure",
            infrastructure,
            delta,
        )
    framework = next(
        (
            item
            for item in events
            if item.get("owner") == "framework"
            or (
                item.get("scope") == "shared_run"
                and item.get("owner") != "infrastructure"
            )
        ),
        None,
    )
    if framework is not None:
        return _event_disposition(
            SelfImprovementDispositionKind.HANDOFF_GOAL,
            "typed_framework_or_shared_blocker",
            framework,
            delta,
        )
    non_retryable_infrastructure = next(
        (item for item in events if item.get("owner") == "infrastructure"),
        None,
    )
    if (
        non_retryable_infrastructure is not None
        or (
            isinstance(terminal, Mapping)
            and terminal.get("failure_class") == "infrastructure"
        )
    ):
        if non_retryable_infrastructure is not None:
            return _event_disposition(
                SelfImprovementDispositionKind.PAUSE_OPERATOR,
                "typed_infrastructure_failure_not_retryable",
                non_retryable_infrastructure,
                delta,
            )
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.PAUSE_OPERATOR,
            reason_code="typed_infrastructure_failure_not_retryable",
            owner="infrastructure",
            stage=_optional_string(terminal.get("stage")),
            scope="shared_run",
            repairable=False,
            progress_delta_ids=delta,
        )
    task = next((item for item in events if item.get("owner") == "task"), None)
    if task is not None:
        return _event_disposition(
            SelfImprovementDispositionKind.PAUSE_OPERATOR,
            "typed_task_failure_not_repairable",
            task,
            delta,
        )
    non_repairable_candidate = next(
        (item for item in events if item.get("owner") == "candidate"),
        None,
    )
    if non_repairable_candidate is not None:
        if (
            non_repairable_candidate.get("code")
            == "candidate_generation_policy_frontier_stalled"
        ):
            return _event_disposition(
                SelfImprovementDispositionKind.EXHAUSTED,
                "candidate_generation_policy_frontier_stalled",
                non_repairable_candidate,
                delta,
            )
        return _event_disposition(
            SelfImprovementDispositionKind.EXHAUSTED,
            "candidate_failure_not_repairable",
            non_repairable_candidate,
            delta,
        )

    if isinstance(attribution, Mapping) and attribution.get("duplicate_only") is True:
        return SelfImprovementDisposition(
            kind=SelfImprovementDispositionKind.EXHAUSTED,
            reason_code="semantic_duplicate_frontier_exhausted",
            owner="candidate",
            repairable=False,
            progress_delta_ids=delta,
        )
    return SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.PAUSE_OPERATOR,
        reason_code="legacy_report_missing_typed_disposition",
        progress_delta_ids=delta,
    )


def campaign_usage_from_report(report: Mapping[str, Any]) -> CampaignUsage:
    budget = report.get("budget")
    if not isinstance(budget, Mapping):
        raise ValueError("campaign continuation requires a typed budget report")
    ledger = budget.get("ledger")
    spent = (
        ledger.get("spent_by_stage")
        if isinstance(ledger, Mapping)
        else budget.get("spent_by_stage")
    )
    if not isinstance(spent, Mapping):
        raise ValueError("campaign budget report lacks spent_by_stage")
    usage = CampaignUsage()
    for value in spent.values():
        if not isinstance(value, Mapping):
            raise ValueError("campaign stage usage must be a mapping")
        usage = usage + CampaignUsage.from_dict(value)
    return usage


def build_goal_handoff(
    campaign: SelfImprovementCampaign,
    report: Mapping[str, Any],
) -> dict[str, object]:
    disposition = campaign.latest_disposition
    if disposition is None or disposition.kind is not SelfImprovementDispositionKind.HANDOFF_GOAL:
        raise ValueError("goal handoff requires a framework/shared disposition")
    diagnostic_refs = list(disposition.diagnostic_refs)
    if (
        isinstance(campaign.latest_report_path, str)
        and campaign.latest_report_path
        and campaign.latest_report_path not in diagnostic_refs
    ):
        diagnostic_refs.append(campaign.latest_report_path)
    return {
        "schema_version": "aworld.self_evolve.goal_handoff.v1",
        "campaign_id": campaign.campaign_id,
        "campaign_status": campaign.status.value,
        "objective": (
            "Resolve the typed self-evolve framework/shared blocker for campaign "
            f"{campaign.campaign_id}, verify the framework change, then resume the campaign."
        ),
        "latest_run_id": campaign.run_ids[-1],
        "latest_report_path": campaign.latest_report_path,
        "disposition": disposition.to_dict(),
        "diagnostic_refs": diagnostic_refs[:16],
        "semantic_frontier_ids": list(
            campaign.latest_progress.semantic_frontier_ids
            if campaign.latest_progress is not None
            else ()
        )[:32],
        "constraint_ids": list(
            campaign.latest_progress.constraint_ids
            if campaign.latest_progress is not None
            else ()
        )[:32],
        "next_action": f"aworld-cli optimize --resume-campaign {campaign.campaign_id}",
    }


def _default_run_once(**request: Any) -> Mapping[str, Any]:
    import aworld.self_evolve as self_evolve

    return self_evolve.optimize_from_cli_request(**request)


def _limit_campaign(
    campaign: SelfImprovementCampaign,
    *,
    reason_code: str,
) -> SelfImprovementCampaign:
    disposition = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.EXHAUSTED,
        reason_code=reason_code,
        owner=(campaign.latest_disposition.owner if campaign.latest_disposition else None),
        stage=(campaign.latest_disposition.stage if campaign.latest_disposition else None),
        scope=(campaign.latest_disposition.scope if campaign.latest_disposition else None),
    )
    return replace(
        campaign,
        status=SelfImprovementCampaignStatus.BUDGET_LIMITED,
        latest_disposition=disposition,
    )


def _exhaust_campaign(
    campaign: SelfImprovementCampaign,
    *,
    reason_code: str,
) -> SelfImprovementCampaign:
    disposition = SelfImprovementDisposition(
        kind=SelfImprovementDispositionKind.EXHAUSTED,
        reason_code=reason_code,
        owner=(campaign.latest_disposition.owner if campaign.latest_disposition else None),
        stage=(campaign.latest_disposition.stage if campaign.latest_disposition else None),
        scope=(campaign.latest_disposition.scope if campaign.latest_disposition else None),
    )
    return replace(
        campaign,
        status=SelfImprovementCampaignStatus.EXHAUSTED,
        latest_disposition=disposition,
    )


def _status_for_disposition(
    disposition: SelfImprovementDisposition,
) -> SelfImprovementCampaignStatus:
    if disposition.kind is SelfImprovementDispositionKind.COMPLETE:
        return SelfImprovementCampaignStatus.COMPLETE
    if disposition.continuable:
        return SelfImprovementCampaignStatus.ACTIVE
    if disposition.kind is SelfImprovementDispositionKind.EXHAUSTED:
        if disposition.reason_code in {
            "campaign_cumulative_budget_exhausted",
            "campaign_cycle_budget_exhausted",
            "campaign_infrastructure_retry_budget_exhausted",
            "campaign_usage_telemetry_missing",
        }:
            return SelfImprovementCampaignStatus.BUDGET_LIMITED
        return SelfImprovementCampaignStatus.EXHAUSTED
    return SelfImprovementCampaignStatus.PAUSED


def _campaign_authoritative_candidate_limit(
    campaign: SelfImprovementCampaign,
) -> int:
    return _positive_int(
        campaign.request.get("max_full_evaluation_candidates") or 3,
        "max_full_evaluation_candidates",
    )


def _campaign_effective_max_cycles(campaign: SelfImprovementCampaign) -> int:
    return (
        campaign.max_cycles
        + campaign.measurement_ledger.control_plane_run_count
        + int(campaign.repair_continuation_used)
    )


def _campaign_candidate_cycle_count(
    campaign: SelfImprovementCampaign,
) -> int:
    """Return mutation/evaluation cycles, excluding invalid measurement runs."""

    return max(
        0,
        campaign.cycle_index - campaign.measurement_ledger.control_plane_run_count,
    )


def _campaign_effective_authoritative_candidate_limit(
    campaign: SelfImprovementCampaign,
) -> int:
    return _campaign_authoritative_candidate_limit(campaign) + int(
        campaign.repair_continuation_used
    )


def _campaign_limit_axes(
    campaign: SelfImprovementCampaign,
) -> tuple[str, ...]:
    axes: list[str] = []
    if _campaign_candidate_cycle_count(campaign) >= (
        campaign.max_cycles + int(campaign.repair_continuation_used)
    ):
        axes.append("cycle")
    if (
        campaign.cumulative_authoritative_candidates
        >= _campaign_effective_authoritative_candidate_limit(campaign)
    ):
        axes.append("authoritative_candidate")
    return tuple(axes)


def _campaign_exhaustion_axes(
    campaign: SelfImprovementCampaign,
) -> tuple[str, ...]:
    if campaign.status is not SelfImprovementCampaignStatus.EXHAUSTED:
        return ()
    return _campaign_limit_axes(campaign)


def _campaign_exhaustion_reason(campaign: SelfImprovementCampaign) -> str:
    axes = _campaign_limit_axes(campaign)
    if axes == ("cycle", "authoritative_candidate"):
        return "campaign_cycle_and_authoritative_frontier_exhausted"
    if axes == ("authoritative_candidate",):
        return "campaign_authoritative_frontier_exhausted"
    return "campaign_cycle_limit_reached"


def _report_candidate_counterexample_fingerprints(
    report: Mapping[str, Any],
) -> frozenset[str]:
    fingerprints: set[str] = set()
    pending: list[tuple[object, int]] = [(report.get("gate_results"), 0)]
    visited = 0
    while pending and visited < 4096:
        current, depth = pending.pop()
        visited += 1
        if depth > 12:
            continue
        if isinstance(current, Mapping):
            counterexample_id = current.get("counterexample_id")
            counterexample_schema = current.get("schema_version")
            if (
                isinstance(counterexample_schema, str)
                and counterexample_schema.startswith(
                    "aworld.self_evolve."
                )
                and "counterexample" in counterexample_schema
                and isinstance(counterexample_id, str)
                and counterexample_id
            ):
                fingerprints.add(
                    hashlib.sha256(counterexample_id.encode("utf-8")).hexdigest()
                )
                continue
            normalized = normalize_counterexample(current)
            if normalized is not None and normalized.get("owner") == "candidate":
                fingerprints.add(
                    hashlib.sha256(
                        json.dumps(
                            normalized,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    ).hexdigest()
                )
                continue
            pending.extend((value, depth + 1) for value in current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend((value, depth + 1) for value in current[:256])
    return frozenset(fingerprints)


def _report_has_new_candidate_counterexample(
    report: Mapping[str, Any],
    *,
    prior_reports: Iterable[Mapping[str, Any]],
) -> bool:
    current = _report_candidate_counterexample_fingerprints(report)
    if not current:
        return False
    prior: set[str] = set()
    for prior_report in prior_reports:
        prior.update(_report_candidate_counterexample_fingerprints(prior_report))
    return bool(current - prior)


def _report_has_new_candidate_repair_evidence(
    report: Mapping[str, Any],
    *,
    prior_reports: Iterable[Mapping[str, Any]],
) -> bool:
    """Recognize replay counterexamples and typed release constraints alike."""

    prior_reports = tuple(prior_reports)
    if _report_has_new_candidate_counterexample(
        report,
        prior_reports=prior_reports,
    ):
        return True
    current_constraints = _candidate_evidence_repair_constraint_identities(
        report
    )
    if not current_constraints:
        return False
    prior_constraints: set[str] = set()
    for prior_report in prior_reports:
        prior_constraints.update(
            _candidate_evidence_repair_constraint_identities(prior_report)
        )
    return bool(current_constraints - prior_constraints)


def _candidate_evidence_repair_constraint_identities(
    report: Mapping[str, Any],
) -> set[str]:
    identities: set[str] = set()
    for item in _walk_mappings(report):
        if (
            item.get("schema_version")
            != "aworld.self_evolve.evidence_repair_constraint.v1"
            or item.get("owner") != "candidate"
        ):
            continue
        digest = item.get("constraint_identity_digest")
        if isinstance(digest, str) and digest:
            identities.add(digest.removeprefix("sha256:"))
    return identities


def _report_authoritative_candidate_count(report: Mapping[str, Any]) -> int:
    funnel = report.get("verification_funnel")
    explicit_attempt_count = (
        funnel.get("authoritative_candidate_attempt_count")
        if isinstance(funnel, Mapping)
        else None
    )
    explicit_candidate_ids = (
        funnel.get("authoritative_candidate_ids")
        if isinstance(funnel, Mapping)
        else None
    )
    authoritative_case_observations = (
        funnel.get("authoritative_case_observations")
        if isinstance(funnel, Mapping)
        else None
    )
    has_explicit_authoritative_evidence = bool(
        (
            isinstance(explicit_candidate_ids, list)
            and any(
                isinstance(candidate_id, str) and candidate_id
                for candidate_id in explicit_candidate_ids
            )
        )
        or (
            isinstance(authoritative_case_observations, Mapping)
            and bool(authoritative_case_observations)
        )
    )
    if (
        _report_has_repairable_candidate_prerequisite_failure(report)
        and not _report_has_authoritative_measurement_observation(report)
        and not has_explicit_authoritative_evidence
        and (
            isinstance(report.get("measurement"), Mapping)
            or explicit_attempt_count == 0
        )
    ):
        # A candidate rejected before rollout/evaluation has no authoritative
        # treatment observation.  A generic required-measurement gate may also
        # be present because release is fail-closed, but it must not turn the
        # failed prerequisite into an authoritative experiment attempt.
        return 0
    measurement_outcome = campaign_measurement_outcome_from_report(report)
    if (
        measurement_outcome is not None
        and measurement_outcome.execution_status
        is not MeasurementExecutionStatus.COMPLETED
    ):
        # Work may have consumed rollout time, but it did not produce one
        # authoritative candidate conclusion.
        return 0
    if not isinstance(funnel, Mapping):
        return 0
    value = funnel.get("authoritative_candidate_count")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    count = max(0, int(value))
    attribution = report.get("campaign_failure_attribution")
    if (
        count > 0
        and isinstance(attribution, Mapping)
        and attribution.get("failure_class") == "measurement"
        and attribution.get("failure_owner") == "framework"
        and attribution.get("failure_scope") == "shared_run"
        and attribution.get("repairable") is True
    ):
        # The invalid experiment consumed wall time, but did not release an
        # authoritative conclusion about its candidate.
        count -= 1
    return count


def _campaign_pending_candidate_was_authoritative(
    store: Any,
    *,
    campaign: SelfImprovementCampaign,
) -> bool:
    """Return whether the frozen measurement candidate was already charged.

    A resumed paired replay completes the same immutable candidate experiment;
    it does not create another authoritative candidate.  Prefer the explicit
    funnel identity ledger and admit the legacy fallback only when the report
    has exactly one possible candidate identity.
    """

    pending_candidate_id = campaign.measurement_pending_candidate_id
    if pending_candidate_id is None:
        return False
    for run_id in campaign.run_ids:
        try:
            report = store.read_report(run_id)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if _report_authoritative_candidate_count(report) <= 0:
            continue
        funnel = report.get("verification_funnel")
        raw_authoritative_ids = (
            funnel.get("authoritative_candidate_ids")
            if isinstance(funnel, Mapping)
            else None
        )
        authoritative_ids = {
            item
            for item in (
                raw_authoritative_ids
                if isinstance(raw_authoritative_ids, (list, tuple))
                else ()
            )
            if isinstance(item, str) and item
        }
        if pending_candidate_id in authoritative_ids:
            return True
        if authoritative_ids:
            continue
        raw_candidate_ids = report.get("candidate_ids")
        candidate_ids = {
            item
            for item in (
                raw_candidate_ids
                if isinstance(raw_candidate_ids, (list, tuple))
                else ()
            )
            if isinstance(item, str) and item
        }
        if candidate_ids == {pending_candidate_id}:
            return True
    return False


def _report_has_authoritative_measurement_observation(
    report: Mapping[str, Any],
) -> bool:
    raw = report.get("measurement")
    if not isinstance(raw, Mapping):
        return False
    comparable_pair_count = raw.get("comparable_pair_count")
    if (
        not isinstance(comparable_pair_count, bool)
        and isinstance(comparable_pair_count, (int, float))
        and comparable_pair_count > 0
    ):
        return True
    if raw.get("validity_status") in {"valid", "valid_limited"}:
        return True
    return str(raw.get("effect_direction") or "unmeasured") not in {
        "",
        "unmeasured",
    }


def _report_has_repairable_candidate_prerequisite_failure(
    report: Mapping[str, Any],
) -> bool:
    """Return whether candidate-owned admission failed before measurement.

    Required measurement gates are derived release guards.  They cannot own
    the causal disposition when capability compilation or deterministic
    preflight already rejected the candidate package.
    """

    raw_gates = report.get("gate_results")
    if isinstance(raw_gates, list) and any(
        causal_admission_prerequisite_blocker(
            gate_name=str(item.get("gate_name") or ""),
            passed=item.get("passed") is True,
            details=(
                item.get("details")
                if isinstance(item.get("details"), Mapping)
                else None
            ),
        )
        for item in raw_gates
        if isinstance(item, Mapping)
    ):
        return True

    prerequisite_stages = {
        "candidate_generation",
        "candidate_repair_conformance",
        "candidate_screening",
        "capability_compile",
        "capability_preflight",
    }
    for event in _typed_failure_events(report):
        if (
            event.get("owner") == "candidate"
            and event.get("scope") == "candidate"
            and event.get("repairable") is True
            and str(event.get("stage") or "") in prerequisite_stages
        ):
            return True
    for key in ("rejection_attribution", "campaign_failure_attribution"):
        attribution = report.get(key)
        if not isinstance(attribution, Mapping):
            continue
        gate_name = str(attribution.get("primary_gate") or "")
        stage = str(
            attribution.get("failure_stage")
            or attribution.get("stage")
            or ""
        )
        if (
            attribution.get("failure_class") == "candidate"
            and attribution.get("failure_owner", "candidate") == "candidate"
            and attribution.get("repairable") is True
            and (
                gate_name.startswith("candidate_capability_")
                or gate_name == "candidate_repair_conformance"
                or stage in prerequisite_stages
            )
        ):
            return True
    return False


def _measurement_resume_checkpoint(
    store: Any,
    *,
    run_id: str,
    report: Mapping[str, Any],
) -> MeasurementResumeCheckpointV1 | PairedReplayResumeCheckpointV1 | None:
    """Resolve a typed, filesystem-validated shared replay checkpoint."""

    authoritative = load_measurement_resume_checkpoint(
        store,
        run_id=run_id,
        report=report,
    )
    if authoritative is not None:
        return authoritative
    return load_paired_replay_resume_checkpoint(
        store,
        run_id=run_id,
        report=report,
    )


def _campaign_measurement_resume_checkpoint(
    store: Any,
    *,
    campaign: SelfImprovementCampaign,
) -> MeasurementResumeCheckpointV1 | PairedReplayResumeCheckpointV1 | None:
    run_id = campaign.measurement_pending_run_id
    candidate_id = campaign.measurement_pending_candidate_id
    if run_id is None or candidate_id is None or run_id not in campaign.run_ids:
        return None
    try:
        report = store.read_report(run_id)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    checkpoint = _measurement_resume_checkpoint(
        store,
        run_id=run_id,
        report=report,
    )
    if checkpoint is None or checkpoint.candidate_id != candidate_id:
        return None
    return checkpoint


def _campaign_summary(
    campaign: SelfImprovementCampaign,
    latest: Mapping[str, Any],
) -> dict[str, Any]:
    summary = dict(latest)
    summary.update(
        {
            "campaign_id": campaign.campaign_id,
            "campaign_status": campaign.status.value,
            "campaign_cycle": campaign.cycle_index,
            "campaign_max_cycles": _campaign_effective_max_cycles(campaign),
            "campaign_configured_max_cycles": campaign.max_cycles,
            "campaign_repair_continuation_used": (
                campaign.repair_continuation_used
            ),
            "campaign_candidate_cycle_count": (
                _campaign_candidate_cycle_count(campaign)
            ),
            "campaign_measurement_retry_count": (
                campaign.measurement_retry_count
            ),
            "campaign_measurement_continuation_count": (
                campaign.measurement_continuation_count
            ),
            "campaign_framework_blocked_count": (
                campaign.framework_blocked_count
            ),
            "campaign_infrastructure_retry_count": (
                campaign.infrastructure_retry_count
            ),
            "campaign_max_measurement_retries": (
                campaign.max_measurement_retries
            ),
            "campaign_max_infrastructure_retries": (
                campaign.max_infrastructure_retries
            ),
            "campaign_measurement_pending_run_id": (
                campaign.measurement_pending_run_id
            ),
            "campaign_measurement_pending_candidate_id": (
                campaign.measurement_pending_candidate_id
            ),
            "campaign_path": str(
                Path(".aworld")
                / "self_evolve"
                / "campaigns"
                / campaign.campaign_id
                / "campaign.json"
            ),
            "goal_handoff_path": campaign.goal_handoff_path,
            "campaign_authoritative_candidate_count": (
                campaign.cumulative_authoritative_candidates
            ),
            "campaign_max_authoritative_candidates": (
                _campaign_effective_authoritative_candidate_limit(campaign)
            ),
            "campaign_configured_max_authoritative_candidates": (
                _campaign_authoritative_candidate_limit(campaign)
            ),
            "campaign_exhaustion_axes": list(
                _campaign_exhaustion_axes(campaign)
            ),
            "campaign_contract_stable_cycle_count": (
                campaign.contract_stable_cycle_count
            ),
        }
    )
    if campaign.latest_disposition is not None:
        summary["self_improvement_disposition"] = campaign.latest_disposition.to_dict()
    if campaign.latest_measurement_outcome is not None:
        summary["campaign_measurement_outcome"] = (
            campaign.latest_measurement_outcome.to_dict()
        )
        summary["campaign_measurement_projection"] = (
            campaign.latest_measurement_outcome.projection.value
        )
    return summary


def _campaign_prior_run_ids_by_champion(
    store: Any,
    run_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """Order prior runs so bounded feedback loading sees the champion first.

    Runner history loading intentionally walks the supplied IDs in reverse
    order and may stop at its feedback bound. Keeping the best verified
    frontier last therefore makes it authoritative without dropping audit
    access to newer, weaker runs.
    """

    if len(run_ids) < 2:
        return run_ids
    reports: dict[str, Mapping[str, Any]] = {}
    for run_id in run_ids:
        try:
            reports[run_id] = store.read_report(run_id)
        except (OSError, ValueError):
            continue
    if not reports:
        return run_ids
    positions = {run_id: index for index, run_id in enumerate(run_ids)}
    champion = max(
        reports,
        key=lambda run_id: (
            *_campaign_report_quality(reports[run_id]),
            positions[run_id],
        ),
    )
    return tuple(item for item in run_ids if item != champion) + (champion,)


def _campaign_report_quality(report: Mapping[str, Any]) -> tuple[object, ...]:
    metrics = (
        report.get("candidate_metrics")
        if isinstance(report.get("candidate_metrics"), Mapping)
        else {}
    )
    confidence = (
        report.get("acceptance_confidence")
        if isinstance(report.get("acceptance_confidence"), Mapping)
        else {}
    )
    post_apply = (
        report.get("post_apply")
        if isinstance(report.get("post_apply"), Mapping)
        else {}
    )
    regression_evidence = (
        report.get("regression_evidence")
        if isinstance(report.get("regression_evidence"), Mapping)
        else {}
    )
    gates = report.get("gate_results")
    failed_gate_count = sum(
        1
        for item in gates
        if isinstance(item, Mapping) and item.get("passed") is False
    ) if isinstance(gates, list) else 0
    measurement = (
        report.get("measurement")
        if isinstance(report.get("measurement"), Mapping)
        else {}
    )
    measurement_required = measurement.get("mode") == "required"
    measurement_validity_rank = {
        "failed": 0,
        "invalid": 1,
        "inconclusive": 2,
        "valid_limited": 3,
        "valid": 4,
    }.get(str(measurement.get("validity_status") or ""), 0)
    measurement_effect_rank = {
        "negative": 0,
        "unmeasured": 1,
        "inconclusive": 2,
        "neutral": 3,
        "positive": 4,
    }.get(str(measurement.get("effect_direction") or ""), 1)
    trusted_measurement_quality = (
        (
            measurement.get("promotion_eligible") is True,
            measurement_validity_rank,
            measurement_effect_rank,
            _finite_metric(measurement.get("confidence_lower_bound")),
        )
        if measurement_required
        else (False, 0, 1, float("-inf"))
    )
    progress = self_improvement_progress(report)
    repair_focus_present = isinstance(
        report.get("repair_focus_candidate_id"), str
    )
    return (
        str(report.get("status") or "") == "succeeded",
        post_apply.get("release_state") in {"verified", "verified_only"},
        repair_focus_present,
        progress.deepest_stage_rank if repair_focus_present else 0,
        not measurement_required
        or measurement.get("promotion_eligible") is True,
        *trusted_measurement_quality,
        isinstance(report.get("selected_candidate_id"), str),
        confidence.get("passed") is True,
        metrics.get("evidence_incomplete") is False,
        metrics.get("deterministic_signal") is True,
        regression_evidence.get("passed") is True,
        _finite_metric(metrics.get("command_pass_rate")),
        -failed_gate_count,
        _finite_metric(metrics.get("score")),
        progress.deepest_stage_rank,
    )


def _candidate_quality_progress(
    report: Mapping[str, Any],
) -> CandidateQualityProgress | None:
    raw_metrics = report.get("candidate_metrics")
    if not isinstance(raw_metrics, Mapping):
        return None
    score = _finite_metric(raw_metrics.get("score"))
    groundedness = _finite_metric(raw_metrics.get("A1_groundedness"))
    command_pass_rate = _finite_metric(raw_metrics.get("command_pass_rate"))
    failed_repetition_count = raw_metrics.get("failed_repetition_count")
    regression_evidence = (
        report.get("regression_evidence")
        if isinstance(report.get("regression_evidence"), Mapping)
        else {}
    )
    quality = CandidateQualityProgress(
        score_points=(
            max(0, math.floor(score))
            if score != float("-inf")
            else None
        ),
        groundedness_tenths=(
            max(0, math.floor(groundedness * 10))
            if groundedness != float("-inf")
            else None
        ),
        command_pass_basis_points=(
            min(10_000, max(0, round(command_pass_rate * 10_000)))
            if command_pass_rate != float("-inf")
            else None
        ),
        evidence_incomplete=_optional_bool(
            raw_metrics.get("evidence_incomplete")
        ),
        deterministic_signal=_optional_bool(
            raw_metrics.get("deterministic_signal")
        ),
        global_regression_passed=_optional_bool(
            regression_evidence.get("passed")
        ),
        failed_repetition_count=(
            int(failed_repetition_count)
            if isinstance(failed_repetition_count, (int, float))
            and not isinstance(failed_repetition_count, bool)
            and math.isfinite(float(failed_repetition_count))
            and float(failed_repetition_count).is_integer()
            and failed_repetition_count >= 0
            else None
        ),
    )
    return quality if any(value is not None for value in quality.to_dict().values()) else None


def _trusted_measurement_progress(
    report: Mapping[str, Any],
) -> TrustedMeasurementProgress | None:
    raw = report.get("measurement")
    if not isinstance(raw, Mapping):
        return None
    mode = str(raw.get("mode") or "off")
    stage = str(raw.get("measurement_readiness_stage") or "unplanned")
    lower = _finite_metric(raw.get("confidence_lower_bound"))
    independent_cases = raw.get("independent_case_count")
    comparable_pairs = raw.get("comparable_pair_count")
    return TrustedMeasurementProgress(
        authoritative=mode == "required",
        readiness_rank=_MEASUREMENT_READINESS_RANK.get(stage, 0),
        independent_case_count=(
            int(independent_cases)
            if isinstance(independent_cases, int)
            and not isinstance(independent_cases, bool)
            and independent_cases >= 0
            else 0
        ),
        comparable_pair_count=(
            int(comparable_pairs)
            if isinstance(comparable_pairs, int)
            and not isinstance(comparable_pairs, bool)
            and comparable_pairs >= 0
            else 0
        ),
        validity_status=_optional_string(raw.get("validity_status")),
        effect_direction=_optional_string(raw.get("effect_direction")),
        confidence_lower_micros=(
            int(round(lower * 1_000_000))
            if lower != float("-inf")
            else None
        ),
        promotion_eligible=raw.get("promotion_eligible") is True,
    )


def _finite_metric(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float("-inf")
    numeric = float(value)
    if numeric != numeric or numeric in {float("inf"), float("-inf")}:
        return float("-inf")
    return numeric


def _interrupted_run_reservation(
    request: Mapping[str, Any],
) -> CampaignUsage:
    """Charge configured hard ceilings when an interrupted run has no telemetry."""

    raw_tokens = request.get(
        "total_run_token_budget",
        request.get("max_run_tokens"),
    )
    tokens = (
        _positive_int(raw_tokens, "interrupted run token reservation")
        if raw_tokens is not None
        else 0
    )
    cost = request.get("max_run_cost_usd")
    wall = request.get("max_run_wall_seconds")
    return CampaignUsage(
        tokens=tokens,
        cost_usd=Decimal(str(cost)) if cost is not None else Decimal("0"),
        wall_seconds=Decimal(str(wall)) if wall is not None else Decimal("0"),
    )


def _remaining_budget_request(campaign: SelfImprovementCampaign) -> dict[str, Any]:
    request = campaign.request
    token_ceiling = request.get("_campaign_total_run_token_budget")
    if token_ceiling is None:
        token_ceiling = request.get("total_run_token_budget")
    if token_ceiling is None:
        token_ceiling = request.get("max_run_tokens")
    remaining_tokens = _remaining_int(token_ceiling, campaign.cumulative_usage.tokens)
    remaining_cost = _remaining_decimal(
        request.get("max_run_cost_usd"), campaign.cumulative_usage.cost_usd
    )
    remaining_wall = _remaining_decimal(
        request.get("max_run_wall_seconds"), campaign.cumulative_usage.wall_seconds
    )
    if remaining_tokens is not None and remaining_tokens <= 0:
        raise ValueError("campaign token budget is exhausted")
    if remaining_cost is not None and remaining_cost <= 0:
        raise ValueError("campaign cost budget is exhausted")
    if remaining_wall is not None and remaining_wall <= 0:
        raise ValueError("campaign wall-time budget is exhausted")
    raw_per_cycle_tokens = request.get("max_run_tokens")
    per_cycle_tokens = (
        _positive_int(raw_per_cycle_tokens, "max_run_tokens")
        if raw_per_cycle_tokens is not None
        else None
    )
    payload: dict[str, Any] = {}
    if remaining_tokens is not None:
        payload["total_run_token_budget"] = (
            min(remaining_tokens, per_cycle_tokens)
            if per_cycle_tokens is not None
            else remaining_tokens
        )
    elif per_cycle_tokens is not None:
        payload["total_run_token_budget"] = per_cycle_tokens
    if remaining_cost is not None:
        payload["max_run_cost_usd"] = remaining_cost
    if remaining_wall is not None:
        payload["max_run_wall_seconds"] = remaining_wall
    return payload


def _typed_failure_events(report: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    events: dict[str, dict[str, Any]] = {}
    for item in _walk_mappings(report):
        owner = item.get("owner")
        stage = item.get("stage")
        scope = item.get("scope")
        repairable = item.get("repairable")
        if (
            owner not in {"candidate", "task", "infrastructure", "framework"}
            or not isinstance(stage, str)
            or scope not in {"variant", "member", "candidate", "shared_run"}
            or not isinstance(repairable, bool)
        ):
            continue
        normalized = {
            "semantic_key": item.get("semantic_key"),
            "code": str(item.get("code") or "typed_failure"),
            "owner": owner,
            "stage": stage,
            "scope": scope,
            "repairable": repairable,
            "category": str(item.get("category") or ""),
            "capability_identity_digest": item.get("capability_identity_digest"),
            "requirement_identity_digest": item.get("requirement_identity_digest"),
            "contract_identity_digest": item.get("contract_identity_digest"),
            "diagnostic_refs": _public_diagnostic_refs(item),
        }
        events.setdefault(_event_identity(normalized), normalized)
    return tuple(events[key] for key in sorted(events))


def _event_identity(event: Mapping[str, Any]) -> str:
    supplied = event.get("semantic_key")
    if isinstance(supplied, str) and supplied:
        return supplied
    return "campaign-frontier-" + hashlib.sha256(
        json.dumps(
            {
                key: event.get(key)
                for key in (
                    "code",
                    "owner",
                    "stage",
                    "scope",
                    "repairable",
                    "category",
                    "capability_identity_digest",
                    "requirement_identity_digest",
                    "contract_identity_digest",
                )
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _constraint_identities(report: Mapping[str, Any]) -> set[str]:
    identities: set[str] = set()
    for item in _walk_mappings(report):
        counterexample_id = item.get("counterexample_id")
        counterexample_schema = item.get("schema_version")
        if (
            isinstance(counterexample_schema, str)
            and counterexample_schema.startswith("aworld.self_evolve.")
            and "counterexample" in counterexample_schema
            and isinstance(counterexample_id, str)
            and counterexample_id
        ):
            identities.add("constraint-" + counterexample_id)
        if (
            item.get("projection_schema_version")
            == "aworld.self_evolve.repair_conformance.public.v1"
            and isinstance(item.get("required_branch_paths"), list)
        ):
            # Track the actionable contract independently from candidate ids
            # and baseline hashes. Moving ownership from compiler to runtime
            # must advance Campaign continuation, while rebasing the same
            # contract onto another candidate must not manufacture progress.
            contract_shape = {
                key: item.get(key)
                for key in (
                    "failure_codes",
                    "required_branch_paths",
                    "manifest_path",
                    "compiler_path",
                    "runtime_paths",
                    "fixture_probe_constraints",
                    "schema_field_constraints",
                    "runtime_artifact_constraints",
                    "runtime_response_constraints",
                    "required_runtime_transitions",
                )
            }
            identities.add(
                "constraint-repair-contract-"
                + _fingerprint(contract_shape)[7:]
            )
        for key in (
            "schema_field_constraints",
            "fixture_probe_constraints",
            "runtime_artifact_constraints",
            "repair_constraints",
        ):
            constraints = item.get(key)
            if not isinstance(constraints, list):
                continue
            for constraint in constraints:
                identity = _typed_constraint_identity(constraint, kind=key)
                if identity is not None:
                    identities.add(identity)
    return identities


def _recovery_frontier_identities(report: Mapping[str, Any]) -> set[str]:
    """Return monotonic, payload-free progress identities for recovery gains."""

    identities: set[str] = set()
    seen_traces: set[str] = set()
    for item in _walk_mappings(report):
        if item.get("schema_version") != RECOVERY_TRACE_SCHEMA_VERSION:
            continue
        trace = validate_public_recovery_trace(item)
        if trace is None:
            continue
        fingerprint = _fingerprint(trace)
        if fingerprint in seen_traces:
            continue
        seen_traces.add(fingerprint)
        members = trace.get("members")
        if not isinstance(members, list):
            continue
        for member in members:
            if not isinstance(member, Mapping):
                continue
            identity = member.get("member_identity")
            if not isinstance(identity, str) or not identity:
                continue
            repetitions = member.get("candidate_repetition_count")
            success_rate = member.get("candidate_success_rate")
            success_count = 0
            if (
                isinstance(repetitions, (int, float))
                and not isinstance(repetitions, bool)
                and isinstance(success_rate, (int, float))
                and not isinstance(success_rate, bool)
            ):
                success_count = max(
                    0,
                    min(64, round(float(repetitions) * float(success_rate))),
                )
            digest = identity.removeprefix("sha256:")
            for index in range(success_count):
                identities.add(f"recovery-member-{digest}-success-{index + 1}")
            if member.get("classification") == "stable_recovery":
                identities.add(f"recovery-member-{digest}-stable")
            transition_count = member.get("failure_to_success_transition_count")
            if isinstance(transition_count, (int, float)) and not isinstance(
                transition_count, bool
            ):
                for index in range(max(0, min(64, int(transition_count)))):
                    identities.add(
                        f"recovery-member-{digest}-transition-{index + 1}"
                    )
    return identities


def _typed_constraint_identity(value: Any, *, kind: str) -> str | None:
    if not isinstance(value, Mapping) or value.get("kind") == "bounded_public_summary":
        return None
    supplied = value.get("constraint_identity_digest")
    if isinstance(supplied, str) and supplied:
        return "constraint-" + supplied.removeprefix("sha256:")
    if kind == "schema_field_constraints":
        required = ("schema_layer", "field_path", "rule")
    elif kind == "fixture_probe_constraints":
        required = ("kind", "path")
    elif kind == "runtime_artifact_constraints":
        required = (
            "artifact_kind",
            "relative_path",
            "producer_layer",
            "availability_milestone",
        )
    else:
        required = ("identity",)
    if not all(isinstance(value.get(field), str) and value.get(field) for field in required):
        return None
    canonical = {
        key: value.get(key)
        for key in sorted(value)
        if key
        not in {
            "actual_fingerprint",
            "actual_type",
            "affected_case_ids",
            "occurrence_count",
        }
    }
    return "constraint-" + _fingerprint(canonical)[7:]


def _walk_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_mappings(child)


def _event_disposition(
    kind: SelfImprovementDispositionKind,
    reason_code: str,
    event: Mapping[str, Any],
    delta: tuple[str, ...],
) -> SelfImprovementDisposition:
    return SelfImprovementDisposition(
        kind=kind,
        reason_code=reason_code,
        owner=str(event.get("owner")),
        stage=str(event.get("stage")),
        scope=str(event.get("scope")),
        repairable=event.get("repairable") is True,
        progress_delta_ids=delta,
        diagnostic_refs=_string_tuple(event.get("diagnostic_refs")),
    )


def _public_diagnostic_refs(event: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("artifact_refs", "diagnostic_refs", "evidence_refs"):
        raw = event.get(key)
        if not isinstance(raw, (list, tuple)):
            continue
        for item in raw:
            text = str(item).strip()
            if text and "\n" not in text and "\r" not in text:
                refs.append(text[:500])
    return sorted(set(refs))[:16]


def _verification_request(request: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "apply_policy",
        "auto_apply_target_types",
        "baseline_replay_repetitions",
        "candidate_replay_repetitions",
        "inferred_new_skill_policy",
        "iterations",
        "judge_config",
        "judge_repetitions",
        "judge_timeout_seconds",
        "min_eval_cases",
        "min_score_delta",
        "measurement_bootstrap_samples",
        "measurement_confidence_level",
        "measurement_min_independent_cases",
        "measurement_minimum_effect",
        "measurement_mode",
        "measurement_primary_metric",
        "measurement_zero_yield_patience",
        "measurement_invalid_control_patience",
        "measurement_maximum_interval_width",
        "replay_candidate_limit",
        "replay_enabled",
        "replay_max_steps",
        "replay_stability_margin",
        "replay_timeout_seconds",
        "replay_total_timeout_seconds",
    )
    return {key: request.get(key) for key in keys}


def _request_has_source(request: Mapping[str, Any]) -> bool:
    return any(
        request.get(key) is not None
        for key in {
            "batch_config",
            "current_trajectory",
            "dataset",
            "from_session",
            "from_source",
            "from_trajectory",
            "from_trajectory_set",
            "frozen_ingestion_id",
        }
    )


def _source_snapshot(
    request: Mapping[str, Any],
    *,
    workspace_root: str | Path,
) -> dict[str, Any]:
    frozen_ingestion_id = request.get("frozen_ingestion_id")
    if isinstance(frozen_ingestion_id, str) and frozen_ingestion_id:
        from aworld.self_evolve.store import FilesystemSelfEvolveStore
        from aworld.self_evolve.ingestion.semantic_snapshot import (
            FrozenSemanticIngestionSnapshotV2,
        )

        snapshot = FilesystemSelfEvolveStore(workspace_root).read_ingestion(
            frozen_ingestion_id
        )
        semantic = isinstance(
            snapshot,
            FrozenSemanticIngestionSnapshotV2,
        )
        result = {
            "kind": "agentic_source",
            "ingestion_id": snapshot.ingestion_id,
            "source_fingerprint": snapshot.inventory.source_root_fingerprint,
            "normalization_kind": (
                "semantic_evidence"
                if semantic
                else "structural_mapping"
            ),
            "mapping_fingerprint": (
                None
                if semantic
                else snapshot.selected_mapping.fingerprint
            ),
            "normalized_dataset_fingerprint": (
                snapshot.normalized_dataset_fingerprint
            ),
            "manifest_fingerprint": snapshot.manifest_fingerprint,
            "extractor_fingerprints": list(snapshot.extractor_fingerprints),
            "ingestor_name": snapshot.ingestor_name,
            "ingestor_version": snapshot.ingestor_version,
            "ingestor_trust_level": snapshot.ingestor_trust_level.value,
            "ingestion_schema_version": snapshot.schema_version,
        }
        if semantic:
            result.update(
                {
                    "normalization_fingerprint": (
                        snapshot.compiled_dataset
                        .normalization_fingerprint
                    ),
                    "evidence_graph_logical_fingerprint": (
                        snapshot.evidence_graph.logical_fingerprint
                    ),
                    "evidence_graph_provenance_fingerprint": (
                        snapshot.evidence_graph.provenance_fingerprint
                    ),
                    "improvement_signal_set_fingerprint": (
                        snapshot.improvement_signal_set.fingerprint
                    ),
                    "evaluation_plan_bundle_fingerprint": (
                        snapshot.compiled_dataset
                        .evaluation_plan_bundle_fingerprint
                    ),
                    "target_evidence_bundle_fingerprint": (
                        snapshot.compiled_dataset
                        .target_evidence_bundle.fingerprint
                    ),
                    "manifest_origin": snapshot.manifest_origin.value,
                    "semantic_model_profile_fingerprint": (
                        snapshot.semantic_model_profile_fingerprint
                    ),
                    "semantic_provider_fingerprint": (
                        snapshot.semantic_provider_fingerprint
                    ),
                    "semantic_protocol_fingerprint": (
                        snapshot.semantic_protocol_fingerprint
                    ),
                    "qualification_report_fingerprint": (
                        snapshot.qualification_report.report_fingerprint
                        if snapshot.qualification_report is not None
                        else None
                    ),
                    "qualification_registry_fingerprint": (
                        snapshot.qualification_registry.fingerprint
                    ),
                    "evidence_authority_context_fingerprint": (
                        snapshot.evidence_authority_context.fingerprint
                    ),
                }
            )
        return result

    root = Path(workspace_root)
    file_keys = {
        "batch_config",
        "dataset",
        "from_trajectory",
        "from_trajectory_set",
        "from_source",
        "source_manifest",
    }
    snapshot: dict[str, Any] = {}
    for key in sorted(_SOURCE_REQUEST_KEYS):
        value = request.get(key)
        if value is None:
            snapshot[key] = None
            continue
        if key not in file_keys or not isinstance(value, str):
            snapshot[key] = {"value_fingerprint": _fingerprint(value)}
            continue
        source_path = Path(value).expanduser()
        if not source_path.is_absolute():
            source_path = root / source_path
        entry: dict[str, Any] = {"path": value}
        if source_path.is_file() and not source_path.is_symlink():
            digest = hashlib.sha256()
            size = 0
            with source_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            entry.update(
                {
                    "state": "file",
                    "sha256": digest.hexdigest(),
                    "size": size,
                }
            )
        elif source_path.is_symlink():
            entry["state"] = "symlink"
        else:
            entry["state"] = "missing"
        snapshot[key] = entry
    return snapshot


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_value(model_dump())
    raise TypeError(f"campaign request value is not serializable: {type(value).__name__}")


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _remaining_int(value: Any, spent: int) -> int | None:
    if value is None:
        return None
    return max(0, int(value) - spent)


def _remaining_decimal(value: Any, spent: Decimal) -> Decimal | None:
    if value is None:
        return None
    return max(Decimal("0"), Decimal(str(value)) - spent)


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be positive")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be positive") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive")
    return parsed


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be non-negative")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be non-negative") from exc
    if parsed < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return parsed


def _optional_non_negative_int(value: Any) -> int | None:
    if value is None:
        return None
    return _non_negative_int(value, "optional campaign quality metric")


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_int_increased(current: int | None, previous: int | None) -> bool:
    return current is not None and previous is not None and current > previous


def _optional_int_regressed(current: int | None, previous: int | None) -> bool:
    return current is not None and previous is not None and current < previous


def _validate_id(value: str, field_name: str) -> None:
    if not _ID_RE.fullmatch(str(value)) or value in {".", ".."}:
        raise ValueError(f"invalid {field_name}: {value!r}")


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item))


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
