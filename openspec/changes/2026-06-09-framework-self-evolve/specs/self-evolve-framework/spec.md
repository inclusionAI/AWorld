## ADDED Requirements

### Requirement: AWorld framework MUST provide self-evolve as a first-class capability

AWorld framework MUST own the reusable self-evolve capability for optimizing
agent-facing harness artifacts. In phase 1, self-evolve means harness-text and
allowlisted harness-configuration evolution; it MUST NOT train model weights,
replace the agent policy, or modify framework/runtime/CLI product logic.

#### Scenario: Existing train/evolve remains separate

- **WHEN** a caller uses `Runners.evolve(...)` or `train.evolve.EvolutionRunner`
- **THEN** AWorld MUST continue to treat that as the existing training-oriented
  evolution pipeline
- **AND** framework self-evolve MUST use distinct `SelfEvolve*` names and
  `.aworld/self_evolve/` artifacts for controlled harness optimization

#### Scenario: SDK caller invokes self-evolve without aworld-cli

- **WHEN** a Python caller constructs a self-evolve run through framework APIs
- **THEN** AWorld MUST be able to load the target, run baseline evaluation,
  generate candidates, evaluate candidates, run gates, and persist artifacts
- **AND** the caller MUST NOT need `aworld-cli` command internals to use the
  capability

#### Scenario: aworld-cli invokes self-evolve

- **WHEN** `aworld-cli` runs a self-evolve command
- **THEN** it MUST delegate core behavior to the framework self-evolve APIs
- **AND** CLI code MUST NOT become the owner of target optimization,
  evaluation, gating, or artifact persistence logic

### Requirement: Self-evolve MUST be disabled by default

Agents MUST NOT become self-evolving unless explicitly configured.

#### Scenario: Agent config uses defaults

- **WHEN** an agent is constructed with default `AgentConfig`
- **THEN** self-evolve eligibility MUST be disabled
- **AND** task execution MUST behave as it did before this capability

#### Scenario: Agent opts into self-evolve mode

- **WHEN** an agent config sets `self_evolve_config.mode` to `offline`,
  `shadow`, or `online`
- **THEN** the agent MAY be considered opted into the corresponding
  self-evolve workflow
- **AND** automatic application MUST occur only when `online` mode and an
  explicit verified apply policy are configured

### Requirement: Agent self-evolve configuration MUST use mode as the opt-in surface

AWorld MUST model self-evolve opt-in through `SelfEvolveConfig.mode`.
`SelfEvolveConfig.mode` MUST include `off`, `offline`, `shadow`, and `online`.
AWorld MUST NOT require a separate `AgentConfig.optimize` or `enabled` flag for
phase 1.

#### Scenario: Self-evolve mode is off

- **WHEN** an agent's self-evolve mode is `off`
- **THEN** normal task execution MUST NOT enqueue self-evolve work
- **AND** explicit optimize APIs MUST treat the agent as disabled unless the
  caller overrides the mode for that run

#### Scenario: Self-evolve mode is offline

- **WHEN** an agent's self-evolve mode is `offline`
- **THEN** normal task execution MUST NOT persistently mutate harness artifacts
- **AND** self-evolve MUST run only through explicit framework or CLI optimize
  invocations

#### Scenario: Self-evolve mode is shadow

- **WHEN** self-evolve mode is `shadow`
- **THEN** AWorld MAY asynchronously collect diagnostics or generate candidate
  proposals after an agent run produces a trajectory
- **AND** it MUST NOT apply those candidates to active harness artifacts

#### Scenario: Self-evolve mode is online

- **WHEN** self-evolve mode is `online`
- **THEN** AWorld MAY asynchronously perform bounded optimization actions after
  an agent run produces a trajectory
- **AND** online mode MUST support controlled automatic application for at least
  one allowlisted target type when verified apply policy is enabled
- **AND** online self-evolve MUST NOT change the result of the task that already
  completed
- **AND** online apply MUST re-evaluate after application and roll back or mark
  the candidate rejected when post-apply metrics regress

### Requirement: Post-run self-evolve MUST be asynchronous and best-effort

When an opted-in agent completes a task with a usable trajectory, AWorld MUST
support enqueueing a background self-evolve job. That enqueue path MUST be
lightweight and MUST NOT affect the main task result.

#### Scenario: Runtime completion path exposes trajectory and llm calls

- **WHEN** `TaskEventRunner.do_run(...)` has completed task execution
- **AND** `_save_trajectories()` has populated `TaskResponse.trajectory`
- **AND** `_response()` has copied `llm_calls` into the response
- **THEN** AWorld MAY perform the post-run self-evolve eligibility check and
  best-effort enqueue
- **AND** `Runners.run(...)` MUST remain a delegating wrapper rather than owning
  trajectory extraction or enqueue semantics
- **AND** enqueue failures MUST NOT replace or mutate the completed
  `TaskResponse`

#### Scenario: Post-run enqueue succeeds

- **WHEN** an agent task completes
- **AND** `SelfEvolveConfig.mode` is `shadow` or `online`
- **AND** a trajectory is available
- **THEN** AWorld MUST be able to enqueue a background self-evolve job
- **AND** the task response MUST be returned without waiting for candidate
  generation, evaluation, gates, or artifact writing

#### Scenario: Post-run enqueue fails

- **WHEN** post-run self-evolve enqueue fails
- **THEN** AWorld MUST NOT fail the completed task
- **AND** it SHOULD record a diagnostic or warning for the enqueue failure

#### Scenario: Background self-evolve job fails

- **WHEN** background self-evolve fails after creating a run
- **THEN** AWorld MUST persist the failure reason in self-evolve artifacts
- **AND** it MUST NOT mutate active runtime behavior

#### Scenario: Short-lived process enqueues post-run self-evolve

- **WHEN** post-run self-evolve is enqueued from a process that may exit before
  background work completes
- **THEN** AWorld MUST persist a durable pending job before enqueue returns
- **AND** it MUST NOT rely only on an in-memory fire-and-forget task

### Requirement: Self-evolve MUST infer targets from trajectory evidence in phase 1

When a self-evolve run is task-driven or post-run trajectory-driven, AWorld MUST
analyze the trajectory before candidate generation and produce an auditable
target selection report.

#### Scenario: Credit-assignment spike has not been accepted

- **WHEN** the phase-0 credit-assignment spike has not demonstrated acceptable
  target-selection precision/recall on real manually labeled trajectories
- **THEN** AWorld MUST NOT expand task-driven candidate generation, async
  post-run scheduling, or automatic application
- **AND** implementation MUST remain limited to diagnostics and explicit-target
  proposal experiments

#### Scenario: Trajectory evidence selects a target

- **WHEN** a trajectory contains sufficient evidence that a skill, prompt
  section, tool description, whitelisted config knob, or agent-produced
  workspace artifact is the likely improvement target
- **THEN** AWorld MUST select that target through a framework credit-assignment
  policy
- **AND** the run artifacts MUST record the evidence references and confidence
  score

#### Scenario: Trajectory evidence is insufficient

- **WHEN** trajectory evidence is ambiguous or below confidence threshold
- **THEN** AWorld MUST record a `no_target` diagnostic
- **AND** it MUST avoid speculative candidate generation

#### Scenario: Caller supplies an explicit target

- **WHEN** a CLI or SDK caller provides an explicit target
- **THEN** AWorld MAY bypass target inference
- **AND** it SHOULD still record trajectory evidence when a trajectory source is
  supplied

### Requirement: Self-evolve MUST normalize trajectory evidence before target selection

AWorld MUST convert current trajectories, prior sessions, and trajectory logs
into bounded trace packs before credit assignment or trace-reflective candidate
generation.

#### Scenario: Raw trajectory is larger than the configured trace budget

- **WHEN** a trajectory exceeds the configured trace-pack budget
- **THEN** AWorld MUST preserve task input, first turns, final turns, tool
  calls, tool results, failed arguments, verification outputs, generated
  artifact references, LLM usage/cost metadata, and stable evidence ids
- **AND** it MAY summarize middle turns
- **AND** summaries MUST retain evidence references usable by target selection
  reports

#### Scenario: Optimizer uses trajectory feedback

- **WHEN** a trace-reflective optimizer is invoked
- **THEN** AWorld MUST provide the optimizer a trace pack, scorer feedback, and
  trainable failure cases
- **AND** it MUST NOT provide raw unbounded trajectories by default

### Requirement: Self-evolve MUST optimize explicit target types

AWorld MUST model each optimizable harness artifact as an explicit target with
a stable identity, load behavior, fingerprint, diff rendering, and apply
contract.

#### Scenario: Skill text target is selected

- **WHEN** a self-evolve run targets `skill:<name>`
- **THEN** AWorld MUST load that skill's `SKILL.md` or equivalent skill text
- **AND** it MUST compute a fingerprint for the current target content
- **AND** it MUST be able to render candidate changes as a reviewable diff

#### Scenario: Prompt section target is selected

- **WHEN** a self-evolve run targets a named prompt section
- **THEN** AWorld MUST resolve that section through a stable target identity
- **AND** candidate changes MUST remain scoped to that section

#### Scenario: Tool description target is selected

- **WHEN** a self-evolve run targets a tool description
- **THEN** AWorld MUST optimize only agent-visible descriptive text unless a
  later change explicitly expands the target contract
- **AND** the tool schema MUST remain valid after candidate generation

#### Scenario: Agent config target is selected

- **WHEN** a self-evolve run targets an agent config knob
- **THEN** AWorld MUST restrict candidates to whitelisted self-evolve-safe
  config fields
- **AND** it MUST NOT mutate arbitrary config or secrets

#### Scenario: Workspace-local artifact target is selected

- **WHEN** a self-evolve run targets code or files produced by agent task
  execution
- **THEN** AWorld MUST verify that the path is workspace-local and outside
  framework, `aworld-cli`, runtime, shared infrastructure, package metadata,
  secret/config paths, and AWorld product logic
- **AND** candidate evaluation MUST run in an isolated candidate workspace or
  overlay before proposal and diff artifacts are selected

### Requirement: Self-evolve targets MUST carry provenance and trust metadata

AWorld MUST track provenance separately from target file content so self-evolve
can distinguish framework-owned, user-authored, generated, external, and
protected artifacts.

#### Scenario: Target inventory is built

- **WHEN** AWorld builds a target inventory
- **THEN** each target entry MUST include source kind, write origin, trust
  level, protected status, and protected reason when applicable
- **AND** this metadata MUST NOT require modifying the target file itself

#### Scenario: Agent-produced workspace artifact is considered as a target

- **WHEN** a workspace-local artifact is selected as a target
- **THEN** AWorld MUST verify from trajectory/provenance evidence that the
  artifact was produced by the agent during task execution
- **AND** it MUST reject user-authored foreground files unless the caller
  explicitly targets them under a configured policy

#### Scenario: Existing app evaluator skill is encountered

- **WHEN** target inventory includes `aworld-skills/app_evaluator/SKILL.md`
- **THEN** AWorld MUST mark it protected for phase 1
- **AND** target inference MUST NOT select it as a default target
- **AND** candidate generation and proposal application MUST NOT mutate it
- **AND** it MAY only be used as an explicitly configured read-only scorer or
  fixture

### Requirement: Phase-1 self-evolve MUST NOT include framework or runtime code evolution

The first self-evolve implementation phase MUST optimize text harness artifacts,
selected config knobs, and isolated agent-produced workspace artifacts only.

#### Scenario: User requests framework or runtime code evolution

- **WHEN** a self-evolve request targets framework source, `aworld-cli` source,
  runtime source, shared infrastructure, package metadata, secret/config paths,
  or AWorld product logic
- **THEN** AWorld MUST reject or mark the request unsupported for phase 1
- **AND** it SHOULD explain that code evolution requires a later change with
  stronger deterministic tests and review gates

#### Scenario: Candidate diff touches protected source paths

- **WHEN** a candidate diff touches protected framework, `aworld-cli`, runtime,
  shared infrastructure, package metadata, secret/config paths, or AWorld
  product logic
- **THEN** AWorld MUST fail safety gates for that candidate
- **AND** it MUST NOT apply that candidate automatically

#### Scenario: Candidate diff touches app evaluator skill

- **WHEN** a candidate diff touches `aworld-skills/app_evaluator/SKILL.md`
- **THEN** AWorld MUST fail safety gates for that candidate
- **AND** it MUST preserve diagnostics explaining that the path is protected
- **AND** it MUST NOT apply that candidate automatically

### Requirement: Self-evolve MUST use a pluggable evaluation contract

Self-evolve MUST evaluate baseline and candidate variants through an evaluation
interface rather than depending directly on a single evaluator agent.

#### Scenario: Existing AWorld evaluation backend is used

- **WHEN** self-evolve is configured with the default evaluation backend
- **THEN** AWorld SHOULD reuse existing `EvaluateRunner`, `EvalTarget`, and
  `Scorer` capabilities
- **AND** it MUST return comparable baseline and candidate metrics

#### Scenario: Command verification is configured

- **WHEN** a self-evolve run includes deterministic verification commands
- **THEN** AWorld MUST execute those commands as evaluation signals
- **AND** failed verification MUST be represented in candidate diagnostics and
  gates

#### Scenario: Evaluator agent is configured as one signal

- **WHEN** a use case configures an evaluator agent or evaluator skill
- **THEN** AWorld MAY include that signal in the evaluation result
- **AND** self-evolve MUST still allow additional objective scorers and gates

### Requirement: Self-evolve LLM judges MUST be configurable evaluation signals

AWorld MUST allow judge behavior to be configured independently from optimizer
behavior. The default judge MUST belong to the self-evolve subsystem and operate
on trajectory evidence, while users MAY provide an explicit `agent.md` or
custom agent as an additional judge signal.

#### Scenario: No judge is explicitly configured

- **WHEN** a self-evolve run needs an LLM judge signal and no judge is supplied
- **THEN** AWorld SHOULD use the default self-evolve trajectory judge
- **AND** that judge MUST evaluate compact trace packs, target selection
  reports, baseline/candidate outputs, and scorer diagnostics

#### Scenario: User supplies an agent.md judge

- **WHEN** a caller configures a judge with an `agent.md` path
- **THEN** AWorld MUST load that agent through the configured agent-loading path
- **AND** it MUST run the judge as an evaluation signal
- **AND** the judge output MUST be persisted in run artifacts

#### Scenario: User supplies a custom judge agent

- **WHEN** a caller configures a custom judge agent or registered agent name
- **THEN** AWorld MUST run that judge through the evaluation contract
- **AND** it MUST NOT let the judge own candidate generation, target mutation,
  or apply policy

#### Scenario: LLM judge is the only positive signal

- **WHEN** a candidate improves only according to LLM judge output
- **THEN** AWorld MAY persist the candidate as a proposal
- **AND** it MUST mark confidence as limited rather than verified

### Requirement: Self-evolve eval sources MUST be pluggable

Self-evolve MUST NOT depend on a hard-coded trajectory log path. The current
run trajectory is the default post-run source, and external trajectory logs,
sessions, jsonl datasets, and batch configs are optional explicit sources.

#### Scenario: Post-run self-evolve uses current trajectory

- **WHEN** self-evolve is triggered after an agent run
- **THEN** AWorld MUST use the current run trajectory as the default
  credit-assignment, evaluation, and diagnostic source
- **AND** it MUST NOT require any external trajectory log file

#### Scenario: Post-run source has too few held-out cases

- **WHEN** a post-run self-evolve job only has the current trajectory or fewer
  than the configured minimum eval cases
- **THEN** AWorld MAY produce a target selection report, diagnostics, proposal,
  and diff
- **AND** it MUST mark candidate confidence as limited
- **AND** it MUST NOT mark the candidate as verified unless additional eval
  sources satisfy held-out and deterministic-signal gates

#### Scenario: External trajectory log is supplied

- **WHEN** a caller supplies a trajectory log path through API or CLI
- **THEN** AWorld MAY use that path as an additional evaluation source
- **AND** the source identity MUST be recorded in run artifacts

#### Scenario: No sufficient eval source is available

- **WHEN** available evaluation sources are insufficient for reliable
  candidate evaluation
- **THEN** AWorld MUST record diagnostics
- **AND** it SHOULD avoid generating or applying candidates

### Requirement: Self-evolve MUST persist dataset recipes and split discipline

AWorld MUST persist how each evaluation dataset was built, including source
selection, filters, split seed, synthetic generation policy, and holdout policy.

#### Scenario: Dataset is built from multiple sources

- **WHEN** AWorld builds a dataset from current trajectory, session history,
  jsonl cases, batch configs, or synthetic cases
- **THEN** it MUST persist a dataset recipe with source identities and filters
- **AND** it MUST persist the resulting train, validation, and held-out test
  split identities

#### Scenario: Failure cases are exposed to an optimizer

- **WHEN** candidate generation receives failure examples
- **THEN** AWorld MAY provide trainable failure cases and validation feedback
- **AND** it MUST keep held-out failure cases and held-out judge outputs hidden
  from the optimizer

### Requirement: Baseline and candidate evaluation MUST be comparable

AWorld MUST evaluate the baseline and all candidates under the same dataset,
scorer policy, and gate policy unless the run explicitly records a justified
exception.

#### Scenario: Candidate metrics are compared to baseline metrics

- **WHEN** AWorld selects the best candidate
- **THEN** it MUST compare candidate metrics against baseline metrics produced
  from the same evaluation policy
- **AND** it MUST persist both metric sets in the self-evolve run artifacts

#### Scenario: Candidate is marked verified

- **WHEN** a candidate is marked as a verified improvement
- **THEN** candidate selection MUST have used validation metrics
- **AND** pass/fail gates MUST have used optimizer-held-out test metrics when at
  least the configured minimum eval case count is available
- **AND** the optimizer MUST NOT have received held-out test cases or held-out
  judge outputs during candidate generation
- **AND** at least one deterministic signal, command verification, exact or
  objective scorer, or configured regression benchmark MUST support the
  improvement

#### Scenario: Eval case count is too low

- **WHEN** there are too few evaluation cases for the configured held-out gate
- **THEN** AWorld MAY still persist a proposal and diff
- **AND** it MUST mark confidence as limited rather than verified

#### Scenario: Global harness-text target is marked verified

- **WHEN** a verified candidate targets skill text, a prompt section, or a tool
  description
- **THEN** AWorld MUST require a configured regression benchmark or equivalent
  objective regression suite independent from the source trajectory
- **AND** if no such benchmark is configured, the candidate MUST remain
  limited-confidence

### Requirement: Self-evolve MUST preserve run artifacts and lineage

Every self-evolve run MUST persist enough information to audit, reproduce, and
review the optimization.

#### Scenario: A self-evolve run completes with candidates

- **WHEN** a run completes
- **THEN** AWorld MUST persist run metadata, target identity, target
  fingerprint, dataset identity, optimizer policy, baseline metrics, candidate
  metrics, gate results, diagnostics, diffs, and selected candidate state
- **AND** it MUST persist target provenance, trust metadata, dataset recipe,
  split identities, and optimizer lineage
- **AND** it MUST persist target selection reports and trajectory evidence for
  inferred targets
- **AND** it MUST persist whether the run came from asynchronous post-run
  enqueue, explicit SDK/API invocation, or CLI invocation
- **AND** it MUST write a human-readable report

#### Scenario: A self-evolve run fails

- **WHEN** candidate generation, evaluation, or gating fails
- **THEN** AWorld MUST persist a failed run record with the failure reason
- **AND** it MUST NOT silently discard diagnostics needed for debugging

### Requirement: Proposal-only MUST be the default apply policy

Self-evolve MUST generate reviewable proposals and diffs by default. Persistent
target mutation MUST require an explicit verified apply policy.

#### Scenario: Apply policy is omitted

- **WHEN** a self-evolve run is started without an apply policy
- **THEN** AWorld MUST use proposal-only behavior
- **AND** target files or active runtime harness artifacts MUST remain unchanged

#### Scenario: Online verified application is configured

- **WHEN** a self-evolve run is configured with `mode="online"` and
  `apply_policy="auto_verified"`
- **AND** the selected candidate targets an allowlisted target type
- **AND** all verification, regression, protected-path, provenance, budget, and
  post-apply re-evaluation gates pass
- **THEN** AWorld MUST automatically apply and accept the candidate without
  requiring human review, approval, confirmation, or intervention
- **AND** it MUST persist the applied diff, post-apply metrics, lineage, and
  acceptance decision

#### Scenario: Online verified application fails post-apply gates

- **WHEN** a candidate is applied under `auto_verified`
- **AND** post-apply metrics regress or required gates fail
- **THEN** AWorld MUST roll back the candidate or mark it rejected according to
  the target apply policy
- **AND** it MUST persist rollback or rejection diagnostics

#### Scenario: Non-allowlisted application is requested

- **WHEN** a self-evolve run requests automatic application for a target type
  that is not allowlisted for auto-apply
- **THEN** AWorld MUST fall back to proposal-only behavior or reject the apply
  policy with diagnostics
- **AND** it MUST preserve proposal and diff artifacts for optional review when
  available

### Requirement: Candidate generation MUST be optimizer-pluggable

AWorld MUST support candidate generation through an optimizer abstraction so
different optimization engines can be used without changing target or
evaluation contracts.

#### Scenario: Low-dependency optimizer is used

- **WHEN** no optional optimizer dependency is configured
- **THEN** AWorld SHOULD support a basic LLM mutator optimizer for text targets
- **AND** candidate generation MUST still produce auditable variants

#### Scenario: Optional DSPy optimizer is selected

- **WHEN** a DSPy/GEPA-style optimizer is selected
- **AND** the optional dependency is not installed
- **THEN** AWorld MUST fail with a clear configuration error at optimizer
  selection time
- **AND** importing the framework MUST NOT fail

#### Scenario: GEPA-style optimizer is selected

- **WHEN** a GEPA-style optimizer is selected for skill, prompt, or tool
  description text
- **THEN** AWorld MUST provide trace packs and validation feedback through the
  optimizer abstraction
- **AND** target loading, evaluation, gates, and artifacts MUST remain owned by
  framework self-evolve contracts rather than by DSPy-specific code

#### Scenario: MIPRO-style optimizer is selected

- **WHEN** a MIPRO-style optimizer is selected for instruction text or few-shot
  examples
- **THEN** AWorld MUST require enough training and validation examples for that
  optimizer policy
- **AND** it MUST fail with a clear configuration error when the dataset is too
  small for the selected optimizer

#### Scenario: Darwinian/code optimizer is requested in phase 1

- **WHEN** a caller requests Darwinian/code evolution
- **THEN** AWorld MUST treat it as a future external CLI/subprocess adapter
- **AND** AWorld core MUST NOT import AGPL Darwinian optimizer libraries
- **AND** phase-1 code target support MUST remain limited to proposal-only
  agent-produced workspace artifacts with isolated evaluation

#### Scenario: Candidate lineage is recorded

- **WHEN** an optimizer returns candidate variants
- **THEN** AWorld MUST record backend identity, parent candidate ids when
  applicable, mutation rationale, and trainable failure-case ids used
- **AND** that lineage MUST be persisted with the run artifacts

### Requirement: Self-evolve MUST enforce safety and regression gates

AWorld MUST validate candidates before selecting or applying them.

#### Scenario: Candidate does not improve enough

- **WHEN** a candidate fails the configured minimum improvement threshold
- **THEN** AWorld MUST mark the candidate as not passing gates
- **AND** it MUST NOT apply that candidate automatically

#### Scenario: Candidate is automatically applied

- **WHEN** AWorld automatically applies a verified candidate
- **THEN** the candidate MUST have passed held-out gates, deterministic or
  objective signal requirements, regression benchmarks for global targets,
  protected-path gates, trust/provenance gates, and run budget gates
- **AND** AWorld MUST re-run required gates after application before accepting
  the change

#### Scenario: Candidate regresses cost or latency beyond policy

- **WHEN** a candidate exceeds configured cost or latency regression limits
- **THEN** AWorld MUST mark the candidate as gated out

#### Scenario: Candidate is malformed

- **WHEN** a candidate breaks required target formatting, frontmatter, schema,
  or token limits
- **THEN** AWorld MUST reject the candidate before application

#### Scenario: Candidate touches protected paths

- **WHEN** a candidate attempts to modify framework, `aworld-cli`, runtime,
  shared infrastructure, package metadata, secret/config paths, or AWorld
  product logic
- **THEN** AWorld MUST mark the candidate as gated out
- **AND** it MUST NOT apply the candidate

#### Scenario: Candidate originates from an external or generated target

- **WHEN** a candidate target has external, generated, or community trust
  metadata
- **THEN** AWorld MUST run configured trust/provenance and static
  security/content gates before selecting the candidate
- **AND** a failed scan MUST prevent the candidate from being marked verified

#### Scenario: Global target lacks a regression benchmark

- **WHEN** a candidate changes a global skill, prompt section, or tool
  description
- **AND** no regression benchmark is configured for that target
- **THEN** AWorld MUST NOT mark the candidate as verified
- **AND** it MAY still emit proposal and diff artifacts for optional review

#### Scenario: Run budget is exhausted

- **WHEN** candidate generation, evaluation, judge repetition, or verification
  would exceed the configured run token or cost budget
- **THEN** AWorld MUST stop the run or skip the remaining work
- **AND** it MUST persist the budget stopping reason

### Requirement: Self-evolve MUST stop bounded runs

AWorld MUST prevent unbounded self-evolve loops.

#### Scenario: Maximum iterations reached

- **WHEN** a self-evolve run reaches its configured maximum iterations
- **THEN** AWorld MUST stop candidate generation for that run
- **AND** it MUST persist the stopping reason

#### Scenario: Improvement stalls

- **WHEN** candidates fail to meet the configured minimum improvement threshold
- **THEN** AWorld MUST stop or mark the run as having no passing candidate

#### Scenario: Pending proposal already exists

- **WHEN** the same agent and target already have a pending proposal
- **THEN** AWorld SHOULD avoid enqueueing duplicate background self-evolve work
- **AND** it SHOULD record a cooldown or duplicate-suppression diagnostic
