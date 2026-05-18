from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.channels.dingding import bridge as bridge_module
from aworld_gateway.channels.dingding.bridge import AworldDingdingBridge
from aworld_gateway.channels.dingding.types import DingdingBridgeResult
from aworld.output.base import ToolResultOutput


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
        self.build_calls: list[tuple[object, str]] = []
        self.pause_checks: list[dict[str, object]] = []
        type(self).instances.append(self)

    async def _build_task(self, text: object, *, session_id: str):
        self.build_calls.append((text, session_id))
        build_index = len(self.build_calls)
        return SimpleNamespace(
            id=f"task-{build_index}",
            context=SimpleNamespace(
                task_id=f"task-{build_index}",
                workspace_path="/tmp/dingding-test-workspace",
            ),
            text=text,
            session_id=session_id,
        )

    async def _should_pause_for_queued_steering_checkpoint(self, **kwargs) -> bool:
        self.pause_checks.append(kwargs)
        runtime = getattr(self, "_base_runtime", None)
        if runtime is None:
            return False
        snapshot = runtime.steering_snapshot(self.kwargs["session_id"])
        return bool(snapshot["interrupt_requested"]) and int(snapshot["pending_count"]) > 0

    async def cleanup_resources(self) -> None:
        self.cleanup_called = True


class FakeStreamingOutputs:
    def __init__(self, events_factory) -> None:
        self._events_factory = events_factory

    async def stream_events(self):
        async for event in self._events_factory():
            yield event


def _install_streamed_run_task(monkeypatch, events_factory) -> None:
    monkeypatch.setattr(
        bridge_module.Runners,
        "streamed_run_task",
        lambda *, task: FakeStreamingOutputs(lambda: events_factory(task)),
    )


def test_bridge_streams_chunks_and_returns_aggregated_text(monkeypatch) -> None:
    seen_chunks: list[str] = []

    class FakeOutput:
        def __init__(self, content: str) -> None:
            self.content = content

    async def events_factory(task):
        assert task.text == "hi"
        assert task.session_id == "dingding_conv"
        for chunk in ["hello", " ", "world"]:
            yield FakeOutput(chunk)

    async def on_text_chunk(chunk: str) -> None:
        seen_chunks.append(chunk)

    FakeExecutor.instances = []
    _install_streamed_run_task(monkeypatch, events_factory)
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
    assert FakeExecutor.instances[0].build_calls == [("hi", "dingding_conv")]
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

    class FakeOutput:
        def __init__(self, content: str) -> None:
            self.content = content

    async def events_factory(_task):
        yield FakeOutput("left")
        yield FakeOutput(" right")

    def on_text_chunk(chunk: str) -> None:
        seen_chunks.append(chunk)

    FakeExecutor.instances = []
    _install_streamed_run_task(monkeypatch, events_factory)
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
    class FakeOutput:
        def __init__(self, content: str) -> None:
            self.content = content

    async def events_factory(_task):
        yield FakeOutput("hello")

    def exploding_callback(_chunk: str) -> None:
        raise RuntimeError("callback failed")

    FakeExecutor.instances = []
    _install_streamed_run_task(monkeypatch, events_factory)
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


def test_bridge_forwards_raw_outputs_to_callback(monkeypatch) -> None:
    class FakeOutput:
        def __init__(self, content: str) -> None:
            self.content = content

    seen_outputs: list[str] = []

    async def events_factory(_task):
        yield FakeOutput("first")
        yield FakeOutput("second")

    async def on_output(output) -> None:
        seen_outputs.append(output.content)

    FakeExecutor.instances = []
    _install_streamed_run_task(monkeypatch, events_factory)
    bridge = AworldDingdingBridge(registry_cls=FakeRegistry, executor_cls=FakeExecutor)

    result = asyncio.run(
        bridge.run(
            agent_id="aworld",
            session_id="dingding_conv",
            text="hi",
            on_output=on_output,
        )
    )

    assert result == DingdingBridgeResult(text="firstsecond")
    assert seen_outputs == ["first", "second"]


def test_bridge_filters_visible_text_from_non_assistant_runtime_outputs(monkeypatch) -> None:
    class FakeChunkOutput:
        def __init__(self, content: str) -> None:
            self.content = content

        def output_type(self) -> str:
            return "chunk"

    class FakeMessageOutput:
        def __init__(self, response: str) -> None:
            self.response = response

        def output_type(self) -> str:
            return "message"

    class FakeStepOutput:
        def __init__(self, content: str) -> None:
            self.content = content

        def output_type(self) -> str:
            return "step"

    seen_chunks: list[str] = []
    seen_output_types: list[str] = []

    async def events_factory(_task):
        yield FakeChunkOutput("hello")
        yield FakeChunkOutput(" world")
        yield ToolResultOutput(
            tool_name="cron",
            action_name="cron_tool",
            data={"job_id": "job-1"},
        )
        yield FakeMessageOutput("hello world")
        yield FakeStepOutput("internal-state")

    async def on_text_chunk(chunk: str) -> None:
        seen_chunks.append(chunk)

    async def on_output(output) -> None:
        output_type_getter = getattr(output, "output_type", None)
        seen_output_types.append(
            output_type_getter() if callable(output_type_getter) else type(output).__name__
        )

    FakeExecutor.instances = []
    _install_streamed_run_task(monkeypatch, events_factory)
    bridge = AworldDingdingBridge(registry_cls=FakeRegistry, executor_cls=FakeExecutor)

    result = asyncio.run(
        bridge.run(
            agent_id="aworld",
            session_id="dingding_conv",
            text="hi",
            on_text_chunk=on_text_chunk,
            on_output=on_output,
        )
    )

    assert result == DingdingBridgeResult(text="hello world")
    assert seen_chunks == ["hello", " world"]
    assert seen_output_types == ["chunk", "chunk", "tool_call_result", "message", "step"]
    assert FakeExecutor.instances[0].cleanup_called is True


def test_bridge_queues_same_session_input_as_steering(monkeypatch) -> None:
    first_chunk_seen = asyncio.Event()
    release_message = asyncio.Event()
    follow_up_prompts: list[str] = []

    class FakeChunkOutput:
        def __init__(self, content: str) -> None:
            self.content = content

        def output_type(self) -> str:
            return "chunk"

    class FakeMessageOutput:
        def __init__(self, response: str) -> None:
            self.response = response

        def output_type(self) -> str:
            return "message"

    async def events_factory(task):
        text = str(task.text)
        if text == "alpha":
            yield FakeChunkOutput("draft")
            first_chunk_seen.set()
            await release_message.wait()
            yield FakeMessageOutput("draft")
            return

        follow_up_prompts.append(text)
        yield FakeMessageOutput("handled beta")

    _install_streamed_run_task(monkeypatch, events_factory)
    FakeExecutor.instances = []
    bridge = AworldDingdingBridge(registry_cls=FakeRegistry, executor_cls=FakeExecutor)

    async def run_scenario() -> tuple[DingdingBridgeResult, DingdingBridgeResult]:
        first_task = asyncio.create_task(
            bridge.run(
                agent_id="aworld",
                session_id="dingding_conv",
                text="alpha",
            )
        )
        await first_chunk_seen.wait()
        steering_ack = await bridge.run(
            agent_id="aworld",
            session_id="dingding_conv",
            text="beta",
        )
        release_message.set()
        return await first_task, steering_ack

    first_result, steering_ack = asyncio.run(run_scenario())

    assert steering_ack == DingdingBridgeResult(text=bridge_module.STEERING_CAPTURED_ACK)
    assert first_result == DingdingBridgeResult(text="handled beta")
    assert len(FakeExecutor.instances) == 1
    assert FakeExecutor.instances[0].build_calls[0] == ("alpha", "dingding_conv")
    assert "Continue the current task with this additional operator steering:" in follow_up_prompts[0]
    assert "beta" in follow_up_prompts[0]
    assert FakeExecutor.instances[0].cleanup_called is True


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

    class FakeOutput:
        def __init__(self, content: str) -> None:
            self.content = content

    async def events_factory(_task):
        yield FakeOutput("ok")

    monkeypatch.setattr(bridge_module, "TaskInput", FakeTaskInput)
    monkeypatch.setattr(bridge_module, "ApplicationContext", FakeApplicationContext)
    _install_streamed_run_task(monkeypatch, events_factory)

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
