"""A directly awaited tool, without registration or event-bus dispatch."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Awaitable, Callable, Mapping

from aworld.core.session.protocols import RunContext


class ToolExecutionError(RuntimeError):
    """A failed tool operation with structured output the model can inspect."""
    def __init__(self, message: str, result: object):
        super().__init__(message)
        self.result = deepcopy(result)


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    parameters: Mapping[str, object]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: Mapping[str, object]
    function: Callable[[dict[str, object], RunContext], Awaitable[object]]
    validate_arguments: Callable[[dict[str, object]], None] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Tool name must be non-empty")
        if not callable(self.function):
            raise TypeError("Tool function must be async callable")
        if self.validate_arguments is not None and not callable(self.validate_arguments):
            raise TypeError("Tool argument validator must be callable")
        schema = deepcopy(dict(self.parameters))
        json.dumps(schema, allow_nan=False)
        object.__setattr__(self, "parameters", schema)

    def schema(self) -> ToolSchema:
        return ToolSchema(self.name, self.description, deepcopy(self.parameters))

    async def execute(self, arguments: Mapping[str, object], context: RunContext) -> object:
        owned = deepcopy(dict(arguments))
        if self.validate_arguments is not None:
            self.validate_arguments(owned)
        return await self.function(owned, context)
