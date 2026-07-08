# Framework Self-Evolve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AWorld framework provide a disabled-by-default self-evolve
capability that can asynchronously optimize agent-facing harness artifacts after
an opted-in agent run produces a trajectory. Phase 0 must first prove
trajectory-driven credit assignment on real labeled trajectories. Phase 1a then
ships the thinnest useful vertical slice: config, trace packaging,
`SkillTextTarget`, one low-dependency mutator, one deterministic/objective
evaluation signal, proposal-only artifacts, and an explicit SDK/CLI target path.
Only after that evidence exists should the plan expand to prompt sections, tool
descriptions, selected agent config knobs, isolated workspace-local files,
async post-run scheduling, or `online` automatic apply. All phases prevent
framework/runtime/CLI product logic changes.

**Architecture:** Add `aworld/self_evolve/` as the framework core. The core
models optimization targets, trajectory credit assignment, eval sources,
candidate optimizers, evaluation backends, gates, async scheduling, run
artifacts, and orchestration. Agent post-run integration only performs a
lightweight durable enqueue; workers run target inference, candidate
generation/evaluation, and artifact persistence. Do not create the full package
shape before the phase-0 credit-assignment gate passes; start with the phase-1a
vertical slice and add later modules only when their tests require them. The
first implementation reuses existing `EvaluateRunner`, trajectory /
`llm_calls`, trajectory scorers, and Ralph verification as backends. Existing
`Runners.evolve(...)` /
`train.evolve.EvolutionRunner` remains the training-oriented evolution pipeline;
this feature uses `aworld.self_evolve` and `.aworld/self_evolve/` for
controlled harness optimization. CLI adds a single generic
`aworld-cli optimize` command as a thin manual/debug caller of the same
framework API, with extension through options and `--target <type>:<id>` forms
rather than new subcommands or slash-command entrypoints. CLI does not own
scheduler, evaluator, optimizer, target inference, durable artifacts, or agent
opt-in semantics. Persistent application defaults to proposal and diff
artifacts, while `online` plus `auto_verified` can apply allowlisted verified
candidates after post-apply re-evaluation. `aworld-skills/app_evaluator/SKILL.md`
is not part of the new subsystem and must remain protected from target inference
and candidate mutation.

**Tech Stack:** Python 3.10+, Pydantic config models, dataclasses or Pydantic
models for internal run records, existing `aworld.evaluations`, existing
trajectory infrastructure, existing `aworld-cli` top-level command pattern,
`pytest`.

---

## File Structure

### New framework files

Create only the phase-1a files at first:

- `aworld/self_evolve/__init__.py`
- `aworld/self_evolve/config.py`
- `aworld/self_evolve/types.py`
- `aworld/self_evolve/targets.py`
- `aworld/self_evolve/trace_pack.py`
- `aworld/self_evolve/credit_assignment.py`
- `aworld/self_evolve/optimizers/__init__.py`
- `aworld/self_evolve/optimizers/base.py`
- `aworld/self_evolve/optimizers/llm_mutator.py`
- `aworld/self_evolve/evaluation.py`
- `aworld/self_evolve/gates.py`
- `aworld/self_evolve/store.py`
- `aworld/self_evolve/runner.py`

Add the remaining files only after the phase-1a slice proves useful:

- `aworld/self_evolve/provenance.py`
- `aworld/self_evolve/datasets.py`
- `aworld/self_evolve/optimizers/dspy_adapter.py`
- `aworld/self_evolve/judge.py`
- `aworld/self_evolve/scheduler.py`

### Framework files to modify

- `aworld/config/conf.py`
- `aworld/runners/event_runner.py` completion path for best-effort post-run
  enqueue, if async scheduling is accepted after phase 1a
- `aworld/evaluations/` only for narrow backend integration if needed
- `aworld/dataset/` only for reuse helpers if needed
- `aworld/skills/` only for skill target loading/apply seams if needed
- Do not modify `aworld-skills/app_evaluator/SKILL.md` or build the subsystem
  by extending that skill.

### New CLI files

- `aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py`
- `aworld-cli/src/aworld_cli/builtin_plugins/optimize_cli/__init__.py`
- `aworld-cli/src/aworld_cli/builtin_plugins/optimize_cli/cli_commands/optimize.py`

### CLI files to modify

- top-level command registration surface only if explicit registration is
  required for the single `aworld-cli optimize` entrypoint

### New tests

- `tests/self_evolve/test_credit_assignment_spike.py`
- `tests/self_evolve/test_config.py`
- `tests/self_evolve/test_targets.py`
- `tests/self_evolve/test_provenance.py`
- `tests/self_evolve/test_trace_pack.py`
- `tests/self_evolve/test_credit_assignment.py`
- `tests/self_evolve/test_store.py`
- `tests/self_evolve/test_runner.py`
- `tests/self_evolve/test_evaluation_backend.py`
- `tests/self_evolve/test_judge.py`
- `tests/self_evolve/test_scheduler.py`
- `tests/cli/test_optimize_command.py`

---

## Task 0: Prove Credit Assignment Before Building The Pipeline

**Files:**

- Create: `tests/self_evolve/fixtures/credit_assignment_cases/`
- Create: `tests/self_evolve/test_credit_assignment_spike.py`
- Optional scratch script: `scripts/self_evolve_credit_assignment_spike.py`

- [x] **Step 1: Collect real trajectory fixtures**

Collect a small, reviewable set of real trajectories that cover successful
runs, skill guidance failures, prompt misunderstanding, tool misuse,
workspace-artifact failures, config-limit failures, and ambiguous cases.
Use `~/Documents/logs/trajectory.log` as the initial source for task records and
extract a sanitized fixture set into
`tests/self_evolve/fixtures/credit_assignment_cases/`. Do not make tests depend
on the developer-local path after fixture generation.

- [x] **Step 1A: Seed the first regression benchmark**

Parse task records from `~/Documents/logs/trajectory.log` into regression
benchmark cases with stable case ids, source task ids, expected observable
outcomes, and optional deterministic verification commands. Persist a dataset
recipe that records source path, content fingerprint, extraction filters, split
seed, and train/validation/held-out case ids.

- [x] **Step 2: Add manual labels**

For each fixture, record the expected target type/id or `no_target`, the
human-readable rationale, and evidence step ids.

- [x] **Step 3: Measure target-selection quality**

Run deterministic signals plus optional LLM-assisted diagnosis and report
precision/recall for target selection and `no_target` rejection. The output must
include false-positive and false-negative examples.

- [x] **Step 4: Make the go/no-go decision explicit**

Do not start candidate generation, async scheduling, broad provenance work,
non-skill targets, DSPy adapters, or online automatic apply until the spike
meets the configured acceptance threshold. If it fails, continue only with
diagnostics and explicit-target proposal experiments.

Result: `tests/self_evolve/fixtures/credit_assignment_cases/spike_report.json`
records a `go` decision for the minimized benchmark with all required target
types covered and target-selection thresholds met. Proceed only to the phase-1a
explicit-target proposal-only slice next; async scheduling, broad provenance
expansion, DSPy adapters, non-skill target expansion, and online automatic apply
remain deferred.

---

## Task 1: Add Disabled-By-Default Config Surface

**Files:**

- Modify: `aworld/config/conf.py`
- Test: `tests/self_evolve/test_config.py`

- [x] **Step 1: Write config tests**

Cover:

- `AgentConfig().self_evolve_config.mode == "off"`
- `SelfEvolveConfig(mode="offline" | "shadow" | "online")` is accepted
- no separate `enabled` or `optimize` flag is required for disabled defaults
- run budget fields parse: max tokens, optional max cost, min eval cases, judge
  repetitions, and cooldown
- judge config parses default trajectory judge, explicit `agent.md`, custom
  agent, and disabled judge modes
- `online` requires explicit `auto_verified` apply policy and cannot apply
  candidates unless all gates and post-apply re-evaluation pass
- old config fields still parse
- unknown model kwargs still enter `llm_config.ext_config`

- [x] **Step 2: Add config models**

Add `SelfEvolveConfig` and fields on `AgentConfig`.

- [x] **Step 3: Run config tests**

Run:

```bash
python -m pytest tests/self_evolve/test_config.py -q
```

Expected: PASS

---

## Task 2: Add Core Self-Evolve Types And Store

**Files:**

- Create: `aworld/self_evolve/__init__.py`
- Create: `aworld/self_evolve/types.py`
- Create: `aworld/self_evolve/provenance.py`
- Create: `aworld/self_evolve/store.py`
- Test: `tests/self_evolve/test_store.py`
- Test: `tests/self_evolve/test_provenance.py`

- [x] **Step 1: Define run record models**

Define:

- `SelfEvolveRun`
- `SelfEvolveTargetRef`
- `CandidateVariant`
- `EvaluationSummary`
- `GateResult`
- `SelfEvolveRunStatus`
- `TargetProvenance`
- `OptimizerLineage`
- `DatasetRecipe`

- [x] **Step 2: Implement provenance sidecar models**

Track target source kind, write origin, trust level, protected status, and
reason without writing those operational fields into target files such as
`SKILL.md`.

- [x] **Step 3: Implement filesystem store**

Persist run artifacts under `.aworld/self_evolve/<run_id>/`.

- [x] **Step 4: Test artifact persistence**

Cover run creation, candidate file writing, report writing, stable paths,
dataset recipe persistence, target provenance, and optimizer lineage.

---

## Task 3: Add Optimization Target Abstractions

**Files:**

- Create: `aworld/self_evolve/targets.py`
- Test: `tests/self_evolve/test_targets.py`

- [x] **Step 1: Define `SelfEvolveTarget` protocol**

Required operations:

- identity
- load current content
- fingerprint current content
- render candidate diff
- preserve proposal and diff artifacts
- expose target-specific apply/rollback hooks only when the target is
  allowlisted for `auto_verified`

- [x] **Step 2: Implement phase-1 targets**

Implement at least:

- `SkillTextTarget`
- `PromptSectionTarget` skeleton
- `ToolDescriptionTarget` skeleton
- `AgentConfigTarget` skeleton
- `WorkspaceArtifactTarget` skeleton with protected-path rejection

Only `SkillTextTarget` must be fully runnable in the first implementation slice.

- [x] **Step 3: Test skill target behavior**

Cover loading `SKILL.md`, fingerprinting, diff rendering, proposal-only
non-mutation, and the allowlisted `auto_verified` apply/rollback path.

---

## Task 4: Add Trajectory Credit Assignment

**Files:**

- Create: `aworld/self_evolve/credit_assignment.py`
- Create: `aworld/self_evolve/trace_pack.py`
- Test: `tests/self_evolve/test_trace_pack.py`
- Test: `tests/self_evolve/test_credit_assignment.py`

- [x] **Step 0: Verify phase-0 gate is accepted**

Before implementing the production credit assigner, confirm Task 0 has produced
accepted precision/recall for target selection on real manually labeled
trajectories. If the gate is not accepted, stop this task and continue only with
diagnostics or explicit-target proposal experiments.

- [x] **Step 1: Add trace pack normalization**

Normalize current trajectories, prior sessions, and trajectory logs into a
bounded `TracePack` that preserves AWorld `TrajectoryItem` SAR fields: task
input and context from `state`, assistant content and tool calls from `action`,
tool outputs/status/score from `reward`, failed arguments, verification output,
LLM usage/cost metadata, generated artifact references, and evidence ids.

- [x] **Step 2: Test trace pack compression**

Cover preservation of initial/final SAR records, middle-record summarization,
evidence id stability, and budget enforcement.

- [x] **Step 3: Define selection report models**

Define `TargetSelectionReport` with selected target id, confidence, evidence
step refs, failure category, and `no_target` diagnostics.

- [x] **Step 4: Build target inventory**

Inventory supported phase-1 targets: skills, prompt sections, tool
descriptions, whitelisted config knobs, and agent-produced workspace artifacts,
including provenance/trust metadata and protected status.

- [x] **Step 5: Implement deterministic signal extraction**

Use trajectory validators, tool call errors, repeated actions, LLM call
metadata, generated artifact references, and task status.

- [x] **Step 6: Add optional LLM-assisted diagnosis**

The LLM diagnosis must cite trajectory evidence and may return `no_target` when
confidence is below policy.

- [x] **Step 7: Test target inference**

Cover inferred skill, prompt-section, tool-description, workspace-artifact, and
insufficient-signal `no_target` cases.

---

## Task 5: Add Eval Source And Dataset Builders

**Files:**

- Create: `aworld/self_evolve/datasets.py`
- Test: `tests/self_evolve/test_datasets.py`

- [x] **Step 1: Add source config models**

Define `SelfEvolveEvalSourceConfig` with `current_trajectory`,
`trajectory_log`, `session`, `jsonl`, and `batch_config` kinds. The default
post-run source is `current_trajectory`; external trajectory logs are optional.

- [x] **Step 2: Add jsonl loader**

Load task/evaluation cases from jsonl.

- [x] **Step 3: Add dataset identity and splits**

Compute dataset fingerprint and deterministic train/validation/test split.

- [x] **Step 4: Add dataset recipe persistence**

Persist source selection, filters, split seed, synthetic generation policy, and
holdout policy. Store trainable failure cases separately from held-out failure
cases so optimizers cannot see final gate data.

- [x] **Step 5: Add current trajectory, session, and trajectory-log mining
interfaces**

Expose interfaces that can feed phase-1 credit assignment and evaluation.

- [x] **Step 6: Add fixture-backed trajectory source tests**

Use a checked-in or temporary trajectory log sample. Do not hard-code
`~/Documents/logs/trajectory.log` into product behavior.

---

## Task 6: Add Evaluation Backend Contract

**Files:**

- Create: `aworld/self_evolve/evaluation.py`
- Create: `aworld/self_evolve/judge.py`
- Test: `tests/self_evolve/test_evaluation_backend.py`
- Test: `tests/self_evolve/test_judge.py`

- [x] **Step 1: Define backend protocol**

Add `EvaluationBackend.evaluate_variant(...)`.

- [x] **Step 2: Add EvaluateRunner-backed implementation**

Wrap existing `EvaluateRunner` where possible.

- [x] **Step 3: Add command verification backend**

Support deterministic shell/pytest-style verification commands as one signal.

- [x] **Step 4: Add configurable judge backend**

Support:

- default self-evolve trajectory judge using `TracePack`, target-selection
  report, baseline/candidate outputs, and scorer diagnostics
- explicit `agent.md` judge loaded through the configured agent-loading path
- custom judge agent or registered agent name
- disabled judge mode for deterministic-only runs

Persist judge prompts, compact inputs, outputs, and verdict metadata in run
artifacts.

- [x] **Step 5: Test baseline/candidate parity**

Ensure baseline and candidate run against identical dataset and policy.

- [x] **Step 6: Test optional external trajectory source behavior**

Ensure a trajectory log can be supplied explicitly, and ensure default post-run
self-evolve does not require one.

- [x] **Step 6A: Build the trajectory-log benchmark seed**

Add a local-only builder path that reads `~/Documents/logs/trajectory.log` when
explicitly requested, extracts task records into benchmark cases, persists the
dataset recipe and source fingerprint, and writes sanitized fixture copies for
tests. Product defaults must not require that path.

- [x] **Step 7: Enforce held-out evaluation discipline**

Candidate ranking uses validation metrics. Verified pass/fail gates use
optimizer-held-out test metrics when at least `min_eval_cases` exist. Too few
cases may still produce proposals, but they must be marked limited-confidence.
Single-trajectory post-run jobs usually fall into this limited-confidence path
unless an additional dataset/session/batch source is configured.

- [x] **Step 7A: Estimate replay cost before evaluation**

Before running baseline/candidate batches, estimate cost as baseline cases plus
candidate-count times candidate eval cases, including judge repetitions and
verification commands. If the estimate exceeds `max_run_tokens` or
`max_run_cost_usd`, reduce candidates/eval cases or stop with budget diagnostics.

- [x] **Step 8: Enforce verified signal discipline**

Judge-only improvements must remain limited-confidence. Verified improvements
require at least one deterministic signal, such as command verification,
exact/objective scoring, or a configured regression benchmark.

---

## Task 7: Add Candidate Optimizer Contract

**Files:**

- Create: `aworld/self_evolve/optimizers/base.py`
- Create: `aworld/self_evolve/optimizers/llm_mutator.py`
- Create: `aworld/self_evolve/optimizers/dspy_adapter.py`
- Test: `tests/self_evolve/test_optimizer_contract.py`

- [x] **Step 1: Define optimizer protocol**

Add `CandidateOptimizer.propose(...)`.

- [x] **Step 2: Implement trace-reflective LLM mutator fallback**

Use existing model config surfaces to propose candidate text variants from
trace packs, scorer feedback, validation failures, and trainable failure cases.

- [x] **Step 3: Implement optional DSPy adapters**

Add optional `DSPyGEPAOptimizer` and `DSPyMIPROOptimizer` selection paths behind
dependency checks. GEPA should be the preferred trace-reflective text optimizer
when installed; MIPRO should be a fallback for instruction text and few-shot
examples when enough examples exist.

- [x] **Step 4: Keep Darwinian/code evolution external**

Represent Darwinian/code evolution as a future external CLI/subprocess adapter
only. Do not import AGPL libraries into AWorld core.

- [x] **Step 5: Implement workspace-local artifact candidate guardrails**

Generate candidates only for workspace-local code/files produced by agent task
execution, never for AWorld or `aworld-cli` product logic, and only through
isolated candidate workspace evaluation.

- [x] **Step 6: Implement optional dependency guards**

If DSPy or future external optimizer dependencies are unavailable, fail only
when that adapter is selected.

- [x] **Step 7: Prevent held-out leakage**

Ensure optimizers receive only training/source cases, validation feedback, and
trajectory diagnostics, never held-out test cases or held-out judge outputs.

- [x] **Step 8: Persist optimizer lineage**

Record parent candidate ids, mutation rationale, backend name/version, trainable
failure-case ids used, and candidate ancestry in run artifacts.

---

## Task 8: Add Gates

**Files:**

- Create: `aworld/self_evolve/gates.py`
- Test: `tests/self_evolve/test_gates.py`

- [x] **Step 1: Add score improvement gate**

- [x] **Step 2: Add cost/latency regression gate**

- [x] **Step 3: Add no-op and malformed-candidate gate**

- [x] **Step 4: Add skill markdown/frontmatter gate**

- [x] **Step 5: Add protected-path gate**

Reject candidates that touch framework, `aworld-cli`, runtime, shared
infrastructure, package metadata, secret/config paths, or any AWorld product
logic.

- [x] **Step 5A: Add app_evaluator protection**

Reject candidates and inferred targets that would modify
`aworld-skills/app_evaluator/SKILL.md`. The skill may only be used as an
explicitly configured read-only scorer/fixture.

- [x] **Step 6: Add stopping condition gates**

Cover max iterations, no meaningful improvement, pending proposal duplicate
suppression, repeated gate failure, and cooldown.

- [x] **Step 7: Add whole-run budget gates**

Enforce max run tokens and optional max run cost across mutator calls,
baseline/candidate evaluation, judge repetitions, and verification.
Include preflight replay-cost estimation for baseline and candidate task
re-execution; do not start a batch that is already projected to exceed budget.

- [x] **Step 8: Add held-out verification gate**

Only mark a candidate as verified when it passes held-out test evaluation under
the configured minimum improvement and variance policy.

- [x] **Step 9: Add trust and provenance gates**

Reject or downgrade candidates from generated, external, or protected targets
unless their trust policy, content scan, and protected-path checks pass.

- [x] **Step 10: Add global target regression benchmark gates**

For `SkillTextTarget`, `PromptSectionTarget`, and `ToolDescriptionTarget`, a
candidate cannot be marked verified unless it passes a configured regression
benchmark or equivalent objective regression suite independent from the source
trajectory.

- [x] **Step 11: Add judge-only limited-confidence gate**

If all positive signal comes from LLM judge output, persist the proposal but do
not mark it verified.

- [x] **Step 12: Add post-apply re-evaluation and rollback gates**

For `auto_verified`, re-run required gates after application. Accept only when
post-apply metrics still pass; otherwise roll back or mark rejected according to
the target apply policy. This path must be unattended after `online` and
`auto_verified` are enabled; it must not pause for human review, approval,
confirmation, or intervention.

---

## Task 9: Add SelfEvolveRunner

**Files:**

- Create: `aworld/self_evolve/runner.py`
- Test: `tests/self_evolve/test_runner.py`

- [x] **Step 1: Write fake target / fake optimizer runner tests**

- [x] **Step 2: Implement orchestration**

Flow:

1. collect eval sources
2. select explicit target or run trajectory credit assignment
3. load target
4. build dataset and splits
5. evaluate baseline
6. generate candidates
7. evaluate candidates
8. run gates
9. persist artifacts and target selection report
10. if apply policy is `proposal`, preserve proposal/diff artifacts without
    applying selected candidate
11. if apply policy is `auto_verified`, apply only allowlisted verified
    candidates, re-run post-apply gates, then accept or roll back/reject

- [x] **Step 3: Verify proposal-only does not mutate target**

- [x] **Step 4: Verify online auto-evolve apply path**

Cover a fake allowlisted `SkillTextTarget` candidate that passes gates, applies,
persists post-apply metrics, and is accepted. Cover a post-apply regression that
rolls back or marks the candidate rejected. Cover that no human approval callback
is required in the successful path.

- [x] **Step 5: Verify bounded stopping conditions**

Cover max iterations, no candidate improvement, and duplicate pending proposal.

---

## Task 10: Add Async Post-Run Scheduler

**Files:**

- Create: `aworld/self_evolve/scheduler.py`
- Modify: `aworld/runners/event_runner.py`
- Test: `tests/self_evolve/test_scheduler.py`

- [x] **Step 1: Define run context and scheduler protocol**

Capture agent id/config, task metadata, current trajectory reference, workspace,
and optional source hints.

- [x] **Step 2: Implement best-effort enqueue**

Eligibility:

- `SelfEvolveConfig.mode in {"shadow", "online"}`
- a current trajectory is available
- concurrency/cooldown/pending proposal policies allow enqueue

Integration point:

- Use `TaskEventRunner.do_run(...)` after `await self._save_trajectories()` and
  `resp = self._response()`, where `TaskResponse.trajectory` and `llm_calls` are
  available.
- Keep `Runners.run(...)` as a delegating convenience wrapper; do not make it
  parse or own trajectory extraction.
- Wrap enqueue in a broad best-effort guard. Enqueue failure must not fail,
  delay, or replace the completed task response.

- [x] **Step 3: Implement durable job record and worker handoff**

The scheduler persists a pending job before returning. Long-lived runtimes may
start an in-process worker. Short-lived CLI processes must leave a durable job
that can be drained by an explicit optimize invocation using the same command
path. The worker calls `SelfEvolveRunner` outside the main task response path
and persists failure artifacts if a run has been created.

- [x] **Step 4: Test non-blocking behavior**

Prove enqueue and worker failures do not alter task response status or answer.

---

## Task 11: Add CLI Optimize Command

**Files:**

- Create: `aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/optimize_cli/__init__.py`
- Create:
  `aworld-cli/src/aworld_cli/builtin_plugins/optimize_cli/cli_commands/optimize.py`
- Test: `tests/cli/test_optimize_command.py`

- [x] **Step 1: Add parser tests**

Cover:

- `aworld-cli optimize --target skill:demo --dataset eval.jsonl`
- `aworld-cli optimize --target prompt:system --dataset eval.jsonl`
- `aworld-cli optimize --target tool:browser --dataset eval.jsonl`
- command registration uses the existing built-in plugin manifest and
  `cli_commands/` entrypoint path, not `register_builtin_top_level_commands`
- all target forms are parsed by the same generic command path
- `--task`
- `--from-session`
- `--from-trajectory`
- `--batch-config`
- `--apply proposal`
- `--apply auto_verified` is passed to framework APIs and never implemented in
  CLI code
- `--apply write` and `--apply branch` are rejected as unsupported in phase 1
- no external trajectory path is required unless `--from-trajectory` is passed
- `--task` without `--target` invokes framework target inference, not CLI-owned
  target selection
- `skill:app_evaluator` is not used as a default target in tests or examples

- [x] **Step 2: Implement command as thin framework caller**

The single CLI command should construct framework config and call
`SelfEvolveRunner`, not own optimizer logic or split target types into separate
commands.

- [x] **Step 3: Print report path and best candidate summary**

---

## Task 12: Keep CLI Out Of Agent Opt-In Ownership

**Files:**

- Test: `tests/cli/test_optimize_command.py`

- [x] **Step 1: Add no-CLI-mode tests**

Verify `aworld-cli optimize` does not define a separate CLI-owned self-evolve
mode for the built-in AWorld main agent.

- [x] **Step 2: Pass through framework config only**

If optimize needs agent config, use framework `AgentConfig.self_evolve_config`
semantics without reinterpreting mode in CLI code.

---

## Task 13: Documentation And Example

**Files:**

- Add or modify docs under `docs/Agents/` or `docs/AWorld CLI/Commands/`
- Add minimal example under `examples/aworld_quick_start/self_evolve/`

- [x] **Step 1: Document framework concepts**

State explicitly that phase-1 self-evolve means harness-text/config evolution,
not model-weight training, replacement of the agent policy, or
framework/runtime/CLI product-code evolution.

- [x] **Step 2: Document async post-run behavior**

Explain that self-evolve runs in the background and cannot affect the completed
task result. Also document that a single current trajectory usually produces a
limited-confidence proposal unless independent eval sources and a
deterministic/objective signal are configured.

- [x] **Step 3: Document boundary with existing evolve/training assets**

- [x] **Step 4: Document CLI usage**

State that `aworld-cli optimize` is the only phase-1 CLI entrypoint and that CLI
invokes framework self-evolve APIs without owning scheduler, evaluator,
optimizer, target inference, or agent opt-in logic.

- [x] **Step 5: Add toy trajectory-driven optimization example**

- [x] **Step 6: Document app_evaluator boundary**

State that `aworld-skills/app_evaluator/SKILL.md` is independent from the new
self-evolve subsystem and protected from mutation.

- [x] **Step 7: Document configurable judge behavior**

Show the default self-evolve trajectory judge and examples for an explicit
`agent.md` judge and a custom registered judge agent.

- [x] **Step 8: Document online closed-loop behavior**

Explain that `shadow` is proposal-only, while `online` with `auto_verified`
supports `apply -> re-evaluate -> accept/rollback` for allowlisted verified
targets without human review or approval after enablement. Document which
broader targets remain future work.

---

## Verification Commands

Run targeted tests first:

```bash
python -m pytest tests/self_evolve -q
python -m pytest tests/cli/test_optimize_command.py -q
```

Then run relevant existing regression tests:

```bash
python -m pytest tests/evaluations tests/dataset tests/runners/test_ralph_iteration_evaluator.py -q
python -m pytest tests/core/test_swarm_yaml_builder.py tests/core/test_markdown_agent_loader_skills.py -q
```
