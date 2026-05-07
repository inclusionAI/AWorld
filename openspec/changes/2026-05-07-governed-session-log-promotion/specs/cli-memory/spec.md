## ADDED Requirements

### Requirement: Governed durable promotion MUST evaluate persisted session-log candidates

Automatic durable promotion for `aworld-cli` MUST evaluate a candidate that has
already been durably written to workspace session logs.

#### Scenario: A completed turn yields a promotable candidate

- **WHEN** turn-end extraction writes a durable-memory candidate into the
  workspace session log
- **THEN** the candidate MUST have a stable source identity
- **AND** any governed promotion decision MUST reference that persisted source
  identity rather than only a transient turn-end string

### Requirement: Governed promotion MUST support explicit policy modes

The CLI memory flow MUST support policy modes `off`, `shadow`, and
`governed`.

#### Scenario: Shadow mode evaluates without mutating active durable memory

- **WHEN** governed promotion mode is `shadow`
- **AND** a session-log candidate would otherwise satisfy promotion policy
- **THEN** the system MUST record the governed decision and explanation
- **AND** it MUST NOT append active durable memory automatically

### Requirement: Governed promotion decisions MUST be explainable and source-linked

Every governed promotion decision MUST carry enough information for later
inspection, review, and rollback.

#### Scenario: An operator inspects a governed decision

- **WHEN** a governed promotion decision is listed through CLI memory status or
  review surfaces
- **THEN** the decision MUST expose its `decision_id`, `policy_mode`,
  `policy_version`, `decision`, `reason`, `confidence`, and `source_ref`
- **AND** any blockers that prevented promotion MUST be visible

### Requirement: Only safe governed candidates MAY become active durable memory

The governed auto-promotion path MUST not promote temporary, duplicate, or
otherwise blocked candidates into active durable memory.

#### Scenario: A candidate satisfies governed promotion policy

- **WHEN** a persisted session-log candidate is non-temporary, non-duplicate,
  eligible for active durable recall, and evaluated under `governed` mode
- **THEN** the system MAY append a durable-memory record for that candidate
- **AND** the resulting record MUST retain a link to the decision that promoted
  it

#### Scenario: A candidate fails governed promotion policy

- **WHEN** a persisted session-log candidate is temporary, duplicate,
  unsupported, or insufficiently safe for governed promotion
- **THEN** the system MUST leave active durable memory unchanged
- **AND** the candidate MUST resolve to either `session_log_only` or
  `rejected`

### Requirement: Governed review and correction MUST remain append-only

Operator review of governed promotion MUST preserve historical source and
decision records.

#### Scenario: An operator reverts a previous governed promotion

- **WHEN** an operator reverts a previously auto-promoted candidate
- **THEN** the system MUST record that correction as an append-only review
  action keyed by `decision_id`
- **AND** future active durable recall MUST exclude the reverted promotion
- **AND** the original session-log candidate and decision records MUST remain
  preserved

### Requirement: Promotion quality metrics MUST include explicit review outcomes

The CLI memory subsystem MUST support promotion-quality metrics that are backed
by explicit review outcomes rather than raw decision counts only.

#### Scenario: Review outcomes are available for governed promotions

- **WHEN** governed decisions have operator review labels such as `confirmed`
  or `reverted`
- **THEN** the system MUST be able to compute reviewed-promotion counts
- **AND** it MUST compute a precision proxy from confirmed promotions
- **AND** it MUST compute a pollution proxy from reverted promotions
- **AND** it MUST expose pending-review counts separately

### Requirement: Broad default auto-promotion rollout MUST be threshold-gated

Default broad rollout of governed auto-promotion MUST remain blocked until
quality thresholds are met.

#### Scenario: Rollout thresholds are not yet satisfied

- **WHEN** fewer than 100 governed-promotion decisions have been reviewed
- **OR** the precision proxy is below `0.90`
- **OR** the pollution proxy is above `0.05`
- **THEN** the default policy mode MUST remain `shadow`
- **AND** status/reporting surfaces MUST show that governed default rollout is
  still blocked

### Requirement: Explicit durable-memory writes MUST remain immediate

Governed session-log promotion MUST NOT block explicit durable-memory writes
requested by the operator.

#### Scenario: The operator explicitly remembers durable guidance

- **WHEN** the operator uses `/remember` or another explicit durable-memory
  write surface
- **THEN** that write MUST update active durable memory immediately
- **AND** it MUST NOT require governed auto-promotion mode to be enabled

### Requirement: Governed promotion MUST preserve runtime message-memory behavior

This change MUST compose with the existing hybrid-memory seam without changing
runtime message-memory semantics for existing consumers.

#### Scenario: Existing runtime message-memory consumers execute under the hybrid provider

- **WHEN** `aworld-cli` runs with the hybrid provider and governed
  session-log promotion enabled
- **THEN** existing runtime-memory operations such as message history access,
  summary generation, and long-term extraction MUST continue to behave as they
  did before this change
- **AND** governed durable-memory decisions MUST remain confined to the CLI
  durable-memory path
