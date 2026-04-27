# WeChat Channel ILink Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `wechat` gateway channel for personal WeChat / Weixin iLink Bot with long-poll intake, peer-scoped `context_token` reuse, final-text replies, and a minimal phase-2 media path without redesigning the global router.

**Architecture:** Keep the existing gateway control plane (`config`, `registry`, `runtime`, `router`, `status`) stable and introduce a channel-local subsystem under `aworld_gateway/channels/wechat/`. Phase 1 covers config/env validation, credential restore, long-poll intake, message-to-envelope translation, token caching, and final-text outbound send. The current extension adds a minimal phase-2 media slice inside the same connector boundary: inbound media downloads to local cache paths, attachment prompts plus metadata for router compatibility, and outbound media upload triggered by explicit local file references in final text. Happy ACP coexistence remains a separate-process deployment concern and does not change the ACP host.

**Tech Stack:** Python 3.10+, `asyncio`, `json`, `pathlib`, `pydantic`, `httpx` for Telegram only, optional `aiohttp` for WeChat long-poll/runtime sessions, existing `aworld_gateway` router/runtime models, `pytest`.

---

## File Structure

### New implementation files

- `aworld_gateway/channels/wechat/__init__.py`
  Exports the WeChat adapter.
- `aworld_gateway/channels/wechat/adapter.py`
  Lifecycle wrapper that instantiates the connector and exposes `send()`.
- `aworld_gateway/channels/wechat/account_store.py`
  Loads and saves account credentials under `.aworld/gateway/wechat/`.
- `aworld_gateway/channels/wechat/context_token_store.py`
  Keeps the latest `context_token` by `account_id + peer_id`.
- `aworld_gateway/channels/wechat/connector.py`
  Owns polling, retry, dedup, inbound translation, and final-text outbound sending.
- `tests/gateway/test_wechat_config.py`
  Config model and loader compatibility coverage.
- `tests/gateway/test_wechat_adapter.py`
  Adapter lifecycle and send delegation.
- `tests/gateway/test_wechat_connector.py`
  Text-only connector behavior, token cache, and polling translation.

### Existing files to modify

- `aworld_gateway/config/models.py`
  Add `WechatChannelConfig` and insert it into `ChannelConfigMap`.
- `aworld_gateway/config/__init__.py`
  Export `WechatChannelConfig`.
- `aworld_gateway/config/loader.py`
  Normalize any legacy `channels.wechat` placeholder-shaped payloads.
- `aworld_gateway/registry.py`
  Register `wechat`, label it, validate env-backed credentials, and build the adapter.
- `aworld_gateway/runtime.py`
  Allow `wechat` to inherit `default_agent_id` just like `dingding`.
- `tests/gateway/test_config_loader.py`
  Extend persisted default config expectations to include `wechat`.
- `tests/gateway/test_registry.py`
  Cover `wechat` list/implemented/configured behavior.
- `tests/gateway/test_runtime.py`
  Cover `wechat` degraded/running status.
- `tests/gateway/test_gateway_status_command.py`
  Ensure `wechat` appears in list/status responses.

### Explicitly avoided files in phase 1

- `aworld_gateway/router.py`
- `aworld_gateway/types.py`
- `aworld-cli/src/aworld_cli/acp/**`
- `aworld_gateway/channels/wecom/**`

The current router contract stays final-text only, and ACP coexistence remains a two-process deployment concern rather than an in-process integration change.

---

### Task 1: Register `wechat` In The Gateway Control Plane

**Files:**
- Modify: `aworld_gateway/config/models.py`
- Modify: `aworld_gateway/config/__init__.py`
- Modify: `aworld_gateway/config/loader.py`
- Modify: `aworld_gateway/registry.py`
- Modify: `aworld_gateway/runtime.py`
- Modify: `tests/gateway/test_config_loader.py`
- Modify: `tests/gateway/test_registry.py`
- Modify: `tests/gateway/test_runtime.py`
- Modify: `tests/gateway/test_gateway_status_command.py`
- Create: `tests/gateway/test_wechat_config.py`

- [ ] **Step 1: Write the failing control-plane tests**

```python
# tests/gateway/test_wechat_config.py
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import GatewayConfigLoader
from aworld_gateway.config import WechatChannelConfig


def test_wechat_config_defaults_are_text_phase_one_safe() -> None:
    cfg = WechatChannelConfig()
    assert cfg.enabled is False
    assert cfg.account_id_env == "AWORLD_WECHAT_ACCOUNT_ID"
    assert cfg.token_env == "AWORLD_WECHAT_TOKEN"
    assert cfg.base_url_env == "AWORLD_WECHAT_BASE_URL"
    assert cfg.cdn_base_url_env == "AWORLD_WECHAT_CDN_BASE_URL"
    assert cfg.dm_policy == "open"
    assert cfg.group_policy == "disabled"
    assert cfg.split_multiline_messages is False


def test_loader_persists_wechat_defaults(tmp_path: Path) -> None:
    base_dir = tmp_path / "project"
    base_dir.mkdir()
    config = GatewayConfigLoader(base_dir=base_dir).load_or_init()
    assert config.channels.wechat.enabled is False
    config_path = base_dir / ".aworld" / "gateway" / "config.yaml"
    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    assert raw["channels"]["wechat"]["enabled"] is False
```

```python
# tests/gateway/test_registry.py
def test_registry_lists_wechat_as_implemented_channel():
    summary = ChannelRegistry().list_channels()
    assert summary["wechat"]["implemented"] is True
```

```python
# tests/gateway/test_runtime.py
def test_runtime_start_degrades_when_enabled_wechat_is_not_configured(monkeypatch):
    monkeypatch.delenv("AWORLD_WECHAT_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("AWORLD_WECHAT_TOKEN", raising=False)
    config = GatewayConfig()
    config.channels.wechat.enabled = True
    runtime = GatewayRuntime(config=config, registry=ChannelRegistry(), router=None)
    asyncio.run(runtime.start())
    status = runtime.status()
    assert status["channels"]["wechat"]["state"] == "degraded"
```

- [ ] **Step 2: Run the targeted tests to verify `wechat` is missing**

Run: `python -m pytest tests/gateway/test_wechat_config.py tests/gateway/test_registry.py tests/gateway/test_runtime.py tests/gateway/test_gateway_status_command.py -q`

Expected: FAIL because `WechatChannelConfig` does not exist and registry/runtime do not know `wechat`.

- [ ] **Step 3: Add `WechatChannelConfig` and wire it through config/registry/runtime**

```python
# aworld_gateway/config/models.py
class WechatChannelConfig(BaseChannelConfig):
    account_id_env: Optional[str] = "AWORLD_WECHAT_ACCOUNT_ID"
    token_env: Optional[str] = "AWORLD_WECHAT_TOKEN"
    base_url_env: Optional[str] = "AWORLD_WECHAT_BASE_URL"
    cdn_base_url_env: Optional[str] = "AWORLD_WECHAT_CDN_BASE_URL"
    dm_policy: Literal["open", "allowlist", "disabled"] = "open"
    group_policy: Literal["open", "allowlist", "disabled"] = "disabled"
    allow_from: list[str] = Field(default_factory=list)
    group_allow_from: list[str] = Field(default_factory=list)
    split_multiline_messages: bool = False


class ChannelConfigMap(StrictConfigModel):
    web: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)
    dingding: DingdingChannelConfig = Field(default_factory=DingdingChannelConfig)
    wechat: WechatChannelConfig = Field(default_factory=WechatChannelConfig)
    feishu: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)
    wecom: PlaceholderChannelConfig = Field(default_factory=PlaceholderChannelConfig)
```

```python
# aworld_gateway/registry.py
from aworld_gateway.channels.wechat.adapter import WechatChannelAdapter
from aworld_gateway.config import WechatChannelConfig

"wechat": ChannelRegistration(
    metadata=ChannelMetadata(name="wechat", implemented=True),
    label="WeChat",
    adapter_class=WechatChannelAdapter,
),

if channel_id == "wechat":
    if not isinstance(config, WechatChannelConfig):
        return False
    if not config.account_id_env or not config.token_env:
        return False
    return bool(os.getenv(config.account_id_env)) and bool(os.getenv(config.token_env))
```

- [ ] **Step 4: Update loader and status/list tests for the new channel**

```python
# aworld_gateway/config/loader.py
wechat = channels.get("wechat")
if isinstance(wechat, dict):
    wechat.pop("implemented", None)
```

```python
# tests/gateway/test_gateway_status_command.py
assert set(rows) >= {"telegram", "web", "dingding", "wechat", "feishu", "wecom"}
```

- [ ] **Step 5: Run the control-plane tests and confirm they pass**

Run: `python -m pytest tests/gateway/test_config_loader.py tests/gateway/test_wechat_config.py tests/gateway/test_registry.py tests/gateway/test_runtime.py tests/gateway/test_gateway_status_command.py -q`

Expected: PASS

- [ ] **Step 6: Commit the control-plane slice**

```bash
git add aworld_gateway/config/models.py aworld_gateway/config/__init__.py aworld_gateway/config/loader.py aworld_gateway/registry.py aworld_gateway/runtime.py tests/gateway/test_config_loader.py tests/gateway/test_wechat_config.py tests/gateway/test_registry.py tests/gateway/test_runtime.py tests/gateway/test_gateway_status_command.py
git commit -m "feat: add wechat gateway control plane"
```

### Task 2: Add The WeChat Adapter, Account Store, And Context Token Store

**Files:**
- Create: `aworld_gateway/channels/wechat/__init__.py`
- Create: `aworld_gateway/channels/wechat/adapter.py`
- Create: `aworld_gateway/channels/wechat/account_store.py`
- Create: `aworld_gateway/channels/wechat/context_token_store.py`
- Create: `tests/gateway/test_wechat_adapter.py`

- [ ] **Step 1: Write the failing adapter/store tests**

```python
# tests/gateway/test_wechat_adapter.py
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.config import WechatChannelConfig
from aworld_gateway.types import OutboundEnvelope


class _FakeConnector:
    def __init__(self, *, config, router):
        self.config = config
        self.router = router
        self.started = False
        self.stopped = False
        self.send_calls = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send_text(self, *, chat_id: str, text: str, metadata: dict | None = None):
        self.send_calls.append((chat_id, text, metadata))
        return {"chat_id": chat_id, "text": text}


def test_wechat_adapter_start_builds_connector():
    from aworld_gateway.channels.wechat.adapter import WechatChannelAdapter

    adapter = WechatChannelAdapter(WechatChannelConfig(), connector_cls=_FakeConnector)
    asyncio.run(adapter.start())
    assert adapter._connector is not None
    assert adapter._connector.started is True


def test_wechat_adapter_send_requires_started_connector():
    from aworld_gateway.channels.wechat.adapter import WechatChannelAdapter

    adapter = WechatChannelAdapter(WechatChannelConfig(), connector_cls=_FakeConnector)
    with pytest.raises(RuntimeError, match="not started"):
        asyncio.run(adapter.send(OutboundEnvelope(channel="wechat", account_id="wechat", conversation_id="peer-1", text="hello")))
```

- [ ] **Step 2: Run the adapter tests to verify the package does not exist yet**

Run: `python -m pytest tests/gateway/test_wechat_adapter.py -q`

Expected: FAIL with `ModuleNotFoundError` for `aworld_gateway.channels.wechat`.

- [ ] **Step 3: Implement the adapter and stores**

```python
# aworld_gateway/channels/wechat/adapter.py
class WechatChannelAdapter(ChannelAdapter):
    def __init__(self, config: WechatChannelConfig | None = None, *, router: object | None = None, connector_cls: type[WechatConnector] = WechatConnector) -> None:
        ...

    @classmethod
    def metadata(cls) -> ChannelMetadata:
        return ChannelMetadata(name="wechat", implemented=True)

    async def start(self) -> None:
        self._connector = self._connector_cls(config=self._config, router=self._router)
        await self._connector.start()

    async def stop(self) -> None:
        if self._connector is not None:
            await self._connector.stop()

    async def send(self, envelope: OutboundEnvelope):
        if self._connector is None:
            raise RuntimeError("WeChat channel adapter is not started.")
        return await self._connector.send_text(chat_id=envelope.conversation_id, text=envelope.text, metadata=envelope.metadata)
```

```python
# aworld_gateway/channels/wechat/account_store.py
def load_account(root: Path, account_id: str) -> dict[str, str] | None: ...
def save_account(root: Path, *, account_id: str, token: str, base_url: str, user_id: str = "") -> None: ...
```

```python
# aworld_gateway/channels/wechat/context_token_store.py
class ContextTokenStore:
    def get(self, account_id: str, peer_id: str) -> str | None: ...
    def set(self, account_id: str, peer_id: str, token: str) -> None: ...
    def restore(self, account_id: str) -> None: ...
```

- [ ] **Step 4: Run the adapter tests and confirm the lifecycle slice passes**

Run: `python -m pytest tests/gateway/test_wechat_adapter.py -q`

Expected: PASS

- [ ] **Step 5: Commit the adapter/store slice**

```bash
git add aworld_gateway/channels/wechat/__init__.py aworld_gateway/channels/wechat/adapter.py aworld_gateway/channels/wechat/account_store.py aworld_gateway/channels/wechat/context_token_store.py tests/gateway/test_wechat_adapter.py
git commit -m "feat: add wechat adapter and token stores"
```

### Task 3: Implement The Phase-1 Text Connector

**Files:**
- Create: `aworld_gateway/channels/wechat/connector.py`
- Create: `tests/gateway/test_wechat_connector.py`

- [ ] **Step 1: Write the failing connector tests for text polling and token reuse**

```python
# tests/gateway/test_wechat_connector.py
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.types import OutboundEnvelope


class _FakeRouter:
    def __init__(self) -> None:
        self.calls = []

    async def handle_inbound(self, inbound, *, channel_default_agent_id):
        self.calls.append((inbound, channel_default_agent_id))
        return OutboundEnvelope(
            channel="wechat",
            account_id=inbound.account_id,
            conversation_id=inbound.conversation_id,
            reply_to_message_id=inbound.message_id,
            text=f"echo:{inbound.text}",
        )


def test_connector_process_message_caches_context_token_and_routes_text(monkeypatch, tmp_path: Path):
    from aworld_gateway.channels.wechat.connector import WechatConnector
    from aworld_gateway.config import WechatChannelConfig

    router = _FakeRouter()
    cfg = WechatChannelConfig(default_agent_id="aworld")
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")
    monkeypatch.setenv("AWORLD_WECHAT_BASE_URL", "https://ilink.example.test")

    connector = WechatConnector(config=cfg, router=router, storage_root=tmp_path)
    asyncio.run(connector.start())
    asyncio.run(
        connector._process_message(
            {
                "message_id": "m-1",
                "from_user_id": "user-1",
                "context_token": "ctx-1",
                "item_list": [{"type": 1, "text_item": {"text": "ping"}}],
            }
        )
    )
    assert router.calls[0][0].text == "ping"
    assert connector._token_store.get("wx-account", "user-1") == "ctx-1"


def test_connector_send_text_reuses_latest_context_token(monkeypatch, tmp_path: Path):
    from aworld_gateway.channels.wechat.connector import WechatConnector
    from aworld_gateway.config import WechatChannelConfig

    seen = {}

    async def fake_send_message(*, base_url, token, to, text, context_token, client_id, session):
        seen.update(
            {
                "base_url": base_url,
                "token": token,
                "to": to,
                "text": text,
                "context_token": context_token,
            }
        )
        return {"ret": 0}

    cfg = WechatChannelConfig()
    monkeypatch.setenv("AWORLD_WECHAT_ACCOUNT_ID", "wx-account")
    monkeypatch.setenv("AWORLD_WECHAT_TOKEN", "wx-token")
    monkeypatch.setenv("AWORLD_WECHAT_BASE_URL", "https://ilink.example.test")
    connector = WechatConnector(config=cfg, router=None, storage_root=tmp_path, send_message_func=fake_send_message)
    asyncio.run(connector.start())
    connector._token_store.set("wx-account", "user-1", "ctx-9")
    asyncio.run(connector.send_text(chat_id="user-1", text="pong"))
    assert seen["context_token"] == "ctx-9"
```

- [ ] **Step 2: Run connector tests to verify the connector is still missing**

Run: `python -m pytest tests/gateway/test_wechat_connector.py -q`

Expected: FAIL with `ModuleNotFoundError` or missing attributes for `WechatConnector`.

- [ ] **Step 3: Implement the text-only connector with injectable poll/send helpers**

```python
# aworld_gateway/channels/wechat/connector.py
class WechatConnector:
    ITEM_TEXT = 1
    LONG_POLL_TIMEOUT_MS = 35_000

    def __init__(..., storage_root: Path | None = None, get_updates_func=None, send_message_func=None) -> None:
        ...

    async def start(self) -> None:
        self._account_id = self._required_env(self._config.account_id_env)
        self._token = self._required_env(self._config.token_env)
        self._base_url = self._optional_env(self._config.base_url_env, default="https://ilinkai.weixin.qq.com")
        self._token_store.restore(self._account_id)

    async def _process_message(self, message: dict[str, object]) -> None:
        sender_id = str(message.get("from_user_id") or "").strip()
        text = self._extract_text(message.get("item_list") or [])
        context_token = str(message.get("context_token") or "").strip()
        if context_token:
            self._token_store.set(self._account_id, sender_id, context_token)
        if not text or self._router is None:
            return
        outbound = await self._router.handle_inbound(...)
        await self.send_text(chat_id=outbound.conversation_id, text=outbound.text, metadata=outbound.metadata)

    async def send_text(self, *, chat_id: str, text: str, metadata: dict | None = None):
        context_token = self._token_store.get(self._account_id, chat_id)
        return await self._send_message_func(...)
```

- [ ] **Step 4: Run connector tests and the affected gateway suite**

Run: `python -m pytest tests/gateway/test_wechat_connector.py tests/gateway/test_wechat_adapter.py tests/gateway/test_registry.py tests/gateway/test_runtime.py -q`

Expected: PASS

- [ ] **Step 5: Commit the connector slice**

```bash
git add aworld_gateway/channels/wechat/connector.py tests/gateway/test_wechat_connector.py
git commit -m "feat: add wechat text polling connector"
```

### Task 4: Verify The OpenSpec Change And Gateway Regression Surface

**Files:**
- Modify: `openspec/changes/wechat-channel-ilink/tasks.md`
- Modify: `openspec/changes/wechat-channel-ilink/implementation-plan.md`

- [ ] **Step 1: Mark completed OpenSpec tasks inline as implementation progresses**

```markdown
- [x] 1.1 Add `WechatChannelConfig` to `aworld_gateway/config/models.py` and wire it into `ChannelConfigMap`.
```

- [ ] **Step 2: Run the full targeted gateway regression**

Run: `python -m pytest tests/gateway/test_config_loader.py tests/gateway/test_wechat_config.py tests/gateway/test_wechat_adapter.py tests/gateway/test_wechat_connector.py tests/gateway/test_registry.py tests/gateway/test_runtime.py tests/gateway/test_gateway_status_command.py -q`

Expected: PASS

- [ ] **Step 3: Validate the OpenSpec change**

Run: `openspec validate wechat-channel-ilink`

Expected: `Change 'wechat-channel-ilink' is valid`

- [ ] **Step 4: Commit the finished phase-1 slice**

```bash
git add openspec/changes/wechat-channel-ilink/tasks.md openspec/changes/wechat-channel-ilink/implementation-plan.md
git commit -m "docs: track wechat phase one implementation progress"
```

---

## Self-Review

- Spec coverage: The plan covers the OpenSpec proposal/design/tasks scope for phase-1 text delivery, long-poll intake, token caching, control-plane wiring, and targeted validation. Deferred media, typing, QR login, and `wecom` work are intentionally excluded.
- Placeholder scan: No `TODO`/`TBD` placeholders remain in the plan steps. Deferred items are named explicitly as phase-two or phase-three work rather than left ambiguous.
- Type consistency: The plan consistently uses `WechatChannelConfig`, `WechatChannelAdapter`, `WechatConnector`, `ContextTokenStore`, and the existing `InboundEnvelope` / `OutboundEnvelope` contracts. No later task renames these interfaces.
