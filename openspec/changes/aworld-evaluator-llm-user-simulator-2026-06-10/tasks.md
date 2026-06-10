## 1. Async Simulator Support

- [x] 1.1 Update `CallableRuntimeHarness` to await awaitable simulator `next_turn` results.
- [x] 1.2 Preserve existing scripted and single-prompt simulator behavior.

## 2. Adaptive LLM User Simulator

- [x] 2.1 Add `LLMUserSimulator`.
- [x] 2.2 Pass case, target, rollout state, last output, and turn index to its generator.
- [x] 2.3 Support string, mapping, `RolloutTurn`, `None`, and explicit stop outputs.
- [x] 2.4 Filter generated metadata through existing serializable turn serialization.

## 3. Verification

- [x] 3.1 Add focused tests for async simulator support, adaptive generation, stop behavior, and metadata filtering.
- [x] 3.2 Run runtime/evaluator regression tests.
- [x] 3.3 Validate this OpenSpec change with `openspec validate aworld-evaluator-llm-user-simulator-2026-06-10 --strict`.
