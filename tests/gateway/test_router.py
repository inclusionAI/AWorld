from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.agent_resolver import AgentResolver
from aworld_gateway import router as router_module
from aworld_gateway.router import GatewayRouter, LocalCliAgentBackend
from aworld_gateway.session_binding import SessionBinding
from aworld_gateway.types import InboundEnvelope


class FakeAgentBackend:
    def __init__(self) -> None:
        self.calls = []

    async def run(self, *, agent_id: str, session_id: str, text: str) -> str:
        self.calls.append(
            {
                "agent_id": agent_id,
                "session_id": session_id,
                "text": text,
            }
        )
        return "backend reply"


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
