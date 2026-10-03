"""An explicit, immutable tool set. No global registration or dispatch bus."""

from typing import Iterable, Mapping

from .function import Tool


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools = {}
        for tool in tools:
            if not isinstance(tool, Tool):
                raise TypeError("tools must contain Tool values")
            if tool.name in self._tools:
                raise ValueError(f"Duplicate tool name: {tool.name}")
            self._tools[tool.name] = tool

    def __iter__(self):
        return iter(self._tools.values())

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise LookupError(f"Unknown tool: {name}") from None

    def schemas(self):
        return tuple(tool.schema() for tool in self)

    def select(self, *names: str) -> "ToolRegistry":
        """Create a separate capability set; the source registry stays unchanged."""
        return ToolRegistry(self.get(name) for name in names)

    def extend(self, tools: Iterable[Tool]) -> "ToolRegistry":
        return ToolRegistry((*self, *tools))

    async def execute(self, name: str, arguments: Mapping[str, object], context):
        return await self.get(name).execute(arguments, context)
