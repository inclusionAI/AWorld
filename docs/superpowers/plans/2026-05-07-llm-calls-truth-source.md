# LLM Calls Truth Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add append-only `llm_calls` truth-source capture, request-id linkage, and raw cache-usage observability while making trajectory prefer real request snapshots without polluting trajectory semantics.

**Architecture:** Capture final provider-bound request snapshots at the `LLMModel` boundary, persist them in task-scoped `llm_calls`, expose them through `TaskResponse` and task artifacts, and update trajectory generation to prefer `llm_calls[*].request.messages` with an explicit fallback for historical tasks. Keep raw cache usage request-linked for logs, HUD, and finished summaries, but never inject cache metadata into `trajectory.state.messages`.

**Tech Stack:** Python, pytest, AWorld runtime context/task pipeline, OpenSpec markdown, CLI HUD/plugin hook snapshots.

---

### Task 1: Extend OpenSpec In-Place For The New Milestone

**Files:**
- Modify: `openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider/proposal.md`
- Modify: `openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider/design.md`
- Modify: `openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider/specs/cli-memory/spec.md`
- Modify: `openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider/specs/agent-memory-provider/spec.md`
- Reference: `docs/superpowers/specs/2026-05-07-llm-calls-truth-source-design.md`

- [ ] **Step 1: Add a failing spec note by searching for missing `llm_calls` language**

Run:

```bash
rg -n "llm_calls|request snapshot|cache usage|request_id" \
  openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider
```

Expected:

```text
No matches or only incidental matches; the current change does not yet define the milestone.
```

- [ ] **Step 2: Update the proposal scope**

Add a scoped extension to `proposal.md` similar to:

```md
- append-only `llm_calls` truth-source capture for real model requests
- provider/internal `request_id` linkage for observability
- request-linked raw cache-usage preservation without changing trajectory semantics
```

- [ ] **Step 3: Update the design document**

Add a section to `design.md` similar to:

```md
### Decision: LLM call truth-source capture extends the durable session-log boundary

Phase 1 keeps runtime message memory semantics unchanged, but adds an append-only
`llm_calls` truth source captured at the model boundary. Trajectory generation
must prefer `llm_calls[*].request.messages` when present. Provider-native cache
usage fields remain request-linked observability data and MUST NOT be injected
into trajectory semantic messages.
```

- [ ] **Step 4: Update requirement specs**

Add or extend requirement language in the two spec files. Include wording like:

```md
#### Requirement: Completed task artifacts MUST preserve append-only llm_calls

- **WHEN** a model call is executed
- **THEN** the runtime MUST append one `llm_calls` record containing the final
  request snapshot, internal request id, provider request id when available,
  normalized usage, and raw usage
- **AND** trajectory generation MUST prefer `llm_calls[*].request.messages`
  before reconstructing history from message memory
- **AND** cache usage fields MUST remain request-linked metadata rather than
  trajectory semantic content
```

- [ ] **Step 5: Re-run the spec search to prove the new scope is documented**

Run:

```bash
rg -n "llm_calls|request snapshot|cache usage|request_id" \
  openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider
```

Expected:

```text
Multiple matches in proposal/design/specs showing the milestone is now defined.
```

- [ ] **Step 6: Commit**

```bash
git add \
  openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider/proposal.md \
  openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider/design.md \
  openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider/specs/cli-memory/spec.md \
  openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider/specs/agent-memory-provider/spec.md
git commit -m "docs: extend memory spec for llm call truth source"
```

### Task 2: Preserve Provider Request IDs And Raw Usage In Model Responses

**Files:**
- Modify: `aworld/models/model_response.py`
- Modify: `aworld/models/openai_provider.py`
- Test: `tests/models/test_model_response_llm_calls.py`

- [ ] **Step 1: Write the failing tests for raw usage and provider request id preservation**

Create `tests/models/test_model_response_llm_calls.py` with tests like:

```python
from aworld.models.model_response import ModelResponse


def test_from_openai_response_preserves_raw_usage_and_provider_request_id():
    response = {
        "id": "chatcmpl_123",
        "model": "gpt-4.1",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "hi"},
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 25,
            "total_tokens": 125,
            "prompt_tokens_details": {"cached_tokens": 80},
            "cache_hit_tokens": 80,
        },
        "request_id": "req_provider_123",
    }

    model_response = ModelResponse.from_openai_response(response)

    assert model_response.usage["prompt_tokens"] == 100
    assert model_response.raw_usage["cache_hit_tokens"] == 80
    assert model_response.provider_request_id == "req_provider_123"
```

- [ ] **Step 2: Run the focused test and verify it fails for the right reason**

Run:

```bash
pytest tests/models/test_model_response_llm_calls.py -q
```

Expected:

```text
FAIL because `ModelResponse` does not yet expose `raw_usage` and `provider_request_id`.
```

- [ ] **Step 3: Implement the minimal response fields**

Update `aworld/models/model_response.py` along these lines:

```python
class ModelResponse:
    def __init__(
        self,
        id: str,
        model: str,
        content: str = None,
        tool_calls: List[ToolCall] = None,
        usage: Dict[str, int] = None,
        raw_usage: Dict[str, Any] = None,
        provider_request_id: str | None = None,
        error: str = None,
        raw_response: Any = None,
        message: Dict[str, Any] = None,
        reasoning_content: str = None,
        finish_reason: str = None,
        reasoning_details: Dict[str, Any] = None,
        video_result: VideoGenerationResult = None,
    ):
        self.usage = usage or {
            "completion_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0,
        }
        self.raw_usage = raw_usage or dict(self.usage)
        self.provider_request_id = provider_request_id
```

Also preserve raw usage and provider request id in the OpenAI factories:

```python
raw_usage = response["usage"] if isinstance(response, dict) else response.usage
provider_request_id = (
    response.get("request_id")
    if isinstance(response, dict)
    else getattr(response, "request_id", None)
)
```

- [ ] **Step 4: Run the focused test and verify it passes**

Run:

```bash
pytest tests/models/test_model_response_llm_calls.py -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Commit**

```bash
git add aworld/models/model_response.py aworld/models/openai_provider.py tests/models/test_model_response_llm_calls.py
git commit -m "feat: preserve provider request ids and raw usage"
```

### Task 3: Capture Append-Only `llm_calls` At The `LLMModel` Boundary

**Files:**
- Modify: `aworld/models/llm.py`
- Modify: `aworld/core/context/base.py`
- Modify: `aworld/core/task.py`
- Modify: `aworld/runners/event_runner.py`
- Test: `tests/hooks/test_llm_call_hooks.py`
- Test: `tests/models/test_llm_call_capture.py`

- [ ] **Step 1: Write the failing runtime-capture tests**

Create `tests/models/test_llm_call_capture.py` with tests like:

```python
from aworld.core.context.amni import AmniContext
from aworld.core.context.session import Session
from aworld.models.llm import LLMModel
from aworld.models.model_response import ModelResponse


class StubProvider:
    model_name = "stub-model"

    async def acompletion(self, **kwargs):
        return ModelResponse(
            id="resp_1",
            model="stub-model",
            content="done",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            raw_usage={
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cache_hit_tokens": 8,
            },
            provider_request_id="provider_req_1",
            message={"role": "assistant", "content": "done"},
        )


async def test_llm_model_appends_llm_calls_after_final_hook_input():
    context = AmniContext(session_id=Session().session_id, task_id="task-1")
    llm_model = LLMModel(custom_provider=StubProvider())

    await llm_model.acompletion([{"role": "user", "content": "hello"}], context=context)

    llm_calls = context.context_info["llm_calls"]
    assert len(llm_calls) == 1
    assert llm_calls[0]["provider_request_id"] == "provider_req_1"
    assert llm_calls[0]["usage_raw"]["cache_hit_tokens"] == 8
```

- [ ] **Step 2: Run the focused capture tests and verify they fail**

Run:

```bash
pytest tests/models/test_llm_call_capture.py tests/hooks/test_llm_call_hooks.py -q
```

Expected:

```text
FAIL because context does not yet append `llm_calls` and hook payloads do not carry the final truth-source record.
```

- [ ] **Step 3: Implement minimal append-only capture**

In `aworld/models/llm.py`, add helpers along these lines:

```python
def _append_llm_call(context, record: dict[str, Any]) -> None:
    if context is None:
        return
    llm_calls = context.context_info.get("llm_calls") or []
    llm_calls.append(record)
    context.context_info["llm_calls"] = llm_calls


def _build_llm_call_record(...):
    return {
        "request_id": request_id,
        "provider_request_id": getattr(resp, "provider_request_id", None),
        "task_id": context.task_id if context else None,
        "model": self.provider.model_name,
        "provider_name": self.provider_name,
        "request": {
            "messages": to_serializable(messages),
            "params": {
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stop": stop,
            },
        },
        "response": {
            "id": resp.id,
            "message": to_serializable(resp.message),
            "finish_reason": resp.finish_reason,
        },
        "usage_normalized": to_serializable(resp.usage),
        "usage_raw": to_serializable(getattr(resp, "raw_usage", resp.usage)),
        "status": "success",
    }
```

Update `aworld/core/task.py`:

```python
@dataclass
class TaskResponse:
    ...
    llm_calls: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            ...
            "llm_calls": self.llm_calls,
        }
```

Update `aworld/runners/event_runner.py` so `_response()` and `_save_trajectories()`
carry `llm_calls`.

- [ ] **Step 4: Re-run the focused capture tests and verify they pass**

Run:

```bash
pytest tests/models/test_llm_call_capture.py tests/hooks/test_llm_call_hooks.py -q
```

Expected:

```text
All tests pass, and hook tests still confirm `request_id` behavior.
```

- [ ] **Step 5: Commit**

```bash
git add \
  aworld/models/llm.py \
  aworld/core/context/base.py \
  aworld/core/task.py \
  aworld/runners/event_runner.py \
  tests/models/test_llm_call_capture.py \
  tests/hooks/test_llm_call_hooks.py
git commit -m "feat: capture append-only llm call records"
```

### Task 4: Make Trajectory Prefer `llm_calls[*].request.messages`

**Files:**
- Modify: `aworld/dataset/trajectory_strategy.py`
- Modify: `aworld/dataset/types.py`
- Test: `tests/train/test_trajectory_llm_calls.py`

- [ ] **Step 1: Write the failing trajectory tests**

Create `tests/train/test_trajectory_llm_calls.py` with tests like:

```python
from aworld.dataset.trajectory_strategy import TrajectoryStrategy


def test_build_trajectory_state_prefers_llm_call_request_messages():
    strategy = TrajectoryStrategy()
    source = type("Message", (), {})()
    source.payload = {"content": "hi"}
    source.receiver = "agent-1"
    source.task_id = "task-1"
    source.context = type(
        "Context",
        (),
        {
            "context_info": {
                "llm_calls": [
                    {
                        "request": {
                            "messages": [{"role": "user", "content": "real request"}]
                        },
                        "usage_raw": {"cache_hit_tokens": 99},
                    }
                ]
            }
        },
    )()

    state = strategy.build_trajectory_state(source)

    assert state.messages == [{"role": "user", "content": "real request"}]
    assert "cache_hit_tokens" not in str(state.messages)
```

- [ ] **Step 2: Run the trajectory tests and verify they fail**

Run:

```bash
pytest tests/train/test_trajectory_llm_calls.py -q
```

Expected:

```text
FAIL because trajectory still reconstructs from memory and ignores `llm_calls`.
```

- [ ] **Step 3: Implement the minimal preference and fallback**

Update `aworld/dataset/trajectory_strategy.py`:

```python
def _get_llm_messages_from_truth_source(self, message: Any):
    ctx = getattr(message, "context", None)
    if not ctx or not hasattr(ctx, "context_info"):
        return None
    llm_calls = ctx.context_info.get("llm_calls") or []
    if not llm_calls:
        return None
    latest = llm_calls[-1]
    request = latest.get("request") or {}
    messages = request.get("messages")
    return to_serializable(messages) if messages else None


async def build_trajectory_state(self, source: Any, **kwargs):
    history_messages = self._get_llm_messages_from_truth_source(source)
    if history_messages is None:
        history_messages = self._get_llm_messages_from_memory(
            source,
            kwargs.get("use_tools_in_prompt", False),
        )
```

Keep cache usage entirely out of `TrajectoryState.messages`.

- [ ] **Step 4: Run the trajectory tests and verify they pass**

Run:

```bash
pytest tests/train/test_trajectory_llm_calls.py tests/train/test_trajectory_log.py -q
```

Expected:

```text
All tests pass, including the existing token-id trajectory smoke test.
```

- [ ] **Step 5: Commit**

```bash
git add aworld/dataset/trajectory_strategy.py aworld/dataset/types.py tests/train/test_trajectory_llm_calls.py
git commit -m "feat: prefer llm call request snapshots in trajectory"
```

### Task 5: Add Request-Linked Cache Observability To Logs, HUD, And Hook Payloads

**Files:**
- Modify: `aworld/logs/prompt_log.py`
- Modify: `aworld-cli/src/aworld_cli/executors/stats.py`
- Modify: `aworld-cli/src/aworld_cli/executors/local.py`
- Modify: `aworld-cli/src/aworld_cli/plugin_capabilities/hooks.py`
- Test: `tests/plugins/test_runtime_hud_snapshot.py`
- Test: `tests/plugins/test_shared_plugin_framework_imports.py`

- [ ] **Step 1: Write the failing observability tests**

Extend `tests/plugins/test_runtime_hud_snapshot.py` with coverage like:

```python
def test_stream_token_stats_preserves_cache_usage_snapshot():
    stats = StreamTokenStats()
    stats.update(
        agent_id="agent-1",
        agent_name="Aworld",
        output_tokens=300,
        input_tokens=1200,
        tool_calls_count=2,
        model_name="gpt-4o",
        usage_raw={"cache_hit_tokens": 800, "cache_write_tokens": 20},
        request_id="req-1",
        provider_request_id="provider-1",
    )

    usage = stats.to_hud_usage()

    assert usage["request_id"] == "req-1"
    assert usage["provider_request_id"] == "provider-1"
    assert usage["cache_usage"]["cache_hit_tokens"] == 800
```

Extend `tests/plugins/test_shared_plugin_framework_imports.py` with:

```python
assert {"usage"} <= TaskProgressHookEvent.__optional_keys__
assert {"task_status", "final_answer"} <= TaskCompletedHookEvent.__optional_keys__
```

and add assertions for any new optional usage fields if the typed dict is expanded.

- [ ] **Step 2: Run the focused observability tests and verify they fail**

Run:

```bash
pytest \
  tests/plugins/test_runtime_hud_snapshot.py \
  tests/plugins/test_shared_plugin_framework_imports.py -q
```

Expected:

```text
FAIL because the HUD usage snapshot does not yet preserve request-linked cache data.
```

- [ ] **Step 3: Implement minimal request-linked observability**

In `aworld-cli/src/aworld_cli/executors/stats.py` extend `update()` and
`to_hud_usage()`:

```python
def update(
    self,
    agent_id: str,
    agent_name: Optional[str],
    output_tokens: int,
    input_tokens: Optional[int],
    tool_calls_count: int,
    ...,
    usage_raw: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
    provider_request_id: Optional[str] = None,
):
    self._stats[key]["usage_raw"] = usage_raw or {}
    self._stats[key]["request_id"] = request_id
    self._stats[key]["provider_request_id"] = provider_request_id

def to_hud_usage(self) -> Dict[str, Any]:
    ...
    return {
        ...
        "request_id": stats.get("request_id"),
        "provider_request_id": stats.get("provider_request_id"),
        "cache_usage": {
            "cache_hit_tokens": raw_usage.get("cache_hit_tokens"),
            "cache_write_tokens": raw_usage.get("cache_write_tokens"),
            "prompt_tokens_details": raw_usage.get("prompt_tokens_details"),
        },
    }
```

In `aworld/logs/prompt_log.py`, add log lines like:

```python
prompt_logger.info(f"│ 🧾 Request ID: {request_id:<{BORDER_WIDTH - 14}} │")
prompt_logger.info(f"│ 🧾 Provider Request ID: {provider_request_id:<{BORDER_WIDTH - 23}} │")
prompt_logger.info(f"│ 🧾 Cache Usage: {json.dumps(cache_usage, ensure_ascii=False):<{BORDER_WIDTH - 15}} │")
```

- [ ] **Step 4: Re-run the focused observability tests and verify they pass**

Run:

```bash
pytest \
  tests/plugins/test_runtime_hud_snapshot.py \
  tests/plugins/test_shared_plugin_framework_imports.py -q
```

Expected:

```text
All tests pass and the new usage fields remain observational.
```

- [ ] **Step 5: Commit**

```bash
git add \
  aworld/logs/prompt_log.py \
  aworld-cli/src/aworld_cli/executors/stats.py \
  aworld-cli/src/aworld_cli/executors/local.py \
  aworld-cli/src/aworld_cli/plugin_capabilities/hooks.py \
  tests/plugins/test_runtime_hud_snapshot.py \
  tests/plugins/test_shared_plugin_framework_imports.py
git commit -m "feat: expose request-linked cache observability"
```

### Task 6: Persist `llm_calls` In Workspace Session Logs And Verify With Real Samples

**Files:**
- Modify: `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/hooks/task_completed.py`
- Modify: `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/common.py`
- Test: `tests/cli_memory/test_memory_acceptance.py`
- Evidence: `/Users/wuman/Documents/logs/trajectory.2026-05-04_13-22-40_150155.log`

- [ ] **Step 1: Write the failing durable-log acceptance test**

Add an acceptance test like:

```python
def test_workspace_session_log_can_append_llm_calls(tmp_path):
    log_path = append_workspace_session_log(
        workspace_path=tmp_path,
        session_id="session-1",
        payload={
            "event": "task_completed",
            "llm_calls": [
                {
                    "request_id": "req-1",
                    "request": {"messages": [{"role": "user", "content": "hi"}]},
                    "usage_raw": {"cache_hit_tokens": 4},
                }
            ],
        },
    )

    content = log_path.read_text(encoding="utf-8")
    assert '"llm_calls"' in content
    assert '"cache_hit_tokens": 4' in content
```

- [ ] **Step 2: Run the durable-log acceptance test and verify it fails**

Run:

```bash
pytest tests/cli_memory/test_memory_acceptance.py -q
```

Expected:

```text
FAIL because the workspace session-log payload does not yet include `llm_calls`.
```

- [ ] **Step 3: Implement minimal workspace log persistence**

Update `task_completed.py` to include the captured `llm_calls`:

```python
append_workspace_session_log(
    workspace_path=workspace_path,
    session_id=session_id,
    payload={
        "event": "task_completed",
        "session_id": session_id,
        "task_id": event.get("task_id"),
        "task_status": event.get("task_status") or "idle",
        "workspace_path": workspace_path,
        "final_answer": final_answer,
        "candidates": candidates,
        "llm_calls": event.get("llm_calls") or [],
    },
)
```

- [ ] **Step 4: Run the durable-log acceptance test and verify it passes**

Run:

```bash
pytest tests/cli_memory/test_memory_acceptance.py -q
```

Expected:

```text
Passing acceptance coverage for session-log persistence of `llm_calls`.
```

- [ ] **Step 5: Produce fresh real samples and compare against the historical sample**

Run:

```bash
pytest \
  tests/models/test_llm_call_capture.py \
  tests/train/test_trajectory_llm_calls.py \
  tests/plugins/test_runtime_hud_snapshot.py -q
```

Then run one small real local task and inspect:

```bash
rg -n "\"llm_calls\"|cache_hit_tokens|provider_request_id|trajectory" \
  ~/.aworld ~/.aworld/memory /Users/wuman/Documents/logs
```

Expected:

```text
Fresh artifacts show `llm_calls`, request-linked cache usage, and trajectory snapshots without cache fields in semantic messages.
```

- [ ] **Step 6: Commit**

```bash
git add \
  aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/hooks/task_completed.py \
  aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/common.py \
  tests/cli_memory/test_memory_acceptance.py
git commit -m "feat: persist llm call records in session logs"
```

### Task 7: Final Verification Audit

**Files:**
- Reference: `docs/superpowers/specs/2026-05-07-llm-calls-truth-source-design.md`
- Reference: `docs/superpowers/plans/2026-05-07-llm-calls-truth-source.md`

- [ ] **Step 1: Run the full focused verification suite**

Run:

```bash
pytest \
  tests/models/test_model_response_llm_calls.py \
  tests/models/test_llm_call_capture.py \
  tests/hooks/test_llm_call_hooks.py \
  tests/train/test_trajectory_llm_calls.py \
  tests/train/test_trajectory_log.py \
  tests/plugins/test_runtime_hud_snapshot.py \
  tests/plugins/test_shared_plugin_framework_imports.py \
  tests/cli_memory/test_memory_acceptance.py -q
```

Expected:

```text
0 failures
```

- [ ] **Step 2: Re-check the requirement mapping against the spec**

Use this checklist:

```text
[ ] append-only llm_calls persisted
[ ] internal request_id carried
[ ] provider request_id carried when available
[ ] raw cache usage preserved
[ ] trajectory prefers llm_calls request snapshots
[ ] cache usage excluded from trajectory semantics
[ ] HUD/logs/finished can observe cache usage by request_id
[ ] one historical sample reviewed
[ ] one fresh real sample reviewed
```

- [ ] **Step 3: Record the evidence in the final summary**

Summarize with concrete evidence only:

```text
- tests run
- artifact paths inspected
- remaining gaps or none
- next smallest follow-up thread
```

- [ ] **Step 4: Commit any final glue changes**

```bash
git add -A
git commit -m "test: verify llm call truth-source milestone"
```
