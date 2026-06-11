## Why

AWorld already has several ingredients needed for agent improvement loops:

- trajectory and `llm_calls` capture in the framework runtime
- `EvaluateRunner`, `EvalTarget`, `Scorer`, and LLM-as-judge scoring
- `RalphRunner` for bounded repair loops with verification feedback
- existing trajectory scorers such as `trajectory_validators`
- skill registry and prompt/context augmentation surfaces
- `aworld-cli` commands, batch execution, session logs, and durable memory
- an existing `Runners.evolve(...)` / `train.evolve.EvolutionRunner` pipeline
  for model/data/tool-synthesis training workflows

However, these pieces are not yet organized as a framework-level self-evolve
capability. Current improvement behavior is mostly task-local or prompt-driven:

- a user can ask the CLI AWorld agent to evaluate and improve an artifact
- `RalphRunner` can repair a task against explicit verification commands
- traces and trajectories are recorded, but not systematically converted into
  candidate harness changes
- existing `train.evolve` focuses on evolution/training workflows, not
  proposal-only harness text improvements

The target is to make self-evolution a first-class AWorld framework capability,
not only an `aworld-cli` workflow. AWorld should be able to run controlled
optimization loops after an agent run has produced a trajectory. In phase 1,
the loop MUST include trajectory-driven credit assignment: analyze trajectory
quality, identify which harness target is most likely responsible for the
failure or inefficiency, propose safe harness improvements, evaluate candidates,
and persist reviewable proposal/diff artifacts without blocking or changing the
original task result.

This proposal is intentionally separate from existing `train.evolve` assets.
Existing evolve code owns model/data/tool-synthesis training and may continue to
use names such as `EvolutionRunner` and `EvolutionConfig`. This change owns
proposal-only harness optimization under `aworld.self_evolve`, with artifacts
stored separately under `.aworld/self_evolve/`.

## What Changes

- Add a framework-owned self-evolve module that manages optimization targets,
  trajectory credit assignment, datasets, candidate generation, evaluation,
  gates, and run artifacts.
- Add an explicit agent-level opt-in surface, `AgentConfig.self_evolve_config`
  with `SelfEvolveConfig.mode`, with self-evolve disabled by default.
- Add an asynchronous post-run trigger path that can enqueue trajectory-driven
  self-evolve work after agent execution, without blocking the main task flow.
- Define a stable evaluation contract for self-evolve so the capability depends
  on an evaluation interface rather than one specific evaluator agent.
- Reuse existing `aworld.evaluations`, trajectory, `llm_calls`, and Ralph
  verification capabilities as default evaluation and repair backends.
- Add framework target types for phase 1:
  - skill text / `SKILL.md`
  - prompt sections
  - tool descriptions
  - agent config / harness knobs
  - workspace-local code or files, only when they are produced by agent task
    execution and validated in an isolated candidate workspace
- Defer framework, `aworld-cli`, and runtime source-code evolution to a later
  phase.
- Add CLI product surfaces to invoke the framework capability:
  - a single top-level `aworld-cli optimize` command with extensible options
    and target forms
  - optional interactive `/optimize` command
  - optional enablement for the built-in AWorld main agent
- Persist self-evolve run metadata, candidate diffs, metrics, diagnostics, and
  approval state under a workspace-scoped `.aworld/self_evolve/` location.

## Capabilities

### New Capabilities

- `framework-self-evolve`: AWorld can optimize agent-facing harness artifacts
  through a controlled, measurable, framework-owned loop that may run
  asynchronously after an opted-in agent execution.
- `self-evolve-credit-assignment`: AWorld can inspect a trajectory and produce
  an evidence-backed target selection report before candidate generation.
- `self-evolve-targets`: AWorld can model skills, prompt sections, tool
  descriptions, agent configuration, and isolated agent-produced workspace
  artifacts as explicit optimization targets.
- `self-evolve-evaluation-contract`: AWorld can evaluate baseline and candidate
  variants through a pluggable evaluation interface.
- `self-evolve-run-artifacts`: AWorld can persist lineage, metrics, diffs,
  diagnostics, and candidate approval state for each self-evolve run.

### Modified Capabilities

- `agent-configuration`: agents can opt into self-evolve through a
  disabled-by-default `self_evolve_config.mode` contract.
- `aworld-cli-task-execution`: CLI can expose commands that invoke framework
  self-evolve for a specified task, target, dataset, or previous session.
- `aworld-evaluation`: existing evaluation components become default backends
  for self-evolve but remain usable independently.

## Impact

- Affected framework areas:
  - `aworld/config/conf.py`
  - `aworld/evaluations/`
  - `aworld/dataset/`
  - `aworld/runners/`
  - `aworld/runners/ralph/`
  - `aworld/skills/`
  - `aworld/core/context/amni/`
- Affected CLI areas:
  - `aworld-cli/src/aworld_cli/top_level_commands/`
  - `aworld-cli/src/aworld_cli/builtin_plugins/`
  - `aworld-cli/src/aworld_cli/builtin_agents/smllc/agents/aworld_agent.py`
  - `aworld-cli/src/aworld_cli/executors/`
  - `aworld-cli/src/aworld_cli/memory/`
- Safety constraints:
  - self-evolve MUST be off by default
  - post-run self-evolve MUST be asynchronous and MUST NOT affect the completed
    task result
  - candidate generation MUST NOT directly mutate active runtime behavior
  - phase-1 runs MUST stop at proposal and diff artifacts
  - framework, `aworld-cli`, and runtime source-code evolution MUST NOT be part
    of phase 1
  - workspace-local task artifact optimization MUST run in isolation and MUST
    produce proposal and diff artifacts only in phase 1
  - self-evolve candidates MUST NOT modify AWorld framework or `aworld-cli`
    product logic
  - candidate selection MUST use validation data, while pass/fail gates MUST use
    optimizer-held-out evaluation data when enough cases are available
  - every run MUST enforce a token/cost budget ceiling
  - evaluator-agent-only loops MUST NOT be the only correctness signal
