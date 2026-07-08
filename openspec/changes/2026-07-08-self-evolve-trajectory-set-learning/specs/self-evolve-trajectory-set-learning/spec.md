## ADDED Requirements

### Requirement: Trajectory Set Optimize Input

AWorld self-evolve SHALL support optimizing from a trajectory set containing
one or more related trajectory members.

#### Scenario: Single trajectory remains valid

- **GIVEN** a user invokes optimize with one trajectory log
- **WHEN** the framework builds the optimization dataset
- **THEN** it SHALL create a trajectory set with one baseline member
- **AND** existing single-trajectory replay and evaluation behavior SHALL remain
  compatible.

#### Scenario: Multi-record trajectory log is internally grouped

- **GIVEN** a user invokes optimize with one trajectory log containing multiple
  task trajectories
- **WHEN** the framework builds the optimization dataset
- **THEN** it SHALL infer target/task-family groups internally
- **AND** candidate generation SHALL receive a target-coherent group rather than
  unrelated task trajectories mixed together
- **AND** the run report SHALL record selected and skipped groups for audit
- **AND** users SHALL NOT be required to author a trajectory-set JSON file for
  normal multi-trajectory log usage.

#### Scenario: Explicit trajectory set

- **GIVEN** a user invokes optimize with a trajectory-set file
- **WHEN** the framework loads the set
- **THEN** it SHALL accept a baseline-only trajectory collection as the normal
  explicit set form
- **AND** it SHALL validate each member's source, role, task identity, and
  available evidence metadata
- **AND** it SHALL reject malformed members with actionable diagnostics.

#### Scenario: Versioned trajectory set contract

- **GIVEN** a trajectory-set file is supplied
- **WHEN** the framework parses it
- **THEN** it SHALL require a versioned JSON contract with
  `schema_version`, `set_id`, `target`, and `members`
- **AND** member roles SHALL be limited to `baseline`, `candidate_replay`,
  `accepted_followup`, `rejected_candidate`, and `operator_added`
- **AND** user-authored files SHALL contain only `baseline` and, when
  explicitly needed, `operator_added` members
- **AND** `candidate_replay`, `accepted_followup`, and `rejected_candidate`
  members SHALL be generated or imported by the self-evolve framework from
  replay/evaluator artifacts and prior run history
- **AND** relative paths SHALL resolve from the trajectory-set file directory
- **AND** absolute paths SHALL be accepted only when they are inside trusted
  workspace or self-evolve artifact roots
- **AND** duplicate members SHALL be rejected or merged using a deterministic
  key derived from task input digest, role, candidate id, and source run id
- **AND** validation failures SHALL include the member index, field name,
  failure reason, and an actionable repair hint.

#### Scenario: Prior runs included

- **GIVEN** optimize is configured to include prior runs
- **WHEN** the target id is known
- **THEN** the framework SHALL import relevant accepted, rejected, replay, and
  follow-up production run summaries into the trajectory set
- **AND** it SHALL preserve source run ids for audit.

### Requirement: Lesson Extraction

AWorld self-evolve SHALL convert trajectory-set evidence into normalized lesson
records before candidate generation.

#### Scenario: Failure lessons

- **GIVEN** a trajectory member has failed gates, replay failures, or low
  evaluator dimensions
- **WHEN** lessons are extracted
- **THEN** the framework SHALL produce bounded failure causes and failure
  memories
- **AND** every lesson SHALL reference source runs, task ids, and evidence refs.

#### Scenario: Success lessons

- **GIVEN** a trajectory member is accepted or high scoring
- **WHEN** lessons are extracted
- **THEN** the framework SHALL produce success memories and a lean solution path
- **AND** the lean solution path SHALL omit failed attempts and preserve only
  necessary successful behavior.

#### Scenario: Evidence compaction

- **GIVEN** raw trajectory evidence is compacted, truncated, or artifact-backed
- **WHEN** lessons are extracted
- **THEN** the framework SHALL summarize the issue in normalized evidence fields
- **AND** it SHALL NOT copy large raw tool transcripts into candidate prompts.

### Requirement: Evidence Minimization And Trust Boundary

AWorld self-evolve SHALL treat trajectory evidence as untrusted input and
minimize sensitive evidence copied into prompts, reports, lessons, candidates,
and released skills.

#### Scenario: Sensitive evidence redaction

- **GIVEN** trajectory evidence contains secret-like values, authorization
  headers, cookies, personal identifiers, or private local paths
- **WHEN** lessons, candidate prompts, reports, or release-normalized skill
  content are written
- **THEN** sensitive values SHALL be redacted or replaced with bounded
  references
- **AND** the original sensitive text SHALL NOT appear in released skill
  instruction bodies.

#### Scenario: Prompt injection in evidence

- **GIVEN** trajectory evidence or tool output contains instructions to ignore
  policies, change evaluator behavior, or reveal hidden context
- **WHEN** lessons or candidates are generated
- **THEN** those strings SHALL be treated as quoted evidence only
- **AND** they SHALL NOT become framework, evaluator, or runtime skill
  instructions.

#### Scenario: Artifact references instead of raw transcripts

- **GIVEN** evidence is large, sensitive, or artifact-backed
- **WHEN** optimizer-facing feedback is built
- **THEN** it SHALL prefer digests, artifact refs, bounded summaries, and
  normalized evidence-quality fields over raw transcript content.

### Requirement: Harness Diagnostics As Advisory Lesson Memory

AWorld self-evolve SHALL record evidence-backed harness diagnostics as advisory
lesson memory before any harness or runtime behavior is changed.

#### Scenario: Harness diagnostic extraction

- **GIVEN** trajectory-set evidence contains context loss, workflow instability,
  tool protocol mistakes, evaluator inconsistencies, repeated rejected variants,
  protected-path attempts, or missing artifacts
- **WHEN** lessons are extracted
- **THEN** the framework SHALL produce `harness_diagnostic` lesson records with a
  diagnostic kind, severity, reproducibility, suggested prevention, source run
  ids, task ids, and evidence refs
- **AND** those records SHALL default to advisory promotion status.

#### Scenario: Reuse existing self-evolve diagnostic sources

- **GIVEN** evaluator summaries, gate results, feedback summaries, trace packs,
  replay diagnostics, or prior-run lineage already contain diagnostic signals
- **WHEN** harness diagnostics are extracted
- **THEN** the framework SHALL reuse those existing self-evolve records as inputs
  rather than requiring a separate global AWorld runtime telemetry contract
- **AND** it SHALL persist the resulting diagnostics as self-evolve-owned lesson
  memory.

#### Scenario: Advisory diagnostics do not mutate harness code

- **GIVEN** a harness diagnostic identifies a framework, workflow, evaluator, or
  permission-boundary problem
- **WHEN** candidate generation runs
- **THEN** the diagnostic MAY influence candidate strategy hints
- **AND** it SHALL NOT authorize mutation of `aworld.self_evolve`, CLI, gates,
  evaluators, or other harness/framework implementation code.

#### Scenario: Diagnostics converted into runtime-safe behavior

- **GIVEN** a harness diagnostic can be addressed by a target skill behavior such
  as preserving bounded evidence, using a safer tool protocol, or avoiding
  unverifiable claims
- **WHEN** a candidate uses that diagnostic
- **THEN** the candidate SHALL express only the runtime-safe behavior delta
- **AND** replay, gates, and release normalization SHALL still be required before
  the behavior becomes visible to normal skill loading.

### Requirement: Candidate Population From Lessons

AWorld self-evolve SHALL generate a bounded population of candidate strategies
from lessons before replaying candidates.

#### Scenario: Multiple candidate strategies

- **GIVEN** a trajectory set has multiple actionable lessons
- **WHEN** candidate generation runs
- **THEN** the framework SHALL produce candidate strategy records that include
  addressed lessons, considered harness diagnostics, preserved success
  behaviors, expected metric impact, risk, and replay priority.

#### Scenario: High baseline conservative delta

- **GIVEN** baseline or accepted follow-up trajectories are already high scoring
- **WHEN** candidate generation runs
- **THEN** candidates SHALL preserve success memories and lean solution paths
- **AND** proposed changes SHALL be limited to lesson-backed deltas such as
  improved evidence durability, fewer unnecessary steps, or robustness.

#### Scenario: Deterministic ranking and replay budget

- **GIVEN** a candidate population is generated
- **WHEN** candidates are selected for replay
- **THEN** the framework SHALL rank candidates deterministically using lesson
  coverage, success-path preservation, expected metric gain, evidence
  confidence, risk, and complexity
- **AND** it SHALL apply deterministic tie breakers based on smaller patch
  size, fewer runtime instruction changes, higher severity lesson coverage,
  and candidate id
- **AND** it SHALL replay only a bounded number of top-ranked candidates by
  default
- **AND** candidates not replayed because of budget SHALL be persisted with a
  `not_replayed_due_to_budget` reason.

#### Scenario: No safe delta

- **GIVEN** no lesson supports a safe behavior change
- **WHEN** candidate generation runs
- **THEN** the framework MAY produce a no-op optimization result
- **AND** the report SHALL explain that no candidate was applied because the
  evidence did not justify mutation.

### Requirement: Patch-Oriented Skill Candidate Materialization

AWorld self-evolve SHALL support materializing candidates from local patch
intents before replay.

#### Scenario: Patch validation

- **GIVEN** a candidate strategy contains patch intents
- **WHEN** the framework materializes a candidate skill
- **THEN** it SHALL validate front matter, protected paths, reference links, and
  size limits
- **AND** invalid materializations SHALL be rejected or repaired before replay.

#### Scenario: Minimal edits

- **GIVEN** a candidate only needs a small behavior delta
- **WHEN** the framework materializes the candidate
- **THEN** it SHOULD prefer localized edits over a full skill rewrite.

### Requirement: Lineage And Lesson Memory

AWorld self-evolve SHALL persist candidate lineage and lesson provenance across
optimize runs.

#### Scenario: Duplicate rejected candidate

- **GIVEN** a newly generated candidate has the same content fingerprint as a
  previously rejected candidate for the target
- **WHEN** duplicate gates run
- **THEN** the framework SHALL reject it without spending replay budget.

#### Scenario: Materially different candidate from old lesson

- **GIVEN** a candidate reuses a prior lesson but has a different semantic
  behavior delta
- **WHEN** duplicate gates run
- **THEN** the framework SHALL allow it to proceed if content and semantic
  fingerprints differ from rejected variants.

#### Scenario: Accepted lineage

- **GIVEN** a candidate is accepted
- **WHEN** apply completes
- **THEN** lineage memory SHALL record the accepted content fingerprint,
  normalized release fingerprint, source lessons, replay metrics, and gates.

### Requirement: Release Normalization

AWorld self-evolve SHALL normalize verified candidate skill content before it
becomes visible to normal runtime skill loading.

#### Scenario: Remove internal evaluation language

- **GIVEN** a candidate contains self-evolve internal terms such as source task
  ids, baseline/candidate score language, gate names, harness diagnostic labels,
  evidence ids, or evaluator rubric names
- **WHEN** release normalization runs
- **THEN** those terms SHALL be removed from runtime instruction bodies
- **AND** the equivalent runtime behavior constraints SHALL be preserved when
  they are needed for passed gates.

#### Scenario: Preserve release metadata

- **GIVEN** a normalized skill is applied
- **WHEN** metadata is written
- **THEN** release state, verified run id, candidate id, and verification time
  MAY be stored in front matter or sidecar metadata
- **AND** normal runtime instructions SHALL remain focused on user-task
  behavior.

#### Scenario: Post-apply verification

- **GIVEN** release normalization changes candidate content before apply
- **WHEN** post-apply verification runs
- **THEN** the runtime-loaded skill SHALL be compared against the normalized
  release content rather than the pre-normalization internal candidate content.

#### Scenario: Normalized release content is verified before apply

- **GIVEN** replay, evaluation, and gates passed for pre-normalization candidate
  content
- **WHEN** release normalization rewrites the candidate before apply
- **THEN** the normalized content SHALL pass either semantic equivalence checks
  for all gate-critical runtime behavior constraints or a replay/gate
  verification fallback
- **AND** apply SHALL be rejected if normalization removes a behavior constraint
  required by the accepted gates
- **AND** reports SHALL record pre-normalization and normalized fingerprints,
  preserved runtime constraints, and the normalization verification result.

### Requirement: Trajectory-Set Reporting

AWorld self-evolve SHALL report trajectory-set learning decisions in durable run
artifacts.

#### Scenario: Report includes learning summary

- **GIVEN** a trajectory-set optimize run completes
- **WHEN** `report.json` is written
- **THEN** it SHALL include links or summaries for trajectory set members,
  extracted lessons, harness diagnostics, generated candidate population,
  selected lineage, and release normalization.

#### Scenario: Harness diagnostic reporting

- **GIVEN** harness diagnostics were extracted
- **WHEN** `report.json` is written
- **THEN** it SHALL summarize diagnostic counts by kind
- **AND** it SHALL distinguish diagnostics used for candidate strategy hints from
  advisory diagnostics that were not promoted.

#### Scenario: Confidence label

- **GIVEN** an optimize run completes
- **WHEN** the report is written
- **THEN** it SHALL label the evidence mode as one of single trajectory,
  repeated single-case replay, trajectory-set validation, or held-out
  validation.

### Requirement: Framework Ownership Boundary

Trajectory-set learning SHALL remain owned by `aworld.self_evolve`, with CLI
and skills acting only as thin invocation or guidance surfaces.

#### Scenario: CLI trajectory-set invocation

- **GIVEN** `aworld-cli optimize` accepts a trajectory-set argument
- **WHEN** the command runs
- **THEN** it SHALL forward the request to framework APIs
- **AND** it SHALL NOT implement lesson extraction, candidate population,
  lineage memory, replay ranking, gates, or release normalization.

#### Scenario: Built-in self-evolve skill guidance

- **GIVEN** the built-in `self_evolve` skill describes trajectory-set optimize
- **WHEN** an agent reads that skill
- **THEN** the skill MAY explain operating workflow and boundaries
- **AND** it SHALL NOT contain framework implementation logic or gate bypass
  instructions.
