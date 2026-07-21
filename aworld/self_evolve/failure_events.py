"""Typed replay failure events shared by execution, policy, and reporting.

The mapping interface intentionally exposes a bounded compatibility view for
older callers.  New persistence and policy code must use the typed attributes
or :meth:`ReplayFailureEvent.to_dict`; prose in the compatibility view is audit
data, never a policy input.
"""

from __future__ import annotations

import re
import hashlib
import json
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from aworld.self_evolve.sanitization import sanitize_text

FAILURE_EVENT_SCHEMA_VERSION = "aworld.self_evolve.replay_failure.v2"
_MAX_SUMMARY_CHARS = 1_000
_MAX_CATEGORY_CHARS = 128
_MAX_DIAGNOSTIC_ITEMS = 32
_MAX_DIAGNOSTIC_DEPTH = 4
_MAX_ARTIFACT_REFS = 16
_MAX_OCCURRENCE_IDS = 64
_MAX_SOURCE_IDS = 32
_CODE_RE = re.compile(r"[^a-z0-9_]+")


class ReplayExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    NOT_RUN = "not_run"


class FailureOwner(str, Enum):
    CANDIDATE = "candidate"
    TASK = "task"
    INFRASTRUCTURE = "infrastructure"
    FRAMEWORK = "framework"


class FailureStage(str, Enum):
    ADAPTATION = "adaptation"
    CAPABILITY_COMPILE = "capability_compile"
    CAPABILITY_PREFLIGHT = "capability_preflight"
    TASK_ROLLOUT = "task_rollout"
    EVALUATION = "evaluation"
    RESULT_NORMALIZATION = "result_normalization"
    LEGACY_IMPORT = "legacy_import"


class FailureScope(str, Enum):
    VARIANT = "variant"
    MEMBER = "member"
    CANDIDATE = "candidate"
    SHARED_RUN = "shared_run"


class FailureEventSource(str, Enum):
    NATIVE = "native"
    LEGACY_INFERRED = "legacy_inferred"
    LEGACY_UNKNOWN = "legacy_unknown"


def _bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= _MAX_DIAGNOSTIC_DEPTH:
        return sanitize_text(str(value), max_chars=256)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return sanitize_text(value, max_chars=1_000)
    if isinstance(value, Mapping):
        bounded: dict[str, Any] = {}
        for key, item in list(value.items())[:_MAX_DIAGNOSTIC_ITEMS]:
            bounded[sanitize_text(str(key), max_chars=128)] = _bounded_value(
                item, depth=depth + 1
            )
        return bounded
    if isinstance(value, (list, tuple)):
        return [
            _bounded_value(item, depth=depth + 1)
            for item in list(value)[:_MAX_DIAGNOSTIC_ITEMS]
        ]
    return sanitize_text(str(value), max_chars=1_000)


def _stable_code(value: Any, *, default: str) -> str:
    normalized = _CODE_RE.sub("_", str(value or "").strip().casefold()).strip("_")
    return normalized[:96] or default


def _semantic_identity(value: Any) -> str | None:
    if value is None:
        return None
    clean = sanitize_text(str(value), max_chars=160).strip()
    return clean or None


@dataclass(frozen=True, eq=False)
class ReplayFailureEvent(Mapping[str, Any]):
    """A bounded causal occurrence with orthogonal ownership and scope."""

    code: str
    owner: FailureOwner
    stage: FailureStage
    scope: FailureScope
    repairable: bool
    category: str = "replay"
    summary: str = ""
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    artifact_refs: tuple[str, ...] = ()
    source: FailureEventSource = FailureEventSource.NATIVE
    causes: tuple[str, ...] = ()
    capability_id: str | None = None
    requirement_id: str | None = None
    contract_fingerprint: str | None = None
    event_id: str = field(default_factory=lambda: f"replay-event-{uuid.uuid4().hex}")
    schema_version: str = FAILURE_EVENT_SCHEMA_VERSION
    _compatibility: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        try:
            owner = FailureOwner(self.owner)
            stage = FailureStage(self.stage)
            scope = FailureScope(self.scope)
            source = FailureEventSource(self.source)
        except ValueError as exc:
            raise ValueError("invalid replay failure event enum value") from exc
        if scope is FailureScope.SHARED_RUN and owner not in {
            FailureOwner.INFRASTRUCTURE,
            FailureOwner.FRAMEWORK,
        }:
            raise ValueError("shared_run failures must be infrastructure or framework owned")
        if scope is FailureScope.SHARED_RUN and source is not FailureEventSource.NATIVE:
            raise ValueError("shared_run scope is reserved for native failure events")
        code = _stable_code(self.code, default="replay_failure")
        if code != self.code:
            raise ValueError("failure event code must be a stable lowercase identifier")
        event_id = sanitize_text(str(self.event_id), max_chars=160).strip()
        if not event_id:
            raise ValueError("failure event_id must be non-empty")
        compatibility = _bounded_value(self._compatibility)
        if not isinstance(compatibility, Mapping):
            compatibility = {}
        diagnostics = _bounded_value(self.diagnostics)
        if not isinstance(diagnostics, Mapping):
            diagnostics = {}
        object.__setattr__(self, "owner", owner)
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(
            self, "category", sanitize_text(str(self.category), max_chars=_MAX_CATEGORY_CHARS)
        )
        object.__setattr__(
            self, "summary", sanitize_text(str(self.summary), max_chars=_MAX_SUMMARY_CHARS)
        )
        object.__setattr__(self, "diagnostics", dict(diagnostics))
        object.__setattr__(
            self,
            "artifact_refs",
            tuple(
                sanitize_text(str(item), max_chars=512)
                for item in self.artifact_refs[:_MAX_ARTIFACT_REFS]
                if str(item).strip()
            ),
        )
        object.__setattr__(
            self,
            "causes",
            tuple(dict.fromkeys(str(item) for item in self.causes if str(item).strip()))[:32],
        )
        object.__setattr__(self, "_compatibility", dict(compatibility))
        # Only exact machine identity fields may flow out of the compatibility
        # boundary.  Nested diagnostics and prose are deliberately ignored.
        compatibility_capability_id = compatibility.get("capability_id") or compatibility.get(
            "replay_capability_id"
        )
        compatibility_requirement_id = compatibility.get("requirement_id")
        object.__setattr__(
            self,
            "capability_id",
            _semantic_identity(self.capability_id or compatibility_capability_id),
        )
        object.__setattr__(
            self,
            "requirement_id",
            _semantic_identity(self.requirement_id or compatibility_requirement_id),
        )
        object.__setattr__(
            self,
            "contract_fingerprint",
            _semantic_identity(self.contract_fingerprint),
        )

    @property
    def semantic_key(self) -> str:
        """Stable failure identity with all occurrence data deliberately excluded."""

        payload = {
            "owner": self.owner.value,
            "stage": self.stage.value,
            "code": self.code,
            "scope": self.scope.value,
            "capability_id": self.capability_id,
            "requirement_id": self.requirement_id,
            "contract_fingerprint": self.contract_fingerprint,
            "repairable": self.repairable,
            "category": self.category,
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]
        return f"replay-failure-{digest}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "code": self.code,
            "owner": self.owner.value,
            "stage": self.stage.value,
            "scope": self.scope.value,
            "repairable": self.repairable,
            "category": self.category,
            "summary": self.summary,
            "diagnostics": dict(self.diagnostics),
            "artifact_refs": list(self.artifact_refs),
            "source": self.source.value,
            "causes": list(self.causes),
            "capability_id": self.capability_id,
            "requirement_id": self.requirement_id,
            "contract_fingerprint": self.contract_fingerprint,
            "semantic_key": self.semantic_key,
        }

    def compatibility_dict(self) -> dict[str, Any]:
        if self._compatibility:
            return dict(self._compatibility)
        return {
            "code": self.code,
            "outcome": f"{self.owner.value}_failure",
            "failure_stage": self.stage.value,
            "failure_scope": self.scope.value,
            "repairable": self.repairable,
            "reason": self.summary or self.code,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReplayFailureEvent":
        if payload.get("schema_version") != FAILURE_EVENT_SCHEMA_VERSION:
            return cls.from_legacy_mapping(payload)
        if "source" not in payload:
            raise ValueError("v2 replay failure event source is required")
        diagnostics = payload.get("diagnostics")
        artifact_refs = payload.get("artifact_refs")
        causes = payload.get("causes")
        return cls(
            schema_version=FAILURE_EVENT_SCHEMA_VERSION,
            event_id=str(payload.get("event_id") or ""),
            code=str(payload.get("code") or ""),
            owner=FailureOwner(str(payload.get("owner") or "")),
            stage=FailureStage(str(payload.get("stage") or "")),
            scope=FailureScope(str(payload.get("scope") or "")),
            repairable=payload.get("repairable") is True,
            category=str(payload.get("category") or "replay"),
            summary=str(payload.get("summary") or ""),
            diagnostics=(diagnostics if isinstance(diagnostics, Mapping) else {}),
            artifact_refs=(
                tuple(str(item) for item in artifact_refs)
                if isinstance(artifact_refs, list)
                else ()
            ),
            source=FailureEventSource(str(payload.get("source") or "")),
            causes=(
                tuple(str(item) for item in causes) if isinstance(causes, list) else ()
            ),
            capability_id=(
                str(payload.get("capability_id"))
                if payload.get("capability_id") is not None
                else None
            ),
            requirement_id=(
                str(payload.get("requirement_id"))
                if payload.get("requirement_id") is not None
                else None
            ),
            contract_fingerprint=(
                str(payload.get("contract_fingerprint"))
                if payload.get("contract_fingerprint") is not None
                else None
            ),
        )

    @classmethod
    def from_legacy_mapping(cls, payload: Mapping[str, Any]) -> "ReplayFailureEvent":
        bounded = _bounded_value(payload)
        if not isinstance(bounded, Mapping):
            bounded = {}
        outcome = str(payload.get("outcome") or "")
        explicit_code = str(payload.get("code") or "")
        failure_class = str(payload.get("failure_class") or "")
        raw_stage = str(payload.get("failure_stage") or "")
        failure_type = str(payload.get("type") or "")
        known = bool(explicit_code or outcome or failure_class or raw_stage or failure_type)
        if outcome == "candidate_failure" or failure_class.startswith("candidate"):
            owner = FailureOwner.CANDIDATE
        elif outcome == "infrastructure_failure":
            owner = FailureOwner.INFRASTRUCTURE
        elif outcome == "task_failure" or failure_type in {
            "ReplayBoundaryViolation",
            "TaskFailure",
            "TimeoutExpired",
        }:
            owner = FailureOwner.TASK
        else:
            owner = FailureOwner.FRAMEWORK
        stage_aliases = {
            "adaptation": FailureStage.ADAPTATION,
            "capability_compile": FailureStage.CAPABILITY_COMPILE,
            "capability_preflight": FailureStage.CAPABILITY_PREFLIGHT,
            "replay_capability": FailureStage.CAPABILITY_PREFLIGHT,
            "task_rollout": FailureStage.TASK_ROLLOUT,
            "evaluation": FailureStage.EVALUATION,
            "result_normalization": FailureStage.RESULT_NORMALIZATION,
        }
        stage = stage_aliases.get(raw_stage, FailureStage.LEGACY_IMPORT)
        machine_code = explicit_code or failure_type or failure_class or outcome
        code = _stable_code(machine_code, default="legacy_unclassified_failure")
        summary = str(payload.get("reason") or payload.get("detail") or code)
        diagnostics = payload.get("diagnostics")
        source = (
            FailureEventSource.LEGACY_INFERRED
            if known
            else FailureEventSource.LEGACY_UNKNOWN
        )
        return cls(
            # A legacy mapping has no occurrence id. Assign one at the import
            # boundary rather than turning its semantic payload into identity.
            event_id=f"legacy-event-{uuid.uuid4().hex}",
            code=code,
            owner=owner,
            stage=stage,
            # Legacy evidence is never sufficient to stop the whole run.
            scope=FailureScope.CANDIDATE,
            repairable=payload.get("repairable") is True or owner is FailureOwner.CANDIDATE,
            category=str(payload.get("category") or failure_class or outcome or "legacy"),
            summary=summary,
            diagnostics=(diagnostics if isinstance(diagnostics, Mapping) else {}),
            source=source,
            capability_id=(
                str(payload.get("capability_id"))
                if payload.get("capability_id") is not None
                else None
            ),
            requirement_id=(
                str(payload.get("requirement_id"))
                if payload.get("requirement_id") is not None
                else None
            ),
            contract_fingerprint=(
                str(payload.get("contract_fingerprint"))
                if payload.get("contract_fingerprint") is not None
                else None
            ),
            _compatibility=dict(bounded),
        )

    def __getitem__(self, key: str) -> Any:
        return self.compatibility_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.compatibility_dict())

    def __len__(self) -> int:
        return len(self.compatibility_dict())

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ReplayFailureEvent):
            return self.to_dict() == other.to_dict()
        if isinstance(other, Mapping):
            return self.compatibility_dict() == dict(other)
        return False

    def __hash__(self) -> int:
        return hash(self.event_id)


def causal_failure_events(events: tuple[ReplayFailureEvent, ...]) -> tuple[ReplayFailureEvent, ...]:
    """Return occurrence-unique causal leaves in stable order."""

    by_id = {event.event_id: event for event in events}
    result: list[ReplayFailureEvent] = []
    seen: set[str] = set()

    def visit(event: ReplayFailureEvent, ancestry: frozenset[str]) -> None:
        if event.event_id in ancestry:
            return
        resolved_causes = tuple(
            by_id[cause] for cause in event.causes if cause in by_id
        )
        if resolved_causes:
            for cause in resolved_causes:
                visit(cause, ancestry | {event.event_id})
            return
        if event.event_id not in seen:
            seen.add(event.event_id)
            result.append(event)

    for event in events:
        visit(event, frozenset())
    return tuple(result)


@dataclass(frozen=True)
class ReplayFailureObservation:
    """One member's relationship to a causal event.

    ``execution_failure`` is false for blocked variants.  Such observations
    expand the affected-member set but never manufacture another occurrence.
    """

    event: ReplayFailureEvent
    case_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    candidate_id: str | None = None
    execution_failure: bool = True


@dataclass(frozen=True)
class AggregatedReplayFailure:
    semantic_key: str
    code: str
    owner: FailureOwner
    stage: FailureStage
    scope: FailureScope
    repairable: bool
    category: str
    capability_id: str | None = None
    requirement_id: str | None = None
    contract_fingerprint: str | None = None
    occurrence_count: int = 1
    affected_member_count: int = 0
    occurrence_ids: tuple[str, ...] = ()
    affected_case_ids: tuple[str, ...] = ()
    source_run_ids: tuple[str, ...] = ()
    source_task_ids: tuple[str, ...] = ()
    source_candidate_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    source_kinds: tuple[str, ...] = ()

    @property
    def distinct_source_count(self) -> int:
        # IDs are parallel dimensions of the same observations, not additive
        # evidence sources.  One run/task/candidate tuple is one source.
        return max(
            len(self.source_run_ids),
            len(self.source_task_ids),
            len(self.source_candidate_ids),
            0,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_key": self.semantic_key,
            "code": self.code,
            "owner": self.owner.value,
            "stage": self.stage.value,
            "scope": self.scope.value,
            "repairable": self.repairable,
            "category": self.category,
            "capability_id": self.capability_id,
            "requirement_id": self.requirement_id,
            "contract_fingerprint": self.contract_fingerprint,
            "occurrence_count": self.occurrence_count,
            "affected_member_count": self.affected_member_count,
            "occurrence_ids": list(self.occurrence_ids),
            "affected_case_ids": list(self.affected_case_ids),
            "source_run_ids": list(self.source_run_ids),
            "source_task_ids": list(self.source_task_ids),
            "source_candidate_ids": list(self.source_candidate_ids),
            "distinct_source_count": self.distinct_source_count,
            "artifact_refs": list(self.artifact_refs),
            "source_kinds": list(self.source_kinds),
        }


def aggregate_replay_failure_observations(
    observations: tuple[ReplayFailureObservation, ...],
) -> tuple[AggregatedReplayFailure, ...]:
    """Aggregate causal leaves by semantic identity in deterministic order."""

    all_events = tuple(observation.event for observation in observations)
    by_id = {event.event_id: event for event in all_events}
    expanded: list[ReplayFailureObservation] = []

    def visit(
        observation: ReplayFailureObservation,
        event: ReplayFailureEvent,
        ancestry: frozenset[str],
    ) -> None:
        if event.event_id in ancestry:
            return
        causes = tuple(by_id[item] for item in event.causes if item in by_id)
        if causes:
            for cause in causes:
                visit(observation, cause, ancestry | {event.event_id})
            return
        expanded.append(
            ReplayFailureObservation(
                event=event,
                case_id=observation.case_id,
                task_id=observation.task_id,
                run_id=observation.run_id,
                candidate_id=observation.candidate_id,
                execution_failure=observation.execution_failure,
            )
        )

    for observation in observations:
        visit(observation, observation.event, frozenset())

    groups: dict[str, list[ReplayFailureObservation]] = {}
    for observation in expanded:
        groups.setdefault(observation.event.semantic_key, []).append(observation)
    aggregates: list[AggregatedReplayFailure] = []
    for semantic_key in sorted(groups):
        items = groups[semantic_key]
        event = items[0].event
        execution_ids = {
            item.event.event_id for item in items if item.execution_failure
        }
        # A legacy blocked-only artifact still represents one known cause, but
        # repeated blocked copies of that cause remain one occurrence.
        occurrence_ids = execution_ids or {item.event.event_id for item in items}
        case_ids = sorted(
            {
                clean
                for item in items
                if (clean := _semantic_identity(item.case_id)) is not None
            }
        )
        run_ids = sorted(
            {
                clean
                for item in items
                if (clean := _semantic_identity(item.run_id)) is not None
            }
        )
        task_ids = sorted(
            {
                clean
                for item in items
                if (clean := _semantic_identity(item.task_id)) is not None
            }
        )
        candidate_ids = sorted(
            {
                clean
                for item in items
                if (clean := _semantic_identity(item.candidate_id)) is not None
            }
        )
        artifact_refs = sorted(
            {
                ref
                for item in items
                for ref in item.event.artifact_refs
                if ref
            }
        )
        aggregates.append(
            AggregatedReplayFailure(
                semantic_key=semantic_key,
                code=event.code,
                owner=event.owner,
                stage=event.stage,
                scope=event.scope,
                repairable=event.repairable,
                category=event.category,
                capability_id=event.capability_id,
                requirement_id=event.requirement_id,
                contract_fingerprint=event.contract_fingerprint,
                occurrence_count=max(1, len(occurrence_ids)),
                affected_member_count=len(case_ids),
                occurrence_ids=tuple(sorted(occurrence_ids)[:_MAX_OCCURRENCE_IDS]),
                affected_case_ids=tuple(case_ids[:_MAX_SOURCE_IDS]),
                source_run_ids=tuple(run_ids[:_MAX_SOURCE_IDS]),
                source_task_ids=tuple(task_ids[:_MAX_SOURCE_IDS]),
                source_candidate_ids=tuple(candidate_ids[:_MAX_SOURCE_IDS]),
                artifact_refs=tuple(artifact_refs[:_MAX_ARTIFACT_REFS]),
                source_kinds=tuple(
                    sorted({item.event.source.value for item in items})
                ),
            )
        )
    return tuple(aggregates)


def observe_replay_failures(
    replay_result: Any,
    *,
    normalized: Any | None = None,
) -> tuple[ReplayFailureObservation, ...]:
    """Collect typed failures from the authoritative member lifecycle view."""

    observations: list[ReplayFailureObservation] = []
    request = replay_result.request
    if normalized is not None:
        members = normalized.members
    elif replay_result.member_results is not None:
        members = replay_result.member_results
    else:
        members = ()

    def add_variant(variant: Any, *, case_id: str | None, member_request: Any) -> None:
        if isinstance(variant.failure, ReplayFailureEvent):
            observations.append(
                ReplayFailureObservation(
                    event=variant.failure,
                    case_id=case_id,
                    task_id=getattr(member_request, "task_id", None),
                    run_id=getattr(member_request, "run_id", None),
                    candidate_id=getattr(member_request, "candidate_id", None),
                    execution_failure=True,
                )
            )
        for event in variant.blocked_by:
            observations.append(
                ReplayFailureObservation(
                    event=event,
                    case_id=case_id,
                    task_id=getattr(member_request, "task_id", None),
                    run_id=getattr(member_request, "run_id", None),
                    candidate_id=getattr(member_request, "candidate_id", None),
                    execution_failure=False,
                )
            )
        for repetition in variant.repetition_results:
            add_variant(
                repetition,
                case_id=case_id,
                member_request=member_request,
            )

    if members:
        for member in members:
            member_request = member.request
            add_variant(member.baseline, case_id=member.case_id, member_request=member_request)
            add_variant(member.candidate, case_id=member.case_id, member_request=member_request)
    else:
        add_variant(replay_result.baseline, case_id=request.task_id, member_request=request)
        add_variant(replay_result.candidate, case_id=request.task_id, member_request=request)
    if normalized is not None:
        for event in normalized.failure_events:
            observations.append(
                ReplayFailureObservation(
                    event=event,
                    run_id=request.run_id,
                    task_id=request.task_id,
                    candidate_id=request.candidate_id,
                )
            )
    return tuple(observations)


def aggregate_replay_failures(
    replay_result: Any,
    *,
    normalized: Any | None = None,
) -> tuple[AggregatedReplayFailure, ...]:
    return aggregate_replay_failure_observations(
        observe_replay_failures(replay_result, normalized=normalized)
    )
