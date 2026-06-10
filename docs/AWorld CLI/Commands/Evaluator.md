# Evaluator

## What It Does

The evaluator command runs suite-backed evaluation flows for local targets and exposes the resulting report as a stable machine-readable contract.

It is the official CLI entrypoint for the framework substrate in `aworld.evaluations`: the CLI resolves targets,
workspace manifests, output paths, and hooks, while suite semantics, execution-backed state normalization, scoring, and
gate decisions remain framework-owned.

Use it when you want to:

- run a built-in evaluator suite such as `app-evaluator`
- load declaration-backed evaluator suites from workspace manifests
- evaluate existing source records such as task+answer JSONL files or AWorld trajectory logs
- inspect which suites match a target
- export the evaluator report schema
- validate a saved evaluator report in automation

## Commands

Top-level CLI usage:

```bash
aworld-cli evaluator --target ./artifact
aworld-cli evaluator --target ./artifact --suite app-evaluator
aworld-cli evaluator --list-suites
aworld-cli evaluator --list-suites --target ./artifact
aworld-cli evaluator --print-report-schema
aworld-cli evaluator --validate-report ./.aworld/evaluations/artifact.app-evaluator.json
```

Source-backed usage:

```bash
aworld-cli evaluator \
  --input ./task_answers.jsonl \
  --kind task-answer \
  --judge-agent ./eval/answer_judge/agent.md \
  --out-dir ./reports

aworld-cli evaluator \
  --input ~/Documents/logs/trajectory.log \
  --kind aworld-trajectory-log \
  --task-id task_20260609193335 \
  --judge-agent ./eval/trajectory_evaluator/agent.md \
  --out-dir ./reports
```

For `task-answer` JSONL inputs, the default fields are `id`, `input`, and `answer`. Use `--id-field`, `--task-field`, and `--answer-field` only when the file uses different names.

Useful options:

```bash
aworld-cli evaluator --target ./artifact --output ./report.json
aworld-cli evaluator --target ./artifact --interactive-approval
aworld-cli evaluator --input ./task_answers.jsonl --kind task-answer --judge-agent ./agent.md --output ./report.json
```

## Declared Suite Manifests

Evaluator suites can be declared under `.aworld/evaluators/*.json` and are loaded before suite resolution. This keeps the runtime on top of AWorld's existing runner and task substrate while letting a workspace expose stricter or context-specific evaluator variants without forking builtin code.

Current manifest scope is intentionally narrow:

- `base_suite` must be `app-evaluator`
- `suite_id` is required and becomes the suite name exposed to `aworld-cli evaluator`
- `target_kinds` optionally narrows matching to `file`, `directory`, and/or `image`
- `gate_policy`, `metadata`, and `priority` override selection and gating behavior on top of the builtin suite

Minimal example:

```json
{
  "suite_id": "strict-ui",
  "base_suite": "app-evaluator",
  "target_kinds": ["file", "directory"],
  "gate_policy": {
    "metric_name": "score",
    "pass_threshold": 0.92,
    "approval_threshold": 0.8
  },
  "metadata": {
    "owner": "qa"
  },
  "priority": 120
}
```

See [declared_evaluator_suite.example.json](/Users/wuman/Documents/workspace/aworld-mas/aworld/examples/aworld_quick_start/cli/declared_evaluator_suite.example.json) for a complete example. The current manifest schema is exported by `aworld_cli.evaluator_runtime.get_declared_evaluator_suite_schema()`.

Resolution rules:

- builtin suites are always available
- declared suites are discovered relative to the evaluation target workspace, not just the current shell cwd
- declared manifests currently extend `app-evaluator`; they are not yet a generic user-defined suite authoring API
- `--list-suites --target ...` and actual evaluator execution use the same target-relative discovery path

## Plugin Hooks

`aworld-cli evaluator` is a builtin plugin-backed command with narrow lifecycle hook points intended for CLI assembly concerns, not framework scoring semantics.

Available hook points:

- `evaluator.pre_discover`: inspect or annotate target/workspace inputs before suite discovery
- `evaluator.post_discover`: react to resolved suite candidates
- `evaluator.pre_run`: add lightweight CLI metadata before evaluation starts
- `evaluator.post_run`: upload or post-process the completed report
- `evaluator.render_summary`: augment rendered terminal summary text

Current event payloads:

- `evaluator.pre_discover`: `target`, `workspace_path`
- `evaluator.post_discover`: `target`, `workspace_path`, `suite_names`
- `evaluator.pre_run` for target mode: `mode`, `target`, `suite`, `workspace_path`
- `evaluator.pre_run` for source mode: `mode`, `input`, `kind`, `task_id`, `judge_agent`, `agent`, `workspace_path`, `output_path`
- `evaluator.post_run` for target mode: `mode`, `report`, `target`, `suite`, `workspace_path`
- `evaluator.post_run` for source mode: `mode`, `report`, `input`, `kind`, `task_id`, `judge_agent`, `agent`, `workspace_path`, `output_path`
- `evaluator.render_summary`: `report`, `workspace_path`

Hook boundaries:

- mutable hook state is limited to lightweight CLI assembly metadata
- hooks should not replace suite logic, judge logic, or gate calculation
- suitable side effects include report upload, notifications, and summary augmentation

## Report Contract

Evaluator reports are JSON documents with a stable top-level format marker:

```json
{
  "report_format": {
    "id": "aworld.evaluator.report",
    "version": 1
  }
}
```

Key report sections:

- `metrics`: normalized aggregate metrics for the resolved suite
- `results`: per-case judge output plus normalized per-case metrics
- `gate`: structured `pass` / `fail` / `needs_approval` decision
- `automation`: exit-code-oriented summary fields for scripts and CI
- `suite_selection`: resolved/defaulted suite selection diagnostics
- `source_selection`: source input diagnostics for source-backed `aworld-cli evaluator --input ...`
- `approval`: approval decision metadata when the gate requires human confirmation

See [evaluator_report.example.json](/Users/wuman/Documents/workspace/aworld-mas/aworld/examples/aworld_quick_start/cli/evaluator_report.example.json) for a minimal example.

## Typical Workflow

1. Inspect matching suites with `aworld-cli evaluator --list-suites --target ./artifact`.
2. Run evaluation with `aworld-cli evaluator --target ./artifact`.
3. For existing outputs, run source-backed evaluation with `aworld-cli evaluator --input <file> --kind task-answer --judge-agent <agent.md>`.
4. Save or collect the emitted JSON report.
5. Validate persisted reports with `aworld-cli evaluator --validate-report <file>`.
6. Export the current JSON Schema with `aworld-cli evaluator --print-report-schema` when integrating with external tooling.

## Exit Codes

- `0`: evaluation passed, schema is valid, or metadata command succeeded
- `2`: evaluation gate failed
- `3`: evaluation requires approval and is not approved
- `4`: evaluator report validation failed

## Notes And Limits

- `--list-suites --target ...` shows only suites matching the target and prints the deterministic default suite.
- declared suite manifests are discovered from `.aworld/evaluators/*.json` relative to the evaluation target workspace.
- declared suite manifests currently layer on `app-evaluator` only; they are not a generic suite authoring format yet.
- `--print-report-schema` prints the current JSON Schema for `aworld.evaluator.report`.
- `--validate-report` validates an existing JSON report against that schema without re-running evaluation.
- `aworld-cli evaluator --input ...` currently supports `task-answer` and `aworld-trajectory-log`; task-only execution sources and generic serialized-state sources are intentionally deferred until the framework provides those source kinds.
- the CLI command is an assembly/product layer; reusable evaluator building blocks stay in `aworld/evaluations/**`.
