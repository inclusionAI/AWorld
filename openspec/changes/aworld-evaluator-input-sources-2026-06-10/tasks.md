## 1. Source Model

- [x] 1.1 Add `EvalSourceRecord` with case id, input, expected, answer/state payload, source metadata, and raw payload.
- [x] 1.2 Add an `EvalSource` protocol/base class that enumerates records and lowers them into `EvalCaseDef` values.
- [x] 1.3 Add source-provided default adapter support for source kinds with one obvious replay adapter.
- [x] 1.4 Ensure source metadata remains serializable and does not retain file handles, clients, or live runtime objects.

## 2. Built-in File Sources

- [x] 2.1 Add `JsonlTaskAnswerSource` for task + answer records that should be judged without re-execution, with default field names `id`, `input`, and `answer`.
- [x] 2.2 Add override options for task+answer field names.
- [x] 2.3 Add `AWorldTrajectoryLogSource` for line-oriented AWorld trajectory logs with task-id selection.
- [x] 2.4 Add validation and error messages for missing id/input/answer fields and missing trajectory task ids.
- [x] 2.5 Defer task-only and generic serialized-state sources to follow-on implementations of the same protocol.

## 3. State Adapters and Replay Harness

- [x] 3.1 Add `EvalStateAdapter` protocol/base class.
- [x] 3.2 Add `AnswerStateAdapter` that converts task+answer records into `EvalState`.
- [x] 3.3 Add `TrajectoryLogStateAdapter` that converts AWorld trajectory-log records into `RolloutState`, including answer, evidence, trajectory, tool calls, usage, timing, outcome, and standard metrics.
- [x] 3.4 Ensure `JsonlTaskAnswerSource` and `AWorldTrajectoryLogSource` expose their default adapters.
- [x] 3.5 Add `ReplayRuntimeHarness` that applies a source record and adapter without re-executing the target.

## 4. Suite Factory Helpers

- [x] 4.1 Add `create_source_eval_suite(...)` helper that wires source cases, replay harness, judge schema/backend, scorers, and gate policy.
- [x] 4.2 Make `state_adapter` optional when the source provides a default adapter.
- [x] 4.3 Ensure `create_source_eval_suite(...)` returns a normal `EvalSuiteDef`.
- [x] 4.4 Support task+answer and trajectory-log sources with replay adapters.
- [x] 4.5 Preserve existing `run_evaluation_flow` report shape and gate behavior.

## 5. Markdown Agent Judge Backend

- [x] 5.1 Add framework helper to load `agent.md` into an AWorld `Agent` without test-local temporary `SKILL.md` materialization.
- [x] 5.2 Add `AgentJudgeBackend.from_agent_markdown(...)` or equivalent factory.
- [x] 5.3 Preserve existing `AgentJudgeBackend(system_prompt=...)` behavior.

## 6. Judge Payload Normalization

- [x] 6.1 Add explicit judge payload normalization support on `JudgeSchemaDef` or a closely scoped suite helper.
- [x] 6.2 Add built-in normalizer/model for dimensions-style trajectory judge reports.
- [x] 6.3 Ensure normalizers run before typed model validation and report assembly.
- [x] 6.4 Do not add hidden global `dimensions -> flat` behavior.

## 7. Refactor Manual Trajectory Regression

- [x] 7.1 Replace test-local trajectory parser with `AWorldTrajectoryLogSource`.
- [x] 7.2 Replace test-local replay harness with `ReplayRuntimeHarness` plus `TrajectoryLogStateAdapter`.
- [x] 7.3 Replace test-local markdown agent backend with framework `AgentJudgeBackend.from_agent_markdown`.
- [x] 7.4 Replace test-local schema flattening with explicit trajectory judge normalizer/model.
- [x] 7.5 Keep the manual LLM-backed regression opt-in through explicit pytest parameters.

## 8. Verification

- [x] 8.1 Add focused tests for task+answer source replay without execution.
- [x] 8.2 Add focused tests for AWorld trajectory-log replay.
- [x] 8.3 Add focused tests for source default adapter selection.
- [x] 8.4 Add focused tests for markdown-agent judge backend loading.
- [x] 8.5 Run evaluator regression tests.
- [x] 8.6 Validate this OpenSpec change with `openspec validate aworld-evaluator-input-sources-2026-06-10 --strict`.
