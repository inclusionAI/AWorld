## 1. Contracts and independent implementation boundary

- [x] 1.1 Define AWorld-native versioned models for `ControlledExperimentSpec`,
  `FrozenIdentity`, `ExperimentArm`, `MeasurementObservation`,
  `ExperimentValidity`, `EffectEstimate`, `TransferPanel`, `BudgetLedger`,
  `AttributionReport`, and the bounded `MeasurementSummary` projection.
- [x] 1.2 Define stable enums for swap axes, policy modes, validity states,
  invalidity reason codes, effect conclusions, next actions, and failure owners.
- [x] 1.3 Add schema-version readers that reject unsupported future versions and
  preserve unknown additive fields where existing AWorld serialization policy
  permits it.
- [x] 1.4 Document each public field's unit, nullability, producer, consumer, and
  interpretation; distinguish `null`/not measured from numeric zero.
- [x] 1.5 Add an independent-implementation review gate confirming that the
  implementation contains no OpenRSI dependency, vendored source, copied
  internal API, prompt, fixture, test, schema, or repository layout.

## 2. Controlled experiment planning and frozen identities

- [x] 2.1 Build an experiment planner that permits exactly one declared swap
  axis: `artifact`, `generator`, `scheduler`, or `task_model`.
- [x] 2.2 Reuse AWorld provenance and fingerprint facilities to freeze the target,
  artifact, task model, generator, scheduler, evaluator, dataset, environment,
  runtime, prompt/context, sampling, and budget identities required by the
  selected swap axis.
- [x] 2.3 Validate arm symmetry before execution and emit
  `multiple_swap_axes_changed`, `identity_missing`, or `identity_mismatch`
  instead of silently treating a drifted run as controlled.
- [x] 2.4 Record experiment manifests before candidate observations are known so
  outcome definitions, panels, budgets, and stopping rules cannot be selected
  after inspecting results.
- [x] 2.5 Add deterministic experiment identifiers and arm fingerprints so
  interrupted measurements can resume without mixing observations from a
  different frozen context.

## 3. Paired execution and control validity

- [x] 3.1 Extend the existing replay/evaluation path to execute control and
  treatment on the same case, repetition seed, limits, and environment identity.
- [x] 3.2 Record one append-only observation per arm/case/repetition with task
  outcome, failure type and owner, quality, regression, token cost, wall time,
  latency, evaluator result, and provenance references.
- [x] 3.3 Implement typed control-validity checks for runnable controls,
  comparable outcomes, evaluator health, environment drift, dataset drift,
  budget parity, and arm completeness.
- [x] 3.4 Ensure invalid controls and two-arm infrastructure failures produce no
  task-plane effect estimate and cannot be converted to a zero-effect claim.
- [x] 3.5 Route candidate-owned failures only after a valid control establishes
  comparability; route infrastructure-, evaluator-, and measurement-owned
  failures to the corresponding measurement repair owner.
- [x] 3.6 Add resumability and idempotency tests proving repeated collection does
  not double-count completed observations.

## 4. Independent cases, repetitions, and uncertainty

- [x] 4.1 Represent case identity separately from repetition identity throughout
  storage, aggregation, APIs, and reports.
- [x] 4.2 Aggregate repetitions within each case for stability and variance
  diagnostics before computing cross-case effects.
- [x] 4.3 Implement paired effect estimators for declared quality, regression,
  cost, latency, timeout, and success outcomes.
- [x] 4.4 Compute uncertainty by resampling independent cases, never by treating
  repeated executions of one case as independent samples.
- [x] 4.5 Emit `insufficient_independent_cases` when a requested confidence
  statement is unsupported, while retaining descriptive observations.
- [x] 4.6 Add deterministic statistical tests for positive, negative,
  inconclusive, degenerate, missing, and small-sample inputs.

## 5. Search-opportunity and budget attribution

- [x] 5.1 Maintain separate ledgers for candidate search and controlled
  measurement, covering task-model tokens, generator tokens, evaluator tokens,
  wall time, retries, and candidate opportunity count.
- [x] 5.2 Normalize generator and scheduler comparisons to equal declared search
  budgets and candidate-opportunity ceilings.
- [x] 5.3 Report candidate-distribution summaries plus best@K and pass@K at
  predefined K values; do not use only the selected champion as the comparison.
- [x] 5.4 Produce quality-regression-cost-latency curves over token and wall-time
  budgets, including time/token to a predeclared threshold where applicable.
- [x] 5.5 Implement a predeclared multiple-comparison policy for experiments that
  evaluate several candidates, thresholds, or K values.
- [x] 5.6 Test that unequal search opportunity cannot be mislabeled as generator,
  scheduler, or harness improvement.

## 6. Measurement yield and stopping behavior

- [x] 6.1 Calculate measurement yield as comparable evidence produced per unit of
  measurement budget, with numerator and denominator shown explicitly.
- [x] 6.2 Track scheduled, completed, comparable, invalid, timed-out, and missing
  arm counts without collapsing them into one success rate.
- [x] 6.3 Add configurable early-stop rules for zero comparable pairs, repeated
  control invalidity, budget exhaustion, decisive regression, and sufficiently
  precise conclusions.
- [x] 6.4 Ensure early stopping records the trigger, evidence available at stop,
  unused budget, and whether a future resume is safe.
- [x] 6.5 Test the zero-yield path so more candidate mutations are not scheduled
  when the blocking owner is the measurement system.

## 7. Held-out and transfer panels

- [x] 7.1 Add manifest-backed panels for in-domain held-out cases, cross-task
  transfer, cross-Skill-family transfer, temporal holdout, and regression
  canaries.
- [x] 7.2 Record case membership, snapshot or cutoff time, visibility class, and
  immutable content fingerprint for every panel.
- [x] 7.3 Prevent hidden evidence from entering generator context, candidate
  feedback, repair prompts, or training examples.
- [x] 7.4 Report each transfer panel separately and prohibit a pooled average from
  hiding a required-panel regression.
- [x] 7.5 Add tests for cutoff violations, fingerprint drift, panel leakage, and a
  candidate that improves in-domain but regresses across a required transfer
  panel.

## 8. Attribution artifacts and `report.json` projection

- [x] 8.1 Persist the experiment spec, append-only observations, attribution
  report, and bounded public summary under the AWorld self-evolve run/campaign
  artifact hierarchy defined in the design.
- [x] 8.2 Build `aworld.self_evolve.attribution_report.v1` with identity checks,
  validity, confidence planes, per-outcome effects, transfer panels, search
  curves, budget ledgers, measurement yield, conclusion, and promotion status.
- [x] 8.3 Add a bounded `measurement` section to `report.json` that contains only
  decision-critical summaries and references the detailed attribution artifact.
- [x] 8.4 Keep raw observations and large curves out of `report.json`; expose them
  through typed artifact references and existing retention controls.
- [x] 8.5 Add serialization, round-trip, missing-artifact, and partial-run tests for
  every new artifact type.

## 9. Campaign policy integration

- [x] 9.1 Track `measurement_readiness_progress` separately from
  `candidate_effect_progress` in Campaign state and cycle summaries.
- [x] 9.2 Map invalid evidence to typed actions such as `repair_measurement` or
  `pause_operator`; never emit it as a behavioral lesson for the target Skill.
- [x] 9.3 Allow `continue_candidate_repair` only for candidate-owned failures from
  a valid controlled experiment and attach bounded observation references.
- [x] 9.4 Update champion selection so required mode uses effect confidence bounds,
  regression invariants, transfer invariants, and budget policy rather than raw
  score or best-of-N outcome alone.
- [x] 9.5 Prevent measurement-readiness progress, such as advancing from
  capability compilation to task rollout, from being reported as evidence that
  the evolved Skill improved.
- [x] 9.6 Add campaign tests covering conclusive improvement, inconclusive effect,
  decisive regression, invalid control, zero yield, and measurement recovery.

## 10. Policy modes and migration

- [x] 10.1 Add `off`, `shadow`, `advisory`, and `required` measurement modes with
  explicit defaults and configuration validation.
- [x] 10.2 In `shadow` mode, collect and report evidence without changing existing
  promotion decisions; log the counterfactual policy outcome.
- [x] 10.3 In `advisory` mode, surface warnings and recommended decisions while
  preserving the configured legacy decision authority.
- [x] 10.4 In `required` mode, block promotion when required identity, validity,
  confidence, regression, transfer, or budget gates are not satisfied.
- [x] 10.5 Treat historical reports without a `measurement` section as mode `off`
  and never synthesize controlled evidence from incomplete legacy metrics.
- [x] 10.6 Preserve existing replay repetition semantics during import and avoid
  reclassifying repetitions as independent held-out cases.

## 11. Concrete campaign-derived fixture

- [x] 11.1 Create a minimized synthetic fixture, with no local absolute paths or
  developer data, that models an operator-explicit `agent-browser` target,
  target resolution confidence `1.0`, bypassed inference, and train/validation/
  held-out partitions.
- [x] 11.2 Model a first cycle that stops at capability compilation and a second
  cycle that reaches task rollout but yields two-arm timeouts, zero comparable
  pairs, and zero authoritative candidates.
- [x] 11.3 Assert that target identity is conclusive while causal-target confidence
  and task-plane effect remain null, validity is invalid, and promotion is
  forbidden.
- [x] 11.4 Assert that Campaign records measurement-readiness progress but no
  candidate-effect progress and routes the next action to measurement repair or
  stop policy rather than another identical mutation cycle.

## 12. Operator surfaces and documentation

- [x] 12.1 Add a thin command/API surface to plan, run, resume, inspect, and compare
  controlled experiments without bypassing Campaign or artifact contracts.
- [x] 12.2 Show validity, effect conclusion, confidence interval, comparable-case
  count, measurement yield, dominant budget use, transfer regressions, and next
  action in operator-readable summaries.
- [x] 12.3 Update self-evolve documentation with the distinction among model gain,
  harness gain, generator/scheduler gain, and best-of-N search gain.
- [x] 12.4 Document how to interpret `invalid`, `inconclusive`, `regressed`, and
  `improved`, including why target confidence `1.0` is not causal evidence.
- [x] 12.5 Document data retention, redaction, reproducibility, and leakage
  requirements for detailed measurement artifacts.

## 13. Rollout and acceptance

- [ ] 13.1 Ship schema and artifact generation behind `shadow` mode and calibrate
  validity reason codes on existing AWorld self-evolve campaigns.
- [ ] 13.2 Run shadow measurements across multiple tasks and Skill families; review
  false invalidation, missing identities, budget overhead, and evaluator drift.
- [ ] 13.3 Define promotion thresholds only after calibration data are reviewed;
  version threshold policies independently from experiment observations.
- [x] 13.4 Demonstrate with controlled tests that artifact, task-model, generator,
  and scheduler gains are separable under equal opportunity and frozen context.
- [x] 13.5 Demonstrate that quality, regression, cost, and latency are available as
  token- and wall-time-budget curves, not only as a final best result.
- [ ] 13.6 Demonstrate held-out transfer across task, Skill family, and temporal
  panels with leakage checks enabled.
- [x] 13.7 Run the focused self-evolve test suites and then the repository-required
  validation suites for all touched modules.
- [x] 13.8 Run `openspec validate
  2026-08-11-self-evolve-trusted-improvement-measurement --strict
  --no-interactive` and resolve every validation error before implementation is
  considered ready.
