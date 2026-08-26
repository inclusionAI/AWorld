"""Provider-neutral boundary for optional benchmark harness integrations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from aworld.cloud.models import (
    BenchmarkMetadata,
    BenchmarkOutcome,
    Run,
    RunFile,
    Workspace,
)


@dataclass(frozen=True)
class BenchmarkPreparation:
    """Adapter-owned executor inputs derived from benchmark metadata."""

    task: str
    environment: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task.strip():
            raise ValueError("benchmark preparation task must not be empty")
        object.__setattr__(
            self, "environment", MappingProxyType(dict(self.environment))
        )


@runtime_checkable
class BenchmarkAdapter(Protocol):
    """Optional harness seam; Cloud core never imports a concrete adapter."""

    async def prepare(
        self,
        run: Run,
        workspace: Workspace,
        metadata: BenchmarkMetadata,
    ) -> BenchmarkPreparation: ...

    async def verify(
        self,
        run: Run,
        metadata: BenchmarkMetadata,
        files: tuple[RunFile, ...],
    ) -> BenchmarkOutcome: ...
