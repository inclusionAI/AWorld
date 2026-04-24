from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.errors import AcpBusyError
from aworld_cli.acp.turn_controller import TurnController


@pytest.mark.asyncio
async def test_rejects_second_prompt_while_running() -> None:
    controller = TurnController()
    gate = asyncio.Event()

    async def never_finishes() -> None:
        await gate.wait()

    task = await controller.start_turn("session-1", never_finishes())

    try:
        with pytest.raises(AcpBusyError):
            await controller.start_turn("session-1", never_finishes())
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_cancel_on_idle_is_noop() -> None:
    controller = TurnController()

    result = await controller.cancel_turn("session-1")

    assert result == "noop"
