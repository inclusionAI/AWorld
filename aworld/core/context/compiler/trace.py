"""Strictly redacted Context Compiler decision traces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Iterable

from .frozen_json import canonical_json_hash
from .models import (
    Authority,
    ContextItem,
    ContextItemRef,
    ContextScope,
    ProviderRequestFidelity,
    ProviderRequestSnapshot,
    RequestCaptureStage,
    ResolutionAction,
    ResolutionDecision,
    ResolutionReason,
    ScopeKind,
    TokenAccounting,
    TokenEstimate,
    Trust,
    _format_datetime,
    _non_empty,
    _parse_datetime,
)


def _redacted_decision(decision: ResolutionDecision) -> dict[str, Any]:
    return {
        "item_id": decision.item_id,
        "action": decision.action.value,
        "reason": decision.reason.value,
        "tokens_before": decision.tokens_before.to_dict(),
        "tokens_after": decision.tokens_after.to_dict(),
        "authority": decision.authority.value,
        "scope_kinds": [kind.value for kind in decision.scope.kinds],
        "trust": decision.trust.value,
        "content_hash": decision.content_hash,
        "artifact_present": decision.artifact_ref is not None,
    }


def _decision_from_redacted(payload: dict[str, Any]) -> ResolutionDecision:
    scope_kinds = tuple(ScopeKind(item) for item in payload["scope_kinds"])
    selector_fields = {
        ScopeKind.WORKSPACE: "workspace_id",
        ScopeKind.DIRECTORY: "directory",
        ScopeKind.PATH_PATTERN: "path_pattern",
        ScopeKind.SESSION: "session_id",
        ScopeKind.TASK: "task_id",
        ScopeKind.TURN: "turn_id",
        ScopeKind.AGENT: "agent_id",
        ScopeKind.CHILD_TASK: "child_task_id",
    }
    redacted_selectors = {
        field_name: "<redacted>"
        for kind, field_name in selector_fields.items()
        if kind in scope_kinds
    }
    return ResolutionDecision(
        item_id=payload["item_id"],
        action=ResolutionAction(payload["action"]),
        reason=ResolutionReason(payload["reason"]),
        tokens_before=TokenEstimate.from_dict(payload["tokens_before"]),
        tokens_after=TokenEstimate.from_dict(payload["tokens_after"]),
        authority=Authority(payload["authority"]),
        scope=ContextScope(kinds=scope_kinds, **redacted_selectors),
        trust=Trust(payload["trust"]),
        content_hash=payload["content_hash"],
        artifact_ref="<redacted>" if payload.get("artifact_present") else None,
    )


def _counts(decisions: Iterable[ResolutionDecision], candidate_count: int) -> dict[str, int]:
    values = {action: 0 for action in ResolutionAction}
    for decision in decisions:
        values[decision.action] += 1
    return {
        "candidates": candidate_count,
        "included": values[ResolutionAction.INCLUDED],
        "excluded": values[ResolutionAction.EXCLUDED],
        "compacted": values[ResolutionAction.COMPACTED],
        "offloaded": values[ResolutionAction.OFFLOADED],
        "unknown": values[ResolutionAction.UNKNOWN],
    }


@dataclass(frozen=True, slots=True)
class ContextDecisionTrace:
    """A safe-to-serialize trace that never retains source/request payloads."""

    SCHEMA_VERSION: ClassVar[str] = "aworld.context.trace.v1"

    trace_id: str | None
    task_id: str | None
    session_id: str | None
    task_epoch: int | None
    compiler_version: str
    items: tuple[ContextItemRef, ...]
    decisions: tuple[ResolutionDecision, ...]
    token_accounting: TokenAccounting
    stable_prefix_hash: str
    serialized_prefix_hash: str
    dynamic_context_hash: str
    request_content_hash: str
    request_provider_name: str | None
    request_capture_stage: RequestCaptureStage
    request_fidelity: ProviderRequestFidelity
    created_at: datetime
    fingerprint: str | None = None

    def __post_init__(self) -> None:
        for name in ("trace_id", "task_id", "session_id"):
            value = getattr(self, name)
            if value is not None:
                _non_empty(name, value)
        if self.task_epoch is not None and (
            isinstance(self.task_epoch, bool)
            or not isinstance(self.task_epoch, int)
            or self.task_epoch < 0
        ):
            raise ValueError("task_epoch must be a non-negative integer or None")
        for name in (
            "compiler_version",
            "stable_prefix_hash",
            "serialized_prefix_hash",
            "dynamic_context_hash",
            "request_content_hash",
        ):
            _non_empty(name, getattr(self, name))
        if self.request_provider_name is not None:
            _non_empty("request_provider_name", self.request_provider_name)
        object.__setattr__(
            self,
            "items",
            tuple(ContextItemRef.from_dict(item) if isinstance(item, dict) else item for item in self.items),
        )
        object.__setattr__(
            self,
            "decisions",
            tuple(
                _decision_from_redacted(
                    item if isinstance(item, dict) else _redacted_decision(item)
                )
                for item in self.decisions
            ),
        )
        if not all(isinstance(item, ContextItemRef) for item in self.items):
            raise TypeError("items must contain ContextItemRef values")
        if not all(isinstance(item, ResolutionDecision) for item in self.decisions):
            raise TypeError("decisions must contain ResolutionDecision values")
        if len(self.items) != len(self.decisions):
            raise ValueError("every candidate item must have exactly one resolution decision")
        item_ids = [item.item_id for item in self.items]
        decision_ids = [decision.item_id for decision in self.decisions]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("trace item ids must be unique; use occurrence for duplicate content")
        if set(item_ids) != set(decision_ids) or len(set(decision_ids)) != len(decision_ids):
            raise ValueError("resolution decisions must map one-to-one to candidate item ids")
        if isinstance(self.token_accounting, dict):
            object.__setattr__(
                self, "token_accounting", TokenAccounting.from_dict(self.token_accounting)
            )
        if not isinstance(self.token_accounting, TokenAccounting):
            raise TypeError("token_accounting must be a TokenAccounting")
        object.__setattr__(
            self, "request_capture_stage", RequestCaptureStage(self.request_capture_stage)
        )
        object.__setattr__(
            self, "request_fidelity", ProviderRequestFidelity(self.request_fidelity)
        )
        _format_datetime(self.created_at)
        expected = canonical_json_hash(self._fingerprint_payload())
        if self.fingerprint is not None and self.fingerprint != expected:
            raise ValueError("trace fingerprint does not match redacted decision content")
        object.__setattr__(self, "fingerprint", expected)

    @classmethod
    def build(
        cls,
        *,
        trace_id: str | None,
        task_id: str | None,
        session_id: str | None,
        task_epoch: int | None,
        compiler_version: str,
        items: Iterable[ContextItem],
        decisions: Iterable[ResolutionDecision],
        token_accounting: TokenAccounting,
        stable_prefix_hash: str,
        serialized_prefix_hash: str,
        dynamic_context_hash: str,
        request_snapshot: ProviderRequestSnapshot,
        created_at: datetime,
    ) -> "ContextDecisionTrace":
        return cls(
            trace_id=trace_id,
            task_id=task_id,
            session_id=session_id,
            task_epoch=task_epoch,
            compiler_version=compiler_version,
            items=tuple(item.to_ref() for item in items),
            decisions=tuple(decisions),
            token_accounting=token_accounting,
            stable_prefix_hash=stable_prefix_hash,
            serialized_prefix_hash=serialized_prefix_hash,
            dynamic_context_hash=dynamic_context_hash,
            request_content_hash=request_snapshot.content_hash or "",
            request_provider_name=request_snapshot.provider_name,
            request_capture_stage=request_snapshot.capture_stage,
            request_fidelity=request_snapshot.fidelity,
            created_at=created_at,
        )

    def _fingerprint_payload(self) -> dict[str, Any]:
        # Correlation IDs and wall-clock timestamps are intentionally excluded.
        # item_id is also excluded because legacy sources may only supply a
        # random identifier; content hash + occurrence preserves duplicates.
        return {
            "compiler_version": self.compiler_version,
            "task_epoch": self.task_epoch,
            "items": [
                {
                    "kind": item.kind.value,
                    "content_hash": item.content_hash,
                    "source_kind": item.source_kind.value,
                    "occurrence": item.occurrence,
                    "preview": item.preview,
                }
                for item in self.items
            ],
            "decisions": [
                {
                    key: value
                    for key, value in _redacted_decision(decision).items()
                    if key != "item_id"
                }
                for decision in self.decisions
            ],
            "counts": _counts(self.decisions, len(self.items)),
            "token_accounting": self.token_accounting.to_dict(),
            "hashes": {
                "stable_prefix": self.stable_prefix_hash,
                "serialized_prefix": self.serialized_prefix_hash,
                "dynamic_context": self.dynamic_context_hash,
            },
            "request": {
                "content_hash": self.request_content_hash,
                "provider_name": self.request_provider_name,
                "capture_stage": self.request_capture_stage.value,
                "fidelity": self.request_fidelity.value,
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "trace_id": self.trace_id,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "task_epoch": self.task_epoch,
            "compiler_version": self.compiler_version,
            "items": [item.to_dict() for item in self.items],
            "decisions": [_redacted_decision(decision) for decision in self.decisions],
            "counts": _counts(self.decisions, len(self.items)),
            "token_accounting": self.token_accounting.to_dict(),
            "hashes": {
                "stable_prefix": self.stable_prefix_hash,
                "serialized_prefix": self.serialized_prefix_hash,
                "dynamic_context": self.dynamic_context_hash,
            },
            "request": {
                "content_hash": self.request_content_hash,
                "provider_name": self.request_provider_name,
                "capture_stage": self.request_capture_stage.value,
                "fidelity": self.request_fidelity.value,
            },
            "created_at": _format_datetime(self.created_at),
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ContextDecisionTrace":
        values = dict(payload)
        schema_version = values.pop("schema_version", cls.SCHEMA_VERSION)
        if schema_version != cls.SCHEMA_VERSION:
            raise ValueError(f"unsupported context trace schema: {schema_version}")
        expected_counts = values.pop("counts", None)
        hashes = values.pop("hashes")
        request = values.pop("request")
        values.update(
            stable_prefix_hash=hashes["stable_prefix"],
            serialized_prefix_hash=hashes["serialized_prefix"],
            dynamic_context_hash=hashes["dynamic_context"],
            request_content_hash=request["content_hash"],
            request_provider_name=request.get("provider_name"),
            request_capture_stage=request["capture_stage"],
            request_fidelity=request["fidelity"],
            created_at=_parse_datetime(values["created_at"]),
        )
        trace = cls(**values)
        if expected_counts is not None and expected_counts != _counts(trace.decisions, len(trace.items)):
            raise ValueError("trace counts do not match decisions")
        return trace


__all__ = ["ContextDecisionTrace"]
