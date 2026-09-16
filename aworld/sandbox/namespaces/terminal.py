# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Terminal namespace: sandbox.terminal.run_code."""

from typing import TYPE_CHECKING, Any, Dict, Mapping

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
        timeout: float = 300,
        output_format: str = "structured",
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Dict[str, Any]:
        """Execute shell code."""
        return await self._call_tool(
            "run_code",
            code=code,
            timeout=timeout,
            output_format=output_format,
            cwd=cwd,
            env=dict(env) if env is not None else None,
        )

    async def read_output_artifact(
        self,
        artifact_ref: str,
        *,
        offset: int = 0,
        limit: int | None = None,
        output: str = "text",
    ) -> Dict[str, Any]:
        """Read a bounded chunk from output offloaded by ``run_code``."""
        return await self._call_tool(
            "read_output_artifact",
            artifact_ref=artifact_ref,
            offset=offset,
            limit=limit,
            output=output,
        )
