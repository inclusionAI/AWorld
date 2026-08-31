"""Immutable owner observations kept outside serialized runtime state."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, ClassVar

from .adapters import AdapterResult
from .frozen_json import canonical_json_hash
from .models import ContextItemRef


def _non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ContextObservationSidecar:
    """One latest immutable owner observation for a runtime namespace.

    Raw owner payloads remain available in memory through ``result``. Default
    serialization exposes the public code-level owner identifier plus hashes,
    redacted item refs, and diagnostic metadata. It never exposes owner
    payloads, diagnostic messages/sources/codes, or unknown field names.
    """

    SCHEMA_VERSION: ClassVar[str] = "aworld.context.sidecar.v2"

    owner: str
    namespace: str
    source_identity: str
    result: AdapterResult

    def __post_init__(self) -> None:
        for name in ("owner", "namespace", "source_identity"):
            _non_empty(name, getattr(self, name))
        if not re.fullmatch(r"[a-z0-9_.-]+", self.owner):
            raise ValueError("owner must be a stable lowercase identifier")
        if not isinstance(self.result, AdapterResult):
            raise TypeError("result must be an AdapterResult")

    @classmethod
    def from_adapter_result(
        cls,
        *,
        owner: str,
        namespace: str,
        source_identity: str,
        result: AdapterResult,
    ) -> "ContextObservationSidecar":
        return cls(
            owner=owner,
            namespace=namespace,
            source_identity=source_identity,
            result=result,
        )

    @staticmethod
    def _redacted_ref(item_ref: ContextItemRef) -> dict[str, Any]:
        payload = item_ref.to_dict()
        payload["item_id"] = (
            f"item:{canonical_json_hash({'item_id': item_ref.item_id})}"
        )
        return payload

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "owner": self.owner,
            "items": [
                self._redacted_ref(item.to_ref()) for item in self.result.items
            ],
            "diagnostics": [
                {
                    "code_hash": canonical_json_hash(
                        {"code": diagnostic.code}
                    ),
                    "severity": diagnostic.severity.value,
                    "occurrence": diagnostic.occurrence,
                    "unknown_field_count": len(diagnostic.unknown_fields),
                }
                for diagnostic in self.result.diagnostics
            ],
        }


__all__ = ["ContextObservationSidecar"]
