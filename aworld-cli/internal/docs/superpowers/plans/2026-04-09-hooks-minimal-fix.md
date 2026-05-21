# Hooks Minimal Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore hook visibility and runtime enforcement for explicitly loaded configs and workspace-bound configs without expanding the Hooks V2 feature surface.

**Architecture:** Keep the existing hook cache and hook protocol intact. Apply a narrow fallback for explicitly loaded nonstandard config paths in `HookFactory`, then make `run_hooks()` and its callers consistently resolve the logical workspace from `context.workspace_path` instead of process cwd.

**Tech Stack:** Python, pytest, existing AWorld hook runtime

---

### Task 1: Lock The Current Regression Surface

**Files:**
- Test: `tests/hooks/test_hook_factory.py`
- Test: `tests/hooks/test_legacy_protocol_e2e.py`
- Test: `tests/hooks/test_user_input_gate.py`
- Test: `tests/hooks/test_user_input_gate_e2e.py`
- Test: `tests/hooks/test_tool_gate_simple.py`

- [ ] **Step 1: Run the failing regression tests**

Run:
```bash
python -m pytest \
  tests/hooks/test_hook_factory.py \
  tests/hooks/test_legacy_protocol_e2e.py \
  tests/hooks/test_user_input_gate.py \
  tests/hooks/test_user_input_gate_e2e.py \
  tests/hooks/test_tool_gate_simple.py -q
```

Expected: failures showing config hooks are not visible or do not affect execution.

- [ ] **Step 2: Keep the failures as the red state**

Do not modify production code before the red state is confirmed.

### Task 2: Fix Hook Discovery For Explicitly Loaded Configs

**Files:**
- Modify: `aworld/runners/hook/hook_factory.py`
- Test: `tests/hooks/test_hook_factory.py`
- Test: `tests/hooks/test_multi_workspace_isolation.py`

- [ ] **Step 1: Update hook config selection logic**

Implement:
- keep standard `.aworld/hooks.yaml` lookup by `workspace_path`,
- if no standard config matches, allow a fallback only when there is exactly one cached nonstandard config path,
- keep strict isolation for standard workspace configs and multi-workspace cases.

- [ ] **Step 2: Run focused tests**

Run:
```bash
python -m pytest \
  tests/hooks/test_hook_factory.py \
  tests/hooks/test_multi_workspace_isolation.py \
  tests/hooks/test_auto_load_config.py -q
```

Expected: all pass.

### Task 3: Fix Workspace Path Resolution In Hook Execution

**Files:**
- Modify: `aworld/runners/hook/utils.py`
- Modify: `aworld/core/tool/base.py`
- Modify: `aworld/runners/event_runner.py`
- Test: `tests/hooks/test_legacy_protocol_e2e.py`
- Test: `tests/hooks/test_user_input_gate.py`
- Test: `tests/hooks/test_user_input_gate_e2e.py`
- Test: `tests/hooks/test_tool_gate_simple.py`

- [ ] **Step 1: Make `run_hooks()` resolve workspace path consistently**

Use:
- explicit `workspace_path` arg first,
- else `context.workspace_path`,
- else `os.getcwd()`.

- [ ] **Step 2: Patch missing runtime call sites**

Update callers that currently omit `workspace_path` but already have access to context so they pass `getattr(context, 'workspace_path', None)`.

- [ ] **Step 3: Run focused tests**

Run:
```bash
python -m pytest \
  tests/hooks/test_legacy_protocol_e2e.py \
  tests/hooks/test_user_input_gate.py \
  tests/hooks/test_user_input_gate_e2e.py \
  tests/hooks/test_tool_gate_simple.py -q
```

Expected: deny/allow/updated_input behavior is observed correctly.

### Task 4: Final Regression Check

**Files:**
- Test: `tests/hooks/`

- [ ] **Step 1: Run the full hook suite**

Run:
```bash
python -m pytest tests/hooks -q
```

Expected: hook suite passes, or only unrelated known issues remain.

- [ ] **Step 2: Summarize any residual risk**

Document if any unimplemented hook points remain intentionally out of scope.
