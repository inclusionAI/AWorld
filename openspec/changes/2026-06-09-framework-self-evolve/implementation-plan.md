# Framework Self-Evolve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AWorld framework provide a disabled-by-default self-evolve
capability that can asynchronously optimize agent-facing harness artifacts after
an opted-in agent run produces a trajectory. Phase 1 covers skills, prompt
sections, tool descriptions, selected agent config knobs, and isolated
workspace-local code/files produced by agent task execution, while preventing
framework/runtime/CLI product logic changes and letting `aworld-cli` expose
manual/debug command surfaces. Phase 1 must close the trajectory-driven loop by
selecting an optimization target from trajectory evidence before proposing
candidate diffs.

**Architecture:** Add `aworld/self_evolve/` as the framework core. The core
models optimization targets, trajectory credit assignment, eval sources,
candidate optimizers, evaluation backends, gates, async scheduling, run
artifacts, and orchestration. Agent post-run integration only performs a
lightweight durable enqueue; workers run target inference, candidate
generation/evaluation, and artifact persistence. The first implementation
reuses existing `EvaluateRunner`, trajectory / `llm_calls`, trajectory scorers,
and Ralph verification as backends. Existing `Runners.evolve(...)` /
`train.evolve.EvolutionRunner` remains the training-oriented evolution pipeline;
this feature uses `aworld.self_evolve` and `.aworld/self_evolve/` for
proposal-only harness optimization. CLI adds a single generic
`aworld-cli optimize` command as a thin manual/debug caller of the same
framework API, with extension through options and `--target <type>:<id>` forms
rather than new subcommands. Persistent application defaults to proposal and
diff artifacts only.

**Tech Stack:** Python 3.10+, Pydantic config models, dataclasses or Pydantic
models for internal run records, existing `aworld.evaluations`, existing
trajectory infrastructure, existing `aworld-cli` top-level command pattern,
`pytest`.

---

## File Structure

### New framework files

- `aworld/self_evolve/__init__.py`
- `aworld/self_evolve/config.py`
- `aworld/self_evolve/types.py`
- `aworld/self_evolve/targets.py`
- `aworld/self_evolve/credit_assignment.py`
- `aworld/self_evolve/datasets.py`
- `aworld/self_evolve/optimizers/__init__.py`
- `aworld/self_evolve/optimizers/base.py`
- `aworld/self_evolve/optimizers/llm_mutator.py`
- `aworld/self_evolve/optimizers/dspy_adapter.py`
- `aworld/self_evolve/evaluation.py`
- `aworld/self_evolve/gates.py`
- `aworld/self_evolve/scheduler.py`
- `aworld/self_evolve/store.py`
- `aworld/self_evolve/runner.py`

### Framework files to modify

- `aworld/config/conf.py`
- `aworld/runner.py` or the task completion path for best-effort post-run
  enqueue, if accepted
- `aworld/evaluations/` only for narrow backend integration if needed
- `aworld/dataset/` only for reuse helpers if needed
- `aworld/skills/` only for skill target loading/apply seams if needed

### New CLI files

- `aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py`
- `aworld-cli/src/aworld_cli/builtin_plugins/optimize_cli/__init__.py`
- `aworld-cli/src/aworld_cli/builtin_plugins/optimize_cli/cli_commands/optimize.py`

### CLI files to modify

- `aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/aworld_agent.py`
- top-level command registration surface if explicit registration is required
- interactive command surface only if `/optimize` is accepted for phase 1

### New tests

- `tests/self_evolve/test_config.py`
- `tests/self_evolve/test_targets.py`
- `tests/self_evolve/test_credit_assignment.py`
- `tests/self_evolve/test_store.py`
- `tests/self_evolve/test_runner.py`
- `tests/self_evolve/test_evaluation_backend.py`
- `tests/self_evolve/test_scheduler.py`
- `tests/cli/test_optimize_command.py`

---

## Task 1: Add Disabled-By-Default Config Surface

**Files:**

- Modify: `aworld/config/conf.py`
- Test: `tests/self_evolve/test_config.py`

- [ ] **Step 1: Write config tests**

Cover:

- `AgentConfig().self_evolve_config.mode == "off"`
- `SelfEvolveConfig(mode="offline" | "shadow" | "online")` is accepted
- no separate `enabled` or `optimize` flag is required for disabled defaults
- run budget fields parse: max tokens, optional max cost, min eval cases, judge
  repetitions, and cooldown
- old config fields still parse
- unknown model kwargs still enter `llm_config.ext_config`

- [ ] **Step 2: Add config models**

Add `SelfEvolveConfig` and fields on `AgentConfig`.

- [ ] **Step 3: Run config tests**

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
- Create: `aworld/self_evolve/store.py`
- Test: `tests/self_evolve/test_store.py`

- [ ] **Step 1: Define run record models**

Define:

- `SelfEvolveRun`
- `SelfEvolveTargetRef`
- `CandidateVariant`
- `EvaluationSummary`
- `GateResult`
- `SelfEvolveRunStatus`

- [ ] **Step 2: Implement filesystem store**

Persist run artifacts under `.aworld/self_evolve/<run_id>/`.

- [ ] **Step 3: Test artifact persistence**

Cover run creation, candidate file writing, report writing, and stable paths.

---

## Task 3: Add Optimization Target Abstractions

**Files:**

- Create: `aworld/self_evolve/targets.py`
- Test: `tests/self_evolve/test_targets.py`

- [ ] **Step 1: Define `SelfEvolveTarget` protocol**

Required operations:

- identity
- load current content
- fingerprint current content
- render candidate diff
- reject write/branch application while preserving proposal and diff artifacts

- [ ] **Step 2: Implement phase-1 targets**

Implement at least:

- `SkillTextTarget`
- `PromptSectionTarget` skeleton
- `ToolDescriptionTarget` skeleton
- `AgentConfigTarget` skeleton
- `WorkspaceArtifactTarget` skeleton with protected-path rejection

Only `SkillTextTarget` must be fully runnable in the first implementation slice.

- [ ] **Step 3: Test skill target behavior**

Cover loading `SKILL.md`, fingerprinting, diff rendering, and proposal-only
non-mutation.

---

## Task 4: Add Trajectory Credit Assignment

**Files:**

- Create: `aworld/self_evolve/credit_assignment.py`
- Test: `tests/self_evolve/test_credit_assignment.py`

- [ ] **Step 1: Define selection report models**

Define `TargetSelectionReport` with selected target id, confidence, evidence
step refs, failure category, and `no_target` diagnostics.

- [ ] **Step 2: Build target inventory**

Inventory supported phase-1 targets: skills, prompt sections, tool
descriptions, whitelisted config knobs, and protected workspace artifacts.

- [ ] **Step 3: Implement deterministic signal extraction**

Use trajectory validators, tool call errors, repeated actions, LLM call
metadata, generated artifact references, and task status.

- [ ] **Step 4: Add optional LLM-assisted diagnosis**

The LLM diagnosis must cite trajectory evidence and may return `no_target` when
confidence is below policy.

- [ ] **Step 5: Test target inference**

Cover inferred skill, prompt-section, tool-description, workspace-artifact, and
insufficient-signal `no_target` cases.

---

## Task 5: Add Eval Source And Dataset Builders

**Files:**

- Create: `aworld/self_evolve/datasets.py`
- Test: `tests/self_evolve/test_datasets.py`

- [ ] **Step 1: Add source config models**

Define `SelfEvolveEvalSourceConfig` with `current_trajectory`,
`trajectory_log`, `session`, `jsonl`, and `batch_config` kinds. The default
post-run source is `current_trajectory`; external trajectory logs are optional.

- [ ] **Step 2: Add jsonl loader**

Load task/evaluation cases from jsonl.

- [ ] **Step 3: Add dataset identity and splits**

Compute dataset fingerprint and deterministic train/validation/test split.

- [ ] **Step 4: Add current trajectory, session, and trajectory-log mining
interfaces**

Expose interfaces that can feed phase-1 credit assignment and evaluation.

- [ ] **Step 5: Add fixture-backed trajectory source tests**

Use a checked-in or temporary trajectory log sample. Do not hard-code
`~/Documents/logs/trajectory.log` into product behavior.

---

## Task 6: Add Evaluation Backend Contract

**Files:**

- Create: `aworld/self_evolve/evaluation.py`
- Test: `tests/self_evolve/test_evaluation_backend.py`

- [ ] **Step 1: Define backend protocol**

Add `EvaluationBackend.evaluate_variant(...)`.

- [ ] **Step 2: Add EvaluateRunner-backed implementation**

Wrap existing `EvaluateRunner` where possible.

- [ ] **Step 3: Add command verification backend**

Support deterministic shell/pytest-style verification commands as one signal.

- [ ] **Step 4: Test baseline/candidate parity**

Ensure baseline and candidate run against identical dataset and policy.

- [ ] **Step 5: Test optional external trajectory source behavior**

Ensure a trajectory log can be supplied explicitly, and ensure default post-run
self-evolve does not require one.

- [ ] **Step 6: Enforce held-out evaluation discipline**

Candidate ranking uses validation metrics. Verified pass/fail gates use
optimizer-held-out test metrics when at least `min_eval_cases` exist. Too few
cases may still produce proposals, but they must be marked limited-confidence.

---

## Task 7: Add Candidate Optimizer Contract

**Files:**

- Create: `aworld/self_evolve/optimizers/base.py`
- Create: `aworld/self_evolve/optimizers/llm_mutator.py`
- Create: `aworld/self_evolve/optimizers/dspy_adapter.py`
- Test: `tests/self_evolve/test_optimizer_contract.py`

- [ ] **Step 1: Define optimizer protocol**

Add `CandidateOptimizer.propose(...)`.

- [ ] **Step 2: Implement LLM mutator fallback**

Use existing model config surfaces to propose candidate text variants.

- [ ] **Step 3: Implement workspace-local artifact candidate guardrails**

Generate candidates only for workspace-local code/files produced by agent task
execution, never for AWorld or `aworld-cli` product logic, and only through
isolated candidate workspace evaluation.

- [ ] **Step 4: Implement optional DSPy adapter guard**

If DSPy is unavailable, fail only when adapter is selected.

- [ ] **Step 5: Prevent held-out leakage**

Ensure optimizers receive only training/source cases, validation feedback, and
trajectory diagnostics, never held-out test cases or held-out judge outputs.

---

## Task 8: Add Gates

**Files:**

- Create: `aworld/self_evolve/gates.py`
- Test: `tests/self_evolve/test_gates.py`

- [ ] **Step 1: Add score improvement gate**

- [ ] **Step 2: Add cost/latency regression gate**

- [ ] **Step 3: Add no-op and malformed-candidate gate**

- [ ] **Step 4: Add skill markdown/frontmatter gate**

- [ ] **Step 5: Add protected-path gate**

Reject candidates that touch framework, `aworld-cli`, runtime, shared
infrastructure, package metadata, secret/config paths, or any AWorld product
logic.

- [ ] **Step 6: Add stopping condition gates**

Cover max iterations, no meaningful improvement, pending proposal duplicate
suppression, repeated gate failure, and cooldown.

- [ ] **Step 7: Add whole-run budget gates**

Enforce max run tokens and optional max run cost across mutator calls,
baseline/candidate evaluation, judge repetitions, and verification.

- [ ] **Step 8: Add held-out verification gate**

Only mark a candidate as verified when it passes held-out test evaluation under
the configured minimum improvement and variance policy.

---

## Task 9: Add SelfEvolveRunner

**Files:**

- Create: `aworld/self_evolve/runner.py`
- Test: `tests/self_evolve/test_runner.py`

- [ ] **Step 1: Write fake target / fake optimizer runner tests**

- [ ] **Step 2: Implement orchestration**

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
10. preserve proposal/diff artifacts without applying selected candidate

- [ ] **Step 3: Verify proposal-only does not mutate target**

- [ ] **Step 4: Verify bounded stopping conditions**

Cover max iterations, no candidate improvement, and duplicate pending proposal.

---

## Task 10: Add Async Post-Run Scheduler

**Files:**

- Create: `aworld/self_evolve/scheduler.py`
- Modify: task/runner completion integration point selected during
  implementation
- Test: `tests/self_evolve/test_scheduler.py`

- [ ] **Step 1: Define run context and scheduler protocol**

Capture agent id/config, task metadata, current trajectory reference, workspace,
and optional source hints.

- [ ] **Step 2: Implement best-effort enqueue**

Eligibility:

- `SelfEvolveConfig.mode in {"shadow", "online"}`
- a current trajectory is available
- concurrency/cooldown/pending proposal policies allow enqueue

Enqueue failure must not fail the completed task.

- [ ] **Step 3: Implement durable job record and worker handoff**

The scheduler persists a pending job before returning. Long-lived runtimes may
start an in-process worker. Short-lived CLI processes must leave a durable job
that can be drained by an explicit optimize invocation using the same command
path. The worker calls `SelfEvolveRunner` outside the main task response path
and persists failure artifacts if a run has been created.

- [ ] **Step 4: Test non-blocking behavior**

Prove enqueue and worker failures do not alter task response status or answer.

---

## Task 11: Add CLI Optimize Command

**Files:**

- Create: `aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/optimize_cli/__init__.py`
- Create:
  `aworld-cli/src/aworld_cli/builtin_plugins/optimize_cli/cli_commands/optimize.py`
- Test: `tests/cli/test_optimize_command.py`

- [ ] **Step 1: Add parser tests**

Cover:

- `aworld-cli optimize --target skill:demo --dataset eval.jsonl`
- `aworld-cli optimize --target prompt:system --dataset eval.jsonl`
- `aworld-cli optimize --target tool:browser --dataset eval.jsonl`
- all target forms are parsed by the same generic command path
- `--task`
- `--from-session`
- `--from-trajectory`
- `--batch-config`
- `--apply proposal`
- `--apply write` and `--apply branch` are rejected as unsupported in phase 1
- no external trajectory path is required unless `--from-trajectory` is passed
- `--task` without `--target` invokes framework target inference, not CLI-owned
  target selection

- [ ] **Step 2: Implement command as thin framework caller**

The single CLI command should construct framework config and call
`SelfEvolveRunner`, not own optimizer logic or split target types into separate
commands.

- [ ] **Step 3: Print report path and best candidate summary**

---

## Task 12: Wire Built-In AWorld Main Agent Opt-In

**Files:**

- Modify: `aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/aworld_agent.py`
- Test: targeted CLI agent config test if one exists, otherwise a narrow unit
  test for config construction helper

- [ ] **Step 1: Add env/config parsing**

Support:

- `AWORLD_SELF_EVOLVE_MODE=off|offline|shadow|online`

- [ ] **Step 2: Keep defaults off**

Verify no env means `SelfEvolveConfig.mode == "off"`.

---

## Task 13: Documentation And Example

**Files:**

- Add or modify docs under `docs/Agents/` or `docs/AWorld CLI/Commands/`
- Add minimal example under `examples/aworld_quick_start/self_evolve/`

- [ ] **Step 1: Document framework concepts**

- [ ] **Step 2: Document async post-run behavior**

Explain that self-evolve runs in the background and cannot affect the completed
task result.

- [ ] **Step 3: Document boundary with existing evolve/training assets**

- [ ] **Step 4: Document CLI usage**

- [ ] **Step 5: Add toy trajectory-driven optimization example**

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
