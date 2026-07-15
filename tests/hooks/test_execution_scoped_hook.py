from __future__ import annotations

from types import SimpleNamespace

import pytest

from aworld.core.event.base import Message
from aworld.runners.hook.hooks import PreLLMCallHook
from aworld.runners.hook.scoped import ExecutionScopedHook


class _InteractiveOnlyHook(ExecutionScopedHook, PreLLMCallHook):
    allowed_execution_scopes = frozenset({"cli_interactive"})

    def __init__(self) -> None:
        self.calls = 0

    async def _exec_scoped(self, message, context=None):
        self.calls += 1
        return message


def _message() -> Message:
    return Message(
        category="agent_hook",
        payload={"event": "before_llm_call"},
        sender="agent",
        headers={},
    )


@pytest.mark.asyncio
async def test_execution_scoped_hook_runs_only_for_allowed_scope() -> None:
    hook = _InteractiveOnlyHook()
    message = _message()

    skipped = await hook.exec(
        message,
        context=SimpleNamespace(execution_scope="self_evolve"),
    )
    applied = await hook.exec(
        message,
        context=SimpleNamespace(execution_scope="cli_interactive"),
    )

    assert skipped is message
    assert applied is message
    assert hook.calls == 1


@pytest.mark.asyncio
async def test_execution_scoped_hook_reads_scope_from_context_info() -> None:
    hook = _InteractiveOnlyHook()
    message = _message()
    context = SimpleNamespace(context_info={"execution_scope": "cli_interactive"})

    result = await hook.exec(message, context=context)

    assert result is message
    assert hook.calls == 1


@pytest.mark.asyncio
async def test_execution_scoped_hook_requires_an_explicit_scope() -> None:
    hook = _InteractiveOnlyHook()
    message = _message()

    result = await hook.exec(message, context=SimpleNamespace())

    assert result is message
    assert hook.calls == 0

