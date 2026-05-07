## Context

The current CLI memory branch already established the lowest-risk baseline:

- explicit durable writes through `/memory` and `/remember`
- append-only session logs for turn-end extraction
- a narrow heuristic `PromotionDecision`
- append-only `llm_calls` truth records with request-linked observability

That baseline was intentional. It created durable truth sources without forcing
governance decisions too early.

The next smallest clean step is to govern how session-log candidates can become
active durable memory.

## Goals / Non-Goals

**Goals**

- Promote durable memory only from persisted session-log candidates.
- Make every promotion decision explainable and source-linked.
- Replace env-flagged heuristic auto-promotion with explicit governance modes.
- Add minimal operator review and correction surfaces.
- Add promotion quality metrics and rollout thresholds.
- Keep explicit user durable writes immediate and unchanged.

**Non-Goals**

- Do not expand durable-memory taxonomy in this change.
- Do not change trajectory semantics in this change.
- Do not change cache strategy in this change.
- Do not rewrite runtime message memory in this change.
- Do not add background or scheduled promotion loops in this change.

## Decisions

### Decision: Session logs become the promotion truth source

Governed promotion must evaluate a persisted session-log candidate, not only a
transient turn-end string.

Each candidate must have a stable identity that can be referenced later by:

- promotion decisions
- operator review actions
- quality labels
- durable-memory records created by governed promotion

At minimum, the source reference must let the system resolve:

- `session_id`
- `task_id`
- session-log record offset or candidate id

`llm_calls`, `request_id`, and cache usage may remain linked provenance when
available, but they are not promotion semantics. This change does not alter the
trajectory or cache-observability contracts delivered by the current branch.

Why:

- explainability is weak if promotion is detached from a durable source record
- quality review requires stable IDs
- replaying policy decisions becomes possible only when source records are
  durable

### Decision: Promotion uses explicit governance modes

Promotion behavior must be controlled by a governance mode rather than a single
boolean auto-promotion flag.

Modes:

- `off`: no governed auto-promotion; explicit writes still work
- `shadow`: evaluate and record governed decisions, but never auto-write
  durable memory
- `governed`: allow auto-promotion only for decisions that satisfy the
  promotion policy

Default mode for the new change should be `shadow`.

The current `AWORLD_CLI_ENABLE_AUTO_PROMOTION` behavior is too coarse for
governed rollout. Implementations may support it temporarily as a compatibility
alias, but the stable contract becomes governance mode rather than a boolean.

Why:

- `shadow` provides safe observability before durable writes
- rollout needs a stable canary mode
- boolean enablement cannot express "evaluate but do not promote"

### Decision: Governed decisions have three outcomes

Each evaluated candidate must end in one explicit outcome:

- `durable_memory`: candidate is promoted into active durable memory
- `session_log_only`: candidate stays in session logs and may be reviewed later
- `rejected`: candidate is explicitly blocked from governed promotion unless an
  operator performs an explicit write

This outcome is independent from whether the source session-log event remains
stored. Session logs remain append-only truth records in all cases.

Why:

- `session_log_only` and `rejected` are different governance states
- quality metrics need to distinguish "not yet safe" from "should not
  auto-promote"

### Decision: Promotion must be explanation-first

Every governed decision must record a durable explanation payload.

Minimum explanation fields:

- `decision_id`
- `policy_mode`
- `policy_version`
- `decision`
- `reason`
- `blockers`
- `confidence`
- `memory_type`
- `source_ref`
- `evaluated_at`

When a candidate is promoted, the resulting durable-memory record must retain a
link back to `decision_id` and `source_ref`.

Why:

- operator review is impossible without concrete reasons
- rollout guardrails require auditable policy behavior
- future taxonomy work should build on explicit evidence, not hidden heuristics

### Decision: Governed promotion requires minimal policy gates

The policy for `durable_memory` must be stricter than the current heuristic
auto-promotion path.

A candidate may be auto-promoted only if all of the following are true:

- source candidate is persisted in the workspace session log
- memory type is currently eligible for active durable recall
- content is non-empty after normalization
- candidate is not marked temporary or task-local
- candidate is not an exact duplicate of active durable memory
- decision explanation is complete
- governance mode is `governed`

Candidates that fail safety or quality gates must remain `session_log_only` or
`rejected`.

Why:

- governance should reduce durable-memory pollution, not automate it faster
- duplicate and temporary candidates are common noise sources in current
  extraction flows

### Decision: Corrections stay append-only

Governed review and correction must not rewrite the source session log.

Operator actions such as accept, reject, or revert must be stored as
append-only review records keyed by `decision_id`.

If a previously auto-promoted record is reverted, the system must mark that
promotion inactive for future recall without erasing the historical durable
write event.

Why:

- append-only truth is already the memory design direction
- historical promotion mistakes are valuable evaluation data
- fidelity is better when source events and corrections are both preserved

### Decision: Promotion quality is measured through explicit review labels

Quality metrics must not stop at raw decision counts.

The system must support append-only review labels for governed promotion
decisions:

- `confirmed`: operator agrees the candidate should be active durable memory
- `declined`: operator agrees the candidate should not be auto-promoted
- `reverted`: operator disables a previously auto-promoted record

From those labels, the change must compute at least:

- `reviewed_promotions`
- `confirmed_promotions`
- `reverted_promotions`
- `precision_proxy = confirmed_promotions / reviewed_promotions`
- `pollution_proxy = reverted_promotions / reviewed_promotions`
- `pending_review`

Why:

- promotion quality needs a human-grounded signal before broader rollout
- raw counts of high-confidence candidates do not prove durable-memory quality

### Decision: Default broad rollout is blocked by thresholds

This change defines thresholds for when `governed` may become the default mode
for broader rollout.

Minimum gate:

- at least 100 reviewed governed-promotion decisions in canary use
- `precision_proxy >= 0.90`
- `pollution_proxy <= 0.05`
- 100% of promoted records include explanation and source linkage

Until those thresholds are met, the default mode must remain `shadow`.

Why:

- governed promotion is a quality-sensitive behavior change
- session-log-first safety should remain the default until evidence supports
  broader auto-promotion

## Minimal Product Surface

This change should keep operator UX narrow.

Minimum surface:

- `/memory status` reports governance mode, recent outcomes, and threshold
  readiness
- `/memory promotions` lists recent governed decisions with their explanations
- `/memory promotions accept <decision-id>` confirms and promotes a shadow
  candidate
- `/memory promotions reject <decision-id>` records a rejection label
- `/memory promotions revert <decision-id>` disables a previously active
  governed promotion

Explicit `/remember` writes remain immediate and are not blocked by governance
mode.

## Validation

Validation should prove:

- source linkage is durable and stable across review actions
- `shadow` mode never mutates active durable memory
- `governed` mode promotes only candidates that satisfy policy gates
- reverted promotions stop participating in active durable recall
- status/reporting surfaces expose explanation and rollout-threshold state
- legacy explicit durable writes keep working
