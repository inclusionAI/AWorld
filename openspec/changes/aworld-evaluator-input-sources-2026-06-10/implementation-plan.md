# Implementation Plan: AWorld Evaluator Input Sources

## Phase 1: Core Source Contracts

1. Add `aworld/evaluations/sources.py`.
2. Define `EvalSourceRecord` as a serializable dataclass.
3. Define `EvalSource` protocol/base with `iter_records()` and `to_cases()`.
4. Add source-provided default adapter support for obvious replay pairs.
5. Add unit tests for record serialization, case lowering, and default adapter selection.

Expected commit: source contracts and default adapter tests.

## Phase 2: File Sources

1. Implement JSONL reader helpers with clear field mapping.
2. Implement `JsonlTaskAnswerSource` with default fields `id`, `input`, and `answer`.
3. Implement override options for JSONL field names.
4. Add tests for valid records, missing required fields, and metadata preservation.

Expected commit: task+answer file-backed source implementation.

## Phase 3: State Adapters and Replay

1. Add `aworld/evaluations/state_adapters.py`.
2. Define `EvalStateAdapter`.
3. Implement `AnswerStateAdapter`.
4. Implement `ReplayRuntimeHarness` or add it to `runtime_composition.py` if it fits better with existing harnesses.
5. Add tests proving source records can be replayed into state and reports.

Expected commit: replay adapter path for existing outputs.

## Phase 4: AWorld Trajectory Log Source

1. Move trajectory log parsing out of the manual test into framework code.
2. Implement `AWorldTrajectoryLogSource`.
3. Implement `TrajectoryLogStateAdapter`.
4. Expose `TrajectoryLogStateAdapter` as the trajectory source default adapter.
5. Derive evidence, final answer, trajectory steps, tool calls, outcome, usage/timing defaults, and standard metrics.
6. Add focused tests with small synthetic trajectory logs.

Expected commit: trajectory log source and replay adapter.

## Phase 5: Suite Helpers

1. Add `create_source_eval_suite(...)` helper.
2. Support replay-backed sources through `ReplayRuntimeHarness`.
3. Use source default adapters when `state_adapter` is omitted.
4. Ensure the helper returns a normal `EvalSuiteDef`.
5. Ensure helper remains optional; callers can still manually construct `EvalSuiteDef`.

Expected commit: suite factory helpers.

## Phase 6: Markdown Agent Judge Backend

1. Add framework-level `load_agent_markdown(path)` helper.
2. Add `AgentJudgeBackend.from_agent_markdown(...)` factory or an equivalent named constructor.
3. Reuse existing AWorld Agent execution path.
4. Add tests proving `agent.md` metadata/body become an executable judge agent.

Expected commit: markdown agent judge backend.

## Phase 7: Judge Normalization

1. Add explicit normalizer hook to `JudgeSchemaDef` or introduce a trajectory judge schema helper.
2. Normalize dimensions-style judge reports before typed validation.
3. Add tests for nested dimensions input and flat output.

Expected commit: explicit judge payload normalization.

## Phase 8: Manual Test Refactor

1. Refactor `tests/evaluations/test_trajectory_log_manual_case.py` to use source/adapters/backend factories.
2. Remove test-local parser, replay harness, markdown-agent materializer, and schema flattening.
3. Keep explicit pytest parameters and LLM skip behavior.
4. Run the manual e2e with an explicit local task id when credentials/logs are available.

Expected commit: manual trajectory regression uses framework APIs.

## Verification Commands

```bash
pytest tests/evaluations/test_evaluation_substrate.py tests/evaluations/test_runtime_composition.py -q
pytest tests/evaluations/test_trajectory_log_manual_case.py -q
openspec validate aworld-evaluator-input-sources-2026-06-10 --strict
```
