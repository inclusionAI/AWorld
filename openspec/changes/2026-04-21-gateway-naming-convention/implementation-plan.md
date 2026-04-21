# Gateway Naming Convention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Standardize gateway naming so user-facing surfaces say `aworld-gateway` while Python imports remain `aworld_gateway`.

**Architecture:** Keep the package directory and import paths unchanged for Python compatibility, but centralize gateway naming in package metadata constants and reuse them for CLI and HTTP display strings. Document the distinction in OpenSpec and README so future code changes follow the same rule.

**Tech Stack:** Python, FastAPI, argparse, pytest, OpenSpec

---

### Task 1: Lock naming outputs with tests

**Files:**
- Modify: `tests/test_gateway_cli.py`
- Modify: `tests/gateway/test_gateway_http_app.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_gateway_parser_uses_hyphenated_display_name() -> None:
    ...

def test_gateway_http_app_uses_hyphenated_title() -> None:
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_cli.py tests/gateway/test_gateway_http_app.py -k "hyphenated" -q`
Expected: FAIL because the current parser/app title still uses the old display naming.

- [ ] **Step 3: Write minimal implementation**

```python
GATEWAY_DISPLAY_NAME = "aworld-gateway"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gateway_cli.py tests/gateway/test_gateway_http_app.py -k "hyphenated" -q`
Expected: PASS

### Task 2: Export explicit naming constants and document them

**Files:**
- Modify: `aworld_gateway/__init__.py`
- Modify: `README.md`
- Modify: `README_zh.md`

- [ ] **Step 1: Write the failing test**

```python
def test_gateway_package_exports_import_and_display_names() -> None:
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gateway_cli.py -k "exports_import_and_display_names" -q`
Expected: FAIL because the gateway package does not yet export explicit naming constants.

- [ ] **Step 3: Write minimal implementation**

```python
GATEWAY_IMPORT_NAME = "aworld_gateway"
GATEWAY_DISPLAY_NAME = "aworld-gateway"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gateway_cli.py -k "exports_import_and_display_names" -q`
Expected: PASS

### Task 3: Validate the naming slice

**Files:**
- Modify: `openspec/changes/2026-04-21-gateway-naming-convention/tasks.md`
- Test: `tests/test_gateway_cli.py`
- Test: `tests/gateway/test_gateway_http_app.py`

- [ ] **Step 1: Run the targeted regression suite**

Run: `pytest tests/test_gateway_cli.py tests/gateway/test_gateway_http_app.py -q`
Expected: PASS

- [ ] **Step 2: Mark the OpenSpec tasks complete**

```markdown
- [x] 1.1 ...
- [x] 1.2 ...
- [x] 1.3 ...
- [x] 1.4 ...
- [x] 1.5 ...
- [x] 2.1 ...
```
