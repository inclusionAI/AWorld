# Evaluator

## What It Does

The evaluator command runs suite-backed evaluation flows for local targets and exposes the resulting report as a stable machine-readable contract.

Use it when you want to:

- run a built-in evaluator suite such as `app-evaluator`
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

Useful options:

```bash
aworld-cli evaluator --target ./artifact --output ./report.json
aworld-cli evaluator --target ./artifact --interactive-approval
```

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

See [evaluator_report.example.json](/Users/wuman/Documents/workspace/aworld-mas/aworld/examples/aworld_quick_start/cli/evaluator_report.example.json) for a minimal example.

## Typical Workflow

1. Inspect matching suites with `aworld-cli evaluator --list-suites --target ./artifact`.
2. Run evaluation with `aworld-cli evaluator --target ./artifact`.
3. Save or collect the emitted JSON report.
4. Validate persisted reports with `aworld-cli evaluator --validate-report <file>`.
5. Export the current JSON Schema with `aworld-cli evaluator --print-report-schema` when integrating with external tooling.

## Exit Codes

- `0`: evaluation passed, schema is valid, or metadata command succeeded
- `2`: evaluation gate failed
- `3`: evaluation requires approval and is not approved
- `4`: evaluator report validation failed

## Notes And Limits

- `--list-suites --target ...` shows only suites matching the target and prints the deterministic default suite.
- `--print-report-schema` prints the current JSON Schema for `aworld.evaluator.report`.
- `--validate-report` validates an existing JSON report against that schema without re-running evaluation.
