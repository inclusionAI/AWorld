from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


SELF_EVOLVE_CONSTITUTION_SCHEMA_VERSION = (
    "aworld.self_evolve.constitution.v1"
)
AGENTIC_STAGE_REPORT_SCHEMA_VERSION = (
    "aworld.self_evolve.agentic_stage_report.v1"
)
SEMANTIC_ROLLOUT_POLICY_SCHEMA_VERSION = (
    "aworld.self_evolve.semantic_rollout_policy.v1"
)
SELF_EVOLVE_STAGE_CONTRACT_SCHEMA_VERSION = (
    "aworld.self_evolve.stage_contract.v1"
)

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ConstitutionContractError(ValueError):
    """A stable failure in the self-evolve constitution contract."""

    def __init__(self, reason_code: str, message: str) -> None:
        if not _REASON_PATTERN.fullmatch(reason_code):
            raise ValueError("reason_code must be lower_snake_case")
        self.reason_code = reason_code
        super().__init__(message)


class SelfEvolveStage(str, Enum):
    DISCOVER = "discover"
    UNDERSTAND = "understand"
    EXTRACT = "extract"
    VERIFY_COVERAGE_AND_ENTAILMENT = "verify_coverage_and_entailment"
    RESOLVE_AND_DETECT_CONFLICT = "resolve_and_detect_conflict"
    SYNTHESIZE_IMPROVEMENT_SIGNALS = "synthesize_improvement_signals"
    PLAN_EVALUATION = "plan_evaluation"
    FREEZE = "freeze"
    EVOLVE = "evolve"
    REPLAY_JUDGE_GATE = "replay_judge_gate"


class AgenticStageStatus(str, Enum):
    COMPLETE = "complete"
    NEEDS_REVISION = "needs_revision"
    EXHAUSTED = "exhausted"
    REJECTED = "rejected"


class AgenticRole(str, Enum):
    CONTROL_PLANE = "control_plane"
    SOURCE_UNDERSTANDING = "source_understanding"
    EVIDENCE_EXTRACTION = "evidence_extraction"
    COVERAGE_AUDIT = "coverage_audit"
    ENTAILMENT_VERIFICATION = "entailment_verification"
    RESOLUTION_CRITIC = "resolution_critic"
    SIGNAL_SYNTHESIS = "signal_synthesis"
    SIGNAL_CRITIC = "signal_critic"
    EVALUATION_PLANNING = "evaluation_planning"
    DETERMINISTIC_COMPILER = "deterministic_compiler"


class SemanticRolloutStage(str, Enum):
    SHADOW = "shadow"
    PROPOSAL = "proposal"
    TARGET_EVIDENCE = "target_evidence"
    VERIFIED = "verified"


@dataclass(frozen=True)
class AgenticStageBudgetV1:
    stage: SelfEvolveStage
    max_attempts: int
    max_model_calls: int
    max_source_bytes: int
    max_backtracks: int = 2
    max_tokens: int = 128_000

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", SelfEvolveStage(self.stage))
        for field_name in (
            "max_attempts",
            "max_model_calls",
            "max_source_bytes",
            "max_tokens",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ConstitutionContractError(
                    "invalid_stage_budget",
                    f"{field_name} must be a positive integer",
                )
        if (
            isinstance(self.max_backtracks, bool)
            or not isinstance(self.max_backtracks, int)
            or self.max_backtracks < 0
        ):
            raise ConstitutionContractError(
                "invalid_stage_budget",
                "max_backtracks must be a non-negative integer",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "max_attempts": self.max_attempts,
            "max_model_calls": self.max_model_calls,
            "max_source_bytes": self.max_source_bytes,
            "max_backtracks": self.max_backtracks,
            "max_tokens": self.max_tokens,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgenticStageBudgetV1":
        return cls(
            stage=SelfEvolveStage(str(payload.get("stage") or "")),
            max_attempts=payload.get("max_attempts"),  # type: ignore[arg-type]
            max_model_calls=payload.get("max_model_calls"),  # type: ignore[arg-type]
            max_source_bytes=payload.get("max_source_bytes"),  # type: ignore[arg-type]
            max_backtracks=payload.get("max_backtracks", 2),  # type: ignore[arg-type]
            max_tokens=payload.get("max_tokens", 128_000),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class SelfEvolveStageContractV1:
    stage: SelfEvolveStage
    allowed_roles: tuple[AgenticRole, ...]
    required_input_schemas: tuple[str, ...]
    required_output_schemas: tuple[str, ...]
    schema_version: str = SELF_EVOLVE_STAGE_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SELF_EVOLVE_STAGE_CONTRACT_SCHEMA_VERSION:
            raise ConstitutionContractError(
                "schema_version_mismatch",
                "invalid self-evolve stage contract schema",
            )
        object.__setattr__(self, "stage", SelfEvolveStage(self.stage))
        object.__setattr__(
            self,
            "allowed_roles",
            tuple(
                sorted(
                    (AgenticRole(item) for item in self.allowed_roles),
                    key=lambda item: item.value,
                )
            ),
        )
        if not self.allowed_roles:
            raise ConstitutionContractError(
                "stage_role_missing",
                "stage contract requires at least one allowed role",
            )
        if len(self.allowed_roles) != len(set(self.allowed_roles)):
            raise ConstitutionContractError(
                "duplicate_identity",
                "stage contract roles must be unique",
            )
        for values, field_name in (
            (self.required_input_schemas, "required_input_schemas"),
            (self.required_output_schemas, "required_output_schemas"),
        ):
            if not values:
                raise ConstitutionContractError(
                    "stage_schema_missing",
                    f"{field_name} must not be empty",
                )
            _safe_ids(values, field_name=field_name)
        object.__setattr__(
            self,
            "required_input_schemas",
            tuple(sorted(self.required_input_schemas)),
        )
        object.__setattr__(
            self,
            "required_output_schemas",
            tuple(sorted(self.required_output_schemas)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "stage": self.stage.value,
            "allowed_roles": sorted(
                item.value for item in self.allowed_roles
            ),
            "required_input_schemas": sorted(
                self.required_input_schemas
            ),
            "required_output_schemas": sorted(
                self.required_output_schemas
            ),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "SelfEvolveStageContractV1":
        _schema(
            payload,
            SELF_EVOLVE_STAGE_CONTRACT_SCHEMA_VERSION,
            "self-evolve stage contract",
        )
        return cls(
            stage=SelfEvolveStage(str(payload.get("stage") or "")),
            allowed_roles=tuple(
                AgenticRole(str(item))
                for item in _sequence(
                    payload.get("allowed_roles", ()),
                    "allowed_roles",
                )
            ),
            required_input_schemas=_string_tuple(
                payload,
                "required_input_schemas",
            ),
            required_output_schemas=_string_tuple(
                payload,
                "required_output_schemas",
            ),
        )


@dataclass(frozen=True)
class AgenticStageReportV1:
    report_id: str
    stage: SelfEvolveStage
    input_fingerprints: tuple[str, ...]
    output_fingerprints: tuple[str, ...]
    agent_role: AgenticRole
    provider_fingerprint: str
    model_fingerprint: str
    protocol_fingerprint: str
    independence_group: str
    attempt_count: int
    status: AgenticStageStatus
    reason_codes: tuple[str, ...] = ()
    next_stage_proposal: SelfEvolveStage | None = None
    input_schema_versions: tuple[str, ...] = ()
    output_schema_versions: tuple[str, ...] = ()
    model_call_count: int = 0
    source_bytes_consumed: int = 0
    token_count: int = 0
    schema_version: str = AGENTIC_STAGE_REPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AGENTIC_STAGE_REPORT_SCHEMA_VERSION:
            raise ConstitutionContractError(
                "schema_version_mismatch",
                "invalid agentic stage report schema",
            )
        _safe_id(self.report_id, field_name="report_id")
        _safe_id(self.independence_group, field_name="independence_group")
        object.__setattr__(self, "stage", SelfEvolveStage(self.stage))
        object.__setattr__(self, "agent_role", AgenticRole(self.agent_role))
        object.__setattr__(self, "status", AgenticStageStatus(self.status))
        if self.next_stage_proposal is not None:
            object.__setattr__(
                self,
                "next_stage_proposal",
                SelfEvolveStage(self.next_stage_proposal),
            )
        if (
            isinstance(self.attempt_count, bool)
            or not isinstance(self.attempt_count, int)
            or self.attempt_count <= 0
        ):
            raise ConstitutionContractError(
                "invalid_attempt_count",
                "attempt_count must be a positive integer",
            )
        if not self.input_fingerprints:
            raise ConstitutionContractError(
                "stage_input_missing",
                "stage report requires at least one input fingerprint",
            )
        for name, values in (
            ("input_fingerprint", self.input_fingerprints),
            ("output_fingerprint", self.output_fingerprints),
        ):
            if len(values) != len(set(values)):
                raise ConstitutionContractError(
                    "duplicate_identity",
                    f"{name}s must be unique",
                )
            for value in values:
                _fingerprint(value, field_name=name)
        for name in (
            "provider_fingerprint",
            "model_fingerprint",
            "protocol_fingerprint",
        ):
            _fingerprint(getattr(self, name), field_name=name)
        for reason_code in self.reason_codes:
            if not _REASON_PATTERN.fullmatch(reason_code):
                raise ConstitutionContractError(
                    "invalid_reason_code",
                    "stage reason codes must be lower_snake_case",
                )
        for values, field_name in (
            (self.input_schema_versions, "input_schema_versions"),
            (self.output_schema_versions, "output_schema_versions"),
        ):
            _safe_ids(values, field_name=field_name)
        for field_name in (
            "model_call_count",
            "source_bytes_consumed",
            "token_count",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                raise ConstitutionContractError(
                    "invalid_stage_usage",
                    f"{field_name} must be a non-negative integer",
                )
        if (
            self.status is AgenticStageStatus.COMPLETE
            and not self.output_fingerprints
        ):
            raise ConstitutionContractError(
                "stage_output_missing",
                "complete stage report requires output fingerprints",
            )
        if (
            self.status
            in {AgenticStageStatus.EXHAUSTED, AgenticStageStatus.REJECTED}
            and self.next_stage_proposal is not None
        ):
            raise ConstitutionContractError(
                "stage_transition_not_allowed",
                "terminal stage report cannot propose a next stage",
            )
        object.__setattr__(
            self,
            "input_fingerprints",
            tuple(sorted(self.input_fingerprints)),
        )
        object.__setattr__(
            self,
            "output_fingerprints",
            tuple(sorted(self.output_fingerprints)),
        )
        object.__setattr__(
            self,
            "reason_codes",
            tuple(sorted(self.reason_codes)),
        )
        object.__setattr__(
            self,
            "input_schema_versions",
            tuple(sorted(self.input_schema_versions)),
        )
        object.__setattr__(
            self,
            "output_schema_versions",
            tuple(sorted(self.output_schema_versions)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "stage": self.stage.value,
            "input_fingerprints": list(self.input_fingerprints),
            "output_fingerprints": list(self.output_fingerprints),
            "agent_role": self.agent_role.value,
            "provider_fingerprint": self.provider_fingerprint,
            "model_fingerprint": self.model_fingerprint,
            "protocol_fingerprint": self.protocol_fingerprint,
            "independence_group": self.independence_group,
            "attempt_count": self.attempt_count,
            "status": self.status.value,
            "reason_codes": list(self.reason_codes),
            "next_stage_proposal": (
                self.next_stage_proposal.value
                if self.next_stage_proposal is not None
                else None
            ),
            "input_schema_versions": sorted(
                self.input_schema_versions
            ),
            "output_schema_versions": sorted(
                self.output_schema_versions
            ),
            "model_call_count": self.model_call_count,
            "source_bytes_consumed": self.source_bytes_consumed,
            "token_count": self.token_count,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AgenticStageReportV1":
        _schema(
            payload,
            AGENTIC_STAGE_REPORT_SCHEMA_VERSION,
            "agentic stage report",
        )
        return cls(
            report_id=str(payload.get("report_id") or ""),
            stage=SelfEvolveStage(str(payload.get("stage") or "")),
            input_fingerprints=_string_tuple(
                payload,
                "input_fingerprints",
            ),
            output_fingerprints=_string_tuple(
                payload,
                "output_fingerprints",
            ),
            agent_role=AgenticRole(str(payload.get("agent_role") or "")),
            provider_fingerprint=str(payload.get("provider_fingerprint") or ""),
            model_fingerprint=str(payload.get("model_fingerprint") or ""),
            protocol_fingerprint=str(payload.get("protocol_fingerprint") or ""),
            independence_group=str(payload.get("independence_group") or ""),
            attempt_count=payload.get("attempt_count"),  # type: ignore[arg-type]
            status=AgenticStageStatus(str(payload.get("status") or "")),
            reason_codes=_string_tuple(payload, "reason_codes"),
            next_stage_proposal=(
                SelfEvolveStage(str(payload["next_stage_proposal"]))
                if payload.get("next_stage_proposal") is not None
                else None
            ),
            input_schema_versions=_string_tuple(
                payload,
                "input_schema_versions",
            ),
            output_schema_versions=_string_tuple(
                payload,
                "output_schema_versions",
            ),
            model_call_count=payload.get("model_call_count", 0),  # type: ignore[arg-type]
            source_bytes_consumed=payload.get(
                "source_bytes_consumed",
                0,
            ),  # type: ignore[arg-type]
            token_count=payload.get("token_count", 0),  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class SelfEvolveConstitutionV1:
    constitution_id: str
    stages: tuple[SelfEvolveStage, ...]
    allowed_roles: tuple[AgenticRole, ...]
    stage_budgets: tuple[AgenticStageBudgetV1, ...]
    stage_contracts: tuple[SelfEvolveStageContractV1, ...]
    invariants: tuple[str, ...]
    schema_version: str = SELF_EVOLVE_CONSTITUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SELF_EVOLVE_CONSTITUTION_SCHEMA_VERSION:
            raise ConstitutionContractError(
                "schema_version_mismatch",
                "invalid self-evolve constitution schema",
            )
        _safe_id(self.constitution_id, field_name="constitution_id")
        object.__setattr__(
            self,
            "stages",
            tuple(SelfEvolveStage(item) for item in self.stages),
        )
        object.__setattr__(
            self,
            "allowed_roles",
            tuple(
                sorted(
                    (AgenticRole(item) for item in self.allowed_roles),
                    key=lambda item: item.value,
                )
            ),
        )
        object.__setattr__(
            self,
            "stage_budgets",
            tuple(
                sorted(
                    self.stage_budgets,
                    key=lambda item: self.stages.index(item.stage),
                )
            ),
        )
        object.__setattr__(
            self,
            "stage_contracts",
            tuple(
                sorted(
                    self.stage_contracts,
                    key=lambda item: self.stages.index(item.stage),
                )
            ),
        )
        object.__setattr__(
            self,
            "invariants",
            tuple(sorted(self.invariants)),
        )
        if tuple(self.stages) != tuple(SelfEvolveStage):
            raise ConstitutionContractError(
                "stage_order_invalid",
                "constitution must contain the canonical stage order",
            )
        if len(self.allowed_roles) != len(set(self.allowed_roles)):
            raise ConstitutionContractError(
                "duplicate_identity",
                "constitution roles must be unique",
            )
        budget_stages = tuple(item.stage for item in self.stage_budgets)
        if set(budget_stages) != set(self.stages) or len(budget_stages) != len(
            self.stages
        ):
            raise ConstitutionContractError(
                "stage_budget_incomplete",
                "constitution requires exactly one budget for every stage",
            )
        contract_stages = tuple(
            item.stage for item in self.stage_contracts
        )
        if (
            set(contract_stages) != set(self.stages)
            or len(contract_stages) != len(self.stages)
        ):
            raise ConstitutionContractError(
                "stage_contract_incomplete",
                "constitution requires exactly one contract for every stage",
            )
        if any(
            not set(item.allowed_roles).issubset(
                set(self.allowed_roles)
            )
            for item in self.stage_contracts
        ):
            raise ConstitutionContractError(
                "stage_role_not_allowed",
                "stage contract role is outside constitution roles",
            )
        if len(self.invariants) != len(set(self.invariants)):
            raise ConstitutionContractError(
                "duplicate_identity",
                "constitution invariants must be unique",
            )
        for invariant in self.invariants:
            if not _REASON_PATTERN.fullmatch(invariant):
                raise ConstitutionContractError(
                    "invalid_reason_code",
                    "constitution invariants must be lower_snake_case",
                )

    @property
    def fingerprint(self) -> str:
        return _fingerprint_json(self.to_dict())

    def budget_for(self, stage: SelfEvolveStage) -> AgenticStageBudgetV1:
        normalized = SelfEvolveStage(stage)
        return next(item for item in self.stage_budgets if item.stage is normalized)

    def contract_for(
        self,
        stage: SelfEvolveStage,
    ) -> SelfEvolveStageContractV1:
        normalized = SelfEvolveStage(stage)
        return next(
            item
            for item in self.stage_contracts
            if item.stage is normalized
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "constitution_id": self.constitution_id,
            "stages": [item.value for item in self.stages],
            "allowed_roles": sorted(
                item.value for item in self.allowed_roles
            ),
            "stage_budgets": [
                item.to_dict()
                for item in sorted(
                    self.stage_budgets,
                    key=lambda value: self.stages.index(value.stage),
                )
            ],
            "stage_contracts": [
                item.to_dict()
                for item in sorted(
                    self.stage_contracts,
                    key=lambda value: self.stages.index(value.stage),
                )
            ],
            "invariants": sorted(self.invariants),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SelfEvolveConstitutionV1":
        _schema(
            payload,
            SELF_EVOLVE_CONSTITUTION_SCHEMA_VERSION,
            "self-evolve constitution",
        )
        budgets = payload.get("stage_budgets", ())
        if not isinstance(budgets, Sequence) or isinstance(budgets, (str, bytes)):
            raise ConstitutionContractError(
                "schema_invalid",
                "stage_budgets must be an array",
            )
        contracts = _sequence(
            payload.get("stage_contracts", ()),
            "stage_contracts",
        )
        return cls(
            constitution_id=str(payload.get("constitution_id") or ""),
            stages=tuple(
                SelfEvolveStage(str(item))
                for item in _sequence(payload.get("stages", ()), "stages")
            ),
            allowed_roles=tuple(
                AgenticRole(str(item))
                for item in _sequence(
                    payload.get("allowed_roles", ()),
                    "allowed_roles",
                )
            ),
            stage_budgets=tuple(
                AgenticStageBudgetV1.from_dict(_as_mapping(item))
                for item in budgets
            ),
            stage_contracts=tuple(
                SelfEvolveStageContractV1.from_dict(
                    _as_mapping(item)
                )
                for item in contracts
            ),
            invariants=tuple(
                str(item)
                for item in _sequence(
                    payload.get("invariants", ()),
                    "invariants",
                )
            ),
        )


@dataclass(frozen=True)
class SemanticRolloutPolicyV1:
    policy_id: str
    enabled_stage: SemanticRolloutStage = SemanticRolloutStage.SHADOW
    capability_fingerprints: tuple[str, ...] = ()
    prerequisite_capabilities: tuple[str, ...] = ()
    schema_version: str = SEMANTIC_ROLLOUT_POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SEMANTIC_ROLLOUT_POLICY_SCHEMA_VERSION:
            raise ConstitutionContractError(
                "schema_version_mismatch",
                "invalid semantic rollout policy schema",
            )
        _safe_id(self.policy_id, field_name="policy_id")
        object.__setattr__(
            self,
            "enabled_stage",
            SemanticRolloutStage(self.enabled_stage),
        )
        object.__setattr__(
            self,
            "capability_fingerprints",
            tuple(sorted(self.capability_fingerprints)),
        )
        object.__setattr__(
            self,
            "prerequisite_capabilities",
            tuple(sorted(self.prerequisite_capabilities)),
        )
        for values, field_name in (
            (self.capability_fingerprints, "capability_fingerprint"),
            (self.prerequisite_capabilities, "prerequisite_capability"),
        ):
            if len(values) != len(set(values)):
                raise ConstitutionContractError(
                    "duplicate_identity",
                    f"{field_name}s must be unique",
                )
            for value in values:
                _fingerprint(value, field_name=field_name)
        if not set(self.prerequisite_capabilities).issubset(
            set(self.capability_fingerprints)
        ):
            raise ConstitutionContractError(
                "rollout_prerequisite_missing",
                "rollout prerequisites must be present in frozen capabilities",
            )

    @property
    def fingerprint(self) -> str:
        return _fingerprint_json(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "enabled_stage": self.enabled_stage.value,
            "capability_fingerprints": sorted(self.capability_fingerprints),
            "prerequisite_capabilities": sorted(self.prerequisite_capabilities),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SemanticRolloutPolicyV1":
        _schema(
            payload,
            SEMANTIC_ROLLOUT_POLICY_SCHEMA_VERSION,
            "semantic rollout policy",
        )
        return cls(
            policy_id=str(payload.get("policy_id") or ""),
            enabled_stage=SemanticRolloutStage(
                str(payload.get("enabled_stage") or "shadow")
            ),
            capability_fingerprints=tuple(
                str(item)
                for item in _sequence(
                    payload.get("capability_fingerprints", ()),
                    "capability_fingerprints",
                )
            ),
            prerequisite_capabilities=tuple(
                str(item)
                for item in _sequence(
                    payload.get("prerequisite_capabilities", ()),
                    "prerequisite_capabilities",
                )
            ),
        )


def default_self_evolve_constitution() -> SelfEvolveConstitutionV1:
    stages = tuple(SelfEvolveStage)
    return SelfEvolveConstitutionV1(
        constitution_id="self-evolve-default-v1",
        stages=stages,
        allowed_roles=tuple(AgenticRole),
        stage_budgets=tuple(
            AgenticStageBudgetV1(
                stage=stage,
                max_attempts=3,
                max_model_calls=2,
                max_source_bytes=512 * 1024,
                max_backtracks=2,
            )
            for stage in stages
        ),
        stage_contracts=_default_stage_contracts(),
        invariants=(
            "source_content_is_untrusted",
            "accepted_claims_require_citations",
            "authority_is_framework_owned",
            "target_selection_is_framework_owned",
            "dataset_split_is_framework_owned",
            "held_out_evidence_is_isolated",
            "historical_evidence_is_not_fresh_evaluation",
            "freeze_precedes_evolution",
            "apply_requires_existing_gates",
        ),
    )


def _default_stage_contracts() -> tuple[SelfEvolveStageContractV1, ...]:
    definitions = {
        SelfEvolveStage.DISCOVER: (
            (AgenticRole.CONTROL_PLANE,),
            ("aworld.self_evolve.source_inventory.v1",),
            ("aworld.self_evolve.source_bundle.v1",),
        ),
        SelfEvolveStage.UNDERSTAND: (
            (AgenticRole.SOURCE_UNDERSTANDING,),
            ("aworld.self_evolve.source_bundle.v1",),
            ("aworld.self_evolve.source_understanding.v1",),
        ),
        SelfEvolveStage.EXTRACT: (
            (AgenticRole.EVIDENCE_EXTRACTION,),
            (
                "aworld.self_evolve.source_bundle.v1",
                "aworld.self_evolve.source_understanding.v1",
            ),
            ("aworld.self_evolve.evidence_candidate.v1",),
        ),
        SelfEvolveStage.VERIFY_COVERAGE_AND_ENTAILMENT: (
            (
                AgenticRole.COVERAGE_AUDIT,
                AgenticRole.ENTAILMENT_VERIFICATION,
            ),
            (
                "aworld.self_evolve.source_bundle.v1",
                "aworld.self_evolve.evidence_candidate.v1",
            ),
            ("aworld.self_evolve.evidence_graph.v1",),
        ),
        SelfEvolveStage.RESOLVE_AND_DETECT_CONFLICT: (
            (AgenticRole.RESOLUTION_CRITIC,),
            ("aworld.self_evolve.evidence_graph.v1",),
            ("aworld.self_evolve.resolved_evidence_graph.v1",),
        ),
        SelfEvolveStage.SYNTHESIZE_IMPROVEMENT_SIGNALS: (
            (
                AgenticRole.SIGNAL_SYNTHESIS,
                AgenticRole.SIGNAL_CRITIC,
            ),
            ("aworld.self_evolve.resolved_evidence_graph.v1",),
            (
                "aworld.self_evolve.improvement_signal_set.v1",
                "aworld.self_evolve.target_evidence_bundle.v1",
            ),
        ),
        SelfEvolveStage.PLAN_EVALUATION: (
            (AgenticRole.EVALUATION_PLANNING,),
            (
                "aworld.self_evolve.resolved_evidence_graph.v1",
                "aworld.self_evolve.improvement_signal_set.v1",
            ),
            ("aworld.self_evolve.evaluation_plan.v1",),
        ),
        SelfEvolveStage.FREEZE: (
            (
                AgenticRole.CONTROL_PLANE,
                AgenticRole.DETERMINISTIC_COMPILER,
            ),
            ("aworld.self_evolve.evaluation_plan.v1",),
            ("aworld.self_evolve.frozen_semantic_snapshot.v1",),
        ),
        SelfEvolveStage.EVOLVE: (
            (
                AgenticRole.CONTROL_PLANE,
                AgenticRole.DETERMINISTIC_COMPILER,
            ),
            ("aworld.self_evolve.frozen_semantic_snapshot.v1",),
            ("aworld.self_evolve.candidate_package.v1",),
        ),
        SelfEvolveStage.REPLAY_JUDGE_GATE: (
            (AgenticRole.CONTROL_PLANE,),
            ("aworld.self_evolve.candidate_package.v1",),
            ("aworld.self_evolve.apply_gate_result.v1",),
        ),
    }
    return tuple(
        SelfEvolveStageContractV1(
            stage=stage,
            allowed_roles=definitions[stage][0],
            required_input_schemas=definitions[stage][1],
            required_output_schemas=definitions[stage][2],
        )
        for stage in SelfEvolveStage
    )


def default_semantic_rollout_policy() -> SemanticRolloutPolicyV1:
    return SemanticRolloutPolicyV1(
        policy_id="semantic-rollout-default-shadow",
        enabled_stage=SemanticRolloutStage.SHADOW,
    )


def validate_stage_transition(
    constitution: SelfEvolveConstitutionV1,
    report: AgenticStageReportV1,
    requested_stage: SelfEvolveStage,
    *,
    prior_reports: Sequence[AgenticStageReportV1] = (),
) -> None:
    """Validate one constitution-owned transition without executing it."""

    requested = SelfEvolveStage(requested_stage)
    _validate_report_against_contract(constitution, report)
    current_index = constitution.stages.index(report.stage)
    requested_index = constitution.stages.index(requested)
    if report.status is AgenticStageStatus.COMPLETE:
        active_prior = validate_report_chain(
            constitution,
            prior_reports,
        )
        expected_prior_stages = constitution.stages[:current_index]
        if tuple(item.stage for item in active_prior) != (
            expected_prior_stages
        ):
            raise ConstitutionContractError(
                "prior_stage_report_missing",
                "all and only prior lifecycle stages must be complete",
            )
        if current_index > 0:
            previous = active_prior[-1]
            if not set(previous.output_fingerprints).intersection(
                report.input_fingerprints
            ):
                raise ConstitutionContractError(
                    "stage_fingerprint_chain_broken",
                    "stage input is not linked to the prior stage output",
                )
        if current_index == len(constitution.stages) - 1:
            raise ConstitutionContractError(
                "stage_transition_not_allowed",
                "the final lifecycle stage cannot advance",
            )
        if requested_index != current_index + 1:
            raise ConstitutionContractError(
                "stage_transition_not_allowed",
                "complete stages may advance exactly one stage",
            )
        if report.next_stage_proposal not in {None, requested}:
            raise ConstitutionContractError(
                "stage_transition_not_allowed",
                "requested stage differs from the report proposal",
            )
        return
    if report.status is AgenticStageStatus.NEEDS_REVISION:
        if requested_index != current_index - 1:
            raise ConstitutionContractError(
                "stage_transition_not_allowed",
                "revision may backtrack exactly one stage",
            )
        budget = constitution.budget_for(report.stage)
        backtracks = sum(
            item.status is AgenticStageStatus.NEEDS_REVISION
            and item.stage is report.stage
            for item in prior_reports
        )
        if backtracks >= budget.max_backtracks:
            raise ConstitutionContractError(
                "stage_backtrack_budget_exhausted",
                "stage backtrack budget is exhausted",
            )
        return
    raise ConstitutionContractError(
        "stage_transition_not_allowed",
        "terminal stage status cannot transition",
    )


def validate_report_chain(
    constitution: SelfEvolveConstitutionV1,
    reports: Sequence[AgenticStageReportV1],
) -> tuple[AgenticStageReportV1, ...]:
    """Validate the unique, contiguous complete lineage in a report ledger."""

    for report in reports:
        _validate_report_against_contract(constitution, report)
    complete_by_stage: dict[
        SelfEvolveStage,
        AgenticStageReportV1,
    ] = {}
    for report in reports:
        if report.status is not AgenticStageStatus.COMPLETE:
            continue
        if report.stage in complete_by_stage:
            raise ConstitutionContractError(
                "duplicate_active_stage_report",
                "report chain has multiple complete reports for one stage",
            )
        complete_by_stage[report.stage] = report
    active = tuple(
        complete_by_stage[stage]
        for stage in constitution.stages
        if stage in complete_by_stage
    )
    active_stages = tuple(item.stage for item in active)
    if active_stages != constitution.stages[: len(active_stages)]:
        raise ConstitutionContractError(
            "stage_report_chain_not_contiguous",
            "complete report stages must form a canonical prefix",
        )
    for previous, current in zip(active, active[1:]):
        if not set(previous.output_fingerprints).intersection(
            current.input_fingerprints
        ):
            raise ConstitutionContractError(
                "stage_fingerprint_chain_broken",
                "adjacent stage reports do not share a fingerprint edge",
            )
    return active


def _validate_report_against_contract(
    constitution: SelfEvolveConstitutionV1,
    report: AgenticStageReportV1,
) -> None:
    contract = constitution.contract_for(report.stage)
    if (
        report.agent_role not in constitution.allowed_roles
        or report.agent_role not in contract.allowed_roles
    ):
        raise ConstitutionContractError(
            "agent_role_not_allowed",
            "stage report role is not allowed for this stage",
        )
    budget = constitution.budget_for(report.stage)
    if report.attempt_count > budget.max_attempts:
        raise ConstitutionContractError(
            "stage_budget_exhausted",
            "stage report exceeds the configured attempt budget",
        )
    if (
        report.model_call_count > budget.max_model_calls
        or report.source_bytes_consumed > budget.max_source_bytes
        or report.token_count > budget.max_tokens
    ):
        raise ConstitutionContractError(
            "stage_budget_exhausted",
            "stage report exceeds its measured usage budget",
        )
    if not set(contract.required_input_schemas).issubset(
        set(report.input_schema_versions)
    ):
        raise ConstitutionContractError(
            "stage_input_schema_missing",
            "stage report is missing a required input schema",
        )
    if (
        report.status is AgenticStageStatus.COMPLETE
        and not set(contract.required_output_schemas).issubset(
            set(report.output_schema_versions)
        )
    ):
        raise ConstitutionContractError(
            "stage_output_schema_missing",
            "stage report is missing a required output schema",
        )


def validate_rollout_advance(
    current: SemanticRolloutPolicyV1 | None,
    requested: SemanticRolloutPolicyV1,
) -> None:
    """Require explicit, one-step rollout enablement with frozen prerequisites."""

    current_policy = current or default_semantic_rollout_policy()
    order = tuple(SemanticRolloutStage)
    current_index = order.index(current_policy.enabled_stage)
    requested_index = order.index(requested.enabled_stage)
    if requested_index != current_index + 1:
        raise ConstitutionContractError(
            "rollout_transition_not_allowed",
            "semantic rollout must advance exactly one stage",
        )
    if not requested.prerequisite_capabilities:
        raise ConstitutionContractError(
            "rollout_prerequisite_missing",
            "rollout advance requires frozen prerequisite capabilities",
        )


def safe_rollout_fallback(
    policy: SemanticRolloutPolicyV1 | None,
    *,
    capabilities_available: bool,
) -> SemanticRolloutStage:
    if policy is None or not capabilities_available:
        return SemanticRolloutStage.SHADOW
    return policy.enabled_stage


def _schema(payload: Mapping[str, Any], expected: str, name: str) -> None:
    if payload.get("schema_version") != expected:
        raise ConstitutionContractError(
            "schema_version_mismatch",
            f"invalid {name} schema",
        )


def _safe_id(value: str, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not _SAFE_ID_PATTERN.fullmatch(value)
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
    ):
        raise ConstitutionContractError(
            "unsafe_identity",
            f"{field_name} is not a safe stable identity",
        )
    return value


def _fingerprint(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not _FINGERPRINT_PATTERN.fullmatch(value):
        raise ConstitutionContractError(
            "invalid_fingerprint",
            f"{field_name} must be sha256:<64 lowercase hex>",
        )
    return value


def _safe_ids(values: Sequence[str], *, field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ConstitutionContractError(
            "duplicate_identity",
            f"{field_name} contains duplicate values",
        )
    for value in values:
        _safe_id(value, field_name=field_name)


def _sequence(value: Any, field_name: str) -> Sequence[Any]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
    ):
        raise ConstitutionContractError(
            "schema_invalid",
            f"{field_name} must be an array",
        )
    return value


def _string_tuple(
    payload: Mapping[str, Any],
    name: str,
) -> tuple[str, ...]:
    return tuple(
        str(item)
        for item in _sequence(payload.get(name, ()), name)
    )


def _fingerprint_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConstitutionContractError(
            "schema_invalid",
            "expected an object",
        )
    return value
