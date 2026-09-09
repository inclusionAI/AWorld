"""Context observations for the tool catalog finalized by its agent owner.

The caller supplies the exact schema occurrences that will be exposed to the
model after existing owner-side filtering and lowering.  This adapter only
captures that boundary: it does not filter, minimize, authorize, deduplicate,
reorder, or infer compiler metadata from schema names and descriptions.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from aworld.core.context.compiler.adapters import (
    AdapterDiagnostic,
    AdapterDiagnosticSeverity,
    AdapterResult,
    LegacyToolSchemaAdapter,
)


_OWNER_UNKNOWN_FIELDS = (
    "authority",
    "scope",
    "lifetime",
    "priority",
    "required",
    "permissions",
    "trust",
    "stability",
    "token_estimate",
    "task_epoch",
)


class FinalToolCatalogContextAdapter:
    """Observe final owner-supplied tool schemas without changing semantics."""

    def adapt(
        self,
        occurrences: Sequence[Any],
        *,
        source_identity: str,
    ) -> AdapterResult:
        # task_epoch deliberately remains unknown here.  The final schema list
        # owner proves payload, order, occurrence, and source identity only.
        result = LegacyToolSchemaAdapter().adapt(
            occurrences,
            source_identity=source_identity,
            task_epoch=None,
        )
        occurrence_diagnostics = tuple(
            AdapterDiagnostic(
                code=diagnostic.code,
                message=diagnostic.message,
                severity=diagnostic.severity,
                source_identity=diagnostic.source_identity,
                occurrence=diagnostic.occurrence,
                unknown_fields=(*diagnostic.unknown_fields, "permissions"),
            )
            for diagnostic in result.diagnostics
        )
        boundary = AdapterDiagnostic(
            code="owner_finalized_tool_catalog",
            message=(
                "Occurrences are observed at the owner-finalized exposure boundary "
                "in caller-supplied order; the adapter did not filter, minimize, "
                "authorize, deduplicate, or reorder them."
            ),
            severity=AdapterDiagnosticSeverity.INFO,
            source_identity=source_identity,
            unknown_fields=_OWNER_UNKNOWN_FIELDS,
        )
        return AdapterResult(
            items=result.items,
            diagnostics=(boundary, *occurrence_diagnostics),
        )


def adapt_final_tool_catalog(
    occurrences: Sequence[Any],
    *,
    source_identity: str,
) -> AdapterResult:
    """Adapt the exact final tool-schema occurrences supplied by their owner."""

    return FinalToolCatalogContextAdapter().adapt(
        occurrences,
        source_identity=source_identity,
    )


__all__ = [
    "FinalToolCatalogContextAdapter",
    "adapt_final_tool_catalog",
]
