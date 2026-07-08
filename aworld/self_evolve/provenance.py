from __future__ import annotations

from dataclasses import dataclass

from aworld.self_evolve.types import SelfEvolveTargetRef


@dataclass(frozen=True)
class TargetProvenance:
    target: SelfEvolveTargetRef
    source_kind: str
    write_origin: str
    trust_level: str
    protected: bool
    reason: str
