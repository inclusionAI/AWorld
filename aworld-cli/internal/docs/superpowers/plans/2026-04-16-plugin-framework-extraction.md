# Plugin Framework Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move framework-level plugin primitives out of `aworld-cli` into shared `aworld` code, while keeping CLI-specific plugin surfaces in `aworld-cli` and preserving current `aworld-hud` behavior.

**Architecture:** Split the current `aworld_cli.plugin_framework` package into two layers. Shared plugin primitives move to a new `aworld.plugins` package. CLI-only surfaces remain in `aworld-cli` under a new adapter package, with temporary compatibility shims to avoid breaking imports during migration.

**Tech Stack:** Python 3.12, pytest, Rich, prompt_toolkit, existing AWorld runtime/plugin loader structure.

---

## File Structure

### Shared framework layer to create under `aworld/`

- Create: `aworld/plugins/__init__.py`
- Create: `aworld/plugins/manifest.py`
- Create: `aworld/plugins/models.py`
- Create: `aworld/plugins/discovery.py`
- Create: `aworld/plugins/registry.py`
- Create: `aworld/plugins/resources.py`

Responsibilities:

- `manifest.py`: load `.aworld-plugin/plugin.json`
- `models.py`: plugin manifest and entrypoint data models
- `discovery.py`: identify plugins from root directories
- `registry.py`: register plugins and capabilities
- `resources.py`: resolve plugin-root resource paths

### CLI adapter layer to keep under `aworld-cli`

- Create: `aworld-cli/src/aworld_cli/plugin_runtime/__init__.py`
- Create: `aworld-cli/src/aworld_cli/plugin_runtime/commands.py`
- Create: `aworld-cli/src/aworld_cli/plugin_runtime/context.py`
- Create: `aworld-cli/src/aworld_cli/plugin_runtime/hooks.py`
- Create: `aworld-cli/src/aworld_cli/plugin_runtime/hud.py`
- Create: `aworld-cli/src/aworld_cli/plugin_runtime/state.py`

Responsibilities:

- `commands.py`: slash-command registration bridge for plugin commands
- `context.py`: CLI context-phase loading/execution
- `hooks.py`: CLI executor/user-input hook integration
- `hud.py`: HUD line collection and rendering integration for CLI
- `state.py`: plugin state storage rooted at `cwd/.aworld/plugin_state`

### Temporary compatibility layer to keep during migration

- Modify: `aworld-cli/src/aworld_cli/plugin_framework/__init__.py`
- Modify: `aworld-cli/src/aworld_cli/plugin_framework/discovery.py`
- Modify: `aworld-cli/src/aworld_cli/plugin_framework/manifest.py`
- Modify: `aworld-cli/src/aworld_cli/plugin_framework/models.py`
- Modify: `aworld-cli/src/aworld_cli/plugin_framework/registry.py`
- Modify: `aworld-cli/src/aworld_cli/plugin_framework/resources.py`

Responsibilities:

- Re-export moved shared APIs from `aworld.plugins.*`
- Keep old imports working until all callers are migrated

### Runtime and manager call sites to migrate

- Modify: `aworld-cli/src/aworld_cli/runtime/base.py`
- Modify: `aworld-cli/src/aworld_cli/runtime/cli.py`
- Modify: `aworld-cli/src/aworld_cli/core/plugin_manager.py`
- Modify: `aworld-cli/src/aworld_cli/commands/plugins_cmd.py`

### Tests to migrate or add

- Modify: `tests/plugins/test_plugin_framework_discovery.py`
- Modify: `tests/plugins/test_plugin_framework_manifest.py`
- Modify: `tests/plugins/test_plugin_framework_resources.py`
- Modify: `tests/plugins/test_plugin_registry.py`
- Modify: `tests/plugins/test_plugin_context.py`
- Modify: `tests/plugins/test_plugin_hooks.py`
- Modify: `tests/plugins/test_plugin_commands.py`
- Modify: `tests/plugins/test_plugin_hud.py`
- Modify: `tests/plugins/test_plugin_end_to_end.py`
- Modify: `tests/test_slash_commands.py`
- Add: `tests/plugins/test_shared_plugin_framework_imports.py`

---

### Task 1: Freeze Current Plugin Behavior With Import-Level Regression Tests

**Files:**
- Modify: `tests/plugins/test_plugin_framework_discovery.py`
- Modify: `tests/plugins/test_plugin_framework_manifest.py`
- Modify: `tests/plugins/test_plugin_framework_resources.py`
- Modify: `tests/plugins/test_plugin_registry.py`
- Add: `tests/plugins/test_shared_plugin_framework_imports.py`

- [ ] **Step 1: Add a failing test that describes the target shared import surface**

```python
from aworld.plugins.discovery import discover_plugins
from aworld.plugins.manifest import load_plugin_manifest
from aworld.plugins.registry import PluginCapabilityRegistry


def test_shared_plugin_framework_exports_core_primitives():
    assert callable(discover_plugins)
    assert callable(load_plugin_manifest)
    assert PluginCapabilityRegistry is not None
```

- [ ] **Step 2: Add a failing compatibility test for old CLI import paths**

```python
from aworld_cli.plugin_framework.discovery import discover_plugins as cli_discover_plugins
from aworld.plugins.discovery import discover_plugins as shared_discover_plugins


def test_cli_plugin_framework_discovery_reexports_shared_symbol():
    assert cli_discover_plugins is shared_discover_plugins
```

- [ ] **Step 3: Run tests to verify they fail before extraction**

Run:
```bash
pytest tests/plugins/test_shared_plugin_framework_imports.py -q
```

Expected:
- import failure for `aworld.plugins.*`

- [ ] **Step 4: Commit the failing-test checkpoint only after the full task is green**

```bash
git add tests/plugins/test_shared_plugin_framework_imports.py tests/plugins/test_plugin_framework_discovery.py tests/plugins/test_plugin_framework_manifest.py tests/plugins/test_plugin_framework_resources.py tests/plugins/test_plugin_registry.py
git commit -m "test: lock plugin framework extraction surface"
```

---

### Task 2: Create Shared `aworld.plugins` Core Modules

**Files:**
- Create: `aworld/plugins/__init__.py`
- Create: `aworld/plugins/manifest.py`
- Create: `aworld/plugins/models.py`
- Create: `aworld/plugins/discovery.py`
- Create: `aworld/plugins/registry.py`
- Create: `aworld/plugins/resources.py`

- [ ] **Step 1: Copy the current core modules into shared package with unchanged logic**

```python
# aworld/plugins/__init__.py
from .discovery import discover_plugins
from .manifest import load_plugin_manifest
from .models import PluginEntrypoint, PluginManifest
from .registry import PluginCapabilityRegistry, RegisteredEntrypoint
from .resources import PluginResourceResolver
```

- [ ] **Step 2: Keep imports inside the shared package self-contained**

```python
# aworld/plugins/discovery.py
from .manifest import load_plugin_manifest
from .models import PluginManifest
```

- [ ] **Step 3: Run the new shared import tests**

Run:
```bash
pytest tests/plugins/test_shared_plugin_framework_imports.py -q
```

Expected:
- PASS

- [ ] **Step 4: Run existing primitive framework tests**

Run:
```bash
pytest tests/plugins/test_plugin_framework_discovery.py tests/plugins/test_plugin_framework_manifest.py tests/plugins/test_plugin_framework_resources.py tests/plugins/test_plugin_registry.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit the shared core package**

```bash
git add aworld/plugins tests/plugins/test_shared_plugin_framework_imports.py tests/plugins/test_plugin_framework_discovery.py tests/plugins/test_plugin_framework_manifest.py tests/plugins/test_plugin_framework_resources.py tests/plugins/test_plugin_registry.py
git commit -m "feat: add shared plugin framework core"
```

---

### Task 3: Convert CLI `plugin_framework` Core Modules Into Compatibility Shims

**Files:**
- Modify: `aworld-cli/src/aworld_cli/plugin_framework/__init__.py`
- Modify: `aworld-cli/src/aworld_cli/plugin_framework/discovery.py`
- Modify: `aworld-cli/src/aworld_cli/plugin_framework/manifest.py`
- Modify: `aworld-cli/src/aworld_cli/plugin_framework/models.py`
- Modify: `aworld-cli/src/aworld_cli/plugin_framework/registry.py`
- Modify: `aworld-cli/src/aworld_cli/plugin_framework/resources.py`

- [ ] **Step 1: Replace moved CLI modules with explicit re-exports**

```python
# aworld-cli/src/aworld_cli/plugin_framework/discovery.py
from aworld.plugins.discovery import *  # noqa: F403
```

- [ ] **Step 2: Keep `__all__` stable in the CLI package root**

```python
# aworld-cli/src/aworld_cli/plugin_framework/__init__.py
from aworld.plugins import (
    PluginCapabilityRegistry,
    PluginEntrypoint,
    PluginManifest,
    PluginResourceResolver,
    RegisteredEntrypoint,
    discover_plugins,
    load_plugin_manifest,
)
from .context import CONTEXT_PHASES, PluginContextAdapter, load_plugin_contexts, run_context_phase
from .hooks import PluginHookResult, load_plugin_hooks
from .hud import HudLine, collect_hud_lines
```

- [ ] **Step 3: Run compatibility import tests**

Run:
```bash
pytest tests/plugins/test_shared_plugin_framework_imports.py tests/plugins/test_plugin_framework_discovery.py tests/plugins/test_plugin_framework_manifest.py tests/plugins/test_plugin_framework_resources.py tests/plugins/test_plugin_registry.py -q
```

Expected:
- PASS

- [ ] **Step 4: Commit the compatibility layer**

```bash
git add aworld-cli/src/aworld_cli/plugin_framework tests/plugins/test_shared_plugin_framework_imports.py tests/plugins/test_plugin_framework_discovery.py tests/plugins/test_plugin_framework_manifest.py tests/plugins/test_plugin_framework_resources.py tests/plugins/test_plugin_registry.py
git commit -m "refactor: reexport shared plugin framework from cli shims"
```

---

### Task 4: Rename CLI-Specific Surfaces To `plugin_runtime`

**Files:**
- Create: `aworld-cli/src/aworld_cli/plugin_runtime/__init__.py`
- Create: `aworld-cli/src/aworld_cli/plugin_runtime/commands.py`
- Create: `aworld-cli/src/aworld_cli/plugin_runtime/context.py`
- Create: `aworld-cli/src/aworld_cli/plugin_runtime/hooks.py`
- Create: `aworld-cli/src/aworld_cli/plugin_runtime/hud.py`
- Create: `aworld-cli/src/aworld_cli/plugin_runtime/state.py`
- Modify: `aworld-cli/src/aworld_cli/runtime/base.py`
- Modify: `aworld-cli/src/aworld_cli/core/plugin_manager.py`
- Modify: `aworld-cli/src/aworld_cli/runtime/cli.py`

- [ ] **Step 1: Copy CLI-specific modules to the new package name without behavior changes**

```python
# aworld-cli/src/aworld_cli/plugin_runtime/hud.py
from aworld_cli.plugin_framework.hud import *  # temporary bridge during rename
```

- [ ] **Step 2: Change runtime call sites to import CLI adapters from `plugin_runtime`**

```python
from ..plugin_runtime.context import CONTEXT_PHASES, load_plugin_contexts
from ..plugin_runtime.commands import sync_plugin_commands
from ..plugin_runtime.hooks import load_plugin_hooks
from ..plugin_runtime.hud import collect_hud_lines
from ..plugin_runtime.state import PluginStateStore
```

- [ ] **Step 3: Leave `aworld_cli.plugin_framework.context/hooks/hud/state/commands` in place as temporary re-export shims**

```python
# aworld-cli/src/aworld_cli/plugin_framework/hud.py
from aworld_cli.plugin_runtime.hud import *  # noqa: F403
```

- [ ] **Step 4: Run CLI plugin integration tests**

Run:
```bash
pytest tests/plugins/test_plugin_context.py tests/plugins/test_plugin_hooks.py tests/plugins/test_plugin_commands.py tests/plugins/test_plugin_hud.py tests/plugins/test_plugin_end_to_end.py tests/test_slash_commands.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit the adapter rename**

```bash
git add aworld-cli/src/aworld_cli/plugin_runtime aworld-cli/src/aworld_cli/runtime/base.py aworld-cli/src/aworld_cli/runtime/cli.py aworld-cli/src/aworld_cli/core/plugin_manager.py aworld-cli/src/aworld_cli/plugin_framework
git commit -m "refactor: split cli plugin runtime from shared framework"
```

---

### Task 5: Move Built-In Plugin Roots To Explicit CLI Builtins Naming

**Files:**
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/`
- Move: `aworld-cli/src/aworld_cli/plugins/aworld_hud` -> `aworld-cli/src/aworld_cli/builtin_plugins/aworld_hud`
- Modify: `aworld-cli/src/aworld_cli/core/plugin_manager.py`
- Modify: `tests/plugins/test_plugin_hud.py`
- Modify: `tests/plugins/test_plugin_end_to_end.py`

- [ ] **Step 1: Add a failing test for the new built-in plugin root resolver**

```python
def test_get_builtin_plugin_roots_reads_builtin_plugins_directory():
    roots = get_builtin_plugin_roots()
    assert any(root.name == "aworld_hud" for root in roots)
```

- [ ] **Step 2: Update builtin-plugin resolver to prefer `builtin_plugins/`**

```python
def get_builtin_plugins_base_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "builtin_plugins"
```

- [ ] **Step 3: Keep a compatibility fallback to the old `plugins/` directory during transition**

```python
for base_dir in (
    Path(__file__).resolve().parent.parent / "builtin_plugins",
    Path(__file__).resolve().parent.parent / "plugins",
    Path(__file__).resolve().parent.parent / "inner_plugins",
):
    ...
```

- [ ] **Step 4: Run built-in plugin discovery and HUD tests**

Run:
```bash
pytest tests/plugins/test_plugin_hud.py tests/plugins/test_plugin_end_to_end.py tests/test_slash_commands.py -q
```

Expected:
- PASS

- [ ] **Step 5: Commit the builtin root rename**

```bash
git add aworld-cli/src/aworld_cli/builtin_plugins aworld-cli/src/aworld_cli/core/plugin_manager.py tests/plugins/test_plugin_hud.py tests/plugins/test_plugin_end_to_end.py tests/test_slash_commands.py
git commit -m "refactor: move cli built-in plugins under builtin_plugins"
```

---

### Task 6: Remove Obsolete CLI `plugin_framework` Shims

**Files:**
- Delete: `aworld-cli/src/aworld_cli/plugin_framework/discovery.py`
- Delete: `aworld-cli/src/aworld_cli/plugin_framework/manifest.py`
- Delete: `aworld-cli/src/aworld_cli/plugin_framework/models.py`
- Delete: `aworld-cli/src/aworld_cli/plugin_framework/registry.py`
- Delete: `aworld-cli/src/aworld_cli/plugin_framework/resources.py`
- Delete or reduce: `aworld-cli/src/aworld_cli/plugin_framework/__init__.py`
- Modify: any remaining imports found by search

- [ ] **Step 1: Search for remaining legacy imports**

Run:
```bash
rg -n "aworld_cli\\.plugin_framework" aworld-cli/src tests
```

Expected:
- Only intentionally retained compatibility references, or none

- [ ] **Step 2: Replace all remaining imports with final targets**

```python
from aworld.plugins.discovery import discover_plugins
from aworld_cli.plugin_runtime.hud import collect_hud_lines
```

- [ ] **Step 3: Run full plugin-related regression suite**

Run:
```bash
pytest tests/plugins tests/test_slash_commands.py tests/core/scheduler/test_notifications.py tests/executors/test_stream.py -q
```

Expected:
- PASS

- [ ] **Step 4: Commit final cleanup**

```bash
git add aworld-cli/src/aworld_cli/plugin_framework aworld-cli/src/aworld_cli/plugin_runtime aworld/plugins tests/plugins tests/test_slash_commands.py tests/core/scheduler/test_notifications.py tests/executors/test_stream.py
git commit -m "refactor: finalize shared plugin framework extraction"
```

---

## Self-Review

### Spec coverage

- Shared plugin primitives move out of `aworld-cli`: covered by Tasks 2 and 3.
- CLI-only plugin surfaces stay in `aworld-cli`: covered by Task 4.
- Built-in plugin instance remains CLI-owned: covered by Task 5.
- Incremental migration with compatibility shims: covered by Tasks 3 and 6.

### Placeholder scan

- No `TODO`, `TBD`, or “appropriate handling” placeholders remain.
- Every task names exact files and exact test commands.

### Type consistency

- Shared package name is consistently `aworld.plugins`.
- CLI adapter package name is consistently `aworld_cli.plugin_runtime`.
- Legacy compatibility namespace remains `aworld_cli.plugin_framework` until Task 6.
