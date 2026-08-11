## ADDED Requirements

### Requirement: Independent AWorld Implementation

AWorld SHALL implement trusted improvement measurement with native AWorld
contracts and SHALL treat OpenRSI and other external systems as conceptual
references only.

#### Scenario: No external source dependency

- **GIVEN** trusted improvement measurement is built, tested, or executed
- **WHEN** its dependencies and runtime imports are resolved
- **THEN** no OpenRSI source, package, submodule, vendored file, or runtime
  service SHALL be required
- **AND** the capability SHALL use AWorld replay, evaluation, Campaign, budget,
  provenance, report, and storage contracts.

#### Scenario: Independent schemas and implementation

- **GIVEN** external projects describe controlled swaps or self-improvement
  measurement
- **WHEN** AWorld defines schemas, APIs, prompts, fixtures, tests, and artifact
  layouts
- **THEN** those assets SHALL follow AWorld naming and conventions
- **AND** OpenRSI implementation files, internal APIs, prompts, tests, fixtures,
  data models, and repository structure SHALL NOT be copied or treated as
  normative behavior.

### Requirement: Versioned Controlled Experiment

AWorld self-evolve SHALL represent every trusted improvement measurement as a
versioned controlled experiment with one declared swap axis.

#### Scenario: Artifact swap

- **GIVEN** an installed harness artifact and an evolved candidate artifact
- **WHEN** an artifact experiment is created
- **THEN** the experiment SHALL declare `swap_axis=artifact`
- **AND** it SHALL identify control and treatment fingerprints
- **AND** it SHALL freeze task model, generator, scheduler, evaluator, dataset,
  environment, runtime, prompt/context, sampling, and budget identities.

#### Scenario: Generator swap

- **GIVEN** two generator/operator implementations are compared
- **WHEN** a generator experiment is created
- **THEN** target baseline, framework, scheduler, evaluator, dataset, candidate
  opportunity ceiling, and search budget SHALL remain fixed
- **AND** the result SHALL compare candidate distributions and equal-budget
  best@K/pass@K curves rather than only final champions.

#### Scenario: Scheduler swap

- **GIVEN** two candidate schedulers are compared
- **WHEN** a scheduler experiment is created
- **THEN** generator, target baseline, evaluator, dataset, candidate interfaces,
  repair interfaces, and total search budget SHALL remain fixed
- **AND** the result SHALL compare quality-budget curves and time to threshold.

#### Scenario: Task-model swap

- **GIVEN** two task models are compared under one harness
- **WHEN** a task-model experiment is created
- **THEN** harness artifact, tools, prompt/context, dataset, evaluator,
  environment, and execution budget SHALL remain fixed
- **AND** task-model gain SHALL NOT be reported as harness-artifact gain.

#### Scenario: Multiple axes changed

- **GIVEN** more than one controlled component differs between experiment arms
- **WHEN** the initial single-axis executor validates the experiment
- **THEN** it SHALL mark the experiment invalid with
  `multiple_swap_axes_changed`
- **AND** it SHALL NOT produce a trusted component-attribution claim.

### Requirement: Separate Confidence Planes

AWorld self-evolve SHALL represent target resolution, experiment validity, and
improvement effect confidence as separate report sections.

#### Scenario: Explicit target confidence is not causal confidence

- **GIVEN** an operator explicitly selects `skill:agent-browser`
- **AND** target inference is bypassed
- **WHEN** a measurement report is written
- **THEN** target-resolution confidence MAY be `1.0`
- **BUT** causal-target confidence SHALL remain `null` unless separately
  measured
- **AND** improvement-effect confidence SHALL remain unavailable until a valid
  control/treatment experiment exists.

#### Scenario: Validity precedes effect

- **GIVEN** target resolution succeeded
- **WHEN** the controlled observations are incomparable
- **THEN** experiment validity SHALL be invalid or inconclusive as appropriate
- **AND** effect estimate and confidence interval SHALL be `null`
- **AND** target-resolution confidence SHALL NOT be reused as effect
  confidence.

### Requirement: Frozen Identity And Drift Validation

AWorld self-evolve SHALL fingerprint or explicitly mark the completeness of all
identities that can affect a controlled comparison.

#### Scenario: Required identities match

- **GIVEN** a controlled experiment declares frozen identities
- **WHEN** control and treatment observations are assembled
- **THEN** the framework SHALL verify task model, generator, scheduler,
  evaluator, dataset, environment, runtime, prompt/context, and budget
  fingerprints against the experiment contract
- **AND** matching observations MAY proceed to comparability analysis.

#### Scenario: Identity missing in shadow mode

- **GIVEN** shadow measurement cannot obtain one required identity fingerprint
- **WHEN** the experiment is evaluated
- **THEN** the report SHALL identify the missing field
- **AND** it SHALL downgrade the result to non-conclusive
- **AND** existing release behavior SHALL remain unchanged.

#### Scenario: Identity missing in required mode

- **GIVEN** required measurement cannot obtain one required identity
  fingerprint
- **WHEN** promotion eligibility is evaluated
- **THEN** the experiment SHALL fail closed with
  `missing_frozen_identity`
- **AND** the candidate SHALL NOT become promotion-eligible from that
  experiment.

### Requirement: Typed Experiment Validity

AWorld self-evolve SHALL classify experiment validity before estimating or
consuming an effect.

#### Scenario: Comparable task-level baseline failure

- **GIVEN** a baseline has a deterministic, task-level, replay-comparable
  failure with valid source evidence
- **AND** the treatment runs under the same contract
- **WHEN** paired evidence is assembled
- **THEN** the baseline failure MAY remain a valid control
- **AND** a candidate recovery MAY contribute a positive paired effect.

#### Scenario: Invalid control

- **GIVEN** a control is blocked, infrastructure-owned, mixed, unclassified, or
  otherwise incomparable under existing replay semantics
- **WHEN** experiment validity is evaluated
- **THEN** the experiment SHALL be invalid with a typed reason such as
  `control_not_comparable`
- **AND** effect SHALL be `null`
- **AND** the invalid control SHALL NOT be represented as zero or negative
  candidate improvement.

#### Scenario: Both arms fail without a comparable pair

- **GIVEN** baseline and candidate both time out or fail
- **AND** the replay contract reports zero comparable pairs
- **WHEN** measurement is summarized
- **THEN** task-plane candidate effect SHALL be `unmeasured`
- **AND** the report MAY separately record measurement-readiness progress
- **AND** the result SHALL NOT authorize candidate repair solely because the
  candidate did not recover.

#### Scenario: Dataset or environment drift

- **GIVEN** control and treatment use different dataset, split, environment, or
  runtime fingerprints outside the declared swap axis
- **WHEN** comparability is evaluated
- **THEN** the experiment SHALL be invalid with the corresponding drift reason
- **AND** no trusted effect SHALL be emitted.

### Requirement: Independent Cases And Repetitions

AWorld self-evolve SHALL distinguish independent task evidence from repeated
executions of the same task.

#### Scenario: Repetitions estimate stability

- **GIVEN** one task case is executed repeatedly under control and treatment
- **WHEN** observations are aggregated
- **THEN** repetitions SHALL contribute stability and within-case variance
- **AND** they SHALL NOT increase the independent case count.

#### Scenario: Uncertainty resamples independent cases

- **GIVEN** multiple independent task cases and repetitions exist
- **WHEN** a bootstrap or other resampling estimator is used
- **THEN** it SHALL resample independent task cases
- **AND** repetitions SHALL first be aggregated within their owning case.

#### Scenario: Small independent sample

- **GIVEN** comparable observations exist but the configured independent-case
  minimum is not met
- **WHEN** effect confidence is evaluated
- **THEN** the experiment SHALL be `valid_limited` or `inconclusive`
- **AND** required promotion SHALL fail closed
- **AND** the report SHALL state the independent and repetition counts.

### Requirement: Predeclared Outcomes And Effect Estimation

AWorld self-evolve SHALL predeclare primary outcomes, thresholds, aggregation,
and confidence level before treatment outcomes are evaluated.

#### Scenario: Paired effect report

- **GIVEN** a valid artifact or task-model experiment
- **WHEN** its primary effect is estimated
- **THEN** the report SHALL include point estimate, confidence interval,
  minimum-effect threshold, direction, estimator version, independent case
  count, and repetition count
- **AND** it SHALL preserve secondary quality, regression, cost, and latency
  metrics separately.

#### Scenario: Inconclusive positive point estimate

- **GIVEN** a valid experiment has a positive point estimate
- **BUT** its confidence interval does not clear the configured minimum effect
- **WHEN** a decision is derived
- **THEN** the effect SHALL be `inconclusive`
- **AND** the candidate SHALL NOT replace a trusted champion solely on the
  positive point estimate.

#### Scenario: Conclusive negative effect

- **GIVEN** a valid experiment's upper confidence bound is below the configured
  non-regression threshold
- **WHEN** the decision is derived
- **THEN** it SHALL produce `stop_negative_effect`
- **AND** the candidate SHALL NOT be promoted.

### Requirement: Search Opportunity And Budget Normalization

AWorld self-evolve SHALL separate component quality from additional search
opportunity.

#### Scenario: Unequal candidate counts

- **GIVEN** two generators or schedulers produced different candidate counts
- **WHEN** their results are compared
- **THEN** the report SHALL identify the opportunity mismatch
- **AND** it SHALL compare only shared supported K/budget points or mark the
  comparison descriptive
- **AND** it SHALL NOT attribute the full final-best difference to component
  quality.

#### Scenario: Best-of-N reporting

- **GIVEN** a candidate population is evaluated
- **WHEN** search performance is summarized
- **THEN** the report SHALL include actual candidate counts, best@1, best@K,
  pass@1, pass@K, validity rate, and authoritative-candidate yield at declared
  budget points
- **AND** it SHALL record the champion selection protocol.

#### Scenario: Search and measurement budgets are separate

- **GIVEN** a controlled experiment executes generation and evaluation work
- **WHEN** budget usage is persisted
- **THEN** search generation/repair/screening usage SHALL be accounted
  separately from control/treatment/evaluator/transfer usage
- **AND** both SHALL remain bounded by the Campaign total ceiling.

### Requirement: Measurement Yield And Early Stopping

AWorld self-evolve SHALL report how much comparable information a search or
measurement budget produced.

#### Scenario: Zero measurement yield

- **GIVEN** repeated candidate attempts produce no new comparable pairs, no new
  validity progress, and no reduction in effect uncertainty
- **WHEN** measurement yield is evaluated
- **THEN** the report SHALL record zero marginal measurement yield
- **AND** advisory or required policy SHALL stop, change measurement strategy,
  or route to measurement repair rather than continue identical candidate
  mutation.

#### Scenario: Information-bearing additional evidence

- **GIVEN** a valid but underpowered experiment
- **AND** additional independent cases are available within budget
- **WHEN** expected information value exceeds the configured threshold
- **THEN** the decision MAY be `collect_more_evidence`
- **AND** it SHALL identify the bounded additional measurement budget.

### Requirement: Transfer Panels

AWorld self-evolve SHALL support frozen cross-task, cross-Skill-family, and
temporal held-out transfer panels.

#### Scenario: Hidden transfer evidence

- **GIVEN** a transfer panel is assigned to an experiment
- **WHEN** candidates are generated, repaired, screened, or adaptively selected
- **THEN** panel contents and metrics SHALL remain unavailable to candidate
  generators and schedulers until the designated final evaluation stage.

#### Scenario: Temporal panel

- **GIVEN** a temporal transfer panel is used
- **WHEN** its manifest is created
- **THEN** it SHALL record the optimization evidence cutoff and immutable panel
  fingerprint
- **AND** every eligible case SHALL be sealed after or independently from the
  optimization evidence cutoff.

#### Scenario: Transfer regression

- **GIVEN** primary in-distribution effect is positive
- **BUT** a required transfer panel violates its configured non-regression
  policy
- **WHEN** promotion eligibility is evaluated
- **THEN** the candidate SHALL not be promotion-eligible
- **AND** the report SHALL preserve both the positive primary effect and the
  transfer regression without collapsing them into one score.

### Requirement: Measurement Artifacts And Report Boundary

AWorld self-evolve SHALL persist detailed measurement artifacts separately from
the bounded release-facing run report.

#### Scenario: Experiment artifact layout

- **GIVEN** a controlled experiment is created
- **WHEN** artifacts are persisted
- **THEN** the framework SHALL write `experiment.json`,
  `observations.jsonl`, and `attribution_report.json` under
  `.aworld/self_evolve/<run_id>/experiments/<experiment_id>/`
- **AND** canonical contracts and summaries SHALL use atomic or equivalent
  crash-safe persistence.

#### Scenario: Bounded report summary

- **GIVEN** an attribution report exists
- **WHEN** `report.json` is written
- **THEN** it SHALL include an optional versioned `measurement` summary with
  experiment id, mode, swap axis, validity, effect direction, bounded effect
  estimate, confidence lower bound, budget-normalization status,
  promotion-eligibility decision, reason, and attribution-report path
- **AND** it SHALL NOT copy raw per-case/per-seed observations into
  `report.json`.

#### Scenario: Public diagnostic projection

- **GIVEN** measurement observations reference trajectories, logs, failures,
  provider metadata, or local paths
- **WHEN** public artifacts are written
- **THEN** existing evidence minimization, secret redaction, path safety, and
  public diagnostic projection SHALL apply
- **AND** secrets and unbounded raw transcripts SHALL not be written into
  measurement summaries.

### Requirement: Measurement Policy Modes

AWorld self-evolve SHALL support `off`, `shadow`, `advisory`, and `required`
measurement-policy modes.

#### Scenario: Shadow mode preserves behavior

- **GIVEN** measurement mode is `shadow`
- **WHEN** an experiment completes
- **THEN** measurement artifacts SHALL be written
- **BUT** existing candidate selection, Campaign continuation, gate, apply, and
  rollback behavior SHALL remain unchanged.

#### Scenario: Advisory mode

- **GIVEN** measurement mode is `advisory`
- **WHEN** typed measurement decisions are produced
- **THEN** they MAY recommend evidence collection, measurement repair, stopping,
  or component switching
- **BUT** they SHALL NOT independently promote or reject an otherwise
  release-eligible candidate.

#### Scenario: Required mode

- **GIVEN** measurement mode is `required`
- **WHEN** promotion eligibility is evaluated
- **THEN** a valid conclusive positive effect, complete required identities,
  configured transfer policy, and all existing release gates SHALL be required
- **AND** missing, invalid, or inconclusive measurement SHALL fail closed.

### Requirement: Campaign Measurement Progress

AWorld Campaign SHALL track measurement readiness separately from candidate
effect progress.

#### Scenario: Measurement readiness advances without candidate quality

- **GIVEN** a previous cycle failed at replay capability compilation
- **AND** a later cycle reaches task rollout but produces no comparable pair
- **WHEN** Campaign progress is updated
- **THEN** measurement-readiness progress MAY advance
- **BUT** candidate-effect progress SHALL remain absent
- **AND** no quality champion SHALL be replaced by readiness progress alone.

#### Scenario: Invalid experiment routes ownership

- **GIVEN** an experiment is invalid because its control is not comparable
- **WHEN** Campaign derives its next action
- **THEN** it SHALL choose `repair_measurement` or `pause_operator` with typed
  ownership
- **AND** it SHALL NOT emit the measurement failure as a Skill behavior lesson
  or automatically authorize another candidate mutation.

#### Scenario: Valid candidate-owned failure

- **GIVEN** the control is valid and the candidate exhibits a typed
  candidate-owned repairable failure
- **WHEN** Campaign derives its next action
- **THEN** it MAY choose `continue_candidate_repair`
- **AND** the bounded feedback SHALL reference the valid experiment and failed
  candidate observation.

#### Scenario: Confidence-aware champion

- **GIVEN** two release-safe candidate runs have controlled effect reports
- **WHEN** a champion is selected in required mode
- **THEN** a candidate with invalid evidence or a lower effect confidence bound
  SHALL NOT replace a more trustworthy champion merely because its raw score
  or best-of-N result is higher
- **AND** regression and transfer invariants SHALL remain monotonic.

### Requirement: Concrete Invalid-Control Characterization

AWorld SHALL include a minimized test fixture that reproduces the structural
conditions of the observed `agent-browser` Campaign without depending on local
developer artifacts.

#### Scenario: Explicit target and unmeasured effect

- **GIVEN** a synthetic Campaign has an operator-explicit Skill target with
  resolution confidence `1.0`
- **AND** target inference is bypassed
- **AND** the frozen dataset has train, validation, and held-out members
- **AND** baseline and candidate both produce incomparable timeout outcomes
- **AND** zero comparable pairs and zero authoritative candidates result
- **WHEN** measurement is summarized
- **THEN** target resolution SHALL remain conclusive
- **AND** causal-target confidence and task-plane effect SHALL remain `null`
- **AND** experiment validity SHALL be `invalid`
- **AND** promotion eligibility SHALL be false.

#### Scenario: Measurement progress does not become Skill progress

- **GIVEN** the synthetic first cycle fails during replay capability compilation
- **AND** the second cycle reaches task rollout without a comparable pair
- **WHEN** Campaign progress is updated
- **THEN** measurement-readiness progress SHALL record the later stage
- **AND** candidate-effect progress SHALL remain absent
- **AND** repeated zero-yield candidate attempts SHALL lead to
  `repair_measurement`, `stop_no_effect`, or `pause_operator` according to
  policy rather than another identical candidate-repair cycle.

### Requirement: Backward Compatibility

Trusted improvement measurement SHALL be additive and SHALL preserve existing
self-evolve behavior when disabled or operating in shadow mode.

#### Scenario: Historical run report

- **GIVEN** a historical `report.json` has no `measurement` section
- **WHEN** it is loaded by Campaign or report tooling
- **THEN** it SHALL remain valid
- **AND** measurement mode SHALL be treated as `off`
- **AND** historical metrics SHALL not be upgraded to controlled evidence when
  required identities or observations are absent.

#### Scenario: Existing replay repetition semantics

- **GIVEN** existing replay artifacts contain multiple repetitions for one case
- **WHEN** they are imported into a measurement report
- **THEN** their stability information SHALL be preserved
- **AND** they SHALL NOT be reclassified as additional independent held-out
  cases.
