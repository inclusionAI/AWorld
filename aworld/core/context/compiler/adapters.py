"""Pure occurrence adapters for observing already-finalized legacy request inputs.

These adapters do not resolve, filter, deduplicate, tokenize, or otherwise alter
the legacy request.  They create immutable ``ContextItem`` observations in the
exact order supplied by the caller and report every provenance dimension that
the legacy payload cannot prove.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from .models import (
    Authority,
    ContextItem,
    ContextKind,
    ContextScope,
    ContextSource,
    Lifetime,
    SourceKind,
    Stability,
    Trust,
)


class AdapterDiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class AdapterDiagnostic:
    code: str
    message: str
    severity: AdapterDiagnosticSeverity
    source_identity: str
    occurrence: int | None = None
    unknown_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("diagnostic code must be a non-empty string")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("diagnostic message must be a non-empty string")
        object.__setattr__(self, "severity", AdapterDiagnosticSeverity(self.severity))
        if not isinstance(self.source_identity, str) or not self.source_identity.strip():
            raise ValueError("diagnostic source_identity must be a non-empty string")
        if self.occurrence is not None and (
            isinstance(self.occurrence, bool)
            or not isinstance(self.occurrence, int)
            or self.occurrence < 0
        ):
            raise ValueError("diagnostic occurrence must be a non-negative integer or None")
        object.__setattr__(self, "unknown_fields", tuple(self.unknown_fields))
        if any(not isinstance(field, str) or not field for field in self.unknown_fields):
            raise ValueError("diagnostic unknown_fields must contain non-empty strings")


@dataclass(frozen=True, slots=True)
class AdapterResult:
    items: tuple[ContextItem, ...]
    diagnostics: tuple[AdapterDiagnostic, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if not all(isinstance(item, ContextItem) for item in self.items):
            raise TypeError("adapter result items must contain ContextItem values")
        if not all(isinstance(item, AdapterDiagnostic) for item in self.diagnostics):
            raise TypeError("adapter result diagnostics must contain AdapterDiagnostic values")


@runtime_checkable
class OccurrenceContextAdapter(Protocol):
    """Common side-effect-free contract for owner-specific occurrence adapters."""

    def adapt(
        self,
        occurrences: Sequence[Any],
        *,
        source_identity: str,
        task_epoch: int | None = None,
    ) -> AdapterResult:
        ...


_MESSAGE_ROLE_KIND = {
    "system": ContextKind.SYSTEM,
    "developer": ContextKind.INSTRUCTION,
    "user": ContextKind.USER,
    "tool": ContextKind.TOOL_RESULT,
    "function": ContextKind.TOOL_RESULT,
}

_BASE_UNKNOWN_FIELDS = (
    "authority",
    "scope",
    "lifetime",
    "priority",
    "required",
    "trust",
    "stability",
    "token_estimate",
)


def _validate_adapt_input(
    occurrences: Sequence[Any], source_identity: str, task_epoch: int | None
) -> None:
    if isinstance(occurrences, (str, bytes, bytearray)) or not isinstance(
        occurrences, Sequence
    ):
        raise TypeError("occurrences must be a sequence")
    if not isinstance(source_identity, str) or not source_identity.strip():
        raise ValueError("source_identity must be a non-empty string")
    if task_epoch is not None and (
        isinstance(task_epoch, bool)
        or not isinstance(task_epoch, int)
        or task_epoch < 0
    ):
        raise ValueError("task_epoch must be a non-negative integer or None")


def _unknown_diagnostic(
    *,
    source_identity: str,
    occurrence: int,
    unknown_fields: tuple[str, ...],
) -> AdapterDiagnostic:
    return AdapterDiagnostic(
        code="legacy_semantics_unknown",
        message=(
            "Legacy occurrence does not prove these compiler semantics; UNKNOWN/None "
            "sentinels were retained without inference."
        ),
        severity=AdapterDiagnosticSeverity.INFO,
        source_identity=source_identity,
        occurrence=occurrence,
        unknown_fields=unknown_fields,
    )


def _context_item(
    *,
    item_id: str,
    kind: ContextKind,
    payload: Any,
    task_epoch: int | None,
    source_kind: SourceKind,
    source_identity: str,
    adapter_name: str,
    occurrence: int,
    stability: Stability = Stability.UNKNOWN,
) -> ContextItem:
    # priority=0 and required=False are inert storage sentinels. Their semantics
    # remain explicitly unknown in AdapterDiagnostic and no decision is emitted.
    return ContextItem(
        id=item_id,
        kind=kind,
        payload=payload,
        task_epoch=task_epoch,
        authority=Authority.UNKNOWN,
        scope=ContextScope.unknown(),
        lifetime=Lifetime.UNKNOWN,
        priority=0,
        required=False,
        trust=Trust.UNKNOWN,
        stability=stability,
        token_limit=None,
        reducer=None,
        source=ContextSource(
            kind=source_kind,
            uri=source_identity,
            ref={"adapter": adapter_name, "occurrence": occurrence},
        ),
        version=None,
        activation_reason=f"observed_{adapter_name}_occurrence",
        created_at=None,
        occurrence=occurrence,
    )


class LegacyFinalMessageAdapter:
    """Observe the final legacy message list after cleanup and transform hooks."""

    def adapt(
        self,
        occurrences: Sequence[Any],
        *,
        source_identity: str = "legacy-final-messages",
        task_epoch: int | None = None,
    ) -> AdapterResult:
        _validate_adapt_input(occurrences, source_identity, task_epoch)
        items: list[ContextItem] = []
        diagnostics: list[AdapterDiagnostic] = []
        for occurrence, payload in enumerate(occurrences):
            role = payload.get("role") if isinstance(payload, Mapping) else None
            normalized_role = role.strip().lower() if isinstance(role, str) else None
            kind = _MESSAGE_ROLE_KIND.get(normalized_role, ContextKind.UNKNOWN)
            unknown_fields = list(_BASE_UNKNOWN_FIELDS)
            if task_epoch is None:
                unknown_fields.append("task_epoch")
            if kind is ContextKind.UNKNOWN:
                unknown_fields.append("kind")
            items.append(
                _context_item(
                    item_id=f"{source_identity}:legacy-message:{occurrence}",
                    kind=kind,
                    payload=payload,
                    task_epoch=task_epoch,
                    source_kind=SourceKind.LEGACY_MESSAGE,
                    source_identity=source_identity,
                    adapter_name="legacy_final_message",
                    occurrence=occurrence,
                )
            )
            diagnostics.append(
                _unknown_diagnostic(
                    source_identity=source_identity,
                    occurrence=occurrence,
                    unknown_fields=tuple(unknown_fields),
                )
            )
        return AdapterResult(items=tuple(items), diagnostics=tuple(diagnostics))


class LegacyToolSchemaAdapter:
    """Observe final tool-schema occurrences without sorting or deduplication."""

    def adapt(
        self,
        occurrences: Sequence[Any],
        *,
        source_identity: str = "legacy-tool-schemas",
        task_epoch: int | None = None,
    ) -> AdapterResult:
        _validate_adapt_input(occurrences, source_identity, task_epoch)
        items: list[ContextItem] = []
        diagnostics: list[AdapterDiagnostic] = []
        for occurrence, payload in enumerate(occurrences):
            unknown_fields = list(_BASE_UNKNOWN_FIELDS)
            if task_epoch is None:
                unknown_fields.append("task_epoch")
            items.append(
                _context_item(
                    item_id=f"{source_identity}:tool-schema:{occurrence}",
                    kind=ContextKind.TOOL_CATALOG,
                    payload=payload,
                    task_epoch=task_epoch,
                    source_kind=SourceKind.TOOL_CATALOG,
                    source_identity=source_identity,
                    adapter_name="legacy_tool_schema",
                    occurrence=occurrence,
                )
            )
            diagnostics.append(
                _unknown_diagnostic(
                    source_identity=source_identity,
                    occurrence=occurrence,
                    unknown_fields=tuple(unknown_fields),
                )
            )
        return AdapterResult(items=tuple(items), diagnostics=tuple(diagnostics))


def adapt_final_messages(
    messages: Sequence[Any],
    *,
    source_identity: str = "legacy-final-messages",
    task_epoch: int | None = None,
) -> AdapterResult:
    return LegacyFinalMessageAdapter().adapt(
        messages,
        source_identity=source_identity,
        task_epoch=task_epoch,
    )


def adapt_tool_schemas(
    tools: Sequence[Any],
    *,
    source_identity: str = "legacy-tool-schemas",
    task_epoch: int | None = None,
) -> AdapterResult:
    return LegacyToolSchemaAdapter().adapt(
        tools,
        source_identity=source_identity,
        task_epoch=task_epoch,
    )


__all__ = [
    "AdapterDiagnostic",
    "AdapterDiagnosticSeverity",
    "AdapterResult",
    "LegacyFinalMessageAdapter",
    "LegacyToolSchemaAdapter",
    "OccurrenceContextAdapter",
    "adapt_final_messages",
    "adapt_tool_schemas",
]
