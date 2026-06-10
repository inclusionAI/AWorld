## 1. Parser And Command Shape

- [x] 1.1 Confirm `aworld-evaluator-input-sources-2026-06-10` has landed before implementing source-backed CLI behavior.
- [x] 1.2 Extend the existing `EvaluatorTopLevelCommand` parser with source-backed `--input` mode.
- [x] 1.3 Add `--input`, `--kind`, `--judge-agent`, `--out-dir`, `--output`, `--task-id`, `--agent`, and optional JSONL field mapping arguments for source mode.
- [x] 1.4 Default task+answer JSONL field mappings to `id`, `input`, and `answer`.
- [x] 1.5 Preserve existing `--target`, `--suite`, `--list-suites`, `--print-report-schema`, `--validate-report`, and `--interactive-approval` behavior.
- [x] 1.6 Add clear validation errors for mixing incompatible target-mode and source-mode arguments.

## 2. Runtime Assembly

- [x] 2.1 Add a source-backed runtime helper in `aworld_cli.evaluator_runtime`.
- [x] 2.2 Resolve source kind to framework source/adapters from `aworld.evaluations`.
- [x] 2.3 Resolve `agent.md` judge path through framework `AgentJudgeBackend.from_agent_markdown`.
- [x] 2.4 For task+answer and trajectory-log sources, use framework replay/state adapters without re-execution.
- [x] 2.5 Treat task-only and serialized-state source kinds as unsupported until the framework source layer provides those built-ins.
- [x] 2.6 Persist reports with deterministic default names under the requested output directory.

## 3. Plugin And Hook Integration

- [x] 3.1 Keep evaluator command exposure through the existing builtin plugin command entrypoint.
- [x] 3.2 Reuse `_load_evaluator_hooks` and `_run_evaluator_hooks` for source-backed runs.
- [x] 3.3 Extend evaluator hook event payloads with `mode`, `input`, `kind`, `task_id`, `judge_agent`, `agent`, and output path fields.
- [x] 3.4 Document that hooks may customize CLI metadata, side effects, and rendering but must not redefine framework execution, scoring, gate, or report semantics.

## 4. UX And Reporting

- [x] 4.1 Render the same evaluator summary shape for source-backed reports.
- [x] 4.2 Include resolved source mode, input path, kind, selected task ids, and report path in summary or automation metadata.
- [x] 4.3 Keep exit codes based on gate status and approval state.
- [x] 4.4 Add examples for trajectory-log and task+answer evaluation, and document task-only evaluation as deferred until the framework source exists.

## 5. Tests

- [x] 5.1 Add parser tests for source-backed `evaluator --input` arguments.
- [x] 5.2 Add validation tests for required source-mode arguments and incompatible argument combinations.
- [x] 5.3 Add runtime delegation tests using fake framework source helpers.
- [x] 5.4 Add hook payload tests for source-backed pre-run/post-run/render events.
- [x] 5.5 Add compatibility tests for the existing target/suite evaluator path.
- [x] 5.6 Validate this OpenSpec change with `openspec validate aworld-cli-evaluator-source-run-2026-06-10 --strict`.
