# Implementation Plan

## Commit 1: Parser Shape

- Confirm the framework input-source change has landed.
- Add source-backed `run` parsing to `EvaluatorTopLevelCommand`.
- Add JSONL field defaults for task+answer sources.
- Keep the builtin evaluator plugin command as the registration path.
- Add tests for command parsing and incompatible argument combinations.

## Commit 2: Runtime Delegation

- Add a `run_evaluator_source_cli(...)` helper in `aworld_cli.evaluator_runtime`.
- Map initially supported `--kind` values to framework input-source APIs.
- Return clear unsupported-kind errors for source kinds not yet implemented by the framework layer.
- Build source-backed flows through framework helpers only.
- Add runtime delegation tests with monkeypatched framework helpers.

## Commit 3: Hooks And Reporting

- Extend evaluator hook payloads for source-backed mode.
- Preserve existing target-mode hook payloads.
- Add automation/report metadata for source input, kind, task ids, and output path.
- Add hook payload and summary tests.

## Commit 4: Examples And Manual Regression

- Document the trajectory-log command that replaces the pytest-specific manual invocation.
- Add task+answer examples.
- Mention task-only examples only after the framework source layer supports task-only sources.
- Keep the existing pytest manual regression as a lower-level framework e2e until the source API is fully adopted.

## Verification

- `pytest` for evaluator CLI tests.
- Evaluator framework tests from the input-source change.
- `openspec validate aworld-cli-evaluator-source-run-2026-06-10 --strict`.
