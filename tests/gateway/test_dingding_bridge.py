from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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
