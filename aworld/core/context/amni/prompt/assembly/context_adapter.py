"""Owner-side adapter from Amni ``PromptSection`` to compiler observations.

The adapter consumes one caller-ordered sequence.  It intentionally does not
accept stable/dynamic groups or a PromptAssemblyPlan because their original
cross-group occurrence order cannot be reconstructed.
"""

from __future__ import annotations

from collections.abc import Sequence
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
    SourceKind,
    Stability,
    Trust,
)

from .plan import PromptSection


_SECTION_KIND = {
    "system": ContextKind.SYSTEM,
    "user": ContextKind.USER,
    "instruction": ContextKind.INSTRUCTION,
    "skill": ContextKind.SKILL,
    "memory": ContextKind.MEMORY,
    "tool_catalog": ContextKind.TOOL_CATALOG,
    "tool_result": ContextKind.TOOL_RESULT,
    "steering": ContextKind.STEERING,
    "delegation": ContextKind.DELEGATION,
}

_SECTION_STABILITY = {
    "stable": Stability.STABLE,
    "session_stable": Stability.SESSION_STABLE,
    "dynamic": Stability.TURN_DYNAMIC,
    "turn_dynamic": Stability.TURN_DYNAMIC,
}

_BASE_UNKNOWN_FIELDS = (
    "authority",
    "scope",
    "lifetime",
    "priority",
    "required",
    "trust",
    "token_estimate",
)


class PromptSectionContextAdapter:
    """Adapt only explicit PromptSection fields in caller-supplied order."""

    def adapt(
        self,
        occurrences: Sequence[PromptSection],
        *,
        source_identity: str = "amni-prompt-sections",
        task_epoch: int | None = None,
    ) -> AdapterResult:
        if isinstance(occurrences, (str, bytes, bytearray)) or not isinstance(
            occurrences, Sequence
        ):
            raise TypeError("occurrences must be a sequence of PromptSection values")
        if not isinstance(source_identity, str) or not source_identity.strip():
            raise ValueError("source_identity must be a non-empty string")
        if task_epoch is not None and (
            isinstance(task_epoch, bool)
            or not isinstance(task_epoch, int)
            or task_epoch < 0
        ):
            raise ValueError("task_epoch must be a non-negative integer or None")

        items: list[ContextItem] = []
        diagnostics: list[AdapterDiagnostic] = [
            AdapterDiagnostic(
                code="caller_supplied_prompt_section_order",
                message=(
                    "PromptSection occurrence order is exactly the supplied sequence; "
                    "stable/dynamic group order is not reconstructed."
                ),
                severity=AdapterDiagnosticSeverity.INFO,
                source_identity=source_identity,
            )
        ]

        for occurrence, section in enumerate(occurrences):
            if not isinstance(section, PromptSection):
                raise TypeError("occurrences must contain PromptSection values")
            normalized_kind = (
                section.kind.strip().lower() if isinstance(section.kind, str) else ""
            )
            normalized_stability = (
                section.stability.strip().lower()
                if isinstance(section.stability, str)
                else ""
            )
            kind = _SECTION_KIND.get(normalized_kind, ContextKind.UNKNOWN)
            stability = _SECTION_STABILITY.get(normalized_stability, Stability.UNKNOWN)
            payload: dict[str, Any] = {
                "name": section.name,
                "kind": section.kind,
                "stability": section.stability,
                "content": section.content,
                "hash": section.hash,
            }
            item = ContextItem(
                id=f"{source_identity}:prompt-section:{occurrence}",
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
                    kind=SourceKind.PROMPT_SECTION,
                    uri=source_identity,
                    ref={"occurrence": occurrence, "name": section.name},
                ),
                version=None,
                activation_reason="observed_prompt_section_occurrence",
                created_at=None,
                occurrence=occurrence,
            )
            items.append(item)

            unknown_fields = list(_BASE_UNKNOWN_FIELDS)
            if task_epoch is None:
                unknown_fields.append("task_epoch")
            if kind is ContextKind.UNKNOWN:
                unknown_fields.append("kind")
            if stability is Stability.UNKNOWN:
                unknown_fields.append("stability")
            diagnostics.append(
                AdapterDiagnostic(
                    code="prompt_section_semantics_unknown",
                    message=(
                        "PromptSection fields do not prove these compiler semantics; "
                        "UNKNOWN/None sentinels were retained without inference."
                    ),
                    severity=AdapterDiagnosticSeverity.INFO,
                    source_identity=source_identity,
                    occurrence=occurrence,
                    unknown_fields=tuple(unknown_fields),
                )
            )
            if section.hash is not None:
                diagnostics.append(
                    AdapterDiagnostic(
                        code="prompt_section_hash_is_source_metadata",
                        message=(
                            "PromptSection.hash is retained as source payload metadata; "
                            "ContextItem.content_hash is computed from the canonical payload."
                        ),
                        severity=AdapterDiagnosticSeverity.INFO,
                        source_identity=source_identity,
                        occurrence=occurrence,
                    )
                )

        return AdapterResult(items=tuple(items), diagnostics=tuple(diagnostics))


def adapt_prompt_sections(
    sections: Sequence[PromptSection],
    *,
    source_identity: str = "amni-prompt-sections",
    task_epoch: int | None = None,
) -> AdapterResult:
    return PromptSectionContextAdapter().adapt(
        sections,
        source_identity=source_identity,
        task_epoch=task_epoch,
    )


__all__ = ["PromptSectionContextAdapter", "adapt_prompt_sections"]
