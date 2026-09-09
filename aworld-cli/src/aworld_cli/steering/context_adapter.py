"""Owner-side Context Compiler observation for drained CLI steering inputs."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from aworld.core.context.compiler.adapters import (
    AdapterDiagnostic,
    AdapterDiagnosticSeverity,
    AdapterResult,
)
from aworld.core.context.compiler.models import (
    Authority,
    ContextItem,
    ContextKind,
    ContextScope,
    ContextSource,
    Lifetime,
    ScopeKind,
    SourceKind,
    Stability,
    Trust,
)

from .coordinator import SteeringInput


_UNKNOWN_POLICY_FIELDS = (
    "authority",
    "lifetime",
    "priority",
    "required",
    "trust",
    "stability",
    "token_estimate",
)


def _validate_input(
    occurrences: Sequence[Any],
    source_identity: str,
    session_id: str | None,
    task_id: str | None,
    task_epoch: int | None,
) -> None:
    if isinstance(occurrences, (str, bytes, bytearray)) or not isinstance(
        occurrences, Sequence
    ):
        raise TypeError("occurrences must be a sequence")
    if not isinstance(source_identity, str) or not source_identity.strip():
        raise ValueError("source_identity must be a non-empty string")
    for field_name, value in (("session_id", session_id), ("task_id", task_id)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{field_name} must be a non-empty string or None")
    if task_epoch is not None and (
        isinstance(task_epoch, bool)
        or not isinstance(task_epoch, int)
        or task_epoch < 0
    ):
        raise ValueError("task_epoch must be a non-negative integer or None")


def _explicit_scope(
    *, session_id: str | None, task_id: str | None
) -> ContextScope:
    kinds: list[ScopeKind] = []
    values: dict[str, str] = {}
    if session_id is not None:
        kinds.append(ScopeKind.SESSION)
        values["session_id"] = session_id
    if task_id is not None:
        kinds.append(ScopeKind.TASK)
        values["task_id"] = task_id
    if not kinds:
        return ContextScope.unknown()
    return ContextScope(kinds=tuple(kinds), **values)


def _created_at(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("SteeringInput.created_at must be a non-empty ISO-8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("SteeringInput.created_at must be timezone-aware")
    return parsed


class SteeringInputContextAdapter:
    """Observe steering already drained by its coordinator, without applying it."""

    def adapt(
        self,
        occurrences: Sequence[SteeringInput],
        *,
        source_identity: str = "aworld-cli://steering/checkpoint",
        task_epoch: int | None = None,
        session_id: str | None = None,
        task_id: str | None = None,
    ) -> AdapterResult:
        _validate_input(
            occurrences, source_identity, session_id, task_id, task_epoch
        )
        scope = _explicit_scope(session_id=session_id, task_id=task_id)
        items: list[ContextItem] = []
        diagnostics: list[AdapterDiagnostic] = [
            AdapterDiagnostic(
                code="owner_drained_steering_order",
                message=(
                    "Steering inputs retain the coordinator's drained order and "
                    "duplicates; this adapter did not drain, apply, sort, or merge them."
                ),
                severity=AdapterDiagnosticSeverity.INFO,
                source_identity=source_identity,
            )
        ]
        for occurrence, item in enumerate(occurrences):
            if not isinstance(item, SteeringInput):
                raise TypeError("steering occurrences must contain SteeringInput values")
            unknown_fields = list(_UNKNOWN_POLICY_FIELDS)
            if scope.kinds == (ScopeKind.UNKNOWN,):
                unknown_fields.append("scope")
            if task_epoch is None:
                unknown_fields.append("task_epoch")
            payload = {
                "sequence": item.sequence,
                "text": item.text,
                "created_at": item.created_at,
            }
            # priority/required are inert observation sentinels. Their compiler
            # meaning remains unknown in the adjacent diagnostic.
            items.append(
                ContextItem(
                    id=f"{source_identity}:steering:{occurrence}",
                    kind=ContextKind.STEERING,
                    payload=payload,
                    task_epoch=task_epoch,
                    authority=Authority.UNKNOWN,
                    scope=scope,
                    lifetime=Lifetime.UNKNOWN,
                    priority=0,
                    required=False,
                    trust=Trust.UNKNOWN,
                    stability=Stability.UNKNOWN,
                    token_limit=None,
                    reducer=None,
                    source=ContextSource(
                        kind=SourceKind.STEERING,
                        uri=source_identity,
                        ref={"sequence": item.sequence, "occurrence": occurrence},
                    ),
                    version=None,
                    activation_reason="observed_steering_input_occurrence",
                    created_at=_created_at(item.created_at),
                    occurrence=occurrence,
                )
            )
            diagnostics.append(
                AdapterDiagnostic(
                    code="steering_policy_unknown",
                    message=(
                        "Steering owner data proves sequence, content, time, and "
                        "explicitly supplied scope only; policy was not inferred."
                    ),
                    severity=AdapterDiagnosticSeverity.INFO,
                    source_identity=source_identity,
                    occurrence=occurrence,
                    unknown_fields=tuple(unknown_fields),
                )
            )
        return AdapterResult(items=tuple(items), diagnostics=tuple(diagnostics))


def adapt_steering_inputs(
    occurrences: Sequence[SteeringInput],
    *,
    source_identity: str = "aworld-cli://steering/checkpoint",
    task_epoch: int | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
) -> AdapterResult:
    return SteeringInputContextAdapter().adapt(
        occurrences,
        source_identity=source_identity,
        task_epoch=task_epoch,
        session_id=session_id,
        task_id=task_id,
    )


class AppliedSteeringContextAdapter:
    """Owner proof for the exact user messages appended by the CLI hook."""

    def adapt(
        self,
        occurrences: Sequence[SteeringInput],
        *,
        source_identity: str,
        task_epoch: int,
        session_id: str,
        task_id: str,
    ) -> AdapterResult:
        _validate_input(
            occurrences, source_identity, session_id, task_id, task_epoch
        )
        scope = _explicit_scope(session_id=session_id, task_id=task_id)
        return AdapterResult(
            items=tuple(
                ContextItem(
                    id=f"{source_identity}:applied:{index}",
                    kind=ContextKind.STEERING,
                    payload={"role": "user", "content": item.text},
                    task_epoch=task_epoch,
                    authority=Authority.USER,
                    scope=scope,
                    lifetime=Lifetime.SINGLE_CALL,
                    priority=item.sequence,
                    required=True,
                    trust=Trust.USER_CONTROLLED,
                    stability=Stability.TURN_DYNAMIC,
                    token_limit=None,
                    reducer=None,
                    source=ContextSource(
                        kind=SourceKind.STEERING,
                        uri=source_identity,
                        version="cli-steering-applied-v1",
                        ref={"sequence": item.sequence, "created_at": item.created_at},
                    ),
                    version="v1",
                    activation_reason="cli_steering_drained_before_final_compile",
                    created_at=_created_at(item.created_at),
                    occurrence=index,
                )
                for index, item in enumerate(occurrences)
            ),
            diagnostics=(),
        )


def adapt_applied_steering(
    occurrences: Sequence[SteeringInput],
    *,
    source_identity: str,
    task_epoch: int,
    session_id: str,
    task_id: str,
) -> AdapterResult:
    return AppliedSteeringContextAdapter().adapt(
        occurrences,
        source_identity=source_identity,
        task_epoch=task_epoch,
        session_id=session_id,
        task_id=task_id,
    )


__all__ = [
    "AppliedSteeringContextAdapter",
    "SteeringInputContextAdapter",
    "adapt_applied_steering",
    "adapt_steering_inputs",
]
