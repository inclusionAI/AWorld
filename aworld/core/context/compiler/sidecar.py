"""Immutable owner observations kept outside serialized runtime state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, ClassVar

from .adapters import AdapterResult
from .frozen_json import canonical_json_hash
from .models import ContextItemRef
from .attribution import AttributionCollection


class ModelResidency(str, Enum):
    UNKNOWN = "unknown"
    RESIDENT = "resident"
    NOT_RESIDENT = "not_resident"


class ContextEmissionIntent(str, Enum):
    EVIDENCE_ONLY = "evidence_only"
    MESSAGE = "message"


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
    request_id_hash: str | None = None
    collection: AttributionCollection | None = None
    task_epoch: int | None = None
    model_residency: ModelResidency = ModelResidency.UNKNOWN
    emission_intent: ContextEmissionIntent = ContextEmissionIntent.EVIDENCE_ONLY

    def __post_init__(self) -> None:
        for name in ("owner", "namespace", "source_identity"):
            _non_empty(name, getattr(self, name))
        if not re.fullmatch(r"[a-z0-9_.-]+", self.owner):
            raise ValueError("owner must be a stable lowercase identifier")
        if not isinstance(self.result, AdapterResult):
            raise TypeError("result must be an AdapterResult")
        if self.request_id_hash is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", self.request_id_hash):
            raise ValueError("request_id_hash must be canonical or None")
        if self.collection is not None:
            object.__setattr__(self, "collection", AttributionCollection(self.collection))
        if self.task_epoch is not None and (
            isinstance(self.task_epoch, bool) or not isinstance(self.task_epoch, int) or self.task_epoch < 0
        ):
            raise ValueError("task_epoch must be non-negative or None")
        object.__setattr__(self, "model_residency", ModelResidency(self.model_residency))
        object.__setattr__(self, "emission_intent", ContextEmissionIntent(self.emission_intent))
        if (
            self.emission_intent is ContextEmissionIntent.MESSAGE
            and self.model_residency is not ModelResidency.NOT_RESIDENT
        ):
            raise ValueError("message emission requires explicit not_resident evidence")

    @classmethod
    def from_adapter_result(
        cls,
        *,
        owner: str,
        namespace: str,
        source_identity: str,
        result: AdapterResult,
        request_id_hash: str | None = None,
        collection: AttributionCollection | None = None,
        task_epoch: int | None = None,
        model_residency: ModelResidency = ModelResidency.UNKNOWN,
        emission_intent: ContextEmissionIntent = ContextEmissionIntent.EVIDENCE_ONLY,
    ) -> "ContextObservationSidecar":
        return cls(
            owner=owner,
            namespace=namespace,
            source_identity=source_identity,
            result=result,
            request_id_hash=request_id_hash,
            collection=collection,
            task_epoch=task_epoch,
            model_residency=model_residency,
            emission_intent=emission_intent,
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
            "model_residency": self.model_residency.value,
            "emission_intent": self.emission_intent.value,
            "request_binding": (
                {
                    "request_id_hash": self.request_id_hash,
                    "collection": self.collection.value,
                    "task_epoch": self.task_epoch,
                }
                if self.request_id_hash is not None and self.collection is not None
                else None
            ),
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


__all__ = [
    "ContextEmissionIntent",
    "ContextObservationSidecar",
    "ModelResidency",
]
