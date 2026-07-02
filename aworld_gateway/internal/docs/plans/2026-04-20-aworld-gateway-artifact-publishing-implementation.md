# Aworld Gateway Artifact Publishing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a gateway-local temporary artifact HTTP publishing capability, define `artifact://` reference handling, and integrate it into the DingTalk channel while preserving current absolute-path and `attachment://` compatibility.

**Architecture:** Keep artifact publishing outside `aworld/core` and outside agent business logic. Add an in-memory artifact service under `aworld_gateway/http/`, expose `GET /artifacts/{token}` on the existing gateway FastAPI app, inject the service through gateway runtime/registry into the DingTalk adapter, and only convert local artifact references into temporary HTTP URLs at the gateway edge.

**Tech Stack:** Python 3.10+, FastAPI, `FileResponse`, Pydantic v2, `httpx`, pytest, existing `aworld-cli gateway serve` bootstrap path, current DingTalk adapter/connector/runtime infrastructure.

---

## Planned File Structure

- `aworld_gateway/config/models.py`: add optional `public_base_url` to `GatewayServerConfig`
- `aworld_gateway/http/artifact_service.py`: in-memory token registry, safe path validation, URL building
- `aworld_gateway/http/artifact_router.py`: `GET /artifacts/{token}` FastAPI route
- `aworld_gateway/http/server.py`: register the artifact route on the gateway app
- `aworld_gateway/registry.py`: pass `artifact_service` into adapters that accept it
- `aworld_gateway/runtime.py`: accept optional `artifact_service` and forward it during adapter construction
- `aworld-cli/src/aworld_cli/gateway_cli.py`: resolve DingTalk workspace root, create `ArtifactService`, and pass it into both runtime and HTTP app
- `aworld_gateway/channels/dingding/adapter.py`: accept and forward `artifact_service`
- `aworld_gateway/channels/dingding/connector.py`: resolve `artifact://` and legacy local references, keep native DingTalk upload when possible, fall back to published HTTP URLs when needed
- `tests/gateway/test_gateway_http_app.py`: artifact route coverage
- `tests/gateway/test_config_loader.py`: `public_base_url` default and persisted config coverage
- `tests/gateway/test_runtime.py`: runtime injection coverage for `artifact_service`
- `tests/gateway/test_gateway_status_command.py`: CLI wiring coverage for artifact service bootstrapping
- `tests/gateway/test_dingding_adapter.py`: adapter-to-connector wiring coverage
- `tests/gateway/test_dingding_connector.py`: `artifact://`, absolute-path, and `attachment://` rewrite coverage for DingTalk

### Task 1: Build The Gateway-Local Artifact Service And HTTP Route

**Files:**
- Create: `aworld_gateway/http/artifact_service.py`
- Create: `aworld_gateway/http/artifact_router.py`
- Modify: `aworld_gateway/http/server.py`
- Modify: `tests/gateway/test_gateway_http_app.py`

- [ ] **Step 1: Write the failing artifact route tests**

```python
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.http.artifact_service import ArtifactService
from aworld_gateway.http.server import create_gateway_app


def test_gateway_http_app_serves_registered_artifact(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    html_file = artifact_root / "report.html"
    html_file.write_text("<h1>report</h1>", encoding="utf-8")

    service = ArtifactService(
        public_base_url="http://127.0.0.1:18888",
        allowed_roots=[artifact_root],
    )
    token = service.publish(html_file)

    app = create_gateway_app(
        runtime_status={"channels": {}},
        artifact_service=service,
    )
    client = TestClient(app)

    response = client.get(f"/artifacts/{token}")

    assert response.status_code == 200
    assert "report" in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_gateway_http_app_rejects_unknown_artifact_token(tmp_path: Path) -> None:
    service = ArtifactService(
        public_base_url="http://127.0.0.1:18888",
        allowed_roots=[tmp_path],
    )
    app = create_gateway_app(
        runtime_status={"channels": {}},
        artifact_service=service,
    )
    client = TestClient(app)

    response = client.get("/artifacts/missing-token")

    assert response.status_code == 404
    assert response.json()["detail"] == "Artifact not found."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_gateway_http_app.py -q`
Expected: FAIL because `ArtifactService`, artifact route registration, and the `artifact_service` parameter on `create_gateway_app()` do not exist yet.

- [ ] **Step 3: Implement the artifact service and route**

```python
# aworld_gateway/http/artifact_service.py
from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from time import time
from uuid import uuid4


@dataclass(frozen=True)
class PublishedArtifact:
    token: str
    path: Path
    content_type: str
    published_at: float


class ArtifactService:
    def __init__(
        self,
        *,
        public_base_url: str | None,
        allowed_roots: list[Path],
    ) -> None:
        self._public_base_url = (public_base_url or "").rstrip("/")
        self._allowed_roots = [root.expanduser().resolve() for root in allowed_roots]
        self._published: dict[str, PublishedArtifact] = {}

    def publish(self, path: str | Path) -> str:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(resolved)
        if not any(self._is_within_root(resolved, root) for root in self._allowed_roots):
            raise ValueError(f"Artifact path is outside allowed roots: {resolved}")

        token = uuid4().hex
        content_type = (
            mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        )
        self._published[token] = PublishedArtifact(
            token=token,
            path=resolved,
            content_type=content_type,
            published_at=time(),
        )
        return token

    def resolve(self, token: str) -> PublishedArtifact | None:
        artifact = self._published.get(token)
        if artifact is None:
            return None
        if not artifact.path.exists() or not artifact.path.is_file():
            self._published.pop(token, None)
            return None
        return artifact

    def build_external_url(self, token: str) -> str:
        if not self._public_base_url:
            raise ValueError("Artifact public_base_url is not configured.")
        return f"{self._public_base_url}/artifacts/{token}"

    @staticmethod
    def _is_within_root(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True
```

```python
# aworld_gateway/http/artifact_router.py
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from aworld_gateway.http.artifact_service import ArtifactService


def register_artifact_routes(
    app: FastAPI,
    *,
    artifact_service: ArtifactService | None,
) -> None:
    @app.get("/artifacts/{token}")
    async def read_artifact(token: str):
        if artifact_service is None:
            raise HTTPException(
                status_code=503,
                detail="Artifact service is not running.",
            )
        artifact = artifact_service.resolve(token)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")
        return FileResponse(
            path=str(artifact.path),
            media_type=artifact.content_type,
            filename=artifact.path.name,
            content_disposition_type="inline",
        )
```

```python
# aworld_gateway/http/server.py
from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI

from aworld_gateway.channels.telegram.webhook import register_telegram_webhook
from aworld_gateway.http.artifact_router import register_artifact_routes
from aworld_gateway.http.artifact_service import ArtifactService


def create_gateway_app(
    *,
    runtime_status: dict[str, object] | Callable[[], dict[str, object]],
    telegram_adapter: object | None = None,
    telegram_webhook_path: str = "/webhooks/telegram",
    artifact_service: ArtifactService | None = None,
) -> FastAPI:
    app = FastAPI(title="Aworld Gateway", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True}

    @app.get("/channels")
    async def channels() -> dict[str, object]:
        status_payload = runtime_status() if callable(runtime_status) else runtime_status
        channels_payload = status_payload.get("channels")
        if isinstance(channels_payload, dict):
            return channels_payload
        return {}

    register_artifact_routes(app, artifact_service=artifact_service)
    register_telegram_webhook(
        app,
        adapter=telegram_adapter,
        path=telegram_webhook_path,
    )
    return app
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_gateway_http_app.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld_gateway/http/artifact_service.py aworld_gateway/http/artifact_router.py aworld_gateway/http/server.py tests/gateway/test_gateway_http_app.py
git commit -m "feat: add gateway artifact http service"
```

### Task 2: Add Config, Runtime, Registry, And CLI Wiring For Artifact Publishing

**Files:**
- Modify: `aworld_gateway/config/models.py`
- Modify: `aworld_gateway/registry.py`
- Modify: `aworld_gateway/runtime.py`
- Modify: `aworld-cli/src/aworld_cli/gateway_cli.py`
- Modify: `tests/gateway/test_config_loader.py`
- Modify: `tests/gateway/test_runtime.py`
- Modify: `tests/gateway/test_gateway_status_command.py`

- [ ] **Step 1: Write the failing config and wiring tests**

```python
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld_cli import gateway_cli
from aworld_gateway.config import GatewayConfig, GatewayConfigLoader
from aworld_gateway.runtime import GatewayRuntime


def test_load_or_init_persists_public_base_url_default(tmp_path: Path) -> None:
    config = GatewayConfigLoader(base_dir=tmp_path).load_or_init()
    config_path = tmp_path / ".aworld" / "gateway" / "config.yaml"

    with config_path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    assert config.gateway.public_base_url is None
    assert raw["gateway"]["public_base_url"] is None


def test_runtime_passes_artifact_service_to_registry_builder() -> None:
    captured: dict[str, object] = {}

    class FakeAdapter:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    class FakeRegistry:
        def list_channels(self):
            return {"web": {"label": "Web", "implemented": True}}

        def get_meta(self, channel_id: str):
            if channel_id == "web":
                return {"label": "Web", "implemented": True}
            return None

        def is_configured(self, channel_id: str, config):
            return channel_id == "web"

        def build_adapter(self, channel_id: str, config, *, router=None, artifact_service=None):
            captured["artifact_service"] = artifact_service
            return FakeAdapter()

    config = GatewayConfig()
    config.channels.web.enabled = True
    artifact_service = object()
    runtime = GatewayRuntime(
        config=config,
        registry=FakeRegistry(),
        router=None,
        artifact_service=artifact_service,
    )

    asyncio.run(runtime.start())

    assert captured["artifact_service"] is artifact_service


def test_serve_gateway_builds_artifact_service_and_passes_it_through(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    cfg = GatewayConfig()
    cfg.gateway.host = "0.0.0.0"
    cfg.gateway.port = 18999
    cfg.gateway.public_base_url = "https://gateway.example.com"

    calls: dict[str, object] = {}

    async def fake_load_all_agents(*, remote_backends, local_dirs, agent_files):
        return []

    class FakeLoader:
        def __init__(self, *, base_dir):
            self.base_dir = base_dir

        def load_or_init(self):
            return cfg

    class FakeRuntime:
        def __init__(self, *, config, registry, router, artifact_service):
            calls["runtime_artifact_service"] = artifact_service

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        def status(self) -> dict[str, object]:
            return {"state": "running", "channels": {}}

        def get_started_channel(self, channel_name: str):
            return None

    def fake_create_gateway_app(
        *,
        runtime_status,
        telegram_adapter,
        telegram_webhook_path,
        artifact_service,
    ):
        calls["app_artifact_service"] = artifact_service
        return "fake-app"

    class FakeUvicornConfig:
        def __init__(self, *, app, host, port):
            calls["uvicorn_host"] = host
            calls["uvicorn_port"] = port

    class FakeUvicornServer:
        def __init__(self, config):
            self.config = config

        async def serve(self) -> None:
            raise RuntimeError("stop after serve")

    monkeypatch.setattr("aworld_cli.main.load_all_agents", fake_load_all_agents)
    monkeypatch.setattr(gateway_cli, "GatewayConfigLoader", FakeLoader)
    monkeypatch.setattr(gateway_cli, "GatewayRuntime", FakeRuntime)
    monkeypatch.setattr(gateway_cli, "create_gateway_app", fake_create_gateway_app)
    monkeypatch.setattr(gateway_cli.uvicorn, "Config", FakeUvicornConfig)
    monkeypatch.setattr(gateway_cli.uvicorn, "Server", FakeUvicornServer)

    with pytest.raises(RuntimeError, match="stop after serve"):
        asyncio.run(
            gateway_cli.serve_gateway(
                base_dir=tmp_path,
                remote_backends=None,
                local_dirs=None,
                agent_files=None,
            )
        )

    assert calls["runtime_artifact_service"] is calls["app_artifact_service"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_config_loader.py tests/gateway/test_runtime.py tests/gateway/test_gateway_status_command.py -q`
Expected: FAIL because `GatewayServerConfig.public_base_url` does not exist, `GatewayRuntime` does not accept `artifact_service`, and `serve_gateway()` does not construct or pass an artifact service.

- [ ] **Step 3: Implement config, runtime, registry, and CLI wiring**

```python
# aworld_gateway/config/models.py
class GatewayServerConfig(StrictConfigModel):
    host: str = "127.0.0.1"
    port: int = 18888
    public_base_url: str | None = None
```

```python
# aworld_gateway/registry.py
def build_adapter(
    self,
    channel_id: str,
    config: BaseChannelConfig,
    *,
    router: object | None = None,
    artifact_service: object | None = None,
) -> ChannelAdapter | None:
    adapter_class = self.get_adapter_class(channel_id)
    if adapter_class is None:
        return None
    init_params = inspect.signature(adapter_class.__init__).parameters
    kwargs: dict[str, object] = {}
    if router is not None and "router" in init_params:
        kwargs["router"] = router
    if artifact_service is not None and "artifact_service" in init_params:
        kwargs["artifact_service"] = artifact_service
    return adapter_class(config, **kwargs)
```

```python
# aworld_gateway/runtime.py
class GatewayRuntime:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        registry: ChannelRegistry | None = None,
        router: object | None = None,
        artifact_service: object | None = None,
    ) -> None:
        self._config = config
        self._registry = registry or ChannelRegistry()
        self._router = router
        self._artifact_service = artifact_service
        self._channel_states = self._build_base_channel_states()
        self._state = self._derive_runtime_state(self._channel_states)
        self._started_channels: dict[str, ChannelAdapter] = {}

    async def start(self) -> None:
        await self._stop_started_adapters()
        self._channel_states = self._build_base_channel_states()

        for channel_name, channel_state in self._channel_states.items():
            if not channel_state["enabled"]:
                continue
            if not channel_state["configured"]:
                continue
            if not channel_state["implemented"]:
                channel_state["state"] = "degraded"
                continue

            channel_config = getattr(self._config.channels, channel_name, None)
            adapter = self._registry.build_adapter(
                channel_name,
                channel_config,
                router=self._router,
                artifact_service=self._artifact_service,
            )
            if adapter is None:
                channel_state["state"] = "degraded"
                channel_state["error"] = "Channel adapter is not available."
                continue
```

```python
# aworld-cli/src/aworld_cli/gateway_cli.py
from aworld_gateway.http.artifact_service import ArtifactService


def _resolve_dingding_workspace_dir(*, base_dir: Path, config: GatewayConfig) -> Path:
    configured = str(config.channels.dingding.workspace_dir or "").strip()
    if configured:
        workspace_dir = Path(configured).expanduser()
        if not workspace_dir.is_absolute():
            workspace_dir = base_dir / workspace_dir
    else:
        workspace_dir = base_dir / ".aworld" / "gateway" / "dingding"
    workspace_dir = workspace_dir.resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    config.channels.dingding.workspace_dir = str(workspace_dir)
    return workspace_dir


def _build_artifact_service(*, base_dir: Path, config: GatewayConfig) -> ArtifactService:
    dingding_workspace_dir = _resolve_dingding_workspace_dir(
        base_dir=base_dir,
        config=config,
    )
    return ArtifactService(
        public_base_url=config.gateway.public_base_url,
        allowed_roots=[dingding_workspace_dir],
    )


async def serve_gateway(
    *,
    base_dir: Path | str | None,
    remote_backends: list[str] | None,
    local_dirs: list[str] | None,
    agent_files: list[str] | None,
) -> None:
    from aworld_cli.main import load_all_agents

    await load_all_agents(
        remote_backends=remote_backends,
        local_dirs=local_dirs,
        agent_files=agent_files,
    )

    resolved_base_dir = Path.cwd() if base_dir is None else Path(base_dir)
    config = GatewayConfigLoader(base_dir=resolved_base_dir).load_or_init()
    router = GatewayRouter(
        session_binding=SessionBinding(),
        agent_resolver=AgentResolver(default_agent_id=config.default_agent_id),
        agent_backend=LocalCliAgentBackend(),
    )
    artifact_service = _build_artifact_service(
        base_dir=resolved_base_dir,
        config=config,
    )
    runtime = GatewayRuntime(
        config=config,
        registry=ChannelRegistry(),
        router=router,
        artifact_service=artifact_service,
    )
    await runtime.start()
    telegram_adapter = runtime.get_started_channel("telegram")
    app = create_gateway_app(
        runtime_status=runtime.status(),
        telegram_adapter=telegram_adapter,
        telegram_webhook_path=config.channels.telegram.webhook_path,
        artifact_service=artifact_service,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_config_loader.py tests/gateway/test_runtime.py tests/gateway/test_gateway_status_command.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld_gateway/config/models.py aworld_gateway/registry.py aworld_gateway/runtime.py aworld-cli/src/aworld_cli/gateway_cli.py tests/gateway/test_config_loader.py tests/gateway/test_runtime.py tests/gateway/test_gateway_status_command.py
git commit -m "feat: wire gateway artifact publishing runtime"
```

### Task 3: Teach The DingTalk Channel To Publish Artifact URLs

**Files:**
- Modify: `aworld_gateway/channels/dingding/adapter.py`
- Modify: `aworld_gateway/channels/dingding/connector.py`
- Modify: `tests/gateway/test_dingding_adapter.py`
- Modify: `tests/gateway/test_dingding_connector.py`

- [ ] **Step 1: Write the failing DingTalk artifact compatibility tests**

```python
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_gateway.channels.dingding.adapter import DingdingChannelAdapter
from aworld_gateway.channels.dingding.connector import DingTalkConnector
from aworld_gateway.config import DingdingChannelConfig


class _FakeArtifactService:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def publish(self, path: str | Path) -> str:
        resolved = Path(path).resolve()
        self.paths.append(resolved)
        return f"token-{resolved.name}"

    def build_external_url(self, token: str) -> str:
        return f"https://gateway.example.com/artifacts/{token}"


def test_dingding_adapter_passes_artifact_service_to_connector(monkeypatch) -> None:
    stream_module = object()
    seen: dict[str, object] = {}

    class FakeConnector:
        def __init__(self, *, config, bridge, stream_module, artifact_service) -> None:
            seen["artifact_service"] = artifact_service

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(
        DingdingChannelAdapter,
        "_import_stream_module",
        lambda self: stream_module,
    )

    artifact_service = _FakeArtifactService()
    adapter = DingdingChannelAdapter(
        config=DingdingChannelConfig(),
        artifact_service=artifact_service,
        connector_cls=FakeConnector,
    )

    asyncio.run(adapter.start())

    assert seen["artifact_service"] is artifact_service


def test_connector_rewrites_artifact_scheme_to_gateway_url(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    artifact_dir = workspace_dir / "reports"
    artifact_dir.mkdir(parents=True)
    report_path = artifact_dir / "summary.html"
    report_path.write_text("<h1>summary</h1>", encoding="utf-8")

    connector = DingTalkConnector(
        config=DingdingChannelConfig(
            enable_attachments=True,
            workspace_dir=str(workspace_dir),
        ),
        bridge=object(),
        stream_module=object(),
        artifact_service=_FakeArtifactService(),
    )
    connector._get_oapi_access_token = lambda: asyncio.sleep(0, result=None)  # type: ignore[method-assign]

    cleaned, pending_files = asyncio.run(
        connector._process_local_media_links(
            "查看 [HTML 报告](artifact://reports/summary.html)"
        )
    )

    assert cleaned == (
        "查看 [HTML 报告]"
        "(https://gateway.example.com/artifacts/token-summary.html)"
    )
    assert pending_files == []


def test_connector_rewrites_legacy_attachment_url_when_native_upload_unavailable(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "report.txt"
    file_path.write_text("report", encoding="utf-8")

    connector = DingTalkConnector(
        config=DingdingChannelConfig(enable_attachments=True),
        bridge=object(),
        stream_module=object(),
        artifact_service=_FakeArtifactService(),
    )
    connector._get_oapi_access_token = lambda: asyncio.sleep(0, result=None)  # type: ignore[method-assign]

    cleaned, pending_files = asyncio.run(
        connector._process_local_media_links(
            f"下载附件 [report](attachment://{file_path})"
        )
    )

    assert cleaned == (
        "下载附件 [report]"
        "(https://gateway.example.com/artifacts/token-report.txt)"
    )
    assert pending_files == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_dingding_adapter.py tests/gateway/test_dingding_connector.py -q`
Expected: FAIL because `DingdingChannelAdapter` and `DingTalkConnector` do not accept `artifact_service`, and the connector does not rewrite `artifact://` or `attachment://` references into gateway URLs.

- [ ] **Step 3: Implement artifact-aware DingTalk delivery**

```python
# aworld_gateway/channels/dingding/adapter.py
from aworld_gateway.http.artifact_service import ArtifactService


class DingdingChannelAdapter(ChannelAdapter):
    def __init__(
        self,
        config: DingdingChannelConfig | None = None,
        *,
        bridge: AworldDingdingBridge | None = None,
        artifact_service: ArtifactService | None = None,
        connector_cls: type[DingTalkConnector] = DingTalkConnector,
    ) -> None:
        if config is None:
            config = DingdingChannelConfig()
        super().__init__(config)
        self._config = config
        self._bridge = bridge or AworldDingdingBridge()
        self._artifact_service = artifact_service
        self._connector_cls = connector_cls
        self._connector: DingTalkConnector | None = None

    async def start(self) -> None:
        stream_module = self._import_stream_module()
        self._connector = self._connector_cls(
            config=self._config,
            bridge=self._bridge,
            stream_module=stream_module,
            artifact_service=self._artifact_service,
        )
        await self._connector.start()
```

```python
# aworld_gateway/channels/dingding/connector.py
ARTIFACT_REF_RE = re.compile(
    r"(artifact://[^\s)\]>]+|attachment://[^\s)\]>]+|file://[^\s)\]>]+|MEDIA:[^\s)\]>]+|(?:/|~|[A-Za-z]:[\\/])[^\s)\]>]+)"
)


class DingTalkConnector:
    def __init__(
        self,
        *,
        config: DingdingChannelConfig,
        bridge: AworldDingdingBridge,
        stream_module,
        artifact_service=None,
        http_client: httpx.AsyncClient | None = None,
        thread_cls: type[threading.Thread] = threading.Thread,
    ) -> None:
        self._config = config
        self._bridge = bridge
        self._stream_module = stream_module
        self._artifact_service = artifact_service
        self._http = http_client or httpx.AsyncClient(timeout=60.0)
        self._thread_cls = thread_cls
        self._session_ids: dict[str, str] = {}
        self._client = None
        self._stream_thread: threading.Thread | None = None
        self._access_token: str | None = None
        self._access_token_expiry: float = 0.0
        self._oapi_access_token: str | None = None
        self._oapi_access_token_expiry: float = 0.0

    async def _process_local_media_links(
        self,
        content: str,
    ) -> tuple[str, list[PendingFileMessage]]:
        if not content:
            return content, []

        result = content
        pending_files: list[PendingFileMessage] = []
        oapi_token = (
            await self._get_oapi_access_token()
            if self._config.enable_attachments
            else None
        )

        for match in list(MARKDOWN_IMAGE_RE.finditer(result)):
            full_match, alt_text, raw_url = match.group(0), match.group(1), match.group(2)
            local_path = self._extract_local_file_path(raw_url)
            if not local_path:
                continue
            if oapi_token and self._is_image_path(local_path):
                media_id = await self._upload_local_file_to_dingtalk(
                    local_path,
                    "image",
                    oapi_token,
                )
                if media_id:
                    result = result.replace(full_match, f"![{alt_text}]({media_id})", 1)
                    continue
            published_url = self._publish_local_reference(raw_url)
            if published_url:
                result = result.replace(full_match, f"![{alt_text}]({published_url})", 1)

        for match in list(MARKDOWN_LINK_RE.finditer(result)):
            full_match, link_text, raw_url = match.group(0), match.group(1), match.group(2)
            local_path = self._extract_local_file_path(raw_url)
            if not local_path:
                continue
            if oapi_token:
                media_id = await self._upload_local_file_to_dingtalk(
                    local_path,
                    "file",
                    oapi_token,
                )
                if media_id:
                    pending_files.append(
                        PendingFileMessage(
                            media_id=media_id,
                            file_name=local_path.name,
                            file_type=local_path.suffix.lstrip(".").lower() or "bin",
                        )
                    )
                    result = result.replace(full_match, "", 1)
                    continue
            published_url = self._publish_local_reference(raw_url)
            if published_url:
                result = result.replace(full_match, f"[{link_text}]({published_url})", 1)

        for match in list(ARTIFACT_REF_RE.finditer(result)):
            raw_ref = match.group(0)
            published_url = self._publish_local_reference(raw_ref)
            if published_url:
                result = result.replace(raw_ref, published_url, 1)

        return self._cleanup_processed_text(result), pending_files

    def _publish_local_reference(self, raw_reference: str) -> str | None:
        if self._artifact_service is None:
            return None
        local_path = self._extract_local_file_path(raw_reference)
        if local_path is None:
            return None
        try:
            token = self._artifact_service.publish(local_path)
            return self._artifact_service.build_external_url(token)
        except Exception:
            return None

    def _extract_local_file_path(self, raw_url: str) -> Path | None:
        candidate = raw_url.strip().strip("<>").strip("'").strip('"')
        if not candidate:
            return None
        candidate = candidate.replace("\\ ", " ")
        workspace_dir = str(self._config.workspace_dir or "").strip()
        if candidate.startswith("artifact://"):
            relative_path = unquote(candidate[len("artifact://") :]).lstrip("/")
            if not workspace_dir or not relative_path:
                return None
            path = (Path(workspace_dir).expanduser() / relative_path).resolve()
            return path if path.exists() and path.is_file() else None
        if candidate.startswith("file://"):
            candidate = candidate[len("file://") :]
        elif candidate.startswith("MEDIA:"):
            candidate = candidate[len("MEDIA:") :]
        elif candidate.startswith("attachment://"):
            candidate = candidate[len("attachment://") :]
        candidate = unquote(candidate).strip()
        if not candidate or not LOCAL_PATH_RE.match(candidate):
            return None
        path = Path(candidate).expanduser()
        if not path.is_absolute() or not path.exists() or not path.is_file():
            return None
        return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_dingding_adapter.py tests/gateway/test_dingding_connector.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld_gateway/channels/dingding/adapter.py aworld_gateway/channels/dingding/connector.py tests/gateway/test_dingding_adapter.py tests/gateway/test_dingding_connector.py
git commit -m "feat: publish gateway artifacts for dingtalk"
```

## Self-Review

### Spec Coverage

- `GatewayServerConfig.public_base_url`: covered by Task 2.
- Gateway-local `ArtifactService` and `GET /artifacts/{token}`: covered by Task 1.
- Runtime/registry/CLI injection of `artifact_service`: covered by Task 2.
- DingTalk support for `artifact://`, absolute local paths, and `attachment://` compatibility: covered by Task 3.
- Keep native DingTalk media upload where richer delivery is available, otherwise fall back to published HTTP URL: covered by Task 3.
- Keep the feature outside `aworld/core`: enforced by file layout in Tasks 1-3.

### Placeholder Scan

- Verified this plan contains no placeholder instructions and no deferred implementation notes.

### Signature Consistency

- `create_gateway_app(runtime_status, telegram_adapter=None, telegram_webhook_path="/webhooks/telegram", artifact_service: ArtifactService | None = None)`
- `GatewayRuntime(config, registry=None, router=None, artifact_service: object | None = None)`
- `ChannelRegistry.build_adapter(channel_id, config, router=None, artifact_service: object | None = None)`
- `DingdingChannelAdapter(config=None, bridge=None, artifact_service: ArtifactService | None = None, connector_cls=DingTalkConnector)`
- `DingTalkConnector(config, bridge, stream_module, artifact_service=None, http_client=None, thread_cls=threading.Thread)`
- `GatewayServerConfig.public_base_url: str | None = None`

Plan complete and saved to `docs/superpowers/plans/2026-04-20-aworld-gateway-artifact-publishing-implementation.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
