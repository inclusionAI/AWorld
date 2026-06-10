## 1. Trial Policy Model

- [ ] 1.1 Add `TrialPolicyDef` with `num_trials`, `pass_at_k`, `pass_caret_k`, and `success_metric`.
- [ ] 1.2 Add `trial_policy` to `EvalSuiteDef` with a default one-trial policy.
- [ ] 1.3 Validate trial policy during flow compilation.
- [ ] 1.4 Preserve existing single-shot behavior when `num_trials == 1`.

## 2. Trial Case Expansion

- [ ] 2.1 Expand each suite case into independent trial case rows when `num_trials > 1`.
- [ ] 2.2 Preserve `original_case_id`, `trial_index`, and `trial_id` in case metadata.
- [ ] 2.3 Ensure runtime-composed harnesses receive trial metadata without storing live handles.
- [ ] 2.4 Add tests for stable case grouping and trial metadata.

## 3. Trial Outcome Aggregation

- [ ] 3.1 Determine per-trial pass/fail from the configured `success_metric`.
- [ ] 3.2 Compute pass@k per original case.
- [ ] 3.3 Compute pass^k per original case.
- [ ] 3.4 Aggregate pass@k/pass^k rates across original cases.
- [ ] 3.5 Allow composite gates to reference trial aggregate metrics.

## 4. Retry / Trial Separation

- [ ] 4.1 Add tests proving retry child attempts do not increase trial count.
- [ ] 4.2 Ensure pass@k/pass^k uses the selected terminal rollout of each trial.
- [ ] 4.3 Preserve retry attempts only inside trial artifacts/metadata.

## 5. Report Shape

- [ ] 5.1 Add report-level trial policy metadata.
- [ ] 5.2 Add report-level trial count summaries.
- [ ] 5.3 Add per-case trial grouping or trial metadata sufficient to reconstruct groups.
- [ ] 5.4 Keep existing report schema compatible via additive fields.

## 6. Verification

- [ ] 6.1 Add focused tests for trial policy validation, trial expansion, pass@k/pass^k aggregation, retry separation, and report shape.
- [ ] 6.2 Run evaluator regression tests.
- [ ] 6.3 Validate this OpenSpec change with `openspec validate aworld-evaluator-trials-passk-2026-06-10 --strict`.
