"""Context observations for history replay after the agent owner has cleaned it.

``LLMAgent.async_messages_transform`` owns history retrieval and replay cleanup,
including incomplete tool-call turn removal and tool-result ordering.  This
module deliberately starts after that owner logic: it observes exactly the
caller-supplied occurrences and never repeats or extends replay semantics.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from aworld.core.context.compiler.adapters import (
    AdapterDiagnostic,
    AdapterDiagnosticSeverity,
    AdapterResult,
    LegacyFinalMessageAdapter,
)


class CleanedHistoryReplayContextAdapter:
    """Observe an owner-finalized replay sequence without changing it."""

    def adapt(
        self,
        occurrences: Sequence[Any],
        *,
        source_identity: str,
        task_epoch: int | None = None,
    ) -> AdapterResult:
        result = LegacyFinalMessageAdapter().adapt(
            occurrences,
            source_identity=source_identity,
            task_epoch=task_epoch,
        )
        boundary = AdapterDiagnostic(
            code="owner_finalized_history_replay",
            message=(
                "Occurrences are observed after owner cleanup in caller-supplied "
                "order; the adapter did not clean, pair, deduplicate, or reorder them."
            ),
            severity=AdapterDiagnosticSeverity.INFO,
            source_identity=source_identity,
        )
        return AdapterResult(
            items=result.items,
            diagnostics=(boundary, *result.diagnostics),
        )


def adapt_cleaned_history_replay(
    occurrences: Sequence[Any],
    *,
    source_identity: str,
    task_epoch: int | None = None,
) -> AdapterResult:
    """Adapt the final replay occurrences supplied by their owner."""

    return CleanedHistoryReplayContextAdapter().adapt(
        occurrences,
        source_identity=source_identity,
        task_epoch=task_epoch,
    )


__all__ = [
    "CleanedHistoryReplayContextAdapter",
    "adapt_cleaned_history_replay",
]
