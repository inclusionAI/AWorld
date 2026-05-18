from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.agent_resolver import AgentResolver
from aworld_gateway import router as router_module
from aworld_gateway.router import GatewayRouter, LocalCliAgentBackend
from aworld_gateway.session_binding import SessionBinding
from aworld_gateway.types import InboundEnvelope
from aworld.core.task import TaskResponse
from aworld.output.base import ToolResultOutput


class FakeAgentBackend:
    def __init__(self) -> None:
        self.calls = []

    async def run(
        self,
        *,
        agent_id: str,
        session_id: str,
        text: str,
        on_output=None,
        allowed_tools=None,
    ) -> str:
        call = {
            "agent_id": agent_id,
            "session_id": session_id,
            "text": text,
        }
        if on_output is not None:
            call["on_output"] = on_output
        if allowed_tools is not None:
            call["allowed_tools"] = allowed_tools
        self.calls.append(call)
        return "backend reply"


class FakeCommandBridge:
    def __init__(self, *, handled: bool = True, text: str = "bridge reply") -> None:
        self.handled = handled
        self.text = text
        self.calls: list[dict[str, object]] = []

    async def execute(
        self,
        *,
        text: str,
        cwd: str,
        session_id: str,
        runtime=None,
        prompt_executor=None,
        on_output=None,
    ):
        self.calls.append(
            {
                "text": text,
                "cwd": cwd,
                "session_id": session_id,
                "runtime": runtime,
            }
        )
        return SimpleNamespace(
            handled=self.handled,
            command_name="memory",
            status="completed" if self.handled else "unknown",
            text=self.text,
        )


def test_handle_inbound_resolves_agent_builds_session_and_routes_execution():
    backend = FakeAgentBackend()
    router = GatewayRouter(
        session_binding=SessionBinding(),
        agent_resolver=AgentResolver(default_agent_id="aworld"),
        agent_backend=backend,
    )
    inbound = InboundEnvelope(
        channel="telegram",
        account_id="acct-1",
        conversation_id="conv-1",
        conversation_type="group",
        sender_id="sender-1",
        sender_name="Sender",
        message_id="msg-9",
        text="hello",
    )

    outbound = asyncio.run(
        router.handle_inbound(
            inbound,
            channel_default_agent_id="channel-agent",
            matched_route_agent_id="route-agent",
        )
    )

    assert backend.calls == [
        {
            "agent_id": "channel-agent",
            "session_id": "gw:channel-agent:telegram:acct-1:group:conv-1",
            "text": "hello",
        }
    ]
    assert outbound.channel == "telegram"
    assert outbound.account_id == "acct-1"
    assert outbound.conversation_id == "conv-1"
    assert outbound.reply_to_message_id == "msg-9"
    assert outbound.text == "backend reply"


def test_handle_inbound_uses_session_binding_conversation_override_from_metadata():
    backend = FakeAgentBackend()
    router = GatewayRouter(
        session_binding=SessionBinding(),
        agent_resolver=AgentResolver(default_agent_id="aworld"),
        agent_backend=backend,
    )
    inbound = InboundEnvelope(
        channel="wechat",
        account_id="acct-override",
        conversation_id="conv-visible",
        conversation_type="dm",
        sender_id="sender-override",
        sender_name="Sender",
        message_id="msg-override",
        text="hello after reset",
        metadata={"session_binding_conversation_id": "conv-reset-2"},
    )

    outbound = asyncio.run(
        router.handle_inbound(
            inbound,
            channel_default_agent_id="channel-agent",
        )
    )

    assert backend.calls == [
        {
            "agent_id": "channel-agent",
            "session_id": "gw:channel-agent:wechat:acct-override:dm:conv-reset-2",
            "text": "hello after reset",
        }
    ]
    assert outbound.conversation_id == "conv-visible"
    assert outbound.reply_to_message_id == "msg-override"
    assert outbound.metadata == {}


def test_handle_inbound_prefers_explicit_agent_id_over_other_sources():
    backend = FakeAgentBackend()
    router = GatewayRouter(
        session_binding=SessionBinding(),
        agent_resolver=AgentResolver(default_agent_id="aworld"),
        agent_backend=backend,
    )
    inbound = InboundEnvelope(
        channel="telegram",
        account_id="acct-2",
        conversation_id="conv-2",
        conversation_type="dm",
        sender_id="acct-2",
        sender_name=None,
        message_id="msg-2",
        text="ping",
    )

    outbound = asyncio.run(
        router.handle_inbound(
            inbound,
            channel_default_agent_id="channel-agent",
            explicit_agent_id="explicit-agent",
            session_agent_id="session-agent",
            matched_route_agent_id="route-agent",
        )
    )

    assert backend.calls[0]["agent_id"] == "explicit-agent"
    assert outbound.reply_to_message_id == "msg-2"


def test_handle_inbound_forwards_on_output_callback_to_backend():
    backend = FakeAgentBackend()
    router = GatewayRouter(
        session_binding=SessionBinding(),
        agent_resolver=AgentResolver(default_agent_id="aworld"),
        agent_backend=backend,
    )
    inbound = InboundEnvelope(
        channel="telegram",
        account_id="acct-3",
        conversation_id="conv-3",
        conversation_type="dm",
        sender_id="acct-3",
        sender_name=None,
        message_id="msg-3",
        text="observe",
    )

    def on_output(_output) -> None:
        return None

    asyncio.run(
        router.handle_inbound(
            inbound,
            channel_default_agent_id="channel-agent",
            on_output=on_output,
        )
    )

    assert backend.calls == [
        {
            "agent_id": "channel-agent",
            "session_id": "gw:channel-agent:telegram:acct-3:dm:conv-3",
            "text": "observe",
            "on_output": on_output,
        }
    ]


def test_handle_inbound_executes_slash_command_before_backend():
    backend = FakeAgentBackend()
    command_bridge = FakeCommandBridge(text="Memory instruction status")
    router = GatewayRouter(
        session_binding=SessionBinding(),
        agent_resolver=AgentResolver(default_agent_id="aworld"),
        agent_backend=backend,
        command_bridge=command_bridge,
    )
    inbound = InboundEnvelope(
        channel="wecom",
        account_id="acct-4",
        conversation_id="conv-4",
        conversation_type="dm",
        sender_id="acct-4",
        sender_name=None,
        message_id="msg-4",
        text="/memory status",
    )

    outbound = asyncio.run(
        router.handle_inbound(
            inbound,
            channel_default_agent_id="channel-agent",
        )
    )

    assert backend.calls == []
    assert command_bridge.calls == [
        {
            "text": "/memory status",
            "cwd": str(Path.cwd()),
            "session_id": "gw:channel-agent:wecom:acct-4:dm:conv-4",
            "runtime": None,
        }
    ]
    assert outbound.text == "Memory instruction status"
    assert outbound.reply_to_message_id == "msg-4"


def test_handle_inbound_executes_prompt_command_via_backend():
    backend = FakeAgentBackend()
    router = GatewayRouter(
        session_binding=SessionBinding(),
        agent_resolver=AgentResolver(default_agent_id="aworld"),
        agent_backend=backend,
    )
    inbound = InboundEnvelope(
        channel="wecom",
        account_id="acct-5",
        conversation_id="conv-5",
        conversation_type="dm",
        sender_id="acct-5",
        sender_name=None,
        message_id="msg-5",
        text="/diff main",
    )

    outbound = asyncio.run(
        router.handle_inbound(
            inbound,
            channel_default_agent_id="channel-agent",
        )
    )

    assert len(backend.calls) == 1
    assert backend.calls[0]["agent_id"] == "channel-agent"
    assert backend.calls[0]["session_id"] == "gw:channel-agent:wecom:acct-5:dm:conv-5"
    assert "Diff Summary Task" in backend.calls[0]["text"]
    assert "main" in backend.calls[0]["text"]
    assert "git_diff" in backend.calls[0]["allowed_tools"]
    assert outbound.text == "backend reply"


def test_handle_inbound_logs_routing_flow(caplog: pytest.LogCaptureFixture):
    backend = FakeAgentBackend()
    router = GatewayRouter(
        session_binding=SessionBinding(),
        agent_resolver=AgentResolver(default_agent_id="aworld"),
        agent_backend=backend,
    )
    inbound = InboundEnvelope(
        channel="wechat",
        account_id="acct-1",
        conversation_id="conv-1",
        conversation_type="dm",
        sender_id="sender-1",
        sender_name="Sender",
        message_id="msg-1",
        text="hello",
    )
    caplog.set_level(logging.INFO, logger="aworld.gateway")

    outbound = asyncio.run(
        router.handle_inbound(
            inbound,
            channel_default_agent_id="channel-agent",
        )
    )

    assert outbound.text == "backend reply"
    assert "Gateway router inbound channel=wechat conversation=conv-1" in caplog.text
    assert "Gateway router resolved agent=channel-agent session=gw:channel-agent:wechat:acct-1:dm:conv-1" in caplog.text
    assert "Gateway router outbound channel=wechat conversation=conv-1 reply_to=msg-1" in caplog.text


def test_handle_inbound_logs_backend_failure(caplog: pytest.LogCaptureFixture):
    class FailingBackend:
        async def run(self, **kwargs) -> str:
            raise RuntimeError("backend boom")

    router = GatewayRouter(
        session_binding=SessionBinding(),
        agent_resolver=AgentResolver(default_agent_id="aworld"),
        agent_backend=FailingBackend(),
    )
    inbound = InboundEnvelope(
        channel="wechat",
        account_id="acct-1",
        conversation_id="conv-1",
        conversation_type="dm",
        sender_id="sender-1",
        sender_name="Sender",
        message_id="msg-1",
        text="hello",
    )
    caplog.set_level(logging.INFO, logger="aworld.gateway")

    with pytest.raises(RuntimeError, match="backend boom"):
        asyncio.run(
            router.handle_inbound(
                inbound,
                channel_default_agent_id="channel-agent",
            )
        )

    assert "Gateway router backend failed agent=channel-agent session=gw:channel-agent:wechat:acct-1:dm:conv-1 error=backend boom" in caplog.text


class _MissingRegistry:
    @staticmethod
    def get_agent(agent_id: str):
        return None


class _SuccessExecutor:
    instances = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.cleanup_called = False
        type(self).instances.append(self)

    async def chat(self, text: str) -> str:
        return f"ok:{text}"

    async def cleanup_resources(self) -> None:
        self.cleanup_called = True


class _StreamingExecutor(_SuccessExecutor):
    instances = []

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.build_calls = []

    async def _build_task(self, text: str, *, session_id: str):
        self.build_calls.append((text, session_id))
        return "TASK"


class _FailingExecutor(_SuccessExecutor):
    async def chat(self, text: str) -> str:
        raise RuntimeError("chat failed")


class _SimpleAgent:
    def __init__(self, context_config="cfg", hooks=None) -> None:
        self.context_config = context_config
        self.hooks = hooks

    async def get_swarm(self, context):
        return "swarm"


class _SimpleRegistry:
    agent = _SimpleAgent()

    @classmethod
    def get_agent(cls, agent_id: str):
        return cls.agent


def test_local_cli_backend_missing_agent_raises_value_error():
    backend = LocalCliAgentBackend(
        registry_cls=_MissingRegistry,
        executor_cls=_SuccessExecutor,
    )

    with pytest.raises(ValueError, match="Agent not found: missing"):
        asyncio.run(backend.run(agent_id="missing", session_id="s1", text="hi"))


def test_local_cli_backend_cleans_up_executor_after_successful_chat():
    _SuccessExecutor.instances = []
    backend = LocalCliAgentBackend(
        registry_cls=_SimpleRegistry,
        executor_cls=_SuccessExecutor,
    )

    result = asyncio.run(backend.run(agent_id="aworld", session_id="s1", text="hi"))

    assert result == "ok:hi"
    assert len(_SuccessExecutor.instances) == 1
    assert _SuccessExecutor.instances[0].cleanup_called is True


def test_local_cli_backend_cleans_up_executor_when_chat_raises():
    _FailingExecutor.instances = []
    backend = LocalCliAgentBackend(
        registry_cls=_SimpleRegistry,
        executor_cls=_FailingExecutor,
    )

    with pytest.raises(RuntimeError, match="chat failed"):
        asyncio.run(backend.run(agent_id="aworld", session_id="s2", text="boom"))

    assert len(_FailingExecutor.instances) == 1
    assert _FailingExecutor.instances[0].cleanup_called is True


def test_local_cli_backend_queues_same_session_input_as_steering():
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class QueuedSteeringExecutor(_SuccessExecutor):
        instances = []

        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.chat_calls: list[str] = []

        async def chat(self, text: str) -> str:
            self.chat_calls.append(text)
            if text == "alpha":
                first_started.set()
                await release_first.wait()
            return f"ok:{text}"

    backend = LocalCliAgentBackend(
        registry_cls=_SimpleRegistry,
        executor_cls=QueuedSteeringExecutor,
    )

    async def run_scenario() -> tuple[str, str]:
        first_task = asyncio.create_task(
            backend.run(agent_id="aworld", session_id="s-steer", text="alpha")
        )
        await first_started.wait()
        steering_ack = await backend.run(
            agent_id="aworld",
            session_id="s-steer",
            text="beta",
        )
        release_first.set()
        return await first_task, steering_ack

    first_result, steering_ack = asyncio.run(run_scenario())

    assert steering_ack == router_module.STEERING_CAPTURED_ACK
    assert len(QueuedSteeringExecutor.instances) == 1
    assert QueuedSteeringExecutor.instances[0].chat_calls[0] == "alpha"
    assert "Continue the current task with this additional operator steering:" in (
        QueuedSteeringExecutor.instances[0].chat_calls[1]
    )
    assert "beta" in QueuedSteeringExecutor.instances[0].chat_calls[1]
    assert "beta" in first_result
    assert QueuedSteeringExecutor.instances[0].cleanup_called is True


def test_local_cli_backend_on_output_streams_visible_text_and_cleans_up(monkeypatch):
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

    class FakeStreamingOutputs:
        async def stream_events(self):
            yield FakeChunkOutput("hello")
            yield FakeChunkOutput(" world")
            yield ToolResultOutput(
                tool_name="cron",
                action_name="cron_tool",
                data={"job_id": "job-1"},
            )
            yield FakeMessageOutput("hello world")
            yield FakeStepOutput("internal-state")

    seen_output_types = []

    async def on_output(output) -> None:
        output_type_getter = getattr(output, "output_type", None)
        seen_output_types.append(
            output_type_getter() if callable(output_type_getter) else type(output).__name__
        )

    _StreamingExecutor.instances = []
    monkeypatch.setattr(
        router_module.Runners,
        "streamed_run_task",
        lambda *, task: FakeStreamingOutputs(),
    )
    backend = LocalCliAgentBackend(
        registry_cls=_SimpleRegistry,
        executor_cls=_StreamingExecutor,
    )

    result = asyncio.run(
        backend.run(
            agent_id="aworld",
            session_id="stream-session",
            text="hi",
            on_output=on_output,
        )
    )

    assert result == "hello world"
    assert seen_output_types == ["chunk", "chunk", "tool_call_result", "message", "step"]
    assert len(_StreamingExecutor.instances) == 1
    assert _StreamingExecutor.instances[0].build_calls == [("hi", "stream-session")]
    assert _StreamingExecutor.instances[0].cleanup_called is True


def test_local_cli_backend_on_output_prefers_final_task_answer_over_accumulated_progress(
    monkeypatch,
):
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

    class FakeStreamingOutputs:
        def __init__(self) -> None:
            self._task_response = TaskResponse(
                success=True,
                answer="最终抓取完成。",
                msg="ok",
            )

        async def stream_events(self):
            yield FakeChunkOutput("先打开 X。")
            yield FakeMessageOutput("先打开 X。")
            yield ToolResultOutput(tool_name="bash", action_name="bash", data="opened")
            yield FakeChunkOutput("确认已登录。")
            yield FakeMessageOutput("确认已登录。")
            yield FakeChunkOutput("最终抓取完成。")
            yield FakeMessageOutput("最终抓取完成。")

        def response(self):
            return self._task_response

    seen_output_types = []

    async def on_output(output) -> None:
        output_type_getter = getattr(output, "output_type", None)
        seen_output_types.append(
            output_type_getter() if callable(output_type_getter) else type(output).__name__
        )

    _StreamingExecutor.instances = []
    monkeypatch.setattr(
        router_module.Runners,
        "streamed_run_task",
        lambda *, task: FakeStreamingOutputs(),
    )
    backend = LocalCliAgentBackend(
        registry_cls=_SimpleRegistry,
        executor_cls=_StreamingExecutor,
    )

    result = asyncio.run(
        backend.run(
            agent_id="aworld",
            session_id="stream-session",
            text="hi",
            on_output=on_output,
        )
    )

    assert result == "最终抓取完成。"
    assert seen_output_types == [
        "chunk",
        "message",
        "tool_call_result",
        "chunk",
        "message",
        "chunk",
        "message",
    ]
    assert len(_StreamingExecutor.instances) == 1
    assert _StreamingExecutor.instances[0].cleanup_called is True


def test_local_cli_backend_on_output_cleans_up_when_callback_raises(monkeypatch):
    class FakeChunkOutput:
        def __init__(self, content: str) -> None:
            self.content = content

        def output_type(self) -> str:
            return "chunk"

    class FakeStreamingOutputs:
        async def stream_events(self):
            yield FakeChunkOutput("hello")

    def exploding_callback(_output) -> None:
        raise RuntimeError("callback failed")

    _StreamingExecutor.instances = []
    monkeypatch.setattr(
        router_module.Runners,
        "streamed_run_task",
        lambda *, task: FakeStreamingOutputs(),
    )
    backend = LocalCliAgentBackend(
        registry_cls=_SimpleRegistry,
        executor_cls=_StreamingExecutor,
    )

    with pytest.raises(RuntimeError, match="callback failed"):
        asyncio.run(
            backend.run(
                agent_id="aworld",
                session_id="stream-session",
                text="hi",
                on_output=exploding_callback,
            )
        )

    assert len(_StreamingExecutor.instances) == 1
    assert _StreamingExecutor.instances[0].cleanup_called is True


def test_local_cli_backend_context_bootstrap_fallback_on_type_error(monkeypatch):
    class NeedsContextAgent:
        def __init__(self) -> None:
            self.context_config = "agent-context-config"
            self.hooks = ["h1"]
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

    class CaptureExecutor(_SuccessExecutor):
        instances = []

    monkeypatch.setattr(router_module, "TaskInput", FakeTaskInput)
    monkeypatch.setattr(router_module, "ApplicationContext", FakeApplicationContext)

    CaptureExecutor.instances = []
    backend = LocalCliAgentBackend(
        registry_cls=NeedsContextRegistry,
        executor_cls=CaptureExecutor,
    )

    result = asyncio.run(backend.run(agent_id="aworld", session_id="s3", text="hello"))

    assert result == "ok:hello"
    assert NeedsContextRegistry.agent.calls == [None, "TEMP_CONTEXT"]
    assert len(FakeApplicationContext.calls) == 1
    assert FakeApplicationContext.calls[0][1] == "agent-context-config"
    assert CaptureExecutor.instances[0].kwargs["swarm"] == "swarm-with-context"
    assert CaptureExecutor.instances[0].cleanup_called is True


def test_local_cli_backend_init_allows_partial_dependency_override():
    class CustomRegistry:
        pass

    class CustomExecutor:
        pass

    backend_with_custom_registry = LocalCliAgentBackend(registry_cls=CustomRegistry)
    assert backend_with_custom_registry._registry_cls is CustomRegistry
    assert backend_with_custom_registry._executor_cls is not None

    backend_with_custom_executor = LocalCliAgentBackend(executor_cls=CustomExecutor)
    assert backend_with_custom_executor._executor_cls is CustomExecutor
    assert backend_with_custom_executor._registry_cls is not None
