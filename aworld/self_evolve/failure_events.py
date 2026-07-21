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

FAILURE_EVENT_SCHEMA_VERSION = "aworld.self_evolve.replay_failure.v3"
_LEGACY_TYPED_FAILURE_EVENT_SCHEMA_VERSION = "aworld.self_evolve.replay_failure.v2"
AGGREGATED_FAILURE_SCHEMA_VERSION = "aworld.self_evolve.replay_failure_aggregate.v1"
_MAX_SUMMARY_CHARS = 1_000
_MAX_CATEGORY_CHARS = 128
_MAX_DIAGNOSTIC_ITEMS = 32
_MAX_DIAGNOSTIC_DEPTH = 4
_MAX_ARTIFACT_REFS = 16
_MAX_OCCURRENCE_IDS = 64
_MAX_SOURCE_IDS = 32
_CODE_RE = re.compile(r"[^a-z0-9_]+")
_SHA256_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*-sha256-[0-9a-f]{64}$")


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
    clean = str(value).strip()
    return clean or None


def _identity_digest(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_identity_digest(
    *,
    field_name: str,
    value: str | None,
    digest: str | None,
    allow_digest_only: bool = False,
) -> str | None:
    expected = _identity_digest(value)
    if digest is None:
        return expected
    if not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
        raise ValueError(f"{field_name} must be a full sha256 digest")
    if value is None and allow_digest_only:
        return digest
    if expected != digest:
        raise ValueError(f"{field_name} does not match its canonical identity")
    return digest


def _exact_occurrence_id(value: Any, *, field_name: str = "event_id") -> str:
    clean = str(value)
    if not clean:
        raise ValueError(f"failure {field_name} must be non-empty")
    if clean != clean.strip():
        raise ValueError(f"failure {field_name} must not have surrounding whitespace")
    if len(clean) > 160:
        raise ValueError(f"failure {field_name} exceeds 160 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in clean):
        raise ValueError(f"failure {field_name} must not contain control characters")
    return clean


def _failure_semantic_key(
    *,
    owner: FailureOwner,
    stage: FailureStage,
    code: str,
    scope: FailureScope,
    capability_identity_digest: str | None,
    requirement_identity_digest: str | None,
    contract_identity_digest: str | None,
    repairable: bool,
    category: str,
) -> str:
    payload = {
        "owner": owner.value,
        "stage": stage.value,
        "code": code,
        "scope": scope.value,
        "capability_identity_digest": capability_identity_digest,
        "requirement_identity_digest": requirement_identity_digest,
        "contract_identity_digest": contract_identity_digest,
        "repairable": repairable,
        "category": category,
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"replay-failure-{digest}"


def _stable_digest_id(prefix: str, payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-sha256-{digest}"


def _identity_sample(value: str, *, max_chars: int = 160) -> str:
    """Return a bounded display sample without using it as identity."""

    sanitized = sanitize_text(value, max_chars=max_chars)
    if sanitized == value and len(value) <= max_chars:
        return sanitized
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    suffix = f"...#{digest}"
    prefix = sanitize_text(value, max_chars=max(1, max_chars - len(suffix)))
    return f"{prefix}{suffix}"[:max_chars]


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
    capability_identity_digest: str | None = None
    requirement_identity_digest: str | None = None
    contract_identity_digest: str | None = None
    event_id: str = field(default_factory=lambda: f"replay-event-{uuid.uuid4().hex}")
    schema_version: str = FAILURE_EVENT_SCHEMA_VERSION
    _compatibility: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.schema_version != FAILURE_EVENT_SCHEMA_VERSION:
            raise ValueError("unsupported replay failure schema_version")
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
        event_id = _exact_occurrence_id(self.event_id)
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
        if len(self.causes) > 32:
            raise ValueError("failure causes exceeds 32 event ids")
        object.__setattr__(
            self,
            "causes",
            tuple(
                dict.fromkeys(
                    _exact_occurrence_id(item, field_name="cause event_id")
                    for item in self.causes
                )
            ),
        )
        object.__setattr__(self, "_compatibility", dict(compatibility))
        # Only exact machine identity fields may flow out of the compatibility
        # boundary.  Nested diagnostics and prose are deliberately ignored.
        compatibility_capability_id = compatibility.get("capability_id") or compatibility.get(
            "replay_capability_id"
        )
        compatibility_requirement_id = compatibility.get("requirement_id")
        capability_id = _semantic_identity(
            self.capability_id or compatibility_capability_id
        )
        requirement_id = _semantic_identity(
            self.requirement_id or compatibility_requirement_id
        )
        contract_fingerprint = _semantic_identity(self.contract_fingerprint)
        object.__setattr__(self, "capability_id", capability_id)
        object.__setattr__(self, "requirement_id", requirement_id)
        object.__setattr__(self, "contract_fingerprint", contract_fingerprint)
        object.__setattr__(
            self,
            "capability_identity_digest",
            _validate_identity_digest(
                field_name="capability_identity_digest",
                value=capability_id,
                digest=self.capability_identity_digest,
            ),
        )
        object.__setattr__(
            self,
            "requirement_identity_digest",
            _validate_identity_digest(
                field_name="requirement_identity_digest",
                value=requirement_id,
                digest=self.requirement_identity_digest,
            ),
        )
        object.__setattr__(
            self,
            "contract_identity_digest",
            _validate_identity_digest(
                field_name="contract_identity_digest",
                value=contract_fingerprint,
                digest=self.contract_identity_digest,
            ),
        )
        object.__setattr__(
            self,
            "contract_fingerprint",
            _semantic_identity(self.contract_fingerprint),
        )

    @property
    def semantic_key(self) -> str:
        """Stable failure identity with all occurrence data deliberately excluded."""

        return _failure_semantic_key(
            owner=self.owner,
            stage=self.stage,
            code=self.code,
            scope=self.scope,
            capability_identity_digest=self.capability_identity_digest,
            requirement_identity_digest=self.requirement_identity_digest,
            contract_identity_digest=self.contract_identity_digest,
            repairable=self.repairable,
            category=self.category,
        )

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
            "capability_identity_digest": self.capability_identity_digest,
            "requirement_identity_digest": self.requirement_identity_digest,
            "contract_identity_digest": self.contract_identity_digest,
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
        serialized_schema = payload.get("schema_version")
        if serialized_schema not in {
            FAILURE_EVENT_SCHEMA_VERSION,
            _LEGACY_TYPED_FAILURE_EVENT_SCHEMA_VERSION,
        }:
            return cls.from_legacy_mapping(payload)
        if "source" not in payload:
            raise ValueError("v2 replay failure event source is required")
        if serialized_schema == FAILURE_EVENT_SCHEMA_VERSION:
            if not payload.get("semantic_key"):
                raise ValueError("v3 replay failure event semantic_key is required")
            for identity_field, digest_field in (
                ("capability_id", "capability_identity_digest"),
                ("requirement_id", "requirement_identity_digest"),
                ("contract_fingerprint", "contract_identity_digest"),
            ):
                if payload.get(identity_field) is not None and not payload.get(
                    digest_field
                ):
                    raise ValueError(
                        f"v3 replay failure {identity_field} requires {digest_field}"
                    )
        diagnostics = payload.get("diagnostics")
        artifact_refs = payload.get("artifact_refs")
        causes = payload.get("causes")
        event = cls(
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
            capability_identity_digest=(
                str(payload.get("capability_identity_digest"))
                if payload.get("capability_identity_digest") is not None
                else None
            ),
            requirement_identity_digest=(
                str(payload.get("requirement_identity_digest"))
                if payload.get("requirement_identity_digest") is not None
                else None
            ),
            contract_identity_digest=(
                str(payload.get("contract_identity_digest"))
                if payload.get("contract_identity_digest") is not None
                else None
            ),
        )
        serialized_key = payload.get("semantic_key")
        if (
            serialized_schema == FAILURE_EVENT_SCHEMA_VERSION
            and serialized_key is not None
            and serialized_key != event.semantic_key
        ):
            raise ValueError("replay failure semantic_key does not match typed identity")
        return event

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

    by_id = _events_by_id(events)
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
    capability_identity_digest: str | None = None
    requirement_identity_digest: str | None = None
    contract_identity_digest: str | None = None
    occurrence_count: int = 1
    affected_member_count: int = 0
    distinct_source_count: int = 0
    occurrence_ids: tuple[str, ...] = ()
    affected_case_ids: tuple[str, ...] = ()
    source_run_ids: tuple[str, ...] = ()
    source_task_ids: tuple[str, ...] = ()
    source_candidate_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    source_kinds: tuple[str, ...] = ()
    batch_id: str = ""
    emission_id: str = ""
    aggregate_digest: str = ""
    schema_version: str = AGGREGATED_FAILURE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AGGREGATED_FAILURE_SCHEMA_VERSION:
            raise ValueError("unsupported aggregate replay failure schema_version")
        try:
            owner = FailureOwner(self.owner)
            stage = FailureStage(self.stage)
            scope = FailureScope(self.scope)
        except ValueError as exc:
            raise ValueError("invalid aggregate replay failure enum value") from exc
        code = _stable_code(self.code, default="replay_failure")
        if code != self.code:
            raise ValueError("aggregate failure code must be a stable lowercase identifier")
        category = sanitize_text(str(self.category), max_chars=_MAX_CATEGORY_CHARS)
        capability_id = _semantic_identity(self.capability_id)
        requirement_id = _semantic_identity(self.requirement_id)
        contract_fingerprint = _semantic_identity(self.contract_fingerprint)
        capability_digest = _validate_identity_digest(
            field_name="capability_identity_digest",
            value=capability_id,
            digest=self.capability_identity_digest,
            allow_digest_only=True,
        )
        requirement_digest = _validate_identity_digest(
            field_name="requirement_identity_digest",
            value=requirement_id,
            digest=self.requirement_identity_digest,
            allow_digest_only=True,
        )
        contract_digest = _validate_identity_digest(
            field_name="contract_identity_digest",
            value=contract_fingerprint,
            digest=self.contract_identity_digest,
            allow_digest_only=True,
        )
        expected_key = _failure_semantic_key(
            owner=owner,
            stage=stage,
            code=code,
            scope=scope,
            capability_identity_digest=capability_digest,
            requirement_identity_digest=requirement_digest,
            contract_identity_digest=contract_digest,
            repairable=self.repairable,
            category=category,
        )
        if self.semantic_key != expected_key:
            raise ValueError("aggregate semantic_key does not match typed identity")
        occurrence_count = _exact_nonnegative_count(
            self.occurrence_count,
            field_name="occurrence_count",
            minimum=1,
        )
        affected_member_count = _exact_nonnegative_count(
            self.affected_member_count,
            field_name="affected_member_count",
        )
        distinct_source_count = _exact_nonnegative_count(
            self.distinct_source_count,
            field_name="distinct_source_count",
        )
        occurrence_ids = _bounded_id_samples(
            self.occurrence_ids,
            limit=_MAX_OCCURRENCE_IDS,
            exact_occurrence_ids=True,
        )
        affected_case_ids = _bounded_id_samples(
            self.affected_case_ids, limit=_MAX_SOURCE_IDS
        )
        source_run_ids = _bounded_id_samples(
            self.source_run_ids, limit=_MAX_SOURCE_IDS
        )
        source_task_ids = _bounded_id_samples(
            self.source_task_ids, limit=_MAX_SOURCE_IDS
        )
        source_candidate_ids = _bounded_id_samples(
            self.source_candidate_ids, limit=_MAX_SOURCE_IDS
        )
        if occurrence_count < len(occurrence_ids):
            raise ValueError("occurrence_count cannot be smaller than occurrence samples")
        if affected_member_count < len(affected_case_ids):
            raise ValueError(
                "affected_member_count cannot be smaller than affected case samples"
            )
        if distinct_source_count < max(
            len(source_run_ids),
            len(source_task_ids),
            len(source_candidate_ids),
            0,
        ):
            raise ValueError(
                "distinct_source_count cannot be smaller than source id samples"
            )
        artifact_refs = tuple(
            sanitize_text(str(item), max_chars=512)
            for item in self.artifact_refs[:_MAX_ARTIFACT_REFS]
            if str(item).strip()
        )
        source_kinds = tuple(
            sorted(
                {
                    FailureEventSource(str(item)).value
                    for item in self.source_kinds
                }
            )
        )
        object.__setattr__(self, "owner", owner)
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "capability_id", capability_id)
        object.__setattr__(self, "requirement_id", requirement_id)
        object.__setattr__(self, "contract_fingerprint", contract_fingerprint)
        object.__setattr__(self, "capability_identity_digest", capability_digest)
        object.__setattr__(self, "requirement_identity_digest", requirement_digest)
        object.__setattr__(self, "contract_identity_digest", contract_digest)
        object.__setattr__(self, "occurrence_count", occurrence_count)
        object.__setattr__(self, "affected_member_count", affected_member_count)
        object.__setattr__(self, "distinct_source_count", distinct_source_count)
        object.__setattr__(self, "occurrence_ids", occurrence_ids)
        object.__setattr__(self, "affected_case_ids", affected_case_ids)
        object.__setattr__(self, "source_run_ids", source_run_ids)
        object.__setattr__(self, "source_task_ids", source_task_ids)
        object.__setattr__(self, "source_candidate_ids", source_candidate_ids)
        object.__setattr__(self, "artifact_refs", artifact_refs)
        object.__setattr__(self, "source_kinds", source_kinds)
        batch_id = self.batch_id or _stable_digest_id(
            "replay-batch",
            {
                "semantic_key": expected_key,
                "occurrence_count": occurrence_count,
                "affected_member_count": affected_member_count,
                "distinct_source_count": distinct_source_count,
                "occurrence_ids": occurrence_ids,
                "affected_case_ids": affected_case_ids,
                "source_run_ids": source_run_ids,
                "source_task_ids": source_task_ids,
                "source_candidate_ids": source_candidate_ids,
            },
        )
        _validate_digest_id(batch_id, prefix="replay-batch")
        object.__setattr__(self, "batch_id", batch_id)
        aggregate_digest = _stable_digest_id(
            "replay-aggregate",
            self._digest_payload(),
        )
        if self.aggregate_digest and self.aggregate_digest != aggregate_digest:
            raise ValueError("aggregate_digest does not match typed aggregate payload")
        object.__setattr__(self, "aggregate_digest", aggregate_digest)
        emission_id = _stable_digest_id(
            "replay-emission",
            {"batch_id": batch_id, "aggregate_digest": aggregate_digest},
        )
        if self.emission_id and self.emission_id != emission_id:
            raise ValueError("emission_id does not match aggregate emission")
        object.__setattr__(self, "emission_id", emission_id)

    def _digest_payload(self) -> dict[str, Any]:
        return {
            "semantic_key": self.semantic_key,
            "code": self.code,
            "owner": self.owner.value,
            "stage": self.stage.value,
            "scope": self.scope.value,
            "repairable": self.repairable,
            "category": self.category,
            "capability_identity_digest": self.capability_identity_digest,
            "requirement_identity_digest": self.requirement_identity_digest,
            "contract_identity_digest": self.contract_identity_digest,
            "occurrence_count": self.occurrence_count,
            "affected_member_count": self.affected_member_count,
            "distinct_source_count": self.distinct_source_count,
            "occurrence_ids": list(self.occurrence_ids),
            "affected_case_ids": list(self.affected_case_ids),
            "source_run_ids": list(self.source_run_ids),
            "source_task_ids": list(self.source_task_ids),
            "source_candidate_ids": list(self.source_candidate_ids),
            "source_kinds": list(self.source_kinds),
            "batch_id": self.batch_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
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
            "capability_identity_digest": self.capability_identity_digest,
            "requirement_identity_digest": self.requirement_identity_digest,
            "contract_identity_digest": self.contract_identity_digest,
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
            "batch_id": self.batch_id,
            "emission_id": self.emission_id,
            "aggregate_digest": self.aggregate_digest,
        }

    def to_feedback_dict(self) -> dict[str, Any]:
        """Return the typed, path-free optimizer/lesson transport payload."""

        payload = self.to_dict()
        payload.pop("artifact_refs", None)
        # Full canonical identifiers remain in persisted typed artifacts where
        # their digests can be verified. Optimizer transport needs only the
        # identity digests; prose/path-like identifier values are not copied.
        payload.pop("capability_id", None)
        payload.pop("requirement_id", None)
        payload.pop("contract_fingerprint", None)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "AggregatedReplayFailure":
        serialized_schema = payload.get("schema_version")
        strict = serialized_schema == AGGREGATED_FAILURE_SCHEMA_VERSION
        if serialized_schema is not None and not strict:
            raise ValueError("unsupported aggregate replay failure schema_version")
        if strict:
            required = {
                "semantic_key",
                "occurrence_count",
                "affected_member_count",
                "distinct_source_count",
                "batch_id",
                "emission_id",
                "aggregate_digest",
            }
            missing = sorted(
                key for key in required if key not in payload or payload.get(key) == ""
            )
            if missing:
                raise ValueError(
                    "typed aggregate is missing required fields: " + ", ".join(missing)
                )
            for identity_field, digest_field in (
                ("capability_id", "capability_identity_digest"),
                ("requirement_id", "requirement_identity_digest"),
                ("contract_fingerprint", "contract_identity_digest"),
            ):
                if payload.get(identity_field) is not None and not payload.get(
                    digest_field
                ):
                    raise ValueError(
                        f"typed aggregate {identity_field} requires {digest_field}"
                    )
        owner = FailureOwner(str(payload.get("owner") or ""))
        stage = FailureStage(str(payload.get("stage") or ""))
        scope = FailureScope(str(payload.get("scope") or ""))
        code = str(payload.get("code") or "")
        category = str(payload.get("category") or "replay")
        capability_id = _optional_string(payload.get("capability_id"))
        requirement_id = _optional_string(payload.get("requirement_id"))
        contract_fingerprint = _optional_string(payload.get("contract_fingerprint"))
        capability_digest = _validate_identity_digest(
            field_name="capability_identity_digest",
            value=capability_id,
            digest=_optional_string(payload.get("capability_identity_digest")),
            allow_digest_only=True,
        )
        requirement_digest = _validate_identity_digest(
            field_name="requirement_identity_digest",
            value=requirement_id,
            digest=_optional_string(payload.get("requirement_identity_digest")),
            allow_digest_only=True,
        )
        contract_digest = _validate_identity_digest(
            field_name="contract_identity_digest",
            value=contract_fingerprint,
            digest=_optional_string(payload.get("contract_identity_digest")),
            allow_digest_only=True,
        )
        semantic_key = _failure_semantic_key(
            owner=owner,
            stage=stage,
            code=code,
            scope=scope,
            capability_identity_digest=capability_digest,
            requirement_identity_digest=requirement_digest,
            contract_identity_digest=contract_digest,
            repairable=payload.get("repairable") is True,
            category=sanitize_text(category, max_chars=_MAX_CATEGORY_CHARS),
        )
        if strict and payload.get("semantic_key") != semantic_key:
            raise ValueError("serialized aggregate semantic_key is invalid")
        occurrence_ids = _mapping_string_tuple(payload.get("occurrence_ids"))
        affected_case_ids = _mapping_string_tuple(payload.get("affected_case_ids"))
        source_run_ids = _mapping_string_tuple(payload.get("source_run_ids"))
        source_task_ids = _mapping_string_tuple(payload.get("source_task_ids"))
        source_candidate_ids = _mapping_string_tuple(
            payload.get("source_candidate_ids")
        )
        occurrence_count = _payload_count(
            payload,
            "occurrence_count",
            default=max(1, len(occurrence_ids)),
            minimum=1,
        )
        affected_member_count = _payload_count(
            payload,
            "affected_member_count",
            default=len(affected_case_ids),
        )
        distinct_source_count = _payload_count(
            payload,
            "distinct_source_count",
            default=max(
                len(source_run_ids),
                len(source_task_ids),
                len(source_candidate_ids),
                0,
            ),
        )
        source_kinds = _mapping_string_tuple(payload.get("source_kinds"))
        if not source_kinds:
            source_kinds = (FailureEventSource.LEGACY_INFERRED.value,)
        return cls(
            semantic_key=semantic_key,
            code=code,
            owner=owner,
            stage=stage,
            scope=scope,
            repairable=payload.get("repairable") is True,
            category=category,
            capability_id=capability_id,
            requirement_id=requirement_id,
            contract_fingerprint=contract_fingerprint,
            capability_identity_digest=capability_digest,
            requirement_identity_digest=requirement_digest,
            contract_identity_digest=contract_digest,
            occurrence_count=occurrence_count,
            affected_member_count=affected_member_count,
            distinct_source_count=distinct_source_count,
            occurrence_ids=occurrence_ids,
            affected_case_ids=affected_case_ids,
            source_run_ids=source_run_ids,
            source_task_ids=source_task_ids,
            source_candidate_ids=source_candidate_ids,
            artifact_refs=_mapping_string_tuple(payload.get("artifact_refs")),
            source_kinds=source_kinds,
            batch_id=(str(payload.get("batch_id") or "") if strict else ""),
            emission_id=(str(payload.get("emission_id") or "") if strict else ""),
            aggregate_digest=(
                str(payload.get("aggregate_digest") or "") if strict else ""
            ),
        )


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _mapping_string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _exact_nonnegative_count(
    value: Any,
    *,
    field_name: str,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field_name} must be an integer >= {minimum}")
    return value


def _payload_count(
    payload: Mapping[str, Any],
    field_name: str,
    *,
    default: int,
    minimum: int = 0,
) -> int:
    value = payload.get(field_name, default)
    return _exact_nonnegative_count(value, field_name=field_name, minimum=minimum)


def _bounded_id_samples(
    values: tuple[str, ...],
    *,
    limit: int,
    exact_occurrence_ids: bool = False,
) -> tuple[str, ...]:
    samples: list[str] = []
    for value in values[:limit]:
        if exact_occurrence_ids:
            samples.append(_exact_occurrence_id(value, field_name="occurrence sample"))
        else:
            samples.append(_identity_sample(str(value)))
    return tuple(dict.fromkeys(samples))


def _validate_digest_id(value: str, *, prefix: str) -> None:
    if not _SHA256_ID_RE.fullmatch(value) or not value.startswith(f"{prefix}-sha256-"):
        raise ValueError(f"{prefix} id must contain a full sha256 digest")


def aggregate_replay_failure_observations(
    observations: tuple[ReplayFailureObservation, ...],
) -> tuple[AggregatedReplayFailure, ...]:
    """Aggregate causal leaves by semantic identity in deterministic order."""

    all_events = tuple(observation.event for observation in observations)
    by_id = _events_by_id(all_events)
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
        distinct_source_count = max(
            len(run_ids), len(task_ids), len(candidate_ids), 0
        )
        batch_id = _stable_digest_id(
            "replay-batch",
            {
                "semantic_key": semantic_key,
                "observations": sorted(
                    [
                    (
                        item.event.event_id,
                        _semantic_identity(item.case_id),
                        _semantic_identity(item.run_id),
                        _semantic_identity(item.task_id),
                        _semantic_identity(item.candidate_id),
                        item.execution_failure,
                    )
                    for item in items
                    ],
                    key=lambda value: json.dumps(
                        value, ensure_ascii=False, separators=(",", ":")
                    ),
                ),
                "occurrence_ids": sorted(occurrence_ids),
                "case_ids": case_ids,
                "run_ids": run_ids,
                "task_ids": task_ids,
                "candidate_ids": candidate_ids,
            },
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
                capability_identity_digest=event.capability_identity_digest,
                requirement_identity_digest=event.requirement_identity_digest,
                contract_identity_digest=event.contract_identity_digest,
                occurrence_count=max(1, len(occurrence_ids)),
                affected_member_count=len(case_ids),
                distinct_source_count=distinct_source_count,
                occurrence_ids=tuple(sorted(occurrence_ids)[:_MAX_OCCURRENCE_IDS]),
                affected_case_ids=tuple(
                    _identity_sample(item) for item in case_ids[:_MAX_SOURCE_IDS]
                ),
                source_run_ids=tuple(
                    _identity_sample(item) for item in run_ids[:_MAX_SOURCE_IDS]
                ),
                source_task_ids=tuple(
                    _identity_sample(item) for item in task_ids[:_MAX_SOURCE_IDS]
                ),
                source_candidate_ids=tuple(
                    _identity_sample(item)
                    for item in candidate_ids[:_MAX_SOURCE_IDS]
                ),
                artifact_refs=tuple(artifact_refs[:_MAX_ARTIFACT_REFS]),
                source_kinds=tuple(
                    sorted({item.event.source.value for item in items})
                ),
                batch_id=batch_id,
            )
        )
    return tuple(aggregates)


def _events_by_id(
    events: tuple[ReplayFailureEvent, ...],
) -> dict[str, ReplayFailureEvent]:
    by_id: dict[str, ReplayFailureEvent] = {}
    for event in events:
        previous = by_id.setdefault(event.event_id, event)
        if previous.to_dict() != event.to_dict():
            raise ValueError(
                f"replay failure event_id {event.event_id!r} was reused for a different occurrence"
            )
    return by_id


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
