from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.channels.dingding import bridge as bridge_module
from aworld_gateway.channels.dingding.bridge import AworldDingdingBridge
from aworld_gateway.channels.dingding.types import DingdingBridgeResult


class FakeRegistry:
    @staticmethod
    def get_agent(agent_id: str):
        class FakeAgent:
            context_config = None
            hooks = None

            async def get_swarm(self, _context):
                return "fake-swarm"

        return FakeAgent()


class FakeExecutor:
    instances: list["FakeExecutor"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.cleanup_called = False
        type(self).instances.append(self)

    async def cleanup_resources(self) -> None:
        self.cleanup_called = True


def test_bridge_streams_chunks_and_returns_aggregated_text(monkeypatch) -> None:
    seen_chunks: list[str] = []

    async def fake_stream_text(self, *, executor, text, session_id):
        assert executor is FakeExecutor.instances[0]
        assert text == "hi"
        assert session_id == "dingding_conv"
        for chunk in ["hello", " ", "world"]:
            yield chunk

    async def on_text_chunk(chunk: str) -> None:
        seen_chunks.append(chunk)

    FakeExecutor.instances = []
    monkeypatch.setattr(AworldDingdingBridge, "_stream_text", fake_stream_text)
    bridge = AworldDingdingBridge(registry_cls=FakeRegistry, executor_cls=FakeExecutor)

    result = asyncio.run(
        bridge.run(
            agent_id="aworld",
            session_id="dingding_conv",
            text="hi",
            on_text_chunk=on_text_chunk,
        )
    )

    assert result == DingdingBridgeResult(text="hello world")
    assert seen_chunks == ["hello", " ", "world"]
    assert FakeExecutor.instances[0].cleanup_called is True


def test_bridge_raises_value_error_for_missing_agent() -> None:
    class MissingRegistry:
        @staticmethod
        def get_agent(agent_id: str):
            return None

    bridge = AworldDingdingBridge(registry_cls=MissingRegistry, executor_cls=FakeExecutor)

    with pytest.raises(ValueError, match="Agent not found: missing"):
        asyncio.run(bridge.run(agent_id="missing", session_id="s1", text="hi"))


def test_bridge_supports_sync_chunk_callback(monkeypatch) -> None:
    seen_chunks: list[str] = []

    async def fake_stream_text(self, *, executor, text, session_id):
        yield "left"
        yield " right"

    def on_text_chunk(chunk: str) -> None:
        seen_chunks.append(chunk)

    FakeExecutor.instances = []
    monkeypatch.setattr(AworldDingdingBridge, "_stream_text", fake_stream_text)
    bridge = AworldDingdingBridge(registry_cls=FakeRegistry, executor_cls=FakeExecutor)

    result = asyncio.run(
        bridge.run(
            agent_id="aworld",
            session_id="dingding_conv",
            text="hi",
            on_text_chunk=on_text_chunk,
        )
    )

    assert result == DingdingBridgeResult(text="left right")
    assert seen_chunks == ["left", " right"]
    assert FakeExecutor.instances[0].cleanup_called is True


def test_bridge_cleans_up_executor_when_chunk_callback_raises(monkeypatch) -> None:
    async def fake_stream_text(self, *, executor, text, session_id):
        yield "hello"

    def exploding_callback(_chunk: str) -> None:
        raise RuntimeError("callback failed")

    FakeExecutor.instances = []
    monkeypatch.setattr(AworldDingdingBridge, "_stream_text", fake_stream_text)
    bridge = AworldDingdingBridge(registry_cls=FakeRegistry, executor_cls=FakeExecutor)

    with pytest.raises(RuntimeError, match="callback failed"):
        asyncio.run(
            bridge.run(
                agent_id="aworld",
                session_id="dingding_conv",
                text="hi",
                on_text_chunk=exploding_callback,
            )
        )

    assert FakeExecutor.instances[0].cleanup_called is True


def test_stream_text_uses_runners_streaming_outputs(monkeypatch) -> None:
    class FakeOutput:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeStreamingOutputs:
        async def stream_events(self):
            for value in ["hello", " ", "world"]:
                yield FakeOutput(value)

    class FakeTaskExecutor:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def _build_task(self, text: str, *, session_id: str):
            self.calls.append((text, session_id))
            return "TASK"

    monkeypatch.setattr(
        bridge_module.Runners,
        "streamed_run_task",
        lambda *, task: FakeStreamingOutputs(),
    )
    executor = FakeTaskExecutor()
    bridge = AworldDingdingBridge(registry_cls=FakeRegistry, executor_cls=FakeExecutor)

    async def collect_chunks():
        return [
            chunk
            async for chunk in bridge._stream_text(
                executor=executor,
                text="prompt",
                session_id="session-1",
            )
        ]

    chunks = asyncio.run(collect_chunks())

    assert executor.calls == [("prompt", "session-1")]
    assert chunks == ["hello", " ", "world"]


def test_bridge_falls_back_to_temp_context_when_agent_requires_it(monkeypatch) -> None:
    class NeedsContextAgent:
        def __init__(self) -> None:
            self.context_config = "agent-context-config"
            self.hooks = None
            self.calls = []

        async def get_swarm(self, context):
            self.calls.append(context)
            if context is None:
                raise TypeError("context required")
            return "swarm-with-context"

    class NeedsContextRegistry:
        agent = NeedsContextAgent()

        @classmethod
        def get_agent(cls, agent_id: str):
            return cls.agent

    class FakeTaskInput:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class FakeApplicationContext:
        calls = []

        @classmethod
        async def from_input(cls, task_input, *, context_config):
            cls.calls.append((task_input, context_config))
            return "TEMP_CONTEXT"

    async def fake_stream_text(self, *, executor, text, session_id):
        yield "ok"

    monkeypatch.setattr(bridge_module, "TaskInput", FakeTaskInput)
    monkeypatch.setattr(bridge_module, "ApplicationContext", FakeApplicationContext)
    monkeypatch.setattr(AworldDingdingBridge, "_stream_text", fake_stream_text)

    FakeExecutor.instances = []
    bridge = AworldDingdingBridge(
        registry_cls=NeedsContextRegistry,
        executor_cls=FakeExecutor,
    )

    result = asyncio.run(
        bridge.run(
            agent_id="aworld",
            session_id="ctx-session",
            text="hello",
        )
    )

    assert result == DingdingBridgeResult(text="ok")
    assert NeedsContextRegistry.agent.calls == [None, "TEMP_CONTEXT"]
    assert len(FakeApplicationContext.calls) == 1
    assert FakeApplicationContext.calls[0][1] == "agent-context-config"
    assert FakeExecutor.instances[0].kwargs["swarm"] == "swarm-with-context"
