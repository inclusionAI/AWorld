from __future__ import annotations

import hashlib
import json
import math
import random
import re
import statistics
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence


CONTROLLED_EXPERIMENT_SCHEMA_VERSION = (
    "aworld.self_evolve.controlled_experiment.v1"
)
MEASUREMENT_OBSERVATION_SCHEMA_VERSION = (
    "aworld.self_evolve.measurement_observation.v1"
)
ATTRIBUTION_REPORT_SCHEMA_VERSION = "aworld.self_evolve.attribution_report.v1"
MEASUREMENT_SUMMARY_SCHEMA_VERSION = "aworld.self_evolve.measurement_summary.v1"
SEARCH_PERFORMANCE_SCHEMA_VERSION = "aworld.self_evolve.search_performance.v1"
TRANSFER_PANEL_SCHEMA_VERSION = "aworld.self_evolve.transfer_panel.v1"
ESTIMATOR_VERSION = "aworld.paired_case_bootstrap.v1"

_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_REQUIRED_FROZEN_IDENTITIES = (
    "task_model",
    "generator",
    "scheduler",
    "evaluator",
    "dataset",
    "environment",
    "runtime",
    "prompt_context",
    "budget",
)
_CONTROLLED_EXPERIMENT_KNOWN_FIELDS = frozenset(
    {
        "schema_version",
        "experiment_id",
        "run_id",
        "mode",
        "swap_axis",
        "changed_axes",
        "control",
        "treatment",
        "frozen_identities",
        "sampling",
        "outcomes",
        "budgets",
        "transfer_panels",
        "search_visible_case_ids",
        "selection_protocol",
        "stopping_policy",
        "created_at",
    }
)
_READINESS_RANK = {
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


class MeasurementPolicyMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    ADVISORY = "advisory"
    REQUIRED = "required"


class SwapAxis(str, Enum):
    ARTIFACT = "artifact"
    TASK_MODEL = "task_model"
    GENERATOR = "generator"
    SCHEDULER = "scheduler"


class ArmRole(str, Enum):
    CONTROL = "control"
    TREATMENT = "treatment"


class ExperimentValidityStatus(str, Enum):
    VALID = "valid"
    VALID_LIMITED = "valid_limited"
    INCONCLUSIVE = "inconclusive"
    INVALID = "invalid"
    FAILED = "failed"


class ComparabilityStatus(str, Enum):
    COMPARABLE = "comparable"
    INCOMPARABLE = "incomparable"
    NOT_EVALUATED = "not_evaluated"


class ObservationExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_RUN = "not_run"
    TIMEOUT = "timeout"


class EffectDirection(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    INCONCLUSIVE = "inconclusive"
    UNMEASURED = "unmeasured"


class MeasurementNextAction(str, Enum):
    PROMOTE_CANDIDATE = "promote_candidate"
    CONTINUE_CANDIDATE_REPAIR = "continue_candidate_repair"
    COLLECT_MORE_EVIDENCE = "collect_more_evidence"
    REPAIR_MEASUREMENT = "repair_measurement"
    SWITCH_GENERATOR = "switch_generator"
    SWITCH_SCHEDULER = "switch_scheduler"
    STOP_NO_EFFECT = "stop_no_effect"
    STOP_NEGATIVE_EFFECT = "stop_negative_effect"
    PAUSE_OPERATOR = "pause_operator"


class MeasurementStopTrigger(str, Enum):
    ZERO_COMPARABLE_PAIRS = "zero_comparable_pairs"
    REPEATED_CONTROL_INVALIDITY = "repeated_control_invalidity"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DECISIVE_REGRESSION = "decisive_regression"
    SUFFICIENTLY_PRECISE = "sufficiently_precise"


class TransferPanelRole(str, Enum):
    IN_DOMAIN_HELD_OUT = "in_domain_held_out"
    CROSS_TASK = "cross_task"
    CROSS_SKILL_FAMILY = "cross_skill_family"
    TEMPORAL = "temporal"
    REGRESSION_CANARY = "regression_canary"


class VisibilityClass(str, Enum):
    FINAL_ONLY = "final_only"
    HIDDEN = "hidden"
    PUBLIC = "public"


@dataclass(frozen=True)
class ComponentIdentity:
    component_id: str
    fingerprint: str | None

    def __post_init__(self) -> None:
        _safe_id(self.component_id, "component_id")
        _optional_fingerprint(self.fingerprint, "component fingerprint")

    @property
    def complete(self) -> bool:
        return self.fingerprint is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "component_id": self.component_id,
            "fingerprint": self.fingerprint,
            "complete": self.complete,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ComponentIdentity":
        return cls(
            component_id=_required_text(value.get("component_id"), "component_id"),
            fingerprint=_optional_text(value.get("fingerprint")),
        )


@dataclass(frozen=True)
class FrozenIdentities:
    task_model: str | None = None
    generator: str | None = None
    scheduler: str | None = None
    evaluator: str | None = None
    dataset: str | None = None
    environment: str | None = None
    runtime: str | None = None
    prompt_context: str | None = None
    budget: str | None = None

    def __post_init__(self) -> None:
        for name, value in self.values.items():
            _optional_fingerprint(value, f"frozen identity {name}")

    @property
    def values(self) -> dict[str, str | None]:
        return {name: getattr(self, name) for name in _REQUIRED_FROZEN_IDENTITIES}

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(name for name, value in self.values.items() if value is None)

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def fingerprint(self) -> str:
        return stable_measurement_fingerprint(self.values)

    def to_dict(self) -> dict[str, object]:
        return {
            **self.values,
            "complete": self.complete,
            "missing": list(self.missing),
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "FrozenIdentities":
        identities = cls(
            **{
                name: _optional_text(value.get(name))
                for name in _REQUIRED_FROZEN_IDENTITIES
            }
        )
        declared = value.get("fingerprint")
        if declared is not None and declared != identities.fingerprint:
            raise ValueError("frozen identity fingerprint does not match fields")
        return identities


@dataclass(frozen=True)
class SamplingPlan:
    independent_case_ids: tuple[str, ...]
    repetitions_per_case: int = 1
    seeds: tuple[int, ...] = ()
    pairing: str = "same_case_same_repetition"

    def __post_init__(self) -> None:
        cases = tuple(self.independent_case_ids)
        if not cases or len(set(cases)) != len(cases):
            raise ValueError("independent case ids must be non-empty and unique")
        for case_id in cases:
            _safe_id(case_id, "case_id")
        if (
            isinstance(self.repetitions_per_case, bool)
            or not isinstance(self.repetitions_per_case, int)
            or self.repetitions_per_case <= 0
        ):
            raise ValueError("repetitions_per_case must be positive")
        seeds = tuple(self.seeds)
        if seeds and len(seeds) != self.repetitions_per_case:
            raise ValueError("seeds must be empty or match repetitions_per_case")
        if any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds):
            raise ValueError("sampling seeds must be integers")
        if self.pairing != "same_case_same_repetition":
            raise ValueError("unsupported pairing policy")
        object.__setattr__(self, "independent_case_ids", cases)
        object.__setattr__(self, "seeds", seeds)

    def to_dict(self) -> dict[str, object]:
        return {
            "independent_case_ids": list(self.independent_case_ids),
            "repetitions_per_case": self.repetitions_per_case,
            "seeds": list(self.seeds),
            "pairing": self.pairing,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SamplingPlan":
        return cls(
            independent_case_ids=_string_tuple(value.get("independent_case_ids")),
            repetitions_per_case=_positive_int(
                value.get("repetitions_per_case", 1), "repetitions_per_case"
            ),
            seeds=_int_tuple(value.get("seeds")),
            pairing=str(value.get("pairing") or "same_case_same_repetition"),
        )


@dataclass(frozen=True)
class OutcomePlan:
    primary_metric: str
    secondary_metrics: tuple[str, ...] = ()
    minimum_effect: float = 0.0
    non_regression_threshold: float = 0.0
    confidence_level: float = 0.95
    minimum_independent_cases: int = 2
    aggregation: str = "mean"
    higher_is_better: bool = True
    bootstrap_samples: int = 2_000
    multiple_comparison_policy: str = "independent_final_panel"

    def __post_init__(self) -> None:
        _safe_metric_name(self.primary_metric)
        secondary = tuple(dict.fromkeys(self.secondary_metrics))
        for metric in secondary:
            _safe_metric_name(metric)
        if self.primary_metric in secondary:
            raise ValueError("primary metric cannot also be secondary")
        for name in ("minimum_effect", "non_regression_threshold"):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            object.__setattr__(self, name, value)
        confidence = float(self.confidence_level)
        if not 0 < confidence < 1:
            raise ValueError("confidence_level must be between zero and one")
        if (
            isinstance(self.minimum_independent_cases, bool)
            or not isinstance(self.minimum_independent_cases, int)
            or self.minimum_independent_cases <= 0
        ):
            raise ValueError("minimum_independent_cases must be positive")
        if self.aggregation not in {"mean", "median"}:
            raise ValueError("unsupported outcome aggregation")
        if (
            isinstance(self.bootstrap_samples, bool)
            or not isinstance(self.bootstrap_samples, int)
            or self.bootstrap_samples < 200
            or self.bootstrap_samples > 100_000
        ):
            raise ValueError("bootstrap_samples must be between 200 and 100000")
        object.__setattr__(self, "secondary_metrics", secondary)
        object.__setattr__(self, "confidence_level", confidence)

    def to_dict(self) -> dict[str, object]:
        return {
            "primary_metric": self.primary_metric,
            "secondary_metrics": list(self.secondary_metrics),
            "minimum_effect": self.minimum_effect,
            "non_regression_threshold": self.non_regression_threshold,
            "confidence_level": self.confidence_level,
            "minimum_independent_cases": self.minimum_independent_cases,
            "aggregation": self.aggregation,
            "higher_is_better": self.higher_is_better,
            "bootstrap_samples": self.bootstrap_samples,
            "multiple_comparison_policy": self.multiple_comparison_policy,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "OutcomePlan":
        return cls(
            primary_metric=_required_text(
                value.get("primary_metric"), "primary_metric"
            ),
            secondary_metrics=_string_tuple(value.get("secondary_metrics")),
            minimum_effect=_finite_number(value.get("minimum_effect", 0.0)),
            non_regression_threshold=_finite_number(
                value.get("non_regression_threshold", 0.0)
            ),
            confidence_level=_finite_number(value.get("confidence_level", 0.95)),
            minimum_independent_cases=_positive_int(
                value.get("minimum_independent_cases", 2),
                "minimum_independent_cases",
            ),
            aggregation=str(value.get("aggregation") or "mean"),
            higher_is_better=value.get("higher_is_better") is not False,
            bootstrap_samples=_positive_int(
                value.get("bootstrap_samples", 2_000), "bootstrap_samples"
            ),
            multiple_comparison_policy=str(
                value.get("multiple_comparison_policy")
                or "independent_final_panel"
            ),
        )


@dataclass(frozen=True)
class MeasurementUsage:
    tokens: int | None = None
    cost_usd: float | None = None
    wall_seconds: float | None = None
    retries: int = 0
    candidate_opportunities: int = 0

    def __post_init__(self) -> None:
        if self.tokens is not None and (
            isinstance(self.tokens, bool)
            or not isinstance(self.tokens, int)
            or self.tokens < 0
        ):
            raise ValueError("usage tokens must be non-negative")
        for name in ("cost_usd", "wall_seconds"):
            value = getattr(self, name)
            if value is not None:
                parsed = float(value)
                if not math.isfinite(parsed) or parsed < 0:
                    raise ValueError(f"usage {name} must be non-negative and finite")
                object.__setattr__(self, name, parsed)
        for name in ("retries", "candidate_opportunities"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"usage {name} must be non-negative")

    @property
    def complete(self) -> bool:
        return self.tokens is not None and self.wall_seconds is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "wall_seconds": self.wall_seconds,
            "retries": self.retries,
            "candidate_opportunities": self.candidate_opportunities,
            "complete": self.complete,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "MeasurementUsage":
        return cls(
            tokens=_optional_non_negative_int(value.get("tokens")),
            cost_usd=_optional_non_negative_number(value.get("cost_usd")),
            wall_seconds=_optional_non_negative_number(value.get("wall_seconds")),
            retries=_non_negative_int(value.get("retries", 0), "retries"),
            candidate_opportunities=_non_negative_int(
                value.get("candidate_opportunities", 0),
                "candidate_opportunities",
            ),
        )


@dataclass(frozen=True)
class ExperimentBudget:
    search: MeasurementUsage = field(default_factory=MeasurementUsage)
    measurement: MeasurementUsage = field(default_factory=MeasurementUsage)

    def to_dict(self) -> dict[str, object]:
        return {"search": self.search.to_dict(), "measurement": self.measurement.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ExperimentBudget":
        search = value.get("search")
        measurement = value.get("measurement")
        return cls(
            search=MeasurementUsage.from_dict(search) if isinstance(search, Mapping) else MeasurementUsage(),
            measurement=MeasurementUsage.from_dict(measurement) if isinstance(measurement, Mapping) else MeasurementUsage(),
        )


@dataclass(frozen=True)
class BudgetLedger:
    """Actual search and measurement use; missing telemetry remains null."""

    search: MeasurementUsage = field(default_factory=MeasurementUsage)
    measurement: MeasurementUsage = field(default_factory=MeasurementUsage)

    @property
    def total(self) -> MeasurementUsage:
        return MeasurementUsage(
            tokens=_sum_optional_int(self.search.tokens, self.measurement.tokens),
            cost_usd=_sum_optional_float(
                self.search.cost_usd,
                self.measurement.cost_usd,
            ),
            wall_seconds=_sum_optional_float(
                self.search.wall_seconds,
                self.measurement.wall_seconds,
            ),
            retries=self.search.retries + self.measurement.retries,
            candidate_opportunities=(
                self.search.candidate_opportunities
                + self.measurement.candidate_opportunities
            ),
        )

    @property
    def dominant_use(self) -> str | None:
        for left, right in (
            (self.search.tokens, self.measurement.tokens),
            (self.search.wall_seconds, self.measurement.wall_seconds),
            (self.search.cost_usd, self.measurement.cost_usd),
        ):
            if left is None or right is None:
                continue
            if left == right:
                return "tied"
            return "search" if left > right else "measurement"
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "search": self.search.to_dict(),
            "measurement": self.measurement.to_dict(),
            "total": self.total.to_dict(),
            "dominant_use": self.dominant_use,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "BudgetLedger":
        search = value.get("search")
        measurement = value.get("measurement")
        return cls(
            search=(
                MeasurementUsage.from_dict(search)
                if isinstance(search, Mapping)
                else MeasurementUsage()
            ),
            measurement=(
                MeasurementUsage.from_dict(measurement)
                if isinstance(measurement, Mapping)
                else MeasurementUsage()
            ),
        )


@dataclass(frozen=True)
class TransferPanel:
    panel_id: str
    role: TransferPanelRole
    case_ids: tuple[str, ...]
    fingerprint: str
    visibility: VisibilityClass = VisibilityClass.FINAL_ONLY
    optimization_cutoff_at: str | None = None
    sealed_at: str | None = None
    case_sealed_at: Mapping[str, str] = field(default_factory=dict)
    exposed_to_search: bool = False
    required: bool = True

    def __post_init__(self) -> None:
        _safe_id(self.panel_id, "panel_id")
        object.__setattr__(self, "role", TransferPanelRole(self.role))
        object.__setattr__(self, "visibility", VisibilityClass(self.visibility))
        if not self.case_ids or len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("transfer panel case ids must be non-empty and unique")
        for case_id in self.case_ids:
            _safe_id(case_id, "transfer case id")
        _fingerprint(self.fingerprint, "transfer panel fingerprint")
        for timestamp in (
            self.optimization_cutoff_at,
            self.sealed_at,
            *self.case_sealed_at.values(),
        ):
            if timestamp is not None:
                _utc_datetime(timestamp, "transfer timestamp")
        unknown = set(self.case_sealed_at) - set(self.case_ids)
        if unknown:
            raise ValueError("case_sealed_at references cases outside the panel")

    @classmethod
    def create(
        cls,
        *,
        panel_id: str,
        role: TransferPanelRole | str,
        case_ids: Sequence[str],
        visibility: VisibilityClass | str = VisibilityClass.FINAL_ONLY,
        optimization_cutoff_at: str | None = None,
        sealed_at: str | None = None,
        case_sealed_at: Mapping[str, str] | None = None,
        exposed_to_search: bool = False,
        required: bool = True,
    ) -> "TransferPanel":
        payload = {
            "panel_id": panel_id,
            "role": TransferPanelRole(role).value,
            "case_ids": list(case_ids),
            "visibility": VisibilityClass(visibility).value,
            "optimization_cutoff_at": optimization_cutoff_at,
            "sealed_at": sealed_at,
            "case_sealed_at": dict(case_sealed_at or {}),
            "exposed_to_search": exposed_to_search,
            "required": required,
        }
        return cls(fingerprint=stable_measurement_fingerprint(payload), **payload)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": TRANSFER_PANEL_SCHEMA_VERSION,
            "panel_id": self.panel_id,
            "role": self.role.value,
            "case_ids": list(self.case_ids),
            "fingerprint": self.fingerprint,
            "visibility": self.visibility.value,
            "optimization_cutoff_at": self.optimization_cutoff_at,
            "sealed_at": self.sealed_at,
            "case_sealed_at": dict(self.case_sealed_at),
            "exposed_to_search": self.exposed_to_search,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TransferPanel":
        _require_schema(value, TRANSFER_PANEL_SCHEMA_VERSION, "transfer panel")
        panel = cls(
            panel_id=_required_text(value.get("panel_id"), "panel_id"),
            role=TransferPanelRole(str(value.get("role"))),
            case_ids=_string_tuple(value.get("case_ids")),
            fingerprint=_required_text(value.get("fingerprint"), "fingerprint"),
            visibility=VisibilityClass(str(value.get("visibility") or "final_only")),
            optimization_cutoff_at=_optional_text(value.get("optimization_cutoff_at")),
            sealed_at=_optional_text(value.get("sealed_at")),
            case_sealed_at=_string_mapping(value.get("case_sealed_at")),
            exposed_to_search=value.get("exposed_to_search") is True,
            required=value.get("required") is not False,
        )
        expected = TransferPanel.create(
            panel_id=panel.panel_id,
            role=panel.role,
            case_ids=panel.case_ids,
            visibility=panel.visibility,
            optimization_cutoff_at=panel.optimization_cutoff_at,
            sealed_at=panel.sealed_at,
            case_sealed_at=panel.case_sealed_at,
            exposed_to_search=panel.exposed_to_search,
            required=panel.required,
        ).fingerprint
        if panel.fingerprint != expected:
            raise ValueError("transfer panel fingerprint does not match contract")
        return panel


@dataclass(frozen=True)
class ControlledExperimentSpec:
    experiment_id: str
    run_id: str
    mode: MeasurementPolicyMode
    swap_axis: SwapAxis
    changed_axes: tuple[SwapAxis, ...]
    control: ComponentIdentity
    treatment: ComponentIdentity
    frozen_identities: FrozenIdentities
    sampling: SamplingPlan
    outcomes: OutcomePlan
    budgets: ExperimentBudget
    transfer_panels: tuple[TransferPanel, ...] = ()
    search_visible_case_ids: tuple[str, ...] = ()
    selection_protocol: str = "predeclared_candidate"
    stopping_policy: "MeasurementEarlyStopPolicy" = field(
        default_factory=lambda: MeasurementEarlyStopPolicy()
    )
    created_at: str | None = None
    extensions: Mapping[str, object] = field(default_factory=dict, compare=False)
    schema_version: str = CONTROLLED_EXPERIMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONTROLLED_EXPERIMENT_SCHEMA_VERSION:
            raise ValueError("unsupported controlled experiment schema")
        _safe_id(self.run_id, "run_id")
        if not re.fullmatch(r"experiment-[0-9a-f]{32}", self.experiment_id):
            raise ValueError("invalid experiment_id")
        object.__setattr__(self, "mode", MeasurementPolicyMode(self.mode))
        object.__setattr__(self, "swap_axis", SwapAxis(self.swap_axis))
        axes = tuple(SwapAxis(axis) for axis in self.changed_axes)
        if len(axes) != 1 or axes[0] is not self.swap_axis:
            raise ValueError("controlled experiment requires exactly one swap axis")
        object.__setattr__(self, "changed_axes", axes)
        if self.control.component_id == self.treatment.component_id and (
            self.control.fingerprint == self.treatment.fingerprint
        ):
            raise ValueError("control and treatment identities must differ")
        if len({panel.panel_id for panel in self.transfer_panels}) != len(
            self.transfer_panels
        ):
            raise ValueError("transfer panel ids must be unique")
        visible_case_ids = tuple(dict.fromkeys(self.search_visible_case_ids))
        for case_id in visible_case_ids:
            _safe_id(case_id, "search-visible case id")
        hidden_transfer_cases = {
            case_id
            for panel in self.transfer_panels
            if panel.visibility
            in {VisibilityClass.HIDDEN, VisibilityClass.FINAL_ONLY}
            for case_id in panel.case_ids
        }
        if hidden_transfer_cases & set(visible_case_ids):
            raise ValueError("held_out_leakage: hidden transfer case exposed to search")
        object.__setattr__(self, "search_visible_case_ids", visible_case_ids)
        if not isinstance(self.stopping_policy, MeasurementEarlyStopPolicy):
            raise TypeError("stopping_policy must be a typed measurement policy")
        if self.created_at is not None:
            _utc_datetime(self.created_at, "created_at")
        if any(key in _CONTROLLED_EXPERIMENT_KNOWN_FIELDS for key in self.extensions):
            raise ValueError("experiment extension collides with a known field")

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        mode: MeasurementPolicyMode | str,
        swap_axis: SwapAxis | str,
        control: ComponentIdentity,
        treatment: ComponentIdentity,
        frozen_identities: FrozenIdentities,
        sampling: SamplingPlan,
        outcomes: OutcomePlan,
        budgets: ExperimentBudget,
        changed_axes: Sequence[SwapAxis | str] | None = None,
        transfer_panels: Sequence[TransferPanel] = (),
        search_visible_case_ids: Sequence[str] = (),
        selection_protocol: str = "predeclared_candidate",
        stopping_policy: "MeasurementEarlyStopPolicy | None" = None,
        created_at: str | None = None,
    ) -> "ControlledExperimentSpec":
        axis = SwapAxis(swap_axis)
        axes = tuple(SwapAxis(item) for item in (changed_axes or (axis,)))
        identity_payload = {
            "schema_version": CONTROLLED_EXPERIMENT_SCHEMA_VERSION,
            "run_id": run_id,
            "mode": MeasurementPolicyMode(mode).value,
            "swap_axis": axis.value,
            "changed_axes": [item.value for item in axes],
            "control": control.to_dict(),
            "treatment": treatment.to_dict(),
            "frozen_identities": frozen_identities.to_dict(),
            "sampling": sampling.to_dict(),
            "outcomes": outcomes.to_dict(),
            "budgets": budgets.to_dict(),
            "transfer_panels": [panel.to_dict() for panel in transfer_panels],
            "search_visible_case_ids": list(search_visible_case_ids),
            "selection_protocol": selection_protocol,
            "stopping_policy": (
                stopping_policy or MeasurementEarlyStopPolicy()
            ).to_dict(),
        }
        experiment_id = "experiment-" + _digest(identity_payload)[:32]
        return cls(
            experiment_id=experiment_id,
            run_id=run_id,
            mode=MeasurementPolicyMode(mode),
            swap_axis=axis,
            changed_axes=axes,
            control=control,
            treatment=treatment,
            frozen_identities=frozen_identities,
            sampling=sampling,
            outcomes=outcomes,
            budgets=budgets,
            transfer_panels=tuple(transfer_panels),
            search_visible_case_ids=tuple(search_visible_case_ids),
            selection_protocol=selection_protocol,
            stopping_policy=stopping_policy or MeasurementEarlyStopPolicy(),
            created_at=created_at,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "run_id": self.run_id,
            "mode": self.mode.value,
            "swap_axis": self.swap_axis.value,
            "changed_axes": [axis.value for axis in self.changed_axes],
            "control": self.control.to_dict(),
            "treatment": self.treatment.to_dict(),
            "frozen_identities": self.frozen_identities.to_dict(),
            "sampling": self.sampling.to_dict(),
            "outcomes": self.outcomes.to_dict(),
            "budgets": self.budgets.to_dict(),
            "transfer_panels": [panel.to_dict() for panel in self.transfer_panels],
            "search_visible_case_ids": list(self.search_visible_case_ids),
            "selection_protocol": self.selection_protocol,
            "stopping_policy": self.stopping_policy.to_dict(),
            "created_at": self.created_at,
            **dict(self.extensions),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ControlledExperimentSpec":
        _require_schema(value, CONTROLLED_EXPERIMENT_SCHEMA_VERSION, "controlled experiment")
        control = _mapping(value.get("control"), "control")
        treatment = _mapping(value.get("treatment"), "treatment")
        identities = _mapping(value.get("frozen_identities"), "frozen_identities")
        sampling = _mapping(value.get("sampling"), "sampling")
        outcomes = _mapping(value.get("outcomes"), "outcomes")
        budgets = _mapping(value.get("budgets"), "budgets")
        stopping_policy = _mapping(
            value.get("stopping_policy", {}),
            "stopping_policy",
        )
        raw_panels = _sequence(value.get("transfer_panels", ()), "transfer_panels")
        loaded = cls(
            experiment_id=_required_text(value.get("experiment_id"), "experiment_id"),
            run_id=_required_text(value.get("run_id"), "run_id"),
            mode=MeasurementPolicyMode(str(value.get("mode"))),
            swap_axis=SwapAxis(str(value.get("swap_axis"))),
            changed_axes=tuple(
                SwapAxis(item) for item in _string_tuple(value.get("changed_axes"))
            ),
            control=ComponentIdentity.from_dict(control),
            treatment=ComponentIdentity.from_dict(treatment),
            frozen_identities=FrozenIdentities.from_dict(identities),
            sampling=SamplingPlan.from_dict(sampling),
            outcomes=OutcomePlan.from_dict(outcomes),
            budgets=ExperimentBudget.from_dict(budgets),
            transfer_panels=tuple(
                TransferPanel.from_dict(_mapping(item, "transfer panel"))
                for item in raw_panels
            ),
            search_visible_case_ids=_string_tuple(
                value.get("search_visible_case_ids")
            ),
            selection_protocol=str(
                value.get("selection_protocol") or "predeclared_candidate"
            ),
            stopping_policy=MeasurementEarlyStopPolicy.from_dict(
                stopping_policy
            ),
            created_at=_optional_text(value.get("created_at")),
            extensions={
                key: item
                for key, item in value.items()
                if key not in _CONTROLLED_EXPERIMENT_KNOWN_FIELDS
            },
        )
        expected = cls.create(
            run_id=loaded.run_id,
            mode=loaded.mode,
            swap_axis=loaded.swap_axis,
            changed_axes=loaded.changed_axes,
            control=loaded.control,
            treatment=loaded.treatment,
            frozen_identities=loaded.frozen_identities,
            sampling=loaded.sampling,
            outcomes=loaded.outcomes,
            budgets=loaded.budgets,
            transfer_panels=loaded.transfer_panels,
            search_visible_case_ids=loaded.search_visible_case_ids,
            selection_protocol=loaded.selection_protocol,
            stopping_policy=loaded.stopping_policy,
            created_at=loaded.created_at,
        ).experiment_id
        if loaded.experiment_id != expected:
            raise ValueError("experiment id does not match canonical contract")
        return loaded


@dataclass(frozen=True)
class MeasurementObservation:
    observation_id: str
    experiment_id: str
    run_id: str
    arm: ArmRole
    swap_axis: SwapAxis
    case_id: str
    case_fingerprint: str
    split: str
    repetition_index: int
    seed: int | None
    component_fingerprint: str | None
    frozen_identity_fingerprint: str
    execution_status: ObservationExecutionStatus
    comparability: ComparabilityStatus
    comparability_reason: str | None
    task_success: bool | None
    metrics: Mapping[str, float | int | bool | None]
    usage: MeasurementUsage
    panel_id: str | None = None
    artifact_refs: tuple[str, ...] = ()
    failure_owner: str | None = None
    failure_stage: str | None = None
    failure_scope: str | None = None
    failure_code: str | None = None
    schema_version: str = MEASUREMENT_OBSERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MEASUREMENT_OBSERVATION_SCHEMA_VERSION:
            raise ValueError("unsupported measurement observation schema")
        if not re.fullmatch(r"observation-[0-9a-f]{32}", self.observation_id):
            raise ValueError("invalid observation_id")
        if not re.fullmatch(r"experiment-[0-9a-f]{32}", self.experiment_id):
            raise ValueError("invalid experiment_id")
        _safe_id(self.run_id, "run_id")
        _safe_id(self.case_id, "case_id")
        _fingerprint(self.case_fingerprint, "case_fingerprint")
        _optional_fingerprint(self.component_fingerprint, "component_fingerprint")
        _fingerprint(self.frozen_identity_fingerprint, "frozen_identity_fingerprint")
        if isinstance(self.repetition_index, bool) or self.repetition_index <= 0:
            raise ValueError("repetition_index must be positive")
        if self.seed is not None and isinstance(self.seed, bool):
            raise ValueError("observation seed must be an integer")
        object.__setattr__(self, "arm", ArmRole(self.arm))
        object.__setattr__(self, "swap_axis", SwapAxis(self.swap_axis))
        object.__setattr__(
            self, "execution_status", ObservationExecutionStatus(self.execution_status)
        )
        object.__setattr__(self, "comparability", ComparabilityStatus(self.comparability))
        sanitized_metrics: dict[str, float | int | bool | None] = {}
        for name, metric in self.metrics.items():
            _safe_metric_name(str(name))
            if metric is not None and not isinstance(metric, (int, float, bool)):
                raise ValueError("measurement metrics must be scalar")
            if isinstance(metric, float) and not math.isfinite(metric):
                raise ValueError("measurement metrics must be finite")
            sanitized_metrics[str(name)] = metric
        object.__setattr__(self, "metrics", sanitized_metrics)
        refs = tuple(dict.fromkeys(self.artifact_refs))
        for ref in refs:
            _safe_artifact_ref(ref)
        object.__setattr__(self, "artifact_refs", refs)

    @classmethod
    def create(
        cls,
        *,
        experiment: ControlledExperimentSpec,
        arm: ArmRole | str,
        case_id: str,
        case_fingerprint: str,
        split: str,
        repetition_index: int,
        seed: int | None,
        component_fingerprint: str | None,
        execution_status: ObservationExecutionStatus | str,
        comparability: ComparabilityStatus | str,
        task_success: bool | None,
        metrics: Mapping[str, float | int | bool | None] | None = None,
        usage: MeasurementUsage | None = None,
        comparability_reason: str | None = None,
        panel_id: str | None = None,
        artifact_refs: Sequence[str] = (),
        failure_owner: str | None = None,
        failure_stage: str | None = None,
        failure_scope: str | None = None,
        failure_code: str | None = None,
    ) -> "MeasurementObservation":
        payload = {
            "experiment_id": experiment.experiment_id,
            "run_id": experiment.run_id,
            "arm": ArmRole(arm).value,
            "case_id": case_id,
            "repetition_index": repetition_index,
            "seed": seed,
            "panel_id": panel_id,
        }
        return cls(
            observation_id="observation-" + _digest(payload)[:32],
            experiment_id=experiment.experiment_id,
            run_id=experiment.run_id,
            arm=ArmRole(arm),
            swap_axis=experiment.swap_axis,
            case_id=case_id,
            case_fingerprint=case_fingerprint,
            split=split,
            repetition_index=repetition_index,
            seed=seed,
            component_fingerprint=component_fingerprint,
            frozen_identity_fingerprint=experiment.frozen_identities.fingerprint,
            execution_status=ObservationExecutionStatus(execution_status),
            comparability=ComparabilityStatus(comparability),
            comparability_reason=comparability_reason,
            task_success=task_success,
            metrics=dict(metrics or {}),
            usage=usage or MeasurementUsage(),
            panel_id=panel_id,
            artifact_refs=tuple(artifact_refs),
            failure_owner=failure_owner,
            failure_stage=failure_stage,
            failure_scope=failure_scope,
            failure_code=failure_code,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "observation_id": self.observation_id,
            "experiment_id": self.experiment_id,
            "run_id": self.run_id,
            "arm": self.arm.value,
            "swap_axis": self.swap_axis.value,
            "case_id": self.case_id,
            "case_fingerprint": self.case_fingerprint,
            "split": self.split,
            "panel_id": self.panel_id,
            "repetition_index": self.repetition_index,
            "seed": self.seed,
            "component_fingerprint": self.component_fingerprint,
            "frozen_identity_fingerprint": self.frozen_identity_fingerprint,
            "execution_status": self.execution_status.value,
            "comparability": self.comparability.value,
            "comparability_reason": self.comparability_reason,
            "task_success": self.task_success,
            "metrics": dict(self.metrics),
            "usage": self.usage.to_dict(),
            "artifact_refs": list(self.artifact_refs),
            "failure_owner": self.failure_owner,
            "failure_stage": self.failure_stage,
            "failure_scope": self.failure_scope,
            "failure_code": self.failure_code,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "MeasurementObservation":
        _require_schema(value, MEASUREMENT_OBSERVATION_SCHEMA_VERSION, "observation")
        metrics = _mapping(value.get("metrics", {}), "metrics")
        usage = _mapping(value.get("usage", {}), "usage")
        loaded = cls(
            observation_id=_required_text(value.get("observation_id"), "observation_id"),
            experiment_id=_required_text(value.get("experiment_id"), "experiment_id"),
            run_id=_required_text(value.get("run_id"), "run_id"),
            arm=ArmRole(str(value.get("arm"))),
            swap_axis=SwapAxis(str(value.get("swap_axis"))),
            case_id=_required_text(value.get("case_id"), "case_id"),
            case_fingerprint=_required_text(value.get("case_fingerprint"), "case_fingerprint"),
            split=_required_text(value.get("split"), "split"),
            panel_id=_optional_text(value.get("panel_id")),
            repetition_index=_positive_int(value.get("repetition_index"), "repetition_index"),
            seed=_optional_int(value.get("seed")),
            component_fingerprint=_optional_text(value.get("component_fingerprint")),
            frozen_identity_fingerprint=_required_text(
                value.get("frozen_identity_fingerprint"), "frozen_identity_fingerprint"
            ),
            execution_status=ObservationExecutionStatus(str(value.get("execution_status"))),
            comparability=ComparabilityStatus(str(value.get("comparability"))),
            comparability_reason=_optional_text(value.get("comparability_reason")),
            task_success=value.get("task_success") if isinstance(value.get("task_success"), bool) else None,
            metrics=dict(metrics),
            usage=MeasurementUsage.from_dict(usage),
            artifact_refs=_string_tuple(value.get("artifact_refs")),
            failure_owner=_optional_text(value.get("failure_owner")),
            failure_stage=_optional_text(value.get("failure_stage")),
            failure_scope=_optional_text(value.get("failure_scope")),
            failure_code=_optional_text(value.get("failure_code")),
        )
        expected_id = "observation-" + _digest(
            {
                "experiment_id": loaded.experiment_id,
                "run_id": loaded.run_id,
                "arm": loaded.arm.value,
                "case_id": loaded.case_id,
                "repetition_index": loaded.repetition_index,
                "seed": loaded.seed,
                "panel_id": loaded.panel_id,
            }
        )[:32]
        if loaded.observation_id != expected_id:
            raise ValueError("observation id does not match coordinates")
        return loaded


@dataclass(frozen=True)
class ExperimentValidity:
    status: ExperimentValidityStatus
    reason_codes: tuple[str, ...]
    independent_case_count: int
    repetition_count: int
    comparable_pair_count: int
    incomparable_pair_count: int
    scheduled_pair_count: int
    completed_arm_count: int
    failed_arm_count: int
    timed_out_arm_count: int
    blocked_arm_count: int
    missing_arm_count: int
    identity_complete: bool
    control_viable: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "reason_codes": list(self.reason_codes),
            "independent_case_count": self.independent_case_count,
            "repetition_count": self.repetition_count,
            "comparable_pair_count": self.comparable_pair_count,
            "incomparable_pair_count": self.incomparable_pair_count,
            "scheduled_pair_count": self.scheduled_pair_count,
            "completed_arm_count": self.completed_arm_count,
            "failed_arm_count": self.failed_arm_count,
            "timed_out_arm_count": self.timed_out_arm_count,
            "blocked_arm_count": self.blocked_arm_count,
            "missing_arm_count": self.missing_arm_count,
            "identity_complete": self.identity_complete,
            "control_viable": self.control_viable,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ExperimentValidity":
        return cls(
            status=ExperimentValidityStatus(str(value.get("status"))),
            reason_codes=_string_tuple(value.get("reason_codes")),
            independent_case_count=_non_negative_int(value.get("independent_case_count", 0), "independent_case_count"),
            repetition_count=_non_negative_int(value.get("repetition_count", 0), "repetition_count"),
            comparable_pair_count=_non_negative_int(value.get("comparable_pair_count", 0), "comparable_pair_count"),
            incomparable_pair_count=_non_negative_int(value.get("incomparable_pair_count", 0), "incomparable_pair_count"),
            scheduled_pair_count=_non_negative_int(value.get("scheduled_pair_count", 0), "scheduled_pair_count"),
            completed_arm_count=_non_negative_int(value.get("completed_arm_count", 0), "completed_arm_count"),
            failed_arm_count=_non_negative_int(
                value.get("failed_arm_count", 0), "failed_arm_count"
            ),
            timed_out_arm_count=_non_negative_int(
                value.get("timed_out_arm_count", 0), "timed_out_arm_count"
            ),
            blocked_arm_count=_non_negative_int(
                value.get("blocked_arm_count", 0), "blocked_arm_count"
            ),
            missing_arm_count=_non_negative_int(
                value.get("missing_arm_count", 0), "missing_arm_count"
            ),
            identity_complete=value.get("identity_complete") is True,
            control_viable=value.get("control_viable") is True,
        )


@dataclass(frozen=True)
class EffectEstimate:
    metric: str
    point_estimate: float
    confidence_lower_bound: float | None
    confidence_upper_bound: float | None
    confidence_level: float
    minimum_effect: float
    non_regression_threshold: float
    direction: EffectDirection
    estimator_version: str
    aggregation: str
    independent_case_count: int
    repetition_count: int
    multiple_comparison_policy: str

    def to_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "point_estimate": self.point_estimate,
            "confidence_lower_bound": self.confidence_lower_bound,
            "confidence_upper_bound": self.confidence_upper_bound,
            "confidence_level": self.confidence_level,
            "minimum_effect": self.minimum_effect,
            "non_regression_threshold": self.non_regression_threshold,
            "direction": self.direction.value,
            "estimator_version": self.estimator_version,
            "aggregation": self.aggregation,
            "independent_case_count": self.independent_case_count,
            "repetition_count": self.repetition_count,
            "multiple_comparison_policy": self.multiple_comparison_policy,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "EffectEstimate":
        return cls(
            metric=_required_text(value.get("metric"), "metric"),
            point_estimate=_finite_number(value.get("point_estimate")),
            confidence_lower_bound=_optional_finite_number(value.get("confidence_lower_bound")),
            confidence_upper_bound=_optional_finite_number(value.get("confidence_upper_bound")),
            confidence_level=_finite_number(value.get("confidence_level")),
            minimum_effect=_finite_number(value.get("minimum_effect")),
            non_regression_threshold=_finite_number(value.get("non_regression_threshold", 0.0)),
            direction=EffectDirection(str(value.get("direction"))),
            estimator_version=_required_text(value.get("estimator_version"), "estimator_version"),
            aggregation=_required_text(value.get("aggregation"), "aggregation"),
            independent_case_count=_non_negative_int(value.get("independent_case_count", 0), "independent_case_count"),
            repetition_count=_non_negative_int(value.get("repetition_count", 0), "repetition_count"),
            multiple_comparison_policy=_required_text(
                value.get("multiple_comparison_policy"), "multiple_comparison_policy"
            ),
        )


@dataclass(frozen=True)
class TargetResolutionConfidence:
    confidence: float | None
    origin: str
    inference_bypassed: bool
    causal_confidence: float | None = None

    def __post_init__(self) -> None:
        for name in ("confidence", "causal_confidence"):
            value = getattr(self, name)
            if value is not None and not 0 <= float(value) <= 1:
                raise ValueError(f"{name} must be between zero and one")

    def to_dict(self) -> dict[str, object]:
        return {
            "confidence": self.confidence,
            "origin": self.origin,
            "inference_bypassed": self.inference_bypassed,
            "causal_confidence": self.causal_confidence,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TargetResolutionConfidence":
        return cls(
            confidence=_optional_probability(value.get("confidence")),
            origin=str(value.get("origin") or "unknown"),
            inference_bypassed=value.get("inference_bypassed") is True,
            causal_confidence=_optional_probability(value.get("causal_confidence")),
        )


@dataclass(frozen=True)
class MeasurementReadiness:
    previous_stage: str | None = None
    current_stage: str = "experiment_planned"

    @property
    def rank(self) -> int:
        return _READINESS_RANK.get(self.current_stage, 0)

    @property
    def progressed(self) -> bool:
        return self.rank > _READINESS_RANK.get(self.previous_stage or "unplanned", 0)

    def to_dict(self) -> dict[str, object]:
        return {
            "previous_stage": self.previous_stage,
            "current_stage": self.current_stage,
            "rank": self.rank,
            "progressed": self.progressed,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "MeasurementReadiness":
        return cls(
            previous_stage=_optional_text(value.get("previous_stage")),
            current_stage=str(value.get("current_stage") or "unplanned"),
        )


@dataclass(frozen=True)
class TransferAudit:
    panel_id: str
    role: TransferPanelRole
    passed: bool
    required: bool = True
    reason_codes: tuple[str, ...] = ()
    effect: EffectEstimate | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "panel_id": self.panel_id,
            "role": self.role.value,
            "passed": self.passed,
            "required": self.required,
            "reason_codes": list(self.reason_codes),
            "effect": self.effect.to_dict() if self.effect is not None else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TransferAudit":
        raw_effect = value.get("effect")
        return cls(
            panel_id=_required_text(value.get("panel_id"), "panel_id"),
            role=TransferPanelRole(str(value.get("role"))),
            passed=value.get("passed") is True,
            required=value.get("required") is not False,
            reason_codes=_string_tuple(value.get("reason_codes")),
            effect=EffectEstimate.from_dict(raw_effect) if isinstance(raw_effect, Mapping) else None,
        )


@dataclass(frozen=True)
class SearchCandidateResult:
    candidate_id: str
    score: float | None
    passed: bool
    valid: bool = True
    authoritative: bool = False
    tokens: int | None = None
    wall_seconds: float | None = None
    cost_usd: float | None = None
    regression_passed: bool | None = None

    def __post_init__(self) -> None:
        _safe_id(self.candidate_id, "candidate_id")
        if self.score is not None and not math.isfinite(float(self.score)):
            raise ValueError("candidate score must be finite")
        if self.tokens is not None and (isinstance(self.tokens, bool) or self.tokens < 0):
            raise ValueError("candidate tokens must be non-negative")
        for name in ("wall_seconds", "cost_usd"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(float(value)) or float(value) < 0):
                raise ValueError(f"candidate {name} must be non-negative and finite")


@dataclass(frozen=True)
class SearchKPoint:
    requested_k: int
    actual_k: int
    best_score: float | None
    pass_probability: float
    valid_candidate_count: int
    authoritative_candidate_count: int

    def __post_init__(self) -> None:
        for name in (
            "requested_k",
            "actual_k",
            "valid_candidate_count",
            "authoritative_candidate_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.requested_k == 0:
            raise ValueError("requested_k must be positive")
        _probability(self.pass_probability, "pass_probability")

    def to_dict(self) -> dict[str, object]:
        return {
            "requested_k": self.requested_k,
            "actual_k": self.actual_k,
            "best_score": self.best_score,
            "pass_probability": self.pass_probability,
            "valid_candidate_count": self.valid_candidate_count,
            "authoritative_candidate_count": self.authoritative_candidate_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SearchKPoint":
        return cls(
            requested_k=_positive_int(value.get("requested_k"), "requested_k"),
            actual_k=_non_negative_int(value.get("actual_k", 0), "actual_k"),
            best_score=_optional_finite_number(value.get("best_score")),
            pass_probability=_finite_number(value.get("pass_probability", 0.0)),
            valid_candidate_count=_non_negative_int(
                value.get("valid_candidate_count", 0), "valid_candidate_count"
            ),
            authoritative_candidate_count=_non_negative_int(
                value.get("authoritative_candidate_count", 0),
                "authoritative_candidate_count",
            ),
        )


@dataclass(frozen=True)
class SearchBudgetPoint:
    requested_budget: float
    actual_budget: float
    candidate_count: int
    best_score: float | None
    passed: bool
    validity_rate: float = 0.0
    regression_pass_rate: float | None = None
    cumulative_tokens: int | None = None
    cumulative_cost_usd: float | None = None
    cumulative_wall_seconds: float | None = None

    def __post_init__(self) -> None:
        for name in ("requested_budget", "actual_budget"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be non-negative and finite")
            object.__setattr__(self, name, value)
        if (
            isinstance(self.candidate_count, bool)
            or not isinstance(self.candidate_count, int)
            or self.candidate_count < 0
        ):
            raise ValueError("candidate_count must be a non-negative integer")
        _probability(self.validity_rate, "validity_rate")
        if self.regression_pass_rate is not None:
            _probability(self.regression_pass_rate, "regression_pass_rate")

    def to_dict(self) -> dict[str, object]:
        return {
            "requested_budget": self.requested_budget,
            "actual_budget": self.actual_budget,
            "candidate_count": self.candidate_count,
            "best_score": self.best_score,
            "passed": self.passed,
            "validity_rate": self.validity_rate,
            "regression_pass_rate": self.regression_pass_rate,
            "cumulative_tokens": self.cumulative_tokens,
            "cumulative_cost_usd": self.cumulative_cost_usd,
            "cumulative_wall_seconds": self.cumulative_wall_seconds,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SearchBudgetPoint":
        return cls(
            requested_budget=_finite_number(value.get("requested_budget", 0.0)),
            actual_budget=_finite_number(value.get("actual_budget", 0.0)),
            candidate_count=_non_negative_int(
                value.get("candidate_count", 0), "candidate_count"
            ),
            best_score=_optional_finite_number(value.get("best_score")),
            passed=value.get("passed") is True,
            validity_rate=_finite_number(value.get("validity_rate", 0.0)),
            regression_pass_rate=_optional_probability(
                value.get("regression_pass_rate")
            ),
            cumulative_tokens=_optional_non_negative_int(
                value.get("cumulative_tokens")
            ),
            cumulative_cost_usd=_optional_non_negative_number(
                value.get("cumulative_cost_usd")
            ),
            cumulative_wall_seconds=_optional_non_negative_number(
                value.get("cumulative_wall_seconds")
            ),
        )


@dataclass(frozen=True)
class SearchPerformance:
    candidate_count: int
    k_points: tuple[SearchKPoint, ...]
    token_curve: tuple[SearchBudgetPoint, ...]
    wall_time_curve: tuple[SearchBudgetPoint, ...]
    validity_rate: float
    authoritative_yield: float
    selection_protocol: str
    quality_threshold: float | None = None
    tokens_to_threshold: int | None = None
    wall_seconds_to_threshold: float | None = None
    schema_version: str = SEARCH_PERFORMANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SEARCH_PERFORMANCE_SCHEMA_VERSION:
            raise ValueError("unsupported search performance schema")
        _probability(self.validity_rate, "validity_rate")
        _probability(self.authoritative_yield, "authoritative_yield")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "candidate_count": self.candidate_count,
            "k_points": [point.to_dict() for point in self.k_points],
            "token_curve": [point.to_dict() for point in self.token_curve],
            "wall_time_curve": [point.to_dict() for point in self.wall_time_curve],
            "validity_rate": self.validity_rate,
            "authoritative_yield": self.authoritative_yield,
            "selection_protocol": self.selection_protocol,
            "quality_threshold": self.quality_threshold,
            "tokens_to_threshold": self.tokens_to_threshold,
            "wall_seconds_to_threshold": self.wall_seconds_to_threshold,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "SearchPerformance":
        _require_schema(value, SEARCH_PERFORMANCE_SCHEMA_VERSION, "search performance")
        k_points = _sequence(value.get("k_points", ()), "k_points")
        token_curve = _sequence(value.get("token_curve", ()), "token_curve")
        wall_curve = _sequence(
            value.get("wall_time_curve", ()), "wall_time_curve"
        )
        return cls(
            candidate_count=_non_negative_int(
                value.get("candidate_count", 0), "candidate_count"
            ),
            k_points=tuple(
                SearchKPoint.from_dict(_mapping(item, "search K point"))
                for item in k_points
            ),
            token_curve=tuple(
                SearchBudgetPoint.from_dict(_mapping(item, "token curve point"))
                for item in token_curve
            ),
            wall_time_curve=tuple(
                SearchBudgetPoint.from_dict(_mapping(item, "wall curve point"))
                for item in wall_curve
            ),
            validity_rate=_finite_number(value.get("validity_rate", 0.0)),
            authoritative_yield=_finite_number(
                value.get("authoritative_yield", 0.0)
            ),
            selection_protocol=_required_text(
                value.get("selection_protocol"), "selection_protocol"
            ),
            quality_threshold=_optional_finite_number(
                value.get("quality_threshold")
            ),
            tokens_to_threshold=_optional_non_negative_int(
                value.get("tokens_to_threshold")
            ),
            wall_seconds_to_threshold=_optional_non_negative_number(
                value.get("wall_seconds_to_threshold")
            ),
        )


@dataclass(frozen=True)
class SearchPerformanceComparison:
    opportunity_matched: bool
    attribution_allowed: bool
    shared_k: tuple[int, ...]
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "opportunity_matched": self.opportunity_matched,
            "attribution_allowed": self.attribution_allowed,
            "shared_k": list(self.shared_k),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class MeasurementYield:
    generated_candidate_count: int
    total_usage: MeasurementUsage
    comparable_pair_count: int
    search_tokens: int | None = None
    authoritative_candidate_count: int = 0
    conclusive_experiment_count: int = 0

    @property
    def comparable_pairs_per_100k_tokens(self) -> float | None:
        tokens = self.total_usage.tokens
        if tokens is None or tokens <= 0:
            return None
        return self.comparable_pair_count * 100_000.0 / tokens

    @property
    def authoritative_candidates_per_100k_tokens(self) -> float | None:
        tokens = self.search_tokens
        if tokens is None or tokens <= 0:
            return None
        return self.authoritative_candidate_count * 100_000.0 / tokens

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_candidate_count": self.generated_candidate_count,
            "total_usage": self.total_usage.to_dict(),
            "comparable_pair_count": self.comparable_pair_count,
            "search_tokens": self.search_tokens,
            "authoritative_candidate_count": self.authoritative_candidate_count,
            "conclusive_experiment_count": self.conclusive_experiment_count,
            "comparable_pairs_per_100k_tokens": self.comparable_pairs_per_100k_tokens,
            "authoritative_candidates_per_100k_tokens": self.authoritative_candidates_per_100k_tokens,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "MeasurementYield":
        usage = _mapping(value.get("total_usage", {}), "total_usage")
        return cls(
            generated_candidate_count=_non_negative_int(value.get("generated_candidate_count", 0), "generated_candidate_count"),
            total_usage=MeasurementUsage.from_dict(usage),
            comparable_pair_count=_non_negative_int(value.get("comparable_pair_count", 0), "comparable_pair_count"),
            search_tokens=_optional_non_negative_int(value.get("search_tokens")),
            authoritative_candidate_count=_non_negative_int(value.get("authoritative_candidate_count", 0), "authoritative_candidate_count"),
            conclusive_experiment_count=_non_negative_int(value.get("conclusive_experiment_count", 0), "conclusive_experiment_count"),
        )


@dataclass(frozen=True)
class MeasurementDecision:
    promotion_eligible: bool
    next_action: MeasurementNextAction
    reason: str
    owner: str | None = None
    policy_authoritative: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "promotion_eligible": self.promotion_eligible,
            "next_action": self.next_action.value,
            "reason": self.reason,
            "owner": self.owner,
            "policy_authoritative": self.policy_authoritative,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "MeasurementDecision":
        return cls(
            promotion_eligible=value.get("promotion_eligible") is True,
            next_action=MeasurementNextAction(str(value.get("next_action"))),
            reason=_required_text(value.get("reason"), "decision reason"),
            owner=_optional_text(value.get("owner")),
            policy_authoritative=value.get("policy_authoritative") is True,
        )


@dataclass(frozen=True)
class MeasurementEarlyStopPolicy:
    zero_yield_patience: int = 2
    invalid_control_patience: int = 2
    stop_on_decisive_regression: bool = True
    maximum_interval_width: float | None = None

    def __post_init__(self) -> None:
        for name in ("zero_yield_patience", "invalid_control_patience"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.maximum_interval_width is not None:
            width = float(self.maximum_interval_width)
            if not math.isfinite(width) or width < 0:
                raise ValueError(
                    "maximum_interval_width must be non-negative and finite"
                )
            object.__setattr__(self, "maximum_interval_width", width)

    def to_dict(self) -> dict[str, object]:
        return {
            "zero_yield_patience": self.zero_yield_patience,
            "invalid_control_patience": self.invalid_control_patience,
            "stop_on_decisive_regression": self.stop_on_decisive_regression,
            "maximum_interval_width": self.maximum_interval_width,
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
    ) -> "MeasurementEarlyStopPolicy":
        return cls(
            zero_yield_patience=_positive_int(
                value.get("zero_yield_patience", 2),
                "zero_yield_patience",
            ),
            invalid_control_patience=_positive_int(
                value.get("invalid_control_patience", 2),
                "invalid_control_patience",
            ),
            stop_on_decisive_regression=(
                value.get("stop_on_decisive_regression") is not False
            ),
            maximum_interval_width=_optional_non_negative_number(
                value.get("maximum_interval_width")
            ),
        )


@dataclass(frozen=True)
class MeasurementStopRecord:
    triggered: bool
    trigger: MeasurementStopTrigger | None
    evidence_experiment_ids: tuple[str, ...]
    unused_budget: MeasurementUsage
    resume_safe: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "triggered": self.triggered,
            "trigger": self.trigger.value if self.trigger is not None else None,
            "evidence_experiment_ids": list(self.evidence_experiment_ids),
            "unused_budget": self.unused_budget.to_dict(),
            "resume_safe": self.resume_safe,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "MeasurementStopRecord":
        raw_trigger = value.get("trigger")
        return cls(
            triggered=value.get("triggered") is True,
            trigger=(
                MeasurementStopTrigger(str(raw_trigger))
                if raw_trigger is not None
                else None
            ),
            evidence_experiment_ids=_string_tuple(
                value.get("evidence_experiment_ids")
            ),
            unused_budget=MeasurementUsage.from_dict(
                _mapping(value.get("unused_budget", {}), "unused_budget")
            ),
            resume_safe=value.get("resume_safe") is True,
            reason=_required_text(value.get("reason"), "stop reason"),
        )


@dataclass(frozen=True)
class MeasurementSummary:
    experiment_id: str
    mode: MeasurementPolicyMode
    swap_axis: SwapAxis
    validity_status: ExperimentValidityStatus
    effect_direction: EffectDirection
    effect_estimate: float | None
    confidence_lower_bound: float | None
    confidence_upper_bound: float | None
    budget_normalized: bool
    promotion_eligible: bool
    decision_reason: str
    next_action: MeasurementNextAction
    attribution_report_path: str | None
    independent_case_count: int
    comparable_pair_count: int
    measurement_readiness_stage: str
    comparable_pairs_per_100k_tokens: float | None = None
    dominant_budget_use: str | None = None
    required_transfer_failure_count: int = 0
    stopping_trigger: MeasurementStopTrigger | None = None
    schema_version: str = MEASUREMENT_SUMMARY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MEASUREMENT_SUMMARY_SCHEMA_VERSION:
            raise ValueError("unsupported measurement summary schema")
        if self.attribution_report_path is not None:
            _safe_artifact_ref(self.attribution_report_path)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "mode": self.mode.value,
            "swap_axis": self.swap_axis.value,
            "status": self.validity_status.value,
            "validity_status": self.validity_status.value,
            "effect_direction": self.effect_direction.value,
            "effect_estimate": self.effect_estimate,
            "confidence_lower_bound": self.confidence_lower_bound,
            "confidence_upper_bound": self.confidence_upper_bound,
            "budget_normalized": self.budget_normalized,
            "promotion_eligible": self.promotion_eligible,
            "decision_reason": self.decision_reason,
            "next_action": self.next_action.value,
            "attribution_report_path": self.attribution_report_path,
            "independent_case_count": self.independent_case_count,
            "comparable_pair_count": self.comparable_pair_count,
            "measurement_readiness_stage": self.measurement_readiness_stage,
            "comparable_pairs_per_100k_tokens": (
                self.comparable_pairs_per_100k_tokens
            ),
            "dominant_budget_use": self.dominant_budget_use,
            "required_transfer_failure_count": (
                self.required_transfer_failure_count
            ),
            "stopping_trigger": (
                self.stopping_trigger.value
                if self.stopping_trigger is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "MeasurementSummary":
        _require_schema(value, MEASUREMENT_SUMMARY_SCHEMA_VERSION, "measurement summary")
        return cls(
            experiment_id=_required_text(value.get("experiment_id"), "experiment_id"),
            mode=MeasurementPolicyMode(str(value.get("mode"))),
            swap_axis=SwapAxis(str(value.get("swap_axis"))),
            validity_status=ExperimentValidityStatus(str(value.get("validity_status"))),
            effect_direction=EffectDirection(str(value.get("effect_direction"))),
            effect_estimate=_optional_finite_number(value.get("effect_estimate")),
            confidence_lower_bound=_optional_finite_number(value.get("confidence_lower_bound")),
            confidence_upper_bound=_optional_finite_number(
                value.get("confidence_upper_bound")
            ),
            budget_normalized=value.get("budget_normalized") is True,
            promotion_eligible=value.get("promotion_eligible") is True,
            decision_reason=_required_text(value.get("decision_reason"), "decision_reason"),
            next_action=MeasurementNextAction(str(value.get("next_action"))),
            attribution_report_path=_optional_text(value.get("attribution_report_path")),
            independent_case_count=_non_negative_int(value.get("independent_case_count", 0), "independent_case_count"),
            comparable_pair_count=_non_negative_int(value.get("comparable_pair_count", 0), "comparable_pair_count"),
            measurement_readiness_stage=str(value.get("measurement_readiness_stage") or "unplanned"),
            comparable_pairs_per_100k_tokens=_optional_non_negative_number(
                value.get("comparable_pairs_per_100k_tokens")
            ),
            dominant_budget_use=_optional_text(value.get("dominant_budget_use")),
            required_transfer_failure_count=_non_negative_int(
                value.get("required_transfer_failure_count", 0),
                "required_transfer_failure_count",
            ),
            stopping_trigger=(
                MeasurementStopTrigger(str(value.get("stopping_trigger")))
                if value.get("stopping_trigger") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class AttributionReport:
    experiment_id: str
    run_id: str
    mode: MeasurementPolicyMode
    swap_axis: SwapAxis
    status: ExperimentValidityStatus
    target_resolution: TargetResolutionConfidence
    validity: ExperimentValidity
    effect: EffectEstimate | None
    secondary_effects: tuple[EffectEstimate, ...]
    measurement_readiness: MeasurementReadiness
    budget_ledger: BudgetLedger
    measurement_yield: MeasurementYield
    decision: MeasurementDecision
    budget_normalized: bool
    transfer: tuple[TransferAudit, ...] = ()
    search_performance: SearchPerformance | None = None
    stopping: MeasurementStopRecord | None = None
    schema_version: str = ATTRIBUTION_REPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ATTRIBUTION_REPORT_SCHEMA_VERSION:
            raise ValueError("unsupported attribution report schema")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "run_id": self.run_id,
            "mode": self.mode.value,
            "swap_axis": self.swap_axis.value,
            "status": self.status.value,
            "target_resolution": self.target_resolution.to_dict(),
            "validity": self.validity.to_dict(),
            "effect": self.effect.to_dict() if self.effect is not None else None,
            "secondary_effects": [effect.to_dict() for effect in self.secondary_effects],
            "measurement_readiness": self.measurement_readiness.to_dict(),
            "budget_ledger": self.budget_ledger.to_dict(),
            "measurement_yield": self.measurement_yield.to_dict(),
            "budget_normalized": self.budget_normalized,
            "transfer": [panel.to_dict() for panel in self.transfer],
            "search_performance": self.search_performance.to_dict() if self.search_performance is not None else None,
            "stopping": self.stopping.to_dict() if self.stopping is not None else None,
            "decision": self.decision.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "AttributionReport":
        _require_schema(value, ATTRIBUTION_REPORT_SCHEMA_VERSION, "attribution report")
        target = _mapping(value.get("target_resolution"), "target_resolution")
        validity = _mapping(value.get("validity"), "validity")
        readiness = _mapping(value.get("measurement_readiness"), "measurement_readiness")
        raw_budget_ledger = value.get("budget_ledger", {})
        yield_payload = _mapping(value.get("measurement_yield"), "measurement_yield")
        decision = _mapping(value.get("decision"), "decision")
        effect = value.get("effect")
        secondary = _sequence(value.get("secondary_effects", ()), "secondary_effects")
        transfer = _sequence(value.get("transfer", ()), "transfer")
        search_performance = value.get("search_performance")
        stopping = value.get("stopping")
        return cls(
            experiment_id=_required_text(value.get("experiment_id"), "experiment_id"),
            run_id=_required_text(value.get("run_id"), "run_id"),
            mode=MeasurementPolicyMode(str(value.get("mode"))),
            swap_axis=SwapAxis(str(value.get("swap_axis"))),
            status=ExperimentValidityStatus(str(value.get("status"))),
            target_resolution=TargetResolutionConfidence.from_dict(target),
            validity=ExperimentValidity.from_dict(validity),
            effect=EffectEstimate.from_dict(effect) if isinstance(effect, Mapping) else None,
            secondary_effects=tuple(
                EffectEstimate.from_dict(_mapping(item, "secondary effect"))
                for item in secondary
            ),
            measurement_readiness=MeasurementReadiness.from_dict(readiness),
            budget_ledger=BudgetLedger.from_dict(
                _mapping(raw_budget_ledger, "budget_ledger")
            ),
            measurement_yield=MeasurementYield.from_dict(yield_payload),
            decision=MeasurementDecision.from_dict(decision),
            budget_normalized=value.get("budget_normalized") is True,
            transfer=tuple(
                TransferAudit.from_dict(_mapping(item, "transfer audit"))
                for item in transfer
            ),
            search_performance=(
                SearchPerformance.from_dict(search_performance)
                if isinstance(search_performance, Mapping)
                else None
            ),
            stopping=(
                MeasurementStopRecord.from_dict(stopping)
                if isinstance(stopping, Mapping)
                else None
            ),
        )

    def summary(self, *, attribution_report_path: str | None = None) -> MeasurementSummary:
        return MeasurementSummary(
            experiment_id=self.experiment_id,
            mode=self.mode,
            swap_axis=self.swap_axis,
            validity_status=self.validity.status,
            effect_direction=(self.effect.direction if self.effect is not None else EffectDirection.UNMEASURED),
            effect_estimate=(self.effect.point_estimate if self.effect is not None else None),
            confidence_lower_bound=(self.effect.confidence_lower_bound if self.effect is not None else None),
            confidence_upper_bound=(
                self.effect.confidence_upper_bound
                if self.effect is not None
                else None
            ),
            budget_normalized=self.budget_normalized,
            promotion_eligible=self.decision.promotion_eligible,
            decision_reason=self.decision.reason,
            next_action=self.decision.next_action,
            attribution_report_path=attribution_report_path,
            independent_case_count=self.validity.independent_case_count,
            comparable_pair_count=self.validity.comparable_pair_count,
            measurement_readiness_stage=self.measurement_readiness.current_stage,
            comparable_pairs_per_100k_tokens=(
                self.measurement_yield.comparable_pairs_per_100k_tokens
            ),
            dominant_budget_use=self.budget_ledger.dominant_use,
            required_transfer_failure_count=sum(
                1 for item in self.transfer if item.required and not item.passed
            ),
            stopping_trigger=(
                self.stopping.trigger
                if self.stopping is not None and self.stopping.triggered
                else None
            ),
        )


@dataclass(frozen=True)
class MeasurementArtifactSnapshot:
    experiment: ControlledExperimentSpec
    observations: tuple[MeasurementObservation, ...]
    attribution: AttributionReport | None


class TrustedMeasurementService:
    """Thin framework API for plan, run/resume, inspect, and compare workflows."""

    def __init__(self, store: object) -> None:
        required = (
            "write_measurement_experiment",
            "read_measurement_experiment",
            "append_measurement_observations",
            "read_measurement_observations",
            "write_measurement_attribution_report",
            "read_measurement_attribution_report",
        )
        if any(not callable(getattr(store, name, None)) for name in required):
            raise TypeError("trusted measurement service requires an AWorld store")
        self.store = store

    def plan(self, experiment: ControlledExperimentSpec) -> ControlledExperimentSpec:
        self.store.write_measurement_experiment(experiment)
        return self.store.read_measurement_experiment(
            experiment.run_id,
            experiment.experiment_id,
        )

    def resume(
        self,
        run_id: str,
        experiment_id: str,
    ) -> MeasurementArtifactSnapshot:
        experiment = self.store.read_measurement_experiment(run_id, experiment_id)
        observations = self.store.read_measurement_observations(
            run_id,
            experiment_id,
            missing_ok=True,
        )
        try:
            attribution = self.store.read_measurement_attribution_report(
                run_id,
                experiment_id,
            )
        except FileNotFoundError:
            attribution = None
        return MeasurementArtifactSnapshot(
            experiment=experiment,
            observations=observations,
            attribution=attribution,
        )

    inspect = resume

    def execute(
        self,
        experiment: ControlledExperimentSpec,
        executor: Callable[
            [ControlledExperimentSpec, ArmRole, str, int, int | None],
            MeasurementObservation,
        ],
        *,
        target_resolution: TargetResolutionConfidence,
        readiness: MeasurementReadiness | None = None,
        search_usage: MeasurementUsage | None = None,
        measurement_usage: MeasurementUsage | None = None,
        generated_candidate_count: int = 0,
        authoritative_candidate_count: int = 0,
        transfer_audits: Sequence[TransferAudit] | None = None,
        search_performance: SearchPerformance | None = None,
        stopping: MeasurementStopRecord | None = None,
    ) -> AttributionReport:
        """Execute a frozen two-arm experiment for any declared swap axis.

        The executor receives no observation from the opposite arm, and both
        arms are invoked with the same case/repetition/seed coordinates.  This
        keeps generator, scheduler, task-model, and artifact experiments on one
        symmetric execution contract instead of relabeling artifact replay.
        """

        self.plan(experiment)
        existing = self.store.read_measurement_observations(
            experiment.run_id,
            experiment.experiment_id,
            missing_ok=True,
        )
        completed = {
            (
                observation.arm,
                observation.case_id,
                observation.repetition_index,
                observation.seed,
            )
            for observation in existing
        }
        collected: list[MeasurementObservation] = []
        for case_id in experiment.sampling.independent_case_ids:
            for repetition in range(
                1,
                experiment.sampling.repetitions_per_case + 1,
            ):
                seed = (
                    experiment.sampling.seeds[repetition - 1]
                    if repetition <= len(experiment.sampling.seeds)
                    else None
                )
                for arm in (ArmRole.CONTROL, ArmRole.TREATMENT):
                    coordinate = (arm, case_id, repetition, seed)
                    if coordinate in completed:
                        continue
                    observation = executor(
                        experiment,
                        arm,
                        case_id,
                        repetition,
                        seed,
                    )
                    if not isinstance(observation, MeasurementObservation):
                        raise TypeError(
                            "controlled arm executor must return a measurement observation"
                        )
                    if (
                        observation.arm,
                        observation.case_id,
                        observation.repetition_index,
                        observation.seed,
                    ) != coordinate:
                        raise ValueError(
                            "controlled arm executor changed frozen sampling coordinates"
                        )
                    collected.append(observation)
        return self.run(
            experiment,
            collected,
            target_resolution=target_resolution,
            readiness=readiness,
            search_usage=search_usage,
            measurement_usage=measurement_usage,
            generated_candidate_count=generated_candidate_count,
            authoritative_candidate_count=authoritative_candidate_count,
            transfer_audits=transfer_audits,
            search_performance=search_performance,
            stopping=stopping,
        )

    def run(
        self,
        experiment: ControlledExperimentSpec,
        observations: Sequence[MeasurementObservation],
        *,
        target_resolution: TargetResolutionConfidence,
        readiness: MeasurementReadiness | None = None,
        search_usage: MeasurementUsage | None = None,
        measurement_usage: MeasurementUsage | None = None,
        generated_candidate_count: int = 0,
        authoritative_candidate_count: int = 0,
        transfer_audits: Sequence[TransferAudit] | None = None,
        search_performance: SearchPerformance | None = None,
        stopping: MeasurementStopRecord | None = None,
    ) -> AttributionReport:
        self.plan(experiment)
        self.store.append_measurement_observations(
            experiment.run_id,
            experiment.experiment_id,
            tuple(observations),
        )
        complete_observations = self.store.read_measurement_observations(
            experiment.run_id,
            experiment.experiment_id,
            missing_ok=True,
        )
        report = build_attribution_report(
            experiment,
            complete_observations,
            target_resolution=target_resolution,
            readiness=readiness,
            search_usage=search_usage,
            measurement_usage=measurement_usage,
            generated_candidate_count=generated_candidate_count,
            authoritative_candidate_count=authoritative_candidate_count,
            transfer_audits=transfer_audits,
            search_performance=search_performance,
            stopping=stopping,
        )
        self.store.write_measurement_attribution_report(report)
        return report

    def compare(
        self,
        control_run_id: str,
        control_experiment_id: str,
        treatment_run_id: str,
        treatment_experiment_id: str,
    ) -> SearchPerformanceComparison:
        control = self.store.read_measurement_attribution_report(
            control_run_id,
            control_experiment_id,
        )
        treatment = self.store.read_measurement_attribution_report(
            treatment_run_id,
            treatment_experiment_id,
        )
        if control.search_performance is None or treatment.search_performance is None:
            raise ValueError("both attribution reports require search performance")
        return compare_search_performance(
            control.search_performance,
            treatment.search_performance,
        )


def assess_experiment_validity(
    experiment: ControlledExperimentSpec,
    observations: Sequence[MeasurementObservation],
    *,
    admitted_primary_case_ids: Sequence[str] | None = None,
) -> ExperimentValidity:
    reasons: set[str] = set()
    if len(experiment.changed_axes) != 1 or experiment.changed_axes[0] is not experiment.swap_axis:
        reasons.add("multiple_swap_axes_changed")
    if experiment.frozen_identities.missing:
        reasons.add("missing_frozen_identity")
    if not experiment.control.complete or not experiment.treatment.complete:
        reasons.add("missing_component_identity")

    expected_components = {
        ArmRole.CONTROL: experiment.control.fingerprint,
        ArmRole.TREATMENT: experiment.treatment.fingerprint,
    }
    scheduled_primary_case_ids = tuple(
        dict.fromkeys(
            admitted_primary_case_ids
            if admitted_primary_case_ids is not None
            else experiment.sampling.independent_case_ids
        )
    )
    if not set(scheduled_primary_case_ids).issubset(
        experiment.sampling.independent_case_ids
    ):
        raise ValueError("admitted primary case is outside frozen sampling")
    grouped: dict[tuple[str, int, int | None, str | None], dict[ArmRole, MeasurementObservation]] = {}
    completed_arms = 0
    failed_arms = 0
    timed_out_arms = 0
    blocked_arms = 0
    not_run_arms = 0
    observed_coordinates: set[
        tuple[str, int, int | None, str | None, ArmRole]
    ] = set()
    identity_mismatch = False
    declared_transfer_case_ids = {
        case_id
        for panel in experiment.transfer_panels
        for case_id in panel.case_ids
    }
    for observation in observations:
        if observation.case_id in declared_transfer_case_ids:
            # Transfer observations have their own sealed audit and must not
            # inflate or invalidate the primary paired-effect panel.
            continue
        if (
            observation.experiment_id != experiment.experiment_id
            or observation.run_id != experiment.run_id
            or observation.swap_axis is not experiment.swap_axis
            or observation.case_id not in experiment.sampling.independent_case_ids
            or observation.repetition_index > experiment.sampling.repetitions_per_case
        ):
            identity_mismatch = True
            continue
        if (
            observation.frozen_identity_fingerprint
            != experiment.frozen_identities.fingerprint
            or observation.component_fingerprint
            != expected_components[observation.arm]
        ):
            identity_mismatch = True
        key = (
            observation.case_id,
            observation.repetition_index,
            observation.seed,
            observation.panel_id,
        )
        observed_coordinates.add((*key, observation.arm))
        arm_map = grouped.setdefault(key, {})
        if observation.arm in arm_map and arm_map[observation.arm] != observation:
            identity_mismatch = True
        arm_map[observation.arm] = observation
        if observation.execution_status not in {
            ObservationExecutionStatus.NOT_RUN,
            ObservationExecutionStatus.BLOCKED,
        }:
            completed_arms += 1
        if observation.execution_status is ObservationExecutionStatus.FAILED:
            failed_arms += 1
        elif observation.execution_status is ObservationExecutionStatus.TIMEOUT:
            timed_out_arms += 1
        elif observation.execution_status is ObservationExecutionStatus.BLOCKED:
            blocked_arms += 1
        elif observation.execution_status is ObservationExecutionStatus.NOT_RUN:
            not_run_arms += 1
    if identity_mismatch:
        reasons.add("identity_mismatch")

    scheduled_pair_count = (
        len(scheduled_primary_case_ids)
        * experiment.sampling.repetitions_per_case
    )
    scheduled_arm_count = scheduled_pair_count * 2
    missing_arm_count = not_run_arms + max(
        0,
        scheduled_arm_count - len(observed_coordinates),
    )
    comparable_keys: list[tuple[str, int, int | None, str | None]] = []
    complete_pair_count = 0
    control_viable = False
    for key, arms in grouped.items():
        control = arms.get(ArmRole.CONTROL)
        treatment = arms.get(ArmRole.TREATMENT)
        if control is None or treatment is None:
            continue
        complete_pair_count += 1
        if control.comparability is ComparabilityStatus.COMPARABLE:
            control_viable = True
        if (
            control.comparability is ComparabilityStatus.COMPARABLE
            and treatment.comparability is ComparabilityStatus.COMPARABLE
        ):
            comparable_keys.append(key)

    comparable_pair_count = len(comparable_keys)
    incomparable_pair_count = max(0, scheduled_pair_count - comparable_pair_count)
    independent_case_count = len({key[0] for key in comparable_keys})
    if complete_pair_count < scheduled_pair_count:
        reasons.add("treatment_not_executed")
    if not control_viable or comparable_pair_count == 0:
        reasons.add("control_not_comparable")

    hard_invalid_reasons = {
        "multiple_swap_axes_changed",
        "identity_mismatch",
        "missing_component_identity",
        "treatment_not_executed",
        "control_not_comparable",
    }
    if "missing_frozen_identity" in reasons:
        status = (
            ExperimentValidityStatus.INVALID
            if experiment.mode is MeasurementPolicyMode.REQUIRED
            else ExperimentValidityStatus.INCONCLUSIVE
        )
    elif reasons & hard_invalid_reasons:
        status = ExperimentValidityStatus.INVALID
    elif independent_case_count < experiment.outcomes.minimum_independent_cases:
        reasons.add("insufficient_independent_cases")
        status = ExperimentValidityStatus.VALID_LIMITED
    else:
        status = ExperimentValidityStatus.VALID
    return ExperimentValidity(
        status=status,
        reason_codes=tuple(sorted(reasons)),
        independent_case_count=independent_case_count,
        repetition_count=comparable_pair_count,
        comparable_pair_count=comparable_pair_count,
        incomparable_pair_count=incomparable_pair_count,
        scheduled_pair_count=scheduled_pair_count,
        completed_arm_count=completed_arms,
        failed_arm_count=failed_arms,
        timed_out_arm_count=timed_out_arms,
        blocked_arm_count=blocked_arms,
        missing_arm_count=missing_arm_count,
        identity_complete=experiment.frozen_identities.complete,
        control_viable=control_viable,
    )


def estimate_paired_effect(
    experiment: ControlledExperimentSpec,
    observations: Sequence[MeasurementObservation],
    *,
    validity: ExperimentValidity | None = None,
    metric: str | None = None,
) -> EffectEstimate | None:
    validity = validity or assess_experiment_validity(experiment, observations)
    if validity.status not in {
        ExperimentValidityStatus.VALID,
        ExperimentValidityStatus.VALID_LIMITED,
    }:
        return None
    metric_name = metric or experiment.outcomes.primary_metric
    grouped: dict[tuple[str, int, int | None, str | None], dict[ArmRole, MeasurementObservation]] = {}
    for observation in observations:
        key = (
            observation.case_id,
            observation.repetition_index,
            observation.seed,
            observation.panel_id,
        )
        grouped.setdefault(key, {})[observation.arm] = observation
    case_deltas: dict[str, list[float]] = {}
    repetition_count = 0
    for key, arms in grouped.items():
        control = arms.get(ArmRole.CONTROL)
        treatment = arms.get(ArmRole.TREATMENT)
        if control is None or treatment is None:
            continue
        if (
            control.comparability is not ComparabilityStatus.COMPARABLE
            or treatment.comparability is not ComparabilityStatus.COMPARABLE
        ):
            continue
        control_value = _observation_metric(control, metric_name)
        treatment_value = _observation_metric(treatment, metric_name)
        if control_value is None or treatment_value is None:
            continue
        raw_delta = treatment_value - control_value
        delta = raw_delta if experiment.outcomes.higher_is_better else -raw_delta
        case_deltas.setdefault(key[0], []).append(delta)
        repetition_count += 1
    if not case_deltas:
        return None
    aggregate = statistics.mean if experiment.outcomes.aggregation == "mean" else statistics.median
    per_case = [float(aggregate(values)) for _, values in sorted(case_deltas.items())]
    point = float(aggregate(per_case))
    lower: float | None = None
    upper: float | None = None
    if len(per_case) >= experiment.outcomes.minimum_independent_cases:
        lower, upper = _case_bootstrap_interval(
            per_case,
            confidence_level=experiment.outcomes.confidence_level,
            samples=experiment.outcomes.bootstrap_samples,
            seed_material=f"{experiment.experiment_id}:{metric_name}",
            aggregation=experiment.outcomes.aggregation,
        )
    direction = EffectDirection.INCONCLUSIVE
    if lower is not None and upper is not None:
        if (
            lower >= experiment.outcomes.minimum_effect
            and point > experiment.outcomes.non_regression_threshold
        ):
            direction = EffectDirection.POSITIVE
        elif upper < experiment.outcomes.non_regression_threshold:
            direction = EffectDirection.NEGATIVE
        elif (
            lower >= experiment.outcomes.non_regression_threshold
            and upper <= experiment.outcomes.minimum_effect
        ):
            direction = EffectDirection.NEUTRAL
    return EffectEstimate(
        metric=metric_name,
        point_estimate=point,
        confidence_lower_bound=lower,
        confidence_upper_bound=upper,
        confidence_level=experiment.outcomes.confidence_level,
        minimum_effect=experiment.outcomes.minimum_effect,
        non_regression_threshold=experiment.outcomes.non_regression_threshold,
        direction=direction,
        estimator_version=ESTIMATOR_VERSION,
        aggregation=f"{experiment.outcomes.aggregation}_within_case_then_across_cases",
        independent_case_count=len(per_case),
        repetition_count=repetition_count,
        multiple_comparison_policy=experiment.outcomes.multiple_comparison_policy,
    )


def build_search_performance(
    results: Sequence[SearchCandidateResult],
    *,
    k_values: Sequence[int] = (1, 2, 4, 8),
    token_budget_points: Sequence[int] = (),
    wall_time_budget_points: Sequence[float] = (),
    selection_protocol: str,
    quality_threshold: float | None = None,
) -> SearchPerformance:
    candidates = tuple(results)
    if any(k <= 0 for k in k_values):
        raise ValueError("K values must be positive")
    k_points: list[SearchKPoint] = []
    for requested_k in tuple(dict.fromkeys(k_values)):
        actual_k = min(requested_k, len(candidates))
        prefix = candidates[:actual_k]
        valid_scores = [float(item.score) for item in prefix if item.valid and item.score is not None]
        k_points.append(
            SearchKPoint(
                requested_k=requested_k,
                actual_k=actual_k,
                best_score=max(valid_scores) if valid_scores else None,
                pass_probability=(1.0 if any(item.valid and item.passed for item in prefix) else 0.0),
                valid_candidate_count=sum(1 for item in prefix if item.valid),
                authoritative_candidate_count=sum(1 for item in prefix if item.authoritative),
            )
        )
    tokens_to_threshold, wall_seconds_to_threshold = _budget_to_threshold(
        candidates,
        quality_threshold=quality_threshold,
    )
    return SearchPerformance(
        candidate_count=len(candidates),
        k_points=tuple(k_points),
        token_curve=_budget_curve(candidates, token_budget_points, field_name="tokens"),
        wall_time_curve=_budget_curve(candidates, wall_time_budget_points, field_name="wall_seconds"),
        validity_rate=(sum(1 for item in candidates if item.valid) / len(candidates) if candidates else 0.0),
        authoritative_yield=(sum(1 for item in candidates if item.authoritative) / len(candidates) if candidates else 0.0),
        selection_protocol=selection_protocol,
        quality_threshold=quality_threshold,
        tokens_to_threshold=tokens_to_threshold,
        wall_seconds_to_threshold=wall_seconds_to_threshold,
    )


def compare_search_performance(
    control: SearchPerformance,
    treatment: SearchPerformance,
) -> SearchPerformanceComparison:
    control_k = {point.actual_k for point in control.k_points if point.actual_k > 0}
    treatment_k = {point.actual_k for point in treatment.k_points if point.actual_k > 0}
    shared = tuple(sorted(control_k & treatment_k))
    opportunity_matched = control.candidate_count == treatment.candidate_count
    protocol_matched = control.selection_protocol == treatment.selection_protocol
    reasons: list[str] = []
    if not opportunity_matched:
        reasons.append("candidate_opportunity_mismatch")
    if not protocol_matched:
        reasons.append("selection_protocol_mismatch")
    return SearchPerformanceComparison(
        opportunity_matched=opportunity_matched,
        attribution_allowed=opportunity_matched and protocol_matched,
        shared_k=shared,
        reason_codes=tuple(reasons),
    )


def validate_transfer_panel(panel: TransferPanel) -> TransferAudit:
    reasons: set[str] = set()
    if panel.exposed_to_search or panel.visibility is VisibilityClass.PUBLIC:
        reasons.add("held_out_leakage")
    if panel.role is TransferPanelRole.TEMPORAL:
        if panel.optimization_cutoff_at is None or panel.sealed_at is None:
            reasons.add("temporal_cutoff_missing")
        else:
            cutoff = _utc_datetime(panel.optimization_cutoff_at, "optimization_cutoff_at")
            sealed = _utc_datetime(panel.sealed_at, "sealed_at")
            if sealed < cutoff:
                reasons.add("temporal_cutoff_violation")
            for case_id in panel.case_ids:
                case_sealed = panel.case_sealed_at.get(case_id)
                if case_sealed is None or _utc_datetime(case_sealed, "case_sealed_at") < cutoff:
                    reasons.add("temporal_cutoff_violation")
    return TransferAudit(
        panel_id=panel.panel_id,
        role=panel.role,
        passed=not reasons,
        required=panel.required,
        reason_codes=tuple(sorted(reasons)),
    )


def build_attribution_report(
    experiment: ControlledExperimentSpec,
    observations: Sequence[MeasurementObservation],
    *,
    target_resolution: TargetResolutionConfidence,
    readiness: MeasurementReadiness | None = None,
    total_usage: MeasurementUsage | None = None,
    search_usage: MeasurementUsage | None = None,
    measurement_usage: MeasurementUsage | None = None,
    generated_candidate_count: int = 0,
    authoritative_candidate_count: int = 0,
    transfer_audits: Sequence[TransferAudit] | None = None,
    search_performance: SearchPerformance | None = None,
    stopping: MeasurementStopRecord | None = None,
    admitted_primary_case_ids: Sequence[str] | None = None,
) -> AttributionReport:
    validity = assess_experiment_validity(
        experiment,
        observations,
        admitted_primary_case_ids=admitted_primary_case_ids,
    )
    effect = estimate_paired_effect(experiment, observations, validity=validity)
    secondary_effects = tuple(
        estimated
        for metric in experiment.outcomes.secondary_metrics
        if (estimated := estimate_paired_effect(experiment, observations, validity=validity, metric=metric)) is not None
    )
    effective_readiness = readiness or _readiness_from_validity(validity)
    usage = measurement_usage or total_usage or MeasurementUsage()
    ledger = BudgetLedger(
        search=search_usage
        or MeasurementUsage(candidate_opportunities=generated_candidate_count),
        measurement=usage,
    )
    budget_normalized = usage.complete
    audits = _effective_transfer_audits(
        experiment.transfer_panels,
        transfer_audits,
    )
    measurement_yield = MeasurementYield(
        generated_candidate_count=generated_candidate_count,
        total_usage=usage,
        comparable_pair_count=validity.comparable_pair_count,
        search_tokens=ledger.search.tokens,
        authoritative_candidate_count=authoritative_candidate_count,
        conclusive_experiment_count=(
            1
            if effect is not None
            and effect.direction in {EffectDirection.POSITIVE, EffectDirection.NEGATIVE, EffectDirection.NEUTRAL}
            else 0
        ),
    )
    decision = _measurement_decision(
        experiment,
        validity=validity,
        effect=effect,
        transfer=audits,
        budget_normalized=budget_normalized,
        observations=observations,
    )
    return AttributionReport(
        experiment_id=experiment.experiment_id,
        run_id=experiment.run_id,
        mode=experiment.mode,
        swap_axis=experiment.swap_axis,
        status=validity.status,
        target_resolution=target_resolution,
        validity=validity,
        effect=effect,
        secondary_effects=secondary_effects,
        measurement_readiness=effective_readiness,
        budget_ledger=ledger,
        measurement_yield=measurement_yield,
        decision=decision,
        budget_normalized=budget_normalized,
        transfer=audits,
        search_performance=search_performance,
        stopping=stopping,
    )


def measurement_summary_from_report(
    report: Mapping[str, object],
) -> MeasurementSummary | None:
    raw = report.get("measurement")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("report measurement summary must be an object")
    return MeasurementSummary.from_dict(raw)


def evaluate_measurement_stopping(
    reports: Sequence[AttributionReport],
    *,
    policy: MeasurementEarlyStopPolicy,
    unused_budget: MeasurementUsage | None = None,
) -> MeasurementStopRecord:
    history = tuple(reports)
    remaining = unused_budget or MeasurementUsage()
    evidence_ids = tuple(item.experiment_id for item in history[-16:])
    if not history:
        return MeasurementStopRecord(
            triggered=False,
            trigger=None,
            evidence_experiment_ids=(),
            unused_budget=remaining,
            resume_safe=True,
            reason="no_measurement_evidence",
        )
    latest = history[-1]
    if policy.stop_on_decisive_regression and (
        latest.effect is not None
        and latest.effect.direction is EffectDirection.NEGATIVE
    ):
        return MeasurementStopRecord(
            True,
            MeasurementStopTrigger.DECISIVE_REGRESSION,
            evidence_ids,
            remaining,
            False,
            "effect interval establishes regression",
        )
    if (
        policy.maximum_interval_width is not None
        and latest.effect is not None
        and latest.effect.confidence_lower_bound is not None
        and latest.effect.confidence_upper_bound is not None
        and latest.effect.confidence_upper_bound
        - latest.effect.confidence_lower_bound
        <= policy.maximum_interval_width
    ):
        return MeasurementStopRecord(
            True,
            MeasurementStopTrigger.SUFFICIENTLY_PRECISE,
            evidence_ids,
            remaining,
            False,
            "configured effect precision reached",
        )
    if _usage_exhausted(remaining):
        return MeasurementStopRecord(
            True,
            MeasurementStopTrigger.BUDGET_EXHAUSTED,
            evidence_ids,
            remaining,
            False,
            "measurement budget exhausted",
        )
    invalid_tail = history[-policy.invalid_control_patience :]
    if len(invalid_tail) == policy.invalid_control_patience and all(
        item.validity.status
        in {ExperimentValidityStatus.INVALID, ExperimentValidityStatus.FAILED}
        and not item.validity.control_viable
        for item in invalid_tail
    ):
        return MeasurementStopRecord(
            True,
            MeasurementStopTrigger.REPEATED_CONTROL_INVALIDITY,
            tuple(item.experiment_id for item in invalid_tail),
            remaining,
            True,
            "control remained invalid across the configured patience window",
        )
    zero_yield_tail = history[-policy.zero_yield_patience :]
    if len(zero_yield_tail) == policy.zero_yield_patience and all(
        item.measurement_yield.comparable_pair_count == 0
        for item in zero_yield_tail
    ):
        return MeasurementStopRecord(
            True,
            MeasurementStopTrigger.ZERO_COMPARABLE_PAIRS,
            tuple(item.experiment_id for item in zero_yield_tail),
            remaining,
            True,
            "no comparable evidence was produced within the patience window",
        )
    return MeasurementStopRecord(
        triggered=False,
        trigger=None,
        evidence_experiment_ids=evidence_ids,
        unused_budget=remaining,
        resume_safe=True,
        reason="additional measurement may still be informative",
    )


def _effective_transfer_audits(
    panels: Sequence[TransferPanel],
    supplied: Sequence[TransferAudit] | None,
) -> tuple[TransferAudit, ...]:
    """Fail closed when a declared transfer panel has no measured effect.

    Manifest validity only proves that a panel is sealed and hidden.  It is not
    evidence that the treatment preserved behavior on that panel.
    """

    supplied_by_id = {
        audit.panel_id: audit for audit in supplied or ()
    }
    audits: list[TransferAudit] = []
    for panel in panels:
        manifest = validate_transfer_panel(panel)
        audit = supplied_by_id.pop(panel.panel_id, None)
        if audit is None:
            reasons = set(manifest.reason_codes)
            reasons.add("transfer_evidence_missing")
            audits.append(
                TransferAudit(
                    panel_id=panel.panel_id,
                    role=panel.role,
                    passed=False,
                    required=panel.required,
                    reason_codes=tuple(sorted(reasons)),
                    effect=None,
                )
            )
            continue
        reasons = set(manifest.reason_codes) | set(audit.reason_codes)
        if audit.role is not panel.role:
            reasons.add("transfer_panel_role_mismatch")
        if audit.effect is None:
            reasons.add("transfer_effect_missing")
        elif audit.effect.direction is EffectDirection.NEGATIVE:
            reasons.add("transfer_non_regression_failed")
        elif audit.effect.direction in {
            EffectDirection.INCONCLUSIVE,
            EffectDirection.UNMEASURED,
        }:
            reasons.add("transfer_effect_inconclusive")
        audits.append(
            TransferAudit(
                panel_id=panel.panel_id,
                role=panel.role,
                passed=audit.passed and not reasons,
                required=panel.required,
                reason_codes=tuple(sorted(reasons)),
                effect=audit.effect,
            )
        )
    # Preserve explicitly supplied diagnostics for undeclared optional panels,
    # but never let them satisfy a declared panel by position.
    audits.extend(supplied_by_id.values())
    return tuple(audits)


def observations_from_replay(
    experiment: ControlledExperimentSpec,
    *,
    dataset: object,
    replay_result: object,
    run_root: str | Path | None = None,
) -> tuple[MeasurementObservation, ...]:
    """Project paired AWorld replay results into bounded measurement records.

    The adapter intentionally reads only typed replay lifecycle fields, scalar
    metrics, and safe artifact references. Trajectories and raw logs remain in
    the replay artifact tree.
    """

    raw_cases = getattr(dataset, "cases", ())
    cases = {
        str(getattr(case, "case_id", "")): case
        for case in raw_cases
        if str(getattr(case, "case_id", ""))
    }
    raw_members = getattr(replay_result, "member_results", None)
    if raw_members is None:
        if len(cases) != 1:
            return ()
        case_id = next(iter(cases))
        members: tuple[object, ...] = (
            _ReplayMeasurementMember(
                case_id=case_id,
                baseline=getattr(replay_result, "baseline", None),
                candidate=getattr(replay_result, "candidate", None),
            ),
        )
    else:
        members = tuple(raw_members)
    root = Path(run_root).resolve(strict=False) if run_root is not None else None
    observations: list[MeasurementObservation] = []
    seen_cases: set[str] = set()
    for member in members:
        case_id = str(getattr(member, "case_id", ""))
        case = cases.get(case_id)
        if case is None or case_id in seen_cases:
            continue
        seen_cases.add(case_id)
        baseline = getattr(member, "baseline", None)
        candidate = getattr(member, "candidate", None)
        baseline_repetitions = _physical_replay_results(baseline)
        candidate_repetitions = _physical_replay_results(candidate)
        repetition_count = max(
            len(baseline_repetitions),
            len(candidate_repetitions),
            experiment.sampling.repetitions_per_case,
        )
        for index in range(1, repetition_count + 1):
            baseline_item = (
                baseline_repetitions[index - 1]
                if index <= len(baseline_repetitions)
                else None
            )
            candidate_item = (
                candidate_repetitions[index - 1]
                if index <= len(candidate_repetitions)
                else None
            )
            comparable, comparability_reason = _replay_pair_comparability(
                baseline_item,
                candidate_item,
            )
            for arm, result, component in (
                (ArmRole.CONTROL, baseline_item, experiment.control),
                (ArmRole.TREATMENT, candidate_item, experiment.treatment),
            ):
                status = _measurement_execution_status(result)
                failure = getattr(result, "failure", None) if result is not None else None
                observations.append(
                    MeasurementObservation.create(
                        experiment=experiment,
                        arm=arm,
                        case_id=case_id,
                        case_fingerprint=stable_measurement_fingerprint(
                            {
                                "case_id": case_id,
                                "input": getattr(case, "input", None),
                                "expected_output": getattr(
                                    case, "expected_output", None
                                ),
                                "source": getattr(case, "source", {}),
                            }
                        ),
                        split=_dataset_case_split(dataset, case_id),
                        repetition_index=index,
                        seed=(
                            experiment.sampling.seeds[index - 1]
                            if index <= len(experiment.sampling.seeds)
                            else None
                        ),
                        component_fingerprint=component.fingerprint,
                        execution_status=status,
                        comparability=(
                            ComparabilityStatus.COMPARABLE
                            if comparable
                            else ComparabilityStatus.INCOMPARABLE
                            if result is not None
                            else ComparabilityStatus.NOT_EVALUATED
                        ),
                        comparability_reason=comparability_reason,
                        task_success=_replay_task_success(result),
                        metrics=_scalar_replay_metrics(result),
                        usage=_replay_measurement_usage(result),
                        artifact_refs=_replay_artifact_refs(result, run_root=root),
                        failure_owner=_failure_field(failure, "owner"),
                        failure_stage=_failure_field(failure, "stage"),
                        failure_scope=_failure_field(failure, "scope"),
                        failure_code=_failure_field(failure, "code"),
                    )
                )
    return tuple(observations)


def observations_from_evaluation(
    experiment: ControlledExperimentSpec,
    *,
    dataset: object,
    baseline_summary: object | None,
    candidate_summary: object | None,
) -> tuple[MeasurementObservation, ...]:
    """Project ordered evaluator samples into paired case observations.

    AWorld evaluator repetitions are flattened repetition-major. This adapter
    preserves those coordinates and fails closed when either arm does not
    expose an ordered scalar for the predeclared primary outcome.
    """

    if baseline_summary is None and candidate_summary is None:
        return ()
    cases_by_id = {
        str(getattr(case, "case_id", "")): case
        for case in getattr(dataset, "cases", ())
        if str(getattr(case, "case_id", ""))
    }
    case_ids = tuple(
        case_id
        for case_id in experiment.sampling.independent_case_ids
        if case_id in cases_by_id
    )
    if not case_ids:
        return ()
    baseline_metrics = _evaluation_metrics(baseline_summary)
    candidate_metrics = _evaluation_metrics(candidate_summary)
    baseline_samples = _ordered_evaluation_samples(
        baseline_metrics,
        metric=experiment.outcomes.primary_metric,
        case_count=len(case_ids),
        repetitions=experiment.sampling.repetitions_per_case,
    )
    candidate_samples = _ordered_evaluation_samples(
        candidate_metrics,
        metric=experiment.outcomes.primary_metric,
        case_count=len(case_ids),
        repetitions=experiment.sampling.repetitions_per_case,
    )
    observations: list[MeasurementObservation] = []
    for repetition_index in range(1, experiment.sampling.repetitions_per_case + 1):
        for case_index, case_id in enumerate(case_ids):
            coordinate = (repetition_index - 1) * len(case_ids) + case_index
            baseline_value = (
                baseline_samples[coordinate]
                if coordinate < len(baseline_samples)
                else None
            )
            candidate_value = (
                candidate_samples[coordinate]
                if coordinate < len(candidate_samples)
                else None
            )
            comparable = baseline_value is not None and candidate_value is not None
            case = cases_by_id[case_id]
            case_fingerprint = stable_measurement_fingerprint(
                {
                    "case_id": case_id,
                    "input": getattr(case, "input", None),
                    "expected_output": getattr(case, "expected_output", None),
                    "source": getattr(case, "source", {}),
                }
            )
            seed = (
                experiment.sampling.seeds[repetition_index - 1]
                if experiment.sampling.seeds
                else None
            )
            for arm, value, component, summary in (
                (
                    ArmRole.CONTROL,
                    baseline_value,
                    experiment.control,
                    baseline_summary,
                ),
                (
                    ArmRole.TREATMENT,
                    candidate_value,
                    experiment.treatment,
                    candidate_summary,
                ),
            ):
                task_success = _evaluation_task_success(
                    _evaluation_metrics(summary),
                    sample_index=coordinate,
                    sample_count=len(case_ids)
                    * experiment.sampling.repetitions_per_case,
                )
                metrics: dict[str, float | int | bool | None] = {}
                if value is not None:
                    metrics[experiment.outcomes.primary_metric] = value
                usage = (
                    _evaluation_measurement_usage(_evaluation_metrics(summary))
                    if len(case_ids)
                    * experiment.sampling.repetitions_per_case
                    == 1
                    else MeasurementUsage()
                )
                observations.append(
                    MeasurementObservation.create(
                        experiment=experiment,
                        arm=arm,
                        case_id=case_id,
                        case_fingerprint=case_fingerprint,
                        split=_dataset_case_split(dataset, case_id),
                        repetition_index=repetition_index,
                        seed=seed,
                        component_fingerprint=component.fingerprint,
                        execution_status=(
                            ObservationExecutionStatus.SUCCEEDED
                            if summary is not None
                            else ObservationExecutionStatus.NOT_RUN
                        ),
                        comparability=(
                            ComparabilityStatus.COMPARABLE
                            if comparable
                            else ComparabilityStatus.NOT_EVALUATED
                        ),
                        comparability_reason=(
                            None if comparable else "ordered_primary_metric_missing"
                        ),
                        task_success=task_success,
                        metrics=metrics,
                        usage=usage,
                    )
                )
    return tuple(observations)


def observations_with_usage_fallback(
    observations: Sequence[MeasurementObservation],
    usage_observations: Sequence[MeasurementObservation],
) -> tuple[MeasurementObservation, ...]:
    """Fill missing usage from the exact paired task-execution coordinate.

    Judge score observations and replay observations describe the same frozen
    case/arm/repetition coordinates but different outcome layers. Multi-case
    evaluator summaries cannot safely distribute aggregate usage, while replay
    owns per-member signed runtime telemetry. Only a complete exact-coordinate
    fallback is accepted; ambiguous or partial telemetry remains missing.
    """

    def coordinate(item: MeasurementObservation) -> tuple[object, ...]:
        return (
            item.experiment_id,
            item.run_id,
            item.case_id,
            item.case_fingerprint,
            item.arm,
            item.split,
            item.panel_id,
            item.repetition_index,
            item.seed,
            item.component_fingerprint,
            item.frozen_identity_fingerprint,
        )

    usage_by_coordinate: dict[tuple[object, ...], MeasurementUsage] = {}
    ambiguous_coordinates: set[tuple[object, ...]] = set()
    for item in usage_observations:
        if not item.usage.complete:
            continue
        item_coordinate = coordinate(item)
        if item_coordinate in usage_by_coordinate:
            ambiguous_coordinates.add(item_coordinate)
            usage_by_coordinate.pop(item_coordinate, None)
            continue
        if item_coordinate not in ambiguous_coordinates:
            usage_by_coordinate[item_coordinate] = item.usage
    return tuple(
        replace(
            item,
            # Once the per-execution replay layer exists, it is the sole
            # authority for usage. A single-case evaluator aggregate cannot
            # mask missing, partial, or ambiguous replay telemetry.
            usage=usage_by_coordinate.get(
                coordinate(item),
                MeasurementUsage(),
            ),
        )
        for item in observations
    )


def stable_measurement_fingerprint(value: object) -> str:
    return "sha256:" + _digest(value)


@dataclass(frozen=True)
class _ReplayMeasurementMember:
    case_id: str
    baseline: object
    candidate: object


def _physical_replay_results(result: object | None) -> tuple[object, ...]:
    if result is None:
        return ()
    repetitions = getattr(result, "repetition_results", ())
    return tuple(repetitions) if repetitions else (result,)


def _replay_pair_comparability(
    baseline: object | None,
    candidate: object | None,
) -> tuple[bool, str | None]:
    if baseline is None or candidate is None:
        return False, "arm_missing"
    if _replay_status_value(baseline) == "succeeded" and _replay_status_value(
        candidate
    ) == "succeeded":
        return True, None
    failure = getattr(baseline, "failure", None)
    owner = _failure_field(failure, "owner")
    stage = _failure_field(failure, "stage")
    if _replay_status_value(candidate) == "succeeded" and (
        owner == "task" or (owner == "candidate" and stage == "task_rollout")
    ):
        return True, "comparable_task_level_control_failure"
    return False, "control_not_comparable"


def _measurement_execution_status(
    result: object | None,
) -> ObservationExecutionStatus:
    if result is None:
        return ObservationExecutionStatus.NOT_RUN
    status = _replay_status_value(result)
    failure_code = _failure_field(getattr(result, "failure", None), "code") or ""
    if status == "failed" and "timeout" in failure_code:
        return ObservationExecutionStatus.TIMEOUT
    try:
        return ObservationExecutionStatus(status)
    except ValueError:
        return ObservationExecutionStatus.FAILED


def _replay_status_value(result: object) -> str:
    status = getattr(result, "status", "not_run")
    value = getattr(status, "value", status)
    return str(value)


def _replay_task_success(result: object | None) -> bool | None:
    if result is None:
        return None
    status = _replay_status_value(result)
    if status == "succeeded":
        return True
    if status != "failed":
        return None
    failure = getattr(result, "failure", None)
    owner = _failure_field(failure, "owner")
    stage = _failure_field(failure, "stage")
    if owner == "task" or (owner == "candidate" and stage == "task_rollout"):
        return False
    return None


def _failure_field(failure: object, field_name: str) -> str | None:
    if failure is None:
        return None
    value = (
        failure.get(field_name)
        if isinstance(failure, Mapping)
        else getattr(failure, field_name, None)
    )
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _scalar_replay_metrics(
    result: object | None,
) -> dict[str, float | int | bool | None]:
    if result is None:
        return {}
    raw = getattr(result, "metrics", {})
    if not isinstance(raw, Mapping):
        return {}
    metrics: dict[str, float | int | bool | None] = {}
    for name, value in raw.items():
        metric_name = str(name)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,95}", metric_name):
            continue
        if value is None or isinstance(value, (bool, int)):
            metrics[metric_name] = value
        elif isinstance(value, float) and math.isfinite(value):
            metrics[metric_name] = value
    return metrics


def _replay_measurement_usage(result: object | None) -> MeasurementUsage:
    metrics = _scalar_replay_metrics(result)
    tokens = _first_non_negative_int(
        metrics,
        ("total_tokens", "token_usage", "tokens"),
    )
    cost = _first_non_negative_float(metrics, ("cost_usd", "total_cost_usd"))
    wall = _first_non_negative_float(
        metrics,
        ("wall_seconds", "elapsed_seconds"),
    )
    if wall is None:
        latency = _first_non_negative_float(metrics, ("latency_ms",))
        wall = latency / 1000.0 if latency is not None else None
    return MeasurementUsage(tokens=tokens, cost_usd=cost, wall_seconds=wall)


def _first_non_negative_int(
    values: Mapping[str, object], names: Sequence[str]
) -> int | None:
    for name in names:
        value = values.get(name)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value).is_integer()
            and value >= 0
        ):
            return int(value)
    return None


def _first_non_negative_float(
    values: Mapping[str, object], names: Sequence[str]
) -> float | None:
    for name in names:
        value = values.get(name)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and value >= 0
        ):
            return float(value)
    return None


def _replay_artifact_refs(
    result: object | None,
    *,
    run_root: Path | None,
) -> tuple[str, ...]:
    if result is None:
        return ()
    refs: list[str] = []
    for field_name in ("stdout_path", "stderr_path"):
        raw = getattr(result, field_name, None)
        if not isinstance(raw, str) or not raw:
            continue
        path = Path(raw)
        if path.is_absolute():
            if run_root is None:
                continue
            try:
                relative = path.resolve(strict=False).relative_to(run_root)
            except ValueError:
                continue
        else:
            relative = path
        ref = relative.as_posix()
        try:
            _safe_artifact_ref(ref)
        except ValueError:
            continue
        refs.append(ref)
    return tuple(dict.fromkeys(refs))


def _dataset_case_split(dataset: object, case_id: str) -> str:
    recipe = getattr(dataset, "recipe", None)
    splits = getattr(recipe, "splits", {})
    if isinstance(splits, Mapping):
        for split_name, raw_case_ids in splits.items():
            if isinstance(raw_case_ids, (list, tuple)) and case_id in raw_case_ids:
                return str(split_name)
    held_out = getattr(recipe, "held_out_case_ids", ())
    if case_id in held_out:
        return "held_out"
    trainable = getattr(recipe, "trainable_case_ids", ())
    if case_id in trainable:
        return "train"
    return "unspecified"


def _evaluation_metrics(summary: object | None) -> Mapping[str, object]:
    metrics = getattr(summary, "metrics", {}) if summary is not None else {}
    return metrics if isinstance(metrics, Mapping) else {}


def _ordered_evaluation_samples(
    metrics: Mapping[str, object],
    *,
    metric: str,
    case_count: int,
    repetitions: int,
) -> tuple[float, ...]:
    expected = case_count * repetitions
    sample_keys = (f"{metric}_samples",)
    if metric == "score":
        sample_keys = ("score_samples",)
    for key in sample_keys:
        raw = metrics.get(key)
        if not isinstance(raw, (list, tuple)) or len(raw) != expected:
            continue
        parsed: list[float] = []
        for value in raw:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                parsed = []
                break
            parsed.append(float(value))
        if len(parsed) == expected:
            return tuple(parsed)
    if expected == 1:
        value = metrics.get(metric)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        ):
            return (float(value),)
    return ()


def _evaluation_task_success(
    metrics: Mapping[str, object],
    *,
    sample_index: int,
    sample_count: int,
) -> bool | None:
    raw_samples = metrics.get("task_success_samples")
    if isinstance(raw_samples, (list, tuple)) and len(raw_samples) == sample_count:
        value = raw_samples[sample_index]
        return value if isinstance(value, bool) else None
    if sample_count == 1:
        for key in ("task_success", "evaluator_gate_passed"):
            value = metrics.get(key)
            if isinstance(value, bool):
                return value
    return None


def _evaluation_measurement_usage(
    metrics: Mapping[str, object],
) -> MeasurementUsage:
    tokens = _first_non_negative_int(
        metrics,
        ("total_tokens", "token_usage", "tokens"),
    )
    cost = _first_non_negative_float(
        metrics,
        ("cost_usd", "total_cost_usd"),
    )
    wall = _first_non_negative_float(
        metrics,
        ("wall_seconds", "elapsed_seconds"),
    )
    if wall is None:
        latency_ms = _first_non_negative_float(metrics, ("latency_ms",))
        wall = latency_ms / 1_000.0 if latency_ms is not None else None
    return MeasurementUsage(tokens=tokens, cost_usd=cost, wall_seconds=wall)


def _measurement_decision(
    experiment: ControlledExperimentSpec,
    *,
    validity: ExperimentValidity,
    effect: EffectEstimate | None,
    transfer: Sequence[TransferAudit],
    budget_normalized: bool,
    observations: Sequence[MeasurementObservation],
) -> MeasurementDecision:
    authoritative = experiment.mode is MeasurementPolicyMode.REQUIRED
    if validity.status in {ExperimentValidityStatus.INVALID, ExperimentValidityStatus.FAILED}:
        return MeasurementDecision(
            promotion_eligible=False,
            next_action=MeasurementNextAction.REPAIR_MEASUREMENT,
            reason=validity.reason_codes[0] if validity.reason_codes else "experiment_invalid",
            owner="evaluation_harness",
            policy_authoritative=authoritative,
        )
    if validity.status in {ExperimentValidityStatus.VALID_LIMITED, ExperimentValidityStatus.INCONCLUSIVE}:
        return MeasurementDecision(
            promotion_eligible=False,
            next_action=MeasurementNextAction.COLLECT_MORE_EVIDENCE,
            reason="insufficient_independent_cases",
            owner="evaluation_harness",
            policy_authoritative=authoritative,
        )
    if effect is None:
        candidate_failure = next(
            (
                item
                for item in observations
                if item.arm is ArmRole.TREATMENT and item.failure_owner == "candidate"
            ),
            None,
        )
        return MeasurementDecision(
            promotion_eligible=False,
            next_action=(
                MeasurementNextAction.CONTINUE_CANDIDATE_REPAIR
                if candidate_failure is not None and validity.control_viable
                else MeasurementNextAction.REPAIR_MEASUREMENT
            ),
            reason="primary_effect_unavailable",
            owner="candidate" if candidate_failure is not None else "evaluation_harness",
            policy_authoritative=authoritative,
        )
    if effect.direction is EffectDirection.NEGATIVE:
        return MeasurementDecision(
            False,
            MeasurementNextAction.STOP_NEGATIVE_EFFECT,
            "conclusive_negative_effect",
            "candidate",
            authoritative,
        )
    if effect.direction is EffectDirection.NEUTRAL:
        return MeasurementDecision(
            False,
            MeasurementNextAction.STOP_NO_EFFECT,
            "conclusive_effect_below_minimum",
            "candidate",
            authoritative,
        )
    if effect.direction is EffectDirection.INCONCLUSIVE:
        return MeasurementDecision(
            False,
            MeasurementNextAction.COLLECT_MORE_EVIDENCE,
            "effect_interval_inconclusive",
            "evaluation_harness",
            authoritative,
        )
    failed_transfer = next(
        (item for item in transfer if item.required and not item.passed),
        None,
    )
    if failed_transfer is not None:
        if "transfer_effect_inconclusive" in failed_transfer.reason_codes:
            return MeasurementDecision(
                False,
                MeasurementNextAction.COLLECT_MORE_EVIDENCE,
                "required_transfer_panel_inconclusive",
                "evaluation_harness",
                authoritative,
            )
        measurement_failure = bool(
            set(failed_transfer.reason_codes)
            & {
                "held_out_leakage",
                "temporal_cutoff_missing",
                "temporal_cutoff_violation",
                "panel_fingerprint_drift",
                "transfer_evidence_missing",
                "transfer_effect_missing",
                "transfer_panel_role_mismatch",
            }
        )
        return MeasurementDecision(
            False,
            (
                MeasurementNextAction.REPAIR_MEASUREMENT
                if measurement_failure
                else MeasurementNextAction.STOP_NEGATIVE_EFFECT
            ),
            (
                "required_transfer_panel_invalid"
                if measurement_failure
                else "required_transfer_panel_regressed"
            ),
            "evaluation_harness" if measurement_failure else "candidate",
            authoritative,
        )
    if authoritative and not budget_normalized:
        return MeasurementDecision(
            False,
            MeasurementNextAction.REPAIR_MEASUREMENT,
            "missing_usage_telemetry",
            "evaluation_harness",
            True,
        )
    return MeasurementDecision(
        True,
        MeasurementNextAction.PROMOTE_CANDIDATE,
        "controlled_positive_effect",
        "candidate",
        authoritative,
    )


def _readiness_from_validity(validity: ExperimentValidity) -> MeasurementReadiness:
    if validity.independent_case_count >= 1:
        stage = (
            "minimum_independent_evidence"
            if validity.status is ExperimentValidityStatus.VALID
            else "first_comparable_pair"
        )
    elif validity.completed_arm_count:
        stage = "task_rollout"
    elif validity.identity_complete:
        stage = "identity_contract_complete"
    else:
        stage = "experiment_planned"
    return MeasurementReadiness(current_stage=stage)


def _case_bootstrap_interval(
    values: Sequence[float],
    *,
    confidence_level: float,
    samples: int,
    seed_material: str,
    aggregation: str,
) -> tuple[float, float]:
    if not values:
        raise ValueError("bootstrap requires values")
    aggregate = statistics.mean if aggregation == "mean" else statistics.median
    seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    estimates = sorted(
        float(aggregate([values[rng.randrange(len(values))] for _ in values]))
        for _ in range(samples)
    )
    alpha = (1.0 - confidence_level) / 2.0
    return _quantile(estimates, alpha), _quantile(estimates, 1.0 - alpha)


def _quantile(values: Sequence[float], probability: float) -> float:
    if len(values) == 1:
        return float(values[0])
    position = probability * (len(values) - 1)
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return float(values[lower_index])
    fraction = position - lower_index
    return float(values[lower_index] * (1 - fraction) + values[upper_index] * fraction)


def _observation_metric(
    observation: MeasurementObservation, metric: str
) -> float | None:
    value: object
    if metric == "task_success":
        value = observation.task_success
    elif metric == "timeout":
        value = observation.execution_status is ObservationExecutionStatus.TIMEOUT
    elif metric == "failure":
        value = observation.execution_status in {
            ObservationExecutionStatus.FAILED,
            ObservationExecutionStatus.TIMEOUT,
        }
    elif metric == "total_tokens":
        value = observation.usage.tokens
    elif metric == "cost_usd":
        value = observation.usage.cost_usd
    elif metric in {"latency", "latency_ms"}:
        value = observation.metrics.get(metric)
        if value is None and metric == "latency_ms" and observation.usage.wall_seconds is not None:
            value = observation.usage.wall_seconds * 1000.0
    else:
        value = observation.metrics.get(metric)
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _budget_curve(
    candidates: Sequence[SearchCandidateResult],
    points: Sequence[int | float],
    *,
    field_name: str,
) -> tuple[SearchBudgetPoint, ...]:
    result: list[SearchBudgetPoint] = []
    for requested in tuple(dict.fromkeys(float(point) for point in points)):
        if requested < 0:
            raise ValueError("budget points must be non-negative")
        consumed = 0.0
        selected: list[SearchCandidateResult] = []
        for candidate in candidates:
            raw = getattr(candidate, field_name)
            if raw is None:
                break
            value = float(raw)
            if consumed + value > requested:
                break
            consumed += value
            selected.append(candidate)
        scores = [float(item.score) for item in selected if item.valid and item.score is not None]
        regression_results = tuple(
            item.regression_passed
            for item in selected
            if item.regression_passed is not None
        )
        result.append(
            SearchBudgetPoint(
                requested_budget=requested,
                actual_budget=consumed,
                candidate_count=len(selected),
                best_score=max(scores) if scores else None,
                passed=any(item.valid and item.passed for item in selected),
                validity_rate=(
                    sum(1 for item in selected if item.valid) / len(selected)
                    if selected
                    else 0.0
                ),
                regression_pass_rate=(
                    sum(1 for passed in regression_results if passed)
                    / len(regression_results)
                    if regression_results
                    else None
                ),
                cumulative_tokens=_sum_candidate_usage(selected, "tokens", int),
                cumulative_cost_usd=_sum_candidate_usage(
                    selected,
                    "cost_usd",
                    float,
                ),
                cumulative_wall_seconds=_sum_candidate_usage(
                    selected,
                    "wall_seconds",
                    float,
                ),
            )
        )
    return tuple(result)


def _budget_to_threshold(
    candidates: Sequence[SearchCandidateResult],
    *,
    quality_threshold: float | None,
) -> tuple[int | None, float | None]:
    if quality_threshold is None:
        return None, None
    threshold = float(quality_threshold)
    if not math.isfinite(threshold):
        raise ValueError("quality threshold must be finite")
    tokens = 0
    wall_seconds = 0.0
    tokens_complete = True
    wall_complete = True
    for candidate in candidates:
        if candidate.tokens is None:
            tokens_complete = False
        else:
            tokens += candidate.tokens
        if candidate.wall_seconds is None:
            wall_complete = False
        else:
            wall_seconds += candidate.wall_seconds
        if (
            candidate.valid
            and candidate.score is not None
            and candidate.score >= threshold
        ):
            return (
                tokens if tokens_complete else None,
                wall_seconds if wall_complete else None,
            )
    return None, None


def _sum_candidate_usage(
    candidates: Sequence[SearchCandidateResult],
    field_name: str,
    cast: type[int] | type[float],
) -> int | float | None:
    if not candidates:
        return cast(0)
    values = tuple(getattr(candidate, field_name) for candidate in candidates)
    if any(value is None for value in values):
        return None
    return cast(sum(float(value) for value in values if value is not None))


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sum_optional_int(left: int | None, right: int | None) -> int | None:
    if left is None or right is None:
        return None
    return left + right


def _sum_optional_float(
    left: float | None,
    right: float | None,
) -> float | None:
    if left is None or right is None:
        return None
    return left + right


def _usage_exhausted(usage: MeasurementUsage) -> bool:
    return any(
        value == 0
        for value in (usage.tokens, usage.cost_usd, usage.wall_seconds)
        if value is not None
    )


def _json_default(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    raise TypeError(f"measurement value is not JSON serializable: {type(value).__name__}")


def _safe_id(value: str, field_name: str) -> None:
    if not _ID_RE.fullmatch(str(value)) or value in {".", ".."}:
        raise ValueError(f"invalid {field_name}: {value!r}")


def _safe_metric_name(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,95}", value):
        raise ValueError(f"invalid metric name: {value!r}")


def _safe_artifact_ref(value: str) -> None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("measurement artifact references must be safe relative paths")


def _fingerprint(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not _FINGERPRINT_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a full sha256 fingerprint")


def _optional_fingerprint(value: object, field_name: str) -> None:
    if value is not None:
        _fingerprint(value, field_name)


def _require_schema(value: Mapping[str, object], expected: str, label: str) -> None:
    if value.get("schema_version") != expected:
        raise ValueError(f"unsupported {label} schema")


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text field must be a string")
    return value


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be an object")
    return value


def _sequence(value: object, field_name: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be an array")
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item))


def _int_tuple(value: object) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise ValueError("expected integer array")
    return tuple(value)


def _string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    if any(not isinstance(item, str) for item in value.values()):
        raise ValueError("expected string mapping")
    return {str(key): str(item) for key, item in value.items()}


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected finite number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("expected finite number")
    return parsed


def _optional_finite_number(value: object) -> float | None:
    return None if value is None else _finite_number(value)


def _optional_non_negative_number(value: object) -> float | None:
    if value is None:
        return None
    parsed = _finite_number(value)
    if parsed < 0:
        raise ValueError("expected non-negative number")
    return parsed


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return value


def _non_negative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _optional_non_negative_int(value: object) -> int | None:
    if value is None:
        return None
    return _non_negative_int(value, "optional integer")


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected optional integer")
    return value


def _optional_probability(value: object) -> float | None:
    if value is None:
        return None
    parsed = _finite_number(value)
    if not 0 <= parsed <= 1:
        raise ValueError("probability must be between zero and one")
    return parsed


def _probability(value: object, field_name: str) -> float:
    parsed = _finite_number(value)
    if not 0 <= parsed <= 1:
        raise ValueError(f"{field_name} must be between zero and one")
    return parsed


def _utc_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed.astimezone(timezone.utc)
