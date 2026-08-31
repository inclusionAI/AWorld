"""Owner-side Context Compiler adapters for loaded Skill records.

The adapters observe Skill descriptors/content in caller order. They do not
activate Skills, load files, resolve scope, infer permissions, or change the
legacy prompt path.
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
from aworld.skills.models import SkillContent, SkillDescriptor


_UNKNOWN_SKILL_SEMANTICS = (
    "authority",
    "scope",
    "lifetime",
    "priority",
    "required",
    "trust",
    "stability",
    "token_estimate",
    "activation",
)


def _validate_input(
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


def _order_diagnostic(source_identity: str, record_type: str) -> AdapterDiagnostic:
    return AdapterDiagnostic(
        code="owner_skill_occurrence_order",
        message=(
            f"Skill {record_type} occurrences retain caller order and duplicates; "
            "the adapter did not activate, load, sort, or deduplicate Skills."
        ),
        severity=AdapterDiagnosticSeverity.INFO,
        source_identity=source_identity,
    )


def _unknown_diagnostic(
    *, source_identity: str, occurrence: int, task_epoch: int | None
) -> AdapterDiagnostic:
    unknown_fields = list(_UNKNOWN_SKILL_SEMANTICS)
    if task_epoch is None:
        unknown_fields.append("task_epoch")
    return AdapterDiagnostic(
        code="skill_semantics_unknown",
        message=(
            "The loaded Skill record proves its owner payload but not compiler "
            "policy; unknown semantics were retained without inference."
        ),
        severity=AdapterDiagnosticSeverity.INFO,
        source_identity=source_identity,
        occurrence=occurrence,
        unknown_fields=tuple(unknown_fields),
    )


def _item(
    *,
    payload: dict[str, Any],
    source_identity: str,
    record_type: str,
    skill_id: str,
    provider_id: str | None,
    occurrence: int,
    task_epoch: int | None,
) -> ContextItem:
    # priority/required are inert storage sentinels. Their policy meaning stays
    # explicitly unknown in the adjacent diagnostic.
    source_ref: dict[str, Any] = {
        "record_type": record_type,
        "skill_id": skill_id,
        "occurrence": occurrence,
    }
    if provider_id is not None:
        source_ref["provider_id"] = provider_id
    return ContextItem(
        id=f"{source_identity}:skill-{record_type}:{occurrence}",
        kind=ContextKind.SKILL,
        payload=payload,
        task_epoch=task_epoch,
        authority=Authority.UNKNOWN,
        scope=ContextScope.unknown(),
        lifetime=Lifetime.UNKNOWN,
        priority=0,
        required=False,
        trust=Trust.UNKNOWN,
        stability=Stability.UNKNOWN,
        token_limit=None,
        reducer=None,
        source=ContextSource(
            kind=SourceKind.SKILL,
            uri=source_identity,
            ref=source_ref,
        ),
        version=None,
        activation_reason=f"observed_skill_{record_type}_occurrence",
        created_at=None,
        occurrence=occurrence,
    )


class SkillDescriptorContextAdapter:
    """Observe already-listed Skill descriptors without loading their content."""

    def adapt(
        self,
        occurrences: Sequence[SkillDescriptor],
        *,
        source_identity: str = "skills://registry/descriptors",
        task_epoch: int | None = None,
    ) -> AdapterResult:
        _validate_input(occurrences, source_identity, task_epoch)
        items: list[ContextItem] = []
        diagnostics = [_order_diagnostic(source_identity, "descriptor")]
        for occurrence, descriptor in enumerate(occurrences):
            if not isinstance(descriptor, SkillDescriptor):
                raise TypeError("descriptor occurrences must contain SkillDescriptor values")
            payload = {
                "record_type": "descriptor",
                "skill_id": descriptor.skill_id,
                "provider_id": descriptor.provider_id,
                "skill_name": descriptor.skill_name,
                "display_name": descriptor.display_name,
                "description": descriptor.description,
                "source_type": descriptor.source_type,
                "scope": descriptor.scope,
                "visibility": descriptor.visibility,
                "asset_root": descriptor.asset_root,
                "skill_file": descriptor.skill_file,
                "metadata": dict(descriptor.metadata),
                "execution_assets": dict(descriptor.execution_assets),
                "requirements": dict(descriptor.requirements),
            }
            items.append(
                _item(
                    payload=payload,
                    source_identity=source_identity,
                    record_type="descriptor",
                    skill_id=descriptor.skill_id,
                    provider_id=descriptor.provider_id,
                    occurrence=occurrence,
                    task_epoch=task_epoch,
                )
            )
            diagnostics.append(
                _unknown_diagnostic(
                    source_identity=source_identity,
                    occurrence=occurrence,
                    task_epoch=task_epoch,
                )
            )
        return AdapterResult(items=tuple(items), diagnostics=tuple(diagnostics))


class SkillContentContextAdapter:
    """Observe content that its owner has already loaded for a Skill."""

    def adapt(
        self,
        occurrences: Sequence[SkillContent],
        *,
        source_identity: str = "skills://registry/content",
        task_epoch: int | None = None,
    ) -> AdapterResult:
        _validate_input(occurrences, source_identity, task_epoch)
        items: list[ContextItem] = []
        diagnostics = [_order_diagnostic(source_identity, "content")]
        for occurrence, content in enumerate(occurrences):
            if not isinstance(content, SkillContent):
                raise TypeError("content occurrences must contain SkillContent values")
            payload = {
                "record_type": "content",
                "skill_id": content.skill_id,
                "usage": content.usage,
                "tool_list": dict(content.tool_list),
                "raw_frontmatter": dict(content.raw_frontmatter),
                "execution_assets": dict(content.execution_assets),
            }
            items.append(
                _item(
                    payload=payload,
                    source_identity=source_identity,
                    record_type="content",
                    skill_id=content.skill_id,
                    provider_id=None,
                    occurrence=occurrence,
                    task_epoch=task_epoch,
                )
            )
            diagnostics.append(
                _unknown_diagnostic(
                    source_identity=source_identity,
                    occurrence=occurrence,
                    task_epoch=task_epoch,
                )
            )
        return AdapterResult(items=tuple(items), diagnostics=tuple(diagnostics))


def adapt_skill_descriptors(
    descriptors: Sequence[SkillDescriptor],
    *,
    source_identity: str = "skills://registry/descriptors",
    task_epoch: int | None = None,
) -> AdapterResult:
    return SkillDescriptorContextAdapter().adapt(
        descriptors,
        source_identity=source_identity,
        task_epoch=task_epoch,
    )


def adapt_skill_contents(
    contents: Sequence[SkillContent],
    *,
    source_identity: str = "skills://registry/content",
    task_epoch: int | None = None,
) -> AdapterResult:
    return SkillContentContextAdapter().adapt(
        contents,
        source_identity=source_identity,
        task_epoch=task_epoch,
    )


__all__ = [
    "SkillContentContextAdapter",
    "SkillDescriptorContextAdapter",
    "adapt_skill_contents",
    "adapt_skill_descriptors",
]
