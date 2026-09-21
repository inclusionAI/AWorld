from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aworld.sandbox.builder.sandbox_builder import SandboxBuilder


@pytest.mark.asyncio
async def test_builder_terminal_proxy_uses_production_defaults_and_scoped_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = SimpleNamespace(run_code=AsyncMock(return_value={"success": True}))
    builder = SandboxBuilder()
    monkeypatch.setattr(builder, "build", lambda: SimpleNamespace(terminal=terminal))

    result = await builder.run_code(
        "pwd",
        cwd="workspace",
        env={"MODE": "test"},
    )

    assert result == {"success": True}
    terminal.run_code.assert_awaited_once_with(
        code="pwd",
        timeout=300,
        output_format="structured",
        cwd="workspace",
        env={"MODE": "test"},
    )


@pytest.mark.asyncio
async def test_builder_terminal_proxy_exposes_artifact_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = SimpleNamespace(
        read_output_artifact=AsyncMock(return_value={"content": "chunk"})
    )
    builder = SandboxBuilder()
    monkeypatch.setattr(builder, "build", lambda: SimpleNamespace(terminal=terminal))

    result = await builder.read_output_artifact(
        "aworld-terminal-output://sha256/example",
        offset=10,
        limit=20,
        output="base64",
    )

    assert result == {"content": "chunk"}
    terminal.read_output_artifact.assert_awaited_once_with(
        artifact_ref="aworld-terminal-output://sha256/example",
        offset=10,
        limit=20,
        output="base64",
    )
