## 1. Parser And Command Shape

- [ ] 1.1 Confirm `aworld-evaluator-input-sources-2026-06-10` has landed before implementing source-backed CLI behavior.
- [ ] 1.2 Extend the existing `EvaluatorTopLevelCommand` parser with a source-backed `run` mode.
- [ ] 1.3 Add `--input`, `--kind`, `--judge-agent`, `--out-dir`, `--output`, `--task-id`, `--agent`, and optional JSONL field mapping arguments for source mode.
- [ ] 1.4 Default task+answer JSONL field mappings to `id`, `input`, and `answer`.
- [ ] 1.5 Preserve existing `--target`, `--suite`, `--list-suites`, `--print-report-schema`, `--validate-report`, and `--interactive-approval` behavior.
- [ ] 1.6 Add clear validation errors for mixing incompatible target-mode and source-mode arguments.

## 2. Runtime Assembly

- [ ] 2.1 Add a source-backed runtime helper in `aworld_cli.evaluator_runtime`.
- [ ] 2.2 Resolve source kind to framework source/adapters from `aworld.evaluations`.
- [ ] 2.3 Resolve `agent.md` judge path through framework `AgentJudgeBackend.from_agent_markdown`.
- [ ] 2.4 For task+answer and trajectory-log sources, use framework replay/state adapters without re-execution.
- [ ] 2.5 Treat task-only and serialized-state source kinds as unsupported until the framework source layer provides those built-ins.
- [ ] 2.6 Persist reports with deterministic default names under the requested output directory.

## 3. Plugin And Hook Integration

- [ ] 3.1 Keep evaluator command exposure through the existing builtin plugin command entrypoint.
- [ ] 3.2 Reuse `_load_evaluator_hooks` and `_run_evaluator_hooks` for source-backed runs.
- [ ] 3.3 Extend evaluator hook event payloads with `mode`, `input`, `kind`, `task_id`, `judge_agent`, `agent`, and output path fields.
- [ ] 3.4 Document that hooks may customize CLI metadata, side effects, and rendering but must not redefine framework execution, scoring, gate, or report semantics.

## 4. UX And Reporting

- [ ] 4.1 Render the same evaluator summary shape for source-backed reports.
- [ ] 4.2 Include resolved source mode, input path, kind, selected task ids, and report path in summary or automation metadata.
- [ ] 4.3 Keep exit codes based on gate status and approval state.
- [ ] 4.4 Add examples for trajectory-log, task+answer, and task-only evaluation.

## 5. Tests

- [ ] 5.1 Add parser tests for source-backed `evaluator run` arguments.
- [ ] 5.2 Add validation tests for required source-mode arguments and incompatible argument combinations.
- [ ] 5.3 Add runtime delegation tests using fake framework source helpers.
- [ ] 5.4 Add hook payload tests for source-backed pre-run/post-run/render events.
- [ ] 5.5 Add compatibility tests for the existing target/suite evaluator path.
- [ ] 5.6 Validate this OpenSpec change with `openspec validate aworld-cli-evaluator-source-run-2026-06-10 --strict`.
