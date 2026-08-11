## Overview

This change adds a trusted measurement plane around the existing AWorld
self-evolve optimization and release loop.

The existing loop remains responsible for:

1. building a frozen dataset
2. selecting a target
3. generating candidate artifacts
4. screening and replaying candidates
5. evaluating validation, held-out, regression, and challenger evidence
6. selecting a candidate
7. applying, verifying, or rolling back according to policy

The measurement plane adds a separate question:

> Under a declared, comparable budget and environment, what changed, how large
> was the effect, how uncertain is it, and which component can receive credit?

It does not replace gates. A release may be safe but show no measurable gain;
an experiment may show a likely gain but remain ineligible for release because
regression, provenance, or post-apply verification failed.

## Design Principles

### Measure Before Attributing

Target identity, experiment validity, and effect confidence are separate
claims:

1. **Target resolution**: which artifact or component is under consideration?
2. **Experiment validity**: were control and treatment comparable enough to
   estimate an effect?
3. **Effect confidence**: given a valid experiment, how strongly does the
   evidence support positive, neutral, or negative change?

No confidence score may be reused across these planes.

### Control One Axis At A Time

The initial execution model supports exactly one active swap axis:

- `artifact`
- `task_model`
- `generator`
- `scheduler`

Every other identity is frozen or explicitly recorded as unavailable. An
experiment with more than one changed axis is observational unless a later
factorial-experiment capability defines the interaction design.

### Preserve Release Governance

Current gates remain authoritative. Measurement adds evidence; it does not
bypass:

- candidate shape and provenance
- replay adaptation and conformance
- evidence integrity
- held-out verification
- global regression
- challenger evidence
- apply and post-apply verification
- rollback

### Treat Measurement As A Budgeted Product

Search and measurement have different purposes and must not share an opaque
total:

- **Search budget** generates, repairs, and selects candidates.
- **Measurement budget** executes controls, treatments, repeated runs, and
  transfer panels.

The total Campaign budget remains the hard outer ceiling.

### Independent Native Implementation

This is an AWorld-native design. External projects, including OpenRSI, provide
conceptual motivation for controlled component comparisons and improvement
attribution only. The implementation is grounded in AWorld contracts and does
not copy or depend on external source, schemas, prompts, tests, or runtime
components.

## Terminology

- **Experiment**: a declared control/treatment comparison with frozen
  identities, outcomes, and budgets.
- **Swap axis**: the one component intentionally changed between control and
  treatment.
- **Independent case**: a distinct task/evaluation unit eligible to contribute
  independent evidence.
- **Repetition**: another execution of the same case, used for stability and
  variance estimation but not counted as another independent case.
- **Observation**: one bounded execution result for one arm, case, and
  repetition.
- **Control viability**: whether the control outcome is executable,
  classifiable, and comparable under the declared experiment contract.
- **Measurement readiness**: how far the evaluation harness progressed toward
  producing comparable evidence.
- **Effect estimate**: treatment minus control under a predeclared metric and
  aggregation rule.
- **Measurement yield**: useful comparable evidence produced per unit of
  search/measurement budget.
- **Attribution**: the component that may receive credit after validity and
  effect requirements pass.

## Architecture

```text
Frozen Dataset / Transfer Panels
              |
              v
ControlledExperimentSpec -----> Identity & Leakage Validation
              |                              |
              |                              v
              |                    invalid experiment report
              v
Control/Treatment Execution
              |
              v
Bounded Observations -----> Comparability & Readiness
              |                       |
              |                       v
              |              invalid/inconclusive report
              v
Effect + Uncertainty + Budget Curves
              |
              v
AttributionReport
              |
      +-------+--------+
      |                |
      v                v
report.json       Campaign typed policy
summary/link      shadow/advisory/required
```

The measurement implementation should be isolated in a focused module such as
`aworld.self_evolve.measurement`. `runner.py` orchestrates it, `store.py`
persists it, and `campaign.py` consumes only its bounded typed summary.

## Controlled Experiment Contract

The initial schema is
`aworld.self_evolve.controlled_experiment.v1`.

```json
{
  "schema_version": "aworld.self_evolve.controlled_experiment.v1",
  "experiment_id": "experiment-sha256-...",
  "run_id": "campaign-...-cycle-002",
  "mode": "shadow",
  "swap_axis": "artifact",
  "control": {
    "component_id": "skill:agent-browser",
    "fingerprint": "sha256:..."
  },
  "treatment": {
    "component_id": "candidate:llm-mutator-...",
    "fingerprint": "sha256:..."
  },
  "frozen_identities": {
    "task_model": "model-fingerprint:...",
    "generator": "optimizer-fingerprint:...",
    "scheduler": "scheduler-fingerprint:...",
    "evaluator": "evaluator-fingerprint:...",
    "dataset": "sha256:...",
    "environment": "sha256:...",
    "runtime": "runtime-fingerprint:...",
    "prompt_context": "sha256:..."
  },
  "sampling": {
    "independent_case_ids": ["case-1", "case-2"],
    "seeds": [101, 202, 303],
    "repetitions_per_case": 3,
    "pairing": "same_case_same_seed"
  },
  "outcomes": {
    "primary_metric": "task_success",
    "secondary_metrics": ["score", "latency_ms", "total_tokens"],
    "minimum_effect": 0.02,
    "confidence_level": 0.95
  },
  "budgets": {
    "search": {"tokens": 100000, "cost_usd": null, "wall_seconds": 900},
    "measurement": {"tokens": 100000, "cost_usd": null, "wall_seconds": 1800}
  },
  "transfer_panels": {
    "cross_task": null,
    "cross_skill_family": null,
    "temporal": null
  }
}
```

### Experiment Identity

`experiment_id` is derived from the canonical public experiment contract. It
must exclude secrets and raw provider configuration while including every
field that can change comparability.

The implementation must distinguish:

- public identity fields safe for reports
- private runtime configuration used to execute an arm
- completeness flags indicating whether identity telemetry was available

Required mode fails closed when a required identity is missing. Shadow mode
records the missing identity and produces a non-conclusive report.

### Swap-Axis Semantics

#### Artifact Swap

Freeze task model, generator history, runtime, evaluator, dataset, seeds,
environment, and measurement budget. Execute the installed baseline artifact
and candidate overlay on paired cases.

This is the first implementation target because current AWorld replay already
supports isolated Skill overlays and paired baseline/candidate execution.

#### Task-Model Swap

Freeze harness artifact, prompt/context, tools, dataset, evaluator, scheduler,
and execution budget. This axis measures model contribution and prevents model
upgrades from being reported as harness improvement.

Task-model swaps are measurement-only in this change; they do not authorize
model-weight promotion.

#### Generator Swap

Freeze target baseline, framework version, dataset, evaluator, scheduler,
candidate count ceiling, and total search budget. Compare candidate
distributions and equal-budget best@K/pass@K curves across repeated independent
search trials.

The unit of analysis is not one final champion. It is the distribution of
quality and success under the same opportunity budget.

#### Scheduler Swap

Freeze generator, target baseline, dataset, evaluator, candidate and repair
interfaces, and total search budget. Compare quality-budget curves and time to
threshold. A scheduler receives no credit merely because it evaluated more
candidates or consumed more retries.

## Observation Contract

Each execution writes one
`aworld.self_evolve.measurement_observation.v1` JSONL record.

Required fields:

- experiment id
- run id
- arm: `control` or `treatment`
- swap axis
- independent case id and case fingerprint
- split and transfer-panel identity
- repetition index and seed, when supported
- component fingerprint
- frozen identity fingerprint
- execution status
- comparability status and typed reason
- task outcome and normalized evaluator metrics
- token, cost, wall time, latency, step, and tool-call usage when available
- replay/evaluator artifact references
- failure owner, stage, scope, and typed identity when execution fails
- observation fingerprint

The record uses public diagnostic projection. Raw trajectories and large logs
remain in existing replay/evaluator artifacts and are referenced by path and
fingerprint.

## Experiment Validity

Effect estimation is downstream of a typed validity decision.

### Validity Status

- `valid`: enough comparable independent evidence exists for the declared
  estimator.
- `valid_limited`: comparable evidence exists but only supports a bounded or
  limited-confidence claim.
- `inconclusive`: execution was comparable but statistical evidence is
  insufficient to determine direction.
- `invalid`: the control/treatment contract was violated or observations are
  not comparable.
- `failed`: the measurement system itself failed before it could classify the
  experiment.

### Typed Invalidity Reasons

Initial reason codes include:

- `missing_frozen_identity`
- `multiple_swap_axes_changed`
- `dataset_or_split_drift`
- `environment_drift`
- `budget_mismatch`
- `control_not_comparable`
- `treatment_not_executed`
- `mixed_or_unclassified_failure`
- `insufficient_independent_cases`
- `held_out_leakage`
- `missing_usage_telemetry`
- `selection_protocol_mismatch`

A failed baseline is not automatically invalid. Existing AWorld semantics for
deterministic task-level failure followed by candidate recovery remain valid.
Infrastructure, model initialization, credential, trajectory-capture, blocked,
mixed, and currently incomparable timeout outcomes remain invalid for trusted
effect estimation.

Invalid experiments use `effect = null`. They do not encode the result as a
zero delta and do not create candidate-owned repair evidence unless a separate
typed candidate failure was observed under a viable control.

## Confidence Planes

The attribution report contains three distinct sections.

### Target Resolution Confidence

Imported from target selection and provenance:

- resolution origin
- target identity
- resolution/provenance confidence
- whether inference was executed or bypassed
- causal-target confidence, if a separate credit-assignment experiment exists

For an operator-explicit target, resolution confidence may be `1.0` while
causal-target confidence remains `null`.

### Experiment Validity Confidence

Derived deterministically from identity completeness, control viability,
pairing, dataset/split integrity, budget comparability, and observation counts.
This section reports status and reasons rather than a single opaque probability.

### Effect Confidence

Available only for valid or valid-limited experiments:

- point estimate
- confidence interval
- minimum effect threshold
- direction: positive, neutral, negative, or inconclusive
- estimator and aggregation version
- independent case count
- repetition count
- selection/multiple-comparison adjustment

## Effect Estimation

### Paired Artifact And Model Effects

For paired independent cases, the primary estimate is the aggregate of
treatment-minus-control case effects. The estimator must be configured by
metric type:

- binary success: paired success-rate difference with a conservative interval
- bounded continuous metric: paired mean or robust median difference with a
  bootstrap interval over independent cases
- latency/cost/token: paired relative and absolute deltas with explicit missing
  telemetry

Bootstrap resampling operates over independent cases, not repetitions.
Repetitions are first aggregated within case and may contribute stability or
within-case variance.

### Generator And Scheduler Effects

Generator and scheduler comparisons report distributions across independent
search trials:

- pass@1, pass@K
- best@1, best@K
- median and lower-confidence-bound best score
- candidate validity rate
- authoritative-candidate yield
- time/tokens/cost to first threshold crossing
- area under the quality-versus-budget curve
- regression-adjusted quality frontier

The report must include the actual K and actual budget at every point. Curves
with unequal opportunity budgets are descriptive unless normalized onto a
shared supported budget range.

### Multiple Comparisons

When one champion is selected from multiple candidates, the report records:

- number of generated candidates
- number screened and authoritatively evaluated
- selection rule
- adaptive retries and repairs
- whether the reported effect uses an independent final panel

Trusted promotion should prefer a final panel that was not used to select the
champion. When that is unavailable, the report must downgrade confidence or use
a conservative configured correction.

## Transfer Measurement

Transfer panels are frozen datasets with roles:

- `cross_task`
- `cross_skill_family`
- `temporal`

Each panel records:

- panel id and fingerprint
- selection cutoff timestamp where applicable
- independent case count
- target/task-family eligibility
- leakage audit status
- control/treatment effect and confidence

Temporal cases must be created or sealed after the optimization evidence
cutoff. Candidate generators and adaptive schedulers must not receive panel
contents or metrics before the designated final evaluation stage.

Transfer results do not need to be uniformly positive, but required mode must
apply a configured non-regression or minimum-transfer policy before promotion.

## Budget And Measurement Yield

Every experiment records separate ledgers:

```text
campaign total
  search
    generation
    focused repair
    screening selection
  measurement
    control execution
    treatment execution
    evaluator/judge
    transfer panels
```

Required derived metrics:

- comparable pairs per 100k tokens
- authoritative candidates per 100k search tokens
- conclusive experiments per hour and per configured cost unit
- quality gain per 100k total tokens
- time and budget to first valid control
- time and budget to first conclusive positive effect
- marginal quality gain between adjacent budget points

Missing usage telemetry prevents budget-normalized claims in required mode.

## Attribution Report

Detailed results use
`aworld.self_evolve.attribution_report.v1`.

```json
{
  "schema_version": "aworld.self_evolve.attribution_report.v1",
  "experiment_id": "experiment-sha256-...",
  "swap_axis": "artifact",
  "status": "invalid",
  "target_resolution": {
    "confidence": 1.0,
    "origin": "operator_explicit",
    "inference_bypassed": true,
    "causal_confidence": null
  },
  "validity": {
    "status": "invalid",
    "reason_codes": ["control_not_comparable"],
    "independent_case_count": 1,
    "comparable_pair_count": 0,
    "incomparable_pair_count": 1
  },
  "effect": null,
  "measurement_readiness": {
    "previous_stage": "capability_compile",
    "current_stage": "task_rollout",
    "progressed": true
  },
  "budget_curves": null,
  "transfer": null,
  "measurement_yield": {
    "generated_candidate_count": 11,
    "total_tokens": 465993,
    "comparable_pairs_per_100k_tokens": 0.0
  },
  "decision": {
    "promotion_eligible": false,
    "next_action": "repair_measurement",
    "owner": "evaluation_harness"
  }
}
```

The detailed report is canonical for measurement audit. It does not become a
prompt for candidate generation in raw form.

## Run Report Integration

`report.json` remains a bounded release-facing summary. It gains only:

```json
{
  "measurement": {
    "schema_version": "aworld.self_evolve.measurement_summary.v1",
    "experiment_id": "experiment-sha256-...",
    "mode": "shadow",
    "swap_axis": "artifact",
    "status": "invalid",
    "validity_status": "invalid",
    "effect_direction": "unmeasured",
    "effect_estimate": null,
    "confidence_lower_bound": null,
    "budget_normalized": false,
    "promotion_eligible": false,
    "decision_reason": "control_not_comparable",
    "attribution_report_path": "experiments/.../attribution_report.json"
  }
}
```

Raw observations, per-case metrics, and seed-level results must not be copied
into `report.json`.

## Artifact Layout

```text
.aworld/self_evolve/<run_id>/
  report.json
  experiments/
    <experiment_id>/
      experiment.json
      observations.jsonl
      attribution_report.json
      transfer/
        <panel-id>.json
```

Experiment artifacts use atomic writes where current run artifacts do. Artifact
retention protects reports referenced by active Campaigns and may reclaim raw
observations under the same terminal cleanup policy as replay artifacts.
Canonical experiment and attribution summaries remain durable.

## Measurement Policy Modes

### Off

No new experiment is executed. Existing behavior is unchanged.

### Shadow

The framework writes experiment artifacts and measurement summaries, but
existing candidate selection, Campaign continuation, and release gates remain
unchanged. Shadow is the initial default when measurement is enabled.

### Advisory

Typed measurement decisions may stop clearly wasteful additional measurement
or recommend an owner/next action, but they cannot independently promote or
reject an otherwise release-eligible candidate.

### Required

Configured targets or apply policies require a valid, conclusive measurement
summary before promotion. Missing identities, invalid controls, missing usage,
or inconclusive effects fail closed.

Transition from shadow to advisory or required requires an explicit policy
change and calibration report; it is not enabled implicitly by code rollout.

## Campaign Integration

Campaign stores two new typed progress projections.

### Measurement Readiness Progress

Examples:

- identity contract complete
- baseline/control executable
- paired observations captured
- first comparable pair
- minimum independent evidence reached
- transfer panel executable

This progress can authorize measurement-harness repair or additional evidence
collection. It cannot promote a candidate.

### Candidate Effect Progress

Examples:

- valid-limited positive effect
- conclusive positive lower bound
- regression-safe transfer
- improved quality-budget frontier
- lower cost to threshold without quality regression

Only candidate-effect progress can replace a quality champion in required
mode.

### Typed Next Actions

The deterministic decision layer may return:

- `promote_candidate`
- `continue_candidate_repair`
- `collect_more_evidence`
- `repair_measurement`
- `switch_generator`
- `switch_scheduler`
- `stop_no_effect`
- `stop_negative_effect`
- `pause_operator`

Rules:

- invalid control -> `repair_measurement` or `pause_operator`
- valid but underpowered -> `collect_more_evidence` when information value and
  budget permit
- conclusive negative -> `stop_negative_effect`
- conclusive neutral -> `stop_no_effect`
- candidate-owned failure under valid control -> candidate repair may continue
- repeated zero measurement yield -> stop or change measurement strategy
- conclusive positive plus all existing gates -> candidate may become
  promotion-eligible

The LLM mutator receives bounded candidate-owned feedback only. It does not
receive measurement-harness failures as Skill behavior requirements.

## Champion Selection

Current verification-quality ordering remains the fallback in off and shadow
mode.

Required mode compares promotion candidates lexicographically:

1. existing release safety and regression invariants
2. valid experiment and complete required identities
3. positive effect lower confidence bound above the configured minimum effect
4. transfer non-regression
5. quality/cost/latency Pareto frontier
6. smaller candidate complexity and deterministic candidate-id tie break

A later run cannot replace a champion using a higher raw score when its effect
is invalid, less certain, based on a larger opportunity budget, or regresses an
already-required invariant.

## Concrete Campaign Walkthrough

For the structural conditions observed in
`campaign-f0b510b7bb07ae5de6f0`:

1. Target resolution reports `operator_explicit`, confidence `1.0`, and causal
   confidence `null`.
2. Cycle 1 measurement readiness records a capability-compilation blocker.
3. Cycle 2 records readiness progress because task rollout is reached.
4. Baseline and candidate executions both time out and produce no comparable
   pair.
5. Validity becomes `invalid: control_not_comparable`; effect remains `null`.
6. Candidate quality remains `unmeasured`, not `negative` and not `zero`.
7. Five repeated candidate attempts with no new comparable pair drive
   measurement yield toward zero.
8. The deterministic next action becomes `repair_measurement` or
   `pause_operator`, not another Skill mutation.
9. No transfer, regression, or promotion claim is made.

A committed test fixture must reproduce these structural facts using synthetic
ids and paths rather than depending on the developer's local `.aworld` data.

## Backward Compatibility

- Existing `report.json` readers continue to work because `measurement` is an
  additive optional section.
- Existing Campaign files without measurement progress load with measurement
  mode `off` and preserve current behavior.
- Existing proposal and verified flows remain unchanged in off and shadow
  modes.
- Existing replay repetitions retain their current meaning and are not
  retroactively counted as independent evidence.
- Existing target-selection confidence remains available but is renamed or
  projected explicitly as target-resolution confidence in measurement reports.
- Historical reports may be summarized as observational evidence, but they
  cannot be upgraded to controlled experiments when required identities or
  observations are missing.

## Rollout Strategy

### Phase 1: Contracts And Shadow Artifact Swap

- implement experiment, observation, attribution, and summary schemas
- support artifact swaps using existing paired replay
- write reports only
- characterize the concrete invalid-control Campaign shape

### Phase 2: Statistical And Budget Curves

- add independent-case aggregation and uncertainty intervals
- add measurement yield and quality-budget curves
- add best@K/pass@K for recorded candidate populations
- keep decisions shadow-only

### Phase 3: Advisory Campaign Decisions

- add measurement readiness and effect progress
- add `collect_more_evidence` and `repair_measurement`
- prevent invalid measurement failures from becoming candidate lessons
- validate decisions against historical Campaign fixtures

### Phase 4: Required Promotion And Component Swaps

- enable explicit required policy for allowlisted targets
- add generator and scheduler controlled swaps
- add task-model attribution measurements
- add transfer panels

Each phase must preserve current release safety and can be rolled back by
returning measurement policy to `off` or `shadow`.

## Open Questions

- Which confidence-interval implementation and small-sample fallback should be
  standardized without adding a heavy dependency?
- Which metrics are eligible as primary outcomes for each target type?
- How many independent cases are required before required mode can make a
  positive claim for binary versus continuous outcomes?
- Should temporal panels be sealed by timestamp, immutable manifest, or both?
- Which current timeout failures can be deterministically classified as valid
  task-level controls rather than incomparable execution failures?
- When should measurement-harness repair remain within a Campaign versus create
  a separate framework-owned Goal handoff?
- What calibration evidence is sufficient to move a target class from shadow
  to advisory or required mode?
