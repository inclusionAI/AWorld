# Aworld Gateway Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase-1 `aworld_gateway` package and expose it through `aworld-cli` so gateway channels can route text messages into the default Aworld Agent, with full `telegram` support and registered-but-disabled skeletons for `web`, `dingding`, `feishu`, and `wecom`.

**Architecture:** Add a top-level `aworld_gateway` package that owns config loading, session binding, Agent resolution, router/runtime, channel registry, and HTTP/webhook serving. Keep `aworld-cli` as the operator-facing command surface by adding an `aworld gateway` subcommand that bootstraps agents through the existing CLI registry and then starts the gateway runtime.

**Tech Stack:** Python 3.10+, Pydantic v2, FastAPI, Uvicorn, pytest, `aworld-cli` LocalAgentRegistry/LocalAgentExecutor.

---

### Task 1: Scaffold `aworld_gateway` Core Models And Config Loader

**Files:**
- Create: `aworld_gateway/__init__.py`
- Create: `aworld_gateway/types.py`
- Create: `aworld_gateway/config/__init__.py`
- Create: `aworld_gateway/config/models.py`
- Create: `aworld_gateway/config/loader.py`
- Create: `tests/gateway/test_config_loader.py`

- [ ] **Step 1: Write the failing config-loader tests**

```python
from pathlib import Path

from aworld_gateway.config.loader import GatewayConfigLoader


def test_load_or_init_creates_default_gateway_config(tmp_path: Path) -> None:
    loader = GatewayConfigLoader(base_dir=tmp_path)

    cfg = loader.load_or_init()

    assert (tmp_path / ".aworld" / "gateway" / "config.yaml").exists()
    assert cfg.default_agent_id == "aworld"
    assert cfg.channels.telegram.enabled is False
    assert cfg.channels.web.enabled is False


def test_load_or_init_preserves_existing_channel_values(tmp_path: Path) -> None:
    gateway_dir = tmp_path / ".aworld" / "gateway"
    gateway_dir.mkdir(parents=True)
    (gateway_dir / "config.yaml").write_text(
        "\n".join(
            [
                "default_agent_id: ops_agent",
                "gateway:",
                "  host: 127.0.0.1",
                "  port: 18888",
                "channels:",
                "  telegram:",
                "    enabled: true",
                "    default_agent_id: ops_agent",
                "    bot_token_env: AWORLD_TELEGRAM_BOT_TOKEN",
                "    webhook_path: /webhooks/telegram",
            ]
        ),
        encoding="utf-8",
    )

    cfg = GatewayConfigLoader(base_dir=tmp_path).load_or_init()

    assert cfg.default_agent_id == "ops_agent"
    assert cfg.channels.telegram.enabled is True
    assert cfg.channels.telegram.default_agent_id == "ops_agent"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_config_loader.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'aworld_gateway'`

- [ ] **Step 3: Write the minimal core config implementation**

```python
# aworld_gateway/types.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class InboundEnvelope(BaseModel):
    channel: str
    account_id: str
    conversation_id: str
    conversation_type: Literal["dm", "group", "web"]
    sender_id: str
    sender_name: str | None = None
    message_id: str
    text: str
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OutboundEnvelope(BaseModel):
    channel: str
    account_id: str
    conversation_id: str
    reply_to_message_id: str | None = None
    text: str
    events: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
```

```python
# aworld_gateway/config/models.py
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class GatewayServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 18888


class BaseChannelConfig(BaseModel):
    enabled: bool = False
    default_agent_id: str | None = None


class TelegramChannelConfig(BaseChannelConfig):
    bot_token_env: str | None = "AWORLD_TELEGRAM_BOT_TOKEN"
    webhook_path: str = "/webhooks/telegram"


class PlaceholderChannelConfig(BaseChannelConfig):
    implemented: bool = False


class ChannelConfigMap(BaseModel):
    web: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    dingding: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)
    feishu: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)
    wecom: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)


class RouteRule(BaseModel):
    channel: str | None = None
    account_id: str | None = None
    conversation_type: Literal["dm", "group", "web"] | None = None
    conversation_id: str | None = None
    sender_id: str | None = None
    agent_id: str


class GatewayConfig(BaseModel):
    default_agent_id: str = "aworld"
    gateway: GatewayServerConfig = Field(default_factory=GatewayServerConfig)
    channels: ChannelConfigMap = Field(default_factory=ChannelConfigMap)
    routes: list[RouteRule] = Field(default_factory=list)
```

```python
# aworld_gateway/config/loader.py
from __future__ import annotations

from pathlib import Path

import yaml

from .models import GatewayConfig


class GatewayConfigLoader:
    def __init__(self, base_dir: Path | str | None = None) -> None:
        self.base_dir = Path(base_dir or Path.cwd())
        self.gateway_dir = self.base_dir / ".aworld" / "gateway"
        self.config_path = self.gateway_dir / "config.yaml"

    def load_or_init(self) -> GatewayConfig:
        self.gateway_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            cfg = GatewayConfig()
            self.config_path.write_text(
                yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False),
                encoding="utf-8",
            )
            return cfg

        payload = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        return GatewayConfig.model_validate(payload)
```

```python
# aworld_gateway/__init__.py
from .types import InboundEnvelope, OutboundEnvelope

__all__ = ["InboundEnvelope", "OutboundEnvelope"]
```

```python
# aworld_gateway/config/__init__.py
from .loader import GatewayConfigLoader
from .models import GatewayConfig

__all__ = ["GatewayConfigLoader", "GatewayConfig"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_config_loader.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld_gateway/__init__.py aworld_gateway/types.py aworld_gateway/config/__init__.py aworld_gateway/config/models.py aworld_gateway/config/loader.py tests/gateway/test_config_loader.py
git commit -m "feat: add aworld gateway config scaffolding"
```

### Task 2: Add Session Binding, Agent Resolution, And Execution Router

**Files:**
- Create: `aworld_gateway/session_binding.py`
- Create: `aworld_gateway/agent_resolver.py`
- Create: `aworld_gateway/router.py`
- Create: `tests/gateway/test_session_binding.py`
- Create: `tests/gateway/test_agent_resolver.py`
- Create: `tests/gateway/test_router.py`

- [ ] **Step 1: Write the failing binding/resolver/router tests**

```python
from aworld_gateway.agent_resolver import AgentResolver
from aworld_gateway.router import GatewayRouter
from aworld_gateway.session_binding import SessionBinding
from aworld_gateway.types import InboundEnvelope


def test_session_binding_uses_channel_account_and_conversation() -> None:
    session_id = SessionBinding().build(
        agent_id="aworld",
        channel="telegram",
        account_id="bot_main",
        conversation_type="dm",
        conversation_id="12345",
    )

    assert session_id == "gw:aworld:telegram:bot_main:dm:12345"


def test_agent_resolver_prefers_channel_default_over_global_default() -> None:
    resolver = AgentResolver(default_agent_id="aworld")

    agent_id = resolver.resolve(
        explicit_agent_id=None,
        session_agent_id=None,
        channel_default_agent_id="ops_agent",
        matched_route_agent_id=None,
    )

    assert agent_id == "ops_agent"


async def test_router_builds_outbound_text_from_executor_response() -> None:
    inbound = InboundEnvelope(
        channel="telegram",
        account_id="bot_main",
        conversation_id="12345",
        conversation_type="dm",
        sender_id="user_1",
        sender_name="User",
        message_id="msg-1",
        text="hello",
    )

    class FakeBackend:
        async def run_text(self, *, agent_id: str, session_id: str, text: str) -> str:
            assert agent_id == "aworld"
            assert session_id == "gw:aworld:telegram:bot_main:dm:12345"
            assert text == "hello"
            return "hi there"

    router = GatewayRouter(
        session_binding=SessionBinding(),
        agent_resolver=AgentResolver(default_agent_id="aworld"),
        agent_backend=FakeBackend(),
    )

    outbound = await router.handle_inbound(inbound, channel_default_agent_id=None)

    assert outbound.channel == "telegram"
    assert outbound.conversation_id == "12345"
    assert outbound.text == "hi there"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_session_binding.py tests/gateway/test_agent_resolver.py tests/gateway/test_router.py -q`
Expected: FAIL with import or attribute errors for missing binding/resolver/router classes

- [ ] **Step 3: Write the minimal binding/resolver/router implementation**

```python
# aworld_gateway/session_binding.py
from __future__ import annotations


class SessionBinding:
    def build(
        self,
        *,
        agent_id: str,
        channel: str,
        account_id: str,
        conversation_type: str,
        conversation_id: str,
    ) -> str:
        return f"gw:{agent_id}:{channel}:{account_id}:{conversation_type}:{conversation_id}"
```

```python
# aworld_gateway/agent_resolver.py
from __future__ import annotations


class AgentResolver:
    def __init__(self, default_agent_id: str) -> None:
        self.default_agent_id = default_agent_id

    def resolve(
        self,
        *,
        explicit_agent_id: str | None,
        session_agent_id: str | None,
        channel_default_agent_id: str | None,
        matched_route_agent_id: str | None,
    ) -> str:
        return (
            explicit_agent_id
            or session_agent_id
            or channel_default_agent_id
            or matched_route_agent_id
            or self.default_agent_id
        )
```

```python
# aworld_gateway/router.py
from __future__ import annotations

from aworld_cli.core.agent_registry import LocalAgentRegistry
from aworld_cli.executors.local import LocalAgentExecutor

from .agent_resolver import AgentResolver
from .session_binding import SessionBinding
from .types import InboundEnvelope, OutboundEnvelope


class LocalCliAgentBackend:
    async def run_text(self, *, agent_id: str, session_id: str, text: str) -> str:
        local_agent = LocalAgentRegistry.get_agent(agent_id)
        if local_agent is None:
            raise ValueError(f"Unknown agent: {agent_id}")

        swarm = await local_agent.get_swarm()
        executor = LocalAgentExecutor(
            swarm=swarm,
            context_config=local_agent.context_config,
            session_id=session_id,
            hooks=local_agent.hooks,
        )
        return await executor.chat(text)


class GatewayRouter:
    def __init__(
        self,
        *,
        session_binding: SessionBinding,
        agent_resolver: AgentResolver,
        agent_backend: LocalCliAgentBackend,
    ) -> None:
        self.session_binding = session_binding
        self.agent_resolver = agent_resolver
        self.agent_backend = agent_backend

    async def handle_inbound(
        self,
        inbound: InboundEnvelope,
        *,
        channel_default_agent_id: str | None,
        explicit_agent_id: str | None = None,
        session_agent_id: str | None = None,
        matched_route_agent_id: str | None = None,
    ) -> OutboundEnvelope:
        agent_id = self.agent_resolver.resolve(
            explicit_agent_id=explicit_agent_id,
            session_agent_id=session_agent_id,
            channel_default_agent_id=channel_default_agent_id,
            matched_route_agent_id=matched_route_agent_id,
        )
        session_id = self.session_binding.build(
            agent_id=agent_id,
            channel=inbound.channel,
            account_id=inbound.account_id,
            conversation_type=inbound.conversation_type,
            conversation_id=inbound.conversation_id,
        )
        text = await self.agent_backend.run_text(
            agent_id=agent_id,
            session_id=session_id,
            text=inbound.text,
        )
        return OutboundEnvelope(
            channel=inbound.channel,
            account_id=inbound.account_id,
            conversation_id=inbound.conversation_id,
            reply_to_message_id=inbound.message_id,
            text=text,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_session_binding.py tests/gateway/test_agent_resolver.py tests/gateway/test_router.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld_gateway/session_binding.py aworld_gateway/agent_resolver.py aworld_gateway/router.py tests/gateway/test_session_binding.py tests/gateway/test_agent_resolver.py tests/gateway/test_router.py
git commit -m "feat: add aworld gateway routing core"
```

### Task 3: Add Channel Base Classes, Registry, Runtime, And Skeleton Channels

**Files:**
- Create: `aworld_gateway/channels/__init__.py`
- Create: `aworld_gateway/channels/base.py`
- Create: `aworld_gateway/channels/web/__init__.py`
- Create: `aworld_gateway/channels/web/adapter.py`
- Create: `aworld_gateway/channels/dingding/__init__.py`
- Create: `aworld_gateway/channels/dingding/adapter.py`
- Create: `aworld_gateway/channels/feishu/__init__.py`
- Create: `aworld_gateway/channels/feishu/adapter.py`
- Create: `aworld_gateway/channels/wecom/__init__.py`
- Create: `aworld_gateway/channels/wecom/adapter.py`
- Create: `aworld_gateway/registry.py`
- Create: `aworld_gateway/runtime.py`
- Create: `tests/gateway/test_registry.py`
- Create: `tests/gateway/test_runtime.py`

- [ ] **Step 1: Write the failing registry/runtime tests**

```python
import pytest

from aworld_gateway.config.models import GatewayConfig
from aworld_gateway.registry import ChannelRegistry
from aworld_gateway.runtime import GatewayRuntime


def test_registry_lists_builtin_channels_with_implementation_flags() -> None:
    registry = ChannelRegistry()

    summary = registry.list_channels()

    assert summary["telegram"]["implemented"] is True
    assert summary["web"]["implemented"] is False
    assert summary["dingding"]["implemented"] is False


@pytest.mark.asyncio
async def test_runtime_marks_unimplemented_enabled_channel_as_degraded() -> None:
    cfg = GatewayConfig.model_validate(
        {
            "default_agent_id": "aworld",
            "channels": {
                "web": {"enabled": True},
                "telegram": {"enabled": False},
                "dingding": {"enabled": False},
                "feishu": {"enabled": False},
                "wecom": {"enabled": False},
            }
        }
    )

    runtime = GatewayRuntime(config=cfg, registry=ChannelRegistry(), router=None)
    await runtime.start()
    try:
        status = runtime.status()
        assert status["channels"]["web"]["state"] == "degraded"
    finally:
        await runtime.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_registry.py tests/gateway/test_runtime.py -q`
Expected: FAIL with missing registry/runtime implementation

- [ ] **Step 3: Write the minimal channel/runtime implementation**

```python
# aworld_gateway/channels/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from aworld_gateway.types import InboundEnvelope, OutboundEnvelope


@dataclass
class ChannelMeta:
    channel_id: str
    label: str
    implemented: bool


class ChannelAdapter(ABC):
    channel_id: str

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def send(self, outbound: OutboundEnvelope) -> None:
        raise NotImplementedError
```

```python
# aworld_gateway/channels/web/adapter.py
from __future__ import annotations

from aworld_gateway.channels.base import ChannelAdapter
from aworld_gateway.types import OutboundEnvelope


class WebChannelAdapter(ChannelAdapter):
    channel_id = "web"

    async def start(self) -> None:
        raise NotImplementedError("web channel is registered but not implemented in phase one")

    async def stop(self) -> None:
        return None

    async def send(self, outbound: OutboundEnvelope) -> None:
        raise NotImplementedError("web channel is registered but not implemented in phase one")
```

```python
# aworld_gateway/channels/dingding/adapter.py
from aworld_gateway.channels.web.adapter import WebChannelAdapter as DingTalkChannelAdapter
```

```python
# aworld_gateway/channels/feishu/adapter.py
from aworld_gateway.channels.web.adapter import WebChannelAdapter as FeishuChannelAdapter
```

```python
# aworld_gateway/channels/wecom/adapter.py
from aworld_gateway.channels.web.adapter import WebChannelAdapter as WeComChannelAdapter
```

```python
# aworld_gateway/registry.py
from __future__ import annotations

from aworld_gateway.channels.base import ChannelMeta
from aworld_gateway.channels.dingding.adapter import DingTalkChannelAdapter
from aworld_gateway.channels.feishu.adapter import FeishuChannelAdapter
from aworld_gateway.channels.web.adapter import WebChannelAdapter
from aworld_gateway.channels.wecom.adapter import WeComChannelAdapter


class ChannelRegistry:
    def __init__(self) -> None:
        self._meta = {
            "telegram": ChannelMeta("telegram", "Telegram", True),
            "web": ChannelMeta("web", "Web", False),
            "dingding": ChannelMeta("dingding", "DingTalk", False),
            "feishu": ChannelMeta("feishu", "Feishu", False),
            "wecom": ChannelMeta("wecom", "WeCom", False),
        }
        self._skeletons = {
            "web": WebChannelAdapter,
            "dingding": DingTalkChannelAdapter,
            "feishu": FeishuChannelAdapter,
            "wecom": WeComChannelAdapter,
        }

    def list_channels(self) -> dict[str, dict[str, object]]:
        return {
            key: {"label": meta.label, "implemented": meta.implemented}
            for key, meta in self._meta.items()
        }

    def get_meta(self, channel_id: str) -> ChannelMeta:
        return self._meta[channel_id]

    def build_skeleton(self, channel_id: str):
        builder = self._skeletons[channel_id]
        return builder()
```

```python
# aworld_gateway/runtime.py
from __future__ import annotations

from aworld_gateway.config.models import GatewayConfig
from aworld_gateway.registry import ChannelRegistry


class GatewayRuntime:
    def __init__(self, *, config: GatewayConfig, registry: ChannelRegistry, router) -> None:
        self.config = config
        self.registry = registry
        self.router = router
        self._states: dict[str, dict[str, object]] = {}
        self._started_channels: list[object] = []

    async def start(self) -> None:
        channel_cfgs = self.config.channels.model_dump()
        for channel_id, channel_cfg in channel_cfgs.items():
            meta = self.registry.get_meta(channel_id)
            state = "registered"
            if channel_cfg.get("enabled"):
                if not meta.implemented:
                    state = "degraded"
                else:
                    state = "configured"
            self._states[channel_id] = {
                "enabled": bool(channel_cfg.get("enabled")),
                "implemented": meta.implemented,
                "state": state,
            }

    async def stop(self) -> None:
        for adapter in reversed(self._started_channels):
            await adapter.stop()
        self._started_channels.clear()

    def status(self) -> dict[str, object]:
        return {"channels": self._states}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_registry.py tests/gateway/test_runtime.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld_gateway/channels/__init__.py aworld_gateway/channels/base.py aworld_gateway/channels/web/__init__.py aworld_gateway/channels/web/adapter.py aworld_gateway/channels/dingding/__init__.py aworld_gateway/channels/dingding/adapter.py aworld_gateway/channels/feishu/__init__.py aworld_gateway/channels/feishu/adapter.py aworld_gateway/channels/wecom/__init__.py aworld_gateway/channels/wecom/adapter.py aworld_gateway/registry.py aworld_gateway/runtime.py tests/gateway/test_registry.py tests/gateway/test_runtime.py
git commit -m "feat: add aworld gateway runtime skeleton"
```

### Task 4: Add `aworld gateway` CLI Entry And HTTP Status Surface

**Files:**
- Create: `aworld-cli/src/aworld_cli/gateway_cli.py`
- Create: `aworld_gateway/http/__init__.py`
- Create: `aworld_gateway/http/server.py`
- Create: `tests/test_gateway_cli.py`
- Modify: `aworld-cli/src/aworld_cli/main.py`

- [ ] **Step 1: Write the failing CLI/status tests**

```python
from argparse import Namespace

from aworld_cli.gateway_cli import build_gateway_parser


def test_gateway_parser_accepts_status_and_channels_list() -> None:
    parser = build_gateway_parser()

    status_args = parser.parse_args(["status"])
    list_args = parser.parse_args(["channels", "list"])

    assert status_args.gateway_action == "status"
    assert list_args.gateway_action == "channels"
    assert list_args.channels_action == "list"
```

```python
import pytest

from aworld_gateway.http.server import create_gateway_app


@pytest.mark.asyncio
async def test_gateway_http_app_exposes_health_and_channel_status() -> None:
    app = create_gateway_app(
        runtime_status={
            "channels": {
                "telegram": {"enabled": False, "implemented": True, "state": "registered"}
            }
        }
    )

    routes = {route.path for route in app.routes}

    assert "/healthz" in routes
    assert "/channels" in routes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_cli.py -q`
Expected: FAIL because `aworld_cli.gateway_cli` and gateway HTTP server do not exist

- [ ] **Step 3: Write the minimal CLI and HTTP implementation**

```python
# aworld-cli/src/aworld_cli/gateway_cli.py
from __future__ import annotations

import argparse
from pathlib import Path

from aworld_gateway.config.loader import GatewayConfigLoader
from aworld_gateway.registry import ChannelRegistry


def build_gateway_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aworld-cli gateway", description="Gateway commands")
    subparsers = parser.add_subparsers(dest="gateway_action", required=True)

    subparsers.add_parser("serve", help="Start the gateway service")
    subparsers.add_parser("status", help="Show gateway status")

    channels = subparsers.add_parser("channels", help="Channel operations")
    channel_subparsers = channels.add_subparsers(dest="channels_action", required=True)
    channel_subparsers.add_parser("list", help="List registered channels")
    return parser


def handle_gateway_status(base_dir: Path | str | None = None) -> dict[str, object]:
    cfg = GatewayConfigLoader(base_dir=base_dir).load_or_init()
    registry = ChannelRegistry()
    summary = registry.list_channels()
    return {
        "default_agent_id": cfg.default_agent_id,
        "channels": {
            channel_id: {
                "enabled": getattr(cfg.channels, channel_id).enabled,
                "implemented": summary[channel_id]["implemented"],
            }
            for channel_id in summary
        },
    }
```

```python
# aworld_gateway/http/server.py
from __future__ import annotations

from fastapi import FastAPI


def create_gateway_app(*, runtime_status: dict[str, object]) -> FastAPI:
    app = FastAPI(title="Aworld Gateway", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True}

    @app.get("/channels")
    async def channels() -> dict[str, object]:
        return runtime_status

    return app
```

```python
# aworld-cli/src/aworld_cli/main.py (targeted edits)
# 1. extend minimal special handling:
if minimal_args.command == "gateway":
    from .gateway_cli import build_gateway_parser, handle_gateway_status

    gateway_parser = build_gateway_parser()
    gateway_args = gateway_parser.parse_args(sys.argv[2:])

    if gateway_args.gateway_action == "status":
        print(handle_gateway_status())
        return
```

```python
# aworld-cli/src/aworld_cli/main.py (choices update)
choices=['interactive', 'list', 'serve', 'batch', 'batch-job', 'gateway']
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gateway_cli.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/gateway_cli.py aworld_gateway/http/__init__.py aworld_gateway/http/server.py aworld-cli/src/aworld_cli/main.py tests/test_gateway_cli.py
git commit -m "feat: add aworld gateway cli entry"
```

### Task 5: Implement Telegram Channel And Runtime Start/Send Behavior

**Files:**
- Create: `aworld_gateway/channels/telegram/__init__.py`
- Create: `aworld_gateway/channels/telegram/adapter.py`
- Create: `aworld_gateway/channels/telegram/webhook.py`
- Modify: `aworld_gateway/registry.py`
- Modify: `aworld_gateway/runtime.py`
- Modify: `aworld_gateway/http/server.py`
- Create: `tests/gateway/test_telegram_adapter.py`
- Create: `tests/gateway/test_gateway_http_app.py`

- [ ] **Step 1: Write the failing Telegram tests**

```python
import pytest

from aworld_gateway.channels.telegram.adapter import TelegramChannelAdapter
from aworld_gateway.types import OutboundEnvelope


@pytest.mark.asyncio
async def test_telegram_adapter_requires_token_env_when_enabled(monkeypatch) -> None:
    monkeypatch.delenv("AWORLD_TELEGRAM_BOT_TOKEN", raising=False)

    adapter = TelegramChannelAdapter(
        account_id="telegram_default",
        bot_token_env="AWORLD_TELEGRAM_BOT_TOKEN",
        webhook_path="/webhooks/telegram",
        router=None,
    )

    with pytest.raises(ValueError, match="AWORLD_TELEGRAM_BOT_TOKEN"):
        await adapter.start()


@pytest.mark.asyncio
async def test_telegram_adapter_posts_send_message(monkeypatch) -> None:
    calls = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            calls["url"] = url
            calls["json"] = json
            return FakeResponse()

    monkeypatch.setenv("AWORLD_TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setattr("aworld_gateway.channels.telegram.adapter.httpx.AsyncClient", FakeClient)

    adapter = TelegramChannelAdapter(
        account_id="telegram_default",
        bot_token_env="AWORLD_TELEGRAM_BOT_TOKEN",
        webhook_path="/webhooks/telegram",
        router=None,
    )
    await adapter.start()
    await adapter.send(
        OutboundEnvelope(
            channel="telegram",
            account_id="telegram_default",
            conversation_id="1001",
            reply_to_message_id="42",
            text="hello back",
        )
    )

    assert calls["url"].endswith("/bottoken-123/sendMessage")
    assert calls["json"]["chat_id"] == "1001"
    assert calls["json"]["text"] == "hello back"
```

```python
import pytest
from fastapi.testclient import TestClient

from aworld_gateway.http.server import create_gateway_app
from aworld_gateway.types import OutboundEnvelope


def test_telegram_webhook_route_invokes_router() -> None:
    seen = {}

    class FakeAdapter:
        channel_id = "telegram"

        async def handle_update(self, payload):
            seen["payload"] = payload

    app = create_gateway_app(runtime_status={"channels": {}}, telegram_adapter=FakeAdapter())
    client = TestClient(app)

    response = client.post(
        "/webhooks/telegram",
        json={"message": {"message_id": 1, "chat": {"id": 1001}, "from": {"id": 7}, "text": "hi"}},
    )

    assert response.status_code == 200
    assert seen["payload"]["message"]["text"] == "hi"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_telegram_adapter.py tests/gateway/test_gateway_http_app.py -q`
Expected: FAIL because Telegram adapter and webhook handling are incomplete

- [ ] **Step 3: Write the minimal Telegram implementation**

```python
# aworld_gateway/channels/telegram/adapter.py
from __future__ import annotations

import os

import httpx

from aworld_gateway.channels.base import ChannelAdapter
from aworld_gateway.types import InboundEnvelope, OutboundEnvelope


class TelegramChannelAdapter(ChannelAdapter):
    channel_id = "telegram"

    def __init__(self, *, account_id: str, bot_token_env: str, webhook_path: str, router) -> None:
        self.account_id = account_id
        self.bot_token_env = bot_token_env
        self.webhook_path = webhook_path
        self.router = router
        self._token: str | None = None

    async def start(self) -> None:
        token = os.getenv(self.bot_token_env or "")
        if not token:
            raise ValueError(f"Missing Telegram token env: {self.bot_token_env}")
        self._token = token

    async def stop(self) -> None:
        return None

    async def send(self, outbound: OutboundEnvelope) -> None:
        assert self._token is not None
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {"chat_id": outbound.conversation_id, "text": outbound.text}
        if outbound.reply_to_message_id:
            payload["reply_to_message_id"] = int(outbound.reply_to_message_id)
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()

    async def handle_update(self, payload: dict) -> None:
        message = payload.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        text = message.get("text") or ""
        if not text:
            return

        outbound = await self.router.handle_inbound(
            InboundEnvelope(
                channel="telegram",
                account_id=self.account_id,
                conversation_id=str(chat.get("id")),
                conversation_type="dm",
                sender_id=str(sender.get("id")),
                sender_name=sender.get("username") or sender.get("first_name"),
                message_id=str(message.get("message_id")),
                text=text,
                raw_payload=payload,
            ),
            channel_default_agent_id=None,
        )
        await self.send(outbound)
```

```python
# aworld_gateway/http/server.py
from __future__ import annotations

from fastapi import FastAPI, Request


def create_gateway_app(
    *,
    runtime_status: dict[str, object],
    telegram_adapter=None,
) -> FastAPI:
    app = FastAPI(title="Aworld Gateway", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True}

    @app.get("/channels")
    async def channels() -> dict[str, object]:
        return runtime_status

    @app.post("/webhooks/telegram")
    async def telegram_webhook(request: Request) -> dict[str, object]:
        payload = await request.json()
        if telegram_adapter is not None:
            await telegram_adapter.handle_update(payload)
        return {"ok": True}

    return app
```

```python
# aworld_gateway/registry.py (targeted additions)
from aworld_gateway.channels.telegram.adapter import TelegramChannelAdapter

# inside __init__
self._builders = {
    "telegram": TelegramChannelAdapter,
    "web": WebChannelAdapter,
    "dingding": DingTalkChannelAdapter,
    "feishu": FeishuChannelAdapter,
    "wecom": WeComChannelAdapter,
}
```

```python
# aworld_gateway/runtime.py (replace start logic)
from __future__ import annotations

from aworld_gateway.channels.telegram.adapter import TelegramChannelAdapter
from aworld_gateway.config.models import GatewayConfig
from aworld_gateway.registry import ChannelRegistry


class GatewayRuntime:
    def __init__(self, *, config: GatewayConfig, registry: ChannelRegistry, router) -> None:
        self.config = config
        self.registry = registry
        self.router = router
        self._states: dict[str, dict[str, object]] = {}
        self._started_channels: dict[str, object] = {}

    async def start(self) -> None:
        channel_cfgs = self.config.channels.model_dump()
        for channel_id, channel_cfg in channel_cfgs.items():
            meta = self.registry.get_meta(channel_id)
            state = "registered"
            if channel_cfg.get("enabled"):
                if not meta.implemented:
                    state = "degraded"
                else:
                    try:
                        if channel_id == "telegram":
                            adapter = TelegramChannelAdapter(
                                account_id="telegram_default",
                                bot_token_env=channel_cfg.get("bot_token_env"),
                                webhook_path=channel_cfg.get("webhook_path", "/webhooks/telegram"),
                                router=self.router,
                            )
                            await adapter.start()
                            self._started_channels[channel_id] = adapter
                            state = "running"
                        else:
                            state = "degraded"
                    except Exception as exc:
                        state = "degraded"
                        self._states[channel_id] = {
                            "enabled": True,
                            "implemented": meta.implemented,
                            "state": state,
                            "error": str(exc),
                        }
                        continue
            self._states[channel_id] = {
                "enabled": bool(channel_cfg.get("enabled")),
                "implemented": meta.implemented,
                "state": state,
            }

    async def stop(self) -> None:
        for adapter in reversed(list(self._started_channels.values())):
            await adapter.stop()
        self._started_channels.clear()

    def status(self) -> dict[str, object]:
        return {"channels": self._states}

    def get_channel(self, channel_id: str):
        return self._started_channels.get(channel_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_telegram_adapter.py tests/gateway/test_gateway_http_app.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld_gateway/channels/telegram/__init__.py aworld_gateway/channels/telegram/adapter.py aworld_gateway/channels/telegram/webhook.py aworld_gateway/registry.py aworld_gateway/runtime.py aworld_gateway/http/server.py tests/gateway/test_telegram_adapter.py tests/gateway/test_gateway_http_app.py
git commit -m "feat: add telegram gateway channel"
```

### Task 6: Wire `aworld gateway serve/status/channels list` End-To-End

**Files:**
- Modify: `aworld-cli/src/aworld_cli/gateway_cli.py`
- Modify: `aworld-cli/src/aworld_cli/main.py`
- Modify: `aworld_gateway/http/server.py`
- Create: `tests/gateway/test_gateway_status_command.py`

- [ ] **Step 1: Write the failing end-to-end CLI behavior test**

```python
from pathlib import Path

from aworld_cli.gateway_cli import handle_gateway_channels_list, handle_gateway_status


def test_gateway_status_reports_registered_enabled_and_implemented(tmp_path: Path) -> None:
    status = handle_gateway_status(base_dir=tmp_path)

    assert status["default_agent_id"] == "aworld"
    assert status["channels"]["telegram"]["enabled"] is False
    assert status["channels"]["telegram"]["implemented"] is True


def test_gateway_channels_list_contains_placeholder_channels(tmp_path: Path) -> None:
    rows = handle_gateway_channels_list(base_dir=tmp_path)

    assert set(rows) >= {"telegram", "web", "dingding", "feishu", "wecom"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_gateway_status_command.py -q`
Expected: FAIL because `handle_gateway_channels_list` and final status flow are incomplete

- [ ] **Step 3: Complete the CLI command handlers and serve flow**

```python
# aworld-cli/src/aworld_cli/gateway_cli.py
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import uvicorn

from aworld_gateway.agent_resolver import AgentResolver
from aworld_gateway.config.loader import GatewayConfigLoader
from aworld_gateway.http.server import create_gateway_app
from aworld_gateway.registry import ChannelRegistry
from aworld_gateway.router import GatewayRouter, LocalCliAgentBackend
from aworld_gateway.runtime import GatewayRuntime
from aworld_gateway.session_binding import SessionBinding


def handle_gateway_channels_list(base_dir: Path | str | None = None) -> dict[str, dict[str, object]]:
    cfg = GatewayConfigLoader(base_dir=base_dir).load_or_init()
    registry = ChannelRegistry()
    meta = registry.list_channels()
    return {
        channel_id: {
            "enabled": getattr(cfg.channels, channel_id).enabled,
            "implemented": meta[channel_id]["implemented"],
            "label": meta[channel_id]["label"],
        }
        for channel_id in meta
    }


async def serve_gateway(*, base_dir: Path | str | None, remote_backends, local_dirs, agent_files) -> None:
    from .main import load_all_agents

    await load_all_agents(
        remote_backends=remote_backends,
        local_dirs=local_dirs,
        agent_files=agent_files,
    )
    cfg = GatewayConfigLoader(base_dir=base_dir).load_or_init()
    router = GatewayRouter(
        session_binding=SessionBinding(),
        agent_resolver=AgentResolver(default_agent_id=cfg.default_agent_id),
        agent_backend=LocalCliAgentBackend(),
    )
    runtime = GatewayRuntime(config=cfg, registry=ChannelRegistry(), router=router)
    await runtime.start()
    app = create_gateway_app(
        runtime_status=runtime.status(),
        telegram_adapter=runtime.get_channel("telegram"),
    )
    config = uvicorn.Config(app=app, host=cfg.gateway.host, port=cfg.gateway.port)
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await runtime.stop()
```

```python
# aworld-cli/src/aworld_cli/main.py (targeted gateway branch)
if minimal_args.command == "gateway":
    from .gateway_cli import build_gateway_parser, handle_gateway_channels_list, handle_gateway_status, serve_gateway

    gateway_parser = build_gateway_parser()
    gateway_args = gateway_parser.parse_args(sys.argv[2:])

    if gateway_args.gateway_action == "status":
        print(handle_gateway_status())
        return
    if gateway_args.gateway_action == "channels" and gateway_args.channels_action == "list":
        print(handle_gateway_channels_list())
        return
    if gateway_args.gateway_action == "serve":
        asyncio.run(
            serve_gateway(
                base_dir=Path.cwd(),
                remote_backends=None,
                local_dirs=None,
                agent_files=None,
            )
        )
        return
```

- [ ] **Step 4: Run targeted tests and a smoke verification**

Run: `pytest tests/gateway/test_gateway_status_command.py tests/gateway/test_config_loader.py tests/gateway/test_session_binding.py tests/gateway/test_agent_resolver.py tests/gateway/test_router.py tests/gateway/test_registry.py tests/gateway/test_runtime.py tests/gateway/test_telegram_adapter.py tests/gateway/test_gateway_http_app.py tests/test_gateway_cli.py -q`
Expected: PASS

Run: `python -m aworld_cli.main gateway status`
Expected: prints a status dict containing `telegram`, `web`, `dingding`, `feishu`, and `wecom`

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/gateway_cli.py aworld-cli/src/aworld_cli/main.py aworld_gateway/http/server.py tests/gateway/test_gateway_status_command.py
git commit -m "feat: wire aworld gateway cli flow"
```
