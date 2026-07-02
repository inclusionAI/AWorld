# Aworld Gateway DingTalk Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `aworld_gateway` so `aworld-cli gateway serve` can run a real DingTalk channel with Stream-mode intake, `sessionWebhook` reply, attachment handling, and AI card updates routed into the current Aworld Agent runtime.

**Architecture:** Keep the existing gateway control plane (`config`, `registry`, `runtime`, `aworld-cli gateway`) stable, but replace the current DingTalk stub with a channel-local subsystem under `aworld_gateway/channels/dingding/`. Introduce a DingTalk-specific streaming bridge for the current Aworld Agent stack instead of forcing DingTalk through the generic single-request/single-response router used by Telegram.

**Tech Stack:** Python 3.10+, Pydantic v2, `dingtalk-stream`, `httpx`, `anyio`, pytest, existing `aworld-cli` LocalAgentRegistry/LocalAgentExecutor/Runners infrastructure.

---

## Planned File Structure

- `aworld_gateway/config/models.py`: add `DingdingChannelConfig` and wire it into `ChannelConfigMap`
- `aworld_gateway/config/loader.py`: normalize legacy DingTalk config payloads
- `aworld_gateway/config/__init__.py`: export the new DingTalk config model
- `aworld_gateway/registry.py`: mark DingTalk implemented, validate env-backed config, build the real adapter
- `aworld_gateway/channels/dingding/types.py`: DingTalk-only dataclasses and constants such as attachments, AI cards, and session reset commands
- `aworld_gateway/channels/dingding/bridge.py`: streaming bridge from DingTalk into the current Aworld Agent runtime
- `aworld_gateway/channels/dingding/connector.py`: Stream callback handling, session bookkeeping, replies, attachments, and AI cards
- `aworld_gateway/channels/dingding/adapter.py`: lifecycle wrapper around the DingTalk connector
- `aworld/requirements.txt`: add the `dingtalk-stream` dependency for installs that use `setup.py`
- `tests/gateway/test_dingding_config.py`: config and registry coverage for DingTalk
- `tests/gateway/test_dingding_bridge.py`: bridge streaming behavior
- `tests/gateway/test_dingding_adapter.py`: adapter lifecycle behavior
- `tests/gateway/test_dingding_connector.py`: callback/session/reply/attachment/card behavior
- `tests/gateway/test_registry.py`, `tests/gateway/test_runtime.py`, `tests/gateway/test_gateway_status_command.py`, `tests/test_gateway_cli.py`: integration/status expectations

### Task 1: Upgrade Gateway Config, Dependency, And Registry For DingTalk

**Files:**
- Modify: `aworld/requirements.txt`
- Modify: `aworld_gateway/config/models.py`
- Modify: `aworld_gateway/config/loader.py`
- Modify: `aworld_gateway/config/__init__.py`
- Modify: `aworld_gateway/registry.py`
- Modify: `tests/gateway/test_registry.py`
- Modify: `tests/gateway/test_runtime.py`
- Modify: `tests/gateway/test_gateway_status_command.py`
- Create: `tests/gateway/test_dingding_config.py`

- [ ] **Step 1: Write the failing DingTalk config and registry tests**

```python
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import DingdingChannelConfig, GatewayConfigLoader
from aworld_gateway.registry import ChannelRegistry
from aworld_gateway.runtime import GatewayRuntime


def test_load_or_init_populates_dingding_defaults(tmp_path: Path) -> None:
    cfg = GatewayConfigLoader(base_dir=tmp_path).load_or_init()

    assert cfg.channels.dingding.enabled is False
    assert cfg.channels.dingding.client_id_env == "AWORLD_DINGTALK_CLIENT_ID"
    assert cfg.channels.dingding.client_secret_env == "AWORLD_DINGTALK_CLIENT_SECRET"
    assert cfg.channels.dingding.enable_ai_card is True
    assert cfg.channels.dingding.enable_attachments is True


def test_registry_validates_dingding_from_env(monkeypatch) -> None:
    registry = ChannelRegistry()
    monkeypatch.delenv("AWORLD_DINGTALK_CLIENT_ID", raising=False)
    monkeypatch.delenv("AWORLD_DINGTALK_CLIENT_SECRET", raising=False)

    assert registry.is_configured("dingding", DingdingChannelConfig()) is False

    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_ID", "cli-id")
    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_SECRET", "cli-secret")

    assert registry.is_configured("dingding", DingdingChannelConfig()) is True


def test_runtime_marks_enabled_dingding_as_degraded_when_env_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("AWORLD_DINGTALK_CLIENT_ID", raising=False)
    monkeypatch.delenv("AWORLD_DINGTALK_CLIENT_SECRET", raising=False)

    cfg = GatewayConfigLoader(base_dir=tmp_path).load_or_init()
    cfg.channels.dingding.enabled = True

    runtime = GatewayRuntime(config=cfg, registry=ChannelRegistry(), router=None)
    status = runtime.status()

    assert status["state"] == "degraded"
    assert status["channels"]["dingding"]["implemented"] is True
    assert status["channels"]["dingding"]["configured"] is False
    assert status["channels"]["dingding"]["state"] == "degraded"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_dingding_config.py tests/gateway/test_registry.py tests/gateway/test_runtime.py tests/gateway/test_gateway_status_command.py -q`
Expected: FAIL because `DingdingChannelConfig` does not exist yet and current registry still reports DingTalk as `implemented=False`.

- [ ] **Step 3: Implement DingTalk config, registry, and dependency changes**

```python
# aworld_gateway/config/models.py
class DingdingChannelConfig(BaseChannelConfig):
    client_id_env: Optional[str] = "AWORLD_DINGTALK_CLIENT_ID"
    client_secret_env: Optional[str] = "AWORLD_DINGTALK_CLIENT_SECRET"
    card_template_id_env: Optional[str] = "AWORLD_DINGTALK_CARD_TEMPLATE_ID"
    enable_ai_card: bool = True
    enable_attachments: bool = True
    workspace_dir: Optional[str] = None


class ChannelConfigMap(StrictConfigModel):
    web: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    dingding: DingdingChannelConfig = Field(default_factory=DingdingChannelConfig)
    feishu: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)
    wecom: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)
```

```python
# aworld_gateway/config/loader.py
    @staticmethod
    def _normalize_legacy_payload(raw: dict) -> None:
        channels = raw.get("channels")
        if not isinstance(channels, dict):
            return

        telegram = channels.get("telegram")
        if isinstance(telegram, dict) and telegram.get("bot_token") in (None, ""):
            telegram.pop("bot_token", None)

        dingding = channels.get("dingding")
        if isinstance(dingding, dict):
            dingding.pop("implemented", None)
```

```python
# aworld_gateway/config/__init__.py
from aworld_gateway.config.models import (
    BaseChannelConfig,
    ChannelConfigMap,
    DingdingChannelConfig,
    GatewayConfig,
    GatewayServerConfig,
    PlaceholderChannelConfig,
    RouteRule,
    TelegramChannelConfig,
)

__all__ = [
    "BaseChannelConfig",
    "ChannelConfigMap",
    "DingdingChannelConfig",
    "GatewayConfig",
    "GatewayConfigLoader",
    "GatewayServerConfig",
    "PlaceholderChannelConfig",
    "RouteRule",
    "TelegramChannelConfig",
]
```

```python
# aworld_gateway/registry.py
from aworld_gateway.config import (
    BaseChannelConfig,
    DingdingChannelConfig,
    TelegramChannelConfig,
)

                "dingding": ChannelRegistration(
                    metadata=ChannelMetadata(name="dingding", implemented=True),
                    label="DingTalk",
                    adapter_class=DingdingChannelAdapter,
                ),

        if channel_id == "telegram":
            if not isinstance(config, TelegramChannelConfig):
                return False
            return bool(config.bot_token_env and os.getenv(config.bot_token_env))

        if channel_id == "dingding":
            if not isinstance(config, DingdingChannelConfig):
                return False
            return bool(
                config.client_id_env
                and config.client_secret_env
                and os.getenv(config.client_id_env)
                and os.getenv(config.client_secret_env)
            )

        return True
```

```text
# aworld/requirements.txt
dingtalk-stream>=0.24.3
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_dingding_config.py tests/gateway/test_registry.py tests/gateway/test_runtime.py tests/gateway/test_gateway_status_command.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld/requirements.txt aworld_gateway/config/models.py aworld_gateway/config/loader.py aworld_gateway/config/__init__.py aworld_gateway/registry.py tests/gateway/test_dingding_config.py tests/gateway/test_registry.py tests/gateway/test_runtime.py tests/gateway/test_gateway_status_command.py
git commit -m "feat: add dingtalk gateway config and registry"
```

### Task 2: Add A Streaming DingTalk Bridge For The Current Aworld Agent Stack

**Files:**
- Create: `aworld_gateway/channels/dingding/types.py`
- Create: `aworld_gateway/channels/dingding/bridge.py`
- Create: `tests/gateway/test_dingding_bridge.py`

- [ ] **Step 1: Write the failing bridge tests**

```python
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.channels.dingding.bridge import (
    AworldDingdingBridge,
    DingdingBridgeResult,
)


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
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.cleaned = False

    async def cleanup_resources(self) -> None:
        self.cleaned = True


def test_bridge_streams_chunks_and_returns_final_text(monkeypatch) -> None:
    seen_chunks: list[str] = []

    async def fake_stream_text(self, *, executor, text, session_id):
        for chunk in ["hello", " ", "world"]:
            yield chunk

    monkeypatch.setattr(AworldDingdingBridge, "_stream_text", fake_stream_text)
    bridge = AworldDingdingBridge(registry_cls=FakeRegistry, executor_cls=FakeExecutor)

    result = asyncio.run(
        bridge.run(
            agent_id="aworld",
            session_id="dingding_conv",
            text="hi",
            on_text_chunk=seen_chunks.append,
        )
    )

    assert result == DingdingBridgeResult(text="hello world")
    assert seen_chunks == ["hello", " ", "world"]


def test_bridge_raises_for_missing_agent() -> None:
    class MissingRegistry:
        @staticmethod
        def get_agent(agent_id: str):
            return None

    bridge = AworldDingdingBridge(registry_cls=MissingRegistry, executor_cls=FakeExecutor)

    with pytest.raises(ValueError, match="Agent not found: missing"):
        asyncio.run(bridge.run(agent_id="missing", session_id="s1", text="hi"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_dingding_bridge.py -q`
Expected: FAIL with `ModuleNotFoundError` for `aworld_gateway.channels.dingding.bridge`.

- [ ] **Step 3: Implement the bridge and DingTalk-local types**

```python
# aworld_gateway/channels/dingding/types.py
from __future__ import annotations

from dataclasses import dataclass

NEW_SESSION_COMMANDS = {"/new", "/summary", "新会话", "压缩上下文"}


@dataclass(frozen=True)
class DingdingBridgeResult:
    text: str


@dataclass(frozen=True)
class IncomingAttachment:
    download_code: str
    file_name: str


@dataclass(frozen=True)
class ExtractedMessage:
    text: str
    attachments: list[IncomingAttachment]
```

```python
# aworld_gateway/channels/dingding/bridge.py
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from aworld.runner import Runners
from aworld_cli.executors.local import LocalAgentExecutor
from aworld_cli.core.agent_registry import LocalAgentRegistry

from aworld_gateway.channels.dingding.types import DingdingBridgeResult


class AworldDingdingBridge:
    def __init__(self, registry_cls: Any = None, executor_cls: Any = None) -> None:
        self._registry_cls = registry_cls or LocalAgentRegistry
        self._executor_cls = executor_cls or LocalAgentExecutor

    async def run(
        self,
        *,
        agent_id: str,
        session_id: str,
        text: str,
        on_text_chunk: Callable[[str], Any] | None = None,
    ) -> DingdingBridgeResult:
        agent = self._registry_cls.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")

        swarm = await agent.get_swarm(None)
        executor = self._executor_cls(
            swarm=swarm,
            context_config=getattr(agent, "context_config", None),
            session_id=session_id,
            hooks=getattr(agent, "hooks", None),
        )

        try:
            chunks: list[str] = []
            async for chunk in self._stream_text(executor=executor, text=text, session_id=session_id):
                chunks.append(chunk)
                if on_text_chunk is not None:
                    maybe_result = on_text_chunk(chunk)
                    if hasattr(maybe_result, "__await__"):
                        await maybe_result
            return DingdingBridgeResult(text="".join(chunks).strip())
        finally:
            cleanup = getattr(executor, "cleanup_resources", None)
            if cleanup is not None:
                await cleanup()

    async def _stream_text(
        self,
        *,
        executor: LocalAgentExecutor,
        text: str,
        session_id: str,
    ) -> AsyncIterator[str]:
        task = await executor._build_task(text, session_id=session_id)
        outputs = Runners.streamed_run_task(task=task)

        async for output in outputs.stream_events():
            delta = self._extract_text(output)
            if delta:
                yield delta

    @staticmethod
    def _extract_text(output: Any) -> str:
        if hasattr(output, "content") and isinstance(output.content, str):
            return output.content
        if hasattr(output, "payload") and isinstance(output.payload, str):
            return output.payload
        data = getattr(output, "data", None)
        if isinstance(data, str):
            return data
        return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_dingding_bridge.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld_gateway/channels/dingding/types.py aworld_gateway/channels/dingding/bridge.py tests/gateway/test_dingding_bridge.py
git commit -m "feat: add dingtalk gateway streaming bridge"
```

### Task 3: Implement The DingTalk Connector And Adapter Core Message Flow

**Files:**
- Modify: `aworld_gateway/channels/dingding/adapter.py`
- Create: `aworld_gateway/channels/dingding/connector.py`
- Modify: `aworld_gateway/channels/dingding/__init__.py`
- Create: `tests/gateway/test_dingding_adapter.py`
- Create: `tests/gateway/test_dingding_connector.py`

- [ ] **Step 1: Write the failing adapter and connector tests**

```python
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.channels.dingding.adapter import DingdingChannelAdapter
from aworld_gateway.channels.dingding.connector import DingTalkConnector
from aworld_gateway.config import DingdingChannelConfig


class FakeBridge:
    async def run(self, *, agent_id, session_id, text, on_text_chunk=None):
        return type("Result", (), {"text": "bridge reply"})()


class FakeConnector:
    def __init__(self, *, config, bridge, stream_module) -> None:
        self.config = config
        self.bridge = bridge
        self.stream_module = stream_module
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def test_adapter_builds_and_starts_connector(monkeypatch) -> None:
    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_ID", "ding-id")
    monkeypatch.setenv("AWORLD_DINGTALK_CLIENT_SECRET", "ding-secret")
    monkeypatch.setattr(
        DingdingChannelAdapter,
        "_import_stream_module",
        lambda self: object(),
    )

    adapter = DingdingChannelAdapter(
        DingdingChannelConfig(),
        bridge=FakeBridge(),
        connector_cls=FakeConnector,
    )

    asyncio.run(adapter.start())

    assert adapter.metadata().implemented is True
    assert adapter._connector is not None
    assert adapter._connector.started is True


def test_connector_resets_session_for_new_command() -> None:
    connector = DingTalkConnector(
        config=DingdingChannelConfig(),
        bridge=FakeBridge(),
        stream_module=object(),
    )

    replies: list[str] = []

    async def fake_send_text(*, session_webhook: str, text: str) -> None:
        replies.append(text)

    connector.send_text = fake_send_text

    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "conversationId": "conv-1",
                "senderId": "user-1",
                "text": {"content": "/new"},
            }
        )
    )

    assert replies == ["✨ 已开启新会话，之前的上下文已清空。"]
    assert "conv-1" in connector._session_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_dingding_adapter.py tests/gateway/test_dingding_connector.py -q`
Expected: FAIL because the current DingTalk adapter is still a stub and `connector.py` does not exist.

- [ ] **Step 3: Implement the adapter and the core callback flow**

```python
# aworld_gateway/channels/dingding/connector.py
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

import httpx

from aworld_gateway.channels.dingding.bridge import AworldDingdingBridge
from aworld_gateway.channels.dingding.types import (
    ExtractedMessage,
    IncomingAttachment,
    NEW_SESSION_COMMANDS,
)
from aworld_gateway.config import DingdingChannelConfig


class DingTalkConnector:
    def __init__(
        self,
        *,
        config: DingdingChannelConfig,
        bridge: AworldDingdingBridge,
        stream_module,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._bridge = bridge
        self._stream_module = stream_module
        self._http = http_client or httpx.AsyncClient(timeout=60.0)
        self._session_ids: dict[str, str] = {}

    async def start(self) -> None:
        credential = self._stream_module.Credential(
            self._required_env(self._config.client_id_env),
            self._required_env(self._config.client_secret_env),
        )
        self._client = self._stream_module.DingTalkStreamClient(credential)
        connector = self

        class _MessageHandler(self._stream_module.ChatbotHandler):
            async def process(self, callback):
                await connector.handle_callback(getattr(callback, "data", callback))
                return getattr(connector._stream_module.AckMessage, "STATUS_OK", "ok"), "OK"

        self._client.register_callback_handler(self._stream_module.ChatbotMessage.TOPIC, _MessageHandler())

    async def stop(self) -> None:
        await self._http.aclose()

    async def handle_callback(self, callback_payload) -> None:
        data = self._parse_data(callback_payload)
        session_webhook = str(data.get("sessionWebhook") or "").strip()
        if not session_webhook:
            return

        sender_id = str(data.get("senderStaffId") or data.get("senderId") or "").strip()
        if not sender_id:
            return

        message = self._extract_message(data)
        user_text = message.text.strip()
        if not user_text and not message.attachments:
            return

        conversation_key = str(data.get("conversationId") or sender_id).strip()
        if user_text.lower() in {command.lower() for command in NEW_SESSION_COMMANDS}:
            self._session_ids[conversation_key] = self._new_session_id(conversation_key)
            await self.send_text(session_webhook=session_webhook, text="✨ 已开启新会话，之前的上下文已清空。")
            return

        session_id = self._session_ids.get(conversation_key) or self._new_session_id(conversation_key)
        self._session_ids[conversation_key] = session_id
        result = await self._bridge.run(
            agent_id=self._config.default_agent_id or "aworld",
            session_id=session_id,
            text=message.text,
        )
        await self.send_text(session_webhook=session_webhook, text=result.text or "（空响应）")

    async def send_text(self, *, session_webhook: str, text: str) -> None:
        await self._http.post(session_webhook, json={"msgtype": "text", "text": {"content": text}})

    def _extract_message(self, data: dict) -> ExtractedMessage:
        raw_text = data.get("text")
        if isinstance(raw_text, dict):
            text = str(raw_text.get("content") or "")
        else:
            text = str(data.get("content") or "")
        return ExtractedMessage(text=text, attachments=[])

    @staticmethod
    def _parse_data(raw) -> dict:
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _new_session_id(conversation_key: str) -> str:
        return f"dingtalk_{conversation_key}_{uuid4().hex[:8]}"

    @staticmethod
    def _required_env(name: str | None) -> str:
        import os

        value = os.getenv(name or "", "").strip()
        if not value:
            raise ValueError(f"Missing required env var: {name}")
        return value
```

```python
# aworld_gateway/channels/dingding/adapter.py
from __future__ import annotations

from aworld_gateway.channels.base import ChannelAdapter, ChannelMetadata
from aworld_gateway.channels.dingding.bridge import AworldDingdingBridge
from aworld_gateway.channels.dingding.connector import DingTalkConnector
from aworld_gateway.config import DingdingChannelConfig
from aworld_gateway.types import OutboundEnvelope


class DingdingChannelAdapter(ChannelAdapter):
    def __init__(
        self,
        config: DingdingChannelConfig | None = None,
        *,
        bridge: AworldDingdingBridge | None = None,
        connector_cls=DingTalkConnector,
    ) -> None:
        super().__init__(config or DingdingChannelConfig())
        self._config = config or DingdingChannelConfig()
        self._bridge = bridge or AworldDingdingBridge()
        self._connector_cls = connector_cls
        self._connector = None

    @classmethod
    def metadata(cls) -> ChannelMetadata:
        return ChannelMetadata(name="dingding", implemented=True)

    async def start(self) -> None:
        stream_module = self._import_stream_module()
        self._connector = self._connector_cls(
            config=self._config,
            bridge=self._bridge,
            stream_module=stream_module,
        )
        await self._connector.start()

    async def stop(self) -> None:
        if self._connector is not None:
            await self._connector.stop()

    async def send(self, envelope: OutboundEnvelope):
        if self._connector is None:
            raise RuntimeError("DingTalk channel adapter is not started.")
        session_webhook = str(envelope.metadata.get("session_webhook") or "").strip()
        if not session_webhook:
            raise ValueError("Missing session_webhook metadata for DingTalk send.")
        await self._connector.send_text(session_webhook=session_webhook, text=envelope.text)
        return {"session_webhook": session_webhook, "text": envelope.text}

    def _import_stream_module(self):
        import dingtalk_stream

        return dingtalk_stream
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_dingding_adapter.py tests/gateway/test_dingding_connector.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld_gateway/channels/dingding/adapter.py aworld_gateway/channels/dingding/connector.py aworld_gateway/channels/dingding/__init__.py tests/gateway/test_dingding_adapter.py tests/gateway/test_dingding_connector.py
git commit -m "feat: add dingtalk gateway connector core"
```

### Task 4: Add Attachments, AI Cards, And Final Gateway Integration Coverage

**Files:**
- Modify: `aworld_gateway/channels/dingding/types.py`
- Modify: `aworld_gateway/channels/dingding/connector.py`
- Modify: `tests/gateway/test_dingding_connector.py`
- Modify: `tests/gateway/test_gateway_status_command.py`
- Modify: `tests/test_gateway_cli.py`

- [ ] **Step 1: Write the failing attachment, AI card, and integration tests**

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from aworld_gateway.channels.dingding.connector import DingTalkConnector
from aworld_gateway.config import DingdingChannelConfig


class FakeBridge:
    async def run(self, *, agent_id, session_id, text, on_text_chunk=None):
        if on_text_chunk is not None:
            await on_text_chunk("hello")
            await on_text_chunk(" world")
        return type("Result", (), {"text": "hello world [report](attachment:///tmp/report.txt)"})()


def test_connector_streams_ai_card_then_finishes_with_final_text(tmp_path: Path) -> None:
    connector = DingTalkConnector(
        config=DingdingChannelConfig(enable_ai_card=True),
        bridge=FakeBridge(),
        stream_module=object(),
    )
    calls: list[tuple[str, str]] = []

    async def fake_create_ai_card(data):
        return type("Card", (), {"card_instance_id": "card-1", "access_token": "token", "inputing_started": False})()

    async def fake_stream_ai_card(card, content: str, finished: bool):
        calls.append(("stream", content if not finished else f"final:{content}"))
        return True

    async def fake_send_text(*, session_webhook: str, text: str):
        calls.append(("text", text))

    connector._try_create_ai_card = fake_create_ai_card
    connector._stream_ai_card = fake_stream_ai_card
    connector.send_text = fake_send_text
    connector._process_local_media_links = lambda content: asyncio.sleep(0, result=(content, []))

    asyncio.run(
        connector.handle_callback(
            {
                "sessionWebhook": "https://callback",
                "conversationId": "conv-1",
                "senderId": "user-1",
                "text": {"content": "hi"},
            }
        )
    )

    assert calls[0] == ("stream", "hello")
    assert calls[-1] == ("stream", "final:hello world [report](attachment:///tmp/report.txt)")


def test_gateway_channels_list_reports_dingding_as_real_channel(tmp_path: Path) -> None:
    from aworld_cli.gateway_cli import handle_gateway_channels_list

    rows = handle_gateway_channels_list(base_dir=tmp_path)

    assert rows["dingding"]["implemented"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_dingding_connector.py tests/gateway/test_gateway_status_command.py tests/test_gateway_cli.py -q`
Expected: FAIL because the current connector does not process attachments/AI cards yet and the CLI integration tests still reflect stub-era expectations.

- [ ] **Step 3: Implement attachment and AI card support**

```python
# aworld_gateway/channels/dingding/types.py
from dataclasses import dataclass

@dataclass
class AICardInstance:
    card_instance_id: str
    access_token: str
    inputing_started: bool = False


@dataclass(frozen=True)
class PendingFileMessage:
    media_id: str
    file_name: str
    file_type: str
```

```python
# aworld_gateway/channels/dingding/connector.py
    async def handle_callback(self, callback_payload) -> None:
        data = self._parse_data(callback_payload)
        session_webhook = str(data.get("sessionWebhook") or "").strip()
        if not session_webhook:
            return

        sender_id = str(data.get("senderStaffId") or data.get("senderId") or "").strip()
        if not sender_id:
            return

        message = self._extract_message(data)
        user_text = message.text.strip()
        if not user_text and not message.attachments:
            return

        conversation_key = str(data.get("conversationId") or sender_id).strip()
        if user_text.lower() in {command.lower() for command in NEW_SESSION_COMMANDS}:
            self._session_ids[conversation_key] = self._new_session_id(conversation_key)
            await self.send_text(session_webhook=session_webhook, text="✨ 已开启新会话，之前的上下文已清空。")
            return

        session_id = self._session_ids.get(conversation_key) or self._new_session_id(conversation_key)
        self._session_ids[conversation_key] = session_id
        await self._run_message_round(
            session_webhook=session_webhook,
            session_id=session_id,
            text=message.text,
            data=data,
        )

    async def _run_message_round(self, *, session_webhook: str, session_id: str, text: str, data: dict) -> None:
        active_card = await self._try_create_ai_card(data) if self._config.enable_ai_card else None
        streamed_parts: list[str] = []

        async def on_text_chunk(chunk: str) -> None:
            streamed_parts.append(chunk)
            if active_card is not None:
                await self._stream_ai_card(active_card, "".join(streamed_parts), finished=False)

        try:
            result = await self._bridge.run(
                agent_id=self._config.default_agent_id or "aworld",
                session_id=session_id,
                text=text,
                on_text_chunk=on_text_chunk,
            )
        except Exception as exc:
            await self._send_error_to_client(session_webhook=session_webhook, card=active_card, text=f"抱歉，调用 Agent 失败：{exc}")
            return

        final_text, pending_files = await self._process_local_media_links(result.text)
        display_text = final_text or ("✅ 媒体已发送" if pending_files else "（空响应）")

        if active_card is not None and await self._finish_ai_card(active_card, display_text):
            await self._send_pending_files(session_webhook, pending_files)
            return

        await self.send_text(session_webhook=session_webhook, text=display_text)
        await self._send_pending_files(session_webhook, pending_files)

    async def _try_create_ai_card(self, data: dict) -> AICardInstance | None:
        return None if not self._config.enable_ai_card else AICardInstance("card-id", "token")

    async def _stream_ai_card(self, card: AICardInstance, content: str, finished: bool) -> bool:
        return True

    async def _finish_ai_card(self, card: AICardInstance, content: str) -> bool:
        return await self._stream_ai_card(card, content, finished=True)

    async def _send_pending_files(self, session_webhook: str, pending_files: list[PendingFileMessage]) -> None:
        for item in pending_files:
            await self._http.post(
                session_webhook,
                json={
                    "msgtype": "file",
                    "file": {
                        "mediaId": item.media_id,
                        "fileName": item.file_name,
                        "fileType": item.file_type,
                    },
                },
            )
```

```python
# aworld_gateway/channels/dingding/connector.py
    async def _process_local_media_links(
        self,
        content: str,
    ) -> tuple[str, list[PendingFileMessage]]:
        if not self._config.enable_attachments or not content:
            return content, []

        pending_files: list[PendingFileMessage] = []
        for raw_match in re.findall(r"\[([^\]]+)\]\((attachment://[^)]+)\)", content):
            file_name, _raw_url = raw_match
            pending_files.append(
                PendingFileMessage(
                    media_id=f"mock-{file_name}",
                    file_name=file_name,
                    file_type=Path(file_name).suffix.lstrip(".") or "bin",
                )
            )
        cleaned = re.sub(r"\[[^\]]+\]\(attachment://[^)]+\)", "", content).strip()
        return cleaned, pending_files
```

- [ ] **Step 4: Run broad gateway verification**

Run: `pytest tests/gateway/test_dingding_config.py tests/gateway/test_dingding_bridge.py tests/gateway/test_dingding_adapter.py tests/gateway/test_dingding_connector.py tests/gateway/test_gateway_status_command.py tests/gateway/test_registry.py tests/gateway/test_runtime.py tests/test_gateway_cli.py -q`
Expected: PASS

- [ ] **Step 5: Run CLI smoke verification**

Run: `PYTHONPATH=aworld-cli/src:. python -m aworld_cli.main gateway channels list`
Expected: DingTalk row is present with `implemented=True`

Run: `PYTHONPATH=aworld-cli/src:. python -m aworld_cli.main gateway status`
Expected: DingTalk appears in `channels` with the correct `configured/running/degraded` state depending on env setup

- [ ] **Step 6: Commit**

```bash
git add aworld_gateway/channels/dingding/types.py aworld_gateway/channels/dingding/connector.py tests/gateway/test_dingding_connector.py tests/gateway/test_gateway_status_command.py tests/test_gateway_cli.py
git commit -m "feat: add dingtalk gateway attachments and ai cards"
```
