## ADDED Requirements

### Requirement: The self_evolve skill MUST align guidance with implemented framework capability

The built-in skill MUST NOT present planned self-evolve capabilities as
available unless the corresponding framework path is implemented and covered by
tests. The skill MUST label each workflow or target tier as available,
conditional, or roadmap-only.

#### Scenario: Framework capability is implemented end to end

- **WHEN** the framework runner, target adapter, evaluation path, gates, and
  artifact reporting are implemented and tested for a workflow
- **THEN** the skill MAY document that workflow as available
- **AND** examples for that workflow MUST match current CLI or SDK behavior

#### Scenario: Framework capability requires explicit configuration

- **WHEN** a workflow depends on an evaluation backend, held-out cases,
  deterministic/objective verification, a target allowlist, post-apply
  re-evaluation, or an injected real optimizer
- **THEN** the skill MUST label that workflow as conditional
- **AND** it MUST list the required prerequisites before instructing agents to
  use that path

#### Scenario: Framework capability is planned but not wired

- **WHEN** a target adapter remains a skeleton, raises `NotImplementedError`,
  lacks runner integration, or lacks end-to-end tests
- **THEN** the skill MUST label that target tier as roadmap-only
- **AND** it MUST NOT instruct agents to run it as a currently available
  self-evolve path

### Requirement: AWorld MUST provide a built-in self-evolve operating skill

AWorld MUST ship a built-in `self_evolve` skill that helps agents use
framework self-evolve safely. The skill MUST be an operating guide and workflow
adapter, not a second implementation of candidate generation, evaluation,
gating, scheduling, application, or artifact persistence.

#### Scenario: Repository skill catalog includes self_evolve

- **WHEN** built-in repository skills are loaded from the `aworld-skills/`
  catalog
- **THEN** `self_evolve` MUST be available as a skill
- **AND** its primary skill file MUST live at
  `aworld-skills/self_evolve/SKILL.md`

#### Scenario: Agent uses the self_evolve skill

- **WHEN** an agent activates the `self_evolve` skill for an optimization task
- **THEN** the skill MUST instruct the agent to use `aworld.self_evolve`
  framework APIs or `aworld-cli optimize`
- **AND** it MUST NOT instruct the agent to bypass the framework runner, gates,
  or artifact store

#### Scenario: Skill content would duplicate framework behavior

- **WHEN** the built-in skill is implemented
- **THEN** it MUST NOT implement a separate optimizer, evaluator, scheduler,
  gate system, apply policy, rollback path, or self-evolve artifact store
- **AND** those responsibilities MUST remain owned by `aworld.self_evolve`

### Requirement: The self_evolve skill MUST default to proposal-only operation

The built-in skill MUST make proposal-only or diagnostics-only behavior the
default. Automatic application MUST be described only as a framework-gated path
that requires explicit configuration and verified gate success.

#### Scenario: User requests self-evolve without apply policy

- **WHEN** a user asks the agent to self-evolve a target without specifying an
  apply policy
- **THEN** the skill MUST guide the agent to run proposal-only or diagnostic
  self-evolve
- **AND** the expected outcome MUST be report and diff artifacts rather than
  direct target mutation

#### Scenario: User requests auto_verified application

- **WHEN** a user explicitly requests `auto_verified` application
- **THEN** the skill MUST require delegation to framework self-evolve
- **AND** framework gates MUST decide whether the candidate is applied,
  rejected, or left as a proposal
- **AND** the skill MUST require evidence that the framework path includes an
  evaluation backend, held-out cases, deterministic/objective signal, target
  allowlist, budget checks, protected-path checks, and post-apply
  re-evaluation
- **AND** the skill MUST NOT present manual text editing as an equivalent
  verified apply path

#### Scenario: Gate information is missing

- **WHEN** the agent cannot obtain complete framework gate results
- **THEN** the skill MUST instruct the agent to report proposal-only,
  diagnostic, or rejected status
- **AND** it MUST NOT claim verified improvement

### Requirement: The self_evolve skill MUST define target tier priority

The built-in skill MUST guide agents to prefer lower-risk, higher-signal target
types before broader harness or artifact changes.

#### Scenario: Skill text is a plausible target

- **WHEN** trajectory evidence or user intent points to a skill procedure issue
- **THEN** the skill MUST prefer target form `skill:<name>`
- **AND** candidate changes MUST preserve valid skill markdown and frontmatter

#### Scenario: Tool selection is the failure mode

- **WHEN** evidence shows wrong tool choice, missing tool use, or repeated tool
  invocation failure caused by agent-visible tool wording
- **THEN** the skill MAY guide the agent to target `tool:<tool-name>` only when
  the framework tool-description target adapter is implemented end to end
- **AND** candidate changes MUST be limited to agent-visible tool descriptions
  unless a later spec expands the target contract
- **AND** when that adapter is not implemented, the skill MUST label this tier
  roadmap-only

#### Scenario: A recurring behavior issue crosses tasks

- **WHEN** evidence suggests a prompt-section issue instead of a single skill
  or tool description issue
- **THEN** the skill MAY guide the agent to target `prompt:<section>` only when
  the framework prompt-section target adapter is implemented end to end
- **AND** the skill MUST require stronger regression evidence because prompt
  sections have broader blast radius
- **AND** when that adapter is not implemented, the skill MUST label this tier
  roadmap-only

#### Scenario: Agent configuration is considered

- **WHEN** a self-evolve run considers an agent configuration knob
- **THEN** the skill MUST restrict guidance to framework-allowlisted config
  fields and implemented config target adapters
- **AND** it MUST NOT suggest arbitrary config, secret, or credential mutation
- **AND** when the config target adapter is not implemented, the skill MUST
  label this tier roadmap-only

#### Scenario: Workspace artifact is considered

- **WHEN** a task-produced workspace artifact is considered as a target
- **THEN** the skill MUST require framework provenance and protected-path gates
- **AND** phase-1 guidance MUST remain proposal-only unless a later target
  policy explicitly permits automatic application

### Requirement: The self_evolve skill MUST preserve framework safety boundaries

The built-in skill MUST communicate the same high-level safety boundaries as
framework self-evolve.

#### Scenario: Protected path is selected

- **WHEN** the selected target is in framework, runtime, `aworld-cli`, package
  metadata, secret/config paths, shared infrastructure, product source code, or
  a protected skill
- **THEN** the skill MUST instruct the agent to reject or downgrade the run to
  diagnostics
- **AND** it MUST NOT instruct the agent to mutate the target

#### Scenario: app_evaluator is considered

- **WHEN** `aworld-skills/app_evaluator/SKILL.md` is considered as a default
  self-evolve target
- **THEN** the skill MUST treat it as protected
- **AND** the skill MUST NOT use it as a mutation template or default target

#### Scenario: self_evolve skill is considered

- **WHEN** `aworld-skills/self_evolve/SKILL.md` is considered as a default
  self-evolve target
- **THEN** the skill MUST treat it as protected
- **AND** the skill MUST NOT rewrite its own operating and safety instructions
  unless a later explicit self-hosting policy defines stronger review gates

#### Scenario: Judge-only improvement is observed

- **WHEN** candidate improvement is supported only by LLM judge output
- **THEN** the skill MUST describe the result as limited-confidence
- **AND** verified improvement MUST require framework-approved deterministic
  or objective signals

#### Scenario: Held-out evidence exists

- **WHEN** framework self-evolve uses held-out gate data
- **THEN** the skill MUST NOT instruct agents to expose held-out gate data to
  candidate mutators
- **AND** it MUST preserve the distinction between trainable failure cases and
  held-out verification cases

### Requirement: The self_evolve skill MUST produce auditable summaries

The built-in skill MUST instruct agents to summarize self-evolve work in a way
that lets users inspect what happened and continue the run.

#### Scenario: Self-evolve run completes

- **WHEN** a self-evolve run completes through the skill workflow
- **THEN** the agent's response MUST include the run id when available
- **AND** it MUST include the target or target-selection status
- **AND** it MUST include the evidence source
- **AND** it MUST include the best candidate id when available
- **AND** it MUST include metric deltas or explain why metrics are unavailable
- **AND** it MUST include gate status
- **AND** it MUST include report or artifact paths
- **AND** it MUST include whether changes were applied or only proposed

#### Scenario: Target inference declines to select a target

- **WHEN** framework self-evolve returns `no_target` or insufficient evidence
- **THEN** the skill MUST instruct the agent to report that no candidate was
  generated
- **AND** the agent SHOULD include the diagnostic reason and evidence summary

### Requirement: The self_evolve skill MUST include a plan-style reference

The built-in skill MUST include a concise plan reference file for larger
self-evolve planning and explanation. The skill body MUST stay operational and
MUST load the reference only when the user asks for architecture, roadmap,
strategy, or a larger self-evolve rollout plan.

#### Scenario: User asks how self-evolve should work

- **WHEN** the user asks for self-evolve architecture, roadmap, plan, or
  rollout strategy
- **THEN** the skill MUST direct the agent to read
  `aworld-skills/self_evolve/references/plan.md`
- **AND** that reference MUST cover vision, target tiers, optimization loop,
  AWorld integration points, safety gates, invocation forms, phases, and
  non-goals

#### Scenario: User asks to run a narrow optimization

- **WHEN** the user asks to run or prepare a narrow self-evolve proposal
- **THEN** the skill SHOULD use the concise workflow in `SKILL.md`
- **AND** it SHOULD NOT require loading the full plan reference unless needed
  for the task

### Requirement: The self_evolve skill MUST include verified invocation examples

The built-in skill MUST include copy-pasteable examples for the currently
available CLI and SDK paths. Examples MUST not imply that the CLI fallback
mutator produces real improvements when no real optimizer is configured.

#### Scenario: CLI proposal-only example is shown

- **WHEN** the skill shows a CLI example
- **THEN** it MUST include an explicit proposal-only invocation using
  `aworld-cli optimize`
- **AND** it MUST include a target or trajectory source form supported by the
  current CLI
- **AND** it MUST state that the CLI may preserve the baseline proposal when no
  real optimizer is configured

#### Scenario: SDK optimizer example is shown

- **WHEN** the skill shows an example that is expected to generate changed
  candidate content
- **THEN** the example MUST use a caller-supplied `CandidateOptimizer` or other
  configured real optimizer
- **AND** it MUST route execution through `SelfEvolveRunner` or
  `optimize_explicit_target(...)`
- **AND** it MUST NOT show direct target file mutation as the self-evolve
  mechanism

### Requirement: The self_evolve skill MUST remain compatible with existing CLI and framework surfaces

The built-in skill MUST use the existing framework and CLI ownership model for
self-evolve.

#### Scenario: Manual CLI path is appropriate

- **WHEN** the agent needs a manual command-line invocation
- **THEN** the skill MUST prefer `aworld-cli optimize` with target, dataset,
  session, trajectory, mode, and apply options as supported by the framework
- **AND** it MUST NOT introduce a second CLI command in phase 1

#### Scenario: SDK path is appropriate

- **WHEN** the work is inside framework code, tests, or a Python integration
- **THEN** the skill MAY guide use of `aworld.self_evolve` APIs directly
- **AND** it MUST still preserve framework-owned target, evaluation, gate, and
  artifact semantics

#### Scenario: Asynchronous post-run behavior is requested

- **WHEN** the user asks for self-evolve after agent runs
- **THEN** the skill MUST describe this as framework configuration and
  scheduler behavior
- **AND** it MUST NOT add skill-local background job semantics

### Requirement: The self_evolve skill MUST be distinguishable from the legacy optimizer skill

The built-in skill MUST use trigger language that separates framework-gated
self-evolve from broader legacy agent optimization workflows.

#### Scenario: A request asks for trace-backed self-evolve

- **WHEN** a request mentions self-evolve, trajectory-backed improvement,
  framework gates, self-evolve artifacts, `aworld-cli optimize`, or verified
  apply policy
- **THEN** runtime skill selection SHOULD prefer `self_evolve`

#### Scenario: A request asks for generic prompt or tool configuration editing

- **WHEN** a request asks for direct prompt or tool configuration optimization
  without framework self-evolve, evaluation artifacts, or gate semantics
- **THEN** the `self_evolve` skill SHOULD NOT claim ownership of that workflow
- **AND** the older `optimizer` skill MAY remain the better match
