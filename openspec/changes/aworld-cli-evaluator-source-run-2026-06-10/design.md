# AWorld CLI Evaluator Source Run

## Context

`aworld-cli evaluator` currently runs suite-backed evaluations for local targets. It is already exposed through the builtin plugin command model and uses evaluator hooks around discovery, pre-run, post-run, and summary rendering. That is the right extension surface for CLI-level concerns.

The new framework input-source layer will make evaluation inputs first-class: task-only files, task+answer files, serialized states, and AWorld trajectory logs all normalize into source records and framework state adapters. The CLI should not duplicate parsing or replay logic. Its job is to assemble a source-backed flow from user-facing arguments and then call the same `run_evaluation_flow` substrate used by code callers.

## Goals / Non-Goals

**Goals:**

- Provide a simple CLI path for evaluating files/logs without writing a Python test harness.
- Keep the evaluator command plugin-backed and compatible with existing top-level command registration.
- Reuse existing evaluator hooks and extend their payloads for source-backed runs.
- Make common source kinds usable with a small argument set.
- Keep framework evaluation semantics in `aworld.evaluations`, not in CLI handlers.
- Preserve existing `--target` / `--suite` evaluator behavior.

**Non-Goals:**

- Adding a separate `aworld-cli trajectory-eval` command.
- Making trajectory logs a special CLI-only feature.
- Implementing source parsing, state replay, judge normalization, scoring, or gate logic in `aworld-cli`.
- Replacing the plugin command system or hook infrastructure.
- Adding remote storage connectors, sandbox lifecycle management, or training/optimizer flows.

## Command Shape

The canonical source-backed path should be:

```bash
aworld-cli evaluator \
  --input ~/Documents/logs/trajectory.log \
  --kind aworld-trajectory-log \
  --task-id task_20260609193335 \
  --judge-agent eval/trajectory_evaluator/agent.md \
  --out-dir eval/trajectory_evaluator/reports
```

Task+answer files:

```bash
aworld-cli evaluator \
  --input task_answers.jsonl \
  --kind task-answer \
  --judge-agent eval/answer_judge/agent.md \
  --out-dir reports
```

The default JSONL fields are `id`, `input`, and `answer`. `--id-field`, `--task-field`, and `--answer-field` are override flags for files that do not follow that convention.

Task-only files are a follow-on source kind once the framework input-source layer adds task-only source support:

```bash
aworld-cli evaluator \
  --input tasks.jsonl \
  --kind task \
  --id-field task_id \
  --task-field task \
  --agent ./agent.md \
  --judge-agent eval/answer_judge/agent.md \
  --out-dir reports
```

`--kind auto` can be added once detection is reliable, but the first version should require explicit `--kind` to keep failures predictable.

## CLI Boundary

The evaluator command owns:

- argument parsing and validation
- path normalization
- selecting a framework source class by `--kind`
- passing field mappings and task filters to the source
- selecting a framework state adapter or execution spec
- loading a judge agent through framework helpers
- invoking framework flow execution
- writing the report and rendering a summary
- invoking evaluator hooks with source-aware payloads

The evaluator command does not own:

- parsing trajectory internals
- converting source records into `EvalState` or `RolloutState`
- judge payload normalization
- scorer implementation
- gate implementation
- report schema semantics
- trial, sandbox, or simulator semantics

## Plugin And Hook Integration

The implementation should follow existing CLI conventions:

- keep `EvaluatorTopLevelCommand` as the command object exposed through the builtin evaluator plugin entrypoint
- route source-backed `--input` arguments through the same command object without creating a new top-level command
- keep source-backed flow assembly in `aworld_cli.evaluator_runtime`
- use `PluginManager`, `get_builtin_plugin_roots`, `load_plugin_hooks`, and `_run_evaluator_hooks` as the hook path

Hook payloads should gain source-aware fields while preserving existing keys:

- `mode`: `target` or `source`
- `input`: resolved input path for source mode
- `kind`: source kind for source mode
- `task_id` or `task_ids` when provided
- `judge_agent`: resolved judge-agent path when provided
- `agent`: resolved execution agent path/name when provided
- `workspace_path`
- `output_path` or report path after resolution

Allowed hook behavior remains CLI-scoped:

- pre-discover/pre-run hooks may add metadata or override summary fields
- post-run hooks may upload, notify, or record report metadata
- render hooks may append summary text
- hooks must not replace framework execution, scoring, gate decisions, or report contracts

## Data Flow

```text
CLI args
  -> EvaluatorTopLevelCommand parser
  -> aworld_cli.evaluator_runtime source runner
  -> framework EvalSource + EvalStateAdapter / execution spec
  -> create source-backed EvalSuiteDef / EvaluationFlowDef
  -> run_evaluation_flow
  -> report write + render summary + hooks
```

For the trajectory-log manual case, the CLI path should be equivalent to the current pytest invocation but without test-local glue:

```text
--input trajectory.log
  -> AWorldTrajectoryLogSource
  -> TrajectoryLogStateAdapter
  -> ReplayRuntimeHarness
  -> AgentJudgeBackend.from_agent_markdown(agent.md)
  -> typed schema + gate + report
```

## Compatibility

Existing usage remains valid:

```bash
aworld-cli evaluator --target ./some-target --suite app-evaluator
```

The new `evaluator --input ...` source path should not break `--list-suites`, `--print-report-schema`, `--validate-report`, or interactive approval behavior.

## Risks / Trade-offs

- [Command ambiguity] `evaluator --target` and `evaluator --input` are mutually exclusive, so parser errors must clearly explain which mode is active.
- [Too many flags] Field mappings are necessary for generic JSONL. Presets can reduce repeated arguments later.
- [Case-specific drift] Avoid canonical `evaluator trajectory-log`; if aliases are added later, they should delegate to `evaluator --input ... --kind aworld-trajectory-log`.
- [Plugin overreach] Hook contracts must state that plugins customize CLI assembly and side effects only.

## Migration Plan

1. Land framework input sources first, including the manual test refactor.
2. Add source-backed `--input` parser mode to the existing evaluator command.
3. Add source-run runtime helper that calls framework APIs.
4. Extend evaluator hook event payloads with source mode fields.
5. Add CLI tests for argument validation and runtime delegation.
6. Add one opt-in manual command example for the trajectory evaluator case.
7. Keep old target/suite command behavior unchanged.
