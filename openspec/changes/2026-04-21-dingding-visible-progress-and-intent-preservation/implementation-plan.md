# DingTalk Visible Progress And AI Card Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make DingTalk user-visible progress reliable by diagnosing AI Card lifecycle failures and sending a fallback acknowledgement when cards are unavailable, without changing cron runtime routing.

**Architecture:** Keep the current dual-path connector design intact: `on_text_chunk` continues to drive user-visible content, while `on_output` remains the sole raw runtime channel for cron binding and notification fanout. Tighten the DingTalk connector around AI Card lifecycle logging, fallback acknowledgement policy, and stream summary diagnostics.

**Tech Stack:** Python, pytest, DingTalk connector/channel layer, OpenSpec

---

### Task 1: Lock The New Behavior With Connector Tests

**Files:**
- Modify: `tests/gateway/test_dingding_connector.py`
- Test: `tests/gateway/test_dingding_connector.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_connector_sends_processing_ack_when_ai_card_unavailable_for_short_request() -> None:
    ...

def test_connector_logs_ai_card_disable_reason_and_fallback_summary(caplog) -> None:
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_dingding_connector.py -k "ai_card_unavailable_for_short_request or ai_card_disable_reason" -q`
Expected: FAIL because the connector only acknowledges complex requests and does not emit the new diagnostics yet.

- [ ] **Step 3: Write minimal implementation**

```python
if active_card is None:
    await self.send_text(session_webhook=session_webhook, text=PROCESSING_ACK_TEXT)
    ...
logger.info("DingTalk AI Card unavailable ...")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_dingding_connector.py -k "ai_card_unavailable_for_short_request or ai_card_disable_reason" -q`
Expected: PASS

### Task 2: Add AI Card Lifecycle Diagnostics Without Touching Cron Routing

**Files:**
- Modify: `aworld_gateway/channels/dingding/connector.py`
- Test: `tests/gateway/test_dingding_connector.py`

- [ ] **Step 1: Write the failing lifecycle test**

```python
def test_connector_logs_ai_card_finalize_fallback(caplog) -> None:
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_dingding_connector.py -k "finalize_fallback" -q`
Expected: FAIL because finalize fallback is currently silent.

- [ ] **Step 3: Write minimal implementation**

```python
logger.info("DingTalk AI Card finalize failed ...")
logger.info("DingTalk stream summary ...")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_dingding_connector.py -k "finalize_fallback" -q`
Expected: PASS

### Task 3: Validate The Full DingTalk Regression Slice

**Files:**
- Modify: `openspec/changes/2026-04-21-dingding-visible-progress-and-intent-preservation/tasks.md`
- Test: `tests/gateway/test_dingding_connector.py`
- Test: `tests/gateway/test_dingding_bridge.py`
- Test: `tests/gateway/test_gateway_status_command.py`

- [ ] **Step 1: Run the targeted regression suite**

Run: `pytest tests/gateway/test_dingding_connector.py tests/gateway/test_dingding_bridge.py tests/gateway/test_gateway_status_command.py -q`
Expected: PASS

- [ ] **Step 2: Mark the OpenSpec tasks complete**

```markdown
- [x] 1.4 ...
- [x] 1.5 ...
- [x] 1.6 ...
- [x] 2.2 ...
```

