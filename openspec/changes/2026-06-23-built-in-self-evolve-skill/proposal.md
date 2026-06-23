## Why

AWorld now has a framework-owned self-evolve subsystem that can package
trajectories, infer improvement targets, generate candidates, evaluate them,
run safety gates, persist artifacts, and optionally apply verified candidates
under explicit policy. That subsystem is intentionally not implemented as a
skill.

There is still a missing agent-facing surface: an agent can use the framework
only if it already knows when to call self-evolve, how to choose targets, which
evaluation evidence is acceptable, and which safety boundaries must never be
bypassed. Without a built-in skill, these operating rules remain scattered
across OpenSpec, CLI documentation, and framework APIs.

The goal of this change is to add a built-in `self_evolve` skill under the
repository skill catalog, modeled after the existing `aworld-skills/`
distribution style. The skill should act as an operating guide and thin
workflow adapter for self-evolve. It should make self-evolve discoverable to
agents while preserving the existing architectural boundary: the framework is
the engine; the skill is the agent-facing procedure for using the engine.

The plan shape should be similar to external self-evolution plans that describe
vision, target tiers, optimization loop, integration points, phase gates, and
execution flow. AWorld should adapt that structure to its own framework safety
model rather than copying external implementation details or licenses.

## Framework Prerequisites

This change depends on the framework self-evolve safety loop being the source
of truth. The built-in skill MUST NOT publish guidance that implies a framework
capability is production-ready until that capability is implemented and covered
by tests.

The initial skill content MUST distinguish:

- **available path**: skill-text targets, explicit target invocation,
  trajectory-backed target inference, proposal-only artifacts, and framework
  gate reporting when available
- **conditional path**: `auto_verified`, asynchronous post-run jobs, and
  verified apply guidance, which may be documented only when the corresponding
  framework runner, worker, gates, and post-apply checks are present and tested
- **roadmap path**: tool-description, prompt-section, agent-config, and
  workspace-artifact optimization when their target adapters remain skeletons or
  are otherwise not fully wired into the runner

If implementation finds the framework path is missing a required gate,
evaluation signal, target adapter, worker drain path, or non-no-op optimizer,
the skill MUST label that path as unavailable or roadmap-only instead of
teaching agents to rely on it.

## What Changes

- Add a built-in skill at `aworld-skills/self_evolve/SKILL.md`.
- Add a concise plan reference at
  `aworld-skills/self_evolve/references/plan.md`.
- Define the skill as an agent-facing procedure for invoking framework
  self-evolve, not as a replacement implementation for
  `aworld.self_evolve`.
- Make the skill default to proposal-only behavior and require framework gates
  for any automatic application.
- Make the skill prioritize target tiers:
  - skill text / `SKILL.md`
  - tool descriptions, only when the framework target adapter is implemented
  - prompt sections, only when the framework target adapter is implemented
  - allowlisted agent configuration knobs, only when the framework target
    adapter is implemented
  - workspace-local task artifacts only when framework provenance,
    protected-path gates, and isolated candidate evaluation allow them
- Keep framework, runtime, `aworld-cli`, package metadata, secrets, and
  protected built-in skills outside default mutation scope.
- Require the skill to use existing framework and CLI surfaces, especially
  `aworld-cli optimize` and `aworld.self_evolve` APIs.
- Require the skill to produce auditable outputs: target, evidence, candidate,
  dataset source, metrics, gate decisions, artifact paths, and apply status.
- Require the skill to include copy-pasteable examples for the currently
  available CLI and SDK paths, and to mark examples that need an injected real
  optimizer rather than the CLI fallback mutator.
- Ensure built-in skill discovery can expose the new skill in the same class of
  repository-distributed skills as `app_evaluator`.

## Capabilities

### New Capabilities

- `built-in-self-evolve-skill`: AWorld provides a built-in skill that teaches
  agents how to run controlled self-evolve workflows through framework-owned
  APIs and CLI commands.
- `self-evolve-plan-reference`: AWorld ships a concise plan-style reference
  describing target tiers, workflow stages, safety gates, and phased expansion
  for self-evolve use.

### Modified Capabilities

- `skill-catalog`: the repository skill catalog includes a built-in
  self-evolve operating skill.
- `self-evolve-framework`: framework self-evolve remains the execution owner,
  while a built-in skill becomes the human and agent-facing usage guide.
- `aworld-cli-self-evolve`: `aworld-cli optimize` remains the manual command
  surface used by the built-in skill when an agent needs a CLI path.

## Impact

- Affected files:
  - `aworld-skills/self_evolve/SKILL.md`
  - `aworld-skills/self_evolve/references/plan.md`
  - skill discovery or packaging tests, if repository-distributed skills are
    not already discovered from `aworld-skills/`
  - self-evolve documentation, if a user-facing index exists
- Safety constraints:
  - the skill MUST NOT directly edit targets as its primary behavior
  - the skill MUST NOT implement a second optimizer, scheduler, evaluator, gate
    system, or artifact store
  - the skill MUST NOT instruct agents to bypass framework gates
  - the skill MUST default to proposal-only or diagnostics-only operation
  - any `auto_verified` apply path MUST go through framework self-evolve gates
  - protected paths and protected skills MUST remain non-default targets
  - `aworld-skills/app_evaluator/SKILL.md` MUST remain protected and MUST NOT
    be treated as the template to mutate during self-evolve runs
  - `aworld-skills/self_evolve/SKILL.md` MUST remain protected from default
    self-mutation unless a later explicit policy allows self-hosting
    experiments with stronger review gates
  - framework, runtime, `aworld-cli`, package metadata, secrets, and product
    source code MUST remain out of phase-1 mutation scope
  - the skill MUST keep detailed plan content in a reference file so normal
    skill activation does not consume excessive prompt context
  - the skill description MUST be narrower than the existing `optimizer` skill
    and MUST steer trace-backed, framework-gated self-evolve requests to
    `self_evolve`
