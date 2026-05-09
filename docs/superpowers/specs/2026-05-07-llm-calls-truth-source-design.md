# LLM Calls Truth Source Design

## Goal

Extend the existing `2026-04-28-aworld-cli-memory-hybrid-provider` change with
one narrow precondition milestone:

- persist append-only `llm_calls` as the truth source for real model requests
- carry internal and provider `request_id`
- preserve provider-native cache usage fields as raw observability data
- improve trajectory fidelity by preferring `llm_calls[*].request.messages`
- keep cache usage out of trajectory semantics and only associate it by
  `request_id`

This design intentionally does **not** implement a full prompt-cache strategy,
provider-specific cache optimization, or a broad runtime-memory rewrite.

## Scope Decision

This work should extend `2026-04-28-aworld-cli-memory-hybrid-provider` instead
of introducing a new OpenSpec change.

Why:

- that change already freezes the separation between runtime message memory and
  append-only durable/session-log paths
- `llm_calls` is another append-only observability and recall artifact, not a
  replacement for `AworldMemory`
- the existing change already owns workspace-scoped session logs and relevant
  recall boundaries, which are the closest current product surface for this
  work

Relevant evidence:

- `openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider/proposal.md`
- `openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider/design.md`
- `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/common.py`
- `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/hooks/task_completed.py`

## Problem Statement

Two precondition problems remain unresolved.

### 1. Trajectory fidelity is weak

Current trajectory construction reconstructs model-facing messages from memory
instead of reading a per-call request snapshot.

Evidence:

- `aworld/agents/llm_agent.py` stores `context.context_info["llm_input"]`, but
  only as transient per-agent state
- `aworld/models/llm.py` is the real model-call boundary and already generates
  `request_id`
- `aworld/dataset/trajectory_strategy.py` currently rebuilds
  `state.messages` from memory via `_get_llm_messages_from_memory()`
- `aworld/runners/handler/memory.py` stores assistant raw responses in message
  metadata, but not an append-only request snapshot truth source
- sample log
  `/Users/wuman/Documents/logs/trajectory.2026-05-04_13-22-40_150155.log`
  shows `trajectory.state.messages` that look like a model request transcript,
  but are actually reconstructed semantic history with embedded raw response
  data

### 2. Cache observability is weak

Current logging and HUD surfaces normalize usage to prompt/completion/total and
do not preserve provider-native cache usage fields end to end.

Evidence:

- `aworld/logs/prompt_log.py` logs prompt structure and token breakdown, but not
  provider `request_id` or raw cache usage
- `aworld/models/model_response.py` normalizes usage and may discard provider
  usage detail outside prompt/completion/total
- `aworld-cli/src/aworld_cli/executors/stats.py` and
  `aworld-cli/src/aworld_cli/executors/local.py` only expose normalized usage
  into HUD snapshots

## Design Principles

- `llm_calls` is a truth source, not a derived convenience field
- append-only records are preferred over mutation of prior memory items
- trajectory should consume real request snapshots, not reconstruct them when a
  truth source exists
- cache usage is observability metadata, not trajectory semantics
- runtime message memory semantics must remain stable
- fallback behavior must exist for historical tasks that do not contain
  `llm_calls`

## Data Model

Add a new append-only `llm_calls` collection to runtime task context and
downstream task artifacts.

Each item should contain at least:

```json
{
  "request_id": "llm_xxx",
  "provider_request_id": "req_xxx",
  "task_id": "task_xxx",
  "agent_id": "agent_xxx",
  "model": "gpt-4.1",
  "provider_name": "openai",
  "status": "success",
  "started_at": "2026-05-07T10:00:00Z",
  "finished_at": "2026-05-07T10:00:01Z",
  "request": {
    "messages": [],
    "tools": [],
    "params": {
      "temperature": 0.2,
      "max_tokens": 4096,
      "stop": null
    }
  },
  "response": {
    "id": "chatcmpl_xxx",
    "message": {},
    "finish_reason": "tool_calls"
  },
  "usage_normalized": {
    "prompt_tokens": 100,
    "completion_tokens": 50,
    "total_tokens": 150
  },
  "usage_raw": {
    "prompt_tokens": 100,
    "completion_tokens": 50,
    "total_tokens": 150,
    "cache_hit_tokens": 80,
    "cache_write_tokens": 20,
    "prompt_tokens_details": {
      "cached_tokens": 80
    }
  }
}
```

Notes:

- `request.messages` must be captured **after** `before_llm_call` hook
  transforms, because that is the actual provider-bound request
- `provider_request_id` may be `null` when the provider does not expose one
- `usage_raw` must preserve provider-native shape as far as it is available
- `usage_normalized` remains the stable cross-provider interface

## Architecture

### Capture point

Capture and append `llm_calls` inside `aworld/models/llm.py`, not in
`aworld/agents/llm_agent.py`.

Why:

- `aworld/models/llm.py` already owns internal `request_id`
- it sits after hook mutation and before provider submission
- it sees provider response objects and can preserve raw usage and provider
  request ids
- it is the narrowest stable call boundary across agents

### Runtime storage

Store the append-only collection in task context, for example under
`context.context_info["llm_calls"]`.

The context layer already carries transient task-scoped execution state and is
the least invasive path for introducing a new truth source before broader
storage refactors.

### Durable exposure

Expose `llm_calls` in two downstream artifacts:

1. `TaskResponse.llm_calls`
2. task-level trajectory logging output from `aworld/runners/event_runner.py`

If workspace-scoped session logs are enabled through the existing memory plugin,
the task-completed path may also append `llm_calls` into the workspace session
log entry as an append-only durable record.

### Trajectory generation

Update `aworld/dataset/trajectory_strategy.py` so trajectory building:

1. prefers `llm_calls[*].request.messages` for the corresponding step when
   available
2. falls back to current memory reconstruction only for old tasks that do not
   contain `llm_calls`

This preserves backward compatibility while making new trajectories higher
fidelity.

### Observability surfaces

Prompt log, HUD, finished summaries, and plugin hook payloads may consume
associated usage data from `llm_calls`, but only as observability fields keyed
by `request_id`.

Cache usage must **not** be injected into:

- `trajectory.state.messages`
- reconstructed prompt history
- memory recall content

## Minimal File Cut

Expected implementation cut:

- `aworld/models/llm.py`
  capture final request snapshot, internal `request_id`, provider
  `request_id`, raw usage, normalized usage, and append `llm_calls`
- `aworld/models/model_response.py`
  preserve provider request identifiers and raw usage detail instead of reducing
  everything to prompt/completion/total
- `aworld/core/context/base.py`
  ensure `llm_calls` can survive task lifetime and merge behavior where needed
- `aworld/core/task.py`
  add `llm_calls` to `TaskResponse`
- `aworld/runners/event_runner.py`
  emit `llm_calls` with task artifacts and trajectory log output
- `aworld/dataset/trajectory_strategy.py`
  prefer `llm_calls[*].request.messages`, fallback to memory reconstruction
- `aworld/logs/prompt_log.py`
  log `request_id`, provider `request_id`, and raw cache usage association
- `aworld-cli/src/aworld_cli/executors/stats.py`
  pass through cache-related usage observability without making it semantic
- `aworld-cli/src/aworld_cli/executors/local.py`
  include associated cache usage in HUD and finished snapshots
- `aworld-cli/src/aworld_cli/plugin_capabilities/hooks.py`
  extend typed hook payloads as needed for associated raw usage observability
- `aworld-cli/src/aworld_cli/builtin_plugins/memory_cli/hooks/task_completed.py`
  optionally persist `llm_calls` into workspace session logs under the existing
  append-only durable path

## OpenSpec Update Plan

When this work moves from design to implementation, the existing change should
be extended in place:

- update `openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider/proposal.md`
  to include `llm_calls` truth-source capture as a scoped extension
- update `openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider/design.md`
  to define the new runtime observability path
- update the affected spec files under
  `openspec/changes/2026-04-28-aworld-cli-memory-hybrid-provider/specs/`
  so requirements cover `llm_calls`, request snapshot fidelity, and cache usage
  association

This design does not require a separate new change directory.

## Validation Plan

### 1. Truth-source capture tests

Add focused tests proving each LLM call appends one `llm_calls` entry that
contains:

- final request messages after hook transforms
- internal `request_id`
- provider `request_id`
- normalized usage
- raw usage with provider-native cache fields preserved

Existing starting point:

- `tests/hooks/test_llm_call_hooks.py`

### 2. Trajectory fidelity tests

Add tests proving:

- trajectory prefers `llm_calls[*].request.messages` when present
- old tasks without `llm_calls` still use the fallback reconstruction path
- cache usage fields do not appear inside `trajectory.state.messages`

### 3. HUD and logging tests

Add tests proving:

- HUD snapshots can expose associated cache usage data
- finished summaries can expose associated cache usage data
- prompt logs record `request_id`, provider `request_id`, and raw usage linkage
- plugin task progress and task completed payloads accept the added usage
  association fields without changing trajectory semantics

Existing starting points:

- `tests/plugins/test_runtime_hud_snapshot.py`
- `tests/plugins/test_shared_plugin_framework_imports.py`

### 4. Real-sample verification

Use two samples:

1. the provided trajectory log
   `/Users/wuman/Documents/logs/trajectory.2026-05-04_13-22-40_150155.log`
   as the before-state proof of weak fidelity
2. one or two fresh real task runs after implementation to confirm:
   - trajectory request messages match the actual sent prompt snapshot
   - cache usage is visible by request association
   - cache usage does not pollute trajectory semantics

## Non-Goals

- no full prompt-cache redesign
- no provider-specific cache optimization strategy work
- no attempt to maximize cache hit rate in this milestone
- no replacement of `AworldMemory` as runtime message memory
- no requirement to replay raw provider cache usage into prompts, memory recall,
  or trajectory semantics

## Risks And Boundaries

### Risk: Request snapshots drift from actual provider input

Mitigation:

- capture after `before_llm_call` hook transforms
- keep one append-only record per call

### Risk: Raw usage shape becomes provider-specific and messy

Mitigation:

- preserve raw usage as-is for observability
- keep normalized usage as the stable shared contract

### Risk: Trajectory logic breaks on historical tasks

Mitigation:

- preserve explicit fallback to current memory reconstruction

### Risk: Cache observability leaks into prompt semantics

Mitigation:

- keep cache fields off `trajectory.state.messages`
- treat cache usage as request-linked metadata only

## Acceptance Gate

This milestone is complete only when all of the following are true:

- `llm_calls` is persisted append-only for each model call
- internal and provider `request_id` are both carried when available
- raw cache-related usage fields survive end to end
- trajectory prefers real request snapshots from `llm_calls`
- cache usage does not appear in trajectory semantics
- HUD, finished output, or logs can observe cache usage by `request_id`
- at least one historical sample and one fresh real sample have been checked

## Next Smallest Follow-up Thread

After this milestone, the next smallest thread should be:

`use preserved llm_calls and request-linked cache usage to identify stable cacheable prompt prefixes and measure cache hit opportunities without changing trajectory semantics`
