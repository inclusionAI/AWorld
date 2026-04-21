# Gateway Quiet Startup Logs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce `aworld-cli gateway server` startup noise without hiding DingTalk, cron, or gateway runtime logs.

**Architecture:** Introduce a gateway-only quiet boot environment flag from `gateway_cli`, then route noisy boot logs in `loader` and `plugin_manager` through a small helper that downgrades them to `DEBUG` when the flag is enabled. Keep runtime business logs and startup summaries at `INFO`.

**Tech Stack:** Python, pytest, aworld-cli gateway CLI, OpenSpec

---

### Task 1: Lock quiet boot activation with tests

**Files:**
- Modify: `tests/gateway/test_gateway_status_command.py`
- Modify: `aworld-cli/src/aworld_cli/gateway_cli.py`

- [ ] **Step 1: Write the failing test**

```python
def test_serve_gateway_enables_quiet_boot_before_loading_agents(...):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_gateway_status_command.py -k quiet_boot -q`
Expected: FAIL because gateway startup does not yet set the quiet boot env var.

- [ ] **Step 3: Write minimal implementation**

```python
os.environ["AWORLD_GATEWAY_QUIET_BOOT"] = "true"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_gateway_status_command.py -k quiet_boot -q`
Expected: PASS

### Task 2: Lock loader and plugin manager boot-noise downgrades

**Files:**
- Create: `aworld-cli/src/aworld_cli/core/boot_logging.py`
- Modify: `aworld-cli/src/aworld_cli/core/loader.py`
- Modify: `aworld-cli/src/aworld_cli/core/plugin_manager.py`
- Modify: `tests/gateway/test_gateway_status_command.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_loader_verbose_boot_logs_downgrade_to_debug_in_quiet_mode(...):
    ...

def test_plugin_manager_verbose_boot_warnings_downgrade_to_debug_in_quiet_mode(...):
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_gateway_status_command.py -k "loader_verbose_boot_logs or plugin_manager_verbose_boot_warnings" -q`
Expected: FAIL because the noisy boot logs still use their original levels.

- [ ] **Step 3: Write minimal implementation**

```python
def log_verbose_boot(logger_obj, message: str, *, level: str = "info") -> None:
    ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_gateway_status_command.py -k "loader_verbose_boot_logs or plugin_manager_verbose_boot_warnings" -q`
Expected: PASS

### Task 3: Validate the gateway logging slice

**Files:**
- Modify: `openspec/changes/2026-04-21-gateway-quiet-startup-logs/tasks.md`
- Test: `tests/gateway/test_gateway_status_command.py`

- [ ] **Step 1: Run the targeted regression suite**

Run: `pytest tests/gateway/test_gateway_status_command.py -q`
Expected: PASS

- [ ] **Step 2: Mark the OpenSpec tasks complete**

```markdown
- [x] 1.1 ...
- [x] 1.2 ...
- [x] 1.3 ...
- [x] 1.4 ...
- [x] 2.1 ...
```
