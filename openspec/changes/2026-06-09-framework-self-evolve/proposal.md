## Why

AWorld already has several ingredients needed for agent improvement loops:

- trajectory and `llm_calls` capture in the framework runtime
- `EvaluateRunner`, `EvalTarget`, `Scorer`, and LLM-as-judge scoring
- `RalphRunner` for bounded repair loops with verification feedback
- skill registry and prompt/context augmentation surfaces
- `aworld-cli` commands, batch execution, session logs, and durable memory

However, these pieces are not yet organized as a framework-level self-evolve
capability. Current improvement behavior is mostly task-local or prompt-driven:

- a user can ask the CLI AWorld agent to evaluate and improve an artifact
- `RalphRunner` can repair a task against explicit verification commands
- traces and trajectories are recorded, but not systematically converted into
  candidate harness changes

The target is to make self-evolution a first-class AWorld framework capability,
not only an `aworld-cli` workflow. AWorld should be able to run controlled
optimization loops over agent-facing artifacts such as skills, prompt sections,
tool descriptions, and agent configuration, while keeping the behavior safe,
measurable, and off by default.

## What Changes

- Add a framework-owned self-evolve module that manages optimization targets,
  datasets, candidate generation, evaluation, gates, and run artifacts.
- Add an explicit agent-level opt-in surface, such as `AgentConfig.optimize` and
  `SelfEvolveConfig`, with self-evolve disabled by default.
- Define a stable evaluation contract for self-evolve so the capability depends
  on an evaluation interface rather than one specific evaluator agent.
- Reuse existing `aworld.evaluations`, trajectory, `llm_calls`, and Ralph
  verification capabilities as default evaluation and repair backends.
- Add framework target types for phase 1:
  - skill text / `SKILL.md`
  - prompt sections
  - tool descriptions
  - agent config / harness knobs
- Defer code evolution to a later phase unless strong deterministic tests and
  explicit human approval are present.
- Add CLI product surfaces to invoke the framework capability:
  - a top-level `aworld-cli optimize` command
  - optional interactive `/optimize` command
  - optional enablement for the built-in AWorld main agent
- Persist evolution run metadata, candidate diffs, metrics, diagnostics, and
  approval state under a workspace-scoped `.aworld/evolution/` location.

## Capabilities

### New Capabilities

- `framework-self-evolve`: AWorld can optimize agent-facing harness artifacts
  through a controlled, measurable, framework-owned loop.
- `self-evolve-targets`: AWorld can model skills, prompt sections, tool
  descriptions, and agent configuration as explicit optimization targets.
- `self-evolve-evaluation-contract`: AWorld can evaluate baseline and candidate
  variants through a pluggable evaluation interface.
- `self-evolve-run-artifacts`: AWorld can persist lineage, metrics, diffs,
  diagnostics, and candidate approval state for each evolution run.

### Modified Capabilities

- `agent-configuration`: agents can opt into self-evolve eligibility through a
  disabled-by-default `optimize` / `self_evolve_config` contract.
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
  - candidate generation MUST NOT directly mutate active runtime behavior
  - automatic application MUST require explicit configuration and passing gates
  - code evolution MUST NOT be part of phase 1
  - evaluator-agent-only loops MUST NOT be the only correctness signal
