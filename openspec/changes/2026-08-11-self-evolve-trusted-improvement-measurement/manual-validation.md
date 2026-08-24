# Trusted Improvement Measurement: Manual Validation Runbook

Use this runbook to complete rollout tasks 13.1, 13.2, 13.3, and 13.6. These
steps require representative campaign data and human review; deterministic unit
tests cannot establish the operating thresholds.

## Safety boundary

- Keep every calibration run in `--measurement-mode shadow`.
- Use `--apply verified_only` so accepted packages are retained but not published.
- Do not select `required` or publish a candidate until the calibration review is
  signed off.
- Use frozen datasets and retain the complete run directory under
  `.aworld/self_evolve/<run_id>/` for review.

## Validation matrix

Prepare a matrix that contains, at minimum:

- multiple tasks for each selected Skill;
- at least two materially different Skill families;
- successful controls, known-invalid controls, candidate regressions, and
  inconclusive cases;
- in-domain held-out, cross-task, cross-Skill-family, temporal holdout, and
  regression-canary panels;
- more than one evaluator/runtime window so evaluator and environment drift are
  observable.

Record the dataset fingerprint, target fingerprint, evaluator identity, runtime
identity, and cutoff time before each run. Hidden panel contents must not be
included in candidate-generation inputs or repair feedback.

## Run shadow campaigns

Run each matrix cell with the same predeclared thresholds and budgets:

```bash
aworld-cli optimize \
  --target skill:<skill-name> \
  --from-trajectory-set <frozen-trajectory-set.json> \
  --apply verified_only \
  --measurement-mode shadow \
  --measurement-primary-metric <metric> \
  --measurement-minimum-effect <provisional-effect> \
  --measurement-confidence-level 0.95 \
  --measurement-min-independent-cases <provisional-case-floor>
```

Do not tune a threshold after looking at one treatment result. If a provisional
policy changes, assign the new run set a different decision-policy version and
repeat the complete matrix.

## Review each run

Inspect both artifacts:

- `.aworld/self_evolve/<run_id>/report.json`
- `.aworld/self_evolve/<run_id>/experiments/<experiment_id>/attribution_report.json`

For every run, record:

1. experiment validity and every validity reason code;
2. whether the human reviewer agrees with valid/invalid classification;
3. missing or mismatched frozen identities;
4. comparable, invalid, timed-out, blocked, and missing arm counts;
5. point estimate, confidence interval, independent-case count, and conclusion;
6. measurement token, cost, and wall-time overhead versus the legacy path;
7. evaluator/runtime drift and whether it changed the conclusion;
8. each transfer panel separately, including leakage audit and regressions;
9. shadow counterfactual decision versus the existing release decision;
10. whether failure ownership and the proposed next action are correct.

## Acceptance gates

Complete the rollout tasks only when the review set demonstrates all of the
following:

- false invalidation and false-valid rates are understood and acceptable for
  every represented Skill family;
- required identities are available reliably or fail closed with an actionable
  owner;
- measurement overhead fits the declared token, cost, and wall-time budgets;
- evaluator/environment drift cannot silently convert invalid evidence into a
  candidate effect;
- required transfer panels remain hidden and a panel regression cannot be hidden
  by an aggregate score;
- the selected minimum effect, confidence level, independent-case floor, maximum
  interval width, and patience values are justified by the collected data;
- the threshold/decision policy has a new immutable version independent from the
  observation artifacts.

After sign-off, run a final non-publishing canary with an explicit
`--measurement-mode required`. Compare its decision and artifacts with the
corresponding shadow run before enabling required mode in any publishing flow.

## Stop conditions

Stop validation and return to implementation if any run exposes hidden-panel
leakage, artifact identity drift, unowned measurement failure, non-idempotent
resume behavior, inconsistent budget accounting, or disagreement between
`report.json` and the attribution report.
