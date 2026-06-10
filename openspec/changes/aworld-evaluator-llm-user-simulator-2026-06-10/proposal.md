# AWorld Evaluator LLM User Simulator

## Why

Scripted and single-prompt simulators are useful for deterministic smoke tests, but conversational agent evaluation often needs an adaptive user that reacts to the assistant's previous output and rollout state. The evaluator runtime already owns turns and rollout state, so the next step is a provider-agnostic LLM-backed simulator contract that can drive multi-turn conversations without coupling the substrate to one model vendor.

## What Changes

- Add an adaptive `LLMUserSimulator` that delegates user-turn generation to an injected sync or async callable.
- Allow `CallableRuntimeHarness` to await simulator `next_turn` implementations.
- Pass case input, target metadata, current rollout state, previous assistant output, and turn index to the simulator generator.
- Support generator outputs as strings, mappings, `RolloutTurn`, or explicit stop signals.
- Preserve only serializable simulator metadata in emitted turns.

## Impact

- Affected code: `aworld/evaluations/runtime_composition.py`.
- Affected tests: add focused coverage for async simulator support, adaptive LLM-style generation, stop behavior, and report-safe metadata.
- Non-goal: this change does not ship a concrete OpenAI/Anthropic client adapter or manage API keys.
