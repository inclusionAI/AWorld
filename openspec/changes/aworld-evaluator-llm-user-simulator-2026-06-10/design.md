## Context

Runtime composition currently includes:

- `ScriptedUserSimulator` for fixed user turns
- `SinglePromptUserSimulator` for one-shot prompts
- `CallableRuntimeHarness` for multi-turn rollout execution

This is enough for deterministic tests but not for adaptive dialog evaluation. A realistic user simulator should inspect the current conversation, the last assistant output, and case goal before deciding whether to continue, clarify, challenge, or stop.

## Goals / Non-Goals

**Goals:**

- Add a provider-agnostic adaptive simulator class.
- Support sync and async simulator generation.
- Keep generated turns serializable and report-safe.
- Give the generator enough structured context to implement LLM-backed user behavior.
- Preserve existing scripted and single-prompt behavior.

**Non-Goals:**

- Adding a concrete LLM provider client.
- Storing live model clients, credentials, or API responses in suite/report state.
- Adding training or optimizer integration.
- Replacing deterministic scripted simulators.

## Decisions

### 1. Use injected generator callable

`LLMUserSimulator` accepts a `turn_generator` callable. The callable receives:

- `case`
- `target`
- `state`
- `last_output`
- `turn_index`

It may return:

- `str`: user content
- `RolloutTurn`: full turn
- `Mapping`: `{"content": "...", "metadata": {...}}`
- `Mapping` with `{"stop": True}` or `None`: stop conversation

This keeps provider integration outside the substrate while making the runtime API ready for LLM-backed adapters.

### 2. Await simulator outputs in the harness

`CallableRuntimeHarness` should call `await _maybe_await(simulator.next_turn(...))`. Existing sync simulators keep working, and async LLM-backed simulators become first-class.

### 3. Keep metadata serializable

Generated mapping metadata is filtered through existing `RolloutTurn.to_dict()` serialization. Live clients remain in the simulator instance, not in `RolloutState`.

### 4. Stop behavior is explicit

The simulator returns `None` or `{"stop": True}` to end the rollout. This keeps max-turn enforcement in `CallableRuntimeHarness` and stop-decision semantics in the simulator.

## Risks / Trade-offs

- [Provider ambiguity] -> Mitigation: this change is adapter-ready but provider-neutral.
- [Non-determinism in tests] -> Mitigation: tests use deterministic fake generators.
- [Live handle leakage] -> Mitigation: turn metadata is serialized through existing filtering; simulator internals are never copied into state.

## Migration Plan

1. Add async simulator support to `CallableRuntimeHarness`.
2. Add `LLMUserSimulator`.
3. Add tests for string, mapping, turn, stop, and async generation behavior.
4. Keep existing simulator tests green.

## Deferred Questions

- Concrete provider adapters should be separate changes.
- Training/optimizer integration remains deferred until evaluator runtime primitives stabilize.
