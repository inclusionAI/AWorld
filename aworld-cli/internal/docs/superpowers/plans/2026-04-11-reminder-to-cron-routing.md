# Reminder To Cron Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Aworld prefer `cron` for natural-language reminder requests and block shell-based delayed reminder simulation such as `sleep 60 && echo "提醒我喝水"`.

**Architecture:** Add an explicit reminder-routing policy to Aworld's system prompt so the model chooses `cron` for future or recurring reminders. Add a narrow terminal guard in the GAIA terminal tool to reject shell commands that combine `sleep` with reminder-style delayed output, then lock the behavior with prompt and terminal tests.

**Tech Stack:** Python, pytest, Aworld agent prompt template, GAIA terminal MCP tool

---

### Task 1: Add Prompt Policy For Reminder Requests

**Files:**
- Modify: `/Users/wuman/Documents/workspace/aworld-mas/aworld/aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/prompt.txt`
- Modify: `/Users/wuman/Documents/workspace/aworld-mas/aworld/aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/aworld_agent.py`
- Test: `/Users/wuman/Documents/workspace/aworld-mas/aworld/tests/core/agent/test_aworld_prompt_policy.py`

- [ ] **Step 1: Write the failing prompt-policy test**

```python
from aworld_cli.builtin_agents.smllc.agents.aworld_agent import load_aworld_system_prompt


def test_aworld_prompt_routes_reminders_to_cron():
    prompt = load_aworld_system_prompt()

    assert "For future reminders, delayed execution, or recurring reminders, use `cron`" in prompt
    assert "Do not use `bash` with `sleep`, foreground waiting, delayed `echo`, or temp-file polling to implement reminders." in prompt
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/core/agent/test_aworld_prompt_policy.py -q`
Expected: FAIL because `load_aworld_system_prompt` does not exist yet and the current prompt text does not contain the reminder-routing policy.

- [ ] **Step 3: Add a small prompt loader helper in `aworld_agent.py`**

```python
from pathlib import Path


def load_aworld_system_prompt() -> str:
    return (Path(__file__).resolve().parent / "prompt.txt").read_text(encoding="utf-8")
```

Then replace the inline `read_text(...)` call in `build_aworld_agent()` with:

```python
system_prompt=load_aworld_system_prompt(),
```

- [ ] **Step 4: Add reminder-routing policy text to `prompt.txt`**

Append the following section under `## 5. Critical Guardrails` near the existing `bash` guidance:

```text
- **Scheduled Reminders And Future Triggers:** For future reminders, delayed execution, or recurring reminders, use `cron` to create a scheduled task and reply immediately after the task is created.
- **Do Not Simulate Reminders With Shell Waiting:** Do not use `bash` with `sleep`, foreground waiting, delayed `echo`, or temp-file polling to implement reminders. If the user wants "X minutes later remind me", "tomorrow remind me", or recurring reminders, create a `cron` task instead.
```

- [ ] **Step 5: Re-run the prompt-policy test**

Run: `pytest tests/core/agent/test_aworld_prompt_policy.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/prompt.txt \
        aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/aworld_agent.py \
        tests/core/agent/test_aworld_prompt_policy.py
git commit -m "feat: route reminder requests to cron in aworld prompt"
```

### Task 2: Add A Narrow Terminal Guard For Shell-Based Reminder Simulation

**Files:**
- Modify: `/Users/wuman/Documents/workspace/aworld-mas/aworld/examples/gaia/mcp_collections/tools/terminal.py`
- Test: `/Users/wuman/Documents/workspace/aworld-mas/aworld/tests/tools/test_terminal_reminder_guard.py`

- [ ] **Step 1: Write the failing guard tests**

```python
import pytest

from examples.gaia.mcp_collections.base import ActionArguments
from examples.gaia.mcp_collections.tools.terminal import (
    TerminalActionCollection,
    _check_delayed_reminder_simulation,
)


def test_check_delayed_reminder_simulation_blocks_sleep_reminder():
    blocked, reason = _check_delayed_reminder_simulation('sleep 60 && echo "提醒我喝水"')
    assert blocked is True
    assert "cron" in reason.lower()


@pytest.mark.parametrize(
    "command",
    [
        "sleep 1",
        "sleep 1 && echo done",
        "python -c \"import time; time.sleep(1); print('done')\"",
    ],
)
def test_check_delayed_reminder_simulation_allows_normal_sleep_usage(command):
    blocked, reason = _check_delayed_reminder_simulation(command)
    assert blocked is False
    assert reason is None


@pytest.mark.asyncio
async def test_execute_command_rejects_sleep_based_reminder():
    terminal = TerminalActionCollection(ActionArguments(name="terminal", unittest=True))

    result = await terminal.mcp_execute_command(
        command='sleep 60 && echo "⏰ 提醒：该喝水了！💧"',
        timeout=10,
        output_format="text",
    )

    assert result.success is False
    assert result.metadata["error_type"] == "reminder_delay_blocked"
    assert "cron" in result.message.lower()
```

- [ ] **Step 2: Run the guard tests to verify they fail**

Run: `pytest tests/tools/test_terminal_reminder_guard.py -q`
Expected: FAIL because `_check_delayed_reminder_simulation` does not exist and delayed reminder commands are not blocked yet.

- [ ] **Step 3: Add a reusable delayed-reminder detection helper in `terminal.py`**

Add these module-level definitions near the existing imports and safety helpers:

```python
REMINDER_KEYWORDS = ("提醒", "喝水", "remind", "reminder")
REMINDER_OUTPUT_PATTERNS = (
    r"(?:&&|;)\s*echo\b",
    r"(?:&&|;)\s*cat\b",
    r">\s*[^|]+",
)


def _check_delayed_reminder_simulation(command: str) -> tuple[bool, str | None]:
    if not re.search(r"(^|[\s;&|])sleep\s+\d+(?:\.\d+)?(\s|$)", command, re.IGNORECASE):
        return False, None

    lower_command = command.lower()
    has_keyword = any(keyword in command for keyword in ("提醒", "喝水")) or any(
        keyword in lower_command for keyword in ("remind", "reminder")
    ) or re.search(r"该.{0,20}了", command)

    has_delayed_output = any(re.search(pattern, command, re.IGNORECASE) for pattern in REMINDER_OUTPUT_PATTERNS)

    if not (has_keyword and has_delayed_output):
        return False, None

    return (
        True,
        "Delayed reminder simulation with shell waiting is not allowed. Use `cron` to create a scheduled reminder instead.",
    )
```

- [ ] **Step 4: Call the guard from `mcp_execute_command()` before real execution**

Insert this check after the existing security and interactive checks, but before `_execute_command_async(...)`:

```python
            blocked, reminder_reason = _check_delayed_reminder_simulation(command)
            if blocked:
                return ActionResponse(
                    success=False,
                    message=reminder_reason,
                    metadata=TerminalMetadata(
                        command=command,
                        platform=self.platform_info["system"],
                        working_directory=str(self.workspace),
                        timeout_seconds=timeout,
                        safety_check_passed=True,
                        error_type="reminder_delay_blocked",
                    ).model_dump(),
                )
```

- [ ] **Step 5: Re-run the guard tests**

Run: `pytest tests/tools/test_terminal_reminder_guard.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add examples/gaia/mcp_collections/tools/terminal.py \
        tests/tools/test_terminal_reminder_guard.py
git commit -m "feat: block shell-based delayed reminder simulation"
```

### Task 3: Verify Prompt And Guard Work Together Without Regressions

**Files:**
- Modify: `/Users/wuman/Documents/workspace/aworld-mas/aworld/tests/core/agent/test_aworld_prompt_policy.py`
- Modify: `/Users/wuman/Documents/workspace/aworld-mas/aworld/tests/tools/test_terminal_reminder_guard.py`

- [ ] **Step 1: Add broader prompt and guard coverage cases**

Extend the tests with concrete reminder examples:

```python
def test_aworld_prompt_mentions_examples_for_future_and_recurring_reminders():
    prompt = load_aworld_system_prompt()

    assert "X minutes later remind me" in prompt
    assert "tomorrow remind me" in prompt
    assert "recurring reminders" in prompt


@pytest.mark.parametrize(
    "command",
    [
        'sleep 300; echo "reminder"',
        'sleep 60 && echo "提醒我提交代码" > reminder.txt',
    ],
)
def test_check_delayed_reminder_simulation_blocks_common_variants(command):
    blocked, reason = _check_delayed_reminder_simulation(command)
    assert blocked is True
    assert "cron" in reason.lower()
```

- [ ] **Step 2: Run targeted tests**

Run: `pytest tests/core/agent/test_aworld_prompt_policy.py tests/tools/test_terminal_reminder_guard.py -q`
Expected: PASS

- [ ] **Step 3: Run the existing prompt preservation regression test**

Run: `pytest tests/core/agent/test_system_prompt_update.py -q`
Expected: PASS

- [ ] **Step 4: Run the full relevant regression slice**

Run: `pytest tests/core/agent/test_system_prompt_update.py tests/core/agent/test_aworld_prompt_policy.py tests/tools/test_terminal_reminder_guard.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite verification**

Run: `pytest -x -q`
Expected: PASS with no new failures

- [ ] **Step 6: Commit**

```bash
git add tests/core/agent/test_aworld_prompt_policy.py \
        tests/tools/test_terminal_reminder_guard.py
git commit -m "test: cover reminder cron routing behavior"
```
