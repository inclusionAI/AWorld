"""Pure owner-side observations of Amni neuron outputs before prompt folding.

The adapter never executes a neuron and never accepts a folded system message.
Callers provide the exact executed-output occurrences in their original order;
the compiler contracts receive a detached sidecar without affecting the legacy
prompt assembly path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
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

from . import Neuron


@dataclass(frozen=True, slots=True)
class NeuronOutputOccurrence:
    """One owner-observed neuron output plus explicitly proved semantics.

    ``None`` means the owner did not prove a field. Enum ``UNKNOWN`` values are
    also retained as unknown. The output must be JSON-compatible because core
    ContextItems deliberately reject implicit stringification of runtime
    objects.
    """

    neuron: Neuron
    output: Any
    kind: ContextKind | None = None
    authority: Authority | None = None
    scope: ContextScope | None = None
    lifetime: Lifetime | None = None
    trust: Trust | None = None
    stability: Stability | None = None
    priority: int | None = None
    required: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.neuron, Neuron):
            raise TypeError("neuron must be a Neuron instance")
        for field_name, enum_type in (
            ("kind", ContextKind),
            ("authority", Authority),
            ("lifetime", Lifetime),
            ("trust", Trust),
            ("stability", Stability),
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, enum_type(value))
        if self.scope is not None and not isinstance(self.scope, ContextScope):
            raise TypeError("scope must be a ContextScope or None")
        if self.priority is not None and (
            isinstance(self.priority, bool) or not isinstance(self.priority, int)
        ):
            raise ValueError("priority must be an integer or None")
        if self.required is not None and not isinstance(self.required, bool):
            raise TypeError("required must be a boolean or None")


def _non_empty_owner_name(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _neuron_identity(neuron: Neuron) -> tuple[str | None, str | None, str, str]:
    instance_name = _non_empty_owner_name(getattr(neuron, "name", None))
    registered_name = _non_empty_owner_name(
        getattr(type(neuron), "REGISTERED_NAME", None)
    )
    neuron_type = type(neuron)
    return (
        instance_name or registered_name,
        registered_name,
        neuron_type.__module__,
        neuron_type.__qualname__,
    )


def _validate_adapter_input(
    occurrences: Sequence[NeuronOutputOccurrence],
    source_identity: str,
    task_epoch: int | None,
) -> None:
    if isinstance(occurrences, (str, bytes, bytearray)) or not isinstance(
        occurrences, Sequence
    ):
        raise TypeError("occurrences must be a sequence of NeuronOutputOccurrence values")
    if not isinstance(source_identity, str) or not source_identity.strip():
        raise ValueError("source_identity must be a non-empty string")
    if task_epoch is not None and (
        isinstance(task_epoch, bool)
        or not isinstance(task_epoch, int)
        or task_epoch < 0
    ):
        raise ValueError("task_epoch must be a non-negative integer or None")


class NeuronContextAdapter:
    """Adapt exact pre-fold neuron output occurrences without inference."""

    def adapt(
        self,
        occurrences: Sequence[NeuronOutputOccurrence],
        *,
        source_identity: str = "amni-neuron-outputs",
        task_epoch: int | None = None,
    ) -> AdapterResult:
        _validate_adapter_input(occurrences, source_identity, task_epoch)
        items: list[ContextItem] = []
        diagnostics: list[AdapterDiagnostic] = []

        for occurrence_index, occurrence in enumerate(occurrences):
            if not isinstance(occurrence, NeuronOutputOccurrence):
                raise TypeError(
                    "occurrences must contain NeuronOutputOccurrence values"
                )
            neuron_name, registered_name, module, qualname = _neuron_identity(
                occurrence.neuron
            )
            kind = occurrence.kind or ContextKind.UNKNOWN
            authority = occurrence.authority or Authority.UNKNOWN
            scope = occurrence.scope or ContextScope.unknown()
            lifetime = occurrence.lifetime or Lifetime.UNKNOWN
            trust = occurrence.trust or Trust.UNKNOWN
            stability = occurrence.stability or Stability.UNKNOWN
            priority = occurrence.priority if occurrence.priority is not None else 0
            required = occurrence.required if occurrence.required is not None else False
            payload = {
                "neuron": {
                    "name": neuron_name,
                    "registered_name": registered_name,
                    "type": {"module": module, "qualname": qualname},
                },
                "output": occurrence.output,
            }
            item = ContextItem(
                id=f"{source_identity}:neuron-output:{occurrence_index}",
                kind=kind,
                payload=payload,
                task_epoch=task_epoch,
                authority=authority,
                scope=scope,
                lifetime=lifetime,
                priority=priority,
                required=required,
                trust=trust,
                stability=stability,
                token_limit=None,
                reducer=None,
                source=ContextSource(
                    kind=SourceKind.NEURON,
                    uri=source_identity,
                    ref={
                        "occurrence": occurrence_index,
                        "neuron_name": neuron_name,
                        "registered_name": registered_name,
                        "neuron_type": {"module": module, "qualname": qualname},
                    },
                ),
                version=None,
                activation_reason="observed_amni_neuron_output_occurrence",
                created_at=None,
                occurrence=occurrence_index,
            )
            items.append(item)

            unknown_fields = [
                "token_estimate",
                "token_limit",
                "reducer",
                "version",
                "created_at",
            ]
            if kind is ContextKind.UNKNOWN:
                unknown_fields.insert(0, "kind")
            if authority is Authority.UNKNOWN:
                unknown_fields.insert(0, "authority")
            if scope.kinds == (ScopeKind.UNKNOWN,):
                unknown_fields.insert(0, "scope")
            if lifetime is Lifetime.UNKNOWN:
                unknown_fields.insert(0, "lifetime")
            if occurrence.priority is None:
                unknown_fields.insert(0, "priority")
            if occurrence.required is None:
                unknown_fields.insert(0, "required")
            if trust is Trust.UNKNOWN:
                unknown_fields.insert(0, "trust")
            if stability is Stability.UNKNOWN:
                unknown_fields.insert(0, "stability")
            if task_epoch is None:
                unknown_fields.append("task_epoch")
            if neuron_name is None:
                unknown_fields.append("neuron_name")
            diagnostics.append(
                AdapterDiagnostic(
                    code="neuron_semantics_unknown",
                    message=(
                        "Only explicit owner fields prove neuron compiler semantics; "
                        "UNKNOWN/None sentinels were retained without inspecting output "
                        "text, role, or a folded system message."
                    ),
                    severity=AdapterDiagnosticSeverity.INFO,
                    source_identity=source_identity,
                    occurrence=occurrence_index,
                    unknown_fields=tuple(unknown_fields),
                )
            )

        return AdapterResult(items=tuple(items), diagnostics=tuple(diagnostics))


def adapt_neuron_outputs(
    occurrences: Sequence[NeuronOutputOccurrence],
    *,
    source_identity: str = "amni-neuron-outputs",
    task_epoch: int | None = None,
) -> AdapterResult:
    return NeuronContextAdapter().adapt(
        occurrences,
        source_identity=source_identity,
        task_epoch=task_epoch,
    )


__all__ = [
    "NeuronContextAdapter",
    "NeuronOutputOccurrence",
    "adapt_neuron_outputs",
]
