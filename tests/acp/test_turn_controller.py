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


@pytest.mark.asyncio
async def test_can_pause_running_turn_without_dropping_session_state() -> None:
    controller = TurnController()
    gate = asyncio.Event()

    async def waits() -> None:
        await gate.wait()

    task = await controller.start_turn("session-1", waits())
    controller.pause_turn("session-1")

    try:
        assert controller.has_active_turn("session-1") is True
        assert controller.is_paused("session-1") is True
    finally:
        gate.set()
        await task


@pytest.mark.asyncio
async def test_resume_paused_turn_runs_new_coroutine() -> None:
    controller = TurnController()
    first_gate = asyncio.Event()
    resumed = asyncio.Event()

    async def first_turn() -> None:
        await first_gate.wait()

    async def resumed_turn() -> None:
        resumed.set()

    task = await controller.start_turn("session-1", first_turn())
    controller.pause_turn("session-1")
    first_gate.set()
    await task

    resumed_task = await controller.resume_turn("session-1", resumed_turn())
    await resumed_task

    assert resumed.is_set() is True
    assert controller.has_active_turn("session-1") is False


@pytest.mark.asyncio
async def test_cancel_on_paused_turn_clears_paused_state() -> None:
    controller = TurnController()
    gate = asyncio.Event()

    async def waits() -> None:
        await gate.wait()

    task = await controller.start_turn("session-1", waits())
    controller.pause_turn("session-1")
    gate.set()
    await task

    result = await controller.cancel_turn("session-1")

    assert result == "cancelled"
    assert controller.has_active_turn("session-1") is False
    assert controller.is_paused("session-1") is False
