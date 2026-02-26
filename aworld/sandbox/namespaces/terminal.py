# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Terminal namespace: sandbox.terminal.run_code."""

from typing import TYPE_CHECKING, Any, Dict

from aworld.sandbox.namespaces.base import ToolNamespace, resolve_service_name

if TYPE_CHECKING:
    from aworld.sandbox.implementations.sandbox import Sandbox


class TerminalNamespace(ToolNamespace):
    """Terminal operations. Use sandbox.terminal.run_code()."""

    def __init__(self, sandbox: "Sandbox"):
        service_name = resolve_service_name(sandbox, "terminal")
        super().__init__(sandbox, service_name)

    async def run_code(
        self,
        code: str,
        timeout: int = 30,
        output_format: str = "markdown",
    ) -> Dict[str, Any]:
        """Execute shell code."""
        return await self._call_tool(
            "run_code",
            code=code,
            timeout=timeout,
            output_format=output_format,
        )
