"""The single owner of conversation history and request preparation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4


@dataclass(frozen=True)
class ContextEntry:
    run_id: str
    kind: str
    data: object


class ContextStorage(Protocol):
    def append(self, context_id: str, entry: ContextEntry) -> None: ...
    def read(self, context_id: str) -> tuple[ContextEntry, ...]: ...


class ContextPolicy(Protocol):
    id: str
    version: str
    async def prepare(self, history: tuple[ContextEntry, ...]) -> tuple[ContextEntry, ...]: ...


@dataclass(frozen=True)
class FullHistoryPolicy:
    id: str = "full-history"
    version: str = "1"

    async def prepare(self, history: tuple[ContextEntry, ...]) -> tuple[ContextEntry, ...]:
        return history


class Context:
    """Own history once; storage and transformations are implementation options.

    Session claims one context. Executors receive a fenced RunContext view,
    so retained references cannot write after a run stops.
    """

    def __init__(
        self, *, policy: ContextPolicy | None = None, storage: ContextStorage | None = None,
    ) -> None:
        from .storage import InMemoryContextStorage

        self._id = uuid4().hex
        self._policy = FullHistoryPolicy() if policy is None else policy
        if not callable(getattr(self._policy, "prepare", None)):
            raise TypeError("policy must implement async prepare()")
        for identifier in (self._policy.id, self._policy.version):
            if not isinstance(identifier, str) or not identifier.strip():
                raise ValueError("Context policy id and version must be non-empty strings")
        self._policy_id, self._policy_version = self._policy.id, self._policy.version
        self._storage = InMemoryContextStorage() if storage is None else storage
        if not all(callable(getattr(self._storage, method, None)) for method in ("append", "read")):
            raise TypeError("storage must implement append() and read()")
        self._owner: object | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._resources: dict[object, object] = {}

    def _resource(self, key, factory):
        """Session-scoped tool state, separate from conversation facts."""
        self._check_loop()
        if key not in self._resources:
            self._resources[key] = factory()
        return self._resources[key]

    async def _close_resources(self):
        errors = []
        for resource in reversed(tuple(self._resources.values())):
            close = getattr(resource, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception as exc:
                    errors.append(exc)
        self._resources.clear()
        if errors:
            raise RuntimeError("Failed to close session tool resources") from errors[0]

    @property
    def id(self) -> str:
        return self._id

    @property
    def policy_id(self) -> str:
        return self._policy_id

    @property
    def policy_version(self) -> str:
        return self._policy_version

    def _claim(self, owner: object) -> None:
        if self._owner is not None:
            raise ValueError("Context already belongs to a session")
        self._owner = owner
        self._loop = asyncio.get_running_loop()

    def _check_loop(self) -> None:
        if self._loop is not None and asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("Context belongs to a different event loop")

    def history(self) -> tuple[ContextEntry, ...]:
        self._check_loop()
        return deepcopy(tuple(self._storage.read(self.id)))

    async def prepare(self) -> tuple[ContextEntry, ...]:
        self._check_loop()
        view = tuple(await self._policy.prepare(self.history()))
        if not all(isinstance(entry, ContextEntry) for entry in view):
            raise TypeError("Context policy must return ContextEntry values")
        return deepcopy(view)

    async def prepare_request(self, request, *, model, execution):
        """Apply optional model-aware preparation through the fenced Run view."""
        from aworld.core.agent.messages import ModelRequest
        prepare = getattr(self._policy, "prepare_request", None)
        if prepare is None:
            return deepcopy(request)
        value = await prepare(deepcopy(request), history=self.history(), model=model, execution=execution,
                              read_history=self.history)
        if not isinstance(value, ModelRequest):
            raise TypeError("Context policy must return a ModelRequest")
        return deepcopy(value)

    def _append(self, entry: ContextEntry) -> None:
        self._check_loop()
        self._storage.append(self.id, deepcopy(entry))
