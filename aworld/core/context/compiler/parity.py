"""Privacy-safe semantic parity evidence across AWorld entry points."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from .final import FinalCompileResult
from .frozen_json import FrozenMap, canonical_json_hash, freeze_json, thaw_json


class ContextEntryPoint(str, Enum):
    UNKNOWN = "unknown"
    DIRECT = "direct"
    AGENT = "agent"
    AMNI = "amni"
    CLI = "cli"
    ACP = "acp"
    RESUME = "resume"
    CHILD = "child"


def _semantic_projection(result: FinalCompileResult) -> FrozenMap:
    if not isinstance(result, FinalCompileResult):
        raise TypeError("result must be FinalCompileResult")
    selected_by_id = {item.id: item for item in result.selected_items}
    projection = {
        "compiler_identity": result.compiler_identity,
        "compiler_version": result.compiler_version,
        "policy_version": result.policy_version,
        "request_content_hash": result.request_snapshot.content_hash,
        "provider_name": result.request_snapshot.provider_name,
        "token_accounting": result.token_accounting.to_dict(),
        "stable_prefix_hash": result.stable_partition.stable_prefix_hash,
        "dynamic_context_hash": result.stable_partition.dynamic_context_hash,
        "tool_catalog_hash": result.tool_catalog_hash,
        "skill_set_hash": result.skill_set_hash,
        "enforce_ready": result.enforce_ready,
        "blocker_codes": list(result.blocker_codes),
        "decisions": [
            {
                "ordinal": ordinal,
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
            for ordinal, decision in enumerate(result.decisions)
        ],
        "selected": [
            {
                "ordinal": ordinal,
                "kind": item.kind.value,
                "source_kind": item.source.kind.value,
                "authority": item.authority.value,
                "scope_kinds": [kind.value for kind in item.scope.kinds],
                "trust": item.trust.value,
                "stability": item.stability.value,
                "lifetime": item.lifetime.value,
                "content_hash": item.content_hash,
                "decision_content_hash": next(
                    (
                        decision.content_hash
                        for decision in result.decisions
                        if decision.item_id == item.id
                    ),
                    None,
                ),
            }
            for ordinal, item in enumerate(result.selected_items)
            if item.id in selected_by_id
        ],
    }
    frozen = freeze_json(projection)
    if not isinstance(frozen, FrozenMap):
        raise TypeError("semantic projection must be an object")
    return frozen


@dataclass(frozen=True, slots=True)
class ContextEntrypointParityReceipt:
    entry_point: ContextEntryPoint
    semantic_projection: FrozenMap
    semantic_fingerprint: str | None = None

    SCHEMA_VERSION = "aworld.context.entrypoint-parity.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_point", ContextEntryPoint(self.entry_point))
        projection = freeze_json(self.semantic_projection)
        if not isinstance(projection, FrozenMap):
            raise TypeError("semantic_projection must be an object")
        object.__setattr__(self, "semantic_projection", projection)
        expected = canonical_json_hash(projection)
        if self.semantic_fingerprint is not None and self.semantic_fingerprint != expected:
            raise ValueError("entrypoint parity fingerprint mismatch")
        object.__setattr__(self, "semantic_fingerprint", expected)

    @classmethod
    def from_final_result(
        cls, *, entry_point: ContextEntryPoint | str, result: FinalCompileResult
    ) -> "ContextEntrypointParityReceipt":
        return cls(
            entry_point=ContextEntryPoint(entry_point),
            semantic_projection=_semantic_projection(result),
        )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ContextEntrypointParityReceipt":
        if not isinstance(value, dict) or value.get("schema_version") != cls.SCHEMA_VERSION:
            raise ValueError("unsupported entrypoint parity receipt")
        return cls(
            entry_point=value["entry_point"],
            semantic_projection=value["semantic_projection"],
            semantic_fingerprint=value.get("semantic_fingerprint"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "entry_point": self.entry_point.value,
            "semantic_projection": thaw_json(self.semantic_projection),
            "semantic_fingerprint": self.semantic_fingerprint,
        }


def assess_entrypoint_parity(
    receipts: Iterable[ContextEntrypointParityReceipt],
    *,
    required_entry_points: Iterable[ContextEntryPoint | str],
) -> dict[str, Any]:
    values = tuple(receipts)
    required = tuple(ContextEntryPoint(value) for value in required_entry_points)
    if len(set(required)) != len(required):
        raise ValueError("required entry points must be unique")
    if not all(isinstance(value, ContextEntrypointParityReceipt) for value in values):
        raise TypeError("receipts must contain ContextEntrypointParityReceipt values")
    grouped: dict[ContextEntryPoint, list[ContextEntrypointParityReceipt]] = {}
    for value in values:
        grouped.setdefault(value.entry_point, []).append(value)
    missing = [entry.value for entry in required if entry not in grouped]
    duplicates = [entry.value for entry in required if len(grouped.get(entry, ())) > 1]
    if missing or duplicates:
        return {
            "status": "unavailable",
            "reason_code": "entrypoint_evidence_incomplete",
            "missing": missing,
            "duplicates": duplicates,
        }
    fingerprints = {
        grouped[entry][0].semantic_fingerprint for entry in required
    }
    return {
        "status": "available" if len(fingerprints) == 1 else "mismatch",
        "reason_code": None if len(fingerprints) == 1 else "entrypoint_semantics_mismatch",
        "entry_points": [entry.value for entry in required],
        "semantic_fingerprint": next(iter(fingerprints)) if len(fingerprints) == 1 else None,
        "fingerprints": {
            entry.value: grouped[entry][0].semantic_fingerprint for entry in required
        },
    }


__all__ = [
    "ContextEntryPoint",
    "ContextEntrypointParityReceipt",
    "assess_entrypoint_parity",
]
