# Framework Self-Evolve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AWorld framework provide a disabled-by-default self-evolve
capability that can optimize agent-facing harness artifacts such as skills,
prompt sections, tool descriptions, and selected agent config knobs, while
letting `aworld-cli` expose command and main-agent enablement surfaces.

**Architecture:** Add `aworld/self_evolve/` as the framework core. The core
models optimization targets, datasets, candidate optimizers, evaluation
backends, gates, run artifacts, and orchestration. The first implementation
reuses existing `EvaluateRunner`, trajectory / `llm_calls`, scorers, and Ralph
verification as backends. CLI adds `aworld-cli optimize` as a thin caller of the
framework API. Persistent application defaults to proposal-only.

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
- `aworld/self_evolve/datasets.py`
- `aworld/self_evolve/optimizers/__init__.py`
- `aworld/self_evolve/optimizers/base.py`
- `aworld/self_evolve/optimizers/llm_mutator.py`
- `aworld/self_evolve/optimizers/dspy_adapter.py`
- `aworld/self_evolve/evaluation.py`
- `aworld/self_evolve/gates.py`
- `aworld/self_evolve/store.py`
- `aworld/self_evolve/runner.py`

### Framework files to modify

- `aworld/config/conf.py`
- `aworld/runner.py` if a public `Runners.self_evolve(...)` convenience method
  is accepted
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
- `tests/self_evolve/test_store.py`
- `tests/self_evolve/test_runner.py`
- `tests/self_evolve/test_evaluation_backend.py`
- `tests/cli/test_optimize_command.py`

---

## Task 1: Add Disabled-By-Default Config Surface

**Files:**

- Modify: `aworld/config/conf.py`
- Test: `tests/self_evolve/test_config.py`

- [ ] **Step 1: Write config tests**

Cover:

- `AgentConfig().optimize is False`
- `AgentConfig().self_evolve_config.enabled is False`
- `AgentConfig(optimize=True)` is accepted
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

- `EvolutionRun`
- `EvolutionTargetRef`
- `CandidateVariant`
- `EvaluationSummary`
- `GateResult`
- `EvolutionRunStatus`

- [ ] **Step 2: Implement filesystem store**

Persist run artifacts under `.aworld/evolution/<run_id>/`.

- [ ] **Step 3: Test artifact persistence**

Cover run creation, candidate file writing, report writing, and stable paths.

---

## Task 3: Add Optimization Target Abstractions

**Files:**

- Create: `aworld/self_evolve/targets.py`
- Test: `tests/self_evolve/test_targets.py`

- [ ] **Step 1: Define `EvolutionTarget` protocol**

Required operations:

- identity
- load current content
- fingerprint current content
- render candidate diff
- apply candidate if explicitly requested

- [ ] **Step 2: Implement phase-1 targets**

Implement at least:

- `SkillTextTarget`
- `PromptSectionTarget` skeleton
- `ToolDescriptionTarget` skeleton
- `AgentConfigTarget` skeleton

Only `SkillTextTarget` must be fully runnable in the first implementation slice.

- [ ] **Step 3: Test skill target behavior**

Cover loading `SKILL.md`, fingerprinting, diff rendering, and proposal-only
non-mutation.

---

## Task 4: Add Dataset Builders

**Files:**

- Create: `aworld/self_evolve/datasets.py`
- Test: `tests/self_evolve/test_datasets.py`

- [ ] **Step 1: Add jsonl loader**

Load task/evaluation cases from jsonl.

- [ ] **Step 2: Add dataset identity and splits**

Compute dataset fingerprint and deterministic train/validation/test split.

- [ ] **Step 3: Add placeholders for session and trajectory mining**

Expose interfaces but keep them read-only and minimal in phase 1.

---

## Task 5: Add Evaluation Backend Contract

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

---

## Task 6: Add Candidate Optimizer Contract

**Files:**

- Create: `aworld/self_evolve/optimizers/base.py`
- Create: `aworld/self_evolve/optimizers/llm_mutator.py`
- Create: `aworld/self_evolve/optimizers/dspy_adapter.py`
- Test: `tests/self_evolve/test_optimizer_contract.py`

- [ ] **Step 1: Define optimizer protocol**

Add `CandidateOptimizer.propose(...)`.

- [ ] **Step 2: Implement LLM mutator fallback**

Use existing model config surfaces to propose candidate text variants.

- [ ] **Step 3: Implement optional DSPy adapter guard**

If DSPy is unavailable, fail only when adapter is selected.

---

## Task 7: Add Gates

**Files:**

- Create: `aworld/self_evolve/gates.py`
- Test: `tests/self_evolve/test_gates.py`

- [ ] **Step 1: Add score improvement gate**

- [ ] **Step 2: Add cost/latency regression gate**

- [ ] **Step 3: Add no-op and malformed-candidate gate**

- [ ] **Step 4: Add skill markdown/frontmatter gate**

---

## Task 8: Add SelfEvolveRunner

**Files:**

- Create: `aworld/self_evolve/runner.py`
- Test: `tests/self_evolve/test_runner.py`

- [ ] **Step 1: Write fake target / fake optimizer runner tests**

- [ ] **Step 2: Implement orchestration**

Flow:

1. load target
2. build dataset
3. evaluate baseline
4. generate candidates
5. evaluate candidates
6. run gates
7. persist artifacts
8. optionally apply selected candidate

- [ ] **Step 3: Verify proposal-only does not mutate target**

---

## Task 9: Add CLI Optimize Command

**Files:**

- Create: `aworld-cli/src/aworld_cli/top_level_commands/optimize_cmd.py`
- Create: `aworld-cli/src/aworld_cli/builtin_plugins/optimize_cli/__init__.py`
- Create:
  `aworld-cli/src/aworld_cli/builtin_plugins/optimize_cli/cli_commands/optimize.py`
- Test: `tests/cli/test_optimize_command.py`

- [ ] **Step 1: Add parser tests**

Cover:

- `aworld-cli optimize --target skill:demo --dataset eval.jsonl`
- `--task`
- `--from-session`
- `--from-trajectory`
- `--batch-config`
- `--apply proposal`

- [ ] **Step 2: Implement command as thin framework caller**

The CLI command should construct framework config and call
`SelfEvolveRunner`, not own optimizer logic.

- [ ] **Step 3: Print report path and best candidate summary**

---

## Task 10: Wire Built-In AWorld Main Agent Opt-In

**Files:**

- Modify: `aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/aworld_agent.py`
- Test: targeted CLI agent config test if one exists, otherwise a narrow unit
  test for config construction helper

- [ ] **Step 1: Add env/config parsing**

Support:

- `AWORLD_AGENT_OPTIMIZE=1`
- `AWORLD_SELF_EVOLVE=1`
- `AWORLD_SELF_EVOLVE_MODE=offline|shadow|online`

- [ ] **Step 2: Keep defaults off**

Verify no env means no optimize eligibility.

---

## Task 11: Documentation And Example

**Files:**

- Add or modify docs under `docs/Agents/` or `docs/AWorld CLI/Commands/`
- Add minimal example under `examples/aworld_quick_start/self_evolve/`

- [ ] **Step 1: Document framework concepts**

- [ ] **Step 2: Document CLI usage**

- [ ] **Step 3: Add toy skill optimization example**

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
