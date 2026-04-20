# Unified Framework Plugin System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a framework-level plugin system for AWorld that supports typed entrypoints, packaged assets, scoped state, command and hook contracts, and HUD as one concrete extension case.

**Architecture:** Introduce a new `aworld_cli.plugin_framework` package that owns manifest parsing, entrypoint descriptors, resource resolution, scoped state, and activation state. Adapt existing CLI plugin loading, command registration, hook execution, and HUD rendering to consume that framework instead of discovering plugin files ad hoc.

**Tech Stack:** Python, `dataclasses`, `pathlib`, `json`, `typing`, `prompt_toolkit`, existing AWorld hook runtime, `pytest`

---

### Task 1: Build Core Plugin Framework Primitives

**Files:**
- Create: `aworld-cli/src/aworld_cli/plugin_framework/__init__.py`
- Create: `aworld-cli/src/aworld_cli/plugin_framework/models.py`
- Create: `aworld-cli/src/aworld_cli/plugin_framework/manifest.py`
- Create: `aworld-cli/src/aworld_cli/plugin_framework/resources.py`
- Create: `aworld-cli/src/aworld_cli/plugin_framework/registry.py`
- Test: `tests/plugins/test_plugin_framework_manifest.py`
- Test: `tests/plugins/test_plugin_framework_resources.py`
- Create: `tests/fixtures/plugins/framework_minimal/.aworld-plugin/plugin.json`
- Create: `tests/fixtures/plugins/framework_minimal/commands/echo.md`

- [ ] **Step 1: Write the failing manifest and resource tests**

```python
# tests/plugins/test_plugin_framework_manifest.py
from pathlib import Path

from aworld_cli.plugin_framework.manifest import load_plugin_manifest


def test_load_plugin_manifest_exposes_typed_entrypoints():
    plugin_root = Path("tests/fixtures/plugins/framework_minimal").resolve()

    manifest = load_plugin_manifest(plugin_root)

    assert manifest.plugin_id == "framework-minimal"
    assert manifest.capabilities == {"commands", "hud"}
    assert manifest.entrypoints["commands"][0].entrypoint_id == "echo"
    assert manifest.entrypoints["commands"][0].target == "commands/echo.md"


def test_invalid_duplicate_entrypoint_ids_raise_value_error(tmp_path):
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    (plugin_root / ".aworld-plugin").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        '''
        {
          "id": "dup-plugin",
          "name": "dup-plugin",
          "version": "1.0.0",
          "entrypoints": {
            "commands": [
              {"id": "dup", "name": "dup", "target": "commands/one.md"},
              {"id": "dup", "name": "dup2", "target": "commands/two.md"}
            ]
          }
        }
        ''',
        encoding="utf-8",
    )

    try:
        load_plugin_manifest(plugin_root)
    except ValueError as exc:
        assert "duplicate entrypoint id" in str(exc).lower()
    else:
        raise AssertionError("expected duplicate entrypoint ids to fail")
```

```python
# tests/plugins/test_plugin_framework_resources.py
from pathlib import Path

from aworld_cli.plugin_framework.manifest import load_plugin_manifest
from aworld_cli.plugin_framework.resources import PluginResourceResolver


def test_resolve_packaged_asset_within_plugin_root():
    plugin_root = Path("tests/fixtures/plugins/framework_minimal").resolve()
    manifest = load_plugin_manifest(plugin_root)
    resolver = PluginResourceResolver(plugin_root=plugin_root, plugin_id=manifest.plugin_id)

    resolved = resolver.resolve_asset("commands/echo.md")

    assert resolved == plugin_root / "commands" / "echo.md"


def test_resolve_packaged_asset_rejects_escape(tmp_path):
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    resolver = PluginResourceResolver(plugin_root=plugin_root, plugin_id="escape-test")

    try:
        resolver.resolve_asset("../outside.txt")
    except ValueError as exc:
        assert "plugin root" in str(exc).lower()
    else:
        raise AssertionError("expected path traversal to fail")
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run: `pytest tests/plugins/test_plugin_framework_manifest.py tests/plugins/test_plugin_framework_resources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aworld_cli.plugin_framework'`

- [ ] **Step 3: Implement manifest models, typed entrypoints, and resource resolution**

```python
# aworld-cli/src/aworld_cli/plugin_framework/models.py
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass(frozen=True)
class PluginEntrypoint:
    entrypoint_id: str
    entrypoint_type: str
    name: Optional[str]
    target: Optional[str]
    scope: str = "workspace"
    visibility: str = "public"
    description: Optional[str] = None
    metadata: Dict[str, object] = field(default_factory=dict)
    permissions: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    capabilities: Set[str]
    entrypoints: Dict[str, List[PluginEntrypoint]]
    plugin_root: str
```

```python
# aworld-cli/src/aworld_cli/plugin_framework/manifest.py
import json
from pathlib import Path

from .models import PluginEntrypoint, PluginManifest


def load_plugin_manifest(plugin_root: Path) -> PluginManifest:
    manifest_path = plugin_root / ".aworld-plugin" / "plugin.json"
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    entrypoints = {}
    capabilities = set()

    for entrypoint_type, items in (raw.get("entrypoints") or {}).items():
        seen_ids = set()
        parsed_items = []
        for item in items:
            entrypoint_id = item["id"]
            if entrypoint_id in seen_ids:
                raise ValueError(f"duplicate entrypoint id: {entrypoint_type}:{entrypoint_id}")
            seen_ids.add(entrypoint_id)
            parsed_items.append(
                PluginEntrypoint(
                    entrypoint_id=entrypoint_id,
                    entrypoint_type=entrypoint_type,
                    name=item.get("name"),
                    target=item.get("target"),
                    scope=item.get("scope", "workspace"),
                    visibility=item.get("visibility", "public"),
                    description=item.get("description"),
                    metadata=item.get("metadata", {}),
                    permissions=item.get("permissions", {}),
                )
            )
        entrypoints[entrypoint_type] = parsed_items
        capabilities.add(entrypoint_type)

    return PluginManifest(
        plugin_id=raw["id"],
        name=raw.get("name", raw["id"]),
        version=raw["version"],
        capabilities=capabilities,
        entrypoints=entrypoints,
        plugin_root=str(plugin_root),
    )
```

```python
# aworld-cli/src/aworld_cli/plugin_framework/resources.py
from pathlib import Path


class PluginResourceResolver:
    def __init__(self, plugin_root: Path, plugin_id: str):
        self.plugin_root = plugin_root.resolve()
        self.plugin_id = plugin_id

    def resolve_asset(self, relative_path: str) -> Path:
        resolved = (self.plugin_root / relative_path).resolve()
        if self.plugin_root not in resolved.parents and resolved != self.plugin_root:
            raise ValueError(f"asset path escapes plugin root for {self.plugin_id}: {relative_path}")
        return resolved
```

- [ ] **Step 4: Run the tests and verify they pass**

Run: `pytest tests/plugins/test_plugin_framework_manifest.py tests/plugins/test_plugin_framework_resources.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/plugin_framework \
  tests/plugins/test_plugin_framework_manifest.py \
  tests/plugins/test_plugin_framework_resources.py \
  tests/fixtures/plugins/framework_minimal
git commit -m "feat: add plugin framework primitives"
```

### Task 2: Add Scoped Plugin State And Compatibility Discovery

**Files:**
- Create: `aworld-cli/src/aworld_cli/plugin_framework/state.py`
- Create: `aworld-cli/src/aworld_cli/plugin_framework/discovery.py`
- Modify: `aworld-cli/src/aworld_cli/core/plugin_manager.py`
- Modify: `aworld-cli/src/aworld_cli/runtime/loaders.py`
- Modify: `aworld-cli/src/aworld_cli/runtime/cli.py`
- Test: `tests/plugins/test_plugin_framework_discovery.py`
- Test: `tests/plugins/test_plugin_framework_state.py`
- Create: `tests/fixtures/plugins/legacy_agents_only/agents/demo/agent.yaml`
- Create: `tests/fixtures/plugins/legacy_agents_only/skills/demo/SKILL.md`

- [ ] **Step 1: Write failing tests for scoped state and legacy bridge discovery**

```python
# tests/plugins/test_plugin_framework_state.py
from pathlib import Path

from aworld_cli.plugin_framework.state import PluginStateStore


def test_session_state_is_shared_within_same_plugin_scope(tmp_path):
    store = PluginStateStore(base_dir=tmp_path)

    first = store.session_state("ralph-like", "session-1")
    first.write_text('{"iteration": 1}', encoding="utf-8")

    second = store.session_state("ralph-like", "session-1")
    assert second.read_text(encoding="utf-8") == '{"iteration": 1}'


def test_workspace_state_isolated_by_workspace_slug(tmp_path):
    store = PluginStateStore(base_dir=tmp_path)

    alpha = store.workspace_state("plugin-a", "/tmp/workspace-alpha")
    beta = store.workspace_state("plugin-a", "/tmp/workspace-beta")

    assert alpha != beta
```

```python
# tests/plugins/test_plugin_framework_discovery.py
from pathlib import Path

from aworld_cli.plugin_framework.discovery import discover_plugins


def test_discover_manifest_and_legacy_plugins():
    roots = [
        Path("tests/fixtures/plugins/framework_minimal").resolve(),
        Path("tests/fixtures/plugins/legacy_agents_only").resolve(),
    ]

    discovered = discover_plugins(roots)
    plugin_ids = {plugin.manifest.plugin_id for plugin in discovered}

    assert "framework-minimal" in plugin_ids
    assert "legacy_agents_only" in plugin_ids
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/plugins/test_plugin_framework_state.py tests/plugins/test_plugin_framework_discovery.py -v`
Expected: FAIL with `ImportError` for `PluginStateStore` and `discover_plugins`

- [ ] **Step 3: Implement scoped state storage and compatibility discovery**

```python
# aworld-cli/src/aworld_cli/plugin_framework/state.py
import hashlib
from pathlib import Path


class PluginStateStore:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _scope_dir(self, scope: str, plugin_id: str, key: str) -> Path:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        path = self.base_dir / scope / plugin_id / digest
        path.mkdir(parents=True, exist_ok=True)
        return path

    def session_state(self, plugin_id: str, session_id: str) -> Path:
        return self._scope_dir("session", plugin_id, session_id) / "state.json"

    def workspace_state(self, plugin_id: str, workspace_path: str) -> Path:
        return self._scope_dir("workspace", plugin_id, workspace_path) / "state.json"
```

```python
# aworld-cli/src/aworld_cli/plugin_framework/discovery.py
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from .manifest import load_plugin_manifest
from .models import PluginManifest


@dataclass(frozen=True)
class DiscoveredPlugin:
    manifest: PluginManifest
    source: str


def discover_plugins(roots: Iterable[Path]) -> List[DiscoveredPlugin]:
    discovered = []
    for root in roots:
        if (root / ".aworld-plugin" / "plugin.json").exists():
            discovered.append(DiscoveredPlugin(manifest=load_plugin_manifest(root), source="manifest"))
            continue

        capabilities = []
        if (root / "agents").exists():
            capabilities.append("agents")
        if (root / "skills").exists():
            capabilities.append("skills")
        if not capabilities:
            continue

        manifest = PluginManifest(
            plugin_id=root.name,
            name=root.name,
            version="0.0.0-legacy",
            capabilities=set(capabilities),
            entrypoints={},
            plugin_root=str(root.resolve()),
        )
        discovered.append(DiscoveredPlugin(manifest=manifest, source="legacy"))
    return discovered
```

- [ ] **Step 4: Wire discovery into existing plugin manager and runtime loader, then run tests**

Run: `pytest tests/plugins/test_plugin_framework_state.py tests/plugins/test_plugin_framework_discovery.py tests/test_slash_commands.py -v`
Expected: PASS for the new tests, and existing slash-command tests remain green

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/plugin_framework/state.py \
  aworld-cli/src/aworld_cli/plugin_framework/discovery.py \
  aworld-cli/src/aworld_cli/core/plugin_manager.py \
  aworld-cli/src/aworld_cli/runtime/loaders.py \
  aworld-cli/src/aworld_cli/runtime/cli.py \
  tests/plugins/test_plugin_framework_state.py \
  tests/plugins/test_plugin_framework_discovery.py \
  tests/fixtures/plugins/legacy_agents_only
git commit -m "feat: add plugin discovery and scoped state"
```

### Task 3: Implement Plugin Command Entrypoints

**Files:**
- Create: `aworld-cli/src/aworld_cli/plugin_framework/commands.py`
- Modify: `aworld-cli/src/aworld_cli/core/command_system.py`
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Test: `tests/plugins/test_plugin_commands.py`
- Modify: `tests/test_slash_commands.py`
- Create: `tests/fixtures/plugins/code_review_like/.aworld-plugin/plugin.json`
- Create: `tests/fixtures/plugins/code_review_like/commands/code-review.md`

- [ ] **Step 1: Write failing tests for plugin command registration and command-specific tool policy**

```python
# tests/plugins/test_plugin_commands.py
from pathlib import Path

from aworld_cli.core.command_system import CommandContext, CommandRegistry
from aworld_cli.plugin_framework.commands import register_plugin_commands
from aworld_cli.plugin_framework.discovery import discover_plugins


def test_register_plugin_command_from_manifest():
    plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    CommandRegistry.clear()
    register_plugin_commands([plugin])

    command = CommandRegistry.get("code-review")
    assert command is not None
    assert command.description == "Review the current pull request"
    assert "gh pr view" in command.allowed_tools[0]


async def test_plugin_prompt_command_reads_packaged_prompt():
    plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    CommandRegistry.clear()
    register_plugin_commands([plugin])
    command = CommandRegistry.get("code-review")
    prompt = await command.get_prompt(CommandContext(cwd=str(plugin_root), user_args="--comment"))

    assert "Provide a code review for the given pull request." in prompt
    assert "--comment" in prompt
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/plugins/test_plugin_commands.py tests/test_slash_commands.py -v`
Expected: FAIL because `register_plugin_commands` does not exist and plugin commands are not in the registry

- [ ] **Step 3: Implement a plugin command adapter backed by typed entrypoints**

```python
# aworld-cli/src/aworld_cli/plugin_framework/commands.py
from pathlib import Path

from aworld_cli.core.command_system import Command, CommandRegistry, CommandContext

from .resources import PluginResourceResolver


class PluginPromptCommand(Command):
    def __init__(self, plugin, entrypoint):
        self._plugin = plugin
        self._entrypoint = entrypoint
        self._resolver = PluginResourceResolver(Path(plugin.manifest.plugin_root), plugin.manifest.plugin_id)

    @property
    def name(self) -> str:
        return self._entrypoint.name or self._entrypoint.entrypoint_id

    @property
    def description(self) -> str:
        return self._entrypoint.description or ""

    @property
    def allowed_tools(self) -> list[str]:
        tools = self._entrypoint.permissions.get("allowed_tools", [])
        return [str(item) for item in tools]

    async def get_prompt(self, context: CommandContext) -> str:
        prompt_path = self._resolver.resolve_asset(self._entrypoint.target)
        prompt = prompt_path.read_text(encoding="utf-8")
        return f"{prompt}\n\nUser args: {context.user_args}".strip()


def register_plugin_commands(plugins) -> None:
    for plugin in plugins:
        for entrypoint in plugin.manifest.entrypoints.get("commands", []):
            if entrypoint.visibility == "hidden":
                continue
            CommandRegistry.register(PluginPromptCommand(plugin, entrypoint))
```

- [ ] **Step 4: Update command registry metadata and completer integration, then run tests**

Run: `pytest tests/plugins/test_plugin_commands.py tests/test_slash_commands.py -v`
Expected: PASS, including the existing completion and registration assertions

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/plugin_framework/commands.py \
  aworld-cli/src/aworld_cli/core/command_system.py \
  aworld-cli/src/aworld_cli/console.py \
  tests/plugins/test_plugin_commands.py \
  tests/test_slash_commands.py \
  tests/fixtures/plugins/code_review_like
git commit -m "feat: add plugin command entrypoints"
```

### Task 4: Implement Plugin Hook Entrypoints And Session-Control Results

**Files:**
- Create: `aworld-cli/src/aworld_cli/plugin_framework/hooks.py`
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Modify: `aworld-cli/src/aworld_cli/runtime/base.py`
- Test: `tests/plugins/test_plugin_hooks.py`
- Modify: `tests/test_cli_user_input_hooks.py`
- Create: `tests/fixtures/plugins/ralph_like/.aworld-plugin/plugin.json`
- Create: `tests/fixtures/plugins/ralph_like/hooks/stop_hook.py`

- [x] **Step 1: Write failing tests for plugin hooks that rewrite input and block termination with follow-up content**

```python
# tests/plugins/test_plugin_hooks.py
from pathlib import Path

from aworld_cli.plugin_framework.discovery import discover_plugins
from aworld_cli.plugin_framework.hooks import load_plugin_hooks


def test_load_plugin_hook_entrypoints():
    plugin_root = Path("tests/fixtures/plugins/ralph_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    hooks = load_plugin_hooks([plugin])

    assert "stop" in hooks
    assert hooks["stop"][0].entrypoint_id == "loop-stop"


@pytest.mark.asyncio
async def test_stop_hook_can_block_and_continue_session(tmp_path):
    plugin_root = Path("tests/fixtures/plugins/ralph_like").resolve()
    plugin = discover_plugins([plugin_root])[0]
    hooks = load_plugin_hooks([plugin])

    result = await hooks["stop"][0].run(
        event={"transcript_path": str(tmp_path / "transcript.jsonl")},
        state={"iteration": 1, "prompt": "keep going"},
    )

    assert result.action == "block_and_continue"
    assert result.follow_up_prompt == "keep going"
```

- [x] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/plugins/test_plugin_hooks.py tests/test_cli_user_input_hooks.py -v`
Expected: FAIL because plugin hook adapters and typed hook results do not exist

- [x] **Step 3: Implement typed hook results and plugin hook loading**

```python
# aworld-cli/src/aworld_cli/plugin_framework/hooks.py
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from .resources import PluginResourceResolver


@dataclass(frozen=True)
class PluginHookResult:
    action: str
    reason: str | None = None
    updated_input: str | None = None
    follow_up_prompt: str | None = None
    system_message: str | None = None


class PluginHookAdapter:
    def __init__(self, plugin, entrypoint):
        self.plugin = plugin
        self.entrypoint = entrypoint
        self.resolver = PluginResourceResolver(Path(plugin.manifest.plugin_root), plugin.manifest.plugin_id)

    async def run(self, event: dict, state: dict) -> PluginHookResult:
        hook_path = self.resolver.resolve_asset(self.entrypoint.target)
        spec = spec_from_file_location(f"{self.plugin.manifest.plugin_id}_{self.entrypoint.entrypoint_id}", hook_path)
        module = module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        payload = module.handle_event(event=event, state=state)
        return PluginHookResult(**payload)


def load_plugin_hooks(plugins):
    loaded = {}
    for plugin in plugins:
        for entrypoint in plugin.manifest.entrypoints.get("hooks", []):
            hook_point = str(entrypoint.metadata["hook_point"])
            loaded.setdefault(hook_point, []).append(PluginHookAdapter(plugin, entrypoint))
    return loaded
```

- [x] **Step 4: Adapt CLI stop and user-input flows to consume typed plugin hook results, then run tests**

Run: `pytest tests/plugins/test_plugin_hooks.py tests/test_cli_user_input_hooks.py tests/hooks/test_command_hook_wrapper.py -v`
Expected: PASS, and existing hook wrapper tests remain green

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/plugin_framework/hooks.py \
  aworld-cli/src/aworld_cli/console.py \
  aworld-cli/src/aworld_cli/runtime/base.py \
  tests/plugins/test_plugin_hooks.py \
  tests/test_cli_user_input_hooks.py \
  tests/fixtures/plugins/ralph_like
git commit -m "feat: add plugin hook entrypoints"
```

### Task 5: Implement HUD Providers As A Plugin Case

**Files:**
- Create: `aworld-cli/src/aworld_cli/plugin_framework/hud.py`
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Modify: `aworld-cli/src/aworld_cli/runtime/base.py`
- Test: `tests/plugins/test_plugin_hud.py`
- Create: `tests/fixtures/plugins/hud_like/.aworld-plugin/plugin.json`
- Create: `tests/fixtures/plugins/hud_like/hud/status.py`

- [x] **Step 1: Write failing tests for HUD context snapshots and line-provider ordering**

```python
# tests/plugins/test_plugin_hud.py
from pathlib import Path

from aworld_cli.plugin_framework.discovery import discover_plugins
from aworld_cli.plugin_framework.hud import collect_hud_lines


def test_collect_hud_lines_orders_by_section_and_priority():
    plugin_root = Path("tests/fixtures/plugins/hud_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    lines = collect_hud_lines(
        plugins=[plugin],
        context={
            "workspace": {"name": "aworld"},
            "session": {"agent": "developer", "mode": "Chat"},
            "notifications": {"cron_unread": 0},
        },
    )

    assert [line.section for line in lines] == ["session", "custom"]
    assert lines[0].text.startswith("Agent:")
```

- [x] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/plugins/test_plugin_hud.py -v`
Expected: FAIL because `collect_hud_lines` does not exist

- [x] **Step 3: Implement HUD snapshot models and line-provider adapters**

```python
# aworld-cli/src/aworld_cli/plugin_framework/hud.py
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from .resources import PluginResourceResolver


SECTION_ORDER = {"identity": 0, "session": 1, "context": 2, "activity": 3, "tasks": 4, "custom": 5}


@dataclass(frozen=True)
class HudLine:
    section: str
    priority: int
    text: str
    provider_id: str


def collect_hud_lines(plugins, context: dict) -> list[HudLine]:
    lines: list[HudLine] = []
    for plugin in plugins:
        resolver = PluginResourceResolver(Path(plugin.manifest.plugin_root), plugin.manifest.plugin_id)
        for entrypoint in plugin.manifest.entrypoints.get("hud", []):
            module_path = resolver.resolve_asset(entrypoint.target)
            spec = spec_from_file_location(f"hud_{plugin.manifest.plugin_id}_{entrypoint.entrypoint_id}", module_path)
            module = module_from_spec(spec)
            assert spec and spec.loader
            spec.loader.exec_module(module)
            for payload in module.render_lines(context):
                lines.append(
                    HudLine(
                        section=payload["section"],
                        priority=int(payload.get("priority", 100)),
                        text=payload["text"],
                        provider_id=entrypoint.entrypoint_id,
                    )
                )
    return sorted(lines, key=lambda item: (SECTION_ORDER[item.section], item.priority, item.provider_id, item.text))
```

- [x] **Step 4: Update `AWorldCLI._build_status_bar` to merge built-in and plugin HUD lines, then run tests**

Run: `pytest tests/plugins/test_plugin_hud.py tests/test_slash_commands.py tests/test_cli_user_input_hooks.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/plugin_framework/hud.py \
  aworld-cli/src/aworld_cli/console.py \
  aworld-cli/src/aworld_cli/runtime/base.py \
  tests/plugins/test_plugin_hud.py \
  tests/fixtures/plugins/hud_like
git commit -m "feat: add plugin hud providers"
```

### Task 6: Finish CLI Plugin Lifecycle Operations And End-To-End Verification

**Files:**
- Modify: `aworld-cli/src/aworld_cli/core/plugin_manager.py`
- Modify: `aworld-cli/src/aworld_cli/runtime/cli.py`
- Modify: `aworld-cli/src/aworld_cli/core/skill_registry.py`
- Modify: `aworld-cli/src/aworld_cli/console.py`
- Test: `tests/plugins/test_plugin_cli_lifecycle.py`
- Test: `tests/plugins/test_plugin_end_to_end.py`

- [x] **Step 1: Write failing lifecycle and end-to-end tests**

```python
# tests/plugins/test_plugin_cli_lifecycle.py
from pathlib import Path

from aworld_cli.core.plugin_manager import PluginManager


def test_enable_disable_reload_framework_plugin(tmp_path):
    manager = PluginManager(plugin_dir=tmp_path / "plugins")
    plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()

    manager.install("code-review-like", local_path=str(plugin_root))
    manager.disable("code-review-like")
    disabled = manager.list()
    assert disabled["code-review-like"]["enabled"] is False

    manager.enable("code-review-like")
    enabled = manager.list()
    assert enabled["code-review-like"]["enabled"] is True
```

```python
# tests/plugins/test_plugin_end_to_end.py
from pathlib import Path

from aworld_cli.core.command_system import CommandRegistry
from aworld_cli.plugin_framework.discovery import discover_plugins
from aworld_cli.plugin_framework.commands import register_plugin_commands


def test_code_review_like_plugin_registers_command_and_assets():
    plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    CommandRegistry.clear()
    register_plugin_commands([plugin])

    assert CommandRegistry.get("code-review") is not None
```

- [x] **Step 2: Run the tests and verify they fail**

Run: `pytest tests/plugins/test_plugin_cli_lifecycle.py tests/plugins/test_plugin_end_to_end.py -v`
Expected: FAIL because `PluginManager` does not yet expose framework-aware `enable` / `disable` / `reload`

- [x] **Step 3: Implement framework-aware lifecycle operations in `PluginManager`**

```python
# aworld-cli/src/aworld_cli/core/plugin_manager.py
def enable(self, plugin_name: str) -> None:
    if plugin_name not in self._manifest:
        raise KeyError(plugin_name)
    self._manifest[plugin_name]["enabled"] = True
    self._save_manifest()


def disable(self, plugin_name: str) -> None:
    if plugin_name not in self._manifest:
        raise KeyError(plugin_name)
    self._manifest[plugin_name]["enabled"] = False
    self._save_manifest()


def reload(self, plugin_name: str) -> dict:
    plugin_root = self.plugin_dir / plugin_name
    discovered = discover_plugins([plugin_root])
    if not discovered:
        raise RuntimeError(f"plugin not found: {plugin_name}")
    self._manifest[plugin_name]["capabilities"] = sorted(discovered[0].manifest.capabilities)
    self._save_manifest()
    return self._manifest[plugin_name]
```

- [x] **Step 4: Run the focused and regression test suites**

Run: `pytest tests/plugins -v`
Expected: PASS

Run: `pytest tests/test_slash_commands.py tests/test_cli_user_input_hooks.py tests/hooks -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aworld-cli/src/aworld_cli/core/plugin_manager.py \
  aworld-cli/src/aworld_cli/runtime/cli.py \
  aworld-cli/src/aworld_cli/core/skill_registry.py \
  aworld-cli/src/aworld_cli/console.py \
  tests/plugins/test_plugin_cli_lifecycle.py \
  tests/plugins/test_plugin_end_to_end.py
git commit -m "feat: finish framework plugin lifecycle"
```
