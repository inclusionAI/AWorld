# HUD Plugin Boundary Batch 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit plugin-facing HUD helper boundary and prove that a third-party HUD plugin can use hook-driven state plus the generic HUD renderer without host plugin-specific branches.

**Architecture:** Keep the current accepted HUD UX unchanged. Add a stable HUD helper module under a host-owned plugin-facing namespace, move the built-in `aworld-hud` plugin onto that boundary, and add an end-to-end mock HUD plugin test that exercises discovery, hooks, plugin state, and final HUD rendering through generic runtime/CLI paths.

**Tech Stack:** Python, pytest, existing plugin discovery/runtime framework, OpenSpec-guided refactor

---

### Task 1: Add explicit HUD helper boundary

**Files:**
- Create: `aworld-cli/src/aworld_cli/plugin_capabilities/__init__.py`
- Create: `aworld-cli/src/aworld_cli/plugin_capabilities/hud_helpers.py`
- Modify: `aworld-cli/src/aworld_cli/builtin_plugins/aworld_hud/hud/status.py`
- Test: `tests/plugins/test_plugin_hud.py`

- [ ] **Step 1: Write the failing tests**

Add tests that:
- assert HUD helper formatters are importable from the explicit plugin-facing module
- assert the built-in HUD plugin keeps rendering the same semantic segments after switching imports

- [ ] **Step 2: Run focused tests to verify failure**

Run: `pytest tests/plugins/test_plugin_hud.py -q`

Expected: failures showing the new helper module does not exist yet.

- [ ] **Step 3: Write the minimal implementation**

Create a plugin-facing HUD helper module that exports stable formatting helpers for:
- token counts
- elapsed time
- HUD context bar text

Update the built-in HUD plugin to import from that explicit helper boundary instead of `aworld_cli.executors.stats`.

- [ ] **Step 4: Run focused tests to verify pass**

Run: `pytest tests/plugins/test_plugin_hud.py -q`

Expected: PASS

### Task 2: Add end-to-end third-party HUD plugin validation

**Files:**
- Create: `tests/fixtures/plugins/hud_stateful_like/.aworld-plugin/plugin.json`
- Create: `tests/fixtures/plugins/hud_stateful_like/hooks/task_started.py`
- Create: `tests/fixtures/plugins/hud_stateful_like/hud/status.py`
- Modify: `tests/plugins/test_plugin_end_to_end.py`
- Modify: `tests/plugins/test_plugin_hooks.py`

- [ ] **Step 1: Write the failing end-to-end tests**

Add a test that:
- discovers a third-party HUD plugin fixture
- runs a lifecycle hook through `BaseCliRuntime.run_plugin_hooks(...)`
- proves plugin-scoped state flows into HUD rendering
- proves CLI status bar text renders the resulting lines without knowing the plugin name

- [ ] **Step 2: Run focused tests to verify failure**

Run: `pytest tests/plugins/test_plugin_end_to_end.py tests/plugins/test_plugin_hooks.py -q`

Expected: failures showing the synthetic plugin or helper boundary contract is incomplete.

- [ ] **Step 3: Write the minimal implementation**

Add the fixture plugin and any minimal compatibility code needed so the runtime and CLI exercise the generic path only.

- [ ] **Step 4: Run focused tests to verify pass**

Run: `pytest tests/plugins/test_plugin_end_to_end.py tests/plugins/test_plugin_hooks.py -q`

Expected: PASS

### Task 3: Final regression verification

**Files:**
- Modify: `openspec/changes/hud-plugin-boundary/tasks.md`

- [ ] **Step 1: Run the regression suite**

Run: `pytest tests/plugins tests/test_cli_user_input_hooks.py -q`

Expected: PASS

- [ ] **Step 2: Update OpenSpec task tracking**

Mark the completed Batch 2 task items in `openspec/changes/hud-plugin-boundary/tasks.md`.

- [ ] **Step 3: Commit**

Run:

```bash
git add \
  aworld-cli/src/aworld_cli/plugin_capabilities \
  aworld-cli/src/aworld_cli/builtin_plugins/aworld_hud/hud/status.py \
  tests/fixtures/plugins/hud_stateful_like \
  tests/plugins/test_plugin_hud.py \
  tests/plugins/test_plugin_end_to_end.py \
  tests/plugins/test_plugin_hooks.py \
  openspec/changes/hud-plugin-boundary/tasks.md
git commit -m "refactor: add hud plugin sdk boundary"
```
