# AWorld Evaluator Input Sources

## Context

AWorld's evaluator stack now has the core pieces for serious agent evaluation:

- suite/case/judge/gate/report substrate
- execution adapters for static, agent, task, and program modes
- runtime-composed rollout harnesses and serializable `RolloutState`
- outcome/state checks, trajectory scorers, standard metrics, trials, environment isolation hooks, and LLM user simulators

The missing layer is input normalization. The framework can evaluate well once a caller has produced `EvalCaseDef` plus `EvalState` or `RolloutState`, but external evaluation data usually arrives as files or logs. The current manual trajectory-log test manually implements parsing, replay, markdown-agent loading, schema flattening, and suite wiring. That is useful as a spike, but it is not the framework-level integration experience AWorld should expose.

This change introduces a framework-owned input source layer. It should not create a separate evaluator stack. It should feed existing `EvalSuiteDef`, `EvalCaseDef`, `EvalExecutionSpec`, `RuntimeHarness`, `JudgeBackend`, and report assembly paths.

## Goals / Non-Goals

**Goals:**

- Provide reusable source primitives for external evaluation input records.
- Support task+answer inputs that should be judged without runtime execution.
- Support AWorld trajectory-log inputs by parsing them once in framework code and replaying them into `RolloutState`.
- Keep task-only and serialized-state sources as follow-on implementations of the same protocol rather than first-version built-ins.
- Keep source parsing, state adaptation, judge backend wiring, and suite creation discoverable from `aworld.evaluations`.
- Make the manual trajectory-log regression a small consumer of framework APIs rather than a copy of framework internals.

**Non-Goals:**

- Adding CLI commands or argument parsing in this change.
- Adding first-version built-ins for task-only execution sources or generic serialized-state files.
- Adding database, object-store, or remote log connectors.
- Executing untrusted code from input files.
- Running shell commands or external environment checks from source adapters.
- Replacing `EvaluationConfig`, `EvaluateRunner`, `EvalSuiteDef`, or runtime-composition harnesses.
- Adding production sandbox or clean-environment reset implementations.

## Proposed Abstractions

### 1. `EvalSource`

`EvalSource` is a trusted framework object that enumerates evaluation records and converts them into cases.

Conceptually:

```python
class EvalSource(Protocol):
    def iter_records(self) -> Iterable[EvalSourceRecord]: ...
    def to_cases(self) -> tuple[EvalCaseDef, ...]: ...
```

`EvalSourceRecord` should contain:

- `case_id`
- `input`
- optional `expected`
- optional existing `answer`
- optional existing `state`
- optional source metadata
- optional raw source payload for trusted adapters

Source records must be serializable or sanitize non-serializable values before report state.

If a source kind uniquely determines its replay adapter, the source should expose `default_adapter()` or equivalent metadata. Callers may override the adapter for advanced cases, but the happy path should not require both `source=AWorldTrajectoryLogSource(...)` and `state_adapter=TrajectoryLogStateAdapter()`.

### 2. `EvalStateAdapter`

`EvalStateAdapter` converts source records that already contain outputs into normalized state.

First-version examples:

- `AnswerStateAdapter`: turns a task+answer record into `EvalState(answer=answer, completion=[answer])`
- `TrajectoryLogStateAdapter`: turns one AWorld trajectory-log record into `RolloutState`

Task-only records do not use a replay adapter; they flow through existing execution modes (`AGENT`, `TASK`, `PROGRAM`, or `STATIC` when judge-only). That path is intentionally deferred from this first version because the current simplification target is existing-output replay.

### 3. `ReplayRuntimeHarness`

`ReplayRuntimeHarness` is a runtime harness that receives source records and state adapters, then returns `RolloutState` or bridgeable state without re-executing the target.

The harness owns:

- selecting the source record for the case
- applying the adapter
- preserving source metadata
- deriving tool calls, usage, timing, and standard metrics where available

It does not own judging, scoring, gate decisions, trial expansion, or environment reset.

### 4. Built-in Sources

The first implementation should include the file-backed sources that have immediate consumers:

- `JsonlTaskAnswerSource`
  - default fields: `id`, `input`, `answer`; optional `expected`, optional metadata
  - field names may be overridden by constructor options
  - used with `AnswerStateAdapter`
- `AWorldTrajectoryLogSource`
  - reads AWorld line-oriented trajectory logs
  - extracts records by task id
  - used with `TrajectoryLogStateAdapter`

The API should not hardcode these as evaluator types. They are source/adapters that feed the same suite-backed evaluator.

Deferred source implementations:

- `JsonlTaskSource` for task-only records that require runtime execution.
- `RolloutStateFileSource` for generic serialized `EvalState` or `RolloutState` records.

### 5. Markdown Agent Loading

The manual regression showed a separate but related gap: a judge agent may be provided as `agent.md`, while framework loading currently favors `SKILL.md`.

This change should add a framework helper, not a test-local workaround:

```python
load_agent_markdown(path) -> Agent
AgentJudgeBackend.from_agent_markdown(path, prompt_builder=..., timeout_seconds=...)
```

The helper can internally reuse skill loading or instantiate an AWorld agent directly, but callers should not materialize temporary `SKILL.md` files.

### 6. Judge Payload Normalization

The trajectory evaluator agent currently returns:

```json
{
  "weighted_score": 78,
  "dimensions": {
    "A1_groundedness": {"score": 4}
  }
}
```

The evaluator substrate prefers flat judge payload fields:

```json
{"score": 78, "A1_groundedness": 4}
```

This change should avoid hidden global flattening. Instead, add explicit normalization support:

- `JudgeSchemaDef(normalizer=callable)` or equivalent
- a built-in trajectory judge output model/normalizer for dimensions-style reports

Suite authors should opt into a normalizer so report contracts remain explicit.

## Data Flow

### Deferred: Task-only file

```text
JsonlTaskSource -> EvalCaseDef -> existing EvalExecutionSpec -> EvalState -> judge/scorers/gate/report
```

### Task + answer file

```text
JsonlTaskAnswerSource -> EvalCaseDef + answer record -> AnswerStateAdapter -> EvalState -> judge/scorers/gate/report
```

### Deferred: Serialized rollout state file

```text
RolloutStateFileSource -> RolloutStateAdapter -> RolloutState/EvalState -> judge/scorers/gate/report
```

### AWorld trajectory log

```text
AWorldTrajectoryLogSource -> TrajectoryLogStateAdapter -> RolloutState -> judge/scorers/gate/report
```

## API Shape

Expected high-level usage:

```python
source = JsonlTaskAnswerSource(
    path="task_answers.jsonl",
)

suite = create_source_eval_suite(
    source=source,
    judge_backend=AgentJudgeBackend.from_agent_markdown("eval/judge/agent.md"),
    judge_schema=JudgeSchemaDef(output_model=AnswerJudgeOutput),
    gate_policy=GatePolicyDef(metric_name="score", pass_threshold=70),
)

report = await run_evaluation_flow(EvaluationFlowDef(target={"kind": "source"}, suite=suite))
```

`create_source_eval_suite(...)` must return a normal `EvalSuiteDef`. It is syntax sugar over the existing suite substrate, not a second suite type or execution stack.

Expected trajectory-log usage:

```python
source = AWorldTrajectoryLogSource(
    path="~/Documents/logs/trajectory.log",
    task_ids=["task_20260609193335"],
)

suite = create_source_eval_suite(
    source=source,
    judge_backend=AgentJudgeBackend.from_agent_markdown("eval/trajectory_evaluator/agent.md"),
    judge_schema=TrajectoryJudgeSchema.default(),
    gate_policy=TrajectoryJudgeGate.default(),
)
```

## Risks / Trade-offs

- [Too much abstraction] -> Mitigation: keep first version limited to task+answer and trajectory-log file-backed sources; allow explicit adapter overrides only for advanced callers.
- [Case-by-case source creep] -> Mitigation: require new sources to implement the same record/state adapter contracts rather than custom evaluator flows.
- [Untrusted file assumptions] -> Mitigation: sources parse data only; they do not execute code or commands.
- [Schema normalization ambiguity] -> Mitigation: make normalizers explicit on schema/suite.
- [Runtime vs replay confusion] -> Mitigation: document that current task+answer and trajectory sources replay existing outputs; future task-only sources will execute through normal execution specs.

## Migration Plan

1. Add source record and source protocols, including optional source-provided default adapters.
2. Add JSONL task+answer source with default `id`, `input`, and `answer` field names.
3. Add answer-record state adapter.
4. Add trajectory-log source and trajectory-log state adapter.
5. Add replay harness and source-backed suite factory.
6. Add markdown-agent judge backend factory.
7. Add explicit judge payload normalizer support or built-in trajectory judge schema.
8. Refactor the manual trajectory-log test to use the new framework APIs.
9. Keep existing suite-backed APIs compatible.

## Deferred Questions

- Task-only execution sources and generic serialized-state file sources should be separate follow-on implementations of the same protocol.
- Concrete remote source connectors should be separate changes.
- CLI integration should be a later consumer of this framework layer.
- Dataset registry integration can be considered after file-backed sources settle.
- Environment reset remains owned by the environment-isolation capability, not by sources.
- The trajectory evaluator `agent.md` currently contains prompt-local trajectory extraction guidance. This change removes the test-local parser duplication; a later cleanup should either feed the agent framework-extracted trajectory content directly or explicitly keep the prompt-local parsing instructions as evaluator-agent policy.
