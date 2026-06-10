## MODIFIED Requirements

### Requirement: Trial-based evaluation

Suite-backed evaluation flows SHALL support independent repeated trials for each evaluation case while preserving current one-trial behavior by default.

#### Scenario: Suite declares multiple trials
- **WHEN** a suite-backed evaluator declares a trial policy with `num_trials` greater than one
- **THEN** the framework SHALL execute each original case independently for the configured number of trials

#### Scenario: Suite does not declare trials
- **WHEN** a suite-backed evaluator does not declare a trial policy
- **THEN** the framework SHALL execute each case once and preserve existing report behavior

#### Scenario: Trial metadata is attached
- **WHEN** a case is expanded into trial executions
- **THEN** each trial execution SHALL preserve the original case id, trial index, and trial id in serializable case or state metadata

### Requirement: pass@k and pass^k metrics

Trial-based evaluation SHALL compute pass@k and pass^k metrics from independent trial outcomes.

#### Scenario: pass@k is computed
- **WHEN** a suite declares pass@k for a metric and a case has at least k trials
- **THEN** the framework SHALL mark that case as pass@k when any of the first k independent trials passes the configured success metric

#### Scenario: pass^k is computed
- **WHEN** a suite declares pass^k for a metric and a case has at least k trials
- **THEN** the framework SHALL mark that case as pass^k when all of the first k independent trials pass the configured success metric

#### Scenario: Trial metrics are aggregated
- **WHEN** pass@k or pass^k is computed for all cases
- **THEN** the framework SHALL expose aggregate pass@k/pass^k rates as normal report metrics that composite gates can reference

### Requirement: Retry attempts are not trials

Trial-based evaluation SHALL keep runtime retry/fallback attempts separate from independent trials.

#### Scenario: Retry wrapper runs inside a trial
- **WHEN** a runtime-composed trial uses a retry or fallback wrapper
- **THEN** the framework SHALL count the selected terminal rollout as one trial and preserve child attempts only as trial artifacts or metadata

#### Scenario: pass@k excludes retry attempts
- **WHEN** pass@k or pass^k metrics are calculated
- **THEN** retry or fallback child attempts SHALL NOT increase the number of trials or directly contribute separate trial outcomes

### Requirement: Trial reports

Evaluator reports SHALL expose trial metadata and aggregate trial metrics additively.

#### Scenario: Multiple trials are reported
- **WHEN** a suite runs multiple trials
- **THEN** the report SHALL include trial policy metadata, total trial counts, and per-trial metadata sufficient to group trials by original case id

#### Scenario: Single-trial reports remain compatible
- **WHEN** a suite runs one trial
- **THEN** existing required report fields SHALL remain compatible and trial-specific fields SHALL be additive only

### Requirement: Environment reset is deferred

Trial-based evaluation SHALL acknowledge clean-environment isolation as a separate concern.

#### Scenario: Suite requires clean environment per trial
- **WHEN** a suite requires filesystem, database, sandbox, or external state reset between trials
- **THEN** the framework SHALL treat that reset orchestration as out of scope for this change and leave it to a dedicated environment-isolation change
