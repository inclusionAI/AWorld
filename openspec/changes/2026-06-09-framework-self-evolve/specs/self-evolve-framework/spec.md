## ADDED Requirements

### Requirement: AWorld framework MUST provide self-evolve as a first-class capability

AWorld framework MUST own the reusable self-evolve capability for optimizing
agent-facing harness artifacts.

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

#### Scenario: Agent opts into optimization eligibility

- **WHEN** an agent config sets an explicit optimize flag such as
  `optimize=True`
- **THEN** the agent MAY be considered eligible for self-evolve workflows
- **AND** candidate application MUST still follow the self-evolve mode, gates,
  and apply policy

### Requirement: Agent self-evolve configuration MUST separate eligibility from execution mode

AWorld MUST model whether an agent is eligible for optimization separately from
how self-evolve runs are executed.

#### Scenario: Optimize eligibility is true but self-evolve mode is offline

- **WHEN** an agent has optimize eligibility enabled
- **AND** its self-evolve mode is `offline`
- **THEN** normal task execution MUST NOT persistently mutate harness artifacts
- **AND** self-evolve MUST run only through explicit framework or CLI optimize
  invocations

#### Scenario: Self-evolve mode is shadow

- **WHEN** self-evolve mode is `shadow`
- **THEN** AWorld MAY collect diagnostics or generate candidate proposals
- **AND** it MUST NOT apply those candidates to active harness artifacts

#### Scenario: Self-evolve mode is online

- **WHEN** self-evolve mode is `online`
- **THEN** AWorld MAY perform bounded task-local repair or optimization actions
- **AND** persistent harness mutation MUST still require passing gates and an
  explicit apply policy

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

### Requirement: Phase-1 self-evolve MUST NOT include arbitrary code evolution

The first self-evolve implementation phase MUST optimize text harness artifacts
and selected config knobs only.

#### Scenario: User requests arbitrary code evolution

- **WHEN** a self-evolve request targets arbitrary framework or tool source code
- **THEN** AWorld MUST reject or mark the request unsupported for phase 1
- **AND** it SHOULD explain that code evolution requires a later change with
  stronger deterministic tests and review gates

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

### Requirement: Baseline and candidate evaluation MUST be comparable

AWorld MUST evaluate the baseline and all candidates under the same dataset,
scorer policy, and gate policy unless the run explicitly records a justified
exception.

#### Scenario: Candidate metrics are compared to baseline metrics

- **WHEN** AWorld selects the best candidate
- **THEN** it MUST compare candidate metrics against baseline metrics produced
  from the same evaluation policy
- **AND** it MUST persist both metric sets in the evolution run artifacts

### Requirement: Self-evolve MUST preserve run artifacts and lineage

Every self-evolve run MUST persist enough information to audit, reproduce, and
review the optimization.

#### Scenario: A self-evolve run completes with candidates

- **WHEN** a run completes
- **THEN** AWorld MUST persist run metadata, target identity, target
  fingerprint, dataset identity, optimizer policy, baseline metrics, candidate
  metrics, gate results, diagnostics, diffs, and selected candidate state
- **AND** it MUST write a human-readable report

#### Scenario: A self-evolve run fails

- **WHEN** candidate generation, evaluation, or gating fails
- **THEN** AWorld MUST persist a failed run record with the failure reason
- **AND** it MUST NOT silently discard diagnostics needed for debugging

### Requirement: Proposal-only MUST be the default apply policy

Self-evolve MUST generate reviewable proposals by default and MUST NOT mutate
target artifacts unless an explicit apply policy allows it.

#### Scenario: Apply policy is omitted

- **WHEN** a self-evolve run is started without an apply policy
- **THEN** AWorld MUST use proposal-only behavior
- **AND** target files or active runtime harness artifacts MUST remain unchanged

#### Scenario: Apply policy writes a candidate

- **WHEN** a self-evolve run is configured to write or branch a selected
  candidate
- **THEN** AWorld MUST apply only candidates that pass required gates
- **AND** it MUST record the applied candidate id and apply outcome

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

### Requirement: Self-evolve MUST enforce safety and regression gates

AWorld MUST validate candidates before selecting or applying them.

#### Scenario: Candidate does not improve enough

- **WHEN** a candidate fails the configured minimum improvement threshold
- **THEN** AWorld MUST mark the candidate as not passing gates
- **AND** it MUST NOT apply that candidate automatically

#### Scenario: Candidate regresses cost or latency beyond policy

- **WHEN** a candidate exceeds configured cost or latency regression limits
- **THEN** AWorld MUST mark the candidate as gated out

#### Scenario: Candidate is malformed

- **WHEN** a candidate breaks required target formatting, frontmatter, schema,
  or token limits
- **THEN** AWorld MUST reject the candidate before application
